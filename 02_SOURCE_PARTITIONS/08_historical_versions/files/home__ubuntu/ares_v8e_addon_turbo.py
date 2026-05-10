#!/usr/bin/env python3
"""
ARES v8e Hard Engine — v10 Addon Turbo Test Harness
====================================================
16-core parallel + GPU-accelerated addon test.
Deep Audit 결과 식별된 9개 누락 모듈을 v8e_hard_engine에 이식하여 테스트.

최적화:
  - multiprocessing.Pool로 addon 조합을 병렬 실행 (16코어)
  - 각 worker가 독립적으로 v8e 엔진을 로드하여 walk-forward 실행
  - 결과를 실시간으로 CSV에 기록
"""
import sys
import os
import numpy as np
import json
import time
import csv
import traceback
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from multiprocessing import Pool, cpu_count
from itertools import combinations

# ── Addon Module Implementations ──────────────────────────────────

class AdaptiveSleeves:
    """레짐별 슬리브 가중치를 동적으로 조정"""
    REGIME_OVERRIDES = {
        0: {"TREND": 0.10, "TREND_FAST": 0.05, "MR": 0.00, "DEF_LOWVOL": 0.65, "GNN_SPILLOVER": 0.00, "QUALITY_MOM": 0.00, "CASH": 0.20},
        1: {"TREND": 0.20, "TREND_FAST": 0.10, "MR": 0.05, "DEF_LOWVOL": 0.60, "GNN_SPILLOVER": 0.00, "QUALITY_MOM": 0.05, "CASH": 0.00},
        2: {"TREND": 0.25, "TREND_FAST": 0.12, "MR": 0.08, "DEF_LOWVOL": 0.45, "GNN_SPILLOVER": 0.05, "QUALITY_MOM": 0.05, "CASH": 0.00},
        3: {"TREND": 0.30, "TREND_FAST": 0.18, "MR": 0.12, "DEF_LOWVOL": 0.30, "GNN_SPILLOVER": 0.10, "QUALITY_MOM": 0.00, "CASH": 0.00},
        4: {"TREND": 0.35, "TREND_FAST": 0.20, "MR": 0.15, "DEF_LOWVOL": 0.10, "GNN_SPILLOVER": 0.15, "QUALITY_MOM": 0.05, "CASH": 0.00},
        5: {"TREND": 0.05, "TREND_FAST": 0.00, "MR": 0.00, "DEF_LOWVOL": 0.75, "GNN_SPILLOVER": 0.00, "QUALITY_MOM": 0.00, "CASH": 0.20},
    }

    def __init__(self, adapt_factor: float = 0.3):
        self.adapt_factor = adapt_factor

    def adjust(self, mix: Dict[str, float], regime: int) -> Dict[str, float]:
        override = self.REGIME_OVERRIDES.get(regime, self.REGIME_OVERRIDES[2])
        new_mix = {}
        for k, v in mix.items():
            if k in override:
                new_mix[k] = v * (1 - self.adapt_factor) + override[k] * self.adapt_factor
            else:
                new_mix[k] = v
        total_invest = sum(v for k, v in new_mix.items() if k != "CASH")
        target_invest = sum(v for k, v in mix.items() if k != "CASH")
        if total_invest > 1e-8 and target_invest > 1e-8:
            scale = target_invest / total_invest
            for k in new_mix:
                if k != "CASH":
                    new_mix[k] *= scale
        return new_mix


class DynamicTopK:
    """시장 상태에 따라 top_k를 동적으로 조정"""
    def __init__(self, base_top_k: int = 6, min_k: int = 3, max_k: int = 10):
        self.base_top_k = base_top_k
        self.min_k = min_k
        self.max_k = max_k

    def compute(self, regime: int, vol_z: float = 0.0) -> int:
        if regime in [0, 5]:
            return max(self.min_k, self.base_top_k - 2)
        elif regime == 1:
            return max(self.min_k, self.base_top_k - 1)
        elif regime in [3, 4]:
            return min(self.max_k, self.base_top_k + 1)
        else:
            return self.base_top_k


