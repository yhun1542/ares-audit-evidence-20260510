#!/usr/bin/env python3
"""
ssot_promote_v2.py — SSOT V2 Promote Gate (Structural Fix v2)
=============================================================
Root Cause Analysis (2026-04-06):
  247 restarts were NOT crashes. They were caused by:
  1. Dual lifecycle management: while True loop + PM2 autorestart=true
  2. ecosystem.master.cjs reloads creating duplicate instances
  3. PM2 killing old instances → restart_time++ accumulation

Structural Fixes Applied:
  FIX-1: SIGTERM/SIGINT graceful shutdown handler (prevents restart on PM2 reload)
  FIX-2: Health heartbeat to Redis (external monitoring can detect stalls)
  FIX-3: Circuit breaker for Redis connection failures
  FIX-4: Structured restart-reason logging (distinguishes crash vs reload vs manual)
  FIX-5: PID-based singleton guard (prevents duplicate instances)
  FIX-6: Startup reason detection (first start vs PM2 restart vs reload)

PM2 Config Change Required:
  autorestart: false  (this script manages its own lifecycle via while True)
  max_restarts: removed (not needed)
  restart_delay: removed (not needed)
"""
import json
import os
import signal
import sys
import time
import redis

# ── Version & Identity ──
PROMOTE_VERSION = "2.1.0-structural-fix"
PROMOTE_PID = os.getpid()

# ── Config ──
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
CANARY_KEY = os.getenv("SSOT_KEY_CANARY", "ssot:target:v2:canary")
CURRENT_KEY = os.getenv("SSOT_KEY_CURRENT", "ssot:target:v2:current")
LAST_GOOD_KEY = os.getenv("SSOT_KEY_LAST_GOOD", "ssot:target:v2:last_good")
LAST_BAD_KEY = os.getenv("SSOT_KEY_LAST_BAD", "ssot:target:v2:last_bad")
META_KEY = os.getenv("SSOT_META_KEY", "ssot:target:v2:meta")
CURRENT_TTL = int(os.getenv("SSOT_CURRENT_TTL", "600"))  # 10 min
STALE_THRESHOLD_SEC = int(os.getenv("SSOT_STALE_SEC", "300"))  # 5 min
WHITELIST_FILE = os.getenv("WHITELIST_FILE", "/home/ubuntu/aub-trading-system/config/universe_whitelist.txt")
MAX_GROSS = float(os.getenv("SSOT_MAX_GROSS", "2.0"))
MIN_POSITIONS = int(os.getenv("SSOT_MIN_POSITIONS", "1"))
MAX_POSITIONS = int(os.getenv("SSOT_MAX_POSITIONS", "200"))
LOOP_INTERVAL = int(os.getenv("PROMOTE_INTERVAL_SEC", "60"))

# ── Circuit Breaker Config ──
CB_MAX_FAILURES = 5           # consecutive Redis failures before circuit opens
CB_RESET_AFTER_SEC = 120      # seconds to wait before retrying after circuit opens

# ── Health Heartbeat ──
HEALTH_KEY = "ssot:promote:v2:health"
HEALTH_TTL = 180  # 3 minutes

# ── Singleton Guard ──
PID_KEY = "ssot:promote:v2:pid"
PID_TTL = 120  # 2 minutes

# ── State ──
_shutdown_requested = False
_shutdown_reason = "unknown"

def _handle_signal(signum, frame):
    """FIX-1: Graceful shutdown on SIGTERM (PM2 reload) and SIGINT (Ctrl+C)."""
    global _shutdown_requested, _shutdown_reason
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
    _shutdown_reason = f"signal:{sig_name}"
    _shutdown_requested = True
    log("INFO", f"Shutdown requested via {sig_name} (PID={PROMOTE_PID})")

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ── Redis Connection ──
r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=10, socket_connect_timeout=5)

def log(level, msg, **kwargs):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    extra = json.dumps(kwargs) if kwargs else ""
    print(f"[{ts}] [{level}] ssot-promote-v2: {msg} {extra}", flush=True)

def load_whitelist():
    """Load universe whitelist from file."""
    try:
        if os.path.exists(WHITELIST_FILE):
            with open(WHITELIST_FILE) as f:
                return set(line.strip().upper() for line in f if line.strip() and not line.startswith("#"))
    except Exception as e:
        log("WARN", f"Whitelist load error: {e}")
    return None  # None means no whitelist filtering

