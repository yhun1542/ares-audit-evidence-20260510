#!/usr/bin/env python3
"""
shadow_balance_key_manager.py
==============================
ARES Shadow Balance Key Manager — Polling 기반 TTL 자동 부여 + 강제 만료 데몬

목적:
  1. [TTL 자동 부여] kis:balance_shadow:* 키 중 TTL이 없는 키(TTL=-1)를 주기적으로
     스캔하여 DEFAULT_TTL(7일)을 자동으로 부여한다.
  2. [메모리 임계치 초과 시 강제 만료] Redis 메모리 사용량이 MEMORY_THRESHOLD_BYTES를
     초과하거나 kis:balance_shadow:* 키 수가 KEY_COUNT_THRESHOLD를 초과하면,
     TTL이 가장 짧게 남은(=가장 오래된) 키부터 EVICT_BATCH_SIZE개씩 강제 DEL한다.
  3. [Heartbeat] ares:sentinel:shadow_balance_key_manager:heartbeat 키를 600초 TTL로
     갱신하여 외부에서 생존 여부를 모니터링할 수 있게 한다.
  4. [감사 로그] 모든 eviction 이벤트를 shadow_balance_key_manager:audit stream에 기록한다.

설계 원칙:
  - AWS ElastiCache는 CONFIG 명령어가 차단되어 Keyspace Notification 직접 활성화 불가
  - 따라서 Polling(SCAN) 기반으로 동작하며, SCAN은 cursor 방식으로 분산 처리
  - 프로덕션 키(kis:live:*, ares:final_trade_gate 등)에 절대 영향을 주지 않음
  - 모든 DEL 전에 감사 로그(XADD) 기록
  - SIGTERM/SIGINT 수신 시 graceful shutdown

환경변수:
  REDIS_URL            Redis 연결 URL (rediss:// TLS 포함)
  ARES_REDIS_URL       대체 Redis URL
  SKM_CYCLE_S          메인 루프 주기 (기본: 300초 = 5분)
  SKM_TTL_SCAN_COUNT   SCAN COUNT 파라미터 (기본: 500)
  SKM_DEFAULT_TTL      TTL 없는 키에 부여할 TTL 초 (기본: 604800 = 7일)
  SKM_MEMORY_THRESHOLD 강제 만료 트리거 메모리 임계치 바이트 (기본: 1610612736 = 1.5GB)
  SKM_KEY_COUNT_THRESHOLD 강제 만료 트리거 키 수 임계치 (기본: 50000)
  SKM_EVICT_BATCH_SIZE 1회 강제 만료 키 수 (기본: 1000)
  SKM_EVICT_TARGET_TTL 강제 만료 시 이 TTL 이하인 키를 우선 삭제 (기본: 86400 = 1일)
  SKM_DRY_RUN          1이면 실제 DEL/EXPIRE 없이 로그만 출력 (기본: 0)
"""

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

# ─── 설정 상수 ────────────────────────────────────────────────────────────────
REDIS_URL = os.environ.get("REDIS_URL") or os.environ.get("ARES_REDIS_URL")

SCAN_PATTERN          = "kis:balance_shadow:*"
HEARTBEAT_KEY         = "ares:sentinel:shadow_balance_key_manager:heartbeat"
HEARTBEAT_TTL         = 600
AUDIT_STREAM          = "shadow_balance_key_manager:audit"
AUDIT_STREAM_MAXLEN   = 5000
STATUS_KEY            = "ares:sentinel:shadow_balance_key_manager:status"
STATUS_TTL            = 660  # heartbeat보다 약간 길게

CYCLE_S               = int(os.environ.get("SKM_CYCLE_S", "300"))
TTL_SCAN_COUNT        = int(os.environ.get("SKM_TTL_SCAN_COUNT", "500"))
DEFAULT_TTL           = int(os.environ.get("SKM_DEFAULT_TTL", str(7 * 24 * 3600)))  # 7일
MEMORY_THRESHOLD      = int(os.environ.get("SKM_MEMORY_THRESHOLD", str(1610612736)))  # 1.5GB
KEY_COUNT_THRESHOLD   = int(os.environ.get("SKM_KEY_COUNT_THRESHOLD", "50000"))
EVICT_BATCH_SIZE      = int(os.environ.get("SKM_EVICT_BATCH_SIZE", "1000"))
EVICT_TARGET_TTL      = int(os.environ.get("SKM_EVICT_TARGET_TTL", str(24 * 3600)))  # 1일
DRY_RUN               = os.environ.get("SKM_DRY_RUN", "0") == "1"

# ─── 전역 상태 ────────────────────────────────────────────────────────────────
_running = True
_cycle_count = 0
_total_ttl_assigned = 0
_total_evicted = 0


def _shutdown(signum, frame):
    global _running
    _running = False
    print(f"[INFO] received signal {signum}, shutting down gracefully", flush=True)


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def get_redis():
    """Redis 클라이언트 생성 (TLS 지원)"""
    import redis as redis_lib
    url = REDIS_URL
    if not url:
        host = os.environ.get("REDIS_HOST", "master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com")
        port = int(os.environ.get("REDIS_PORT", "6379"))
        password = os.environ.get("REDIS_PASSWORD", "")
        username = os.environ.get("REDIS_USERNAME", "ares-admin")
        url = f"rediss://{username}:{password}@{host}:{port}/0"
    return redis_lib.Redis.from_url(url, ssl_cert_reqs=None, decode_responses=True)


