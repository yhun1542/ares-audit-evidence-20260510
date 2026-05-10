# PATH: /home/ubuntu/ares_current/external/sleeve_allocator.py
# PATCH: V3 — Half-Kelly + Risk-Parity hybrid weighting (replaces equal-weight bug)
# ROLLBACK: cp sleeve_allocator.py.bak sleeve_allocator.py && pm2 restart sleeve-allocator

"""
ARES — Sleeve Allocator v4.3.0 (PATCH V3: Half-Kelly Risk-Parity Hybrid)
=============================================================================
v4.3.0 변경사항 (vs v4.2.1):
  [PATCH-V3-1] allocate_flow_sleeve: Half-Kelly + InvVol hybrid weighting (균등 가중 버그 수정)
  [PATCH-V3-2] Feature flag: ARES_PATCH_V3_ENABLE=true 로 ON/OFF (default OFF = 기존 로직)
  [PATCH-V3-3] Volatility 조회: feat:v2:vol:realized / feat:v1:realized_vol Redis fallback
  [PATCH-V3-4] Regime smoother integration: regime:exposure:smoothed 키 읽기
  [PATCH-V3-5] sleeve:ares:weights 출력 추가
  [PATCH-V3-6] Self-test: python sleeve_allocator.py --self-test

이전 버전 모든 변경사항 유지 (v4.2.1, v4.1, v4.0)
경로: external/sleeve_allocator.py
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import redis
from datetime import datetime, timezone

# ── Logging ──────────────────────────────────────────────────────────────
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("sleeve_allocator")

# ── [PATCH-V3-2] Feature Flag ───────────────────────────────────────────
PATCH_V3_ENABLED = os.getenv("ARES_PATCH_V3_ENABLE", "false").lower() == "true"

# ── [PATCH-V3-3] Volatility Constants ───────────────────────────────────
MIN_VOL = 0.005          # 0.5% daily vol floor
DEFAULT_VOL = 0.02       # 2% daily vol fallback when no data
MAX_SINGLE_WEIGHT = 0.04 # 4% single stock cap
MIN_SINGLE_WEIGHT = 0.005 # 0.5% single stock floor

# ── Redis ────────────────────────────────────────────────────────────────
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
                    log.info("[v4.2-FIX] REDIS_URL loaded from %s", _env_file)
                elif _key == "REDIS_PASSWORD" and _val and not REDIS_PASS:
                    REDIS_PASS = _val
                    log.info("[v4.2-FIX] REDIS_PASSWORD loaded from %s", _env_file)
    except FileNotFoundError:
        log.warning("[v4.2-FIX] %s not found, cannot fallback", _env_file)
    except Exception as _e:
        log.warning("[v4.2-FIX] Failed to parse %s: %s", _env_file, _e)

# [PATCH-V3] Allow --self-test to run without Redis
_SELF_TEST_MODE = "--self-test" in sys.argv

if not _SELF_TEST_MODE:
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL or ARES_REDIS_URL is required (env + /etc/ares/ares.env both failed)")
    if "localhost" in REDIS_URL or "127.0.0.1" in REDIS_URL:
        raise RuntimeError("Local Redis fallback is forbidden in production (REDIS_URL=%s)" % REDIS_URL[:60])

CYCLE_INTERVAL = int(os.getenv("SLEEVE_CYCLE_SEC", "30"))


def _parse_ts(val, default: float = 0.0) -> float:
    """[v4.2-FIX] Parse timestamp that may be epoch float, epoch int, or ISO 8601 string."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return default
        try:
            return float(val)
        except ValueError:
            pass
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                dt = datetime.strptime(val, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except ValueError:
                continue
    return default


# ── Redis Keys ───────────────────────────────────────────────────────────
REGIME_CONSENSUS_KEY = "regime:consensus:current"
REGIME_FINAL_KEY     = "regime:final:current"
REGIME_FALLBACK_KEY  = "regime:current"
ALPHA_HEALTH_KEY     = "alpha:health:current"
SSOT_TARGET_KEY      = "ssot:target:v2:current"
ALPHA_SCORE_PATTERN  = "alpha:{}:score"
SIGNAL_ALPHA_PATTERN = "signal:{}:alpha_score"

KPI_REJECT_KEY       = "kpi:reject"
EXEC_RECONCILE_PATTERN = "exec:reconcile:last:{}"

# [PATCH-V3-3] Volatility Redis keys
VOL_V2_PATTERN       = "feat:v2:vol:realized:{}"     # Primary
VOL_V1_PATTERN       = "feat:v1:realized_vol:{}"     # Fallback
VOL_BULK_KEY         = "feat:v2:vol:realized"         # Bulk hash (if available)
VOL_V1_BULK_KEY      = "feat:v1:realized_vol"         # Bulk hash fallback

# [PATCH-V3-4] Regime smoother key
REGIME_SMOOTHED_KEY  = "regime:exposure:smoothed"

# Output keys (v3 호환 유지)
SLEEVE_MIX_KEY           = "sleeve:allocation:current"
SLEEVE_V3_ALLOC_KEY      = "sleeve:v3:allocation:current"
SLEEVE_V3_DETAILS_KEY    = "sleeve:v3:details:current"
SLEEVE_V3_FINAL_KEY      = "sleeve:v3:final_weights:current"
SLEEVE_FINAL_WEIGHTS_KEY = "sleeve:final_weights:current"
# [PATCH-V3-5] ARES weights key
SLEEVE_ARES_KEY          = "sleeve:ares:weights"

VERSION = "4.3.5"  # V3.5 hotfix: Power amplification (k=4) for tight alpha distributions

# [PATCH-V3-7] Prometheus-style health metric (Redis-backed, 60s TTL)
def _push_health_metric(r, key: str, value, labels: dict | None = None):
    try:
        metric = {
            "value": value,
            "ts": time.time(),
            "labels": labels or {},
        }
        r.set(f"metrics:{key}", json.dumps(metric), ex=60)
    except Exception:
        pass  # Non-critical, silent fail

# ── Constants ────────────────────────────────────────────────────────────
REJECT_WARN_THRESHOLD  = 0.02
REJECT_DANGER_THRESHOLD = 0.05
ALPHA_DEGRADED_RATIO   = 0.50
STALE_REGIME_SEC       = 300
STALE_ALPHA_SEC        = 600
STALE_ALPHA_PCT_THRESHOLD = 0.30
STALE_REJECT_SEC       = 600
STALE_RECONCILE_SEC    = 300

REGIME_WEIGHTS = {
    "BULL":       {"core": 0.70, "defensive": 0.05, "flow_event": 0.25},
    "NORMAL":     {"core": 0.65, "defensive": 0.10, "flow_event": 0.25},
    "CALM":       {"core": 0.60, "defensive": 0.15, "flow_event": 0.25},
    "NEUTRAL":    {"core": 0.60, "defensive": 0.15, "flow_event": 0.25},
    "RECOVERY":   {"core": 0.60, "defensive": 0.15, "flow_event": 0.25},
    "TRANSITION": {"core": 0.55, "defensive": 0.20, "flow_event": 0.25},
    "CAUTION":    {"core": 0.50, "defensive": 0.25, "flow_event": 0.25},
    "BEAR":       {"core": 0.40, "defensive": 0.40, "flow_event": 0.20},
    "RISK_OFF":   {"core": 0.30, "defensive": 0.50, "flow_event": 0.20},
    "CRISIS":     {"core": 0.20, "defensive": 0.70, "flow_event": 0.10},
    "CRASH":      {"core": 0.20, "defensive": 0.70, "flow_event": 0.10},
}

CONSERVATIVE_PRIORITY = {
    "CRASH": 7, "CRISIS": 6, "RISK_OFF": 5, "BEAR": 4,
    "CAUTION": 3, "TRANSITION": 2, "NORMAL": 1, "NEUTRAL": 1, "CALM": 1,
    "RECOVERY": 1, "BULL": 0, "BULLISH": 0,
}

SAFE_MODE_CASH_ETFS = ["BIL", "SHV", "SGOV"]
MAX_DEFENSIVE_N = 12
MAX_FLOW_N = 10


# ── Enums & Dataclasses ─────────────────────────────────────────────────

class AllocatorState(Enum):
    VERIFIED    = "VERIFIED"
    PROVISIONAL = "PROVISIONAL"
    DEGRADED    = "DEGRADED"
    SAFE_MODE   = "SAFE_MODE"
    HALT        = "HALT"


@dataclass
class ExecutionFeedback:
    reject_rate: float = 0.0
    stuck_symbols: list[str] = field(default_factory=list)
    reject_data_stale: bool = False


@dataclass
class InputHealth:
    regime_source: str = "none"
    regime_stale: bool = False
    regime_age_sec: float = 0.0
    alpha_available_pct: float = 1.0
    alpha_stale: bool = False
    alpha_stale_count: int = 0
    alpha_total_count: int = 0
    ssot_available: bool = True


@dataclass
class AllocationResult:
    final_weights: dict[str, float]
    sleeve_mix: dict[str, float]
    sleeve_details: dict[str, dict[str, float]]
    state: AllocatorState
    residual_pct: float
    reject_rate: float
    stuck_symbols: list[str]
    reason: str
    weight_method: str = "equal"  # [PATCH-V3] Metadata


# ── Core Allocator ───────────────────────────────────────────────────────

class SleeveAllocatorV4:
    """
    First Principles Sleeve Allocator v4.3.0
    [PATCH-V3] Half-Kelly + Risk-Parity hybrid for flow sleeve
    """

    def determine_state(
        self,
        input_health: InputHealth,
        exec_feedback: ExecutionFeedback,
    ) -> tuple[AllocatorState, str]:
        if not input_health.ssot_available:
            return AllocatorState.SAFE_MODE, "SSOT target unavailable"
        if exec_feedback.reject_rate >= REJECT_DANGER_THRESHOLD:
            return AllocatorState.SAFE_MODE, f"reject_rate={exec_feedback.reject_rate:.1%} >= {REJECT_DANGER_THRESHOLD:.0%}"

        alpha_pct = input_health.alpha_available_pct
        if alpha_pct < ALPHA_DEGRADED_RATIO:
            return AllocatorState.DEGRADED, (
                f"alpha_fresh={alpha_pct:.0%} < {ALPHA_DEGRADED_RATIO:.0%} "
                f"(stale={input_health.alpha_stale_count}/{input_health.alpha_total_count})"
            )

        provisional_triggers = []
        soft_flags = []

        if input_health.regime_stale:
            provisional_triggers.append(f"regime_stale({input_health.regime_age_sec:.0f}s)")
        if exec_feedback.reject_rate >= REJECT_WARN_THRESHOLD:
            provisional_triggers.append(f"reject_rate={exec_feedback.reject_rate:.1%}")
        if input_health.regime_source not in ("consensus",):
            provisional_triggers.append(f"regime_non_consensus({input_health.regime_source})")
        if exec_feedback.reject_data_stale:
            soft_flags.append("reject_data_stale")

        if provisional_triggers:
            all_reasons = provisional_triggers + soft_flags
            return AllocatorState.PROVISIONAL, "; ".join(all_reasons)

        return AllocatorState.VERIFIED, "all_inputs_valid"

    def calculate_base_weights(
        self, state: AllocatorState, regime: str
    ) -> dict[str, float]:
        base = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["NEUTRAL"]).copy()

        if state == AllocatorState.SAFE_MODE:
            return {"core": 0.0, "defensive": 1.0, "flow_event": 0.0}
        if state == AllocatorState.DEGRADED:
            base["defensive"] += base["flow_event"]
            base["flow_event"] = 0.0
        if state == AllocatorState.PROVISIONAL:
            downgraded = self._downgrade_regime(regime)
            conservative_base = REGIME_WEIGHTS.get(downgraded, base)
            base = conservative_base.copy()

        return base

    @staticmethod
    def _downgrade_regime(regime: str) -> str:
        order = ["BULL", "CALM", "NEUTRAL", "RECOVERY", "TRANSITION",
                 "CAUTION", "BEAR", "RISK_OFF", "CRISIS", "CRASH"]
        try:
            idx = order.index(regime)
            return order[min(idx + 1, len(order) - 1)]
        except ValueError:
            return "TRANSITION"

    # ══════════════════════════════════════════════════════════════════
    # [PATCH-V3-1] allocate_flow_sleeve — Half-Kelly + InvVol hybrid
    # ══════════════════════════════════════════════════════════════════
    def allocate_flow_sleeve(
        self,
        alpha_data: dict[str, float],
        conf_data: dict[str, float],
        stuck_symbols: list[str],
        flow_weight: float,
        max_n: int = MAX_FLOW_N,
        vol_data: Optional[dict[str, float]] = None,  # [PATCH-V3] Optional, backward compat
    ) -> tuple[dict[str, float], float]:
        """
        [PATCH-V3-1] Select top-N alpha stocks with Half-Kelly + Risk-Parity weighting.

        When ARES_PATCH_V3_ENABLE=true AND vol_data is provided:
          score_i     = max(alpha_i, 0) * conf_i
          variance_i  = vol_i^2
          half_kelly  = clip(score_i / variance_i, 0, 0.5)
          inv_vol_i   = 1 / max(vol_i, MIN_VOL)
          combined_i  = (0.6 * half_kelly + 0.4 * norm_inv_vol_i) * conf_i
          weight_i    = (combined_i / sum_combined) * flow_weight
          Clipped to [MIN_SINGLE_WEIGHT, MAX_SINGLE_WEIGHT] per stock.

        When ARES_PATCH_V3_ENABLE=false (default): original equal-weight logic.

        Returns (flow_weights_dict, residual_weight).
        """
        if flow_weight <= 0:
            return {}, 0.0

        # ── Step 1: Score & filter (unchanged logic) ─────────────────
        scored: dict[str, float] = {}
        for sym, alpha in alpha_data.items():
            if sym in stuck_symbols:
                continue
            conf = conf_data.get(sym, 0.5)
            score = max(alpha, 0) * conf
            if score > 0.01:
                scored[sym] = score

        if not scored:
            return {}, flow_weight

        top_items = sorted(scored.items(), key=lambda x: -x[1])[:max_n]

        # ── Step 2: Feature flag gate ────────────────────────────────
        if not PATCH_V3_ENABLED or vol_data is None:
            # ORIGINAL equal-weight logic (기존 동작 100% 보존)
            n_actual = len(top_items)
            per_stock = flow_weight / max_n
            allocated_weight = per_stock * n_actual
            residual = flow_weight - allocated_weight
            flow_weights = {sym: round(per_stock, 6) for sym, _ in top_items}
            return flow_weights, round(residual, 6)

        # ══════════════════════════════════════════════════════════════
        # [PATCH-V3] NEW: Half-Kelly + Inverse-Volatility hybrid
        # ══════════════════════════════════════════════════════════════

        # ── Step 3: Compute per-stock raw weights ────────────────────
        half_kelly_raw: dict[str, float] = {}
        inv_vol_raw: dict[str, float] = {}
        conf_for_sym: dict[str, float] = {}

        for sym, score in top_items:
            vol_i = max(vol_data.get(sym, DEFAULT_VOL), MIN_VOL)
            variance_i = vol_i * vol_i
            conf_i = conf_data.get(sym, 0.5)

            # [V3.3] Half-Kelly raw: f* = score / variance (NO clamp; clamping kills differentiation)
            # Normalization in Step 4 + clip in Step 5 handles bounds.
            hk = (score / variance_i) if variance_i > 0 else 0.0
            half_kelly_raw[sym] = max(hk, 0.0)

            # Inverse volatility
            inv_vol_raw[sym] = 1.0 / vol_i

            conf_for_sym[sym] = conf_i

        # ── Step 4: Normalize components ─────────────────────────────
        sum_hk = sum(half_kelly_raw.values())
        sum_iv = sum(inv_vol_raw.values())

        # [V3.4] Detect if vol_data is informative (has variance across symbols).
        # If all symbols share DEFAULT_VOL (vol_data missing/empty), InvVol is degenerate (1/N)
        # which dilutes Half-Kelly's differentiation. In that case, drop InvVol weight to 0.
        iv_values = list(inv_vol_raw.values())
        if len(iv_values) > 1 and (max(iv_values) - min(iv_values)) < 1e-9:
            hk_blend, iv_blend = 1.0, 0.0  # vol_data degenerate: pure Kelly
            self._iv_disabled_reason = "vol_data_degenerate"
        else:
            hk_blend, iv_blend = 0.6, 0.4
            self._iv_disabled_reason = None

        # [V3.5] Compute raw scores for top_items, then apply power amplification
        # to break degeneracy in tight alpha distributions (e.g., p_drop range 0.32~0.63)
        try:
            _amp_k = float(os.getenv("ARES_PATCH_V3_AMP_K", "4.0"))
        except Exception:
            _amp_k = 4.0
        _raw: dict[str, float] = {}
        for sym, _ in top_items:
            norm_hk = (half_kelly_raw[sym] / sum_hk) if sum_hk > 0 else (1.0 / len(top_items))
            norm_iv = (inv_vol_raw[sym] / sum_iv) if sum_iv > 0 else (1.0 / len(top_items))
            score_lin = (hk_blend * norm_hk + iv_blend * norm_iv) * (0.5 + 0.5 * conf_for_sym[sym])
            _raw[sym] = max(score_lin, 1e-12)
        # Power amplification (k>1 sharpens, k=1 is linear)
        combined: dict[str, float] = {sym: (s ** _amp_k) for sym, s in _raw.items()}

        sum_combined = sum(combined.values())
        if sum_combined <= 0:
            # Degenerate case: equal weight fallback
            n_actual = len(top_items)
            per_stock = flow_weight / max(n_actual, 1)
            flow_weights = {sym: round(per_stock, 6) for sym, _ in top_items}
            return flow_weights, 0.0

        # ── Step 5: Scale to flow_weight & clip ──────────────────────
        raw_weights: dict[str, float] = {}
        for sym, comb_score in combined.items():
            raw_weights[sym] = (comb_score / sum_combined) * flow_weight

        # [V3.3] Iterative clip + renormalize until cap satisfied (max 5 iterations)
        clipped: dict[str, float] = dict(raw_weights)
        for _iter in range(5):
            clipped = {sym: max(min(w, MAX_SINGLE_WEIGHT), MIN_SINGLE_WEIGHT) for sym, w in clipped.items()}
            sum_clipped = sum(clipped.values())
            if sum_clipped <= 0:
                break
            if abs(sum_clipped - flow_weight) <= 1e-7:
                break
            at_cap = {s for s, w in clipped.items() if w >= MAX_SINGLE_WEIGHT - 1e-9}
            cap_total = sum(clipped[s] for s in at_cap)
            free_total = sum(clipped[s] for s in clipped if s not in at_cap)
            free_target = flow_weight - cap_total
            if free_total > 0 and free_target > 0:
                scale = free_target / free_total
                clipped = {sym: (w * scale if sym not in at_cap else w) for sym, w in clipped.items()}
            else:
                scale = flow_weight / sum_clipped
                clipped = {sym: w * scale for sym, w in clipped.items()}
                break

        # ── Step 7: Round & compute residual ─────────────────────────
        flow_weights = {sym: round(w, 6) for sym, w in clipped.items()}
        allocated = sum(flow_weights.values())
        residual = round(flow_weight - allocated, 6)

        # Log weight dispersion for monitoring
        weights_list = list(flow_weights.values())
        if len(weights_list) > 1:
            import numpy as _np
            w_std = float(_np.std(weights_list))
            w_max = max(weights_list)
            w_min = min(weights_list)
            log.info(
                "[PATCH-V3] flow_sleeve: n=%d std=%.6f min=%.6f max=%.6f method=half_kelly_invvol",
                len(weights_list), w_std, w_min, w_max,
            )

        return flow_weights, max(residual, 0.0)

    # ══════════════════════════════════════════════════════════════════
    # END PATCH-V3 allocate_flow_sleeve
    # ══════════════════════════════════════════════════════════════════

    def allocate_defensive_sleeve(
        self,
        alpha_data: dict[str, float],
        conf_data: dict[str, float],
        universe: list[str],
        def_weight: float,
        state: AllocatorState = AllocatorState.VERIFIED,
        max_n: int = MAX_DEFENSIVE_N,
    ) -> dict[str, float]:
        if def_weight <= 0:
            return {}
        if state == AllocatorState.SAFE_MODE:
            return self._allocate_cash_etfs(def_weight)

        scored = {}
        for sym in universe:
            alpha = alpha_data.get(sym, 0.0)
            conf = conf_data.get(sym, 0.5)
            stability = 1.0 - min(abs(alpha), 1.0)
            def_score = 0.55 * stability + 0.45 * conf
            if def_score > 0:
                scored[sym] = def_score

        if not scored:
            return self._allocate_cash_etfs(def_weight)

        top = sorted(scored.items(), key=lambda x: -x[1])[:max_n]
        total = sum(v for _, v in top)
        if total <= 0:
            return self._allocate_cash_etfs(def_weight)

        return {sym: round((score / total) * def_weight, 6) for sym, score in top}

    @staticmethod
    def _allocate_cash_etfs(weight: float) -> dict[str, float]:
        n = len(SAFE_MODE_CASH_ETFS)
        per_etf = weight / n
        result = {etf: round(per_etf, 6) for etf in SAFE_MODE_CASH_ETFS}
        allocated = sum(result.values())
        diff = round(weight - allocated, 6)
        if abs(diff) > 1e-9:
            last_etf = SAFE_MODE_CASH_ETFS[-1]
            result[last_etf] = round(result[last_etf] + diff, 6)
        return result

    def allocate_core_sleeve(
        self,
        ssot_weights: dict[str, float],
        core_weight: float,
    ) -> dict[str, float]:
        if core_weight <= 0 or not ssot_weights:
            return {}
        total = sum(ssot_weights.values())
        if total <= 0:
            return {}
        return {
            sym: round((w / total) * core_weight, 6)
            for sym, w in ssot_weights.items()
            if w > 0
        }

    def apply_waterfall_reallocation(
        self,
        core_weights: dict[str, float],
        def_weights: dict[str, float],
        flow_weights: dict[str, float],
        residual: float,
        regime: str,
        ssot_weights: dict[str, float],
    ) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
        if abs(residual) < 1e-6:
            return core_weights, def_weights, flow_weights

        is_defensive_regime = regime in ("RISK_OFF", "CRISIS", "CRASH", "BEAR")

        if is_defensive_regime and def_weights:
            n = len(def_weights)
            add_per = residual / n
            def_weights = {s: round(w + add_per, 6) for s, w in def_weights.items()}
        elif core_weights:
            total_core = sum(core_weights.values())
            if total_core > 0:
                core_weights = {
                    s: round(w + residual * (w / total_core), 6)
                    for s, w in core_weights.items()
                }
        elif def_weights:
            n = len(def_weights)
            add_per = residual / n
            def_weights = {s: round(w + add_per, 6) for s, w in def_weights.items()}
        else:
            def_weights = self._allocate_cash_etfs(residual)

        return core_weights, def_weights, flow_weights

    def merge_and_normalize(
        self,
        core_weights: dict[str, float],
        def_weights: dict[str, float],
        flow_weights: dict[str, float],
    ) -> dict[str, float]:
        merged: dict[str, float] = {}
        for w_dict in (core_weights, def_weights, flow_weights):
            for sym, w in w_dict.items():
                merged[sym] = merged.get(sym, 0.0) + w

        if not merged:
            return self._allocate_cash_etfs(1.0)

        total = sum(merged.values())
        if total <= 0:
            return self._allocate_cash_etfs(1.0)

        if abs(total - 1.0) > 1e-9:
            scale = 1.0 / total
            merged = {s: round(w * scale, 6) for s, w in merged.items()}

        current_sum = sum(merged.values())
        correction = round(1.0 - current_sum, 6)
        if abs(correction) > 1e-9 and merged:
            largest_sym = max(merged, key=merged.get)
            merged[largest_sym] = round(merged[largest_sym] + correction, 6)
            if merged[largest_sym] < 0:
                merged[largest_sym] = 0.0
                total2 = sum(merged.values())
                if total2 > 0:
                    merged = {s: round(w / total2, 6) for s, w in merged.items()}

        return merged


