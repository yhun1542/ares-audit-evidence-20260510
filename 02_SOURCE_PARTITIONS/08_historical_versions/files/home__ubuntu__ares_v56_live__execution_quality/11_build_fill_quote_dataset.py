#!/usr/bin/env python3
"""11_build_fill_quote_dataset.py — EC2-corrected version
Builds fill/quote dataset from stream:fills and premium:polygon:{symbol} HASH keys.
Fixes:
  - quotes-prefix: premium:polygon: (not premium:polygon:quote:)
  - Redis auth via ARES_REDIS_URL env var
  - HASH type handling (HGETALL first, then GET fallback)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import redis as redis_mod
except Exception:
    redis_mod = None

FILL_FIELD_ALIASES = {
    "symbol": ["symbol", "sym", "ticker"],
    "side": ["side", "action"],
    "price": ["price", "fill_price", "avg_fill_price", "exec_price"],
    "qty": ["qty", "quantity", "filled_qty", "shares"],
    "ts": ["ts", "timestamp", "fill_ts", "time"],
    "order_id": ["order_id", "clordid", "client_order_id", "id"],
    "arrival_px": ["arrival_px", "arrival_price", "decision_px", "signal_px"],
}

QUOTE_FIELD_ALIASES = {
    "bid": ["bid", "best_bid", "b"],
    "ask": ["ask", "best_ask", "a"],
    "ts": ["ts", "timestamp", "t", "quote_ts", "updated_at"],
}

def first_value(d: Dict[str, Any], names: List[str]) -> Any:
    for n in names:
        if n in d and d[n] not in ("", None):
            return d[n]
    return None

def to_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def to_int(x: Any) -> Optional[int]:
    try:
        return int(float(x))
    except Exception:
        return None

def parse_stream_entry(fields: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, aliases in FILL_FIELD_ALIASES.items():
        out[k] = first_value(fields, aliases)
    return out

def parse_quote(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "bid": first_value(raw, QUOTE_FIELD_ALIASES["bid"]),
        "ask": first_value(raw, QUOTE_FIELD_ALIASES["ask"]),
        "ts": first_value(raw, QUOTE_FIELD_ALIASES["ts"]),
    }

def connect():
    url = os.environ.get("ARES_REDIS_URL") or os.environ.get("REDIS_URL")
    if redis_mod is None:
        raise RuntimeError("python redis package is required: pip3 install redis")
    if url:
        return redis_mod.from_url(url, decode_responses=True)
    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    db = int(os.environ.get("REDIS_DB", "0"))
    return redis_mod.Redis(host=host, port=port, db=db, decode_responses=True)

def compute_slippage_bps(side: str, fill_px: float, ref_px: float) -> float:
    if ref_px <= 0:
        return math.nan
    s = (side or "").upper()
    if s.startswith("B"):
        return (fill_px / ref_px - 1.0) * 10000.0
    return (ref_px / fill_px - 1.0) * 10000.0

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fills-stream", default="stream:fills")
    # CORRECTED: prefix is premium:polygon: (not premium:polygon:quote:)
    ap.add_argument("--quotes-prefix", default="premium:polygon:")
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--out-csv", default="out/execution_quality/fill_quote_dataset.csv")
    ap.add_argument("--out-summary", default="out/execution_quality/slippage_summary.json")
    args = ap.parse_args()

    r = connect()
    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_summary = Path(args.out_summary)
    out_summary.parent.mkdir(parents=True, exist_ok=True)

    rows = r.xrevrange(args.fills_stream, count=args.count)
    dataset: List[Dict[str, Any]] = []

    for entry_id, fields in rows:
        fill = parse_stream_entry(fields)
        symbol = str(fill.get("symbol") or "").upper()
        if not symbol:
            continue
        price = to_float(fill.get("price"))
        side = str(fill.get("side") or "")
        if not price or not side:
            continue

        # CORRECTED: HGETALL first (HASH type), then GET fallback
        quote_key = f"{args.quotes_prefix}{symbol}"
        quote_raw = r.hgetall(quote_key) or {}
        if not quote_raw:
            qstr = r.get(quote_key)
            if qstr:
                try:
                    quote_raw = json.loads(qstr)
                except Exception:
                    quote_raw = {}

        # If quote_raw has a 'data' field (nested JSON), parse it
        if "data" in quote_raw and isinstance(quote_raw["data"], str):
            try:
                nested = json.loads(quote_raw["data"])
                quote_raw.update(nested)
            except Exception:
                pass

        quote = parse_quote(quote_raw)
        bid = to_float(quote.get("bid"))
        ask = to_float(quote.get("ask"))
        mid = None
        if bid and ask and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0

        arrival_px = to_float(fill.get("arrival_px")) or mid or bid or ask
        quote_ts = to_int(quote.get("ts"))
        fill_ts = to_int(fill.get("ts"))
        quote_age_ms = None
        if quote_ts and fill_ts:
            quote_age_ms = max(0, fill_ts - quote_ts)

        spread_bps = None
        if bid and ask and mid and mid > 0:
            spread_bps = ((ask - bid) / mid) * 10000.0

        slippage_bps = None
        if arrival_px and arrival_px > 0:
            slippage_bps = compute_slippage_bps(side, price, arrival_px)

        dataset.append({
            "entry_id": entry_id,
            "symbol": symbol,
            "side": side,
            "fill_price": price,
            "arrival_px": arrival_px,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread_bps": spread_bps,
            "quote_age_ms": quote_age_ms,
            "slippage_bps": slippage_bps,
            "qty": to_float(fill.get("qty")),
            "order_id": fill.get("order_id"),
            "fill_ts": fill_ts,
            "quote_ts": quote_ts,
        })

    fieldnames = list(dataset[0].keys()) if dataset else [
        "entry_id", "symbol", "side", "fill_price", "arrival_px", "bid", "ask",
        "mid", "spread_bps", "quote_age_ms", "slippage_bps", "qty", "order_id",
        "fill_ts", "quote_ts"
    ]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in dataset:
            w.writerow(row)

    valid = [x["slippage_bps"] for x in dataset if isinstance(x.get("slippage_bps"), (int, float)) and not math.isnan(x["slippage_bps"])]
    valid_sorted = sorted(valid)
    def pct(p: float) -> Optional[float]:
        if not valid_sorted:
            return None
        idx = min(len(valid_sorted)-1, max(0, int(round((len(valid_sorted)-1) * p))))
        return round(valid_sorted[idx], 4)

    summary = {
        "count": len(dataset),
        "valid_slippage_count": len(valid_sorted),
        "mean_slippage_bps": round(sum(valid_sorted) / len(valid_sorted), 4) if valid_sorted else None,
        "p50_slippage_bps": pct(0.50),
        "p95_slippage_bps": pct(0.95),
        "symbols": sorted({x["symbol"] for x in dataset}),
    }
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Dataset: {out_csv} ({len(dataset)} rows)")
    print(f"[OK] Summary: {out_summary}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
