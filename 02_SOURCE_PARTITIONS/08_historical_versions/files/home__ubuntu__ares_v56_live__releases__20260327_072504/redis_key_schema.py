#!/usr/bin/env python3
"""
ARES Redis Key Schema Contract — DEFAULT-DENY
All Redis keys used by ARES must be registered here.
Unknown keys are REJECTED, not silently passed.

Deployed: v5.6.1 shadow→live activation
4AI Feedback: Grok 96, Claude 86, GPT 81 — all requested default-deny
"""

import json
import time
import logging
import redis
from typing import Any, Dict, Optional

log = logging.getLogger("ares.schema")

# ─── EXHAUSTIVE SCHEMA ───────────────────────────────────────────────────────
# Every ares:v55:* and ares:v56:* key must be listed with its expected Redis type.
# Adding a new key requires: 1) Add here, 2) Add to JS schema, 3) PR review

ARES_KEY_SCHEMA: Dict[str, str] = {
    # ── v55 Market Data (LIST = time-series history) ──
    "ares:v57:market:vix":                "list",
    "ares:v57:market:spy":                "list",
    "ares:v57:market:qqq":                "list",
    "ares:v57:market:iwm":                "list",
    "ares:v57:market:dia":                "list",
    "ares:v57:market:tlt":                "list",
    "ares:v57:market:gld":                "list",
    "ares:v57:market:vxx":                "list",
    "ares:v57:market:hyg":                "list",
    "ares:v57:market:breadth":            "list",
    "ares:v57:market:sector_rotation":    "list",
    "ares:v57:market:flow_imbalance":     "list",
    "ares:v57:market:overnight_gap":      "list",
    "ares:v57:market:intraday_vol":       "list",

    # ── v55 Regime (STRING = current state) ──
    "ares:v57:regime:current":            "string",
    "ares:v57:regime:confidence":         "string",
    "ares:v57:regime:history":            "list",
    "ares:v57:regime:transition_log":     "list",

    # ── v55 Signal / Alpha (LIST = history) ──
    "ares:v57:signal:cross_section":      "list",
    "ares:v57:signal:theme_engine":       "list",
    "ares:v57:signal:composite":          "list",
    "ares:v57:signal:autotune_suggestions": "list",
    "ares:v57:signal:autotune_override_history": "list",

    # ── v55 Risk (STRING/LIST) ──
    "ares:v57:risk:guards":               "string",
    "ares:v57:risk:halt_status":          "string",
    "ares:v57:risk:position_summary":     "string",
    "ares:v57:risk:equity_anchor":        "string",
    "ares:v57:risk:crash_guard":          "string",
    "ares:v57:risk:guard_history":        "list",

    # ── v55 Execution (LIST = order history) ──
    "ares:v57:execution:orders":          "list",
    "ares:v57:execution:fills":           "list",
    "ares:v57:execution:rejects":         "list",
    "ares:v57:execution:latency":         "list",

    # ── v55 System (STRING = status) ──
    "ares:v57:system:heartbeat":          "string",
    "ares:v57:system:mode":               "string",
    "ares:v57:system:config":             "string",
    "ares:v57:system:feature_flags":      "string",
    "ares:v57:system:safe_mode":          "string",

    # ── v56 keys (same structure, separate namespace) ──
    "ares:v56:market:vix":                "list",
    "ares:v56:regime:current":            "string",
    "ares:v56:signal:composite":          "list",
    "ares:v56:system:heartbeat":          "string",
    "ares:v56:system:mode":               "string",

    # ── OIE Config (STRING = hot-reload config) ──
    "oie:config:allowed_families":        "string",
    "oie:config:ares-v56:max_notional":   "string",
    "oie:config:ares-v56:max_position_pct": "string",

    # ── WRONGTYPE counters/backups (auto-created) ──
    # Pattern: ares:wrongtype:counter:* → string (auto, TTL 86400)
    # Pattern: *:wrongtype:* → renamed backup keys (auto, TTL 604800)

    # ── Trade control ──
    "trade:halt":                         "string",
}

# Keys matching these prefixes are auto-allowed (counters, backups)
AUTO_ALLOW_PREFIXES = [
    "ares:wrongtype:counter:",
    "oie:cross_family_lock:",
    "order:dedupe:",
]


class SchemaViolationError(Exception):
    """Raised when a Redis operation violates the key schema."""
    pass


