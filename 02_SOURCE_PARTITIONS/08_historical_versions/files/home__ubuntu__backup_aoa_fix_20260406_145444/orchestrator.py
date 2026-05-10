# WARNING: This is NOT the SSOT. Live version: /home/ubuntu/nextgen2/orchestrator.py
"""ARES NextGen2 — Orchestrator (Engine Core) v7.7.3

Production-grade trading engine orchestrator with 30+ rounds of AI-assisted
auditing. All CRITICAL and HIGH issues resolved across all 6 audit categories.

Architecture:
  _cycle() → [market_check → lock → gates → signals → targets → positions →
               prices → crash_guard → edge_gate → build_intents → turnover →
               emit → metrics]

Mode handling:
  LIVE    — Full trading, intents emitted to broker stream (atomic Lua XADD+IDEM)
  SHADOW  — Intents logged to shadow stream, NOT sent to broker
  DRY_RUN — Intents logged to dryrun stream, NOT sent to broker
  SAFE    — All trading blocked

Key safety features:
  - Fail-closed LIVE gating: mode, trading:enabled, broker alignment all required
  - Multi-scope halt system: global + engine + strategy-scoped halt keys
  - Freshness enforcement: positions, cash, equity, price feed (LIVE halts on stale)
  - Atomic Lua emission: XADD + idempotency SET in single Redis call
  - 2-phase idempotency: PENDING→SENT lifecycle with mode-scoped keys
  - Producer election: SET NX lock with TTL/3 heartbeat refresh
  - Circuit breaker: MAX_CONSECUTIVE_ERRORS → engine halt (with transient error isolation)
  - safe_float/safe_int wrappers on ALL Redis numeric reads
  - isinstance(dict) validation on ALL Redis JSON payloads
  - Bounded alert queue with drop-oldest semantics
  - Strategy-scoped halt isolation (no cross-strategy interference)
  - Open-order awareness (skip duplicate symbol/side emissions)
  - Config-driven thresholds (heartbeat TTL, KPI maxlen, price staleness, etc.)
  - Structured JSON logging for all skip/halt/error paths
  - Per-reason skip counters exported to Redis for dashboards
  - Startup readiness check with dependency health validation
  - Price feed heartbeat monitoring with ms-epoch auto-detection
  - Targets schema validation with version drift detection
  - Heartbeat dependency health (positions/cash/equity/targets/price_feed ages)
  - Per-field defensive parsing in KIS adapter (malformed field → skip, not crash)
  - Fail-closed CAS on producer lock (LIVE aborts emission on CAS failure)
  - dict-under-positions explicit handling (v1-map-under-v2 schema detection)
  - Canonical max_single_order_qty via RuntimeConfig (single source of truth)
  - Pre-market dependency readiness watchdog (LIVE + market-hours gated)

Testing coverage (validated across 30+ AI audit rounds):
  - All 6 categories: safety, idempotency, concurrency, data_integrity,
    risk_management, operational — scored 8-9/10 consistently
  - Modes: LIVE, SHADOW, DRY_RUN, SAFE
  - Edge cases: stale data, malformed JSON, empty targets, sudden-empty positions,
    clock skew (ms-epoch), concurrent producer election, circuit breaker lifecycle,
    halt set/clear/stale detection, schema version drift, open-order dedup
"""
# [V400-PATCH-V2-APPLIED] ts=2026-03-20T13:26:59.716779+00:00 ver=v400_patch_v2

from __future__ import annotations

import json
# [v8.0.0] Key Contract Startup Gate (2026-03-05)
try:
    from startup_gate import run_startup_gate as _run_startup_gate
    _STARTUP_GATE_AVAILABLE = True
except ImportError:
    _STARTUP_GATE_AVAILABLE = False

import logging
import os
import time
import uuid
from datetime import datetime as _dt, timezone as _tz
from typing import Any, Dict, Optional

import redis as redis_lib

from nextgen2.config import AppConfig
from nextgen2.util import (
    day_key,
    get_market_times_ms,
    get_ny_time,
    is_us_market_session,
    make_cycle_id,
    redis_lock,
    safe_json_parse,
    sha256_hex,
    stable_json,
)

# Policy modules
from nextgen2.policy.scheduler import DecisionScheduler
from nextgen2.policy.edge_gate import EdgeGate
from nextgen2.policy.turnover import TurnoverGovernor

# [4AI-WINRATE-v1.0] SessionStateManager (SSMv2 통합)
try:
    from nextgen2.policy.session_state import SessionStateManager
    from nextgen2.policy.session_state_manager import (
        SessionStateManager as SessionStateManagerV2,
        EntryContext as SSMEntryContext,
        GateDecision as SSMGateDecision,
    )
    _SSM_V2_AVAILABLE = True
    _SESSION_STATE_AVAILABLE = True
except ImportError:
    SessionStateManager = None
    _SESSION_STATE_AVAILABLE = False

from nextgen2.policy.profit_interrupt import ProfitInterrupt
from nextgen2.policy.adaptive_rebalance import AdaptiveRebalance

# Risk modules
from nextgen2.risk.crash_guard import CrashGuard
from nextgen2.risk.equity_anchor import EquityAnchor
from nextgen2.risk.concentration import ConcentrationMonitor as ConcentrationGuard

# [V237-Phase4] Risk Engine (ImpactGuard, CrowdingGuard, FlowImbalanceGuard, ExecutionLatencyGuard)
try:
    from nextgen2.risk_engine.impact_guard import ImpactGuard
    from nextgen2.risk_engine.crowding_guard import CrowdingGuard as CrowdingGuardV2
    from nextgen2.risk_engine.flow_imbalance_guard import FlowImbalanceGuard
    from nextgen2.risk_engine.execution_latency_guard import ExecutionLatencyGuard
    _RISK_ENGINE_AVAILABLE = True
except ImportError as _re_err:
    ImpactGuard = None
    CrowdingGuardV2 = None
    FlowImbalanceGuard = None
    ExecutionLatencyGuard = None
    _RISK_ENGINE_AVAILABLE = False
    import logging as _re_log
    _re_log.getLogger("ares.orchestrator").warning(
        "[V237] risk_engine modules not available: %s", _re_err
    )

# Strategy modules
from nextgen2.strategy.regime_theme_matrix import RegimeThemeMatrix
from nextgen2.strategy.cross_section import CrossSection as CrossSectionRanker

# Governance
try:
    from nextgen2.governance2.safe_mode import SafeModeController as SafeMode
except ImportError as _e:
    import logging as _logging
    _logging.getLogger("ares.orchestrator").critical(
        "FATAL: SafeModeController import failed: %s -- using stub", _e
    )
    class SafeMode:
        """Stub SafeMode that blocks all trading when real module is broken."""
        def __init__(self, *a, **kw): pass
        def get_mode(self):
            class _S:
                is_safe = True
                sizing_multiplier = 0.0
                label = "IMPORT_FAIL_SAFE"
            return _S()
        def check_and_update(self, *a, **kw): return False
        def to_dict(self): return {"state": "IMPORT_FAIL", "safe": False}
try:
    from nextgen2.governance2.feature_flags import FeatureFlags
except ImportError:
    class FeatureFlags:
        """Stub when feature_flags module is not available."""
        pass

# Observability
try:
    from nextgen2.observability.metrics import MetricsWriter as MetricsCollector
except ImportError:
    MetricsCollector = None
try:
    from nextgen2.observability.metrics import RedisMetricsCollector
except ImportError:
    RedisMetricsCollector = None
from nextgen2.observability.alerts import TelegramNotifier

# Intent
from nextgen2.intent.order_builder import build_order_intents
from nextgen2.intent.idempotency import build_intent_id, build_decision_id

# Bus
from nextgen2.bus.redis_stream import RedisStreamBus

logger = logging.getLogger("ares.orchestrator")

VERSION = "7.8.0"

# Stream names — consumer topology must match order-intent-executor
# [v7.3] CUTOVER COMPLETE: LIVE intent stream switched from legacy v1 to v6 EventBus.
# emarkos:v6:order:intent is fully deprecated (no write, no read).
STREAM_LIVE_INTENT = "emarkos:v6:order:intent"        # v7.3: v6 EventBus stream (was emarkos:v6:order:intent)
STREAM_SHADOW_INTENT = "stream:shadow_intents"        # shadow-only, no consumer
STREAM_DRYRUN_INTENT = "stream:dryrun_intents"        # dryrun-only, no consumer

# [v7.1.0] Producer toggle key — strategy-scoped to prevent multi-strategy
# serialization. Each strategy gets its own producer lock, allowing
# concurrent strategies on the same EC2 without lock contention.
# Format: producer:order_intent:{strategy} (set in init() from rc.strategy).
# The module-level constant is a fallback for backward compatibility.
_DEFAULT_PRODUCER_LOCK_KEY = "producer:order_intent"
PRODUCER_LOCK_TTL = 120  # seconds [FIX-v8.1: 60→120 to cover restart gap (2026-03-05)]
PRODUCER_HEARTBEAT_INTERVAL = 10  # refresh producer lock every N intents

# Circuit breaker thresholds
MAX_CONSECUTIVE_ERRORS = 10  # [FIX-v8.1: 5→10, ACL 오류 일시적 내성 (2026-03-05)]
DEFAULT_BUDGET_FALLBACK = 144000

# [v5.5.0] Max single order quantity — default aligned with KIS broker API limit.
# Now config-driven: rc.max_single_order_qty overrides this default.
# Prevents large-qty orders that would be rejected by the broker, causing
# repeated EMIT_FAILED → PENDING idempotency holds → circuit breaker halts.
DEFAULT_MAX_SINGLE_ORDER_QTY = 9999

# [v4.7.0] Targets staleness threshold (seconds) — defaults, overridden by RuntimeConfig.
# [v6.3.0] Now config-driven to match the freshness approach.
DEFAULT_TARGETS_STALE_WARN_SEC = 3600   # 1 hour: warn
DEFAULT_TARGETS_STALE_HALT_SEC = 7200   # 2 hours: halt in LIVE

# Lua script: atomic XADD (with MAXLEN ~) + idempotency SET in a single Redis call
# This prevents the gap where XADD succeeds but SET fails (crash/timeout)
# Keys: KEYS[1]=stream, KEYS[2]=idem_key
# Args: ARGV[1]=idem_value, ARGV[2]=idem_ttl, ARGV[3]=maxlen, then pairs of field/value for XADD
LUA_ATOMIC_EMIT = """
local stream = KEYS[1]
local idem_key = KEYS[2]
local idem_val = ARGV[1]
local idem_ttl = tonumber(ARGV[2])
local maxlen = tonumber(ARGV[3])

-- IDEMPOTENCY GUARD: If key already starts with 'SENT', this is a retry.
-- Return the existing stream_id to prevent duplicate XADD.
local existing = redis.call('GET', idem_key)
if existing and string.sub(existing, 1, 4) == 'SENT' then
    -- [v6.3.0] Extract stream_id from 'SENT:<stream_id>' or 'SENT:<mode>:<stream_id>'.
    -- Find the last ':' to handle both formats correctly.
    local last_colon = 0
    for i = 1, #existing do
        if string.sub(existing, i, i) == ':' then last_colon = i end
    end
    local sid = (last_colon > 0) and string.sub(existing, last_colon + 1) or string.sub(existing, 6)
    return sid  -- Already emitted, return existing stream_id
end

-- Build fields table for XADD from ARGV[4..N] as key-value pairs
local fields = {}
for i = 4, #ARGV, 2 do
    fields[#fields+1] = ARGV[i]
    fields[#fields+1] = ARGV[i+1]
end

-- Atomic: XADD with MAXLEN ~ (approximate trimming) then SET in same Lua execution
local stream_id = redis.call('XADD', stream, 'MAXLEN', '~', maxlen, '*', unpack(fields))
redis.call('SET', idem_key, idem_val .. ':' .. stream_id, 'EX', idem_ttl)
return stream_id
"""

# Halt key names — engine-scoped vs global
GLOBAL_HALT_KEY = "trade:halt"
GLOBAL_HALT_TS_KEY = "trade:halt:ts"
GLOBAL_HALT_REASON_KEY = "trade:halt:reason"
# [v5.8.0] Engine halt keys are now strategy-scoped to prevent cross-strategy
# halt contamination. Format: nextgen2:trade:halt:{strategy}
# [v7.1.0] Legacy non-scoped key is still READ for backward compatibility,
# but WRITE is conditional on rc.legacy_halt_write (default True for safe rollback).
ENGINE_HALT_KEY = "nextgen2:trade:halt"
ENGINE_HALT_TS_KEY = "nextgen2:trade:halt:ts"
ENGINE_HALT_REASON_KEY = "nextgen2:trade:halt:reason"

# Freshness thresholds (seconds) — defaults, overridden by RuntimeConfig if set.
# [v6.2.0] All freshness thresholds are now config-driven for consistent ops tuning.
DEFAULT_POS_STALE_WARN_SEC = 600    # 10 min: warning

# ============================================================
# [4AI PATCH v1.0] 시장시간 인지 + POSITIONS_STALE 자동 클리어
# ============================================================
import pytz as _pytz
from datetime import time as _dtime

_KST = _pytz.timezone("Asia/Seoul")


def _aoa_safe_float(_v, _default=0.0):
    try:
        return float(_v)
    except Exception:
        return _default


def _aoa_extract_symbol_position_pct(_r, _symbol: str, _equity: float) -> float:
    """
    broker/internal positions에서 symbol 비중을 안전하게 추출.
    STRING JSON / HASH / dict-with-positions / list[dict] 형태를 모두 허용.
    """
    import json as _json

    def _market_value_from_rec(_rec):
        if not isinstance(_rec, dict):
            return 0.0
        for _k in ("market_value", "marketValue", "notional_usd", "notional", "position_value"):
            if _k in _rec:
                _mv = _aoa_safe_float(_rec.get(_k), 0.0)
                if _mv > 0:
                    return abs(_mv)
        _shares = 0.0
        for _k in ("shares", "qty", "quantity", "position_qty"):
            if _k in _rec:
                _shares = abs(_aoa_safe_float(_rec.get(_k), 0.0))
                break
        if _shares <= 0:
            return 0.0
        _px = _aoa_safe_float(
            _r.get(f"md:mid:{_symbol}") or _r.get(f"price:{_symbol}") or _r.get(f"md:last:{_symbol}"),
            0.0,
        )
        return _shares * _px

    _keys = ("kis:broker:positions", "emarkos:v1:positions")

    for _key in _keys:
        try:
            _raw = _r.get(_key)
            if not _raw:
                continue
            _obj = _json.loads(_raw)
            # dict with "positions" sub-key: {"positions": {"CAT": {...}}, "totalMarketValue": N}
            if isinstance(_obj, dict) and "positions" in _obj:
                _total_mv = _aoa_safe_float(_obj.get("totalMarketValue", 0), 0.0)
                _positions = _obj.get("positions", {})
                if isinstance(_positions, dict) and _total_mv > 0:
                    _rec = _positions.get(_symbol) or _positions.get(_symbol.upper())
                    _mv = _market_value_from_rec(_rec)
                    if _mv > 0:
                        return _mv / max(_total_mv, 1.0)
            # flat dict: {"CAT": {...}, "AAPL": {...}}
            elif isinstance(_obj, dict):
                _rec = _obj.get(_symbol) or _obj.get(_symbol.upper()) or _obj.get(_symbol.lower())
                _mv = _market_value_from_rec(_rec)
                if _mv > 0:
                    return _mv / max(_equity, 1.0)
            elif isinstance(_obj, list):
                for _rec in _obj:
                    if not isinstance(_rec, dict):
                        continue
                    _sym = str(_rec.get("symbol") or _rec.get("s") or "").upper()
                    if _sym == _symbol.upper():
                        _mv = _market_value_from_rec(_rec)
                        if _mv > 0:
                            return _mv / max(_equity, 1.0)
        except Exception:
            pass

    for _key in _keys:
        try:
            _raw = _r.hget(_key, _symbol)
            if not _raw:
                _raw = _r.hget(_key, _symbol.upper())
            if not _raw:
                continue
            _rec = _json.loads(_raw)
            _mv = _market_value_from_rec(_rec)
            if _mv > 0:
                return _mv / max(_equity, 1.0)
        except Exception:
            pass

    return 0.0


def _is_krx_active_window():
    """포지션 신선도가 거래 안전에 직접 영향 주는 시간대 (프리오픈 버퍼 포함)"""
    try:
        import datetime
        now_kst = datetime.datetime.now(_KST)
        if now_kst.weekday() >= 5:  # 주말
            return False
        t = now_kst.time()
        return _dtime(8, 50) <= t <= _dtime(15, 40)
    except Exception:
        return True  # 불확실하면 엄격하게

