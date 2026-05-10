#!/usr/bin/env python3
"""
ssot_writer_v2_patched.py — SSOT V2 Writer with Engine Router Integration
===========================================================================
Drop-in replacement for ssot_writer_v2.py.
Adds:
  1. Engine Router integration (rollout key reading + engine selection + failover)
  2. ENGINE_FAILOVER event recording
  3. Rollout meta annotation in SSOT payload

Changes from original:
  - After transform_to_v2(), calls route_and_build() to apply engine logic
  - Falls back to base payload on any router error (safe)
"""
import json
import os
import sys
import time
import uuid
import redis

# ── Config ──
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
SRC_KEY = os.getenv("SSOT_SOURCE_KEY", os.getenv("SSOT_SRC_KEY", "ssot:target:v2:last_good"))
LAST_GOOD_KEY = os.getenv("SSOT_LAST_GOOD_KEY", "ssot:target:v2:last_good")
LEGACY_KEY = os.getenv("SSOT_LEGACY_KEY", "ssot:target:v1:current")
ALLOW_LEGACY_FALLBACK = os.getenv("SSOT_ALLOW_LEGACY_FALLBACK", "true").lower() == "true"
CANARY_KEY = os.getenv("SSOT_CANARY_KEY", "ssot:target:v2:canary")
CANARY_TTL = int(os.getenv("SSOT_CANARY_TTL", "300"))
ENGINE_VERSION = os.getenv("ENGINE_VERSION", "ares-nextgen-core-prod")
LOOP_INTERVAL = int(os.getenv("WRITER_INTERVAL_SEC", "30"))
ENABLE_ENGINE_ROUTER = os.getenv("ENABLE_ENGINE_ROUTER", "true").lower() == "true"

r = redis.from_url(REDIS_URL, decode_responses=True)


def log(level, msg, **kwargs):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    extra = json.dumps(kwargs, ensure_ascii=False) if kwargs else ""
    print(f"[{ts}] [{level}] ssot-writer-v2: {msg} {extra}", flush=True)


def _read_json_key(key: str):
    raw = r.get(key)
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception as e:
        log("ERROR", "JSON parse error", key=key, err=str(e))
        return None



def _read_gpu_regime():
    """Read GPU regime classifier status from Redis (shadow, non-blocking)."""
    try:
        meta = r.hgetall("gpu:regime:classifier:meta")
        if not meta:
            return {"status": "not_available"}
        return {
            "status": meta.get("status", "INACTIVE"),
            "mean_auc": float(meta.get("mean_auc", 0)),
            "model_hash": meta.get("model_hash", ""),
            "bound_at": meta.get("bound_at", ""),
            "device": meta.get("device", "cpu"),
        }
    except Exception:
        return {"status": "error"}

def read_source():
    obj = _read_json_key(SRC_KEY)
    if obj:
        return obj, SRC_KEY
    obj = _read_json_key(LAST_GOOD_KEY)
    if obj:
        return obj, LAST_GOOD_KEY
    if ALLOW_LEGACY_FALLBACK:
        obj = _read_json_key(LEGACY_KEY)
        if obj:
            return obj, LEGACY_KEY
    return None, None