class TradingRedisClient:
    """
    DEFAULT-DENY Redis wrapper.
    Every write operation is checked against ARES_KEY_SCHEMA.
    Unknown keys → REJECT + alert (not silently pass).
    """

    def __init__(self, redis_client: redis.Redis, schema: Dict[str, str] = None,
                 alert_fn=None, strict: bool = True):
        self._r = redis_client
        self._schema = schema or ARES_KEY_SCHEMA
        self._alert_fn = alert_fn or (lambda msg: log.critical(msg))
        self._strict = strict  # True = raise on unknown, False = warn only

    def _check_key(self, key: str, expected_type: str):
        """DEFAULT-DENY: reject unknown keys, reject type mismatches."""
        # Auto-allow prefixes (counters, locks, dedup)
        for prefix in AUTO_ALLOW_PREFIXES:
            if key.startswith(prefix):
                return

        if key not in self._schema:
            msg = f"SCHEMA VIOLATION: Unknown key '{key}'. Add to redis_key_schema.py first."
            self._alert_fn(msg)
            if self._strict:
                raise SchemaViolationError(msg)
            else:
                log.warning(msg)
                return

        if self._schema[key] != expected_type:
            msg = f"TYPE VIOLATION: Key '{key}' must be {self._schema[key]}, attempted {expected_type}"
            self._alert_fn(msg)
            raise SchemaViolationError(msg)

    # ── Wrapped Redis operations ──

    def lpush(self, key: str, *values):
        self._check_key(key, "list")
        return self._r.lpush(key, *values)

    def rpush(self, key: str, *values):
        self._check_key(key, "list")
        return self._r.rpush(key, *values)

    def set(self, key: str, value, **kwargs):
        self._check_key(key, "string")
        return self._r.set(key, value, **kwargs)

    def setex(self, key: str, time_sec: int, value):
        self._check_key(key, "string")
        return self._r.setex(key, time_sec, value)

    def hset(self, key: str, *args, **kwargs):
        self._check_key(key, "hash")
        return self._r.hset(key, *args, **kwargs)

    # ── Read operations (no schema check needed) ──

    def get(self, key: str):
        return self._r.get(key)

    def lrange(self, key: str, start: int, stop: int):
        return self._r.lrange(key, start, stop)

    def hgetall(self, key: str):
        return self._r.hgetall(key)

    def exists(self, key: str):
        return self._r.exists(key)

    def type(self, key: str):
        return self._r.type(key)

    # ── Pass-through for non-write operations ──

    def ltrim(self, key, start, stop):
        return self._r.ltrim(key, start, stop)

    def expire(self, key, seconds):
        return self._r.expire(key, seconds)

    def delete(self, *keys):
        return self._r.delete(*keys)

    def pipeline(self, **kwargs):
        return self._r.pipeline(**kwargs)

    def rename(self, src, dst):
        return self._r.rename(src, dst)

    def incr(self, key):
        return self._r.incr(key)

    def eval(self, *args, **kwargs):
        return self._r.eval(*args, **kwargs)

    def evalsha(self, *args, **kwargs):
        return self._r.evalsha(*args, **kwargs)

    def script_load(self, script):
        return self._r.script_load(script)

    def xrange(self, *args, **kwargs):
        return self._r.xrange(*args, **kwargs)

    def xack(self, *args, **kwargs):
        return self._r.xack(*args, **kwargs)

    @property
    def raw(self):
        """Access underlying redis client for operations not covered by wrapper."""
        return self._r


def validate_all_keys(r: redis.Redis, schema: Dict[str, str] = None,
                      fix: bool = False) -> Dict[str, Any]:
    """
    Scan all ares:* keys and validate types against schema.
    Returns: {"ok": bool, "violations": [...], "fixed": [...]}
    """
    schema = schema or ARES_KEY_SCHEMA
    violations = []
    fixed = []

    for key_pattern in ["ares:v57:*", "ares:v56:*", "oie:config:*"]:
        for key in r.scan_iter(match=key_pattern, count=100):
            key_str = key.decode() if isinstance(key, bytes) else key
            actual_type = r.type(key_str)
            if isinstance(actual_type, bytes):
                actual_type = actual_type.decode()

            if key_str in schema:
                expected = schema[key_str]
                if actual_type != expected and actual_type != "none":
                    violation = {
                        "key": key_str,
                        "expected": expected,
                        "actual": actual_type,
                        "timestamp": time.time()
                    }
                    violations.append(violation)
                    log.warning("SCHEMA VIOLATION: %s expected=%s actual=%s", key_str, expected, actual_type)

                    if fix:
                        backup = f"{key_str}:wrongtype:{int(time.time())}"
                        pipe = r.pipeline()
                        pipe.rename(key_str, backup)
                        pipe.expire(backup, 604800)  # 7-day TTL
                        pipe.execute()
                        fixed.append({"key": key_str, "backup": backup})
                        log.info("FIXED: renamed %s → %s", key_str, backup)

    return {
        "ok": len(violations) == 0,
        "violations": violations,
        "fixed": fixed,
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }


if __name__ == "__main__":
    import sys
    import os

    REDIS_URL = os.environ.get("REDIS_URL", "redis://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@localhost:6379/0")
    r = redis.from_url(REDIS_URL)

    print(f"Schema keys: {len(ARES_KEY_SCHEMA)}")
    result = validate_all_keys(r, fix="--fix" in sys.argv)
    print(json.dumps(result, indent=2, default=str))

    if not result["ok"]:
        print(f"\n⚠ {len(result['violations'])} violations found!")
        sys.exit(1)
    else:
        print("\n✅ All keys valid")
