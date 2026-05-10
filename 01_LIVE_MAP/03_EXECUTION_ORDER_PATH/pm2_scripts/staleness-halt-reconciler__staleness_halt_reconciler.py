#!/usr/bin/env python3
"""
staleness_halt_reconciler.py — MUSK RCA-v7 (STRUCTURAL FIX 2026-05-08)
======================================================================

근본 원인 (1원리 분석):
  - 기존 PRICE_FRESH_SEC=30s, STABLE_SEC=60s 였으나
  - realtime-data-feed-v3 의 fetch interval = 60s + cycle duration ~60s
  - 따라서 prices:latest:ts 는 약 60-65s 마다만 갱신
  - => fresh window(<30s)가 매 사이클마다 0~30s 만 존재
  - => STABLE_SEC 60s 동안 연속 fresh 가 NEVER 달성
  - => HALT 자동 클리어가 영원히 발생 못 함 → OPS 수동 개입 필요

구조적 수정:
  1) PRICE_FRESH_SEC: 30s → 75s (RTDF 1cycle + buffer)
  2) STABLE_SEC: 60s → 90s (확실한 fresh 윈도우)
  3) 가격 keepalive 활용: realtime:feed:heartbeat 를 우선 (가벼움)
  4) HALT 발생 직후 grace period 30s (race 방지)
  5) 클리어 후 재발 방지 cooldown 60s

Environment:
  PRICE_FRESH_SEC      (default 75)
  STABLE_SEC           (default 90)
  TICK_SEC             (default 5)
  CLEAR_COOLDOWN_SEC   (default 60)
  HALT_GRACE_SEC       (default 30)
"""
from __future__ import annotations
# [ARES_BOOTSTRAP_2026-05-07] strip quoted env BEFORE redis import
import os as _ARES_OS
import sys as _ARES_SYS
_ARES_SYS.path.insert(0, _ARES_OS.path.dirname(_ARES_OS.path.abspath(__file__)))
try:
    from _ares_redis_env import bootstrap as _ares_bootstrap
    _ares_bootstrap(verbose=True)
except Exception as _e:
    _ARES_SYS.stderr.write("[ARES_BOOTSTRAP] failed: " + str(_e) + "\n")

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
import redis

LOG_PATH = "/home/ubuntu/logs/staleness_halt_reconciler.log"
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("staleness-halt-reconciler-v7")

# ── MUSK RCA-v7: RTDF interval-aware thresholds ──
PRICE_FRESH_SEC = int(os.getenv("PRICE_FRESH_SEC", "75"))     # was 30 — must be > RTDF cycle (60s)
STABLE_SEC = int(os.getenv("STABLE_SEC", "90"))               # was 60 — must be ≥ 1.5× cycle
TICK_SEC = int(os.getenv("TICK_SEC", "5"))
CLEAR_COOLDOWN_SEC = int(os.getenv("CLEAR_COOLDOWN_SEC", "60"))
HALT_GRACE_SEC = int(os.getenv("HALT_GRACE_SEC", "30"))

