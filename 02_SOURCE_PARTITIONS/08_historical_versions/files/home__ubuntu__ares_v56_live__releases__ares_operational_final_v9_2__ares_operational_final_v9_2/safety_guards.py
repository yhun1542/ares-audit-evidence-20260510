#!/usr/bin/env python3
"""
safety_guards.py — ARES Production Safety Guards v2.1.0
========================================================
Surgical hotfixes on top of current guard deployment:
1) Effective drawdown halt via trading:enabled=false + buy_freeze=true
2) Fallback freshness parsing/attestation hardened
3) AOA duplicate-emit risk guard (detect + optional hard stop)

Design principle:
- Fail only on clear operational risk, not on normal alpha variance.
- Use the same runtime gates that live-trading-kis already honors.
"""

import json
import os
import sys
import time
import signal
import subprocess
import traceback
from datetime import datetime, timezone

__version__ = "2.1.0"
GUARD_NAME = "ares-safety-guards"

REDIS_URL = os.getenv("ARES_REDIS_URL") or os.getenv("REDIS_URL") or ""
if not REDIS_URL:
    print("[FATAL] REDIS_URL not set", flush=True)
    sys.exit(1)

import redis as redis_lib
r = redis_lib.from_url(REDIS_URL, decode_responses=True)

DRAWDOWN_LIMIT_PCT = float(os.getenv("SAFETY_DRAWDOWN_LIMIT_PCT", "-3.0"))
FALLBACK_TTL_SEC = int(os.getenv("SAFETY_FALLBACK_TTL_SEC", "900"))
GROSS_EXPOSURE_HARD = float(os.getenv("SAFETY_GROSS_HARD", "0.98"))
GROSS_EXPOSURE_SOFT = float(os.getenv("SAFETY_GROSS_SOFT", "0.90"))
NET_EXPOSURE_HARD = float(os.getenv("SAFETY_NET_HARD", "0.95"))
PER_SYMBOL_MAX_PCT = float(os.getenv("SAFETY_PER_SYMBOL_MAX_PCT", "10.0"))
CHECK_INTERVAL_SEC = int(os.getenv("SAFETY_CHECK_INTERVAL_SEC", "30"))
ALERT_STREAM_KEY = "ares:safety:alerts"
ALERT_STREAM_MAXLEN = 5000
HALT_FLAG_KEY = "ares:v57:halt"
HEALTH_KEY = f"{GUARD_NAME}:health"
HEALTH_TTL = 120
AUA_HARD_BLOCK = str(os.getenv("SAFETY_AOA_HARD_BLOCK", "true")).lower() not in {"0", "false", "off"}

EQUITY_KEY = "ares:equity:total"
POSITIONS_KEY = "emarkos:v1:positions"
TARGET_LAST_KEY = "ares:v57:target:last"
SSOT_CURRENT_KEY = "ssot:target:v2:current"
TRADING_ENABLED_KEY = "trading:enabled"
TRADING_DISABLE_REASON_KEY = "trading:disable_reason"
BUY_FREEZE_KEY = "policy:buy_freeze"
METRICS_KEY = "ares:v57:metrics"

_shutdown = False
_day_start_equity = None
_day_date = None
_alert_counts = {}


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log("INFO", f"Shutdown requested via signal {signum}")


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def log(level, msg, **kwargs):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    extra = json.dumps(kwargs, ensure_ascii=False, default=str) if kwargs else ""
    print(f"[{ts}] [{level}] {GUARD_NAME}: {msg} {extra}", flush=True)


def alert(severity, guard_name, message, details=None):
    global _alert_counts
    key = f"{guard_name}:{severity}"
    _alert_counts[key] = _alert_counts.get(key, 0) + 1
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "guard": guard_name,
        "message": message,
        "details": json.dumps(details or {}, default=str),
        "count": str(_alert_counts[key]),
    }
    try:
        r.xadd(ALERT_STREAM_KEY, payload, maxlen=ALERT_STREAM_MAXLEN, approximate=True)
    except Exception as e:
        log("ERROR", f"Failed to write alert stream: {e}")
    log_level = "CRITICAL" if severity == "CRITICAL" else "WARN"
    log(log_level, f"[ALERT] [{guard_name}] {message}", **payload)


