from typing import Any, Optional, Dict
import json
import os
import time
import logging

log = logging.getLogger("ares.redis_store")

try:
    import redis
except ImportError:
    redis = None

# ── [STRUCTURAL_PATCH 2026-04-11] Redis 연결 견고화 ──
# 변경 사항:
#   1. auto-reconnect with exponential backoff
#   2. READONLY 에러 감지 → 자동 재연결
#   3. Circuit breaker (연속 실패 시 잠시 대기)
#   4. 환경변수 통합 (ARES_REDIS_URL / REDIS_URL)

_RECONNECT_BASE_SEC = 0.5
_RECONNECT_MAX_SEC = 15.0
_RECONNECT_MULTIPLIER = 2.0
_CB_MAX_FAILURES = 5
_CB_COOLDOWN_SEC = 10.0

# ares_redis_store_module.py - RedisStore (hardened)
class RedisStore:
    def __init__(self, url: str):
        if redis is None:
            raise RuntimeError("redis-py is required at runtime for live orchestration")
        self._url = url
        self._consecutive_failures = 0
        self._last_failure_ts = 0
        self.r = redis.from_url(url, decode_responses=False,
                                socket_timeout=10, socket_connect_timeout=5,
                                retry_on_timeout=True)
        self.r.ping()
        log.info("[HARDENED] RedisStore connected to %s", self._sanitize_url(url))

    @staticmethod
    def _sanitize_url(url: str) -> str:
        """비밀번호 마스킹."""
        if "@" in url:
            parts = url.split("@")
            return "redis://***@" + parts[-1]
        return url

    def _reconnect(self, reason: str = "unknown"):
        """지수 백오프 기반 자동 재연결."""
        attempt = 0
        while True:
            backoff = min(_RECONNECT_BASE_SEC * (_RECONNECT_MULTIPLIER ** attempt), _RECONNECT_MAX_SEC)
            log.warning("[HARDENED] Redis reconnecting (reason=%s, attempt=%d, backoff=%.1fs)",
                        reason, attempt + 1, backoff)
            time.sleep(backoff)
            try:
                self.r = redis.from_url(self._url, decode_responses=False,
                                        socket_timeout=10, socket_connect_timeout=5,
                                        retry_on_timeout=True)
                self.r.ping()
                self._consecutive_failures = 0
                log.info("[HARDENED] Redis reconnected successfully after %d attempts", attempt + 1)
                return
            except Exception as e:
                attempt += 1
                if attempt >= 10:
                    log.error("[HARDENED] Redis reconnect failed after 10 attempts: %s", e)
                    raise

    def _safe_execute(self, func, *args, **kwargs):
        """모든 Redis 명령을 래핑하여 자동 재연결 및 서킷 브레이커 적용."""
        # 서킷 브레이커 체크
        if self._consecutive_failures >= _CB_MAX_FAILURES:
            elapsed = time.time() - self._last_failure_ts
            if elapsed < _CB_COOLDOWN_SEC:
                raise redis.ConnectionError(
                    f"[HARDENED] Circuit breaker OPEN: {self._consecutive_failures} consecutive failures, "
                    f"cooldown {_CB_COOLDOWN_SEC - elapsed:.1f}s remaining")
            else:
                log.info("[HARDENED] Circuit breaker cooldown expired, retrying...")
                self._consecutive_failures = 0

        try:
            result = func(*args, **kwargs)
            self._consecutive_failures = 0
            return result
        except Exception as e:
            err_str = str(e)
            self._consecutive_failures += 1
            self._last_failure_ts = time.time()

            # READONLY 에러 → 마스터 재연결
            if "READONLY" in err_str or "read only" in err_str.lower():
                log.error("[HARDENED] READONLY detected — reconnecting to master")
                self._reconnect("READONLY")
                return func(*args, **kwargs)

            # 연결 끊김 → 재연결
            if isinstance(e, (redis.ConnectionError, redis.TimeoutError, ConnectionResetError, OSError)):
                log.error("[HARDENED] Connection error: %s — reconnecting", err_str[:200])
                self._reconnect(type(e).__name__)
                return func(*args, **kwargs)

            raise

    def get_json(self, key: str, default=None):
        v = self._safe_execute(self.r.get, key)
        if not v:
            return default
        try:
            if isinstance(v, bytes):
                v = v.decode()
            return json.loads(v)
        except Exception:
            return default

    def set_json(self, key: str, obj: Any, ex: Optional[int] = None):
        raw = json.dumps(obj, separators=(",", ":"), default=str)
        if ex:
            self._safe_execute(self.r.setex, key, ex, raw)
        else:
            self._safe_execute(self.r.set, key, raw)

    def hgetall_text(self, key: str) -> Dict[str, str]:
        raw = self._safe_execute(self.r.hgetall, key)
        out = {}
        for k, v in raw.items():
            kk = k.decode() if isinstance(k, bytes) else str(k)
            vv = v.decode() if isinstance(v, bytes) else str(v)
            out[kk] = vv
        return out

    _LUA_QUARANTINE = """
    local key = KEYS[1]
    local data = ARGV[1]
    local quarantine_prefix = ARGV[2]
    local current_type = redis.call('TYPE', key)
    if type(current_type) == 'table' then current_type = current_type['ok'] end
    if current_type ~= 'none' and current_type ~= 'list' then
        local t = redis.call('TIME')
        local seq_key = quarantine_prefix .. ':seq'
        local seq = redis.call('INCR', seq_key)
        redis.call('EXPIRE', seq_key, 604800)
        local quarantine_key = quarantine_prefix .. ':' .. t[1] .. '-' .. t[2] .. '-' .. seq
        redis.call('RENAME', key, quarantine_key)
        redis.call('EXPIRE', quarantine_key, 604800)
    end
    return redis.call('LPUSH', key, data)
    """
    _lua_quarantine_sha = None
    MAX_WRONGTYPE_RETRIES = 3

    def publish_history_point(self, key: str, payload: Dict[str, Any], ttl_sec: int, max_items: int = 2000):
        raw = json.dumps(payload, separators=(",", ":"), default=str)
        try:
            self._safe_execute(self.r.lpush, key, raw)
        except Exception as e:
            if "WRONGTYPE" in str(e):
                counter_key = f"ares:wrongtype:counter:{key}"
                count = self._safe_execute(self.r.incr, counter_key)
                self._safe_execute(self.r.expire, counter_key, 86400)
                if count > self.MAX_WRONGTYPE_RETRIES:
                    log.critical("WRONGTYPE on %s exceeded %dx — HALT justified, quarantine key check required", key, self.MAX_WRONGTYPE_RETRIES)
                    raise
                log.warning("WRONGTYPE on %s (count=%d) — Lua atomic quarantine + retry", key, count)
                if self._lua_quarantine_sha is None:
                    self._lua_quarantine_sha = self._safe_execute(self.r.script_load, self._LUA_QUARANTINE)
                try:
                    self._safe_execute(self.r.evalsha, self._lua_quarantine_sha, 1, key, raw, f"{key}:wrongtype")
                except Exception:
                    self._safe_execute(self.r.eval, self._LUA_QUARANTINE, 1, key, raw, f"{key}:wrongtype")
                log.info("Recovered %s via Lua atomic handler", key)
                self._safe_execute(self.r.ltrim, key, 0, max_items - 1)
                self._safe_execute(self.r.expire, key, ttl_sec)
                return
            else:
                raise
        self._safe_execute(self.r.ltrim, key, 0, max_items - 1)
        self._safe_execute(self.r.expire, key, ttl_sec)


# ---------------------------------------------------------------------------
# Market / account adapters
# ---------------------------------------------------------------------------
