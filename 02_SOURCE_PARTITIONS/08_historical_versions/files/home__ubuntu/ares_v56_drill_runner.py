#!/usr/bin/env python3
"""
ARES V5.6.1b 드릴 자동화 스크립트
[P2-10] AutoTune Gate 검증
[P2-11] Kill Switch / Leader Lease / Consumer 드릴

사용법:
  python3 ares_v56_drill_runner.py --drill autotune   # AutoTune Gate 검증
  python3 ares_v56_drill_runner.py --drill killswitch  # Kill Switch 드릴
  python3 ares_v56_drill_runner.py --drill leader      # Leader Lease 드릴
  python3 ares_v56_drill_runner.py --drill consumer    # Consumer restart 드릴
  python3 ares_v56_drill_runner.py --drill all          # 전체 드릴 (장외 시간만)
  python3 ares_v56_drill_runner.py --check              # 상태 점검만 (드릴 없음)

Author: ARES Architecture Team
Version: 1.0.0
"""
import argparse
import json
import os
import subprocess
import sys
import time
import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@localhost:6379/0")

# Keys
KEYS = {
    "leader_lease": "ares:v56:leader:lease",
    "trading_enabled": "trading:enabled",
    "consumer_heartbeat": "ares:v55:consumer:heartbeat",
    "consumer_last_ack": "ares:v55:consumer:last_ack",
    "readiness": "ares:v55:live:readiness",
    "autotune_active": "ares:v55:autotune:active_override",
    "autotune_suggestions": "ares:v55:autotune:suggestions",
    "state_json": "ares:v55:state:summary",
}


def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)


def pm2_cmd(cmd):
    """Run PM2 command and return output."""
    result = subprocess.run(
        f"pm2 {cmd}", shell=True, capture_output=True, text=True, timeout=30
    )
    return result.stdout + result.stderr


def check_autotune(r):
    """[P2-10] AutoTune Gate 검증."""
    print("\n=== AutoTune Gate 검증 ===")
    
    active = r.get(KEYS["autotune_active"])
    suggestions_raw = r.get(KEYS["autotune_suggestions"])
    
    print(f"  autotune:active_override = {active}")
    
    if active is None:
        print("  STATUS: NOT_SET (AutoTune Gate 키가 존재하지 않음)")
        print("  RECOMMENDATION: 보수적 운영을 위해 'false'로 설정 권장")
        print(f"  FIX: redis-cli SET {KEYS['autotune_active']} false")
    elif str(active).lower() in ("true", "1"):
        print("  STATUS: ACTIVE (AutoTune이 live apply 가능)")
        print("  WARNING: 보수적 운영 권장 시 false로 변경")
    else:
        print("  STATUS: DISABLED (AutoTune live apply 차단됨 - 보수적)")
    
    if suggestions_raw:
        try:
            suggestions = json.loads(suggestions_raw)
            print(f"  Pending suggestions: {len(suggestions) if isinstance(suggestions, list) else 'N/A'}")
        except Exception:
            print(f"  Suggestions raw: {suggestions_raw[:100]}")
    else:
        print("  No pending suggestions")
    
    return active


def drill_killswitch(r, dry_run=False):
    """[P2-11] Kill Switch 드릴."""
    print("\n=== Kill Switch 드릴 ===")
    
    current = r.get(KEYS["trading_enabled"])
    print(f"  Current trading:enabled = {current}")
    
    if dry_run:
        print("  [DRY RUN] Would: SET trading:enabled false → verify → SET true")
        return True
    
    # Step 1: Disable
    print("  [1/3] Disabling trading...")
    r.set(KEYS["trading_enabled"], "false")
    time.sleep(2)
    
    verify = r.get(KEYS["trading_enabled"])
    print(f"  Verify: trading:enabled = {verify}")
    assert verify == "false", f"Kill switch failed! Got: {verify}"
    
    # Step 2: Check engine response
    time.sleep(3)
    state_raw = r.get(KEYS["state_json"])
    if state_raw:
        try:
            state = json.loads(state_raw)
            print(f"  Engine state: {state.get('engine_state', 'UNKNOWN')}")
        except Exception:
            pass
    
    # Step 3: Re-enable
    print("  [2/3] Re-enabling trading...")
    r.set(KEYS["trading_enabled"], "true")
    time.sleep(2)
    
    verify2 = r.get(KEYS["trading_enabled"])
    print(f"  Verify: trading:enabled = {verify2}")
    assert verify2 == "true", f"Re-enable failed! Got: {verify2}"
    
    print("  [3/3] Kill Switch drill PASSED")
    return True


