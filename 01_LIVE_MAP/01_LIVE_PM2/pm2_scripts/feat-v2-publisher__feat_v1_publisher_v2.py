#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES Feature v2 Publisher  [PATCH 20260507]
==========================================

PURPOSE
-------
Successor of `feat_v1_publisher.py` that publishes EXACTLY the 27 features
expected by xgb_riskflag_writer_v8 / model-v2 (22 equities + 5 options).

DUAL-WRITE
----------
For safe rollout, this publisher writes BOTH:
  feat:v1:{SYMBOL}   ← the legacy 22-feature payload (compat for v7 writer)
  feat:v2:{SYMBOL}   ← the new 27-feature payload   (input for v8 writer)

OPTION FEATURES SOURCE
----------------------
Reads `premium:options:{SYMBOL}` (written by `options_feature_collector.py`)
and merges the 5 numeric features into the feature vector. Fail-soft:
- key missing or older than `OPT_STALE_S` → option features filled with
  `OPT_DEFAULTS` (1.0/1.0/0.0/0.18/0.0 — neutral) and `opt_source='default'`
- otherwise `opt_source='premium:options'`.

The legacy v1 payload is unchanged (22 features); v2 adds the 5 option
columns at the END to match `xgb_feature_cols_v2.json`.

KEY CONTRACTS
-------------
- Schema v1 SSOT  : /home/ubuntu/ares_current/models/xgb_feature_cols.json     (22)
- Schema v2 SSOT  : /home/ubuntu/ares_current/models/xgb_feature_cols_v2.json  (27)
- Output keys     : feat:v1:{SYMBOL} (TTL 600s), feat:v2:{SYMBOL} (TTL 600s)
- Cycle           : 300s

FAIL-CLOSED
-----------
- If equity features (22) cannot be computed → skip symbol entirely.
- If option features missing → fill with neutral defaults (does not block).
- DB stale > 6h → emit STALE alert but continue (degraded).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import redis

# ============================================================================
# CONFIG
# ============================================================================
ARES_HOME       = os.getenv("ARES_HOME", "/home/ubuntu/ares_current")
SCHEMA_V1_PATH  = os.getenv("FEAT_V1_SCHEMA",    f"{ARES_HOME}/models/xgb_feature_cols.json")
SCHEMA_V2_PATH  = os.getenv("FEAT_V2_SCHEMA",    f"{ARES_HOME}/models/xgb_feature_cols_v2.json")
DB_PATH         = os.getenv("FEAT_V1_DB_PATH",   "/home/ubuntu/ares_x_v11_0.db")
SYMBOLS_PATH    = os.getenv("FEAT_V1_SYMBOLS",   f"{ARES_HOME}/config/active_symbols.json")

REDIS_HOST      = os.getenv("REDIS_HOST",        "127.0.0.1")
REDIS_PORT      = int(os.getenv("REDIS_PORT",    "6379"))
REDIS_USERNAME  = os.getenv("REDIS_USERNAME",    "ares-admin")
REDIS_PASSWORD  = os.getenv("REDIS_PASSWORD",    "")
REDIS_SSL       = os.getenv("REDIS_SSL",         "true").lower() == "true"

KEY_PREFIX_V1   = "feat:v1:"
KEY_PREFIX_V2   = "feat:v2:"
KEY_TTL         = int(os.getenv("FEAT_V1_TTL",    "600"))
CYCLE_SEC       = int(os.getenv("FEAT_V1_CYCLE",  "300"))
HEARTBEAT_KEY   = "feat:v2:heartbeat"
STATUS_KEY      = "feat:v2:status"
HALT_KEY        = "feat:v2:halt"

OPT_KEY_PREFIX  = "premium:options:"
OPT_STALE_S     = int(os.getenv("FEAT_V2_OPT_STALE_S", "1800"))  # 30 min
MAX_DB_STALE_H  = float(os.getenv("FEAT_V1_MAX_STALE_H", "6"))
SPY_LOOKBACK    = 252

# Neutral defaults (used when option payload is missing/stale).
# These are sample-distribution medians; chosen so model behaves like v1
# when option features are unavailable.
OPT_DEFAULTS: Dict[str, float] = {
    "opt_pcr_volume":     1.0,
    "opt_pcr_oi":         1.0,
    "opt_iv_skew_25d":    0.0,
    "opt_atm_mid_iv":     0.18,
    "opt_zero_dte_share": 0.0,
}
OPT_FEATURES = list(OPT_DEFAULTS.keys())

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [feat_v2] %(message)s")
log = logging.getLogger("feat_v2")


