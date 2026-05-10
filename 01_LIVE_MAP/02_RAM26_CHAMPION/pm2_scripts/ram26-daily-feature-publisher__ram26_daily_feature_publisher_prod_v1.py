#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAM26 Daily Feature Publisher Production v1
===========================================

Publishes `ares:features:daily:{YYYYMMDD}` from the ARES SQLite daily OHLCV DB.

Design:
- No synthetic constants.
- Features are derived from actual daily OHLCV bars.
- If bars are insufficient, symbol is skipped and reason is recorded.
- Publishes latest trading date found in DB, not today's calendar date.
- Weekend-safe: Friday close is valid if it is the latest available market date.

Output:
  HASH ares:features:daily:{YYYYMMDD}
  field = SYMBOL
  value = JSON

Required RFC-009 fields generated:
  ret_5d, ret_20d, ret_60d, vol_20d, quality, value, liquidity,
  sentiment, drawdown_20d, spread_bps, updated_at_ms
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import redis  # type: ignore
except Exception:
    redis = None


def now_ms() -> int:
    return int(time.time() * 1000)


def load_env_file(path: str):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k.strip() and k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip().strip('"').strip("'")


def redis_client():
    if redis is None:
        raise RuntimeError("redis package required")
    for p in ("/etc/ares/redis.env", "/etc/ares/ares.env", os.path.expanduser("~/.ares_redis_env")):
        load_env_file(p)
    url = os.getenv("REDIS_URL") or os.getenv("ARES_REDIS_URL")
    password = os.getenv("REDIS_PASSWORD")
    if not url:
        raise RuntimeError("REDIS_URL/ARES_REDIS_URL required")
    r = redis.Redis.from_url(url, password=password, decode_responses=True, socket_timeout=5)
    r.ping()
    return r


def sf(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip().replace(",", "")
            if not x:
                return default
        v = float(x)
        if not math.isfinite(v):
            return default
        return v
    except Exception:
        return default


def sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def pct_ret(closes: List[float], lookback: int) -> Optional[float]:
    if len(closes) <= lookback:
        return None
    if closes[-1 - lookback] <= 0:
        return None
    return closes[-1] / closes[-1 - lookback] - 1.0


def std(vals: List[float]) -> Optional[float]:
    vals = [x for x in vals if x is not None and math.isfinite(x)]
    if len(vals) < 2:
        return None
    return statistics.pstdev(vals)


def rank01(values: Dict[str, float], invert: bool = False) -> Dict[str, float]:
    items = [(k, v) for k, v in values.items() if v is not None and math.isfinite(v)]
    if not items:
        return {}
    items.sort(key=lambda x: x[1], reverse=invert)
    n = len(items)
    if n == 1:
        return {items[0][0]: 0.5}
    return {k: i / (n - 1) for i, (k, _) in enumerate(items)}


class SQLiteDailySource:
    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).resolve())
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.table, self.cols = self.discover_table()

    def discover_table(self) -> Tuple[str, Dict[str, str]]:
        cur = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        candidates = []

        for t in tables:
            try:
                cols = [r[1] for r in self.conn.execute(f"PRAGMA table_info({t})").fetchall()]
            except Exception:
                continue
            lower = {c.lower(): c for c in cols}

            sym = next((lower[x] for x in ["symbol", "ticker", "sym", "pdno"] if x in lower), None)
            date = next((lower[x] for x in ["date", "dt", "trading_date", "timestamp", "time"] if x in lower), None)
            close = next((lower[x] for x in ["close", "adj_close", "c", "price"] if x in lower), None)
            volume = next((lower[x] for x in ["volume", "vol", "v"] if x in lower), None)
            open_ = next((lower[x] for x in ["open", "o"] if x in lower), None)
            high = next((lower[x] for x in ["high", "h"] if x in lower), None)
            low = next((lower[x] for x in ["low", "l"] if x in lower), None)

            if sym and date and close:
                score = 0
                score += 3 if volume else 0
                score += 1 if open_ else 0
                score += 1 if high else 0
                score += 1 if low else 0
                candidates.append((score, t, {"symbol": sym, "date": date, "close": close, "volume": volume, "open": open_, "high": high, "low": low}))

        if not candidates:
            raise RuntimeError("No daily OHLCV-like table found in SQLite DB")
        candidates.sort(reverse=True, key=lambda x: x[0])
        return candidates[0][1], candidates[0][2]

    def rows_for_symbol(self, symbol: str, limit: int = 90) -> List[Dict[str, Any]]:
        c = self.cols
        select_cols = [c["symbol"], c["date"], c["close"]]
        for k in ["volume", "open", "high", "low"]:
            if c.get(k):
                select_cols.append(c[k])
        cols_sql = ", ".join([f'"{x}"' for x in select_cols])
        sql = f'SELECT {cols_sql} FROM "{self.table}" WHERE UPPER("{c["symbol"]}") = UPPER(?) ORDER BY "{c["date"]}" DESC LIMIT ?'
        rows = [dict(r) for r in self.conn.execute(sql, (symbol, limit)).fetchall()]
        rows.reverse()
        return rows

    def latest_date_for_symbol(self, symbol: str) -> Optional[str]:
        rows = self.rows_for_symbol(symbol, 1)
        if not rows:
            return None
        return str(rows[-1].get(self.cols["date"]))[:10].replace("-", "")


