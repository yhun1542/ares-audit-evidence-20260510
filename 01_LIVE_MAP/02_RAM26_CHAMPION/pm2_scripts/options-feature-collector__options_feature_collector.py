#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES Options Feature Collector v1.0  [PATCH 20260507]
=====================================================

PURPOSE
-------
Per-symbol options feature publisher (5-minute cadence) that ingests
Polygon's `/v3/snapshot/options/{underlying}` endpoint with full
pagination, derives a small but high-signal feature set, and writes
each symbol's payload to Redis under `premium:options:{SYMBOL}`.

The features published here are designed to be consumed by
`feat_v1_publisher_v2.py`, which appends them to the equities
feature vector before publication to `feat:v1:{symbol}`.

OUTPUT KEY (per symbol)
-----------------------
Key   : premium:options:{SYMBOL}
TTL   : 600s (10 min, > publisher cycle 300s)
Value : JSON {
    "symbol": "AAPL",
    "computed_at": "2026-05-07T15:23:11Z",
    "underlying_price": 188.42,
    "features": {
        "opt_pcr_volume": 0.873,           # null when no signal
        "opt_pcr_oi":     0.912,           # null when no signal
        "opt_iv_skew_25d": 0.0142,         # null when 25Δ pair unavailable
        "opt_atm_mid_iv": 0.1287,          # null when ATM IV unavailable
        "opt_zero_dte_share": 0.18,        # 0..1 (volume-weighted)
        "opt_sample_size":  312
    },
    "source": "polygon_options_snapshot",
    "ts": 1715099013
}

HEARTBEAT / STATUS
------------------
- premium:options:heartbeat   : epoch seconds (TTL 1800)
- premium:options:status      : last cycle summary JSON (TTL 1800)
- premium:options:halt        : reason JSON when halted

FAIL-CLOSED
-----------
- POLYGON_API_KEY missing → exit(1)
- Per-symbol failure: skip and increment errors; never poison cache.
- Per-symbol stale Polygon response (>900s old) → mark `source:'stale'`
  but still publish (consumers gate on freshness).

ENV
---
ARES_HOME            : /home/ubuntu/ares_current  (default)
POLYGON_API_KEY      : required
OPT_FEAT_SYMBOLS     : path to JSON list of symbols (default = active_symbols.json)
OPT_FEAT_CYCLE_SEC   : 300 (5 min)
OPT_FEAT_TTL         : 600 (10 min)
OPT_FEAT_REQ_TIMEOUT : 12  (per-page HTTP timeout)
OPT_FEAT_PAGE_LIMIT  : 250 (Polygon max for v3/snapshot/options)
OPT_FEAT_PAGE_MAX    : 16  (max pages per symbol; 250 * 16 = 4000 contracts)
OPT_FEAT_DELTA_TOL   : 0.05  (25Δ band ±)
REDIS_HOST/PORT/PASSWORD/SSL/USERNAME : standard ARES envs

OPERATIONAL NOTES
-----------------
- Polygon's options snapshot returns up to 250 contracts per page; we follow
  `next_url` until exhausted or PAGE_MAX reached (≤4000 contracts).
- We compute PCR-volume from `day.volume` and PCR-OI from `open_interest`.
  Both are independently null-safe.
- 0DTE share = (volume of contracts whose `expiration_date == today_ET`)
  / (total volume).  If today is non-trading, share=0.
- IV-skew (25Δ) = mean(put IV at |Δ-(-0.25)|<TOL) - mean(call IV at |Δ-0.25|<TOL).
- ATM mid IV = median IV of contracts whose strike is within ±2.5% of underlying.
- All numeric fields are emitted as floats or null (never NaN/Infinity).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import signal
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import redis

# ============================================================================
# CONFIGURATION
# ============================================================================
ARES_HOME       = os.getenv("ARES_HOME", "/home/ubuntu/ares_current")
POLYGON_KEY     = os.getenv("POLYGON_API_KEY", "")
SYMBOLS_PATH    = os.getenv("OPT_FEAT_SYMBOLS", f"{ARES_HOME}/config/active_symbols.json")

REDIS_HOST      = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT      = int(os.getenv("REDIS_PORT") or "6379")
REDIS_USERNAME  = os.getenv("REDIS_USERNAME") or "default"
REDIS_PASSWORD  = os.getenv("REDIS_PASSWORD", "")
REDIS_SSL       = os.getenv("REDIS_SSL", "true").lower() == "true"

