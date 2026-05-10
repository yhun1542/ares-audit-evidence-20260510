#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ops_halt_request_lifecycle_sentinel.py — P9 (Manus integrated production)
=========================================================================

근본 원인:
  ARES RAM26 시스템에서 `ops:halt:request` Redis 키가
  생산자(producer)에 의해 TTL 없이 SET되어 영구 잔존(stale)하고,
  소비자(`kill-switch-authority`, `final-trade-gate`)가 매 1초마다
  이 stale 값을 읽어 `trading:enabled=false`를 반복 토글한다.

설계 (4AI consensus + Manus 안전 보강):
  1) **Stale 판정 매우 보수적** (fail-closed):
     - 값이 JSON이고 ts_ms 필드 존재
     - AND ts_ms가 STALE_THRESHOLD_S (기본 600s) 이상 경과
     - AND **같은 reason의 ops:halt:active가 존재하지 않음**
       (= legitimate active halt를 무력화하지 않음)
  2) **TTL 보강**: TTL 없는 request에 DEFAULT_REQUEST_TTL_S 자동 적용
     (producer가 EXPIRE를 누락한 case 보완)
  3) **Leader lock** (SETNX EX 30) — 단일 instance만 cleanup
  4) **Audit stream** ops:halt:audit (XADD MAXLEN ~10000)
  5) **Manual kill-switch** ops:halt:request:auto_cleanup_disabled = "1"
  6) **Self-halt** on consecutive failure ≥ 10 (OPEN999_LIFECYCLE_SENTINEL_DOWN)
  7) **Heartbeat** ops:halt:request:sentinel:heartbeat (TTL 30s)
  8) **fail-closed parse**: parse 실패 시 절대 DEL 안 함 (보수)

Runtime:
  - Python 3.12 (EC2 production)
  - redis-py 7.x with TLS (AWS ElastiCache rediss://)
  - PM2 fork_mode, single instance: ops-halt-request-lifecycle-sentinel

Manus 보강 vs Claude 원안:
  - Claude는 "ts_ms older than threshold" 만 검사
  - Manus는 추가로 "ops:halt:active의 reason과 비교"하여
    legitimate active halt를 무력화하지 않음
  - REDIS_URL이 없을 때 ARES_REDIS_URL 자동 폴백
  - JSON encode/decode 더 안전한 try/except
  - Audit record에 reason/ts_ms를 명시적으로 분리하여 forensics 용이
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import ssl as _ssl
import sys
import time
import traceback
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import redis  # redis-py 7.x


# =====================================================================
# Configuration
# =====================================================================

# Manus 보강: REDIS_URL 또는 ARES_REDIS_URL 둘 다 지원
REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("ARES_REDIS_URL")
    or ""
)
if not REDIS_URL:
    print(json.dumps({
        "level": "FATAL",
        "msg": "Neither REDIS_URL nor ARES_REDIS_URL is set",
        "ts": time.time(),
    }), flush=True)
    sys.exit(2)

# Cleanup behavior
STALE_THRESHOLD_S = int(os.environ.get("P9_STALE_THRESHOLD_S", "600"))   # 10 min
DEFAULT_REQUEST_TTL_S = int(os.environ.get("P9_REQUEST_TTL_S", "300"))   # 5 min
POLL_INTERVAL_S = float(os.environ.get("P9_POLL_INTERVAL_S", "1.0"))
LEADER_LOCK_TTL_S = int(os.environ.get("P9_LEADER_LOCK_TTL_S", "30"))
HEARTBEAT_TTL_S = 30

# Audit stream
AUDIT_STREAM = "ops:halt:audit"
AUDIT_MAXLEN_APPROX = 10000

# Failure handling
MAX_CONSECUTIVE_FAILURES = 10
SELF_HALT_REASON = "OPEN999_LIFECYCLE_SENTINEL_DOWN"

# Redis keys
K_HALT_REQUEST = "ops:halt:request"
K_HALT_ACTIVE = "ops:halt:active"
K_AUTO_CLEANUP_DISABLED = "ops:halt:request:auto_cleanup_disabled"
K_LEADER = "ops:halt:request:sentinel:leader"
K_HEARTBEAT = "ops:halt:request:sentinel:heartbeat"
K_DEDUP_PREFIX = "ops:halt:request:dedup:"
K_LAST_CLEANUP = "ops:halt:request:sentinel:last_cleanup"

# Identity
HOSTNAME = socket.gethostname()
INSTANCE_ID = f"{HOSTNAME}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