def get_memory_usage(r) -> int:
    """Redis used_memory 바이트 반환. 실패 시 0 반환."""
    try:
        info = r.info("memory")
        return int(info.get("used_memory", 0))
    except Exception as e:
        print(f"[WARN] memory info 조회 실패: {e}", flush=True)
        return 0


def count_shadow_keys(r) -> int:
    """kis:balance_shadow:* 키 총 개수를 SCAN으로 집계."""
    count = 0
    cursor = 0
    try:
        while True:
            cursor, keys = r.scan(cursor=cursor, match=SCAN_PATTERN, count=TTL_SCAN_COUNT)
            count += len(keys)
            if cursor == 0:
                break
    except Exception as e:
        print(f"[WARN] 키 개수 집계 실패: {e}", flush=True)
    return count


def assign_missing_ttl(r) -> dict:
    """
    TTL이 없는(TTL=-1) kis:balance_shadow:* 키에 DEFAULT_TTL을 부여한다.
    Returns: {"scanned": int, "assigned": int, "errors": int}
    """
    result = {"scanned": 0, "assigned": 0, "errors": 0}
    cursor = 0
    try:
        while True:
            cursor, keys = r.scan(cursor=cursor, match=SCAN_PATTERN, count=TTL_SCAN_COUNT)
            for key in keys:
                result["scanned"] += 1
                try:
                    ttl = r.ttl(key)
                    if ttl == -1:  # TTL 없음
                        if not DRY_RUN:
                            r.expire(key, DEFAULT_TTL)
                        result["assigned"] += 1
                        if result["assigned"] <= 10:  # 처음 10개만 로그
                            print(f"[TTL] assigned {DEFAULT_TTL}s → {key}", flush=True)
                except Exception as e:
                    result["errors"] += 1
                    print(f"[ERROR] TTL 부여 실패 {key}: {e}", flush=True)
            if cursor == 0:
                break
    except Exception as e:
        print(f"[ERROR] TTL 부여 SCAN 실패: {e}", flush=True)
    return result


def evict_oldest_keys(r, target_count: int) -> dict:
    """
    TTL이 EVICT_TARGET_TTL 이하로 남은 키를 우선 수집하여 target_count개 DEL.
    TTL이 짧은 키 = 곧 만료될 키 = 가장 오래된 키로 간주.
    Returns: {"collected": int, "evicted": int, "errors": int}
    """
    result = {"collected": 0, "evicted": 0, "errors": 0}
    candidates = []  # (ttl, key)
    cursor = 0

    print(f"[EVICT] 강제 만료 대상 수집 시작 (목표: {target_count}개)", flush=True)

    try:
        while len(candidates) < target_count * 3:  # 충분한 후보 수집
            cursor, keys = r.scan(cursor=cursor, match=SCAN_PATTERN, count=TTL_SCAN_COUNT)
            for key in keys:
                try:
                    ttl = r.ttl(key)
                    if 0 < ttl <= EVICT_TARGET_TTL:
                        candidates.append((ttl, key))
                except Exception:
                    pass
            result["collected"] += len(keys)
            if cursor == 0:
                break
    except Exception as e:
        print(f"[ERROR] eviction 후보 수집 실패: {e}", flush=True)
        return result

    # TTL 오름차순 정렬 (가장 짧은 = 가장 오래된 키 우선)
    candidates.sort(key=lambda x: x[0])
    to_evict = candidates[:target_count]

    print(f"[EVICT] 후보 {len(candidates)}개 수집, {len(to_evict)}개 DEL 예정", flush=True)

    for ttl, key in to_evict:
        try:
            # 감사 로그 기록
            audit_entry = {
                "ts": now_iso(),
                "key": key,
                "ttl_remaining": str(ttl),
                "reason": "memory_pressure_eviction",
                "dry_run": "1" if DRY_RUN else "0",
            }
            r.xadd(AUDIT_STREAM, audit_entry, maxlen=AUDIT_STREAM_MAXLEN, approximate=True)

            if not DRY_RUN:
                r.delete(key)
            result["evicted"] += 1
        except Exception as e:
            result["errors"] += 1
            print(f"[ERROR] DEL 실패 {key}: {e}", flush=True)

    return result


def write_heartbeat(r, cycle_summary: dict):
    """Heartbeat 및 상태 키 갱신"""
    try:
        payload = json.dumps({
            "ts": now_iso(),
            "cycle": _cycle_count,
            "total_ttl_assigned": _total_ttl_assigned,
            "total_evicted": _total_evicted,
            "last_cycle": cycle_summary,
            "dry_run": DRY_RUN,
            "config": {
                "cycle_s": CYCLE_S,
                "default_ttl": DEFAULT_TTL,
                "memory_threshold_gb": round(MEMORY_THRESHOLD / 1073741824, 2),
                "key_count_threshold": KEY_COUNT_THRESHOLD,
                "evict_batch_size": EVICT_BATCH_SIZE,
            },
        })
        r.setex(HEARTBEAT_KEY, HEARTBEAT_TTL, payload)
        r.setex(STATUS_KEY, STATUS_TTL, payload)
    except Exception as e:
        print(f"[ERROR] heartbeat 갱신 실패: {e}", flush=True)


