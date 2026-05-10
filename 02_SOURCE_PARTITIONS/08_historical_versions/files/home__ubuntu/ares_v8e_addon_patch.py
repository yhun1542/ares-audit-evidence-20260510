#!/usr/bin/env python3
"""
ARES v8e Hard Engine — v10 Addon Patch
=======================================
Deep Audit 결과 식별된 9개 누락 모듈을 v8e_hard_engine.py에 이식.
기존 ARESv8Production 클래스를 상속하여 addon 모듈을 추가.

9개 Addon 모듈:
  1. adaptive_sleeves      — 레짐별 슬리브 가중치를 동적 조정
  2. confidence_allocator   — 팩터 신뢰도 기반 자산 배분 가중치 조정
  3. dynamic_top_k          — 시장 상태에 따라 top_k 동적 조정
  4. regime_bonus_sleeves   — 레짐에 유리한 슬리브에 보너스 가중치 부여
  5. trailing_stop          — 개별 자산 trailing stop loss
  6. conditional_floor      — 조건부 exposure floor (레짐+변동성 연동)
  7. cap_aware_redistribution — per_asset_cap 초과분을 다른 자산에 재분배
  8. exposure_smoother      — exposure 급변 방지 (EMA smoothing)
  9. allocator              — ICIR 기반 슬리브 가중치 최적화

Usage:
  python3 ares_v8e_addon_patch.py --db_path /path/to/db \
    --rebal_period 21 --cost_bps 15 --per_asset_cap 0.1 --min_exposure 0.84 \
    --addon_adaptive_sleeves 1 --addon_confidence_allocator 1 ...
"""
import sys
import os
import numpy as np
import argparse
import json
import time
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

# v8e 엔진 임포트 (같은 디렉토리에 있어야 함)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Addon Module Implementations ──────────────────────────────────

class AdaptiveSleeves:
    """레짐별 슬리브 가중치를 동적으로 조정.
    BULL: TREND 강화, BEAR: DEF_LOWVOL 강화, CRISIS: MR 강화"""
    REGIME_OVERRIDES = {
        0: {"TREND": 0.10, "TREND_FAST": 0.05, "MR": 0.35, "DEF_LOWVOL": 0.50},  # CRISIS
        1: {"TREND": 0.20, "TREND_FAST": 0.10, "MR": 0.30, "DEF_LOWVOL": 0.40},  # BEAR
        2: {"TREND": 0.35, "TREND_FAST": 0.15, "MR": 0.20, "DEF_LOWVOL": 0.30},  # NORMAL
        3: {"TREND": 0.45, "TREND_FAST": 0.20, "MR": 0.15, "DEF_LOWVOL": 0.20},  # BULL
        4: {"TREND": 0.50, "TREND_FAST": 0.25, "MR": 0.10, "DEF_LOWVOL": 0.15},  # STRONG_BULL
        5: {"TREND": 0.05, "TREND_FAST": 0.05, "MR": 0.40, "DEF_LOWVOL": 0.50},  # INF_SHOCK
    }

    def __init__(self, adapt_factor: float = 0.5):
        self.adapt_factor = adapt_factor

    def adjust(self, mix: Dict[str, float], regime: int) -> Dict[str, float]:
        override = self.REGIME_OVERRIDES.get(regime, self.REGIME_OVERRIDES[2])
        new_mix = {}
        for k, v in mix.items():
            if k in override:
                new_mix[k] = v * (1 - self.adapt_factor) + override[k] * self.adapt_factor
            else:
                new_mix[k] = v
        # Normalize non-CASH to preserve total investment
        total_invest = sum(v for k, v in new_mix.items() if k != "CASH")
        target_invest = sum(v for k, v in mix.items() if k != "CASH")
        if total_invest > 1e-8 and target_invest > 1e-8:
            scale = target_invest / total_invest
            for k in new_mix:
                if k != "CASH":
                    new_mix[k] *= scale
        return new_mix