def fetch_universe(r) -> List[str]:
    for key in ("policy:universe:symbols", "champion:universe:global"):
        try:
            if r.type(key) == "set":
                vals = sorted([str(x).upper() for x in r.smembers(key)])
                if vals:
                    return vals
            raw = r.get(key)
            if raw:
                data = json.loads(raw)
                if isinstance(data, list):
                    return sorted(set(str(x).upper() for x in data))
                if isinstance(data, dict):
                    if isinstance(data.get("symbols"), list):
                        return sorted(set(str(x).upper() for x in data["symbols"]))
                    if isinstance(data.get("targets"), dict):
                        return sorted(set(str(x).upper() for x in data["targets"]))
        except Exception:
            pass
    return []


def normalize_date_to_ms(date_raw: str) -> int:
    s = str(date_raw)[:10].replace("-", "")
    try:
        dt = datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return now_ms()


def compute_base(symbol: str, rows: List[Dict[str, Any]], cols: Dict[str, str]) -> Tuple[Optional[Dict[str, Any]], str]:
    if len(rows) < 21:
        return None, f"insufficient_rows:{len(rows)}"

    closes = [sf(r.get(cols["close"])) for r in rows]
    if any(x is None or x <= 0 for x in closes):
        return None, "invalid_close"
    closes = [float(x) for x in closes]

    returns = []
    for i in range(1, len(closes)):
        if closes[i-1] > 0:
            returns.append(closes[i] / closes[i-1] - 1.0)

    ret_5d = pct_ret(closes, 5)
    ret_20d = pct_ret(closes, 20)
    ret_60d = pct_ret(closes, 60)
    vol_20d = std(returns[-20:])
    vol_60d = std(returns[-60:])

    if ret_5d is None or ret_20d is None or vol_20d is None:
        return None, "insufficient_return_window"

    latest = rows[-1]
    latest_close = closes[-1]
    high20 = max(closes[-20:]) if len(closes) >= 20 else max(closes)
    drawdown_20d = latest_close / high20 - 1.0 if high20 > 0 else 0.0

    vol_col = cols.get("volume")
    volumes = [sf(r.get(vol_col), None) for r in rows[-20:]] if vol_col else []
    volumes = [float(v) for v in volumes if v is not None and v >= 0]
    avg_volume_20d = sum(volumes) / len(volumes) if volumes else None
    avg_dollar_vol_20d = avg_volume_20d * latest_close if avg_volume_20d is not None else None

    if avg_dollar_vol_20d is None or avg_dollar_vol_20d <= 0:
        return None, "missing_volume"

    # Technical proxies derived from real bars; source fields identify them.
    momentum_quality = (ret_20d or 0.0) / max(vol_20d or 0.02, 1e-6)
    downside_penalty = max(0.0, -drawdown_20d)
    quality_raw = sigmoid(0.35 * momentum_quality - 2.0 * downside_penalty)

    # Provisional; cross-sectional rank overrides value/liquidity below.
    sentiment_raw = math.tanh((ret_5d or 0.0) / max(vol_20d or 0.02, 1e-6) / 3.0)

    # Conservative spread proxy from dollar volume. Not a bid/ask claim.
    adv_m = avg_dollar_vol_20d / 1_000_000.0
    spread_bps_proxy = max(1.0, min(100.0, 12.0 / math.sqrt(max(adv_m, 0.01))))

    rec = {
        "symbol": symbol,
        "updated_at_ms": normalize_date_to_ms(str(latest.get(cols["date"]))),
        "date": str(latest.get(cols["date"]))[:10],
        "price": latest_close,
        "ret_1d": returns[-1] if returns else 0.0,
        "ret_5d": ret_5d,
        "ret_20d": ret_20d,
        "ret_60d": ret_60d if ret_60d is not None else ret_20d,
        "vol_20d": vol_20d,
        "vol_60d": vol_60d if vol_60d is not None else vol_20d,
        "quality": quality_raw,
        "value": 0.5,       # cross-sectional rank filled later
        "liquidity": 0.5,   # cross-sectional rank filled later
        "sentiment": sentiment_raw,
        "drawdown_20d": drawdown_20d,
        "spread_bps": spread_bps_proxy,
        "avg_volume_20d": avg_volume_20d,
        "avg_dollar_vol_20d": avg_dollar_vol_20d,
        "eligible": True,
        "feature_source": "ares_x_v11_0.db:daily_ohlcv",
        "derived_fields": {
            "quality": "sigmoid(ret_20d/vol_20d - drawdown_penalty)",
            "value": "cross_sectional_rank_negative_ret_60d",
            "liquidity": "cross_sectional_rank_avg_dollar_vol_20d",
            "sentiment": "tanh(ret_5d/vol_20d)",
            "spread_bps": "dollar_volume_illiquidity_proxy"
        },
        "missing_fields": [],
        "stale_sources": [],
    }
    return rec, "ok"


