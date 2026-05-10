#!/usr/bin/env python3
"""
redis-schema-validator
======================
P4-3: Redis schema registry contract validator (read-only sidecar).

목적:
- /home/ubuntu/ares_current/config/redis-schema-registry.yaml 의 25개 contract 정의를
  매 cycle (300s) 읽어서 라이브 Redis 키와 비교 검증.
- 위반 (missing key / WRONGTYPE / TTL out-of-bounds / unexpected value) 발견 시
  monitor:schema:violations stream + per-violation alert key 발행.
- 어떤 키도 직접 SET/DEL 하지 않음 (read-only).
- 와일드카드 패턴 (price:*, gw:macro:*) 은 SCAN 으로 샘플 1개만 확인.

설계 원칙 (3-AI 컨센서스 반영):
- cycle 300s (5분) — 부담 최소화, contract 위반은 보통 분 단위 issue.
- per-cycle 결과 summary heartbeat: ares:sentinel:redis_schema_validator:heartbeat (TTL 600s).
  (Renamed 2026-05-07 per 4-AI A3 unanimous consensus to coexist with external
  ares-standard-validator (PM2 #56) which writes to ares:sentinel:schema_validator:heartbeat.)
- violation stream MAXLEN=5000.
- alert key TTL=900s (15분), 자동 만료.
- 첫 cycle baseline 기록 후 정상 검증 시작.
- DEPRECATED 키는 위반으로 카운트하지 않고 별도 INFO 처리.
"""
import os
import sys
import json
import time
from datetime import datetime, timezone

REDIS_URL = os.environ.get("ARES_REDIS_URL") or os.environ.get("REDIS_URL")
REGISTRY_PATH = "/home/ubuntu/ares_current/config/redis-schema-registry.yaml"
OVERLAY_PATH = "/home/ubuntu/ares_current/config/redis-schema-registry-overlay-v1.1.yaml"
# 4-AI A3 consensus (2026-05-07): renamed to avoid collision with external
# tools/ares_standard_validator.py (PM2 #56) which already writes to
# ares:sentinel:schema_validator:heartbeat. Both validators now coexist.
HEARTBEAT_KEY = "ares:sentinel:redis_schema_validator:heartbeat"
HEARTBEAT_TTL = 600
VIOLATION_STREAM = "monitor:schema:violations"
STREAM_MAXLEN = 5000
ALERT_KEY_PREFIX = "ares:alert:schema_violation:"
ALERT_TTL = 900
CYCLE_S = 300

if not REDIS_URL:
    print("[FATAL] ARES_REDIS_URL/REDIS_URL not set", flush=True)
    sys.exit(2)

try:
    import redis
    import yaml
except ImportError as e:
    print(f"[FATAL] missing lib: {e}", flush=True)
    sys.exit(2)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def load_registry() -> dict:
    """Load v1.0 registry and merge v1.1 overlay (deprecations + amendments + additions)."""
    with open(REGISTRY_PATH, "r") as f:
        data = yaml.safe_load(f)

    # Try to load overlay v1.1 (P4 권고2)
    overlay = None
    if os.path.exists(OVERLAY_PATH):
        try:
            with open(OVERLAY_PATH, "r") as f:
                overlay = yaml.safe_load(f)
        except Exception as e:
            print(f"[WARN] failed to load overlay {OVERLAY_PATH}: {e}", flush=True)

    if overlay:
        # 1. Apply deprecations: mark v1.0 schemas with deprecated=true
        deprecated_keys = {d["key_pattern"]: d for d in overlay.get("deprecations", [])}
        for sch in data.get("schemas", []):
            kp = sch.get("key_pattern")
            if kp in deprecated_keys:
                d = deprecated_keys[kp]
                # Set deprecated_since so check_key()'s `deprecated` flag becomes True
                sch["deprecated_since"] = d.get("deprecated_since", "2026-05-07")
                sch["sunset_date"] = d.get("sunset_date")
                sch["deprecated_reason"] = d.get("reason")
                sch["replacement_keys"] = d.get("replacement_keys", [])
                sch["migration_status"] = d.get("migration_status")
        # 2. Apply amendments: ttl_relaxation / enum_extension
        amendments = {a["key_pattern"]: a for a in overlay.get("amendments", [])}
        for sch in data.get("schemas", []):
            kp = sch.get("key_pattern")
            if kp in amendments:
                am = amendments[kp]
                if am.get("amendment_type") == "enum_extension":
                    if am.get("values_clear"):
                        sch["values"] = None  # Disable UNEXPECTED_VALUE check
                    else:
                        cur = sch.get("values", []) or []
                        added = am.get("values_added", [])
                        sch["values"] = sorted(set(cur) | set(added))
                ttl_am = am.get("ttl_amendment")
                if ttl_am:
                    if "new_ttl" in ttl_am:
                        sch["ttl"] = ttl_am["new_ttl"]
                    elif "new_ttl_max" in ttl_am:
                        sch["ttl"] = {"min": ttl_am.get("new_ttl_min", 1), "max": ttl_am["new_ttl_max"]}
        # 3. Apply additions: new schemas (also apply deprecations to additions)
        for add in overlay.get("additions", []):
            new_sch = dict(add)
            kp = new_sch.get("key_pattern")
            if kp in deprecated_keys:
                d = deprecated_keys[kp]
                new_sch["deprecated_since"] = d.get("deprecated_since", "2026-05-07")
                new_sch["deprecated_reason"] = d.get("reason")
            data.setdefault("schemas", []).append(new_sch)
        data["_overlay_loaded"] = True
        data["_overlay_version"] = overlay.get("version", "1.1")

    return data