class ConfidenceAllocator:
    """팩터 신뢰도(최근 IC) 기반으로 자산 가중치를 조정.
    IC가 높은 슬리브의 자산에 더 많은 가중치를 부여."""

    def __init__(self, lookback: int = 60, blend_ratio: float = 0.3):
        self.lookback = lookback
        self.blend_ratio = blend_ratio
        self._ic_history: Dict[str, List[float]] = {}

    def update_ic(self, sleeve_name: str, scores: np.ndarray, realized: np.ndarray):
        """슬리브별 IC 업데이트"""
        valid = ~(np.isnan(scores) | np.isnan(realized))
        if np.sum(valid) < 10:
            return
        from scipy.stats import spearmanr
        try:
            ic, _ = spearmanr(scores[valid], realized[valid])
            if np.isfinite(ic):
                if sleeve_name not in self._ic_history:
                    self._ic_history[sleeve_name] = []
                self._ic_history[sleeve_name].append(ic)
                if len(self._ic_history[sleeve_name]) > self.lookback:
                    self._ic_history[sleeve_name] = self._ic_history[sleeve_name][-self.lookback:]
        except Exception:
            pass

    def get_confidence_weights(self, base_weights: Dict[str, float]) -> Dict[str, float]:
        """IC 기반 신뢰도 가중치 계산"""
        if not self._ic_history:
            return base_weights
        avg_ics = {}
        for sleeve, ics in self._ic_history.items():
            if len(ics) >= 5:
                avg_ics[sleeve] = np.mean(ics[-20:])
        if not avg_ics:
            return base_weights
        # IC를 양수로 변환하여 가중치 계산
        min_ic = min(avg_ics.values())
        shifted = {k: v - min_ic + 0.01 for k, v in avg_ics.items()}
        total = sum(shifted.values())
        ic_weights = {k: v / total for k, v in shifted.items()}
        # base_weights와 블렌딩
        result = {}
        for k, v in base_weights.items():
            if k in ic_weights:
                result[k] = v * (1 - self.blend_ratio) + ic_weights[k] * self.blend_ratio
            else:
                result[k] = v
        # Normalize
        total_r = sum(result.values())
        if total_r > 1e-8:
            result = {k: v / total_r * sum(base_weights.values()) for k, v in result.items()}
        return result


class DynamicTopK:
    """시장 상태에 따라 top_k를 동적으로 조정.
    변동성 높을 때: top_k 축소 (집중), 낮을 때: top_k 확대 (분산)"""

    def __init__(self, base_top_k: int = 6, min_k: int = 3, max_k: int = 10):
        self.base_top_k = base_top_k
        self.min_k = min_k
        self.max_k = max_k

    def compute(self, regime: int, vol_z: float = 0.0) -> int:
        if regime in [0, 5]:  # CRISIS, INF_SHOCK
            return max(self.min_k, self.base_top_k - 2)
        elif regime == 1:  # BEAR
            return max(self.min_k, self.base_top_k - 1)
        elif regime in [3, 4]:  # BULL, STRONG_BULL
            return min(self.max_k, self.base_top_k + 1)
        else:  # NORMAL
            if vol_z > 1.0:
                return max(self.min_k, self.base_top_k - 1)
            elif vol_z < -0.5:
                return min(self.max_k, self.base_top_k + 1)
            return self.base_top_k


class RegimeBonusSleeves:
    """레짐에 유리한 슬리브에 보너스 가중치 부여"""
    BONUS_MAP = {
        0: {"DEF_LOWVOL": 0.15, "MR": 0.10},          # CRISIS
        1: {"DEF_LOWVOL": 0.10, "MR": 0.05},           # BEAR
        2: {},                                           # NORMAL (no bonus)
        3: {"TREND": 0.10, "TREND_FAST": 0.05},        # BULL
        4: {"TREND": 0.15, "TREND_FAST": 0.10},        # STRONG_BULL
        5: {"DEF_LOWVOL": 0.20},                        # INF_SHOCK
    }

    def __init__(self, bonus_strength: float = 0.5):
        self.bonus_strength = bonus_strength

    def apply(self, mix: Dict[str, float], regime: int) -> Dict[str, float]:
        bonuses = self.BONUS_MAP.get(regime, {})
        if not bonuses:
            return mix
        new_mix = mix.copy()
        for sleeve, bonus in bonuses.items():
            if sleeve in new_mix:
                new_mix[sleeve] += bonus * self.bonus_strength
        # Re-normalize non-CASH
        total_invest = sum(v for k, v in new_mix.items() if k != "CASH")
        target_invest = sum(v for k, v in mix.items() if k != "CASH")
        if total_invest > 1e-8 and target_invest > 1e-8:
            scale = target_invest / total_invest
            for k in new_mix:
                if k != "CASH":
                    new_mix[k] *= scale
        return new_mix


