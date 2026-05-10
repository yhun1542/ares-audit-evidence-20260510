#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KIS WS Status Synthesizer v1

Observation-only health publisher:
- reads internal fill/canonical/broker truth stream lengths
- publishes kis:ws:status:current, kis:ws:heartbeat, kis:fills:last_ts
- does not call KIS order APIs
- does not restart processes
"""

from __future__ import annotations
import json, os, time, datetime as dt
import redis

REDIS_URL = os.getenv("REDIS_URL") or os.getenv("ARES_REDIS_URL")
REDIS_PASS = os.getenv("REDIS_PASSWORD")
if not REDIS_URL:
    raise SystemExit("REDIS_URL/ARES_REDIS_URL required")

CYCLE_SEC = int(os.getenv("KIS_WS_STATUS_SYNTH_CYCLE_SEC", "30"))
STREAMS = ["stream:fills", "ares:fills:canonical", "ares:broker_truth:events", "ares:order_executions"]

def iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def is_market_hours() -> bool:
    now = dt.datetime.utcnow()
    if now.weekday() >= 5:
        return False
    h = now.hour + now.minute / 60.0
    return 13.5 <= h <= 20.0  # EDT regular session approximation

def cycle(r):
    lens = {}
    latest_ids = {}
    for s in STREAMS:
        try:
            lens[s] = int(r.xlen(s))
            latest = r.xrevrange(s, count=1)
            latest_ids[s] = latest[0][0] if latest else None
        except Exception:
            lens[s] = -1
            latest_ids[s] = None
    status = "OBSERVED"
    reason = "internal_stream_observed"
    if is_market_hours() and lens.get("stream:fills", 0) == 0:
        status = "WARN"
        reason = "market_hours_no_internal_fills_yet"
    now = time.time()
    payload = {
        "state": status,
        "status": status,
        "reason": reason,
        "producer": "kis_ws_status_synthesizer_v1",
        "source": "internal_streams",
        "market_hours": is_market_hours(),
        "lens": lens,
        "latest_ids": latest_ids,
        "last_heartbeat_ts": now,
        "updated_at": iso(),
    }
    p = r.pipeline()
    p.set("kis:ws:status:current", json.dumps(payload, separators=(",", ":")), ex=90)
    p.set("kis:ws:heartbeat", str(now), ex=90)
    p.set("kis:fills:last_ts", str(now), ex=90)
    p.xadd("kis:ws:status:synth:events", {"status": status, "payload": json.dumps(payload, separators=(",", ":"))}, maxlen=1000, approximate=True)
    p.execute()
    print(json.dumps(payload, ensure_ascii=False))

def main():
    r = redis.Redis.from_url(REDIS_URL, password=REDIS_PASS, decode_responses=True, socket_timeout=5)
    once = "--once" in os.sys.argv
    while True:
        cycle(r)
        if once:
            break
        time.sleep(CYCLE_SEC)

if __name__ == "__main__":
    main()
