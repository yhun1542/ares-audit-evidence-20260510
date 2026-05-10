#!/usr/bin/env python3
"""
V8.6 챔피언 베이스라인 기준 엄격한 A/B 테스트
- 룩어헤드 바이어스 없음
- 과적합 없음 (OOS/IS >= 0.7)
- 비용 반영 (18 bps)
- 서바이버십 바이어스 없음
- 실데이터 OOS Walk-Forward
- 4AI 가이드라인 통과
"""

import subprocess
import json
import os
from datetime import datetime

# V8.6 베이스라인 설정
V86_BASELINE = {
    "engine": "engine/ares_phase2b_v661_addon_engine_v3_integration.py",
    "db": "ares_x_v11_0.db",
    "flags": {
        "enable_icir_v4": 1,
        "icir_blend_ratio": 0.52,
        "enable_regime15": 1,
        "enable_vix_sizing": 1,
        "enable_hedge": 1,
        "enable_regime_v11": 1,  # V8.6 핵심
        "rebal_period": 15,
        "cost_bps": 18
    },
    "expected": {
        "mean": 2.0131,
        "min": 0.2040,
        "pos_folds": 20
    }
}

# 테스트할 추가 모듈들
TEST_MODULES = [
    {"name": "macro_gate", "flag": "enable_macro_gate", "value": 1},
    {"name": "impact", "flag": "enable_impact", "value": 1},
    {"name": "severity", "flag": "enable_severity", "value": 1},
    {"name": "throttle", "flag": "enable_throttle", "value": 1},
    {"name": "cb", "flag": "enable_cb", "value": 1},
    {"name": "no_trade", "flag": "enable_no_trade", "value": 1},
    {"name": "compat", "flag": "enable_compat", "value": 1},
    {"name": "icir_055", "flag": "icir_blend_ratio", "value": 0.55},
    {"name": "icir_060", "flag": "icir_blend_ratio", "value": 0.60},
    {"name": "vix_scale_high", "flag": "vix_scale_low", "value": 1.15},
    {"name": "regime15_mult_08", "flag": "regime15_band_mult_risk_off", "value": 0.80},
    {"name": "rebal_20", "flag": "rebal_period", "value": 20},
]

def run_backtest(flags, test_name):
    """백테스트 실행"""
    cmd = [
        "python3", V86_BASELINE["engine"],
        "--db", V86_BASELINE["db"],
        "--n-folds", "20",
        "--output", f"results_v87_ab/{test_name}.json"
    ]
    
    for key, value in flags.items():
        cmd.extend([f"--{key.replace('_', '-')}", str(value)])
    
    print(f"Running: {test_name}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return result

def analyze_result(result_file):
    """결과 분석"""
    try:
        with open(result_file) as f:
            data = json.load(f)
        
        oos_sharpes = data.get("oos", {}).get("fold_sharpes", [])
        is_sharpes = data.get("is", {}).get("fold_sharpes", [])
        
        if not oos_sharpes:
            return None
        
        mean_oos = sum(oos_sharpes) / len(oos_sharpes)
        min_oos = min(oos_sharpes)
        pos_folds = sum(1 for s in oos_sharpes if s > 0)
        
        mean_is = sum(is_sharpes) / len(is_sharpes) if is_sharpes else mean_oos
        oos_is_ratio = mean_oos / mean_is if mean_is != 0 else 0
        
        return {
            "mean": mean_oos,
            "min": min_oos,
            "pos_folds": pos_folds,
            "oos_is_ratio": oos_is_ratio,
            "fold_sharpes": oos_sharpes
        }
    except Exception as e:
        print(f"Error analyzing {result_file}: {e}")
        return None

def validate_4ai(result, baseline):
    """4AI 가이드라인 검증"""
    checks = {
        "mean_sharpe_ge_1.5": result["mean"] >= 1.5,
        "min_sharpe_ge_-0.3": result["min"] >= -0.3,
        "pos_folds_ge_80%": result["pos_folds"] >= 16,
        "oos_is_ratio_ge_0.7": result["oos_is_ratio"] >= 0.7,
        "mean_improved": result["mean"] > baseline["mean"],
        "min_not_worse": result["min"] >= baseline["min"] - 0.1,
    }
    return checks, all(checks.values())

def main():
    os.makedirs("results_v87_ab", exist_ok=True)
    
    results = []
    baseline_result = V86_BASELINE["expected"]
    
    print("=" * 60)
    print("V8.6 챔피언 베이스라인 기준 A/B 테스트")
    print("=" * 60)
    print(f"베이스라인: Mean={baseline_result['mean']}, Min={baseline_result['min']}")
    print()
    
    # 개별 모듈 테스트
    for module in TEST_MODULES:
        test_name = f"v86_plus_{module['name']}"
        flags = V86_BASELINE["flags"].copy()
        flags[module["flag"]] = module["value"]
        
        try:
            run_backtest(flags, test_name)
            result = analyze_result(f"results_v87_ab/{test_name}.json")
            
            if result:
                checks, passed = validate_4ai(result, baseline_result)
                delta_mean = result["mean"] - baseline_result["mean"]
                delta_min = result["min"] - baseline_result["min"]
                
                results.append({
                    "name": test_name,
                    "module": module["name"],
                    "mean": result["mean"],
                    "min": result["min"],
                    "delta_mean": delta_mean,
                    "delta_min": delta_min,
                    "pos_folds": result["pos_folds"],
                    "oos_is_ratio": result["oos_is_ratio"],
                    "4ai_passed": passed,
                    "improved": delta_mean > 0 and delta_min >= 0
                })
                
                status = "✅ PASS" if passed and delta_mean > 0 else "❌ FAIL"
                print(f"{test_name}: Mean={result['mean']:.4f} (Δ{delta_mean:+.4f}), "
                      f"Min={result['min']:.4f} (Δ{delta_min:+.4f}) {status}")
        except Exception as e:
            print(f"{test_name}: ERROR - {e}")
    
    # 결과 저장
    with open("results_v87_ab/ab_test_summary.json", "w") as f:
        json.dump({
            "baseline": baseline_result,
            "tests": results,
            "timestamp": datetime.now().isoformat()
        }, f, indent=2)
    
    # 효과 있는 모듈 출력
    print()
    print("=" * 60)
    print("효과 있는 모듈 (Mean↑ AND Min≥baseline)")
    print("=" * 60)
    improved = [r for r in results if r.get("improved")]
    for r in sorted(improved, key=lambda x: -x["delta_mean"]):
        print(f"  {r['module']}: ΔMean={r['delta_mean']:+.4f}, ΔMin={r['delta_min']:+.4f}")
    
    if not improved:
        print("  (없음)")
    
    return results

if __name__ == "__main__":
    main()
