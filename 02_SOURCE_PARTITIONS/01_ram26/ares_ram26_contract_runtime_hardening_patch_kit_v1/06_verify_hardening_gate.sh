#!/usr/bin/env bash
set -Eeuo pipefail
REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi
REDIS_CLI=(redis-cli)
if [[ -n "${REDIS_URL:-}" ]]; then
  if [[ "$REDIS_URL" == rediss://* ]]; then REDIS_CLI=(redis-cli -u "$REDIS_URL" --tls --insecure); else REDIS_CLI=(redis-cli -u "$REDIS_URL"); fi
fi
python3 - <<'PY'
import os,json,subprocess,re,time,sys
base=["redis-cli"]
url=os.environ.get("REDIS_URL")
if url:
    base=["redis-cli","-u",url]
    if url.startswith("rediss://"): base+=["--tls","--insecure"]
def raw(args):
    try: return subprocess.check_output(base+args,text=True,stderr=subprocess.DEVNULL).strip()
    except Exception: return ""
def get(k): return raw(["--raw","GET",k])
def hkeys(k): return raw(["--raw","HKEYS",k]).splitlines()
def hlen(k):
    try: return int(raw(["HLEN",k]) or 0)
    except: return 0
def hget(k,f): return raw(["--raw","HGET",k,f])
def load(k):
    try: return json.loads(get(k))
    except: return {}
keys=hkeys("champion:targets:ssot")
legacy=hkeys("champion:target")
numeric=[k for k in keys if k.isdigit()]
marker=hget("champion:targets:ssot","_meta_marker")
legacy_marker=hget("champion:target","_meta_marker")
try: marker_j=json.loads(marker)
except: marker_j={}
try: lm=json.loads(legacy_marker)
except: lm={}
rr_ts=get("state:risk_rate:ts")
import datetime as _dt
def _parse_iso(s):
    if not s: return None
    s=s.strip()
    try:
        if s.endswith("Z"): s=s[:-1]+"+00:00"
        return _dt.datetime.fromisoformat(s)
    except Exception:
        return None
_t=_parse_iso(rr_ts)
if _t is not None:
    if _t.tzinfo is None:
        _t=_t.replace(tzinfo=_dt.timezone.utc)
    rr_age=(_dt.datetime.now(_dt.timezone.utc)-_t).total_seconds()
else:
    rr_age=None
ssot=load("ssot:target:v2:current")
policy=load("policy:champion:active")
proj=load("ram26:target_ssot_projector:last")
out={
 "mode": get("emarkos:v1:mode"),
 "trading_enabled": get("trading:enabled"),
 "active_strategy_id": get("emarkos:v1:ram26:active_strategy_id"),
 "g8": get("ares:open009:g8:status"),
 "kill": get("kill-switch:active"),
 "features_status": load("ram26:features:v1:meta").get("status"),
 "engine_gate_pass": load("ram26:engine:gate:last").get("pass"),
 "ssot_engine_version": ssot.get("engine_version"),
 "ssot_targets_shape": list((ssot.get("targets") or {}).keys())[:8] if isinstance(ssot.get("targets"),dict) else type(ssot.get("targets")).__name__,
 "policy_champion_strategy_name": policy.get("champion_strategy_name"),
 "projector_pass": proj.get("pass"),
 "projector_reasons": proj.get("reasons"),
 "champion_targets_ssot_hlen": hlen("champion:targets:ssot"),
 "champion_target_hlen": hlen("champion:target"),
 "numeric_keys": numeric[:20],
 "has_meta_marker": bool(marker_j.get("phase9_b1_marker")),
 "legacy_has_meta_marker": bool(lm.get("phase9_b1_marker")),
 "risk_rate": get("state:risk_rate"),
 "risk_rate_ts": rr_ts,
 "risk_rate_age_sec": rr_age,
 "kis_ws_status_present": bool(get("kis:ws:status:current")),
 "kis_ws_heartbeat": get("kis:ws:heartbeat"),
}
checks={
 "active_ram26": out["active_strategy_id"]=="RAM26_ALPHA_0001_b215c119ea23",
 "features_published": out["features_status"]=="PUBLISHED",
 "engine_gate_pass": out["engine_gate_pass"] is True,
 "policy_ram26": out["policy_champion_strategy_name"]=="RAM26_ALPHA_0001",
 "champion_hash_ok": out["champion_targets_ssot_hlen"]>=9 and not out["numeric_keys"] and out["has_meta_marker"],
 "legacy_hash_marker_ok": out["champion_target_hlen"]>=9 and out["legacy_has_meta_marker"],
 "g8_ready": out["g8"]=="READY",
 "risk_rate_fresh": rr_age is not None and rr_age<120,
 "kis_status_present": out["kis_ws_status_present"],
 "kill_clear": out["kill"]!="true",
}
out["checks"]=checks
out["pass"]=all(checks.values())
print(json.dumps(out,indent=2,ensure_ascii=False))
sys.exit(0 if out["pass"] else 2)
PY