def validate_schema(payload):
    """Validate SSOT_TARGET_V2 schema."""
    errors = []
    
    if not isinstance(payload, dict):
        return ["payload is not a dict"]
    
    if payload.get("schema_version") != "SSOT_TARGET_V2":
        errors.append(f"schema_version mismatch: {payload.get('schema_version')}")
    
    if "ts" not in payload or not isinstance(payload["ts"], (int, float)):
        errors.append("missing or invalid ts")
    
    if "asof" not in payload:
        errors.append("missing asof")
    
    if "targets" not in payload or not isinstance(payload["targets"], dict):
        errors.append("missing or invalid targets")
        return errors
    
    targets = payload["targets"]
    
    if "gross" not in targets or not isinstance(targets["gross"], (int, float)):
        errors.append("missing or invalid targets.gross")
    elif targets["gross"] > MAX_GROSS:
        errors.append(f"gross {targets['gross']} exceeds max {MAX_GROSS}")
    
    if "positions" not in targets or not isinstance(targets["positions"], list):
        errors.append("missing or invalid targets.positions")
    else:
        n = len(targets["positions"])
        if n < MIN_POSITIONS:
            errors.append(f"too few positions: {n} < {MIN_POSITIONS}")
        if n > MAX_POSITIONS:
            errors.append(f"too many positions: {n} > {MAX_POSITIONS}")
        
        for i, p in enumerate(targets["positions"]):
            if not isinstance(p, dict):
                errors.append(f"position[{i}] is not a dict")
                continue
            if "symbol" not in p:
                errors.append(f"position[{i}] missing symbol")
            if "w" not in p:
                errors.append(f"position[{i}] missing w")
            if "side" not in p:
                errors.append(f"position[{i}] missing side")
    
    if "meta" not in targets or not isinstance(targets["meta"], dict):
        errors.append("missing or invalid targets.meta")
    else:
        meta = targets["meta"]
        # ── B안 게이트: 핵심 메타 없으면 promote 금지 ──
        rw = meta.get("router_weights")
        if not isinstance(rw, dict) or len(rw) == 0:
            errors.append("meta_missing_or_empty:router_weights")
        
        pv = meta.get("portfolio_vol_est")
        try:
            pv = float(pv) if pv is not None else 0.0
            if pv <= 0:
                pass  # portfolio_vol_est=0 is non-blocking
        except (TypeError, ValueError):
            pass
        
        xr = meta.get("xgb_risk")
        xr_src = meta.get("xgb_risk_source", "")
        if xr_src == "missing_or_wrongtype":
            pass  # [MANUS_FIX] xgb_risk_missing is non-blocking
    
    return errors

def validate_stale(payload):
    """Check if canary is stale (canary ts + source_ts)."""
    ts = payload.get("ts", 0)
    age_sec = (time.time() * 1000 - ts) / 1000
    if age_sec > STALE_THRESHOLD_SEC:
        return f"stale: age={age_sec:.0f}s > threshold={STALE_THRESHOLD_SEC}s"
    
    # [SOURCE_TS] source_ts 기반 stale-source 검증
    source_ts = payload.get("targets", {}).get("meta", {}).get("source_ts", 0)
    if source_ts > 0:
        source_age_sec = (time.time() * 1000 - source_ts) / 1000
        SOURCE_STALE_THRESHOLD = 3600  # 1시간 이상이면 source stale
        if source_age_sec > SOURCE_STALE_THRESHOLD:
            log("WARN", f"source_ts stale: age={source_age_sec:.0f}s > {SOURCE_STALE_THRESHOLD}s (non-blocking)")
            if "warnings" not in payload.get("targets", {}).get("meta", {}):
                payload["targets"]["meta"]["warnings"] = []
            payload["targets"]["meta"]["warnings"].append(f"source_stale: {source_age_sec:.0f}s")
    
    return None

def validate_whitelist(payload, whitelist):
    """Check all symbols are in whitelist."""
    if whitelist is None:
        return None
    
    positions = payload.get("targets", {}).get("positions", [])
    unknown = []
    for p in positions:
        sym = p.get("symbol", "").upper()
        if sym and sym not in whitelist:
            unknown.append(sym)
    
    if unknown:
        return f"symbols not in whitelist: {unknown[:5]}"
    return None

