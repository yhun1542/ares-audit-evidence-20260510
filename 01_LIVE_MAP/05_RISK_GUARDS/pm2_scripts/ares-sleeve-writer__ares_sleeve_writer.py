#!/usr/bin/env python3
# SOURCE: /home/ubuntu/ares_sleeve_writer_v84.py
# PATCHED: 2026-05-07 — F1 (ctx:regime:state 부재 시 라이브 컴포넌트 fallback)
#                       + 자기보호: 디폴트 source 강제 알람화
# Original SIZE: 23683 chars

"""
ares_sleeve_writer_v84.py — ARES Sleeve Writer v8.4 (Structural Fix, 2026-04-24)
                            + F1 PATCH (2026-05-07)

Problem statement (original)
----------------------------
The pm2 process "ares-sleeve-writer" was executing a v8.0 data-collection
orchestrator test harness that never SETs `sleeve:ares:weights`. Because
Bridge requires that key (with a `metadata{dd_20, vix_z, vol_z}` object) for
the v8.4 λ-overlay and the v8.5.x risk_rate path, λ went 0% applied and
`raw_rr` got frozen at 0 for ~8.7 hours (LAMBDA_APPLY_SKIP x 6200+).

F1 PATCH (2026-05-07)
---------------------
Field finding: even though regime_writer_v88 calls
  redis.set("ctx:regime:state", JSON.stringify(state), "EX", 600)
ElastiCache returns TYPE=none/STRLEN=0 for that key (audit twin survives).
Cause is upstream and may take time to fix; meanwhile the writer was
publishing `metadata.source=default_fallback` 100% with all-zero stress
inputs, which silently masked stress signals at the Bridge.

This patch:
  (a) Adds an explicit live-component fallback path when ctx:regime:state
      is missing/empty: dd_20 from ofg:drawdown:current, vix_z from
      market:vix:current/market:vix_ma20, vol_z from market:vol:zscore (if
      present, else 0). source = "live_components_only".
  (b) Refuses to publish source=default_fallback unless ALL three live
      components are also missing; in that explicit case the WRITER logs
      [WRITER_DEFAULT_FALLBACK] at WARNING level so journald/cron alarms
      can pick it up.
  (c) Emits a writer-side `bridge_metadata_audit` Redis hash (TTL 600s)
      with the resolved source/inputs so external tools can verify the
      data-quality state without parsing pm2 logs.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

try:
    import redis  # type: ignore
except Exception as exc:  # pragma: no cover
    print(f"[ARES_SLEEVE_WRITER_FATAL] redis import failed: {exc}", file=sys.stderr)
    raise SystemExit(65)


VERSION = "8.4.1-f1patch-20260507"

# --------------------------------------------------------------------------
# Config (env-driven; defaults mirror v3.1 production)
# --------------------------------------------------------------------------
SLEEVE_KEY       = os.environ.get("ARES_WEIGHTS_KEY", "sleeve:ares:weights")
FINAL_WEIGHTS_KEY = os.environ.get("SLEEVE_FINAL_WEIGHTS_KEY", "sleeve:final_weights:current")
LATEST_KEY       = os.environ.get("ARES_SLEEVE_WRITER_KEY", "ares:sleeve:writer:latest")
HEARTBEAT_KEY    = os.environ.get("ARES_SLEEVE_WRITER_HEARTBEAT_KEY", "ares:sleeve:writer:heartbeat")
HEALTH_HB_KEY    = "ares:health:ares-sleeve-writer:heartbeat"
INTERVAL_SEC     = int(os.environ.get("SLEEVE_INTERVAL", "5"))
TTL_SEC          = int(os.environ.get("SLEEVE_TTL", "30"))
HB_TTL_SEC       = int(os.environ.get("ARES_SLEEVE_WRITER_HB_TTL_SEC", "180"))
RAMP_MINUTES     = int(os.environ.get("SLEEVE_RAMP_MINUTES", "10"))

CHAMPION_SIGNALS_KEY = "champion:v660:signals"
REGIME_FINAL_KEY     = "regime:final:current"
REGIME_STATE_KEY     = "ctx:regime:state"
DD_CURRENT_KEY       = "ofg:drawdown:current"
VIX_CUR_KEY          = "market:vix:current"
VIX_MA_KEY           = "market:vix_ma20"
VOL_Z_KEY            = "market:vol:zscore"   # F1: optional vol_z SSOT
META_AUDIT_KEY       = "ares:sleeve:writer:metadata_audit"   # F1: writer audit
META_AUDIT_TTL_SEC   = int(os.environ.get("ARES_META_AUDIT_TTL_SEC", "600"))

V300_WEIGHTS_KEY       = os.environ.get("V300_WEIGHTS_KEY", "sleeve:ares:v300:weights")
S2_WEIGHTS_KEY         = os.environ.get("S2_WEIGHTS_KEY", "sleeve:ares:strategy2:weights")
AP_WEIGHTS_KEY         = os.environ.get("AP_WEIGHTS_KEY", "sleeve:ares:alpha_prod:shadow")
AP_KILL_SWITCH_KEY     = "sleeve:ares:alpha_prod:kill_switch"

V300_MAX_STALE_SEC = int(os.environ.get("V300_MAX_STALE_SEC", "90000"))
S2_MAX_STALE_SEC   = int(os.environ.get("S2_MAX_STALE_SEC",   "90000"))
AP_MAX_STALE_SEC   = int(os.environ.get("AP_MAX_STALE_SEC",   "180000"))

GROSS_MAX      = float(os.environ.get("GROSS_MAX",      "2.0"))
NET_MAX        = float(os.environ.get("NET_MAX",        "0.30"))
SINGLE_POS_MAX = float(os.environ.get("SINGLE_POS_MAX", "0.10"))

# F1 PATCH: live override knobs
VIX_Z_SCALE = float(os.environ.get("ARES_VIX_Z_SCALE", "5.0"))
VIX_MA_MIN  = float(os.environ.get("ARES_VIX_MA_MIN", "1e-6"))

HYBRID_LAMBDA_BY_ASM = {
    "CALM":     0.30,
    "RISK_OFF": 0.15,
    "CRISIS":   0.00,
}

# --------------------------------------------------------------------------
# Logging — stdout only (stderr reserved for real errors that pm2 shows red)
# --------------------------------------------------------------------------
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter(
    fmt="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("ares_sleeve_writer_v84")


# --------------------------------------------------------------------------
# Redis connection — fail fast on localhost in prod; exponential backoff
# --------------------------------------------------------------------------
def _load_redis_url() -> str:
    for key in ("REDIS_URL", "ARES_REDIS_URL", "REDIS_WRITER_URL"):
        v = os.environ.get(key)
        if v:
            return _validate_redis_url(v)
    env_file = "/etc/ares/redis.env"
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                k, val = raw.split("=", 1)
                if k.strip() in {"REDIS_URL", "ARES_REDIS_URL", "REDIS_WRITER_URL"} and val.strip():
                    return _validate_redis_url(val.strip().strip('"').strip("'"))
    raise RuntimeError("REDIS_URL missing; refusing to run sleeve writer without SSOT Redis")


def _validate_redis_url(url: str) -> str:
    low = url.lower()
    if ("127.0.0.1" in low or "localhost" in low) and \
       os.environ.get("ARES_ALLOW_LOCALHOST_REDIS") != "1":
        raise RuntimeError(
            f"[FATAL] REDIS_URL points to localhost ({url}); refused in production"
        )
    return url


def _safe_redis_connect(url: str, max_retries: int = 5) -> "redis.Redis":
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            r = redis.from_url(url, decode_responses=True, socket_timeout=10, socket_connect_timeout=10)
            r.ping()
            log.info("Redis connected OK (attempt %d): %s", attempt, url.split("@")[-1])
            return r
        except Exception as exc:
            last_exc = exc
            wait = min(2 ** attempt, 30)
            log.error("Redis connect failed (attempt %d/%d): %s. Retry in %ds...",
                      attempt, max_retries, exc, wait)
            time.sleep(wait)
    raise ConnectionError(f"Redis connection failed after {max_retries} attempts: {last_exc}")


# --------------------------------------------------------------------------
# Input readers (robust to missing/partial data)
# --------------------------------------------------------------------------
def _safe_json(s: Any) -> Optional[dict]:
    if s is None:
        return None
    try:
        return json.loads(s) if isinstance(s, str) else s
    except Exception:
        return None


def _is_finite_num(x: Any) -> bool:
    try:
        f = float(x)
        return f == f and f not in (float("inf"), float("-inf"))
    except Exception:
        return False


def get_asm_state(r: "redis.Redis") -> Tuple[str, dict]:
    """Return (asm_state, regime_info)."""
    try:
        raw = r.hgetall(REGIME_FINAL_KEY) or {}
        asm = str(raw.get("asm_state") or raw.get("state") or "CALM").upper()
        if asm not in HYBRID_LAMBDA_BY_ASM:
            preset = str(raw.get("preset", "")).upper()
            if "CRISIS" in preset or "HARD_RISK" in preset:
                asm = "CRISIS"
            elif "RISK_OFF" in preset or "SHOCK" in preset:
                asm = "RISK_OFF"
            else:
                asm = "CALM"
        return asm, raw
    except Exception as e:
        log.warning("regime:final:current read failed: %s; defaulting asm=CALM", e)
        return "CALM", {}


def get_champion_weights(r: "redis.Redis") -> Optional[Dict[str, float]]:
    """Read champion weights exactly as v3.1 does."""
    try:
        raw = r.hget(CHAMPION_SIGNALS_KEY, "final_allocation")
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode()
            j = _safe_json(raw)
            if isinstance(j, dict) and j:
                return {str(k): float(v) for k, v in j.items() if _is_finite_num(v)}
    except Exception as e:
        log.warning("champion read (final_allocation) failed: %s", e)

    try:
        raw2 = r.get("sleeve:v3:final_weights:current")
        if raw2:
            if isinstance(raw2, bytes):
                raw2 = raw2.decode()
            j2 = _safe_json(raw2)
            if isinstance(j2, dict):
                w2 = j2.get("weights") or {}
                if isinstance(w2, dict) and w2:
                    log.info("[CHAMPION_FALLBACK] using sleeve:v3:final_weights:current.weights")
                    return {str(k): float(v) for k, v in w2.items() if _is_finite_num(v)}
    except Exception as e:
        log.warning("champion read (v3 fallback) failed: %s", e)
    return None


def _get_sleeve_weights(r: "redis.Redis", key: str, max_stale_sec: int) -> Tuple[Optional[Dict[str, float]], Optional[dict]]:
    try:
        raw = r.get(key)
        if not raw:
            return None, None
        data = _safe_json(raw)
        if not data:
            return None, None
        ts = data.get("ts")
        if ts:
            try:
                t0 = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                if (time.time() - t0) > max_stale_sec:
                    return None, (data.get("metadata") or {})
            except Exception:
                pass
        weights = data.get("weights") or {}
        weights = {str(k): float(v) for k, v in weights.items() if _is_finite_num(v)}
        meta = data.get("metadata") or {}
        return (weights or None), meta
    except Exception as e:
        log.warning("%s read failed: %s", key, e)
        return None, None


# --------------------------------------------------------------------------
# F1 PATCH: live components helper
# --------------------------------------------------------------------------
def _read_live_components(r: "redis.Redis") -> Dict[str, Any]:
    """Read the three live SSOT inputs Bridge stress depends on.

    Returns a dict with keys dd_20, vix_z, vol_z, _have_dd, _have_vix, _have_vol.
    Missing/non-finite values become 0.0 with the corresponding _have_* False.
    """
    out: Dict[str, Any] = {
        "dd_20": 0.0, "vix_z": 0.0, "vol_z": 0.0,
        "_have_dd": False, "_have_vix": False, "_have_vol": False,
        "vix_cur": None, "vix_ma": None,
    }
    # dd_20 — ofg:drawdown:current is a non-negative magnitude, Bridge expects negative
    try:
        dd_raw = r.get(DD_CURRENT_KEY)
        if dd_raw is not None:
            dd_val = float(dd_raw)
            if _is_finite_num(dd_val):
                out["dd_20"] = -abs(dd_val)
                out["_have_dd"] = True
    except Exception as e:
        log.warning("[F1] read dd failed: %s", e)
    # vix_z — synthesized from VIX live / 20-day MA
    try:
        vix_cur_raw = r.get(VIX_CUR_KEY)
        vix_ma_raw  = r.get(VIX_MA_KEY)
        vix_cur = float(vix_cur_raw) if vix_cur_raw is not None else float("nan")
        vix_ma  = float(vix_ma_raw)  if vix_ma_raw  is not None else float("nan")
        if _is_finite_num(vix_cur) and _is_finite_num(vix_ma) and vix_ma > VIX_MA_MIN:
            out["vix_z"]   = (vix_cur - vix_ma) / vix_ma * VIX_Z_SCALE
            out["vix_cur"] = vix_cur
            out["vix_ma"]  = vix_ma
            out["_have_vix"] = True
    except Exception as e:
        log.warning("[F1] read vix failed: %s", e)
    # vol_z — optional SSOT key
    try:
        vol_raw = r.get(VOL_Z_KEY)
        if vol_raw is not None:
            vol_val = float(vol_raw)
            if _is_finite_num(vol_val):
                out["vol_z"] = vol_val
                out["_have_vol"] = True
    except Exception as e:
        log.warning("[F1] read vol_z failed: %s", e)
    return out


def build_bridge_metadata(r: "redis.Redis") -> Dict[str, Any]:
    """Produce the `metadata {dd_20, vix_z, vol_z}` object Bridge requires.

    Priority order (F1 PATCHED):
      1. ctx:regime:state.components.stress {dd20, vix_z, vol_z}, with live
         dd/vix overrides; source = ctx_regime_state[_live].
      2. ctx:regime:state missing/empty → live components only
         (ofg:drawdown:current + market:vix:current/vix_ma20 + market:vol:zscore);
         source = live_components_only.
      3. All three live components missing too → all-zero placeholder;
         source = default_fallback. WARNING is logged (alarmable).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    meta: Dict[str, Any] = {
        "dd_20": 0.0, "vix_z": 0.0, "vol_z": 0.0,
        "ts": now_iso, "source": "default_fallback",
    }
    # Snapshot of live components is always computed; cheap (3 GETs) and
    # used both as override and as fallback.
    live = _read_live_components(r)
    # Always-defined regime_raw so the audit block below cannot NameError.
    regime_raw = None

    try:
        regime_raw = r.get(REGIME_STATE_KEY)
        if regime_raw:
            regime = _safe_json(regime_raw) or {}
            stress = (regime.get("components") or {}).get("stress") or {}
            dd20  = float(stress.get("dd20", 0.0) or 0.0)
            vix_z = float(stress.get("vix_z", 0.0) or 0.0)
            vol_z = float(stress.get("vol_z", 0.0) or 0.0)
            # dd_20 override — prefer live ofg:drawdown:current if available
            if live["_have_dd"]:
                dd20 = live["dd_20"]
            # vix_z live override when ctx says ~0 but live computed differs
            if live["_have_vix"] and abs(vix_z) < 1e-3 and abs(live["vix_z"]) > 1e-3:
                vix_z = live["vix_z"]
                source = "ctx_regime_state_live"
            else:
                source = "ctx_regime_state"
            # vol_z live override (analogous to vix_z rule)
            if live["_have_vol"] and abs(vol_z) < 1e-6 and abs(live["vol_z"]) > 1e-6:
                vol_z = live["vol_z"]
                source = "ctx_regime_state_live"
            meta.update({
                "dd_20": dd20, "vix_z": vix_z, "vol_z": vol_z,
                "ts": datetime.now(timezone.utc).isoformat(),
                "source": source,
            })
        else:
            # F1: Live-only fallback
            have_any = live["_have_dd"] or live["_have_vix"] or live["_have_vol"]
            if have_any:
                meta.update({
                    "dd_20": live["dd_20"], "vix_z": live["vix_z"], "vol_z": live["vol_z"],
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "source": "live_components_only",
                })
            else:
                # All three missing → final fallback. Warn so CRON/journald alarms fire.
                log.warning("[WRITER_DEFAULT_FALLBACK] all live components missing "
                            "(dd=%s vix=%s vol=%s); meta.source stays default_fallback",
                            live["_have_dd"], live["_have_vix"], live["_have_vol"])
                meta["ts"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        log.warning("[METADATA] build_bridge_metadata failed: %s", e)

    # F1: writer-side audit hash (for self-checks & external alarms)
    try:
        audit = {
            "ts": meta["ts"],
            "source": meta["source"],
            "dd_20": meta["dd_20"], "vix_z": meta["vix_z"], "vol_z": meta["vol_z"],
            "have_dd": live["_have_dd"], "have_vix": live["_have_vix"], "have_vol": live["_have_vol"],
            "vix_cur": live["vix_cur"], "vix_ma": live["vix_ma"],
            "ctx_regime_state_present": bool(regime_raw),
        }
        r.set(META_AUDIT_KEY, json.dumps(audit, ensure_ascii=False), ex=META_AUDIT_TTL_SEC)
    except Exception as e:
        # Don't crash the publish loop on audit failure; log at WARN.
        log.warning("[F1] writer audit publish failed: %s", e)
    return meta


# --------------------------------------------------------------------------
# Blend logic (v3.1 parity, with safe fallbacks)
# --------------------------------------------------------------------------
def _validate_weights(w: Optional[Dict[str, float]], tag: str) -> Optional[Dict[str, float]]:
    if not w:
        return None
    g = sum(abs(float(v)) for v in w.values())
    if g > GROSS_MAX * 1.5:
        log.warning("[%s] gross=%.3f exceeds sanity 1.5x; dropping", tag, g)
        return None
    return w


def compute_champion_tilt(champion: Dict[str, float], asm: str) -> Dict[str, float]:
    factor = {"CALM": 1.00, "RISK_OFF": 0.85, "CRISIS": 0.60}.get(asm, 1.00)
    return {k: float(v) * factor for k, v in champion.items()}


def blend_weights_4way(champion_tilt: Dict[str, float],
                       v300: Optional[Dict[str, float]],
                       s2: Optional[Dict[str, float]],
                       ap: Optional[Dict[str, float]],
                       asm: str) -> Dict[str, float]:
    active_sources: list = [("champion", champion_tilt)]
    if v300: active_sources.append(("v300", v300))
    if s2:   active_sources.append(("s2",   s2))
    if ap:   active_sources.append(("ap",   ap))

    if len(active_sources) == 1:
        return dict(champion_tilt)

    lam = HYBRID_LAMBDA_BY_ASM.get(asm, 0.30)
    others = [w for name, w in active_sources if name != "champion"]
    per_w = lam / max(1, len(others))

    all_syms = set(champion_tilt.keys())
    for w in others:
        all_syms |= set(w.keys())

    out: Dict[str, float] = {}
    for s in all_syms:
        val = (1.0 - lam) * float(champion_tilt.get(s, 0.0))
        for w in others:
            val += per_w * float(w.get(s, 0.0))
        out[s] = val
    return out


def post_blend_risk_check(weights: Dict[str, float]) -> Tuple[Dict[str, float], list]:
    violations: list = []
    if not weights:
        return weights, violations

    clipped = {}
    for s, w in weights.items():
        wf = float(w)
        if abs(wf) > SINGLE_POS_MAX:
            violations.append({"rule": "single_pos", "sym": s, "w": wf, "cap": SINGLE_POS_MAX})
            wf = SINGLE_POS_MAX if wf > 0 else -SINGLE_POS_MAX
        clipped[s] = wf

    g = sum(abs(v) for v in clipped.values())
    if g > GROSS_MAX and g > 0:
        scale = GROSS_MAX / g
        clipped = {s: v * scale for s, v in clipped.items()}
        violations.append({"rule": "gross", "before": g, "after": GROSS_MAX})

    n = sum(v for v in clipped.values() if v > 0) + sum(v for v in clipped.values() if v < 0)
    if n > NET_MAX:
        scale = NET_MAX / n
        clipped = {s: (v * scale if v > 0 else v) for s, v in clipped.items()}
        violations.append({"rule": "net_long", "before": n, "after": NET_MAX})

    return clipped, violations


# --------------------------------------------------------------------------
# Core publish — writes weights, metadata, latest, heartbeat atomically.
# --------------------------------------------------------------------------
def publish_once(r: "redis.Redis", ramp_factor: float) -> Dict[str, Any]:
    asm, _ = get_asm_state(r)
    champion = get_champion_weights(r)
    if champion is None:
        champion = {}

    champion_tilt = compute_champion_tilt(champion, asm)

    v300, v300_meta = _get_sleeve_weights(r, V300_WEIGHTS_KEY, V300_MAX_STALE_SEC)
    v300 = _validate_weights(v300, "V300")

    s2, s2_meta = _get_sleeve_weights(r, S2_WEIGHTS_KEY, S2_MAX_STALE_SEC)
    s2 = _validate_weights(s2, "S2")

    ap_kill = False
    try:
        ks = r.get(AP_KILL_SWITCH_KEY)
        ap_kill = bool(ks and str(ks).lower() not in {"0", "false", "no", ""})
    except Exception:
        pass
    ap, ap_meta = (None, None) if ap_kill else _get_sleeve_weights(r, AP_WEIGHTS_KEY, AP_MAX_STALE_SEC)
    ap = _validate_weights(ap, "AP")

    blended = blend_weights_4way(champion_tilt, v300, s2, ap, asm)
    clipped, risk_violations = post_blend_risk_check(blended)

    bridge_metadata = build_bridge_metadata(r)

    now_iso = datetime.now(timezone.utc).isoformat()
    payload: Dict[str, Any] = {
        "weights": clipped,
        "state": asm,
        "lambda_hint": HYBRID_LAMBDA_BY_ASM.get(asm, 0.0) * ramp_factor,
        "ts": now_iso,
        "source": "ares_sleeve_writer_v84",
        "ramp_factor": ramp_factor,
        "v300_active": bool(v300),
        "s2_active":   bool(s2),
        "ap_active":   bool(ap),
        "risk_violations": risk_violations,
        "version": VERSION,
        "metadata": bridge_metadata,
    }
    if v300_meta: payload["v300_metadata"] = v300_meta
    if s2_meta:   payload["s2_metadata"]   = s2_meta
    if ap_meta:   payload["ap_metadata"]   = ap_meta

    final_payload: Dict[str, Any] = {
        "schema_version": "ares.sleeve.final_weights.v1",
        "weights": clipped,
        "ts": time.time(),
        "asof": now_iso,
        "source": "ares_sleeve_writer_v84",
        "state": asm,
        "sleeve_mix": {"core": 1.0, "defensive": 0.0, "flow_event": 0.0},
        "metadata": bridge_metadata,
        "ttl_sec": max(TTL_SEC, 300),
        "risk_violations": risk_violations,
        "version": VERSION,
    }

    pipe = r.pipeline(transaction=True)
    pipe.set(SLEEVE_KEY, json.dumps(payload, ensure_ascii=False), ex=TTL_SEC)
    pipe.set(LATEST_KEY, json.dumps(payload, ensure_ascii=False), ex=max(TTL_SEC, 300))
    pipe.set(HEARTBEAT_KEY, json.dumps({
        "ts": now_iso, "pid": os.getpid(), "ttl_sec": TTL_SEC, "version": VERSION,
    }, ensure_ascii=False), ex=HB_TTL_SEC)
    pipe.set(HEALTH_HB_KEY, json.dumps({
        "service": "ares-sleeve-writer",
        "status":  "OK",
        "pid":     os.getpid(),
        "timestamp": now_iso,
        "version": VERSION,
        "metrics": {
            "n_weights":  len(clipped),
            "asm":        asm,
            "v300_active": bool(v300),
            "s2_active":   bool(s2),
            "ap_active":   bool(ap),
            "ttl_sec":     TTL_SEC,
            "meta_source": bridge_metadata.get("source"),
        },
    }, ensure_ascii=False), ex=HB_TTL_SEC)
    pipe.set(FINAL_WEIGHTS_KEY, json.dumps(final_payload, ensure_ascii=False), ex=max(TTL_SEC, 300))
    pipe.execute()

    # Self-check
    try:
        back = r.get(SLEEVE_KEY)
        if not back:
            raise RuntimeError("self-check: SLEEVE_KEY missing immediately after SET")
        j = json.loads(back)
        if "weights" not in j or "metadata" not in j:
            raise RuntimeError(f"self-check: payload missing required fields: keys={list(j.keys())}")
        m = j["metadata"]
        for f in ("dd_20", "vix_z", "vol_z"):
            if f not in m:
                raise RuntimeError(f"self-check: metadata.{f} missing")
        payload["_self_check"] = "PASS"
    except Exception as e:
        payload["_self_check"] = f"FAIL:{e}"
        log.error("[WRITER_SELFCHECK_FAIL] %s", e)

    return payload


# --------------------------------------------------------------------------
# Main daemon
# --------------------------------------------------------------------------
_SHUTDOWN = False


def _sig(_signum, _frame):
    global _SHUTDOWN
    _SHUTDOWN = True


def main() -> int:
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    url = _load_redis_url()
    log.info("=" * 60)
    log.info("ARES Sleeve Writer v8.4 starting (VERSION=%s)", VERSION)
    log.info("  REDIS_URL:    %s", url.split("@")[-1])
    log.info("  SLEEVE_KEY:   %s (TTL %ds)", SLEEVE_KEY, TTL_SEC)
    log.info("  LATEST_KEY:   %s", LATEST_KEY)
    log.info("  HEARTBEAT_KEY:%s (TTL %ds)", HEARTBEAT_KEY, HB_TTL_SEC)
    log.info("  INTERVAL:     %ds   RAMP:%d min", INTERVAL_SEC, RAMP_MINUTES)
    log.info("  META_AUDIT:   %s (TTL %ds)", META_AUDIT_KEY, META_AUDIT_TTL_SEC)
    log.info("=" * 60)

    r = _safe_redis_connect(url)
    start = time.time()
    tick = 0
    consecutive_fail = 0

    while not _SHUTDOWN:
        tick += 1
        cycle_t0 = time.time()
        try:
            elapsed_min = (time.time() - start) / 60.0
            ramp = 0.5 + 0.5 * min(1.0, elapsed_min / RAMP_MINUTES) if elapsed_min < RAMP_MINUTES else 1.0

            payload = publish_once(r, ramp)
            consecutive_fail = 0

            if tick % 12 == 0 or tick <= 3:
                n_syms = len(payload.get("weights", {}))
                src    = payload.get("metadata", {}).get("source", "?")
                log.info(
                    "[PUBLISH] tick=%d asm=%s n=%d lambda_hint=%.2f meta_src=%s dd=%.3f vz=%.2f volz=%.2f v300=%s s2=%s ap=%s selfcheck=%s",
                    tick, payload["state"], n_syms, payload["lambda_hint"], src,
                    payload["metadata"]["dd_20"], payload["metadata"]["vix_z"], payload["metadata"]["vol_z"],
                    payload["v300_active"], payload["s2_active"], payload["ap_active"],
                    payload.get("_self_check", "?"),
                )

        except redis.RedisError as e:
            consecutive_fail += 1
            log.error("[REDIS_ERR] tick=%d fail=%d err=%s", tick, consecutive_fail, e)
            if consecutive_fail >= 3:
                try:
                    r = _safe_redis_connect(url)
                    log.info("[REDIS_RECONNECT] tick=%d", tick)
                    consecutive_fail = 0
                except Exception as rc_e:
                    log.error("[REDIS_RECONNECT_FAIL] %s", rc_e)
        except Exception as e:
            consecutive_fail += 1
            log.error("[CYCLE_ERR] tick=%d fail=%d err=%s", tick, consecutive_fail, e)

        cycle_elapsed = time.time() - cycle_t0
        sleep_for = max(1.0, INTERVAL_SEC - cycle_elapsed)
        slept = 0.0
        while slept < sleep_for and not _SHUTDOWN:
            time.sleep(min(1.0, sleep_for - slept))
            slept += 1.0

    log.info("Shutdown signal received; exiting cleanly after tick=%d", tick)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
