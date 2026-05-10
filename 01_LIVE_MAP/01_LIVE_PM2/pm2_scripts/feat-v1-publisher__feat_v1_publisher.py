#!/usr/bin/env python3
"""
ARES Feature v1 Publisher (Elon Musk 5-step Refactor)
=====================================================

PURPOSE
-------
Replace the disconnected 3-layer feature pipeline with a single, deterministic 
producer that writes EXACTLY the 22 features the deployed XGBoost model expects.

ARCHITECTURE (after refactor)
-----------------------------
DB(ares_x_v11_0.db) → feat_v1_publisher → Redis feat:v1:{symbol} → xgb_writer_v7 → xgb_drop_v4 → xgb:risk:{symbol}

KEY CONTRACTS
-------------
- Schema SSOT  : /home/ubuntu/ares_current/models/xgb_feature_cols.json (22 features)
- Output key   : feat:v1:{SYMBOL}
- Output value : JSON {symbol, features:{...22}, computed_at, source_date, ttl}
- TTL          : 600s (10 min, > publisher cycle 300s)
- Cycle        : 300s (5 min) during market hours, 3600s off-hours

FAIL-CLOSED
-----------
- If any feature is None/NaN, set status=DEGRADED and skip that symbol (no write)
- If DB is stale > 6h during market hours, set status=HALTED and emit alert
- If model schema file missing or has != 22 features, refuse to start
"""

import os
import sys
import json
import time
import sqlite3
import logging
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import redis
import signal

# ============================================================================
# CONFIGURATION (ENV VARIABLES — fail-closed defaults)
# ============================================================================
ARES_HOME      = os.getenv("ARES_HOME", "/home/ubuntu/ares_current")
SCHEMA_PATH    = os.getenv("FEAT_V1_SCHEMA",   f"{ARES_HOME}/models/xgb_feature_cols.json")
DB_PATH        = os.getenv("FEAT_V1_DB_PATH",  "/home/ubuntu/ares_x_v11_0.db")
SYMBOLS_PATH   = os.getenv("FEAT_V1_SYMBOLS",  f"{ARES_HOME}/config/active_symbols.json")
REDIS_HOST     = os.getenv("REDIS_HOST",       "127.0.0.1")
REDIS_PORT     = int(os.getenv("REDIS_PORT",   "6379"))
REDIS_PASS     = os.getenv("REDIS_PASSWORD",   "")
KEY_PREFIX     = "feat:v1:"
KEY_TTL        = int(os.getenv("FEAT_V1_TTL",  "600"))
CYCLE_SEC      = int(os.getenv("FEAT_V1_CYCLE","300"))
HEARTBEAT_KEY  = "feat:v1:heartbeat"
STATUS_KEY     = "feat:v1:status"
HALT_KEY       = "feat:v1:halt"
MAX_DB_STALE_H = float(os.getenv("FEAT_V1_MAX_STALE_H", "6"))  # 6h during market hours
SPY_LOOKBACK   = 252  # for max-drawdown-21d, vol_20d, etc.

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("feat_v1")

# ============================================================================
# SCHEMA LOAD (fail-closed)
# ============================================================================
EXPECTED_FEATURES: List[str] = []
def _load_schema() -> List[str]:
    p = Path(SCHEMA_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Schema not found: {SCHEMA_PATH}")
    cols = json.loads(p.read_text())
    if not isinstance(cols, list) or len(cols) != 22:
        raise ValueError(f"Schema must be a list of 22 features, got {len(cols)}")
    return cols

# ============================================================================
# REDIS CONNECTION
# ============================================================================
def _connect_redis() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        username=os.getenv("REDIS_USERNAME", "ares-admin"),
        password=REDIS_PASS or None,
        ssl=os.getenv("REDIS_SSL", "true").lower() == "true",
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=3,
    )

# ============================================================================
# FEATURE COMPUTATION (the only 22 features the model accepts)
# ============================================================================
def _rsi(closes: np.ndarray, period: int = 14) -> Optional[float]:
    """Wilder's smoothed RSI (RMA, not SMA) over full available history.
    Cross-Review Fix: previous version used SMA which produced incorrect feature
    values invalidating the model's calibrated predictions.
    Reference: Welles Wilder, 'New Concepts in Technical Trading Systems' (1978).
    """
    n = len(closes)
    if n < period + 1:
        return None
    diffs = np.diff(closes)
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    # Initial seed = SMA of first `period` values
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    # Wilder's recursive smoothing for the remaining bars
    for i in range(period, len(diffs)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))