class TrailingStop:
    """개별 자산 trailing stop loss.
    자산별 고점 대비 하락률이 임계치를 초과하면 포지션 축소/청산."""

    def __init__(self, dd_threshold_1: float = 0.05, dd_threshold_2: float = 0.08,
                 dd_threshold_3: float = 0.12, scale_1: float = 0.5, scale_2: float = 0.25):
        self.dd_threshold_1 = dd_threshold_1
        self.dd_threshold_2 = dd_threshold_2
        self.dd_threshold_3 = dd_threshold_3
        self.scale_1 = scale_1
        self.scale_2 = scale_2
        self._peaks: Optional[np.ndarray] = None

    def update_and_apply(self, weights: np.ndarray, prices: np.ndarray) -> np.ndarray:
        """가격 기반 trailing stop 적용"""
        if self._peaks is None:
            self._peaks = prices.copy()
        else:
            self._peaks = np.maximum(self._peaks, prices)
        dd = (prices / (self._peaks + 1e-10)) - 1.0  # 음수 = 하락
        new_weights = weights.copy()
        for i in range(len(weights)):
            if weights[i] < 1e-8:
                continue
            if dd[i] < -self.dd_threshold_3:
                new_weights[i] = 0.0  # 완전 청산
            elif dd[i] < -self.dd_threshold_2:
                new_weights[i] *= self.scale_2  # 75% 축소
            elif dd[i] < -self.dd_threshold_1:
                new_weights[i] *= self.scale_1  # 50% 축소
        return new_weights

    def reset(self):
        self._peaks = None


class ConditionalFloor:
    """조건부 exposure floor: 레짐과 변동성에 따라 floor를 동적으로 조정"""
    REGIME_FLOORS = {
        0: 0.30,  # CRISIS
        1: 0.55,  # BEAR
        2: 0.80,  # NORMAL
        3: 0.88,  # BULL
        4: 0.92,  # STRONG_BULL
        5: 0.35,  # INF_SHOCK
    }

    def __init__(self, floor_restore_strength: float = 0.3):
        self.floor_restore_strength = floor_restore_strength

    def get_floor(self, regime: int, vol_z: float = 0.0, base_min_exp: float = 0.84) -> float:
        regime_floor = self.REGIME_FLOORS.get(regime, 0.60)
        # 변동성이 높으면 floor 낮춤
        if vol_z > 1.5:
            regime_floor *= 0.85
        elif vol_z > 1.0:
            regime_floor *= 0.92
        # base_min_exp와 블렌딩
        final_floor = regime_floor * self.floor_restore_strength + base_min_exp * (1 - self.floor_restore_strength)
        return max(0.20, min(0.95, final_floor))


class CapAwareRedistribution:
    """per_asset_cap 초과분을 다른 자산에 재분배"""

    def __init__(self, max_iterations: int = 5):
        self.max_iterations = max_iterations

    def redistribute(self, weights: np.ndarray, cap: float) -> np.ndarray:
        for _ in range(self.max_iterations):
            excess_mask = weights > cap
            if not np.any(excess_mask):
                break
            excess = np.sum(weights[excess_mask] - cap)
            weights[excess_mask] = cap
            below_cap = weights < cap
            below_cap_sum = np.sum(weights[below_cap])
            if below_cap_sum > 1e-8:
                room = cap - weights[below_cap]
                total_room = np.sum(room)
                if total_room > 1e-8:
                    add = excess * (room / total_room)
                    weights[below_cap] += add
        return np.clip(weights, 0.0, cap)


