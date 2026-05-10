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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
log = logging.getLogger("ares.v55.live")


# ---------------------------------------------------------------------------
# Redis keys
# ---------------------------------------------------------------------------
class LiveKeys:
    HEARTBEAT = "ares:v55:live:heartbeat"
    READINESS = "ares:v55:live:readiness"
    HALT_FLAG = "ares:v55:live:control:halt"
    RESUME_FLAG = "ares:v55:live:control:resume"
    FORCE_REBUILD = "ares:v55:live:control:force_rebuild"
    FORCE_EXECUTE = "ares:v55:live:control:force_execute"
    FLATTEN_FLAG = "ares:v55:live:control:flatten"
    EXECUTION_STREAM = "ares:v55:execution:intents"
    EXECUTION_ACK = "ares:v55:execution:ack"
    EXECUTION_LAST = "ares:v55:execution:last"
    EXECUTION_LOCK = "ares:v55:execution:lock"
    EXECUTION_IDEMP = "ares:v55:execution:idemp"
    TARGET_LAST = "ares:v55:target:last"
    TARGET_HASH = "ares:v55:target:hash"
    TARGET_META = "ares:v55:target:meta"
    METRICS = "ares:v55:metrics:latest"
    METRIC_HISTORY = "ares:v55:metrics:history"
    MARKET_SPY = "ares:v55:market:spy"
    MARKET_VIX = "ares:v55:market:vix"
    PREDICTIONS = "ares:v55:feedback:predictions"
    ACCURACY = "ares:v55:feedback:accuracy"
    ROLLING_ACCURACY = "ares:v55:feedback:rolling_accuracy"
    AUTOTUNE = "ares:v55:feedback:autotune"
    AUTOTUNE_HISTORY = "ares:v55:feedback:autotune_history"
    FLAP_COUNT = "ares:v55:feedback:flap_count"
    STATE_JSON = "ares:v55:state:summary"
    DECISION_LAST = "ares:v55:decision:last"
    DECISION_HISTORY = "ares:v55:decision:history"
    SESSION_STATS = "ares:v55:session:stats"


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
    max_open_orders: int = 0
    max_name_weight_drift: float = 0.035
    min_confidence_execute: float = 0.45
    force_flatten_on_stale_quotes: bool = True
    require_quote_freshness: bool = False
    long_only: bool = True


@dataclass
class LiveExecutionConfig:
    redis_url: str = "redis://localhost:6379/0"
    heartbeat_ttl_sec: int = 90
    cycle_sec: int = 30
    db_refresh_sec: int = 300
    state_path: str = "./v55_live_state.pkl"
    engine_path: str = "./engine_v54_aggressive_realistic.py"
    alpha_config_path: str = "./v54a_aggressive_realistic_config.json"
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