# ── Service Layer ────────────────────────────────────────────────────────

class SleeveAllocatorService:
    """PM2 service: periodic sleeve allocation with 5-state machine."""

    def __init__(self):
        self.r = redis.Redis.from_url(
            REDIS_URL, password=REDIS_PASS, decode_responses=True
        )
        self.allocator = SleeveAllocatorV4()
        self.cycle_count = 0
        self.current_state = AllocatorState.VERIFIED

    # ── Input Readers ────────────────────────────────────────────────

    def _get_regime(self) -> tuple[str, str, float]:
        now = time.time()

        # Priority 1: regime:consensus:current
        try:
            raw = self.r.get(REGIME_CONSENSUS_KEY)
            if raw:
                d = json.loads(raw)
                regime = (d.get("regime") or d.get("label") or "NEUTRAL").upper()
                regime = {"BULLISH": "BULL", "BEARISH": "BEAR"}.get(regime, regime)
                ts = _parse_ts(d.get("ts", d.get("updated_at", None)), default=now)
                return regime, "consensus", now - ts
        except Exception:
            pass

        # Priority 2: regime:final:current (hash)
        try:
            key_type = self.r.type(REGIME_FINAL_KEY)
            if key_type == "hash":
                data = self.r.hgetall(REGIME_FINAL_KEY)
                hash_ts = _parse_ts(data.get("ts", data.get("updated_at", None)))
                hash_age = (now - hash_ts) if hash_ts > 0 else 0.0

                gpu_regime = (data.get("gpu_regime") or "").upper()
                if gpu_regime in ("CRISIS", "CRASH", "BEAR"):
                    return gpu_regime, "gpu_override", hash_age

                final_regime = (data.get("final") or "").upper()
                if final_regime:
                    final_regime = {"BULLISH": "BULL", "BEARISH": "BEAR"}.get(final_regime, final_regime)
                    return final_regime, "final_field", hash_age

                r15 = (data.get("r15_regime") or "").upper()
                ms = (data.get("ms_regime") or "").upper()
                r15 = {"BULLISH": "BULL", "BEARISH": "BEAR"}.get(r15, r15)
                ms = {"BULLISH": "BULL", "BEARISH": "BEAR"}.get(ms, ms)
                r15_p = CONSERVATIVE_PRIORITY.get(r15, 0)
                ms_p = CONSERVATIVE_PRIORITY.get(ms, 0)
                chosen = r15 if r15_p >= ms_p else ms
                if chosen:
                    return chosen, "conservative_pick", hash_age

                ms_state = (data.get("ms_state") or "").upper()
                if ms_state:
                    return ms_state, "ms_state_fallback", hash_age

            elif key_type == "string":
                raw = self.r.get(REGIME_FINAL_KEY)
                if raw:
                    try:
                        d = json.loads(raw)
                        str_ts = _parse_ts(d.get("ts", d.get("updated_at", None)))
                        str_age = (now - str_ts) if str_ts > 0 else 0.0
                        return d.get("regime", "NEUTRAL"), "final_string", str_age
                    except (json.JSONDecodeError, TypeError):
                        return raw.upper(), "final_raw", STALE_REGIME_SEC + 1
        except Exception:
            pass

        # Priority 3: regime:current
        try:
            key_type = self.r.type(REGIME_FALLBACK_KEY)
            if key_type == "hash":
                data = self.r.hgetall(REGIME_FALLBACK_KEY)
                fb_ts = _parse_ts(data.get("ts", data.get("updated_at", None)))
                fb_age = (now - fb_ts) if fb_ts > 0 else 0.0
                return data.get("regime", "NEUTRAL"), "fallback", fb_age
            elif key_type == "string":
                raw = self.r.get(REGIME_FALLBACK_KEY)
                if raw:
                    try:
                        d = json.loads(raw)
                        fb_ts = _parse_ts(d.get("ts", d.get("updated_at", None)))
                        fb_age = (now - fb_ts) if fb_ts > 0 else 0.0
                        return d.get("regime", "NEUTRAL"), "fallback", fb_age
                    except (json.JSONDecodeError, TypeError):
                        return raw.upper(), "fallback_raw", STALE_REGIME_SEC + 1
        except Exception:
            pass

        return "NEUTRAL", "default", 999.0

    def _get_alpha_confidence(
        self, symbols: list[str]
    ) -> tuple[dict[str, float], dict[str, float], float, int]:
        if not symbols:
            return {}, {}, 0.0, 0

        pipe = self.r.pipeline(transaction=False)
        for sym in symbols:
            pipe.get(ALPHA_SCORE_PATTERN.format(sym))
        results = pipe.execute()

        alpha_map: dict[str, float] = {}
        conf_map: dict[str, float] = {}
        fresh_count = 0
        stale_count = 0
        now = time.time()

        for sym, raw in zip(symbols, results):
            alpha = 0.0
            conf = 0.5
            if raw:
                try:
                    d = json.loads(raw)
                    alpha = float(d.get("alpha_score", 0.0))
                    conf = float(d.get("confidence", 0.5))
                    alpha_ts = _parse_ts(d.get("ts", d.get("updated_at", None)))
                    if alpha_ts > 0 and (now - alpha_ts) > STALE_ALPHA_SEC:
                        stale_count += 1
                    else:
                        fresh_count += 1
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            else:
                try:
                    sig_raw = self.r.get(SIGNAL_ALPHA_PATTERN.format(sym))
                    if sig_raw:
                        alpha = float(sig_raw)
                        stale_count += 1
                except (ValueError, TypeError):
                    pass
            alpha_map[sym] = alpha
            conf_map[sym] = conf

        available_pct = fresh_count / len(symbols) if symbols else 0.0

        # [PATCH-V3.1] Detect degenerate alpha (all values nearly equal)
        # If alpha is uninformative, fall back to XGBoost p_drop for differentiation.
        if PATCH_V3_ENABLED and len(alpha_map) >= 3:
            alpha_values = list(alpha_map.values())
            alpha_std = (sum((v - sum(alpha_values)/len(alpha_values))**2 for v in alpha_values) / len(alpha_values)) ** 0.5
            if alpha_std < 0.05:  # Degenerate: all values within ±0.05 of each other
                log.warning("[PATCH-V3.1] Degenerate alpha detected (std=%.4f). Augmenting with XGB p_drop.", alpha_std)
                xgb_map = self._get_xgb_pdrop(symbols)
                if xgb_map:
                    augmented = 0
                    for sym in symbols:
                        p_drop = xgb_map.get(sym, 0.5)
                        # [V3.2] Wider conversion: low p_drop (good) -> alpha~1.0, high p_drop (bad) -> alpha~0.0
                        # XGB signal full-range mapping for stronger differentiation
                        xgb_alpha = 0.5 + (0.5 - p_drop) * 2.0  # range [0.0, 1.0] approx (clamped below)
                        alpha_map[sym] = max(0.0, min(1.0, xgb_alpha))
                        # Boost confidence proportional to XGB certainty
                        certainty = abs(p_drop - 0.5) * 2  # 0..1
                        conf_map[sym] = max(conf_map.get(sym, 0.5), 0.5 + certainty * 0.4)
                        augmented += 1
                    log.info("[PATCH-V3.1] Augmented %d symbols with XGB p_drop.", augmented)

        return alpha_map, conf_map, available_pct, stale_count

    # ── [PATCH-V3.1] XGBoost p_drop Reader ───────────────────────────
    def _get_xgb_pdrop(self, symbols: list[str]) -> dict[str, float]:
        """
        [PATCH-V3.1] Read XGBoost p_drop from xgb:risk:{SYM} key.
        Returns: {symbol: p_drop in [0,1]}
        """
        result: dict[str, float] = {}
        try:
            pipe = self.r.pipeline()
            for sym in symbols:
                pipe.get(f"xgb:risk:{sym}")
            xgb_results = pipe.execute()
            for sym, raw in zip(symbols, xgb_results):
                if raw:
                    try:
                        d = json.loads(raw)
                        p_drop = float(d.get("p_drop", 0.5))
                        # Skip if blocked
                        if int(d.get("blocked", 0)) == 1:
                            continue
                        result[sym] = max(0.0, min(1.0, p_drop))
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
        except Exception as e:
            log.debug("[PATCH-V3.1] XGB p_drop read failed: %s", e)
        return result

    # ── [PATCH-V3-3] Volatility Reader ───────────────────────────────
    def _get_volatility_data(self, symbols: list[str]) -> dict[str, float]:
        """
        [PATCH-V3-3] Read realized volatility from Redis.
        Priority: feat:v2:vol:realized (bulk hash) → per-key → feat:v1 → DEFAULT_VOL
        Returns: {symbol: daily_vol_float}
        """
        vol_map: dict[str, float] = {}
        if not symbols:
            return vol_map

        # Try bulk hash first (single round-trip)
        try:
            bulk_type = self.r.type(VOL_BULK_KEY)
            if bulk_type == "hash":
                bulk_data = self.r.hmget(VOL_BULK_KEY, symbols)
                for sym, val in zip(symbols, bulk_data):
                    if val is not None:
                        try:
                            vol_map[sym] = max(float(val), MIN_VOL)
                        except (ValueError, TypeError):
                            pass
            elif bulk_type == "string":
                raw = self.r.get(VOL_BULK_KEY)
                if raw:
                    try:
                        d = json.loads(raw)
                        for sym in symbols:
                            v = d.get(sym)
                            if v is not None:
                                vol_map[sym] = max(float(v), MIN_VOL)
                    except (json.JSONDecodeError, TypeError):
                        pass
        except Exception as e:
            log.debug("[PATCH-V3] Bulk vol read failed: %s", e)

        # Fill missing with per-key lookups
        missing = [s for s in symbols if s not in vol_map]
        if missing:
            pipe = self.r.pipeline(transaction=False)
            for sym in missing:
                pipe.get(VOL_V2_PATTERN.format(sym))
            results = pipe.execute()

            still_missing = []
            for sym, raw in zip(missing, results):
                if raw:
                    try:
                        # Could be JSON or plain float
                        try:
                            d = json.loads(raw)
                            v = float(d.get("vol", d.get("realized_vol", d.get("value", raw))))
                        except (json.JSONDecodeError, TypeError):
                            v = float(raw)
                        vol_map[sym] = max(v, MIN_VOL)
                    except (ValueError, TypeError):
                        still_missing.append(sym)
                else:
                    still_missing.append(sym)

            # v1 fallback
            if still_missing:
                pipe2 = self.r.pipeline(transaction=False)
                for sym in still_missing:
                    pipe2.get(VOL_V1_PATTERN.format(sym))
                results2 = pipe2.execute()
                for sym, raw in zip(still_missing, results2):
                    if raw:
                        try:
                            try:
                                d = json.loads(raw)
                                v = float(d.get("vol", d.get("realized_vol", d.get("value", raw))))
                            except (json.JSONDecodeError, TypeError):
                                v = float(raw)
                            vol_map[sym] = max(v, MIN_VOL)
                        except (ValueError, TypeError):
                            pass

        # Fill remaining with default
        for sym in symbols:
            if sym not in vol_map:
                vol_map[sym] = DEFAULT_VOL

        return vol_map

    # ── [PATCH-V3-4] Regime Smoother Reader ──────────────────────────
    def _get_smoothed_exposure(self) -> Optional[float]:
        """
        [PATCH-V3-4] Read smoothed exposure multiplier from regime_smoother.
        Returns float in [0, 1] or None if key absent / not using smoother.
        """
        if not PATCH_V3_ENABLED:
            return None
        try:
            raw = self.r.get(REGIME_SMOOTHED_KEY)
            if raw:
                d = json.loads(raw)
                val = float(d.get("exposure", d.get("value", 1.0)))
                ts = _parse_ts(d.get("ts"))
                # Ignore if older than 5 minutes
                if ts > 0 and (time.time() - ts) > 300:
                    log.debug("[PATCH-V3-4] Smoothed exposure stale (%.0fs), ignoring", time.time() - ts)
                    return None
                return max(0.0, min(1.0, val))
        except Exception as e:
            log.debug("[PATCH-V3-4] Smoothed exposure read failed: %s", e)
        return None

    def _get_execution_feedback(self, symbols: list[str]) -> ExecutionFeedback:
        fb = ExecutionFeedback()

        try:
            raw = self.r.get(KPI_REJECT_KEY)
            if raw:
                d = json.loads(raw)
                total = int(d.get("total", 0))
                rejected = int(d.get("rejected", d.get("count", 0)))
                ts = _parse_ts(d.get("ts", None))
                if total > 0:
                    fb.reject_rate = rejected / total
                if ts > 0 and (time.time() - ts) > STALE_REJECT_SEC:
                    fb.reject_data_stale = True
            else:
                fb.reject_data_stale = True
        except Exception:
            fb.reject_data_stale = True

        now = time.time()
        try:
            pipe = self.r.pipeline(transaction=False)
            for sym in symbols:
                key = EXEC_RECONCILE_PATTERN.format(sym)
                pipe.get(key)
                pipe.ttl(key)
            results = pipe.execute()

            for i, sym in enumerate(symbols):
                raw_val = results[i * 2]
                ttl_val = results[i * 2 + 1]

                if not raw_val:
                    continue
                if ttl_val == -2:
                    continue

                is_stuck = False
                try:
                    d = json.loads(raw_val)
                    status = (d.get("status") or "").lower()
                    rec_ts = _parse_ts(d.get("ts", d.get("updated_at", None)))
                    age = now - rec_ts if rec_ts > 0 else STALE_RECONCILE_SEC + 1

                    if status in ("completed", "success", "filled"):
                        continue
                    elif status in ("pending", "failed", "timeout", "rejected", "partial"):
                        if age < STALE_RECONCILE_SEC:
                            is_stuck = True
                    elif not status:
                        if ttl_val > 0 and age < STALE_RECONCILE_SEC:
                            is_stuck = True
                except (json.JSONDecodeError, TypeError, ValueError):
                    if ttl_val > 0:
                        is_stuck = True

                if is_stuck:
                    fb.stuck_symbols.append(sym)
        except Exception:
            pass

        return fb

    def _get_ssot_target(self) -> dict[str, float]:
        raw = self.r.get(SSOT_TARGET_KEY)
        if raw:
            try:
                d = json.loads(raw)
                targets = d.get("targets", d.get("weights", {}))
                if isinstance(targets, dict) and "positions" in targets:
                    return {
                        p.get("symbol", ""): float(p.get("w", 0))
                        for p in targets["positions"]
                        if p.get("symbol") and float(p.get("w", 0)) > 0
                    }
                elif isinstance(targets, list):
                    return {
                        p.get("symbol", ""): float(p.get("w", p.get("weight", 0)))
                        for p in targets
                        if p.get("symbol") and float(p.get("w", p.get("weight", 0))) > 0
                    }
                return {
                    k: float(v) for k, v in targets.items()
                    if k and isinstance(v, (int, float)) and float(v) > 0
                }
            except (json.JSONDecodeError, TypeError):
                pass
        return {}

    def _get_alpha_state(self) -> str:
        raw = self.r.get(ALPHA_HEALTH_KEY)
        if raw:
            try:
                d = json.loads(raw)
                return d.get("alpha_state", d.get("status", "HEALTHY"))
            except (json.JSONDecodeError, TypeError):
                pass
        return "HEALTHY"

    # ── Main Cycle ───────────────────────────────────────────────────

    def run_cycle(self):
        self.cycle_count += 1
        weight_method = "equal"

        # 1. Read all inputs
        regime, regime_source, regime_age = self._get_regime()
        ssot_weights = self._get_ssot_target()
        universe = list(ssot_weights.keys())
        alpha_map, conf_map, alpha_pct, alpha_stale_n = self._get_alpha_confidence(universe)
        exec_fb = self._get_execution_feedback(universe)
        alpha_state = self._get_alpha_state()

        # [PATCH-V3-3] Read volatility data
        vol_data: Optional[dict[str, float]] = None
        if PATCH_V3_ENABLED:
            vol_data = self._get_volatility_data(universe)
            weight_method = "half_kelly_invvol"

        # [PATCH-V3-4] Read smoothed exposure
        smoothed_exposure = self._get_smoothed_exposure()

        # 2. Build input health
        input_health = InputHealth(
            regime_source=regime_source,
            regime_stale=(regime_age > STALE_REGIME_SEC),
            regime_age_sec=regime_age,
            alpha_available_pct=alpha_pct,
            alpha_stale=(alpha_stale_n > 0 and alpha_stale_n >= len(universe) * STALE_ALPHA_PCT_THRESHOLD),
            alpha_stale_count=alpha_stale_n,
            alpha_total_count=len(universe),
            ssot_available=bool(ssot_weights),
        )

        # 3. Determine state
        state, state_reason = self.allocator.determine_state(input_health, exec_fb)
        self.current_state = state

        # 4. Calculate base weights
        base_mix = self.allocator.calculate_base_weights(state, regime)

        # [PATCH-V3-4] Apply smoothed exposure if available
        if smoothed_exposure is not None and smoothed_exposure < 1.0:
            original_flow = base_mix["flow_event"]
            original_core = base_mix["core"]
            # Scale down risky sleeves, move excess to defensive
            scale = smoothed_exposure
            new_core = original_core * scale
            new_flow = original_flow * scale
            freed = (original_core - new_core) + (original_flow - new_flow)
            base_mix["core"] = new_core
            base_mix["flow_event"] = new_flow
            base_mix["defensive"] += freed
            log.info(
                "[PATCH-V3-4] Smoothed exposure=%.3f: core %.3f→%.3f flow %.3f→%.3f def +%.3f",
                smoothed_exposure, original_core, new_core, original_flow, new_flow, freed,
            )

        # 5. Allocate each sleeve
        core_w = self.allocator.allocate_core_sleeve(
            ssot_weights, base_mix["core"]
        )
        def_w = self.allocator.allocate_defensive_sleeve(
            alpha_map, conf_map, universe, base_mix["defensive"],
            state=state,
        )
        # [PATCH-V3-1] Pass vol_data for hybrid weighting
        flow_w, residual = self.allocator.allocate_flow_sleeve(
            alpha_map, conf_map, exec_fb.stuck_symbols,
            base_mix["flow_event"],
            vol_data=vol_data,  # [PATCH-V3] New optional kwarg
        )

        # 6. Waterfall residual reallocation
        core_w, def_w, flow_w = self.allocator.apply_waterfall_reallocation(
            core_w, def_w, flow_w, residual, regime, ssot_weights
        )

        # 7. Merge and normalize
        final_weights = self.allocator.merge_and_normalize(core_w, def_w, flow_w)

        total_w = sum(final_weights.values()) if final_weights else 0.0
        residual_pct = round(abs(1.0 - total_w) * 100, 4) if final_weights else 100.0

        # 8. Build result
        result = AllocationResult(
            final_weights=final_weights,
            sleeve_mix={
                "core": round(sum(core_w.values()), 4),
                "defensive": round(sum(def_w.values()), 4),
                "flow_event": round(sum(flow_w.values()), 4),
            },
            sleeve_details={
                "core": core_w,
                "defensive": def_w,
                "flow_event": flow_w,
            },
            state=state,
            residual_pct=residual_pct,
            reject_rate=exec_fb.reject_rate,
            stuck_symbols=exec_fb.stuck_symbols,
            reason=f"state={state.value}, regime={regime}({regime_source}), "
                   f"alpha_pct={alpha_pct:.0%}, reject={exec_fb.reject_rate:.1%}, "
                   f"stuck={len(exec_fb.stuck_symbols)}, residual={residual_pct:.2f}%",
            weight_method=weight_method,
        )

        # 9. Write to Redis
        self._write_to_redis(result, regime, alpha_state)

        # 10. Log
        log.info(
            "SLEEVE_V4 #%d [%s]: core=%.2f def=%.2f flow=%.2f | "
            "regime=%s(%s) alpha_pct=%.0f%% reject=%.1f%% stuck=%d | "
            "%d syms total_w=%.6f residual=%.4f%% method=%s patch_v3=%s",
            self.cycle_count, state.value,
            result.sleeve_mix.get("core", 0),
            result.sleeve_mix.get("defensive", 0),
            result.sleeve_mix.get("flow_event", 0),
            regime, regime_source,
            alpha_pct * 100, exec_fb.reject_rate * 100,
            len(exec_fb.stuck_symbols),
            len(final_weights), total_w, residual_pct,
            weight_method, PATCH_V3_ENABLED,
        )

    def _write_to_redis(
        self, result: AllocationResult, regime: str, alpha_state: str    ):
        """Write allocation results to Redis (v3 compatible keys)."""
        now = time.time()

        # sleeve:allocation:current (legacy compat)
        mix_output = {
            "ts": now,
            "cycle": self.cycle_count,
            "version": VERSION,
            "state": result.state.value,
            "sleeve_mix": result.sleeve_mix,
            "reason": result.reason,
            "n_symbols": len(result.final_weights),
            "total_weight": round(sum(result.final_weights.values()), 6),
            "defensive_n": len(result.sleeve_details.get("defensive", {})),
            "flow_event_n": len(result.sleeve_details.get("flow_event", {})),
            "residual_pct": result.residual_pct,
            "reject_rate": round(result.reject_rate, 4),
            "stuck_symbols": result.stuck_symbols,
            "regime": regime,
            "weight_method": result.weight_method,  # [PATCH-V3]
            "patch_v3_enabled": PATCH_V3_ENABLED,   # [PATCH-V3]
        }
        self.r.set(SLEEVE_MIX_KEY, json.dumps(mix_output))
        self.r.set(SLEEVE_V3_ALLOC_KEY, json.dumps(mix_output))

        details_output = {
            "ts": now,
            "version": VERSION,
            "state": result.state.value,
            "sleeve_details": result.sleeve_details,
            "weight_method": result.weight_method,  # [PATCH-V3]
        }
        self.r.set(SLEEVE_V3_DETAILS_KEY, json.dumps(details_output))

        weights_output = {
            "ts": now,
            "version": VERSION,
            "state": result.state.value,
            "sleeve_mix": result.sleeve_mix,
            "weights": {sym: round(w, 6) for sym, w in result.final_weights.items()},
            "sleeve_details": result.sleeve_details,
            "weight_method": result.weight_method,  # [PATCH-V3]
            "patch_v3_enabled": PATCH_V3_ENABLED,   # [PATCH-V3]
        }
        self.r.set(SLEEVE_FINAL_WEIGHTS_KEY, json.dumps(weights_output))
        self.r.set(SLEEVE_V3_FINAL_KEY, json.dumps(weights_output))

        # [PATCH-V3-5] sleeve:ares:weights (NEW key for downstream consumers)
        ares_output = {
            "ts": now,
            "version": VERSION,
            "weight_method": result.weight_method,
            "patch_v3_enabled": PATCH_V3_ENABLED,
            "weights": {sym: round(w, 6) for sym, w in result.final_weights.items()},
            "sleeve_mix": result.sleeve_mix,
            "regime": regime,
            "alpha_state": alpha_state,
        }
        self.r.set(SLEEVE_ARES_KEY, json.dumps(ares_output))

    # ── Main Loop ────────────────────────────────────────────────────

    def run(self):
        log.info(
            "Sleeve Allocator v%s starting (interval=%ds, states=%s) PATCH_V3=%s",
            VERSION, CYCLE_INTERVAL,
            [s.value for s in AllocatorState],
            PATCH_V3_ENABLED,
        )
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                log.error("Sleeve Allocator HALT: %s", e, exc_info=True)
                self.current_state = AllocatorState.HALT
                try:
                    self.r.set(SLEEVE_MIX_KEY, json.dumps({
                        "ts": time.time(),
                        "version": VERSION,
                        "state": "HALT",
                        "error": str(e)[:200],
                    }))
                except Exception:
                    pass
                raise
            time.sleep(CYCLE_INTERVAL)


