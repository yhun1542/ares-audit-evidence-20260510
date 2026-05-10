#!/bin/bash
# F4 v2 — ARES v8.4 self-diagnostic (재발 방지)
# Path: /home/ubuntu/ares_v84_selfcheck.sh
# Crontab: */5 * * * * /home/ubuntu/ares_v84_selfcheck.sh >> /home/ubuntu/.pm2/logs/ares_v84_selfcheck.log 2>&1
#
# Hardened against the issues raised in 3-AI review:
#   - All redis-cli output is run through `_num` to strip non-digits before arithmetic.
#   - Recent-window grep uses `tail -n 5000` of the pm2 log so the alarm is
#     not driven by the lifetime count.
#   - HB TTL distinguishes -1 (no TTL, anomalous) from -2 (missing) explicitly.
#   - REDIS_OPTS is quoted via an array; redis-cli is configured with -h/-p
#     when REDIS_HOST/REDIS_PORT are exported (else uses defaults).
#   - Alarm keys auto-expire (TTL 30 min); summary line is always emitted.

set -u
TS=$(date -Is)
LOG_TAG="[selfcheck $TS]"
ALERT_TTL=1800

REDIS_HOST="${REDIS_HOST:-}"
REDIS_PORT="${REDIS_PORT:-}"
REDIS_OPTS_ARR=()
[ -n "$REDIS_HOST" ] && REDIS_OPTS_ARR+=( -h "$REDIS_HOST" )
[ -n "$REDIS_PORT" ] && REDIS_OPTS_ARR+=( -p "$REDIS_PORT" )

PM2_LOG=/home/ubuntu/.pm2/logs/final-to-champion-bridge-out.log
RECENT_LINES="${RECENT_LINES:-5000}"

_num() { # echo first integer found, else 0
  echo "${1:-0}" | tr -d '\r\n' | grep -oE '\-?[0-9]+' | head -1 | sed 's/^$/0/'
}

set_alert() {
  local key="$1"; local msg="$2"
  redis-cli "${REDIS_OPTS_ARR[@]}" SET "$key" "$msg" EX "$ALERT_TTL" > /dev/null
  echo "$LOG_TAG ALERT $key: $msg"
}
clear_alert() { redis-cli "${REDIS_OPTS_ARR[@]}" DEL "$1" > /dev/null; }

# 1. ctx:regime:state present?
ST_TYPE=$(redis-cli "${REDIS_OPTS_ARR[@]}" TYPE ctx:regime:state 2>/dev/null)
ST_LEN_RAW=$(redis-cli "${REDIS_OPTS_ARR[@]}" STRLEN ctx:regime:state 2>/dev/null)
ST_LEN=$(_num "$ST_LEN_RAW")
if [ "$ST_TYPE" != "string" ] || [ "$ST_LEN" -lt 50 ]; then
  set_alert ares:alert:ctx_regime_state_missing "type=$ST_TYPE strlen=$ST_LEN"
else
  clear_alert ares:alert:ctx_regime_state_missing
fi

# 2. writer F2 set-verify failure?
FAIL_RAW=$(redis-cli "${REDIS_OPTS_ARR[@]}" GET ctx:regime:state:writer_failure 2>/dev/null)
if [ -n "$FAIL_RAW" ]; then
  set_alert ares:alert:writer_set_verify_fail "$FAIL_RAW"
else
  clear_alert ares:alert:writer_set_verify_fail
fi

# 3. ARES meta_src distribution (F3 counters; 1h rolling window)
DEF=$(_num "$(redis-cli "${REDIS_OPTS_ARR[@]}" GET ares:bridge:counter:meta_src:default_fallback 2>/dev/null)")
LIVE=$(_num "$(redis-cli "${REDIS_OPTS_ARR[@]}" GET ares:bridge:counter:meta_src:live_components_only 2>/dev/null)")
CTX=$(_num "$(redis-cli "${REDIS_OPTS_ARR[@]}" GET ares:bridge:counter:meta_src:ctx_regime_state 2>/dev/null)")
CTX_LIVE=$(_num "$(redis-cli "${REDIS_OPTS_ARR[@]}" GET ares:bridge:counter:meta_src:ctx_regime_state_live 2>/dev/null)")
TOTAL=$(( DEF + LIVE + CTX + CTX_LIVE ))
if [ "$TOTAL" -ge 60 ]; then
  RATIO=$(awk -v a="$DEF" -v b="$TOTAL" 'BEGIN{printf "%.4f", (b>0?a/b:0)}')
  HIGH=$(awk -v r="$RATIO" 'BEGIN{print (r>=0.05)?1:0}')
  if [ "$HIGH" = "1" ]; then
    set_alert ares:alert:meta_src_default_fallback "ratio=$RATIO def=$DEF/total=$TOTAL"
  else
    clear_alert ares:alert:meta_src_default_fallback
  fi