def transform_to_v2(legacy):
    """Transform legacy payload to SSOT_TARGET_V2 format."""
    positions_raw = legacy.get("positions", [])
    positions_v2 = []
    if isinstance(positions_raw, list):
        for p in positions_raw:
            sym = p.get("s") or p.get("symbol") or ""
            w = float(p.get("w") or p.get("weight") or 0)
            side = p.get("side", "LONG" if w >= 0 else "SHORT")
            if sym:
                positions_v2.append({
                    "symbol": sym,
                    "w": round(w, 6),
                    "side": side,
                    "reason": p.get("reason", "")
                })
    elif isinstance(positions_raw, dict):
        for sym, w in positions_raw.items():
            w = float(w)
            positions_v2.append({
                "symbol": sym,
                "w": round(w, 6),
                "side": "LONG" if w >= 0 else "SHORT",
                "reason": ""
            })

    gross = float(legacy.get("gross", sum(abs(p["w"]) for p in positions_v2)))
    meta_raw = legacy.get("meta", {})
    if not isinstance(meta_raw, dict):
        meta_raw = {}

    # Read regime
    regime = "UNKNOWN"
    try:
        regime_raw = r.get("emarkos:v1:regime")
        if regime_raw:
            if regime_raw.startswith("{"):
                rd = json.loads(regime_raw)
                regime = rd.get("ms_state", rd.get("final_regime", regime_raw))
            else:
                regime = regime_raw
    except Exception:
        pass

    # Read VIX
    vix = 0.0
    try:
        vix_raw = r.get("price:VIX") or r.get("ctx:vix") or "0"
        vix = float(vix_raw)
    except Exception:
        pass

    # XGB risk
    xgb_risk = 0.0
    try:
        xgb_risk_raw = r.get("xgb:risk:status") or r.get("xgb:risk:portfolio") or "0"
        if str(xgb_risk_raw).startswith("{"):
            xo = json.loads(xgb_risk_raw)
            xgb_risk = float(xo.get("xgb_risk", xo.get("risk", 0.0)))
        else:
            xgb_risk = float(xgb_risk_raw)
    except Exception:
        pass

    router_weights = meta_raw.get("router_weights", {})
    if not router_weights:
        try:
            rw_raw = r.get("router:v12_3:weights")
            if rw_raw:
                router_weights = json.loads(rw_raw)
        except Exception:
            pass

    risk_scalar = float(meta_raw.get("risk_scalar", 1.0))
    cost_bps = float(meta_raw.get("cost_bps", 30.0))
    vol_target = float(meta_raw.get("vol_target", 0.18))
    universe = [p["symbol"] for p in positions_v2]
    universe_id = meta_raw.get("universe_id", "champion_v92")

    v2_payload = {
        "schema_version": "SSOT_TARGET_V2",
        "ts": int(time.time() * 1000),
        "asof": legacy.get("asof", time.strftime("%Y-%m-%d", time.gmtime())),
        "engine_version": ENGINE_VERSION,
        "correlation_id": str(uuid.uuid4()),
        "targets": {
            "gross": round(gross, 6),
            "positions": positions_v2,
            "meta": {
                "regime": regime,
                "vix": vix,
                "xgb_risk": xgb_risk,
                "router_weights": router_weights,
                "risk_scalar": risk_scalar,
                "cost_bps": cost_bps,
                "vol_target": vol_target,
                "universe_id": universe_id,
                "universe": universe,
                "portfolio_vol_est": float(meta_raw.get("portfolio_vol_est", 0.0) or 0.0),
                "master_scalar": float(meta_raw.get("master_scalar", 1.0) or 1.0),
                "regime_scalar": float(meta_raw.get("regime_scalar", 1.0) or 1.0),
                "rebalance_run_id": meta_raw.get("rebalance_run_id"),
                "router_version": meta_raw.get("router_version", ""),
                "dd_state": meta_raw.get("dd_state", {}),
                "warnings": meta_raw.get("warnings", []),
                "xgb_risk_shadow": float(meta_raw.get("xgb_risk_shadow", 0.0) or 0.0),
                "xgb_risk_shadow_source": meta_raw.get("xgb_risk_shadow_source", "shadow_not_available"),
                "source_key": SRC_KEY,
                # [GPU_REGIME] GPU Learning Lane regime classifier shadow
                "gpu_regime_classifier": _read_gpu_regime(),
                "source_ts": int(legacy.get("ts", 0) or 0),  # [SOURCE_TS] stale-source 검증용
            }
        }
    }
    return v2_payload