def halt_trading(reason):
    """Effective emergency halt using the same gates live-trading-kis already honors."""
    payload = json.dumps({
        "ts": time.time(),
        "reason": reason,
        "source": GUARD_NAME,
        "version": __version__,
    })
    try:
        pipe = r.pipeline()
        pipe.set(HALT_FLAG_KEY, payload, ex=3600)
        pipe.set(TRADING_DISABLE_REASON_KEY, reason, ex=3600)
        pipe.set(TRADING_ENABLED_KEY, "false")
        pipe.set(BUY_FREEZE_KEY, "true")
        pipe.execute()
        log("CRITICAL", f"TRADING HALTED: {reason}")
        alert("CRITICAL", "HALT", reason)
    except Exception as e:
        log("ERROR", f"Failed to set halt flag: {e}")


def parse_json(raw):
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def parse_epochish(v):
    if v is None:
        return None
    if isinstance(v, (int, float)) and v > 0:
        return int(v / 1000) if v > 1e12 else int(v)
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        return int(n / 1000) if n > 1e12 else n
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def extract_ts_and_age(obj):
    if not isinstance(obj, dict):
        return None, None, None
    candidates = [
        ("ts", obj.get("ts")),
        ("updated_at", obj.get("updated_at")),
        ("timestamp", obj.get("timestamp")),
        ("generated_at", obj.get("generated_at")),
        ("meta.updated_at", obj.get("meta", {}).get("updated_at") if isinstance(obj.get("meta"), dict) else None),
        ("meta.ts", obj.get("meta", {}).get("ts") if isinstance(obj.get("meta"), dict) else None),
    ]
    now = int(time.time())
    for field, val in candidates:
        ts = parse_epochish(val)
        if ts:
            return field, ts, max(0, now - ts)
    return None, None, None


def extract_positions_list(obj):
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("positions"), list):
        return obj.get("positions") or []
    if isinstance(obj.get("targets"), dict) and isinstance(obj["targets"].get("positions"), list):
        return obj["targets"].get("positions") or []
    return []