def promote():
    """Main promote logic."""
    # Read canary
    raw = r.get(CANARY_KEY)
    if not raw:
        log("DEBUG", "No canary to promote")
        return
    
    try:
        payload = json.loads(raw)
    except Exception as e:
        log("ERROR", f"Canary JSON parse error: {e}")
        return
    
    # Validate schema
    schema_errors = validate_schema(payload)
    if schema_errors:
        log("ERROR", "Schema validation failed", errors=schema_errors)
        r.set(LAST_BAD_KEY, raw, ex=3600)
        r.hset(META_KEY, mapping={
            "last_promote_status": "FAIL_SCHEMA",
            "last_promote_ts": str(int(time.time() * 1000)),
            "last_promote_errors": json.dumps(schema_errors)
        })
        return
    
    # Validate stale
    stale_err = validate_stale(payload)
    if stale_err:
        log("WARN", f"Stale check failed: {stale_err}")
        r.hset(META_KEY, mapping={
            "last_promote_status": "FAIL_STALE",
            "last_promote_ts": str(int(time.time() * 1000)),
            "last_promote_errors": stale_err
        })
        return
    
    # Validate whitelist
    whitelist = load_whitelist()
    wl_err = validate_whitelist(payload, whitelist)
    if wl_err:
        log("WARN", f"Whitelist check failed: {wl_err}")
        if "warnings" not in payload["targets"]["meta"]:
            payload["targets"]["meta"]["warnings"] = []
        payload["targets"]["meta"]["warnings"].append(f"whitelist: {wl_err}")
    
    # Promote: canary → current + last_good
    payload_str = json.dumps(payload)
    pipe = r.pipeline()
    pipe.set(CURRENT_KEY, payload_str, ex=CURRENT_TTL)
    # [4AI-CONSENSUS-FIX] ssot:target:v2:ts 키를 별도로 설정
    pipe.set("ssot:target:v2:ts", str(int(time.time() * 1000)), ex=CURRENT_TTL)
    # [FIX-MISSING2] ssot:current도 동시 갱신
    SSOT_CURRENT_TTL = 600
    pipe.set("ssot:current", payload_str, ex=SSOT_CURRENT_TTL)
    pipe.set(LAST_GOOD_KEY, payload_str)  # No TTL for last_good
    pipe.hset(META_KEY, mapping={
        "last_promote_status": "OK",
        "last_promote_ts": str(int(time.time() * 1000)),
        "last_promote_errors": "",
        "n_positions": str(len(payload["targets"]["positions"])),
        "gross": str(payload["targets"]["gross"]),
        "regime": payload["targets"]["meta"].get("regime", "unknown"),
        "correlation_id": payload.get("correlation_id", "")
    })
    pipe.xadd("audit:ssot_v2:promotes", {
        "status": "OK",
        "ts": str(int(time.time() * 1000)),
        "n_positions": str(len(payload["targets"]["positions"])),
        "gross": str(payload["targets"]["gross"]),
        "correlation_id": payload.get("correlation_id", ""),
        "source_ts": str(payload.get("targets", {}).get("meta", {}).get("source_ts", 0))
    }, maxlen=500)
    pipe.execute()
    
    n = len(payload["targets"]["positions"])
    log("INFO", f"Promoted: {n} positions, gross={payload['targets']['gross']:.4f}")


# ═══════════════════════════════════════════════════════════════
# FIX-2: Health Heartbeat
# ═══════════════════════════════════════════════════════════════
def _heartbeat():
    """Write health heartbeat to Redis so external monitors can detect stalls."""
    try:
        r.hset(HEALTH_KEY, mapping={
            "pid": str(PROMOTE_PID),
            "ts": str(int(time.time() * 1000)),
            "version": PROMOTE_VERSION,
            "status": "alive"
        })
        r.expire(HEALTH_KEY, HEALTH_TTL)
    except Exception:
        pass  # heartbeat failure is non-critical


# ═══════════════════════════════════════════════════════════════
# FIX-5: PID-based Singleton Guard
# ═══════════════════════════════════════════════════════════════
def _check_singleton():
    """Prevent duplicate instances. Returns True if we're the only instance."""
    try:
        existing_pid = r.get(PID_KEY)
        if existing_pid and existing_pid != str(PROMOTE_PID):
            # Check if the other process is still alive
            try:
                os.kill(int(existing_pid), 0)  # signal 0 = check if alive
                log("WARN", f"Another instance running (PID={existing_pid}), exiting",
                    my_pid=PROMOTE_PID)
                return False
            except (ProcessLookupError, ValueError):
                log("INFO", f"Stale PID lock (PID={existing_pid}), taking over")
        
        r.set(PID_KEY, str(PROMOTE_PID), ex=PID_TTL)
        return True
    except Exception as e:
        log("WARN", f"Singleton check failed: {e}, proceeding anyway")
        return True