fi

# 4. Bridge λ pinned over the last RECENT_LINES log lines?
if [ -r "$PM2_LOG" ]; then
  TAIL_TMP=$(mktemp)
  tail -n "$RECENT_LINES" "$PM2_LOG" > "$TAIL_TMP"

  UNIQ=$(grep "LAMBDA_APPLY" "$TAIL_TMP" | sed -n 's/.*lambda=\([0-9.][0-9.]*\).*/\1/p' | sort -u | wc -l)
  UNIQ=$(_num "$UNIQ")
  CNT=$(grep -c "LAMBDA_APPLY" "$TAIL_TMP" || true); CNT=$(_num "$CNT")
  if [ "$UNIQ" = "1" ] && [ "$CNT" -ge 50 ]; then
    LAST_LAM=$(grep "LAMBDA_APPLY" "$TAIL_TMP" | tail -1 | sed -n 's/.*lambda=\([0-9.][0-9.]*\).*/\1/p')
    set_alert ares:alert:bridge_lambda_pinned "uniq=1 lambda=$LAST_LAM cnt=$CNT (recent ${RECENT_LINES} lines)"
  else
    clear_alert ares:alert:bridge_lambda_pinned
  fi

  # 5. RISK_RATE_v84_FALLBACK ratio over the same window
  RFB=$(grep -c "RISK_RATE_v84_FALLBACK" "$TAIL_TMP" || true); RFB=$(_num "$RFB")
  RTOT=$(grep -cE "RISK_RATE_v84(_FALLBACK)?\b|RISK_RATE_v85" "$TAIL_TMP" || true); RTOT=$(_num "$RTOT")
  if [ "$RTOT" -ge 30 ]; then
    FRATIO=$(awk -v a="$RFB" -v b="$RTOT" 'BEGIN{printf "%.4f", (b>0?a/b:0)}')
    HIGH2=$(awk -v r="$FRATIO" 'BEGIN{print (r>=0.10)?1:0}')
    if [ "$HIGH2" = "1" ]; then
      set_alert ares:alert:risk_rate_v84_fallback "fb=$RFB/total=$RTOT ratio=$FRATIO"
    else
      clear_alert ares:alert:risk_rate_v84_fallback
    fi
  fi
  rm -f "$TAIL_TMP"
fi

# 6. Heartbeats — writer-v84 (Python) + regime-writer-v88 (Node)
WRITER_HB=$(_num "$(redis-cli "${REDIS_OPTS_ARR[@]}" TTL ares:sleeve:writer:heartbeat 2>/dev/null)")
REGIME_HB=$(_num "$(redis-cli "${REDIS_OPTS_ARR[@]}" TTL ctx:regime:state:writer_heartbeat 2>/dev/null)")
# -2 = missing (real fault). -1 = exists but no TTL (anomalous; warn).
if [ "$WRITER_HB" = "-2" ]; then
  set_alert ares:alert:writer_v84_heartbeat_lost "ttl=$WRITER_HB (key missing)"
elif [ "$WRITER_HB" = "-1" ]; then
  set_alert ares:alert:writer_v84_heartbeat_no_ttl "ttl=-1 (no TTL set)"
else
  clear_alert ares:alert:writer_v84_heartbeat_lost
  clear_alert ares:alert:writer_v84_heartbeat_no_ttl
fi
if [ "$REGIME_HB" = "-2" ]; then
  set_alert ares:alert:regime_writer_v88_heartbeat_lost "ttl=$REGIME_HB (key missing)"
elif [ "$REGIME_HB" = "-1" ]; then
  set_alert ares:alert:regime_writer_v88_heartbeat_no_ttl "ttl=-1 (no TTL set)"
else
  clear_alert ares:alert:regime_writer_v88_heartbeat_lost
  clear_alert ares:alert:regime_writer_v88_heartbeat_no_ttl
fi

# Summary
echo "$LOG_TAG OK summary state.type=$ST_TYPE state.strln=$ST_LEN meta_src(def/live/ctx/ctxlive)=$DEF/$LIVE/$CTX/$CTX_LIVE writer_hb=$WRITER_HB regime_hb=$REGIME_HB"