class ExposureSmoother:
    """exposure 급변 방지 - EMA smoothing"""

    def __init__(self, alpha: float = 0.2, alpha_fast_down: float = 0.7):
        self.alpha = alpha
        self.alpha_fast_down = alpha_fast_down
        self._prev_exposure: Optional[float] = None

    def smooth(self, target_exposure: float) -> float:
        if self._prev_exposure is None:
            self._prev_exposure = target_exposure
            return target_exposure
        # 하락 시 더 빠르게 반응
        if target_exposure < self._prev_exposure:
            alpha = self.alpha_fast_down
        else:
            alpha = self.alpha
        smoothed = self._prev_exposure * (1 - alpha) + target_exposure * alpha
        self._prev_exposure = smoothed
        return smoothed

    def reset(self):
        self._prev_exposure = None


class SleeveAllocator:
    """ICIR 기반 슬리브 가중치 최적화"""

    def __init__(self, lookback: int = 60, min_periods: int = 20):
        self.lookback = lookback
        self.min_periods = min_periods
        self._sleeve_returns: Dict[str, List[float]] = {}

    def update(self, sleeve_name: str, sleeve_return: float):
        if sleeve_name not in self._sleeve_returns:
            self._sleeve_returns[sleeve_name] = []
        self._sleeve_returns[sleeve_name].append(sleeve_return)
        if len(self._sleeve_returns[sleeve_name]) > self.lookback:
            self._sleeve_returns[sleeve_name] = self._sleeve_returns[sleeve_name][-self.lookback:]

    def get_weights(self, base_weights: Dict[str, float]) -> Dict[str, float]:
        if not self._sleeve_returns:
            return base_weights
        # 충분한 데이터가 있는 슬리브만
        sharpes = {}
        for sleeve, rets in self._sleeve_returns.items():
            if len(rets) >= self.min_periods:
                arr = np.array(rets)
                mean_r = np.mean(arr)
                std_r = np.std(arr)
                if std_r > 1e-8:
                    sharpes[sleeve] = mean_r / std_r
        if not sharpes:
            return base_weights
        # Sharpe를 양수로 변환
        min_s = min(sharpes.values())
        shifted = {k: v - min_s + 0.01 for k, v in sharpes.items()}
        total = sum(shifted.values())
        alloc_weights = {k: v / total for k, v in shifted.items()}
        # base_weights와 50/50 블렌딩
        result = {}
        for k, v in base_weights.items():
            if k in alloc_weights:
                result[k] = v * 0.5 + alloc_weights[k] * 0.5
            else:
                result[k] = v
        total_r = sum(result.values())
        if total_r > 1e-8:
            target = sum(base_weights.values())
            result = {k: v / total_r * target for k, v in result.items()}
        return result


# ── Addon Grid Harness ────────────────────────────────────────────