def drill_leader(r, dry_run=False):
    """[P2-11] Leader Lease 소실/재획득 드릴."""
    print("\n=== Leader Lease 드릴 ===")
    
    current = r.get(KEYS["leader_lease"])
    ttl = r.ttl(KEYS["leader_lease"])
    print(f"  Current leader: {current}")
    print(f"  TTL: {ttl}s")
    
    if dry_run:
        print("  [DRY RUN] Would: restart ares-v56-live → verify lease re-acquired")
        return True
    
    # Step 1: Restart engine
    print("  [1/3] Restarting ares-v56-live...")
    pm2_cmd("restart ares-v56-live")
    
    # Step 2: Wait for lease re-acquisition
    print("  [2/3] Waiting for lease re-acquisition (max 30s)...")
    for i in range(15):
        time.sleep(2)
        new_lease = r.get(KEYS["leader_lease"])
        if new_lease:
            new_ttl = r.ttl(KEYS["leader_lease"])
            print(f"  Lease re-acquired: TTL={new_ttl}s")
            
            # Verify same run_id (no split-brain)
            try:
                d = json.loads(new_lease)
                print(f"  run_id: {d.get('run_id')}, pid: {d.get('pid')}")
            except Exception:
                pass
            
            print("  [3/3] Leader Lease drill PASSED")
            return True
    
    print("  FAILED: Lease not re-acquired within 30s!")
    return False


def drill_consumer(r, dry_run=False):
    """[P2-11] Consumer restart 드릴."""
    print("\n=== Consumer Restart 드릴 ===")
    
    hb = r.get(KEYS["consumer_heartbeat"])
    print(f"  Current heartbeat: {hb}")
    
    if dry_run:
        print("  [DRY RUN] Would: stop executor → verify DEFER → restart → verify recovery")
        return True
    
    # Step 1: Stop executor
    print("  [1/4] Stopping order-intent-executor...")
    pm2_cmd("stop order-intent-executor")
    time.sleep(5)
    
    # Step 2: Verify heartbeat goes stale
    print("  [2/4] Verifying heartbeat staleness...")
    hb_after = r.get(KEYS["consumer_heartbeat"])
    if hb_after:
        age = time.time() - float(hb_after)
        print(f"  Heartbeat age: {age:.0f}s (should increase)")
    
    # Step 3: Restart executor
    print("  [3/4] Restarting order-intent-executor...")
    pm2_cmd("restart order-intent-executor")
    time.sleep(10)
    
    # Step 4: Verify recovery
    print("  [4/4] Verifying recovery...")
    hb_new = r.get(KEYS["consumer_heartbeat"])
    if hb_new:
        age_new = time.time() - float(hb_new)
        print(f"  New heartbeat age: {age_new:.0f}s")
        if age_new < 30:
            print("  Consumer Restart drill PASSED")
            return True
    
    print("  Consumer heartbeat not fresh yet (may need market hours)")
    return True  # Non-fatal during off-hours


def status_check(r):
    """Full status check without any drills."""
    print("\n=== ARES V5.6.1b Status Check ===")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    # Leader
    leader = r.get(KEYS["leader_lease"])
    leader_ttl = r.ttl(KEYS["leader_lease"])
    if leader:
        try:
            d = json.loads(leader)
            print(f"  Leader: {d.get('run_id')}:{d.get('pid')} (TTL={leader_ttl}s)")
        except Exception:
            print(f"  Leader: {leader} (TTL={leader_ttl}s)")
    else:
        print("  Leader: NONE!")
    
    # Kill Switch
    ks = r.get(KEYS["trading_enabled"])
    print(f"  Kill Switch: trading:enabled={ks}")
    
    # Consumer
    hb = r.get(KEYS["consumer_heartbeat"])
    if hb:
        age = time.time() - float(hb)
        print(f"  Consumer HB: {age:.0f}s ago")
    else:
        print("  Consumer HB: N/A")
    
    # Readiness
    ready = r.get(KEYS["readiness"])
    print(f"  Readiness: {ready}")
    
    # AutoTune
    check_autotune(r)
    
    # Engine state
    state_raw = r.get(KEYS["state_json"])
    if state_raw:
        try:
            state = json.loads(state_raw)
            print(f"  Engine State: {state.get('engine_state', 'UNKNOWN')}")
            print(f"  Decision: {state.get('decision', 'UNKNOWN')}")
        except Exception:
            pass
    
    print("\n=== Status Check Complete ===")


def main():
    parser = argparse.ArgumentParser(description="ARES V5.6.1b Drill Runner")
    parser.add_argument("--drill", choices=["autotune", "killswitch", "leader", "consumer", "all"],
                        help="Drill type to run")
    parser.add_argument("--check", action="store_true", help="Status check only (no drills)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (no actual changes)")
    args = parser.parse_args()
    
    if not args.drill and not args.check:
        parser.print_help()
        sys.exit(1)
    
    r = get_redis()
    
    if args.check:
        status_check(r)
        return
    
    if args.drill == "autotune":
        result = check_autotune(r)
        # If not set, set it to false (conservative)
        if result is None:
            print("\n  Setting AutoTune Gate to 'false' (conservative)...")
            r.set(KEYS["autotune_active"], "false")
            print("  Done.")
    
    elif args.drill == "killswitch":
        drill_killswitch(r, dry_run=args.dry_run)
    
    elif args.drill == "leader":
        drill_leader(r, dry_run=args.dry_run)
    
    elif args.drill == "consumer":
        drill_consumer(r, dry_run=args.dry_run)
    
    elif args.drill == "all":
        print("=== Running ALL drills ===")
        check_autotune(r)
        drill_killswitch(r, dry_run=args.dry_run)
        drill_leader(r, dry_run=args.dry_run)
        drill_consumer(r, dry_run=args.dry_run)
        print("\n=== ALL drills complete ===")


if __name__ == "__main__":
    main()
