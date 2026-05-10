#!/usr/bin/env python3
"""
ARES Redis Atomic Operations Library — Production Module
═══════════════════════════════════════════════════════════
전체 Redis 비원자 연산 감사 결과 9개 패턴을 Lua 원자 연산으로 전환.

배포 위치: /home/ubuntu/ares_common/redis_atomic.py
사용법:
    from ares_common.redis_atomic import AresAtomicOps
    ops = AresAtomicOps(redis_client)
    ops.ssot_promote(payload_str, meta_dict)
    ops.emit_order_intent(stream, fields, idem_key, idem_val, ttl)
    ops.dtch_dedup_and_set(hash_key, hash_val, ttl)

변경 이력:
    v1.0 (2026-04-06): 초기 생성 — 9개 Lua 스크립트
"""
from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("ares.atomic")

# ═══════════════════════════════════════════════════════════════════
# LUA SCRIPT 1: SSOT PROMOTE (6 ops → 1 atomic call)
# ═══════════════════════════════════════════════════════════════════
LUA_SSOT_PROMOTE = """
local payload   = ARGV[1]
local cur_ttl   = tonumber(ARGV[2])
local ts_ms     = ARGV[3]
local leg_ttl   = tonumber(ARGV[4])
local meta_cnt  = tonumber(ARGV[5])

redis.call('SET', KEYS[1], payload, 'EX', cur_ttl)
redis.call('SET', KEYS[2], ts_ms, 'EX', cur_ttl)
redis.call('SET', KEYS[3], payload, 'EX', leg_ttl)
redis.call('SET', KEYS[4], payload)

local meta_args = {}
local offset = 6
for i = 0, meta_cnt - 1 do
    meta_args[#meta_args + 1] = ARGV[offset + i * 2]
    meta_args[#meta_args + 1] = ARGV[offset + i * 2 + 1]
end
if #meta_args > 0 then
    redis.call('HSET', KEYS[5], unpack(meta_args))
end

local audit_offset = offset + meta_cnt * 2
local audit_cnt = tonumber(ARGV[audit_offset])
local audit_args = {}
for i = 0, audit_cnt - 1 do
    audit_args[#audit_args + 1] = ARGV[audit_offset + 1 + i * 2]
    audit_args[#audit_args + 1] = ARGV[audit_offset + 2 + i * 2]
end
if #audit_args > 0 then
    redis.call('XADD', KEYS[6], 'MAXLEN', '~', 500, '*', unpack(audit_args))
end

return 'OK'
"""

# ═══════════════════════════════════════════════════════════════════
# LUA SCRIPT 2: ORDER INTENT EMIT (nextgen2 기존 Lua 재사용)
# ═══════════════════════════════════════════════════════════════════
LUA_ATOMIC_EMIT = """
local stream = KEYS[1]
local idem_key = KEYS[2]
local idem_val = ARGV[1]
local idem_ttl = tonumber(ARGV[2])
local maxlen = tonumber(ARGV[3])

local existing = redis.call('GET', idem_key)
if existing and string.sub(existing, 1, 4) == 'SENT' then
    local last_colon = 0
    for i = 1, #existing do
        if string.sub(existing, i, i) == ':' then last_colon = i end
    end
    local sid = (last_colon > 0) and string.sub(existing, last_colon + 1) or string.sub(existing, 6)
    return sid
end

local fields = {}
for i = 4, #ARGV, 2 do
    fields[#fields+1] = ARGV[i]
    fields[#fields+1] = ARGV[i+1]
end

local stream_id = redis.call('XADD', stream, 'MAXLEN', '~', maxlen, '*', unpack(fields))
redis.call('SET', idem_key, idem_val .. ':' .. stream_id, 'EX', idem_ttl)
return stream_id
"""

# ═══════════════════════════════════════════════════════════════════
# LUA SCRIPT 3: SSOT CANARY PUBLISH (2 ops → 1 atomic)
# ═══════════════════════════════════════════════════════════════════
LUA_CANARY_PUBLISH = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))

