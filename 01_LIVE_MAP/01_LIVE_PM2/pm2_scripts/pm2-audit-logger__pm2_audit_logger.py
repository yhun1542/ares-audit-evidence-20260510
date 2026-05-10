#!/usr/bin/env python3
"""
pm2-audit-logger
================
P4-5: PM2 process lifecycle event audit sidecar.

목적:
- ARES 시스템의 ~30개 PM2 프로세스의 restart/stop/online/error 이벤트를
  Redis stream `monitor:pm2:audit` 에 timestamped 구조화 레코드로 발행.
- 외부 운영자/스크립트가 트리거한 PM2 reload + crash-loop을 모두 attribution 가능하게 만듦.
- 향후 P4-2/P4-3 분석 시 누가 언제 어떤 process를 reload 했는지 추적.

설계 원칙 (3-AI 컨센서스 반영):
- 폴링 주기 30초 (PM2 jlist JSON 파싱).
- 이전 cycle 의 (pid, restart_count, status) snapshot 과 비교하여 변화 감지.
- restart_count 가 증가하면 RESTART 이벤트, status 가 바뀌면 STATUS_CHANGE 이벤트.
- Stream MAXLEN=10000 (대용량 restart 폭주 시 메모리 보호).
- 어떤 process 도 직접 변경하지 않음 (read-only audit only).
- ARES_REDIS_URL 또는 REDIS_URL 환경변수로 Redis 접속.
- heartbeat: ares:sentinel:pm2_audit:heartbeat (60s TTL).
"""
import os
import sys
import json
import time
import subprocess
from datetime import datetime, timezone

REDIS_URL = os.environ.get("ARES_REDIS_URL") or os.environ.get("REDIS_URL")
if not REDIS_URL:
    print("[FATAL] ARES_REDIS_URL/REDIS_URL not set in env", flush=True)
    sys.exit(2)

try:
    import redis
except ImportError:
    print("[FATAL] python redis lib not installed", flush=True)
    sys.exit(2)

CYCLE_S = 30
HEARTBEAT_KEY = "ares:sentinel:pm2_audit:heartbeat"
HEARTBEAT_TTL = 90
AUDIT_STREAM = "monitor:pm2:audit"
STREAM_MAXLEN = 10000


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def get_pm2_snapshot() -> dict:
    """Return {pm_id: {name, pid, status, restart, uptime_ms}} from `pm2 jlist`."""
    try:
        result = subprocess.run(
            ["pm2", "jlist"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[WARN] pm2 jlist failed: {e}", flush=True)
        return {}

    snapshot = {}
    for proc in data:
        pm_id = proc.get("pm_id")
        if pm_id is None:
            continue
        pm2_env = proc.get("pm2_env", {}) or {}
        snapshot[pm_id] = {
            "name": proc.get("name", "?"),
            "pid": proc.get("pid", 0),
            "status": pm2_env.get("status", "?"),
            "restart": pm2_env.get("restart_time", 0),
            "unstable_restarts": pm2_env.get("unstable_restarts", 0),
            "uptime_ms": pm2_env.get("pm_uptime", 0),
            "exit_code": pm2_env.get("exit_code", None),
        }
    return snapshot


def emit_event(r, event_type: str, pm_id: int, name: str, fields: dict) -> None:
    record = {
        "ts": now_iso(),
        "type": event_type,
        "pm_id": str(pm_id),
        "name": name,
    }
    for k, v in fields.items():
        record[k] = str(v) if v is not None else ""
    try:
        r.xadd(AUDIT_STREAM, record, maxlen=STREAM_MAXLEN, approximate=True)
        print(f"[AUDIT] {event_type} pm_id={pm_id} name={name} fields={fields}", flush=True)
    except Exception as e:
        print(f"[ERR] xadd failed: {e}", flush=True)


def emit_heartbeat(r, cycle: int, watched: int, events_this_cycle: int) -> None:
    payload = {
        "ts": now_iso(),
        "cycle": cycle,
        "watched_processes": watched,
        "events_this_cycle": events_this_cycle,
        "stream": AUDIT_STREAM,
    }
    try:
        r.set(HEARTBEAT_KEY, json.dumps(payload), ex=HEARTBEAT_TTL)
    except Exception as e:
        print(f"[ERR] heartbeat set failed: {e}", flush=True)


def main():
    print(f"[INFO] pm2-audit-logger starting | cycle={CYCLE_S}s | stream={AUDIT_STREAM}", flush=True)
    r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=10, socket_connect_timeout=10)
    try:
        r.ping()
    except Exception as e:
        print(f"[FATAL] redis ping failed: {e}", flush=True)
        sys.exit(3)

    prev_snapshot = {}
    cycle = 0
    while True:
        cycle += 1
        events_this_cycle = 0
        try:
            snap = get_pm2_snapshot()
            if not snap:
                emit_heartbeat(r, cycle, 0, 0)
                time.sleep(CYCLE_S)
                continue

            if not prev_snapshot:
                # 첫 cycle: baseline 등록만
                prev_snapshot = snap
                emit_event(r, "BASELINE", -1, "_init", {
                    "watched": len(snap),
                    "cycle": cycle,
                })
                events_this_cycle += 1
            else:
                # 차이 감지
                for pm_id, cur in snap.items():
                    prev = prev_snapshot.get(pm_id)
                    if prev is None:
                        # 신규 process
                        emit_event(r, "ADDED", pm_id, cur["name"], {
                            "pid": cur["pid"],
                            "status": cur["status"],
                        })
                        events_this_cycle += 1
                        continue
                    # restart 증가
                    if cur["restart"] > prev["restart"]:
                        delta = cur["restart"] - prev["restart"]
                        emit_event(r, "RESTART", pm_id, cur["name"], {
                            "delta_restarts": delta,
                            "total_restarts": cur["restart"],
                            "unstable_restarts": cur["unstable_restarts"],
                            "old_pid": prev["pid"],
                            "new_pid": cur["pid"],
                            "status": cur["status"],
                            "exit_code": cur.get("exit_code"),
                        })
                        events_this_cycle += 1
                    # status 변경
                    if cur["status"] != prev["status"]:
                        emit_event(r, "STATUS_CHANGE", pm_id, cur["name"], {
                            "old_status": prev["status"],
                            "new_status": cur["status"],
                            "pid": cur["pid"],
                        })
                        events_this_cycle += 1
                # 사라진 process
                for pm_id, prev in prev_snapshot.items():
                    if pm_id not in snap:
                        emit_event(r, "REMOVED", pm_id, prev["name"], {
                            "last_pid": prev["pid"],
                            "last_status": prev["status"],
                        })
                        events_this_cycle += 1
                prev_snapshot = snap
        except Exception as e:
            print(f"[ERR] cycle {cycle} error: {e}", flush=True)

        emit_heartbeat(r, cycle, len(prev_snapshot), events_this_cycle)
        time.sleep(CYCLE_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[INFO] interrupted by user", flush=True)
    except Exception as e:
        print(f"[FATAL] {e}", flush=True)
        sys.exit(1)