def parse_ttl_bounds(ttl_spec) -> tuple:
    """Return (min_ttl, max_ttl) or (None, None) for 'none'/missing."""
    if ttl_spec is None or ttl_spec == "none":
        return (None, None)
    if isinstance(ttl_spec, int):
        return (1, ttl_spec)
    if isinstance(ttl_spec, dict):
        return (ttl_spec.get("min"), ttl_spec.get("max"))
    return (None, None)


def check_key(r, schema: dict) -> list:
    """Return list of violation dicts for one schema entry."""
    pattern = schema.get("key_pattern")
    expected_type = schema.get("type", "string")
    expected_values = schema.get("values")
    deprecated = schema.get("deprecated_since") is not None
    severity = schema.get("severity", "MEDIUM")
    producer = schema.get("producer", "?")
    min_ttl, max_ttl = parse_ttl_bounds(schema.get("ttl"))
    violations = []

    # Wildcard 패턴 — SCAN 으로 샘플 1개
    if "*" in pattern:
        sample_keys = []
        cursor = 0
        scanned = 0
        while True:
            cursor, batch = r.scan(cursor=cursor, match=pattern, count=50)
            sample_keys.extend(batch)
            scanned += 1
            if cursor == 0 or len(sample_keys) >= 3 or scanned > 5:
                break
        if not sample_keys and not deprecated:
            violations.append({
                "type": "MISSING_PATTERN",
                "pattern": pattern,
                "severity": severity,
                "producer": producer,
                "detail": f"No live keys matching pattern (deprecated={deprecated})",
            })
            return violations
        # 첫 샘플로 type 검증
        if sample_keys:
            keys_to_check = sample_keys[:1]
        else:
            return violations
    else:
        # 정확한 키
        if not r.exists(pattern):
            if not deprecated:
                violations.append({
                    "type": "MISSING_KEY",
                    "pattern": pattern,
                    "severity": severity,
                    "producer": producer,
                    "detail": f"Key not found in Redis (deprecated={deprecated})",
                })
            return violations
        keys_to_check = [pattern]

    for key in keys_to_check:
        # deprecated 키는 존재해도 위반 보고 안 함 (TTL/value 체크 스킵)
        if deprecated:
            continue
        # type 검증
        actual_type = r.type(key)
        if actual_type != expected_type and actual_type != "none":
            violations.append({
                "type": "WRONGTYPE",
                "pattern": pattern,
                "key": key,
                "severity": severity,
                "producer": producer,
                "detail": f"expected={expected_type} actual={actual_type}",
            })
            continue

        # TTL 검증
        ttl = r.ttl(key)
        # ttl == -1: no expiry, ttl == -2: not exists, ttl >= 0: seconds
        if min_ttl is not None and ttl > 0 and ttl < min_ttl:
            violations.append({
                "type": "TTL_TOO_LOW",
                "pattern": pattern,
                "key": key,
                "severity": severity,
                "producer": producer,
                "detail": f"ttl={ttl}s min={min_ttl}s",
            })
        if max_ttl is not None and ttl > max_ttl:
            violations.append({
                "type": "TTL_TOO_HIGH",
                "pattern": pattern,
                "key": key,
                "severity": severity,
                "producer": producer,
                "detail": f"ttl={ttl}s max={max_ttl}s",
            })
        # ttl == -1 expected only if 'none' (min_ttl==max_ttl==None)
        if (min_ttl is not None or max_ttl is not None) and ttl == -1:
            violations.append({
                "type": "TTL_MISSING",
                "pattern": pattern,
                "key": key,
                "severity": severity,
                "producer": producer,
                "detail": "key has no TTL but contract requires bounded TTL",
            })

        # value 검증 (string only, 작은 set)
        if expected_values and expected_type == "string":
            try:
                val = r.get(key)
                if val is not None and val not in expected_values:
                    violations.append({
                        "type": "UNEXPECTED_VALUE",
                        "pattern": pattern,
                        "key": key,
                        "severity": severity,
                        "producer": producer,
                        "detail": f"value={val!r} expected one of {expected_values}",
                    })
            except Exception:
                pass

    return violations