local fields = {}
for i = 3, #ARGV, 2 do
    fields[#fields+1] = ARGV[i]
    fields[#fields+1] = ARGV[i+1]
end
if #fields > 0 then
    redis.call('XADD', KEYS[2], 'MAXLEN', '~', 1000, '*', unpack(fields))
end
return 'OK'
"""

# ═══════════════════════════════════════════════════════════════════
# LUA SCRIPT 4: DTCH DEDUP (TOCTOU 제거)
# ═══════════════════════════════════════════════════════════════════
LUA_DTCH_DEDUP = """
local current_hash = redis.call('GET', KEYS[1])
if current_hash == ARGV[1] then
    return 'DUPLICATE'
end

local cumulative = tonumber(redis.call('GET', KEYS[2]) or '0')
local order_notional = tonumber(ARGV[2])
local max_daily = tonumber(ARGV[3])

if cumulative + order_notional > max_daily then
    return 'DAILY_LIMIT'
end

redis.call('SET', KEYS[1], ARGV[1])
redis.call('INCRBYFLOAT', KEYS[2], ARGV[2])
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))

return 'OK'
"""

# ═══════════════════════════════════════════════════════════════════
# LUA SCRIPT 5: DTCH STATUS PUBLISH (8 ops → 1 atomic)
# ═══════════════════════════════════════════════════════════════════
LUA_DTCH_STATUS = """
redis.call('SET', 'ares:dtch:hedge_ratio', ARGV[1])
redis.call('SET', 'ares:dtch:dynamic_trigger', ARGV[2])
redis.call('SET', 'ares:dtch:dd_slope', ARGV[3])
redis.call('SET', 'ares:dtch:trigger_mode', 'soft')
redis.call('SET', 'ares:dtch:mode', 'live_level35')
redis.call('SET', 'ares:dtch:last_updated', ARGV[4])
redis.call('SETEX', KEYS[1], tonumber(ARGV[5]), ARGV[4])

return 'OK'
"""

# ═══════════════════════════════════════════════════════════════════
# LUA SCRIPT 6: LIST APPEND + TRIM (2 ops → 1 atomic)
# ═══════════════════════════════════════════════════════════════════
LUA_LIST_APPEND_TRIM = """
redis.call('RPUSH', KEYS[1], ARGV[1])
redis.call('LTRIM', KEYS[1], -tonumber(ARGV[2]), -1)
return redis.call('LLEN', KEYS[1])
"""

# ═══════════════════════════════════════════════════════════════════
# LUA SCRIPT 7: PROTECTED KEY WRITE (소유권 검증)
# ═══════════════════════════════════════════════════════════════════
LUA_PROTECTED_WRITE = """
local owner = redis.call('HGET', KEYS[2], KEYS[1])
if owner and owner ~= ARGV[1] then
    return 'REJECTED:owner=' .. owner
end

if tonumber(ARGV[3]) > 0 then
    redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
else
    redis.call('SET', KEYS[1], ARGV[2])
end
return 'OK'
"""

# ═══════════════════════════════════════════════════════════════════
# LUA SCRIPT 8: POSITION RECON SET UPDATE (3 ops → 1 atomic)
# ═══════════════════════════════════════════════════════════════════
LUA_RECON_SET_UPDATE = """
redis.call('DEL', KEYS[1])
for i = 2, #ARGV do
    redis.call('SADD', KEYS[1], ARGV[i])
