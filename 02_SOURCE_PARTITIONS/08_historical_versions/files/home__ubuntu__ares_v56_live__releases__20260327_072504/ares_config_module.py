# ares_config_module.py - Extracted from ares_v55_live_autopilot.py
# Part of ORCHESTRATOR_REFACTORING_PLAN Phase 1

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
    redis_url: str = "redis://localhost:6379/0"  # V5.6.1: prefer ARES_REDIS_URL env var
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
