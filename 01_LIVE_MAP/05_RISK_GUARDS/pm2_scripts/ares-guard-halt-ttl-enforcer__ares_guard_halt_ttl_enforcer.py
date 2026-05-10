#!/usr/bin/env python3
"""
ares_guard_halt_ttl_enforcer.py — MUSK RCA-v7 (NEW SERVICE 2026-05-08)
======================================================================

근본 원인 (1원리 분석):
  - `ares:guard:halt` 키가 TTL=-1 (영구) 상태로 true 로 설정되는 경우 발견
  - 이는 일부 가드/정책 코드가 SET 만 하고 EXPIRE 를 누락한 흔적
  - 결과적으로 운영 사이클이 끝나도 좀비 halt 키가 영원히 남아
    state-machine-guard 가 '거래 차단 상태 감지!' 알람을 반복 발사

구조적 수정:
  - 5분 마다 모든 ARES halt 패밀리 키를 스캔하고 TTL=-1 (PERSIST) 인 키에
    600s TTL 자동 적용
  - false 값으로 설정된 halt 키는 조기에 정리 (5min 후 만료)
  - true 값으로 설정된 halt 키는 보수적으로 처리: 600s TTL (safety > liveness)

대상 키 패밀리:
  - ares:guard:halt
  - ares:halt:active  
  - ares:guardian:*
  - feat:v1:halt
  - chronos:halt:*
"""
from __future__ import annotations
import os as _ARES_OS
import sys as _ARES_SYS
_ARES_SYS.path.insert(0, _ARES_OS.path.dirname(_ARES_OS.path.abspath(__file__)))
try:
    from _ares_redis_env import bootstrap as _ares_bootstrap
    _ares_bootstrap(verbose=True)
except Exception:
    pass

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
import redis

LOG_PATH = "/home/ubuntu/logs/ares_guard_halt_ttl_enforcer.log"
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("guard-halt-ttl-enforcer")

REDIS_URL = os.getenv("ARES_REDIS_URL") or os.getenv("REDIS_URL") or "rediss://localhost:6379/0"
TICK_SEC = int(os.getenv("TICK_SEC", "300"))
DEFAULT_TTL_SEC = int(os.getenv("HALT_DEFAULT_TTL_SEC", "600"))
FALSE_TTL_SEC = int(os.getenv("HALT_FALSE_TTL_SEC", "300"))

TARGET_PATTERNS = [
    "ares:guard:halt",
    "ares:halt:active",
    "ares:halt:active:reason",
    "ares:guardian:*",
    "feat:v1:halt",
    "chronos:halt:*",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def is_falsy(val: str | None) -> bool:
    if val is None:
        return True
    s = val.strip().lower()
    return s in ("false", "0", "", "none", "null", "off", "no")


def main():
    log.info("=" * 60)
    log.info("ARES Guard-Halt TTL Enforcer v7 starting | tick=%ss default_ttl=%ss false_ttl=%ss",
             TICK_SEC, DEFAULT_TTL_SEC, FALSE_TTL_SEC)
    r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=10)
    log.info("Redis connected: ping=%s", r.ping())

    while True:
        try:
            applied = 0
            cleared = 0
            scanned = 0
            for pattern in TARGET_PATTERNS:
                for key in r.scan_iter(match=pattern, count=200):
                    scanned += 1
                    try:
                        ttl = r.ttl(key)
                    except Exception:
                        continue
                    if ttl != -1:
                        # already has TTL — leave it alone (or no key)
                        continue
                    try:
                        val = r.get(key)
                    except Exception:
                        # not a string type — skip
                        continue
                    if is_falsy(val):
                        # false-valued zombie — short TTL to clean up
                        r.expire(key, FALSE_TTL_SEC)
                        cleared += 1
                        log.info("FALSE_TTL applied: key=%s val=%r ttl=%ds",
                                 key, val[:30] if val else val, FALSE_TTL_SEC)
                    else:
                        # true-valued — must be a real halt; apply default TTL safety net
                        r.expire(key, DEFAULT_TTL_SEC)
                        applied += 1
                        log.warning("DEFAULT_TTL applied: key=%s val=%r ttl=%ds",
                                    key, val[:30] if val else val, DEFAULT_TTL_SEC)

            # Audit snapshot
            snapshot = {
                "ts": now_iso(),
                "scanned": scanned,
                "ttl_applied": applied,
                "ttl_cleared": cleared,
            }
            r.setex("ops:guard_halt_ttl_enforcer:last", 900, json.dumps(snapshot))
            log.info("tick: scanned=%d applied=%d cleared=%d", scanned, applied, cleared)
        except Exception as e:
            log.error("loop error: %s", e)
        time.sleep(TICK_SEC)


if __name__ == "__main__":
    main()
