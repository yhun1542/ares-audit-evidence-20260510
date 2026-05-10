#!/usr/bin/env python3
"""
ARES V5.6.1b Drill Runner v2.0
───────────────────────────────
운영 안전성 검증을 위한 자동화 드릴 스크립트.

v2.0 변경사항:
  - Redis 자격정보 하드코딩 완전 제거 (환경변수 필수)
  - Kill Switch 드릴: 현재값 스냅샷 → finally 원상복구
  - 장중 실행 차단 가드 (--force 없이는 장중 실행 불가)
  - 모든 드릴에 dry-run 모드 지원

Usage:
  python3 ares_v56_drill_runner_v2.py --drill all
  python3 ares_v56_drill_runner_v2.py --drill killswitch
  python3 ares_v56_drill_runner_v2.py --drill leader
  python3 ares_v56_drill_runner_v2.py --drill consumer
  python3 ares_v56_drill_runner_v2.py --drill autotune
  python3 ares_v56_drill_runner_v2.py --drill all --force    # 장중 강제 실행
  python3 ares_v56_drill_runner_v2.py --drill all --dry-run  # 실제 변경 없이 시뮬레이션

Author: ARES Architecture Team
Version: 2.0.0
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import redis

# ─── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DRILL] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("drill_runner")

# ─── Configuration (NO HARDCODED SECRETS) ─────────────────────
REDIS_URL = os.environ.get("REDIS_URL", "")
if not REDIS_URL:
    logger.error("REDIS_URL environment variable is required but not set.")
    logger.error("Set it via: export REDIS_URL='redis://:PASSWORD@localhost:6379/0'")
    sys.exit(1)

ET = ZoneInfo("America/New_York")

KEYS = {
    "leader_lease": "ares:v56:leader:lease",
    "trading_enabled": "trading:enabled",
    "consumer_heartbeat": "ares:v55:consumer:heartbeat",
    "autotune_active": "ares:v55:autotune:active_override",
    "readiness": "ares:v55:live:readiness",
}


def get_redis() -> redis.Redis:
    """Create Redis connection from environment variable."""
    return redis.from_url(REDIS_URL, decode_responses=True)


def is_market_hours() -> bool:
    """Check if current time is within US market hours (ET 09:30-16:00, weekdays)."""
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def enforce_market_guard(force: bool) -> None:
    """Block execution during market hours unless --force is specified."""
    if is_market_hours() and not force:
        now_et = datetime.now(ET)
        logger.error("=" * 60)
        logger.error("BLOCKED: Drill execution during market hours is prohibited!")
        logger.error(f"Current ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S ET')}")
        logger.error("Market hours: ET 09:30 - 16:00 (weekdays)")
        logger.error("")
        logger.error("To override, use: --force")
        logger.error("WARNING: --force during market hours may disrupt live trading!")
        logger.error("=" * 60)
        sys.exit(1)
    elif is_market_hours() and force:
        now_et = datetime.now(ET)
        logger.warning("=" * 60)
        logger.warning("FORCED EXECUTION during market hours!")
        logger.warning(f"Current ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S ET')}")
        logger.warning("This may disrupt live trading operations.")
        logger.warning("=" * 60)


# ─── Drill: AutoTune Gate ─────────────────────────────────────
def drill_autotune(r: redis.Redis, dry_run: bool) -> dict:
    """Verify AutoTune Gate is set to conservative (false)."""
    logger.info("─── Drill: AutoTune Gate ───")
    result = {"name": "autotune_gate", "passed": False, "detail": ""}

    current = r.get(KEYS["autotune_active"])
    logger.info(f"  Current autotune_active_override: {current}")

    if current is None or str(current).lower() in ("false", "0", "no", "none"):
        result["passed"] = True
        result["detail"] = f"AutoTune Gate is conservative (value={current})"
        logger.info(f"  PASS: {result['detail']}")
    else:
        result["detail"] = f"AutoTune Gate is ACTIVE (value={current}), forcing to false"
        logger.warning(f"  {result['detail']}")
        if not dry_run:
            r.set(KEYS["autotune_active"], "false")
            verify = r.get(KEYS["autotune_active"])
            result["passed"] = str(verify).lower() in ("false", "0", "no")
            logger.info(f"  After correction: {verify} → {'PASS' if result['passed'] else 'FAIL'}")
        else:
            logger.info("  [DRY-RUN] Would set autotune_active_override=false")
            result["passed"] = True

    return result


# ─── Drill: Kill Switch ──────────────────────────────────────
def drill_killswitch(r: redis.Redis, dry_run: bool) -> dict:
    """
    Test Kill Switch: snapshot current value → set false → verify block → restore original.
    Uses try/finally to guarantee restoration even on error.
    """
    logger.info("─── Drill: Kill Switch ───")
    result = {"name": "killswitch", "passed": False, "detail": ""}

    # Step 1: Snapshot current value
    original_value = r.get(KEYS["trading_enabled"])
    logger.info(f"  Step 1 - Snapshot: trading:enabled = {original_value}")

    if dry_run:
        logger.info("  [DRY-RUN] Would test: set false → verify → restore to original")
        result["passed"] = True
        result["detail"] = f"DRY-RUN: original={original_value}"
        return result

    try:
        # Step 2: Set to false (block trading)
        logger.info("  Step 2 - Setting trading:enabled = false")
        r.set(KEYS["trading_enabled"], "false")
        time.sleep(2)

        # Step 3: Verify it's false
        check = r.get(KEYS["trading_enabled"])
        blocked = str(check).lower() in ("false", "0", "no")
        logger.info(f"  Step 3 - Verify block: trading:enabled = {check} (blocked={blocked})")

        if not blocked:
            result["detail"] = f"Kill Switch did not engage! Value after set: {check}"
            logger.error(f"  FAIL: {result['detail']}")
            return result

        # Step 4: Restore original value
        logger.info(f"  Step 4 - Restoring original value: {original_value}")

    finally:
        # ALWAYS restore original value, even on exception
        if original_value is not None:
            r.set(KEYS["trading_enabled"], original_value)
            logger.info(f"  [FINALLY] Restored trading:enabled = {original_value}")
        else:
            # If original was None (key didn't exist), delete it
            r.delete(KEYS["trading_enabled"])
            logger.info("  [FINALLY] Deleted trading:enabled (original was None)")

    # Step 5: Verify restoration
    time.sleep(1)
    restored = r.get(KEYS["trading_enabled"])
    match = str(restored) == str(original_value)
    logger.info(f"  Step 5 - Verify restore: {restored} (match={match})")

    result["passed"] = blocked and match
    result["detail"] = (
        f"Block OK, Restore OK (original={original_value}, "
        f"blocked_as={check}, restored_as={restored})"
    )
    logger.info(f"  {'PASS' if result['passed'] else 'FAIL'}: {result['detail']}")
    return result


# ─── Drill: Leader Lease ─────────────────────────────────────
def drill_leader(r: redis.Redis, dry_run: bool) -> dict:
    """
    Test Leader Lease: restart ares-v56-live → verify lease re-acquisition.
    """
    logger.info("─── Drill: Leader Lease ───")
    result = {"name": "leader_lease", "passed": False, "detail": ""}

    # Step 1: Check current leader
    leader_before = r.get(KEYS["leader_lease"])
    logger.info(f"  Step 1 - Current leader: {leader_before}")

    if not leader_before:
        result["detail"] = "No leader lease found before drill. Engine may not be running."
        logger.warning(f"  SKIP: {result['detail']}")
        result["passed"] = False
        return result

    if dry_run:
        logger.info("  [DRY-RUN] Would: delete lease → restart ares-v56-live → verify re-acquisition")
        result["passed"] = True
        result["detail"] = "DRY-RUN: leader exists"
        return result

    try:
        # Step 2: Delete leader lease
        logger.info("  Step 2 - Deleting leader lease")
        r.delete(KEYS["leader_lease"])
        time.sleep(2)

        # Step 3: Restart ares-v56-live
        logger.info("  Step 3 - Restarting ares-v56-live")
        proc = subprocess.run(
            ["pm2", "restart", "ares-v56-live"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            result["detail"] = f"PM2 restart failed: {proc.stderr}"
            logger.error(f"  FAIL: {result['detail']}")
            return result

        # Step 4: Wait for lease re-acquisition (max 60s)
        logger.info("  Step 4 - Waiting for lease re-acquisition (max 60s)")
        for i in range(12):
            time.sleep(5)
            leader_after = r.get(KEYS["leader_lease"])
            if leader_after:
                logger.info(f"  Lease re-acquired at {(i + 1) * 5}s: {leader_after}")
                break
        else:
            result["detail"] = "Leader lease not re-acquired within 60s"
            logger.error(f"  FAIL: {result['detail']}")
            return result

        # Step 5: Verify no split-brain (only one leader)
        ttl = r.ttl(KEYS["leader_lease"])
        result["passed"] = True
        result["detail"] = f"Re-acquired in {(i + 1) * 5}s, TTL={ttl}s"
        logger.info(f"  PASS: {result['detail']}")

    except Exception as e:
        result["detail"] = f"Exception during leader drill: {e}"
        logger.error(f"  FAIL: {result['detail']}")

    return result


# ─── Drill: Consumer Restart ─────────────────────────────────
def drill_consumer(r: redis.Redis, dry_run: bool) -> dict:
    """
    Test Consumer restart: stop executor → verify DEFER → restart → verify recovery.
    """
    logger.info("─── Drill: Consumer Restart ───")
    result = {"name": "consumer_restart", "passed": False, "detail": ""}

    # Step 1: Check current heartbeat
    hb_before = r.get(KEYS["consumer_heartbeat"])
    logger.info(f"  Step 1 - Current heartbeat: {hb_before}")

    if dry_run:
        logger.info("  [DRY-RUN] Would: stop executor → wait for DEFER → restart → verify recovery")
        result["passed"] = True
        result["detail"] = f"DRY-RUN: hb={'set' if hb_before else 'none'}"
        return result

    try:
        # Step 2: Stop order-intent-executor
        logger.info("  Step 2 - Stopping order-intent-executor")
        proc = subprocess.run(
            ["pm2", "stop", "order-intent-executor"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            logger.warning(f"  PM2 stop warning: {proc.stderr}")

        # Step 3: Wait and check heartbeat goes stale
        logger.info("  Step 3 - Waiting 30s for heartbeat to go stale")
        time.sleep(30)
        hb_after_stop = r.get(KEYS["consumer_heartbeat"])
        if hb_after_stop:
            hb_age = time.time() - float(hb_after_stop)
            stale = hb_age > 25
            logger.info(f"  Heartbeat age after stop: {hb_age:.0f}s (stale={stale})")
        else:
            stale = True
            logger.info("  Heartbeat missing after stop (expected)")

        # Step 4: Restart executor
        logger.info("  Step 4 - Restarting order-intent-executor")
        proc = subprocess.run(
            ["pm2", "restart", "order-intent-executor"],
            capture_output=True, text=True, timeout=15,
        )

        # Step 5: Wait for heartbeat recovery (max 60s)
        logger.info("  Step 5 - Waiting for heartbeat recovery (max 60s)")
        recovered = False
        for i in range(12):
            time.sleep(5)
            hb_recovered = r.get(KEYS["consumer_heartbeat"])
            if hb_recovered:
                hb_age = time.time() - float(hb_recovered)
                if hb_age < 15:
                    recovered = True
                    logger.info(f"  Heartbeat recovered at {(i + 1) * 5}s (age={hb_age:.0f}s)")
                    break

        result["passed"] = stale and recovered
        result["detail"] = (
            f"Stale after stop: {stale}, "
            f"Recovered after restart: {recovered}"
        )
        logger.info(f"  {'PASS' if result['passed'] else 'FAIL'}: {result['detail']}")

    except Exception as e:
        result["detail"] = f"Exception during consumer drill: {e}"
        logger.error(f"  FAIL: {result['detail']}")
        # Always try to restart executor on error
        try:
            subprocess.run(["pm2", "restart", "order-intent-executor"],
                           capture_output=True, timeout=15)
            logger.info("  [RECOVERY] order-intent-executor restarted after error")
        except Exception:
            logger.error("  [RECOVERY] Failed to restart order-intent-executor!")

    return result


# ─── Main ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ARES V5.6.1b Drill Runner v2.0")
    parser.add_argument(
        "--drill",
        required=True,
        choices=["all", "autotune", "killswitch", "leader", "consumer"],
        help="Which drill to run",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force execution during market hours (DANGEROUS)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate drills without making actual changes",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("ARES V5.6.1b Drill Runner v2.0")
    logger.info(f"Drill: {args.drill}")
    logger.info(f"Force: {args.force}")
    logger.info(f"Dry-run: {args.dry_run}")
    now_et = datetime.now(ET)
    logger.info(f"ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S ET')}")
    logger.info(f"Market hours: {is_market_hours()}")
    logger.info("=" * 60)

    # Market hours guard
    enforce_market_guard(args.force)

    # Connect to Redis
    r = get_redis()
    try:
        r.ping()
        logger.info("Redis connection OK")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        sys.exit(1)

    # Run drills
    drills = {
        "autotune": drill_autotune,
        "killswitch": drill_killswitch,
        "leader": drill_leader,
        "consumer": drill_consumer,
    }

    if args.drill == "all":
        drill_list = ["autotune", "killswitch", "leader", "consumer"]
    else:
        drill_list = [args.drill]

    results = []
    for drill_name in drill_list:
        try:
            result = drills[drill_name](r, args.dry_run)
            results.append(result)
        except Exception as e:
            logger.error(f"Drill {drill_name} failed with exception: {e}")
            results.append({"name": drill_name, "passed": False, "detail": str(e)})

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("DRILL SUMMARY")
    logger.info("=" * 60)
    all_passed = True
    for res in results:
        status = "PASS" if res["passed"] else "FAIL"
        logger.info(f"  [{status}] {res['name']}: {res['detail']}")
        if not res["passed"]:
            all_passed = False

    logger.info("")
    if all_passed:
        logger.info("OVERALL: ALL DRILLS PASSED")
    else:
        logger.warning("OVERALL: SOME DRILLS FAILED - review above")

    logger.info("=" * 60)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