REDIS_URL = (
    os.getenv("ARES_REDIS_URL")
    or os.getenv("REDIS_URL")
    or "rediss://localhost:6379/0"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?Z?$")


def parse_iso(ts: str) -> float | None:
    if not ts:
        return None
    try:
        clean = ts.rstrip("Z")
        d = datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def main():
    log.info("=" * 60)
    log.info(
        "ARES Staleness-Halt Reconciler v7 starting | fresh=%ss stable=%ss tick=%ss cooldown=%ss grace=%ss",
        PRICE_FRESH_SEC, STABLE_SEC, TICK_SEC, CLEAR_COOLDOWN_SEC, HALT_GRACE_SEC,
    )
    r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)
    log.info("Redis connected: ping=%s", r.ping())

    fresh_since: float | None = None
    last_clear_at: float = 0.0
    halt_first_seen_at: float | None = None

    while True:
        try:
            # ── Read both legacy and feed-anchored heartbeat keys ──
            halt = (r.get("trade:halt") or "").strip().lower()
            reason = (r.get("trade:halt:reason") or "").strip()
            # Prefer the lightweight heartbeat that RTDF v3 also writes; fall back to prices:latest:ts.
            price_ts = r.get("realtime:feed:heartbeat") or r.get("prices:latest:ts") or ""
            ts_epoch = parse_iso(price_ts)
            now = time.time()
            age = (now - ts_epoch) if ts_epoch else None

            is_halted = halt == "true"
            is_price_staleness = reason.upper().startswith("PRICE_STALENESS") or reason.upper().startswith("PRICE_FEED_STALE") or reason.upper().startswith("PRICE_FEED_NO_TS")
            is_fresh = (age is not None) and (age < PRICE_FRESH_SEC)

            # ── Track halt grace window (avoid clearing too soon after halt was just set) ──
            if is_halted and is_price_staleness:
                if halt_first_seen_at is None:
                    halt_first_seen_at = now
            else:
                halt_first_seen_at = None

            if is_fresh:
                if fresh_since is None:
                    fresh_since = now
            else:
                fresh_since = None

            stable_for = (now - fresh_since) if fresh_since else 0.0
            time_since_last_clear = now - last_clear_at
            halt_age = (now - halt_first_seen_at) if halt_first_seen_at else 0.0

            log.info(
                "tick: halt=%s reason=%r price_age=%s fresh=%s stable_for=%.1fs halt_age=%.1fs cooldown=%.1fs",
                halt,
                reason[:60],
                f"{age:.1f}s" if age is not None else "n/a",
                is_fresh,
                stable_for,
                halt_age,
                time_since_last_clear,
            )

            # ── CLEAR conditions (all must be true) ──
            should_clear = (
                is_halted
                and is_price_staleness
                and is_fresh
                and stable_for >= STABLE_SEC
                and halt_age >= HALT_GRACE_SEC                    # don't clear immediately on transient
                and time_since_last_clear >= CLEAR_COOLDOWN_SEC   # don't oscillate
            )

            if should_clear:
                ts_now = now_iso()
                new_reason = f"CLEARED_BY_AUTO_RECONCILER_V7_{ts_now}"
                log.warning(
                    "CLEAR conditions met — clearing halt. prev_reason=%r price_age=%.1fs stable_for=%.1fs halt_age=%.1fs",
                    reason, age, stable_for, halt_age,
                )
                pipe = r.pipeline()
                pipe.set("trade:halt", "false")
                pipe.set("trade:halt:reason", new_reason)
                pipe.set("trade:halt:ts", ts_now)
                pipe.set("nextgen2:trade:halt", "false")
                pipe.set("nextgen2:trade:halt:reason", new_reason)
                pipe.set("nextgen2:trade:halt:ts", ts_now)
                # Also clear strategy-scoped halt key for ram26 if present (v5.8.0 alignment)
                pipe.set("nextgen2:trade:halt:ram26", "false")
                pipe.set("nextgen2:trade:halt:reason:ram26", new_reason)
                pipe.set("nextgen2:trade:halt:ts:ram26", ts_now)
                # Reset the consecutive-cycles counter so LIVE engine doesn't immediately re-halt
                pipe.delete("ares:price_staleness:consecutive_halt_cycles")
                pipe.delete("policy:buy_freeze:price_staleness")
                pipe.xadd(
                    "ares:halt:writer_audit",
                    {
                        "actor": "staleness_halt_reconciler_v7",
                        "action": "clear_price_staleness_halt",
                        "prev_reason": reason,
                        "new_state": "false",
                        "price_ts": price_ts,
                        "stable_for_sec": f"{stable_for:.1f}",
                        "halt_age_sec": f"{halt_age:.1f}",
                    },
                    maxlen=10000,
                    approximate=True,
                )
                pipe.execute()
                log.info("HALT CLEARED at %s", ts_now)
                # reset state windows
                fresh_since = None
                halt_first_seen_at = None
                last_clear_at = now

        except Exception as e:
            log.error("loop error: %s", e)
        time.sleep(TICK_SEC)


if __name__ == "__main__":
    main()