def apply_engine_router(v2_payload):
    """
    Apply engine router to transform v2 payload through selected engine.
    Safe: on any error, returns original payload unchanged.
    """
    if not ENABLE_ENGINE_ROUTER:
        return v2_payload

    try:
        # Add engines directory to path
        engines_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engines")
        if engines_dir not in sys.path:
            sys.path.insert(0, os.path.dirname(engines_dir))

        from engines.engine_router import route_and_build
        cycle_id = v2_payload.get("correlation_id", str(time.time()))
        result = route_and_build(r, v2_payload, cycle_id)
        return result
    except ImportError as e:
        log("WARN", f"Engine router import failed (running without router): {e}")
        return v2_payload
    except Exception as e:
        log("ERROR", f"Engine router error (safe fallback to base): {e}")
        return v2_payload


def publish_canary(v2_payload):
    payload_str = json.dumps(v2_payload, ensure_ascii=False, separators=(",", ":"))
    pipe = r.pipeline()
    pipe.set(CANARY_KEY, payload_str, ex=CANARY_TTL)
    pipe.xadd("audit:ssot_v2:writes", {
        "key": CANARY_KEY,
        "schema_version": "SSOT_TARGET_V2",
        "ts": str(v2_payload["ts"]),
        "n_positions": str(len(v2_payload["targets"]["positions"])),
        "gross": str(v2_payload["targets"]["gross"]),
        "correlation_id": v2_payload["correlation_id"],
        "engine_version": str(v2_payload.get("engine_version", "")),
        "source_ts": str(v2_payload.get("targets", {}).get("meta", {}).get("source_ts", 0)),
    }, maxlen=1000)
    pipe.execute()
    return len(v2_payload["targets"]["positions"])


def main_loop():
    log("INFO", "Starting SSOT V2 Writer (Engine Router Patched)",
        interval=LOOP_INTERVAL, canary_key=CANARY_KEY,
        src_key=SRC_KEY, fallback_key=LAST_GOOD_KEY,
        engine_router=ENABLE_ENGINE_ROUTER,
        legacy_fallback=ALLOW_LEGACY_FALLBACK)

    consecutive_empty = 0
    while True:
        try:
            obj, src = read_source()
            if not obj:
                consecutive_empty += 1
                if consecutive_empty % 10 == 1:
                    log("WARN", f"SSOT source empty (count={consecutive_empty})",
                        src_key=SRC_KEY, fallback_key=LAST_GOOD_KEY,
                        legacy_key=LEGACY_KEY)
                time.sleep(LOOP_INTERVAL)
                continue

            consecutive_empty = 0

            # If already V2, use directly
            if obj.get("schema_version") == "SSOT_TARGET_V2" and isinstance(obj.get("targets"), dict):
                v2 = obj
                # [SOURCE_TS] V2 path: preserve original ts as source_ts
                _orig_ts = obj.get("ts", 0)
                if "targets" in v2 and "meta" in v2["targets"]:
                    v2["targets"]["meta"]["source_ts"] = int(_orig_ts or 0)
                    # [GPU_REGIME] shadow
                    v2["targets"]["meta"]["gpu_regime_classifier"] = _read_gpu_regime()
            else:
                v2 = transform_to_v2(obj)

            # ★ ENGINE ROUTER INTEGRATION ★
            v2 = apply_engine_router(v2)

            # Always refresh ts and correlation_id
            v2["ts"] = int(time.time() * 1000)
            v2["correlation_id"] = str(uuid.uuid4())

            n = publish_canary(v2)
            engine_v = v2.get("targets", {}).get("meta", {}).get("engine_version", "base")
            source_ts = v2.get("targets", {}).get("meta", {}).get("source_ts", 0)
            log("INFO", f"Canary published: {n} positions, gross={v2['targets']['gross']:.4f}, engine={engine_v}, source_ts={source_ts}",
                source=src)

        except Exception as e:
            log("ERROR", f"Writer error: {e}")

        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main_loop()
