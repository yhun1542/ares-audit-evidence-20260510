#!/usr/bin/env python3
"""
CORRECTED: execution_quality_consumer.py
EC2 실제 스키마에 맞게 수정됨 (2026-04-01)

변경점:
  1. fill_px 와 fill_price 양쪽 모두 인식
  2. order_notional_usd 와 notional_usd 양쪽 모두 인식
  3. fill_ts_ms 와 fill_time(ISO) 양쪽 모두 인식
"""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

try:
    import redis
except Exception as e:
    raise SystemExit(f"redis package is required: {e}")

# CORRECTED: 양쪽 필드명 모두 수용
NUMERIC_KEYS = {
    "arrival_bid", "arrival_ask", "arrival_mid", "arrival_spread_bps",
    "fill_px", "fill_price", "fill_qty", "qty",
    "slippage_bps", "maker_requotes", "escape_used",
    "quote_age_ms", "order_notional_usd", "notional_usd",
    "submit_ts_ms", "fill_ts_ms",
    "arrival_ts_ms", "cost_bps_assumption",
}

ROUTE_MAKER = {"maker_first_fill", "maker_then_taker_escape", "maker_shadow"}
ROUTE_DIRECT = {"direct_market_fallback", "direct_market"}

STOP = False


def on_signal(signum, frame):
    global STOP
    STOP = True


for _sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(_sig, on_signal)


class RollingStats:
    def __init__(self) -> None:
        self.count = 0
        self.slippages: List[float] = []
        self.notional_sum = 0.0
        self.maker_count = 0
        self.direct_count = 0
        self.escape_count = 0
        self.requote_sum = 0.0

    def add(self, rec: Dict[str, Any]) -> None:
        self.count += 1
        route = str(rec.get("route_mode", ""))
        if route in ROUTE_MAKER:
            self.maker_count += 1
        if route in ROUTE_DIRECT:
            self.direct_count += 1
        self.escape_count += int(float(rec.get("escape_used") or 0))
        self.requote_sum += float(rec.get("maker_requotes") or 0)
        # CORRECTED: 양쪽 필드명 수용
        self.notional_sum += float(rec.get("order_notional_usd") or rec.get("notional_usd") or 0)
        slip = rec.get("slippage_bps")
        if slip not in (None, "", "nan"):
            try:
                slip_f = float(slip)
                if math.isfinite(slip_f):
                    self.slippages.append(slip_f)
            except Exception:
                pass

    def summary(self) -> Dict[str, Any]:
        sl = sorted(self.slippages)
        p50 = statistics.median(sl) if sl else None
        p95 = sl[min(len(sl) - 1, max(0, int(math.ceil(len(sl) * 0.95)) - 1))] if sl else None
        mean = statistics.fmean(sl) if sl else None
        return {
            "count": self.count,
            "notional_sum": round(self.notional_sum, 2),
            "maker_count": self.maker_count,
            "direct_count": self.direct_count,
            "maker_share": round(self.maker_count / self.count, 4) if self.count else 0,
            "direct_share": round(self.direct_count / self.count, 4) if self.count else 0,
            "escape_count": self.escape_count,
            "escape_share": round(self.escape_count / self.count, 4) if self.count else 0,
            "avg_requotes": round(self.requote_sum / self.count, 4) if self.count else 0,
            "slippage_mean_bps": round(mean, 4) if mean is not None else None,
            "slippage_p50_bps": round(p50, 4) if p50 is not None else None,
            "slippage_p95_bps": round(p95, 4) if p95 is not None else None,
        }