# ============================================================================
# SCHEMA
# ============================================================================
EXPECTED_V1: List[str] = []
EXPECTED_V2: List[str] = []

def _load_schema(path: str, expected_n: int) -> List[str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"schema_not_found: {path}")
    cols = json.loads(p.read_text())
    if not isinstance(cols, list) or len(cols) != expected_n:
        raise ValueError(f"schema {path} must have {expected_n} cols, got {len(cols) if isinstance(cols,list) else type(cols).__name__}")
    return cols


# ============================================================================
# REDIS
# ============================================================================
def _connect_redis() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT,
        username=REDIS_USERNAME, password=REDIS_PASSWORD or None,
        ssl=REDIS_SSL, decode_responses=True,
        socket_timeout=5, socket_connect_timeout=3,
    )


# ============================================================================
# EQUITY FEATURES (identical to v1)
# ============================================================================
def _rsi(closes: np.ndarray, period: int = 14) -> Optional[float]:
    n = len(closes)
    if n < period + 1:
        return None
    diffs = np.diff(closes)
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
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
    return float(rets.std(ddof=0) * np.sqrt(252))


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


def compute_equity_features(symbol: str, sym_data: Dict, spy_data: Dict, vix_data: Dict) -> Optional[Dict[str, float]]:
    closes = np.array(sym_data["closes"], dtype=np.float64)
    volumes = np.array(sym_data["volumes"], dtype=np.float64)
    highs = np.array(sym_data["highs"], dtype=np.float64)
    lows = np.array(sym_data["lows"], dtype=np.float64)
    spy_closes = np.array(spy_data["closes"], dtype=np.float64)
    vix_closes = np.array(vix_data["closes"], dtype=np.float64)

    if len(closes) < 252 or len(spy_closes) < 22 or len(vix_closes) < 6:
        return None

    feats: Dict[str, Optional[float]] = {}
    feats["ret_1d"]   = _ret(closes, 1)
    feats["ret_5d"]   = _ret(closes, 5)
    feats["ret_10d"]  = _ret(closes, 10)
    feats["ret_21d"]  = _ret(closes, 21)
    feats["vol_5d"]   = _vol(closes, 5)
    feats["vol_10d"]  = _vol(closes, 10)
    feats["vol_20d"]  = _vol(closes, 20)
    feats["rsi_14"]   = _rsi(closes, 14)
    feats["bb_position_20"] = _bollinger_position(closes, 20)
    feats["vol_ratio_5d"]   = _vol_ratio(volumes, 5)
    feats["vol_ratio_20d"]  = _vol_ratio(volumes, 20)
    high_52w = float(highs[-252:].max())
    low_52w  = float(lows[-252:].min())
    last     = float(closes[-1])
    feats["pct_from_high_52w"] = (last - high_52w) / high_52w if high_52w > 0 else None
    feats["pct_from_low_52w"]  = (last - low_52w)  / low_52w  if low_52w  > 0 else None
    vwap5 = _vwap(closes, volumes, 5)
    feats["price_vs_vwap"] = (last / vwap5 - 1) if vwap5 else None
    feats["spy_ret_1d"]   = _ret(spy_closes, 1)
    feats["spy_ret_5d"]   = _ret(spy_closes, 5)
    feats["spy_vol_20d"]  = _vol(spy_closes, 20)
    feats["vix_level"]    = float(vix_closes[-1])
    feats["vix_change_5d"]= _ret(vix_closes, 5)
    spy_ret_21 = _ret(spy_closes, 21)
    sym_ret_21 = _ret(closes, 21)
    feats["relative_strength_21d"] = (sym_ret_21 - spy_ret_21) if (sym_ret_21 is not None and spy_ret_21 is not None) else None
    feats["max_drawdown_21d"] = _max_dd_21d(closes)
    vwap5_prev = _vwap(closes[:-5], volumes[:-5], 5) if len(closes) > 10 else None
    feats["vwap_ret_5d"] = (vwap5 / vwap5_prev - 1) if (vwap5 and vwap5_prev) else None

    for k, v in feats.items():
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return None

    if set(feats.keys()) != set(EXPECTED_V1):
        log.error(f"{symbol}: schema_v1 mismatch missing={set(EXPECTED_V1) - set(feats.keys())} extra={set(feats.keys()) - set(EXPECTED_V1)}")
        return None

    return {k: float(feats[k]) for k in EXPECTED_V1}


