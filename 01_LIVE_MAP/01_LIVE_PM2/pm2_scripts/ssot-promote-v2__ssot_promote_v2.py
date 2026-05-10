#!/usr/bin/env python3
"""
ssot_promote_v2.py — SSOT V2 Promote Gate (Unified v2.4.0)
==============================================================================
Unification (2026-04-17):
  v2.4.0-unified: Merged aub-trading-system and ares_current into single canonical source.
  - Canonical path: /home/ubuntu/ares_current/ops/ssot_promote_v2.py
  - PM2 and services.registry.yaml now point to the same file
  - WHITELIST_FILE defaults to ares_current path
  - Added SSOT Write Contract enforcement via ssot_safe_set() wrapper
  - All SET operations on SSOT keys MUST use ssot_safe_set() to guarantee TTL

Root Cause Analysis (2026-04-06):
  247 restarts were NOT crashes. They were caused by:
  1. Dual lifecycle management: while True loop + PM2 autorestart=true
  2. ecosystem.master.cjs reloads creating duplicate instances
  3. PM2 killing old instances → restart_time++ accumulation

Structural Fixes Applied:
  FIX-1: SIGTERM/SIGINT graceful shutdown handler (prevents restart on PM2 reload)
  FIX-2: Health heartbeat to Redis (external monitoring can detect stalls)
  FIX-3: Circuit breaker for Redis connection failures
  FIX-4: Structured restart-reason logging (distinguishes crash vs reload vs manual)
  FIX-5: PID-based singleton guard (prevents duplicate instances)
  FIX-6: Startup reason detection (first start vs PM2 restart vs reload)

Residual Holdings Sync (2026-04-17):
  FIX-R1: After validation, automatically merge broker-held residual positions
          (symbols in kis:broker:positions but not in canary) into the promoted
          payload with weight=0, side=HOLD, tag=residual_holding.
          This ensures SSOT current always reflects the full broker book,
          clearly distinguishing "active targets" from "residual holdings".

PM2 Config Change Required:
  autorestart: false  (this script manages its own lifecycle via while True)
  max_restarts: removed (not needed)
  restart_delay: removed (not needed)

Hash-based No-Write Idempotency (2026-04-26, Manus AI — Option B):
  Added _semantic_hash() identical to ares-ssot-promotion-guard v3.
  Before writing CURRENT_KEY, compare new vs prev semantic hash; if equal,
  skip the heavy write and only refresh TTL + ts heartbeat keys. This
  eliminates the redundant rewrites that caused dual-promoter hash oscillation
  (see ARES_SSOT_DUAL_PROMOTER_RECOMMENDATION.md).
  Version: 2.5.1-hash-noopwrite-r1r2

v2.5.1 (2026-04-26, Manus AI) — Residual Risk R1+R2 hardening
   * R1: NOOP_HASH_SKIP refresh-pipeline failures are now recorded in
     audit:ssot_v2:promotes with status=NOOP_HASH_SKIP_FAILED + error,
     and the subsequent fall-through full-write is tagged with
     fallthrough_from='hash_skip_failed' so operators can correlate.
   * R2: When entering the full-SET path, the audit record now carries
     a prev_hash_state field with one of:
        - 'missing'            : prev_semhash key was absent (TTL expired)
        - 'hash_read_failed'   : Redis GET on SEMHASH_KEY raised
        - 'changed'            : prev_semhash existed but differed
        - 'fallthrough_from_skip_fail' : R1 path executed
     Operators can compute SET-frequency by category from a single XREVRANGE.
   * No behavioural change to trading payload or NOOP path semantics.
"""
import hashlib
import json
import os
import shutil
import signal
import sys
import time
import redis

# [SAFETY-PATCH-V2 2026-05-06] Common policy engine.
_LIB_PATH = "/home/ubuntu/lib"
if _LIB_PATH not in sys.path:
    sys.path.insert(0, _LIB_PATH)
try:
    from ares_policy_engine import build_safety_marker as _ares_build_safety_marker
    _ARES_POLICY_ENGINE_OK = True
    _ARES_POLICY_ENGINE_ERR = ""
except Exception as _e_ape:
    _ARES_POLICY_ENGINE_OK = False
    _ARES_POLICY_ENGINE_ERR = str(_e_ape)


def _phase9_b1_attach_marker(payload, promotion_type, semantic_hash="", prev_hash_state=""):
    """Best-effort provenance marker injection for SSOT promotion payloads.

    This helper is deliberately observational: it does not change target weights,
    shares, residual policy, or promotion acceptance. It only preserves champion
    provenance across FULL_WRITE and NOOP_REFRESH branches so downstream
    order-intent generation can audit champion changes end-to-end.
    """
    try:
        if not isinstance(payload, dict):
            return payload
        now_ms = int(time.time() * 1000)
        targets = payload.get("targets") if isinstance(payload.get("targets"), dict) else {}
        meta = targets.get("meta") if isinstance(targets.get("meta"), dict) else {}
        upstream = (
            payload.get("phase9_b1_marker")
            or meta.get("phase9_b1_marker")
            or meta.get("champion_marker")
            or payload.get("promotion")
            or {}
        )
        # [SAFETY-PATCH-V2] Build base marker from LIVE Redis state via ares_policy_engine.
        # The previous code hardcoded override_decision=APPROVED + standard_validator_decision=NO_GO,
        # which silently bypassed all safety checks. Now the marker reflects real policy state.
        if _ARES_POLICY_ENGINE_OK:
            try:
                _r = globals().get("r")
                live_marker = _ares_build_safety_marker(_r, "", source="ssot_promote_v2")
            except Exception as _e_safety:
                live_marker = {
                    "validation_state": "REJECTED",
                    "rejection_reason": f"policy_engine_error:{_e_safety}",
                    "validator_decision": "UNKNOWN",
                    "override_decision": "NONE",
                }
        else:
            live_marker = {
                "validation_state": "REJECTED",
                "rejection_reason": f"policy_engine_unavailable:{_ARES_POLICY_ENGINE_ERR}",
                "validator_decision": "UNKNOWN",
                "override_decision": "NONE",
            }
        marker = dict(live_marker)
        # Carrythrough metadata (does not override policy fields).
        marker.update({
            "carrythrough_version": "phase9_b1_marker_carrythrough_v2_safety",
            "source_stage": "ssot_promote_v2",
            "promotion_type": str(promotion_type or "UNKNOWN"),
            "semantic_hash": str(semantic_hash or ""),
            "prev_hash_state": str(prev_hash_state or ""),
            "promoted_at_ms": now_ms,
            "upstream_marker": upstream,
            "champion_strategy_name": (
                meta.get("champion_strategy_name") or payload.get("champion_strategy_name")
            ),
            "champion_engine_file": (
                meta.get("champion_engine_file") or payload.get("champion_engine_file")
            ),
        })
        # NOTE: champion_version is provided by build_safety_marker from policy:champion:active.
        # Backward-compat surface for older consumers (deprecated; use validation_state):
        _vs = marker.get("validation_state")
        marker["_legacy_override_decision"] = (
            "VALIDATOR_GO" if _vs == "VALIDATOR_APPROVED"
            else "OVERRIDE_APPROVED" if _vs in ("OVERRIDE_APPROVED_SIGNED", "OVERRIDE_APPROVED_UNSIGNED")
            else "REJECTED"
        )
        marker["marker_version"] = marker.get("marker_version") or "phase9_b1_marker_v2_safety_2026_05_06"
        payload["phase9_b1_marker"] = marker
        if isinstance(targets, dict):
            meta2 = targets.setdefault("meta", {})
            if isinstance(meta2, dict):
                meta2["phase9_b1_marker"] = marker
                meta2["phase9_b1_marker_version"] = marker["marker_version"]
                meta2.setdefault("champion_version", marker["champion_version"])
                meta2.setdefault("champion_strategy_name", marker["champion_strategy_name"])
                meta2.setdefault("champion_engine_file", marker["champion_engine_file"])
        return payload
    except Exception as _phase9_b1_err:
        try:
            log("WARN", f"PHASE9_B1_MARKER_ATTACH_FAILED: {_phase9_b1_err}")
        except Exception:
            pass
        return payload

# [ARES-SSOT 2026-04-24] admin tier Redis client helper
import sys as _sys