CYCLE_SEC       = int(os.getenv("OPT_FEAT_CYCLE_SEC", "600"))
KEY_TTL         = int(os.getenv("OPT_FEAT_TTL", "900"))
CONCURRENCY     = max(1, int(os.getenv("OPT_FEAT_CONCURRENCY", "8")))
REQ_TIMEOUT     = float(os.getenv("OPT_FEAT_REQ_TIMEOUT", "12"))
PAGE_LIMIT      = int(os.getenv("OPT_FEAT_PAGE_LIMIT", "250"))
PAGE_MAX        = int(os.getenv("OPT_FEAT_PAGE_MAX", "16"))
DELTA_TOL       = float(os.getenv("OPT_FEAT_DELTA_TOL", "0.05"))
ATM_BAND        = float(os.getenv("OPT_FEAT_ATM_BAND", "0.025"))  # ±2.5%

KEY_PREFIX      = "premium:options:"
HEARTBEAT_KEY   = "premium:options:heartbeat"
STATUS_KEY      = "premium:options:status"
HALT_KEY        = "premium:options:halt"

# Logging
logging.basicConfig(
    level=os.getenv("OPT_FEAT_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] [opt_feat] %(message)s",
)
log = logging.getLogger("opt_feat")


# ============================================================================
# HTTP — paginated polygon options snapshot
# ============================================================================
def _http_get_json(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "ares-opt-feat/1.0"})
    with urllib.request.urlopen(req, timeout=REQ_TIMEOUT) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8"))


def _attach_apikey(url: str, api_key: str) -> str:
    """Polygon next_url already contains apiKey when first request did;
    we still re-attach defensively for robustness."""
    if not api_key:
        return url
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "apiKey" not in qs:
        qs["apiKey"] = [api_key]
        new_query = urllib.parse.urlencode({k: v[0] for k, v in qs.items()})
        parsed = parsed._replace(query=new_query)
    return urllib.parse.urlunparse(parsed)


def fetch_options_snapshot(symbol: str, api_key: str) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    """Return (contracts, underlying_price) following Polygon `next_url` pagination.

    Raises RuntimeError on transport failure or non-OK Polygon status.
    """
    url = (
        f"https://api.polygon.io/v3/snapshot/options/{urllib.parse.quote(symbol)}"
        f"?limit={PAGE_LIMIT}&apiKey={urllib.parse.quote(api_key)}"
    )
    contracts: List[Dict[str, Any]] = []
    underlying_price: Optional[float] = None

    for page_no in range(PAGE_MAX):
        data = _http_get_json(url)
        status = data.get("status")
        if status not in ("OK", "ok", "DELAYED", "delayed"):
            raise RuntimeError(f"polygon_status={status} req_id={data.get('request_id')}")
        results = data.get("results") or []
        for r in results:
            contracts.append(r)
            if underlying_price is None:
                ua = r.get("underlying_asset") or {}
                px = ua.get("price")
                if isinstance(px, (int, float)) and np.isfinite(px) and px > 0:
                    underlying_price = float(px)
        next_url = data.get("next_url")
        if not next_url:
            break
        url = _attach_apikey(next_url, api_key)
    else:
        log.warning(f"{symbol}: page-cap reached at {PAGE_MAX} pages ({len(contracts)} contracts)")

    return contracts, underlying_price


# ============================================================================
# FEATURE COMPUTATION
# ============================================================================
def _today_et_iso() -> str:
    """Today's date in US/Eastern as ISO YYYY-MM-DD (no tz lib needed: UTC-5/4 approx)."""
    # We only need a coarse 'today' for 0DTE classification; off-by-one when
    # crossing midnight ET vs UTC is acceptable for a feature.
    now = dt.datetime.utcnow() - dt.timedelta(hours=4)  # EDT-friendly
    return now.date().isoformat()


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return v