# ============================================================================
# OPTION FEATURE LOOKUP (premium:options:{SYM}) — fail-soft to defaults
# ============================================================================
def fetch_option_features(r: redis.Redis, symbol: str) -> Tuple[Dict[str, float], str]:
    """Returns (features_dict, source_label)."""
    key = f"{OPT_KEY_PREFIX}{symbol}"
    try:
        raw = r.get(key)
    except Exception as e:
        log.debug(f"{symbol}: opt redis get failed {e}")
        return dict(OPT_DEFAULTS), "default_redis_error"
    if not raw:
        return dict(OPT_DEFAULTS), "default_missing"
    try:
        obj = json.loads(raw)
    except Exception:
        return dict(OPT_DEFAULTS), "default_parse_error"

    ts = int(obj.get("ts") or 0)
    if ts and (int(time.time()) - ts) > OPT_STALE_S:
        return dict(OPT_DEFAULTS), "default_stale"

    src_feats = (obj.get("features") or {})
    out: Dict[str, float] = {}
    used_default = 0
    for k in OPT_FEATURES:
        v = src_feats.get(k)
        if v is None:
            out[k] = OPT_DEFAULTS[k]
            used_default += 1
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            out[k] = OPT_DEFAULTS[k]
            used_default += 1
            continue
        if not np.isfinite(v):
            out[k] = OPT_DEFAULTS[k]
            used_default += 1
            continue
        out[k] = v
    src = "premium:options" if used_default == 0 else f"partial_default_{used_default}"
    return out, src


# ============================================================================
# DB
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
    rows = list(reversed(rows))
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
    spy_dt = dt.datetime.fromisoformat(spy_last_date)
    hours = (dt.datetime.utcnow() - spy_dt).total_seconds() / 3600
    return (hours <= MAX_DB_STALE_H, hours)


def run_cycle(r: redis.Redis, conn: sqlite3.Connection, symbols: List[str], cycle_n: int) -> Dict:
    started = time.time()
    spy = _load_symbol_history(conn, "SPY")
    if spy is None:
        log.error("SPY unavailable — HALT")
        r.set(HALT_KEY, json.dumps({"reason": "spy_unavailable", "ts": dt.datetime.utcnow().isoformat() + "Z"}), ex=KEY_TTL)
        return {"status": "HALTED", "published_v1": 0, "published_v2": 0, "errors": 0}
    vix = _load_symbol_history(conn, "VIX") or _load_symbol_history(conn, "^VIX")
    if vix is None:
        log.error("VIX unavailable — HALT")
        r.set(HALT_KEY, json.dumps({"reason": "vix_unavailable", "ts": dt.datetime.utcnow().isoformat() + "Z"}), ex=KEY_TTL)
        return {"status": "HALTED", "published_v1": 0, "published_v2": 0, "errors": 0}

    is_fresh, hours_stale = _check_freshness(spy["last_date"])
    if not is_fresh:
        log.warning(f"DB stale {hours_stale:.1f}h > {MAX_DB_STALE_H}h")
        r.set(HALT_KEY, json.dumps({"reason": "db_stale", "hours_stale": round(hours_stale, 2),
                                    "spy_last_date": spy["last_date"],
                                    "ts": dt.datetime.utcnow().isoformat() + "Z"}), ex=KEY_TTL)
    else:
        r.delete(HALT_KEY)

    pub_v1 = pub_v2 = errors = 0
    opt_present = opt_default = 0
    feature_diversity: List[Dict[str, float]] = []
    pipe = r.pipeline()

    for sym in symbols:
        try:
            sym_hist = _load_symbol_history(conn, sym)
            if sym_hist is None:
                continue
            eq_feats = compute_equity_features(sym, sym_hist, spy, vix)
            if eq_feats is None:
                errors += 1
                continue

            opt_feats, opt_src = fetch_option_features(r, sym)
            if opt_src.startswith("default"):
                opt_default += 1
            else:
                opt_present += 1

            now_iso = dt.datetime.utcnow().isoformat() + "Z"

            payload_v1 = {
                "symbol": sym, "features": eq_feats,
                "source_date": sym_hist["last_date"], "computed_at": now_iso,
                "schema_version": "v1", "n_features": len(EXPECTED_V1),
            }
            v2_feats = dict(eq_feats)
            for k in OPT_FEATURES:
                v2_feats[k] = opt_feats[k]
            payload_v2 = {
                "symbol": sym, "features": v2_feats,
                "source_date": sym_hist["last_date"], "computed_at": now_iso,
                "schema_version": "v2", "n_features": len(EXPECTED_V2),
                "opt_source": opt_src,
            }
            pipe.set(f"{KEY_PREFIX_V1}{sym}", json.dumps(payload_v1), ex=KEY_TTL)
            pipe.set(f"{KEY_PREFIX_V2}{sym}", json.dumps(payload_v2), ex=KEY_TTL)
            pub_v1 += 1
            pub_v2 += 1
            feature_diversity.append(eq_feats)
        except Exception as e:
            errors += 1
            log.warning(f"{sym}: error {e}")
    pipe.execute()

    elapsed = time.time() - started
    diversity_ok = False
    if len(feature_diversity) > 5:
        rets = [f["ret_1d"] for f in feature_diversity]
        diversity_ok = bool(np.std(rets) > 1e-6)
    summary = {
        "cycle":         cycle_n,
        "published_v1":  pub_v1,
        "published_v2":  pub_v2,
        "errors":        errors,
        "opt_present":   opt_present,
        "opt_default":   opt_default,
        "elapsed_sec":   round(elapsed, 3),
        "spy_last_date": spy["last_date"],
        "hours_stale":   round(hours_stale, 2),
        "status":        "OK" if (is_fresh and diversity_ok and pub_v2 > 0) else ("STALE" if not is_fresh else "DEGRADED"),
        "diversity_ok":  diversity_ok,
        "ts":            dt.datetime.utcnow().isoformat() + "Z",
    }
    r.set(STATUS_KEY,    json.dumps(summary), ex=KEY_TTL * 3)
    r.set(HEARTBEAT_KEY, str(int(time.time())), ex=KEY_TTL * 3)
    log.info(
        f"cycle={cycle_n} pub_v1={pub_v1} pub_v2={pub_v2} err={errors} "
        f"opt_present={opt_present} opt_default={opt_default} "
        f"elapsed={elapsed:.2f}s status={summary['status']} stale={hours_stale:.1f}h"
    )
    return summary