# =====================================================================
# Structured logging
# =====================================================================

def log(level: str, msg: str, **fields: Any) -> None:
    rec = {
        "ts": time.time(),
        "level": level,
        "component": "p9.lifecycle_sentinel",
        "instance": INSTANCE_ID,
        "msg": msg,
    }
    rec.update(fields)
    try:
        print(json.dumps(rec, default=str), flush=True)
    except Exception:
        print(f"{level} {msg} (log-encode-fail)", flush=True)


# =====================================================================
# Redis client (TLS / ElastiCache friendly)
# =====================================================================

def build_redis_client() -> redis.Redis:
    """
    AWS ElastiCache rediss:// 와 호환되는 Redis 클라이언트 빌드.
    REDIS_TLS_INSECURE=1 시 인증서 검증 완화 (긴급 fallback).
    """
    insecure = os.environ.get("REDIS_TLS_INSECURE") == "1"

    kwargs: dict[str, Any] = dict(
        decode_responses=True,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
        retry_on_timeout=True,
        health_check_interval=15,
    )
    if REDIS_URL.startswith("rediss://") and insecure:
        kwargs["ssl_cert_reqs"] = _ssl.CERT_NONE

    client = redis.Redis.from_url(REDIS_URL, **kwargs)
    client.ping()
    return client


# =====================================================================
# Lua scripts (atomic operations)
# =====================================================================

# 보강: TTL 없으면 default TTL 적용
LUA_ENSURE_TTL = """
local key = KEYS[1]
local ttl = tonumber(ARGV[1])
local exists = redis.call('EXISTS', key)
if exists == 0 then return 0 end
local cur = redis.call('PTTL', key)
if cur == -1 then
    redis.call('PEXPIRE', key, ttl * 1000)
    return 1
end
return 0
"""

# Manus 보강: stale 판정에 active reason 비교 추가
# DEL 조건: (a) JSON parse 가능 (b) ts_ms 존재 (c) age >= stale (d) active의 reason과 다름
LUA_DEL_IF_STALE_AND_NOT_ACTIVE = """
local req_key = KEYS[1]
local active_key = KEYS[2]
local now_ms = tonumber(ARGV[1])
local stale_ms = tonumber(ARGV[2])

local req_val = redis.call('GET', req_key)
if not req_val then
    return cjson.encode({deleted=false, reason='absent'})
end

-- Parse request as JSON
local ok, decoded = pcall(cjson.decode, req_val)
if not ok or type(decoded) ~= 'table' then
    return cjson.encode({deleted=false, reason='unparseable_or_string', value=req_val})
end

local ts_ms = tonumber(decoded.ts_ms)
local req_reason = decoded.reason or ''

if ts_ms == nil then
    return cjson.encode({deleted=false, reason='no_ts_ms', value=req_val})
end

local age_ms = now_ms - ts_ms
if age_ms < stale_ms then
    return cjson.encode({deleted=false, reason='fresh', age_ms=age_ms})
end

-- Manus safety check: 같은 reason이 ops:halt:active에 있으면 보존 (legitimate active halt)
local active_val = redis.call('GET', active_key)
local active_reason = ''
if active_val then
    -- active is usually a plain string (e.g. "OPEN009_RAM26_G8_NOT_READY")
    active_reason = tostring(active_val)
    -- Normalize: extract the OPEN-class prefix if present
    local s = string.find(active_reason, '[%s,]')
    if s then active_reason = string.sub(active_reason, 1, s-1) end
end

-- If active reason matches request reason exactly, keep request (legitimate)
if active_reason ~= '' and active_reason == req_reason then
    return cjson.encode({deleted=false, reason='active_matches', age_ms=age_ms,
                         req_reason=req_reason, active_reason=active_reason})
end

-- Safe to delete: request is stale AND not currently active
redis.call('DEL', req_key)
return cjson.encode({deleted=true, reason='stale_and_not_active', age_ms=age_ms,
                     req_reason=req_reason, active_reason=active_reason,
                     value=req_val})
"""

# Leader election
LUA_LEADER_ACQUIRE = """
local key = KEYS[1]
local me = ARGV[1]
local ttl = tonumber(ARGV[2])
local cur = redis.call('GET', key)
if cur == false or cur == nil then
    redis.call('SET', key, me, 'EX', ttl)
    return 1
end
if cur == me then
    redis.call('EXPIRE', key, ttl)
    return 1
end
return 0
"""

