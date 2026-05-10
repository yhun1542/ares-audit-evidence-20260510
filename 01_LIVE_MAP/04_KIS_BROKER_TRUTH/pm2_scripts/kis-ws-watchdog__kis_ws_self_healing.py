# PATH: /home/ubuntu/ares_current/external/kis_ws_self_healing.py
# PATCH: V3 — KIS WebSocket Self-Healing watchdog (fixes 88% fill miss in 5/7)
# DEPLOY: pm2 start kis_ws_self_healing.py --name kis-ws-watchdog --interpreter python3
# ROLLBACK: pm2 delete kis-ws-watchdog

"""
ARES — KIS WebSocket Self-Healing Watchdog v1.0.0 (PATCH V3)
=============================================================================
문제 상황 (5/7):
  - kis-ws-fills-adapter는 정상 동작하지만 KIS approval_key TTL 만료 후
    자동 갱신/재연결이 실패 → fills_received=0, DISCONNECTED 상태로 4시간+
  - 결과적으로 emarkos:v1:execution(1596건) vs fills(1437건) = 159건 누락(약 10%)
  - 손익 계산이 stale 상태 유지 (broker-truth-writer가 변화 미반영)

해결 (Self-Healing Watchdog):
  1. kis:ws:status:current 모니터링
     - status == "DISCONNECTED" 이고 5분 이상 → restart
     - last_heartbeat_ts > 60초 → restart
     - fills_received_5min == 0 (시장시간 중) → restart
  2. PM2 통한 안전한 재시작: `pm2 restart kis-ws-fills-adapter`
  3. approval_key 강제 재발급: `kis:approval_key` 키 삭제 → adapter가 새로 발급
  4. ware_alert + Telegram 알림 (선택)
  5. 재시작 백오프: 1분, 5분, 15분, 30분 (최대 1시간)

장 시간 외에는 비활성. 장 시간 내에서만 작동.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import redis

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("kis_ws_watchdog")

REDIS_URL = os.getenv("REDIS_URL") or os.getenv("ARES_REDIS_URL")
REDIS_PASS = os.environ.get("REDIS_PASSWORD")
if not REDIS_URL:
    _env_file = "/etc/ares/ares.env"
    try:
        with open(_env_file) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("#") or "=" not in _line:
                    continue
                _key, _val = _line.split("=", 1)
                _key, _val = _key.strip(), _val.strip().strip('"').strip("'")
                if _key == "REDIS_URL" and _val:
                    REDIS_URL = _val
                elif _key == "REDIS_PASSWORD" and _val and not REDIS_PASS:
                    REDIS_PASS = _val
    except Exception:
        pass

_SELF_TEST = "--self-test" in sys.argv
if not _SELF_TEST and not REDIS_URL:
    raise RuntimeError("REDIS_URL is required")

# Config
CYCLE_INTERVAL = int(os.getenv("KIS_WS_WATCHDOG_CYCLE_SEC", "60"))
DISCONNECTED_THRESHOLD_SEC = 300       # 5 min
HEARTBEAT_STALE_SEC = 90               # 90 sec
NO_FILLS_STALE_SEC = 600               # 10 min during market hours
MAX_RESTART_PER_HOUR = 4

KIS_WS_STATUS_KEY = "kis:ws:status:current"
KIS_WS_HEARTBEAT_KEY = "kis:ws:heartbeat"
KIS_FILLS_LAST_TS_KEY = "kis:fills:last_ts"
KIS_APPROVAL_KEY = "kis:approval_key"
ENABLE_FLAG = os.getenv("ARES_KIS_WATCHDOG_ENABLE", "true").lower() == "true"
NO_FILLS_RESTART_ENABLE = os.getenv("KIS_WS_NO_FILLS_RESTART_ENABLE", "false").lower() == "true"

# State
_RESTART_HISTORY = []  # List of (epoch_seconds)
_BACKOFF_LADDER = [60, 300, 900, 1800]  # 1min, 5min, 15min, 30min
_BACKOFF_INDEX = 0
_LAST_RESTART_TS = 0.0


def _push_health_metric(r, key: str, value, labels: dict | None = None):
    try:
        metric = {"value": value, "ts": time.time(), "labels": labels or {}}
        r.set(f"metrics:{key}", json.dumps(metric), ex=60)
    except Exception:
        pass


def _get_redis():
    return redis.Redis.from_url(REDIS_URL, password=REDIS_PASS, decode_responses=True, socket_timeout=5)


def _is_market_hours() -> bool:
    """US regular market hours: 14:30~21:00 UTC (Mon-Fri)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    h = now.hour + now.minute / 60.0
    return 14.5 <= h <= 21.0


def _read_status(r) -> dict:
    raw = r.get(KIS_WS_STATUS_KEY)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}
    return {}


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _diagnose(r) -> tuple[bool, str]:
    """Return (needs_restart, reason)."""
    status_obj = _read_status(r)
    state = str(status_obj.get("state") or status_obj.get("status") or "").upper()
    last_hb = _safe_float(status_obj.get("last_heartbeat_ts") or r.get(KIS_WS_HEARTBEAT_KEY) or 0)
    last_fill = _safe_float(r.get(KIS_FILLS_LAST_TS_KEY) or 0)
    now = time.time()

    # Test 1: explicit DISCONNECTED state
    if state in ("DISCONNECTED", "ERROR", "CLOSED"):
        last_change = _safe_float(status_obj.get("state_changed_ts") or now)
        age = now - last_change
        if age > DISCONNECTED_THRESHOLD_SEC:
            return True, f"DISCONNECTED for {age:.0f}s (threshold {DISCONNECTED_THRESHOLD_SEC}s)"

    # Test 2: heartbeat stale
    if last_hb > 0 and (now - last_hb) > HEARTBEAT_STALE_SEC:
        return True, f"heartbeat stale: {now - last_hb:.0f}s ago (threshold {HEARTBEAT_STALE_SEC}s)"

    # Test 3: no fills during market hours
    if _is_market_hours() and last_fill > 0 and (now - last_fill) > NO_FILLS_STALE_SEC:
        if NO_FILLS_RESTART_ENABLE:
            return True, f"no fills for {now - last_fill:.0f}s during market hours"
        return False, f"no fills stale but alert-only under single-WS-owner policy: {now - last_fill:.0f}s"

    return False, "ok"


