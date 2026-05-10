#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRM="${ARES_REDIS_ENV_AUDIT_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_AUDIT_PM2_REDIS_ENV" ]]; then
  echo "ARES_REDIS_ENV_AUDIT_CONFIRM must be YES_AUDIT_PM2_REDIS_ENV"
  exit 2
fi

REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
ALLOW_RELOAD="${ALLOW_REDIS_ENV_RELOAD:-NO}"
RELOAD_APPS_CSV="${RELOAD_APPS_CSV:-}"
EXCLUDE_REGEX="${EXCLUDE_REGEX:-nextgen2-live|position-sync-service|order-intent-executor|live-trading-kis}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/contract_runtime_hardening/${TS}_redis_env_audit"
mkdir -p "$OUT_DIR"

if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi
SSOT_REDIS_URL="${REDIS_URL:-}"
SSOT_REDIS_PASSWORD="${REDIS_PASSWORD:-}"

pm2 jlist > "$OUT_DIR/pm2_jlist_raw.json" 2>/dev/null || echo "[]" > "$OUT_DIR/pm2_jlist_raw.json"
python3 - "$OUT_DIR/pm2_jlist_raw.json" "$OUT_DIR/redis_env_matrix.json" <<'PY'
import json, os, sys, hashlib
arr=json.load(open(sys.argv[1]))
ssot_url=os.environ.get("SSOT_REDIS_URL") or os.environ.get("REDIS_URL","")
ssot_pass=os.environ.get("SSOT_REDIS_PASSWORD") or os.environ.get("REDIS_PASSWORD","")
def fp(x): return hashlib.sha256((x or "").encode()).hexdigest()[:16] if x else ""
rows=[]
for p in arr:
    e=p.get("pm2_env",{})
    env=e.get("env",{}) or {}
    name=p.get("name")
    ru=env.get("REDIS_URL") or e.get("REDIS_URL") or ""
    rp=env.get("REDIS_PASSWORD") or e.get("REDIS_PASSWORD") or ""
    rows.append({
      "name":name,
      "status":e.get("status"),
      "restart_time":e.get("restart_time"),
      "pm_exec_path":e.get("pm_exec_path"),
      "redis_url_fp":fp(ru),
      "redis_password_fp":fp(rp),
      "matches_ssot_url": bool(ru) and fp(ru)==fp(ssot_url),
      "matches_ssot_password": bool(rp) and fp(rp)==fp(ssot_pass),
      "has_redis_url": bool(ru),
      "has_redis_password": bool(rp),
    })
json.dump({"ssot_url_fp":fp(ssot_url),"ssot_password_fp":fp(ssot_pass),"rows":rows},open(sys.argv[2],"w"),indent=2)
PY

python3 - "$OUT_DIR/redis_env_matrix.json" > "$OUT_DIR/redis_env_summary.txt" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); rows=d["rows"]
bad=[r for r in rows if r["status"]=="online" and (not r["matches_ssot_url"] or not r["matches_ssot_password"])]
print("online_total",sum(1 for r in rows if r["status"]=="online"))
print("bad_online",len(bad))
for r in bad[:100]:
    print(r["name"],"url",r["matches_ssot_url"],"pass",r["matches_ssot_password"],"path",r["pm_exec_path"])
PY

if [[ "$ALLOW_RELOAD" == "YES_RELOAD_LISTED_APPS" && -n "$RELOAD_APPS_CSV" ]]; then
  IFS=',' read -ra APPS <<< "$RELOAD_APPS_CSV"
  for app in "${APPS[@]}"; do
    app="$(echo "$app" | xargs)"
    [[ -z "$app" ]] && continue
    if echo "$app" | grep -Eq "$EXCLUDE_REGEX"; then
      echo "SKIP_EXCLUDED $app" | tee -a "$OUT_DIR/reload.log"
      continue
    fi
    if pm2 describe "$app" >/dev/null 2>&1; then
      echo "RELOAD $app" | tee -a "$OUT_DIR/reload.log"
      REDIS_URL="$SSOT_REDIS_URL" REDIS_PASSWORD="$SSOT_REDIS_PASSWORD" pm2 restart "$app" --update-env || true
    fi
  done
  pm2 save || true
fi

cat > "$OUT_DIR/REDIS_ENV_AUDIT_REPORT.md" <<EOF
# PM2 Redis Env Audit Report

## Verdict
ARES_PM2_REDIS_ENV_AUDIT_COMPLETE

## Artifacts
- redis_env_matrix.json
- redis_env_summary.txt

## Reload
- allow: ${ALLOW_RELOAD}
- apps: ${RELOAD_APPS_CSV:-none}
EOF
echo "REPORT=$OUT_DIR/REDIS_ENV_AUDIT_REPORT.md"