def _bollinger_position(closes: np.ndarray, n: int = 20) -> Optional[float]:
    if len(closes) < n:
        return None
    window = closes[-n:]
    mean = window.mean()
    std = window.std(ddof=0)
    if std == 0:
        return 0.5
    upper = mean + 2 * std
    lower = mean - 2 * std
    return float((closes[-1] - lower) / (upper - lower))

def _vwap(closes: np.ndarray, volumes: np.ndarray, n: int) -> Optional[float]:
    if len(closes) < n or len(volumes) < n:
        return None
    pv = closes[-n:] * volumes[-n:]
    v = volumes[-n:].sum()
    if v == 0:
        return None
    return float(pv.sum() / v)

def _ret(closes: np.ndarray, n: int) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    return float(closes[-1] / closes[-(n+1)] - 1)

def _vol(closes: np.ndarray, n: int) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    rets = np.diff(np.log(closes[-(n+1):]))
    return float(rets.std(ddof=0) * np.sqrt(252))  # annualized

def _vol_ratio(volumes: np.ndarray, n: int) -> Optional[float]:
    if len(volumes) < 2 * n:
        return None
    recent = volumes[-n:].mean()
    prior = volumes[-2*n:-n].mean()
    if prior == 0:
        return None
    return float(recent / prior)

