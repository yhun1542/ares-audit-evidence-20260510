"""
leader_lease_simplify.py — Leader Lease 단순화 패치
=====================================================
First Principles Patch: Leader Lease 역설 해결

문제:
  - 단일 머신 + PM2 instances:1 환경에서 split-brain 방지가 불필요
  - Redis 쓰기 실패 시 lease 획득 불가 → RuntimeError → PM2 재시작 → 반복
  - pipeline-guard가 lease를 삭제해줘야 함 → 외부 프로세스에 의존하는 자기 복구
  - 이전 프로세스의 lease가 남아있으면 30초 × 6회 = 180초 대기 후 RuntimeError

해결:
  1. Leader Lease를 "soft lock"으로 변경: 실패해도 진행 가능
  2. Redis 쓰기 실패 시 로컬 파일 기반 fallback
  3. 시작 시 무조건 이전 lease 인수 (같은 머신이므로 안전)
  4. lease 획득 실패가 RuntimeError가 아닌 WARNING으로 변경

적용 방법:
  ares_v55_live_autopilot.py의 _acquire_leader_lease() 메서드를 교체

@version 1.0.0
@date 2026-04-11
"""
import os
import time
import json
import logging

log = logging.getLogger("ares.leader_lease")

# ── 로컬 파일 기반 fallback ──
LOCAL_LEASE_FILE = "/tmp/ares_v56_leader_lease.json"


def acquire_leader_lease_simplified(self):
    """Leader Lease 단순화 버전.

    변경점:
    1. Redis 쓰기 실패 → WARNING + 로컬 파일 fallback (RuntimeError 대신)
    2. 이전 프로세스의 lease → 같은 머신이면 무조건 인수
    3. 최대 대기 시간: 10초 (기존 180초에서 단축)
    4. lease 없이도 진행 가능 (degraded mode)
    """
    lease_key = getattr(self, 'LEADER_LEASE_KEY', 'ares:v56:leader:lease')
    my_pid = os.getpid()
    my_hostname = os.uname().nodename
    lease_value = json.dumps({
        "pid": my_pid,
        "hostname": my_hostname,
        "ts": int(time.time() * 1000),
        "version": "simplified-v1.0"
    })

    # ── 1단계: Redis lease 시도 (최대 2회, 5초 간격) ──
    for attempt in range(2):
        try:
            existing = self.r.get(lease_key)
            if existing:
                try:
                    existing_data = json.loads(existing if isinstance(existing, str)
                                               else existing.decode())
                    existing_hostname = existing_data.get("hostname", "")
                    existing_pid = existing_data.get("pid", 0)

                    # 같은 머신이면 무조건 인수 (split-brain 불가)
                    if existing_hostname == my_hostname:
                        log.info("Same host lease found (PID=%s), taking over", existing_pid)
                        # 이전 프로세스가 살아있는지 확인 (정보 로깅용)
                        try:
                            os.kill(existing_pid, 0)
                            log.warning("Previous process PID=%s still alive, "
                                        "but PM2 manages lifecycle — taking over", existing_pid)
                        except ProcessLookupError:
                            log.info("Previous process PID=%s already dead", existing_pid)
                    else:
                        # 다른 머신의 lease — 이 경우에만 대기 (실제로는 발생하지 않을 것)
                        log.warning("Different host lease found: %s (mine: %s). "
                                    "Waiting 5s before takeover...",
                                    existing_hostname, my_hostname)
                        time.sleep(5)
                except (json.JSONDecodeError, KeyError):
                    log.warning("Unparseable lease, taking over")

            # Lease 쓰기 (SET NX가 아닌 SET — 무조건 덮어쓰기)
            self.r.set(lease_key, lease_value, ex=300)  # 5분 TTL
            log.info("Leader lease acquired via Redis (PID=%s)", my_pid)
            return True

        except Exception as e:
            log.warning("Redis lease attempt %d failed: %s", attempt + 1, e)
            time.sleep(2)

    # ── 2단계: Redis 실패 → 로컬 파일 fallback ──
    try:
        with open(LOCAL_LEASE_FILE, 'w') as f:
            json.dump({
                "pid": my_pid,
                "hostname": my_hostname,
                "ts": int(time.time() * 1000),
                "mode": "local_fallback",
                "reason": "redis_write_failed"
            }, f)
        log.warning("Leader lease acquired via LOCAL FILE fallback (Redis unavailable)")
        return True
    except Exception as e:
        log.error("Local file fallback also failed: %s", e)

    # ── 3단계: 모든 방법 실패 → 경고하되 진행 (degraded mode) ──
    log.error("ALL lease methods failed — proceeding in DEGRADED MODE "
              "(no leader lease, risk of duplicate if another instance exists)")
    return True  # RuntimeError 대신 True 반환 — 진행 허용


def renew_leader_lease_simplified(self):
    """Leader Lease 갱신 — 실패해도 크래시하지 않음."""
    lease_key = getattr(self, 'LEADER_LEASE_KEY', 'ares:v56:leader:lease')
    my_pid = os.getpid()
    try:
        self.r.set(lease_key, json.dumps({
            "pid": my_pid,
            "hostname": os.uname().nodename,
            "ts": int(time.time() * 1000),
            "version": "simplified-v1.0"
        }), ex=300)
    except Exception as e:
        log.warning("Leader lease renewal failed (non-fatal): %s", e)
        # 갱신 실패는 non-fatal — 다음 사이클에 재시도


# ── 적용 안내 ──
# ares_v55_live_autopilot.py에서:
# 1. _acquire_leader_lease() → acquire_leader_lease_simplified() 교체
# 2. _renew_leader_lease() → renew_leader_lease_simplified() 교체
# 3. RuntimeError("Could not acquire leader lease") 제거
# 4. LEADER_LEASE_RETRIES, LEADER_LEASE_WAIT_SEC 상수 제거 가능