def parse_stream_entry(entry_id: str, fields: Dict[bytes, bytes]) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"stream_id": entry_id}
    for k_b, v_b in fields.items():
        k = k_b.decode("utf-8") if isinstance(k_b, (bytes, bytearray)) else str(k_b)
        v = v_b.decode("utf-8") if isinstance(v_b, (bytes, bytearray)) else str(v_b)
        if k in NUMERIC_KEYS and v not in ("", None):
            try:
                rec[k] = float(v)
            except Exception:
                rec[k] = v
        else:
            rec[k] = v

    # CORRECTED: fill_price -> fill_px 정규화
    if "fill_price" in rec and "fill_px" not in rec:
        rec["fill_px"] = rec["fill_price"]
    if "notional_usd" in rec and "order_notional_usd" not in rec:
        rec["order_notional_usd"] = rec["notional_usd"]

    # CORRECTED: fill_time(ISO) -> fill_ts_ms 변환
    ts_ms = rec.get("fill_ts_ms") or rec.get("submit_ts_ms") or rec.get("arrival_ts_ms")
    if ts_ms:
        try:
            dt = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
    elif rec.get("fill_time"):
        try:
            dt = datetime.fromisoformat(str(rec["fill_time"]).replace("Z", "+00:00"))
        except Exception:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)

    rec["event_time_utc"] = dt.isoformat()
    rec["event_date_utc"] = dt.strftime("%Y-%m-%d")
    rec["event_hour_utc"] = dt.strftime("%Y-%m-%dT%H")
    return rec


def ensure_group(r: redis.Redis, stream: str, group: str) -> None:
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def dump_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def build_summary(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    overall = RollingStats()
    by_symbol: Dict[str, RollingStats] = defaultdict(RollingStats)
    by_route: Dict[str, RollingStats] = defaultdict(RollingStats)

    for rec in rows:
        overall.add(rec)
        by_symbol[str(rec.get("symbol", "UNKNOWN"))].add(rec)
        by_route[str(rec.get("route_mode", "UNKNOWN"))].add(rec)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall": overall.summary(),
        "by_symbol": {k: v.summary() for k, v in sorted(by_symbol.items())},
        "by_route_mode": {k: v.summary() for k, v in sorted(by_route.items())},
    }


def load_existing_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
    ap.add_argument("--stream", default="stream:execution_quality")
    ap.add_argument("--group", default="execution-quality-consumers")
    ap.add_argument("--consumer", default=f"consumer-{os.getpid()}")
    ap.add_argument("--outdir", default="out/execution_quality")
    ap.add_argument("--block-ms", type=int, default=5000)
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--flush-every", type=int, default=50)
    args = ap.parse_args()

    outdir = Path(args.outdir)
    r = redis.Redis.from_url(args.redis_url)
    ensure_group(r, args.stream, args.group)

    pending_rows: List[Dict[str, Any]] = []
    processed = 0

    while not STOP:
        resp = r.xreadgroup(args.group, args.consumer, {args.stream: ">"}, count=args.count, block=args.block_ms)
        if not resp:
            continue

        for stream_name, entries in resp:
            for entry_id_b, fields in entries:
                entry_id = entry_id_b.decode("utf-8") if isinstance(entry_id_b, (bytes, bytearray)) else str(entry_id_b)
                rec = parse_stream_entry(entry_id, fields)
                day_file = outdir / f"date={rec['event_date_utc']}" / "execution_quality.jsonl"
                append_jsonl(day_file, rec)
                pending_rows.append(rec)
                r.xack(args.stream, args.group, entry_id)
                processed += 1

                if processed % args.flush_every == 0:
                    all_rows = load_existing_jsonl(day_file)
                    summary = build_summary(all_rows)
                    dump_json(day_file.with_name("summary.json"), summary)
                    dump_json(outdir / "latest_summary.json", summary)
                    pending_rows.clear()

    # final flush
    touched_days = sorted({rec["event_date_utc"] for rec in pending_rows})
    for day in touched_days:
        day_file = outdir / f"date={day}" / "execution_quality.jsonl"
        all_rows = load_existing_jsonl(day_file)
        summary = build_summary(all_rows)
        dump_json(day_file.with_name("summary.json"), summary)
        dump_json(outdir / "latest_summary.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
