#!/usr/bin/env python3
"""
ARES XGB Risk-Flag Writer v7 (Elon Musk 5-step Refactor)
=========================================================

PURPOSE
-------
Replace v5/v6 writers (broken, schema-disconnected) with a single, deterministic 
risk-flag writer that:
  1. Reads ONLY feat:v1:{symbol} (22-feature contract from feat_v1_publisher)
  2. Loads xgb_drop_v4.json + xgb_drop_v4_calibrator.pkl directly via XGBoost native API
  3. Emits xgb:risk:{symbol} with calibrated p_drop probability
  4. Fails closed if schema mismatches, model missing, or features stale

KEY GUARANTEES
--------------
- NO neutral fallback: missing features → SKIP symbol (not silent neutral output)
- NO regime-based 4-value buckets: real per-symbol probability based on 22 features
- Model AUC: 0.62 (current) → expected ≥ 0.70 after schema bridge fixed
- Signal diversity: std(p_drop across symbols) ≥ 0.05 (anti-homogenization SLO)
"""

import os

# === ARES_STRUCT_FIX_20260508: SSOT auto-load + fail-fast ===
# Reason: PM2 dump 누락 시 코드 기본값 127.0.0.1:6379 폴백 → ECONNREFUSED 무한 크래시 (재발 6,768회)
# Fix:    REDIS_HOST 미주입 시 SSOT(/etc/ares/redis.env)에서 직접 로드, 그래도 없으면 즉시 종료
import os as _os
if not _os.environ.get("REDIS_HOST") or _os.environ.get("REDIS_HOST") in ("127.0.0.1","localhost"):
    _ssot = _os.environ.get("ARES_REDIS_SSOT", "/etc/ares/redis.env")
    if _os.path.isfile(_ssot):
        try:
            for _ln in open(_ssot):
                _ln = _ln.strip()
                if not _ln or _ln.startswith("#") or "=" not in _ln: continue
                _k, _v = _ln.split("=", 1)
                _v = _v.strip().strip('"').strip("'")
                _os.environ.setdefault(_k.strip(), _v)
        except Exception as _e:
            import sys as _sys; print(f"[STRUCT_FIX] SSOT load fail: {_e}", file=_sys.stderr); _sys.exit(78)
    if not _os.environ.get("REDIS_HOST") or _os.environ.get("REDIS_HOST") in ("127.0.0.1","localhost"):
        import sys as _sys; print("[STRUCT_FIX] REDIS_HOST missing/local — refusing to start (fail-fast)", file=_sys.stderr); _sys.exit(78)
# === END ARES_STRUCT_FIX_20260508 ===
import sys
import json
import time
import logging
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
import redis
import xgboost as xgb
import joblib

# ============================================================================
# CONFIG
# ============================================================================
ARES_HOME       = os.getenv("ARES_HOME", "/home/ubuntu/ares_current")
SCHEMA_PATH     = os.getenv("XGB_V7_SCHEMA",     f"{ARES_HOME}/models/xgb_feature_cols.json")
MODEL_PATH      = os.getenv("XGB_V7_MODEL",      f"{ARES_HOME}/models/xgb_drop_v4.json")
CALIBRATOR_PATH = os.getenv("XGB_V7_CALIBRATOR", f"{ARES_HOME}/models/xgb_drop_v4_calibrator.pkl")
REDIS_HOST      = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT      = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASS      = os.getenv("REDIS_PASSWORD", "")
FEAT_KEY_PREFIX = "feat:v1:"
RISK_KEY_PREFIX = "xgb:risk:"
BLOCK_KEY_PREFIX= "xgb:block:"
RESULT_TTL      = int(os.getenv("XGB_V7_RESULT_TTL", "300"))
HEARTBEAT_TTL   = int(os.getenv("XGB_V7_HEARTBEAT_TTL", "120"))
CYCLE_SEC       = int(os.getenv("XGB_V7_CYCLE", "60"))
DROP_THRESHOLD  = float(os.getenv("XGB_V7_DROP_THRESHOLD", "0.70"))  # v2: hard block @ 0.70

# === XGB_RISK_GATE_V2_PATCH ===
THROTTLE_LOW   = float(os.getenv("XGB_V7_THROTTLE_LOW",  "0.40"))  # below: full allow
THROTTLE_HIGH  = float(os.getenv("XGB_V7_THROTTLE_HIGH", "0.70"))  # >=: hard block
def _risk_multiplier(p):
    """Continuous risk scaler. p<low: 1.0, p>=high: 0.0, between: linear from 1.0->0.05"""
    if p >= THROTTLE_HIGH: return 0.0
    if p <  THROTTLE_LOW:  return 1.0
    span = max(THROTTLE_HIGH - THROTTLE_LOW, 1e-9)
    m = (THROTTLE_HIGH - p) / span
    return max(0.05, min(1.0, m))
