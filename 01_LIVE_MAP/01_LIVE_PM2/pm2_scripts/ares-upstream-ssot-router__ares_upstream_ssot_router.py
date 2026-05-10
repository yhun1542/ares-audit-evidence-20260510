#!/usr/bin/env python3
# SOURCE: /home/ubuntu/ops/ares_upstream_ssot_router.py
# VERSION: 5.0.0_struct_fix_2026_05_08
# CHANGES vs 4.1.0_safety_2026_05_06:
#   - [STRUCT-FIX-2026-05-08] Added explicit ssot:source_state publication so
#     downstream guards (ssot-pipeline-guard, owner-guard, watchdog) can tell
#     "publisher is silent because validator is NO_GO" apart from
#     "publisher is dead". States: PUBLISHING, SOURCE_EMPTY, SOURCE_STALE,
#     TOO_FEW_SYMBOLS, HOLDING_NO_GO, HOLDING_FAIL_CLOSED, REDIS_ERROR.
#   - [STRUCT-FIX-2026-05-08] Added stale-while-revalidate hold-over: when the
#     publish must be skipped (source empty / fail-closed / stale source) AND a
#     `ssot:target:v2:last_good` payload is fresh enough, the router re-asserts
#     it on engine_src/exec_src with a SHORT TTL (HOLDOVER_TTL_SEC, default 90s)
#     so that the consumer chain does not see TTL=-2 and trigger spurious
#     CRITICAL restarts of nextgen2-live / reconciler. The held-over payload
#     is annotated with meta.holdover=true and meta.hold_reason.
#   - [STRUCT-FIX-2026-05-08] On every successful publish we also persist the
#     payload to `ssot:target:v2:last_good` (PERSIST, no TTL — overwritten only
#     on the next successful publish; cleared only by ops).
#   - [STRUCT-FIX-2026-05-08] Heartbeat now includes source_state so external
#     watchdogs can dedupe NO_GO-driven STALE alerts from real outages.
"""ARES Upstream SSOT Router v5.0.0 (Structural Fix).

Reads ``champion:targets:ssot`` (HASH) and publishes:

  * ``ssot:target:v2:engine_src``   (STRING, JSON, TTL ENGINE_SRC_TTL or HOLDOVER_TTL)
  * ``ssot:target:v2:exec_src``     (STRING, JSON, TTL EXEC_SRC_TTL   or HOLDOVER_TTL)
  * ``ssot:target:v2:last_good``    (STRING, JSON, no TTL)
  * ``<NAME>:health``               (STRING, JSON, TTL HEALTH_TTL)
  * ``ssot:source:router:heartbeat``(STRING, JSON, TTL HEARTBEAT_TTL)
  * ``ssot:source_state``           (STRING, JSON, TTL HEARTBEAT_TTL)

Idempotent, fail-closed, single-writer. Designed to be supervised by PM2.

KEY STRUCTURAL CHANGE (2026-05-08):
The previous router treated "validator NO_GO" and "process dead" identically
— both produced TTL=-2 on engine_src/exec_src, which the downstream pipeline
guard interpreted as CRITICAL and force-restarted nextgen2-live, which in
turn pushed execution-reconciler into a SIGINT/FATAL loop. This version
distinguishes the two by (a) emitting an explicit source_state key and
(b) holding over the last good payload for a short TTL window so consumers
can keep reading a coherent snapshot while the policy chain catches up.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import redis

# [ARES-SSOT 2026-04-24] admin tier Redis client helper
import sys as _sys

# === [P3-PATCH-HOLIDAY-AWARE] BEGIN ===
# Phase-3 patch: holiday-aware session classifier (added 2026-05-08).
# 외부 모듈 ares_market_calendar (NYSE holiday/early close 인식)을 우선 사용.
# 라이브러리/모듈 부재 시 기존 weekday 기반 fallback이 작동.
import sys as _p3_sys
_p3_sys.path.insert(0, "/home/ubuntu/scripts")
try:
    from ares_market_calendar import get_session as _ares_get_session_external
    _ARES_HOLIDAY_AWARE = True
except Exception:
    _ares_get_session_external = None
    _ARES_HOLIDAY_AWARE = False
# === [P3-PATCH-HOLIDAY-AWARE] END ===

_sys.path.insert(0, "/home/ubuntu/scripts")
try:
    from redis_ssot_loader import get_redis_client as _ares_get_redis
except ImportError as _e:
    raise RuntimeError(
        "ARES SSOT loader not available. Ensure /home/ubuntu/scripts/redis_ssot_loader.py exists."
    ) from _e

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
NAME = "ares-upstream-ssot-router"
VERSION = "5.0.0_struct_fix_2026_05_08"

REDIS_URL = os.getenv("ARES_REDIS_URL") or os.getenv("REDIS_URL")
if not REDIS_URL:
    env_path = "/etc/ares/redis.env"
    if os.path.isfile(env_path):
        for line in open(env_path):
            if line.startswith("REDIS_URL="):
                REDIS_URL = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
if not REDIS_URL:
    print("[FATAL] REDIS_URL not set and /etc/ares/redis.env missing", flush=True)
    sys.exit(1)

SOURCE_HASH_KEY   = os.getenv("ARES_SOURCE_HASH_KEY",   "champion:targets:ssot")
SOURCE_TS_KEY     = os.getenv("ARES_SOURCE_TS_KEY",     "champion:targets:ssot:ts")
ENGINE_SRC_KEY    = os.getenv("ARES_ENGINE_SRC_KEY",    "ssot:target:v2:engine_src")
EXEC_SRC_KEY      = os.getenv("ARES_EXEC_SRC_KEY",      "ssot:target:v2:exec_src")
LAST_GOOD_KEY     = os.getenv("ARES_LAST_GOOD_KEY",     "ssot:target:v2:last_good")
HEALTH_KEY        = os.getenv("ARES_HEALTH_KEY",        f"{NAME}:health")
HEARTBEAT_KEY     = os.getenv("ARES_ROUTER_HEARTBEAT_KEY", "ssot:source:router:heartbeat")
SOURCE_STATE_KEY  = os.getenv("ARES_SOURCE_STATE_KEY",  "ssot:source_state")

ENGINE_SRC_TTL    = int(os.getenv("ARES_ENGINE_SRC_TTL_SEC", "900"))
EXEC_SRC_TTL      = int(os.getenv("ARES_EXEC_SRC_TTL_SEC",   "600"))
HEALTH_TTL        = int(os.getenv("ARES_HEALTH_TTL_SEC",     "180"))
HEARTBEAT_TTL     = int(os.getenv("ARES_HEARTBEAT_TTL_SEC",  "120"))
HOLDOVER_TTL      = int(os.getenv("ARES_HOLDOVER_TTL_SEC",   "90"))
HOLDOVER_MAX_AGE  = int(os.getenv("ARES_HOLDOVER_MAX_AGE_SEC", "900"))  # do not hold over older than 15min

LOOP_SEC          = int(os.getenv("ARES_LOOP_SEC",           "10"))
MIN_SYMBOLS       = int(os.getenv("ARES_MIN_SYMBOLS",        "20"))
# Legacy single-threshold (fallback if session-specific not set)
MAX_SOURCE_AGE_S  = int(os.getenv("ARES_MAX_SOURCE_AGE_SEC", "300"))

# [STRUCT-PATCH 2026-05-08 Manus] Session-aware stale thresholds.
# Upstream publisher legitimately publishes less frequently outside RTH;
# applying a single 300s threshold creates spurious ERROR alerts at night/weekends.
# Defaults below match NYSE schedule (US/Eastern); override via env if needed.
#   RTH    = Regular Trading Hours       (09:30-16:00 ET)
#   EXT    = Extended Hours / Pre+After  (04:00-09:30, 16:00-20:00 ET)
#   CLOSED = Outside above + weekends    (everything else, incl. US holidays)
MAX_SOURCE_AGE_S_RTH    = int(os.getenv("ARES_MAX_SOURCE_AGE_SEC_RTH",    str(MAX_SOURCE_AGE_S)))
MAX_SOURCE_AGE_S_EXT    = int(os.getenv("ARES_MAX_SOURCE_AGE_SEC_EXT",    "900"))
MAX_SOURCE_AGE_S_CLOSED = int(os.getenv("ARES_MAX_SOURCE_AGE_SEC_CLOSED", "3600"))

def _current_session() -> str:
    """Return one of {'RTH','EXT','CLOSED'} based on US/Eastern wall clock.
    Approximation only -- does not account for US holidays / early closes.
    Watchdog accuracy is more important than perfect calendar precision.
    """
    # [P3-HOLIDAY-INJECT]
    if _ARES_HOLIDAY_AWARE and _ares_get_session_external is not None:
        try:
            return _ares_get_session_external().get('session', 'CLOSED')
        except Exception:
            pass  # fall through to weekday-based fallback
    try:
        from zoneinfo import ZoneInfo
        et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback: ET = UTC - 5h (no DST handling). Acceptable for stale alerting.
        et = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)
    if et.weekday() >= 5:  # Sat/Sun
        return "CLOSED"
    minute_of_day = et.hour * 60 + et.minute
    rth_start = 9 * 60 + 30   # 09:30
    rth_end   = 16 * 60       # 16:00
    ext_start = 4 * 60        # 04:00
    ext_end   = 20 * 60       # 20:00
    if rth_start <= minute_of_day < rth_end:
        return "RTH"
    if ext_start <= minute_of_day < ext_end:
        return "EXT"
    return "CLOSED"

def current_max_source_age() -> Tuple[int, str]:
    """Return (threshold_seconds, session_label) appropriate for current time."""
    sess = _current_session()
    if sess == "RTH":
        return MAX_SOURCE_AGE_S_RTH, sess
    if sess == "EXT":
        return MAX_SOURCE_AGE_S_EXT, sess
    return MAX_SOURCE_AGE_S_CLOSED, sess
PORTFOLIO_VOL_EST = float(os.getenv("ARES_PORTFOLIO_VOL_EST", "0.13"))
ENGINE_VERSION    = os.getenv("ARES_ENGINE_VERSION",         "v7.0_LOCKED_20260130")

# SAFETY-PATCH: optional shadow mode. When set, NO_GO without override does
# not block the SSOT write but only logs+writes a halt request that downstream
# can choose to honor. Default OFF (strict fail-closed).
SHADOW_MODE       = os.getenv("ARES_SAFETY_SHADOW", "0").strip() == "1"

HOST = socket.gethostname()

# Source-state vocabulary (single source of truth for downstream consumers).
STATE_PUBLISHING       = "PUBLISHING"
STATE_SOURCE_EMPTY     = "SOURCE_EMPTY"
STATE_SOURCE_STALE     = "SOURCE_STALE"
STATE_TOO_FEW_SYMBOLS  = "TOO_FEW_SYMBOLS"
STATE_HOLDING_NO_GO    = "HOLDING_NO_GO"
STATE_HOLDING_FAIL_CLOSED = "HOLDING_FAIL_CLOSED"
STATE_REDIS_ERROR      = "REDIS_ERROR"

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
def log(level: str, msg: str, /, **kw: Any) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    extra = f" {json.dumps(kw, ensure_ascii=False, default=str)}" if kw else ""
    print(f"[{ts}] [{level}] {NAME}: {msg}{extra}", flush=True)

# -----------------------------------------------------------------------------
# Signal handling
# -----------------------------------------------------------------------------
_shutdown = False

def _sig(*_: Any) -> None:
    global _shutdown
    _shutdown = True

for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, _sig)

# -----------------------------------------------------------------------------
# Source parsers
# -----------------------------------------------------------------------------
def _parse_source_ts(raw: str | None) -> float:
    if not raw:
        return 0.0
    raw = raw.strip()
    try:
        n = float(raw)
        return n / 1000.0 if n > 1e12 else n
    except ValueError:
        pass
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).timestamp()
    except Exception:
        return 0.0

def _read_source(r: redis.Redis) -> Tuple[List[Dict[str, Any]], float]:
    raw_ts = r.get(SOURCE_TS_KEY)
    src_ts = _parse_source_ts(raw_ts)
    h = r.hgetall(SOURCE_HASH_KEY)
    if not h:
        return [], src_ts
    positions: List[Dict[str, Any]] = []
    for sym, vraw in h.items():
        # Skip the dedicated marker field if present
        if sym == "_meta_marker":
            continue
        try:
            entry = json.loads(vraw) if isinstance(vraw, str) else vraw
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        sym_u = str(entry.get("symbol") or sym).upper().strip()
        if not sym_u:
            continue
        try:
            w = float(entry.get("weight", 0))
        except Exception:
            continue
        if abs(w) < 1e-10:
            continue
        positions.append({
            "symbol": sym_u,
            "w": round(w, 10),
            "side": "BUY" if w >= 0 else "SELL",
            "target_value": float(entry.get("target_value", 0.0) or 0.0),
        })
    positions.sort(key=lambda p: p["symbol"])
    return positions, src_ts

# -----------------------------------------------------------------------------
# SAFETY-PATCH: Phase 9 marker carrythrough with fail-closed gate
# -----------------------------------------------------------------------------
class FailClosed(RuntimeError):
    """Raised when the SSOT write cycle must be skipped due to a safety gate."""

def _read_upstream_marker(r: redis.Redis) -> Dict[str, Any] | None:
    """Read phase9_b1_marker from the upstream `champion:targets:ssot`.

    Preference order:
      1. Dedicated field `_meta_marker` (future-friendly).
      2. Any symbol entry's `phase9_b1_marker` JSON sub-object.
    """
    raw = r.hget(SOURCE_HASH_KEY, "_meta_marker")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    h = r.hgetall(SOURCE_HASH_KEY)
    for sym, vraw in h.items():
        if sym == "_meta_marker":
            continue
        if not isinstance(vraw, str):
            continue
        try:
            entry = json.loads(vraw)
        except Exception:
            continue
        m = entry.get("phase9_b1_marker") if isinstance(entry, dict) else None
        if isinstance(m, dict) and m:
            return m
    return None


def _is_override_signed_and_fresh(r: redis.Redis, marker: Dict[str, Any]) -> bool:
    raw = r.get("policy:override:active")
    if not raw:
        return False
    try:
        active = json.loads(raw)
    except Exception:
        return False
    if not isinstance(active, dict):
        return False
    expected_sha = marker.get("candidate_config_sha256")
    if not expected_sha or active.get("candidate_config_sha256") != expected_sha:
        return False
    expires_at_ms = active.get("expires_at_ms", 0)
    if not isinstance(expires_at_ms, (int, float)) or expires_at_ms <= int(time.time() * 1000):
        return False
    sig = active.get("signature")
    signing_key = os.getenv("ARES_OVERRIDE_SIGNING_KEY", "")
    if signing_key and sig:
        body_obj = {k: active[k] for k in active if k != "signature"}
        body = json.dumps(body_obj, sort_keys=True, separators=(",", ":"))
        expected = hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
    elif signing_key and not sig:
        return False
    return True


def _resolve_phase9_marker_block(r: redis.Redis) -> Dict[str, Any]:
    marker = _read_upstream_marker(r)
    if marker is None:
        raise FailClosed("phase9_b1_marker missing from upstream — refusing to publish SSOT")

    decision = (marker.get("standard_validator_decision") or "").upper()
    override = (marker.get("override_decision") or "").upper()

    if decision == "NO_GO":
        if not _is_override_signed_and_fresh(r, marker):
            try:
                r.xadd("ares:validator:rejected", {
                    "ts_ms": str(int(time.time() * 1000)),
                    "router_host": HOST,
                    "marker_decision": decision,
                    "override_decision": override or "ABSENT",
                    "candidate_id": marker.get("candidate_id", "unknown"),
                    "reason": "NO_GO_without_signed_override",
                }, maxlen=10000, approximate=True)
            except Exception:
                pass
            if SHADOW_MODE:
                validation_state = "SHADOW_DEGRADED"
            else:
                raise FailClosed(
                    f"standard_validator_decision={decision} without signed override "
                    f"(candidate_id={marker.get('candidate_id', 'unknown')})"
                )
        else:
            validation_state = "OVERRIDE_APPROVED"
    elif decision == "GO":
        validation_state = "VALIDATOR_APPROVED"
    elif decision == "":
        raise FailClosed("standard_validator_decision absent — refusing to publish SSOT")
    else:
        raise FailClosed(f"unknown standard_validator_decision={decision!r}")

    try:
        r.xadd("ares:validator:approved", {
            "ts_ms": str(int(time.time() * 1000)),
            "router_host": HOST,
            "marker_decision": decision,
            "override_decision": override or "NONE",
            "candidate_id": marker.get("candidate_id", "unknown"),
            "validation_state": validation_state,
        }, maxlen=10000, approximate=True)
    except Exception:
        pass

    return {
        "phase9_b1_marker": dict(marker),
        "candidate_id": marker.get("candidate_id"),
        "candidate_config_sha256": marker.get("candidate_config_sha256"),
        "universe_sha256": marker.get("universe_sha256"),
        "validation_state": validation_state,
        "marker_resolved_at_ms": int(time.time() * 1000),
        "marker_resolved_by": NAME,
    }

# -----------------------------------------------------------------------------
# Payload builder
# -----------------------------------------------------------------------------
def build_payload(positions: List[Dict[str, Any]], src_ts: float, r: redis.Redis) -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    now_iso_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    raw_gross = sum(abs(p["w"]) for p in positions)
    leverage = round(raw_gross, 10) if raw_gross > 0 else 1.0
    scale = (1.0 / raw_gross) if raw_gross > 1.0 else 1.0

    norm_positions: List[Dict[str, Any]] = []
    router_weights: Dict[str, Dict[str, float]] = {}
    for p in positions:
        w_raw = p["w"]
        w_norm = round(w_raw * scale, 10)
        norm_positions.append({
            "symbol": p["symbol"],
            "w": w_norm,
            "side": "BUY" if w_norm >= 0 else "SELL",
            "target_value": p.get("target_value", 0.0),
        })
        router_weights[p["symbol"]] = {
            "w_raw": w_raw,
            "w_norm": w_norm,
        }

    raw_normed_gross = sum(abs(p["w"]) for p in norm_positions)
    if raw_normed_gross > 1.0:
        fix_scale = 1.0 / raw_normed_gross
        for np_ in norm_positions:
            np_["w"] = round(np_["w"] * fix_scale, 10)
        for sym in router_weights:
            router_weights[sym]["w_norm"] = round(router_weights[sym]["w_norm"] * fix_scale, 10)
    gross = round(sum(abs(p["w"]) for p in norm_positions), 10)
    if gross > 1.0:
        gross = 1.0
    net = round(sum(p["w"] for p in norm_positions), 10)

    # SAFETY-PATCH: dynamic marker resolution with fail-closed.
    marker_block = _resolve_phase9_marker_block(r)

    return {
        "schema_version": "SSOT_TARGET_V2",
        "ts": now_ms,
        "asof": now_iso_date,
        "engine_version": ENGINE_VERSION,
        "correlation_id": f"router-{HOST}-{now_ms}",
        "targets": {
            "gross": gross,
            "net": net,
            "positions": norm_positions,
            "meta": {
                "source_hash_key": SOURCE_HASH_KEY,
                "source_ts_key": SOURCE_TS_KEY,
                "source_ts_ms": int(src_ts * 1000),
                "router": NAME,
                "router_version": VERSION,
                "router_host": HOST,
                "portfolio_vol_est": PORTFOLIO_VOL_EST,
                "leverage": leverage,
                "raw_gross": round(raw_gross, 10),
                "router_weights": router_weights,
                "holdover": False,
                **marker_block,
            },
        },
    }

# -----------------------------------------------------------------------------
# Write cycle
# -----------------------------------------------------------------------------
def write_sinks(r: redis.Redis, payload: Dict[str, Any]) -> Tuple[int, int]:
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    p = r.pipeline(transaction=False)
    p.set(ENGINE_SRC_KEY, blob, ex=ENGINE_SRC_TTL)
    p.set(EXEC_SRC_KEY,   blob, ex=EXEC_SRC_TTL)
    # [STRUCT-FIX-2026-05-08] persist last_good with no TTL — overwritten on
    # every successful publish, never expires. This is the snapshot the router
    # will hold over when downstream policy briefly forbids re-publishing.
    p.set(LAST_GOOD_KEY, blob)
    p.execute()
    ttl_engine = r.ttl(ENGINE_SRC_KEY)
    ttl_exec   = r.ttl(EXEC_SRC_KEY)
    return ttl_engine, ttl_exec


def _holdover_publish(r: redis.Redis, hold_reason: str, source_state: str) -> Tuple[bool, int, int, float]:
    """Stale-while-revalidate: re-assert last_good with a SHORT TTL.

    Returns (ok, ttl_engine, ttl_exec, age_s). Refuses if last_good is older
    than HOLDOVER_MAX_AGE.
    """
    raw = r.get(LAST_GOOD_KEY)
    if not raw:
        return False, -2, -2, -1.0
    try:
        payload = json.loads(raw)
    except Exception:
        return False, -2, -2, -1.0
    payload_ts_ms = int(payload.get("ts", 0) or 0)
    age_s = max(0.0, (time.time() * 1000.0 - payload_ts_ms) / 1000.0)
    if age_s > HOLDOVER_MAX_AGE:
        return False, -2, -2, age_s
    # Mark the held-over copy so consumers can tell.
    try:
        meta = payload.get("targets", {}).get("meta", {})
        meta["holdover"] = True
        meta["hold_reason"] = hold_reason
        meta["hold_state"] = source_state
        meta["holdover_emitted_at_ms"] = int(time.time() * 1000)
        payload["targets"]["meta"] = meta
    except Exception:
        pass
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    p = r.pipeline(transaction=False)
    p.set(ENGINE_SRC_KEY, blob, ex=HOLDOVER_TTL)
    p.set(EXEC_SRC_KEY,   blob, ex=HOLDOVER_TTL)
    p.execute()
    return True, r.ttl(ENGINE_SRC_KEY), r.ttl(EXEC_SRC_KEY), age_s


def write_health(r: redis.Redis, status: str, note: str, source_state: str = STATE_PUBLISHING, **extras: Any) -> None:
    body = {
        "ts": time.time(),
        "name": NAME,
        "version": VERSION,
        "host": HOST,
        "status": status,
        "note": note,
        "source_state": source_state,
        **extras,
    }
    p = r.pipeline(transaction=False)
    p.set(HEALTH_KEY,    json.dumps(body, ensure_ascii=False), ex=HEALTH_TTL)
    p.set(HEARTBEAT_KEY, json.dumps({
        "ts": body["ts"],
        "name": NAME,
        "host": HOST,
        "status": status,
        "source_state": source_state,
    }, ensure_ascii=False), ex=HEARTBEAT_TTL)
    # [STRUCT-FIX-2026-05-08] explicit source_state for downstream guards
    p.set(SOURCE_STATE_KEY, json.dumps({
        "ts": body["ts"],
        "state": source_state,
        "note": note,
        "by": NAME,
        "version": VERSION,
        "host": HOST,
    }, ensure_ascii=False), ex=HEARTBEAT_TTL)
    p.execute()

def _write_auto_halt(r: redis.Redis, reason: str) -> None:
    """Write an auto halt request to ops:halt:request when safety gate trips."""
    try:
        r.set("ops:halt:request", json.dumps({
            "ts_ms": int(time.time() * 1000),
            "actor": NAME,
            "reason": reason,
            "auto": True,
            "host": HOST,
        }), ex=3600)
    except Exception as e:
        log("ERROR", "failed to write auto halt request", err=str(e))

# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------
def main() -> int:
    r = _ares_get_redis()
    log("INFO", "starting",
        source_hash_key=SOURCE_HASH_KEY, source_ts_key=SOURCE_TS_KEY,
        engine_src_key=ENGINE_SRC_KEY,  exec_src_key=EXEC_SRC_KEY,
        last_good_key=LAST_GOOD_KEY,
        engine_ttl=ENGINE_SRC_TTL, exec_ttl=EXEC_SRC_TTL,
        holdover_ttl=HOLDOVER_TTL, holdover_max_age=HOLDOVER_MAX_AGE,
        loop_sec=LOOP_SEC, min_symbols=MIN_SYMBOLS, max_source_age_s=MAX_SOURCE_AGE_S,
        version=VERSION, shadow_mode=SHADOW_MODE)

    cycle = 0
    err_streak = 0
    while not _shutdown:
        cycle += 1
        t0 = time.time()
        try:
            positions, src_ts = _read_source(r)
            if not positions:
                # Source HASH is empty — most likely upstream bridge is in
                # validator NO_GO skip. Keep downstream alive by holding over.
                ho_ok, ho_e, ho_x, ho_age = _holdover_publish(r, "source_empty_upstream_skip", STATE_SOURCE_EMPTY)
                state = STATE_SOURCE_EMPTY
                write_health(r, "warn" if ho_ok else "fail",
                             "source_empty" + ("_holdover" if ho_ok else ""),
                             source_state=state,
                             cycle=cycle, n_positions=0,
                             source_hash_key=SOURCE_HASH_KEY,
                             holdover=ho_ok, holdover_age_s=round(ho_age, 1),
                             engine_src_ttl=ho_e, exec_src_ttl=ho_x)
                log("WARN" if ho_ok else "ERROR",
                    "source empty; " + ("holdover_published" if ho_ok else "holdover_unavailable"),
                    source_hash_key=SOURCE_HASH_KEY, source_ts_key=SOURCE_TS_KEY,
                    holdover=ho_ok, holdover_age_s=round(ho_age, 1))
                err_streak += 1
            else:
                age = time.time() - src_ts if src_ts > 0 else None
                # [STRUCT-PATCH 2026-05-08 Manus] session-aware threshold
                eff_max_age, session_label = current_max_source_age()
                if age is not None and age > eff_max_age:
                    ho_ok, ho_e, ho_x, ho_age = _holdover_publish(r, f"source_stale_{age:.0f}s_{session_label}", STATE_SOURCE_STALE)
                    # Severity: only RTH stale is ERROR; EXT is WARN; CLOSED is INFO (alarm fatigue control)
                    sev = "fail" if session_label == "RTH" else ("warn" if session_label == "EXT" else "warn")
                    log_lvl = "ERROR" if session_label == "RTH" else ("WARN" if session_label == "EXT" else "INFO")
                    write_health(r, sev, f"source_stale:{age:.0f}s>{eff_max_age}s ({session_label})",
                                 source_state=STATE_SOURCE_STALE,
                                 cycle=cycle, n_positions=len(positions), age_s=age,
                                 session=session_label, max_age_s_effective=eff_max_age,
                                 holdover=ho_ok, holdover_age_s=round(ho_age, 1),
                                 engine_src_ttl=ho_e, exec_src_ttl=ho_x)
                    log(log_lvl, "source stale beyond threshold; holdover=" + str(ho_ok),
                        age_s=age, max_age_s=eff_max_age, session=session_label,
                        n_positions=len(positions), holdover=ho_ok)
                    err_streak += 1
                elif len(positions) < MIN_SYMBOLS:
                    ho_ok, ho_e, ho_x, ho_age = _holdover_publish(r, f"too_few_symbols_{len(positions)}", STATE_TOO_FEW_SYMBOLS)
                    write_health(r, "fail", f"too_few_symbols:{len(positions)}<{MIN_SYMBOLS}",
                                 source_state=STATE_TOO_FEW_SYMBOLS,
                                 cycle=cycle, n_positions=len(positions),
                                 holdover=ho_ok, holdover_age_s=round(ho_age, 1),
                                 engine_src_ttl=ho_e, exec_src_ttl=ho_x)
                    log("ERROR", "too few symbols; holdover=" + str(ho_ok),
                        n_positions=len(positions), min_symbols=MIN_SYMBOLS, holdover=ho_ok)
                    err_streak += 1
                else:
                    try:
                        payload = build_payload(positions, src_ts, r)
                    except FailClosed as fc:
                        # Validator NO_GO without signed override is the most
                        # common reason here. Hold over to keep nextgen2-live
                        # and reconciler from being uselessly restarted.
                        msg = str(fc)
                        is_no_go = "NO_GO" in msg or "validator_decision" in msg
                        state = STATE_HOLDING_NO_GO if is_no_go else STATE_HOLDING_FAIL_CLOSED
                        ho_ok, ho_e, ho_x, ho_age = _holdover_publish(r, f"safety_gate:{msg[:120]}", state)
                        write_health(r, "warn" if ho_ok else "fail",
                                     f"safety_gate:{msg[:120]}",
                                     source_state=state,
                                     cycle=cycle, n_positions=len(positions),
                                     holdover=ho_ok, holdover_age_s=round(ho_age, 1),
                                     engine_src_ttl=ho_e, exec_src_ttl=ho_x)
                        log("WARN" if ho_ok else "ERROR",
                            "SAFETY GATE: " + ("holdover_published" if ho_ok else "publish refused"),
                            reason=msg, n_positions=len(positions), state=state, holdover=ho_ok)
                        _write_auto_halt(r, f"SAFETY_GATE:{msg[:200]}")
                        err_streak += 1
                    else:
                        ttl_e, ttl_x = write_sinks(r, payload)
                        write_health(r, "ok", "published",
                                     source_state=STATE_PUBLISHING,
                                     cycle=cycle,
                                     n_positions=len(positions),
                                     source_age_s=age,
                                     gross=payload["targets"]["gross"],
                                     net=payload["targets"]["net"],
                                     validation_state=payload["targets"]["meta"].get("validation_state"),
                                     engine_src_ttl=ttl_e, exec_src_ttl=ttl_x)
                        log("INFO", "published",
                            n=len(positions), age_s=round(age or 0, 1),
                            gross=payload["targets"]["gross"],
                            validation_state=payload["targets"]["meta"].get("validation_state"),
                            ttl_engine=ttl_e, ttl_exec=ttl_x)
                        err_streak = 0
        except redis.RedisError as e:
            err_streak += 1
            log("ERROR", "redis error", err=str(e), err_streak=err_streak)
            try:
                write_health(r, "fail", f"redis_error:{type(e).__name__}",
                             source_state=STATE_REDIS_ERROR,
                             cycle=cycle, error=str(e), err_streak=err_streak)
            except Exception:
                pass
        except Exception as e:
            err_streak += 1
            log("ERROR", "cycle failed", err=str(e), err_streak=err_streak)
            try:
                write_health(r, "fail", f"exception:{type(e).__name__}",
                             source_state=STATE_REDIS_ERROR,
                             cycle=cycle, error=str(e), err_streak=err_streak)
            except Exception:
                pass

        elapsed = time.time() - t0
        sleep_for = max(0.1, LOOP_SEC - elapsed)
        slept = 0.0
        while slept < sleep_for and not _shutdown:
            time.sleep(min(0.5, sleep_for - slept))
            slept += 0.5

    log("INFO", "shutdown", cycles=cycle, last_err_streak=err_streak)
    try:
        write_health(r, "stopped", "graceful_shutdown",
                     source_state="STOPPED", cycle=cycle)
    except Exception:
        pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