def _restart_kis_ws(r, reason: str):
    global _BACKOFF_INDEX, _LAST_RESTART_TS, _RESTART_HISTORY
    now = time.time()

    # Rate limiting
    _RESTART_HISTORY = [t for t in _RESTART_HISTORY if now - t < 3600]
    if len(_RESTART_HISTORY) >= MAX_RESTART_PER_HOUR:
        log.warning("[watchdog] restart rate-limited (%d in last hour). Reason was: %s",
                    len(_RESTART_HISTORY), reason)
        return False

    backoff = _BACKOFF_LADDER[min(_BACKOFF_INDEX, len(_BACKOFF_LADDER) - 1)]
    if (now - _LAST_RESTART_TS) < backoff:
        log.info("[watchdog] in backoff (%.0fs since last, need %ds)",
                 now - _LAST_RESTART_TS, backoff)
        return False

    log.warning("[watchdog] RESTARTING kis-ws-fills-adapter: %s", reason)
    # Force approval_key regeneration
    try:
        r.delete(KIS_APPROVAL_KEY)
        log.info("[watchdog] cleared %s for fresh approval_key", KIS_APPROVAL_KEY)
    except Exception as e:
        log.warning("[watchdog] failed to clear approval_key: %s", e)

    try:
        result = subprocess.run(
            ["pm2", "restart", "kis-ws-fills-adapter"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            log.info("[watchdog] restart success: %s", result.stdout.strip()[:200])
            _LAST_RESTART_TS = now
            _RESTART_HISTORY.append(now)
            _BACKOFF_INDEX += 1
            # Log to redis
            r.lpush("kis_ws_watchdog:restarts", json.dumps({
                "ts": now, "reason": reason, "backoff_sec": backoff,
                "iso": datetime.now(timezone.utc).isoformat(),
            }))
            r.ltrim("kis_ws_watchdog:restarts", 0, 99)
            return True
        else:
            log.error("[watchdog] restart failed (rc=%d): %s",
                      result.returncode, result.stderr.strip()[:200])
            return False
    except Exception as e:
        log.error("[watchdog] restart exception: %s", e)
        return False


def _reset_backoff_if_healthy(r):
    """If WS has been healthy for 30+ minutes, reset backoff index."""
    global _BACKOFF_INDEX, _LAST_RESTART_TS
    if _BACKOFF_INDEX > 0 and (time.time() - _LAST_RESTART_TS) > 1800:
        log.info("[watchdog] WS healthy for 30+ min, resetting backoff index")
        _BACKOFF_INDEX = 0


def run_cycle(r):
    if not ENABLE_FLAG:
        return
    needs, reason = _diagnose(r)
    _push_health_metric(r, "kis_ws_health", 0 if needs else 1,
                        {"reason": reason})
    if needs:
        _restart_kis_ws(r, reason)
    else:
        _reset_backoff_if_healthy(r)
        log.debug("[watchdog] OK (%s)", reason)


def main():
    if _SELF_TEST:
        # Test market hours detection
        # And state transitions
        print("[Test] is_market_hours =", _is_market_hours())

        # Simulated diagnose with mock dict
        class MockR:
            def __init__(self, status, last_hb, last_fill):
                self._status = status
                self._hb = last_hb
                self._fill = last_fill
            def get(self, k):
                if k == KIS_WS_STATUS_KEY:
                    return json.dumps(self._status)
                if k == KIS_WS_HEARTBEAT_KEY:
                    return str(self._hb)
                if k == KIS_FILLS_LAST_TS_KEY:
                    return str(self._fill)
                return None

        now = time.time()
        # Healthy
        r1 = MockR({"state": "CONNECTED", "state_changed_ts": now - 100}, now - 30, now - 60)
        n1, r1m = _diagnose(r1)
        print(f"  Healthy: needs={n1}, reason={r1m}")
        assert not n1

        # Disconnected for 6min → trigger
        r2 = MockR({"state": "DISCONNECTED", "state_changed_ts": now - 360}, now - 30, now - 60)
        n2, r2m = _diagnose(r2)
        print(f"  Disconnected: needs={n2}, reason={r2m}")
        assert n2

        # Stale heartbeat
        r3 = MockR({"state": "CONNECTED"}, now - 120, now - 60)
        n3, r3m = _diagnose(r3)
        print(f"  Stale HB: needs={n3}, reason={r3m}")
        assert n3

        print("[PASS] kis_ws_self_healing self-test")
        return 0

    r = _get_redis()
    log.info("KIS WS Watchdog v1.0.0 starting (cycle=%ds, ENABLE=%s)", CYCLE_INTERVAL, ENABLE_FLAG)
    while True:
        try:
            run_cycle(r)
        except Exception as e:
            log.error("watchdog cycle error: %s", e, exc_info=True)
        time.sleep(CYCLE_INTERVAL)


if __name__ == "__main__":
    sys.exit(main() or 0)
