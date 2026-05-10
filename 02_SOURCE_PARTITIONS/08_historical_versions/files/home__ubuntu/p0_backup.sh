#!/bin/bash
# P0 런북 STEP 3: 삭제 대상 Redis 키 백업 및 audit 기록
# 실행일시: $(date -u +%Y-%m-%dT%H:%M:%SZ)

source /etc/ares/redis.env
RCLI="redis-cli -h $ARES_REDIS_HOST -p $ARES_REDIS_PORT -a $ARES_REDIS_PASSWORD --tls --no-auth-warning"
TS=$(date -u +%Y%m%dT%H%M%SZ)
AUDIT_KEY="ares:audit:manual_reset:${TS}"

echo "=== STEP 3: Redis 키 백업 시작 ${TS} ==="

# 1) 삭제 대상 키 값 수집
EQ_TOTAL=$($RCLI GET ares:equity:total)
EQ_SNAPSHOT=$($RCLI GET ares:equity:snapshot)
EQ_CASH=$($RCLI GET ares:equity:cash)
ANCHOR=$($RCLI GET champion:equity_anchor:usd)
DD_CURRENT=$($RCLI GET ares:drawdown:current)
DD_ACTUAL=$($RCLI GET ares:drawdown:actual)
GR_STATUS=$($RCLI GET guardrail:status)
GR_DETAILS=$($RCLI HGETALL guardrail:details)

echo "ares:equity:total = $EQ_TOTAL"
echo "ares:equity:snapshot = (JSON, length=${#EQ_SNAPSHOT})"
echo "ares:equity:cash = $EQ_CASH"
echo "champion:equity_anchor:usd = $ANCHOR"
echo "ares:drawdown:current = $DD_CURRENT"
echo "ares:drawdown:actual = $DD_ACTUAL"
echo "guardrail:status = $GR_STATUS"

# 2) audit 키에 JSON dump 저장
BACKUP_JSON=$(cat <<ENDJSON
{
  "ts": "${TS}",
  "operator": "manus-ai-p0-runbook",
  "reason": "equity_calculator_cash_source_ctrp_buy_margin_bug_causing_fake_28k_asset_and_13.5pct_phantom_drawdown",
  "action": "manual_reset_contaminated_keys",
  "pre_reset_values": {
    "ares:equity:total": "${EQ_TOTAL}",
    "ares:equity:cash": "${EQ_CASH}",
    "champion:equity_anchor:usd": "${ANCHOR}",
    "ares:drawdown:current": "${DD_CURRENT}",
    "ares:drawdown:actual": "${DD_ACTUAL}",
    "guardrail:status": "${GR_STATUS}",
    "ares:equity:snapshot_length": "${#EQ_SNAPSHOT}"
  }
}
ENDJSON
)

$RCLI SET "$AUDIT_KEY" "$BACKUP_JSON" EX 604800
echo ""
echo "=== audit 키 저장 완료: $AUDIT_KEY (TTL 7일) ==="
$RCLI GET "$AUDIT_KEY"

# 3) 로컬 파일 백업도 남김
BACKUP_DIR="/home/ubuntu/logs/p0_backup_${TS}"
mkdir -p "$BACKUP_DIR"
echo "$EQ_TOTAL" > "$BACKUP_DIR/ares_equity_total.txt"
echo "$EQ_SNAPSHOT" > "$BACKUP_DIR/ares_equity_snapshot.json"
echo "$EQ_CASH" > "$BACKUP_DIR/ares_equity_cash.txt"
echo "$ANCHOR" > "$BACKUP_DIR/champion_equity_anchor_usd.txt"
echo "$DD_CURRENT" > "$BACKUP_DIR/ares_drawdown_current.txt"
echo "$DD_ACTUAL" > "$BACKUP_DIR/ares_drawdown_actual.txt"
echo "$GR_STATUS" > "$BACKUP_DIR/guardrail_status.txt"
echo "$BACKUP_JSON" > "$BACKUP_DIR/audit_record.json"

echo ""
echo "=== 로컬 백업 완료: $BACKUP_DIR ==="
ls -la "$BACKUP_DIR"
echo ""
echo "=== STEP 3 완료 ==="