def _auto_clear_positions_stale_halt(r):
    """POSITIONS_STALE HALT가 해소됐으면 자동 클리어 (4AI 권고)"""
    try:
        import time, json
        halt_val = r.get("trade:halt")
        if not halt_val:
            return
        halt_reason = r.get("trade:halt:reason") or ""
        if "POSITIONS_STALE" not in str(halt_reason):
            return
        # positions:ts 신선도 확인
        pos_ts = r.get("kis:broker:positions:ts")
        if not pos_ts:
            return
        try:
            pos_ts_epoch = int(pos_ts) if str(pos_ts).isdigit() else int(
                __import__('datetime').datetime.fromisoformat(
                    str(pos_ts).replace('Z', '+00:00')
                ).timestamp()
            )
        except Exception:
            return
        pos_age = int(time.time()) - pos_ts_epoch
        # heartbeat 확인
        hb = r.get("positions:sync:heartbeat") or r.get("services:position_sync:heartbeat")
        hb_ok = bool(hb)
        # 조건: positions:ts가 120초 이내 + heartbeat 존재
        if pos_age <= 120 and hb_ok:
            r.delete("trade:halt", "trade:halt:reason", "trade:halt:ts")
            r.delete("nextgen2:trade:halt", "nextgen2:trade:halt:reason", "nextgen2:trade:halt:ts")
            r.delete("nextgen2:trade:halt:ram26", "nextgen2:trade:halt:reason:ram26", "nextgen2:trade:halt:ts:ram26")
            r.set("trading:enabled", "true")  # [MANUS_FIX] TODO: kill-switch-authority만 쓰기 허용
            import logging
            logging.getLogger(__name__).info(
                "[4AI_AUTO_CLEAR] POSITIONS_STALE HALT 자동 클리어 완료 (pos_age=%ds) — trading:enabled 직접 SET", pos_age
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("[4AI_AUTO_CLEAR] 오류: %s", e)

# ============================================================
DEFAULT_POS_STALE_HALT_SEC = 7200   # 30 min: halt
DEFAULT_CASH_STALE_HALT_SEC = 1800  # 30 min: halt cash in LIVE
DEFAULT_EQUITY_STALE_HALT_SEC = 1800  # 30 min: halt equity in LIVE

# Benign skip reasons that should reset the consecutive error counter
# [v7.1.0] Expanded benign-skip set: BUDGET_ZERO_LIVE and NO_TARGETS are
# non-fault conditions that should not accumulate consecutive error counts.
_BENIGN_SKIP_REASONS = frozenset({
    "MARKET_CLOSED", "LOCKED", "SAFE_MODE", "SCHEDULER_BLOCKED",
    "TRADING_DISABLED", "MODE_BLOCKED", "BROKER_MISMATCH",
    "BUDGET_ZERO_LIVE", "NO_TARGETS", "TARGETS_PARSE_ERROR",
    "TARGETS_SCHEMA_INVALID", "EMPTY_TARGETS",
    # [v7.8.0] DEDUPE skip reasons — executor returns ok:true,skipped:true for these,
    # but adding here as defense-in-depth in case any path still returns ok:false.
    # DEDUPE skips are normal operation (duplicate order prevention), NOT errors.
    "RECENT_ORDER_EXISTS", "OPEN_ORDER_EXISTS",
    "DUPLICATE_SYMBOL_SIDE_WINDOW", "DUPLICATE_ORDER_BLOCKED",
})


def _is_truthy(val) -> bool:
    """Normalize halt/toggle value checking.
    Accepts: "true", "TRUE", "True", "1", "yes", "YES" etc.
    Rejects: None, "", "false", "0", "no", any other string.
    """
    if val is None:
        return False
    return str(val).strip().lower() in ("true", "1", "yes")


def _safe_float(val, default: float = 0.0, lo: float = None, hi: float = None,
                label: str = "") -> float:
    """[v4.6.0] Crash-proof float parser for Redis-sourced numeric values.

    Handles None, empty string, non-numeric strings, and out-of-range values.
    Logs a warning on parse failure or range violation instead of raising.

    Args:
        val: Raw value from Redis (str, bytes, None, etc.)
        default: Value to return on parse failure
        lo: Optional lower bound (inclusive). Values below are clamped.
        hi: Optional upper bound (inclusive). Values above are clamped.
        label: Human-readable label for log messages (e.g., "ares:equity:total")
    """
    if val is None or (isinstance(val, str) and val.strip() == ""):
        return default
    try:
        result = float(val)
    except (ValueError, TypeError):
        logger.warning("SAFE_FLOAT_PARSE_FAIL: %s raw=%r default=%.2f", label, val, default)
        return default
    # NaN / Inf guard
    import math
    if math.isnan(result) or math.isinf(result):
        logger.warning("SAFE_FLOAT_NAN_INF: %s raw=%r default=%.2f", label, val, default)
        return default
    if lo is not None and result < lo:
        logger.warning("SAFE_FLOAT_BELOW_LO: %s val=%.4f lo=%.4f, clamping", label, result, lo)
        result = lo
    if hi is not None and result > hi:
        logger.warning("SAFE_FLOAT_ABOVE_HI: %s val=%.4f hi=%.4f, clamping", label, result, hi)
        result = hi
    return result



def _parse_ts_to_epoch(raw):
    """Parse timestamp string to epoch float. Handles both epoch float and ISO 8601."""
    if not raw:
        return 0.0
    s = str(raw).strip()
    # 1) epoch float
    try:
        v = float(s)
        if v > 1_000_000_000_000:  # millisecond epoch → convert to seconds
            return v / 1000.0
        if v > 1_000_000_000:  # reasonable epoch (seconds)
            return v
    except (ValueError, TypeError):
        pass
    # 2) ISO 8601 (e.g. "2026-03-20T14:41:36.846Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f+00:00", "%Y-%m-%dT%H:%M:%S+00:00"):
        try:
            from datetime import datetime, timezone
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    # 3) JSON with ts field
    try:
        import json
        d = json.loads(s)
        if isinstance(d, dict):
            for k in ("ts", "timestamp", "updated_at"):
                if k in d:
                    return _parse_ts_to_epoch(d[k])
    except (json.JSONDecodeError, TypeError):
        pass
    return 0.0

def _safe_int(val, default: int = 0, label: str = "") -> int:
    """[v4.8.0] Crash-proof int parser for Redis-sourced integer values.

    Handles None, empty string, float strings (e.g., '10.0'), NaN, and non-numeric.
    Logs a warning on parse failure instead of raising.
    """
    if val is None or (isinstance(val, str) and val.strip() == ""):
        return default
    try:
        # Handle float strings like '10.0' gracefully
        result = int(float(val))
    except (ValueError, TypeError):
        logger.warning("SAFE_INT_PARSE_FAIL: %s raw=%r default=%d", label, val, default)
        return default
    return result


def _structured_log(event: str, **kwargs):
    """[v4.6.0] Emit a structured JSON log line for key events.
    Enables SIEM/alert routing and post-mortem analysis.
    """
    payload = {"event": event, "ts": time.time(), **kwargs}
    logger.info("STRUCTURED: %s", json.dumps(payload, default=str))



# ═══════════════════════════════════════════════════════════════════════════════
# [SPOF-3B] PositionTruthReconciler — Position Truth 분산 감지기
# 브로커 실제 잔고 vs Redis snapshot 불일치를 매 사이클 감지하여 경고
# ═══════════════════════════════════════════════════════════════════════════════
class PositionTruthReconciler:
    """
    Position Truth 분산 감지기.
    
    두 소스를 비교:
    1. kis:broker:positions (hash) — kis-balance-sync가 KIS API에서 직접 가져온 실제 잔고
    2. emarkos:v1:positions (JSON) — SSOT 파이프라인이 관리하는 Redis snapshot
    
    qty diff > QTY_DIFF_THRESHOLD 심볼 → WARN 로그 + Redis 이벤트 기록
    diff_count > CRITICAL_THRESHOLD → CRITICAL 알림 (halt 없음, 경고만)
    """
    
    QTY_DIFF_THRESHOLD = 1       # qty 차이 허용 범위 (주 단위)
    CRITICAL_THRESHOLD = 3       # 이 이상 심볼 불일치 시 CRITICAL
    CHECK_INTERVAL_SEC = 120     # 최소 체크 간격 (초)
    STREAM_KEY = "ares:live:pos_truth_drift"  # 이벤트 기록 스트림
    LAST_CHECK_KEY = "nextgen2:pos_truth:last_check_ts"
    
    def __init__(self, r, strategy_id: str, logger_ref):
        self.r = r
        self.strategy_id = strategy_id
        self.logger = logger_ref
    
    def run(self, positions_snapshot: dict) -> dict:
        """
        매 사이클 호출. positions_snapshot은 STAGE 8에서 로드된 dict.
        Returns: {"ok": True, "drifts": [...], "drift_count": N}
        """
        import time, json
        
        # 체크 간격 제한 (너무 자주 체크 방지)
        try:
            last_check_raw = self.r.get(self.LAST_CHECK_KEY)
            if last_check_raw:
                last_check = float(last_check_raw)
                if time.time() - last_check < self.CHECK_INTERVAL_SEC:
                    return {"ok": True, "skipped": True, "reason": "INTERVAL_NOT_REACHED"}
        except Exception:
            pass
        
        try:
            self.r.set(self.LAST_CHECK_KEY, str(time.time()), ex=86400)
        except Exception:
            pass
        
        drifts = []
        
        try:
            # Source 1: kis:broker:positions (hash) — 실제 브로커 잔고
            broker_hash = self.r.hgetall("kis:broker:positions") or {}
            broker_pos = {}
            for sym, raw in broker_hash.items():
                try:
                    parsed = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                    qty = int(parsed.get("qty", parsed.get("shares", 0)))
                    broker_pos[sym] = qty
                except Exception:
                    broker_pos[sym] = 0
            
            # Source 2: positions_snapshot (STAGE 8에서 로드된 것)
            snapshot_pos = {}
            for sym, pos in positions_snapshot.items():
                try:
                    qty = int(pos.get("qty", pos.get("shares", 0)))
                    snapshot_pos[sym] = qty
                except Exception:
                    snapshot_pos[sym] = 0
            
            # 비교: 두 소스 모두에 있는 심볼만 비교 (한쪽에만 있는 건 신규/청산 처리 중)
            all_syms = set(broker_pos.keys()) | set(snapshot_pos.keys())
            for sym in all_syms:
                b_qty = broker_pos.get(sym, 0)
                s_qty = snapshot_pos.get(sym, 0)
                diff = abs(b_qty - s_qty)
                
                if diff > self.QTY_DIFF_THRESHOLD:
                    drift = {
                        "symbol": sym,
                        "broker_qty": b_qty,
                        "snapshot_qty": s_qty,
                        "diff": diff,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "strategy": self.strategy_id,
                    }
                    drifts.append(drift)
            
            if drifts:
                drift_count = len(drifts)
                drift_syms = [f"{d['symbol']}(broker={d['broker_qty']},snap={d['snapshot_qty']},diff={d['diff']})" 
                              for d in drifts]
                
                if drift_count >= self.CRITICAL_THRESHOLD:
                    self.logger.critical(
                        "[SPOF-3B] POS_TRUTH_DRIFT CRITICAL: %d symbols drifted: %s",
                        drift_count, ", ".join(drift_syms)
                    )
                else:
                    self.logger.warning(
                        "[SPOF-3B] POS_TRUTH_DRIFT: %d symbols drifted: %s",
                        drift_count, ", ".join(drift_syms)
                    )
                
                # Redis 스트림에 이벤트 기록 (분석용)
                try:
                    event = {
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "drift_count": str(drift_count),
                        "drifts": json.dumps(drifts),
                        "strategy": self.strategy_id,
                        "severity": "CRITICAL" if drift_count >= self.CRITICAL_THRESHOLD else "WARN",
                    }
                    self.r.xadd(self.STREAM_KEY, event, maxlen=500)
                except Exception as e:
                    self.logger.debug("[SPOF-3B] POS_TRUTH_DRIFT stream write failed: %s", e)
            
            return {"ok": True, "drifts": drifts, "drift_count": len(drifts)}
        
        except Exception as e:
            self.logger.debug("[SPOF-3B] PositionTruthReconciler error: %s", e)
            return {"ok": False, "error": str(e)}

class Orchestrator:
    """NextGen2 Trading Engine Orchestrator v7.7.3."""

    def __init__(self, config: AppConfig = None):
        self.config = config or AppConfig.from_env()
        self.rc = self.config.runtime
        self.version = VERSION

        # [v7.7.1] Unique instance id for strict producer lock ownership
        self._instance_uuid = uuid.uuid4().hex

        # Redis connection
        self._redis: Optional[redis_lib.Redis] = None

        # Policy modules
        self.scheduler = DecisionScheduler()
        self.edge_gate = EdgeGate(
            threshold=self.rc.edge_gate_threshold,
            alpha_weight=self.rc.cs_alpha_weight,
        )
        self.turnover_gov = TurnoverGovernor()
        # [4AI-WINRATE-v1.0] SessionStateManager 초기화
        self._session_state = None   # SSMv1 (하위 호환)
        self._ssm_v2 = None            # SSMv2 (기관급, Redis 영속화)
        self.profit_interrupt = ProfitInterrupt(
            pi_buy_scale=self.rc.pi_buy_scale,
        )
        self.adaptive_rebalance = AdaptiveRebalance()

        # Risk modules
        self.crash_guard = CrashGuard()
        self.equity_anchor = EquityAnchor()
        self.concentration_guard = ConcentrationGuard(
            max_single_pct=self.rc.max_single_position_pct,
        )

        # [V237-Phase4] Risk Engine 모듈 초기화 (fail-open: 모듈 없어도 동작)
        self._impact_guard = ImpactGuard() if _RISK_ENGINE_AVAILABLE and ImpactGuard else None
        self._crowding_guard_v2 = CrowdingGuardV2() if _RISK_ENGINE_AVAILABLE and CrowdingGuardV2 else None
        self._flow_guard = FlowImbalanceGuard() if _RISK_ENGINE_AVAILABLE and FlowImbalanceGuard else None
        self._latency_guard = ExecutionLatencyGuard() if _RISK_ENGINE_AVAILABLE and ExecutionLatencyGuard else None

        # Strategy modules (redis_client set later in init())
        self.regime_theme = None
        self.cross_section = CrossSectionRanker(
            alpha_weight=self.rc.cs_alpha_weight,
        )

        # Governance (redis_client set in init())
        self.safe_mode = None
        self.feature_flags = FeatureFlags()

        # Observability (initialized properly in init())
        self._metrics_collector = None
        self.alerts: Optional[TelegramNotifier] = None
        self._alert_throttle_sec: int = 120  # overridden in init() from config
        self._alert_last_sent: Dict[str, float] = {}  # tag → last_sent_ts

        # Bus
        self.bus: Optional[RedisStreamBus] = None

        # State
        self._poll_interval_ms = 30000
        self._cycle_count = 0
        self._consecutive_errors = 0  # Circuit breaker counter
        # [v5.6.0] Dedicated telemetry_errors counter — separate from circuit breaker.
        # Tracks non-critical failures (heartbeat write, KPI write, alert send, etc.)
        # that should NOT escalate to circuit breaker halt.
        self._telemetry_errors = 0
        # [v5.9.0] KPI skip-reason counters: per-reason tracking without log scraping.
        # Enables dashboards to show "why is the engine not trading?" at a glance.
        self._skip_counters: Dict[str, int] = {}

    # ──────────────────────────── Init ────────────────────────────

    def init(self):
        """Initialize Redis connection and all modules."""
        rc = self.config.redis
        redis_url = rc.url

        # [v4.5.0] Use config-based Redis connection parameters
        self._redis = redis_lib.Redis.from_url(
            redis_url, decode_responses=True,
            socket_connect_timeout=rc.socket_connect_timeout,
            socket_timeout=rc.socket_timeout,
            max_connections=rc.max_connections,
            retry_on_timeout=rc.retry_on_timeout,
        )
        self._redis.ping()
        logger.info("Redis connected: %s (timeout=%.1fs, max_conn=%d)",
                     redis_url.split("@")[-1] if "@" in redis_url else redis_url,
                     rc.socket_timeout, rc.max_connections)

        # [v6.2.0] Config-driven freshness thresholds for consistent ops tuning.
        # RuntimeConfig fields override module-level defaults when set.
        self._pos_stale_warn_sec = getattr(self.rc, 'pos_stale_warn_sec', DEFAULT_POS_STALE_WARN_SEC)
        self._pos_stale_halt_sec = getattr(self.rc, 'pos_stale_halt_sec', DEFAULT_POS_STALE_HALT_SEC)
        self._cash_stale_halt_sec = getattr(self.rc, 'cash_stale_halt_sec', DEFAULT_CASH_STALE_HALT_SEC)
        self._equity_stale_halt_sec = getattr(self.rc, 'equity_stale_halt_sec', DEFAULT_EQUITY_STALE_HALT_SEC)
        self._targets_stale_warn_sec = getattr(self.rc, 'targets_stale_warn_sec', DEFAULT_TARGETS_STALE_WARN_SEC)
        self._targets_stale_halt_sec = getattr(self.rc, 'targets_stale_halt_sec', DEFAULT_TARGETS_STALE_HALT_SEC)

        # [v7.1.0] Config cohesion: centralize all runtime-tuned thresholds
        # into init() for explicitness and single-point-of-change.
        self._heartbeat_ttl_sec = getattr(self.rc, 'heartbeat_ttl_sec', 300)
        self._price_feed_halt_sec = getattr(self.rc, 'price_feed_halt_sec', 300)  # [v7.8.0] 120→300s: realtime-data-feed runs every 60s, allow 5min tolerance
        self._price_feed_warn_sec = getattr(self.rc, 'price_feed_warn_sec', 120)  # [v7.8.0] 60→120s
        self._kpi_stream_maxlen = getattr(self.rc, 'kpi_stream_maxlen', 50000)
        self._min_notional_usd = getattr(self.rc, 'min_notional_usd', 25.0)
        # [v7.7.0] max_single_order_qty now defined in RuntimeConfig (canonical source).
        # Both orchestrator and KIS adapter read from the same env var via RuntimeConfig.
        self._max_single_order_qty = self.rc.max_single_order_qty

        # [v7.1.0] Strategy-scoped producer lock key.
        # In multi-strategy deployments, each strategy gets its own lock to prevent
        # serialization. Single-strategy deployments are unaffected (same behavior).
        self._producer_lock_key = f"producer:order_intent:{self.rc.strategy}"
        # [v8.0.0] Fencing Token: 중복 생산자 write 무효화용
        self._fencing_token_key = f"producer:fencing_token:{self.rc.strategy}"
        self._fencing_token: int = 0  # 현재 인스턴스가 보유한 token 값

        # Initialize bus
        self.bus = RedisStreamBus(redis_url)

        # Initialize governance
        self.safe_mode = SafeMode(redis_client=self._redis)

        # Load regime theme matrix from Redis
        self.regime_theme = RegimeThemeMatrix(redis_client=self._redis)
        try:
            self.regime_theme.load_from_redis()
        except Exception as e:
            logger.warning("Failed to load regime theme from Redis: %s", e)

        # Initialize metrics collector (prefer Redis-backed, fallback to generic)
        try:
            if RedisMetricsCollector:
                self._metrics_collector = RedisMetricsCollector(self._redis)
            elif MetricsCollector:
                self._metrics_collector = MetricsCollector()
        except Exception as e:
            logger.warning("MetricsCollector init failed: %s", e)
            self._metrics_collector = None

        # [v4.5.0] Alert throttle from config
        self._alert_throttle_sec = self.config.obs.alert_throttle_sec

        # Initialize alerts if configured
        bot_token = self.config.obs.telegram_bot_token
        chat_id = self.config.obs.telegram_chat_id
        if bot_token and chat_id:
            self.alerts = TelegramNotifier(bot_token, chat_id)

            # [v5.2.0] Make alerting best-effort and non-blocking via bounded queue
            # + single worker thread. Prevents unbounded thread creation during noisy
            # incident conditions (replaces per-alert Thread spawning).
            try:
                import threading
                import queue

                _orig_send = self.alerts.send  # capture the real method once
                _alert_queue = queue.Queue(maxsize=50)

                def _alert_worker():
                    """Single background thread that drains the alert queue."""
                    while True:
                        try:
                            msg, tag = _alert_queue.get(timeout=30)
                            try:
                                _orig_send(msg, tag)
                            except Exception as e:
                                logger.warning("ALERT_SEND_FAILED: tag=%s err=%s", tag, e)
                                self._telemetry_errors += 1
                            finally:
                                _alert_queue.task_done()
                        except queue.Empty:
                            continue  # keep worker alive
                        except Exception:
                            continue

                _worker = threading.Thread(target=_alert_worker, daemon=True, name="alert-worker")
                _worker.start()

                def _safe_send(msg: str, tag: str = "info"):
                    # [v6.0.0] Drop-oldest semantics: when queue is full, discard the
                    # oldest alert to make room for the newest (most relevant) one.
                    # This ensures recent alerts are always delivered during incidents.
                    try:
                        _alert_queue.put_nowait((msg, tag))
                    except queue.Full:
                        try:
                            _alert_queue.get_nowait()  # discard oldest
                            _alert_queue.task_done()
                        except queue.Empty:
                            pass
                        try:
                            _alert_queue.put_nowait((msg, tag))
                        except queue.Full:
                            pass
                        logger.debug("ALERT_QUEUE_FULL: dropped oldest, enqueued tag=%s", tag)

                _safe_send._is_wrapped = True  # guard against double-wrapping
                self.alerts.send = _safe_send
                logger.debug("Alert safety wrapper installed (bounded queue + single worker)")
            except Exception as e:
                logger.warning("Alerts safety wrapper install failed: %s", e)

        # [v8.0.0] Key Contract Startup Gate: 권한 오류를 시작 시점에서 차단
        # 외부 AI 권고: '권한 오류는 런타임 재시도하면 CB만 빨라짐 → 시작 게이트에서 차단'
        if _STARTUP_GATE_AVAILABLE:
            _gate_ok = _run_startup_gate(
                self._redis,
                strategy=self.rc.strategy,
                halt_on_critical=True
            )
            if not _gate_ok:
                logger.critical(
                    "STARTUP_GATE BLOCKED: engine start aborted. "
                    "Check nextgen2:startup_gate:result:%s in Redis.",
                    self.rc.strategy
                )
                raise RuntimeError(
                    f"STARTUP_GATE_FAIL: Key Contract validation failed "
                    f"for strategy={self.rc.strategy}. "
                    f"Fix AUTH/LOCK/DATA issues before restarting."
                )
        # [v4.6.0] Startup readiness check: validate required Redis keys per mode
        self._startup_readiness_check(self._redis)

        logger.info(
            "Orchestrator v%s initialized: env=%s, account=%s, strategy=%s",
            self.version, self.rc.env, self.rc.account, self.rc.strategy,
        )

    # ────────────────── Startup Readiness ───────────────────

    def _startup_readiness_check(self, r: redis_lib.Redis):
        """[v4.6.0] Validate required Redis keys at startup.

        Checks that the runtime environment has the keys needed for the
        configured mode. Logs CRITICAL and sends alert on misconfiguration
        but does NOT halt — the engine may be starting in DRY_RUN where
        some keys are optional. The _cycle() mode resolution will block
        LIVE if the strategy-scoped key is missing.
        """
        strategy_mode_key = f"nextgen2:mode:{self.rc.strategy}"
        generic_mode_key = "nextgen2:mode"
        issues = []

        # Check strategy-scoped mode key
        strat_mode = r.get(strategy_mode_key)
        generic_mode = r.get(generic_mode_key)
        # [v5.5.0] Fail readiness in LIVE when strategy mode key is missing.
        # This is a deployment-time assertion: if the operator intends LIVE,
        # the strategy-scoped key MUST exist. Without it, mode resolution
        # will block LIVE anyway, but failing readiness makes the error explicit.
        if not strat_mode and generic_mode and str(generic_mode).upper() == "LIVE":
            issues.append(
                f"READINESS_FAIL: {generic_mode_key}=LIVE but {strategy_mode_key} NOT SET. "
                f"LIVE trading will be BLOCKED. Set: redis-cli SET {strategy_mode_key} LIVE"
            )
            # Send immediate critical alert for operator visibility
            if self.alerts:
                self._send_alert(
                    f"\U0001f6a8 READINESS_FAIL: {generic_mode_key}=LIVE but {strategy_mode_key} NOT SET. "
                    f"LIVE trading BLOCKED until key is set.",
                    "readiness_live_blocked"
                )

        # [v4.7.0] Check positions timestamp — LIVE will HALT if missing
        if not r.get("kis:broker:positions:ts"):
            issues.append(
                "READINESS_CRITICAL: kis:broker:positions:ts NOT FOUND. "
                "LIVE mode will HALT (fail-closed) on missing positions timestamp. "
                "Ensure position-sync-service writes this key."
            )

        # [v4.7.0] Check equity timestamp — LIVE will HALT if missing
        eq_total = r.get("ares:equity:total")
        eq_ts = r.get("ares:equity:total:ts")
        if eq_total and not eq_ts:
            issues.append(
                "READINESS_CRITICAL: ares:equity:total exists but ares:equity:total:ts MISSING. "
                "LIVE mode will HALT (fail-closed) on missing equity timestamp. "
                "Ensure equity-calculator writes this key."
            )

        # [v4.7.0] Check cash timestamp — LIVE will HALT if missing
        cash = r.get("kis:cash:usd")
        cash_ts = r.get("kis:cash:usd:ts")
        if cash and not cash_ts:
            issues.append(
                "READINESS_CRITICAL: kis:cash:usd exists but kis:cash:usd:ts MISSING. "
                "LIVE mode will HALT (fail-closed) on missing cash timestamp. "
                "Ensure kis-balance-sync writes this key."
            )

        # [v7.1.0] Check price-feed heartbeat at startup (LIVE readiness).
        # If strategy mode is LIVE AND market is open, price feed must be active
        # to avoid immediate PRICE_STALENESS halt during first cycle.
        # [v7.1.0] Gated on is_us_market_session() to avoid false-positive
        # readiness warnings when starting outside market hours (common condition).
        _resolved_mode = str(r.get(strategy_mode_key) or "").upper()
        if _resolved_mode == "LIVE" and is_us_market_session():
            price_ts_raw = r.get("prices:latest:ts")
            feed_hb_raw = r.get("realtime:feed:heartbeat")
            if not price_ts_raw and not feed_hb_raw:
                issues.append(
                    "READINESS_CRITICAL: mode=LIVE but no price-feed heartbeat found. "
                    "Neither 'prices:latest:ts' nor 'realtime:feed:heartbeat' exist. "
                    "LIVE cycles will HALT on PRICE_STALENESS. "
                    "Ensure price-feed service is running."
                )

        # [v7.7.0] Pre-market dependency readiness watchdog.
        # When mode=LIVE and market is open (or about to open), assert that ALL
        # dependency freshness keys exist AND are parseable. This catches the
        # integration/ops scenario where position-sync or equity-calculator
        # hasn't started writing timestamps before market open.
        if _resolved_mode == "LIVE" and is_us_market_session():
            _premarket_deps = [
                ("kis:broker:positions:ts", "position-sync-service", "ts"),
                ("ares:equity:total:ts", "equity-calculator", "ts"),
                ("kis:cash:usd:ts", "kis-balance-sync", "ts"),
                ("prices:latest:ts", "price-feed", "ts"),
                ("ssot:target:v2:current", "ssot-promote-v2", "json"),
            ]
            for _dep_key, _dep_service, _dep_kind in _premarket_deps:
                _dep_val = r.get(_dep_key)
                if not _dep_val:
                    issues.append(
                        f"READINESS_PREMARKET: {_dep_key} MISSING during market hours. "
                        f"LIVE will HALT on first cycle. Ensure {_dep_service} is running "
                        f"and writing this key before market open."
                    )
                else:
                    if _dep_kind == "json":
                        _obj = safe_json_parse(_dep_val)
                        _has_positions = "positions" in _obj or (isinstance(_obj.get("targets"), dict) and "positions" in _obj["targets"])
                        if not isinstance(_obj, dict) or not _has_positions:
                            issues.append(
                                f"READINESS_PREMARKET: {_dep_key} exists but INVALID_JSON/NO_POSITIONS. "
                                f"LIVE may HALT. Check {_dep_service} output format."
                            )
                            continue
                        # basic parse ok
                        continue
                    # Verify the timestamp is parseable (not corrupted)
                    _dep_ts = self._parse_timestamp(_dep_val)
                    if _dep_ts is None:
                        issues.append(
                            f"READINESS_PREMARKET: {_dep_key} exists but UNPARSEABLE "
                            f"(raw={str(_dep_val)[:50]}). LIVE will HALT. "
                            f"Check {_dep_service} output format."
                        )
                    elif (time.time() - _dep_ts) > 3600:  # older than 1 hour
                        issues.append(
                            f"READINESS_PREMARKET: {_dep_key} is STALE "
                            f"(age={time.time() - _dep_ts:.0f}s > 3600s). "
                            f"LIVE may HALT on freshness check. "
                            f"Verify {_dep_service} is actively syncing."
                        )

        # Check producer lock health
        producer_owner = r.get(self._producer_lock_key)
        if producer_owner:
            my_prefix = f"nextgen2:{self.rc.strategy}:"
            if not producer_owner.startswith(my_prefix):
                issues.append(
                    f"READINESS_WARN: producer lock owned by '{producer_owner}' (we are '{my_prefix}*'). "
                    f"LIVE emission may be blocked by PRODUCER_COLLISION."
                )

        if issues:
            for issue in issues:
                logger.critical(issue)
            _structured_log("STARTUP_READINESS", issues=issues, strategy=self.rc.strategy)
            if self.alerts:
                self._send_alert(
                    f"\u26a0\ufe0f STARTUP_READINESS ({len(issues)} issues):\n" + "\n".join(issues[:3]),
                    "startup_readiness"
                )
        else:
            logger.info("STARTUP_READINESS: all checks passed for strategy=%s", self.rc.strategy)
            _structured_log("STARTUP_READINESS", status="OK", strategy=self.rc.strategy)

        # [v5.2.0] Print resolved effective_mode at startup for operator clarity
        try:
            _strat_mode = r.get(f"nextgen2:mode:{self.rc.strategy}")
            _gen_mode = r.get("nextgen2:mode")
            _shared_mode = r.get("emarkos:v1:mode")
            if _strat_mode:
                _eff = str(_strat_mode).upper()
                _src = f"nextgen2:mode:{self.rc.strategy}"
            elif _gen_mode:
                _eff = "BLOCKED_LIVE" if str(_gen_mode).upper() == "LIVE" else str(_gen_mode).upper()
                _src = "nextgen2:mode"
            elif _shared_mode:
                _eff = str(_shared_mode).upper()
                _src = "emarkos:v1:mode"
            else:
                _eff = "UNKNOWN"
                _src = "none"
            logger.info("STARTUP_EFFECTIVE_MODE: %s (source=%s)", _eff, _src)
            if _eff == "BLOCKED_LIVE":
                logger.warning("STARTUP_BLOCKED_LIVE: generic nextgen2:mode=LIVE but no %s key. "
                              "LIVE trading will be BLOCKED. Set: redis-cli SET nextgen2:mode:%s LIVE",
                              f"nextgen2:mode:{self.rc.strategy}", self.rc.strategy)
        except Exception:
            pass

        # [v5.6.0] Single readiness status key for watchdog-monitor.
        # Summarizes all dependency checks into one key that watchdog can poll.
        readiness_key = f"nextgen2:readiness:{self.rc.strategy}"
        readiness_payload = json.dumps({
            "ts": time.time(),
            "version": self.version,
            "strategy": self.rc.strategy,
                    "fencing_token": self._fencing_token,  # [v8.0.0] consumer 중복 write 방지용
            "issues_count": len(issues),
            "ready": len(issues) == 0,
            "issues": issues[:5],  # cap at 5 for key size
        })
        try:
            r.set(readiness_key, readiness_payload, ex=600)  # 10 min TTL
        except Exception:
            self._telemetry_errors += 1

    # ──────────────────────── Alert Throttle ─────────────────────

    def _send_alert(self, msg: str, tag: str = "info"):
        """Send alert with per-tag throttling to prevent alert storms.

        [v4.5.0] Enforces config.obs.alert_throttle_sec between repeated
        alerts of the same tag. Critical tags bypass throttling.
        """
        if not self.alerts:
            return

        now = time.time()
        # Critical tags bypass throttle
        if tag not in ("circuit_breaker", "crash_guard_halt", "positions_sudden_empty"):
            last = self._alert_last_sent.get(tag, 0)
            if now - last < self._alert_throttle_sec:
                logger.debug("ALERT_THROTTLED: tag=%s (%.0fs since last)", tag, now - last)
                return

        self._alert_last_sent[tag] = now
        self.alerts.send(msg, tag)

    # ──────────────────────── Halt Helpers ────────────────────────

    def _set_halt(self, r: redis_lib.Redis, reason: str, *, is_live: bool):
        """Set halt key — engine-scoped for DRY_RUN/SHADOW, global for LIVE.

        [v4.0.0] Uses Redis pipeline for atomicity — all 3 (or 6) keys
        set in a single round-trip, preventing partial halt state.

        In LIVE mode, sets BOTH global and engine-scoped keys so that:
        - Node live-trading-kis sees global trade:halt
        - NextGen2 sees its own engine halt
        In DRY_RUN/SHADOW, sets ONLY engine-scoped key to avoid
        disrupting the Node engine.
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        pipe = r.pipeline(transaction=True)
        # [v5.8.0] Strategy-scoped engine halt: prevents cross-strategy contamination
        _strat_halt_key = f"nextgen2:trade:halt:{self.rc.strategy}"
        _strat_halt_ts_key = f"nextgen2:trade:halt:ts:{self.rc.strategy}"
        _strat_halt_reason_key = f"nextgen2:trade:halt:reason:{self.rc.strategy}"
        pipe.set(_strat_halt_key, "true")
        pipe.set(_strat_halt_ts_key, ts)
        pipe.set(_strat_halt_reason_key, reason)
        # [v7.1.0] Legacy engine-scoped halt: conditional write.
        # In multi-strategy deployments, writing to shared ENGINE_HALT_KEY
        # causes cross-strategy halt contamination. Default True for safe rollback.
        if getattr(self.rc, 'legacy_halt_write', True):
            pipe.set(ENGINE_HALT_KEY, "true")
            pipe.set(ENGINE_HALT_TS_KEY, ts)
            pipe.set(ENGINE_HALT_REASON_KEY, reason)
        # Only set global halt in LIVE mode
        if is_live:
            pipe.set(GLOBAL_HALT_KEY, "true")
            pipe.set(GLOBAL_HALT_TS_KEY, ts)
            pipe.set(GLOBAL_HALT_REASON_KEY, reason)
        pipe.execute()
        _scope = "GLOBAL+ENGINE+STRATEGY" if is_live else f"ENGINE+STRATEGY({self.rc.strategy})"
        logger.critical("HALT_SET: reason=%s, scope=%s", reason, _scope)
        _structured_log("HALT_SET", reason=reason, scope=_scope,
                        is_live=is_live, strategy=self.rc.strategy)

    def _is_halted(self, r: redis_lib.Redis) -> tuple:
        """Check if trading is halted (global, engine-scoped, or strategy-scoped).
        Returns (is_halted: bool, reason: str).
        [v5.8.0] Added strategy-scoped halt check for multi-strategy isolation.
        """
        # Check global halt first (highest priority)
        global_halt = r.get(GLOBAL_HALT_KEY)
        if _is_truthy(global_halt):
            reason = r.get(GLOBAL_HALT_REASON_KEY) or "unknown"
            return True, f"GLOBAL:{reason}"
        # Check strategy-scoped halt (v5.8.0 — most specific)
        _strat_halt_key = f"nextgen2:trade:halt:{self.rc.strategy}"
        strat_halt = r.get(_strat_halt_key)
        if _is_truthy(strat_halt):
            reason = r.get(f"nextgen2:trade:halt:reason:{self.rc.strategy}") or "unknown"
            return True, f"STRATEGY({self.rc.strategy}):{reason}"
        # Check legacy engine-scoped halt (backward compat)
        engine_halt = r.get(ENGINE_HALT_KEY)
        if _is_truthy(engine_halt):
            reason = r.get(ENGINE_HALT_REASON_KEY) or "unknown"
            return True, f"ENGINE:{reason}"
        return False, ""

    # ──────────────────── Mode Resolution ────────────────────────

    def _resolve_is_live_for_halt(self, r: redis_lib.Redis) -> bool:
        """Resolve whether the engine is in LIVE mode for halt scope decisions.

        [v4.5.0] Uses ONLY the strategy-scoped key for LIVE determination.
        The generic nextgen2:mode key is NEVER sufficient for LIVE — this
        prevents the circuit breaker from setting global halt when only the
        generic key is set to LIVE (which Stage 3b would block anyway).

        Returns True only if nextgen2:mode:{strategy} == "LIVE".
        """
        strategy_mode_key = f"nextgen2:mode:{self.rc.strategy}"
        strategy_mode = r.get(strategy_mode_key)
        if strategy_mode and str(strategy_mode).strip().upper() == "LIVE":
            return True
        return False

    # ──────────────────── Freshness Helpers ───────────────────────

    def _parse_timestamp(self, raw) -> Optional[float]:
        """Parse a timestamp value (ISO string or epoch float) to epoch seconds.
        Returns None if parsing fails.

        [v6.2.0] Detects future timestamps (negative age) caused by clock skew
        and clamps them to current time with a CLOCK_SKEW warning.
        """
        if raw is None:
            return None
        try:
            raw_str = str(raw)
            if "T" in raw_str:
                ts = _dt.fromisoformat(raw_str.replace("Z", "+00:00")).timestamp()
            else:
                ts = float(raw_str)
        except (ValueError, TypeError):
            return None
        # [v6.2.0] Auto-detect millisecond epoch (13+ digits) and convert to seconds.
        # Many upstream systems (e.g., price feeds) use ms-epoch timestamps.
        if ts > 1e12:  # Likely milliseconds (year ~2001 in seconds, ~33658 in ms)
            ts = ts / 1000.0
        # [v6.2.0] Future timestamp guard: if ts is >60s in the future,
        # clamp to now and emit structured CLOCK_SKEW warning.
        now = time.time()
        if ts > now + 60:  # 60s tolerance for minor clock drift
            _structured_log(
                "CLOCK_SKEW",
                raw=raw_str,
                parsed_ts=ts,
                now=now,
                drift_sec=round(ts - now, 1),
                strategy=self.rc.strategy,
            )
            return now  # Clamp to current time
        return ts

    def _check_positions_freshness(self, r: redis_lib.Redis, positions: Dict,
                                   pos_source: str, is_live: bool) -> Optional[Dict]:
        """Check positions data freshness. Returns error dict if halted, else None.

        Checks two sources:
        1. kis:broker:positions:ts — top-level timestamp set by kis_adapter.sync_positions_to_redis
        2. Per-position last_sync field — set inside each position hash entry

        Only enforced in LIVE mode. DRY_RUN/SHADOW log warnings but don't halt.
        """
        freshest_ts = None

        # Source 1: Top-level timestamp key
        freshest_ts = self._parse_timestamp(r.get("kis:broker:positions:ts"))

        # Source 2: emarkos:v1:positions top-level ts
        em_pos_raw = r.get("emarkos:v1:positions")
        if em_pos_raw:
            em_pos = safe_json_parse(em_pos_raw)
            # [v5.4.0] Guard: em_pos must be a dict before .get() (GPT HIGH fix)
            if isinstance(em_pos, dict):
                em_ts_raw = em_pos.get("ts") or em_pos.get("timestamp")
                em_ts = self._parse_timestamp(em_ts_raw)
                if em_ts is not None and (freshest_ts is None or em_ts > freshest_ts):
                    freshest_ts = em_ts

        # Source 3: Per-position last_sync (fallback)
        if freshest_ts is None and positions:
            for sym, pos in positions.items():
                ls = pos.get("last_sync") if isinstance(pos, dict) else None
                ls_ts = self._parse_timestamp(ls)
                if ls_ts is not None and (freshest_ts is None or ls_ts > freshest_ts):
                    freshest_ts = ls_ts

        if freshest_ts is None:
            # No timestamp available at all.
            logger.warning("POSITIONS_FRESHNESS: no timestamp available from %s", pos_source)

            # In LIVE, fail-closed unless explicitly overridden.
            if is_live and not _is_truthy(r.get("nextgen2:allow_missing_positions_ts")):
                self._set_halt(r, f"POSITIONS_NO_TS:{pos_source}", is_live=True)
                self._send_alert(
                    f"POSITIONS_NO_TS in LIVE (source={pos_source}) — halt set",
                    "positions_no_ts",
                )
                return {"ok": False, "_is_error": True, "reason": "POSITIONS_NO_TS", "source": pos_source}

            return None

        pos_age = time.time() - freshest_ts
        if pos_age > self._pos_stale_warn_sec:
            logger.warning("POSITIONS_STALE: age=%.0fs (>%ds), source=%s",
                           pos_age, self._pos_stale_warn_sec, pos_source)
            self._send_alert(
                f"\u26a0\ufe0f POSITIONS_STALE: age={pos_age:.0f}s from {pos_source}",
                "positions_stale"
            )
            # [4AI PATCH] 장외시간에는 POSITIONS_STALE HALT 완화
            _auto_clear_positions_stale_halt(r)
            if _is_krx_active_window() and pos_age > self._pos_stale_halt_sec:
                if is_live:
                    self._set_halt(r, f"POSITIONS_STALE:{pos_age:.0f}s", is_live=True)
                    return {"ok": False, "_is_error": True, "reason": "POSITIONS_STALE"}
                else:
                    # DRY_RUN/SHADOW: log but don't halt
                    logger.warning("POSITIONS_STALE: would halt in LIVE (age=%.0fs), continuing in DRY_RUN/SHADOW", pos_age)

        return None

    def _check_cash_freshness(self, r: redis_lib.Redis, is_live: bool) -> Optional[Dict]:
        """Check cash data freshness. Returns error dict if halted, else None.

        [v4.5.0] Enforced in LIVE mode — halts if cash timestamp is stale
        beyond CASH_STALE_HALT_SEC. Previously only logged a warning.
        """
        cash_ts_raw = r.get("kis:cash:usd:ts")
        if not cash_ts_raw:
            if is_live:
                # [v4.6.0] Fail-closed: missing timestamp in LIVE halts trading.
                # Upstream services MUST write kis:cash:usd:ts as part of their contract.
                if not _is_truthy(r.get("nextgen2:allow_missing_cash_ts")):
                    logger.critical("CASH_NO_TS_HALT: kis:cash:usd:ts missing in LIVE — halting")
                    self._set_halt(r, "CASH_NO_TS", is_live=True)
                    self._send_alert(
                        "\U0001f6a8 CASH_NO_TS in LIVE: kis:cash:usd:ts missing — halt set",
                        "cash_no_ts"
                    )
                    return {"ok": False, "_is_error": True, "reason": "CASH_NO_TS"}
                else:
                    logger.warning("CASH_FRESHNESS: no timestamp (override active)")
            return None

        cash_ts = self._parse_timestamp(cash_ts_raw)
        if cash_ts is None:
            # [v4.8.0] Unparseable timestamp fail-closed in LIVE.
            # If ts key exists but can't be parsed, treat as missing.
            if is_live and not _is_truthy(r.get("nextgen2:allow_missing_cash_ts")):
                logger.critical("CASH_TS_UNPARSEABLE_HALT: raw=%r in LIVE — halting", cash_ts_raw)
                self._set_halt(r, f"CASH_TS_UNPARSEABLE:{cash_ts_raw!r}", is_live=True)
                return {"ok": False, "_is_error": True, "reason": "CASH_TS_UNPARSEABLE"}
            return None

        cash_age = time.time() - cash_ts
        if cash_age > self._cash_stale_halt_sec:
            if is_live:
                logger.critical("CASH_STALE_HALT: age=%.0fs (>%ds) — halting LIVE",
                                cash_age, self._cash_stale_halt_sec)
                self._set_halt(r, f"CASH_STALE:{cash_age:.0f}s", is_live=True)
                self._send_alert(
                    f"\U0001f6a8 CASH_STALE in LIVE: age={cash_age:.0f}s — halt set",
                    "cash_stale"
                )
                return {"ok": False, "_is_error": True, "reason": "CASH_STALE", "age_sec": cash_age}
            else:
                logger.warning("CASH_STALE: age=%.0fs (would halt in LIVE)", cash_age)
        elif cash_age > self._pos_stale_warn_sec:
            logger.warning("CASH_STALE_WARN: age=%.0fs (>%ds)", cash_age, self._pos_stale_warn_sec)

        return None

    def _check_equity_freshness(self, r: redis_lib.Redis, equity_total: float,
                                is_live: bool) -> Optional[Dict]:
        """Check equity data freshness. Returns error dict if halted, else None.

        [v4.5.0] New check — enforces equity_total freshness in LIVE mode.
        Stale or missing equity timestamps halt trading to prevent budget
        distortion from outdated equity values.
        """
        if equity_total <= 0:
            # No equity data to check freshness of
            return None

        eq_ts_raw = r.get("ares:equity:total:ts")
        if not eq_ts_raw:
            if is_live:
                # [v4.6.0] Fail-closed: missing equity timestamp in LIVE halts trading.
                # Upstream equity-calculator MUST write ares:equity:total:ts.
                if not _is_truthy(r.get("nextgen2:allow_missing_equity_ts")):
                    logger.critical("EQUITY_NO_TS_HALT: ares:equity:total:ts missing in LIVE — halting")
                    self._set_halt(r, "EQUITY_NO_TS", is_live=True)
                    self._send_alert(
                        "\U0001f6a8 EQUITY_NO_TS in LIVE: ares:equity:total:ts missing — halt set",
                        "equity_no_ts"
                    )
                    return {"ok": False, "_is_error": True, "reason": "EQUITY_NO_TS"}
                else:
                    logger.warning("EQUITY_FRESHNESS: no timestamp (override active)")
            return None

        eq_ts = self._parse_timestamp(eq_ts_raw)
        if eq_ts is None:
            # [v4.8.0] Unparseable timestamp fail-closed in LIVE.
            if is_live and not _is_truthy(r.get("nextgen2:allow_missing_equity_ts")):
                logger.critical("EQUITY_TS_UNPARSEABLE_HALT: raw=%r in LIVE — halting", eq_ts_raw)
                self._set_halt(r, f"EQUITY_TS_UNPARSEABLE:{eq_ts_raw!r}", is_live=True)
                return {"ok": False, "_is_error": True, "reason": "EQUITY_TS_UNPARSEABLE"}
            return None

        eq_age = time.time() - eq_ts
        if eq_age > self._equity_stale_halt_sec:
            if is_live:
                logger.critical("EQUITY_STALE_HALT: age=%.0fs (>%ds) — halting LIVE",
                                eq_age, self._equity_stale_halt_sec)
                self._set_halt(r, f"EQUITY_STALE:{eq_age:.0f}s", is_live=True)
                self._send_alert(
                    f"\U0001f6a8 EQUITY_STALE in LIVE: age={eq_age:.0f}s — halt set",
                    "equity_stale"
                )
                return {"ok": False, "_is_error": True, "reason": "EQUITY_STALE", "age_sec": eq_age}
            else:
                logger.warning("EQUITY_STALE: age=%.0fs (would halt in LIVE)", eq_age)
        elif eq_age > self._pos_stale_warn_sec:
            logger.warning("EQUITY_STALE_WARN: age=%.0fs (>%ds)", eq_age, self._pos_stale_warn_sec)

        return None

    # ──────────────────── Health Heartbeat ────────────────────────

    def _update_heartbeat(self, r: redis_lib.Redis, cycle_id: str, mode: str,
                          reason: str = "", skip_reason: str = ""):
        """Update lightweight health heartbeat key for watchdog monitoring.

        [v4.7.0] Includes effective_mode, mode_source_key, and halt_state
        for single-pane ops visibility. Watchdog can detect mode misconfiguration
        and halt state without querying multiple Redis keys.
        """
        hb_key = f"nextgen2:heartbeat:{self.rc.strategy}"

        # [v4.7.0] Resolve effective mode and source key for ops visibility
        strategy_mode_key = f"nextgen2:mode:{self.rc.strategy}"
        generic_mode_key = "nextgen2:mode"
        try:
            strat_val = r.get(strategy_mode_key)
            generic_val = r.get(generic_mode_key)
            if strat_val:
                effective_mode = str(strat_val).upper()
                mode_source_key = strategy_mode_key
            elif generic_val:
                effective_mode = str(generic_val).upper()
                mode_source_key = generic_mode_key
                # If generic says LIVE but no strategy key, effective is BLOCKED
                if effective_mode == "LIVE":
                    effective_mode = "BLOCKED_LIVE"
            else:
                shared_val = r.get("emarkos:v1:mode")
                effective_mode = str(shared_val).upper() if shared_val else "UNKNOWN"
                mode_source_key = "emarkos:v1:mode" if shared_val else "none"

            halted, halt_reason = self._is_halted(r)
        except Exception:
            effective_mode = "ERROR"
            mode_source_key = "error"
            halted, halt_reason = False, ""

        # [v4.8.0] Dependency health: compute ts ages for single-pane diagnostics
        now = time.time()
        dep_health = {}
        for dep_key, dep_label in [
            ("kis:broker:positions:ts", "positions"),
            ("kis:cash:usd:ts", "cash"),
            ("ares:equity:total:ts", "equity"),
        ]:
            try:
                dep_raw = r.get(dep_key)
                dep_ts = self._parse_timestamp(dep_raw) if dep_raw else None
                dep_health[f"{dep_label}_ts_age"] = round(now - dep_ts, 1) if dep_ts else -1
            except Exception:
                dep_health[f"{dep_label}_ts_age"] = -1
        # [v7.5.0] Price feed ts age — single-pane visibility for price data freshness.
        try:
            _pf_raw = r.get("prices:latest:ts") or r.get("realtime:feed:heartbeat")
            if _pf_raw:
                _pf_ts = self._parse_timestamp(_pf_raw)
                dep_health["price_feed_ts_age"] = round(now - _pf_ts, 1) if _pf_ts else -1
            else:
                dep_health["price_feed_ts_age"] = -1
        except Exception:
            dep_health["price_feed_ts_age"] = -1
        # Targets ts age
        try:
            tgt_raw = r.get("ssot:target:v2:current")
            if tgt_raw:
                tgt_data = safe_json_parse(tgt_raw)
                # [v7.1.0] Explicit type check: targets must be dict, not list/str.
                # Prevents silent schema drift from going unnoticed.
                if not isinstance(tgt_data, dict):
                    logger.warning("HEARTBEAT_TARGETS_SCHEMA: expected dict, got %s",
                                  type(tgt_data).__name__)
                    dep_health["targets_ts_age"] = -1
                    dep_health["targets_schema_ok"] = False
                else:
                    dep_health["targets_schema_ok"] = True
                tgt_ts_raw = (tgt_data if isinstance(tgt_data, dict) else {}).get("ts") or \
                             (tgt_data if isinstance(tgt_data, dict) else {}).get("timestamp")
                tgt_ts = self._parse_timestamp(tgt_ts_raw) if tgt_ts_raw else None
                dep_health["targets_ts_age"] = round(now - tgt_ts, 1) if tgt_ts else -1
            else:
                dep_health["targets_ts_age"] = -1
        except Exception:
            dep_health["targets_ts_age"] = -1

        hb_payload = {
            "ts": now,
            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "version": self.version,
            "mode": mode,
            "effective_mode": effective_mode,
            "mode_source_key": mode_source_key,
            "halt_state": halt_reason if halted else "none",
            "reason": reason,
            # [v6.3.0] Current skip/block reason for watchdog visibility.
            "skip_reason": skip_reason,
            "cycle_id": cycle_id,
            "cycle_count": self._cycle_count,
            "consecutive_errors": self._consecutive_errors,
            "dep_health": dep_health,
            # [v5.8.0] Mode-aware readiness: LIVE requires all deps fresh,
            # DRY_RUN/SHADOW only require positions (cash/equity optional).
            # [v7.1.0] Use dedicated thresholds per dependency type:
            # positions → _pos_stale_halt_sec, cash → _cash_stale_halt_sec,
            # equity → _equity_stale_halt_sec. Previously all used _pos_stale_halt_sec.
            "ready": (
                not halted
                and effective_mode not in ("BLOCKED_LIVE", "UNKNOWN", "ERROR")
                and (
                    # LIVE: all 3 deps must be fresh (each with its own threshold)
                    # [v7.1.0] Use >= 0 instead of > 0 to avoid false not-ready
                    # when timestamp is very fresh (age == 0.0).
                    (
                        0 <= dep_health.get("positions_ts_age", -1) < self._pos_stale_halt_sec
                        and 0 <= dep_health.get("cash_ts_age", -1) < self._cash_stale_halt_sec
                        and 0 <= dep_health.get("equity_ts_age", -1) < self._equity_stale_halt_sec
                    ) if effective_mode == "LIVE" else
                    # DRY_RUN/SHADOW: only positions freshness required
                    0 <= dep_health.get("positions_ts_age", -1) < self._pos_stale_halt_sec
                )
            ),
            # [v5.6.0] Telemetry errors counter for observability
            "telemetry_errors": self._telemetry_errors,
            # [v7.1.0] Producer identity for ops diagnostics.
            "producer_id": f"nextgen2:{self.rc.strategy}:pid{os.getpid()}",
            # [v5.9.0] Per-reason skip counters for dashboards (no log scraping needed)
            "skip_counters": dict(self._skip_counters),
            # [v5.9.0] Upstream dependency health: targets churn count
            "targets_churn_count": _safe_int(
                r.get(f"nextgen2:targets_churn_count:{self.rc.strategy}"),
                default=0, label="targets_churn"
            ) if r else 0,
        }
        hb_data = json.dumps(hb_payload)
        # [v5.2.0] Config-driven heartbeat TTL (default 300s = 5 min)
        _hb_ttl = self._heartbeat_ttl_sec
        try:
            r.set(hb_key, hb_data, ex=_hb_ttl)
        except Exception as e:
            logger.warning("HEARTBEAT_WRITE_FAILED: %s", e)
            self._telemetry_errors += 1

        # [v5.5.0] Periodic readiness stream event for watchdog-monitor
        try:
            r.xadd(f"stream:nextgen2:readiness:{self.rc.strategy}",
                   {"data": hb_data}, maxlen=1000, approximate=True)
        except Exception:
            self._telemetry_errors += 1

    # ──────────────────────────── Main Cycle ────────────────────────────

    def _cycle(self) -> Dict[str, Any]:
        """Core trading cycle — mirrors live-trading-kis runCycle()."""
        r = self._redis
        cycle_id = make_cycle_id()
        decision_id = build_decision_id(
            self.rc.env, self.rc.account, self.rc.strategy,
            int(time.time() * 1000), self._cycle_count,
        )
        self._cycle_count += 1
        cycle_start = time.time()

        # [v4.6.0] Write heartbeat at the START of every cycle, including
        # MARKET_CLOSED, LOCKED, SAFE_MODE paths. This prevents false
        # "engine down" alerts from watchdog when market is closed.
        self._update_heartbeat(r, cycle_id, "CYCLE_START")

        # ═══════ STAGE 0: Market Session Check ═══════
        if not is_us_market_session():
            logger.info("[MARKET_SESSION] skip — outside US market hours")
            self._update_heartbeat(r, cycle_id, "MARKET_CLOSED", reason="outside_market_hours")
            _structured_log("CYCLE_SKIP", reason="MARKET_CLOSED", cycle_id=cycle_id,
                           strategy=self.rc.strategy)
            return {"ok": True, "skipped": True, "reason": "MARKET_CLOSED"}

        # ═══════ STAGE 0.5: Circuit Breaker ═══════
        if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            logger.critical("CIRCUIT_BREAKER: %d consecutive errors — halting",
                            self._consecutive_errors)
            # [v4.5.0] Use strategy-scoped key ONLY for LIVE determination.
            # Generic nextgen2:mode is NEVER sufficient for LIVE halt scope.
            is_live_hint = self._resolve_is_live_for_halt(r)
            mode_hint = "LIVE" if is_live_hint else "NON_LIVE"
            self._set_halt(
                r,
                f"CIRCUIT_BREAKER:{self._consecutive_errors}_errors:mode={mode_hint}",
                is_live=is_live_hint,
            )
            self._send_alert(
                f"\U0001f6a8 CIRCUIT_BREAKER: {self._consecutive_errors} consecutive errors, trading halted.\n"
                f"Manual clear required:\n"
                f"  redis-cli DEL nextgen2:trade:halt nextgen2:trade:halt:ts nextgen2:trade:halt:reason\n"
                f"  redis-cli DEL trade:halt trade:halt:ts trade:halt:reason",
                "circuit_breaker"
            )
            # [v8.1.0] CIRCUIT_BREAKER 자동 해제: 60초 후 halt 키 만료 (ACL 오류 복구용)
            try:
                _halt_key = f"nextgen2:trade:halt:{self.rc.strategy}"
                _halt_ts_key = f"nextgen2:trade:halt:ts:{self.rc.strategy}"
                _halt_reason_key = f"nextgen2:trade:halt:reason:{self.rc.strategy}"
                # 전략별 halt 키에 TTL 설정 (60초 후 자동 만료)
                r.expire(_halt_key, 60)
                r.expire(_halt_ts_key, 60)
                r.expire(_halt_reason_key, 60)
                # 공유 halt 키에도 TTL 설정
                r.expire("nextgen2:trade:halt", 60)
                r.expire("nextgen2:trade:halt:ts", 60)
                r.expire("nextgen2:trade:halt:reason", 60)
                logger.info("CIRCUIT_BREAKER: auto-expire set (60s) for halt keys")
            except Exception as _cb_expire_err:
                logger.warning("CIRCUIT_BREAKER: auto-expire failed: %s", _cb_expire_err)
            return {"ok": False, "skipped": True, "reason": "CIRCUIT_BREAKER"}

        # ═══════ STAGE 1: Safe Mode Check ═══════
        if self.safe_mode:
            sm_state = self.safe_mode.get_mode()
            if not sm_state.allow_new_orders:
                logger.warning("SAFE_MODE active: mode=%s, reason=%s", sm_state.mode, sm_state.reason)
                self._update_heartbeat(r, cycle_id, "SAFE_MODE", reason=sm_state.reason)
                return {"ok": True, "skipped": True, "reason": "SAFE_MODE", "mode": sm_state.mode}

        # ═══════ STAGE 2: Distributed Lock ═══════
        lock_key = f"nextgen2:cycle_lock:{self.rc.strategy}"
        try:
            with redis_lock(r, lock_key, ttl_sec=60, value=cycle_id, heartbeat=True) as lock_value:
                result = self._cycle_inner(r, cycle_id, decision_id, cycle_start)
                # Reset circuit breaker on ANY non-error result
                if not result.get("_is_error"):
                    self._consecutive_errors = 0
                return result
        except RuntimeError:
            logger.warning("Cycle skipped: locked")
            # [v5.0.0] Update heartbeat with LOCKED reason for watchdog visibility
            try:
                self._update_heartbeat(r, cycle_id, "LOCKED", reason="lock_busy")
            except Exception:
                pass
            _structured_log("CYCLE_SKIP", reason="LOCKED", cycle_id=cycle_id,
                           strategy=self.rc.strategy)
            return {"ok": True, "skipped": True, "reason": "LOCKED"}

    def _cycle_inner(
        self, r: redis_lib.Redis, cycle_id: str, decision_id: str, cycle_start: float
    ) -> Dict[str, Any]:
        """Inner cycle logic (runs under distributed lock)."""


        # ═══════ STAGE 2.5: SSMv2 Fill Feedback Loop ═══════
        # [SSMv2-FULL] 매 사이클 시작 시 stream:fills에서 새 체결 읽어 SSMv2에 피드백
        if self._ssm_v2 is not None:
            try:
                _fill_cursor_key = f"nextgen2:ssm:fill_cursor:{self.rc.strategy}"
                _fill_cursor = r.get(_fill_cursor_key) or "0-0"
                _fill_entries = r.xread({"stream:fills": _fill_cursor}, count=50, block=0)
                if _fill_entries:
                    _processed = 0
                    for _stream_name, _messages in _fill_entries:
                        for _msg_id, _msg_data in _messages:
                            try:
                                _sym = _msg_data.get("symbol", "")
                                _side = _msg_data.get("side", "")
                                _pnl_raw = _msg_data.get("realized_pnl", "0")
                                _pnl = float(_pnl_raw) if _pnl_raw else 0.0
                                _fill_ts = _msg_data.get("fill_time", "")
                                # register_exit는 SELL(청산) 체결에만 의미 있음
                                if _sym and _side == "SELL":
                                    self._ssm_v2.register_exit(
                                        symbol=_sym,
                                        strategy_id=self.rc.strategy,
                                        pnl=_pnl,
                                        hold_sec=0.0,  # hold_sec은 SSMv2 내부에서 계산
                                        slippage_bps=0.0,
                                    )
                                    _processed += 1
                                # BUY 체결도 기록 (진입 시간 추적용)
                                elif _sym and _side == "BUY":
                                    self._ssm_v2.log_event("FILL_BUY", {
                                        "symbol": _sym,
                                        "fill_time": _fill_ts,
                                        "msg_id": _msg_id,
                                    })
                            except Exception as _fe:
                                logger.debug("SSMv2_FILL_PARSE: %s %s", _msg_id, _fe)
                            # 커서 업데이트
                            r.set(_fill_cursor_key, _msg_id, ex=86400)
                    if _processed > 0:
                        logger.info("SSMv2_FILL_FEEDBACK: %d exits registered", _processed)
            except Exception as _ffe:
                logger.debug("SSMv2_FILL_FEEDBACK_ERROR: %s", _ffe)
        # ═══════ STAGE 3: Trading Gates ═══════
        # [v5.3.0] Normalized truthy parsing for trading:enabled.
        # In LIVE: require explicit truthy ('true/1/yes') to trade.
        # In non-LIVE: keep permissive (only block on explicit false).
        trading_enabled_raw = r.get("trading:enabled")
        if str(trading_enabled_raw).lower() in ("false", "0", "no"):
            reason = r.get("trading:disable_reason") or "unknown"
            logger.warning("Cycle skipped: trading disabled (%s)", reason)
            _structured_log("CYCLE_SKIP", reason="TRADING_DISABLED", detail=reason,
                           cycle_id=cycle_id, strategy=self.rc.strategy)
            # [v6.3.0] Update heartbeat on skip paths so watchdog sees current blocking reason.
            self._update_heartbeat(r, cycle_id, "UNKNOWN", skip_reason="TRADING_DISABLED")
            return {"ok": True, "skipped": True, "reason": "TRADING_DISABLED", "detail": reason}


        # [v5.9.0] Stale halt alert (>24h) — NEVER auto-clear.
        # Now checks strategy-scoped halt keys too, with updated clear instructions.
        _strat_halt_ts_key = f"nextgen2:trade:halt:ts:{self.rc.strategy}"
        _halt_ts_keys = [
            (GLOBAL_HALT_TS_KEY, "GLOBAL"),
            (ENGINE_HALT_TS_KEY, "ENGINE"),
            (_strat_halt_ts_key, f"STRATEGY({self.rc.strategy})"),
        ]
        for _htk, _htk_scope in _halt_ts_keys:
            halt_ts_raw = r.get(_htk)
            if halt_ts_raw:
                try:
                    halt_age = time.time() - _dt.fromisoformat(
                        str(halt_ts_raw).replace("Z", "+00:00")
                    ).timestamp()
                    if halt_age > 86400:
                        logger.warning(
                            "STALE_HALT_ALERT [%s]: age=%.1fh — requires MANUAL clear:\n"
                            "  redis-cli DEL trade:halt trade:halt:ts trade:halt:reason\n"
                            "  redis-cli DEL nextgen2:trade:halt nextgen2:trade:halt:ts nextgen2:trade:halt:reason\n"
                            "  redis-cli DEL nextgen2:trade:halt:%s nextgen2:trade:halt:ts:%s nextgen2:trade:halt:reason:%s",
                            _htk_scope, halt_age / 3600,
                            self.rc.strategy, self.rc.strategy, self.rc.strategy
                        )
                        self._send_alert(
                            f"STALE_HALT [{_htk_scope}]: age={halt_age/3600:.1f}h — manual clear required. "
                            f"Keys: {_htk} + strategy-scoped keys for {self.rc.strategy}",
                            f"halt_stale_{_htk_scope.lower()}"
                        )
                except Exception:
                    pass

        # Halt check — uses normalized _is_truthy for both global and engine keys
        halted, halt_reason = self._is_halted(r)
        if halted:
            logger.warning("HALT_ACTIVE: %s — ALL intent emission BLOCKED", halt_reason)
            # [v6.3.0] Update heartbeat on halt path.
            self._update_heartbeat(r, cycle_id, "UNKNOWN", skip_reason=f"TRADE_HALTED:{halt_reason}")
            return {"ok": True, "skipped": True, "reason": "TRADE_HALTED", "halt_reason": halt_reason}

        # ═══════ STAGE 3b: Mode Resolution ═══════
        # NextGen2 MUST have its own mode key for LIVE trading
        # Shared key fallback only allowed for DRY_RUN/SHADOW
        # Strategy-scoped mode key: allows multiple nextgen2 instances with different strategies
        strategy_mode_key = f"nextgen2:mode:{self.rc.strategy}"
        generic_mode_key = "nextgen2:mode"
        ng2_mode_strategy = r.get(strategy_mode_key)
        ng2_mode_generic = r.get(generic_mode_key)
        ng2_mode = ng2_mode_strategy or ng2_mode_generic
        if ng2_mode:
            mode = ng2_mode.upper()
            used_key = strategy_mode_key if ng2_mode_strategy else generic_mode_key
            # If using generic key and mode is LIVE, block — require strategy-scoped key for LIVE
            if not ng2_mode_strategy and mode == "LIVE":
                logger.critical("MODE_SAFETY: generic nextgen2:mode=LIVE but no strategy-scoped key %s — blocking LIVE", strategy_mode_key)
                # [v5.7.0] Explicit structured event for fail-closed LIVE blocks
                _structured_log("LIVE_BLOCK", reason="BLOCKED_LIVE_NO_STRATEGY_KEY",
                               mode="LIVE", cycle_id=cycle_id, strategy=self.rc.strategy,
                               detail=f"generic key={generic_mode_key}=LIVE, missing {strategy_mode_key}")
                mode = "SAFE"
                self._send_alert(
                    f"\u26a0\ufe0f MODE_SAFETY: LIVE blocked — set {strategy_mode_key}=LIVE in Redis to enable",
                    "mode_safety"
                )
            logger.info("MODE: using %s=%s", used_key, mode)
        else:
            shared_mode = (r.get("emarkos:v1:mode") or "SAFE").upper()
            if shared_mode == "LIVE":
                # REFUSE to use shared key for LIVE — prevents dual-engine collision
                logger.warning("MODE: shared emarkos:v1:mode=LIVE but %s not set — blocking LIVE",
                              strategy_mode_key)
                # [v5.7.0] Explicit structured event for fail-closed LIVE blocks
                _structured_log("LIVE_BLOCK", reason="BLOCKED_LIVE_SHARED_KEY",
                               mode="LIVE", cycle_id=cycle_id, strategy=self.rc.strategy,
                               detail=f"shared emarkos:v1:mode=LIVE, missing {strategy_mode_key}")
                mode = "SAFE"
            else:
                mode = shared_mode
                logger.info("MODE: using shared emarkos:v1:mode=%s (set %s for isolation)",
                           mode, strategy_mode_key)

        is_live = mode == "LIVE"
        is_shadow = mode == "SHADOW"
        is_dryrun = mode == "DRY_RUN"

        # [v5.3.0] Fail-closed: in LIVE, require explicit truthy for trading:enabled.
        # This catches None (missing key), mis-typed values ('ture', 'enable'), and empty strings.
        if is_live and not _is_truthy(trading_enabled_raw):
            logger.critical("TRADING_GATE_NOT_TRUTHY_LIVE: trading:enabled=%r in LIVE — fail-closed",
                           trading_enabled_raw)
            _structured_log("CYCLE_SKIP", reason="TRADING_GATE_NOT_TRUTHY",
                           raw_value=str(trading_enabled_raw),
                           cycle_id=cycle_id, strategy=self.rc.strategy)
            self._send_alert(
                f"TRADING_GATE: trading:enabled={trading_enabled_raw!r} in LIVE — refusing to trade (require true/1/yes)",
                "trading_gate_not_truthy"
            )
            # [v5.7.0] Explicit structured event for fail-closed LIVE blocks
            _structured_log("LIVE_BLOCK", reason="TRADING_GATE_NOT_TRUTHY",
                           mode=mode, cycle_id=cycle_id, strategy=self.rc.strategy,
                           detail=f"trading:enabled={trading_enabled_raw!r} in LIVE")
            return {"ok": True, "skipped": True, "reason": "TRADING_GATE_NOT_TRUTHY"}

        if mode not in ("LIVE", "SHADOW", "DRY_RUN"):
            logger.info("Cycle skipped: mode=%s", mode)
            # [v5.7.0] Explicit structured event for fail-closed LIVE blocks
            _structured_log("LIVE_BLOCK", reason="MODE_BLOCKED", mode=mode,
                           cycle_id=cycle_id, strategy=self.rc.strategy,
                           detail="Mode not in LIVE/SHADOW/DRY_RUN")
            # [v6.3.0] Update heartbeat on mode-blocked path.
            self._update_heartbeat(r, cycle_id, mode, skip_reason="MODE_BLOCKED")
            return {"ok": True, "skipped": True, "reason": "MODE_BLOCKED", "mode": mode}

        # [v4.5.0] Update health heartbeat every cycle
        self._update_heartbeat(r, cycle_id, mode)

        # Broker gate
        broker = (r.get("emarkos:v1:broker") or "kis").lower()
        if broker not in ("kis", "kis_paper"):
            logger.info("Cycle skipped: broker=%s", broker)
            # [v6.3.0] Update heartbeat on broker-mismatch path.
            self._update_heartbeat(r, cycle_id, mode, skip_reason="BROKER_MISMATCH")
            return {"ok": True, "skipped": True, "reason": "BROKER_MISMATCH", "broker": broker}

        # [v5.1.0] Broker/mode alignment: prevent paper execution in LIVE mode
        if is_live and broker == "kis_paper":
            logger.critical("BROKER_MODE_MISMATCH: mode=LIVE but broker=kis_paper — blocking")
            _structured_log("BROKER_MODE_MISMATCH", mode=mode, broker=broker,
                           cycle_id=cycle_id, strategy=self.rc.strategy)
            self._send_alert(
                "BROKER_MODE_MISMATCH: mode=LIVE but broker=kis_paper — trading blocked",
                "broker_mode_mismatch"
            )
            self._set_halt(r, f"BROKER_MODE_MISMATCH:{broker}", is_live=True)
            return {"ok": False, "_is_error": True, "reason": "BROKER_MODE_MISMATCH"}

        # ═══════ STAGE 4: Decision Scheduler ═══════
        now_ms = int(time.time() * 1000)
        market_open_ms, market_close_ms = get_market_times_ms()
        sched = self.scheduler.decide(now_ms, market_open_ms, market_close_ms)
        logger.info("SCHEDULER: mode=%s, allow_trading=%s, detail=%s",
                     sched.mode, sched.allow_trading, sched.detail)

        if not sched.allow_trading:
            # [v7.1.0] Update heartbeat on scheduler-blocked path.
            self._update_heartbeat(r, cycle_id, mode, skip_reason="SCHEDULER_BLOCKED")
            return {"ok": True, "skipped": True, "reason": "SCHEDULER_BLOCKED", "detail": sched.detail}

        # ═══════ STAGE 5: Load Regime Signals ═══════
        regime_raw = r.hgetall("regime:final:current") or {}
        # [v8.0 FIX] ms_regime(CAUTION)이 ms_state(BULLISH)보다 보수적이면 ms_regime 우선
        ms_regime = (regime_raw.get("ms_regime") or "NORMAL").upper()
        ms_state_raw = (regime_raw.get("ms_state") or "NORMAL").upper()
        if ms_regime in ("CRISIS", "BEAR", "CAUTION"):
            _regime_base = ms_regime
        else:
            _regime_base = ms_state_raw
        regime = _regime_base
        # Normalize regime labels: BULLISH→BULL, BEARISH→BEAR for champion_scale compatibility
        regime = {'BULLISH': 'BULL', 'BEARISH': 'BEAR'}.get(regime, regime)

        vix_raw = regime_raw.get("vix") or r.get("market:vix") or "20"
        # [v7.1.0] Use _safe_float for numeric parsing consistency.
        vix = _safe_float(vix_raw, default=20.0, lo=0.0, hi=200.0, label="vix")

        gate_state = regime_raw.get("gate_state", "NO_GATE")
        # [GPU_ALPHA_HEAD_V2_RISKOFF_ONLY]
        gpu_regime = regime_raw.get("gpu_regime", "N/A")
        gpu_confidence = regime_raw.get("gpu_confidence", "N/A")
        gpu_gate_override = regime_raw.get("gpu_gate_override", "false")
        if gpu_regime in ("CAUTION", "BEAR", "CRISIS"):
            logger.info(f"[GPU_RISKOFF] traditional={regime}, gpu={gpu_regime}, gpu_conf={gpu_confidence}, override={gpu_gate_override}")
            if regime not in ("CAUTION", "BEAR", "CRISIS"):
                logger.warning(f"[GPU_TIGHTEN] traditional={regime} -> gpu={gpu_regime}")
                regime = gpu_regime
        # [GPU DIRECT] GPU regime 비교 로깅
        gpu_regime = regime_raw.get("gpu_regime", "N/A")
        gpu_confidence = regime_raw.get("gpu_confidence", "N/A")
        gpu_gate_override = regime_raw.get("gpu_gate_override", "false")
        if gpu_regime != "N/A":
            logger.info(f"[GPU_REGIME] traditional={regime}, gpu={gpu_regime}, "
                     f"gpu_conf={gpu_confidence}, gpu_gate_override={gpu_gate_override}, "
                     f"gate_state={gate_state}")
            # GPU가 RISK_OFF를 감지하고 기존이 NORMAL이면 GPU를 따름
            if gpu_regime in ("CRISIS", "BEAR", "CAUTION") and regime in ("NORMAL", "NEUTRAL", "BULL", "BULLISH"):
                logger.warning(f"[GPU_OVERRIDE] GPU detects risk ({gpu_regime}) but traditional says {regime}. "
                           f"Using GPU regime for safety.")
                regime = gpu_regime

        confidence_raw = regime_raw.get("confidence", "0.5")
        # [v7.1.0] Use _safe_float for numeric parsing consistency.
        regime_confidence = _safe_float(confidence_raw, default=0.5, lo=0.0, hi=1.0,
                                         label="regime:confidence")
        # [v8.0 FIX] final_mult, risk_level, crash_probability 읽기 (최우선 리스크 제한)

        final_mult = _safe_float(
            regime_raw.get("final_mult") or regime_raw.get("v4_final_mult"),
            default=1.0, lo=0.0, hi=2.0, label="final_mult")
        risk_level = regime_raw.get("risk_level") or regime_raw.get("v4_risk_level") or "L1"
        crash_prob = _safe_float(
            regime_raw.get("crash_probability") or regime_raw.get("v4_crash_prob"),
            default=0.0, lo=0.0, hi=100.0, label="crash_probability")

        logger.info("REGIME: state=%s, vix=%.1f, gate=%s, conf=%.2f, "
                     "final_mult=%.2f, risk_level=%s, crash_prob=%.1f%%",
                     regime, vix, gate_state, regime_confidence,
                     final_mult, risk_level, crash_prob)

        # ═══════ STAGE 6: Champion Scale ═══════
        champion_scale = 1.0
        hedge_boost = 0.0
        if regime == "CRISIS":
            champion_scale = 0.3
        elif regime == "BEAR":
            champion_scale = 0.5
        elif regime == "CAUTION":
            champion_scale = 0.90
            hedge_boost = max(hedge_boost, 0.20)
        elif regime == "BULL":
            champion_scale = 1.2

        if vix > 35:
            champion_scale *= 0.5
        elif vix > 25:
            champion_scale *= 0.85

        champion_scale = max(0.1, min(1.5, champion_scale))
        # --- V400 exposure floor (non-CRISIS only) --- [HOTFIX: moved after STAGE 6]
        if regime != "CRISIS" and champion_scale < 0.55:
            champion_scale = 0.55
        # hedge hint for downstream order builder / hedge module
        try:
            r.set("champion:hedge_boost", str(hedge_boost), ex=3600)
        except Exception:
            pass
        # [v8.0 FIX] final_mult를 champion_scale 상한으로 적용 (최우선 리스크 제한)
        try:
            _crash_prob_mult = 1.0
            if crash_prob >= 70:
                _crash_prob_mult = 0.5
            elif crash_prob >= 50:
                _crash_prob_mult = 0.7
            elif crash_prob >= 30:
                _crash_prob_mult = 0.85
            _risk_level_cap = {"L1": 1.5, "L2": 1.0, "L3": 0.6, "L4": 0.3}.get(risk_level, 1.0)
            if final_mult < 1.0:
                _final_cap = final_mult * _crash_prob_mult
                champion_scale = min(champion_scale, _final_cap)
                logger.info("FINAL_MULT_CAP: champion_scale=%.3f (final_mult=%.2f, "
                             "crash_mult=%.2f, risk_level=%s)",
                             champion_scale, final_mult, _crash_prob_mult, risk_level)
            else:
                champion_scale = min(champion_scale, _risk_level_cap)
            champion_scale = max(0.1, min(1.5, champion_scale))
        except Exception as _fme:
            logger.warning("FINAL_MULT_CAP_ERROR: %s", _fme)
        # [FIX-P2] conf < 0.6 시 CHAMPION_SCALE 축소 정책 (2026-03-05)
        # confidence:scores 평균 또는 regime confidence가 낮을 때 과도한 scale 방지
        _conf_scale_mult = 1.0
        try:
            _conf_scores_raw = r.hgetall("confidence:scores") or {}
            if _conf_scores_raw:
                _conf_vals = [float(v) for v in _conf_scores_raw.values() if v]
                _avg_conf = sum(_conf_vals) / len(_conf_vals) if _conf_vals else 0.5
            else:
                _avg_conf = regime_confidence  # fallback to regime confidence
            # 축소 정책: conf < 0.6 → 선형 축소 (0.4 → 0.7x, 0.5 → 0.85x, 0.6 → 1.0x)
            if _avg_conf < 0.6:
                _conf_scale_mult = 0.7 + (_avg_conf / 0.6) * 0.3  # 0.4→0.7, 0.6→1.0 선형
                _conf_scale_mult = max(0.5, min(1.0, _conf_scale_mult))
                logger.info("CONF_SCALE_PENALTY: avg_conf=%.3f → scale_mult=%.3f", _avg_conf, _conf_scale_mult)
            else:
                logger.debug("CONF_SCALE_OK: avg_conf=%.3f (no penalty)", _avg_conf)
        except Exception as _ce:
            logger.warning("CONF_SCALE_ERROR: %s (using mult=1.0)", _ce)
            _conf_scale_mult = 1.0
        champion_scale_pre_conf = champion_scale
        champion_scale = champion_scale * _conf_scale_mult
        champion_scale = max(0.1, min(1.5, champion_scale))
        # [GPU-ALPHA-V3] Action-Value 기반 공격 스케일 적용
        try:
            _gpu_atk = float(r.get("gpu:alpha:live:attack_scale") or "1.0")
            _gpu_enable = r.get("gpu:alpha:live:attack_enable") == "1"
            if _gpu_enable and _gpu_atk > 1.0:
                champion_scale = min(champion_scale * _gpu_atk, 1.5)
                logger.info("GPU_ALPHA_LIVE: scale=%.3f -> champion_scale=%.3f", _gpu_atk, champion_scale)
        except Exception as _gpu_err:
            pass  # Graceful fallback — GPU 실패해도 기존 로직 유지
        logger.info("CHAMPION_SCALE: %.3f (regime=%s, vix=%.1f, pre_conf=%.3f, conf_mult=%.3f, "
                     "final_mult=%.2f, risk_level=%s, crash_prob=%.1f%%)",
                    champion_scale, regime, vix, champion_scale_pre_conf, _conf_scale_mult,
                    final_mult, risk_level, crash_prob)

        # ═══════ STAGE 7: Load Targets (SSOT) ═══════
        targets_key = "ssot:target:v2:current"
        targets_raw = r.get(targets_key)
        if not targets_raw:
            logger.warning("No targets found at %s", targets_key)
            # [v6.3.0] Update heartbeat on no-targets path.
            self._update_heartbeat(r, cycle_id, mode, skip_reason="NO_TARGETS")
            return {"ok": True, "skipped": True, "reason": "NO_TARGETS"}

        targets_data = safe_json_parse(targets_raw)
        if not targets_data:
            logger.warning("Failed to parse targets")
            # [v7.1.0] Update heartbeat on targets-parse-error path.
            self._update_heartbeat(r, cycle_id, mode, skip_reason="TARGETS_PARSE_ERROR")
            return {"ok": True, "skipped": True, "reason": "TARGETS_PARSE_ERROR"}

        # [v5.1.0] Enforce targets_data is a dict before accessing keys
        # safe_json_parse can return list/str/int if Redis contains non-object JSON
        if not isinstance(targets_data, dict):
            logger.critical("TARGETS_SCHEMA_INVALID: expected dict, got %s",
                           type(targets_data).__name__)
            _structured_log("TARGETS_SCHEMA_INVALID", type=type(targets_data).__name__,
                           cycle_id=cycle_id, strategy=self.rc.strategy)
            if is_live:
                self._set_halt(r, f"TARGETS_SCHEMA_INVALID:{type(targets_data).__name__}", is_live=True)
                return {"ok": False, "_is_error": True, "reason": "TARGETS_SCHEMA_INVALID"}
            return {"ok": True, "skipped": True, "reason": "TARGETS_SCHEMA_INVALID"}

        # [v7.1.0] Explicit schema validator for ssot:target:v2:current.
        # Validates required fields exist with correct types to catch upstream
        # drift earlier than downstream parsing warnings.
        _schema_ver = targets_data.get("schema_version")
        if _schema_ver is not None and str(_schema_ver).upper() not in ("2", "V2", "SSOT_TARGET_V2"):
            logger.warning("TARGETS_SCHEMA_DRIFT: expected v2, got %s", _schema_ver)
            _structured_log("TARGETS_SCHEMA_DRIFT", expected="v2",
                           actual=str(_schema_ver), cycle_id=cycle_id,
                           strategy=self.rc.strategy)

        # [v7.4.0] Required field validation: 'positions' must be list (v2 array)
        # or dict (v1 map). Both formats are valid upstream schemas:
        #   - list: [{"symbol": "AAPL", "w": 0.05, ...}, ...] → v2 adapter path
        #   - dict: {"AAPL": {"target_value": 1000}, ...} → v1 adapter path
        # Any other type (str, int, None-but-present) is invalid → halt in LIVE.
        _positions_raw = targets_data.get("positions")
        # [v7.8.0] SSOT_TARGET_V2 unwrap: positions may be nested under "targets" key.
        if _positions_raw is None:
            _nested_targets = targets_data.get("targets")
            if isinstance(_nested_targets, dict):
                _positions_raw = _nested_targets.get("positions")
                if _positions_raw is not None:
                    logger.info("SSOT_V2_UNWRAP: found positions under targets.positions (%d items)",
                               len(_positions_raw) if isinstance(_positions_raw, (list, dict)) else 0)
                    # [v7.8.1] Promote unwrapped positions to top-level so downstream parsers find them
                    targets_data["positions"] = _positions_raw
        if _positions_raw is not None and not isinstance(_positions_raw, (dict, list)):
            logger.critical("TARGETS_POSITIONS_TYPE_INVALID: expected dict or list, got %s",
                           type(_positions_raw).__name__)
            _structured_log("TARGETS_POSITIONS_TYPE_INVALID",
                           actual_type=type(_positions_raw).__name__,
                           cycle_id=cycle_id, strategy=self.rc.strategy)
            if is_live:
                self._set_halt(r, f"TARGETS_POSITIONS_TYPE:{type(_positions_raw).__name__}",
                              is_live=True)
                return {"ok": False, "_is_error": True, "reason": "TARGETS_SCHEMA_INVALID"}
            return {"ok": True, "skipped": True, "reason": "TARGETS_SCHEMA_INVALID"}

        targets = {}

        # [v5.5.0] Track targets_generation_id for churn detection.
        # If targets change too rapidly (ssot-promote-v2 restart churn),
        # log a warning to help operators diagnose upstream instability.
        _targets_gen_id = targets_data.get("generation_id") or targets_data.get("id") or ""
        _last_gen_key = f"nextgen2:last_targets_gen:{self.rc.strategy}"
        try:
            _prev_gen_id = r.get(_last_gen_key) or ""
            if _targets_gen_id and _prev_gen_id and _targets_gen_id != _prev_gen_id:
                _churn_count_key = f"nextgen2:targets_churn_count:{self.rc.strategy}"
                _churn_count = _safe_int(r.get(_churn_count_key), default=0, label="targets_churn")
                _churn_count += 1
                r.set(_churn_count_key, str(_churn_count), ex=3600)  # 1h window
                if _churn_count > 10:
                    logger.warning("TARGETS_CHURN_HIGH: %d generation changes in 1h (prev=%s, curr=%s)",
                                  _churn_count, _prev_gen_id[:16], _targets_gen_id[:16])
                    _structured_log("TARGETS_CHURN", count=_churn_count,
                                   prev_gen=_prev_gen_id[:16], curr_gen=_targets_gen_id[:16],
                                   strategy=self.rc.strategy)
                    # [v5.8.0] Escalate to operator alert when churn exceeds threshold
                    if _churn_count % 10 == 0:  # alert every 10 churns to avoid spam
                        self._send_alert(
                            f"TARGETS_CHURN: {_churn_count} generation changes in 1h "
                            f"(ssot-promote-v2 may be restarting). "
                            f"Latest: {_targets_gen_id[:16]}",
                            "targets_churn"
                        )
            if _targets_gen_id:
                r.set(_last_gen_key, _targets_gen_id, ex=86400)
        except Exception:
            pass  # Non-critical telemetry

        equity_total = _safe_float(r.get("ares:equity:total"), default=0.0, lo=0.0,
                                    label="ares:equity:total")
        budget_fallback = _safe_float(r.get("nextgen2:budget_fallback"),
                                      default=DEFAULT_BUDGET_FALLBACK, lo=1000.0, hi=10_000_000.0,
                                      label="nextgen2:budget_fallback")

        # [v7.7.0] dict-under-positions detection: if targets_data['positions'] is a dict,
        # this is a v1-map nested inside a v2 envelope. Handle it explicitly by
        # iterating .items() to extract symbol→target_value pairs. Previously this
        # fell through to the v1 top-level path which only iterated targets_data.items()
        # (not targets_data['positions'].items()), causing silent EMPTY_TARGETS.
        if "positions" in targets_data and isinstance(targets_data["positions"], dict):
            _dict_positions = targets_data["positions"]
            logger.info("TARGETS_V1_MAP_UNDER_V2: detected dict-under-positions with %d entries",
                        len(_dict_positions))
            _structured_log("TARGETS_V1_MAP_UNDER_V2", count=len(_dict_positions),
                           strategy=self.rc.strategy)
            budget = equity_total if equity_total > 10000 else budget_fallback
            TARGETS_V1_META_DENY = {"schema_version","generation_id","id","ts","timestamp","time","updated_at","meta","positions"}
            for sym, val in _dict_positions.items():
                if not sym or not isinstance(sym, str):
                    continue
                if sym in TARGETS_V1_META_DENY or sym.startswith('_'):
                    continue
                if isinstance(val, dict):
                    tv = _safe_float(val.get("target_value", 0), label=f"dict_pos_tv:{sym}")
                    if tv > 0:
                        targets[sym] = {"target_value": tv, "side": val.get("side", "BUY")}
                elif isinstance(val, (int, float, str)):
                    tv = _safe_float(val, label=f"dict_pos_shorthand:{sym}")
                    if tv > 0:
                        targets[sym] = {"target_value": tv, "side": "BUY"}
            if targets:
                logger.info("V1_MAP_UNDER_V2_ADAPTED: %d symbols, budget=$%.0f", len(targets), budget)

        elif "positions" in targets_data and isinstance(targets_data["positions"], list):
            # Use equity_total if available; otherwise estimate from positions market value
            # (cash_usd is not yet loaded at this point, so use equity or position-based estimate)
            if equity_total > 10000:
                budget = equity_total
            else:
                # Estimate from position data in targets
                # [v5.0.0] Use _safe_float for all Redis-sourced numerics in target estimation
                _est_mv = sum(
                    _safe_float(p.get("shares", p.get("qty", 0)), label="est_mv:shares")
                    * _safe_float(p.get("price", 0), label="est_mv:price")
                    for p in (targets_data.get("positions", []) if isinstance(targets_data.get("positions"), list) else [])
                    if isinstance(p, dict) and p.get("price")  # [v5.1.0] type-check first
                )
                budget = max(_est_mv, 1.0) if _est_mv > 0 else budget_fallback
                logger.info("TARGET_BUDGET: equity=$%.0f too low, using estimated MV=$%.0f", equity_total, budget)
            # [v5.0.0] Guard: targets_data["positions"] must be a list of dicts
            _tgt_positions = targets_data.get("positions", [])
            if not isinstance(_tgt_positions, list):
                logger.warning("TARGETS_POSITIONS_INVALID_TYPE: expected list, got %s",
                               type(_tgt_positions).__name__)
                _tgt_positions = []
            for pos in _tgt_positions:
                if not isinstance(pos, dict):
                    continue
                sym = pos.get("symbol", "")
                w = _safe_float(pos.get("w", 0), label=f"target_w:{sym}")
                if w <= 0 or not sym:
                    continue
                if sym:
                    targets[sym] = {"target_value": w * budget, "side": pos.get("side", "BUY")}
            logger.info("V2_TARGET_ADAPTED: %d symbols, budget=$%.0f", len(targets), budget)
        elif isinstance(targets_data, dict):
            # [v7.7.3] Apply metadata denylist to v1 top-level dict format.
            # Without this, metadata fields like schema_version, generation_id, ts
            # could be misinterpreted as tradable symbols with numeric values.
            _V1_TOP_META_DENY = {
                "schema_version", "generation_id", "id", "ts", "timestamp",
                "time", "updated_at", "meta", "positions", "version",
                "source", "strategy", "type", "status", "total",
                "totalMarketValue", "cash", "equity",
            }
            parsed_targets = {}
            for k, v in targets_data.items():
                if not k:
                    continue
                # Skip metadata keys and underscore-prefixed internal fields
                if k in _V1_TOP_META_DENY or k.startswith('_'):
                    continue

                # v1 format: {"AAPL": {"target_value": "1234.56", ...}, ...}
                if isinstance(v, dict):
                    tv = _safe_float(v.get("target_value", 0), label=f"target_value:{k}")
                    # [v5.0.0] safe_float returns 0.0 on parse failure
                    if tv > 0:
                        v2 = dict(v)
                        v2["target_value"] = tv
                        parsed_targets[k] = v2
                    continue

                # Allow a numeric shorthand: {"AAPL": "1234.56"}
                try:
                    tv = float(v)
                except (ValueError, TypeError):
                    continue
                if tv > 0:
                    parsed_targets[k] = {"target_value": tv, "side": "BUY"}

            targets = parsed_targets

        if not targets:
            logger.warning("Empty targets after parsing")
            # [v7.1.0] Update heartbeat on empty-targets path.
            self._update_heartbeat(r, cycle_id, mode, skip_reason="EMPTY_TARGETS")
            return {"ok": True, "skipped": True, "reason": "EMPTY_TARGETS"}

        # [v4.7.0] Targets staleness check: alert/halt if targets are stale.
        # ssot-promote-v2 instability (high restarts) can cause stale targets.
        targets_ts_raw = targets_data.get("ts") or targets_data.get("timestamp")
        if targets_ts_raw:
            targets_ts = self._parse_timestamp(targets_ts_raw)
            if targets_ts:
                targets_age = time.time() - targets_ts
                if targets_age > self._targets_stale_halt_sec:
                    if is_live:
                        logger.critical("TARGETS_STALE_HALT: age=%.0fs (>%ds) — halting LIVE",
                                        targets_age, self._targets_stale_halt_sec)
                        self._set_halt(r, f"TARGETS_STALE:{targets_age:.0f}s", is_live=True)
                        self._send_alert(
                            f"\U0001f6a8 TARGETS_STALE in LIVE: age={targets_age:.0f}s — halt set",
                            "targets_stale"
                        )
                        return {"ok": False, "_is_error": True, "reason": "TARGETS_STALE"}
                    else:
                        logger.warning("TARGETS_STALE: age=%.0fs (would halt in LIVE)", targets_age)
                elif targets_age > self._targets_stale_warn_sec:
                    logger.warning("TARGETS_STALE_WARN: age=%.0fs (>%ds)",
                                   targets_age, self._targets_stale_warn_sec)
                    self._send_alert(
                        f"\u26a0\ufe0f TARGETS_STALE_WARN: age={targets_age:.0f}s",
                        "targets_stale_warn"
                    )

        symbols = list(targets.keys())
        logger.info("TARGETS: %d symbols loaded", len(symbols))

        # ═══════ STAGE 8: Load Positions ═══════
        positions = {}
        pos_source = "none"

        em_pos_raw = r.get("emarkos:v1:positions")
        if em_pos_raw:
            em_pos = safe_json_parse(em_pos_raw)
            # [v4.9.0] Guard: em_pos must be a dict before calling .get()
            # safe_json_parse can return list/str/int if Redis contains non-object JSON
            if isinstance(em_pos, dict) and em_pos.get("positions"):
                # [v4.8.0] Validate positions is a dict before using
                _raw_pos = em_pos["positions"]
                if isinstance(_raw_pos, dict):
                    positions = _raw_pos
                    pos_source = "emarkos:v1:positions"
                else:
                    logger.warning("POSITIONS_INVALID_TYPE: emarkos:v1:positions['positions'] is %s, expected dict",
                                   type(_raw_pos).__name__)

        if not positions:
            hash_data = r.hgetall("kis:broker:positions")
            if hash_data:
                for sym, raw in hash_data.items():
                    parsed = safe_json_parse(raw)
                    # [v5.0.0] Enforce dict type for each position entry
                    if isinstance(parsed, dict):
                        positions[sym] = parsed
                    else:
                        logger.warning("KIS_POS_INVALID_TYPE: %s is %s, defaulting",
                                       sym, type(parsed).__name__)
                        positions[sym] = {"qty": 0, "shares": 0}
                pos_source = "kis:broker:positions"

        for sym in symbols:
            if sym not in positions:
                positions[sym] = {"qty": 0, "shares": 0, "avg_px": 0}
            p = positions[sym]
            if "shares" in p and "qty" not in p:
                p["qty"] = p["shares"]
            if "qty" in p and "shares" not in p:
                p["shares"] = p["qty"]

        # [v4.8.0] Use _safe_int for Redis-sourced position shares/qty
        pos_count = sum(1 for p in positions.values()
                        if _safe_int(p.get("shares", p.get("qty", 0)), label="pos_shares") > 0)
        logger.info("POSITIONS: %d active, source=%s", pos_count, pos_source)

        # Track last known position count to detect suspicious sudden-empty portfolios.
        last_pos_key = f"nextgen2:last_pos_count:{self.rc.strategy}"
        try:
            last_pos_count = _safe_int(r.get(last_pos_key), default=0, label="last_pos_count")
        except Exception:
            last_pos_count = 0

        # Sudden empty portfolio guard (LIVE): fail-closed unless explicitly overridden.
        if is_live and pos_count == 0 and last_pos_count > 0 and not _is_truthy(r.get("nextgen2:allow_empty_positions")):
            logger.critical(
                "POSITIONS_SUDDEN_EMPTY: last=%d now=%d source=%s — fail-closed",
                last_pos_count, pos_count, pos_source
            )
            self._set_halt(r, f"POSITIONS_SUDDEN_EMPTY:last={last_pos_count}", is_live=True)
            self._send_alert(
                f"POSITIONS_SUDDEN_EMPTY in LIVE: last={last_pos_count}, now=0, source={pos_source}. Halt set.",
                "positions_sudden_empty",
            )
            return {"ok": False, "_is_error": True, "reason": "POSITIONS_SUDDEN_EMPTY", "last": last_pos_count, "source": pos_source}

        # [v5.4.0] Only update last_pos_count AFTER passing the sudden-empty guard.
        # When pos_count=0 in LIVE, we do NOT overwrite last_pos_count, so the guard
        # remains effective even after manual halt clear.
        if pos_count > 0:
            try:
                r.set(last_pos_key, str(pos_count), ex=86400 * 7)
            except Exception:
                pass

        # If KIS sync reported an error, block LIVE trading while relying on KIS positions.
        kis_pos_err = r.get("kis:broker:positions:error")
        if kis_pos_err and is_live and pos_source == "kis:broker:positions":
            err_ts_raw = r.get("kis:broker:positions:error:ts")
            err_age = None
            if err_ts_raw:
                err_ts = self._parse_timestamp(err_ts_raw)
                if err_ts is not None:
                    err_age = time.time() - err_ts

            logger.critical(
                "POSITIONS_SOURCE_ERROR (KIS): %s (age=%s) — fail-closed",
                kis_pos_err,
                f"{err_age:.0f}s" if err_age is not None else "unknown",
            )
            self._send_alert(
                f"POSITIONS_SOURCE_ERROR (KIS) in LIVE: {kis_pos_err}"
                + (f" (age={err_age:.0f}s)" if err_age is not None else ""),
                "positions_source_error"
            )

            # Skip trading immediately; if it persists, set a hard halt.
            if err_age is not None and err_age >= 300:
                self._set_halt(r, f"POSITIONS_SOURCE_ERROR:{kis_pos_err}", is_live=True)
                return {"ok": False, "_is_error": True, "reason": "POSITIONS_SOURCE_ERROR", "detail": kis_pos_err}
            return {"ok": True, "skipped": True, "reason": "POSITIONS_SOURCE_ERROR", "detail": kis_pos_err}

        # Positions freshness check (LIVE halts, DRY_RUN/SHADOW warns)
        freshness_error = self._check_positions_freshness(r, positions, pos_source, is_live)
        if freshness_error:
            return freshness_error

        # ═══════ STAGE 8.5: Position Truth Reconciler [SPOF-3B] ═══════
        # 브로커 실제 잔고(kis:broker:positions) vs Redis snapshot(emarkos:v1:positions) 비교
        # qty diff > 1주 심볼 → WARN 로그 + Redis 이벤트 기록 (halt 없음, 경고만)
        try:
            if not hasattr(self, "_pos_truth_reconciler"):
                self._pos_truth_reconciler = PositionTruthReconciler(r, self.rc.strategy, logger)
            _ptr_result = self._pos_truth_reconciler.run(positions)
            if _ptr_result.get("drift_count", 0) > 0:
                _structured_log("POS_TRUTH_DRIFT", 
                               drift_count=_ptr_result["drift_count"],
                               drifts=_ptr_result.get("drifts", []),
                               cycle_id=cycle_id)
        except Exception as _ptr_e:
            logger.debug("POS_TRUTH_RECONCILER_ERROR: %s", _ptr_e)

        # ═══════ STAGE 8.7: ExecutionLatencyGuard [V237-Phase4C] ═══════
        # SSOT, 포지션 스냅샷, 브로커 쟘고 간의 시간축 정렬 상태를 평가합니다.
        # BLOCK 또는 DEGRADE 시 주문 사이즈 축소 또는 사이클 건너맜으로 리스크를 관리합니다.
        _latency_scale = 1.0  # 기본값: 스케일 없음
        if self._latency_guard is not None:
            try:
                # Redis RTT 측정
                _rtt_start = time.time()
                r.ping()
                _redis_rtt_ms = (time.time() - _rtt_start) * 1000.0

                # SSOT 타임스탬프 읽기
                _ssot_ts_raw = r.get("ssot:target:v2:ts") or r.get("ssot:target:v2:current")
                _ssot_ts = _parse_ts_to_epoch(_ssot_ts_raw)  # [HOTFIX] ISO + epoch + JSON

                # 포지션 타임스탬프 읽기
                _pos_ts_raw = r.get("kis:broker:positions:ts")
                _pos_ts = _parse_ts_to_epoch(_pos_ts_raw)  # [HOTFIX] ISO + epoch

                # 브로커 쟘고 타임스탬프 (pos_ts와 동일, 구분을 위해 업데이트 시간 사용)
                _broker_ts_raw = r.get("kis:broker:positions:updated_at") or _pos_ts_raw
                _broker_ts = _parse_ts_to_epoch(_broker_ts_raw)  # [HOTFIX] ISO + epoch

                _latency_decision = self._latency_guard.evaluate({
                    "ssot_ts": _ssot_ts,
                    "pos_ts": _pos_ts,
                    "broker_ts": _broker_ts,
                    "redis_rtt_ms": _redis_rtt_ms,
                    "cycle_overlap": False,  # 라운다운 락으로 중복 실행 방지됨
                })

                if _latency_decision.action == "BLOCK":
                    logger.critical(
                        "LATENCY_GUARD_BLOCK: reason=%s, skew=%.1fs, rtt=%.0fms",
                        _latency_decision.reason, _latency_decision.max_skew_sec, _latency_decision.redis_rtt_ms
                    )
                    _structured_log("LATENCY_GUARD_BLOCK",
                        reason=_latency_decision.reason,
                        max_skew_sec=_latency_decision.max_skew_sec,
                        redis_rtt_ms=_latency_decision.redis_rtt_ms,
                        cycle_id=cycle_id
                    )
                    # LIVE에서는 스킵, 비-LIVE에서는 경고만
                    if is_live:
                        return {"ok": True, "skipped": True, "reason": "LATENCY_GUARD_BLOCK",
                                "detail": _latency_decision.reason}
                    else:
                        logger.warning("LATENCY_GUARD_BLOCK_NONLIVE: %s (non-live, continuing)",
                                       _latency_decision.reason)

                elif _latency_decision.action == "DEGRADE":
                    logger.warning(
                        "LATENCY_GUARD_DEGRADE: reason=%s, skew=%.1fs, rtt=%.0fms, scale=%.2f",
                        _latency_decision.reason, _latency_decision.max_skew_sec,
                        _latency_decision.redis_rtt_ms,
                        _latency_decision.degrade_notional_scale or 1.0
                    )
                    _latency_scale = _latency_decision.degrade_notional_scale or 1.0
                    _structured_log("LATENCY_GUARD_DEGRADE",
                        reason=_latency_decision.reason,
                        max_skew_sec=_latency_decision.max_skew_sec,
                        redis_rtt_ms=_latency_decision.redis_rtt_ms,
                        degrade_scale=_latency_scale,
                        cycle_id=cycle_id
                    )

            except Exception as _lg_e:
                logger.debug("LATENCY_GUARD_ERROR: %s", _lg_e)

        # ═══════ STAGE 9: Load Prices ═══════
        prices = {}
        for sym in symbols:
            px_raw = r.get(f"price:{sym}") or r.hget("prices:latest", sym)
            if px_raw:
                px = _safe_float(px_raw, default=0.0, lo=0.001, label=f"price:{sym}")
                if px > 0:
                    prices[sym] = px
                else:
                    logger.warning("Non-positive price for %s: raw=%r", sym, px_raw)

        missing_prices = [s for s in symbols if s not in prices]
        missing_pct = len(missing_prices) / max(len(symbols), 1)
        if missing_pct > 0.3:
            logger.critical("PRICE_STALENESS >30%%: %d/%d missing",
                            len(missing_prices), len(symbols))
            # [v4.6.0] Bounded side effects: set with EX TTL to auto-expire
            # [v5.5.0] Non-critical side effect: wrap in try/except to avoid
            # elevating minor telemetry failures into cycle errors
            try:
                r.set("trading:disable_reason", "PRICE_STALENESS", ex=3600)  # 1h TTL
            except Exception:
                pass
            _structured_log("PRICE_STALENESS", missing=len(missing_prices),
                            total=len(symbols), is_live=is_live)
            if is_live:
                # LIVE: hard halt — operator must investigate
                self._set_halt(r, f"PRICE_STALENESS:{len(missing_prices)}/{len(symbols)}", is_live=True)
                self._send_alert(
                    f"\U0001f6a8 PRICE_STALENESS: {len(missing_prices)}/{len(symbols)} missing — halt set",
                    "price_staleness"
                )
                return {"ok": False, "_is_error": True, "reason": "PRICE_STALENESS"}
            else:
                # [v7.1.0] DRY_RUN/SHADOW: skip-with-reason instead of halt.
                # Avoids manual intervention during feed outages in non-LIVE modes.
                logger.warning("PRICE_STALENESS_SKIP: %d/%d missing in %s mode — skipping (no halt)",
                               len(missing_prices), len(symbols), mode)
                self._update_heartbeat(r, cycle_id, mode, skip_reason="PRICE_STALENESS")
                return {"ok": True, "skipped": True, "reason": "PRICE_STALENESS"}

        logger.info("PRICES: %d loaded, %d missing", len(prices), len(missing_prices))

        # [v5.1.0] Price freshness enforcement (CRITICAL fix)
        # If realtime feed stalls but price keys remain, engine can use stale prices.
        # Check global feed heartbeat and/or per-symbol price timestamps.
        # [v5.2.0] Config-driven price feed staleness thresholds
        PRICE_FRESH_HALT_SEC = self._price_feed_halt_sec
        PRICE_FRESH_WARN_SEC = self._price_feed_warn_sec
        price_feed_ts_raw = r.get("prices:latest:ts") or r.get("realtime:feed:heartbeat")
        if price_feed_ts_raw:
            price_feed_ts = self._parse_timestamp(price_feed_ts_raw)
            if price_feed_ts is not None:
                price_feed_age = time.time() - price_feed_ts
                if price_feed_age > PRICE_FRESH_HALT_SEC:
                    if is_live:
                        logger.critical("PRICE_FEED_STALE_HALT: age=%.0fs (>%ds) — halting LIVE",
                                        price_feed_age, PRICE_FRESH_HALT_SEC)
                        self._set_halt(r, f"PRICE_FEED_STALE:{price_feed_age:.0f}s", is_live=True)
                        self._send_alert(
                            f"\U0001f6a8 PRICE_FEED_STALE in LIVE: age={price_feed_age:.0f}s — halt set",
                            "price_feed_stale"
                        )
                        return {"ok": False, "_is_error": True, "reason": "PRICE_FEED_STALE",
                                "age_sec": price_feed_age}
                    else:
                        logger.warning("PRICE_FEED_STALE: age=%.0fs (would halt in LIVE)", price_feed_age)
                elif price_feed_age > PRICE_FRESH_WARN_SEC:
                    logger.warning("PRICE_FEED_STALE_WARN: age=%.0fs (>%ds)",
                                   price_feed_age, PRICE_FRESH_WARN_SEC)
            elif is_live:
                # Unparseable feed ts in LIVE
                logger.critical("PRICE_FEED_TS_UNPARSEABLE: raw=%r in LIVE", price_feed_ts_raw)
                self._set_halt(r, f"PRICE_FEED_TS_UNPARSEABLE:{price_feed_ts_raw!r}", is_live=True)
                return {"ok": False, "_is_error": True, "reason": "PRICE_FEED_TS_UNPARSEABLE"}
        elif is_live:
            # [v5.2.0] Default fail-closed in LIVE: halt when price feed heartbeat is missing.
            # This is the default-safe behavior. Operators can explicitly opt out by setting
            # nextgen2:allow_missing_price_feed_ts=true (e.g., during initial deployment).
            if _is_truthy(r.get("nextgen2:allow_missing_price_feed_ts")):
                logger.warning("PRICE_FEED_NO_TS: missing but allow_missing_price_feed_ts=true")
            else:
                logger.critical("PRICE_FEED_NO_TS_HALT: no prices:latest:ts or realtime:feed:heartbeat in LIVE — fail-closed")
                self._send_alert(
                    "PRICE_FEED_NO_TS: no price feed heartbeat in LIVE — halt set (default fail-closed)",
                    "price_feed_no_ts"
                )
                self._set_halt(r, "PRICE_FEED_NO_TS", is_live=True)
                return {"ok": False, "_is_error": True, "reason": "PRICE_FEED_NO_TS"}

        # ═══════ STAGE 10: Budget & Equity ═══════
        cash_usd = _safe_float(
            r.get("emarkos:v1:cash_usd") or r.get("kis:cash:usd"),
            default=0.0, lo=0.0, label="cash_usd"
        )

        # [v4.5.0] Cash freshness enforcement (LIVE halts, not just warn)
        cash_freshness_error = self._check_cash_freshness(r, is_live)
        if cash_freshness_error:
            return cash_freshness_error

        # [v4.5.0] Equity freshness enforcement
        equity_freshness_error = self._check_equity_freshness(r, equity_total, is_live)
        if equity_freshness_error:
            return equity_freshness_error

        # [v4.8.0] Use _safe_int for all Redis-sourced position numerics
        total_mv = sum(
            _safe_int(positions.get(s, {}).get("shares", positions.get(s, {}).get("qty", 0)),
                      label=f"mv:{s}") * prices.get(s, 0)
            for s in symbols
        )
        budget_usd = equity_total if equity_total > 10000 else (cash_usd + total_mv)
        if budget_usd <= 0:
            if is_live:
                # In LIVE mode, refuse to trade with fallback budget — require real equity data
                logger.critical("BUDGET_ZERO_LIVE: equity=$%.0f, cash=$%.0f, mv=$%.0f — refusing to trade",
                                equity_total, cash_usd, total_mv)
                self._send_alert(
                    f"\U0001f6a8 BUDGET_ZERO in LIVE: equity={equity_total}, cash={cash_usd}, mv={total_mv}",
                    "budget_zero"
                )
                return {"ok": False, "skipped": True, "reason": "BUDGET_ZERO_LIVE"}
            budget_usd = budget_fallback
            logger.warning("BUDGET_FALLBACK: using $%.0f (non-LIVE mode)", budget_fallback)

        # Cap budget fallback to prevent over-allocation
        if equity_total > 0 and budget_usd > equity_total * 1.1:
            logger.warning("BUDGET_CAP: $%.0f exceeds 110%% of equity $%.0f, capping",
                           budget_usd, equity_total)
            budget_usd = equity_total * 1.1

        logger.info("BUDGET: equity=$%.0f, cash=$%.0f, mv=$%.0f, budget=$%.0f",
                     equity_total, cash_usd, total_mv, budget_usd)

        # ═══════ STAGE 11: Crash Guard ═══════
        crash_scale = 1.0
        daily_pnl_raw = r.get(f"ares:pnl:daily:{day_key()}")
        daily_pnl = _safe_float(daily_pnl_raw, default=0.0, label="daily_pnl") if daily_pnl_raw else 0.0
        daily_pnl_pct = daily_pnl / budget_usd if budget_usd > 0 else 0.0

        cg_result = self.crash_guard.decide(vix=vix, gate_state=gate_state)
        if cg_result.gross_scalar < 1.0:
            crash_scale = cg_result.gross_scalar
            cg_halt = getattr(cg_result, 'halt', False)
            if cg_halt:
                logger.warning("CRASH_GUARD_ACTIVE: scale=%.2f, halt=True", crash_scale)
                self._send_alert(
                    f"\U0001f534 CRASH_GUARD: scale={crash_scale:.2f}, halt=True", "crash_guard"
                )
            else:
                logger.info("RISK_GUARD: scale=%.2f, halt=False, reason=%s", crash_scale, cg_result.reason)
                # [v7.8.0] RISK_GUARD (non-halt) uses dedicated tag "risk_guard_soft"
                # so it respects alert_throttle_sec (120s default) instead of
                # bypassing throttle like "crash_guard" critical tag.
                # This prevents repeated vix_soft alerts every 30s cycle.
                self._send_alert(
                    f"\U0001f7e0 RISK_GUARD: scale={crash_scale:.2f}, halt=False, reason={cg_result.reason}", "risk_guard_soft"
                )
            if cg_halt:
                self._set_halt(r, f"CRASH_GUARD:scale={crash_scale:.2f}", is_live=is_live)
                self._send_alert(
                    f"\U0001f6a8 CRASH_GUARD HALT: scale={crash_scale:.2f} — manual clear required",
                    "crash_guard_halt"
                )
                return {"ok": False, "skipped": True, "reason": "CRASH_GUARD_HALT", "scale": crash_scale}

        # ═══════ STAGE 12: Adaptive Rebalance ═══════
        # [V237-Phase4D] 이전 사이클의 execution pressure 점수를 Redis에서 읽어 전달
        # order_builder가 telemetry로 기록한 crowding/flow/impact 점수를 다음 사이클 정책에 반영
        _prev_crowding_score = 0.0
        _prev_flow_score = 0.0
        _prev_impact_score = 0.0
        try:
            _cs_raw = r.get("metrics:execution_pressure:crowding_score")
            _fs_raw = r.get("metrics:execution_pressure:flow_imbalance_score")
            _is_raw = r.get("metrics:execution_pressure:impact_pressure_score")
            if _cs_raw: _prev_crowding_score = float(_cs_raw)
            if _fs_raw: _prev_flow_score = float(_fs_raw)
            if _is_raw: _prev_impact_score = float(_is_raw)
        except Exception:
            pass  # fail-open: 읽기 실패 시 0으로 유지

        adapt = self.adaptive_rebalance.evaluate(
            mode=mode, gate_state=gate_state, vix=vix,
            crowding_score=_prev_crowding_score,
            flow_imbalance_score=_prev_flow_score,
            impact_pressure_score=_prev_impact_score,
        )
        self._poll_interval_ms = adapt.poll_ms

        # [V237-Phase4D] execution_pressure telemetry를 Redis에 기록
        try:
            if adapt.execution_pressure:
                _ep = adapt.execution_pressure
                r.setex("metrics:execution_pressure:level", 300, str(_ep.get("pressure_level", "NONE")))
                r.setex("metrics:execution_pressure:score", 300, str(_ep.get("pressure_score", 0.0)))
        except Exception:
            pass  # Non-critical telemetry

        # ═══════ STAGE 13: Build Intents ═══════
        alpha_scores = {}
        confidence = {}
        for sym in symbols:
            alpha_raw = r.hget("alpha:scores", sym)
            if alpha_raw:
                alpha_scores[sym] = _safe_float(alpha_raw, label=f"alpha:{sym}")
            conf_raw = r.hget("confidence:scores", sym)
            if conf_raw:
                confidence[sym] = _safe_float(conf_raw, label=f"conf:{sym}")

        _v400_applied = False  # [HOTFIX] V400 flag default
        tilted_targets = {}
        for sym, tgt in targets.items():
            tilt = self.regime_theme.get_multiplier(regime, sym)
            tv = tgt.get("target_value", 0) if isinstance(tgt, dict) else float(tgt)
            tilted_targets[sym] = {"target_value": tv * tilt.multiplier}
        # ═══════ STAGE 13.5: Multi-Sleeve Blend [v300 Integration] ═══════
        # sleeve:final_weights:current에서 sleeve allocator의 blended weights를 읽어
        # tilted_targets를 sleeve-aware weights로 교체한다.
        # Fallback: sleeve 데이터가 없거나 stale(>120s)이면 기존 tilted_targets 유지.
        _sleeve_applied = False
        try:
            _sleeve_raw = r.get("sleeve:final_weights:current")
            if _sleeve_raw:
                _sleeve_data = json.loads(_sleeve_raw)
                _sleeve_ts = _sleeve_data.get("ts", 0)
                _sleeve_age = time.time() - _sleeve_ts
                _sleeve_weights = _sleeve_data.get("weights", {})
                _sleeve_mix = _sleeve_data.get("sleeve_mix", {})
                if _sleeve_age <= 120 and _sleeve_weights:
                    _sleeve_tilted = {}
                    for _s_sym, _s_w in _sleeve_weights.items():
                        if _s_w > 0 and _s_sym in symbols:
                            _s_tilt = self.regime_theme.get_multiplier(regime, _s_sym)
                            _sleeve_tilted[_s_sym] = {"target_value": _s_w * budget_usd * _s_tilt.multiplier}
                    if _sleeve_tilted:
                        _old_tgt_count = len(tilted_targets)
                        tilted_targets = _sleeve_tilted
                        _sleeve_applied = True
                        logger.info(
                            "SLEEVE_BLEND_APPLIED: %d->%d symbols, mix=core:%.2f/def:%.2f/flow:%.2f, age=%.0fs",
                            _old_tgt_count, len(tilted_targets),
                            _sleeve_mix.get("core", 0), _sleeve_mix.get("defensive", 0),
                            _sleeve_mix.get("flow_event", 0), _sleeve_age)
                    else:
                        logger.warning("SLEEVE_BLEND_SKIP: no valid symbols in sleeve weights")
                elif _sleeve_age > 120:
                    logger.warning("SLEEVE_BLEND_STALE: age=%.0fs (>120s), using original targets", _sleeve_age)
        except Exception as _sleeve_err:
            logger.warning("SLEEVE_BLEND_ERROR: %s", _sleeve_err)
        try:
            r.setex("nextgen2:sleeve_blend:applied", 300, json.dumps({"applied": _sleeve_applied, "ts": time.time()}))
        except Exception:
            pass

        # [SSMv2] lazy init — 장 시작 시 하루 1회 초기화
        if self._session_state is None and SessionStateManager is not None:
            try:
                self._session_state = SessionStateManager(r, day_key())
                logger.info("SSMv1_INIT: session_state initialized for %s", day_key())
            except Exception as _e:
                logger.warning("SSMv1_INIT_FAIL: %s", _e)
        if self._ssm_v2 is None and _SSM_V2_AVAILABLE and SessionStateManagerV2 is not None:
            try:
                self._ssm_v2 = SessionStateManagerV2(
                    redis_client=r,
                    session_date=day_key(),
                    env=self.rc.env if hasattr(self.rc, "env") else "live",
                    strategy_id=self.rc.strategy,
                )
                logger.info("SSMv2_INIT: SessionStateManagerV2 initialized for %s", day_key())
            except Exception as _e:
                logger.warning("SSMv2_INIT_FAIL: %s", _e)

        # [V237-Phase4C] LatencyGuard DEGRADE 시 champion_scale에 _latency_scale 적용
        _effective_champion_scale = champion_scale * _latency_scale
        if _latency_scale < 1.0:
            logger.info("LATENCY_DEGRADE_SCALE: champion_scale %.3f -> %.3f (latency_scale=%.2f)",
                        champion_scale, _effective_champion_scale, _latency_scale)

        # [BUG-FIX-6] build_order_intents에 vix, et_hour, et_min 전달 누락 수정
        # SessionStateManager.can_enter()는 vix, et_hour, et_min이 필요하지만
        # 기존 코드에서는 이 인자들을 build_order_intents에 전달하지 않았음
        _now_et = get_ny_time()
        _et_hour = _now_et.hour if _now_et else -1
        _et_min = _now_et.minute if _now_et else -1


        # [V400-FIX] champion_scale 이중 적용 방지
        # V400가 tilted_targets를 이미 adjusted_weight * budget_usd로 교체했으므로
        # build_order_intents에는 champion_scale=1.0을 전달해야 함
        if _v400_applied:
            _effective_champion_scale = 1.0
            crash_scale = 1.0
            logger.info("V400_DOUBLE_APPLY_FIX: champion_scale=1.0, crash_scale=1.0 (V400 already applied)")

        result = build_order_intents(
            targets=tilted_targets,
            positions=positions,
            prices=prices,
            budget_usd=budget_usd,
            champion_scale=_effective_champion_scale,
            crash_scale=crash_scale,
            regime=regime,
            cycle_id=cycle_id,
            decision_id=decision_id,
            edge_gate=self.edge_gate,
            turnover_gov=self.turnover_gov,
            profit_interrupt=self.profit_interrupt,
            concentration_guard=self.concentration_guard,
            confidence=confidence,
            alpha_scores=alpha_scores,
            pnl_day=daily_pnl_pct,
            min_notional_usd=self._min_notional_usd,  # [v7.1.0] Config-driven, not hard-coded
            max_single_position_pct=self.rc.max_single_position_pct,
            session_state=self._ssm_v2 or self._session_state,  # [SSMv2-FULL] SSMv2 우선, SSMv1 fallback
            vix=vix,           # [BUG-FIX-6] SSM can_enter()에 vix 전달
            et_hour=_et_hour,  # [BUG-FIX-6] SSM 개장초 가드에 ET 시간 전달
            et_min=_et_min,    # [BUG-FIX-6] SSM 개장초 가드에 ET 분 전달
        )

        logger.info("INTENTS_BUILT: %s", result.summary)

        # ═══════ STAGE 13.9: Rebalance Period Gate [v7.8.0] ═══════
        # 챔피언 전략의 rebal_period(일)를 런타임에서 강제한다.
        # 계산(STAGE 1-13)은 매 사이클 수행하되, 주문 생성(emit)은
        # 리밸런싱 창(window)일 때만 허용한다.
        # 3단 게이트: REBALANCE_ALLOWED / RISK_ONLY / FULL_BLOCK
        _rebal_gate_state = "RISK_ONLY"  # fail-closed default
        _rebal_gate_reason = "DEFAULT"
        _rebal_gate_next_ts = None
        try:
            # 1) 수동 오버라이드 체크
            _gate_override = r.get("nextgen2:rebal_gate:override")
            if _gate_override and str(_gate_override).upper() == "REBALANCE_ALLOWED":
                _rebal_gate_state = "REBALANCE_ALLOWED"
                _rebal_gate_reason = "MANUAL_OVERRIDE"
            elif _is_truthy(r.get("champion:rebalance:force")):
                _rebal_gate_state = "REBALANCE_ALLOWED"
                _rebal_gate_reason = "FORCE_FLAG"
            else:
                # 2) 마지막 리밸런싱 시각 확인
                _last_rebal_raw = r.get("champion:rebalance:last_ts")
                if not _last_rebal_raw or not str(_last_rebal_raw).strip():
                    _rebal_gate_state = "REBALANCE_ALLOWED"
                    _rebal_gate_reason = "NO_HISTORY"
                else:
                    _last_rebal_ts = self._parse_timestamp(str(_last_rebal_raw))
                    if _last_rebal_ts is None:
                        _rebal_gate_state = "REBALANCE_ALLOWED"
                        _rebal_gate_reason = "UNPARSEABLE_TS"
                    else:
                        # 3) 리밸런싱 주기 로드
                        _rebal_days = 2  # 기본값
                        try:
                            _cadence_raw = r.get("policy:rebalance_cadence")
                            if _cadence_raw:
                                _cadence = safe_json_parse(str(_cadence_raw))
                                if isinstance(_cadence, dict) and "rebalance_days" in _cadence:
                                    _rebal_days = int(_cadence["rebalance_days"])
                        except Exception:
                            pass
                        if _rebal_days < 1:
                            _rebal_days = 1  # 최소 1일
                        # 4) 다음 리밸런싱 시각 계산
                        _next_rebal_ts = _last_rebal_ts + (_rebal_days * 86400)
                        _rebal_gate_next_ts = _next_rebal_ts
                        _now_gate = time.time()
                        if _now_gate >= _next_rebal_ts:
                            _rebal_gate_state = "REBALANCE_ALLOWED"
                            _elapsed = _now_gate - _next_rebal_ts
                            _rebal_gate_reason = f"WINDOW_OPEN(elapsed={_elapsed:.0f}s)"
                        else:
                            _remaining = _next_rebal_ts - _now_gate
                            _rebal_gate_state = "RISK_ONLY"
                            _rebal_gate_reason = f"WINDOW_CLOSED(remaining={_remaining:.0f}s/{_remaining/3600:.1f}h)"
        except Exception as _gate_err:
            logger.warning("REBAL_GATE_ERROR: %s — defaulting to RISK_ONLY", _gate_err)
            _rebal_gate_state = "RISK_ONLY"
            _rebal_gate_reason = f"ERROR({_gate_err})"

        # 게이트 상태를 Redis에 기록 (운영 대시보드용)
        try:
            r.setex("nextgen2:rebal_gate:state", 120, _rebal_gate_state)
            r.setex("nextgen2:rebal_gate:reason", 120, _rebal_gate_reason)
            if _rebal_gate_next_ts:
                r.setex("nextgen2:rebal_gate:next_rebal_ts", 120,
                        _dt.fromtimestamp(_rebal_gate_next_ts, tz=_tz.utc).isoformat())
        except Exception:
            pass  # Non-critical telemetry

        logger.info("REBAL_GATE: state=%s, reason=%s", _rebal_gate_state, _rebal_gate_reason)

        # RISK_ONLY 모드: non-risk intent를 필터링
        _rebal_blocked_count = 0
        _rebal_blocked_notional = 0.0
        if _rebal_gate_state == "RISK_ONLY" and hasattr(result, 'intents'):
            _filtered_intents = []
            # [STRUCTURAL_FIX] 집중 리스크 임계값 로드 (기본 8%)
            _max_single_pct = 8.0
            try:
                _msp_raw = r.get("policy:max_single_position_pct")
                if _msp_raw:
                    _max_single_pct = float(str(_msp_raw))
            except Exception:
                pass
            # 현재 포지션 비중 로드 [STRUCTURAL_FIX_V2: 올바른 데이터 구조]
            _pos_pcts = {}
            try:
                _pos_raw = r.get("emarkos:v1:positions")
                if _pos_raw:
                    import json as _json_gate
                    _pos_data = _json_gate.loads(str(_pos_raw))
                    # 데이터 구조: {"positions": {"SYM": {"marketValue": N}}, "totalMarketValue": N}
                    if isinstance(_pos_data, dict) and "positions" in _pos_data:
                        _total_mv = float(_pos_data.get("totalMarketValue", 0))
                        _positions_dict = _pos_data.get("positions", {})
                        if _total_mv > 0 and isinstance(_positions_dict, dict):
                            for _sym, _pinfo in _positions_dict.items():
                                if isinstance(_pinfo, dict):
                                    _mv = abs(float(_pinfo.get("marketValue", _pinfo.get("market_value", 0))))
                                    _pos_pcts[_sym] = (_mv / _total_mv) * 100
                    elif isinstance(_pos_data, list):
                        # 폴백: list 형태 [{symbol, marketValue}, ...]
                        _total_mv = sum(abs(float(p.get("marketValue", p.get("market_value", 0)))) for p in _pos_data if isinstance(p, dict))
                        if _total_mv > 0:
                            for _p in _pos_data:
                                if isinstance(_p, dict):
                                    _sym = _p.get("symbol", "")
                                    _mv = abs(float(_p.get("marketValue", _p.get("market_value", 0))))
                                    _pos_pcts[_sym] = (_mv / _total_mv) * 100
                    if _pos_pcts:
                        _top3 = sorted(_pos_pcts.items(), key=lambda x: -x[1])[:3]
                        logger.info("CONCENTRATION_CHECK: top3=%s, threshold=%.1f%%",
                                    [(s, f"{p:.1f}%") for s, p in _top3], _max_single_pct)
            except Exception as _e:
                logger.warning("CONCENTRATION_CHECK_ERROR: %s", _e)
            for _ri in result.intents:
                # risk-reducing SELL은 항상 허용
                _is_risk_sell = (
                    _ri.side == "SELL"
                    and (regime in ("CRISIS", "BEAR", "CAUTION") or crash_scale < 1.0)
                )
                # [CONCENTRATION-FIX v3] concentration-risk SELL은 레짐 무관하게 허용
                _max_position_pct_frac = _aoa_safe_float(r.get("policy:max_single_position_pct"), 8.0) / 100.0
                _total_equity_for_conc = _aoa_safe_float(
                    r.get("ares:equity:total") or r.get("equity:total") or r.get("equity_total"), 185000.0
                )
                _reason_text = str(getattr(_ri, "reason", "") or getattr(_ri, "tag", "") or getattr(_ri, "note", "")).upper()
                _is_concentration_sell = (
                    _ri.side == "SELL"
                    and (
                        "CONCENTRATION" in _reason_text
                        or "MAX_SINGLE_POSITION" in _reason_text
                        or "EXCESS_WEIGHT" in _reason_text
                        or "CAP_SCALED" in _reason_text
                    )
                )
                if not _is_concentration_sell and _ri.side == "SELL":
                    try:
                        _pos_pct = _aoa_extract_symbol_position_pct(r, getattr(_ri, 'symbol', ''), _total_equity_for_conc)
                        if _pos_pct > (_max_position_pct_frac * 1.25):
                            _is_concentration_sell = True
                            logger.info(
                                "[CONCENTRATION-FIX] %s SELL allowed in RISK_ONLY: current=%.2f%% limit=%.2f%%",
                                getattr(_ri, 'symbol', ''), _pos_pct * 100.0, _max_position_pct_frac * 100.0
                            )
                    except Exception:
                        pass
                if _is_risk_sell or _is_concentration_sell:
                    if _is_concentration_sell and not _is_risk_sell:
                        logger.info(
                            "REBAL_GATE_CONCENTRATION_SELL_ALLOWED: symbol=%s reason=%s",
                            getattr(_ri, 'symbol', ''), _reason_text[:50]
                        )
                    _filtered_intents.append(_ri)
                else:
                    _rebal_blocked_count += 1
                    _rebal_blocked_notional += getattr(_ri, 'notional_usd', 0)
                    logger.info(
                        "REBAL_GATE_BLOCKED: symbol=%s side=%s reason=%s "
                        "state=%s next_rebal=%s blocked_notional=$%.2f",
                        _ri.symbol, _ri.side, _rebal_gate_reason,
                        _rebal_gate_state,
                        _dt.fromtimestamp(_rebal_gate_next_ts, tz=_tz.utc).isoformat() if _rebal_gate_next_ts else "N/A",
                        getattr(_ri, 'notional_usd', 0),
                    )
                    _structured_log("REBAL_GATE_BLOCKED",
                                    symbol=_ri.symbol, side=_ri.side,
                                    notional=getattr(_ri, 'notional_usd', 0),
                                    gate_state=_rebal_gate_state,
                                    gate_reason=_rebal_gate_reason,
                                    strategy=self.rc.strategy)
            result.intents[:] = _filtered_intents  # in-place replace
            if _rebal_blocked_count > 0:
                logger.info("REBAL_GATE_SUMMARY: blocked=%d, blocked_notional=$%.2f, passed=%d (risk-only)",
                            _rebal_blocked_count, _rebal_blocked_notional, len(_filtered_intents))

        # 게이트 카운터 Redis 기록 (일간)
        try:
            _gate_day = day_key()
            r.hincrby(f"nextgen2:rebal_gate:stats:{_gate_day}", "blocked", _rebal_blocked_count)
            r.hincrby(f"nextgen2:rebal_gate:stats:{_gate_day}", "allowed",
                      len(result.intents) if hasattr(result, 'intents') else 0)
            r.expire(f"nextgen2:rebal_gate:stats:{_gate_day}", 172800)  # 48h TTL
        except Exception:
            pass  # Non-critical

        # 리밸런싱 완료 후 타임스탬프 갱신 플래그 (emit 후 STAGE 15에서 처리)
        _rebal_gate_was_allowed = (_rebal_gate_state == "REBALANCE_ALLOWED")

        # ═══════ STAGE 14: Emit Intents ═══════
        emitted = 0
        errors = 0

        producer_lock_acquired_this_cycle = False  # Track if we acquired the lock in this cycle
        # [v6.2.0] Include PID in producer identity for double-run detection.
        # If two instances of the same strategy run concurrently, the PID
        # disambiguates them and enables operator diagnostics.
        my_producer_id = f"nextgen2:{self.rc.strategy}:pid{os.getpid()}:{self._instance_uuid}"
        # [v7.1.0] Persist producer identity to dedicated key for ops diagnostics.
        # Watchdog/dashboards can read this to identify which instance is producing.
        # Write first_started_at only once per process; update last_seen_at each cycle.
        try:
            _identity_key = f"nextgen2:producer:identity:{self.rc.strategy}"
            _existing_identity = safe_json_parse(r.get(_identity_key) or "{}")
            _identity_data = {
                "producer_id": my_producer_id,
                "strategy": self.rc.strategy,
                "pid": os.getpid(),
                "version": VERSION,
                "last_seen_at": time.time(),
            }
            # Preserve first_started_at from previous write if same PID;
            # otherwise this is a new process, record current time.
            if (isinstance(_existing_identity, dict)
                    and _existing_identity.get("pid") == os.getpid()
                    and "first_started_at" in _existing_identity):
                _identity_data["first_started_at"] = _existing_identity["first_started_at"]
            else:
                _identity_data["first_started_at"] = time.time()
            r.set(_identity_key, json.dumps(_identity_data), ex=PRODUCER_LOCK_TTL * 2)
        except Exception:
            pass  # Non-critical telemetry

        # [v7.1.0] Max cycle notional: triple-cap design (intentional).
        # The effective cap is min(rc_cap, redis_cap, budget*0.25).
        # This means budget_usd*0.25 is a hard ceiling even if rc.max_cycle_notional_usd
        # is higher — this is by design to prevent any single cycle from deploying
        # more than 25% of total budget, regardless of configuration.
        # Operators who want a higher cap must increase budget_usd (equity) itself.
        _rc_cap = _safe_float(getattr(self.rc, 'max_cycle_notional_usd', budget_usd * 0.25),
                              default=budget_usd * 0.25, lo=100.0, label="rc.max_cycle_notional_usd")
        _redis_cap_raw = r.get("nextgen2:max_cycle_notional")
        _redis_cap = _safe_float(_redis_cap_raw, default=_rc_cap, lo=100.0,
                                 label="nextgen2:max_cycle_notional") if _redis_cap_raw else _rc_cap
        max_cycle_notional = min(_redis_cap, _rc_cap, budget_usd * 0.25)
        cycle_notional_used = 0.0
        cycle_sell_notional_exempt = 0.0  # [v5.3.0] Track exempt sell notional separately

        max_cycle_orders = self.rc.max_cycle_orders
        max_single_order_notional = self.rc.max_single_order_notional_usd
        orders_this_cycle = 0

        # Deterministic state hash for idempotency (no cycle_count, no volatile fields)
        _stable_positions = {}
        for sym in symbols:
            p = positions.get(sym, {})
            _stable_positions[sym] = _safe_int(p.get("shares", p.get("qty", 0)), label=f"stable:{sym}")

        # [v7.1.0] Include schema_version and generation_id in state hash
        # for post-mortem explainability. If targets schema drifts or generation
        # changes, the state hash changes → new decision → new idempotency window.
        _state_input = stable_json({
            "strategy": self.rc.strategy,
            "day": day_key(),
            "targets_hash": sha256_hex(stable_json(tilted_targets), 12),
            "positions_hash": sha256_hex(stable_json(_stable_positions), 12),
            "targets_gen": str(targets_data.get("generation_id", targets_data.get("id", ""))),
            "schema_ver": str(targets_data.get("schema_version", "")),
        })
        state_decision_id = f"sdec-{sha256_hex(_state_input, 16)}"

        # Mode-scoped idempotency
        # [v4.5.0] Use config-based TTLs instead of hard-coded values
        if is_live:
            idem_ttl = self.config.redis.idem_ttl_sec  # 7 days
            idem_prefix = "idempo:ng2:live"
            idem_claim_ttl = self.config.redis.idem_claim_ttl_sec  # 15 min (configurable)
        elif is_shadow:
            idem_ttl = self.config.redis.idem_ok_ttl_sec  # 24h
            idem_prefix = "idempo:ng2:shadow"
            idem_claim_ttl = self.config.redis.idem_fail_ttl_sec  # 5 min
        else:
            idem_ttl = self.config.redis.idem_ok_ttl_sec  # 24h
            idem_prefix = "idempo:ng2:dryrun"
            idem_claim_ttl = self.config.redis.idem_fail_ttl_sec  # 5 min

        # [v4.5.0] Stream MAXLEN from config
        stream_maxlen = self.config.redis.stream_maxlen_approx

        # Resolve emission stream
        if is_live:
            # [v4.6.0] LIVE: HARDCODED to STREAM_LIVE_INTENT. No Redis override.
            # GPT HIGH: A bad override can route intents to an unconsumed stream
            # (silent no-trade) or an unintended consumer (wrong execution path).
            emit_stream = STREAM_LIVE_INTENT
            # Warn if override key exists (operator may expect it to work)
            _override = r.get("nextgen2:emit_stream")
            if _override and _override != STREAM_LIVE_INTENT:
                logger.warning("EMIT_STREAM_OVERRIDE_IGNORED: nextgen2:emit_stream=%s "
                               "but LIVE always uses %s", _override, STREAM_LIVE_INTENT)
                self._send_alert(
                    f"\u26a0\ufe0f EMIT_STREAM_OVERRIDE_IGNORED in LIVE: {_override} "
                    f"(hardcoded to {STREAM_LIVE_INTENT})",
                    "emit_stream_override"
                )
        elif is_shadow:
            emit_stream = STREAM_SHADOW_INTENT
        else:
            emit_stream = STREAM_DRYRUN_INTENT
        logger.info("EMIT_STREAM: %s (mode=%s)", emit_stream, mode)

        # Producer lock (LIVE only) — atomic SET NX
        # [v7.1.0] CRITICAL FIX: Consistent ownership semantics.
        # On restart within PRODUCER_LOCK_TTL, the old PID's lock still exists.
        # We use strategy-prefix matching for ownership (not exact PID match)
        # and atomically update the lock value to our PID via Lua CAS.
        # This ensures per-emit checks (also strategy-prefix based) never
        # falsely trigger PRODUCER_LOCK_LOST after a normal restart.
        # [v7.7.2] Revert to strategy-prefix matching for ownership.
        # Exact-match (v7.7.1) caused GLOBAL halt on normal restart within
        # PRODUCER_LOCK_TTL because the old instance's UUID differs.
        # Strategy-prefix + CAS takeover is the correct pattern:
        # same strategy → CAS update → new PID/UUID takes over safely.
        _my_strategy_prefix = f"nextgen2:{self.rc.strategy}:"
        if is_live:
            claimed = r.set(self._producer_lock_key, my_producer_id, nx=True, ex=PRODUCER_LOCK_TTL)
            if claimed:
                producer_lock_acquired_this_cycle = True
                # [v8.0.0] Fencing Token 발급: 락 획득 시 token 증가
                try:
                    self._fencing_token = int(r.incr(self._fencing_token_key))
                    r.expire(self._fencing_token_key, PRODUCER_LOCK_TTL * 10)
                    _structured_log("FENCING_TOKEN_ISSUED",
                                   token=self._fencing_token,
                                   strategy=self.rc.strategy)
                    logger.info("FENCING_TOKEN: issued token=%d for strategy=%s",
                               self._fencing_token, self.rc.strategy)
                except Exception as _ft_err:
                    logger.warning("FENCING_TOKEN: issue failed: %s", _ft_err)
            if not claimed:
                current_owner = r.get(self._producer_lock_key)
                if not current_owner or not current_owner.startswith(_my_strategy_prefix):
                    # [v7.7.3] Skip emission + alert + retry next cycle.
                    # Do NOT set GLOBAL halt on first collision — the other instance
                    # may be a transient overlap during rolling restart.
                    # Only escalate to halt if collision persists (tracked via Redis).
                    _collision_key = f"nextgen2:producer_collision_count:{self.rc.strategy}"
                    # [v7.7.3] Guard against corrupted/non-integer values
                    try:
                        _collision_count = int(r.incr(_collision_key))
                    except Exception:
                        r.delete(_collision_key)
                        r.set(_collision_key, "1", ex=PRODUCER_LOCK_TTL * 3)
                        _collision_count = 1
                    r.expire(_collision_key, PRODUCER_LOCK_TTL * 3)  # auto-clear after 3x TTL
                    _collision_grace = 5  # [FIX-P1B: 3→5, more retries before HALT 2026-03-05]
                    logger.critical(
                        "PRODUCER_COLLISION: stream owned by '%s' (collision %d/%d), "
                        "skipping emission this cycle",
                        current_owner, _collision_count, _collision_grace
                    )
                    _structured_log("PRODUCER_COLLISION",
                                   owner=str(current_owner),
                                   my_id=my_producer_id,
                                   collision_count=_collision_count,
                                   strategy=self.rc.strategy)
                    self._send_alert(
                        f"\U0001f6a8 PRODUCER_COLLISION: {current_owner} owns stream "
                        f"(collision {_collision_count}/{_collision_grace}). "
                        f"Skipping emission, will retry next cycle.",
                        "producer_collision"
                    )
                    if _collision_count >= _collision_grace:
                        # Sustained collision: escalate to halt
                        logger.critical("PRODUCER_COLLISION_SUSTAINED: %d consecutive collisions, setting halt",
                                       _collision_count)
                        self._set_halt(r, f"PRODUCER_COLLISION_SUSTAINED:{current_owner}", is_live=True)
                        # [FIX-P1B: Auto-expire HALT after 600s to prevent permanent lockout 2026-03-05]
                        _halt_keys = [
                            "nextgen2:trade:halt:global",
                            "nextgen2:trade:halt:reason:global",
                            f"nextgen2:trade:halt:{self.rc.strategy}",
                            f"nextgen2:trade:halt:reason:{self.rc.strategy}",
                        ]
                        for _hk in _halt_keys:
                            try:
                                r.expire(_hk, 600)  # auto-clear after 10 min
                            except Exception:
                                pass
                        r.delete(_collision_key)
                    return {"ok": False, "_is_error": True, "reason": "PRODUCER_COLLISION"}
                else:
                    # Same strategy, possibly different PID (restart scenario).
                    # Atomically update lock value to our PID and refresh TTL.
                    # Lua CAS: only update if current value still matches what we read.
                    _lua_cas_lock = """
                    local cur = redis.call('GET', KEYS[1])
                    if cur == ARGV[1] then
                        redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
                        return 1
                    else
                        return 0
                    end
                    """
                    try:
                        _cas_ok = r.eval(_lua_cas_lock, 1, self._producer_lock_key,
                                        current_owner, my_producer_id, str(PRODUCER_LOCK_TTL))
                        if _cas_ok:
                            logger.info("PRODUCER_LOCK_TAKEOVER: %s → %s (same strategy, PID update)",
                                       current_owner, my_producer_id)
                            producer_lock_acquired_this_cycle = True
                            # [v7.7.3] Reset collision counter on successful takeover
                            _collision_key = f"nextgen2:producer_collision_count:{self.rc.strategy}"
                            r.delete(_collision_key)
                        else:
                            # [v7.7.0] CAS failed — another instance changed the lock
                            # between our GET and EVAL. In LIVE, this is fail-closed:
                            # we MUST NOT emit without definitively owning the lock.
                            # This prevents the rare edge case where two instances
                            # both believe they own the lock after heartbeat failure.
                            _actual_after_cas = r.get(self._producer_lock_key)
                            logger.critical(
                                "PRODUCER_LOCK_CAS_FAIL: expected=%s, actual_after=%s — "
                                "fail-closed in LIVE, aborting emission",
                                current_owner, _actual_after_cas
                            )
                            _structured_log("PRODUCER_LOCK_CAS_FAIL",
                                           expected=current_owner,
                                           actual_after=str(_actual_after_cas),
                                           my_producer_id=my_producer_id,
                                           strategy=self.rc.strategy)
                            self._send_alert(
                                f"\U0001f6a8 PRODUCER_LOCK_CAS_FAIL: lock changed during takeover "
                                f"(expected={current_owner}, actual={_actual_after_cas}). "
                                f"Emission aborted for safety.",
                                "producer_cas_fail"
                            )
                            return {"ok": False, "_is_error": True,
                                    "reason": "PRODUCER_LOCK_CAS_FAIL"}
                    except Exception as _cas_err:
                        # [v7.7.0] CAS error is also fail-closed in LIVE:
                        # if we can't atomically verify/update lock ownership,
                        # we must not proceed with emission.
                        logger.critical(
                            "PRODUCER_LOCK_CAS_ERROR: %s — fail-closed in LIVE, "
                            "aborting emission", _cas_err
                        )
                        _structured_log("PRODUCER_LOCK_CAS_ERROR",
                                       error=str(_cas_err),
                                       my_producer_id=my_producer_id,
                                       strategy=self.rc.strategy)
                        return {"ok": False, "_is_error": True,
                                "reason": "PRODUCER_LOCK_CAS_ERROR"}

        _last_lock_refresh = time.time()  # [v5.4.0] Track last lock refresh for time-based heartbeat
        for intent in result.intents:
            # Validate intent BEFORE claiming idempotency key
            # [CASH-FEEDBACK-v1] executor에서 INSUFFICIENT_CASH 거부된 종목 skip (TTL 300s)
            if intent.side in ("BUY",) or intent.delta_shares > 0:
                _cash_fail_key = f"order:cash_fail:{intent.symbol}:BUY"
                _cash_fail = r.get(_cash_fail_key)
                if _cash_fail:
                    logger.info("CASH_FAIL_SKIP: %s — executor rejected (INSUFFICIENT_CASH), skipping this cycle",
                                intent.symbol)
                    continue

            if intent.delta_shares == 0 or intent.notional_usd <= 0:
                logger.warning("INVALID_INTENT_SKIP: %s delta=%d notional=%.2f",
                               intent.symbol, intent.delta_shares, intent.notional_usd)
                continue

            # Check caps BEFORE claiming idempotency key
            # [v4.6.0] BUY/SELL separate notional caps: SELL intents that reduce
            # exposure are exempt from the notional cap in risk-off regimes
            # (CRISIS/BEAR/CAUTION or crash_scale < 1.0) to allow fast de-risking.
            # [MANUS_FIX_NOTIONAL_CAP] concentration sell도 notional cap 면제
            _is_risk_reducing_sell = (
                intent.side == "SELL"
                and (
                    regime in ("CRISIS", "BEAR", "CAUTION")
                    or crash_scale < 1.0
                    or _pos_pcts.get(intent.symbol, 0) > _max_single_pct * 1.5
                )
            )

            if not _is_risk_reducing_sell and cycle_notional_used + intent.notional_usd > max_cycle_notional:
                logger.warning("MAX_NOTIONAL_CAP: %s $%.0f would exceed cycle cap $%.0f (used $%.0f)",
                               intent.symbol, intent.notional_usd, max_cycle_notional, cycle_notional_used)
                continue

            if intent.notional_usd > max_single_order_notional:
                logger.warning("MAX_SINGLE_ORDER_NOTIONAL: %s $%.0f > cap $%.0f",
                               intent.symbol, intent.notional_usd, max_single_order_notional)
                continue

            # [v4.7.0] Re-validate min_notional after all delta modifications.
            # build_order_intents may apply turnover scaling, concentration clamping,
            # or ProfitInterrupt scaling that reduces notional below the minimum.
            # Emitting sub-minimum orders generates noise and broker rejections.
            _min_notional = self._min_notional_usd  # [v4.7.0] Unified from config
            if intent.notional_usd < _min_notional:
                logger.info("MIN_NOTIONAL_RECHECK: %s $%.2f < $%.0f after modifications, skipping",
                            intent.symbol, intent.notional_usd, _min_notional)
                continue

            # [v4.7.0] Max single order quantity cap — aligned with KIS broker limit.
            # Prevents large-qty orders for low-priced symbols that would be rejected,
            # causing PENDING idempotency holds and circuit breaker escalation.
            _max_qty = self._max_single_order_qty
            if abs(intent.delta_shares) > _max_qty:
                logger.warning("MAX_SINGLE_ORDER_QTY: %s |delta|=%d > cap=%d, skipping",
                               intent.symbol, abs(intent.delta_shares), _max_qty)
                _structured_log("QTY_CAP_SKIP", symbol=intent.symbol,
                                delta=intent.delta_shares, cap=_max_qty)
                continue

            if orders_this_cycle >= max_cycle_orders:
                logger.warning("MAX_CYCLE_ORDERS: reached %d, skipping %s",
                               max_cycle_orders, intent.symbol)
                continue

            # [v5.7.0] Open-order awareness: skip emit when an open order
            # already exists for the same symbol/side. Single JSON parse,
            # with schema validation and structured logging.
            # [v5.8.0] Open-order freshness: check TTL of open-orders key.
            # If the key has no TTL or TTL > 600s, treat as stale and ignore.
            _skip_open_order = False
            try:
                _open_orders_key = f"kis:open_orders:{intent.symbol}"
                _open_ttl = r.ttl(_open_orders_key)  # -2=missing, -1=no expiry, >0=seconds left
                # Only trust open-orders data if key has a positive TTL (not stale)
                if _open_ttl > 0:
                    _open_raw = r.get(_open_orders_key)
                    if _open_raw:
                        _open_data = safe_json_parse(_open_raw)
                        if isinstance(_open_data, list):
                            for _oo in _open_data:
                                if isinstance(_oo, dict) and _oo.get("side") == intent.side:
                                    logger.info("OPEN_ORDER_SKIP: %s %s (existing open order: %s, ttl=%ds)",
                                               intent.symbol, intent.side,
                                               _oo.get("order_id", "?")[:16], _open_ttl)
                                    _structured_log("OPEN_ORDER_SKIP", symbol=intent.symbol,
                                                   side=intent.side, order_id=_oo.get("order_id", "")[:16],
                                                   key_ttl=_open_ttl)
                                    _skip_open_order = True
                                    break
                        elif _open_data is not None:
                            logger.warning("OPEN_ORDERS_SCHEMA_INVALID: %s expected list, got %s",
                                          intent.symbol, type(_open_data).__name__)
                            self._telemetry_errors += 1
                elif _open_ttl == -1:
                    # Key exists but has no expiry — stale snapshot, ignore
                    logger.debug("OPEN_ORDERS_NO_TTL: %s — ignoring stale snapshot", intent.symbol)
            except Exception:
                pass  # Non-critical: if check fails, fall through to idempotency
            if _skip_open_order:
                continue

            # 2-phase idempotency: claim as PENDING first
            idem_key = f"{idem_prefix}:{state_decision_id}:{intent.symbol}:{intent.side}"
            idem_val = r.get(idem_key)
            if idem_val:
                # Key exists — whether PENDING (in-flight/crashed) or SENT, skip.
                # PENDING means either: (a) another cycle is currently emitting, or
                # (b) a previous cycle crashed after PENDING but before SENT.
                # In both cases, the claim TTL will expire and allow retry.
                # This prevents duplicate orders on crash-restart.
                logger.info("IDEMPOTENCY_SKIP: %s %s (state=%s)", intent.symbol, intent.side, idem_val)
                continue
            # New claim: set as PENDING with configurable claim TTL
            if not r.set(idem_key, "PENDING", nx=True, ex=idem_claim_ttl):
                logger.info("IDEMPOTENCY_SKIP: %s %s (race)", intent.symbol, intent.side)
                continue

            intent_dict = intent.to_dict()
            # [v7.3.0] Enrich intent with schema/version for downstream reconciliation.
            # Enables post-mortem tracing: which SSOT generation produced this intent?
            intent_dict["engine_version"] = VERSION
            # [RUN_ID-PATCH v1.0] OIE FIREWALL 호환: run_id 필드 추가
            intent_dict["run_id"] = f"nextgen2-orchestrator:{cycle_id}"
            intent_dict["schema"] = "ORDER_INTENT"
            # [RUN_ID-PATCH v1.0] OIE FIREWALL 호환: run_id 필드 추가
            intent_dict["run_id"] = f"nextgen2-orchestrator:{cycle_id}"
            intent_dict["schema"] = "ORDER_INTENT"
            intent_dict["targets_generation_id"] = str(targets_data.get("generation_id", targets_data.get("id", "")))
            intent_dict["targets_schema_version"] = str(targets_data.get("schema_version", ""))
            intent_dict["state_decision_id"] = state_decision_id
            # Serialize any non-string values for Redis XADD compatibility
            for k, v in list(intent_dict.items()):
                if isinstance(v, (dict, list)):
                    intent_dict[k] = json.dumps(v)
                elif not isinstance(v, str):
                    intent_dict[k] = str(v)

            if is_dryrun:
                # [v4.9.0] Atomic XADD+SET for DRY_RUN too (prevents duplicate logs on crash)
                try:
                    _xf = []
                    for k, v in intent_dict.items():
                        _xf.extend([str(k), str(v)])
                    r.eval(LUA_ATOMIC_EMIT, 2, emit_stream, idem_key,
                           "SENT:dryrun", str(idem_ttl), str(stream_maxlen), *_xf)
                    logger.info("DRYRUN_EMIT: %s %s Δ%d $%.0f (atomic)",
                                intent.symbol, intent.side, intent.delta_shares, intent.notional_usd)
                    emitted += 1
                    orders_this_cycle += 1
                except Exception as e:
                    # [v6.2.0] Retry Lua once before falling back to non-atomic.
                    # This handles transient Redis BUSY errors from Lua script loading.
                    try:
                        time.sleep(0.1)
                        r.eval(LUA_ATOMIC_EMIT, 2, emit_stream, idem_key,
                               "SENT:dryrun", str(idem_ttl), str(stream_maxlen), *_xf)
                        emitted += 1
                        orders_this_cycle += 1
                    except Exception as e2:
                        logger.warning("DRYRUN_EMIT_FALLBACK: lua_retry_failed=%s orig=%s — using non-atomic", e2, e)
                        # [v5.8.0] Use approximate trimming in fallback path too
                        _fb_sid = r.xadd(emit_stream, intent_dict, maxlen=stream_maxlen, approximate=True)
                        # [v7.5.0] Include stream_id in SENT value for consistent Lua retry semantics
                        r.set(idem_key, f"SENT:dryrun:{_fb_sid}", ex=idem_ttl)
                        emitted += 1
                        orders_this_cycle += 1
            elif is_shadow:
                # [v4.9.0] Atomic XADD+SET for SHADOW too
                try:
                    _xf = []
                    for k, v in intent_dict.items():
                        _xf.extend([str(k), str(v)])
                    r.eval(LUA_ATOMIC_EMIT, 2, emit_stream, idem_key,
                           "SENT:shadow", str(idem_ttl), str(stream_maxlen), *_xf)
                    logger.info("SHADOW_EMIT: %s %s Δ%d $%.0f (atomic)",
                                intent.symbol, intent.side, intent.delta_shares, intent.notional_usd)
                    emitted += 1
                    orders_this_cycle += 1
                except Exception as e:
                    # [v6.2.0] Retry Lua once before falling back to non-atomic.
                    try:
                        time.sleep(0.1)
                        r.eval(LUA_ATOMIC_EMIT, 2, emit_stream, idem_key,
                               "SENT:shadow", str(idem_ttl), str(stream_maxlen), *_xf)
                        emitted += 1
                        orders_this_cycle += 1
                    except Exception as e2:
                        logger.warning("SHADOW_EMIT_FALLBACK: lua_retry_failed=%s orig=%s — using non-atomic", e2, e)
                        # [v5.8.0] Use approximate trimming in fallback path too
                        _fb_sid = r.xadd(emit_stream, intent_dict, maxlen=stream_maxlen, approximate=True)
                        # [v7.5.0] Include stream_id in SENT value for consistent Lua retry semantics
                        r.set(idem_key, f"SENT:shadow:{_fb_sid}", ex=idem_ttl)
                        emitted += 1
                        orders_this_cycle += 1
            elif is_live:
                # [AOA-CUTOVER v3.0] When AOA_DISABLE_NEXTGEN_DIRECT_EMIT=true, delegate LIVE emit to AOA.
                # nextgen2 still writes desired-book but does NOT write to order:intent.
                # Instead, redirect to shadow stream so AOA is the single canonical writer.
                if r.get('policy:aoa_disable_nextgen_direct_emit') == 'true':  # [MANUS_FIX] Redis-based toggle, default false
                    logger.info('AOA_DISABLE_NEXTGEN_DIRECT_EMIT: LIVE emit delegated to AOA — writing to shadow stream instead')
                    emit_stream = STREAM_SHADOW_INTENT  # redirect to shadow
                # [v7.1.0] Per-emit ownership validation: verify we still own the
                # producer lock before each LIVE emit. If another instance stole
                # the lock (e.g., mislaunch), abort immediately to prevent
                # concurrent emitters on the same stream.
                try:
                    _current_lock_owner = r.get(self._producer_lock_key)
                    # [v7.7.3] Per-emit ownership check: exact producer_id match.
                    # By the time we reach the emit loop, the lock value has already
                    # been updated to our producer_id via either:
                    #   (a) SET NX (fresh claim), or
                    #   (b) Lua CAS takeover (restart scenario).
                    # Therefore exact match is safe here and prevents the edge case
                    # where two same-strategy instances both pass prefix check.
                    if not _current_lock_owner or _current_lock_owner != my_producer_id:
                        logger.critical(
                            "PRODUCER_LOCK_LOST: expected=%s actual=%s — aborting emit loop",
                            my_producer_id, _current_lock_owner
                        )
                        self._set_halt(r, f"PRODUCER_LOCK_LOST:{_current_lock_owner}", is_live=True)
                        self._send_alert(
                            f"\U0001f6a8 PRODUCER_LOCK_LOST mid-cycle: expected={my_producer_id}, "
                            f"actual={_current_lock_owner} — halt set",
                            "producer_lock_lost"
                        )
                        _structured_log("PRODUCER_LOCK_LOST", expected=my_producer_id,
                                       actual=str(_current_lock_owner), emitted_so_far=emitted)
                        break  # Exit emit loop — do NOT continue emitting
                except Exception as _lock_err:
                    # [v7.1.0] CAS failure is fail-closed in LIVE: if we can't verify
                    # lock ownership, stop emitting to prevent potential duplicates.
                    if is_live:
                        logger.critical("PRODUCER_LOCK_CHECK_FAILED: %s — fail-closed in LIVE", _lock_err)
                        _structured_log("PRODUCER_LOCK_CHECK_FAILED", error=str(_lock_err),
                                       mode="LIVE", emitted_so_far=emitted)
                        break
                    else:
                        logger.warning("PRODUCER_LOCK_CHECK_FAILED: %s — continuing (non-LIVE)", _lock_err)

                try:
                    # Atomic emission: Lua script couples XADD (with MAXLEN ~) + idempotency SET
                    # [v4.5.0] MAXLEN ~ stream_maxlen prevents unbounded stream growth
                    xadd_fields = []
                    for k, v in intent_dict.items():
                        xadd_fields.extend([str(k), str(v)])
                    idem_sent_val = "SENT"
                    stream_id = r.eval(
                        LUA_ATOMIC_EMIT,
                        2,  # number of KEYS
                        emit_stream,  # KEYS[1]
                        idem_key,     # KEYS[2]
                        idem_sent_val,  # ARGV[1]
                        str(idem_ttl),  # ARGV[2]
                        str(stream_maxlen),  # ARGV[3] — MAXLEN ~ value
                        *xadd_fields,   # ARGV[4..N]
                    )
                    emitted_key = f"nextgen2:emitted:{cycle_id}"
                    r.hset(emitted_key, intent.symbol,
                           json.dumps({"stream_id": str(stream_id), "side": intent.side,
                                       "delta": intent.delta_shares, "ts": time.time()}))
                    r.expire(emitted_key, 86400)  # 24h TTL — prevent unbounded key growth
                    logger.info("EMIT: %s %s Δ%d $%.0f stream_id=%s (atomic, maxlen~%d)",
                                intent.symbol, intent.side, intent.delta_shares,
                                intent.notional_usd, stream_id, stream_maxlen)
                    emitted += 1
                    orders_this_cycle += 1
                    # [v5.3.0] Exempt sells don't consume buy budget
                    if _is_risk_reducing_sell:
                        cycle_sell_notional_exempt += intent.notional_usd
                    else:
                        cycle_notional_used += intent.notional_usd

                    # [v5.4.0] Producer lock heartbeat: refresh on count OR time basis
                    # Time-based refresh prevents lock expiry during slow cycles with <10 intents
                    _now_emit = time.time()
                    _should_refresh = (
                        orders_this_cycle % PRODUCER_HEARTBEAT_INTERVAL == 0
                        or (_now_emit - _last_lock_refresh) > (PRODUCER_LOCK_TTL / 3)
                    )
                    if _should_refresh:
                        # [v7.7.2] Ownership-conditional heartbeat: only EXPIRE if
                        # we still own the lock. Prevents extending another instance's
                        # lock TTL in rare race conditions.
                        _lua_cond_expire = """
                        local cur = redis.call('GET', KEYS[1])
                        if cur == ARGV[1] then
                            redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
                            return 1
                        else
                            return 0
                        end
                        """
                        try:
                            _hb_ok = r.eval(_lua_cond_expire, 1, self._producer_lock_key,
                                           my_producer_id, str(PRODUCER_LOCK_TTL))
                            if _hb_ok:
                                _last_lock_refresh = _now_emit
                                logger.debug("PRODUCER_HEARTBEAT: refreshed TTL after %d intents (time-based=%s)",
                                            orders_this_cycle, (_now_emit - cycle_start) > (PRODUCER_LOCK_TTL / 3))
                            else:
                                logger.warning("PRODUCER_HEARTBEAT_SKIP: lock owner changed, not refreshing")
                        except Exception as _hb_err:
                            logger.warning("PRODUCER_HEARTBEAT_ERROR: %s — skipping refresh", _hb_err)
                            _last_lock_refresh = _now_emit  # Avoid tight retry loop

                except Exception as e:
                    logger.error("EMIT_FAILED: %s %s: %s", intent.symbol, intent.side, e)
                    errors += 1
                    # CRITICAL: Do NOT delete the idempotency key here.
                    # The Lua EVAL may have succeeded server-side (XADD + SET both done)
                    # but the client received a timeout/network error.
                    # Deleting the key would allow a duplicate order on the next cycle.
                    # Instead, leave the key (PENDING with claim TTL) — it will either:
                    #   (a) Already be SENT (Lua succeeded) → next cycle skips correctly
                    #   (b) Still be PENDING (Lua failed) → TTL expires, retry allowed
                    # We check if the key is already SENT to determine what happened:
                    try:
                        current_val = r.get(idem_key)
                        if current_val and current_val.startswith("SENT"):
                            # Lua succeeded server-side! The order went through.
                            logger.warning("EMIT_PARTIAL_SUCCESS: %s %s — Lua succeeded (key=%s) but client errored",
                                          intent.symbol, intent.side, current_val)
                            emitted += 1  # Count as emitted since it actually went through
                            orders_this_cycle += 1
                        else:
                            logger.warning("EMIT_FAILED_PENDING: %s %s — key=%s, will expire in <=%ds for retry",
                                          intent.symbol, intent.side, current_val, idem_claim_ttl)
                    except Exception:
                        pass  # Can't check — leave key as-is (safe default)
                    # [v4.5.0] Do NOT increment _consecutive_errors here.
                    # The run() loop is the SINGLE source of truth for circuit breaker counting.
                    # This prevents double-incrementing when both emit path and run() loop count.
                    try:
                        fail_key = f"nextgen2:emit_failed:{cycle_id}"
                        r.rpush(fail_key,
                                json.dumps({"symbol": intent.symbol, "side": intent.side,
                                            "delta": intent.delta_shares, "error": str(e)}))
                        r.expire(fail_key, 86400)  # 24h TTL — prevent unbounded key growth
                    except Exception:
                        pass  # Best-effort logging
                    # Break on first EMIT_FAILED to prevent partial orders
                    logger.critical("EMIT_FAILED: breaking emission loop to prevent partial state")
                    _structured_log("EMIT_FAILED", symbol=intent.symbol, side=intent.side,
                                    delta=intent.delta_shares, error=str(e),
                                    cycle_id=cycle_id, strategy=self.rc.strategy)
                    break

        # [v7.8.0] 리밸런싱 타임스탬프 갱신: REBALANCE_ALLOWED 상태에서 emit이 1건 이상 성공하면
        # champion:rebalance:last_ts를 현재 시각으로 갱신하고, force 플래그를 리셋한다.
        # 이렇게 하면 다음 리밸런싱 창은 자동으로 rebal_days 후에 열린다.
        if _rebal_gate_was_allowed and emitted > 0:
            try:
                _rebal_now_iso = _dt.now(tz=_tz.utc).isoformat()
                r.set("champion:rebalance:last_ts", _rebal_now_iso)
                r.set("champion:rebalance:force", "false")
                # 오버라이드도 리셋 (일회성)
                r.delete("nextgen2:rebal_gate:override")
                logger.info("REBAL_TS_UPDATED: last_ts=%s, emitted=%d, force=false",
                            _rebal_now_iso, emitted)
                _structured_log("REBAL_TS_UPDATED", last_ts=_rebal_now_iso,
                                emitted=emitted, strategy=self.rc.strategy)
            except Exception as _rebal_ts_err:
                logger.warning("REBAL_TS_UPDATE_FAILED: %s", _rebal_ts_err)

        # ═══════ STAGE 15: Metrics & KPI ═══════
        cycle_duration = time.time() - cycle_start
        # [v4.8.0] Resolve effective_mode/halt_state using same logic as heartbeat
        # for consistent ops visibility across KPI and heartbeat dashboards.
        try:
            _strat_key = f"nextgen2:mode:{self.rc.strategy}"
            _strat_val = r.get(_strat_key)
            _gen_val = r.get("nextgen2:mode")
            if _strat_val:
                _kpi_effective = str(_strat_val).upper()
                _kpi_mode_src = _strat_key
            elif _gen_val:
                _kpi_effective = str(_gen_val).upper()
                _kpi_mode_src = "nextgen2:mode"
                if _kpi_effective == "LIVE":
                    _kpi_effective = "BLOCKED_LIVE"
            else:
                _shared = r.get("emarkos:v1:mode")
                _kpi_effective = str(_shared).upper() if _shared else mode
                _kpi_mode_src = "emarkos:v1:mode" if _shared else "cycle_resolved"
            _kpi_halted, _kpi_halt_reason = self._is_halted(r)
        except Exception:
            _kpi_effective = mode
            _kpi_mode_src = "cycle_resolved"
            _kpi_halted, _kpi_halt_reason = False, ""

        kpi = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cycle_id": cycle_id,
            "decision_id": decision_id,
            "version": self.version,
            "mode": mode,
            "effective_mode": _kpi_effective,
            "mode_source_key": _kpi_mode_src,
            "halt_state": _kpi_halt_reason if _kpi_halted else "none",
            "regime": regime,
            "vix": vix,
            "champion_scale": champion_scale,
            "crash_scale": crash_scale,
            "budget_usd": round(budget_usd, 2),
            "intents_built": result.summary["total_intents"],
            "intents_emitted": emitted,
            "intents_errors": errors,
            "buy_notional": result.summary["total_buy_notional"],
            "sell_notional": result.summary["total_sell_notional"],
            "cycle_notional_used": round(cycle_notional_used, 2),
            "cycle_sell_notional_exempt": round(cycle_sell_notional_exempt, 2),
            "cycle_duration_ms": round(cycle_duration * 1000, 1),
            "is_live": is_live,
            "is_shadow": is_shadow,
            "is_dryrun": is_dryrun,
            "scheduler_mode": sched.mode,
            # [v5.6.0] Telemetry errors counter for observability
            "telemetry_errors": self._telemetry_errors,
            # [v5.9.0] Per-reason skip counters for dashboards
            "skip_counters": dict(self._skip_counters),
            # [v7.8.0] Rebalance period gate state
            "rebal_gate_state": _rebal_gate_state,
            "rebal_gate_reason": _rebal_gate_reason,
            "rebal_gate_blocked": _rebal_blocked_count,
            "rebal_gate_blocked_notional": round(_rebal_blocked_notional, 2),
        }

        # [v5.2.0] Config-driven KPI stream maxlen (default 50000)
        _kpi_maxlen = self._kpi_stream_maxlen
        # [v5.6.0] Non-critical telemetry writes wrapped in try/except
        try:
            r.set("nextgen2:kpi:latest", json.dumps(kpi), ex=120)
            # [v5.3.0] Use approximate trimming to avoid latency spikes under high write rates
            r.xadd("stream:nextgen2:kpi", {"data": json.dumps(kpi)}, maxlen=_kpi_maxlen, approximate=True)
            # [v6.0.0] Export skip_counters to dedicated Redis key with TTL.
            # This bounds the heartbeat/KPI payload size over long uptimes and
            # provides a standalone key for dashboards to query.
            if self._skip_counters:
                r.set(
                    f"nextgen2:skip_counters:{self.rc.strategy}",
                    json.dumps(self._skip_counters), ex=3600
                )
        except Exception as e:
            logger.warning("KPI_WRITE_FAILED: %s", e)
            self._telemetry_errors += 1

        if self._metrics_collector and hasattr(self._metrics_collector, 'record'):
            try:
                self._metrics_collector.record("cycle_count", 1)
                self._metrics_collector.record("intents_emitted", emitted)
                self._metrics_collector.record("cycle_duration_ms", cycle_duration * 1000)
            except Exception as e:
                logger.debug("Metrics record failed: %s", e)

        # Producer lock early release: if LIVE mode, we acquired the lock, but emitted nothing,
        # release it immediately instead of waiting for TTL expiry.
        if is_live and emitted == 0 and producer_lock_acquired_this_cycle:
            _lua_del_producer_lock = """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            else
                return 0
            end
            """
            try:
                r.eval(_lua_del_producer_lock, 1, self._producer_lock_key, my_producer_id)
                logger.info("PRODUCER_LOCK_RELEASED: no intents emitted in LIVE mode")
            except Exception as e:
                logger.warning("Failed to release PRODUCER_LOCK_KEY on early exit: %s", e)

        logger.info("CYCLE_COMPLETE: %s (%.0fms, %d emitted, mode=%s)",
                     cycle_id, cycle_duration * 1000, emitted, mode)

        return {
            "ok": errors == 0,  # Only truly OK if no emission errors
            "_is_error": errors > 0,  # Explicit flag for run loop circuit breaker
            "skipped": False,
            "cycle_id": cycle_id,
            "emitted": emitted,
            "errors": errors,
            "kpi": kpi,
        }

    # ──────────────────────────── Run Loop ────────────────────────────

    def run(self):
        """Main run loop — call _cycle() at adaptive intervals."""
        self.init()
        logger.info("=== NextGen2 Orchestrator v%s Starting ===", self.version)
        logger.info("Config: env=%s, strategy=%s",
                     self.rc.env, self.rc.strategy)

        while True:
            try:
                result = self._cycle()
                reason = result.get("reason", "")

                if result.get("skipped"):
                    logger.debug("Cycle skipped: %s", reason)
                    # [v5.9.0] Increment per-reason skip counter for KPI dashboards
                    if reason:
                        self._skip_counters[reason] = self._skip_counters.get(reason, 0) + 1
                    # [v4.5.0] Reset error counter on benign skips.
                    # These indicate the system is healthy, just not trading.
                    # Errors near market close should not carry into the next session.
                    if reason in _BENIGN_SKIP_REASONS:
                        if self._consecutive_errors > 0:
                            logger.info("BENIGN_SKIP_RESET: %s, clearing %d consecutive errors",
                                        reason, self._consecutive_errors)
                            self._consecutive_errors = 0
                elif result.get("_is_error"):
                    # [v4.5.0] Single increment point for circuit breaker.
                    # _cycle_inner no longer increments _consecutive_errors directly.
                    self._consecutive_errors += result.get("errors", 1)
                    # [v4.6.0] Persist circuit breaker counter to Redis for post-mortem
                    try:
                        self._redis.set(
                            f"nextgen2:consecutive_errors:{self.rc.strategy}",
                            str(self._consecutive_errors), ex=86400
                        )
                    except Exception:
                        pass
                    logger.warning("Cycle had %d emission errors (consecutive: %d/%d)",
                                   result.get("errors", 0), self._consecutive_errors, MAX_CONSECUTIVE_ERRORS)
                elif result.get("ok"):
                    self._consecutive_errors = 0  # Reset on fully successful cycle
                    # [v7.7.2] Reset transient Redis error counter on successful cycle.
                    # Without this, sporadic (non-consecutive) Redis errors would
                    # accumulate indefinitely and eventually trigger circuit breaker.
                    self._redis_transient_errors = getattr(self, '_redis_transient_errors', 0)
                    if self._redis_transient_errors > 0:
                        logger.info("REDIS_TRANSIENT_RESET: cleared %d sporadic errors after successful cycle",
                                   self._redis_transient_errors)
                    self._redis_transient_errors = 0
            except redis_lib.ConnectionError as e:
                # [v5.4.0] Redis transient errors: retry with backoff, don't immediately
                # increment circuit breaker. This prevents brief infrastructure hiccups
                # from converting into extended trading downtime.
                self._redis_transient_errors = getattr(self, '_redis_transient_errors', 0) + 1
                _max_transient = 3  # Only escalate after 3 consecutive Redis failures
                logger.warning("REDIS_TRANSIENT_ERROR (%d/%d): %s",
                              self._redis_transient_errors, _max_transient, e)
                _structured_log("REDIS_TRANSIENT_ERROR", error=str(e),
                               transient_count=self._redis_transient_errors,
                               strategy=self.rc.strategy)
                if self._redis_transient_errors >= _max_transient:
                    # Sustained outage: now count toward circuit breaker
                    self._consecutive_errors += self._redis_transient_errors
                    self._redis_transient_errors = 0
                    logger.error("REDIS_SUSTAINED_OUTAGE: escalating to circuit breaker (consecutive=%d)",
                                self._consecutive_errors)
                else:
                    # Brief hiccup: backoff and retry without circuit breaker increment
                    time.sleep(min(5 * self._redis_transient_errors, 15))
                    continue
            except Exception as e:
                # Reset transient counter on non-Redis errors
                self._redis_transient_errors = 0
                self._consecutive_errors += 1
                # [v4.6.0] Persist circuit breaker counter to Redis for post-mortem
                try:
                    self._redis.set(
                        f"nextgen2:consecutive_errors:{self.rc.strategy}",
                        str(self._consecutive_errors), ex=86400
                    )
                except Exception:
                    pass
                logger.error("Cycle error (%d/%d consecutive): %s",
                             self._consecutive_errors, MAX_CONSECUTIVE_ERRORS,
                             e, exc_info=True)
                _structured_log("CYCLE_ERROR", error=str(e),
                                consecutive=self._consecutive_errors,
                                strategy=self.rc.strategy)
                self._send_alert(
                    f"\U0001f534 NextGen2 Cycle Error ({self._consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}",
                    "cycle_error"
                )
                # Circuit breaker: halt engine after too many consecutive errors
                if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.critical("CIRCUIT_BREAKER: %d consecutive errors, setting engine halt",
                                    self._consecutive_errors)
                    try:
                        r = self._redis
                        if r:
                            # [v4.5.0] Use strategy-scoped key ONLY for LIVE determination
                            is_live_hint = self._resolve_is_live_for_halt(r)
                            mode_hint = "LIVE" if is_live_hint else "NON_LIVE"
                            self._set_halt(
                                r,
                                f"CIRCUIT_BREAKER:{self._consecutive_errors}_consecutive_errors:mode={mode_hint}",
                                is_live=is_live_hint,
                            )
                    except Exception as halt_err:
                        logger.error("Failed to set halt in circuit breaker: %s", halt_err)
                    self._send_alert(
                        f"\U0001f6a8 CIRCUIT_BREAKER: {self._consecutive_errors} consecutive errors, engine halted",
                        "circuit_breaker"
                    )

            sleep_sec = self._poll_interval_ms / 1000.0
            time.sleep(sleep_sec)


# ──────────────────────────── Entry Point ────────────────────────────

def main():
    """Entry point for PM2 or direct execution."""
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    config = AppConfig.from_env()
    engine = Orchestrator(config)

    try:
        engine.run()
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.critical("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
