#!/usr/bin/env python3
"""
ARES NEXUS V5.5-LIVE — Autonomous Production Orchestrator
==========================================================

Direct-live orchestration layer for V5.4-A aggressive-realistic alpha.

What this service does:
- Loads V5.4-A alpha engine and computes the latest daily target.
- Preserves 2-bucket stagger state across restarts.
- Applies a hardened kernel governor before any execution.
- Publishes idempotent execution intents to Redis for the broker/execution bus.
- Maintains real feedback scoring using actual timestamped SPY prices.
- Auto-tunes ONLY bounded execution meta-parameters (never core alpha knobs).
- Fails safe: any severe anomaly => FLATTEN / HALT / DEFER.

Design constraints:
- Long-only production posture for this V5.4-A deployment.
- No shadow/paper branch; all decisions are made for live intent publication.
- Nevertheless, every unsafe condition is wired to conservative actions.

Notes:
- This file assumes an existing execution consumer subscribes to Redis stream
  ``ares:v55:execution:intents`` and converts intents to broker orders.
- Broker credentials / API contract were not present in the uploaded bundle,
  so the live boundary is implemented as a Redis execution bus.
"""
from __future__ import annotations

# === REFACTORING Phase 2: 모듈 import (전환 준비) ===
try:
    from ares_config_module import LiveKeys as _RefLK, AutoTuneBounds as _RefATB, LiveAutopilotConfig as _RefLAC
    from ares_risk_module import BrokerConsumerGuard as _RefBCG, HaltManager as _RefHM
    from ares_signal_module import RegimeTracker as _RefRT, AutoTuner as _RefAT
    _REFACTOR_MODULES_AVAILABLE = True
except ImportError:
    _REFACTOR_MODULES_AVAILABLE = False
# === END REFACTORING Phase 2 ===

import sys
sys.path.insert(0, "/home/ubuntu")
from ares_common.redis_atomic import AresAtomicOps
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")

import argparse
import hashlib
import importlib.util
import json
import logging
import math
import os
import pickle
import signal
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from live_engine_contract_guard import guard_or_raise
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

try:
    import redis  # type: ignore
except Exception:
    redis = None

UTC = timezone.utc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ares.v57.live")


# ---------------------------------------------------------------------------
# Redis keys
# ---------------------------------------------------------------------------
class LiveKeys:
    HEARTBEAT = "ares:v57:live:heartbeat"
    READINESS = "ares:v57:live:readiness"
    HALT_FLAG = "ares:v57:live:control:halt"
    RESUME_FLAG = "ares:v57:live:control:resume"
    FORCE_REBUILD = "ares:v57:live:control:force_rebuild"
    FORCE_EXECUTE = "ares:v57:live:control:force_execute"
    FLATTEN_FLAG = "ares:v57:live:control:flatten"
    EXECUTION_STREAM = "ares:v57:execution:intents"
    EXECUTION_ACK = "ares:v57:execution:ack"
    EXECUTION_LAST = "ares:v57:execution:last"
    EXECUTION_LOCK = "ares:v57:execution:lock"
    EXECUTION_IDEMP = "ares:v57:execution:idemp"
    TARGET_LAST = "ares:v57:target:last"
    TARGET_HASH = "ares:v57:target:hash"
    TARGET_META = "ares:v57:target:meta"
    METRICS = "ares:v57:metrics:latest"
    METRIC_HISTORY = "ares:v57:metrics:history"
    MARKET_SPY = "ares:v57:market:spy"
    MARKET_VIX = "ares:v57:market:vix"
    PREDICTIONS = "ares:v57:feedback:predictions"
    ACCURACY = "ares:v57:feedback:accuracy"
    ROLLING_ACCURACY = "ares:v57:feedback:rolling_accuracy"
    AUTOTUNE = "ares:v57:feedback:autotune"
    AUTOTUNE_HISTORY = "ares:v57:feedback:autotune_history"
    FLAP_COUNT = "ares:v57:feedback:flap_count"
    STATE_JSON = "ares:v57:state:summary"
    DECISION_LAST = "ares:v57:decision:last"
    DECISION_HISTORY = "ares:v57:decision:history"
    SESSION_STATS = "ares:v57:session:stats"
    # --- V5.6.1 Hardening keys ---
    CONSUMER_HEARTBEAT = "ares:v57:consumer:heartbeat"
    CONSUMER_LAST_ACK = "ares:v57:consumer:last_ack"
    AUTOTUNE_SUGGESTIONS = "ares:v57:autotune:suggestions"
    AUTOTUNE_ACTIVE_OVERRIDE = "ares:v57:autotune:active_override"
    AUTOTUNE_OVERRIDE_HISTORY = "ares:v57:autotune:override_history"
    BAD_SESSION_COUNT = "ares:v57:autotune:bad_session_count"
    INCIDENT_LAST = "ares:v57:incident:last"
    INCIDENT_HISTORY = "ares:v57:incident:history"
    # --- V5.6.1b: Production integration keys ---
    TRADING_ENABLED = "trading:enabled"          # kill-switch-authority-v2 SSOT
    TRADE_HALT = "policy:trade_halt"              # legacy emergency halt flag
    TRADE_HALT_COMPAT = "policy:trade:halt"       # compatibility halt flag
    TRADE_HALT_CANONICAL = "trade:halt"           # canonical halt flag
    LEADER_LEASE = "ares:v56:leader:lease"        # split-brain prevention
    CANONICAL_INTENT_STREAM = "emarkos:v6:order:intent"  # production executor stream


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class AutoTuneBounds:
    ntb_base: Tuple[float, float] = (0.008, 0.022)
    to_base: Tuple[float, float] = (0.14, 0.28)
    to_max: Tuple[float, float] = (0.24, 0.45)
    a2b2_base: Tuple[float, float] = (0.45, 0.75)
    a2b2_to: Tuple[float, float] = (0.04, 0.10)
    crash_vix: Tuple[float, float] = (28.0, 42.0)
    drift_mult: Tuple[float, float] = (0.75, 1.25)
    cooldown_mult: Tuple[float, float] = (0.75, 2.50)


@dataclass
class LiveKernelConfig:
    kernel_heartbeat_max_age_sec: int = 60
    max_data_lag_hours: int = 40
    max_quote_stale_sec: int = 300
    emergency_drawdown_pct: float = 0.10
    derisk_drawdown_pct: float = 0.06
    emergency_daily_loss_pct: float = 0.035
    derisk_daily_loss_pct: float = 0.020
    max_leverage: float = 1.02
    max_open_orders: int = 10  # V5.6.1 hardening: allow up to 10
    max_name_weight_drift: float = 0.035
    min_confidence_execute: float = 0.45
    force_flatten_on_stale_quotes: bool = True
    require_quote_freshness: bool = True  # V5.6.1 hardening: default True
    long_only: bool = True


@dataclass
class LiveExecutionConfig:
    redis_url: str = os.getenv("ARES_REDIS_URL") or os.getenv("REDIS_URL") or ""  # REDIS_URL required in production
    heartbeat_ttl_sec: int = 90
    cycle_sec: int = 30
    db_refresh_sec: int = 300
    state_path: str = "./v55_live_state.pkl"
    engine_path: str = "./engine_v65_c6_adapter.py"
    alpha_config_path: str = "./tc6x_config.json"
    db_path: str = "./ares_x_v11_0.db"
    min_match: int = 40
    ack_timeout_sec: int = 15
    min_order_weight_delta: float = 0.0020
    min_order_notional: float = 150.0
    max_single_order_pct_equity: float = 0.06
    max_order_count: int = 24
    max_live_turnover_pct: float = 0.40
    max_published_exposure: float = 0.98
    price_keys: List[str] = field(default_factory=lambda: [
        "realtime:feed:{sym}:last",
        "realtime:price:{sym}",
        "market:{sym}:last",
    ])
    positions_key: str = "emarkos:v1:positions"
    equity_keys: List[str] = field(default_factory=lambda: ["ofg:equity:verified", "emarkos:v1:equity"])
    open_orders_key: str = "ares:orders:open_count"
    regime_hash_key: str = "regime:final:current"
    vix_keys: List[str] = field(default_factory=lambda: ["realtime:vix:current", "market:vix", "realtime:feed:VIXY:last"])
    spy_keys: List[str] = field(default_factory=lambda: ["realtime:feed:SPY:last", "realtime:price:SPY"])
    run_id: str = "nextgen2-v56-live"  # V5.6.1b: must match executor allowlist
    use_canonical_stream: bool = True  # V5.6.1b: default to canonical production stream
    leader_lease_ttl_sec: int = 60  # V5.6.1b: leader lease TTL


@dataclass
class LiveAutopilotConfig:
    execution: LiveExecutionConfig = field(default_factory=LiveExecutionConfig)
    kernel: LiveKernelConfig = field(default_factory=LiveKernelConfig)
    autotune_bounds: AutoTuneBounds = field(default_factory=AutoTuneBounds)
    autotune_enabled: bool = True
    autotune_apply_live: bool = False  # V5.6.1 hardening: suggestion-only by default
    autotune_min_obs: int = 60  # V5.6.1 hardening: raised from 20
    autotune_cooldown_sec: int = 21600  # V5.6.1 hardening: 6h cooldown
    price_history_ttl_sec: int = 172800
    prediction_max_records: int = 4000
    history_keep: int = 2000
    actual_cost_bps: float = 13.0
    shadow_slippage_bps: float = 5.0
    shadow_impact_coeff_bps: float = 8.0
    audit_dir: str = "./audit_v55"

    @classmethod
    def from_json(cls, path: str) -> "LiveAutopilotConfig":
        raw = json.loads(Path(path).read_text())
        exe_raw = raw.get("execution", {})
        # V5.6.1: Redis credential from env var (fallback to config for backward compat)
        env_redis = os.getenv("ARES_REDIS_URL")
        if env_redis:
            exe_raw["redis_url"] = env_redis
            log.info("V5.6.1: Redis URL loaded from ARES_REDIS_URL env var")
        # V5.6.1b: parse new execution config keys with safe defaults
        for k in ("run_id", "use_canonical_stream", "leader_lease_ttl_sec"):
            exe_raw.setdefault(k, getattr(LiveExecutionConfig, k, None))
        exe = LiveExecutionConfig(**exe_raw)
        ker_raw = raw.get("kernel", {})
        # V5.6.1: Force safety defaults
        ker_raw.setdefault("require_quote_freshness", True)
        ker = LiveKernelConfig(**ker_raw)
        bnd = AutoTuneBounds(**raw.get("autotune_bounds", {}))
        return cls(
            execution=exe,
            kernel=ker,
            autotune_bounds=bnd,
            autotune_enabled=raw.get("autotune_enabled", True),
            autotune_apply_live=raw.get("autotune_apply_live", False),  # V5.6.1: default False
            autotune_min_obs=raw.get("autotune_min_obs", 60),  # V5.6.1: raised
            autotune_cooldown_sec=raw.get("autotune_cooldown_sec", 21600),  # V5.6.1: 6h
            price_history_ttl_sec=raw.get("price_history_ttl_sec", 172800),
            prediction_max_records=raw.get("prediction_max_records", 4000),
            history_keep=raw.get("history_keep", 2000),
            actual_cost_bps=raw.get("actual_cost_bps", 13.0),
            shadow_slippage_bps=raw.get("shadow_slippage_bps", 5.0),
            shadow_impact_coeff_bps=raw.get("shadow_impact_coeff_bps", 8.0),
            audit_dir=raw.get("audit_dir", "./audit_v55"),
        )


# ---------------------------------------------------------------------------
# Engine runtime state
# ---------------------------------------------------------------------------
@dataclass
class EngineShadowPersist:
    pw: List[float]
    prev_reg: int
    confidence: float
    sig_hist: List[float]
    ret_hist: List[float]
    daily_returns: List[float]
    last_selected: List[str]
    last_target: Optional[List[float]]


@dataclass
class EngineBookPersist:
    pw: List[float]
    prev_reg: int


@dataclass
class RuntimeState:
    last_engine_index: int = -1
    last_engine_date: Optional[str] = None
    last_selected: List[str] = field(default_factory=list)
    last_confidence: float = 0.5
    last_target_hash: Optional[str] = None
    equity_peak: float = 0.0
    halted: bool = False
    halt_reason: Optional[str] = None
    last_autotune_ts: float = 0.0
    last_exec_id: Optional[str] = None
    turnover_today: float = 0.0
    session_day: Optional[str] = None
    book_a: Optional[EngineBookPersist] = None
    book_b: Optional[EngineBookPersist] = None
    shadows: Dict[str, EngineShadowPersist] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)
    session_start_equity: float = 0.0


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def atomic_pickle_dump(obj: Any, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, p)