def xfloat(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def check_drawdown():
    global _day_start_equity, _day_date
    try:
        equity_raw = r.get(EQUITY_KEY)
        if not equity_raw:
            return {"status": "SKIP", "reason": "no_equity_data"}
        equity = float(equity_raw)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if _day_date != today:
            _day_start_equity = equity
            _day_date = today
            log("INFO", f"[DRAWDOWN] Day reset: start_equity={equity:.2f}")
            return {"status": "OK", "equity": equity, "start": equity}
        if _day_start_equity is None or _day_start_equity <= 0:
            _day_start_equity = equity
            return {"status": "OK", "equity": equity}
        drawdown_pct = ((equity - _day_start_equity) / _day_start_equity) * 100
        if drawdown_pct <= DRAWDOWN_LIMIT_PCT:
            halt_trading(f"Intraday drawdown {drawdown_pct:.2f}% exceeds limit {DRAWDOWN_LIMIT_PCT}%")
            return {"status": "BREACHED", "drawdown_pct": drawdown_pct, "limit": DRAWDOWN_LIMIT_PCT}
        if drawdown_pct <= DRAWDOWN_LIMIT_PCT * 0.6:
            alert("WARN", "DRAWDOWN", f"Drawdown at {drawdown_pct:.2f}% (limit: {DRAWDOWN_LIMIT_PCT}%)",
                  {"equity": equity, "start": _day_start_equity})
        return {"status": "OK", "drawdown_pct": round(drawdown_pct, 4), "equity": equity}
    except Exception as e:
        log("ERROR", f"[DRAWDOWN] check failed: {e}")
        return {"status": "ERROR", "error": str(e)}


def check_fallback_freshness():
    try:
        raw = r.get(TARGET_LAST_KEY)
        if not raw:
            return {"status": "EMPTY", "key": TARGET_LAST_KEY}
        obj = parse_json(raw)
        if not obj:
            return {"status": "PARSE_FAIL", "key": TARGET_LAST_KEY}
        ts_field, ts_sec, age_sec = extract_ts_and_age(obj)
        symbols = len(obj.get("symbols", [])) if isinstance(obj.get("symbols"), list) else len(extract_positions_list(obj))
        if ts_sec is None:
            alert("WARN", "FALLBACK_NO_TS", "ares:v57:target:last has no parseable freshness timestamp", {"symbols": symbols})
            return {"status": "NO_TS", "symbols": symbols}
        if age_sec > FALLBACK_TTL_SEC:
            alert("WARN", "FALLBACK_STALE", f"ares:v57:target:last is {age_sec:.0f}s old (limit: {FALLBACK_TTL_SEC}s)",
                  {"age_sec": age_sec, "symbols": symbols, "ts_field": ts_field})
            r.set("ares:v57:fallback:stale", json.dumps({"age_sec": age_sec, "ts_field": ts_field, "symbols": symbols}), ex=FALLBACK_TTL_SEC)
            return {"status": "STALE", "age_sec": age_sec, "limit": FALLBACK_TTL_SEC, "symbols": symbols, "ts_field": ts_field}
        r.delete("ares:v57:fallback:stale")
        return {"status": "FRESH", "age_sec": round(age_sec, 1), "symbols": symbols, "ts_field": ts_field}
    except Exception as e:
        log("ERROR", f"[FALLBACK_TTL] check failed: {e}")
        return {"status": "ERROR", "error": str(e)}


def check_exposure():
    try:
        raw = r.get(SSOT_CURRENT_KEY)
        if not raw:
            return {"status": "SKIP", "reason": "no_ssot_data"}
        obj = parse_json(raw)
        positions = extract_positions_list(obj)
        if not positions:
            return {"status": "SKIP", "reason": "no_positions"}
        gross = sum(abs(xfloat(p.get("w", p.get("weight", 0)))) for p in positions)
        net = sum(xfloat(p.get("w", p.get("weight", 0))) for p in positions)
        result = {"status": "OK", "gross": round(gross, 6), "net": round(net, 6), "positions": len(positions)}
        if gross > GROSS_EXPOSURE_HARD:
            alert("CRITICAL", "EXPOSURE_HARD", f"Gross exposure {gross:.4f} exceeds hard cap {GROSS_EXPOSURE_HARD}", {"gross": gross, "net": net})
            result["status"] = "BREACHED_HARD"
        elif gross > GROSS_EXPOSURE_SOFT:
            alert("WARN", "EXPOSURE_SOFT", f"Gross exposure {gross:.4f} exceeds soft cap {GROSS_EXPOSURE_SOFT}", {"gross": gross, "net": net})
            result["status"] = "WARN_SOFT"
        if abs(net) > NET_EXPOSURE_HARD:
            alert("CRITICAL", "NET_EXPOSURE", f"Net exposure {net:.4f} exceeds cap {NET_EXPOSURE_HARD}", {"gross": gross, "net": net})
            result["status"] = "BREACHED_NET"
        return result
    except Exception as e:
        log("ERROR", f"[EXPOSURE] check failed: {e}")
        return {"status": "ERROR", "error": str(e)}


def check_position_sizes():
    try:
        raw = r.get(SSOT_CURRENT_KEY)
        if not raw:
            return {"status": "SKIP"}
        obj = parse_json(raw)
        positions = extract_positions_list(obj)
        violations = []
        for p in positions:
            w = abs(xfloat(p.get("w", p.get("weight", 0)))) * 100
            if w > PER_SYMBOL_MAX_PCT:
                violations.append({"symbol": p.get("symbol"), "weight_pct": round(w, 2)})
        if violations:
            alert("WARN", "POSITION_SIZE", f"{len(violations)} symbols exceed {PER_SYMBOL_MAX_PCT}% limit", {"violations": violations})
            return {"status": "VIOLATION", "violations": violations}
        max_weight = max((abs(xfloat(p.get("w", p.get("weight", 0)))) * 100 for p in positions), default=0)
        return {"status": "OK", "max_weight_pct": round(max_weight, 2)}
    except Exception as e:
        log("ERROR", f"[POSITION_SIZE] check failed: {e}")
        return {"status": "ERROR", "error": str(e)}


def check_scaling_bypass():
    try:
        cdm_raw = r.get("champion:direct:mode:status")
        bridge_raw = r.get("ssot-champion-bridge:heartbeat")
        attestation = {
            "scaling_bypass_rationale": (
                "Champion engine already applies target_vol, regime logic, exposure caps and turnover logic. "
                "Additional live scaling layers created systematic A!=C divergence and under-execution."
            ),
            "champion_direct_active": cdm_raw is not None,
            "bridge_heartbeat": bridge_raw is not None,
        }
        r.set("ares:safety:scaling_bypass_attestation", json.dumps(attestation, default=str), ex=600)
        return {"status": "OK", **attestation}
    except Exception as e:
        log("ERROR", f"[SCALING_BYPASS] check failed: {e}")
        return {"status": "ERROR", "error": str(e)}


def check_target_count():
    try:
        raw = r.get(SSOT_CURRENT_KEY)
        if not raw:
            return {"status": "EMPTY"}
        obj = parse_json(raw)
        positions = extract_positions_list(obj)
        count = len(positions)
        fallback_raw = r.get(TARGET_LAST_KEY)
        fallback_count = 0
        if fallback_raw:
            fb = parse_json(fallback_raw) or {}
            if isinstance(fb.get("symbols"), list):
                fallback_count = len(fb.get("symbols") or [])
            else:
                fallback_count = len(extract_positions_list(fb))
        result = {"status": "OK", "ssot_count": count, "fallback_count": fallback_count}
        if count < 15:
            alert("WARN", "TARGET_COUNT", f"ssot:target:v2:current has only {count} positions (threshold: 15)", {"ssot_count": count, "fallback_count": fallback_count})
            result["status"] = "LOW"
        return result
    except Exception as e:
        log("ERROR", f"[TARGET_COUNT] check failed: {e}")
        return {"status": "ERROR", "error": str(e)}


def check_redis_health():
    try:
        start = time.time()
        r.ping()
        latency_ms = (time.time() - start) * 1000
        info = r.info("memory")
        used_memory_mb = info.get("used_memory", 0) / (1024 * 1024)
        result = {"status": "OK", "latency_ms": round(latency_ms, 2), "used_memory_mb": round(used_memory_mb, 2)}
        if latency_ms > 100:
            alert("WARN", "REDIS_LATENCY", f"Redis latency {latency_ms:.1f}ms (threshold: 100ms)")
            result["status"] = "SLOW"
        return result
    except Exception as e:
        alert("CRITICAL", "REDIS_DOWN", f"Redis connection failed: {e}")
        return {"status": "DOWN", "error": str(e)}


def pm2_process_online(name: str):
    try:
        cp = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=10)
        if cp.returncode != 0:
            return False
        arr = json.loads(cp.stdout or "[]")
        for p in arr:
            if p.get("name") == name:
                return p.get("pm2_env", {}).get("status") == "online"
        return False
    except Exception:
        return False