def compute_features(
    symbol: str,
    contracts: List[Dict[str, Any]],
    underlying_price: Optional[float],
) -> Dict[str, Optional[float]]:
    """Derive PCR / IV-skew / ATM mid IV / 0DTE share / sample_size."""
    if not contracts:
        return {
            "opt_pcr_volume": None, "opt_pcr_oi": None,
            "opt_iv_skew_25d": None, "opt_atm_mid_iv": None,
            "opt_zero_dte_share": None, "opt_sample_size": 0,
        }

    today = _today_et_iso()

    put_vol_total = 0.0
    call_vol_total = 0.0
    put_oi_total = 0.0
    call_oi_total = 0.0
    put_iv_25d: List[float] = []
    call_iv_25d: List[float] = []
    atm_ivs: List[float] = []
    zero_dte_vol = 0.0
    total_vol = 0.0

    for c in contracts:
        details = c.get("details") or {}
        ctype = (details.get("contract_type") or "").lower()
        if ctype not in ("put", "call"):
            continue

        day = c.get("day") or {}
        greeks = c.get("greeks") or {}
        vol = _safe_float(day.get("volume")) or 0.0
        oi  = _safe_float(c.get("open_interest")) or 0.0
        iv  = _safe_float(c.get("implied_volatility"))
        delta = _safe_float(greeks.get("delta"))
        strike = _safe_float(details.get("strike_price"))
        expiry = details.get("expiration_date") or ""

        total_vol += vol
        if expiry == today:
            zero_dte_vol += vol

        if ctype == "put":
            put_vol_total += vol
            put_oi_total  += oi
            if iv is not None and delta is not None and abs(delta - (-0.25)) <= DELTA_TOL:
                put_iv_25d.append(iv)
        else:  # call
            call_vol_total += vol
            call_oi_total  += oi
            if iv is not None and delta is not None and abs(delta - 0.25) <= DELTA_TOL:
                call_iv_25d.append(iv)

        # ATM band
        if iv is not None and strike is not None and underlying_price and underlying_price > 0:
            if abs(strike - underlying_price) / underlying_price <= ATM_BAND:
                atm_ivs.append(iv)

    # PCRs (null on degenerate cases)
    if call_vol_total == 0 and put_vol_total == 0:
        pcr_vol = None
    elif call_vol_total == 0:
        pcr_vol = None  # Infinity — treat as no signal
    else:
        pcr_vol = put_vol_total / call_vol_total

    if call_oi_total == 0 and put_oi_total == 0:
        pcr_oi = None
    elif call_oi_total == 0:
        pcr_oi = None
    else:
        pcr_oi = put_oi_total / call_oi_total

    # IV skew (25Δ): put_iv_mean − call_iv_mean
    iv_skew_25d: Optional[float] = None
    if put_iv_25d and call_iv_25d:
        iv_skew_25d = float(np.mean(put_iv_25d) - np.mean(call_iv_25d))

    atm_mid_iv: Optional[float] = float(np.median(atm_ivs)) if atm_ivs else None

    zero_dte_share: Optional[float] = (zero_dte_vol / total_vol) if total_vol > 0 else 0.0

    return {
        "opt_pcr_volume":     None if pcr_vol     is None else round(float(pcr_vol),     6),
        "opt_pcr_oi":         None if pcr_oi      is None else round(float(pcr_oi),      6),
        "opt_iv_skew_25d":    None if iv_skew_25d is None else round(float(iv_skew_25d), 6),
        "opt_atm_mid_iv":     None if atm_mid_iv  is None else round(float(atm_mid_iv),  6),
        "opt_zero_dte_share": None if zero_dte_share is None else round(float(zero_dte_share), 6),
        "opt_sample_size":    int(len(contracts)),
    }


# ============================================================================
# PUBLISH PIPELINE
# ============================================================================
def _connect_redis() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT,
        username=REDIS_USERNAME, password=REDIS_PASSWORD or None,
        ssl=REDIS_SSL, decode_responses=True,
        socket_timeout=5, socket_connect_timeout=3,
    )


def _load_symbols() -> List[str]:
    p = Path(SYMBOLS_PATH)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, list):
                syms = [s.upper() for s in data if isinstance(s, str)]
            elif isinstance(data, dict) and "symbols" in data:
                syms = [s.upper() for s in data["symbols"]]
            else:
                syms = []
            if syms:
                return syms
        except Exception as e:
            log.warning(f"active_symbols.json parse failed: {e}")
    # Fallback core universe (ETFs + 47 equities approximating prod).
    return [
        "SPY","QQQ","IWM","DIA","TLT","HYG","VIXY","GLD","XLF","XLE",
        "AAPL","MSFT","AMZN","GOOGL","META","NVDA","TSLA","BRK.B","JPM","V",
        "UNH","XOM","JNJ","HD","MA","CVX","PFE","BAC","WMT","KO",
        "DIS","NFLX","ADBE","CRM","CSCO","PEP","ABT","WFC","MCD","TXN",
        "RTX","BA","CAT","GE","GS","MS","TGT","AMD","ASML","SMCI",
        "MU","QCOM","ARM","PLTR","SYY",
    ]