# ============================================================================
# ENTRY
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
            log.warning(f"active_symbols.json parse failed: {e}")
    return [
        "SPY","QQQ","IWM","DIA","VIXY","TLT","HYG","LQD","GLD","USO",
        "AAPL","MSFT","AMZN","GOOGL","META","NVDA","TSLA","BRK.B","JPM","V",
        "UNH","XOM","JNJ","PG","HD","MA","CVX","ABBV","PFE","KO","BAC","WMT",
        "MRK","COST","DIS","NFLX","ADBE","CRM","TMO","CSCO","PEP","ABT","WFC",
        "MCD","ACN","DHR","TXN","NKE","UPS","ORCL","RTX","BA","CAT","COP",
        "GE","GS","MS","CMCSA","TGT","REGN","AMD","ASML","CL","PM","SMCI",
        "INTC","MU","QCOM","ARM","PLTR","MO","SYY","CRWD",
    ]


def _sigterm(sig, frame):
    log.info("SIGTERM received — exiting")
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)
    global EXPECTED_V1, EXPECTED_V2
    EXPECTED_V1 = _load_schema(SCHEMA_V1_PATH, 22)
    EXPECTED_V2 = _load_schema(SCHEMA_V2_PATH, 27)
    # Sanity: v2 must equal v1 + 5 option features (in order).
    if EXPECTED_V2[:22] != EXPECTED_V1 or EXPECTED_V2[22:] != OPT_FEATURES:
        raise SystemExit(f"schema mismatch: v2 must extend v1 with {OPT_FEATURES}")
    log.info(f"schema_v1={len(EXPECTED_V1)} schema_v2={len(EXPECTED_V2)}")

    r = _connect_redis()
    r.ping()
    log.info(f"redis ok host={REDIS_HOST}:{REDIS_PORT}")

    symbols = _load_symbols()
    log.info(f"universe={len(symbols)} symbols cycle={CYCLE_SEC}s")

    if not Path(DB_PATH).exists() and not os.path.islink(DB_PATH):
        raise SystemExit(f"DB not found: {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    log.info(f"db ok: {DB_PATH}")

    cycle = 0
    while True:
        cycle += 1
        try:
            run_cycle(r, conn, symbols, cycle)
        except Exception as e:
            log.exception(f"cycle {cycle} fatal: {e}")
        time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    main()
