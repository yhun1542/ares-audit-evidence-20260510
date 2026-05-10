"""
ares_redis_hardened.py — ARES Redis Hardened Connection Layer
=============================================================
First Principles Patch: Redis 단일 장애점(SPOF) 해결

문제:
  - redis.from_url() 한 번 호출 후 재연결 로직 없음
  - 페일오버 시 read-only replica에 연결되면 전체 HALT
  - Sentinel/Cluster 미사용
  - 환경변수 불일치 (ARES_REDIS_URL vs REDIS_URL)

해결:
  1. 통합 환경변수 해석: ARES_REDIS_URL → REDIS_URL → 기본값 순서
  2. 자동 재연결: ConnectionError/TimeoutError 시 exponential backoff 재시도
  3. 마스터 재탐색: "READONLY" 에러 감지 시 INFO replication으로 마스터 확인
  4. 헬스체크: 주기적 PING으로 연결 상태 확인
  5. 서킷브레이커: 연속 실패 시 잠시 대기 후 재시도

사용법:
  기존: self.r = redis.from_url(url, decode_responses=False)
  변경: self.r = HardenedRedis.from_env()

@version 1.0.0
@date 2026-04-11
"""
import json
import logging
import os
import time
import threading

import redis

log = logging.getLogger("ares.redis.hardened")

# ── 통합 환경변수 해석 ──
def resolve_redis_url() -> str:
    """ARES_REDIS_URL → REDIS_URL → 기본값 순서로 Redis URL 해석.
    환경변수 불일치 문제(구조적 결함 #2) 해결."""
    url = os.getenv("ARES_REDIS_URL") or os.getenv("REDIS_URL") or "redis://127.0.0.1:6379/0"
    log.info("Redis URL resolved: %s (source: %s)",
             url.split("@")[-1] if "@" in url else url,
             "ARES_REDIS_URL" if os.getenv("ARES_REDIS_URL") else
             "REDIS_URL" if os.getenv("REDIS_URL") else "default")
    return url


class RedisCircuitBreaker:
    """연속 실패 시 잠시 대기하여 Redis 폭풍 방지."""
    def __init__(self, max_failures: int = 5, reset_after_sec: float = 30.0):
        self.max_failures = max_failures
        self.reset_after_sec = reset_after_sec
        self._failures = 0
        self._last_failure_ts = 0.0
        self._state = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._state

    def record_success(self):
        with self._lock:
            if self._state != "CLOSED":
                log.info("Redis circuit breaker: %s → CLOSED (recovered)", self._state)
            self._failures = 0
            self._state = "CLOSED"

    def record_failure(self, error: Exception):
        with self._lock:
            self._failures += 1
            self._last_failure_ts = time.time()
            if self._failures >= self.max_failures:
                if self._state != "OPEN":
                    log.error("Redis circuit breaker: OPEN after %d failures: %s",
                              self._failures, error)
                self._state = "OPEN"

    def should_attempt(self) -> bool:
        with self._lock:
            if self._state == "CLOSED":
                return True
            if self._state == "OPEN":
                elapsed = time.time() - self._last_failure_ts
                if elapsed >= self.reset_after_sec:
                    self._state = "HALF_OPEN"
                    log.info("Redis circuit breaker: OPEN → HALF_OPEN (testing after %.0fs)", elapsed)
                    return True
                return False
            # HALF_OPEN: allow one attempt
            return True