def pickle_load(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "rb") as f:
        return pickle.load(f)


def sha256_json(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def clamp(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def parse_date_str(x: Any) -> Optional[datetime]:
    if x is None:
        return None
    # Handle pandas Timestamp (may be tz-naive)
    try:
        import pandas as _pd
        if isinstance(x, _pd.Timestamp):
            if x.tzinfo is None:
                x = x.tz_localize("UTC")
            return x.to_pydatetime().astimezone(UTC)
    except Exception:
        pass
    if isinstance(x, datetime):
        if x.tzinfo is None:
            return x.replace(tzinfo=UTC)
        return x.astimezone(UTC)
    s = str(x)
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            continue
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Engine module loader
# ---------------------------------------------------------------------------
def load_engine_module(engine_path: str):
    p = Path(engine_path)
    if not p.exists():
        raise FileNotFoundError(f"engine path not found: {engine_path}")
    spec = importlib.util.spec_from_file_location("ares_v54_engine", str(p))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to create spec for {engine_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------
class RedisStore:
    def __init__(self, url: str):
        if redis is None:
            raise RuntimeError("redis-py is required at runtime for live orchestration")
        if not url:
            raise RuntimeError("REDIS_URL or ARES_REDIS_URL is required")
        if "localhost" in url or "127.0.0.1" in url:
            raise RuntimeError("Local Redis fallback is forbidden in production")
        self.r = redis.from_url(url, decode_responses=False)
        self.r.ping()

    def get_json(self, key: str, default=None):
        v = self.r.get(key)
        if not v:
            return default
        try:
            if isinstance(v, bytes):
                v = v.decode()
            return json.loads(v)
        except Exception:
            return default

    # v57: List-only keys auto-derived from redis_key_schema.py
    # DO NOT edit manually — update redis_key_schema.py instead
    @staticmethod
    def _build_list_only_suffixes():
        try:
            from redis_key_schema import ARES_KEY_SCHEMA
            return frozenset(
                ":".join(k.split(":")[2:])
                for k, v in ARES_KEY_SCHEMA.items()
                if v == "list"
            )
        except ImportError:
            # Fallback if schema not importable
            return frozenset({
                "market:spy", "market:vix", "market:qqq", "market:iwm", "market:dia",
                "market:tlt", "market:gld", "market:vxx", "market:hyg",
                "market:breadth", "market:sector_rotation", "market:flow_imbalance",
                "market:overnight_gap", "market:intraday_vol",
                "feedback:predictions", "feedback:accuracy", "feedback:autotune_history",
                "decision:history", "incident:history", "autotune:suggestions",
                "autotune:override_history", "metrics:history", "session:stats",
            })
    _LIST_ONLY_SUFFIXES = None  # lazy init

    def set_json(self, key: str, obj: Any, ex: Optional[int] = None):
        # v57 type-safety: block SET on list-only keys (auto-derived from schema)
        if self.__class__._LIST_ONLY_SUFFIXES is None:
            self.__class__._LIST_ONLY_SUFFIXES = self._build_list_only_suffixes()
        for suffix in self._LIST_ONLY_SUFFIXES:
            if key.endswith(suffix):
                log.error("BLOCKED: set_json called on list-only key %s — use publish_history_point instead", key)
                raise TypeError(f"set_json cannot be used on list-only key: {key}")
        raw = json.dumps(obj, separators=(",", ":"), default=str)
        if ex:
            self.r.setex(key, ex, raw)
        else:
            self.r.set(key, raw)

    def hgetall_text(self, key: str) -> Dict[str, str]:
        raw = self.r.hgetall(key)
        out = {}
        for k, v in raw.items():
            kk = k.decode() if isinstance(k, bytes) else str(k)
            vv = v.decode() if isinstance(v, bytes) else str(v)
            out[kk] = vv
        return out

    # ── Proactive Atomic Lua: type-check → fix → lpush → ltrim → expire ──
    _LUA_SAFE_LPUSH = """
    local key       = KEYS[1]
    local value     = ARGV[1]
    local max_items = tonumber(ARGV[2])
    local ttl_sec   = tonumber(ARGV[3])

    local ktype = redis.call('TYPE', key)['ok']
    if ktype ~= 'none' and ktype ~= 'list' then
        local quarantine_key = key .. ':wrongtype:' .. tostring(redis.call('TIME')[1])
        redis.call('RENAME', key, quarantine_key)
        redis.call('EXPIRE', quarantine_key, 604800)
        redis.log(redis.LOG_WARNING,
            'ARES safe_lpush: quarantined non-list key [' .. key .. '] (was ' .. ktype .. ')')
    end

    local length = redis.call('LPUSH', key, value)
    if max_items > 0 and length > max_items then
        redis.call('LTRIM', key, 0, max_items - 1)
    end
    if ttl_sec > 0 then
        redis.call('EXPIRE', key, ttl_sec)
    end
    return length
    """
    _safe_lpush_script = None

    def _get_safe_lpush(self):
        """Lazy-load and cache the atomic Lua script."""
        if self._safe_lpush_script is None:
            self._safe_lpush_script = self.r.register_script(self._LUA_SAFE_LPUSH)
        return self._safe_lpush_script

    def publish_history_point(self, key: str, payload: Dict[str, Any], ttl_sec: int, max_items: int = 2000):
        """Proactive atomic publish - prevents WRONGTYPE before it happens."""
        raw = json.dumps(payload, separators=(",", ":"), default=str)
        script = self._get_safe_lpush()
        for attempt in range(5):
            try:
                script(keys=[key], args=[raw, str(max_items), str(ttl_sec)])
                return
            except Exception as e:
                err_str = str(e).upper()
                if "NOSCRIPT" in err_str:
                    self._safe_lpush_script = None
                    script = self._get_safe_lpush()
                    continue
                if "WRONGTYPE" in err_str and attempt < 4:
                    log.warning("WRONGTYPE on %s (attempt %d) - Lua retry", key, attempt + 1)
                    import time; time.sleep(0.01 * (2 ** attempt))
                    continue
                log.error("Redis error publishing to %s: %s", key, e)
                raise
        log.critical("All Lua attempts failed for %s - hard reset", key)
        self.r.delete(key)
        self.r.lpush(key, raw)
        self.r.ltrim(key, 0, max_items - 1)
        self.r.expire(key, ttl_sec)


# ---------------------------------------------------------------------------
# Market / account adapters
# ---------------------------------------------------------------------------
class MarketAccountReader:
    def __init__(self, store: RedisStore, cfg: LiveAutopilotConfig):
        self.store = store
        self.cfg = cfg

    def _read_first_float(self, keys: Iterable[str]) -> Optional[float]:
        for key in keys:
            try:
                v = self.store.r.get(key)
                if v is None:
                    continue
                if isinstance(v, bytes):
                    v = v.decode()
                return float(v)
            except Exception:
                continue
        return None

    def get_vix(self) -> float:
        return self._read_first_float(self.cfg.execution.vix_keys) or 20.0

    def get_spy(self) -> float:
        return self._read_first_float(self.cfg.execution.spy_keys) or 0.0

    def get_equity(self) -> float:
        for key in self.cfg.execution.equity_keys:
            raw = self.store.r.get(key)
            if not raw:
                continue
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                data = json.loads(raw)
                if isinstance(data, dict):
                    return safe_float(data.get("total", data.get("equity", 0.0)), 0.0)
                return safe_float(data, 0.0)
            except Exception:
                try:
                    return float(raw)
                except Exception:
                    continue
        return 0.0

    def get_open_orders(self) -> int:
        raw = self.store.r.get(self.cfg.execution.open_orders_key)
        try:
            if raw is None:
                return 0
            if isinstance(raw, bytes):
                raw = raw.decode()
            return int(float(raw))
        except Exception:
            return 0

    def get_quote(self, sym: str) -> Tuple[Optional[float], Optional[float]]:
        for tmpl in self.cfg.execution.price_keys:
            key = tmpl.format(sym=sym)
            raw = self.store.r.get(key)
            if raw is None:
                continue
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                data = json.loads(raw)
                if isinstance(data, dict):
                    px = safe_float(data.get("last", data.get("price")))
                    ts = data.get("ts") or data.get("timestamp")
                    dt = parse_date_str(ts)
                    age = (utcnow() - dt).total_seconds() if dt else None
                    if px > 0:
                        return px, age
                else:
                    px = float(data)
                    return px, None
            except Exception:
                try:
                    px = float(raw)
                    return px, None
                except Exception:
                    continue
        return None, None

    def get_positions(self) -> Dict[str, Dict[str, float]]:
        raw = self.store.r.get(self.cfg.execution.positions_key)
        if not raw:
            return {}
        try:
            if isinstance(raw, bytes):
                raw = raw.decode()
            data = json.loads(raw)
        except Exception:
            return {}

        # Accept dict[str, payload] or {positions:{...}} or list[dict]
        if isinstance(data, dict) and "positions" in data:
            data = data["positions"]
        out: Dict[str, Dict[str, float]] = {}
        if isinstance(data, list):
            for row in data:
                if not isinstance(row, dict):
                    continue
                sym = row.get("symbol") or row.get("ticker")
                if not sym:
                    continue
                out[str(sym)] = {
                    "qty": safe_float(row.get("qty", row.get("quantity", row.get("shares", 0.0)))),
                    "market_value": safe_float(row.get("market_value", row.get("marketValue", row.get("value", 0.0)))),
                    "weight": safe_float(row.get("weight", 0.0)),
                    "side": 1.0 if str(row.get("side", "LONG")).upper() != "SHORT" else -1.0,
                }
        elif isinstance(data, dict):
            for sym, row in data.items():
                if not isinstance(row, dict):
                    continue
                out[str(sym)] = {
                    "qty": safe_float(row.get("qty", row.get("quantity", row.get("shares", 0.0)))),
                    "market_value": safe_float(row.get("market_value", row.get("marketValue", row.get("value", 0.0)))),
                    "weight": safe_float(row.get("weight", 0.0)),
                    "side": 1.0 if str(row.get("side", "LONG")).upper() != "SHORT" else -1.0,
                }
        return out

    def positions_to_weights(self, symbols: List[str], fallback_prices: Dict[str, float]) -> Tuple[np.ndarray, Dict[str, Any]]:
        positions = self.get_positions()
        equity = max(self.get_equity(), 1.0)
        weights = np.zeros(len(symbols), dtype=np.float64)
        stale_symbols = []
        for i, sym in enumerate(symbols):
            row = positions.get(sym)
            if not row:
                continue
            if abs(row.get("weight", 0.0)) > 1e-9:
                weights[i] = row["weight"] * row.get("side", 1.0)
                continue
            mv = row.get("market_value", 0.0)
            qty = row.get("qty", 0.0)
            px = None
            age = None
            if mv == 0.0 and qty != 0.0:
                px, age = self.get_quote(sym)
                if px is None:
                    px = fallback_prices.get(sym)
                if age is not None and age > self.cfg.kernel.max_quote_stale_sec:
                    stale_symbols.append(sym)
                mv = qty * (px or 0.0)
            weights[i] = mv / equity if equity > 0 else 0.0
        return weights, {"stale_symbols": stale_symbols, "positions": positions, "equity": equity}


# ---------------------------------------------------------------------------
# Execution bus
# ---------------------------------------------------------------------------
class RedisExecutionBus:
    def __init__(self, store: RedisStore, cfg: LiveAutopilotConfig):
        self.store = store
        self.cfg = cfg

    def submit(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        exec_id = intent["exec_id"]
        idem_key = f"{LiveKeys.EXECUTION_IDEMP}:{exec_id}"

        if str(os.getenv("AOA_DISABLE_ARES_DIRECT_EMIT", "false")).lower() in {"1", "true", "yes", "on"}:
            self.store.set_json("ares:aoa:ares_v56_shadow_submit", {
                "ts": time.time(),
                "exec_id": exec_id,
                "intent_id": str(intent.get("intent_id", exec_id)),
                "status": "shadow_only",
                "reason": "AOA_ARES_DIRECT_EMIT_DISABLED",
            }, ex=3600)
            return {
                "status": "acked",
                "exec_id": exec_id,
                "shadow_only": True,
                "reason": "AOA_ARES_DIRECT_EMIT_DISABLED",
            }
        # Canonicalize intent contract for executor compatibility.
        intent.setdefault("run_id", self.cfg.execution.run_id)
        intent.setdefault("intent_id", exec_id)
        intent.setdefault("schema", "ORDER_INTENT")
        target_stream = LiveKeys.CANONICAL_INTENT_STREAM if self.cfg.execution.use_canonical_stream else LiveKeys.EXECUTION_STREAM
        payload = json.dumps(intent, default=str)
        fields = {
            "json": payload,
            "payload": payload,
            "exec_id": exec_id,
            "intent_id": str(intent.get("intent_id", exec_id)),
            "run_id": str(intent.get("run_id", self.cfg.execution.run_id)),
            "schema": str(intent.get("schema", "ORDER_INTENT")),
            "published_at": utcnow().isoformat(),
        }
        # [LUA-ATOMIC] XADD + idempotency SET in single Lua call
        stream_id = self._atomic.emit_order_intent(
            stream=target_stream,
            fields=fields,
            idem_key=idem_key,
            idem_val="SENT:ares-v56",
            idem_ttl=86400,
        )
        if stream_id is None:
            return {"status": "duplicate", "exec_id": exec_id}
        self.store.set_json(LiveKeys.EXECUTION_LAST, {
            "ts": time.time(),
            "exec_id": exec_id,
            "intent_id": str(intent.get("intent_id", exec_id)),
            "stream": target_stream,
            "stream_id": stream_id.decode() if isinstance(stream_id, bytes) else stream_id,
        })
        if self.cfg.execution.ack_timeout_sec <= 0:
            return {"status": "submitted", "exec_id": exec_id, "stream_id": stream_id}

        deadline = time.time() + self.cfg.execution.ack_timeout_sec
        ack_key = f"{LiveKeys.EXECUTION_ACK}:{exec_id}"
        while time.time() < deadline:
            ack = self.store.get_json(ack_key)
            if ack:
                return {"status": "acked", "exec_id": exec_id, "ack": ack, "stream_id": stream_id}
            time.sleep(0.25)
        return {"status": "submitted_no_ack", "exec_id": exec_id, "stream_id": stream_id}


# ---------------------------------------------------------------------------
# V5.6.1: Broker Consumer Guard
# ---------------------------------------------------------------------------
class BrokerConsumerGuard:
    """Checks broker consumer health before allowing order submission.
    Consumer must publish heartbeat to Redis. If stale/missing → DEFER or HALT."""

    def __init__(self, store: RedisStore, cfg: LiveAutopilotConfig):
        self.store = store
        self.cfg = cfg
        self.max_heartbeat_age_sec = 45
        self.max_no_ack_streak = 3
        self._no_ack_streak = 0

    @staticmethod
    def _is_off_hours() -> bool:
        """Check if US market is closed (weekends + outside 13:30-20:00 UTC)."""
        import datetime
        now = datetime.datetime.utcnow()
        if now.weekday() >= 5:  # Saturday=5, Sunday=6
            return True
        if now.hour < 13 or (now.hour == 13 and now.minute < 30) or now.hour >= 20:
            return True
        return False

    def is_consumer_healthy(self) -> Tuple[bool, str]:
        """Check consumer heartbeat. Returns (healthy, reason)."""
        hb_raw = self.store.r.get(LiveKeys.CONSUMER_HEARTBEAT)
        if hb_raw is None:
            # No heartbeat key at all - consumer may not be running
            # Graceful: allow if ack_timeout is 0 (no-ack mode)
            if self.cfg.execution.ack_timeout_sec <= 0:
                return True, "no-ack mode, consumer heartbeat not required"
            # V5.6.1-fix: Off-hours/weekend bypass — consumer is expected to be idle
            if self._is_off_hours():
                return True, "off-hours bypass, consumer heartbeat not required"
            return False, "consumer heartbeat key missing"
        try:
            hb_ts = float(hb_raw if isinstance(hb_raw, (int, float)) else hb_raw.decode() if isinstance(hb_raw, bytes) else hb_raw)
            age = time.time() - hb_ts
            if age > self.max_heartbeat_age_sec:
                return False, f"consumer heartbeat stale ({age:.0f}s > {self.max_heartbeat_age_sec}s)"
            return True, f"consumer alive (age={age:.0f}s)"
        except Exception as e:
            return False, f"consumer heartbeat parse error: {e}"

    def pre_submit_check(self) -> Tuple[bool, str]:
        """Full pre-submission safety check."""
        healthy, reason = self.is_consumer_healthy()
        if not healthy:
            return False, reason
        if self._no_ack_streak >= self.max_no_ack_streak:
            return False, f"no-ack streak {self._no_ack_streak} >= {self.max_no_ack_streak}"
        return True, "consumer ready"

    def record_ack_result(self, ack_status: str):
        """Track ACK results for streak detection."""
        if ack_status == "acked":
            self._no_ack_streak = 0
        elif ack_status in ("submitted_no_ack", "timeout"):
            self._no_ack_streak += 1


# ---------------------------------------------------------------------------
# V5.6.1: AutoTune Promotion Gate
# ---------------------------------------------------------------------------
class AutoTunePromotionGate:
    """Gates autotune parameter promotion to live trading.
    Requires minimum observations, accuracy, stability before allowing live override.
    Auto-rollback after consecutive bad sessions."""

    # Promotion criteria
    MIN_OBS = 60
    MIN_SCORED_1D = 40
    MIN_SESSIONS = 5
    MIN_ACCURACY = 0.58
    MAX_FLAPS_24H = 3
    MAX_DRAWDOWN_PCT = 0.03
    EQUITY_STABILITY_DAYS = 3
    BAD_SESSIONS_FOR_ROLLBACK = 3

    def __init__(self, store: RedisStore, cfg: LiveAutopilotConfig):
        self.store = store
        self.cfg = cfg

    def evaluate(self, autotune_dict: Dict[str, Any], state: Any, metrics: Dict[str, Any]) -> Tuple[bool, str]:
        """Evaluate whether autotune suggestion should be promoted to live.
        Returns (should_promote, reason)."""
        if not autotune_dict:
            return False, "no suggestion"

        # Check observation count
        hist = self.store.r.lrange(LiveKeys.ACCURACY, 0, -1)
        obs_count = len(hist) if hist else 0
        if obs_count < self.MIN_OBS:
            return False, f"insufficient observations ({obs_count} < {self.MIN_OBS})"

        # Check scored 1d predictions
        scored_1d = 0
        for raw in (hist or []):
            try:
                rec = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                if rec.get("ret_1d") is not None:
                    scored_1d += 1
            except Exception:
                pass
        if scored_1d < self.MIN_SCORED_1D:
            return False, f"insufficient 1d scores ({scored_1d} < {self.MIN_SCORED_1D})"

        # Check rolling accuracy
        roll_acc = safe_float(autotune_dict.get("rolling_accuracy"), 0.0)
        if roll_acc < self.MIN_ACCURACY:
            return False, f"accuracy too low ({roll_acc:.3f} < {self.MIN_ACCURACY})"

        # Check flap count
        flap = int(autotune_dict.get("flap_count", 99))
        if flap > self.MAX_FLAPS_24H:
            return False, f"too many flaps ({flap} > {self.MAX_FLAPS_24H})"

        # Check drawdown
        dd = safe_float(metrics.get("drawdown_pct"), 0.0)
        if dd > self.MAX_DRAWDOWN_PCT:
            return False, f"drawdown too high ({dd:.4f} > {self.MAX_DRAWDOWN_PCT})"

        # Check equity stability (session count)
        session_count = safe_float(state.stats.get("session_count"), 0)
        if session_count < self.MIN_SESSIONS:
            return False, f"insufficient sessions ({session_count} < {self.MIN_SESSIONS})"

        return True, "all criteria met"

    def record_suggestion(self, autotune_dict: Dict[str, Any]):
        """Record suggestion without applying to live."""
        suggestion = {
            "ts": time.time(),
            "params": autotune_dict,
            "status": "suggestion_only",
        }
        try:
            self.store.publish_history_point(LiveKeys.AUTOTUNE_SUGGESTIONS, suggestion, 86400, 1000)
        except Exception as e:
            if "WRONGTYPE" in str(e):
                log.warning("WRONGTYPE on %s — handled by Lua atomic script", LiveKeys.AUTOTUNE_SUGGESTIONS)
            else:
                raise
        self.store.r.ltrim(LiveKeys.AUTOTUNE_SUGGESTIONS, 0, 999)
        log.info("V5.6.1 AutoTune: suggestion recorded (not applied). acc=%.3f flap=%d",
                 safe_float(autotune_dict.get("rolling_accuracy"), 0), int(autotune_dict.get("flap_count", 0)))

    def promote_to_live(self, autotune_dict: Dict[str, Any]):
        """Apply autotune override to live (only after gate passes)."""
        override = {
            "ts": time.time(),
            "params": {k: v for k, v in autotune_dict.items() if k not in ("rolling_accuracy", "flap_count", "ts")},
            "status": "promoted",
        }
        self.store.set_json(LiveKeys.AUTOTUNE_ACTIVE_OVERRIDE, override)
        try:
            self.store.publish_history_point(LiveKeys.AUTOTUNE_OVERRIDE_HISTORY, override, 86400, 1000)
        except Exception as e:
            if "WRONGTYPE" in str(e):
                log.warning("WRONGTYPE on %s — handled by Lua atomic script", LiveKeys.AUTOTUNE_OVERRIDE_HISTORY)
            else:
                raise
        self.store.r.ltrim(LiveKeys.AUTOTUNE_OVERRIDE_HISTORY, 0, 99)
        # Also write to the original AUTOTUNE key for backward compat
        self.store.set_json(LiveKeys.AUTOTUNE, autotune_dict)
        log.warning("V5.6.1 AutoTune: PROMOTED to live. acc=%.3f", safe_float(autotune_dict.get("rolling_accuracy"), 0))

    def record_session_outcome(self, pnl_pct: float, dd_pct: float, accuracy: float):
        """Record session outcome for rollback tracking."""
        is_bad = (dd_pct > 0.02) or (accuracy < 0.45) or (pnl_pct < -0.015)
        if is_bad:
            count = int(self.store.r.get(LiveKeys.BAD_SESSION_COUNT) or 0) + 1
            self.store.r.set(LiveKeys.BAD_SESSION_COUNT, str(count))
            log.warning("V5.6.1 AutoTune: bad session #%d (pnl=%.4f, dd=%.4f, acc=%.3f)", count, pnl_pct, dd_pct, accuracy)
        else:
            self.store.r.set(LiveKeys.BAD_SESSION_COUNT, "0")

    def should_rollback(self) -> Tuple[bool, str]:
        """Check if we should rollback the last autotune override."""
        count = int(self.store.r.get(LiveKeys.BAD_SESSION_COUNT) or 0)
        if count >= self.BAD_SESSIONS_FOR_ROLLBACK:
            return True, f"{count} consecutive bad sessions >= {self.BAD_SESSIONS_FOR_ROLLBACK}"
        return False, f"bad sessions: {count}"

    def rollback(self):
        """Rollback: clear active override, reset bad session count."""
        self.store.r.delete(LiveKeys.AUTOTUNE_ACTIVE_OVERRIDE)
        self.store.r.delete(LiveKeys.AUTOTUNE)
        self.store.r.set(LiveKeys.BAD_SESSION_COUNT, "0")
        log.warning("V5.6.1 AutoTune: ROLLBACK executed - override cleared")


# ---------------------------------------------------------------------------
# V5.6.1: Incident Assist
# ---------------------------------------------------------------------------
class IncidentAssist:
    """Generates diagnostic bundles on anomaly. NEVER auto-applies patches."""

    def __init__(self, store: RedisStore, cfg: LiveAutopilotConfig):
        self.store = store
        self.cfg = cfg
        self._last_incident_ts = 0.0
        self._rate_limit_sec = 300  # max 1 incident per 5 min

    def trigger(self, anomaly_type: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate incident bundle. Returns bundle dict."""
        now = time.time()
        if now - self._last_incident_ts < self._rate_limit_sec:
            log.warning("IncidentAssist: rate-limited (last incident %.0fs ago)", now - self._last_incident_ts)
            return {"rate_limited": True}
        self._last_incident_ts = now

        bundle = self._collect_bundle(anomaly_type, context or {})
        # Save to file
        incident_dir = Path(self.cfg.audit_dir) / "incidents"
        incident_dir.mkdir(parents=True, exist_ok=True)
        ts_str = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        path = incident_dir / f"{ts_str}_{anomaly_type}.json"
        path.write_text(json.dumps(bundle, indent=2, default=str))

        # Save to Redis
        self.store.set_json(LiveKeys.INCIDENT_LAST, {"ts": now, "type": anomaly_type, "path": str(path)})
        try:
            self.store.publish_history_point(LiveKeys.INCIDENT_HISTORY, {"ts": now, "type": anomaly_type, "path": str(path)}, 86400, 1000)
        except Exception as e:
            if "WRONGTYPE" in str(e):
                log.warning("WRONGTYPE on %s — handled by Lua atomic script", LiveKeys.INCIDENT_HISTORY)
            else:
                raise
        self.store.r.ltrim(LiveKeys.INCIDENT_HISTORY, 0, 99)

        log.error("INCIDENT [%s]: bundle saved to %s", anomaly_type, path)
        return bundle

    def _collect_bundle(self, anomaly_type: str, context: Dict) -> Dict[str, Any]:
        """Collect diagnostic information."""
        bundle = {
            "ts": time.time(),
            "anomaly_type": anomaly_type,
            "context": context,
        }
        # Redis state snapshot (safe)
        try:
            bundle["redis_state"] = {
                "readiness": self.store.r.get(LiveKeys.READINESS),
                "heartbeat": self.store.r.get(LiveKeys.HEARTBEAT),
                "last_decision": self.store.get_json(LiveKeys.DECISION_LAST, {}),
                "last_metrics": self.store.get_json(LiveKeys.METRICS, {}),
                "last_execution": self.store.get_json(LiveKeys.EXECUTION_LAST, {}),
                "consumer_heartbeat": self.store.r.get(LiveKeys.CONSUMER_HEARTBEAT),
                "autotune_active": self.store.get_json(LiveKeys.AUTOTUNE_ACTIVE_OVERRIDE, {}),
            }
            # Decode bytes
            for k, v in bundle["redis_state"].items():
                if isinstance(v, bytes):
                    bundle["redis_state"][k] = v.decode()
        except Exception as e:
            bundle["redis_state_error"] = str(e)

        # GPT analysis prompt
        bundle["gpt_prompt"] = self._gpt_prompt(anomaly_type, context, bundle.get("redis_state", {}))
        return bundle

    def _gpt_prompt(self, anomaly_type: str, context: Dict, redis_state: Dict) -> str:
        """Generate structured prompt for GPT analysis (read-only, never auto-apply)."""
        return f"""ARES V5.6.1 Incident Analysis Request
===========================================
Anomaly Type: {anomaly_type}
Timestamp: {datetime.now(tz=UTC).isoformat()}

Context:
{json.dumps(context, indent=2, default=str)}

Redis State Snapshot:
{json.dumps(redis_state, indent=2, default=str)}

Please analyze:
1. Root cause of the anomaly
2. Impact assessment (positions, equity, risk)
3. Recommended remediation steps
4. Code patch suggestion (if applicable)

IMPORTANT: Do NOT auto-apply any patches. All changes require human review."""


# ---------------------------------------------------------------------------
# Feedback loop (real prices, bounded autotune)
# ---------------------------------------------------------------------------
@dataclass
class PredictionRecord:
    ts: float
    regime: str
    confidence: float
    spy_at: float
    vix_at: float
    selected: List[str]
    stable_score: float
    scored: bool = False
    score: Optional[float] = None
    ret_1h: Optional[float] = None
    ret_4h: Optional[float] = None
    ret_1d: Optional[float] = None


class LiveFeedbackLoop:
    def __init__(self, store: RedisStore, cfg: LiveAutopilotConfig):
        self.store = store
        self.cfg = cfg

    def record_market_point(self, spy: float, vix: float) -> None:
        now = time.time()
        self.store.publish_history_point(LiveKeys.MARKET_SPY, {"ts": now, "price": spy}, self.cfg.price_history_ttl_sec, 5000)
        self.store.publish_history_point(LiveKeys.MARKET_VIX, {"ts": now, "price": vix}, self.cfg.price_history_ttl_sec, 5000)

    def record_prediction(self, regime: str, confidence: float, spy: float, vix: float, selected: List[str], stable_score: float) -> None:
        rec = PredictionRecord(
            ts=time.time(), regime=regime, confidence=confidence, spy_at=spy,
            vix_at=vix, selected=selected[:], stable_score=stable_score,
        )
        self.store.publish_history_point(LiveKeys.PREDICTIONS, asdict(rec), self.cfg.price_history_ttl_sec, self.cfg.prediction_max_records)

    def _history(self, key: str, limit: int = 4000) -> List[Dict[str, Any]]:
        rows = self.store.r.lrange(key, 0, limit - 1)
        out = []
        for raw in rows:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                out.append(json.loads(raw))
            except Exception:
                continue
        return out

    def _nearest_future_price(self, target_ts: float) -> Optional[float]:
        hist = list(reversed(self._history(LiveKeys.MARKET_SPY, 5000)))
        for row in hist:
            if safe_float(row.get("ts"), 0.0) >= target_ts and safe_float(row.get("price"), 0.0) > 0:
                return safe_float(row.get("price"), 0.0)
        return None

    def score_due_predictions(self) -> int:
        preds = self._history(LiveKeys.PREDICTIONS, self.cfg.prediction_max_records)
        if not preds:
            return 0
        now = time.time()
        scored = 0
        new_list = []
        for pred in preds:
            if pred.get("scored"):
                new_list.append(pred)
                continue
            ts = safe_float(pred.get("ts"), 0.0)
            if now - ts < 86400:
                new_list.append(pred)
                continue
            spy0 = safe_float(pred.get("spy_at"), 0.0)
            if spy0 <= 0:
                pred["scored"] = True
                pred["score"] = 0.0
                new_list.append(pred)
                continue
            px1 = self._nearest_future_price(ts + 3600)
            px4 = self._nearest_future_price(ts + 4 * 3600)
            px1d = self._nearest_future_price(ts + 86400)
            if not px1d:
                new_list.append(pred)
                continue
            ret_1h = (px1 - spy0) / spy0 if px1 else 0.0
            ret_4h = (px4 - spy0) / spy0 if px4 else 0.0
            ret_1d = (px1d - spy0) / spy0
            score = self._score_prediction(pred.get("regime", "CAUTION"), ret_1h, ret_4h, ret_1d)
            pred.update({
                "ret_1h": ret_1h, "ret_4h": ret_4h, "ret_1d": ret_1d,
                "score": score, "scored": True,
            })
            self.store.publish_history_point(LiveKeys.ACCURACY, {"ts": now, "score": score}, self.cfg.price_history_ttl_sec, 2000)
            new_list.append(pred)
            scored += 1
        # Only rewrite if any predictions were actually scored this cycle
        if scored > 0:
            pipe = self.store.r.pipeline()
            pipe.delete(LiveKeys.PREDICTIONS)
            for row in new_list:
                raw = json.dumps(row, separators=(",", ":"), default=str)
                pipe.lpush(LiveKeys.PREDICTIONS, raw)
            pipe.ltrim(LiveKeys.PREDICTIONS, 0, self.cfg.prediction_max_records - 1)
            pipe.expire(LiveKeys.PREDICTIONS, self.cfg.price_history_ttl_sec)
            pipe.execute()
        if scored:
            self.update_rolling_accuracy()
        return scored

    @staticmethod
    def _score_prediction(regime: str, ret_1h: float, ret_4h: float, ret_1d: float) -> float:
        regime = regime.upper()
        direction = "neutral"
        if regime == "BULL":
            direction = "positive"
        elif regime in {"BEAR", "CRISIS"}:
            direction = "negative"
        weights = {"1h": 0.25, "4h": 0.35, "1d": 0.40}
        total = 0.0
        for horizon, ret in (("1h", ret_1h), ("4h", ret_4h), ("1d", ret_1d)):
            w = weights[horizon]
            if direction == "positive":
                total += w * (1.0 if ret > 0.001 else 0.3 if ret > -0.001 else -0.5)
            elif direction == "negative":
                total += w * (1.0 if ret < -0.001 else 0.3 if ret < 0.001 else -0.5)
            else:
                total += w * (0.8 if abs(ret) < 0.002 else 0.3 if abs(ret) < 0.005 else -0.3)
        return clamp(total, 0.0, 1.0)

    def update_rolling_accuracy(self) -> float:
        hist = self._history(LiveKeys.ACCURACY, 200)
        vals = [safe_float(x.get("score"), 0.0) for x in hist if x.get("score") is not None]
        avg = float(np.mean(vals[-100:])) if vals else 0.0
        self.store.r.set(LiveKeys.ROLLING_ACCURACY, str(round(avg, 4)))
        return avg

    def get_flap_count(self) -> int:
        preds = list(reversed(self._history(LiveKeys.PREDICTIONS, 500)))
        cutoff = time.time() - 86400
        prev = None
        flaps = 0
        for p in preds:
            if safe_float(p.get("ts"), 0.0) < cutoff:
                continue
            reg = str(p.get("regime", "UNKNOWN"))
            if prev is not None and reg != prev:
                flaps += 1
            prev = reg
        self.store.r.set(LiveKeys.FLAP_COUNT, str(flaps))
        return flaps

    def bounded_autotune(self, alpha_params: Dict[str, Any], state: RuntimeState, metrics: Dict[str, Any]) -> Dict[str, Any]:
        if not self.cfg.autotune_enabled:
            return {}
        if time.time() - state.last_autotune_ts < self.cfg.autotune_cooldown_sec:
            return {}
        hist = self._history(LiveKeys.ACCURACY, 200)
        scores = [safe_float(x.get("score"), 0.0) for x in hist if x.get("score") is not None]
        if len(scores) < self.cfg.autotune_min_obs:
            return {}
        roll_acc = float(np.mean(scores[-self.cfg.autotune_min_obs:]))
        flap = self.get_flap_count()
        avg_actual_bps = safe_float(metrics.get("avg_actual_cost_bps"), 0.0)
        avg_shadow_bps = safe_float(metrics.get("avg_shadow_cost_bps"), 0.0)
        turnover = safe_float(metrics.get("turnover_today"), 0.0)
        out: Dict[str, Any] = {}
        b = self.cfg.autotune_bounds

        # Conservative reaction: low accuracy, high flapping, or execution friction worse than expected.
        if roll_acc < 0.58 or flap >= 6 or avg_shadow_bps > (avg_actual_bps + 4.0):
            out["ntb_base"] = clamp(safe_float(alpha_params.get("ntb_base"), 0.012) * 1.08, *b.ntb_base)
            out["to_base"] = clamp(safe_float(alpha_params.get("to_base"), 0.22) * 0.92, *b.to_base)
            out["to_max"] = clamp(safe_float(alpha_params.get("to_max"), 0.40) * 0.95, *b.to_max)
            out["a2b2_base"] = clamp(safe_float(alpha_params.get("a2b2_base"), 0.50) * 1.05, *b.a2b2_base)
            out["a2b2_to"] = clamp(safe_float(alpha_params.get("a2b2_to"), 0.07) * 0.95, *b.a2b2_to)
            out["crash_vix"] = clamp(safe_float(alpha_params.get("crash_vix"), 35.0) - 1.0, *b.crash_vix)
            out["drift_mult"] = clamp(1.10, *b.drift_mult)
            out["cooldown_mult"] = clamp(1.15, *b.cooldown_mult)
        elif roll_acc > 0.82 and flap <= 2 and turnover < 0.20:
            out["ntb_base"] = clamp(safe_float(alpha_params.get("ntb_base"), 0.012) * 0.97, *b.ntb_base)
            out["to_base"] = clamp(safe_float(alpha_params.get("to_base"), 0.22) * 1.03, *b.to_base)
            out["to_max"] = clamp(safe_float(alpha_params.get("to_max"), 0.40) * 1.02, *b.to_max)
            out["a2b2_base"] = clamp(safe_float(alpha_params.get("a2b2_base"), 0.50) * 0.98, *b.a2b2_base)
            out["a2b2_to"] = clamp(safe_float(alpha_params.get("a2b2_to"), 0.07) * 1.02, *b.a2b2_to)
            out["crash_vix"] = clamp(safe_float(alpha_params.get("crash_vix"), 35.0) + 1.0, *b.crash_vix)
            out["drift_mult"] = clamp(0.95, *b.drift_mult)
            out["cooldown_mult"] = clamp(0.95, *b.cooldown_mult)

        if out:
            out["rolling_accuracy"] = roll_acc
            out["flap_count"] = flap
            out["ts"] = time.time()
            self.store.set_json(LiveKeys.AUTOTUNE, out)
            self.store.publish_history_point(LiveKeys.AUTOTUNE_HISTORY, out, self.cfg.price_history_ttl_sec, 1000)
            state.last_autotune_ts = time.time()
        return out


# ---------------------------------------------------------------------------
# Enhanced kernel governor
# ---------------------------------------------------------------------------
class Decision:
    EXECUTE: str = "EXECUTE"
    CONSERVATIVE_ONLY: str = "CONSERVATIVE_ONLY"
    DE_RISK_ONLY: str = "DE_RISK_ONLY"
    DEFER: str = "DEFER"
    FLATTEN: str = "FLATTEN"
    HALT: str = "HALT"


@dataclass
class DecisionResult:
    decision: str
    regime: str
    urgency: float
    reason: str
    params: Dict[str, Any] = field(default_factory=dict)


class AggressiveKernelGovernor:
    REGIME_PARAMS = {
        "BULL": {"drift": 0.055, "cooldown_min": 90, "max_turn": 0.28, "exposure": 0.95, "urgency": 0.35},
        "STRONG_BULL": {"drift": 0.060, "cooldown_min": 90, "max_turn": 0.30, "exposure": 0.98, "urgency": 0.30},
        "NORMAL": {"drift": 0.045, "cooldown_min": 120, "max_turn": 0.24, "exposure": 0.85, "urgency": 0.45},
        "BEAR": {"drift": 0.030, "cooldown_min": 60, "max_turn": 0.16, "exposure": 0.55, "urgency": 0.70},
        "CRASH": {"drift": 0.020, "cooldown_min": 30, "max_turn": 0.12, "exposure": 0.35, "urgency": 0.95},
        "INF_SHOCK": {"drift": 0.025, "cooldown_min": 60, "max_turn": 0.14, "exposure": 0.40, "urgency": 0.85},
        "CAUTION": {"drift": 0.035, "cooldown_min": 180, "max_turn": 0.16, "exposure": 0.65, "urgency": 0.55},
    }

    def __init__(self, store: RedisStore, cfg: LiveAutopilotConfig):
        self.store = store
        self.cfg = cfg

    def evaluate(
        self,
        regime: str,
        confidence: float,
        market: Dict[str, Any],
        portfolio: Dict[str, Any],
        target: np.ndarray,
        actual: np.ndarray,
        meta_override: Optional[Dict[str, Any]] = None,
    ) -> DecisionResult:
        rg = str(regime or "CAUTION").upper()
        base = dict(self.REGIME_PARAMS.get(rg, self.REGIME_PARAMS["CAUTION"]))
        if meta_override:
            drift_mult = safe_float(meta_override.get("drift_mult"), 1.0)
            cooldown_mult = safe_float(meta_override.get("cooldown_mult"), 1.0)
            base["drift"] = clamp(base["drift"] * drift_mult, 0.01, 0.10)
            base["cooldown_min"] = int(clamp(base["cooldown_min"] * cooldown_mult, 15, 720))
            base["max_turn"] = clamp(base["max_turn"], 0.05, self.cfg.execution.max_live_turnover_pct)

        reason_parts = []
        vix = safe_float(market.get("vix"), 20.0)
        quote_stale = bool(market.get("quotes_stale"))
        data_lag_hours = safe_float(market.get("data_lag_hours"), 0.0)
        daily_pnl = safe_float(portfolio.get("daily_pnl_pct"), 0.0)
        drawdown = safe_float(portfolio.get("drawdown_pct"), 0.0)
        leverage = safe_float(portfolio.get("leverage"), 0.0)
        open_orders = int(portfolio.get("open_orders", 0))
        gross_exp = safe_float(portfolio.get("gross_exposure"), 0.0)
        drift = safe_float(portfolio.get("max_weight_drift"), 0.0)
        turnover_today = safe_float(portfolio.get("turnover_today"), 0.0)
        flap_count = int(market.get("flap_count", 0))

        if data_lag_hours > self.cfg.kernel.max_data_lag_hours:
            return DecisionResult(Decision.HALT, rg, 1.0, f"DB data lag {data_lag_hours:.1f}h exceeds limit")
        if quote_stale and self.cfg.kernel.force_flatten_on_stale_quotes:
            return DecisionResult(Decision.FLATTEN, rg, 0.95, "Critical quote staleness")
        if leverage > self.cfg.kernel.max_leverage or gross_exp > self.cfg.execution.max_published_exposure + 0.01:
            return DecisionResult(Decision.FLATTEN, rg, 1.0, f"Exposure/leverage breach lev={leverage:.2f} gross={gross_exp:.2f}")
        if daily_pnl <= -self.cfg.kernel.emergency_daily_loss_pct or drawdown >= self.cfg.kernel.emergency_drawdown_pct:
            return DecisionResult(Decision.HALT, rg, 1.0, f"Emergency loss guard pnl={daily_pnl:.2%} dd={drawdown:.2%}")
        if open_orders > self.cfg.kernel.max_open_orders:
            return DecisionResult(Decision.DEFER, rg, 0.2, f"Open orders outstanding={open_orders}")
        if rg in {"BEAR", "CRASH", "INF_SHOCK"} and (vix > 32.0 or drawdown >= self.cfg.kernel.derisk_drawdown_pct or daily_pnl <= -self.cfg.kernel.derisk_daily_loss_pct):
            return DecisionResult(Decision.DE_RISK_ONLY, rg, 0.95, f"Risk-off regime {rg} with stress")
        if confidence < self.cfg.kernel.min_confidence_execute:
            return DecisionResult(Decision.CONSERVATIVE_ONLY, rg, 0.55, f"Low confidence {confidence:.2f}")
        if flap_count >= 8:
            return DecisionResult(Decision.CONSERVATIVE_ONLY, rg, 0.60, f"High regime flapping {flap_count}")

        urgency = base["urgency"]
        if drift > 0:
            urgency += min(0.35, drift / max(base["drift"], 1e-6) * 0.20)
        if turnover_today > base["max_turn"] * 0.8:
            urgency -= 0.10
        if vix > 25.0:
            urgency += 0.05
        urgency = clamp(urgency, 0.0, 1.0)

        if drift >= base["drift"] or urgency >= 0.72:
            if turnover_today >= self.cfg.execution.max_live_turnover_pct:
                return DecisionResult(Decision.DEFER, rg, urgency, f"Daily turnover exhausted {turnover_today:.2%}")
            return DecisionResult(
                Decision.EXECUTE,
                rg,
                urgency,
                f"Execute drift={drift:.3f} urgency={urgency:.2f}",
                params={
                    "max_turnover": min(base["max_turn"], self.cfg.execution.max_live_turnover_pct),
                    "cooldown_min": base["cooldown_min"],
                    "exposure_target": min(base["exposure"], self.cfg.execution.max_published_exposure),
                    "allow_new_longs": True,
                    "allow_new_shorts": False,
                },
            )
        return DecisionResult(Decision.DEFER, rg, urgency, f"Below threshold drift={drift:.3f}")


# ---------------------------------------------------------------------------
# Live engine wrapper
# ---------------------------------------------------------------------------
class LiveEngine:
    def __init__(self, engine_mod, alpha_params: Dict[str, Any]):
        self.eng = engine_mod
        # Merge engine DEFAULT_PARAMS as base, then overlay alpha_params
        base = dict(getattr(engine_mod, 'DEFAULT_PARAMS', {}))
        base.update(alpha_params)
        self.alpha_params = base

    def invalidate_cache(self, db_path: str):
        cache_key = f"full::{db_path}"
        try:
            self.eng._FULL_CACHE.pop(cache_key, None)
        except Exception:
            pass

    def load_db(self, db_path: str, min_match: int):
        self.invalidate_cache(db_path)
        return self.eng.load_full_db(db_path, min_match=min_match)

    def init_state(self, full_data, params: Dict[str, Any]) -> RuntimeState:
        n = full_data["returns"].shape[1]
        state = RuntimeState()
        state.book_a = EngineBookPersist(pw=[0.0] * n, prev_reg=2)
        state.book_b = EngineBookPersist(pw=[0.0] * n, prev_reg=2)
        for uname in params["candidate_universes"]:
            state.shadows[uname] = EngineShadowPersist(
                pw=[0.0] * n,
                prev_reg=2,
                confidence=0.5,
                sig_hist=[],
                ret_hist=[],
                daily_returns=[],
                last_selected=[],
                last_target=None,
            )
        return state

    def _to_runtime_shadow(self, persist: EngineShadowPersist):
        st = self.eng.StrategyState(pw=np.array(persist.pw, dtype=np.float64))
        st.prev_reg = persist.prev_reg
        st.confidence = persist.confidence
        st.sig_hist.extend(persist.sig_hist)
        st.ret_hist.extend(persist.ret_hist)
        st.daily_returns = list(persist.daily_returns)
        st.last_selected = list(persist.last_selected)
        st.last_target = None if persist.last_target is None else np.array(persist.last_target, dtype=np.float64)
        return st

    def _to_runtime_book(self, persist: EngineBookPersist):
        bk = self.eng.BookState(pw=np.array(persist.pw, dtype=np.float64))
        bk.prev_reg = persist.prev_reg
        return bk

    @staticmethod
    def _safe_float_list(arr) -> list:
        """Safely convert a list that may contain numpy arrays to flat float list."""
        out = []
        for x in arr:
            try:
                if hasattr(x, 'tolist'):
                    val = x.tolist()
                    if isinstance(val, list):
                        out.extend(float(v) for v in val)
                    else:
                        out.append(float(val))
                else:
                    out.append(float(x))
            except (TypeError, ValueError):
                continue
        return out

    def _from_runtime_shadow(self, st) -> EngineShadowPersist:
        return EngineShadowPersist(
            pw=list(map(float, st.pw.tolist())),
            prev_reg=int(st.prev_reg),
            confidence=float(st.confidence),
            sig_hist=self._safe_float_list(st.sig_hist),
            ret_hist=self._safe_float_list(st.ret_hist),
            daily_returns=self._safe_float_list(st.daily_returns),
            last_selected=list(st.last_selected),
            last_target=None if st.last_target is None else list(map(float, st.last_target.tolist())),
        )

    def _from_runtime_book(self, bk) -> EngineBookPersist:
        return EngineBookPersist(pw=list(map(float, bk.pw.tolist())), prev_reg=int(bk.prev_reg))

    def replay_to_latest(self, full_data, state: RuntimeState, params: Dict[str, Any]) -> Tuple[RuntimeState, Dict[str, Any]]:
        dates = full_data["dates"]
        n = full_data["returns"].shape[1]
        if state.book_a is None or state.book_b is None or not state.shadows:
            state = self.init_state(full_data, params)
        shadow_states = {k: self._to_runtime_shadow(v) for k, v in state.shadows.items()}
        book_a = self._to_runtime_book(state.book_a)
        book_b = self._to_runtime_book(state.book_b)
        start_idx = max(state.last_engine_index + 1, 252)
        if start_idx < 252:
            start_idx = 252

        selected = state.last_selected[:] if state.last_selected else params.get("fallback_universes", params["candidate_universes"][:3])[:3]
        avg_conf = float(state.last_confidence or 0.5)
        composite = np.zeros(n, dtype=np.float64)
        turnover = actual_cost = shadow_cost = 0.0
        reg = int(book_a.prev_reg if book_a is not None else 2)
        latest_target = 0.5 * (book_a.pw + book_b.pw)

        if start_idx >= len(full_data["returns"]):
            return state, {
                "target": latest_target,
                "selected": list(selected),
                "confidence": float(avg_conf),
                "regime_code": int(reg),
                "regime_name": self.eng.RN.get(int(reg), str(reg)),
                "actual_cost": 0.0,
                "shadow_cost": 0.0,
                "turnover": 0.0,
                "book_a": np.array(book_a.pw, dtype=np.float64),
                "book_b": np.array(book_b.pw, dtype=np.float64),
            }

        for t in range(start_idx, len(full_data["returns"])):
            reg = self.eng._regime(full_data["returns"], t, full_data.get("vix"))
            vx = float(full_data.get("vix")[t]) if full_data.get("vix") is not None else 20.0
            if (t - 252) % params["signal_rebal"] == 0:
                for uname in params["candidate_universes"]:
                    self.eng.step_shadow_strategy(full_data, t, shadow_states[uname], params, uname)
            selected = self.eng.select_top3_universes(shadow_states, params)
            composite, avg_conf = self.eng.blend_selected_targets(shadow_states, selected, n)
            composite = self.eng._enforce_exposure_floor_cap(composite, reg, params)
            if (t - 252) % params["execution_stagger"] == 0:
                _, turnover, actual_cost, shadow_cost, _ = self.eng.execute_book(book_a, composite, reg, vx, avg_conf, params)
            else:
                _, turnover, actual_cost, shadow_cost, _ = self.eng.execute_book(book_b, composite, reg, vx, avg_conf, params)
            latest_target = 0.5 * (book_a.pw + book_b.pw)
            state.last_engine_index = t
            state.last_engine_date = str(pd_timestamp_to_iso(dates[t]))
            state.last_selected = list(selected)
            state.last_confidence = float(avg_conf)

        state.book_a = self._from_runtime_book(book_a)
        state.book_b = self._from_runtime_book(book_b)
        state.shadows = {k: self._from_runtime_shadow(v) for k, v in shadow_states.items()}
        snapshot = {
            "target": latest_target,
            "selected": list(selected),
            "confidence": float(avg_conf),
            "regime_code": int(reg),
            "regime_name": self.eng.RN.get(int(reg), str(reg)),
            "actual_cost": float(actual_cost),
            "shadow_cost": float(shadow_cost),
            "turnover": float(turnover),
            "book_a": np.array(book_a.pw, dtype=np.float64),
            "book_b": np.array(book_b.pw, dtype=np.float64),
        }
        return state, snapshot


def pd_timestamp_to_iso(x: Any) -> str:
    try:
        import pandas as pd  # local import to avoid hard dependency outside engine
        if isinstance(x, pd.Timestamp):
            if x.tzinfo is None:
                x = x.tz_localize("UTC")
            return x.tz_convert("UTC").isoformat()
    except Exception:
        pass
    dt = parse_date_str(x)
    return (dt or utcnow()).isoformat()


# ---------------------------------------------------------------------------
# Live autopilot service
# ---------------------------------------------------------------------------
class LiveAutopilotService:
    def __init__(self, cfg: LiveAutopilotConfig):
        self.cfg = cfg
        ensure_dir(cfg.audit_dir)
        self.store = RedisStore(cfg.execution.redis_url)
        self.reader = MarketAccountReader(self.store, cfg)
        self.bus = RedisExecutionBus(self.store, cfg)
        self.feedback = LiveFeedbackLoop(self.store, cfg)
        self.governor = AggressiveKernelGovernor(self.store, cfg)
        # V5.6.1 Hardening components
        self.consumer_guard = BrokerConsumerGuard(self.store, cfg)
        self.autotune_gate = AutoTunePromotionGate(self.store, cfg)
        self.incident = IncidentAssist(self.store, cfg)
        bootstrap_consumer_ok, bootstrap_consumer_reason = self.consumer_guard.pre_submit_check()
        if self.cfg.execution.ack_timeout_sec > 0 and not bootstrap_consumer_ok:
            raise RuntimeError(f"Broker consumer not ready at startup: {bootstrap_consumer_reason}")
        # V5.6.1b: Leader Lease - prevent split-brain with compare-and-renew ownership.
        self._leader_lease_ttl = cfg.execution.leader_lease_ttl_sec
        self._leader_lease_owner = json.dumps({
            "lease_id": str(uuid.uuid4()),
            "run_id": self.cfg.execution.run_id,
            "pid": os.getpid(),
            "started_at": time.time(),
        }, separators=(",", ":"))
        if not self._acquire_leader_lease():
            log.warning("V5.6.1c: Initial lease acquisition failed. Retrying in 5s...")
            time.sleep(5)
            if not self._acquire_leader_lease():
                # V5.7 SEV-1 런북: blocked mode — exit 대신 대기 후 재시도
                log.warning("V5.6.1c: Leader lease blocked — entering wait mode (30s retry)")
                for attempt in range(6):  # 최대 3분 대기
                    time.sleep(30)
                    if self._acquire_leader_lease():
                        log.info("V5.6.1c: Leader lease acquired after %d retries", attempt + 1)
                        break
                else:
                    # [STRUCTURAL_PATCH 2026-04-11] RuntimeError 대신 graceful exit
                    log.error("V5.6.1c: Failed to acquire leader lease after 6 retries (3min). Exiting gracefully.")
                    raise SystemExit(1)  # PM2 graceful restart  # PM2가 재시작하되, RuntimeError traceback 없이
        log.info("V5.6.1c: Leader lease acquired (TTL=%ds)", self._leader_lease_ttl)
        self.engine_mod = load_engine_module(cfg.execution.engine_path)
        self.alpha_params = json.loads(Path(cfg.execution.alpha_config_path).read_text())
        self.alpha_params["cost_bps"] = cfg.actual_cost_bps
        self.alpha_params["shadow_slippage_bps"] = cfg.shadow_slippage_bps
        self.alpha_params["shadow_impact_coeff_bps"] = cfg.shadow_impact_coeff_bps
        for key in ("ace_base", "ace_max", "ace_floor"):
            if key in self.alpha_params and self.alpha_params[key]:
                self.alpha_params[key] = {int(k): v for k, v in self.alpha_params[key].items()}
        self.engine = LiveEngine(self.engine_mod, self.alpha_params)
        # Use engine's merged params (includes DEFAULT_PARAMS) as the canonical source
        self.alpha_params = self.engine.alpha_params
        self.state: RuntimeState = pickle_load(cfg.execution.state_path) or RuntimeState()
        self.running = True
        self.last_db_refresh = 0.0
        self.full_data = None
        self.last_data_date = None
        self.install_signal_handlers()

    def _halt_with_incident(self, reason: str, context: Optional[Dict] = None):
        """V5.6.1: HALT + generate incident bundle. NEVER auto-apply patches."""
        log.error("V5.6.1 HALT: %s", reason)
        self.state.halted = True
        self.state.halt_reason = reason
        try:
            self.incident.trigger(reason, context or {})
        except Exception as e:
            log.error("IncidentAssist.trigger failed: %s", e)
        self.set_readiness(False, f"HALTED: {reason}")
        self.save_state()

    def _acquire_leader_lease(self) -> bool:
        """Acquire leader lease with stable owner token.
        V5.6.1c: If NX fails, check if the existing owner PID is still alive.
        If the PID is dead (stale lease from a crashed/restarted process),
        forcibly take over to prevent restart-loop deadlock."""
        try:
            acquired = self.store.r.set(
                LiveKeys.LEADER_LEASE,
                self._leader_lease_owner,
                nx=True,
                ex=self._leader_lease_ttl,
            )
            if acquired:
                return True
            # NX failed — another lease exists. Check if the owner is still alive.
            existing = self.store.r.get(LiveKeys.LEADER_LEASE)
            if existing:
                try:
                    existing_str = existing.decode() if isinstance(existing, bytes) else str(existing)
                    existing_data = json.loads(existing_str)
                    existing_pid = existing_data.get("pid")
                    if existing_pid:
                        try:
                            os.kill(int(existing_pid), 0)  # signal 0 = check if alive
                            log.warning("V5.6.1c: Leader lease held by live PID %s. Cannot take over.", existing_pid)
                            return False
                        except (ProcessLookupError, OSError):
                            log.warning("V5.6.1c: Stale leader lease from dead PID %s. Taking over.", existing_pid)
                            self.store.r.set(
                                LiveKeys.LEADER_LEASE,
                                self._leader_lease_owner,
                                ex=self._leader_lease_ttl,
                            )
                            return True
                except (json.JSONDecodeError, KeyError, TypeError) as parse_err:
                    log.warning("V5.6.1c: Unparseable lease data: %s. Forcing takeover.", parse_err)
                    self.store.r.set(
                        LiveKeys.LEADER_LEASE,
                        self._leader_lease_owner,
                        ex=self._leader_lease_ttl,
                    )
                    return True
            return False
        except Exception as e:
            log.error("Leader lease acquisition failed: %s", e)
            return False

    # [STRUCTURAL_PATCH 2026-04-11] Leader Lease 견고화
    _leader_lease_consecutive_failures = 0
    _LEADER_LEASE_MAX_FAILURES = 5
    _LEADER_LEASE_FALLBACK_FILE = "/tmp/ares_leader_lease_fallback.lock"

    def _renew_leader_lease(self) -> bool:
        """Renew leader lease only if this process still owns it.
        [STRUCTURAL_PATCH] Redis 실패 시 halt 대신 graceful retry + 로컬 fallback."""
        try:
            renewed = self.store.r.eval(
                """
                local current = redis.call('GET', KEYS[1])
                if not current then return 0 end
                if current == ARGV[1] then
                    redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
                    return 1
                end
                return 0
                """,
                1,
                LiveKeys.LEADER_LEASE,
                self._leader_lease_owner,
                str(self._leader_lease_ttl),
            )
            if int(renewed or 0) != 1:
                # [STRUCTURAL_PATCH] lease가 없으면 재획득 시도 (다른 PID가 죽었을 수 있음)
                log.warning("[STRUCTURAL_PATCH] Leader lease not renewed — attempting re-acquisition")
                if self._acquire_leader_lease():
                    log.info("[STRUCTURAL_PATCH] Leader lease re-acquired successfully")
                    self._leader_lease_consecutive_failures = 0
                    return True
                log.error("Leader lease lost and re-acquisition failed")
                self._leader_lease_consecutive_failures += 1
                if self._leader_lease_consecutive_failures >= self._LEADER_LEASE_MAX_FAILURES:
                    log.critical("Leader lease lost %d consecutive times — entering halt",
                                 self._leader_lease_consecutive_failures)
                    self.state.halted = True
                    self.state.halt_reason = "leader_lease_lost_consecutive"
                    self.set_readiness(False, "HALTED: leader lease lost")
                    self.running = False
                    return False
                return False  # skip this cycle but don't halt
            self._leader_lease_consecutive_failures = 0
            return True
        except Exception as e:
            self._leader_lease_consecutive_failures += 1
            err_str = str(e)
            log.error("[STRUCTURAL_PATCH] Leader lease renewal failed (attempt %d/%d): %s",
                      self._leader_lease_consecutive_failures, self._LEADER_LEASE_MAX_FAILURES, err_str)
            
            # [STRUCTURAL_PATCH] Redis 일시적 장애 → 로컬 파일 fallback
            if any(kw in err_str.lower() for kw in ["connection", "timeout", "readonly", "reset"]):
                log.warning("[STRUCTURAL_PATCH] Redis transient error — using local fallback, NOT halting")
                try:
                    import pathlib
                    pathlib.Path(self._LEADER_LEASE_FALLBACK_FILE).write_text(
                        self._leader_lease_owner)
                except Exception:
                    pass
                if self._leader_lease_consecutive_failures < self._LEADER_LEASE_MAX_FAILURES:
                    return True  # continue running with local fallback
            
            # 연속 실패 상한 도달 시에만 halt
            if self._leader_lease_consecutive_failures >= self._LEADER_LEASE_MAX_FAILURES:
                log.critical("Leader lease failed %d consecutive times — entering halt",
                             self._leader_lease_consecutive_failures)
                self.state.halted = True
                self.state.halt_reason = f"leader_lease_error_consecutive: {e}"
                self.running = False
                return False
            return False  # skip this cycle but don't halt

    def _release_leader_lease(self):
        """Release leader lease only if this process still owns it."""
        try:
            released = self.store.r.eval(
                """
                local current = redis.call('GET', KEYS[1])
                if current == ARGV[1] then
                    return redis.call('DEL', KEYS[1])
                end
                return 0
                """,
                1,
                LiveKeys.LEADER_LEASE,
                self._leader_lease_owner,
            )
            if int(released or 0) == 1:
                log.info("V5.6.1b: Leader lease released")
        except Exception:
            pass

    def _check_kill_switch(self) -> Tuple[bool, str]:
        """Check canonical + compatibility kill-switch keys."""
        try:
            enabled_raw = self.store.r.get(LiveKeys.TRADING_ENABLED)
            if enabled_raw is not None:
                val = enabled_raw.decode() if isinstance(enabled_raw, bytes) else str(enabled_raw)
                if val.lower() in ("false", "0", "no"):
                    reason_raw = self.store.r.get("trading:disable_reason")
                    reason = (reason_raw.decode() if isinstance(reason_raw, bytes) else str(reason_raw)) if reason_raw else "kill-switch disabled"
                    return False, f"trading:enabled=false ({reason})"
            for halt_key in (LiveKeys.TRADE_HALT_CANONICAL, LiveKeys.TRADE_HALT_COMPAT, LiveKeys.TRADE_HALT):
                halt_raw = self.store.r.get(halt_key)
                if halt_raw is None:
                    continue
                val = halt_raw.decode() if isinstance(halt_raw, bytes) else str(halt_raw)
                if val.lower() in ("true", "1", "yes", "on"):
                    return False, f"{halt_key}=true"
        except Exception as e:
            log.warning("Kill switch check error: %s", e)
        return True, "ok"

    def install_signal_handlers(self):
        def _stop(signum, frame):
            log.warning("Signal %s received, stopping", signum)
            self.running = False
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

    def save_state(self):
        atomic_pickle_dump(self.state, self.cfg.execution.state_path)
        # [V5.6.1b-PATCH] engine_state 필수 필드 추가
        # HALT > last decision > INIT 우선순위로 결정
        _engine_state = "INIT"
        if self.state.halted:
            _engine_state = "HALT"
        elif hasattr(self.state, "engine_state") and self.state.engine_state:
            _engine_state = self.state.engine_state
        self.store.set_json(LiveKeys.STATE_JSON, {
            "last_engine_date": self.state.last_engine_date,
            "engine_state": _engine_state,
            "halted": self.state.halted,
            "halt_reason": self.state.halt_reason,
            "last_selected": self.state.last_selected,
            "last_confidence": self.state.last_confidence,
            "last_exec_id": self.state.last_exec_id,
            "turnover_today": self.state.turnover_today,
            "ts": time.time(),
        })

    def heartbeat(self):
        self.store.r.setex(LiveKeys.HEARTBEAT, self.cfg.execution.heartbeat_ttl_sec, str(time.time()))

    def set_readiness(self, ready: bool, reason: str):
        self.store.set_json(LiveKeys.READINESS, {"ready": ready, "reason": reason, "ts": time.time()}, ex=self.cfg.execution.heartbeat_ttl_sec)

    def load_or_refresh_data(self, force: bool = False):
        if self.full_data is None or force or (time.time() - self.last_db_refresh >= self.cfg.execution.db_refresh_sec):
            self.full_data = self.engine.load_db(self.cfg.execution.db_path, self.cfg.execution.min_match)
            self.last_db_refresh = time.time()
            dt = self.full_data["dates"][-1]
            self.last_data_date = parse_date_str(dt) or utcnow()
        return self.full_data

    def current_market_snapshot(self) -> Dict[str, Any]:
        vix = self.reader.get_vix()
        spy = self.reader.get_spy()
        self.feedback.record_market_point(spy=spy, vix=vix)
        flap = self.feedback.get_flap_count()
        # Check SPY/VIX feed freshness via actual Redis key age
        stale_quotes = False
        if self.cfg.kernel.require_quote_freshness:
            for sym in ["SPY"]:
                _, age = self.reader.get_quote(sym)
                if age is not None and age > self.cfg.kernel.max_quote_stale_sec:
                    stale_quotes = True
                    break
        if spy <= 0.0 and vix <= 0.0:
            stale_quotes = True
        return {
            "vix": vix,
            "spy": spy,
            "quotes_stale": stale_quotes,
            "flap_count": flap,
            "data_lag_hours": max(0.0, (utcnow() - (self.last_data_date or utcnow())).total_seconds() / 3600.0),
        }

    def flatten_target(self, symbols: List[str]) -> np.ndarray:
        return np.zeros(len(symbols), dtype=np.float64)

    def _regime_name(self, code_or_name: Any) -> str:
        if isinstance(code_or_name, str):
            return code_or_name.upper()
        try:
            return str(self.engine_mod.RN.get(int(code_or_name), code_or_name)).upper()
        except Exception:
            return "CAUTION"

    def _apply_meta_override(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.cfg.autotune_apply_live:
            return params
        override = self.store.get_json(LiveKeys.AUTOTUNE, {}) or {}
        if not override:
            return params
        out = dict(params)
        for k in ("ntb_base", "to_base", "to_max", "a2b2_base", "a2b2_to", "crash_vix"):
            if k in override:
                out[k] = override[k]
        return out

    def _portfolio_metrics(self, target: np.ndarray, actual: np.ndarray, equity: float, open_orders: int) -> Dict[str, Any]:
        drift = float(np.max(np.abs(target - actual))) if len(target) else 0.0
        gross = float(np.sum(np.clip(np.abs(actual), 0.0, 10.0)))
        session_day = utcnow().strftime("%Y-%m-%d")
        if self.state.session_day != session_day:
            self.state.session_day = session_day
            self.state.turnover_today = 0.0
            self.state.session_start_equity = equity  # bookmark session start
        if self.state.session_start_equity <= 0:
            self.state.session_start_equity = equity
        peak = max(self.state.equity_peak, equity)
        self.state.equity_peak = peak
        dd = (peak - equity) / peak if peak > 0 else 0.0
        daily_pnl_pct = (equity - self.state.session_start_equity) / self.state.session_start_equity if self.state.session_start_equity > 0 else 0.0
        return {
            "equity": equity,
            "gross_exposure": gross,
            "leverage": gross,
            "max_weight_drift": drift,
            "open_orders": open_orders,
            "drawdown_pct": dd,
            "daily_pnl_pct": daily_pnl_pct,
            "turnover_today": self.state.turnover_today,
        }

    def build_execution_plan(
        self,
        symbols: List[str],
        target: np.ndarray,
        actual: np.ndarray,
        prices: Dict[str, float],
        equity: float,
        decision: DecisionResult,
    ) -> Tuple[Dict[str, Any], np.ndarray, float]:
        tgt = np.array(target, dtype=np.float64)
        if decision.decision == Decision.FLATTEN:
            tgt[:] = 0.0
        elif decision.decision == Decision.DE_RISK_ONLY:
            gross = np.sum(tgt)
            cap = min(decision.params.get("exposure_target", 0.45), self.cfg.execution.max_published_exposure)
            if gross > cap and gross > 0:
                tgt *= cap / gross
        elif decision.decision == Decision.CONSERVATIVE_ONLY:
            gross = np.sum(tgt)
            cap = min(decision.params.get("exposure_target", 0.80), self.cfg.execution.max_published_exposure)
            if gross > cap and gross > 0:
                tgt *= cap / gross

        if self.cfg.kernel.long_only:
            tgt = np.clip(tgt, 0.0, None)
        tgt = np.clip(tgt, 0.0, self.cfg.execution.max_published_exposure)
        gross = float(np.sum(tgt))
        if gross > self.cfg.execution.max_published_exposure and gross > 0:
            tgt *= self.cfg.execution.max_published_exposure / gross

        delta = tgt - actual
        turnover = float(np.sum(np.abs(delta)))
        if turnover > self.cfg.execution.max_live_turnover_pct:
            tgt = actual + (delta * (self.cfg.execution.max_live_turnover_pct / turnover))
            delta = tgt - actual
            turnover = float(np.sum(np.abs(delta)))

        orders = []
        max_single_notional = equity * self.cfg.execution.max_single_order_pct_equity
        for sym, dw in sorted(zip(symbols, delta), key=lambda kv: abs(kv[1]), reverse=True):
            if abs(dw) < self.cfg.execution.min_order_weight_delta:
                continue
            px = prices.get(sym)
            if px is None or px <= 0:
                continue
            d_notional = float(dw * equity)
            if abs(d_notional) < self.cfg.execution.min_order_notional:
                continue
            d_notional = clamp(d_notional, -max_single_notional, max_single_notional)
            orders.append({
                "symbol": sym,
                "side": "BUY" if d_notional > 0 else "SELL",
                "delta_weight": round(float(dw), 8),
                "target_weight": round(float(tgt[symbols.index(sym)]), 8),
                "delta_notional": round(float(d_notional), 2),
                "price": round(float(px), 6),
            })
            if len(orders) >= self.cfg.execution.max_order_count:
                break

        payload = {
            "ts": time.time(),
            "decision": decision.decision,
            "regime": decision.regime,
            "urgency": decision.urgency,
            "reason": decision.reason,
            "orders": orders,
            "turnover": turnover,
            "gross_target": float(np.sum(tgt)),
        }
        return payload, tgt, turnover

    def publish_decision(self, decision: DecisionResult, target_payload: Dict[str, Any], target_hash: str):
        record = {
            "ts": time.time(),
            "decision": decision.decision,
            "regime": decision.regime,
            "urgency": decision.urgency,
            "reason": decision.reason,
            "target_hash": target_hash,
            "turnover": target_payload.get("turnover", 0.0),
        }
        self.store.set_json(LiveKeys.DECISION_LAST, record)
        self.store.publish_history_point(LiveKeys.DECISION_HISTORY, record, self.cfg.price_history_ttl_sec, self.cfg.history_keep)

    def maybe_toggle_halt(self):
        if self.store.r.get(LiveKeys.HALT_FLAG):
            self.state.halted = True
            self.state.halt_reason = "manual halt"
            self.store.r.delete(LiveKeys.HALT_FLAG)
        if self.store.r.get(LiveKeys.RESUME_FLAG):
            self.state.halted = False
            self.state.halt_reason = None
            self.store.r.delete(LiveKeys.RESUME_FLAG)
        if self.store.r.get(LiveKeys.FLATTEN_FLAG):
            self.state.halted = True
            self.state.halt_reason = "manual flatten"
            self.store.r.delete(LiveKeys.FLATTEN_FLAG)
            return True
        return False

    def audit_write(self, name: str, obj: Any):
        path = Path(self.cfg.audit_dir) / f"{utcnow().strftime('%Y%m%dT%H%M%SZ')}_{name}.json"
        path.write_text(json.dumps(obj, indent=2, default=str))


    # [AOA_ARES_MIRROR] desired-book mirror for AOA shadow compare stage
    def _aoa_ares_book_key(self) -> str:
        return os.getenv("AOA_ARES_BOOK_KEY", "ares:aoa:desired_book:us_equity_main:ares-v56")

    def _publish_aoa_desired_book_mirror(
        self,
        *,
        symbols: List[str],
        live_target: np.ndarray,
        prices_used: Dict[str, float],
        equity: float,
        decision: DecisionResult,
        regime_name: str,
        selected: List[str],
        confidence: float,
        exec_id: str,
        target_hash: str,
    ) -> None:
        """Best-effort desired-book mirror for AOA shadow mode.

        This does NOT alter direct execution.
        It only publishes a normalized desired-position snapshot to Redis.
        """
        try:
            ttl_sec = max(120, int(os.getenv("AOA_DESIRED_BOOK_TTL_SEC", "900")))
            account_id = os.getenv("AOA_ACCOUNT_ID", "us_equity_main")
            mode = os.getenv("AOA_ARES_BOOK_MODE", "SHADOW").upper()
            asof_ms = int(time.time() * 1000)
            positions_out = []

            for sym, w in zip(symbols, live_target.tolist()):
                px = prices_used.get(sym)
                if px is None or float(px) <= 0:
                    continue
                target_notional = float(w) * float(equity)
                target_qty = int(round(target_notional / float(px)))
                if target_qty == 0 and abs(float(w)) < 1e-12:
                    continue
                positions_out.append({
                    "symbol": sym,
                    "target_qty": target_qty,
                    "target_weight": round(float(w), 8),
                    "limit_price": round(float(px), 6),
                })

            payload = {
                "schema": "ARES_DESIRED_POSITION_V1",
                "account_id": account_id,
                "family": "ares-v56",
                "mode": mode,
                "asof_ms": asof_ms,
                "correlation_id": exec_id or target_hash or str(uuid.uuid4()),
                "positions": positions_out,
                "meta": {
                    "source": "ares-v56-live",
                    "source_ts": asof_ms,
                    "engine_date": self.state.last_engine_date,
                    "decision": decision.decision,
                    "regime": regime_name,
                    "confidence": round(float(confidence), 6),
                    "selected_universes": selected,
                    "target_hash": target_hash,
                    "gross_target": round(float(np.sum(live_target)), 8),
                    "equity": round(float(equity), 2),
                    "book_stage": "post_decision_post_turnover_pre_emit",
                },
            }
            self.store.set_json(self._aoa_ares_book_key(), payload, ex=ttl_sec)
        except Exception as _aoa_e:
            log.warning("AOA_ARES_MIRROR_ERROR: %s", _aoa_e)

    def run_once(self) -> None:
        self.heartbeat()
        if not self._renew_leader_lease():
            return

        # V5.6.1b: Kill switch check - respect kill-switch-authority-v2
        ks_ok, ks_reason = self._check_kill_switch()
        if not ks_ok:
            log.warning("V5.6.1b Kill Switch: %s -> HALT", ks_reason)
            self.state.halted = True
            self.state.halt_reason = f"kill-switch: {ks_reason}"
            self.state.engine_state = "HALT"  # [V5.6.1b-PATCH]
            self.set_readiness(False, f"HALTED: {ks_reason}")
            self.save_state()
            return

        force_flatten = self.maybe_toggle_halt()
        self.set_readiness(False, "initializing")
        force_rebuild = bool(self.store.r.get(LiveKeys.FORCE_REBUILD))
        force_execute = bool(self.store.r.get(LiveKeys.FORCE_EXECUTE))
        if force_rebuild:
            self.store.r.delete(LiveKeys.FORCE_REBUILD)
            self.state = RuntimeState()
        if force_execute:
            self.store.r.delete(LiveKeys.FORCE_EXECUTE)

        full_data = self.load_or_refresh_data(force=force_rebuild)
        symbols = list(full_data["symbols"])
        market = self.current_market_snapshot()
        self.feedback.score_due_predictions()

        if self.state.halted and not force_flatten:
            self.set_readiness(False, f"HALTED: {self.state.halt_reason}")
            self.save_state()
            return

        state_before = self.state.last_engine_date
        params_live = self._apply_meta_override(dict(self.alpha_params))
        self.state, snapshot = self.engine.replay_to_latest(full_data, self.state, params_live)
        target = snapshot["target"]
        regime_name = self._regime_name(snapshot["regime_name"])
        confidence = float(snapshot["confidence"])
        selected = list(snapshot["selected"])
        stable_score = 1.0 if state_before == self.state.last_engine_date else 0.8

        last_pred_ts = safe_float(self.state.stats.get("last_prediction_ts"), 0.0)
        if time.time() - last_pred_ts >= 300 and market["spy"] > 0:
            self.feedback.record_prediction(regime=regime_name, confidence=confidence, spy=market["spy"], vix=market["vix"], selected=selected, stable_score=stable_score)
            self.state.stats["last_prediction_ts"] = time.time()

        fallback_prices = {sym: float(full_data["prices_df"].iloc[-1][sym]) for sym in symbols}
        actual_w, pos_meta = self.reader.positions_to_weights(symbols, fallback_prices)
        equity = safe_float(pos_meta.get("equity"), self.reader.get_equity())
        quote_stale = any(sym in pos_meta.get("stale_symbols", []) for sym in pos_meta.get("positions", {}).keys())
        market["quotes_stale"] = quote_stale

        portfolio = self._portfolio_metrics(target, actual_w, equity, self.reader.get_open_orders())
        meta = self.store.get_json(LiveKeys.AUTOTUNE, {}) or {}
        decision = self.governor.evaluate(regime_name, confidence, market, portfolio, target, actual_w, meta)
        if force_execute and decision.decision == Decision.DEFER:
            decision = DecisionResult(Decision.EXECUTE, regime_name, 1.0, "force execute override", params=decision.params)
        if force_flatten:
            decision = DecisionResult(Decision.FLATTEN, regime_name, 1.0, "manual flatten", params={})

        target_payload, live_target, turnover = self.build_execution_plan(symbols, target, actual_w, fallback_prices, equity, decision)
        prices_used = {sym: fallback_prices.get(sym) for sym in symbols}
        target_hash = sha256_json({"symbols": symbols, "target": [round(float(x), 8) for x in live_target.tolist()], "decision": decision.decision})
        exec_id = sha256_json({"date": self.state.last_engine_date, "decision": decision.decision, "hash": target_hash})[:24]
        target_payload.update({
            "exec_id": exec_id,
            "engine_date": self.state.last_engine_date,
            "selected_universes": selected,
            "confidence": confidence,
            "prices_used": prices_used,
            "meta_override": meta,
            "alpha_params_subset": {k: params_live.get(k) for k in ["top_k", "signal_rebal", "execution_stagger", "target_vol", "per_cap", "cost_bps", "ntb_base", "to_base", "to_max", "a2b2_base", "a2b2_to", "crash_vix"]},
        })

        # [AOA_ARES_MIRROR] best-effort desired-book snapshot for AOA shadow compare stage
        self._publish_aoa_desired_book_mirror(
            symbols=symbols,
            live_target=live_target,
            prices_used=prices_used,
            equity=equity,
            decision=decision,
            regime_name=regime_name,
            selected=selected,
            confidence=confidence,
            exec_id=exec_id,
            target_hash=target_hash,
        )

        # dead code removed: actual_cost_bps / shadow_cost_bps locals were unused
        # cost metrics are computed inline in the metrics dict below
        metrics = {
            "equity": equity,
            "gross_exposure": float(np.sum(np.abs(actual_w))),
            "target_exposure": float(np.sum(live_target)),
            "turnover_today": self.state.turnover_today,
            "last_turnover": turnover,
            "avg_actual_cost_bps": turnover * self.cfg.actual_cost_bps,
            "avg_shadow_cost_bps": turnover * (self.cfg.actual_cost_bps + self.cfg.shadow_slippage_bps + self.cfg.shadow_impact_coeff_bps * turnover),
            "decision": decision.decision,
            "regime": regime_name,
            "confidence": confidence,
        }
        autotune = self.feedback.bounded_autotune(params_live, self.state, metrics)

        # V5.6.1: AutoTune Promotion Gate - suggestion-only unless gate passes
        if autotune:
            should_promote, gate_reason = self.autotune_gate.evaluate(autotune, self.state, metrics)
            if should_promote and self.cfg.autotune_apply_live:
                self.autotune_gate.promote_to_live(autotune)
                log.info("V5.6.1 AutoTune: promoted (%s)", gate_reason)
            else:
                self.autotune_gate.record_suggestion(autotune)
                log.info("V5.6.1 AutoTune: suggestion only (%s, apply_live=%s)", gate_reason, self.cfg.autotune_apply_live)

        # V5.6.1: Check for autotune rollback
        should_rollback, rb_reason = self.autotune_gate.should_rollback()
        if should_rollback:
            self.autotune_gate.rollback()
            self._halt_with_incident(f"AutoTune rollback: {rb_reason}", {"autotune": autotune, "metrics": metrics})
            return

        self.state.engine_state = decision.decision  # [V5.6.1b-PATCH] track engine_state
        self.publish_decision(decision, target_payload, target_hash)
        self.store.set_json(LiveKeys.TARGET_LAST, {
            "ts": time.time(),
            "symbols": symbols,
            "target": [round(float(x), 8) for x in live_target.tolist()],
            "selected_universes": selected,
            "confidence": confidence,
            "regime": regime_name,
            "decision": decision.decision,
        })
        self.store.r.set(LiveKeys.TARGET_HASH, target_hash)
        self.store.set_json(LiveKeys.TARGET_META, {
            "actual_cost_bps": turnover * self.cfg.actual_cost_bps,
            "shadow_cost_bps": turnover * (self.cfg.actual_cost_bps + self.cfg.shadow_slippage_bps + self.cfg.shadow_impact_coeff_bps * turnover),
            "turnover": turnover,
            "autotune": autotune,
            "ts": time.time(),
        })
        self.store.set_json(LiveKeys.METRICS, metrics)
        self.store.publish_history_point(LiveKeys.METRIC_HISTORY, metrics, self.cfg.price_history_ttl_sec, self.cfg.history_keep)

        if decision.decision in {Decision.EXECUTE, Decision.CONSERVATIVE_ONLY, Decision.DE_RISK_ONLY, Decision.FLATTEN}:
            # V5.6.1: BrokerConsumerGuard pre-submit check
            consumer_ok, consumer_reason = self.consumer_guard.pre_submit_check()
            if not consumer_ok:
                log.warning("V5.6.1 ConsumerGuard: blocked submit (%s) -> DEFER", consumer_reason)
                self.audit_write("consumer_guard_block", {
                    "reason": consumer_reason, "decision": decision.decision, "metrics": metrics
                })
                # Downgrade to DEFER - do NOT commit state
                self.state.engine_state = "DEFER"  # [V5.6.1b-PATCH]
                self.set_readiness(True, f"consumer_guard: {consumer_reason}")
                self.save_state()
                return

            ack = self.bus.submit(target_payload)
            ack_status = ack.get("status", "unknown")
            self.consumer_guard.record_ack_result(ack_status)

            # V5.6.1: ACK-based state commit - only commit if acked or no-ack mode
            if ack_status == "acked" or self.cfg.execution.ack_timeout_sec <= 0:
                self.state.last_exec_id = exec_id
                self.state.last_target_hash = target_hash
                self.state.turnover_today = clamp(self.state.turnover_today + turnover, 0.0, 10.0)
                self.audit_write("execution", {"intent": target_payload, "ack": ack, "metrics": metrics})
                log.info("V5.6.1: execution committed (ack=%s)", ack_status)
            else:
                # submitted_no_ack: log but do NOT commit state
                self.audit_write("execution_no_ack", {"intent": target_payload, "ack": ack, "metrics": metrics})
                log.warning("V5.6.1: execution NOT committed (ack=%s, streak=%d)",
                            ack_status, self.consumer_guard._no_ack_streak)
                if self.consumer_guard._no_ack_streak >= self.consumer_guard.max_no_ack_streak:
                    self._halt_with_incident(
                        f"no-ack streak {self.consumer_guard._no_ack_streak}",
                        {"ack": ack, "metrics": metrics}
                    )
                    return

            if decision.decision in {Decision.FLATTEN, Decision.HALT}:
                self.state.halted = True
                self.state.halt_reason = decision.reason
        elif decision.decision == Decision.HALT:
            self.state.halted = True
            self.state.halt_reason = decision.reason
            zero_target = self.flatten_target(symbols)
            payload, _, _ = self.build_execution_plan(symbols, zero_target, actual_w, fallback_prices, equity, DecisionResult(Decision.FLATTEN, regime_name, 1.0, decision.reason))
            payload.update({"exec_id": exec_id + "-halt", "engine_date": self.state.last_engine_date})
            # HALT always submits (emergency)
            ack = self.bus.submit(payload)
            self.consumer_guard.record_ack_result(ack.get("status", "unknown"))
            self.audit_write("halt", {"decision": asdict(decision), "intent": payload, "ack": ack})
        else:
            self.audit_write("defer", {"decision": asdict(decision), "metrics": metrics})

        self.set_readiness(not self.state.halted, decision.reason)
        self.save_state()

    def run_forever(self):
        # V5.6.1b: ensure leader lease is released on exit
        import atexit
        atexit.register(self._release_leader_lease)
        log.info("ARES V5.6.1 live autopilot starting (hardened)")
        while self.running:
            try:
                self.run_once()
            except Exception as e:
                log.exception("run_once failure: %s", e)
                self._halt_with_incident(f"exception: {e}", {"traceback": str(e)})
            time.sleep(self.cfg.execution.cycle_sec)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="ARES V5.5 live autopilot")
    ap.add_argument("--config", required=True, help="Path to v55 live config JSON")
    ap.add_argument("--once", action="store_true", help="Run exactly one cycle")
    args = ap.parse_args()

    cfg = LiveAutopilotConfig.from_json(args.config)

    # [PATCH 2026-04-09] Fail-closed live contract attestation.
    # Prevents silent champion/live drift and non-reproducible /current launches.
    guard_report_path = os.getenv("ARES_STARTUP_ATTESTATION_PATH", "./startup_attestation.json")
    guard_result = guard_or_raise(
        manifest_path=os.getenv("ARES_LIVE_MANIFEST_PATH", "/home/ubuntu/ssot/live_manifest.json"),
        registry_path=os.getenv("ARES_CHAMPION_REGISTRY_PATH", "/home/ubuntu/ssot/champion_registry.json"),
        adapter_path=cfg.execution.engine_path,
        alpha_config_path=cfg.execution.alpha_config_path,
        runtime_dir=os.getenv("ARES_LIVE_RUNTIME_DIR", os.getcwd()),
        policy_path=os.getenv("ARES_LIVE_DRIFT_POLICY_PATH", "./live_drift_policy.json"),
        report_path=guard_report_path,
    )
    log.info(
        "Live contract attested: engine=%s adapter=%s config=%s",
        guard_result.get("engine_hash"),
        guard_result.get("adapter_hash"),
        guard_result.get("alpha_config_hash"),
    )

    svc = LiveAutopilotService(cfg)
    if args.once:
        svc.run_once()
    else:
        svc.run_forever()


if __name__ == "__main__":
    main()