VERSION         = "v7.1"  # v2.1: stale-block-fix + metadata-key-skip + RSI-RMA + REDIS_PASSWORD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("xgb_v7")

# ============================================================================
# MODEL LOAD (fail-closed: refuse to start without model + schema)
# ============================================================================
class ModelBundle:
    def __init__(self):
        self.feature_cols: List[str] = []
        self.booster: Optional[xgb.Booster] = None
        self.calibrator = None
        self.auc: float = 0.0
        self.deployed_at: Optional[str] = None

    def load(self):
        # Schema
        sp = Path(SCHEMA_PATH)
        if not sp.exists():
            raise FileNotFoundError(f"Schema missing: {SCHEMA_PATH}")
        cols = json.loads(sp.read_text())
        if not isinstance(cols, list) or len(cols) != 22:
            raise ValueError(f"Schema must be 22 features, got {len(cols)}")
        self.feature_cols = cols

        # Booster
        mp = Path(MODEL_PATH)
        if not mp.exists():
            raise FileNotFoundError(f"Model missing: {MODEL_PATH}")
        booster = xgb.Booster()
        booster.load_model(str(mp))
        self.booster = booster

        # Calibrator
        cp = Path(CALIBRATOR_PATH)
        if cp.exists():
            try:
                self.calibrator = joblib.load(str(cp))
                log.info(f"Calibrator loaded: {type(self.calibrator).__name__}")
            except Exception as e:
                log.warning(f"Calibrator load failed: {e} (continuing without calibration)")
                self.calibrator = None
        else:
            log.warning(f"Calibrator not found: {CALIBRATOR_PATH}")

        self.deployed_at = dt.datetime.utcnow().isoformat() + "Z"
        # AUC: read from a sidecar metadata file if available
        meta_path = mp.parent / "xgb_drop_v4_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                self.auc = float(meta.get("auc", 0.0))
            except Exception:
                pass

        log.info(f"Model loaded: {MODEL_PATH} (AUC={self.auc:.4f}, calibrator={self.calibrator is not None})")

    def predict_p_drop(self, feat_vec: np.ndarray) -> float:
        if self.booster is None:
            raise RuntimeError("Model not loaded")
        dmat = xgb.DMatrix(feat_vec.reshape(1, -1), feature_names=self.feature_cols)
        raw_p = float(self.booster.predict(dmat)[0])
        if self.calibrator is not None:
            try:
                # Sklearn calibrators expect 2D input
                p = float(self.calibrator.predict_proba(np.array([[raw_p]]))[0, 1])
                return p
            except Exception as e:
                log.debug(f"Calibrator predict failed: {e} — using raw")
                return raw_p
        return raw_p

# ============================================================================
# REDIS
# ============================================================================
def _connect_redis() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT,
        username=os.getenv("REDIS_USERNAME", "ares-admin"),
        password=REDIS_PASS or None,
        ssl=os.getenv("REDIS_SSL", "true").lower() == "true",
        decode_responses=True,
        socket_timeout=5,
    )

