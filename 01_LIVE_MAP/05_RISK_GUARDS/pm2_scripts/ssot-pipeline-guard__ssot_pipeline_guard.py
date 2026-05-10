#!/usr/bin/env python3
# [STRUCT-FIX-20260424-READ-STATUS-KEY-HASH-AWARE] applied 2026-04-24T17:18:52.079135+00:00
"""
ssot_pipeline_guard.py — SSOT Pipeline Guard (v4.0.0-hardened)

핵심 목표
- duplicate owner가 생겨도 즉시 종료/재시작 폭풍을 만들지 않고 standby 한다.
- current/canary TTL만 보지 않고 writer/promote의 semantic health를 함께 본다.
- stale source → fresh canary 위장 → current 승격 → guard 정상판정의 마스킹 체인을 끊는다.
- 재시작은 필요한 프로세스에만 제한적으로 수행한다.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

import redis
import requests
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

# [ARES-SSOT 2026-04-24] admin tier Redis client helper
import sys as _sys
_sys.path.insert(0, "/home/ubuntu/scripts")
try:
    from redis_ssot_loader import get_redis_client as _ares_get_redis
except ImportError as _e:
    raise RuntimeError(
        "ARES SSOT loader not available. Ensure /home/ubuntu/scripts/redis_ssot_loader.py exists."
    ) from _e

# MUSK_V5_T1B_APPLIED — shared pipeline health contract reader
import sys as _t1b_sys
try:
    if "/home/ubuntu/aub-trading-system/ops/lib" not in _t1b_sys.path:
        _t1b_sys.path.insert(0, "/home/ubuntu/aub-trading-system/ops/lib")
    from pipeline_health_contract import (
        SSOT_PIPELINE_HEALTH_KEY as _T1B_HEALTH_KEY,
        evaluate_raw as _t1b_evaluate_raw,
        REASON_OK as _T1B_REASON_OK,
    )
    _T1B_AVAILABLE = True
except Exception as _t1b_imp_err:
    _T1B_AVAILABLE = False


try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

GUARD_VERSION = "4.1.0-struct-fix-2026-05-08"
_NY_TZ = ZoneInfo("America/New_York")

REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL env var is required")

GUARD_INTERVAL = max(10, min(300, int(os.getenv("GUARD_INTERVAL_SEC", "60"))))
LOCK_FILE = os.getenv("GUARD_LOCK_FILE", "/tmp/ssot_pipeline_guard.lock")
LOCK_WAIT_SEC = max(int(os.getenv("GUARD_LOCK_WAIT_SEC", "5")), 1)
LOCK_STANDBY_LOG_EVERY = max(int(os.getenv("GUARD_LOCK_STANDBY_LOG_EVERY", "60")), 5)
SOURCE_STALE_SEC = max(int(os.getenv("SSOT_SOURCE_STALE_SEC", "300")), 30)
WRITER_HEALTH_KEY = os.getenv("SSOT_WRITER_HEALTH_KEY", "ssot:writer:v2:health")
WRITER_STATUS_KEY = os.getenv("SSOT_WRITER_STATUS_KEY", "ssot:writer:v2:last_status")
PROMOTE_HEALTH_KEY = os.getenv("SSOT_PROMOTE_HEALTH_KEY", "ssot:promote:v2:health")
PROMOTE_STATUS_KEY = os.getenv("SSOT_PROMOTE_STATUS_KEY", "ssot:promote:v2:last_status")
HEARTBEAT_KEY = os.getenv("SSOT_GUARD_HEARTBEAT_KEY", "ssot:guard:v4:heartbeat")
LAST_ISSUE_KEY = os.getenv("SSOT_GUARD_LAST_ISSUE_KEY", "ssot:guard:v4:last_issue")
EVENT_CHANNEL = os.getenv("SSOT_GUARD_EVENT_CHANNEL", "ares:events:ssot_pipeline")
EVENT_STREAM = os.getenv("SSOT_GUARD_EVENT_STREAM", "ares:audit:ssot_pipeline_guard")
EVENT_STREAM_MAXLEN = max(int(os.getenv("SSOT_GUARD_EVENT_STREAM_MAXLEN", "5000")), 100)

# MUSK_V6_PATCH 2026-05-07: per-issue debounce. A single transient FAILED MUST NOT
# escalate to CRITICAL. Default: require N consecutive failed cycles.
# Counter is Redis-backed so survives guard restarts. Set N=1 (env) to disable.
DEBOUNCE_KEY_PREFIX = os.getenv("SSOT_GUARD_DEBOUNCE_PREFIX", "ares:ssot_guard:debounce:")
DEBOUNCE_TTL_SEC    = max(60, int(os.getenv("SSOT_GUARD_DEBOUNCE_TTL_SEC", "600")))
DEBOUNCE_THRESHOLD_WRITER_FAILED   = max(1, int(os.getenv("SSOT_GUARD_DEBOUNCE_WRITER_FAILED",   "3")))
DEBOUNCE_THRESHOLD_WRITER_DEGRADED = max(1, int(os.getenv("SSOT_GUARD_DEBOUNCE_WRITER_DEGRADED", "3")))
DEBOUNCE_THRESHOLD_PROMOTE_FAILED  = max(1, int(os.getenv("SSOT_GUARD_DEBOUNCE_PROMOTE_FAILED",  "3")))

DEDUPE_KEY_PREFIX = os.getenv("SSOT_GUARD_DEDUP_PREFIX", "ares:ssot_guard:dedup:")
ALERT_DEDUP_SEC = max(int(os.getenv("SSOT_ALERT_DEDUP_SEC", "600")), 60)
GLOBAL_RESTART_COOLDOWN = max(int(os.getenv("GLOBAL_RESTART_COOLDOWN", "900")), 60)
WRITER_STATUS_MAX_AGE = max(int(os.getenv("SSOT_WRITER_STATUS_MAX_AGE", "240")), 60)
PROMOTE_STATUS_MAX_AGE = max(int(os.getenv("SSOT_PROMOTE_STATUS_MAX_AGE", "240")), 60)
CURRENT_LOW_TTL_SEC = max(int(os.getenv("SSOT_GUARD_CURRENT_LOW_TTL_SEC", "120")), 30)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SEVERITY_COOLDOWN = {"CRITICAL": 60, "WARNING": 300, "INFO": 900}
SOURCE_PRIORITY = ["exec_src", "engine_src", "canary_src"]
CRITICAL_PROCESSES = ["ssot-writer-v2", "ssot-promote-v2", "nextgen2-live"]

SSOT_KEYS_META = {
    "exec_src": {"key": "ssot:target:v2:exec_src", "type": "string", "json": True},
    "engine_src": {"key": "ssot:target:v2:engine_src", "type": "string", "json": True},
    "canary_src": {"key": "ssot:target:v2:canary_src", "type": "string", "json": True},
    "canary": {"key": "ssot:target:v2:canary", "type": "string", "json": True},
    "current": {"key": "ssot:target:v2:current", "type": "string", "json": True},
    "legacy_current": {"key": "ssot:current", "type": "string", "json": True},
    "ts": {"key": "ssot:target:v2:ts", "type": "string", "json": False},
    "meta": {"key": "ssot:target:v2:meta", "type": "hash", "json": False},
    "last_good": {"key": "ssot:target:v2:last_good", "type": "string", "json": True},
    "trading_enabled": {"key": "trading:enabled", "type": "string", "json": False},
    # [STRUCT-FIX-2026-05-08] Router-published source_state classifies why
    # exec_src/engine_src may be missing (e.g. HOLDING_NO_GO).
    "source_state": {"key": "ssot:source_state", "type": "string", "json": True},
}
SSOT_KEYS = {k: v["key"] for k, v in SSOT_KEYS_META.items()}

# [STRUCT-FIX-2026-05-08] When the router is in any of these states, an
# absence of exec_src/engine_src is a CONSEQUENCE of upstream policy (validator
# NO_GO without override, source HASH empty, etc.) — not a process failure.
# Guard MUST NOT escalate to CRITICAL or restart nextgen2-live in that case;
# nextgen2-live cannot fix a validator decision.
ROUTER_HOLD_STATES = (
    "HOLDING_NO_GO",
    "HOLDING_FAIL_CLOSED",
    "SOURCE_EMPTY",
    "SOURCE_STALE",
    "TOO_FEW_SYMBOLS",
)

ECOSYSTEM_CANDIDATES = [
    os.getenv("SSOT_GUARD_ECOSYSTEM", ""),
    "/home/ubuntu/ecosystem.config.cjs",
    "/home/ubuntu/aub-trading-system/ecosystem.master.cjs",
    "/home/ubuntu/ARES-KIS-US-AUTOPILOT/ecosystem.master.cjs",
    "/home/ubuntu/ARES-KIS-US-AUTOPILOT/ecosystem.config.cjs",
]

_redis_retry = Retry(ExponentialBackoff(cap=10, base=0.5), retries=5)
r = _ares_get_redis()

_shutdown_requested = False
_guard_lock_fd = None
_alert_cooldown: Dict[str, float] = {}
_per_process_restart_ts: Dict[str, int] = {}


def log(level: str, msg: str, **kwargs: Any) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    extra = json.dumps(kwargs, ensure_ascii=False) if kwargs else ""
    print(f"[{ts}] [{level}] ssot-guard: {msg} {extra}", flush=True)



# MUSK_V6_PATCH 2026-05-07: Redis-backed debounce helpers for transient FAILED states.
def _bump_debounce(issue: str) -> int:
    """Increment a Redis-backed debounce counter for `issue` and return the new value.
    On infra error returns a large number (fail-open: do escalate).
    """
    key = DEBOUNCE_KEY_PREFIX + issue
    try:
        n = r.incr(key)
        r.expire(key, DEBOUNCE_TTL_SEC)
        return int(n)
    except Exception as e:
        log("WARN", f"debounce bump failed for {issue}: {e}")
        return 999


def _reset_debounce(issue: str) -> None:
    key = DEBOUNCE_KEY_PREFIX + issue
    try:
        r.delete(key)
    except Exception:
        pass



def _handle_signal(signum: int, _frame: Any) -> None:
    global _shutdown_requested
    _shutdown_requested = True
    name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    log("INFO", f"Shutdown requested via {name}")


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)



def is_us_market_hours() -> bool:
    now = datetime.now(_NY_TZ)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close



def _safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)



def _now_ms() -> int:
    return int(time.time() * 1000)



def _to_epoch_ms(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            ts = float(v)
        else:
            s = str(v).strip()
            if not s:
                return None
            try:
                ts = float(s)
            except ValueError:
                ts = datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        if ts > 1e12:
            return int(ts)
        return int(ts * 1000)
    except Exception:
        return None



def _status_age_sec(payload: Optional[Dict[str, Any]]) -> Optional[int]:
    if not payload:
        return None
    ts_ms = _to_epoch_ms(payload.get("ts"))
    if not ts_ms:
        return None
    return max(0, int((_now_ms() - ts_ms) / 1000))



def _extract_payload_ts_ms(obj: Any) -> Optional[int]:
    if not isinstance(obj, dict):
        return None
    for key in ("ts", "writer_processed_ts", "source_refreshed_ts"):
        parsed = _to_epoch_ms(obj.get(key))
        if parsed:
            return parsed
    meta = obj.get("targets", {}).get("meta", {}) if isinstance(obj.get("targets"), dict) else {}
    for key in ("source_ts", "writer_processed_ts", "source_refreshed_ts"):
        parsed = _to_epoch_ms(meta.get(key))
        if parsed:
            return parsed
    return None



def _read_json_string_key(key: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], bool, bool]:
    try:
        raw = r.get(key)
    except redis.ResponseError as e:
        if "WRONGTYPE" in str(e):
            log("WARN", "WRONGTYPE on GET", key=key)
            return None, None, False, False
        raise
    if not raw:
        return None, None, False, False
    if isinstance(raw, str) and raw.startswith("auto_init_"):
        return None, raw, False, True
    try:
        obj = json.loads(raw)
        return (obj if isinstance(obj, dict) else None), raw, isinstance(obj, dict), False
    except Exception:
        return None, raw, False, False



def inspect_key(label: str, key: str, expect_json: bool = False) -> Dict[str, Any]:
    try:
        exists = bool(r.exists(key))
    except redis.ResponseError:
        exists = False
    try:
        ttl = r.ttl(key) if exists else -2
    except redis.ResponseError:
        ttl = -2
    raw = None
    obj = None
    valid_json = False
    poisoned = False
    if exists:
        if expect_json:
            obj, raw, valid_json, poisoned = _read_json_string_key(key)
        else:
            try:
                key_type = SSOT_KEYS_META.get(label, {}).get("type", "string")
                if key_type == "hash":
                    obj = r.hgetall(key)
                    raw = _safe_json_dumps(obj) if obj else None
                else:
                    raw = r.get(key)
            except redis.ResponseError:
                raw = None
    payload_ts = _extract_payload_ts_ms(obj) if obj else (_to_epoch_ms(raw) if label == "ts" else None)
    age_s = max(0, int((_now_ms() - payload_ts) / 1000)) if payload_ts else None
    return {
        "label": label,
        "key": key,
        "exists": exists,
        "ttl": ttl,
        "raw": raw,
        "obj": obj,
        "valid_json": valid_json,
        "poisoned": poisoned,
        "payload_ts": payload_ts,
        "age_s": age_s,
    }



def _read_status_key(key: str) -> Optional[Dict[str, Any]]:
    """[STRUCT-FIX-20260424-READ-STATUS-KEY-HASH-AWARE]
    Promote/Writer health 키는 string(JSON) 또는 hash 두 가지 스키마로 운영됨.
    레거시: GET만 시도 -> hash 키는 WRONGTYPE -> None -> 알림에 'UNKNOWN' 표기.
    수정: GET 실패시 TYPE 확인 후 HGETALL 폴백.
    """
    raw = None
    # 1) string GET
    try:
        raw = r.get(key)
    except Exception:
        raw = None
    if raw:
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except Exception:
            # JSON parse 실패 시에도 hash 가능성 한 번 더 시도
            pass
    # 2) hash 폴백 (TYPE -> HGETALL)
    try:
        ktype = r.type(key)
    except Exception:
        ktype = None
    if ktype == "hash":
        try:
            h = r.hgetall(key) or {}
            if not h:
                return None
            # bytes 가 섞여 있을 수 있으므로 str 정규화
            out: Dict[str, Any] = {}
            for k, v in h.items():
                kk = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
                vv = v.decode() if isinstance(v, (bytes, bytearray)) else v
                out[kk] = vv
            return out
        except Exception:
            return None
    return None



def _acquire_lock_wait() -> Any:
    lock_dir = os.path.dirname(LOCK_FILE)
    if lock_dir and not os.path.exists(lock_dir):
        os.makedirs(lock_dir, exist_ok=True)
    fd = open(LOCK_FILE, "a+")
    last_log = 0.0
    while not _shutdown_requested:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd.seek(0)
            fd.truncate()
            fd.write(str(os.getpid()))
            fd.flush()
            log("INFO", "Guard lock acquired", lock=LOCK_FILE)
            return fd
        except OSError:
            now = time.time()
            if now - last_log >= LOCK_STANDBY_LOG_EVERY:
                log("INFO", "Guard standby: another instance owns the lock", lock=LOCK_FILE)
                last_log = now
            time.sleep(LOCK_WAIT_SEC)
    return None



def _release_lock() -> None:
    global _guard_lock_fd
    if _guard_lock_fd is None:
        return
    try:
        fcntl.flock(_guard_lock_fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        _guard_lock_fd.close()
    except Exception:
        pass
    _guard_lock_fd = None



def get_pm2_status() -> Dict[str, str]:
    try:
        result = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0 or not (result.stdout or "").strip():
            return {}
        processes = json.loads(result.stdout)
        if not isinstance(processes, list):
            return {}
        return {p["name"]: p.get("pm2_env", {}).get("status", "unknown") for p in processes if isinstance(p, dict) and p.get("name")}
    except Exception as e:
        log("WARN", "PM2 status check failed", err=str(e))
        return {}



def resolve_ecosystem_path() -> Optional[str]:
    for path in ECOSYSTEM_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None



def restart_pm2_process(name: str) -> bool:
    now = int(time.time())
    last = _per_process_restart_ts.get(name, 0)
    if now - last < GLOBAL_RESTART_COOLDOWN:
        log("INFO", "Restart skipped due to cooldown", process=name, elapsed=now - last, cooldown=GLOBAL_RESTART_COOLDOWN)
        return True
    try:
        result = subprocess.run(["pm2", "restart", name], capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            _per_process_restart_ts[name] = now
            log("INFO", "PM2 restart OK", process=name)
            return True
        log("ERROR", "PM2 restart failed", process=name, rc=result.returncode, stderr=(result.stderr or "")[:200])
        return False
    except Exception as e:
        log("ERROR", "PM2 restart exception", process=name, err=str(e))
        return False



def start_pm2_process(name: str) -> bool:
    ecosystem = resolve_ecosystem_path()
    if not ecosystem:
        log("ERROR", "ecosystem config not found", process=name)
        return False
    try:
        result = subprocess.run(["pm2", "start", ecosystem, "--only", name], capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            log("INFO", "PM2 start OK", process=name)
            return True
        if "already launched" in (result.stderr or ""):
            return restart_pm2_process(name)
        log("ERROR", "PM2 start failed", process=name, rc=result.returncode, stderr=(result.stderr or "")[:200])
        return False
    except Exception as e:
        log("ERROR", "PM2 start exception", process=name, err=str(e))
        return False



def verify_runtime_redis_source() -> List[str]:
    issues: List[str] = []
    env_file = "/etc/ares/redis.env"
    elasticache_url = None
    try:
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("REDIS_URL="):
                        elasticache_url = line.split("=", 1)[1].strip()
                        break
    except Exception as e:
        return [f"cannot_read:{env_file}:{e}"]
    if not elasticache_url:
        return [f"missing_redis_url_in:{env_file}"]
    current_url = os.environ.get("REDIS_URL", "")
    if current_url and current_url != elasticache_url and ("127.0.0.1" in current_url or "localhost" in current_url):
        issues.append("runtime_redis_points_local_but_env_points_elasticache")
    return issues



def get_source_stage(key_status: Dict[str, Dict[str, Any]], market_open: bool) -> Dict[str, Any]:
    selected: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] = []
    for label in SOURCE_PRIORITY:
        info = key_status[label]
        candidate = {
            "label": label,
            "exists": info["exists"],
            "ttl": info["ttl"],
            "valid_json": info["valid_json"],
            "poisoned": info["poisoned"],
            "age_s": info["age_s"],
        }
        candidates.append(candidate)
        if selected is None and info["exists"] and info["valid_json"] and not info["poisoned"]:
            selected = candidate
    if selected is None:
        return {"state": "MISSING", "selected": None, "age_s": None, "candidates": candidates}
    age_s = selected.get("age_s")
    if age_s is not None and age_s > SOURCE_STALE_SEC:
        state = "STALE" if market_open else "STALE_CLOSED_MARKET"
    else:
        state = "HEALTHY"
    return {"state": state, "selected": selected["label"], "age_s": age_s, "candidates": candidates}



def get_semantic_state(writer_status: Optional[Dict[str, Any]], market_open: bool) -> Dict[str, Any]:
    if not writer_status:
        return {"state": "UNKNOWN", "age_s": None}
    age_s = _status_age_sec(writer_status)
    state = str(writer_status.get("status", "UNKNOWN") or "UNKNOWN")
    if age_s is not None and age_s > WRITER_STATUS_MAX_AGE:
        return {"state": "STALE", "age_s": age_s, **writer_status}
    fallback_active = bool(writer_status.get("source_fallback_active", False))
    parity_status = str(writer_status.get("parity_status", "") or "")
    if state == "FAILED" or parity_status == "FAILED":
        semantic = "FAILED"
    elif state == "DEGRADED" or fallback_active or parity_status == "DEGRADED":
        semantic = "DEGRADED"
    else:
        semantic = "HEALTHY"
    return {"state": semantic, "age_s": age_s, **writer_status}



def get_stage_states(pm2_status: Dict[str, str], key_status: Dict[str, Dict[str, Any]], writer_status: Optional[Dict[str, Any]], promote_status: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    market_open = is_us_market_hours()
    source = get_source_stage(key_status, market_open)
    writer_semantic = get_semantic_state(writer_status, market_open)
    promote_age_s = _status_age_sec(promote_status)
    promote_health_state = "UNKNOWN"
    if promote_status:
        raw = str(promote_status.get("status", "UNKNOWN") or "UNKNOWN")
        if promote_age_s is not None and promote_age_s > PROMOTE_STATUS_MAX_AGE:
            promote_health_state = "STALE"
        elif raw == "FAILED":
            promote_health_state = "FAILED"
        elif raw == "DEGRADED":
            promote_health_state = "DEGRADED"
        else:
            promote_health_state = "HEALTHY"

    canary_healthy = key_status["canary"]["exists"] and key_status["canary"]["valid_json"] and key_status["canary"]["ttl"] > 0
    current_exists = key_status["current"]["exists"] and key_status["current"]["valid_json"]
    current_ttl = key_status["current"]["ttl"]
    if current_exists and current_ttl == -1:
        current_state = "PERSISTENT_NO_TTL"
    elif current_exists and current_ttl != -2:
        current_state = "HEALTHY"
    else:
        current_state = "MISSING"

    if current_state in ("HEALTHY", "PERSISTENT_NO_TTL"):
        consumer_state = "HEALTHY"
    elif market_open:
        consumer_state = "FAILED"
    else:
        consumer_state = "IDLE_CLOSED_MARKET"

    return {
        "market_open": market_open,
        "source": {
            **source,
            "runtime": "HEALTHY" if pm2_status.get("nextgen2-live") == "online" else ("FAILED" if market_open else "IDLE_CLOSED_MARKET"),
        },
        "writer": {
            "pm2": pm2_status.get("ssot-writer-v2", "missing"),
            "semantic": writer_semantic,
            "canary_state": "HEALTHY" if canary_healthy else "MISSING",
        },
        "promote": {
            "pm2": pm2_status.get("ssot-promote-v2", "missing"),
            "health": promote_health_state,
            "age_s": promote_age_s,
        },
        "current": {
            "state": current_state,
            "ttl": current_ttl,
            "age_s": key_status["current"].get("age_s"),
        },
        "consumer": {"state": consumer_state},
    }



def _severity_order(sev: str) -> int:
    return {"INFO": 0, "WARNING": 1, "CRITICAL": 2}.get(sev, 0)



def _stable_issue_hash(severity: str, issues: List[str], stage_state: Dict[str, Any]) -> str:
    payload = {
        "severity": severity,
        "issues": sorted(issues),
        "market_open": stage_state.get("market_open"),
        "source": stage_state.get("source", {}).get("state"),
        "source_selected": stage_state.get("source", {}).get("selected"),
        "writer": stage_state.get("writer", {}).get("semantic", {}).get("state"),
        "promote": stage_state.get("promote", {}).get("health"),
        "current": stage_state.get("current", {}).get("state"),
    }
    return hashlib.sha256(_safe_json_dumps(payload).encode()).hexdigest()[:16]



def _dedup_allowed(sig: str, severity: str) -> bool:
    now = time.time()
    mem_last = _alert_cooldown.get(sig, 0)
    cooldown = max(SEVERITY_COOLDOWN.get(severity, 300), ALERT_DEDUP_SEC)
    if now - mem_last < cooldown:
        return False
    try:
        if r.get(f"{DEDUPE_KEY_PREFIX}{sig}"):
            return False
    except Exception:
        pass
    return True



def _mark_dedup_sent(sig: str, severity: str) -> None:
    cooldown = max(SEVERITY_COOLDOWN.get(severity, 300), ALERT_DEDUP_SEC)
    _alert_cooldown[sig] = time.time()
    try:
        r.set(f"{DEDUPE_KEY_PREFIX}{sig}", str(int(time.time())), ex=cooldown, nx=True)
    except Exception:
        pass



def publish_event(event: Dict[str, Any]) -> None:
    payload = _safe_json_dumps(event)
    try:
        r.publish(EVENT_CHANNEL, payload)
    except Exception:
        pass
    try:
        r.xadd(EVENT_STREAM, {"event": payload, "ts": str(_now_ms())}, maxlen=EVENT_STREAM_MAXLEN, approximate=True)
    except Exception:
        pass



def send_telegram(msg: str, severity: str = "WARNING", dedup_sig: Optional[str] = None) -> bool:
    if dedup_sig and not _dedup_allowed(dedup_sig, severity):
        return False
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    emoji = {"CRITICAL": "🔴", "WARNING": "⚠️", "INFO": "ℹ️"}.get(severity, "📌")
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": f"{emoji} [{severity}] {msg}"},
            timeout=10,
        )
        if resp.status_code == 200:
            if dedup_sig:
                _mark_dedup_sent(dedup_sig, severity)
            return True
    except Exception:
        pass
    return False



def write_guard_state(stage_state: Dict[str, Any], key_status: Dict[str, Dict[str, Any]], issues: List[str], severity: str) -> None:
    payload = {
        "ts": _now_ms(),
        "version": GUARD_VERSION,
        "stage_state": stage_state,
        "issues": issues,
        "severity": severity,
        "keys": {
            k: {
                "exists": v["exists"],
                "ttl": v["ttl"],
                "valid_json": v.get("valid_json"),
                "age_s": v.get("age_s"),
            }
            for k, v in key_status.items()
            if k in ("exec_src", "engine_src", "canary_src", "canary", "current", "legacy_current", "ts")
        },
        "pid": os.getpid(),
    }
    try:
        r.set(HEARTBEAT_KEY, _safe_json_dumps(payload), ex=max(180, GUARD_INTERVAL * 4))
    except Exception as e:
        log("WARN", "Failed to write guard heartbeat", err=str(e))



def _read_router_source_state() -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """[STRUCT-FIX-2026-05-08] Return (state, raw_payload) of ssot:source_state.

    None when the key is absent. The router writes this key with TTL
    HEARTBEAT_TTL (~120s); a missing key therefore means the router itself
    is not heartbeating and a real incident is in progress.
    """
    try:
        raw = r.get("ssot:source_state")
    except Exception:
        return None, None
    if not raw:
        return None, None
    try:
        d = json.loads(raw)
    except Exception:
        return None, None
    if not isinstance(d, dict):
        return None, None
    return str(d.get("state") or ""), d


def _read_validator_decision() -> Optional[str]:
    """[STRUCT-FIX-2026-05-08] Return latest standard validator decision.

    Used to dedupe "router silent because of NO_GO" from a real outage.
    """
    try:
        raw = r.get("policy:validator:latest")
    except Exception:
        return None
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    return str(d.get("decision") or "").upper() or None


def check_and_heal() -> None:
    issues: List[str] = []
    actions: List[str] = []
    max_severity = "INFO"

    def escalate(sev: str) -> None:
        nonlocal max_severity
        if _severity_order(sev) > _severity_order(max_severity):
            max_severity = sev

    key_status: Dict[str, Dict[str, Any]] = {}
    for label, key in SSOT_KEYS.items():
        key_status[label] = inspect_key(label, key, expect_json=label in {"exec_src", "engine_src", "canary_src", "canary", "current", "legacy_current", "last_good"})

    pm2_status = get_pm2_status()
    writer_status = _read_status_key(WRITER_STATUS_KEY) or _read_status_key(WRITER_HEALTH_KEY)
    promote_status = _read_status_key(PROMOTE_STATUS_KEY) or _read_status_key(PROMOTE_HEALTH_KEY)
    stage_state = get_stage_states(pm2_status, key_status, writer_status, promote_status)

    market_open = stage_state["market_open"]
    source_state = stage_state["source"]["state"]
    writer_semantic_state = stage_state["writer"]["semantic"].get("state")
    current_state = stage_state["current"]["state"]
    canary_healthy = stage_state["writer"]["canary_state"] == "HEALTHY"

    for issue in verify_runtime_redis_source():
        issues.append(f"redis_source:{issue}")
        escalate("WARNING")

    if current_state == "PERSISTENT_NO_TTL":
        issues.append("current_key_persistent_no_ttl")
        escalate("WARNING")

    current_ttl = stage_state["current"]["ttl"]
    if isinstance(current_ttl, int) and current_ttl > 0 and current_ttl <= CURRENT_LOW_TTL_SEC and market_open:
        issues.append(f"current_ttl_low:{current_ttl}s")
        escalate("WARNING")

    # [STRUCT-FIX-2026-05-08] Read router-published state once for this cycle.
    router_hold_state, router_state_payload = _read_router_source_state()
    validator_decision = _read_validator_decision()
    upstream_policy_block = (
        router_hold_state in ROUTER_HOLD_STATES
        or validator_decision == "NO_GO"
    )

    # RCA-v7: 장외 시간에는 exec_src/engine_src 미가동이 정상 설계(orchestrator는 CYCLE_SKIP).
    # current + canary 가 살아있으면 파이프라인은 건전한 것이므로 경보를 올리지 않는다.
    if source_state in ("MISSING", "STALE"):
        if not market_open and current_state in ("HEALTHY", "PERSISTENT_NO_TTL"):
            # silence: market-closed 상태에서의 source 부재는 설계된 동작.
            pass
        elif upstream_policy_block:
            # [STRUCT-FIX-2026-05-08] Router is alive and explicitly holding due
            # to upstream validator NO_GO / source-empty. Restart of nextgen2
            # would not help, so demote severity and let the operator-facing
            # alert clearly say WHY publication is paused.
            issues.append(
                f"source_state:{source_state}:router_hold={router_hold_state or 'NA'}:validator={validator_decision or 'NA'}"
            )
            escalate("WARNING")
        else:
            issues.append(f"source_state:{source_state}")
            escalate("CRITICAL" if market_open else "WARNING")

    writer_age = stage_state["writer"]["semantic"].get("age_s")
    if writer_semantic_state == "UNKNOWN":
        issues.append("writer_semantic_unknown")
        escalate("WARNING")
    elif writer_semantic_state == "STALE":
        issues.append(f"writer_status_stale:{writer_age}s")
        escalate("CRITICAL" if market_open else "WARNING")
    elif writer_semantic_state == "FAILED":
        # MUSK_V6_PATCH: debounce transient FAILED -> require N consecutive cycles
        n = _bump_debounce("writer_semantic_failed")
        if n >= DEBOUNCE_THRESHOLD_WRITER_FAILED:
            issues.append(f"writer_semantic_failed[debounced={n}/{DEBOUNCE_THRESHOLD_WRITER_FAILED}]")
            escalate("CRITICAL" if market_open else "WARNING")
        else:
            log("INFO", f"writer_semantic_failed transient (debounce {n}/{DEBOUNCE_THRESHOLD_WRITER_FAILED}); not escalating yet")
    elif writer_semantic_state == "DEGRADED":
        # RCA-v7: writer 의 parity_status=DEGRADED 는 engine_src 부재(장외 cycle skip)의 결과.
        # 장외 + current 정상이면 기능적으로 건전하므로 silence.
        if not market_open and current_state in ("HEALTHY", "PERSISTENT_NO_TTL"):
            _reset_debounce("writer_semantic_failed")
            _reset_debounce("writer_semantic_degraded")
            pass
        else:
            n = _bump_debounce("writer_semantic_degraded")
            if n >= DEBOUNCE_THRESHOLD_WRITER_DEGRADED:
                issues.append(f"writer_semantic_degraded[debounced={n}/{DEBOUNCE_THRESHOLD_WRITER_DEGRADED}]")
                escalate("CRITICAL" if market_open else "WARNING")
            else:
                log("INFO", f"writer_semantic_degraded transient (debounce {n}/{DEBOUNCE_THRESHOLD_WRITER_DEGRADED}); not escalating yet")
    else:
        # MUSK_V6_PATCH: any healthy writer state resets the debounce counters
        _reset_debounce("writer_semantic_failed")
        _reset_debounce("writer_semantic_degraded")

    promote_health = stage_state["promote"]["health"]
    promote_age = stage_state["promote"].get("age_s")
    if promote_health == "STALE":
        issues.append(f"promote_status_stale:{promote_age}s")
        escalate("WARNING")
    elif promote_health == "FAILED":
        # MUSK_V6_PATCH: debounce transient promote FAILED
        n = _bump_debounce("promote_health_failed")
        if n >= DEBOUNCE_THRESHOLD_PROMOTE_FAILED:
            issues.append(f"promote_health_failed[debounced={n}/{DEBOUNCE_THRESHOLD_PROMOTE_FAILED}]")
            escalate("CRITICAL" if market_open and canary_healthy else "WARNING")
        else:
            log("INFO", f"promote_health_failed transient (debounce {n}/{DEBOUNCE_THRESHOLD_PROMOTE_FAILED}); not escalating yet")
    elif promote_health == "DEGRADED":
        issues.append("promote_health_degraded")
        escalate("WARNING")
    else:
        # MUSK_V6_PATCH: any healthy promote state resets debounce
        _reset_debounce("promote_health_failed")

    source_runtime = stage_state["source"].get("runtime")
    exec_src_exists = key_status["exec_src"]["exists"] and key_status["exec_src"]["valid_json"]
    if market_open and source_runtime == "FAILED":
        issues.append("nextgen2_live_runtime_failed")
        escalate("CRITICAL")
        ok = restart_pm2_process("nextgen2-live") if pm2_status.get("nextgen2-live") == "online" else start_pm2_process("nextgen2-live")
        actions.append(f"nextgen2-live_recover->{'OK' if ok else 'FAIL'}")
    elif market_open and not exec_src_exists and pm2_status.get("nextgen2-live") == "online":
        if upstream_policy_block:
            # [STRUCT-FIX-2026-05-08] DO NOT restart nextgen2-live here. The
            # exec_src is missing because the upstream router is intentionally
            # holding (validator NO_GO, signed-override absent, or source HASH
            # empty). Restarting the consumer cannot fix the producer's
            # decision; previously this caused a 14-restart/min flap loop on
            # execution-reconciler downstream of nextgen2.
            issues.append(
                f"exec_src_missing_upstream_held:{router_hold_state or 'NA'}:validator={validator_decision or 'NA'}"
            )
            escalate("WARNING")
            actions.append("nextgen2-live_restart_SUPPRESSED:upstream_policy_block")
        else:
            issues.append("exec_src_missing_while_nextgen_online")
            escalate("CRITICAL")
            ok = restart_pm2_process("nextgen2-live")
            actions.append(f"nextgen2-live_restart_for_missing_exec_src->{'OK' if ok else 'FAIL'}")

    writer_pm2 = pm2_status.get("ssot-writer-v2", "missing")
    if writer_pm2 != "online":
        issues.append(f"writer_pm2:{writer_pm2}")
        escalate("CRITICAL" if source_state == "HEALTHY" and not canary_healthy else "WARNING")
        if source_state == "HEALTHY" and not canary_healthy:
            ok = restart_pm2_process("ssot-writer-v2") if writer_pm2 in ("stopped", "errored", "online") else start_pm2_process("ssot-writer-v2")
            actions.append(f"writer_recover->{'OK' if ok else 'FAIL'}")

    promote_pm2 = pm2_status.get("ssot-promote-v2", "missing")
    if promote_pm2 != "online":
        issues.append(f"promote_pm2:{promote_pm2}")
        escalate("CRITICAL" if canary_healthy and current_state == "MISSING" and market_open else "WARNING")
        if canary_healthy and current_state == "MISSING" and market_open:
            ok = restart_pm2_process("ssot-promote-v2") if promote_pm2 in ("stopped", "errored", "online") else start_pm2_process("ssot-promote-v2")
            actions.append(f"promote_recover->{'OK' if ok else 'FAIL'}")

    if current_state == "MISSING":
        issues.append("current_missing")
        escalate("CRITICAL" if market_open else "WARNING")
    if market_open and canary_healthy and current_state == "MISSING" and promote_pm2 == "online":
        ok = restart_pm2_process("ssot-promote-v2")
        actions.append(f"promote_restart_for_missing_current->{'OK' if ok else 'FAIL'}")


    # MUSK_V5_T1B_APPLIED — shared pipeline-health gate (RCA-v7: 장외 + current 정상 시 silence)
    if _T1B_AVAILABLE:
        try:
            _t1b_raw = r.get(_T1B_HEALTH_KEY)
            _t1b_eval = _t1b_evaluate_raw(_t1b_raw, _now_ms())
            if _t1b_eval["is_stale"]:
                # Writer가 contract와 다른 스키마로 쓰는 레거시 상황에서도, 장외 + current 정상이면 기능적 건전.
                if not market_open and current_state in ("HEALTHY", "PERSISTENT_NO_TTL"):
                    pass
                else:
                    issues.append(f"pipeline_health:{_t1b_eval['reason']}")
                    escalate("CRITICAL" if market_open else "WARNING")
        except Exception as _t1b_err:
            if not (not market_open and current_state in ("HEALTHY", "PERSISTENT_NO_TTL")):
                issues.append(f"pipeline_health:contract_error:{type(_t1b_err).__name__}")
                escalate("WARNING")
    write_guard_state(stage_state, key_status, issues, max_severity)

    if issues:
        dedup_sig = _stable_issue_hash(max_severity, issues, stage_state)
        event = {
            "event_type": "ssot_pipeline_issue",
            "component": "ssot-pipeline-guard",
            "severity": max_severity,
            "issue_hash": dedup_sig,
            "issues": issues,
            "actions": actions,
            "stage_state": stage_state,
            "ts": _now_ms(),
        }
        try:
            r.set(LAST_ISSUE_KEY, _safe_json_dumps(event), ex=max(180, GUARD_INTERVAL * 4))
        except Exception:
            pass
        publish_event(event)
        msg = (
            f"[SSOT-GUARD] Pipeline Issue\n"
            f"Severity: {max_severity}\n"
            f"Issues: {'; '.join(issues)}\n"
            f"Actions: {'; '.join(actions) if actions else 'none'}\n"
            f"Source={stage_state['source'].get('state')}({stage_state['source'].get('selected')}) runtime={stage_state['source'].get('runtime')}\n"
            f"Writer={writer_semantic_state}/{writer_pm2} Promote={promote_health}/{promote_pm2} Current={current_state}(ttl={current_ttl})"
        )
        log("WARN", msg)
        send_telegram(msg, severity=max_severity, dedup_sig=dedup_sig)
    else:
        log(
            "INFO",
            f"Pipeline OK | current={current_state}(ttl={current_ttl}) | canary={stage_state['writer']['canary_state']} | source={stage_state['source'].get('selected')}:{stage_state['source'].get('state')} | writer={writer_semantic_state} | promote={promote_health} | market_open={market_open}",
        )



def main() -> None:
    global _guard_lock_fd
    _guard_lock_fd = _acquire_lock_wait()
    if _guard_lock_fd is None:
        return

    log(
        "INFO",
        f"Starting SSOT Pipeline Guard v{GUARD_VERSION}",
        interval=GUARD_INTERVAL,
        source_stale_sec=SOURCE_STALE_SEC,
        writer_status_key=WRITER_STATUS_KEY,
        promote_status_key=PROMOTE_STATUS_KEY,
        ecosystem=resolve_ecosystem_path(),
    )

    while not _shutdown_requested:
        try:
            check_and_heal()
        except Exception as e:
            log("ERROR", "Guard cycle error", err=str(e))
        for _ in range(GUARD_INTERVAL):
            if _shutdown_requested:
                break
            time.sleep(1)

    _release_lock()


if __name__ == "__main__":
    main()