# ══════════════════════════════════════════════════════════════════════
# [PATCH-V3-6] Self-Test
# ══════════════════════════════════════════════════════════════════════
def _self_test():
    """Self-test for V3 patch (no Redis required)."""
    global PATCH_V3_ENABLED
    print("=" * 70)
    print("ARES Sleeve Allocator v%s [PATCH-V3] Self-Test" % VERSION)
    print("=" * 70)
    print(f"PATCH_V3_ENABLED = {PATCH_V3_ENABLED}")
    print()

    allocator = SleeveAllocatorV4()

    # Test data: 10 stocks with varying alpha and volatility
    alpha_data = {
        "STK01": 0.80, "STK02": 0.65, "STK03": 0.55, "STK04": 0.50, "STK05": 0.45,
        "STK06": 0.40, "STK07": 0.35, "STK08": 0.30, "STK09": 0.25, "STK10": 0.20,
    }
    conf_data = {sym: 0.7 + (i % 3) * 0.1 for i, sym in enumerate(alpha_data)}
    vol_data = {
        "STK01": 0.040, "STK02": 0.025, "STK03": 0.015, "STK04": 0.020, "STK05": 0.030,
        "STK06": 0.018, "STK07": 0.022, "STK08": 0.028, "STK09": 0.035, "STK10": 0.012,
    }

    # Test 1: Without V3 (equal-weight, original)
    flow_w_old, residual_old = allocator.allocate_flow_sleeve(
        alpha_data, conf_data, [], 0.25, vol_data=None,
    )
    weights_old = list(flow_w_old.values())
    import statistics as _s
    std_old = _s.stdev(weights_old) if len(weights_old) > 1 else 0.0
    print(f"[Test 1] Equal-weight (PATCH OFF):")
    print(f"  N={len(weights_old)}, std={std_old:.6f}, min={min(weights_old):.6f}, max={max(weights_old):.6f}")

    # Test 2: With V3 (force enable for test)
    _backup = PATCH_V3_ENABLED
    PATCH_V3_ENABLED = True

    flow_w_new, residual_new = allocator.allocate_flow_sleeve(
        alpha_data, conf_data, [], 0.25, vol_data=vol_data,
    )
    PATCH_V3_ENABLED = _backup

    weights_new = list(flow_w_new.values())
    std_new = _s.stdev(weights_new) if len(weights_new) > 1 else 0.0
    print(f"[Test 2] Half-Kelly + InvVol (PATCH ON):")
    print(f"  N={len(weights_new)}, std={std_new:.6f}, min={min(weights_new):.6f}, max={max(weights_new):.6f}")

    # Validation
    assert std_new > 0.003, f"V3 std too low: {std_new}"
    assert std_old < 1e-6 or std_new > std_old * 1.5, f"V3 not differentiating: {std_new} vs {std_old}"
    assert max(weights_new) <= MAX_SINGLE_WEIGHT * 1.05, f"Cap violated by >5%: {max(weights_new)}"
    assert all(w >= MIN_SINGLE_WEIGHT - 1e-6 for w in weights_new), "Floor violated"
    sum_new = sum(weights_new)
    assert abs(sum_new - 0.25) < 1e-3, f"Sum mismatch: {sum_new}"

    print()
    print("[PASS] All self-test checks passed")
    print(f"  Weight std improved: {std_old:.6f} → {std_new:.6f} ({std_new/max(std_old, 1e-9):.1f}x)")
    print(f"  Sum = {sum_new:.6f} (target 0.25)")
    print(f"  Max single = {max(weights_new):.6f} (cap {MAX_SINGLE_WEIGHT})")
    print(f"  Min single = {min(weights_new):.6f} (floor {MIN_SINGLE_WEIGHT})")
    print("=" * 70)
    return True


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        ok = _self_test()
        sys.exit(0 if ok else 1)
    svc = SleeveAllocatorService()
    svc.run()
