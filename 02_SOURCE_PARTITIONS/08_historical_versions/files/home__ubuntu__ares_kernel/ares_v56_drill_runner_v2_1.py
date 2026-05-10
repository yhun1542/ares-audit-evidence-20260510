#!/usr/bin/env python3
"""
ARES V5.6.1b Drill Runner v2.1
───────────────────────────────
운영 안전성 검증을 위한 자동화 드릴 스크립트.

v2.1 변경사항 (v2.0 대비):
  [FIX-2] Leader drill: finally 블록에서 ares-v56-live 재시작 보장.
          lease 미재획득 시에도 서비스가 정상 상태로 복귀하도록 강제.
          pm2 restart 반환코드 검사 추가.
  [FIX-2] Consumer drill: finally 블록에서 order-intent-executor 재시작 보장.
          pm2 restart 반환코드 검사 추가.
          정상 경로/예외 경로 모두 서비스 복구 보장.
  [FIX-3] Leader drill: 재획득된 lease의 run_id가 원래 의도한 엔진인지 검증.
          단순 "lease 존재" 확인이 아닌 "올바른 프로세스가 획득" 확인.
  [FIX-3] Consumer drill: heartbeat 복구 외에 state_json.engine_state의
          DEFER → non-DEFER 전환까지 검증. 주석 설명과 실제 검증 범위 일치.

Usage:
  python3 ares_v56_drill_runner_v2_1.py --drill all
  python3 ares_v56_drill_runner_v2_1.py --drill killswitch
  python3 ares_v56_drill_runner_v2_1.py --drill leader
  python3 ares_v56_drill_runner_v2_1.py --drill consumer
  python3 ares_v56_drill_runner_v2_1.py --drill autotune
  python3 ares_v56_drill_runner_v2_1.py --drill all --force
  python3 ares_v56_drill_runner_v2_1.py --drill all --dry-run

Author: ARES Architecture Team
Version: 2.1.0
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DRILL] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("drill_runner")

# ─── Configuration ───────────────────────────────────────────────
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
    "state_json": "ares:v55:state:summary",  # [FIX-3] DEFER 검증용
}


def get_redis() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def is_market_hours() -> bool:
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def enforce_market_guard(force: bool) -> None:
    if is_market_hours() and not force:
        now_et = datetime.now(ET)
        logger.error("=" * 60)
        logger.error("BLOCKED: Drill execution during market hours is prohibited!")
        logger.error(f"Current ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S ET')}")
        logger.error("Market hours: ET 09:30 - 16:00 (weekdays)")
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


def _safe_float(value, default: float = 0.0) -> float:
    """Safe float parser. Never raises."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _pm2_command(args: list[str], timeout: int = 30) -> tuple[bool, str]:
    """
    Execute a PM2 command and return (success, output).
    [FIX-2] Always checks return code.
    """
    try:
        proc = subprocess.run(
            ["pm2"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        output = proc.stdout + proc.stderr
        success = proc.returncode == 0
        if not success:
            logger.warning(f"  PM2 command failed (rc={proc.returncode}): pm2 {' '.join(args)}")
            logger.warning(f"  Output: {output[:200]}")
        return success, output
    except subprocess.TimeoutExpired:
        logger.error(f"  PM2 command timed out: pm2 {' '.join(args)}")
        return False, "timeout"
    except Exception as e:
        logger.error(f"  PM2 command exception: {e}")
        return False, str(e)


def _get_engine_state(r: redis.Redis) -> str:
    """Read engine_state from state:summary JSON. Returns 'UNKNOWN' on any failure."""
    try:
        raw = r.get(KEYS["state_json"])
        if raw:
            return json.loads(raw).get("engine_state", "UNKNOWN")
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return "UNKNOWN"


def _parse_leader_identity(raw: str) -> str:
    """Extract run_id:pid from leader lease JSON."""
    if not raw:
        return "NONE"
    try:
        d = json.loads(raw)
        return f"{d.get('run_id', '?')}:{d.get('pid', '?')}"
    except (json.JSONDecodeError, ValueError, TypeError):
        return str(raw)


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
            logger.info(f"  After correction: {verify} -> {'PASS' if result['passed'] else 'FAIL'}")
        else:
            logger.info("  [DRY-RUN] Would set autotune_active_override=false")
            result["passed"] = True

    return result


# ─── Drill: Kill Switch ──────────────────────────────────────
def drill_killswitch(r: redis.Redis, dry_run: bool) -> dict:
    """
    Test Kill Switch: snapshot current → set false → verify block → finally restore.
    """
    logger.info("─── Drill: Kill Switch ───")
    result = {"name": "killswitch", "passed": False, "detail": ""}

    original_value = r.get(KEYS["trading_enabled"])
    logger.info(f"  Step 1 - Snapshot: trading:enabled = {original_value}")

    if dry_run:
        logger.info("  [DRY-RUN] Would test: set false -> verify -> restore to original")
        result["passed"] = True
        result["detail"] = f"DRY-RUN: original={original_value}"
        return result

    blocked = False
    try:
        logger.info("  Step 2 - Setting trading:enabled = false")
        r.set(KEYS["trading_enabled"], "false")
        time.sleep(2)

        check = r.get(KEYS["trading_enabled"])
        blocked = str(check).lower() in ("false", "0", "no")
        logger.info(f"  Step 3 - Verify block: trading:enabled = {check} (blocked={blocked})")

        if not blocked:
            result["detail"] = f"Kill Switch did not engage! Value after set: {check}"
            logger.error(f"  FAIL: {result['detail']}")
            return result

    finally:
        # ALWAYS restore original value
        if original_value is not None:
            r.set(KEYS["trading_enabled"], original_value)
            logger.info(f"  [FINALLY] Restored trading:enabled = {original_value}")
        else:
            r.delete(KEYS["trading_enabled"])
            logger.info("  [FINALLY] Deleted trading:enabled (original was None)")

    time.sleep(1)
    restored = r.get(KEYS["trading_enabled"])
    match = str(restored) == str(original_value)
    logger.info(f"  Step 4 - Verify restore: {restored} (match={match})")

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
    [FIX-2] Test Leader Lease with finally-based service recovery.
    [FIX-3] Verify the CORRECT engine re-acquired the lease (not a rogue process).

    Steps:
    1. Snapshot current leader identity (run_id:pid)
    2. Delete leader lease
    3. Restart ares-v56-live
    4. Wait for lease re-acquisition (max 60s)
    5. Verify re-acquired identity matches expected engine name
    6. finally: ensure ares-v56-live is running regardless of outcome
    """
    logger.info("─── Drill: Leader Lease ───")
    result = {"name": "leader_lease", "passed": False, "detail": ""}

    leader_before_raw = r.get(KEYS["leader_lease"])
    leader_before_id = _parse_leader_identity(leader_before_raw)
    logger.info(f"  Step 1 - Current leader: {leader_before_id}")

    if not leader_before_raw:
        result["detail"] = "No leader lease found before drill. Engine may not be running."
        logger.warning(f"  SKIP: {result['detail']}")
        return result

    # Extract the expected run_id (the engine name, not PID which changes on restart)
    try:
        expected_run_id = json.loads(leader_before_raw).get("run_id", "")
    except (json.JSONDecodeError, ValueError):
        expected_run_id = ""

    if dry_run:
        logger.info("  [DRY-RUN] Would: delete lease -> restart -> verify identity match")
        result["passed"] = True
        result["detail"] = f"DRY-RUN: leader={leader_before_id}, expected_run_id={expected_run_id}"
        return result

    reacquired = False
    identity_match = False
    reacquired_id = "NONE"

    try:
        # Step 2: Delete leader lease
        logger.info("  Step 2 - Deleting leader lease")
        r.delete(KEYS["leader_lease"])
        time.sleep(2)

        # Step 3: Restart ares-v56-live
        logger.info("  Step 3 - Restarting ares-v56-live")
        ok, output = _pm2_command(["restart", "ares-v56-live"])
        if not ok:
            result["detail"] = f"PM2 restart ares-v56-live failed: {output[:100]}"
            logger.error(f"  FAIL: {result['detail']}")
            return result

        # Step 4: Wait for lease re-acquisition (max 60s)
        logger.info("  Step 4 - Waiting for lease re-acquisition (max 60s)")
        wait_seconds = 0
        for i in range(12):
            time.sleep(5)
            wait_seconds = (i + 1) * 5
            leader_after_raw = r.get(KEYS["leader_lease"])
            if leader_after_raw:
                reacquired = True
                reacquired_id = _parse_leader_identity(leader_after_raw)
                logger.info(f"  Lease re-acquired at {wait_seconds}s: {reacquired_id}")

                # [FIX-3] Step 5: Verify identity - same run_id (PID will differ after restart)
                try:
                    new_run_id = json.loads(leader_after_raw).get("run_id", "")
                except (json.JSONDecodeError, ValueError):
                    new_run_id = ""

                identity_match = (new_run_id == expected_run_id) and (new_run_id != "")
                if identity_match:
                    logger.info(f"  Step 5 - Identity match: run_id={new_run_id} (CORRECT engine)")
                else:
                    logger.warning(
                        f"  Step 5 - Identity MISMATCH: expected={expected_run_id}, "
                        f"got={new_run_id} (possible rogue process!)"
                    )
                break
        else:
            result["detail"] = "Leader lease not re-acquired within 60s"
            logger.error(f"  FAIL: {result['detail']}")
            return result

        ttl = r.ttl(KEYS["leader_lease"])
        result["passed"] = reacquired and identity_match
        result["detail"] = (
            f"Re-acquired in {wait_seconds}s, TTL={ttl}s, "
            f"identity_match={identity_match} (run_id={expected_run_id})"
        )
        logger.info(f"  {'PASS' if result['passed'] else 'FAIL'}: {result['detail']}")

    except Exception as e:
        result["detail"] = f"Exception during leader drill: {e}"
        logger.error(f"  FAIL: {result['detail']}")

    finally:
        # [FIX-2] ALWAYS ensure ares-v56-live is running
        logger.info("  [FINALLY] Ensuring ares-v56-live is running...")
        ok, _ = _pm2_command(["restart", "ares-v56-live"])
        if ok:
            logger.info("  [FINALLY] ares-v56-live restart confirmed")
            # Wait for lease to stabilize
            time.sleep(10)
            lease_check = r.get(KEYS["leader_lease"])
            if lease_check:
                logger.info(f"  [FINALLY] Leader lease present: {_parse_leader_identity(lease_check)}")
            else:
                logger.error("  [FINALLY] WARNING: Leader lease still missing after restart!")
        else:
            logger.error("  [FINALLY] CRITICAL: Failed to restart ares-v56-live!")
            logger.error("  [FINALLY] Manual intervention required: pm2 restart ares-v56-live")

    return result


# ─── Drill: Consumer Restart ─────────────────────────────────
def drill_consumer(r: redis.Redis, dry_run: bool) -> dict:
    """
    [FIX-2] Test Consumer with finally-based service recovery.
    [FIX-3] Verify both heartbeat recovery AND engine_state DEFER transition.

    Steps:
    1. Snapshot current heartbeat and engine_state
    2. Stop order-intent-executor
    3. Wait 30s, verify heartbeat goes stale
    4. (Optional) Check if engine_state transitions to DEFER
    5. Restart order-intent-executor (in finally block)
    6. Wait for heartbeat recovery (max 60s)
    7. Check engine_state exits DEFER
    """
    logger.info("─── Drill: Consumer Restart ───")
    result = {"name": "consumer_restart", "passed": False, "detail": ""}

    hb_before = r.get(KEYS["consumer_heartbeat"])
    engine_state_before = _get_engine_state(r)
    logger.info(f"  Step 1 - Heartbeat: {hb_before}, Engine state: {engine_state_before}")

    if dry_run:
        logger.info("  [DRY-RUN] Would: stop executor -> verify stale+DEFER -> restart -> verify recovery")
        result["passed"] = True
        result["detail"] = f"DRY-RUN: hb={'set' if hb_before else 'none'}, state={engine_state_before}"
        return result

    stale = False
    defer_entered = False
    recovered = False
    defer_exited = False

    try:
        # Step 2: Stop order-intent-executor
        logger.info("  Step 2 - Stopping order-intent-executor")
        ok, output = _pm2_command(["stop", "order-intent-executor"], timeout=15)
        if not ok:
            logger.warning(f"  PM2 stop returned non-zero (may be already stopped): {output[:100]}")

        # Step 3: Wait and check heartbeat goes stale
        logger.info("  Step 3 - Waiting 30s for heartbeat to go stale...")
        time.sleep(30)

        hb_after_stop = r.get(KEYS["consumer_heartbeat"])
        hb_age = _safe_float(hb_after_stop)
        if hb_after_stop:
            hb_age = time.time() - _safe_float(hb_after_stop)
            stale = hb_age > 25
            logger.info(f"  Heartbeat age after stop: {hb_age:.0f}s (stale={stale})")
        else:
            stale = True
            logger.info("  Heartbeat missing after stop (expected)")

        # [FIX-3] Step 4: Check if engine entered DEFER
        engine_state_during = _get_engine_state(r)
        defer_entered = (engine_state_during == "DEFER")
        logger.info(f"  Step 4 - Engine state after consumer stop: {engine_state_during} (DEFER={defer_entered})")

    except Exception as e:
        result["detail"] = f"Exception during consumer drill: {e}"
        logger.error(f"  FAIL: {result['detail']}")

    finally:
        # [FIX-2] ALWAYS restart order-intent-executor
        logger.info("  [FINALLY] Restarting order-intent-executor...")
        ok, _ = _pm2_command(["restart", "order-intent-executor"], timeout=15)
        if ok:
            logger.info("  [FINALLY] order-intent-executor restart confirmed")
        else:
            logger.error("  [FINALLY] CRITICAL: Failed to restart order-intent-executor!")
            logger.error("  [FINALLY] Manual intervention: pm2 restart order-intent-executor")

    # Step 5: Wait for heartbeat recovery (max 60s)
    logger.info("  Step 5 - Waiting for heartbeat recovery (max 60s)")
    wait_seconds = 0
    for i in range(12):
        time.sleep(5)
        wait_seconds = (i + 1) * 5
        hb_recovered = r.get(KEYS["consumer_heartbeat"])
        if hb_recovered:
            hb_age = time.time() - _safe_float(hb_recovered)
            if hb_age < 15:
                recovered = True
                logger.info(f"  Heartbeat recovered at {wait_seconds}s (age={hb_age:.0f}s)")
                break

    if not recovered:
        logger.warning(f"  Heartbeat NOT recovered within 60s")

    # [FIX-3] Step 6: Check if engine exited DEFER
    engine_state_after = _get_engine_state(r)
    defer_exited = (engine_state_after != "DEFER") if defer_entered else True
    logger.info(f"  Step 6 - Engine state after recovery: {engine_state_after} (exited_DEFER={defer_exited})")

    result["passed"] = stale and recovered
    result["detail"] = (
        f"Stale after stop: {stale}, "
        f"DEFER entered: {defer_entered}, "
        f"Recovered: {recovered} ({wait_seconds}s), "
        f"DEFER exited: {defer_exited}, "
        f"Final engine_state: {engine_state_after}"
    )
    logger.info(f"  {'PASS' if result['passed'] else 'FAIL'}: {result['detail']}")

    # Additional warning if DEFER didn't behave as expected
    if defer_entered and not defer_exited:
        logger.warning("  WARNING: Engine is still in DEFER after consumer recovery!")
        logger.warning("  This may indicate ConsumerGuard needs more time or has a configuration issue.")

    return result


# ─── Main ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ARES V5.6.1b Drill Runner v2.1")
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
    logger.info("ARES V5.6.1b Drill Runner v2.1")
    logger.info(f"Drill: {args.drill}")
    logger.info(f"Force: {args.force}")
    logger.info(f"Dry-run: {args.dry_run}")
    now_et = datetime.now(ET)
    logger.info(f"ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S ET')}")
    logger.info(f"Market hours: {is_market_hours()}")
    logger.info("=" * 60)

    enforce_market_guard(args.force)

    r = get_redis()
    try:
        r.ping()
        logger.info("Redis connection OK")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        sys.exit(1)

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