def emit_violation(r, v: dict, cycle: int) -> None:
    record = {
        "ts": now_iso(),
        "cycle": str(cycle),
        "type": v["type"],
        "pattern": v["pattern"],
        "key": v.get("key", ""),
        "severity": v["severity"],
        "producer": v["producer"],
        "detail": v["detail"],
    }
    try:
        r.xadd(VIOLATION_STREAM, record, maxlen=STREAM_MAXLEN, approximate=True)
        # alert key (severity-based)
        alert_key = f"{ALERT_KEY_PREFIX}{v['type']}:{v['pattern']}"
        r.set(alert_key, json.dumps(record), ex=ALERT_TTL)
    except Exception as e:
        print(f"[ERR] emit_violation: {e}", flush=True)


def emit_heartbeat(r, cycle: int, total: int, violations: int, by_type: dict) -> None:
    payload = {
        "ts": now_iso(),
        "cycle": cycle,
        "schemas_checked": total,
        "violations_this_cycle": violations,
        "by_type": by_type,
        "registry_path": REGISTRY_PATH,
    }
    try:
        r.set(HEARTBEAT_KEY, json.dumps(payload), ex=HEARTBEAT_TTL)
    except Exception as e:
        print(f"[ERR] heartbeat: {e}", flush=True)


def main():
    print(f"[INFO] redis-schema-validator starting | cycle={CYCLE_S}s | registry={REGISTRY_PATH}", flush=True)
    r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=15, socket_connect_timeout=10)
    try:
        r.ping()
    except Exception as e:
        print(f"[FATAL] redis ping failed: {e}", flush=True)
        sys.exit(3)

    cycle = 0
    while True:
        cycle += 1
        try:
            registry = load_registry()
            schemas = registry.get("schemas", [])
            total = len(schemas)
            all_violations = []
            for schema in schemas:
                vlist = check_key(r, schema)
                all_violations.extend(vlist)

            by_type = {}
            for v in all_violations:
                by_type[v["type"]] = by_type.get(v["type"], 0) + 1
                emit_violation(r, v, cycle)

            print(f"[CYCLE {cycle}] schemas={total} violations={len(all_violations)} by_type={by_type}", flush=True)
            # 위반 키 상세 출력 (디버깅용)
            if all_violations:
                for v in all_violations[:5]:  # 최대 5개만 출력
                    print(f"  [VIOLATION] type={v.get('type')} key={v.get('pattern','?')} detail={v.get('detail','')}", flush=True)
            emit_heartbeat(r, cycle, total, len(all_violations), by_type)
        except Exception as e:
            print(f"[ERR] cycle {cycle}: {e}", flush=True)
            emit_heartbeat(r, cycle, 0, 0, {"ERROR": 1})

        time.sleep(CYCLE_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[INFO] interrupted", flush=True)
    except Exception as e:
        print(f"[FATAL] {e}", flush=True)
        sys.exit(1)