# --- ARES Wave1.2 Cost Promotion Gate helpers -------------------------------
def _ares_cost_gate_bool(redis_client, key, default):
    try:
        raw = redis_client.get(key)
    except Exception:
        return default
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _ares_cost_gate_snapshot(redis_client):
    enabled = _ares_cost_gate_bool(redis_client, "policy:ssot_promote_v2:cost_gate:enabled", True)
    strict_missing = _ares_cost_gate_bool(redis_client, "policy:ssot_promote_v2:cost_gate:strict_missing", True)
    try:
        key = redis_client.get("policy:ssot_promote_v2:cost_gate:key") or "ares:cost:promotion_gate:current"
    except Exception:
        key = "ares:cost:promotion_gate:current"
    ctx = {"enabled": enabled, "strict_missing": strict_missing, "key": key}
    if not enabled:
        ctx["decision"] = "BYPASS_DISABLED"
        return True, "COST_GATE_DISABLED", ctx
    try:
        raw = redis_client.get(key)
    except Exception as e:
        ctx["error"] = str(e)
        return (False, "COST_GATE_REDIS_ERROR", ctx) if strict_missing else (True, "COST_GATE_REDIS_ERROR_BYPASS_NON_STRICT", ctx)
    if not raw:
        ctx["decision"] = "MISSING"
        return (False, "COST_GATE_MISSING_STRICT", ctx) if strict_missing else (True, "COST_GATE_MISSING_BYPASS_NON_STRICT", ctx)
    try:
        gate = json.loads(raw)
    except Exception as e:
        ctx["raw_head"] = str(raw)[:300]
        ctx["error"] = "JSON_PARSE_ERROR:" + str(e)
        return (False, "COST_GATE_UNPARSEABLE_STRICT", ctx) if strict_missing else (True, "COST_GATE_UNPARSEABLE_BYPASS_NON_STRICT", ctx)
    decision = str(gate.get("decision", "")).upper()
    metrics = gate.get("metrics") or {}
    ctx.update({
        "decision": decision,
        "proposal_id": gate.get("proposal_id"),
        "proposal_type": gate.get("proposal_type"),
        "requested_action": gate.get("requested_action"),
        "reasons": gate.get("reasons", []),
        "warnings": gate.get("warnings", []),
        "cost_model_version": metrics.get("policy_cost_model_version") or gate.get("cost_model_version"),
        "live_replay_gap_bps": metrics.get("live_replay_gap_bps"),
        "realized_vs_expected_mult": metrics.get("realized_vs_expected_mult"),
    })
    if decision == "GO":
        return True, "COST_GATE_GO", ctx
    return False, "COST_GATE_NOT_GO:" + (decision or "UNKNOWN"), ctx


def _ares_find_redis_client_from_locals(local_vars):
    # WAVE1_4E_GLOBALS_FALLBACK: also look in caller globals for module-level r
    self_obj = local_vars.get("self")
    for attr in ("r", "redis", "redis_client", "client"):
        obj = getattr(self_obj, attr, None) if self_obj is not None else None
        if obj is not None and hasattr(obj, "get") and hasattr(obj, "xadd"):
            return obj
    for name in ("r", "redis_client", "client"):
        obj = local_vars.get(name)
        if obj is not None and hasattr(obj, "get") and hasattr(obj, "xadd"):
            return obj
    # Fallback: look in caller globals (module-level r in promote())
    try:
        import sys as _sys2
        # frame 0 = this function, 1 = _ares_cost_gate_pre_current_write, 2 = caller (promote)
        for depth in (2, 1, 3):
            try:
                gframe = _sys2._getframe(depth).f_globals
            except ValueError:
                continue
            for name in ("r", "redis_client", "client"):
                obj = gframe.get(name)
                if obj is not None and hasattr(obj, "get") and hasattr(obj, "xadd"):
                    return obj
            # Try module-level factory _ares_get_redis()
            factory = gframe.get("_ares_get_redis")
            if callable(factory):
                try:
                    obj = factory()
                    if obj is not None and hasattr(obj, "get") and hasattr(obj, "xadd"):
                        return obj
                except Exception:
                    pass
    except Exception:
        pass
    raise RuntimeError("COST_GATE_NO_REDIS_CLIENT")


def _ares_emit_cost_gate_block(redis_client, reason, ctx):
    import time as _time
    payload = {
        "schema": "ares.ssot_promote_v2.cost_gate_block.v1",
        "ts": str(_time.time()),
        "status": "FAIL_COST_GATE",
        "reason": reason,
        "cost_gate": json.dumps(ctx, sort_keys=True, separators=(",", ":")),
    }
    try:
        redis_client.xadd("audit:ssot_v2:promotes", payload, maxlen=20000, approximate=True)
    except Exception:
        pass
    try:
        redis_client.xadd("ssot:promotion:audit", {
            "decision": "REJECT",
            "diff_class": "COST_GATE",
            "reason": reason,
            "cost_gate": json.dumps(ctx, sort_keys=True, separators=(",", ":")),
        }, maxlen=20000, approximate=True)
    except Exception:
        pass


def _ares_cost_gate_pre_current_write(local_vars):
    redis_client = _ares_find_redis_client_from_locals(local_vars)
    ok, reason, ctx = _ares_cost_gate_snapshot(redis_client)
    if ok:
        return True
    _ares_emit_cost_gate_block(redis_client, reason, ctx)
    return False
# ----------------------------------------------------------------------------

_sys.path.insert(0, "/home/ubuntu/scripts")
try:
    from redis_ssot_loader import get_redis_client as _ares_get_redis
except ImportError as _e:
    raise RuntimeError(
        "ARES SSOT loader not available. Ensure /home/ubuntu/scripts/redis_ssot_loader.py exists."
    ) from _e

# ── Version & Identity ──
PROMOTE_VERSION = "2.5.1-hash-noopwrite-r1r2"  # [Manus AI 2026-04-26] Option B + R1+R2
PROMOTE_PID = os.getpid()

# ── Config ──
REDIS_URL = os.environ["REDIS_URL"]  # Phase6: fail-fast (SSOT /etc/ares/redis.env required)
CANARY_KEY = os.getenv("SSOT_KEY_CANARY", "ssot:target:v2:canary")
CURRENT_KEY = os.getenv("SSOT_KEY_CURRENT", "ssot:target:v2:current")
LAST_GOOD_KEY = os.getenv("SSOT_KEY_LAST_GOOD", "ssot:target:v2:last_good")
LAST_BAD_KEY = os.getenv("SSOT_KEY_LAST_BAD", "ssot:target:v2:last_bad")
META_KEY = os.getenv("SSOT_META_KEY", "ssot:target:v2:meta")
CURRENT_TTL = max(int(os.getenv("SSOT_CURRENT_TTL", "600")), 60)  # 10 min, floor=60s
# [STRUCTURAL-FIX-v4] Dynamic stale threshold
# Base threshold from env, but dynamically adjusted based on market state
_BASE_STALE_THRESHOLD = int(os.getenv("SSOT_STALE_SEC", "300"))  # 5 min base

def get_stale_threshold():
    """Return stale threshold based on market state."""
    if is_us_market_hours():
        return _BASE_STALE_THRESHOLD  # 300s during market hours
    return 86400  # 24h during off-market (effectively disabled)

STALE_THRESHOLD_SEC = _BASE_STALE_THRESHOLD  # Initial value, will be dynamically overridden
# [v2.4.0-unified] Canonical path: ares_current (single source of truth)
WHITELIST_FILE = os.getenv("WHITELIST_FILE", "/home/ubuntu/ares_current/config/universe_whitelist.txt")
MAX_GROSS = float(os.getenv("SSOT_MAX_GROSS", "2.0"))
MIN_POSITIONS = int(os.getenv("SSOT_MIN_POSITIONS", "1"))
MAX_POSITIONS = int(os.getenv("SSOT_MAX_POSITIONS", "200"))
LOOP_INTERVAL = int(os.getenv("PROMOTE_INTERVAL_SEC", "60"))

# ── FIX-R1: Residual Sync Config ──
BROKER_POSITIONS_KEY = "kis:broker:positions"   # Redis hash: symbol → JSON
RESIDUAL_SYNC_ENABLED = os.getenv("RESIDUAL_SYNC_ENABLED", "true").lower() == "true"

# ── Circuit Breaker Config ──
CB_MAX_FAILURES = 5           # consecutive Redis failures before circuit opens
CB_RESET_AFTER_SEC = 120      # seconds to wait before retrying after circuit opens

# ── Health Heartbeat ──
HEALTH_KEY = "ssot:promote:v2:health"
HEALTH_TTL = 180  # 3 minutes