# Self-halt with dedup
LUA_PUBLISH_HALT = """
local req_key = KEYS[1]
local dedup_key = KEYS[2]
local payload = ARGV[1]
local ttl = tonumber(ARGV[2])
local dedup_ttl = tonumber(ARGV[3])
local got = redis.call('SET', dedup_key, '1', 'NX', 'EX', dedup_ttl)
if not got then return 0 end
redis.call('SET', req_key, payload, 'EX', ttl)
return 1
"""


# =====================================================================
# Audit
# =====================================================================

def audit(client: redis.Redis, event: str, **fields: Any) -> None:
    """ops:halt:audit stream에 record 추가 (MAXLEN ~10000 approx trim)."""
    try:
        record = {
            "event": event,
            "ts_ms": str(int(time.time() * 1000)),
            "instance": INSTANCE_ID,
            "host": HOSTNAME,
        }
        for k, v in fields.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                record[k] = str(v) if v is not None else ""
            else:
                record[k] = json.dumps(v, default=str)
        client.xadd(
            AUDIT_STREAM, record,
            maxlen=AUDIT_MAXLEN_APPROX, approximate=True,
        )
    except Exception as e:
        log("WARN", "audit_xadd_failed", error=str(e), event=event)


# =====================================================================
# Sentinel state
# =====================================================================

@dataclass
class SentinelState:
    consecutive_failures: int = 0
    is_leader: bool = False
    last_heartbeat_ms: int = 0
    cleanup_count: int = 0
    ttl_applied_count: int = 0


def reason_dedup_key(reason: str) -> str:
    h = hashlib.sha256(reason.encode("utf-8")).hexdigest()[:16]
    return f"{K_DEDUP_PREFIX}{h}"


def try_acquire_leader(client: redis.Redis) -> bool:
    res = client.eval(LUA_LEADER_ACQUIRE, 1, K_LEADER, INSTANCE_ID, LEADER_LOCK_TTL_S)
    return int(res) == 1


def emit_heartbeat(client: redis.Redis, state: SentinelState) -> None:
    payload = {
        "instance": INSTANCE_ID,
        "ts_ms": int(time.time() * 1000),
        "is_leader": state.is_leader,
        "consecutive_failures": state.consecutive_failures,
        "cleanup_count": state.cleanup_count,
        "ttl_applied_count": state.ttl_applied_count,
    }
    client.set(K_HEARTBEAT, json.dumps(payload), ex=HEARTBEAT_TTL_S)
    state.last_heartbeat_ms = payload["ts_ms"]


def is_cleanup_disabled(client: redis.Redis) -> bool:
    try:
        return client.get(K_AUTO_CLEANUP_DISABLED) == "1"
    except Exception:
        return False


def ensure_ttl(client: redis.Redis) -> Optional[int]:
    try:
        return int(client.eval(LUA_ENSURE_TTL, 1, K_HALT_REQUEST, DEFAULT_REQUEST_TTL_S))
    except Exception as e:
        log("WARN", "ensure_ttl_failed", error=str(e))
        return None


def cleanup_stale(client: redis.Redis) -> Optional[dict]:
    """Manus 보강: stale + active와 reason 다를 때만 DEL."""
    now_ms = int(time.time() * 1000)
    stale_ms = STALE_THRESHOLD_S * 1000
    try:
        raw = client.eval(
            LUA_DEL_IF_STALE_AND_NOT_ACTIVE, 2,
            K_HALT_REQUEST, K_HALT_ACTIVE,
            now_ms, stale_ms,
        )
        return json.loads(raw)
    except Exception as e:
        log("WARN", "cleanup_stale_failed", error=str(e))
        return None


def publish_self_halt(client: redis.Redis) -> None:
    payload = {
        "ts_ms": int(time.time() * 1000),
        "actor": "p9.lifecycle_sentinel",
        "reason": SELF_HALT_REASON,
        "auto": True,
        "host": HOSTNAME,
        "ttl_s": DEFAULT_REQUEST_TTL_S,
        "dedup_key": SELF_HALT_REASON,
        "instance": INSTANCE_ID,
        "schema_version": "v1",
    }
    try:
        rc = int(client.eval(
            LUA_PUBLISH_HALT, 2,
            K_HALT_REQUEST, reason_dedup_key(SELF_HALT_REASON),
            json.dumps(payload), DEFAULT_REQUEST_TTL_S, 300,
        ))
        if rc == 1:
            log("ERROR", "self_halt_published", reason=SELF_HALT_REASON)
            audit(client, "self_halt_published", reason=SELF_HALT_REASON)
    except Exception as e:
        log("ERROR", "self_halt_publish_failed", error=str(e))