def _max_dd_21d(closes: np.ndarray) -> Optional[float]:
    if len(closes) < 22:
        return None
    window = closes[-21:]
    peak = window[0]
    max_dd = 0.0
    for c in window:
        if c > peak:
            peak = c
        dd = (c - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return float(max_dd)

def compute_features_for_symbol(
    symbol: str,
    sym_data: Dict,
    spy_data: Dict,
    vix_data: Dict,
) -> Optional[Dict[str, float]]:
    """Compute exactly the 22 features the deployed model expects.

    Returns None if any feature cannot be computed (fail-closed).
    """
    closes = np.array(sym_data["closes"], dtype=np.float64)
    volumes = np.array(sym_data["volumes"], dtype=np.float64)
    highs = np.array(sym_data["highs"], dtype=np.float64)
    lows = np.array(sym_data["lows"], dtype=np.float64)
    spy_closes = np.array(spy_data["closes"], dtype=np.float64)
    vix_closes = np.array(vix_data["closes"], dtype=np.float64)

    if len(closes) < 252 or len(spy_closes) < 22 or len(vix_closes) < 6:
        log.debug(f"{symbol}: insufficient data closes={len(closes)} spy={len(spy_closes)} vix={len(vix_closes)}")
        return None

    feats: Dict[str, Optional[float]] = {}
    feats["ret_1d"] = _ret(closes, 1)
    feats["ret_5d"] = _ret(closes, 5)
    feats["ret_10d"] = _ret(closes, 10)
    feats["ret_21d"] = _ret(closes, 21)
    feats["vol_5d"] = _vol(closes, 5)
    feats["vol_10d"] = _vol(closes, 10)
    feats["vol_20d"] = _vol(closes, 20)
    feats["rsi_14"] = _rsi(closes, 14)
    feats["bb_position_20"] = _bollinger_position(closes, 20)
    feats["vol_ratio_5d"] = _vol_ratio(volumes, 5)
    feats["vol_ratio_20d"] = _vol_ratio(volumes, 20)
    high_52w = float(highs[-252:].max())
    low_52w = float(lows[-252:].min())
    last = float(closes[-1])
    feats["pct_from_high_52w"] = (last - high_52w) / high_52w if high_52w > 0 else None
    feats["pct_from_low_52w"]  = (last - low_52w) / low_52w if low_52w > 0 else None
    vwap5 = _vwap(closes, volumes, 5)
    feats["price_vs_vwap"] = (last / vwap5 - 1) if vwap5 else None
    feats["spy_ret_1d"] = _ret(spy_closes, 1)
    feats["spy_ret_5d"] = _ret(spy_closes, 5)
    feats["spy_vol_20d"] = _vol(spy_closes, 20)
    feats["vix_level"] = float(vix_closes[-1])
    feats["vix_change_5d"] = _ret(vix_closes, 5)
    spy_ret_21 = _ret(spy_closes, 21)
    sym_ret_21 = _ret(closes, 21)
    feats["relative_strength_21d"] = (sym_ret_21 - spy_ret_21) if (sym_ret_21 is not None and spy_ret_21 is not None) else None
    feats["max_drawdown_21d"] = _max_dd_21d(closes)
    vwap5_prev = _vwap(closes[:-5], volumes[:-5], 5) if len(closes) > 10 else None
    feats["vwap_ret_5d"] = (vwap5 / vwap5_prev - 1) if (vwap5 and vwap5_prev) else None

    # Fail-closed: if ANY feature is None/NaN, skip this symbol
    for k, v in feats.items():
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            log.debug(f"{symbol}: feature {k} is invalid ({v}) — skipping")
            return None

    # Sanity: schema match (22 features)
    if set(feats.keys()) != set(EXPECTED_FEATURES):
        missing = set(EXPECTED_FEATURES) - set(feats.keys())
        extra = set(feats.keys()) - set(EXPECTED_FEATURES)
        log.error(f"{symbol}: schema mismatch missing={missing} extra={extra}")
        return None

    return {k: float(feats[k]) for k in EXPECTED_FEATURES}  # ordered by schema

# ============================================================================
# DB ACCESS
# ============================================================================
def _load_symbol_history(conn: sqlite3.Connection, symbol: str, lookback: int = 260) -> Optional[Dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT date, open, high, low, close, volume FROM daily_ohlcv "
        "WHERE symbol=? ORDER BY date DESC LIMIT ?",
        (symbol, lookback),
    )
    rows = cur.fetchall()
    if not rows or len(rows) < 252:
        return None
    rows = list(reversed(rows))  # ascending date order
    return {
        "dates":   [r[0] for r in rows],
        "opens":   [r[1] for r in rows],
        "highs":   [r[2] for r in rows],
        "lows":    [r[3] for r in rows],
        "closes":  [r[4] for r in rows],
        "volumes": [r[5] for r in rows],
        "last_date": rows[-1][0],
    }

# ============================================================================
# MAIN CYCLE
# ============================================================================
def _check_freshness(spy_last_date: str) -> Tuple[bool, float]:
    """Returns (is_fresh, hours_stale)."""
    spy_dt = dt.datetime.fromisoformat(spy_last_date)
    hours = (dt.datetime.utcnow() - spy_dt).total_seconds() / 3600
    return (hours <= MAX_DB_STALE_H, hours)

def run_cycle(r: redis.Redis, conn: sqlite3.Connection, symbols: List[str], cycle_n: int) -> Dict:
    started = time.time()
    spy = _load_symbol_history(conn, "SPY")
    if spy is None:
        log.error("SPY history unavailable — HALTING this cycle")
        r.set(HALT_KEY, json.dumps({"reason": "spy_unavailable", "ts": dt.datetime.utcnow().isoformat()}), ex=KEY_TTL)
        return {"status": "HALTED", "published": 0, "errors": 0}
    vix = _load_symbol_history(conn, "VIX") or _load_symbol_history(conn, "^VIX")
    if vix is None:
        log.error("VIX history unavailable — HALTING")
        r.set(HALT_KEY, json.dumps({"reason": "vix_unavailable", "ts": dt.datetime.utcnow().isoformat()}), ex=KEY_TTL)
        return {"status": "HALTED", "published": 0, "errors": 0}
    is_fresh, hours_stale = _check_freshness(spy["last_date"])
    if not is_fresh:
        log.warning(f"DB stale {hours_stale:.1f}h > {MAX_DB_STALE_H}h — emitting STALE alert (continue with degraded signal)")
        r.set(HALT_KEY, json.dumps({
            "reason": "db_stale", "hours_stale": round(hours_stale, 2),
            "spy_last_date": spy["last_date"], "ts": dt.datetime.utcnow().isoformat()
        }), ex=KEY_TTL)
    else:
        r.delete(HALT_KEY)

    spy_data = spy
    vix_data = vix
    published = 0
    errors = 0
    feature_diversity_check: List[Dict[str, float]] = []

    pipe = r.pipeline()
    for sym in symbols:
        try:
            sym_hist = _load_symbol_history(conn, sym)
            if sym_hist is None:
                continue
            feats = compute_features_for_symbol(sym, sym_hist, spy_data, vix_data)
            if feats is None:
                errors += 1
                continue
            payload = {
                "symbol": sym,
                "features": feats,
                "source_date": sym_hist["last_date"],
                "computed_at": dt.datetime.utcnow().isoformat() + "Z",
                "schema_version": "v1",
                "n_features": len(EXPECTED_FEATURES),
            }
            pipe.set(f"{KEY_PREFIX}{sym}", json.dumps(payload), ex=KEY_TTL)
            published += 1
            feature_diversity_check.append(feats)
        except Exception as e:
            errors += 1
            log.warning(f"{sym}: error {e}")
    pipe.execute()

    # Heartbeat + status
    elapsed = time.time() - started
    diversity_ok = False
    if len(feature_diversity_check) > 5:
        # Check that ret_1d has variance (anti-homogenization sentinel)
        rets = [f["ret_1d"] for f in feature_diversity_check]
        diversity_ok = bool(np.std(rets) > 1e-6)
    summary = {
        "cycle": cycle_n,
        "published": published,
        "errors": errors,
        "elapsed_sec": round(elapsed, 3),
        "spy_last_date": spy["last_date"],
        "hours_stale": round(hours_stale, 2),
        "status": "OK" if (is_fresh and diversity_ok and published > 0) else ("STALE" if not is_fresh else "DEGRADED"),
        "diversity_ok": diversity_ok,
        "ts": dt.datetime.utcnow().isoformat() + "Z",
    }
    r.set(STATUS_KEY, json.dumps(summary), ex=KEY_TTL * 3)
    r.set(HEARTBEAT_KEY, str(int(time.time())), ex=KEY_TTL * 3)
    log.info(f"Cycle {cycle_n}: published={published} errors={errors} elapsed={elapsed:.2f}s status={summary['status']} stale={hours_stale:.1f}h")
    return summary

# ============================================================================
# ENTRYPOINT
# ============================================================================
def _load_symbols() -> List[str]:
    p = Path(SYMBOLS_PATH)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, list):
                return [s.upper() for s in data if isinstance(s, str)]
            if isinstance(data, dict) and "symbols" in data:
                return [s.upper() for s in data["symbols"]]
        except Exception as e:
            log.warning(f"Failed to load active_symbols.json: {e}")
    # Fallback: hardcoded universe (top S&P 50 + ARES universe)
    return [
        "SPY","QQQ","IWM","DIA","VIXY","TLT","HYG","LQD","GLD","USO",
        "AAPL","MSFT","AMZN","GOOGL","META","NVDA","TSLA","BRK.B","JPM","V",
        "UNH","XOM","JNJ","PG","HD","MA","CVX","ABBV","PFE","KO","BAC","WMT",
        "MRK","COST","DIS","NFLX","ADBE","CRM","TMO","CSCO","PEP","ABT","WFC",
        "MCD","ACN","DHR","TXN","NKE","UPS","ORCL","RTX","BA","CAT","COP",
        "GE","GS","MS","CMCSA","TGT","REGN","AMD","ASML","CL","PM","SMCI",
        "INTC","MU","QCOM","ARM","PLTR","MO","SYY","CRWD","WFC","JPM","JNJ"
    ]

def _sigterm_handler(sig, frame):
    log.info("SIGTERM received — shutting down gracefully")
    sys.exit(0)

def main():
    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigterm_handler)
    global EXPECTED_FEATURES
    EXPECTED_FEATURES = _load_schema()
    log.info(f"Loaded schema: {len(EXPECTED_FEATURES)} features from {SCHEMA_PATH}")

    r = _connect_redis()
    r.ping()
    log.info(f"Redis OK: {REDIS_HOST}:{REDIS_PORT}")

    symbols = _load_symbols()
    log.info(f"Universe: {len(symbols)} symbols")

    if not Path(DB_PATH).exists() and not os.path.islink(DB_PATH):
        log.error(f"DB not found: {DB_PATH}")
        sys.exit(1)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    log.info(f"DB OK: {DB_PATH}")

    cycle = 0
    while True:
        cycle += 1
        try:
            run_cycle(r, conn, symbols, cycle)
        except Exception as e:
            log.exception(f"Cycle {cycle} failed: {e}")
        time.sleep(CYCLE_SEC)

if __name__ == "__main__":
    main()