end
if #ARGV > 1 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return #ARGV - 1
"""


class AresAtomicOps:
    """ARES Redis 원자 연산 통합 인터페이스."""

    def __init__(self, r):
        self.r = r
        self._scripts = {}
        self._register_all()

    def _register_all(self):
        scripts = {
            "ssot_promote": LUA_SSOT_PROMOTE,
            "atomic_emit": LUA_ATOMIC_EMIT,
            "canary_publish": LUA_CANARY_PUBLISH,
            "dtch_dedup": LUA_DTCH_DEDUP,
            "dtch_status": LUA_DTCH_STATUS,
            "list_append_trim": LUA_LIST_APPEND_TRIM,
            "protected_write": LUA_PROTECTED_WRITE,
            "recon_set_update": LUA_RECON_SET_UPDATE,
        }
        for name, lua in scripts.items():
            try:
                self._scripts[name] = self.r.register_script(lua)
                log.debug("Registered Lua script: %s", name)
            except Exception as e:
                log.error("Failed to register Lua script %s: %s", name, e)
                raise

    def ssot_promote(
        self,
        payload_str: str,
        meta: Dict[str, str],
        audit: Dict[str, str],
        current_key: str = "ssot:target:v2:current",
        ts_key: str = "ssot:target:v2:ts",
        legacy_key: str = "ssot:current",
        last_good_key: str = "ssot:target:v2:last_good",
        meta_key: str = "ssot:target:v2:meta",
        audit_stream: str = "audit:ssot_v2:promotes",
        current_ttl: int = 600,
        legacy_ttl: int = 600,
    ) -> str:
        ts_ms = str(int(time.time() * 1000))
        meta_pairs = []
        for k, v in meta.items():
            meta_pairs.extend([k, str(v)])
        audit_pairs = []
        for k, v in audit.items():
            audit_pairs.extend([k, str(v)])
        keys = [current_key, ts_key, legacy_key, last_good_key, meta_key, audit_stream]
        args = [
            payload_str, str(current_ttl), ts_ms, str(legacy_ttl),
            str(len(meta)),
        ] + meta_pairs + [str(len(audit))] + audit_pairs
        result = self._scripts["ssot_promote"](keys=keys, args=args)
        log.info("SSOT_PROMOTE_ATOMIC: ts=%s meta_fields=%d audit_fields=%d",
                 ts_ms, len(meta), len(audit))
        return result

    def emit_order_intent(
        self,
        stream: str,
        fields: Dict[str, str],
        idem_key: str,
        idem_val: str = "SENT",
        idem_ttl: int = 86400,
        maxlen: int = 5000,
    ) -> Optional[str]:
        keys = [stream, idem_key]
        args = [idem_val, str(idem_ttl), str(maxlen)]
        for k, v in fields.items():
            args.extend([k, str(v)])
        result = self._scripts["atomic_emit"](keys=keys, args=args)
        if isinstance(result, bytes):
            result = result.decode()
        log.info("ATOMIC_EMIT: stream=%s idem=%s result=%s", stream, idem_key, result)
        return result

    def canary_publish(
        self,
        canary_key: str,
        payload_str: str,
        canary_ttl: int,
        audit_stream: str = "audit:ssot_v2:writes",
        audit_fields: Optional[Dict[str, str]] = None,
    ) -> str:
        keys = [canary_key, audit_stream]
        args = [payload_str, str(canary_ttl)]
        if audit_fields:
            for k, v in audit_fields.items():
                args.extend([k, str(v)])
        return self._scripts["canary_publish"](keys=keys, args=args)

    def dtch_dedup_and_check(
        self,
        intent_hash: str,
        order_notional: float,
        hash_key: str = "ares:dtch:bridge:last_intent_hash",
        max_daily_notional: float = 75_000,
        daily_key_ttl: int = 90_000,
    ) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_key = f"ares:dtch:daily_notional:{today}"
        keys = [hash_key, daily_key]
        args = [
            intent_hash,
            str(order_notional),
            str(max_daily_notional),
            str(daily_key_ttl),
        ]
        result = self._scripts["dtch_dedup"](keys=keys, args=args)
        if isinstance(result, bytes):
            result = result.decode()
        log.info("DTCH_DEDUP: hash=%s notional=%.2f result=%s",
                 intent_hash[:8], order_notional, result)
        return result

    def dtch_status_publish(
        self,
        hedge_ratio: float,
        dynamic_trigger: float,
        dd_slope: float,
        heartbeat_key: str = "ares:dtch:heartbeat",
        heartbeat_ttl: int = 300,
    ) -> str:
        now_str = datetime.now(timezone.utc).isoformat()
        keys = [heartbeat_key]
        args = [
            str(round(hedge_ratio, 6)),
            str(round(dynamic_trigger, 6)),
            str(round(dd_slope, 8)),
            now_str,
            str(heartbeat_ttl),
        ]
        return self._scripts["dtch_status"](keys=keys, args=args)

    def list_append_trim(self, key: str, value: str, max_len: int) -> int:
        return self._scripts["list_append_trim"](
            keys=[key], args=[value, str(max_len)]
        )

    def protected_write(
        self,
        key: str,
        value: str,
        caller_id: str,
        ttl: int = 0,
        owner_registry: str = "ares:key_owners",
    ) -> str:
        keys = [key, owner_registry]
        args = [caller_id, value, str(ttl)]
        result = self._scripts["protected_write"](keys=keys, args=args)
        if isinstance(result, bytes):
            result = result.decode()
        if result != "OK":
            log.warning("PROTECTED_WRITE_REJECTED: key=%s caller=%s result=%s",
                       key, caller_id, result)
        return result

    def recon_set_update(
        self, key: str, symbols: List[str], ttl: int = 3600
    ) -> int:
        args = [str(ttl)] + symbols
        return self._scripts["recon_set_update"](keys=[key], args=args)


# ═══════════════════════════════════════════════════════════════════
# KEY OWNER REGISTRY SETUP
# ═══════════════════════════════════════════════════════════════════
DEFAULT_KEY_OWNERS = {
    "trading:enabled": "kill-switch-authority",
    "trade:halt": "kill-switch-authority",
    "trade:halt:reason": "kill-switch-authority",
    "trade:halt:ts": "kill-switch-authority",
    "ssot:target:v2:current": "ssot-promote-v2",
    "ssot:target:v2:canary": "ssot-writer-v2",
    "ssot:target:v2:canary_src": "nextgen2-live",
    "ssot:target:v2:last_good": "ssot-promote-v2",
    "ssot:target:v2:meta": "ssot-promote-v2",
    "ssot:current": "ssot-promote-v2",
}


def setup_key_owners(r, owners: Dict[str, str] = None):
    owners = owners or DEFAULT_KEY_OWNERS
    registry_key = "ares:key_owners"
    r.hset(registry_key, mapping=owners)
    log.info("KEY_OWNER_REGISTRY: %d keys registered", len(owners))
    return owners


# ═══════════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════════
def self_test(r) -> Dict[str, str]:
    """모든 Lua 스크립트가 정상 등록되었는지 확인.
    
    Note: redis-py 7.x + decode_responses=True에서 script_exists()가
    항상 False를 반환하는 버그가 있어, SCRIPT LOAD로 직접 검증합니다.
    """
    results = {}
    ops = AresAtomicOps(r)
    scripts_lua = {
        "ssot_promote": LUA_SSOT_PROMOTE,
        "atomic_emit": LUA_ATOMIC_EMIT,
        "canary_publish": LUA_CANARY_PUBLISH,
        "dtch_dedup": LUA_DTCH_DEDUP,
        "dtch_status": LUA_DTCH_STATUS,
        "list_append_trim": LUA_LIST_APPEND_TRIM,
        "protected_write": LUA_PROTECTED_WRITE,
        "recon_set_update": LUA_RECON_SET_UPDATE,
    }
    for name in ops._scripts:
        try:
            # SCRIPT LOAD는 이미 등록된 스크립트도 SHA를 반환 (idempotent)
            sha = r.script_load(scripts_lua[name])
            results[name] = "OK" if sha else "LOAD_FAILED"
        except Exception as e:
            results[name] = f"ERROR: {e}"
    return results


if __name__ == "__main__":
    import os
    import redis as redis_lib

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    password = os.environ.get("REDIS_PASSWORD")
    if not password:
        print("FATAL: REDIS_PASSWORD environment variable required")
        exit(1)

    r = redis_lib.Redis(host="127.0.0.1", port=6379, password=password,
                        decode_responses=True)

    print("=== ARES Atomic Ops Self-Test ===")
    results = self_test(r)
    for name, status in results.items():
        icon = "✅" if status == "OK" else "❌"
        print(f"  {icon} {name}: {status}")

    all_ok = all(v == "OK" for v in results.values())
    print(f"\n{'✅ ALL SCRIPTS OK' if all_ok else '❌ SOME SCRIPTS FAILED'}")

    if all_ok:
        print("\n=== Setting up Key Owner Registry ===")
        owners = setup_key_owners(r)
        for key, owner in owners.items():
            print(f"  {key} → {owner}")