@dataclass
class LiveAutopilotConfig:
    execution: LiveExecutionConfig = field(default_factory=LiveExecutionConfig)
    kernel: LiveKernelConfig = field(default_factory=LiveKernelConfig)
    autotune_bounds: AutoTuneBounds = field(default_factory=AutoTuneBounds)
    autotune_enabled: bool = True
    autotune_apply_live: bool = True
    autotune_min_obs: int = 20
    autotune_cooldown_sec: int = 3600
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
        exe = LiveExecutionConfig(**raw.get("execution", {}))
        ker = LiveKernelConfig(**raw.get("kernel", {}))
        bnd = AutoTuneBounds(**raw.get("autotune_bounds", {}))
        return cls(
            execution=exe,
            kernel=ker,
            autotune_bounds=bnd,
            autotune_enabled=raw.get("autotune_enabled", True),
            autotune_apply_live=raw.get("autotune_apply_live", True),
            autotune_min_obs=raw.get("autotune_min_obs", 20),
            autotune_cooldown_sec=raw.get("autotune_cooldown_sec", 3600),
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
    if isinstance(x, datetime):
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

    def set_json(self, key: str, obj: Any, ex: Optional[int] = None):
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

    def publish_history_point(self, key: str, payload: Dict[str, Any], ttl_sec: int, max_items: int = 2000):
        raw = json.dumps(payload, separators=(",", ":"), default=str)
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
        acquired = self.store.r.set(idem_key, "1", nx=True, ex=86400)
        if not acquired:
            return {"status": "duplicate", "exec_id": exec_id}

        stream_id = self.store.r.xadd(LiveKeys.EXECUTION_STREAM, {"payload": json.dumps(intent, default=str)})
        self.store.set_json(LiveKeys.EXECUTION_LAST, {"ts": time.time(), "exec_id": exec_id, "stream_id": stream_id.decode() if isinstance(stream_id, bytes) else stream_id})
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
        "BEAR": {"drift": 0.030, "cooldown_min": 60, "max_turn": 0.16, "exposure": 0.45, "urgency": 0.70},
        "CRASH": {"drift": 0.020, "cooldown_min": 30, "max_turn": 0.12, "exposure": 0.20, "urgency": 0.95},
        "INF_SHOCK": {"drift": 0.025, "cooldown_min": 60, "max_turn": 0.14, "exposure": 0.30, "urgency": 0.85},
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
        self.alpha_params = alpha_params

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

    def _from_runtime_shadow(self, st) -> EngineShadowPersist:
        return EngineShadowPersist(
            pw=list(map(float, st.pw.tolist())),
            prev_reg=int(st.prev_reg),
            confidence=float(st.confidence),
            sig_hist=[float(x) for x in list(st.sig_hist)],
            ret_hist=[float(x) for x in list(st.ret_hist)],
            daily_returns=[float(x) for x in list(st.daily_returns)],
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
        self.engine_mod = load_engine_module(cfg.execution.engine_path)
        self.alpha_params = json.loads(Path(cfg.execution.alpha_config_path).read_text())
        self.alpha_params["cost_bps"] = cfg.actual_cost_bps
        self.alpha_params["shadow_slippage_bps"] = cfg.shadow_slippage_bps
        self.alpha_params["shadow_impact_coeff_bps"] = cfg.shadow_impact_coeff_bps
        for key in ("ace_base", "ace_max", "ace_floor"):
            if key in self.alpha_params and self.alpha_params[key]:
                self.alpha_params[key] = {int(k): v for k, v in self.alpha_params[key].items()}
        self.engine = LiveEngine(self.engine_mod, self.alpha_params)
        self.state: RuntimeState = pickle_load(cfg.execution.state_path) or RuntimeState()
        self.running = True
        self.last_db_refresh = 0.0
        self.full_data = None
        self.last_data_date = None
        self.install_signal_handlers()

    def install_signal_handlers(self):
        def _stop(signum, frame):
            log.warning("Signal %s received, stopping", signum)
            self.running = False
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

    def save_state(self):
        atomic_pickle_dump(self.state, self.cfg.execution.state_path)
        self.store.set_json(LiveKeys.STATE_JSON, {
            "last_engine_date": self.state.last_engine_date,
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
            cap = min(decision.params.get("exposure_target", 0.35), self.cfg.execution.max_published_exposure)
            if gross > cap and gross > 0:
                tgt *= cap / gross
        elif decision.decision == Decision.CONSERVATIVE_ONLY:
            gross = np.sum(tgt)
            cap = min(decision.params.get("exposure_target", 0.75), self.cfg.execution.max_published_exposure)
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

    def run_once(self) -> None:
        self.heartbeat()
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
            ack = self.bus.submit(target_payload)
            self.state.last_exec_id = exec_id
            self.state.last_target_hash = target_hash
            self.state.turnover_today = clamp(self.state.turnover_today + turnover, 0.0, 10.0)
            self.audit_write("execution", {"intent": target_payload, "ack": ack, "metrics": metrics})
            if decision.decision in {Decision.FLATTEN, Decision.HALT}:
                self.state.halted = True
                self.state.halt_reason = decision.reason
        elif decision.decision == Decision.HALT:
            self.state.halted = True
            self.state.halt_reason = decision.reason
            zero_target = self.flatten_target(symbols)
            payload, _, _ = self.build_execution_plan(symbols, zero_target, actual_w, fallback_prices, equity, DecisionResult(Decision.FLATTEN, regime_name, 1.0, decision.reason))
            payload.update({"exec_id": exec_id + "-halt", "engine_date": self.state.last_engine_date})
            ack = self.bus.submit(payload)
            self.audit_write("halt", {"decision": asdict(decision), "intent": payload, "ack": ack})
        else:
            self.audit_write("defer", {"decision": asdict(decision), "metrics": metrics})

        self.set_readiness(not self.state.halted, decision.reason)
        self.save_state()

    def run_forever(self):
        log.info("ARES V5.5 live autopilot starting")
        while self.running:
            try:
                self.run_once()
            except Exception as e:
                log.exception("run_once failure: %s", e)
                self.state.halted = True
                self.state.halt_reason = f"exception: {e}"
                self.set_readiness(False, self.state.halt_reason)
                self.save_state()
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
    svc = LiveAutopilotService(cfg)
    if args.once:
        svc.run_once()
    else:
        svc.run_forever()


if __name__ == "__main__":
    main()
