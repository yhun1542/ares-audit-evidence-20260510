# PATH: /home/ubuntu/ares_current/external/regime_smoother.py
# PATCH: V3 — Regime Smoother (gradual exposure adjustment, prevents 41%->18% cliff-edge)
# DEPLOY: pm2 start regime_smoother.py --name regime-smoother --interpreter python3
# ROLLBACK: pm2 delete regime-smoother

"""
ARES — Regime Exposure Smoother v1.0.0 (PATCH V3)
=============================================================================
목적: 레짐 전환 시 시장 노출도가 한 번에 41% → 18%로 급변하는 것을 방지.
     점진적(EMA 기반) 노출도 조정으로 Cliff-edge 손실을 완화.

입력:
  - regime:final:current (or regime:consensus:current) - 현재 레짐
  - sleeve:allocation:current - sleeve_allocator의 노출도 출력
출력:
  - regime:exposure:smoothed - 평활화된 노출도 (sleeve_allocator가 읽어옴)
  - regime:exposure:smoothed:meta - 메타데이터

알고리즘:
  smoothed_exposure = α * target_exposure + (1-α) * prev_exposure
  α (smoothing factor):
    - 평상시 (NORMAL/BULL/CALM): 0.10 (=10%/cycle, 약 30분 만에 90% 수렴)
    - 위험 회피 방향 (BEAR/CRISIS): 0.30 (빠르게 축소)
    - 회복 방향 (RECOVERY/CALM after BEAR): 0.05 (천천히 증가, 보수적)

Cycle: 30초 (sleeve_allocator와 동일)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import redis

# Logging
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("regime_smoother")

# Redis setup
REDIS_URL = os.getenv("REDIS_URL") or os.getenv("ARES_REDIS_URL")
REDIS_PASS = os.environ.get("REDIS_PASSWORD")
if not REDIS_URL or "localhost" in REDIS_URL or "127.0.0.1" in REDIS_URL:
    _env_file = "/etc/ares/ares.env"
    try:
        with open(_env_file) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("#") or "=" not in _line:
                    continue
                _key, _val = _line.split("=", 1)
                _key = _key.strip()
                _val = _val.strip().strip('"').strip("'")
                if _key == "REDIS_URL" and _val:
                    REDIS_URL = _val
                elif _key == "REDIS_PASSWORD" and _val and not REDIS_PASS:
                    REDIS_PASS = _val
    except Exception:
        pass

_SELF_TEST = "--self-test" in sys.argv
if not _SELF_TEST and not REDIS_URL:
    raise RuntimeError("REDIS_URL or ARES_REDIS_URL is required")

CYCLE_INTERVAL = int(os.getenv("REGIME_SMOOTHER_CYCLE_SEC", "30"))

# Smoothing factors (alpha)
ALPHA_NORMAL = 0.10
ALPHA_RISK_OFF_FAST = 0.30
ALPHA_RECOVERY_SLOW = 0.05

# Regime → Target Exposure (ratio of NAV to be invested)
REGIME_TARGET_EXPOSURE = {
    "BULL":       0.95,
    "NORMAL":     0.85,
    "CALM":       0.80,
    "NEUTRAL":    0.75,
    "RECOVERY":   0.70,
    "TRANSITION": 0.55,
    "CAUTION":    0.45,
    "BEAR":       0.30,
    "RISK_OFF":   0.20,
    "CRISIS":     0.10,
    "CRASH":      0.05,
}

REGIME_RISK_LEVEL = {
    "BULL": 0, "NORMAL": 1, "CALM": 1, "NEUTRAL": 2, "RECOVERY": 2,
    "TRANSITION": 3, "CAUTION": 4, "BEAR": 5, "RISK_OFF": 6, "CRISIS": 7, "CRASH": 8,
}

REGIME_CURRENT_KEY = "regime:final:current"
REGIME_CONSENSUS_KEY = "regime:consensus:current"
REGIME_FALLBACK_KEY = "regime:current"
SMOOTHED_KEY = "regime:exposure:smoothed"
SMOOTHED_META_KEY = "regime:exposure:smoothed:meta"


def _push_health_metric(r, key: str, value, labels: dict | None = None):
    try:
        metric = {"value": value, "ts": time.time(), "labels": labels or {}}
        r.set(f"metrics:{key}", json.dumps(metric), ex=60)
    except Exception:
        pass


def _get_redis():
    return redis.Redis.from_url(REDIS_URL, password=REDIS_PASS, decode_responses=True, socket_timeout=5)


def _read_regime(r) -> str:
    """Read the current regime from Redis (fallback chain). Supports both string-JSON and hash types."""
    for key in (REGIME_CURRENT_KEY, REGIME_CONSENSUS_KEY, REGIME_FALLBACK_KEY):
        try:
            # [PATCH-V3.1] Detect type and read accordingly (operational keys are hash)
            try:
                ktype = r.type(key)
            except Exception:
                ktype = "none"
            obj = None
            if ktype == "hash":
                hdata = r.hgetall(key)
                if hdata:
                    obj = hdata  # plain dict from hgetall
            elif ktype == "string":
                raw = r.get(key)
                if raw:
                    try:
                        obj = json.loads(raw)
                    except Exception:
                        obj = {"regime": str(raw)}
            if obj:
                try:
                    regime = (obj.get("final_regime") or obj.get("consensus") or
                              obj.get("regime") or obj.get("r15_regime") or
                              obj.get("name") or obj.get("current"))
                    if regime:
                        return str(regime).upper()
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception as e:
            log.warning("Failed to read %s: %s", key, e)
    return "NEUTRAL"


def _read_prev_smoothed(r) -> tuple[float, str]:
    """Read previously smoothed exposure. Returns (exposure, prev_regime)."""
    try:
        raw = r.get(SMOOTHED_KEY)
        if raw:
            obj = json.loads(raw)
            return float(obj.get("exposure", 0.75)), str(obj.get("regime", "NEUTRAL"))
    except Exception:
        pass
    return 0.75, "NEUTRAL"  # Default neutral starting point


def _choose_alpha(prev_regime: str, new_regime: str) -> float:
    """Choose smoothing factor based on direction of change."""
    prev_risk = REGIME_RISK_LEVEL.get(prev_regime, 2)
    new_risk = REGIME_RISK_LEVEL.get(new_regime, 2)
    if new_risk > prev_risk:
        # Moving to riskier regime → adjust faster (defensive)
        return ALPHA_RISK_OFF_FAST
    elif new_risk < prev_risk:
        # Recovering → adjust slower (conservative re-entry)
        return ALPHA_RECOVERY_SLOW
    else:
        return ALPHA_NORMAL


def smooth_exposure(prev_exposure: float, target_exposure: float, alpha: float) -> float:
    """Apply EMA smoothing."""
    return alpha * target_exposure + (1.0 - alpha) * prev_exposure


def run_cycle(r):
    new_regime = _read_regime(r)
    target_exposure = REGIME_TARGET_EXPOSURE.get(new_regime, 0.75)

    prev_exposure, prev_regime = _read_prev_smoothed(r)
    alpha = _choose_alpha(prev_regime, new_regime)

    smoothed = smooth_exposure(prev_exposure, target_exposure, alpha)
    smoothed = max(0.05, min(0.95, smoothed))  # Clamp

    payload = {
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "regime": new_regime,
        "prev_regime": prev_regime,
        "target_exposure": target_exposure,
        "prev_exposure": prev_exposure,
        "exposure": smoothed,
        "alpha": alpha,
        "delta": smoothed - prev_exposure,
        "version": "1.0.0",
    }
    r.set(SMOOTHED_KEY, json.dumps(payload))
    r.set(SMOOTHED_META_KEY, json.dumps(payload))
    _push_health_metric(r, "regime_smoother_exposure", smoothed,
                        {"regime": new_regime, "alpha": alpha})

    log.info(
        "[smoother] regime=%s target=%.3f prev=%.3f smoothed=%.3f α=%.2f Δ=%+.4f",
        new_regime, target_exposure, prev_exposure, smoothed, alpha, smoothed - prev_exposure,
    )


def main():
    if _SELF_TEST:
        # Test: NORMAL -> CAUTIOUS transition should not cliff
        prev_exp = 0.85  # NORMAL
        for i in range(10):
            new = smooth_exposure(prev_exp, 0.45, ALPHA_RISK_OFF_FAST)
            print(f"  cycle {i+1}: prev={prev_exp:.4f} → smoothed={new:.4f} (target 0.45)")
            prev_exp = new
        # After ~7 cycles (3.5min) we should be near target
        assert prev_exp < 0.55, f"Smoothing too slow: {prev_exp}"
        assert prev_exp > 0.40, f"Smoothing overshoot: {prev_exp}"
        # Inverse: CAUTIOUS -> NORMAL recovery (should be slow)
        prev_exp = 0.45
        for i in range(5):
            new = smooth_exposure(prev_exp, 0.85, ALPHA_RECOVERY_SLOW)
            print(f"  recovery cycle {i+1}: prev={prev_exp:.4f} → smoothed={new:.4f}")
            prev_exp = new
        assert prev_exp < 0.65, f"Recovery too fast: {prev_exp}"
        print("[PASS] regime_smoother self-test: cliff prevented + slow recovery")
        return 0

    r = _get_redis()
    log.info("Regime Smoother v1.0.0 starting (cycle=%ds)", CYCLE_INTERVAL)
    while True:
        try:
            run_cycle(r)
        except Exception as e:
            log.error("smoother cycle error: %s", e, exc_info=True)
        time.sleep(CYCLE_INTERVAL)


if __name__ == "__main__":
    sys.exit(main() or 0)