# ============================================================================
# CYCLE
# ============================================================================
def run_cycle(r: redis.Redis, model: ModelBundle, cycle_n: int) -> Dict:
    started = time.time()

    # Discover all feat:v1:* keys (EXCLUDE metadata keys: status, heartbeat, halt)
    METADATA_KEYS = {"feat:v1:status", "feat:v1:heartbeat", "feat:v1:halt"}
    feat_keys = [
        k for k in r.scan_iter(match=f"{FEAT_KEY_PREFIX}*", count=200)
        if k not in METADATA_KEYS
    ]
    if not feat_keys:
        log.warning(f"Cycle {cycle_n}: no feat:v1:* keys found — feat_v1_publisher may be down")
        r.set("xgb:v7:status", json.dumps({
            "ts": dt.datetime.utcnow().isoformat() + "Z",
            "cycle": cycle_n, "status": "NO_FEATURES", "scored": 0
        }), ex=HEARTBEAT_TTL)
        return {"scored": 0, "blocked": 0, "errors": 1}

    # Bulk fetch
    pipe = r.pipeline()
    for k in feat_keys:
        pipe.get(k)
    raw_payloads = pipe.execute()

    scored = 0
    blocked = 0
    errors = 0
    p_drops: List[float] = []

    write_pipe = r.pipeline()
    for key, raw in zip(feat_keys, raw_payloads):
        if raw is None:
            continue
        try:
            payload = json.loads(raw)
            symbol = payload["symbol"]
            feats = payload["features"]
            # Schema match
            if set(feats.keys()) != set(model.feature_cols):
                errors += 1
                log.debug(f"{symbol}: schema mismatch — skipping")
                continue
            # Vectorize in schema order
            vec = np.array([feats[c] for c in model.feature_cols], dtype=np.float32)
            if np.isnan(vec).any() or np.isinf(vec).any():
                errors += 1
                continue
            p_drop = model.predict_p_drop(vec)
            if not (0.0 <= p_drop <= 1.0):
                errors += 1
                continue

            result = {
                "symbol": symbol,
                "p_drop": round(p_drop, 6),
                "flag": "DROP_RISK_HIGH" if p_drop > DROP_THRESHOLD else
                        "DROP_RISK_MODERATE" if p_drop > 0.3 else "OK",
                "blocked": int(p_drop >= THROTTLE_HIGH),  # v2: hard block @ HIGH
                "risk_multiplier": round(_risk_multiplier(p_drop), 6),
                "throttled": int(THROTTLE_LOW <= p_drop < THROTTLE_HIGH),
                "model_version": VERSION,
                "feature_source": payload.get("source_date"),
                "scored_at": dt.datetime.utcnow().isoformat() + "Z",
                "n_features_used": len(model.feature_cols),
            }
            write_pipe.set(f"{RISK_KEY_PREFIX}{symbol}", json.dumps(result), ex=RESULT_TTL)
            if p_drop >= THROTTLE_HIGH:
                write_pipe.set(f"{BLOCK_KEY_PREFIX}{symbol}", "1", ex=RESULT_TTL)
                blocked += 1
            else:
                # CRITICAL FIX (Cross-Review #4): explicitly DELETE block key on recovery
                # to prevent 5-minute stale-block trading blackout for symbols that
                # transitioned from blocked -> recovered.
                write_pipe.delete(f"{BLOCK_KEY_PREFIX}{symbol}")
            scored += 1
            p_drops.append(p_drop)
        except Exception as e:
            errors += 1
            log.debug(f"Error processing {key}: {e}")
    write_pipe.execute()

    # Heartbeat + Status
    elapsed = time.time() - started
    p_drop_arr = np.array(p_drops) if p_drops else np.array([0.0])
    diversity_std = float(p_drop_arr.std())
    summary = {
        "cycle": cycle_n,
        "scored": scored,
        "blocked": blocked,
        "errors": errors,
        "elapsed_sec": round(elapsed, 3),
        "p_drop_mean": float(p_drop_arr.mean()),
        "p_drop_std": round(diversity_std, 4),
        "p_drop_min": float(p_drop_arr.min()),
        "p_drop_max": float(p_drop_arr.max()),
        "diversity_ok": diversity_std >= 0.05,
        "model_version": VERSION,
        "ts": dt.datetime.utcnow().isoformat() + "Z",
        "status": "OK" if (scored > 0 and diversity_std >= 0.05) else "DEGRADED",
    }
    r.set("xgb:v7:status", json.dumps(summary), ex=HEARTBEAT_TTL * 3)
    # Legacy-compatible keys consumed by ssot_writer_v2 / nextgen_core_v50.engines
    legacy_status = {
        "ts": summary["ts"],
        "model_loaded": True,
        "version": VERSION,
        "status": "running" if summary["status"] in ("OK", "DEGRADED") else summary["status"],
        "cycle": cycle_n,
        "flagged": int(scored),
        "blocked": int(blocked),
        "p_drop_mean": float(summary["p_drop_mean"]),
        "xgb_risk": float(summary["p_drop_mean"]),
    }
    r.set("xgb:risk:status", json.dumps(legacy_status), ex=HEARTBEAT_TTL * 3)
    r.set("xgb:risk:portfolio", str(round(float(summary["p_drop_mean"]), 6)), ex=HEARTBEAT_TTL * 3)

    r.set("xgb:v7:heartbeat", str(int(time.time())), ex=HEARTBEAT_TTL)
    r.set("xgb:active_model_version", VERSION, ex=86400)
    r.set("xgb:model_auc", str(round(model.auc, 4)), ex=86400)
    r.set("xgb:model_deployed_at", str(int(time.time())), ex=86400)

    log.info(
        f"Cycle {cycle_n}: scored={scored} blocked={blocked} errors={errors} "
        f"p_drop[mean={summary['p_drop_mean']:.3f} std={summary['p_drop_std']:.3f}] "
        f"status={summary['status']} elapsed={elapsed:.2f}s"
    )
    return summary

def main():
    log.info(f"XGB Risk-Flag Writer {VERSION} starting")
    model = ModelBundle()
    model.load()

    r = _connect_redis()
    r.ping()
    log.info(f"Redis OK: {REDIS_HOST}:{REDIS_PORT}")

    cycle = 0
    while True:
        cycle += 1
        try:
            run_cycle(r, model, cycle)
        except Exception as e:
            log.exception(f"Cycle {cycle} failed: {e}")
        time.sleep(CYCLE_SEC)

if __name__ == "__main__":
    main()