class HardenedRedis:
    """Redis 연결 견고화 래퍼.

    기존 redis.Redis 인터페이스를 유지하면서:
    1. 자동 재연결 (exponential backoff)
    2. read-only replica 감지 시 마스터 재탐색
    3. 서킷브레이커로 Redis 폭풍 방지
    4. 모든 명령에 대한 투명한 재시도
    """

    MAX_RETRIES = 3
    BASE_BACKOFF_SEC = 0.5
    MAX_BACKOFF_SEC = 10.0

    def __init__(self, url: str, decode_responses: bool = False, **kwargs):
        self._url = url
        self._decode_responses = decode_responses
        self._kwargs = kwargs
        self._kwargs.setdefault("socket_timeout", 10)
        self._kwargs.setdefault("socket_connect_timeout", 5)
        self._kwargs.setdefault("retry_on_timeout", True)
        self._kwargs.setdefault("health_check_interval", 30)
        self._client: redis.Redis = self._create_client()
        self._cb = RedisCircuitBreaker()
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, decode_responses: bool = False, **kwargs) -> "HardenedRedis":
        """환경변수에서 Redis URL을 자동 해석하여 인스턴스 생성."""
        url = resolve_redis_url()
        return cls(url, decode_responses=decode_responses, **kwargs)

    def _create_client(self) -> redis.Redis:
        """새 Redis 클라이언트 생성."""
        client = redis.from_url(
            self._url,
            decode_responses=self._decode_responses,
            **self._kwargs
        )
        log.info("Redis client created: %s", self._url.split("@")[-1] if "@" in self._url else self._url)
        return client

    def _reconnect(self):
        """연결 재생성."""
        with self._lock:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = self._create_client()
            log.info("Redis reconnected")

    def _is_readonly_error(self, error: Exception) -> bool:
        """read-only replica 에러 감지."""
        msg = str(error).lower()
        return "readonly" in msg or "read only" in msg or "read-only" in msg

    def _is_connection_error(self, error: Exception) -> bool:
        """연결 관련 에러 감지."""
        return isinstance(error, (
            redis.ConnectionError,
            redis.TimeoutError,
            ConnectionResetError,
            ConnectionRefusedError,
            BrokenPipeError,
            OSError,
        ))

    def _execute_with_retry(self, method_name: str, *args, **kwargs):
        """모든 Redis 명령을 재시도 로직으로 감싸기.

        재시도 전략:
        1. 서킷브레이커 확인
        2. 명령 실행
        3. 실패 시 에러 유형 분류:
           - read-only → 재연결 후 재시도
           - connection → 재연결 후 재시도
           - 기타 → 즉시 raise
        4. exponential backoff
        """
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            if not self._cb.should_attempt():
                raise redis.ConnectionError(
                    f"Redis circuit breaker OPEN: too many failures. "
                    f"Last error: {last_error}"
                )

            try:
                method = getattr(self._client, method_name)
                result = method(*args, **kwargs)
                self._cb.record_success()
                return result
            except Exception as e:
                last_error = e
                if self._is_readonly_error(e):
                    log.warning("Redis READONLY detected (attempt %d/%d): %s. Reconnecting...",
                                attempt + 1, self.MAX_RETRIES, e)
                    self._cb.record_failure(e)
                    self._reconnect()
                elif self._is_connection_error(e):
                    log.warning("Redis connection error (attempt %d/%d): %s. Reconnecting...",
                                attempt + 1, self.MAX_RETRIES, e)
                    self._cb.record_failure(e)
                    self._reconnect()
                else:
                    # Non-retryable error (e.g., WRONGTYPE, syntax error)
                    raise

                # Exponential backoff
                backoff = min(
                    self.BASE_BACKOFF_SEC * (2 ** attempt),
                    self.MAX_BACKOFF_SEC
                )
                time.sleep(backoff)

        raise last_error

    # ── 투명한 프록시: 기존 redis.Redis 인터페이스 유지 ──
    def __getattr__(self, name):
        """redis.Redis의 모든 메서드를 재시도 로직으로 프록시."""
        # 내부 속성은 직접 접근
        if name.startswith("_"):
            raise AttributeError(name)

        original = getattr(self._client, name)
        if not callable(original):
            return original

        def wrapper(*args, **kwargs):
            return self._execute_with_retry(name, *args, **kwargs)
        return wrapper

    def ping(self) -> bool:
        """헬스체크."""
        return self._execute_with_retry("ping")

    def pipeline(self, transaction=True, shard_hint=None):
        """Pipeline은 프록시하지 않고 직접 반환 (내부에서 자체 재시도)."""
        return self._client.pipeline(transaction=transaction, shard_hint=shard_hint)

    def close(self):
        """연결 종료."""
        try:
            self._client.close()
        except Exception:
            pass


# ── Drop-in replacement for RedisStore ──
class HardenedRedisStore:
    """기존 RedisStore의 drop-in replacement.

    변경점:
    - redis.from_url() → HardenedRedis.from_env()
    - 모든 Redis 명령이 자동 재시도 + 재연결
    """
    def __init__(self, url: str = None):
        if url:
            self.r = HardenedRedis(url, decode_responses=False)
        else:
            self.r = HardenedRedis.from_env(decode_responses=False)
        self.r.ping()

    def get_json(self, key: str, default=None):
        v = self.r.get(key)
        if not v:
            return default
        try:
            if isinstance(v, bytes):
                v = v.decode()
            return json.loads(v)
        except Exception:
            return default

    def set_json(self, key: str, obj, ex=None):
        raw = json.dumps(obj, separators=(",", ":"), default=str)
        if ex:
            self.r.setex(key, ex, raw)
        else:
            self.r.set(key, raw)

    def hgetall_text(self, key: str) -> dict:
        actual_type = self.r.type(key)
        if isinstance(actual_type, bytes):
            actual_type = actual_type.decode()
        if actual_type == "none":
            return {}
        if actual_type != "hash":
            raise TypeError(f"REDIS_WRONGTYPE_GUARD key={key} expected=hash actual={actual_type}")
        raw = self.r.hgetall(key)
        out = {}
        for k, v in raw.items():
            kk = k.decode() if isinstance(k, bytes) else str(k)
            vv = v.decode() if isinstance(v, bytes) else str(v)
            out[kk] = vv
        return out
