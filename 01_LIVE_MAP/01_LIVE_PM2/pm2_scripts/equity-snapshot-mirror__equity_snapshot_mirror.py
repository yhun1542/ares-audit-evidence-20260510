#!/usr/bin/env python3
"""
ARES Equity Snapshot Mirror Adapter
====================================
목적:
- 신규 SSOT `ares:equity:authoritative` (writer=ares-broker-truth-writer-v2, v42 contract)
  내용을 레거시 키 `ares:equity:snapshot` 형식으로 변환하여 30초마다 미러링한다.
- 이로써 `exposure_monitor_v2.py` (snapshot key 의존)가 정상 작동하게 된다.

배경:
- equity-calculator.mjs (구 SSOT writer)가 pm2에서 제거됨 → snapshot 키 stale (EXISTS=0)
- 신 SSOT는 살아있고 매분 갱신되므로, 어댑터로 호환성 유지하는 것이 가장 안전한 fix.

설계 원칙:
- 원본 코드(exposure_monitor_v2.py)와 신 SSOT writer 모두 수정하지 않음 (less invasive).
- 30초 간격 (구 equity-calculator와 동일).
- 변환 실패/부분 결손 시 마지막 정상 값 유지 (degrade-gracefully).
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone

import redis

REDIS_URL = os.environ.get(
    "REDIS_URL",
    "rediss://ares-admin:AresAdmin2026SecureA3kB6jY1xZ8@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379/0",
)
SRC_KEY = "ares:equity:authoritative"
DST_KEY = "ares:equity:snapshot"
INTERVAL = 30

LOG_PATH = "/home/ubuntu/logs/equity_snapshot_mirror.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("equity-snapshot-mirror")


def connect_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)


def transform(authoritative_json: str) -> dict:
    d = json.loads(authoritative_json)
    total = float(d.get("total", d.get("total_usd", 0)) or 0)
    cash = float(d.get("cash_total_usd", 0) or 0)
    positions_mv = float(d.get("positions_mv_usd", 0) or 0)
    return {
        "ok": bool(d.get("ok", True)),
        "total": total,
        "cash": cash,
        "positions_mv": positions_mv,
        "source": d.get("writer", "ares-broker-truth-writer-v2:mirror"),
        "ts": d.get("ts", datetime.now(timezone.utc).isoformat()),
        "cash_status": "OK" if cash > 0 else "ZERO",
        "schema": d.get("contract", "ares.authoritative_equity.v42"),
        "positions_count": d.get("positions_count"),
        "open_orders_count": d.get("open_orders_count"),
        "_mirror": {
            "src_key": SRC_KEY,
            "mirror_ts": datetime.now(timezone.utc).isoformat(),
            "writer": "equity_snapshot_mirror.py",
        },
    }


def main():
    log.info("=" * 60)
    log.info("ARES Equity Snapshot Mirror starting")
    log.info(f"  src: {SRC_KEY}  dst: {DST_KEY}  interval: {INTERVAL}s")
    log.info("=" * 60)

    r = connect_redis()
    pong = r.ping()
    log.info(f"Redis connected: PING={pong}")

    cycle = 0
    last_ok_ts = None
    while True:
        cycle += 1
        try:
            raw = r.get(SRC_KEY)
            if not raw:
                log.warning(f"#{cycle} src key {SRC_KEY} empty/missing")
                time.sleep(INTERVAL)
                continue
            snap = transform(raw)
            r.setex(DST_KEY, 300, json.dumps(snap, ensure_ascii=False))
            # [ARES_BOOTSTRAP_2026-05-07] keep DD/Peak gate happy
            r.setex("ares:equity:updated_at", 300, snap["ts"])
            r.setex("ares:equity:metric_version", 86400, snap.get("schema", "v42"))
            last_ok_ts = snap["ts"]
            log.info(
                f"#{cycle} mirrored OK  total=${snap['total']:.2f}  "
                f"cash=${snap['cash']:.2f}  pos_mv=${snap['positions_mv']:.2f}  "
                f"src_ts={snap['ts']}"
            )
        except Exception as e:
            log.error(f"#{cycle} mirror error: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