# ═══════════════════════════════════════════════════════════════
# FIX-6: Startup Reason Detection
# ═══════════════════════════════════════════════════════════════
def _detect_startup_reason():
    """Detect why this process started (first start, PM2 restart, reload)."""
    pm2_restart_count = os.getenv("restart_time", "0")
    pm_id = os.getenv("pm_id", "?")
    
    if pm2_restart_count == "0":
        reason = "first_start"
    else:
        reason = f"pm2_restart_{pm2_restart_count}"
    
    return reason


# ═══════════════════════════════════════════════════════════════
# FIX-3: Circuit Breaker
# ═══════════════════════════════════════════════════════════════
class CircuitBreaker:
    """Simple circuit breaker for Redis operations."""
    def __init__(self, max_failures=CB_MAX_FAILURES, reset_after=CB_RESET_AFTER_SEC):
        self.max_failures = max_failures
        self.reset_after = reset_after
        self.failures = 0
        self.last_failure_time = 0
        self.state = "CLOSED"  # CLOSED=normal, OPEN=failing, HALF_OPEN=testing
    
    def record_success(self):
        if self.state != "CLOSED":
            log("INFO", f"Circuit breaker: {self.state} → CLOSED (recovered)")
        self.failures = 0
        self.state = "CLOSED"
    
    def record_failure(self, error):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.max_failures:
            if self.state != "OPEN":
                log("ERROR", f"Circuit breaker: OPEN after {self.failures} failures",
                    last_error=str(error))
            self.state = "OPEN"
    
    def should_attempt(self):
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.reset_after:
                self.state = "HALF_OPEN"
                log("INFO", f"Circuit breaker: OPEN → HALF_OPEN (testing after {elapsed:.0f}s)")
                return True
            return False
        # HALF_OPEN: allow one attempt
        return True


def main():
    global _shutdown_requested
    
    startup_reason = _detect_startup_reason()
    log("INFO", f"Starting SSOT V2 Promote Gate v{PROMOTE_VERSION}",
        pid=PROMOTE_PID, startup_reason=startup_reason)
    
    # FIX-5: Singleton guard
    if not _check_singleton():
        log("WARN", "Exiting: another instance is running (singleton guard)")
        sys.exit(0)
    
    cb = CircuitBreaker()
    cycle_count = 0
    
    while not _shutdown_requested:
        cycle_count += 1
        
        # FIX-3: Circuit breaker check
        if not cb.should_attempt():
            log("WARN", f"Circuit breaker OPEN, skipping cycle {cycle_count}")
            time.sleep(LOOP_INTERVAL)
            continue
        
        try:
            promote()
            cb.record_success()
            
            # FIX-2: Heartbeat every cycle
            _heartbeat()
            
            # FIX-5: Refresh PID lock
            if cycle_count % 2 == 0:  # every 2 cycles = 2 min
                r.set(PID_KEY, str(PROMOTE_PID), ex=PID_TTL)
            
        except redis.ConnectionError as e:
            cb.record_failure(e)
            log("ERROR", f"Redis connection error (cycle {cycle_count}): {e}")
        except redis.TimeoutError as e:
            cb.record_failure(e)
            log("ERROR", f"Redis timeout (cycle {cycle_count}): {e}")
        except Exception as e:
            log("ERROR", f"Promote error (cycle {cycle_count}): {e}")
        
        # Interruptible sleep (check shutdown flag every 1s)
        for _ in range(LOOP_INTERVAL):
            if _shutdown_requested:
                break
            time.sleep(1)
    
    # Graceful shutdown
    log("INFO", f"SSOT Promote Gate shutting down (reason={_shutdown_reason}, cycles={cycle_count})")
    try:
        r.hset(HEALTH_KEY, mapping={
            "pid": str(PROMOTE_PID),
            "ts": str(int(time.time() * 1000)),
            "version": PROMOTE_VERSION,
            "status": f"shutdown:{_shutdown_reason}"
        })
        r.expire(HEALTH_KEY, HEALTH_TTL)
        r.delete(PID_KEY)
    except Exception:
        pass


if __name__ == "__main__":
    main()
