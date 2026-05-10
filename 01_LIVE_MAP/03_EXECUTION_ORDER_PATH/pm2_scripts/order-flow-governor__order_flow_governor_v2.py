"""
Order Flow Governor v2 (OFG) — L8++ Safety Layer
=================================================
[v9.0.0] 2026-04-16 Hotfix: Multi-tier DD structure + baseline integrity

Changes from v8.0.0:
  - 3-tier Session DD: warning(-5%), reduce-only(-4.5% removed, using warning+halt),
    halt(-7.5%)
  - Lifetime DD layer: soft alert(-15%), hard halt(-22%)
  - Deep DD auto-tighten: if lifetime DD < -15%, session halt shrinks to -5%
  - Stale baseline guard: session_start_equity must be today's date, else rebuild
  - Equity 3-source consistency check: ares:equity:total vs ofg:equity:verified vs
    emarkos:v1:budget_usd — if >1% divergence, DD calc paused + reconcile
  - Restart-safe: on startup, fetch broker truth for baseline instead of using
    first-seen equity
  - session_start_equity persisted to Redis for crash recovery
  - Lifetime peak auto-update when current equity > stored peak

Redis Keys (OFG writes):
  ofg:gate             → "OPEN" | "THROTTLE" | "HALT"
  ofg:gate:status      → mirror of ofg:gate for compatibility
  ofg:status           → JSON { state, reason, ts, metrics, regime }
  ofg:session:metrics  → HASH { orders_total, orders_buy, orders_sell, ... }
  ofg:anomaly:log      → STREAM of detected anomalies
  ofg:heartbeat        → timestamp (for watchdog)
  ofg:telemetry        → STREAM of periodic telemetry snapshots
  ofg:regime:current   → "LOW" | "MEDIUM" | "HIGH" (VIX regime)
  ofg:drawdown:current → current session drawdown percentage
  ofg:session_start_equity       → persisted baseline (float)
  ofg:session_start_equity:meta  → JSON provenance
  ofg:lifetime:peak              → lifetime equity peak
  ofg:lifetime:dd                → current lifetime DD ratio

Redis Keys (OFG reads):
  emarkos:v6:order:intent   → order intent stream (monitor only)
  ares:equity:total          → current total equity (primary)
  ofg:equity:verified        → verified equity JSON (secondary)
  emarkos:v1:budget_usd     → budget equity (tertiary, for consistency)
  trade:halt / trading:enabled / policy:kill_switch
  regime:vix:latest          → latest VIX value from realtime-data-feed
"""

import asyncio
import json
import time
import os
import csv
import signal
from urllib.request import urlopen
from datetime import datetime, timezone, time as dt_time, timedelta
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

# [ARES-SSOT 2026-04-24] admin tier Redis client helper
import sys as _sys
_sys.path.insert(0, "/home/ubuntu/scripts")
try:
    from redis_ssot_loader import get_redis_client as _ares_get_redis
except ImportError as _e:
    raise RuntimeError(
        "ARES SSOT loader not available. Ensure /home/ubuntu/scripts/redis_ssot_loader.py exists."
    ) from _e

# Runtime env bootstrap
try:
    from dotenv import load_dotenv
    load_dotenv("/home/ubuntu/ares_releases/2026-02-17/.env")
    load_dotenv("/home/ubuntu/.env")
except Exception:
    pass


# ─── Configuration ───────────────────────────────────────────────
# [3AI FIX 2026-04-28] Use SSOT as primary Redis URL source
try:
    from redis_ssot_loader import get_redis_url
    REDIS_URL = get_redis_url()
except Exception:
    REDIS_URL = os.environ.get('REDIS_URL') or os.environ.get('ARES_REDIS_URL')
if not REDIS_URL:
    raise RuntimeError('REDIS_URL or ARES_REDIS_URL is required (SSOT also failed)')
if 'localhost' in REDIS_URL or '127.0.0.1' in REDIS_URL:
    raise RuntimeError('Local Redis fallback is forbidden in production')
MONITOR_INTERVAL_SEC = 1.0
HEARTBEAT_INTERVAL_SEC = 5.0
TELEMETRY_INTERVAL_SEC = 10.0
LOG_INTERVAL_SEC = 60.0
OFG_IGNORE_STALE_LEGACY_TRADE_HALT = str(os.environ.get('OFG_IGNORE_STALE_LEGACY_TRADE_HALT', 'true')).lower() != 'false'

# ─── [v9.0.0] Multi-Tier DD Thresholds ──────────────────────────
# Session DD (intraday, per-session)
BASE_SESSION_DD_WARNING_PCT = -0.05       # -5% → soft alert, log only
BASE_SESSION_DD_HALT_PCT = -0.075         # -7.5% → hard HALT
BASE_SESSION_DD_THROTTLE_PCT = -0.03      # -3% → THROTTLE (reduce order rate)

# Lifetime DD (peak-to-trough, multi-day)
LIFETIME_DD_SOFT_ALERT_PCT = -0.15        # -15% → alert + auto-tighten session cap
LIFETIME_DD_HARD_HALT_PCT = -0.22         # -22% → hard HALT (near champion MDD -23.13%)

# Deep DD auto-tighten: when lifetime DD < LIFETIME_DD_SOFT_ALERT_PCT,
# session halt is tightened to this value
DEEP_DD_SESSION_HALT_PCT = -0.05          # -5% session halt when in deep DD

# HALT recovery
HALT_RECOVERY_COOLDOWN_SEC = 300          # 5 min cooldown before recovery check
HALT_RECOVERY_DD_THRESHOLD = -0.04        # DD must recover above -4% to exit HALT
HALT_MAX_RECOVERY_ATTEMPTS = 3            # Max recovery attempts per session

# Rapid drawdown (5-min window)
BASE_RAPID_DD_PCT = -0.012                # -1.2% in 5 minutes
RAPID_DD_WINDOW_MIN = 5

# Rate limits
BASE_RATE_PER_SEC = 12
BASE_RATE_PER_MIN = 180
BASE_RATE_PER_SESSION = 12000

BASE_SELL_STORM_THRESHOLD = 12
SELL_STORM_WINDOW_SEC = 60
FLIP_WINDOW_SEC = 120
FLIP_MAX_PER_SYMBOL = 3
CONCENTRATION_MAX_PCT = 0.15

# ─── [v9.0.0] Baseline Integrity ────────────────────────────────
BASELINE_STALE_MAX_AGE_SEC = 86400        # 24 hours — baseline older than this is stale
EQUITY_CONSISTENCY_THRESHOLD = 0.01       # 1% divergence triggers reconcile
EQUITY_RECONCILE_PAUSE_SEC = 30           # Pause DD calc for 30s during reconcile

# === P0-2 (2026-05-07): Tier Transition Auto-Reset ===
# When equity-calculator changes tier (T3_TTTS → T1_FULL_BROKER),
# total can shift abruptly. OFG must rebuild baseline to avoid false session_dd.
TIER_CHANGE_RESET_DRIFT_PCT = float(os.environ.get("TIER_CHANGE_RESET_DRIFT_PCT", "3.0"))  # >=3% drift triggers reset
TIER_CHANGE_SIGNAL_KEY = "ssot:equity:tier_change_signal"
TIER_CHANGE_LAST_SEEN_KEY = "ofg:tier_change:last_processed_ts"

# [ARES-HARDENING 2026-04-30] OFG drawdown equity-source availability.
# OFG must not depend on the deprecated/optional ares:equity:total key only.
# The default allows last broker truth across non-US-session/off-hours gaps while still
# surfacing source age in ofg:equity:verified for monitoring and later tightening.
def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


try:
    OFG_EQUITY_MAX_AGE_SEC = int(os.environ.get("OFG_EQUITY_MAX_AGE_SEC", "172800"))
except (TypeError, ValueError):
    OFG_EQUITY_MAX_AGE_SEC = 172800

# [FIX-2026-05-07 EMERGENCY] Maximum age for baseline equity data (stale guard)
# Prevents 7-day-old data from being used as session_start_equity baseline.
# Originally placed inside the try block above without indentation, causing
# SyntaxError that crashed OFG for >18 minutes. Moved to its own try/except.
try:
    MAX_BASELINE_STALE_SEC = int(os.environ.get("OFG_MAX_BASELINE_STALE_SEC", "86400"))  # 24h
except (TypeError, ValueError):
    MAX_BASELINE_STALE_SEC = 86400

# [ARES-HARDENING 2026-04-30] Dynamic broker-equity freshness SLA.
# Defaults keep off-hours resilience while enforcing tighter freshness during
# live US trading hours. Values are overrideable without code changes.
OFG_EQUITY_SCHEMA_CONTRACT = os.environ.get(
    "OFG_EQUITY_SCHEMA_CONTRACT",
    "ares.authoritative_equity.v42"
)
OFG_EQUITY_SLA_REGULAR_SEC = _env_int("OFG_EQUITY_SLA_REGULAR_SEC", 900)
OFG_EQUITY_SLA_EXTENDED_SEC = _env_int("OFG_EQUITY_SLA_EXTENDED_SEC", 3600)
OFG_EQUITY_SLA_OFFHOURS_SEC = _env_int("OFG_EQUITY_SLA_OFFHOURS_SEC", OFG_EQUITY_MAX_AGE_SEC)
OFG_EQUITY_SLA_HOLIDAY_SEC = _env_int("OFG_EQUITY_SLA_HOLIDAY_SEC", OFG_EQUITY_MAX_AGE_SEC)
OFG_EQUITY_SLA_WEEKEND_SEC = _env_int("OFG_EQUITY_SLA_WEEKEND_SEC", OFG_EQUITY_MAX_AGE_SEC)

# [ARES-HARDENING 2026-04-30] Optional exchange-calendar provider.
# A JSON/CSV calendar file or API response can override built-in regular/holiday
# rules, including NYSE/Nasdaq early-closes such as Thanksgiving Friday and
# selected Christmas Eve sessions. If no provider is configured or available,
# OFG falls back to the deterministic holiday rule set below.
OFG_MARKET_CALENDAR_PATH = os.environ.get(
    "OFG_MARKET_CALENDAR_PATH",
    "/home/ubuntu/ares_current/config/us_equity_calendar.json",
)
OFG_MARKET_CALENDAR_URL = os.environ.get("OFG_MARKET_CALENDAR_URL", "")
OFG_MARKET_CALENDAR_CACHE_SEC = _env_int("OFG_MARKET_CALENDAR_CACHE_SEC", 3600)
OFG_MARKET_CALENDAR_TIMEOUT_SEC = _env_int("OFG_MARKET_CALENDAR_TIMEOUT_SEC", 3)
_OFG_MARKET_CALENDAR_CACHE = {
    "expires_monotonic": 0,
    "sessions": {},
    "source": "builtin_rule",
    "error": None,
}