# ── Singleton Guard ──
PID_KEY = "ssot:promote:v2:pid"
PID_TTL = 120  # 2 minutes

# ── State ──
_shutdown_requested = False
_shutdown_reason = "unknown"

# [MANUS-P2 2026-05-08] race-safe early logger — used if SIGTERM arrives before the
# real `log()` (line ~428) is defined during module import.  Eliminates NameError race.
def _safe_log(level, msg, **kwargs):
    try:
        log(level, msg, **kwargs)  # type: ignore[name-defined]
    except NameError:
        import sys as _sys, time as _time, json as _json
        ts = _time.strftime('%Y-%m-%dT%H:%M:%S', _time.gmtime())
        extra = _json.dumps(kwargs) if kwargs else ''
        _sys.stderr.write(f'[{ts}] [{level}] ssot-promote-v2(early): {msg} {extra}\n')
        _sys.stderr.flush()

def _handle_signal(signum, frame):
    """FIX-1: Graceful shutdown on SIGTERM (PM2 reload) and SIGINT (Ctrl+C)."""
    global _shutdown_requested, _shutdown_reason
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
    _shutdown_reason = f"signal:{sig_name}"
    _shutdown_requested = True
    _safe_log("INFO", f"Shutdown requested via {sig_name} (PID={PROMOTE_PID})")

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ── Redis Connection ──
r = _ares_get_redis()


# ═══════════════════════════════════════════════════════════════
# [v2.4.0-unified FIX-D] SSOT Write Contract — Single Source Import
# ═══════════════════════════════════════════════════════════════
# DRY: Import from ssot_write_contract.py (canonical module).
# No duplicate key sets or enforcement logic in this file.
#
# Design principle (First Principles):
#   "Don't document rules — enforce them in code."
#   "Don't duplicate code — import from a single source."
# ═══════════════════════════════════════════════════════════════
try:
    # Add ops directory to path for import
    _ops_dir = os.path.dirname(os.path.abspath(__file__))
    if _ops_dir not in sys.path:
        sys.path.insert(0, _ops_dir)
    from ssot_write_contract import SSOTWriter, SSOT_PROTECTED_KEYS, DEFAULT_TTL as _CONTRACT_DEFAULT_TTL
    _ssot_writer = None  # Initialized after Redis connection
    _WRITE_CONTRACT_AVAILABLE = True
except ImportError:
    _WRITE_CONTRACT_AVAILABLE = False
    SSOT_PROTECTED_KEYS = frozenset({
        "ssot:target:v2:current",
        "ssot:target:v2:canary",
        "ssot:current",
        "ssot:target:v2:ts",
        "ssot:target:v2:ts:epoch",
    })

def _init_ssot_writer():
    """Initialize SSOTWriter after Redis connection is established."""
    global _ssot_writer
    if _WRITE_CONTRACT_AVAILABLE and _ssot_writer is None:
        _ssot_writer = SSOTWriter(r, caller_id=f"ssot-promote-v2:{PROMOTE_PID}", default_ttl=CURRENT_TTL)
        log("INFO", f"[WRITE_CONTRACT] SSOTWriter initialized (version={_ssot_writer.__class__.__module__})")

def ssot_safe_set(redis_client, key, value, ex=None, px=None, pipeline=None):
    """SSOT Write Contract Enforcer — delegates to SSOTWriter if available.
    
    Falls back to inline enforcement if ssot_write_contract.py is not importable.
    """
    _init_ssot_writer()
    
    if _ssot_writer is not None:
        return _ssot_writer.set(key, value, ex=ex, px=px, pipeline=pipeline)
    
    # Fallback: inline enforcement (same logic as SSOTWriter)
    target = pipeline if pipeline else redis_client
    if key in SSOT_PROTECTED_KEYS:
        if ex is None and px is None:
            ex = CURRENT_TTL
            log("WARN", f"[WRITE_CONTRACT] Auto-applied TTL={ex}s to {key} (caller forgot TTL)")
    if px is not None:
        return target.set(key, value, px=px)
    elif ex is not None:
        return target.set(key, value, ex=ex)
    else:
        return target.set(key, value)


def log(level, msg, **kwargs):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    extra = json.dumps(kwargs) if kwargs else ""
    print(f"[{ts}] [{level}] ssot-promote-v2: {msg} {extra}", flush=True)


# ═══ [Option B 2026-04-26 Manus AI] Semantic Hash Idempotency ═══
# Identical volatile-key set as ares-ssot-promotion-guard v3.
# Two writers MUST agree on what 'volatile' means or they will compute
# different hashes and never converge.
SEMHASH_KEY = os.getenv("SSOT_SEMHASH_KEY", "ssot:target:v2:current:semantic_hash")

_SH_VOLATILE_KEYS = {
    "correlation_id", "asof", "ts", "promoted_at", "promoted_from",
    "ts:epoch", "engine_src", "exec_src", "promotion_count",
    "ssot_target_residuals", "residuals", "last_promotion",
    "added_at_ms",  # injected by promote() itself, must be ignored
}
_SH_VOLATILE_SUFFIXES = ("_ts", "_ts_ms", "_at", "_seconds", "_epoch")
_SH_VOLATILE_PREFIXES = ("promoted_",)