def check_aoa_duplicate_risk():
    try:
        champion_direct = r.get("champion:direct:mode:status") is not None
        aoa_online = pm2_process_online("aoa-live")
        if champion_direct and aoa_online:
            alert("WARN", "AOA_DUPLICATE_RISK", "aoa-live is online while Champion Direct is active", {"hard_block": AUA_HARD_BLOCK})
            if AUA_HARD_BLOCK:
                cp = subprocess.run(["pm2", "stop", "aoa-live"], capture_output=True, text=True, timeout=15)
                if cp.returncode == 0:
                    alert("WARN", "AOA_HARD_BLOCK", "aoa-live stopped to prevent duplicate emits")
                    return {"status": "BLOCKED", "aoa_online": True}
                return {"status": "WARN", "aoa_online": True, "stop_error": cp.stderr.strip()}
            return {"status": "WARN", "aoa_online": True}
        return {"status": "OK", "aoa_online": aoa_online, "champion_direct": champion_direct}
    except Exception as e:
        log("ERROR", f"[AOA_DUPLICATE] check failed: {e}")
        return {"status": "ERROR", "error": str(e)}


def run_all_checks():
    results = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "version": __version__,
        "drawdown": check_drawdown(),
        "fallback_freshness": check_fallback_freshness(),
        "exposure": check_exposure(),
        "position_sizes": check_position_sizes(),
        "scaling_bypass": check_scaling_bypass(),
        "target_count": check_target_count(),
        "redis_health": check_redis_health(),
        "aoa_duplicate_risk": check_aoa_duplicate_risk(),
    }
    statuses = [v.get("status", "UNKNOWN") for k, v in results.items() if isinstance(v, dict)]
    ok_count = sum(1 for s in statuses if s in ("OK", "FRESH", "SKIP"))
    warn_count = sum(1 for s in statuses if s in ("WARN_SOFT", "STALE", "LOW", "SLOW", "EMPTY", "NO_TS", "WARN"))
    critical_count = sum(1 for s in statuses if s in ("BREACHED", "BREACHED_HARD", "BREACHED_NET", "DOWN", "VIOLATION", "BLOCKED"))
    results["summary"] = {"ok": ok_count, "warn": warn_count, "critical": critical_count, "total": len(statuses)}
    return results


def main():
    log("INFO", f"Starting {GUARD_NAME} v{__version__}")
    tick = 0
    while not _shutdown:
        try:
            tick += 1
            results = run_all_checks()
            r.set(HEALTH_KEY, json.dumps({"ts": time.time(), "tick": tick, "summary": results["summary"], "version": __version__}, default=str), ex=HEALTH_TTL)
            if tick % 10 == 1:
                log("INFO", f"[TICK {tick}] Summary: {results['summary']}")
            if tick % 60 == 1:
                log("INFO", f"[FULL] {json.dumps(results, default=str)}")
        except Exception as e:
            log("ERROR", f"[TICK {tick}] Unhandled error: {e}")
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SEC)
    log("INFO", f"Shutdown complete after {tick} ticks")


def self_test():
    return run_all_checks()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print(json.dumps(self_test(), indent=2, default=str))
    else:
        main()