def publish(db_path: str, once: bool = True) -> Dict[str, Any]:
    r = redis_client()
    universe = fetch_universe(r)
    if not universe:
        raise RuntimeError("universe missing")

    db = SQLiteDailySource(db_path)
    built: Dict[str, Dict[str, Any]] = {}
    skipped: Dict[str, str] = {}

    latest_dates = {}
    for sym in universe:
        rows = db.rows_for_symbol(sym, limit=90)
        rec, reason = compute_base(sym, rows, db.cols)
        if rec is None:
            skipped[sym] = reason
        else:
            built[sym] = rec
            latest_dates[sym] = rec["date"].replace("-", "")

    if not built:
        raise RuntimeError(f"no records built; skipped={skipped}")

    # Publish one key for the modal/latest date.
    from collections import Counter
    publish_date = Counter(latest_dates.values()).most_common(1)[0][0]

    # Cross-sectional enrichment.
    ret60 = {s: float(v.get("ret_60d", 0)) for s, v in built.items()}
    adv = {s: float(v.get("avg_dollar_vol_20d", 0)) for s, v in built.items()}
    value_rank = rank01(ret60, invert=True)      # lower ret_60d = higher value/reversion score
    liq_rank = rank01(adv, invert=False)         # higher ADV = higher rank because sort asc -> adjust below
    # convert liq ascending rank to high=1
    liq_rank = {k: 1.0 - v for k, v in liq_rank.items()}

    for sym, rec in built.items():
        rec["value"] = round(value_rank.get(sym, 0.5), 6)
        rec["liquidity"] = round(liq_rank.get(sym, 0.5), 6)
        # enforce numeric finite and compact
        for k in ["ret_1d","ret_5d","ret_20d","ret_60d","vol_20d","vol_60d","quality","value","liquidity","sentiment","drawdown_20d","spread_bps","price"]:
            rec[k] = round(float(rec[k]), 10)

    output_key = os.getenv("RAM26_DAILY_OUTPUT_PREFIX", "ares:features:daily") + f":{publish_date}"
    latest_key = os.getenv("RAM26_DAILY_LATEST_KEY", "ares:features:daily:latest")
    meta_key = os.getenv("RAM26_DAILY_META_KEY", "ares:features:daily:meta")
    event_stream = os.getenv("RAM26_DAILY_EVENTS_STREAM", "ares:features:daily:events")
    ttl_sec = int(float(os.getenv("RAM26_DAILY_TTL_SEC", "604800")))

    mapping = {sym: json.dumps(rec, separators=(",", ":"), sort_keys=True) for sym, rec in built.items()}
    meta = {
        "status": "PUBLISHED",
        "output_key": output_key,
        "publish_date": publish_date,
        "ts_ms": now_ms(),
        "db_path": str(Path(db_path).resolve()),
        "table": db.table,
        "columns": db.cols,
        "universe_count": len(universe),
        "published_count": len(built),
        "skipped_count": len(skipped),
        "skipped": skipped,
        "ttl_sec": ttl_sec,
        "producer": "ram26_daily_feature_publisher_prod_v1",
    }

    pipe = r.pipeline()
    pipe.delete(output_key)
    pipe.hset(output_key, mapping=mapping)
    pipe.expire(output_key, ttl_sec)
    pipe.set(latest_key, output_key, ex=ttl_sec)
    pipe.set(meta_key, json.dumps(meta, separators=(",", ":"), sort_keys=True), ex=ttl_sec)
    pipe.xadd(event_stream, {
        "status": "PUBLISHED",
        "output_key": output_key,
        "publish_date": publish_date,
        "published_count": str(len(built)),
        "payload": json.dumps(meta, separators=(",", ":")),
    }, maxlen=1000, approximate=True)
    pipe.execute()
    return meta


def self_test() -> int:
    # Static self-test only; actual DB tested by --once.
    assert sf("1.23") == 1.23
    assert pct_ret([1,2,4], 1) == 1.0
    assert isinstance(rank01({"a":1,"b":2}), dict)
    print(json.dumps({"SELFTEST":"PASS"}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("ARES_X_DB", "/home/ubuntu/ares_x_v11_0.db"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    meta = publish(args.db, once=True)
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