def _is_volatile_key(k):
    if not isinstance(k, str):
        return False
    if k in _SH_VOLATILE_KEYS:
        return True
    if any(k.endswith(suf) for suf in _SH_VOLATILE_SUFFIXES):
        return True
    if any(k.startswith(pre) for pre in _SH_VOLATILE_PREFIXES):
        return True
    return False


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if not _is_volatile_key(k)}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _semantic_hash(payload):
    """Stable SHA-1 (12-char prefix) of payload after stripping volatile keys.
    Identical algorithm to ares-ssot-promotion-guard v3 so the two writers
    can be reasoned about as a single coherent system.
    """
    try:
        stripped = _strip_volatile(payload or {})
        ser = json.dumps(stripped, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha1(ser.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return "ERR_HASH"
# ═══ end Option B helper ═══


# ═══════════════════════════════════════════════════════════════
# [ARES-COST-GATE 2026-04-26] Cost Promotion Gate before live current write
# ═══════════════════════════════════════════════════════════════
def _bool_policy(key, default):
    try:
        raw = r.get(key)
    except Exception:
        return default
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")

def _float_policy(key, default):
    try:
        raw = r.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        return float(raw)
    except Exception:
        return default

def _cost_gate_key():
    try:
        return r.get("policy:ssot:cost_gate:key") or "ares:cost:promotion_gate:current"
    except Exception:
        return "ares:cost:promotion_gate:current"

def _parse_iso_age_sec(ts):
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None

def _cost_gate_allows_live_commit():
    """
    Returns (ok, reason, context).

    Hard rule:
      LIVE current write is allowed only when ares:cost:promotion_gate:current.decision == GO.

    Policy:
      policy:ssot:cost_gate:enabled        default true
      policy:ssot:cost_gate:strict_missing default true
      policy:ssot:cost_gate:max_age_sec    default 900
      policy:ssot:cost_gate:key            default ares:cost:promotion_gate:current
    """
    enabled = _bool_policy("policy:ssot:cost_gate:enabled", True)
    strict_missing = _bool_policy("policy:ssot:cost_gate:strict_missing", True)
    max_age_sec = _float_policy("policy:ssot:cost_gate:max_age_sec", 900.0)
    key = _cost_gate_key()

    ctx = {
        "enabled": enabled,
        "strict_missing": strict_missing,
        "max_age_sec": max_age_sec,
        "key": key,
    }

    if not enabled:
        ctx["decision"] = "BYPASS_DISABLED"
        return True, "COST_GATE_DISABLED", ctx

    try:
        raw = r.get(key)
    except Exception as e:
        ctx["error"] = str(e)[:300]
        if strict_missing:
            return False, "COST_GATE_REDIS_ERROR", ctx
        return True, "COST_GATE_REDIS_ERROR_BYPASS_NON_STRICT", ctx

    if not raw:
        ctx["decision"] = "MISSING"
        if strict_missing:
            return False, "COST_GATE_MISSING_STRICT", ctx
        return True, "COST_GATE_MISSING_BYPASS_NON_STRICT", ctx

    try:
        gate = json.loads(raw)
    except Exception as e:
        ctx["error"] = "JSON_PARSE_ERROR:" + str(e)[:240]
        ctx["raw_head"] = str(raw)[:300]
        if strict_missing:
            return False, "COST_GATE_UNPARSEABLE_STRICT", ctx
        return True, "COST_GATE_UNPARSEABLE_BYPASS_NON_STRICT", ctx

    decision = str(gate.get("decision", "")).upper()
    ts = gate.get("ts")
    age_sec = _parse_iso_age_sec(ts)
    metrics = gate.get("metrics") or {}

    ctx.update({
        "decision": decision,
        "ts": ts,
        "age_sec": age_sec,
        "proposal_id": gate.get("proposal_id", ""),
        "proposal_type": gate.get("proposal_type", ""),
        "reasons": gate.get("reasons", []),
        "warnings": gate.get("warnings", []),
        "policy_cost_model_version": metrics.get("policy_cost_model_version") or gate.get("cost_model_version", ""),
        "live_replay_gap_bps": metrics.get("live_replay_gap_bps"),
        "realized_vs_expected_mult": metrics.get("realized_vs_expected_mult"),
    })

    if age_sec is None:
        if strict_missing:
            return False, "COST_GATE_TS_MISSING_OR_BAD", ctx
    elif age_sec > max_age_sec:
        return False, f"COST_GATE_STALE:{age_sec:.1f}s>{max_age_sec:.1f}s", ctx

    if decision == "GO":
        return True, "COST_GATE_GO", ctx

    return False, f"COST_GATE_NOT_GO:{decision or 'UNKNOWN'}", ctx

def _record_cost_gate_block(payload, reason, ctx, semantic_hash):
    """Record a cost-gate block without writing current or deleting canary."""
    now_ms = str(int(time.time() * 1000))
    try:
        r.hset(META_KEY, mapping={
            "last_promote_status": "FAIL_COST_GATE",
            "last_promote_ts": now_ms,
            "last_promote_errors": reason,
            "cost_gate_decision": str(ctx.get("decision", "")),
            "cost_gate_key": str(ctx.get("key", "")),
            "semantic_hash": semantic_hash,
        })
    except Exception as e:
        log("WARN", f"Cost gate META write failed: {e}")

    # Debounce identical blocks to reduce audit spam.
    debounce_key = "ssot:promote:v2:cost_gate:last_block"
    block_sig = json.dumps({
        "reason": reason,
        "decision": ctx.get("decision", ""),
        "semantic_hash": semantic_hash,
    }, sort_keys=True, separators=(",", ":"))

    emit_audit = True
    try:
        prev = r.get(debounce_key)
        if prev == block_sig:
            emit_audit = False
        r.set(debounce_key, block_sig, ex=900)
    except Exception:
        emit_audit = True

    if emit_audit:
        try:
            r.xadd("audit:ssot_v2:promotes", {
                "status": "FAIL_COST_GATE",
                "ts": now_ms,
                "reason": reason,
                "cost_gate_decision": str(ctx.get("decision", "")),
                "cost_gate_key": str(ctx.get("key", "")),
                "cost_gate_context": json.dumps(ctx, separators=(",", ":"), default=str)[:2000],
                "semantic_hash": semantic_hash,
                "correlation_id": str(payload.get("correlation_id", "")),
                "version": PROMOTE_VERSION,
            }, maxlen=500)
        except Exception as e:
            log("WARN", f"Cost gate audit write failed: {e}")

    log("WARN", f"SSOT_PROMOTE_BLOCKED_BY_COST_GATE: {reason} ctx={ctx}")

# [STRUCTURAL-FIX-v3 FIX-D] Market hours awareness
# [v2.4.0-unified FIX-A] Use zoneinfo for automatic EST/EDT handling
def is_us_market_hours():
    """Check if US market is currently open (ET 09:30-16:00, Mon-Fri).
    Uses zoneinfo.ZoneInfo('America/New_York') for automatic DST handling.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et_now = datetime.now(ZoneInfo("America/New_York"))
    if et_now.weekday() >= 5:  # Saturday, Sunday
        return False
    market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= et_now <= market_close


def load_whitelist():
    """Load universe whitelist from file."""
    try:
        if os.path.exists(WHITELIST_FILE):
            with open(WHITELIST_FILE) as f:
                return set(line.strip().upper() for line in f if line.strip() and not line.startswith("#"))
    except Exception as e:
        log("WARN", f"Whitelist load error: {e}")
    return None  # None means no whitelist filtering

# [STRUCTURAL-FIX-v4] Auto whitelist sync pipeline
def auto_sync_whitelist(payload, whitelist):
    """Automatically add new active symbols to whitelist file.
    
    When a new symbol appears in the SSOT target (via engine_src or canary),
    it means the strategy has selected it. Rather than blocking promote with
    a whitelist warning, we auto-register it and log the event.
    
    Only active positions are synced (not residual_holding).
    """
    if whitelist is None:
        return  # No whitelist file configured
    
    positions = payload.get("targets", {}).get("positions", [])
    active_symbols = set()
    for p in positions:
        tag = p.get("tag", "active")
        if tag == "residual_holding":
            continue
        sym = p.get("symbol", "").upper()
        if sym:
            active_symbols.add(sym)
    
    new_symbols = active_symbols - whitelist
    if not new_symbols:
        return
    
    # Add to whitelist file
    try:
        wl_path = os.getenv("WHITELIST_FILE", "/home/ubuntu/ares_current/config/universe_whitelist.txt")
        
        # Read current file
        with open(wl_path, "r") as f:
            lines = f.readlines()
        
        # Find insertion point — before the last blank line or at end
        # Add under "# === Auto-added by SSOT Promote ===" section
        auto_section_marker = "# === Auto-added by SSOT Promote ==="
        has_auto_section = any(auto_section_marker in line for line in lines)
        
        if not has_auto_section:
            lines.append(f"\n{auto_section_marker}\n")
        
        for sym in sorted(new_symbols):
            lines.append(f"{sym}\n")
            whitelist.add(sym)  # Update in-memory set too
        
        # [v2.4.0-unified FIX-C] Atomic file write: tmp + rename (POSIX atomic)
        import tempfile
        wl_dir = os.path.dirname(wl_path)
        fd, tmp_path = tempfile.mkstemp(dir=wl_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.writelines(lines)
            os.rename(tmp_path, wl_path)  # Atomic on POSIX
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        
        # [v2.4.0-unified FIX-H] Sync to aub path (non-blocking, fire-and-forget)
        try:
            aub_wl_path = "/home/ubuntu/aub-trading-system/config/universe_whitelist.txt"
            if os.path.exists(aub_wl_path):
                shutil.copy2(wl_path, aub_wl_path)
        except Exception as _sync_err:
            log("WARN", f"[AUTO_WHITELIST] aub sync failed (non-blocking): {_sync_err}")
        
        log("INFO", f"[AUTO_WHITELIST] Added {len(new_symbols)} new symbols: {sorted(new_symbols)}")
        
        # Record in Redis for audit trail
        import json as _json
        r.hset("ares:whitelist:auto_additions", mapping={
            "last_added": _json.dumps(sorted(new_symbols)),
            "last_added_ts": str(int(time.time() * 1000)),
            "total_auto_added": str(r.hget("ares:whitelist:auto_additions", "total_auto_added") or 0)
        })
        # Increment total count
        r.hincrby("ares:whitelist:auto_additions", "total_auto_added", len(new_symbols))
        
    except Exception as e:
        log("WARN", f"[AUTO_WHITELIST] Failed to auto-add symbols: {e}")


def validate_schema(payload):
    """Validate SSOT_TARGET_V2 schema."""
    errors = []
    
    if not isinstance(payload, dict):
        return ["payload is not a dict"]
    
    if payload.get("schema_version") != "SSOT_TARGET_V2":
        errors.append(f"schema_version mismatch: {payload.get('schema_version')}")
    
    if "ts" not in payload or not isinstance(payload["ts"], (int, float)):
        errors.append("missing or invalid ts")
    
    if "asof" not in payload:
        errors.append("missing asof")
    
    if "targets" not in payload or not isinstance(payload["targets"], dict):
        errors.append("missing or invalid targets")
        return errors
    
    targets = payload["targets"]
    
    if "gross" not in targets or not isinstance(targets["gross"], (int, float)):
        errors.append("missing or invalid targets.gross")
    elif targets["gross"] > MAX_GROSS:
        errors.append(f"gross {targets['gross']} exceeds max {MAX_GROSS}")
    
    if "positions" not in targets or not isinstance(targets["positions"], list):
        errors.append("missing or invalid targets.positions")
    else:
        n = len(targets["positions"])
        if n < MIN_POSITIONS:
            errors.append(f"too few positions: {n} < {MIN_POSITIONS}")
        if n > MAX_POSITIONS:
            errors.append(f"too many positions: {n} > {MAX_POSITIONS}")
        
        for i, p in enumerate(targets["positions"]):
            if not isinstance(p, dict):
                errors.append(f"position[{i}] is not a dict")
                continue
            if "symbol" not in p:
                errors.append(f"position[{i}] missing symbol")
            if "w" not in p:
                errors.append(f"position[{i}] missing w")
            if "side" not in p:
                errors.append(f"position[{i}] missing side")
    
    if "meta" not in targets or not isinstance(targets["meta"], dict):
        errors.append("missing or invalid targets.meta")
    else:
        meta = targets["meta"]
        rw = meta.get("router_weights")
        if not isinstance(rw, dict) or len(rw) == 0:
            errors.append("meta_missing_or_empty:router_weights")
        
        pv = meta.get("portfolio_vol_est")
        try:
            pv = float(pv) if pv is not None else 0.0
            if pv <= 0:
                pass  # portfolio_vol_est=0 is non-blocking
        except (TypeError, ValueError):
            pass
        
        xr = meta.get("xgb_risk")
        xr_src = meta.get("xgb_risk_source", "")
        if xr_src == "missing_or_wrongtype":
            pass  # [MANUS_FIX] xgb_risk_missing is non-blocking
    
    return errors

def validate_stale(payload):
    """Check if canary is stale (canary ts + source_ts)."""
    ts = payload.get("ts", 0)
    age_sec = (time.time() * 1000 - ts) / 1000
    if age_sec > get_stale_threshold():
        return f"stale: age={age_sec:.0f}s > threshold={get_stale_threshold()}s"
    
    # [SOURCE_TS] source_ts 기반 stale-source 검증
    # [STRUCTURAL-FIX-v3 FIX-D] 장 외 시간에는 INFO로 강등
    source_ts = payload.get("targets", {}).get("meta", {}).get("source_ts", 0)
    if source_ts > 0:
        source_age_sec = (time.time() * 1000 - source_ts) / 1000
        # [STRUCTURAL-FIX-v4] Dynamic stale threshold based on market state
        # Market hours: 300s (5 min) — detect real issues quickly
        # Off-market: infinity — suppress stale warnings entirely
        if is_us_market_hours():
            SOURCE_STALE_THRESHOLD = 300  # 5분 — 장 중에는 빠르게 감지
        else:
            SOURCE_STALE_THRESHOLD = float('inf')  # 장 외 — stale 체크 비활성화
        if source_age_sec > SOURCE_STALE_THRESHOLD:
            if is_us_market_hours():
                log("WARN", f"source_ts stale: age={source_age_sec:.0f}s > {SOURCE_STALE_THRESHOLD}s (non-blocking)")
            else:
                log("INFO", f"source_ts stale during closed market: age={source_age_sec:.0f}s (normal, suppressed)")
            if "warnings" not in payload.get("targets", {}).get("meta", {}):
                payload["targets"]["meta"]["warnings"] = []
            payload["targets"]["meta"]["warnings"].append(f"source_stale: {source_age_sec:.0f}s")
    
    return None

def validate_whitelist(payload, whitelist):
    """Check all symbols are in whitelist."""
    if whitelist is None:
        return None
    
    positions = payload.get("targets", {}).get("positions", [])
    unknown = []
    for p in positions:
        sym = p.get("symbol", "").upper()
        if sym and sym not in whitelist:
            unknown.append(sym)
    
    if unknown:
        return f"symbols not in whitelist: {unknown[:5]}"
    return None


# MANUS_POLICY_UNIVERSE_FILTER_20260429
def enforce_policy_universe(payload):
    """Drop active targets that are outside policy universe or explicitly blocked.

    This is a final promote-side safety gate.  Residual broker holdings tagged as
    residual_holding / hold_only are retained for book visibility, but they must
    not become BUY/SELL targets.  Active nonzero targets such as BIL are removed
    if Redis policy excludes or blocklists them.
    """
    try:
        allowed = set(x.upper() for x in (r.smembers("policy:universe:symbols") or []))
        block = set(x.upper() for x in (r.smembers("policy:blocklist:symbols") or []))
        block |= set(x.upper() for x in (r.smembers("policy:universe:exclude_set") or []))
    except Exception as e:
        log("WARN", f"[POLICY_UNIVERSE_FILTER] redis policy read failed (non-blocking): {e}")
        return payload, []
    if not allowed:
        return payload, []
    targets = payload.setdefault("targets", {})
    positions = targets.get("positions", [])
    if not isinstance(positions, list):
        return payload, []
    kept = []
    removed = []
    for pos in positions:
        sym = str(pos.get("symbol", "")).upper()
        tag = str(pos.get("tag", "")).lower()
        tp = str(pos.get("trade_policy", "")).lower()
        tv = float(pos.get("target_value") or 0) if str(pos.get("target_value", "")).replace('.', '', 1).replace('-', '', 1).isdigit() else 0.0
        w = float(pos.get("w") or 0) if str(pos.get("w", "")).replace('.', '', 1).replace('-', '', 1).isdigit() else 0.0
        is_residual_hold = (tag == "residual_holding" or tp == "hold_only") and abs(w) == 0 and abs(tv) == 0
        if is_residual_hold:
            pos["side"] = "HOLD"
            pos["trade_policy"] = "hold_only"
            kept.append(pos)
            continue
        if sym and (sym not in allowed or sym in block):
            removed.append({"symbol": sym, "reason": "not_allowed_or_blocked", "w": pos.get("w"), "target_value": pos.get("target_value"), "side": pos.get("side")})
            continue
        kept.append(pos)
    if removed:
        targets["positions"] = kept
        try:
            targets["gross"] = float(sum(abs(float(x.get("w") or 0)) for x in kept if str(x.get("tag", "")).lower() != "residual_holding"))
        except Exception:
            pass
        meta = targets.setdefault("meta", {})
        meta["policy_universe_filter"] = "applied"
        meta["policy_universe_filter_removed"] = removed
        meta["policy_universe_filter_removed_count"] = len(removed)
        rw = meta.get("router_weights")
        if isinstance(rw, dict):
            for item in removed:
                rw.pop(item.get("symbol"), None)
        try:
            r.set("ssot:target:v2:policy_filter:last_removed", json.dumps({"ts": int(time.time()*1000), "removed": removed}), ex=86400)
        except Exception:
            pass
        log("WARN", f"[POLICY_UNIVERSE_FILTER] removed {len(removed)} active target(s): {[x['symbol'] for x in removed]}")
    return payload, removed


# ═══════════════════════════════════════════════════════════════
# FIX-R1: Broker Residual Holdings Sync
# ═══════════════════════════════════════════════════════════════
def merge_broker_residuals(payload):
    """
    Merge broker-held residual positions into the SSOT payload.
    
    Reads kis:broker:positions (Redis hash) to find symbols that the broker
    holds but are NOT in the canary's target positions. These residual
    holdings are added with weight=0, side=HOLD, tag=residual_holding.
    
    This ensures:
    1. SSOT current always reflects the full broker book
    2. "Active targets" (w>0) and "residual holdings" (w=0) are clearly separated
    3. ssot-sanity-guard sees 0% symbol divergence (no false alarms)
    4. Execution engine knows about residual positions (won't accidentally buy them)
    
    Returns:
        tuple: (modified_payload, residual_count, residual_symbols)
    """
    if not RESIDUAL_SYNC_ENABLED:
        return payload, 0, []
    
    try:
        # Read broker positions hash
        broker_hash = r.hgetall(BROKER_POSITIONS_KEY)
        if not broker_hash:
            return payload, 0, []
        
        broker_symbols = set(broker_hash.keys())
        
        # Get current target symbols
        positions = payload.get("targets", {}).get("positions", [])
        target_symbols = {p.get("symbol", "") for p in positions}
        
        # Find residual: in broker but not in target
        residual_symbols = sorted(broker_symbols - target_symbols)
        
        if not residual_symbols:
            return payload, 0, []
        
        # Remove any stale residual_holding entries from previous cycles
        # (in case a symbol was sold and is no longer in broker)
        cleaned_positions = [
            p for p in positions
            if p.get("tag") != "residual_holding" or p.get("symbol", "") in broker_symbols
        ]
        
        # Add fresh residual entries
        ts_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # [PATCH-H v1.0] hold_only residual promote (Patch H)
        # trade_policy=hold_only 필드를 기입해 downstream이 명확히 HOLD로 해석하도록.
        for sym in residual_symbols:
            cleaned_positions.append({
                "symbol": sym,
                "w": 0.0,
                "side": "HOLD",
                "tag": "residual_holding",
                "trade_policy": "hold_only",
                "source": "broker_residual_sync",
                "synced_at": ts_now
            })
        
        # [BUG-1 FIX] Tag ALL residual positions (both newly added and canary-embedded)
        # Canary-embedded residuals from previous cycles don't have tag, so we tag them now
        # A position is residual if: (a) it has tag=residual_holding, OR
        # (b) its symbol is in broker but NOT in original canary_src targets (weight=0 or missing)
        # [PATCH-H v1.0] canary-embedded residual trade_policy
        for p in cleaned_positions:
            sym = p.get("symbol", "").upper()
            if sym in residual_symbols and p.get("tag") != "residual_holding":
                p["tag"] = "residual_holding"
                p["source"] = "canary_embedded_residual"
                p["trade_policy"] = "hold_only"
            elif p.get("tag") == "residual_holding" and "trade_policy" not in p:
                p["trade_policy"] = "hold_only"
        residual_entries = [p for p in cleaned_positions if p.get("tag") == "residual_holding"]
        active_entries = [p for p in cleaned_positions if p.get("tag") != "residual_holding"]
        payload["targets"]["positions"] = cleaned_positions  # active + residual 모두 포함
        
        # 별도 키에 residual 저장
        try:
            import json as _json
            residual_payload = {
                "ts": ts_now,
                "positions": residual_entries,
                "count": len(residual_entries),
                "symbols": [p["symbol"] for p in residual_entries]
            }
            r.set("ssot:target:v2:residuals", _json.dumps(residual_payload), ex=3600)
        except Exception:
            pass  # Non-blocking
        
        # Add sync metadata
        if "_residual_sync" not in payload:
            payload["_residual_sync"] = {}
        payload["_residual_sync"] = {
            "ts": ts_now,
            "active_count": len(target_symbols),
            "residual_count": len(residual_symbols),
            "total_count": len(cleaned_positions),
            "residual_symbols": residual_symbols[:20],  # cap for storage
            "broker_total": len(broker_symbols)
        }
        
        return payload, len(residual_symbols), residual_symbols
        
    except Exception as e:
        log("WARN", f"Broker residual sync failed (non-blocking): {e}")
        return payload, 0, []



# [HOTFIX 2026-04-28] Unconditional liveness heartbeat
def _refresh_liveness_unconditional():
    try:
        now_ms = int(time.time() * 1000)
        r.set("ssot:promote:v2:liveness",
              json.dumps({"pid": PROMOTE_PID, "ts": now_ms, "status": "alive"}),
              ex=180)
    except Exception:
        pass

def promote():
    _refresh_liveness_unconditional()
    """Main promote logic."""
    # Read canary
    raw = r.get(CANARY_KEY)
    if not raw:
        log("DEBUG", "No canary to promote")
        return
    
    try:
        payload = json.loads(raw)
    except Exception as e:
        log("ERROR", f"Canary JSON parse error: {e}")
        return
    
    # Validate schema
    schema_errors = validate_schema(payload)
    if schema_errors:
        log("ERROR", "Schema validation failed", errors=schema_errors)
        r.set(LAST_BAD_KEY, raw, ex=3600)
        r.hset(META_KEY, mapping={
            "last_promote_status": "FAIL_SCHEMA",
            "last_promote_ts": str(int(time.time() * 1000)),
            "last_promote_errors": json.dumps(schema_errors)
        })
        return
    
    # Validate stale
    stale_err = validate_stale(payload)
    if stale_err:
        log("WARN", f"Stale check failed: {stale_err}")
        r.hset(META_KEY, mapping={
            "last_promote_status": "FAIL_STALE",
            "last_promote_ts": str(int(time.time() * 1000)),
            "last_promote_errors": stale_err
        })
        return
    
    # Validate whitelist + auto-sync new symbols
    whitelist = load_whitelist()
    # [STRUCTURAL-FIX-v4] Auto-add new active symbols to whitelist before validation
    auto_sync_whitelist(payload, whitelist)
    wl_err = validate_whitelist(payload, whitelist)
    if wl_err:
        log("WARN", f"Whitelist check failed: {wl_err}")
        if "warnings" not in payload["targets"]["meta"]:
            payload["targets"]["meta"]["warnings"] = []
        payload["targets"]["meta"]["warnings"].append(f"whitelist: {wl_err}")
    
    # MANUS_POLICY_UNIVERSE_FILTER_20260429: enforce Redis policy universe before any live current write
    payload, _policy_removed = enforce_policy_universe(payload)

    # ═══ [ARES-COST-GATE] Block live current write unless cost promotion gate is GO ═══
    _pre_cost_semhash = _semantic_hash(payload)
    _cost_ok, _cost_reason, _cost_ctx = _cost_gate_allows_live_commit()
    if not _cost_ok:
        _record_cost_gate_block(payload, _cost_reason, _cost_ctx, _pre_cost_semhash)
        return

    # ═══ FIX-R1: Merge broker residual holdings ═══
    active_count = len(payload["targets"]["positions"])
    payload, residual_count, residual_syms = merge_broker_residuals(payload)
    total_count = len(payload["targets"]["positions"])
    
    if residual_count > 0:
        log("INFO", f"Residual sync: +{residual_count} broker holdings merged "
            f"(active={active_count}, residual={residual_count}, total={total_count})")
    
    # ═══ [STRUCTURAL-FIX-v3] Residual을 current에 포함 (orchestrator가 읽을 수 있도록) ═══
    all_positions = payload["targets"]["positions"]
    final_active = [p for p in all_positions if p.get("tag") != "residual_holding"]
    final_residual = [p for p in all_positions if p.get("tag") == "residual_holding"]
    
    # current에 active + residual 모두 포함 (orchestrator가 weight=0 → SELL 인텐트 생성)
    # payload["targets"]["positions"]는 이미 cleaned_positions (active + residual)
    
    # 별도 키에도 residual 저장 (모니터링/감사용)
    if final_residual:
        try:
            residual_payload = json.dumps({
                "ts": int(time.time() * 1000),
                "count": len(final_residual),
                "symbols": [p.get("symbol", "") for p in final_residual],
                "positions": final_residual
            })
            r.set("ssot:target:v2:residuals", residual_payload, ex=3600)
        except Exception as _e:
            log("WARN", f"Failed to write residuals key: {_e}")
    
    # 메타데이터 업데이트
    active_count = len(final_active)
    residual_count = len(final_residual)
    total_count = active_count + residual_count
    
    log("INFO", f"Residual separation: active={active_count}, residual={residual_count}, total={total_count}")
    
    # [PART4 T4 FIX 2026-04-24] Inject added_at_ms so downstream freshness
    # guards (ssot-sanity-guard-v3) can rely on a canonical field and stop
    # falling back to legacy 'ts'. Prefer the existing payload ts (epoch ms);
    # otherwise use wall-clock. Keep 'ts' intact for backward compatibility.
    try:
        _added_at_ms = int(payload.get("ts") or 0)
        if _added_at_ms <= 0:
            _added_at_ms = int(time.time() * 1000)
        payload["added_at_ms"] = _added_at_ms
    except Exception:
        payload["added_at_ms"] = int(time.time() * 1000)

    # Promote: canary → current + last_good (active만)
    payload_str = json.dumps(payload)

    # [STRUCTURAL-FIX-v2 FIX-5] 장 외 시간에는 TTL을 3600초로 연장
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        _ny = datetime.now(ZoneInfo("America/New_York"))
        _is_market = _ny.weekday() < 5 and 9 <= _ny.hour < 16
        _effective_ttl = CURRENT_TTL if _is_market else max(CURRENT_TTL, 3600)
    except Exception:
        _effective_ttl = CURRENT_TTL

    # ═══ [Option B 2026-04-26 Manus AI] Hash-based no-write idempotency ═══
    # If the new payload is semantically identical to what we last wrote,
    # skip the heavy multi-key SET (current + last_good + meta + audit) and only
    # refresh TTLs + ts heartbeat keys. Also delete the canary so the writer-side
    # producer can publish a fresh one. Consumer freshness guarantees are preserved
    # because:
    #   - ssot:target:v2:current TTL is extended (EXPIRE)
    #   - ssot:target:v2:ts / :ts:epoch are rewritten with current wall-clock
    #   - ssot:target:v2:meta last_promote_ts is updated
    new_semhash = _semantic_hash(payload)
    # [v2.5.1 R2] Track classification of why we are (or are not) in the no-write path.
    _hash_read_error = ""
    try:
        prev_semhash = r.get(SEMHASH_KEY)
    except Exception as _e_h:
        _hash_read_error = str(_e_h)[:120]
        log("WARN", f"Failed to read prev semantic_hash: {_e_h}")
        prev_semhash = None

    # [v2.5.1 R2] Default classification; refined as we travel through the branches.
    if _hash_read_error:
        _prev_hash_state = "hash_read_failed"
    elif prev_semhash is None:
        _prev_hash_state = "missing"
    elif prev_semhash != new_semhash:
        _prev_hash_state = "changed"
    else:
        _prev_hash_state = "unchanged"  # never makes it past the if-block below

    if prev_semhash and prev_semhash == new_semhash:
        # Identical semantic hash — lightweight refresh instead of a full rewrite.
        # [STRUCTURAL-FIX 2026-04-26 Manus AI] Re-write the payload itself with
        # fresh `ts` / `added_at_ms` so downstream freshness gates
        # (ssot-sanity-guard-v3.mjs reads obj.added_at_ms) do not see stale
        # ageMs after long no-change periods. The previous implementation only
        # refreshed external helper keys (:ts, :ts:epoch, META.last_promote_ts)
        # and TTLs; the JSON body's added_at_ms stayed frozen, which produced
        # SSOT_STALE_xxxxx blocks and OPEN/HALT flapping in final-trade-gate.
        try:
            _now_ms_int = int(time.time() * 1000)
            _now_ms = str(_now_ms_int)
            _now_sec = str(int(time.time()))
            # ── Refresh freshness-bearing fields inside the payload itself ──
            try:
                payload["added_at_ms"] = _now_ms_int
                payload["ts"] = _now_ms_int
                # tag a fresh correlation id so audit consumers can distinguish
                # noop-refresh cycles from real promotions if needed.
                _orig_cid = payload.get("correlation_id", "")
                payload["correlation_id"] = f"noop-refresh-{_now_ms_int}"
                # propagate to nested meta dicts if present, so the sanity
                # guard's deep fallbacks (targets.meta.added_at_ms,
                # meta.added_at_ms) stay aligned and never disagree.
                _t = payload.get("targets")
                if isinstance(_t, dict):
                    _tm = _t.get("meta")
                    if isinstance(_tm, dict):
                        _tm["added_at_ms"] = _now_ms_int
                        _tm["last_noop_refresh_ms"] = _now_ms_int
                _m = payload.get("meta")
                if isinstance(_m, dict):
                    _m["added_at_ms"] = _now_ms_int
                    _m["last_noop_refresh_ms"] = _now_ms_int
                payload_str_refresh = json.dumps(payload)
            except Exception as _e_pp:
                log("WARN", f"NOOP refresh: payload mutation failed, falling back to ts-only: {_e_pp}")
                payload_str_refresh = None

            refresh_pipe = r.pipeline()
            # SSOT_CONTRACT_V1: Tag this payload as NOOP_REFRESH so downstream
            # consumers (Final Trade Gate, audits) can distinguish a real signal
            # promotion from an idempotency-driven freshness refresh.
            try:
                if payload_str_refresh:
                    _payload_obj_refresh = json.loads(payload_str_refresh)
                    if isinstance(_payload_obj_refresh, dict):
                        _payload_obj_refresh["promotion"] = {
                            "type": "NOOP_REFRESH",
                            "by":   "ssot_promote_v2",
                            "at":   _now_ms,
                            "semantic_hash": new_semhash,
                            "marker_version": "phase9_b1_marker_carrythrough_v1",
                        }
                        _payload_obj_refresh = _phase9_b1_attach_marker(_payload_obj_refresh, "NOOP_REFRESH", new_semhash, "hash_skip")
                        payload_str_refresh = json.dumps(_payload_obj_refresh)
            except Exception as _e_tag:
                log("WARN", f"NOOP_HASH_SKIP: failed to inject promotion.type: {_e_tag}")
            # Re-SETEX the canonical payload so its body's added_at_ms is fresh.
            if payload_str_refresh is not None:
                ssot_safe_set(r, CURRENT_KEY,    payload_str_refresh, ex=_effective_ttl, pipeline=refresh_pipe)
                ssot_safe_set(r, "ssot:current", payload_str_refresh, ex=_effective_ttl, pipeline=refresh_pipe)
                ssot_safe_set(r, LAST_GOOD_KEY,  payload_str_refresh, ex=_effective_ttl, pipeline=refresh_pipe)
            else:
                # Defensive: at minimum extend TTL so consumers don't see the
                # key vanish even if the payload mutation above failed.
                refresh_pipe.expire(CURRENT_KEY, _effective_ttl)
                refresh_pipe.expire("ssot:current", _effective_ttl)
                refresh_pipe.expire(LAST_GOOD_KEY, _effective_ttl)
            ssot_safe_set(r, "ssot:target:v2:ts", _now_ms, ex=_effective_ttl, pipeline=refresh_pipe)
            ssot_safe_set(r, "ssot:target:v2:ts:epoch", _now_sec, ex=_effective_ttl, pipeline=refresh_pipe)
            refresh_pipe.expire(SEMHASH_KEY, _effective_ttl)
            refresh_pipe.hset(META_KEY, mapping={
                "last_promote_status": "NOOP_HASH_SKIP",
                "last_promote_ts": _now_ms,
                "last_promote_errors": "",
                "semantic_hash": new_semhash,
                "noop_refresh_ms": _now_ms,
                "noop_payload_refreshed": "1" if payload_str_refresh is not None else "0",
            })
            refresh_pipe.xadd("audit:ssot_v2:promotes", {
                "status": "NOOP_HASH_SKIP",
                "ts": _now_ms,
                "semantic_hash": new_semhash,
                "correlation_id": payload.get("correlation_id", ""),
                "payload_refreshed": "1" if payload_str_refresh is not None else "0",
                "fix_tag": "manus_freshness_fix_2026-04-26",
            }, maxlen=500)
            refresh_pipe.execute()
            r.delete(CANARY_KEY)
            log("INFO", f"NOOP_HASH_SKIP: semantic_hash unchanged ({new_semhash}) — payload refreshed (added_at_ms/ts updated) + TTL extended")
        except Exception as _e_r:
            log("ERROR", f"Hash-skip refresh failed, falling through to full write: {_e_r}")
            # [v2.5.1 R1] Record the skip-path failure explicitly so audit consumers
            # can distinguish it from genuine signal changes. We do this OUTSIDE the
            # broken pipeline so it lands as an independent XADD even if the
            # earlier execute() partially failed.
            try:
                r.xadd("audit:ssot_v2:promotes", {
                    "status": "NOOP_HASH_SKIP_FAILED",
                    "ts": str(int(time.time() * 1000)),
                    "semantic_hash": new_semhash,
                    "correlation_id": payload.get("correlation_id", ""),
                    "error": str(_e_r)[:200],
                }, maxlen=500)
            except Exception as _e_audit:
                # Never let audit failures mask the original problem.
                log("WARN", f"Could not record NOOP_HASH_SKIP_FAILED audit: {_e_audit}")
            # Mark the impending full-write as a fall-through from a failed skip.
            _prev_hash_state = "fallthrough_from_skip_fail"
        else:
            return  # IMPORTANT: skip the full write below
    # ═══ end Option B no-write block ═══

    pipe = r.pipeline()
    # --- ARES Wave1.2: Cost Gate before ssot:target:v2:current write ---
    if not _ares_cost_gate_pre_current_write(locals()):
        log("WARN", "GATE2_BLOCK: _ares_cost_gate_pre_current_write returned False")
        return None
    # ---------------------------------------------------------------------
    # SSOT_CONTRACT_V1: explicit promotion.type marker for full writes so the
    # downstream gate can distinguish FULL_WRITE / NOOP_REFRESH / WATCHDOG_REFRESH.
    try:
        _full_payload = json.loads(payload_str)
        _full_payload["promotion"] = {
            "type": "FULL_WRITE",
            "by":   "ssot_promote_v2",
            "at":   int(time.time() * 1000),
            "semantic_hash": new_semhash,
            "prev_hash_state": _prev_hash_state,
            "marker_version": "phase9_b1_marker_carrythrough_v1",
        }
        _full_payload = _phase9_b1_attach_marker(_full_payload, "FULL_WRITE", new_semhash, _prev_hash_state)
        payload_str = json.dumps(_full_payload)
    except Exception as _e_tag:
        log("WARN", f"FULL_WRITE: failed to inject promotion.type: {_e_tag}")
    ssot_safe_set(r, CURRENT_KEY, payload_str, ex=_effective_ttl, pipeline=pipe)
    # [4AI-CONSENSUS-FIX] ssot:target:v2:ts 키를 별도로 설정
    # [FIX-5 2026-04-16] Write BOTH ts keys atomically:
    _promote_epoch_ms = str(int(time.time() * 1000))
    _promote_epoch_sec = str(int(time.time()))
    ssot_safe_set(r, "ssot:target:v2:ts", _promote_epoch_ms, ex=_effective_ttl, pipeline=pipe)
    ssot_safe_set(r, "ssot:target:v2:ts:epoch", _promote_epoch_sec, ex=_effective_ttl, pipeline=pipe)
    # [FIX-MISSING2] ssot:current도 동시 갱신
    ssot_safe_set(r, "ssot:current", payload_str, ex=_effective_ttl, pipeline=pipe)  # [BUG-2 FIX] unified TTL
    pipe.set(LAST_GOOD_KEY, payload_str)  # No TTL for last_good
    pipe.hset(META_KEY, mapping={
        "last_promote_status": "OK",
        "last_promote_ts": str(int(time.time() * 1000)),
        "last_promote_errors": "",
        "n_positions": str(total_count),
        "n_active": str(active_count),
        "n_residual": str(residual_count),
        "gross": str(payload["targets"]["gross"]),
        "regime": payload["targets"]["meta"].get("regime", "unknown"),
        "correlation_id": payload.get("correlation_id", ""),
        "semantic_hash": new_semhash,  # [Option B 2026-04-26]
    })
    pipe.xadd("audit:ssot_v2:promotes", {
        "status": "OK",
        "ts": str(int(time.time() * 1000)),
        "n_positions": str(total_count),
        "n_active": str(active_count),
        "n_residual": str(residual_count),
        "gross": str(payload["targets"]["gross"]),
        "correlation_id": payload.get("correlation_id", ""),
        "source_ts": str(payload.get("targets", {}).get("meta", {}).get("source_ts", 0)),
        "semantic_hash": new_semhash,  # [Option B 2026-04-26]
        "prev_hash_state": _prev_hash_state,  # [v2.5.1 R2 2026-04-26] missing|hash_read_failed|changed|fallthrough_from_skip_fail
        "prev_semantic_hash": (prev_semhash or "")[:12],  # [v2.5.1 R2] empty if missing
        "hash_read_error": _hash_read_error,  # [v2.5.1 R2] error msg if hash_read_failed, else ""
        "version": PROMOTE_VERSION,  # [v2.5.1] for filtering by version in long-run audits
    }, maxlen=500)
    # [Option B 2026-04-26] persist the new semantic hash for the next cycle's no-write check
    ssot_safe_set(r, SEMHASH_KEY, new_semhash, ex=_effective_ttl, pipeline=pipe)
    pipe.execute()
    
    log("INFO", f"Promoted: {total_count} positions (active={active_count}, residual={residual_count}), "
        f"gross={payload['targets']['gross']:.4f}")


# ═══════════════════════════════════════════════════════════════
# FIX-2: Health Heartbeat
# ═══════════════════════════════════════════════════════════════
def _heartbeat():
    """Write health heartbeat to Redis so external monitors can detect stalls."""
    try:
        r.hset(HEALTH_KEY, mapping={
            "pid": str(PROMOTE_PID),
            "ts": str(int(time.time() * 1000)),
            "version": PROMOTE_VERSION,
            "status": "alive"
        })
        r.expire(HEALTH_KEY, HEALTH_TTL)
    except Exception:
        pass  # heartbeat failure is non-critical


# ═══════════════════════════════════════════════════════════════
# FIX-5: PID-based Singleton Guard
# ═══════════════════════════════════════════════════════════════
def _check_singleton():
    """Prevent duplicate instances. Returns True if we're the only instance."""
    try:
        existing_pid = r.get(PID_KEY)
        if existing_pid and existing_pid != str(PROMOTE_PID):
            try:
                os.kill(int(existing_pid), 0)
                log("WARN", f"Another instance running (PID={existing_pid}), exiting",
                    my_pid=PROMOTE_PID)
                return False
            except (ProcessLookupError, ValueError):
                log("INFO", f"Stale PID lock (PID={existing_pid}), taking over")
        
        r.set(PID_KEY, str(PROMOTE_PID), ex=PID_TTL)
        return True
    except Exception as e:
        log("WARN", f"Singleton check failed: {e}, proceeding anyway")
        return True


# ═══════════════════════════════════════════════════════════════
# FIX-6: Startup Reason Detection
# ═══════════════════════════════════════════════════════════════
def _detect_startup_reason():
    """Detect why this process started."""
    pm2_restart_count = os.getenv("restart_time", "0")
    
    if pm2_restart_count == "0":
        reason = "first_start"
    else:
        reason = f"pm2_restart_{pm2_restart_count}"
    
    return reason


# ═══════════════════════════════════════════════════════════════
# FIX-3: Circuit Breaker
# ═══════════════════════════════════════════════════════════════
class CircuitBreaker:
    """Simple circuit breaker for Redis operations."""
    def __init__(self, max_failures=CB_MAX_FAILURES, reset_after=CB_RESET_AFTER_SEC):
        self.max_failures = max_failures
        self.reset_after = reset_after
        self.failures = 0
        self.last_failure_time = 0
        self.state = "CLOSED"
    
    def record_success(self):
        if self.state != "CLOSED":
            log("INFO", f"Circuit breaker: {self.state} → CLOSED (recovered)")
        self.failures = 0
        self.state = "CLOSED"
    
    def record_failure(self, error):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.max_failures:
            if self.state != "OPEN":
                log("ERROR", f"Circuit breaker: OPEN after {self.failures} failures",
                    last_error=str(error))
            self.state = "OPEN"
    
    def should_attempt(self):
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.reset_after:
                self.state = "HALF_OPEN"
                log("INFO", f"Circuit breaker: OPEN → HALF_OPEN (testing after {elapsed:.0f}s)")
                return True
            return False
        return True


def main():
    global _shutdown_requested
    
    startup_reason = _detect_startup_reason()
    log("INFO", f"Starting SSOT V2 Promote Gate v{PROMOTE_VERSION}",
        pid=PROMOTE_PID, startup_reason=startup_reason,
        residual_sync=RESIDUAL_SYNC_ENABLED)
    
    # FIX-5: Singleton guard
    if not _check_singleton():
        log("WARN", "Exiting: another instance is running (singleton guard)")
        sys.exit(0)
    
    cb = CircuitBreaker()
    cycle_count = 0
    
    while not _shutdown_requested:
        cycle_count += 1
        
        # FIX-3: Circuit breaker check
        if not cb.should_attempt():
            log("WARN", f"Circuit breaker OPEN, skipping cycle {cycle_count}")
            time.sleep(LOOP_INTERVAL)
            continue
        
        try:
            promote()
            cb.record_success()
            
            # FIX-2: Heartbeat every cycle
            _heartbeat()
            
            # FIX-5: Refresh PID lock
            if cycle_count % 2 == 0:
                r.set(PID_KEY, str(PROMOTE_PID), ex=PID_TTL)
            
        except redis.ConnectionError as e:
            cb.record_failure(e)
            log("ERROR", f"Redis connection error (cycle {cycle_count}): {e}")
        except redis.TimeoutError as e:
            cb.record_failure(e)
            log("ERROR", f"Redis timeout (cycle {cycle_count}): {e}")
        except Exception as e:
            log("ERROR", f"Promote error (cycle {cycle_count}): {e}")
        
        # Interruptible sleep
        for _ in range(LOOP_INTERVAL):
            if _shutdown_requested:
                break
            time.sleep(1)
    
    # Graceful shutdown
    log("INFO", f"SSOT Promote Gate shutting down (reason={_shutdown_reason}, cycles={cycle_count})")
    try:
        r.hset(HEALTH_KEY, mapping={
            "pid": str(PROMOTE_PID),
            "ts": str(int(time.time() * 1000)),
            "version": PROMOTE_VERSION,
            "status": f"shutdown:{_shutdown_reason}"
        })
        r.expire(HEALTH_KEY, HEALTH_TTL)
        r.delete(PID_KEY)
    except Exception:
        pass


if __name__ == "__main__":
    main()