def _nth_weekday(year, month, weekday, n):
    d = datetime(year, month, 1, tzinfo=timezone.utc).date()
    delta = (weekday - d.weekday()) % 7
    return d + timedelta(days=delta + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    if month == 12:
        d = datetime(year + 1, 1, 1, tzinfo=timezone.utc).date() - timedelta(days=1)
    else:
        d = datetime(year, month + 1, 1, tzinfo=timezone.utc).date() - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _easter_date(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime(year, month, day, tzinfo=timezone.utc).date()


def _us_market_holidays(year):
    holidays = set()
    holidays.add(_observed(datetime(year, 1, 1, tzinfo=timezone.utc).date()))
    holidays.add(_nth_weekday(year, 1, 0, 3))   # Martin Luther King Jr. Day
    holidays.add(_nth_weekday(year, 2, 0, 3))   # Washington's Birthday
    holidays.add(_easter_date(year) - timedelta(days=2))  # Good Friday
    holidays.add(_last_weekday(year, 5, 0))     # Memorial Day
    if year >= 2022:
        holidays.add(_observed(datetime(year, 6, 19, tzinfo=timezone.utc).date()))
    holidays.add(_observed(datetime(year, 7, 4, tzinfo=timezone.utc).date()))
    holidays.add(_nth_weekday(year, 9, 0, 1))   # Labor Day
    holidays.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving Day
    holidays.add(_observed(datetime(year, 12, 25, tzinfo=timezone.utc).date()))
    return holidays


def _ofg_parse_hhmm(value, default):
    if value is None:
        return default
    try:
        if isinstance(value, (int, float)):
            hour = int(value)
            minute = int(round((float(value) - hour) * 60))
            return dt_time(hour, minute)
        raw = str(value).strip()
        if not raw:
            return default
        parts = raw.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return dt_time(hour, minute)
    except Exception:
        return default


def _ofg_normalize_calendar_entry(entry, source):
    if not isinstance(entry, dict):
        return None
    date_raw = entry.get("date") or entry.get("session_date") or entry.get("day")
    if not date_raw:
        return None
    date_key = str(date_raw)[:10]
    raw_status = str(entry.get("status") or entry.get("session") or entry.get("market_status") or "regular").strip().lower()
    open_raw = entry.get("open") or entry.get("open_time") or entry.get("market_open") or entry.get("regular_open")
    close_raw = entry.get("close") or entry.get("close_time") or entry.get("market_close") or entry.get("regular_close")
    is_closed = raw_status in {"closed", "holiday", "market_closed", "no_trading"}
    is_early_close = raw_status in {"early_close", "early-close", "half_day", "half-day", "shortened"}
    if not is_closed and close_raw:
        close_time = _ofg_parse_hhmm(close_raw, dt_time(16, 0))
        is_early_close = is_early_close or close_time < dt_time(16, 0)
    return {
        "date": date_key,
        "status": "closed" if is_closed else ("early_close" if is_early_close else "regular"),
        "open": open_raw or "09:30",
        "close": close_raw or ("13:00" if is_early_close else "16:00"),
        "name": entry.get("name") or entry.get("holiday") or entry.get("label"),
        "source": source,
    }


def _ofg_calendar_sessions_from_json(raw, source):
    payload = json.loads(raw)
    if isinstance(payload, dict):
        if isinstance(payload.get("sessions"), dict):
            rows = []
            for date_key, row in payload["sessions"].items():
                if isinstance(row, dict):
                    rows.append({"date": date_key, **row})
        elif isinstance(payload.get("sessions"), list):
            rows = payload["sessions"]
        elif isinstance(payload.get("calendar"), list):
            rows = payload["calendar"]
        elif isinstance(payload.get("days"), list):
            rows = payload["days"]
        else:
            rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    sessions = {}
    for row in rows:
        normalized = _ofg_normalize_calendar_entry(row, source)
        if normalized:
            sessions[normalized["date"]] = normalized
    return sessions


def _ofg_calendar_sessions_from_csv(raw, source):
    sessions = {}
    for row in csv.DictReader(raw.splitlines()):
        normalized = _ofg_normalize_calendar_entry(row, source)
        if normalized:
            sessions[normalized["date"]] = normalized
    return sessions


def _ofg_load_market_calendar_sessions():
    now_mono = time.monotonic()
    if now_mono < float(_OFG_MARKET_CALENDAR_CACHE.get("expires_monotonic", 0)):
        return _OFG_MARKET_CALENDAR_CACHE

    sessions = {}
    source = "builtin_rule"
    error = None
    try:
        if OFG_MARKET_CALENDAR_PATH and os.path.exists(OFG_MARKET_CALENDAR_PATH):
            with open(OFG_MARKET_CALENDAR_PATH, "r", encoding="utf-8") as fh:
                raw = fh.read()
            source = f"file:{OFG_MARKET_CALENDAR_PATH}"
            sessions = _ofg_calendar_sessions_from_csv(raw, source) if OFG_MARKET_CALENDAR_PATH.lower().endswith(".csv") else _ofg_calendar_sessions_from_json(raw, source)
        elif OFG_MARKET_CALENDAR_URL:
            with urlopen(OFG_MARKET_CALENDAR_URL, timeout=max(1, int(OFG_MARKET_CALENDAR_TIMEOUT_SEC))) as resp:
                raw = resp.read().decode("utf-8")
            source = f"url:{OFG_MARKET_CALENDAR_URL}"
            sessions = _ofg_calendar_sessions_from_csv(raw, source) if OFG_MARKET_CALENDAR_URL.lower().split("?", 1)[0].endswith(".csv") else _ofg_calendar_sessions_from_json(raw, source)
    except Exception as exc:
        sessions = {}
        source = "builtin_rule"
        error = str(exc)

    _OFG_MARKET_CALENDAR_CACHE.update({
        "expires_monotonic": now_mono + max(30, int(OFG_MARKET_CALENDAR_CACHE_SEC)),
        "sessions": sessions,
        "source": source,
        "error": error,
    })
    return _OFG_MARKET_CALENDAR_CACHE


def _ofg_session_from_open_close(et_time, open_time, close_time):
    if dt_time(4, 0) <= et_time < open_time:
        return "PREMARKET", OFG_EQUITY_SLA_EXTENDED_SEC
    if open_time <= et_time < close_time:
        return "REGULAR", OFG_EQUITY_SLA_REGULAR_SEC
    if close_time <= et_time < dt_time(20, 0):
        return "AFTERHOURS", OFG_EQUITY_SLA_EXTENDED_SEC
    return "OFFHOURS", OFG_EQUITY_SLA_OFFHOURS_SEC


def _ofg_equity_sla_policy(now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        ny_tz = ZoneInfo("America/New_York") if ZoneInfo else timezone(timedelta(hours=-5))
        now_et = now_utc.astimezone(ny_tz)
    except Exception:
        now_et = now_utc.astimezone(timezone(timedelta(hours=-5)))

    et_date = now_et.date()
    et_time = now_et.time()
    calendar_cache = _ofg_load_market_calendar_sessions()
    calendar_entry = (calendar_cache.get("sessions") or {}).get(et_date.isoformat())
    calendar_source = calendar_cache.get("source") or "builtin_rule"
    calendar_error = calendar_cache.get("error")
    market_open_et = "09:30"
    market_close_et = "16:00"
    calendar_status = "builtin"
    early_close = False

    if et_date.weekday() >= 5:
        session = "WEEKEND"
        sla = OFG_EQUITY_SLA_WEEKEND_SEC
    elif calendar_entry:
        calendar_status = calendar_entry.get("status") or "regular"
        market_open_et = str(calendar_entry.get("open") or "09:30")
        market_close_et = str(calendar_entry.get("close") or "16:00")
        early_close = calendar_status == "early_close"
        if calendar_status == "closed":
            session = "HOLIDAY"
            sla = OFG_EQUITY_SLA_HOLIDAY_SEC
        else:
            open_time = _ofg_parse_hhmm(market_open_et, dt_time(9, 30))
            close_time = _ofg_parse_hhmm(market_close_et, dt_time(16, 0))
            if close_time <= open_time:
                open_time, close_time = dt_time(9, 30), dt_time(16, 0)
            session, sla = _ofg_session_from_open_close(et_time, open_time, close_time)
    elif et_date in _us_market_holidays(et_date.year):
        session = "HOLIDAY"
        sla = OFG_EQUITY_SLA_HOLIDAY_SEC
    else:
        session, sla = _ofg_session_from_open_close(et_time, dt_time(9, 30), dt_time(16, 0))

    return {
        "schema_contract": OFG_EQUITY_SCHEMA_CONTRACT,
        "market_session": session,
        "sla_sec": int(sla),
        "now_utc": now_utc.isoformat(),
        "now_et": now_et.isoformat(),
        "calendar_source": calendar_source,
        "calendar_status": calendar_status,
        "calendar_error": calendar_error,
        "market_open_et": market_open_et,
        "market_close_et": market_close_et,
        "early_close": bool(early_close),
    }

# ─── REBAL_GATE Reader Compatibility ────────────────────────────
REBAL_STATE_KEY = "nextgen2:rebal_gate:state"
REBAL_REASON_KEY = "nextgen2:rebal_gate:reason"
REBAL_NEXT_TS_KEY = "nextgen2:rebal_gate:next_rebal_ts"
REBAL_OVERRIDE_KEY = "nextgen2:rebal_gate:override"
REBAL_MODE_KEY = "ofg:rebalance_mode"
REBAL_MODE_REASON_KEY = "ofg:rebalance_mode:reason"
REBAL_MODE_UNTIL_KEY = "ofg:rebalance_mode:until"
REBAL_MODE_STATE_KEY = "ofg:rebalance_mode:state"
REBAL_STATUS_KEY = "ofg:rebalance_status"
GATE_TTL_SEC = 15
REBAL_MODE_TTL_SEC = 30
REBAL_ALLOWED_SELL_STORM_THRESHOLD = 120
DEFAULT_REBALANCE_DAYS = 2


# ─── VIX Regime Thresholds ──────────────────────────────────────
VIX_LOW = 15.0
VIX_HIGH = 25.0
VIX_EXTREME = 35.0

# Regime multipliers: [rate_mult, dd_mult]
REGIME_ADJUSTMENTS = {
    "LOW":    {"rate_mult": 1.2, "dd_mult": 1.0},
    "MEDIUM": {"rate_mult": 1.0, "dd_mult": 1.0},
    "HIGH":   {"rate_mult": 0.8, "dd_mult": 0.8},
    "EXTREME": {"rate_mult": 0.5, "dd_mult": 0.6},
}

# ─── US Market Session (ET) ─────────────────────────────────────
MARKET_OPEN_HOUR_UTC = 14
MARKET_OPEN_MIN_UTC = 30
MARKET_CLOSE_HOUR_UTC = 21


class OFGState:
    OPEN = "OPEN"
    THROTTLE = "THROTTLE"
    HALT = "HALT"


class OrderFlowGovernorV2:
    def __init__(self):
        self.redis = None
        self.state = OFGState.OPEN
        self.reason = "initialized"
        self.running = True
        self.vix_regime = "MEDIUM"
        self.current_vix = 0.0
        self.current_session_dd = 0.0
        self.current_lifetime_dd = 0.0
        self.rebalance_mode = False
        self.rebalance_gate_state = "UNKNOWN"
        self.rebalance_gate_reason = "not_checked"
        self.rebalance_next_ts = None
        self.rebalance_source = "none"

        # Effective thresholds (adjusted by regime)
        self.eff_rate_sec = BASE_RATE_PER_SEC
        self.eff_rate_min = BASE_RATE_PER_MIN
        self.eff_rate_session = BASE_RATE_PER_SESSION
        self.eff_dd_halt = BASE_SESSION_DD_HALT_PCT
        self.eff_dd_warning = BASE_SESSION_DD_WARNING_PCT
        self.eff_dd_throttle = BASE_SESSION_DD_THROTTLE_PCT
        self.eff_rapid_dd = BASE_RAPID_DD_PCT
        self.eff_sell_storm_threshold = BASE_SELL_STORM_THRESHOLD

        # HALT recovery tracking
        self.halt_entered_ts = None
        self.halt_recovery_count = 0

        # Session tracking
        self.session_start_equity = None
        self.session_start_ts = None
        self.session_start_date = None    # [v9.0.0] date string for stale check
        self.session_date = None
        self.session_orders = []
        self.equity_history = []
        self.symbol_order_history = {}
        self.orders_this_session = 0

        # [v9.0.0] Lifetime DD tracking
        self.lifetime_peak = None
        self.in_deep_dd = False           # True when lifetime DD < -15%

        # [v9.0.0] Equity reconciliation
        self.equity_reconcile_until = 0   # timestamp until which DD calc is paused
        self.last_equity_consistency_check = 0

        # [ARES-HARDENING 2026-04-30] Equity freshness SLA observability
        self.last_equity_sla_policy = _ofg_equity_sla_policy()
        self.last_equity_candidate = None
        self.last_equity_contract_warnings = []

        # Metrics
        self.metrics = {
            "orders_total": 0, "orders_buy": 0, "orders_sell": 0,
            "throttle_events": 0, "halt_events": 0,
            "halt_recoveries": 0, "halt_recovery_attempts": 0,
            "anomalies_detected": 0, "sell_storms": 0,
            "flip_patterns": 0, "circuit_breaker_trips": 0,
            "rate_limit_hits": 0, "session_resets": 0,
            "baseline_rebuilds": 0, "equity_reconciles": 0,
            "lifetime_halt_events": 0, "deep_dd_tighten_events": 0,
        }

        self.last_stream_id = "0-0"

    # ─── Redis Connection ────────────────────────────────────────

    async def connect_redis(self):
        # [PATCHED 2026-04-24] use aioredis.from_url(REDIS_URL) directly
        # because redis_ssot_loader has no async client helper.
        import redis.asyncio as aioredis
        import ssl as _ssl
        url = REDIS_URL
        kwargs = dict(decode_responses=True, socket_keepalive=True,
                      health_check_interval=30, retry_on_timeout=True,
                      socket_connect_timeout=10, socket_timeout=15)
        if url.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = None
        for attempt in range(10):
            try:
                self.redis = aioredis.from_url(url, **kwargs)
                await self.redis.ping()
                self.log("INFO", "Redis connected successfully (aioredis.from_url)")
                return
            except Exception as e:
                self.log("WARN", f"Redis connect attempt {attempt+1}/10: {e}")
                await asyncio.sleep(2 ** min(attempt, 4))
        raise RuntimeError("Failed to connect to Redis after 10 attempts")

    def log(self, level, message, data=None):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "component": "order-flow-governor",
            "message": message,
        }
        if data:
            entry.update(data)
        print(json.dumps(entry), flush=True)

    def _is_truthy(self, value):
        return str(value or "").strip().lower() in ("1", "true", "yes", "on")

    def _safe_float(self, value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _equity_age_sec(self, ts_value):
        parsed = self._parse_ts(ts_value)
        if not parsed:
            return None
        return max(0.0, time.time() - parsed)

    def _json_equity_value(self, raw, fields):
        if not raw:
            return None, None
        try:
            data = json.loads(raw)
        except Exception:
            return None, None
        for field in fields:
            value = self._safe_float(data.get(field), None)
            if value and value > 0:
                return value, data
        return None, data

    async def _get_current_equity_candidate(self, allow_stale=False):
        """Resolve current/broker-truth equity from all known live keys.

        Returns a provenance-rich dict or None. This intentionally centralizes equity
        source selection so baseline init, current DD, and monitoring all see the same
        source-of-truth decision instead of each depending on a different legacy key.
        """
        candidates = []

        async def add_candidate(source, value, ts=None, raw_age=None, degraded=False,
                                health=None, schema=None, contract_warnings=None):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return
            if value <= 0:
                return
            policy = _ofg_equity_sla_policy()
            age_sec = raw_age if raw_age is not None else self._equity_age_sec(ts)
            sla_sec = int(policy.get("sla_sec", OFG_EQUITY_MAX_AGE_SEC))
            age_ok = age_sec is None or age_sec <= sla_sec
            warnings = list(contract_warnings or [])
            candidate_degraded = bool(degraded or (not age_ok) or warnings)
            candidates.append({
                "value": value,
                "source": source,
                "source_ts": ts,
                "age_sec": age_sec,
                "sla_sec": sla_sec,
                "market_session": policy.get("market_session"),
                "freshness_policy": policy,
                "fresh": bool(age_ok) and not candidate_degraded,
                "degraded": candidate_degraded,
                "source_degraded": bool(degraded),
                "health": health,
                "schema": schema,
                "contract_warnings": warnings,
            })

        # Legacy fast path. It may be absent because the newer truth writer publishes
        # ares:equity:authoritative/equity:broker:* instead.
        try:
            raw = await self.redis.get("ares:equity:total")
            ts = await self.redis.get("ares:equity:total:ts")
            if raw:
                await add_candidate("ares:equity:total", raw, ts=ts)
        except Exception as e:
            self.log("WARN", "EQUITY_SOURCE_READ_FAILED", {"source": "ares:equity:total", "error": str(e)})

        # Primary broker truth writer v42 payload.
        try:
            raw = await self.redis.get("ares:equity:authoritative")
            value, data = self._json_equity_value(raw, ("total", "total_usd", "total_equity_usd"))
            if value:
                auth = data or {}
                health = auth.get("health")
                schema = auth.get("schema") or auth.get("contract") or auth.get("schema_contract")
                warnings = []
                if not schema:
                    warnings.append("authoritative_missing_schema")
                elif str(schema) != OFG_EQUITY_SCHEMA_CONTRACT:
                    warnings.append(f"authoritative_schema_mismatch:{schema}")
                if not auth.get("ts"):
                    warnings.append("authoritative_missing_ts")
                await add_candidate(
                    "ares:equity:authoritative",
                    value,
                    ts=auth.get("ts"),
                    degraded=health not in (None, "OK", "ok", "HEALTHY"),
                    health=health,
                    schema=schema,
                    contract_warnings=warnings,
                )
        except Exception as e:
            self.log("WARN", "EQUITY_SOURCE_READ_FAILED", {"source": "ares:equity:authoritative", "error": str(e)})

        # Broker scalar compatibility key plus its timestamp.
        try:
            raw = await self.redis.get("equity:broker:usd")
            ts = await self.redis.get("equity:broker:ts")
            if raw:
                await add_candidate("equity:broker:usd", raw, ts=ts)
        except Exception as e:
            self.log("WARN", "EQUITY_SOURCE_READ_FAILED", {"source": "equity:broker:usd", "error": str(e)})

        # Component payload contains explicit broker freshness/degraded flags.
        try:
            raw = await self.redis.get("equity:broker:components")
            value, data = self._json_equity_value(raw, ("total_equity_usd", "total_usd", "total"))
            if value:
                await add_candidate(
                    "equity:broker:components",
                    value,
                    ts=(data or {}).get("ts"),
                    degraded=bool((data or {}).get("stale") or (data or {}).get("degraded")),
                )
        except Exception as e:
            self.log("WARN", "EQUITY_SOURCE_READ_FAILED", {"source": "equity:broker:components", "error": str(e)})

        # OFG's own last verified mirror is lowest priority and is only used as a
        # crash-recovery bridge, never as a substitute for fresher broker truth.
        try:
            raw = await self.redis.get("ofg:equity:verified")
            value, data = self._json_equity_value(raw, ("total", "total_usd", "value"))
            if value:
                await add_candidate(
                    "ofg:equity:verified",
                    value,
                    ts=(data or {}).get("source_ts") or (data or {}).get("ts"),
                    degraded=bool((data or {}).get("degraded")),
                )
        except Exception as e:
            self.log("WARN", "EQUITY_SOURCE_READ_FAILED", {"source": "ofg:equity:verified", "error": str(e)})

        fresh = [c for c in candidates if c["fresh"]]
        if fresh:
            chosen = fresh[0]
            self.last_equity_candidate = dict(chosen)
            self.last_equity_sla_policy = chosen.get("freshness_policy") or _ofg_equity_sla_policy()
            self.last_equity_contract_warnings = list(chosen.get("contract_warnings") or [])
            return chosen
        if allow_stale and candidates:
            candidates.sort(key=lambda c: c["age_sec"] if c["age_sec"] is not None else 0)
            chosen = candidates[0]
            chosen["fresh"] = False
            chosen["degraded"] = True
            self.last_equity_candidate = dict(chosen)
            self.last_equity_sla_policy = chosen.get("freshness_policy") or _ofg_equity_sla_policy()
            self.last_equity_contract_warnings = list(chosen.get("contract_warnings") or [])
            return chosen
        self.last_equity_sla_policy = _ofg_equity_sla_policy()
        self.last_equity_candidate = None
        self.last_equity_contract_warnings = ["no_equity_candidate"]
        return None

    async def _publish_verified_equity(self, candidate, purpose):
        if not candidate:
            return
        policy = candidate.get("freshness_policy") or _ofg_equity_sla_policy()
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "purpose": purpose,
            "total": candidate.get("value"),
            "source": candidate.get("source"),
            "source_ts": candidate.get("source_ts"),
            "age_sec": candidate.get("age_sec"),
            "sla_sec": candidate.get("sla_sec") or policy.get("sla_sec"),
            "market_session": candidate.get("market_session") or policy.get("market_session"),
            "freshness_policy": policy,
            "fresh": candidate.get("fresh"),
            "degraded": candidate.get("degraded"),
            "source_degraded": candidate.get("source_degraded"),
            "health": candidate.get("health"),
            "schema": candidate.get("schema"),
            "expected_schema": OFG_EQUITY_SCHEMA_CONTRACT,
            "contract_warnings": candidate.get("contract_warnings") or [],
        }
        try:
            ttl = max(60, min(int(payload.get("sla_sec") or OFG_EQUITY_MAX_AGE_SEC), 3600))
            await self.redis.set("ofg:equity:verified", json.dumps(payload), ex=ttl)
        except Exception as e:
            self.log("WARN", "OFG_EQUITY_VERIFIED_PUBLISH_FAILED", {"error": str(e)})

    async def _inspect_legacy_trade_halt(self):
        raw = await self.redis.get('trade:halt')
        truthy = self._is_truthy(raw)
        if not truthy:
            return raw, False, False, 'not_truthy'
        ttl = await self.redis.ttl('trade:halt')
        reason = await self.redis.get('trade:halt:reason')
        ts = await self.redis.get('trade:halt:ts')
        details = await self.redis.get('trade:halt:details')
        has_metadata = bool(reason or ts or details)
        has_expiry = isinstance(ttl, int) and ttl > 0
        if OFG_IGNORE_STALE_LEGACY_TRADE_HALT and (not has_metadata) and (not has_expiry):
            return raw, False, True, f'stale_legacy_trade_halt_ignored raw={raw} ttl={ttl} metadata=absent'
        return raw, True, False, f'trade_halt_effective raw={raw} ttl={ttl} metadata={"present" if has_metadata else "absent"}'

    def _parse_ts(self, raw):
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        try:
            n = float(s)
            if n > 1e12:
                return n / 1000.0
            if n > 1e9:
                return n
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    # ─── [v9.0.0] Broker Truth Baseline Initialization ──────────

    async def _init_session_baseline(self):
        """Initialize session baseline from broker truth on startup/session reset.
        Priority: 1) same-day persisted baseline, 2) current broker truth resolver,
        3) stale-but-bounded broker truth resolver for crash/off-hours recovery."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now = time.time()

        # Try to recover persisted baseline from Redis (crash recovery)
        try:
            meta_raw = await self.redis.get("ofg:session_start_equity:meta")
            if meta_raw:
                meta = json.loads(meta_raw)
                meta_ts = meta.get("ts", "")
                meta_value = meta.get("value")
                meta_date = meta.get("date", "")

                # Only use if it's from today
                if meta_date == today and meta_value and float(meta_value) > 0:
                    self.session_start_equity = float(meta_value)
                    self.session_start_ts = self._parse_ts(meta_ts) or now
                    self.session_start_date = today
                    self.log("INFO", "BASELINE_RECOVERED_FROM_REDIS", {
                        "equity": self.session_start_equity,
                        "meta_ts": meta_ts,
                        "source": "redis_persisted",
                    })
                    return
                else:
                    self.log("WARN", "BASELINE_STALE_IN_REDIS", {
                        "meta_date": meta_date,
                        "today": today,
                        "meta_value": meta_value,
                        "action": "rebuilding_from_broker_truth",
                    })
        except Exception as e:
            self.log("WARN", f"Baseline recovery from Redis failed: {e}")

        # Fetch fresh equity from broker truth
        equity = await self._get_verified_equity()
        if equity and equity > 0:
            self.session_start_equity = equity
            self.session_start_ts = now
            self.session_start_date = today
            await self._persist_baseline(equity, "broker_truth_init")
            self.log("INFO", "BASELINE_SET_FROM_BROKER_TRUTH", {
                "equity": equity,
                "source": "startup_init",
            })
        else:
            self.log("WARN", "BASELINE_INIT_FAILED: no valid equity source available")

    async def _get_verified_equity(self):
        """Get baseline-capable equity from the centralized broker truth resolver."""
        candidate = await self._get_current_equity_candidate(allow_stale=True)
        if candidate:
            await self._publish_verified_equity(candidate, "baseline")
            if not candidate.get("fresh"):
                age_sec = candidate.get("age_sec")
                # [FIX-2026-05-06] Reject baseline data older than MAX_BASELINE_STALE_SEC (24h)
                # Prevents stale equity (e.g., 7-day-old data) from corrupting session_start_equity
                if age_sec is not None and age_sec > MAX_BASELINE_STALE_SEC:
                    self.log("WARN", "BASELINE_REJECTED_TOO_STALE", {
                        "source": candidate.get("source"),
                        "age_sec": age_sec,
                        "max_allowed_sec": MAX_BASELINE_STALE_SEC,
                        "equity": candidate.get("value"),
                        "action": "rejecting_stale_baseline",
                    })
                    return None
                self.log("WARN", "BASELINE_USING_STALE_BROKER_TRUTH", {
                    "source": candidate.get("source"),
                    "age_sec": age_sec,
                    "equity": candidate.get("value"),
                    "sla_sec": candidate.get("sla_sec"),
                    "market_session": candidate.get("market_session"),
                    "contract_warnings": candidate.get("contract_warnings"),
                })
            return candidate.get("value")
        return None

    async def _persist_baseline(self, equity, source):
        """Persist session baseline to Redis for crash recovery."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        meta = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "date": today,
            "source": source,
            "value": equity,
        }
        try:
            pipe = self.redis.pipeline()
            pipe.set("ofg:session_start_equity", str(equity))
            pipe.set("ofg:session:start_equity", str(equity))
            pipe.set("ofg:session_start_equity:meta", json.dumps(meta))
            await pipe.execute()
        except Exception as e:
            self.log("WARN", f"Failed to persist baseline: {e}")

    # ─── [v9.0.0] Baseline Freshness Guard ──────────────────────

    async def _check_baseline_freshness(self):
        """Check if session baseline is stale (wrong date or too old).
        If stale, rebuild from broker truth instead of triggering HALT."""
        if self.session_start_equity is None:
            return  # Will be initialized in circuit breaker

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Check 1: Date mismatch
        if self.session_start_date and self.session_start_date != today:
            self.log("WARN", "BASELINE_STALE_DATE_MISMATCH", {
                "baseline_date": self.session_start_date,
                "today": today,
                "action": "rebuilding",
            })
            self.metrics["baseline_rebuilds"] += 1
            await self._rebuild_baseline("date_mismatch")
            return

        # Check 2: Age too old (>24h)
        if self.session_start_ts:
            age = time.time() - self.session_start_ts
            if age > BASELINE_STALE_MAX_AGE_SEC:
                self.log("WARN", "BASELINE_STALE_AGE", {
                    "age_hours": round(age / 3600, 1),
                    "max_hours": BASELINE_STALE_MAX_AGE_SEC / 3600,
                    "action": "rebuilding",
                })
                self.metrics["baseline_rebuilds"] += 1
                await self._rebuild_baseline("age_exceeded")

    async def _rebuild_baseline(self, reason):
        """Rebuild session baseline from current broker truth."""
        equity = await self._get_verified_equity()
        if equity and equity > 0:
            old = self.session_start_equity
            self.session_start_equity = equity
            self.session_start_ts = time.time()
            self.session_start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self.current_session_dd = 0.0
            await self._persist_baseline(equity, f"rebuild:{reason}")
            self.log("INFO", "BASELINE_REBUILT", {
                "old_equity": old,
                "new_equity": equity,
                "reason": reason,
            })
        else:
            self.log("ERROR", "BASELINE_REBUILD_FAILED: no valid equity", {
                "reason": reason,
            })

    # === P0-2 (2026-05-07): Tier Transition Auto-Reset =============
    async def _check_tier_change_signal(self, current_equity):
        """
        Check if equity-calculator emitted a tier-change signal
        (ssot:equity:tier_change_signal). If so, and drift exceeds
        TIER_CHANGE_RESET_DRIFT_PCT, rebuild session_baseline so that
        OFG does not compute false session_dd from a stale baseline.

        Idempotent: stores the processed signal ts in Redis so the
        same signal is processed at most once even if the OFG main
        loop sees it twice before TTL expiry.
        """
        try:
            raw = await self.redis.get(TIER_CHANGE_SIGNAL_KEY)
            if not raw:
                return False

            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as e:
                self.log("WARN", "TIER_CHANGE_SIGNAL_PARSE_FAILED", {"error": str(e)})
                return False

            signal_ts = payload.get("ts") or payload.get("produced_at")
            if not signal_ts:
                return False

            # Idempotency guard: skip if already processed this signal
            last_processed = await self.redis.get(TIER_CHANGE_LAST_SEEN_KEY)
            if last_processed == signal_ts:
                return False

            drift_pct = float(payload.get("drift_pct", 0.0))
            from_tier = payload.get("from") or "UNKNOWN"
            to_tier = payload.get("to") or "UNKNOWN"
            total_before = payload.get("total_before")
            total_after = payload.get("total_after")

            self.log("INFO", "TIER_CHANGE_SIGNAL_RECEIVED", {
                "from": from_tier,
                "to": to_tier,
                "drift_pct": drift_pct,
                "total_before": total_before,
                "total_after": total_after,
                "signal_ts": signal_ts,
                "reset_threshold_pct": TIER_CHANGE_RESET_DRIFT_PCT,
            })

            # Mark as processed BEFORE doing the action (prevents duplicate processing
            # under race condition with multiple OFG ticks)
            await self.redis.set(TIER_CHANGE_LAST_SEEN_KEY, signal_ts, ex=86400)

            if drift_pct >= TIER_CHANGE_RESET_DRIFT_PCT:
                self.metrics["baseline_rebuilds"] += 1
                reason = f"tier_change:{from_tier}->{to_tier}:drift={drift_pct:.2f}%"
                self.log("WARN", "TIER_CHANGE_BASELINE_RESET", {
                    "reason": reason,
                    "drift_pct": drift_pct,
                    "old_baseline": self.session_start_equity,
                    "new_baseline_target": current_equity,
                })
                # Rebuild from current verified equity (NOT from signal payload
                # because signal could be slightly stale; current_equity is fresher)
                await self._rebuild_baseline(reason)
                return True
            else:
                self.log("INFO", "TIER_CHANGE_DRIFT_BELOW_THRESHOLD", {
                    "drift_pct": drift_pct,
                    "threshold_pct": TIER_CHANGE_RESET_DRIFT_PCT,
                    "action": "baseline_unchanged",
                })
                return False
        except Exception as e:
            # Defensive: never let signal-check failures break the OFG main loop
            self.log("ERROR", "TIER_CHANGE_SIGNAL_CHECK_FAILED", {"error": str(e)})
            return False

    # ─── [v9.0.0] Equity Consistency Check ──────────────────────

    async def _check_equity_consistency(self):
        """Compare equity from multiple sources. If divergence > 1%, pause DD calc."""
        now = time.time()
        if now - self.last_equity_consistency_check < 60:  # Check every 60s
            return True  # Assume OK between checks

        self.last_equity_consistency_check = now
        sources = {}

        # Source 1: ares:equity:total
        try:
            raw = await self.redis.get("ares:equity:total")
            if raw:
                sources["equity_total"] = float(raw)
        except Exception:
            pass

        # Source 2: ofg:equity:verified
        try:
            raw = await self.redis.get("ofg:equity:verified")
            if raw:
                data = json.loads(raw)
                val = float(data.get("total", 0))
                if val > 0:
                    sources["equity_verified"] = val
        except Exception:
            pass

        if len(sources) < 2:
            return True  # Not enough sources to compare

        values = list(sources.values())
        max_val = max(values)
        min_val = min(values)

        if max_val == 0:
            return True

        divergence = (max_val - min_val) / max_val

        if divergence > EQUITY_CONSISTENCY_THRESHOLD:
            self.log("WARN", "EQUITY_CONSISTENCY_BREACH", {
                "sources": sources,
                "divergence_pct": f"{divergence:.2%}",
                "threshold": f"{EQUITY_CONSISTENCY_THRESHOLD:.2%}",
                "action": "pausing_dd_calc_for_reconcile",
            })
            self.equity_reconcile_until = now + EQUITY_RECONCILE_PAUSE_SEC
            self.metrics["equity_reconciles"] += 1
            return False

        return True

    # ─── [v9.0.0] Lifetime DD Tracking ──────────────────────────

    async def _update_lifetime_dd(self, current_equity):
        """Track lifetime peak and DD. Auto-tighten session cap in deep DD."""
        if current_equity <= 0:
            return

        # Load lifetime peak from Redis if not in memory
        if self.lifetime_peak is None:
            try:
                raw = await self.redis.get("ofg:lifetime:peak")
                if raw:
                    self.lifetime_peak = float(raw)
            except Exception:
                pass

        # Also check watchdog's peak
        if self.lifetime_peak is None:
            try:
                raw = await self.redis.get("ares:watchdog:last_check")
                if raw:
                    data = json.loads(raw)
                    peak = data.get("lifetimePeak")
                    if peak and float(peak) > 0:
                        self.lifetime_peak = float(peak)
            except Exception:
                pass

        if self.lifetime_peak is None:
            self.lifetime_peak = current_equity

        # Update peak if current equity exceeds it
        if current_equity > self.lifetime_peak:
            old_peak = self.lifetime_peak
            self.lifetime_peak = current_equity
            self.log("INFO", "LIFETIME_PEAK_UPDATED", {
                "old_peak": old_peak,
                "new_peak": current_equity,
            })

        # Calculate lifetime DD
        self.current_lifetime_dd = (current_equity - self.lifetime_peak) / self.lifetime_peak

        # Persist to Redis
        try:
            pipe = self.redis.pipeline()
            pipe.set("ofg:lifetime:peak", str(self.lifetime_peak))
            pipe.set("ofg:lifetime:dd", f"{self.current_lifetime_dd:.6f}")
            await pipe.execute()
        except Exception:
            pass

        # Deep DD auto-tighten
        was_deep = self.in_deep_dd
        self.in_deep_dd = self.current_lifetime_dd <= LIFETIME_DD_SOFT_ALERT_PCT

        if self.in_deep_dd and not was_deep:
            self.metrics["deep_dd_tighten_events"] += 1
            self.log("WARN", "DEEP_DD_ENTERED: auto-tightening session halt", {
                "lifetime_dd": f"{self.current_lifetime_dd:.2%}",
                "threshold": f"{LIFETIME_DD_SOFT_ALERT_PCT:.2%}",
                "session_halt_old": f"{self.eff_dd_halt:.2%}",
                "session_halt_new": f"{DEEP_DD_SESSION_HALT_PCT:.2%}",
            })

        if not self.in_deep_dd and was_deep:
            self.log("INFO", "DEEP_DD_EXITED: restoring normal session halt", {
                "lifetime_dd": f"{self.current_lifetime_dd:.2%}",
            })

    # ─── Rebalance Gate Sync ────────────────────────────────────

    async def _load_rebalance_policy(self):
        policy = {
            "rebalance_days": DEFAULT_REBALANCE_DAYS,
            "enable_dynamic_cooldown": False,
            "mild_hours": 24.0,
            "moderate_hours": 72.0,
            "severe_hours": 168.0,
            "moderate_vix": 25.0,
            "severe_vix": 35.0,
        }
        try:
            raw = await self.redis.get("policy:rebalance_cadence")
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    policy.update(parsed)
        except Exception as e:
            self.log("WARN", "Failed to load rebalance cadence policy", {"error": str(e)})
        try:
            policy["rebalance_days"] = max(1, int(float(policy.get("rebalance_days", DEFAULT_REBALANCE_DAYS))))
        except Exception:
            policy["rebalance_days"] = DEFAULT_REBALANCE_DAYS
        for key, default in (("mild_hours", 24.0), ("moderate_hours", 72.0), ("severe_hours", 168.0),
                             ("moderate_vix", 25.0), ("severe_vix", 35.0)):
            policy[key] = max(0.0, self._safe_float(policy.get(key), default))
        policy["enable_dynamic_cooldown"] = self._is_truthy(policy.get("enable_dynamic_cooldown"))
        return policy

    def _resolve_cooldown_hours(self, policy, stored_hours):
        if policy.get("enable_dynamic_cooldown"):
            stored = self._safe_float(stored_hours, 0.0)
            if stored > 0:
                return stored, "dynamic:stored_hours"
        legacy_hours = float(max(1, policy.get("rebalance_days", DEFAULT_REBALANCE_DAYS)) * 24)
        return legacy_hours, "legacy:rebalance_days"

    async def sync_rebalance_gate(self):
        prev_mode = self.rebalance_mode
        gate_state = None
        reason = None
        next_ts = None
        source = "redis"

        try:
            override = await self.redis.get(REBAL_OVERRIDE_KEY)
            if override and str(override).upper() == "REBALANCE_ALLOWED":
                gate_state = "REBALANCE_ALLOWED"
                reason = "MANUAL_OVERRIDE"
                source = "override"

            force_flag = await self.redis.get("champion:rebalance:force")
            if gate_state is None and self._is_truthy(force_flag):
                gate_state = "REBALANCE_ALLOWED"
                reason = "FORCE_FLAG"
                source = "force"

            raw_state = await self.redis.get(REBAL_STATE_KEY)
            raw_reason = await self.redis.get(REBAL_REASON_KEY)
            raw_next_ts = await self.redis.get(REBAL_NEXT_TS_KEY)
            parsed_next_ts = self._parse_ts(raw_next_ts)

            if gate_state is None and raw_state in ("REBALANCE_ALLOWED", "RISK_ONLY", "FULL_BLOCK"):
                gate_state = raw_state
                reason = raw_reason or "redis_state"
                next_ts = parsed_next_ts
                source = "redis"

            if gate_state is None:
                source = "fallback"
                policy = await self._load_rebalance_policy()
                last_ts = self._parse_ts(await self.redis.get("champion:rebalance:last_ts"))
                if last_ts is None:
                    gate_state = "REBALANCE_ALLOWED"
                    reason = "NO_HISTORY_FALLBACK"
                else:
                    cooldown_hours, cooldown_source = self._resolve_cooldown_hours(
                        policy, await self.redis.get("champion:rebalance:cooldown_hours")
                    )
                    next_ts = last_ts + (cooldown_hours * 3600.0)
                    now = time.time()
                    if now >= next_ts:
                        gate_state = "REBALANCE_ALLOWED"
                        reason = f"WINDOW_OPEN_FALLBACK(source={cooldown_source},elapsed={now - next_ts:.0f}s)"
                    else:
                        remain = next_ts - now
                        gate_state = "RISK_ONLY"
                        reason = f"WINDOW_CLOSED_FALLBACK(source={cooldown_source},remaining={remain:.0f}s/{remain/3600.0:.1f}h)"

            self.rebalance_mode = gate_state == "REBALANCE_ALLOWED"
            self.rebalance_gate_state = gate_state or "UNKNOWN"
            self.rebalance_gate_reason = reason or "UNKNOWN"
            self.rebalance_next_ts = next_ts
            self.rebalance_source = source
            self.eff_sell_storm_threshold = (
                REBAL_ALLOWED_SELL_STORM_THRESHOLD if self.rebalance_mode else BASE_SELL_STORM_THRESHOLD
            )

            pipe = self.redis.pipeline()
            pipe.set(REBAL_MODE_KEY, "1" if self.rebalance_mode else "0", ex=REBAL_MODE_TTL_SEC)
            pipe.set(REBAL_MODE_REASON_KEY, self.rebalance_gate_reason, ex=REBAL_MODE_TTL_SEC)
            pipe.set(REBAL_MODE_STATE_KEY, self.rebalance_gate_state, ex=REBAL_MODE_TTL_SEC)
            status_payload = {
                "mode": self.rebalance_mode,
                "state": self.rebalance_gate_state,
                "reason": self.rebalance_gate_reason,
                "source": self.rebalance_source,
                "sell_storm_threshold": self.eff_sell_storm_threshold,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            if next_ts:
                iso = datetime.fromtimestamp(next_ts, timezone.utc).isoformat()
                pipe.set(REBAL_MODE_UNTIL_KEY, iso, ex=REBAL_MODE_TTL_SEC)
                status_payload["next_rebal_ts"] = iso
            else:
                pipe.delete(REBAL_MODE_UNTIL_KEY)
            pipe.set(REBAL_STATUS_KEY, json.dumps(status_payload), ex=REBAL_MODE_TTL_SEC)
            await pipe.execute()

            if self.rebalance_mode != prev_mode:
                self.log(
                    "INFO",
                    f"REBALANCE_MODE {'ENABLED' if self.rebalance_mode else 'DISABLED'}",
                    {
                        "state": self.rebalance_gate_state,
                        "reason": self.rebalance_gate_reason,
                        "source": self.rebalance_source,
                        "sell_storm_threshold": self.eff_sell_storm_threshold,
                    },
                )
        except Exception as e:
            self.rebalance_mode = False
            self.rebalance_gate_state = "ERROR"
            self.rebalance_gate_reason = str(e)
            self.rebalance_next_ts = None
            self.rebalance_source = "error"
            self.eff_sell_storm_threshold = BASE_SELL_STORM_THRESHOLD
            self.log("WARN", "REBAL_GATE reader error", {"error": str(e)})

    # ─── VIX Regime Detection ────────────────────────────────────

    async def update_vix_regime(self):
        try:
            vix_raw = await self.redis.get("regime:vix:latest")
            if not vix_raw:
                vix_raw = await self.redis.get("market:vix:current")
            if not vix_raw:
                vix_raw = await self.redis.get("price:consensus:VIX")
            if not vix_raw:
                vix_raw = await self.redis.hget("prices:latest", "VIX")

            if vix_raw:
                self.current_vix = float(vix_raw)
                if self.current_vix < VIX_LOW:
                    new_regime = "LOW"
                elif self.current_vix > VIX_EXTREME:
                    new_regime = "EXTREME"
                elif self.current_vix > VIX_HIGH:
                    new_regime = "HIGH"
                else:
                    new_regime = "MEDIUM"

                if new_regime != self.vix_regime:
                    self.log("INFO", f"VIX regime change: {self.vix_regime} -> {new_regime}",
                            {"vix": self.current_vix})
                    self.vix_regime = new_regime
                    self._apply_regime_adjustments()
                    await self.redis.set("ofg:regime:current", new_regime)
        except Exception as e:
            self.log("WARN", f"VIX regime update error: {e}")

    def _apply_regime_adjustments(self):
        adj = REGIME_ADJUSTMENTS.get(self.vix_regime, REGIME_ADJUSTMENTS["MEDIUM"])
        rm = adj["rate_mult"]
        dm = adj["dd_mult"]

        self.eff_rate_sec = int(BASE_RATE_PER_SEC * rm)
        self.eff_rate_min = int(BASE_RATE_PER_MIN * rm)
        self.eff_rate_session = int(BASE_RATE_PER_SESSION * rm)
        self.eff_dd_halt = BASE_SESSION_DD_HALT_PCT * dm
        self.eff_dd_warning = BASE_SESSION_DD_WARNING_PCT * dm
        self.eff_dd_throttle = BASE_SESSION_DD_THROTTLE_PCT * dm
        self.eff_rapid_dd = BASE_RAPID_DD_PCT * dm

        # [v9.0.0] Deep DD auto-tighten overrides session halt
        if self.in_deep_dd:
            self.eff_dd_halt = max(DEEP_DD_SESSION_HALT_PCT * dm, self.eff_dd_halt)
            # In deep DD, use the tighter of the two
            self.eff_dd_halt = DEEP_DD_SESSION_HALT_PCT * dm

        self.log("INFO", "Thresholds adjusted for regime", {
            "regime": self.vix_regime,
            "vix": self.current_vix,
            "rate_sec": self.eff_rate_sec,
            "rate_min": self.eff_rate_min,
            "rate_session": self.eff_rate_session,
            "dd_warning": f"{self.eff_dd_warning:.3f}",
            "dd_throttle": f"{self.eff_dd_throttle:.3f}",
            "dd_halt": f"{self.eff_dd_halt:.3f}",
            "in_deep_dd": self.in_deep_dd,
            "lifetime_dd": f"{self.current_lifetime_dd:.3f}",
            "session_used": self.orders_this_session,
            "session_remaining": self.eff_rate_session - self.orders_this_session,
        })

    # ─── Session Management ──────────────────────────────────────

    async def check_session_reset(self):
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        if self.session_date != today:
            if now.hour >= MARKET_OPEN_HOUR_UTC:
                await self.reset_session()
                self.session_date = today
                self.metrics["session_resets"] += 1

    async def reset_session(self):
        self.session_start_equity = None
        self.session_start_ts = None
        self.session_start_date = None
        self.session_orders = []
        self.equity_history = []
        self.symbol_order_history = {}
        self.orders_this_session = 0
        for k in self.metrics:
            if k != "session_resets":
                self.metrics[k] = 0
        self.state = OFGState.OPEN
        self.reason = "session_reset"
        self._session_warning_logged = False
        self.halt_entered_ts = None
        self.halt_recovery_count = 0
        self.rebalance_mode = False
        self.rebalance_gate_state = "RESET"
        self.rebalance_gate_reason = "session_reset"
        self.equity_reconcile_until = 0

        # [v9.0.0] Initialize baseline from broker truth on session reset
        await self._init_session_baseline()

        await self._write_state()
        self.log("INFO", "Session reset completed", {
            "session_start_equity": self.session_start_equity,
        })

    # ─── Rate Limiter (3-Tier, Adaptive) ─────────────────────────

    def check_rate_limits(self):
        now = time.time()
        self._clean_rolling_windows(now)

        sec_count = sum(1 for o in self.session_orders if now - o["ts"] < 1.0)
        if sec_count >= self.eff_rate_sec:
            self.metrics["rate_limit_hits"] += 1
            return False, "TIER1_PER_SEC", f"{sec_count}/{self.eff_rate_sec}/sec"

        min_count = sum(1 for o in self.session_orders if now - o["ts"] < 60.0)
        if min_count >= self.eff_rate_min:
            self.metrics["rate_limit_hits"] += 1
            return False, "TIER2_PER_MIN", f"{min_count}/{self.eff_rate_min}/min"

        if self.orders_this_session >= self.eff_rate_session:
            self.metrics["rate_limit_hits"] += 1
            asyncio.ensure_future(self._audit_session_limit_reached())
            return False, "TIER3_PER_SESSION", f"{self.orders_this_session}/{self.eff_rate_session}/session"

        session_usage_pct = self.orders_this_session / max(1, self.eff_rate_session)
        if session_usage_pct >= 0.8 and not getattr(self, "_session_warning_logged", False):
            self.log("WARN", "SESSION_LIMIT_WARNING_80PCT", {
                "orders": self.orders_this_session,
                "limit": self.eff_rate_session,
                "usage_pct": f"{session_usage_pct:.1%}",
                "regime": self.vix_regime,
                "remaining": self.eff_rate_session - self.orders_this_session,
            })
            self._session_warning_logged = True

        return True, "OK", ""

    async def _audit_session_limit_reached(self):
        try:
            audit_data = {
                "event": "SESSION_LIMIT_REACHED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "orders_total": self.orders_this_session,
                "limit": self.eff_rate_session,
                "regime": self.vix_regime,
                "vix": self.current_vix,
                "session_date": self.session_date,
                "rate_sec": self.eff_rate_sec,
                "rate_min": self.eff_rate_min,
                "session_dd": f"{self.current_session_dd:.6f}",
            }
            await self.redis.lpush("ofg:audit:session_limit", json.dumps(audit_data))
            await self.redis.ltrim("ofg:audit:session_limit", 0, 99)
            self.log("WARN", "SESSION_LIMIT_AUDIT", audit_data)
        except Exception as e:
            self.log("WARN", f"Audit write failed: {e}")

    def _clean_rolling_windows(self, now):
        cutoff = now - 300
        self.session_orders = [o for o in self.session_orders if o["ts"] > cutoff]

    # ─── Portfolio Circuit Breaker (v9.0.0 Multi-Tier) ───────────

    async def check_circuit_breaker(self):
        try:
            # [v9.0.0] Check equity consistency first
            equity_consistent = await self._check_equity_consistency()
            if not equity_consistent:
                # During reconcile, don't trigger DD-based state changes
                if time.time() < self.equity_reconcile_until:
                    return OFGState.OPEN, "equity_reconcile_in_progress"

            equity_candidate = await self._get_current_equity_candidate(allow_stale=True)
            if not equity_candidate:
                return OFGState.OPEN, "no_equity_data"

            await self._publish_verified_equity(equity_candidate, "current")
            if not equity_candidate.get("fresh"):
                self.log("WARN", "CURRENT_EQUITY_USING_STALE_BROKER_TRUTH", {
                    "source": equity_candidate.get("source"),
                    "age_sec": equity_candidate.get("age_sec"),
                    "equity": equity_candidate.get("value"),
                    "sla_sec": equity_candidate.get("sla_sec"),
                    "market_session": equity_candidate.get("market_session"),
                    "contract_warnings": equity_candidate.get("contract_warnings"),
                })

            current_equity = float(equity_candidate.get("value"))
            now = time.time()

            # [v9.0.0] Initialize baseline from broker truth, not first-seen
            if self.session_start_equity is None:
                await self._init_session_baseline()
                # If still None after init attempt, use current as fallback
                if self.session_start_equity is None:
                    self.session_start_equity = current_equity
                    self.session_start_ts = now
                    self.session_start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    await self._persist_baseline(current_equity, "first_seen_fallback")
                    self.log("WARN", "BASELINE_FALLBACK_TO_FIRST_SEEN", {
                        "equity": current_equity,
                    })

            # [v9.0.0] Baseline freshness guard
            await self._check_baseline_freshness()

            self.equity_history.append({"ts": now, "equity": current_equity})
            cutoff = now - 600
            self.equity_history = [e for e in self.equity_history if e["ts"] > cutoff]

            # === P0-2 (2026-05-07): Tier Transition Auto-Reset ===
            # Must run BEFORE _update_lifetime_dd so lifetime_peak doesn't get
            # poisoned by stale baseline reference. If a tier change resets baseline,
            # current_equity becomes the new anchor and lifetime_dd is recalculated
            # from the new peak in the next iteration.
            await self._check_tier_change_signal(current_equity)

            # [v9.0.0] Update lifetime DD
            await self._update_lifetime_dd(current_equity)

            # [v9.0.0] Lifetime DD hard halt check
            if self.current_lifetime_dd <= LIFETIME_DD_HARD_HALT_PCT:
                self.metrics["lifetime_halt_events"] += 1
                self.log("CRITICAL", "LIFETIME_DD_HARD_HALT", {
                    "lifetime_dd": f"{self.current_lifetime_dd:.2%}",
                    "threshold": f"{LIFETIME_DD_HARD_HALT_PCT:.2%}",
                    "peak": self.lifetime_peak,
                    "current": current_equity,
                })
                return OFGState.HALT, f"lifetime_dd={self.current_lifetime_dd:.4f}<={LIFETIME_DD_HARD_HALT_PCT}"

            # Session drawdown (multi-tier)
            if self.session_start_equity and self.session_start_equity > 0:
                session_dd = (current_equity - self.session_start_equity) / self.session_start_equity
                self.current_session_dd = session_dd

                _dd_only = min(0.0, session_dd)
                _gain_only = max(0.0, session_dd)

                _pipe = self.redis.pipeline()
                _pipe.set("ofg:drawdown:current", f"{_dd_only:.6f}")
                _pipe.set("ofg:drawdown:session", f"{session_dd:.6f}")
                _pipe.set("ofg:gain:current", f"{_gain_only:.6f}")
                _pipe.set("ofg:pnl_from_start", f"{session_dd:.6f}")
                _pipe.set("ares:drawdown:source", "ofg-v2-signed-dd")
                _pipe.set("ofg:session_start_equity:meta", json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "date": self.session_start_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "source": "broker_truth_resolved",
                    "value": self.session_start_equity,
                }))
                await _pipe.execute()

                # [v9.0.0] Determine effective halt threshold
                effective_halt = self.eff_dd_halt
                if self.in_deep_dd:
                    effective_halt = DEEP_DD_SESSION_HALT_PCT * REGIME_ADJUSTMENTS.get(
                        self.vix_regime, REGIME_ADJUSTMENTS["MEDIUM"])["dd_mult"]

                # Tier 1: Hard HALT
                if session_dd <= effective_halt:
                    self.metrics["circuit_breaker_trips"] += 1
                    return OFGState.HALT, f"session_dd={session_dd:.4f}<={effective_halt:.4f}"

                # Tier 2: THROTTLE
                if session_dd <= self.eff_dd_throttle:
                    return OFGState.THROTTLE, f"session_dd={session_dd:.4f}<={self.eff_dd_throttle:.4f}"

                # Tier 3: Warning (log only, no state change)
                if session_dd <= self.eff_dd_warning:
                    self.log("WARN", "SESSION_DD_WARNING", {
                        "session_dd": f"{session_dd:.4f}",
                        "warning_threshold": f"{self.eff_dd_warning:.4f}",
                        "halt_threshold": f"{effective_halt:.4f}",
                        "lifetime_dd": f"{self.current_lifetime_dd:.4f}",
                        "in_deep_dd": self.in_deep_dd,
                    })

            # Rapid drawdown
            window_cutoff = now - (RAPID_DD_WINDOW_MIN * 60)
            window_equities = [e for e in self.equity_history if e["ts"] > window_cutoff]
            if len(window_equities) >= 2:
                window_start_eq = window_equities[0]["equity"]
                if window_start_eq > 0:
                    rapid_dd = (current_equity - window_start_eq) / window_start_eq
                    if rapid_dd <= self.eff_rapid_dd:
                        self.metrics["circuit_breaker_trips"] += 1
                        return OFGState.HALT, f"rapid_dd={rapid_dd:.4f} in {RAPID_DD_WINDOW_MIN}min"

            return OFGState.OPEN, "within_limits"
        except Exception as e:
            self.log("ERROR", f"Circuit breaker error: {e}")
            return OFGState.OPEN, f"error: {e}"

    # ─── [FP-FIX-v3] Prolonged HALT Equity Anchor Reset ────────
    PROLONGED_HALT_RESET_SEC = 1800

    async def check_prolonged_halt_reset(self):
        if self.state != OFGState.HALT:
            return
        if self.halt_entered_ts is None:
            return
        halt_duration = time.time() - self.halt_entered_ts
        if halt_duration < self.PROLONGED_HALT_RESET_SEC:
            return
        if self.halt_recovery_count < HALT_MAX_RECOVERY_ATTEMPTS:
            return

        try:
            eq_raw = await self.redis.get("ares:equity:total")
            if eq_raw:
                new_anchor = float(eq_raw)
                old_anchor = self.session_start_equity
                self.session_start_equity = new_anchor
                self.session_start_ts = time.time()
                self.session_start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                self.halt_entered_ts = None
                self.halt_recovery_count = 0
                self.current_session_dd = 0.0
                self.metrics["equity_anchor_resets"] = self.metrics.get("equity_anchor_resets", 0) + 1
                await self._persist_baseline(new_anchor, "prolonged_halt_reset")
                self.log("WARN", "PROLONGED_HALT_RESET: equity anchor reset", {
                    "old_anchor": old_anchor,
                    "new_anchor": new_anchor,
                    "halt_duration_sec": halt_duration,
                    "loss_accepted": round((new_anchor - old_anchor) / old_anchor * 100, 2) if old_anchor else 0,
                })
        except Exception as e:
            self.log("ERROR", "Prolonged halt reset error: " + str(e))

    # ─── Anomaly Detector ────────────────────────────────────────

    def detect_anomalies(self):
        anomalies = []
        now = time.time()

        # Sell Storm
        recent_sells = [o for o in self.session_orders
                       if now - o["ts"] < SELL_STORM_WINDOW_SEC and o["side"] == "SELL"]
        if len(recent_sells) > self.eff_sell_storm_threshold:
            anomalies.append({
                "type": "SELL_STORM", "severity": "CRITICAL",
                "detail": f"{len(recent_sells)} sells in {SELL_STORM_WINDOW_SEC}s (threshold={self.eff_sell_storm_threshold})",
                "action": "HALT",
            })
            self.metrics["sell_storms"] += 1

        # Flip Pattern
        for symbol, history in self.symbol_order_history.items():
            recent = [h for h in history if now - h["ts"] < FLIP_WINDOW_SEC]
            if len(recent) >= 3:
                flips = sum(1 for i in range(1, len(recent)) if recent[i]["side"] != recent[i-1]["side"])
                if flips >= FLIP_MAX_PER_SYMBOL:
                    anomalies.append({
                        "type": "FLIP_PATTERN", "severity": "HIGH",
                        "detail": f"{symbol}: {flips} flips in {FLIP_WINDOW_SEC}s",
                        "action": "THROTTLE",
                    })
                    self.metrics["flip_patterns"] += 1

        # Concentration
        if self.session_orders:
            total_notional = sum(o.get("notional", 0) for o in self.session_orders)
            if total_notional > 0:
                symbol_notionals = {}
                for o in self.session_orders:
                    sym = o.get("symbol", "UNKNOWN")
                    symbol_notionals[sym] = symbol_notionals.get(sym, 0) + o.get("notional", 0)
                for sym, notional in symbol_notionals.items():
                    pct = notional / total_notional
                    if pct > CONCENTRATION_MAX_PCT:
                        anomalies.append({
                            "type": "CONCENTRATION", "severity": "MEDIUM",
                            "detail": f"{sym}: {pct:.1%} of session notional",
                            "action": "LOG",
                        })

        return anomalies

    # ─── Order Stream Monitor ────────────────────────────────────

    async def monitor_order_stream(self):
        try:
            result = await self.redis.xread(
                {"emarkos:v6:order:intent": self.last_stream_id},
                count=50, block=100,
            )
            if not result:
                return
            for stream_name, messages in result:
                for msg_id, fields in messages:
                    self.last_stream_id = msg_id
                    await self._process_order_event(msg_id, fields)
        except Exception as e:
            self.log("WARN", f"Stream monitor error: {e}")

    async def _process_order_event(self, msg_id, fields):
        now = time.time()
        symbol = fields.get("symbol", "UNKNOWN")
        side = fields.get("side", "UNKNOWN").upper()
        qty = float(fields.get("qty", 0) or fields.get("delta_shares", 0) or 0)
        price = float(fields.get("price", 0) or fields.get("limit_price", 0) or fields.get("ref_px", 0) or 0)
        notional = abs(qty * price) if price > 0 else 0

        order_record = {
            "ts": now, "symbol": symbol, "side": side,
            "qty": qty, "notional": notional, "msg_id": msg_id,
        }
        self.session_orders.append(order_record)
        self.orders_this_session += 1

        if symbol not in self.symbol_order_history:
            self.symbol_order_history[symbol] = []
        self.symbol_order_history[symbol].append({"ts": now, "side": side})
        cutoff = now - 300
        self.symbol_order_history[symbol] = [
            h for h in self.symbol_order_history[symbol] if h["ts"] > cutoff
        ]

        self.metrics["orders_total"] += 1
        if side == "BUY":
            self.metrics["orders_buy"] += 1
        elif side == "SELL":
            self.metrics["orders_sell"] += 1

    # ─── State Management ────────────────────────────────────────

    async def evaluate_state(self):
        prev_state = self.state
        new_state = OFGState.OPEN
        reasons = []
        upstream_halt = False

        # Rebalance gate reader compatibility
        await self.sync_rebalance_gate()

        # Upstream halt flags
        try:
            trade_halt, effective_trade_halt, ignored_trade_halt, trade_halt_reason = await self._inspect_legacy_trade_halt()
            if effective_trade_halt:
                upstream_halt = True
                new_state = OFGState.HALT
                reasons.append("upstream:trade:halt")
            elif ignored_trade_halt:
                reasons.append(trade_halt_reason)
            # [DEADLOCK-FIX-20260507] trading:enabled is an OUTPUT of go-nogo+kill-switch chain.
            # Reading it as OFG input creates a deadlock: trading:enabled=false -> OFG HALT ->
            # supervisor holds OIE stopped -> go-nogo pm2:FAIL -> kill-switch DISABLED -> trading:enabled=false.
            # Disabled per trade-actuator-supervisor.py design intent.
            # trading_enabled = await self.redis.get("trading:enabled")
            # if not self._is_truthy(trading_enabled):
            #     upstream_halt = True
            #     new_state = OFGState.HALT
            #     reasons.append("upstream:trading:disabled")
            kill_switch = await self.redis.get("policy:kill_switch")
            if self._is_truthy(kill_switch):
                upstream_halt = True
                new_state = OFGState.HALT
                reasons.append("upstream:kill_switch")
        except Exception as e:
            self.log("WARN", f"Upstream flag check error: {e}")

        # Rate limiter
        rate_ok, tier, detail = self.check_rate_limits()
        if not rate_ok:
            if new_state != OFGState.HALT:
                new_state = OFGState.THROTTLE
            reasons.append(f"rate_limit:{tier}:{detail}")
            self.metrics["throttle_events"] += 1

        # Circuit breaker (with HALT recovery)
        cb_state, cb_reason = await self.check_circuit_breaker()
        if cb_state == OFGState.HALT:
            recovery_possible = False
            if self.halt_entered_ts is not None:
                halt_duration = time.time() - self.halt_entered_ts
                if halt_duration >= HALT_RECOVERY_COOLDOWN_SEC:
                    if self.current_session_dd > HALT_RECOVERY_DD_THRESHOLD:
                        if self.halt_recovery_count < HALT_MAX_RECOVERY_ATTEMPTS:
                            recovery_possible = True
                            self.halt_recovery_count += 1
                            self.metrics["halt_recoveries"] = self.halt_recovery_count
                            self.metrics["halt_recovery_attempts"] = self.halt_recovery_count
                            self.halt_entered_ts = None
                            self.log("WARN", f"HALT RECOVERY #{self.halt_recovery_count}: session_dd={self.current_session_dd:.4f} > {HALT_RECOVERY_DD_THRESHOLD}", {
                                "recovery_count": self.halt_recovery_count,
                                "halt_duration_sec": halt_duration,
                                "session_dd": self.current_session_dd,
                            })
                        else:
                            self.log("WARN", f"HALT recovery exhausted ({HALT_MAX_RECOVERY_ATTEMPTS} max)")
            if not recovery_possible:
                new_state = OFGState.HALT
                reasons.append(f"circuit_breaker:{cb_reason}")
            else:
                if new_state == OFGState.OPEN:
                    new_state = OFGState.THROTTLE
                reasons.append(f"circuit_breaker:RECOVERED_TO_THROTTLE")
        elif cb_state == OFGState.THROTTLE and new_state == OFGState.OPEN:
            new_state = OFGState.THROTTLE
            reasons.append(f"circuit_breaker:{cb_reason}")

        # Anomaly detection
        anomalies = self.detect_anomalies()
        for anomaly in anomalies:
            self.metrics["anomalies_detected"] += 1
            if anomaly["action"] == "HALT":
                new_state = OFGState.HALT
                reasons.append(f"anomaly:{anomaly['type']}")
            elif anomaly["action"] == "THROTTLE" and new_state == OFGState.OPEN:
                new_state = OFGState.THROTTLE
                reasons.append(f"anomaly:{anomaly['type']}")
            try:
                await self.redis.xadd("ofg:anomaly:log", {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": anomaly["type"], "severity": anomaly["severity"],
                    "detail": anomaly["detail"], "action": anomaly["action"],
                })
                await self.redis.xtrim("ofg:anomaly:log", maxlen=1000)
            except Exception:
                pass

        self.state = new_state
        self.reason = "; ".join(reasons) if reasons else "all_clear"

        if new_state != prev_state:
            self.log("WARN" if new_state != OFGState.OPEN else "INFO",
                     f"OFG state: {prev_state} -> {new_state}", {"reason": self.reason})
            if new_state == OFGState.HALT:
                self.metrics["halt_events"] += 1
                if self.halt_entered_ts is None:
                    self.halt_entered_ts = time.time()
            elif prev_state == OFGState.HALT:
                self.halt_entered_ts = None

        await self._write_state()

    async def _write_state(self):
        try:
            pipe = self.redis.pipeline()
            pipe.set("ofg:gate", self.state, ex=GATE_TTL_SEC)
            pipe.set("ofg:gate:status", self.state, ex=GATE_TTL_SEC)
            status = {
                "state": self.state, "reason": self.reason,
                "ts": datetime.now(timezone.utc).isoformat(),
                "session_start_equity": self.session_start_equity,
                "orders_this_session": self.orders_this_session,
                "vix_regime": self.vix_regime, "current_vix": self.current_vix,
                "current_session_dd": self.current_session_dd,
                "current_lifetime_dd": self.current_lifetime_dd,
                "lifetime_peak": self.lifetime_peak,
                "in_deep_dd": self.in_deep_dd,
                "rebalance": {
                    "mode": self.rebalance_mode,
                    "state": self.rebalance_gate_state,
                    "reason": self.rebalance_gate_reason,
                    "source": self.rebalance_source,
                    "next_rebal_ts": datetime.fromtimestamp(self.rebalance_next_ts, timezone.utc).isoformat()
                        if self.rebalance_next_ts else None,
                    "sell_storm_threshold": self.eff_sell_storm_threshold,
                },
                "equity_health": {
                    "schema_contract": OFG_EQUITY_SCHEMA_CONTRACT,
                    "freshness_policy": self.last_equity_sla_policy,
                    "last_candidate": self.last_equity_candidate,
                    "contract_warnings": self.last_equity_contract_warnings,
                    "degraded": bool(
                        not self.last_equity_candidate
                        or self.last_equity_candidate.get("degraded")
                        or self.last_equity_contract_warnings
                    ),
                },
                "effective_thresholds": {
                    "rate_sec": self.eff_rate_sec, "rate_min": self.eff_rate_min,
                    "rate_session": self.eff_rate_session,
                    "dd_warning": self.eff_dd_warning,
                    "dd_throttle": self.eff_dd_throttle,
                    "dd_halt": self.eff_dd_halt,
                    "dd_halt_effective": DEEP_DD_SESSION_HALT_PCT if self.in_deep_dd else self.eff_dd_halt,
                    "lifetime_soft_alert": LIFETIME_DD_SOFT_ALERT_PCT,
                    "lifetime_hard_halt": LIFETIME_DD_HARD_HALT_PCT,
                    "sell_storm_threshold": self.eff_sell_storm_threshold,
                },
                "metrics": self.metrics,
            }
            pipe.set("ofg:status", json.dumps(status))
            for k, v in self.metrics.items():
                pipe.hset("ofg:session:metrics", k, str(v))
            await pipe.execute()
        except Exception as e:
            self.log("ERROR", f"Failed to write state: {e}")

    # ─── Telemetry ───────────────────────────────────────────────

    async def write_telemetry(self):
        try:
            now = time.time()
            sec_count = sum(1 for o in self.session_orders if now - o["ts"] < 1.0)
            min_count = sum(1 for o in self.session_orders if now - o["ts"] < 60.0)

            snapshot = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "gate": self.state,
                "regime": self.vix_regime,
                "vix": str(round(self.current_vix, 2)),
                "orders_sec": str(sec_count),
                "orders_min": str(min_count),
                "orders_session": str(self.orders_this_session),
                "rate_limit_sec": str(self.eff_rate_sec),
                "rate_limit_min": str(self.eff_rate_min),
                "sell_storm_threshold": str(self.eff_sell_storm_threshold),
                "rebalance_mode": "1" if self.rebalance_mode else "0",
                "rebal_gate_state": self.rebalance_gate_state,
                "session_dd": f"{self.current_session_dd:.6f}",
                "lifetime_dd": f"{self.current_lifetime_dd:.6f}",
                "in_deep_dd": "1" if self.in_deep_dd else "0",
                "equity": str(self.session_start_equity or 0),
            }

            if self.session_start_equity and self.session_start_equity > 0:
                eq_raw = await self.redis.get("ares:equity:total")
                if eq_raw:
                    dd = (float(eq_raw) - self.session_start_equity) / self.session_start_equity
                    snapshot["session_dd"] = f"{dd:.6f}"
                    snapshot["equity"] = eq_raw

            await self.redis.xadd("ofg:telemetry", snapshot)
            await self.redis.xtrim("ofg:telemetry", maxlen=8640)
        except Exception as e:
            self.log("WARN", f"Telemetry write error: {e}")

    async def write_heartbeat(self):
        try:
            await self.redis.set("ofg:heartbeat", str(int(time.time())), ex=30)
        except Exception:
            pass

    # ─── Main Loop ───────────────────────────────────────────────

    async def run(self):
        self.log("INFO", "=" * 60)
        self.log("INFO", "Order Flow Governor v9.0.0 — Multi-Tier DD + Baseline Integrity")
        self.log("INFO", f"Session DD: warning@{BASE_SESSION_DD_WARNING_PCT:.1%}, "
                         f"throttle@{BASE_SESSION_DD_THROTTLE_PCT:.1%}, "
                         f"halt@{BASE_SESSION_DD_HALT_PCT:.1%}")
        self.log("INFO", f"Lifetime DD: alert@{LIFETIME_DD_SOFT_ALERT_PCT:.1%}, "
                         f"halt@{LIFETIME_DD_HARD_HALT_PCT:.1%}")
        self.log("INFO", f"Deep DD auto-tighten: session halt → {DEEP_DD_SESSION_HALT_PCT:.1%} "
                         f"when lifetime DD < {LIFETIME_DD_SOFT_ALERT_PCT:.1%}")
        self.log("INFO", f"Rates: {BASE_RATE_PER_SEC}/sec, {BASE_RATE_PER_MIN}/min, {BASE_RATE_PER_SESSION}/session")
        self.log("INFO", f"Adaptive: VIX LOW<{VIX_LOW}, HIGH>{VIX_HIGH}, EXTREME>{VIX_EXTREME}")
        self.log("INFO", f"Baseline: stale_max={BASELINE_STALE_MAX_AGE_SEC/3600:.0f}h, "
                         f"consistency_threshold={EQUITY_CONSISTENCY_THRESHOLD:.1%}")
        self.log("INFO", "=" * 60)

        await self.connect_redis()

        # [v9.0.0] Initialize baseline from broker truth on startup
        await self._init_session_baseline()

        # [v9.0.0] Initialize lifetime peak
        await self._update_lifetime_dd(
            self._safe_float(await self.redis.get("ares:equity:total"), 0)
        )

        try:
            info = await self.redis.xinfo_stream("emarkos:v6:order:intent")
            self.last_stream_id = info.get("last-generated-id", "0-0")
            self.log("INFO", f"Stream monitor from: {self.last_stream_id}")
        except Exception:
            self.log("WARN", "Could not get stream info, starting from 0-0")

        await self.redis.set("ofg:gate", OFGState.OPEN, ex=GATE_TTL_SEC)
        await self.redis.set("ofg:gate:status", OFGState.OPEN, ex=GATE_TTL_SEC)
        await self.redis.set("ofg:regime:current", "MEDIUM")
        self._apply_regime_adjustments()
        await self._write_state()

        # Set session date
        self.session_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        last_heartbeat = 0
        last_telemetry = 0
        last_log = 0
        last_vix_check = 0

        while self.running:
            try:
                now = time.time()

                # Session management
                await self.check_session_reset()
                # Prolonged HALT equity anchor reset
                await self.check_prolonged_halt_reset()

                # Monitor order stream
                await self.monitor_order_stream()

                # VIX regime check (every 30s)
                if now - last_vix_check >= 30:
                    await self.update_vix_regime()
                    last_vix_check = now

                # Evaluate state
                await self.evaluate_state()

                # Heartbeat
                if now - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
                    await self.write_heartbeat()
                    last_heartbeat = now

                # Telemetry
                if now - last_telemetry >= TELEMETRY_INTERVAL_SEC:
                    await self.write_telemetry()
                    last_telemetry = now

                # Periodic log
                if now - last_log >= LOG_INTERVAL_SEC:
                    self.log("INFO", "OFG periodic status", {
                        "state": self.state, "regime": self.vix_regime,
                        "vix": self.current_vix,
                        "orders_session": self.orders_this_session,
                        "session_dd": f"{self.current_session_dd:.4f}",
                        "lifetime_dd": f"{self.current_lifetime_dd:.4f}",
                        "in_deep_dd": self.in_deep_dd,
                        "thresholds": {
                            "dd_warning": f"{self.eff_dd_warning:.3f}",
                            "dd_throttle": f"{self.eff_dd_throttle:.3f}",
                            "dd_halt": f"{self.eff_dd_halt:.3f}",
                        },
                        "metrics": self.metrics,
                    })
                    last_log = now

                await asyncio.sleep(MONITOR_INTERVAL_SEC)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log("ERROR", f"Main loop error: {e}")
                await asyncio.sleep(5)

        self.log("INFO", "OFG v9.0.0 shutting down")
        if self.redis:
            await self.redis.aclose()


async def main():
    ofg = OrderFlowGovernorV2()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: setattr(ofg, 'running', False))
    await ofg.run()


if __name__ == "__main__":
    asyncio.run(main())