def run_cycle(r) -> dict:
    """메인 사이클 실행"""
    global _total_ttl_assigned, _total_evicted, _cycle_count
    _cycle_count += 1

    summary = {
        "cycle": _cycle_count,
        "ts": now_iso(),
        "memory_bytes": 0,
        "key_count": 0,
        "ttl_assigned": 0,
        "evicted": 0,
        "eviction_triggered": False,
        "errors": 0,
    }

    # 1. 메모리 사용량 확인
    mem_bytes = get_memory_usage(r)
    summary["memory_bytes"] = mem_bytes
    mem_gb = mem_bytes / 1073741824
    print(f"[CYCLE #{_cycle_count}] 메모리: {mem_gb:.2f}GB | 임계치: {MEMORY_THRESHOLD/1073741824:.2f}GB", flush=True)

    # 2. kis:balance_shadow:* 키 수 확인
    key_count = count_shadow_keys(r)
    summary["key_count"] = key_count
    print(f"[CYCLE #{_cycle_count}] kis:balance_shadow:* 키 수: {key_count:,} | 임계치: {KEY_COUNT_THRESHOLD:,}", flush=True)

    # 3. TTL 없는 키에 자동 TTL 부여
    ttl_result = assign_missing_ttl(r)
    summary["ttl_assigned"] = ttl_result["assigned"]
    summary["errors"] += ttl_result["errors"]
    _total_ttl_assigned += ttl_result["assigned"]

    if ttl_result["assigned"] > 0:
        print(
            f"[TTL] 스캔={ttl_result['scanned']:,} | TTL부여={ttl_result['assigned']:,} | 오류={ttl_result['errors']}",
            flush=True,
        )

    # 4. 임계치 초과 시 강제 만료
    memory_over = mem_bytes > MEMORY_THRESHOLD
    count_over = key_count > KEY_COUNT_THRESHOLD

    if memory_over or count_over:
        summary["eviction_triggered"] = True
        reason = []
        if memory_over:
            reason.append(f"메모리 {mem_gb:.2f}GB > {MEMORY_THRESHOLD/1073741824:.2f}GB")
        if count_over:
            reason.append(f"키 수 {key_count:,} > {KEY_COUNT_THRESHOLD:,}")

        print(f"[ALERT] 임계치 초과! 강제 만료 실행: {' | '.join(reason)}", flush=True)

        # 감사 로그 - 트리거 이벤트
        try:
            r.xadd(
                AUDIT_STREAM,
                {
                    "ts": now_iso(),
                    "event": "eviction_triggered",
                    "reason": " | ".join(reason),
                    "memory_bytes": str(mem_bytes),
                    "key_count": str(key_count),
                },
                maxlen=AUDIT_STREAM_MAXLEN,
                approximate=True,
            )
        except Exception:
            pass

        evict_result = evict_oldest_keys(r, EVICT_BATCH_SIZE)
        summary["evicted"] = evict_result["evicted"]
        summary["errors"] += evict_result["errors"]
        _total_evicted += evict_result["evicted"]

        print(
            f"[EVICT] 완료: 수집={evict_result['collected']:,} | 삭제={evict_result['evicted']:,} | 오류={evict_result['errors']}",
            flush=True,
        )
    else:
        print(
            f"[OK] 임계치 이하 — 메모리: {mem_gb:.2f}GB | 키 수: {key_count:,}",
            flush=True,
        )

    # 5. Heartbeat 갱신
    write_heartbeat(r, summary)

    return summary


def main() -> int:
    dry_label = " [DRY-RUN]" if DRY_RUN else ""
    print(
        f"[INFO] shadow-balance-key-manager 시작{dry_label} | "
        f"cycle={CYCLE_S}s | default_ttl={DEFAULT_TTL}s | "
        f"memory_threshold={MEMORY_THRESHOLD/1073741824:.2f}GB | "
        f"key_count_threshold={KEY_COUNT_THRESHOLD:,} | "
        f"evict_batch={EVICT_BATCH_SIZE}",
        flush=True,
    )

    while _running:
        try:
            r = get_redis()
            summary = run_cycle(r)
            print(
                f"[SUMMARY] cycle={summary['cycle']} | "
                f"ttl_assigned={summary['ttl_assigned']} | "
                f"evicted={summary['evicted']} | "
                f"eviction_triggered={summary['eviction_triggered']}",
                flush=True,
            )
        except Exception as e:
            print(f"[ERROR] 사이클 예외: {e}", flush=True)

        # 다음 사이클까지 대기 (1초 단위로 shutdown 체크)
        for _ in range(CYCLE_S):
            if not _running:
                break
            time.sleep(1)

    print("[INFO] shadow-balance-key-manager 정상 종료", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