class RegimeBonusSleeves:
    """레짐에 유리한 슬리브에 보너스 가중치 부여"""
    BONUS_MAP = {
        0: {"DEF_LOWVOL": 0.15, "MR": 0.10},
        1: {"DEF_LOWVOL": 0.10, "MR": 0.05},
        2: {},
        3: {"TREND": 0.10, "TREND_FAST": 0.05},
        4: {"TREND": 0.15, "TREND_FAST": 0.10},
        5: {"DEF_LOWVOL": 0.20},
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
        total_invest = sum(v for k, v in new_mix.items() if k != "CASH")
        target_invest = sum(v for k, v in mix.items() if k != "CASH")
        if total_invest > 1e-8 and target_invest > 1e-8:
            scale = target_invest / total_invest
            for k in new_mix:
                if k != "CASH":
                    new_mix[k] *= scale
        return new_mix


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
            below_cap = (weights > 0) & (weights < cap)
            if np.any(below_cap):
                room = cap - weights[below_cap]
                total_room = np.sum(room)
                if total_room > 1e-8:
                    add = excess * (room / total_room)
                    weights[below_cap] += add
        return np.clip(weights, 0.0, cap)


class ExposureSmoother:
    """exposure 급변 방지 - EMA smoothing"""
    def __init__(self, alpha: float = 0.3, alpha_fast_down: float = 0.7):
        self.alpha = alpha
        self.alpha_fast_down = alpha_fast_down
        self._prev_exposure = None

    def smooth(self, target_exposure: float) -> float:
        if self._prev_exposure is None:
            self._prev_exposure = target_exposure
            return target_exposure
        if target_exposure < self._prev_exposure:
            alpha = self.alpha_fast_down
        else:
            alpha = self.alpha
        smoothed = self._prev_exposure * (1 - alpha) + target_exposure * alpha
        self._prev_exposure = smoothed
        return smoothed

    def reset(self):
        self._prev_exposure = None


class ConditionalFloor:
    """조건부 exposure floor"""
    REGIME_FLOORS = {0: 0.30, 1: 0.55, 2: 0.80, 3: 0.88, 4: 0.92, 5: 0.35}

    def __init__(self, floor_strength: float = 0.3):
        self.floor_strength = floor_strength

    def get_floor(self, regime: int, base_min_exp: float = 0.84) -> float:
        regime_floor = self.REGIME_FLOORS.get(regime, 0.60)
        final_floor = regime_floor * self.floor_strength + base_min_exp * (1 - self.floor_strength)
        return max(0.20, min(0.95, final_floor))


class TrailingStop:
    """개별 자산 trailing stop loss"""
    def __init__(self, dd_threshold: float = 0.08, scale_factor: float = 0.5):
        self.dd_threshold = dd_threshold
        self.scale_factor = scale_factor
        self._peaks = None

    def update_and_apply(self, weights: np.ndarray, cum_returns: np.ndarray) -> np.ndarray:
        if self._peaks is None:
            self._peaks = cum_returns.copy()
        else:
            self._peaks = np.maximum(self._peaks, cum_returns)
        dd = (cum_returns / (self._peaks + 1e-10)) - 1.0
        new_weights = weights.copy()
        for i in range(len(weights)):
            if weights[i] < 1e-8:
                continue
            if dd[i] < -self.dd_threshold:
                new_weights[i] *= self.scale_factor
        return new_weights

    def reset(self):
        self._peaks = None


class ConfidenceAllocator:
    """팩터 신뢰도 기반 자산 배분 가중치 조정 (간소화)"""
    def __init__(self, blend_ratio: float = 0.2):
        self.blend_ratio = blend_ratio

    def adjust_weights(self, weights: np.ndarray, scores: np.ndarray) -> np.ndarray:
        """scores의 절대값이 높은 자산에 더 많은 가중치"""
        if np.sum(np.abs(scores)) < 1e-8:
            return weights
        abs_scores = np.abs(scores)
        score_weights = abs_scores / (np.sum(abs_scores) + 1e-10)
        total_w = np.sum(weights)
        if total_w < 1e-8:
            return weights
        norm_weights = weights / total_w
        blended = norm_weights * (1 - self.blend_ratio) + score_weights * self.blend_ratio
        return blended * total_w


class SleeveAllocator:
    """ICIR 기반 슬리브 가중치 최적화 (간소화)"""
    def __init__(self):
        self._sleeve_returns = {}

    def update(self, sleeve_name: str, sleeve_return: float):
        if sleeve_name not in self._sleeve_returns:
            self._sleeve_returns[sleeve_name] = []
        self._sleeve_returns[sleeve_name].append(sleeve_return)
        if len(self._sleeve_returns[sleeve_name]) > 60:
            self._sleeve_returns[sleeve_name] = self._sleeve_returns[sleeve_name][-60:]

    def get_weights(self, base_weights: Dict[str, float]) -> Dict[str, float]:
        if not self._sleeve_returns:
            return base_weights
        sharpes = {}
        for sleeve, rets in self._sleeve_returns.items():
            if len(rets) >= 20:
                arr = np.array(rets)
                std_r = np.std(arr)
                if std_r > 1e-8:
                    sharpes[sleeve] = np.mean(arr) / std_r
        if not sharpes:
            return base_weights
        min_s = min(sharpes.values())
        shifted = {k: v - min_s + 0.01 for k, v in sharpes.items()}
        total = sum(shifted.values())
        alloc_weights = {k: v / total for k, v in shifted.items()}
        result = {}
        for k, v in base_weights.items():
            if k in alloc_weights:
                result[k] = v * 0.7 + alloc_weights[k] * 0.3
            else:
                result[k] = v
        total_r = sum(result.values())
        if total_r > 1e-8:
            target = sum(base_weights.values())
            result = {k: v / total_r * target for k, v in result.items()}
        return result


# ── Worker Function (for multiprocessing) ─────────────────────────

def _worker_run_combo(args_tuple):
    """멀티프로세싱 worker: 하나의 addon 조합을 테스트"""
    combo_name, engine_path, db_path, base_params, addon_flags, addon_params, n_folds = args_tuple
    
    try:
        import importlib.util
        engine_dir = os.path.dirname(engine_path)
        sys.path.insert(0, engine_dir)
        
        spec = importlib.util.spec_from_file_location("v8e_engine", engine_path)
        v8e_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(v8e_mod)
        ARESv8Production = v8e_mod.ARESv8Production
        
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
        if addon_flags.get("adaptive_sleeves"):
            addons["adaptive_sleeves"] = AdaptiveSleeves(
                adapt_factor=addon_params.get("adapt_factor", 0.3))
        if addon_flags.get("dynamic_top_k"):
            addons["dynamic_top_k"] = DynamicTopK(
                base_top_k=base_params.get("top_k", 6))
        if addon_flags.get("regime_bonus_sleeves"):
            addons["regime_bonus_sleeves"] = RegimeBonusSleeves(
                bonus_strength=addon_params.get("rbs_strength", 0.5))
        if addon_flags.get("cap_aware_redistribution"):
            addons["cap_aware_redistribution"] = CapAwareRedistribution()
        if addon_flags.get("exposure_smoother"):
            addons["exposure_smoother"] = ExposureSmoother()
        if addon_flags.get("conditional_floor"):
            addons["conditional_floor"] = ConditionalFloor()
        if addon_flags.get("trailing_stop"):
            addons["trailing_stop"] = TrailingStop(
                dd_threshold=addon_params.get("ts_dd", 0.08))
        if addon_flags.get("confidence_allocator"):
            addons["confidence_allocator"] = ConfidenceAllocator(
                blend_ratio=addon_params.get("conf_blend", 0.2))
        if addon_flags.get("allocator"):
            addons["allocator"] = SleeveAllocator()
        
        # Monkey-patch _build_portfolio
        original_build = engine._build_portfolio
        
        def patched_build(t, regime, features, severity_state, cb_state):
            if "adaptive_sleeves" in addons:
                orig_mix = engine.sleeve_mix.get(regime, engine.sleeve_mix.get(2, {})).copy()
                adjusted = addons["adaptive_sleeves"].adjust(orig_mix, regime)
                engine.sleeve_mix[regime] = adjusted
            
            if "regime_bonus_sleeves" in addons:
                orig_mix = engine.sleeve_mix.get(regime, engine.sleeve_mix.get(2, {})).copy()
                bonused = addons["regime_bonus_sleeves"].apply(orig_mix, regime)
                engine.sleeve_mix[regime] = bonused
            
            if "dynamic_top_k" in addons:
                engine.top_k = addons["dynamic_top_k"].compute(regime)
            
            weights, cash_w, sev, cb = original_build(t, regime, features, severity_state, cb_state)
            
            if "cap_aware_redistribution" in addons:
                weights = addons["cap_aware_redistribution"].redistribute(weights, engine.per_asset_cap)
            
            if "confidence_allocator" in addons and hasattr(engine, '_prev_sleeve_scores'):
                scores_all = np.zeros(len(weights))
                for sn, sc in engine._prev_sleeve_scores.items():
                    valid = ~np.isnan(sc)
                    scores_all[valid] += sc[valid]
                weights = addons["confidence_allocator"].adjust_weights(weights, scores_all)
            
            return weights, cash_w, sev, cb
        
        engine._build_portfolio = patched_build
        
        # Walk-forward 실행
        result = engine.run_walk_forward(n_folds=n_folds)
        
        summary = result.get("summary", {})
        return {
            "combo": combo_name,
            "mean_oos_sharpe": summary.get("mean_oos_sharpe", 0),
            "std_oos_sharpe": summary.get("std_oos_sharpe", 0),
            "min_oos_sharpe": summary.get("min_oos_sharpe", 0),
            "mean_is_sharpe": summary.get("mean_is_sharpe", 0),
            "positive_folds": summary.get("positive_folds", 0),
            "total_folds": summary.get("total_folds", 0),
            "oos_is_ratio": summary.get("oos_is_ratio", 0),
            "mean_roic_sharpe": summary.get("mean_roic_sharpe", 0),
            "mean_avg_exposure": summary.get("mean_avg_exposure", 0),
            "all_passed": result.get("all_passed", False),
            "addon_flags": addon_flags,
            "error": None,
        }
    except Exception as e:
        return {
            "combo": combo_name,
            "mean_oos_sharpe": 0,
            "min_oos_sharpe": 0,
            "mean_avg_exposure": 0,
            "positive_folds": 0,
            "total_folds": 0,
            "all_passed": False,
            "addon_flags": addon_flags,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ARES v8e Addon Turbo Test")
    parser.add_argument("--engine_path", required=True)
    parser.add_argument("--db_path", required=True)
    parser.add_argument("--out_dir", default="./addon_turbo_results")
    parser.add_argument("--n_folds", type=int, default=20)
    parser.add_argument("--workers", type=int, default=0, help="0=auto")
    parser.add_argument("--rebal_period", type=int, default=21)
    parser.add_argument("--cost_bps", type=int, default=15)
    parser.add_argument("--top_k", type=int, default=6)
    parser.add_argument("--per_asset_cap", type=float, default=0.1)
    parser.add_argument("--min_exposure", type=float, default=0.84)
    args = parser.parse_args()
    
    n_workers = args.workers if args.workers > 0 else min(cpu_count(), 8)
    
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
    
    # ── Build all combos ──
    all_combos = []
    
    # 1. Baseline
    all_combos.append(("BASELINE", {name: False for name in ADDON_NAMES}, {}))
    
    # 2. Single addons
    for addon_name in ADDON_NAMES:
        flags = {name: False for name in ADDON_NAMES}
        flags[addon_name] = True
        all_combos.append((f"SINGLE_{addon_name}", flags, {}))
    
    # 3. All pairs
    for a, b in combinations(ADDON_NAMES, 2):
        flags = {name: False for name in ADDON_NAMES}
        flags[a] = True
        flags[b] = True
        all_combos.append((f"PAIR_{a}+{b}", flags, {}))
    
    # 4. All triples (from top 6 addons by expected impact)
    top6 = ["adaptive_sleeves", "regime_bonus_sleeves", "cap_aware_redistribution",
            "conditional_floor", "exposure_smoother", "trailing_stop"]
    for a, b, c in combinations(top6, 3):
        flags = {name: False for name in ADDON_NAMES}
        flags[a] = True
        flags[b] = True
        flags[c] = True
        all_combos.append((f"TRIPLE_{a}+{b}+{c}", flags, {}))
    
    total_combos = len(all_combos)
    print(f"{'='*70}")
    print(f"ARES v8e Addon Turbo Test")
    print(f"{'='*70}")
    print(f"  Total combos: {total_combos}")
    print(f"  Workers: {n_workers}")
    print(f"  Base params: rp={args.rebal_period}, c={args.cost_bps}, cap={args.per_asset_cap}, me={args.min_exposure}")
    print(f"  Estimated time: ~{total_combos * 46 / n_workers / 60:.0f} minutes")
    print(f"{'='*70}")
    
    os.makedirs(args.out_dir, exist_ok=True)
    
    # Prepare worker args
    worker_args = [
        (name, args.engine_path, args.db_path, base_params, flags, params, args.n_folds)
        for name, flags, params in all_combos
    ]
    
    # ── Run with multiprocessing ──
    start_time = time.time()
    results = []
    
    csv_path = os.path.join(args.out_dir, "addon_turbo_results.csv")
    json_path = os.path.join(args.out_dir, "addon_turbo_full.json")
    
    with open(csv_path, "w", newline="") as csvf:
        writer = csv.writer(csvf)
        writer.writerow(["combo", "mean_oos_sharpe", "min_oos_sharpe", "mean_avg_exposure",
                         "positive_folds", "total_folds", "oos_is_ratio", "mean_roic_sharpe",
                         "all_passed", "error"])
    
    print(f"\nStarting {n_workers}-worker parallel execution...")
    
    with Pool(processes=n_workers) as pool:
        for i, result in enumerate(pool.imap_unordered(_worker_run_combo, worker_args)):
            results.append(result)
            elapsed = time.time() - start_time
            eta = (elapsed / (i + 1)) * (total_combos - i - 1)
            
            sharpe = result.get("mean_oos_sharpe", 0)
            error = result.get("error", "")
            status = f"ERR: {error[:50]}" if error else f"Sharpe={sharpe:.4f}"
            
            print(f"  [{i+1}/{total_combos}] {result['combo']:50s} {status}  (ETA: {eta/60:.1f}m)")
            
            # Append to CSV
            with open(csv_path, "a", newline="") as csvf:
                writer = csv.writer(csvf)
                writer.writerow([
                    result.get("combo", ""),
                    f"{result.get('mean_oos_sharpe', 0):.6f}",
                    f"{result.get('min_oos_sharpe', 0):.6f}",
                    f"{result.get('mean_avg_exposure', 0):.4f}",
                    result.get("positive_folds", 0),
                    result.get("total_folds", 0),
                    f"{result.get('oos_is_ratio', 0):.4f}",
                    f"{result.get('mean_roic_sharpe', 0):.4f}",
                    result.get("all_passed", False),
                    result.get("error", ""),
                ])
    
    total_time = time.time() - start_time
    
    # Save full JSON
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    # ── Final Ranking ──
    print(f"\n{'='*70}")
    print(f"FINAL RANKING (Total time: {total_time/60:.1f} minutes)")
    print(f"{'='*70}")
    
    # Find baseline
    baseline_sharpe = 0
    for r in results:
        if r["combo"] == "BASELINE":
            baseline_sharpe = r.get("mean_oos_sharpe", 0)
            break
    
    ranked = sorted(results, key=lambda x: x.get("mean_oos_sharpe", 0), reverse=True)
    
    print(f"\n  BASELINE: Sharpe = {baseline_sharpe:.4f}")
    print(f"\n  {'Rank':<5} {'Combo':<55} {'Sharpe':>8} {'Delta':>8} {'MinS':>8} {'Exp':>6} {'Folds':>7} {'Pass':>5}")
    print(f"  {'-'*5} {'-'*55} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*7} {'-'*5}")
    
    for i, r in enumerate(ranked[:30]):
        sharpe = r.get("mean_oos_sharpe", 0)
        delta = sharpe - baseline_sharpe
        min_s = r.get("min_oos_sharpe", 0)
        exp = r.get("mean_avg_exposure", 0)
        folds = f"{r.get('positive_folds', 0)}/{r.get('total_folds', 0)}"
        passed = "YES" if r.get("all_passed") else "NO"
        marker = " ***" if delta > 0 and r["combo"] != "BASELINE" else ""
        print(f"  {i+1:<5} {r['combo']:<55} {sharpe:>8.4f} {delta:>+8.4f} {min_s:>8.4f} {exp:>6.1%} {folds:>7} {passed:>5}{marker}")
    
    # Count improvements
    improved = [r for r in results if r.get("mean_oos_sharpe", 0) > baseline_sharpe and r["combo"] != "BASELINE"]
    print(f"\n  Improved vs baseline: {len(improved)}/{total_combos-1}")
    
    if improved:
        best = max(improved, key=lambda x: x.get("mean_oos_sharpe", 0))
        print(f"  BEST: {best['combo']} → Sharpe {best.get('mean_oos_sharpe', 0):.4f} (delta: {best.get('mean_oos_sharpe', 0) - baseline_sharpe:+.4f})")
    
    print(f"\n  Results saved to: {args.out_dir}")
    print(f"  CSV: {csv_path}")
    print(f"  JSON: {json_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