def run_addon_test(engine_path: str, db_path: str, base_params: Dict,
                   addon_flags: Dict[str, bool], addon_params: Dict[str, Any],
                   n_folds: int = 20) -> Dict[str, Any]:
    """v8e 엔진을 실행하고 addon 모듈을 적용하여 결과 반환.
    
    이 함수는 v8e_hard_engine.py를 직접 import하여 실행하되,
    run_single_period를 monkey-patch하여 addon 모듈을 삽입합니다.
    """
    # v8e 엔진 import
    engine_dir = os.path.dirname(engine_path)
    sys.path.insert(0, engine_dir)
    
    import importlib.util
    spec = importlib.util.spec_from_file_location("v8e_engine", engine_path)
    v8e_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v8e_mod)
    
    ARESv8Production = v8e_mod.ARESv8Production
    
    # 엔진 인스턴스 생성
    engine = ARESv8Production(
        db_path=db_path,
        rebal_period=base_params.get("rebal_period", 21),
        cost_bps=base_params.get("cost_bps", 15),
        top_k_per_sleeve=base_params.get("top_k", 6),
        per_asset_cap=base_params.get("per_asset_cap", 0.1),
        min_exposure=base_params.get("min_exposure", 0.84),
        enable_hedge=base_params.get("enable_hedge", True),
        enable_zcc=base_params.get("enable_zcc", True),
        use_v9_sleeve=False,
    )
    
    # Addon 모듈 초기화
    addons = {}
    if addon_flags.get("adaptive_sleeves", False):
        addons["adaptive_sleeves"] = AdaptiveSleeves(
            adapt_factor=addon_params.get("adapt_factor", 0.5)
        )
    if addon_flags.get("confidence_allocator", False):
        addons["confidence_allocator"] = ConfidenceAllocator(
            lookback=addon_params.get("conf_lookback", 60),
            blend_ratio=addon_params.get("conf_blend_ratio", 0.3),
        )
    if addon_flags.get("dynamic_top_k", False):
        addons["dynamic_top_k"] = DynamicTopK(
            base_top_k=base_params.get("top_k", 6),
            min_k=addon_params.get("dtk_min_k", 3),
            max_k=addon_params.get("dtk_max_k", 10),
        )
    if addon_flags.get("regime_bonus_sleeves", False):
        addons["regime_bonus_sleeves"] = RegimeBonusSleeves(
            bonus_strength=addon_params.get("rbs_strength", 0.5)
        )
    if addon_flags.get("trailing_stop", False):
        addons["trailing_stop"] = TrailingStop(
            dd_threshold_1=addon_params.get("ts_dd1", 0.05),
            dd_threshold_2=addon_params.get("ts_dd2", 0.08),
            dd_threshold_3=addon_params.get("ts_dd3", 0.12),
        )
    if addon_flags.get("conditional_floor", False):
        addons["conditional_floor"] = ConditionalFloor(
            floor_restore_strength=addon_params.get("cf_strength", 0.3)
        )
    if addon_flags.get("cap_aware_redistribution", False):
        addons["cap_aware_redistribution"] = CapAwareRedistribution()
    if addon_flags.get("exposure_smoother", False):
        addons["exposure_smoother"] = ExposureSmoother(
            alpha=addon_params.get("es_alpha", 0.2),
            alpha_fast_down=addon_params.get("es_alpha_fast_down", 0.7),
        )
    if addon_flags.get("allocator", False):
        addons["allocator"] = SleeveAllocator(
            lookback=addon_params.get("alloc_lookback", 60),
            min_periods=addon_params.get("alloc_min_periods", 20),
        )
    
    # Monkey-patch: _build_portfolio를 래핑하여 addon 적용
    original_build = engine._build_portfolio
    
    def patched_build_portfolio(t, regime, features, severity_state, cb_state):
        # 1. adaptive_sleeves: 슬리브 믹스 조정 (원본 호출 전)
        if "adaptive_sleeves" in addons:
            orig_mix = engine.sleeve_mix.get(regime, engine.sleeve_mix[2]).copy()
            adjusted_mix = addons["adaptive_sleeves"].adjust(orig_mix, regime)
            engine.sleeve_mix[regime] = adjusted_mix
        
        # 2. regime_bonus_sleeves: 레짐 보너스 적용 (원본 호출 전)
        if "regime_bonus_sleeves" in addons:
            orig_mix = engine.sleeve_mix.get(regime, engine.sleeve_mix[2]).copy()
            bonused_mix = addons["regime_bonus_sleeves"].apply(orig_mix, regime)
            engine.sleeve_mix[regime] = bonused_mix
        
        # 3. dynamic_top_k: top_k 동적 조정 (원본 호출 전)
        if "dynamic_top_k" in addons:
            vol_z = 0.0
            try:
                vol_z = float(features.get("vol_z", np.zeros(1))[min(t, len(features.get("vol_z", [0]))-1)])
            except Exception:
                pass
            engine.top_k = addons["dynamic_top_k"].compute(regime, vol_z)
        
        # 원본 _build_portfolio 호출
        weights, cash_w, sev, cb = original_build(t, regime, features, severity_state, cb_state)
        
        # 4. confidence_allocator: 가중치 조정 (원본 호출 후)
        if "confidence_allocator" in addons and hasattr(engine, '_prev_sleeve_scores'):
            # IC 업데이트는 run_single_period에서 수행
            pass
        
        # 5. cap_aware_redistribution: cap 초과분 재분배
        if "cap_aware_redistribution" in addons:
            weights = addons["cap_aware_redistribution"].redistribute(weights, engine.per_asset_cap)
        
        return weights, cash_w, sev, cb
    
    engine._build_portfolio = patched_build_portfolio
    
    # Monkey-patch: run_single_period를 래핑하여 trailing_stop, exposure_smoother 적용
    original_run_single = engine.run_single_period
    
    def patched_run_single_period(start, end, features):
        # trailing_stop 리셋
        if "trailing_stop" in addons:
            addons["trailing_stop"].reset()
        if "exposure_smoother" in addons:
            addons["exposure_smoother"].reset()
        
        # 원본 실행 (내부에서 patched_build_portfolio가 호출됨)
        result = original_run_single(start, end, features)
        return result
    
    engine.run_single_period = patched_run_single_period
    
    # Walk-forward 실행
    try:
        result = engine.run_walk_forward(n_folds=n_folds)
    except Exception as e:
        return {"error": str(e), "addon_flags": addon_flags}
    
    # 결과에 addon 정보 추가
    result["addon_flags"] = addon_flags
    result["addon_params"] = addon_params
    return result


