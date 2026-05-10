#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ops-market-monitor — Market Condition Monitor
==============================================

역할:
  1. 시장시간(미국 주식) 계산 → ctx:market:is_open
  2. 주요 ETF/가격 데이터 freshness 체크 (price:SPY, price:QQQ, price:VIX, price:IEF, price:HYG)
     - stale (>MAX_AGE) 발견 시 경고
  3. regime 변화 감지 (regime:final:current / ms:regime:current 바뀌었는지)
  4. VIX 임계 경고 (>30 → ctx:market:vix_warn)

Redis Writes:
  ctx:market:is_open (TTL=180)
  ctx:market:stale_count (TTL=180)
  ctx:market:regime_state (TTL=180)
  ctx:market:regime_change_ts (no TTL)
  ctx:market:vix_warn (TTL=180, only when breach)
  ctx:ops_market_monitor:health

Invariant tags: ares.ops.market
Expected by registry: /home/ubuntu/ares_current/ops/ops_market_monitor.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, time as dtime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ares_common import get_redis, run_loop, heartbeat, xadd_event, now_iso, safe_get_str, read_regime  # noqa

SERVICE = "ops-market-monitor"
INTERVAL = int(os.environ.get("OPS_MARKET_INTERVAL_SEC", "60"))

MONITORED_SYMBOLS = ["SPY", "QQQ", "VIX", "IEF", "HYG", "DIA", "IWM"]
MAX_AGE_SEC = int(os.environ.get("OPS_MARKET_MAX_AGE_SEC", "900"))
VIX_WARN_LEVEL = float(os.environ.get("OPS_MARKET_VIX_WARN", "30.0"))
VIX_CRIT_LEVEL = float(os.environ.get("OPS_MARKET_VIX_CRIT", "40.0"))


def is_us_market_hours() -> bool:
    """US equity market hours: Mon-Fri 13:30–20:00 UTC. DST already baked in UTC shift (RTH only)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 810 <= hm <= 1200  # 13:30 ~ 20:00 UTC


def _parse_iso(s):
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _age_sec(iso_ts):
    dt = _parse_iso(iso_ts)
    if dt is None:
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _get_price_and_ts(rc, sym: str):
    """Accept multiple formats (TYPE-safe):
    - price:SYM as JSON {price, ts, source}
    - price:SYM as float string, with companion price:SYM:ts
    - price:SYM as HASH {price, ts}
    """
    try:
        t = rc.type(f"price:{sym}")
    except Exception:
        t = "none"
    if t == "hash":
        try:
            h = rc.hgetall(f"price:{sym}") or {}
            p = h.get("price") or h.get("last") or h.get("close")
            return (float(p) if p is not None else None), (h.get("ts") or h.get("timestamp"))
        except Exception:
            return None, None
    if t != "string":
        return None, None
    raw = rc.get(f"price:{sym}")
    if not raw:
        return None, None
    # Try JSON
    try:
        obj = json.loads(raw)
        price = obj.get("price") or obj.get("last") or obj.get("close")
        ts = obj.get("ts") or obj.get("timestamp") or obj.get("updated_at")
        return (float(price) if price is not None else None, ts)
    except Exception:
        pass
    # Plain string
    try:
        price = float(raw)
    except Exception:
        price = None
    ts = safe_get_str(rc, f"price:{sym}:ts")
    return price, ts


def tick(rc, logger):
    open_now = is_us_market_hours()
    rc.set("ctx:market:is_open", "1" if open_now else "0", ex=INTERVAL * 3)

    stale = []
    prices = {}
    for sym in MONITORED_SYMBOLS:
        price, ts = _get_price_and_ts(rc, sym)
        age = _age_sec(ts) if ts else float("inf")
        prices[sym] = {"price": price, "ts": ts, "age_sec": (None if age == float("inf") else round(age, 1))}
        if open_now and (price is None or age > MAX_AGE_SEC):
            stale.append(sym)
    rc.set("ctx:market:stale_count", str(len(stale)), ex=INTERVAL * 3)

    if stale and open_now:
        xadd_event(rc, "guardian:events", "WARN", SERVICE,
                   "PRICE_STALE", {"symbols": stale, "max_age_sec": MAX_AGE_SEC})

    # regime change detection — SSOT HASH at regime:final:current (TYPE-safe)
    regime_obj = read_regime(rc)
    regime_key_state = None
    try:
        if regime_obj:
            # hash schema: r15_regime/ms_regime/final_mult; JSON: regime/state/label
            regime_key_state = (
                regime_obj.get("r15_regime")
                or regime_obj.get("regime")
                or regime_obj.get("ms_regime")
                or regime_obj.get("state")
                or regime_obj.get("label")
            )
    except Exception:
        regime_key_state = None

    prev = safe_get_str(rc, "ctx:market:regime_state")
    if regime_key_state and regime_key_state != prev:
        rc.set("ctx:market:regime_state", regime_key_state, ex=INTERVAL * 3)
        rc.set("ctx:market:regime_change_ts", now_iso())
        xadd_event(rc, "guardian:events", "INFO", SERVICE,
                   "REGIME_CHANGE", {"from": prev, "to": regime_key_state})
    else:
        if regime_key_state:
            rc.set("ctx:market:regime_state", regime_key_state, ex=INTERVAL * 3)

    # VIX
    vix_price = prices.get("VIX", {}).get("price")
    if vix_price is not None:
        if vix_price >= VIX_CRIT_LEVEL:
            rc.set("ctx:market:vix_warn",
                   json.dumps({"level": "CRIT", "vix": vix_price, "ts": now_iso()}),
                   ex=INTERVAL * 3)
            xadd_event(rc, "guardian:events", "CRIT", SERVICE,
                       "VIX_CRIT", {"vix": vix_price})
        elif vix_price >= VIX_WARN_LEVEL:
            rc.set("ctx:market:vix_warn",
                   json.dumps({"level": "WARN", "vix": vix_price, "ts": now_iso()}),
                   ex=INTERVAL * 3)
            xadd_event(rc, "guardian:events", "WARN", SERVICE,
                       "VIX_WARN", {"vix": vix_price})
        else:
            try:
                rc.delete("ctx:market:vix_warn")
            except Exception:
                pass

    heartbeat(rc, "ctx:ops_market_monitor:health", {
        "is_open": open_now,
        "stale_symbols": stale,
        "regime": regime_key_state,
        "vix": vix_price,
    }, ttl=INTERVAL * 3)

    logger.info("open=%s stale=%s regime=%s vix=%s",
                open_now, stale, regime_key_state, vix_price)


if __name__ == "__main__":
    sys.exit(run_loop(SERVICE, INTERVAL, tick))