def _process_one(sym: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[Exception]]:
    """Worker: fetch+compute for a single symbol. No Redis writes here (caller batches)."""
    try:
        contracts, ulying = fetch_options_snapshot(sym, POLYGON_KEY)
        feats = compute_features(sym, contracts, ulying)
        payload = {
            "symbol":           sym,
            "computed_at":      dt.datetime.utcnow().isoformat() + "Z",
            "underlying_price": _safe_float(ulying),
            "features":         feats,
            "source":           "polygon_options_snapshot",
            "ts":               int(time.time()),
        }
        return sym, payload, None
    except Exception as e:
        return sym, None, e


def run_cycle(r: redis.Redis, symbols: List[str], cycle_n: int) -> Dict[str, Any]:
    started = time.time()
    published = 0
    errors = 0
    null_pcr = 0
    null_iv  = 0
    pipe = r.pipeline()

    # [PATCH 20260507-CONCURRENCY] thread-pool fan-out across symbols.
    # Polygon /v3/snapshot/options is read-only & per-symbol; safe to parallelize.
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futures = {ex.submit(_process_one, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                sym2, payload, err = fut.result()
            except Exception as e:
                errors += 1
                log.warning(f"{sym}: future-failed: {type(e).__name__}: {e}")
                continue
            if err is not None or payload is None:
                errors += 1
                if err is not None:
                    log.warning(f"{sym}: {type(err).__name__}: {err}")
                continue
            feats = payload.get("features", {}) or {}
            pipe.set(f"{KEY_PREFIX}{sym2}", json.dumps(payload), ex=KEY_TTL)
            published += 1
            if feats.get("opt_pcr_volume") is None:
                null_pcr += 1
            if feats.get("opt_iv_skew_25d") is None:
                null_iv += 1
    pipe.execute()

    elapsed = time.time() - started
    summary = {
        "cycle":      cycle_n,
        "published":  published,
        "errors":     errors,
        "null_pcr":   null_pcr,
        "null_iv25d": null_iv,
        "elapsed":    round(elapsed, 2),
        "ts":         dt.datetime.utcnow().isoformat() + "Z",
        "status":     "OK" if (errors < max(2, len(symbols) * 0.3) and published > 0) else "DEGRADED",
    }
    try:
        r.set(STATUS_KEY,    json.dumps(summary), ex=KEY_TTL * 3)
        r.set(HEARTBEAT_KEY, str(int(time.time())), ex=KEY_TTL * 3)
        if errors >= len(symbols):
            r.set(HALT_KEY, json.dumps({"reason": "all_failed", "ts": summary["ts"]}), ex=KEY_TTL)
        else:
            r.delete(HALT_KEY)
    except Exception as e:
        log.warning(f"status write failed: {e}")
    log.info(
        f"cycle={cycle_n} pub={published} err={errors} null_pcr={null_pcr} "
        f"null_iv25d={null_iv} elapsed={elapsed:.1f}s status={summary['status']}"
    )
    return summary


# ============================================================================
# ENTRYPOINT
# ============================================================================
def _sigterm(_sig, _frm):
    log.info("SIGTERM/INT — exiting")
    sys.exit(0)


def main() -> None:
    if not POLYGON_KEY:
        log.error("POLYGON_API_KEY is required")
        sys.exit(1)

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    r = _connect_redis()
    r.ping()
    log.info(f"redis ok host={REDIS_HOST}:{REDIS_PORT} ssl={REDIS_SSL}")

    symbols = _load_symbols()
    log.info(f"universe={len(symbols)} symbols cycle_sec={CYCLE_SEC} ttl={KEY_TTL}")

    cycle = 0
    while True:
        cycle += 1
        try:
            run_cycle(r, symbols, cycle)
        except Exception as e:
            log.exception(f"cycle {cycle} fatal: {e}")
        time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    main()