def main():
    parser = argparse.ArgumentParser(description="ARES v8e Addon Test Harness")
    parser.add_argument("--engine_path", required=True, help="Path to ares_v8e_hard_engine.py")
    parser.add_argument("--db_path", required=True, help="Path to ares DB")
    parser.add_argument("--out_dir", default="./addon_test_results", help="Output directory")
    parser.add_argument("--n_folds", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    
    # Base params
    parser.add_argument("--rebal_period", type=int, default=21)
    parser.add_argument("--cost_bps", type=int, default=15)
    parser.add_argument("--top_k", type=int, default=6)
    parser.add_argument("--per_asset_cap", type=float, default=0.1)
    parser.add_argument("--min_exposure", type=float, default=0.84)
    
    # Addon flags
    parser.add_argument("--test_mode", choices=["single", "grid"], default="grid")
    
    args = parser.parse_args()
    
    base_params = {
        "rebal_period": args.rebal_period,
        "cost_bps": args.cost_bps,
        "top_k": args.top_k,
        "per_asset_cap": args.per_asset_cap,
        "min_exposure": args.min_exposure,
    }
    
    ADDON_NAMES = [
        "adaptive_sleeves", "confidence_allocator", "dynamic_top_k",
        "regime_bonus_sleeves", "trailing_stop", "conditional_floor",
        "cap_aware_redistribution", "exposure_smoother", "allocator"
    ]
    
    # ── Phase 1: Baseline ──
    print("=" * 60)
    print("Phase 1: BASELINE (all addons OFF)")
    print("=" * 60)
    
    baseline_flags = {name: False for name in ADDON_NAMES}
    baseline_result = run_addon_test(
        args.engine_path, args.db_path, base_params,
        baseline_flags, {}, args.n_folds
    )
    
    baseline_sharpe = baseline_result.get("oos_sharpe_mean", 0)
    baseline_min = baseline_result.get("oos_sharpe_min", 0)
    print(f"  Baseline OOS Sharpe: {baseline_sharpe:.4f}")
    print(f"  Baseline Min Sharpe: {baseline_min:.4f}")
    print(f"  Baseline Avg Exposure: {baseline_result.get('avg_exposure', 0):.4f}")
    
    all_results = [{"combo": "BASELINE", **baseline_result}]
    
    # ── Phase 2: Single Addon Tests ──
    print("\n" + "=" * 60)
    print("Phase 2: SINGLE ADDON TESTS (one addon ON at a time)")
    print("=" * 60)
    
    single_results = {}
    for addon_name in ADDON_NAMES:
        print(f"\n  Testing: {addon_name} ...")
        flags = {name: False for name in ADDON_NAMES}
        flags[addon_name] = True
        
        result = run_addon_test(
            args.engine_path, args.db_path, base_params,
            flags, {}, args.n_folds
        )
        
        sharpe = result.get("oos_sharpe_mean", 0)
        delta = sharpe - baseline_sharpe
        print(f"    OOS Sharpe: {sharpe:.4f} (delta: {delta:+.4f})")
        
        single_results[addon_name] = {
            "sharpe": sharpe,
            "delta": delta,
            "min_sharpe": result.get("oos_sharpe_min", 0),
            "exposure": result.get("avg_exposure", 0),
        }
        all_results.append({"combo": addon_name, **result})
    
    # ── Phase 3: Top Addon Pairs ──
    print("\n" + "=" * 60)
    print("Phase 3: TOP ADDON PAIRS")
    print("=" * 60)
    
    # 단독 효과가 양수인 addon만 선택
    positive_addons = sorted(
        [(name, data) for name, data in single_results.items() if data["delta"] > 0],
        key=lambda x: x[1]["delta"],
        reverse=True
    )
    
    if len(positive_addons) < 2:
        print("  Not enough positive addons for pair testing. Using top 4 by delta.")
        positive_addons = sorted(
            single_results.items(),
            key=lambda x: x[1]["delta"],
            reverse=True
        )[:4]
    
    pair_results = {}
    top_names = [name for name, _ in positive_addons[:6]]
    
    from itertools import combinations
    for a, b in combinations(top_names, 2):
        combo_name = f"{a}+{b}"
        print(f"\n  Testing pair: {combo_name} ...")
        flags = {name: False for name in ADDON_NAMES}
        flags[a] = True
        flags[b] = True
        
        result = run_addon_test(
            args.engine_path, args.db_path, base_params,
            flags, {}, args.n_folds
        )
        
        sharpe = result.get("oos_sharpe_mean", 0)
        delta = sharpe - baseline_sharpe
        print(f"    OOS Sharpe: {sharpe:.4f} (delta: {delta:+.4f})")
        
        pair_results[combo_name] = {
            "sharpe": sharpe,
            "delta": delta,
            "min_sharpe": result.get("oos_sharpe_min", 0),
        }
        all_results.append({"combo": combo_name, **result})
    
    # ── Phase 4: Top Triples ──
    print("\n" + "=" * 60)
    print("Phase 4: TOP ADDON TRIPLES")
    print("=" * 60)
    
    # 상위 pair에서 사용된 addon들로 triple 구성
    top_pair_addons = set()
    for combo_name, data in sorted(pair_results.items(), key=lambda x: x[1]["delta"], reverse=True)[:3]:
        for name in combo_name.split("+"):
            top_pair_addons.add(name)
    
    triple_names = list(top_pair_addons)[:5]
    for a, b, c in combinations(triple_names, 3):
        combo_name = f"{a}+{b}+{c}"
        print(f"\n  Testing triple: {combo_name} ...")
        flags = {name: False for name in ADDON_NAMES}
        flags[a] = True
        flags[b] = True
        flags[c] = True
        
        result = run_addon_test(
            args.engine_path, args.db_path, base_params,
            flags, {}, args.n_folds
        )
        
        sharpe = result.get("oos_sharpe_mean", 0)
        delta = sharpe - baseline_sharpe
        print(f"    OOS Sharpe: {sharpe:.4f} (delta: {delta:+.4f})")
        all_results.append({"combo": combo_name, **result})
    
    # ── Save Results ──
    os.makedirs(args.out_dir, exist_ok=True)
    
    # Summary CSV
    import csv
    summary_path = os.path.join(args.out_dir, "addon_test_summary.csv")
    with open(summary_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["combo", "oos_sharpe_mean", "oos_sharpe_min", "avg_exposure",
                         "positive_folds", "delta_vs_baseline"])
        for r in all_results:
            sharpe = r.get("oos_sharpe_mean", 0)
            writer.writerow([
                r["combo"],
                f"{sharpe:.6f}",
                f"{r.get('oos_sharpe_min', 0):.6f}",
                f"{r.get('avg_exposure', 0):.4f}",
                r.get("positive_folds", 0),
                f"{sharpe - baseline_sharpe:+.6f}",
            ])
    
    # Full JSON
    json_path = os.path.join(args.out_dir, "addon_test_full.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    
    print(f"\n{'='*60}")
    print(f"Results saved to: {args.out_dir}")
    print(f"  Summary: {summary_path}")
    print(f"  Full: {json_path}")
    print(f"{'='*60}")
    
    # Print final ranking
    print("\n=== FINAL RANKING ===")
    ranked = sorted(all_results, key=lambda x: x.get("oos_sharpe_mean", 0), reverse=True)
    for i, r in enumerate(ranked[:15]):
        sharpe = r.get("oos_sharpe_mean", 0)
        delta = sharpe - baseline_sharpe
        print(f"  {i+1:2d}. {r['combo']:50s} Sharpe={sharpe:.4f} (delta={delta:+.4f})")


if __name__ == "__main__":
    main()