# =====================================================================
# Main loop
# =====================================================================

_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    _shutdown = True
    log("INFO", "signal_received", signum=signum)


def main_loop() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log("INFO", "sentinel_starting",
        stale_threshold_s=STALE_THRESHOLD_S,
        request_ttl_s=DEFAULT_REQUEST_TTL_S,
        poll_interval_s=POLL_INTERVAL_S,
        instance=INSTANCE_ID)

    state = SentinelState()
    client: Optional[redis.Redis] = None
    backoff = 1.0

    while not _shutdown:
        try:
            if client is None:
                client = build_redis_client()
                log("INFO", "redis_connected")
                audit(client, "sentinel_started",
                      pid=os.getpid(),
                      stale_threshold_s=STALE_THRESHOLD_S)

            # 1. Heartbeat (always)
            emit_heartbeat(client, state)

            # 2. Leader election
            became_leader = try_acquire_leader(client)
            if became_leader != state.is_leader:
                log("INFO", "leader_state_change",
                    is_leader=became_leader,
                    previously=state.is_leader)
                if became_leader:
                    audit(client, "leader_acquired")
                state.is_leader = became_leader

            if not state.is_leader:
                state.consecutive_failures = 0
                time.sleep(POLL_INTERVAL_S)
                continue

            # 3. Manual kill-switch
            disabled = is_cleanup_disabled(client)

            # 4. TTL 보강 (always, 안전한 op)
            ttl_applied = ensure_ttl(client)
            if ttl_applied == 1:
                state.ttl_applied_count += 1
                log("WARN", "ttl_applied_post_hoc",
                    key=K_HALT_REQUEST,
                    ttl_s=DEFAULT_REQUEST_TTL_S,
                    total_count=state.ttl_applied_count)
                audit(client, "ttl_enforced",
                      key=K_HALT_REQUEST,
                      ttl_s=DEFAULT_REQUEST_TTL_S)

            # 5. Stale cleanup (manual kill-switch가 disabled가 아니면)
            if not disabled:
                result = cleanup_stale(client)
                if result and result.get("deleted"):
                    state.cleanup_count += 1
                    log("WARN", "stale_request_deleted",
                        age_ms=result.get("age_ms"),
                        req_reason=result.get("req_reason"),
                        active_reason=result.get("active_reason"),
                        prev_value=result.get("value"),
                        total_count=state.cleanup_count)
                    audit(client, "stale_cleanup",
                          age_ms=result.get("age_ms"),
                          req_reason=result.get("req_reason"),
                          active_reason=result.get("active_reason"),
                          prev_value=result.get("value"))
                    client.set(K_LAST_CLEANUP, json.dumps({
                        "ts_ms": int(time.time() * 1000),
                        "result": result,
                    }), ex=86400)
                elif result and result.get("reason") == "active_matches":
                    # 다음 cycle도 같은 상황일 가능성 — 한 번만 로깅
                    pass
                elif result and result.get("reason") in ("unparseable_or_string", "no_ts_ms"):
                    log("WARN", "request_unparseable_or_no_ts",
                        reason=result.get("reason"),
                        value=result.get("value"))

            # Reset failure counter
            state.consecutive_failures = 0
            backoff = 1.0
            time.sleep(POLL_INTERVAL_S)

        except redis.RedisError as e:
            state.consecutive_failures += 1
            log("ERROR", "redis_error",
                error=str(e),
                consecutive=state.consecutive_failures,
                trace=traceback.format_exc())
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
            client = None
            time.sleep(min(backoff, 30.0))
            backoff = min(backoff * 2, 30.0)

            if state.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                try:
                    emergency = build_redis_client()
                    publish_self_halt(emergency)
                    emergency.close()
                except Exception as ee:
                    log("FATAL", "self_halt_unreachable", error=str(ee))
                state.consecutive_failures = 0
                time.sleep(60)

        except Exception as e:
            state.consecutive_failures += 1
            log("ERROR", "unexpected_exception",
                error=str(e),
                consecutive=state.consecutive_failures,
                trace=traceback.format_exc())
            time.sleep(5)

    # Graceful shutdown
    if client is not None:
        try:
            audit(client, "sentinel_stopped", reason="signal")
            client.close()
        except Exception:
            pass
    log("INFO", "sentinel_stopped")


if __name__ == "__main__":
    main_loop()
