#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRM="${ARES_HARDEN_AUDIT_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_ARES_HARDEN_READONLY_AUDIT" ]]; then
  echo "ARES_HARDEN_AUDIT_CONFIRM must be YES_ARES_HARDEN_READONLY_AUDIT"
  exit 2
fi

REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/contract_runtime_hardening/${TS}_audit"
mkdir -p "$OUT_DIR"

redact(){ sed -E 's#(rediss?://)[^ @]+@#\1***REDACTED***@#g; s#([A-Z0-9_]*(KEY|SECRET|TOKEN|PASSWORD|PASS)[A-Z0-9_]*=)[^[:space:]]+#\1***REDACTED***#g'; }

if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi

REDIS_CLI=(redis-cli)
if [[ -n "${REDIS_URL:-}" ]]; then
  if [[ "$REDIS_URL" == rediss://* ]]; then REDIS_CLI=(redis-cli -u "$REDIS_URL" --tls --insecure); else REDIS_CLI=(redis-cli -u "$REDIS_URL"); fi
fi
redis_get(){ "${REDIS_CLI[@]}" --raw GET "$1" 2>/dev/null || true; }
redis_hlen(){ "${REDIS_CLI[@]}" --raw HLEN "$1" 2>/dev/null || echo 0; }

{
  date -u
  for k in emarkos:v1:mode trading:enabled trading:enabled:override manual_order_submission_enabled kill-switch:active ops:halt:active ram26:features:v1:meta ram26:engine:gate:last ssot:target:v2:current policy:champion:active champion:targets:ssot:meta champion:target:meta ram26:target_ssot_projector:last state:risk_rate state:risk_rate:ts kis:ws:status:current kis:ws:heartbeat kis:fills:last_ts ares:open009:g8:status; do
    echo "===== $k ====="
    "${REDIS_CLI[@]}" TYPE "$k" 2>/dev/null || true
    redis_get "$k" | head -c 8000
    echo
  done
  echo "HLEN champion:targets:ssot=$(redis_hlen champion:targets:ssot)"
  echo "HLEN champion:target=$(redis_hlen champion:target)"
  echo "--- champion:targets:ssot HKEYS sample ---"
  "${REDIS_CLI[@]}" --raw HKEYS champion:targets:ssot 2>/dev/null | head -80 || true
  echo "--- champion:target HKEYS sample ---"
  "${REDIS_CLI[@]}" --raw HKEYS champion:target 2>/dev/null | head -80 || true
} | redact > "$OUT_DIR/redis_contract_snapshot.txt"

if command -v pm2 >/dev/null 2>&1; then
  pm2 status > "$OUT_DIR/pm2_status.txt" 2>&1 || true
  pm2 jlist | redact > "$OUT_DIR/pm2_jlist_redacted.json" 2>&1 || true
fi

python3 - "$OUT_DIR/redis_contract_snapshot.txt" "$OUT_DIR/audit_summary.json" <<'PY'
import json, re, sys
from pathlib import Path
txt=Path(sys.argv[1]).read_text(errors="ignore")
def sec(k):
    m=re.search(rf"===== {re.escape(k)} =====\n.*?\n(.*?)(?=\n===== |\nHLEN |\Z)", txt, re.S)
    return m.group(1).strip() if m else ""
def load(k):
    try: return json.loads(sec(k))
    except Exception: return {}
def hlen(k):
    m=re.search(rf"HLEN {re.escape(k)}=(\d+)", txt)
    return int(m.group(1)) if m else 0
ssot=load("ssot:target:v2:current")
policy=load("policy:champion:active")
proj=load("ram26:target_ssot_projector:last")
out={
 "mode": sec("emarkos:v1:mode").splitlines()[0] if sec("emarkos:v1:mode") else "",
 "trading_enabled": sec("trading:enabled").splitlines()[0] if sec("trading:enabled") else "",
 "active_ram26": "RAM26_ALPHA_0001_b215c119ea23" in txt,
 "ssot_engine_version": ssot.get("engine_version"),
 "ssot_targets_keys": list((ssot.get("targets") or {}).keys())[:10] if isinstance(ssot.get("targets"),dict) else "non-dict",
 "policy_strategy": policy.get("champion_strategy_name"),
 "projector_pass": proj.get("pass"),
 "projector_reasons": proj.get("reasons"),
 "champion_targets_ssot_hlen": hlen("champion:targets:ssot"),
 "champion_target_hlen": hlen("champion:target"),
 "risk_rate_present": bool(sec("state:risk_rate")),
 "risk_rate_ts_present": bool(sec("state:risk_rate:ts")),
 "kis_ws_status_present": bool(sec("kis:ws:status:current")),
}
Path(sys.argv[2]).write_text(json.dumps(out,indent=2,ensure_ascii=False))
print(json.dumps(out,indent=2,ensure_ascii=False))
PY

cat > "$OUT_DIR/AUDIT_REPORT.md" <<EOF
# ARES Contract Runtime Hardening Audit

## Verdict
ARES_HARDEN_READONLY_AUDIT_COMPLETE

## Artifacts
- redis_contract_snapshot.txt
- pm2_status.txt
- pm2_jlist_redacted.json
- audit_summary.json
EOF

echo "REPORT=$OUT_DIR/AUDIT_REPORT.md"
