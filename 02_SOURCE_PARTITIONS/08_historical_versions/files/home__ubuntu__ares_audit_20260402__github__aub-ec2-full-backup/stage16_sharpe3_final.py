#!/usr/bin/env python3
"""
Stage 16: Sharpe 3.0+ 달성을 위한 최종 최적화
- Stage 15 최고 성능: icir_br55 (Sharpe 2.7063, Min 0.1322)
- 목표: Sharpe 3.0+ 달성 (남은 개선: +0.2937)
- 전략: icir_br55 기반 미세 조정 + 리밸런싱 최적화 + 조합 테스트
"""

import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

# 경로 설정
AUB_DIR = Path("/home/ubuntu/AUB")
BASELINE_DIR = AUB_DIR / "baseline"
RESULTS_DIR = AUB_DIR / "stage16_sharpe3_final"
DB_PATH = "/home/ubuntu/ares_x_v11_0.db"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)

def run_backtest(name, params, timeout=300):
    """백테스트 실행"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = RESULTS_DIR / f"{name}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "result.json"
    
    cmd = [
        "python3",
        str(BASELINE_DIR / "ares_phase2b_v661_addon_engine_v3_integration.py"),
        "--db", DB_PATH,
        "--output", str(output_file)
    ]
    
    for key, value in params.items():
        cmd.extend([f"--{key}", str(value)])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        
        if output_file.exists():
            with open(output_file) as f:
                data = json.load(f)
            return {
                "name": name,
                "sharpe_mean": data.get("sharpe_mean", 0),
                "sharpe_min": data.get("sharpe_min", 0),
                "sharpe_max": data.get("sharpe_max", 0),
                "mdd": data.get("mdd", 0),
                "params": params
            }
    except Exception as e:
        print(f"  ❌ {name}: {e}")
    
    return None

def calculate_composite(sharpe_mean, sharpe_min, weight_mean=0.5, weight_min=0.5):
    """Composite Score 계산"""
    return sharpe_mean * weight_mean + sharpe_min * weight_min

print("=" * 60)
print("Stage 16: Sharpe 3.0+ 달성을 위한 최종 최적화")
print("현재 최고: Sharpe 2.7063 (icir_br55) | 목표: Sharpe 3.0+")
print("=" * 60)

results = []

# Stage 15 최고 성능 기준 파라미터
BASE_PARAMS = {
    "enable_vix_sizing": 0,
    "enable_regime15": 1,
    "regime15_band_mult_risk_off": 0.5,
    "regime15_band_mult_crisis": 0.3,
    "enable_icir_v4": 1,
    "icir_lookback": 120,
    "icir_min_periods": 60,
    "icir_blend_ratio": 0.55
}

# Phase 1: icir_blend_ratio 미세 조정 (0.52-0.58)
print("\n[Phase 1] icir_blend_ratio 미세 조정 (0.52-0.58)...")
for br in [0.52, 0.53, 0.54, 0.56, 0.57, 0.58]:
    params = BASE_PARAMS.copy()
    params["icir_blend_ratio"] = br
    result = run_backtest(f"icir_br{int(br*100)}", params)
    if result:
        results.append(result)
        print(f"  icir_br{int(br*100)}: Sharpe={result['sharpe_mean']:.4f}")

# Phase 2: 리밸런싱 주기 미세 조정 (13-17일)
print("\n[Phase 2] 리밸런싱 주기 미세 조정 (13-17일)...")
for rebal in [13, 14, 16, 17]:
    params = BASE_PARAMS.copy()
    params["rebalance_days"] = rebal
    result = run_backtest(f"rebal_{rebal}", params)
    if result:
        results.append(result)
        print(f"  rebal_{rebal}: Sharpe={result['sharpe_mean']:.4f}")

# Phase 3: icir_lookback 미세 조정 (115-125)
print("\n[Phase 3] icir_lookback 미세 조정 (115-125)...")
for lb in [115, 118, 122, 125]:
    params = BASE_PARAMS.copy()
    params["icir_lookback"] = lb
    result = run_backtest(f"icir_lb{lb}", params)
    if result:
        results.append(result)
        print(f"  icir_lb{lb}: Sharpe={result['sharpe_mean']:.4f}")

# Phase 4: regime15_band_mult 미세 조정
print("\n[Phase 4] regime15_band_mult 미세 조정...")
regime_tests = [
    (0.48, 0.28), (0.48, 0.32), (0.52, 0.28), (0.52, 0.32),
    (0.50, 0.25), (0.50, 0.35)
]
for ro, cr in regime_tests:
    params = BASE_PARAMS.copy()
    params["regime15_band_mult_risk_off"] = ro
    params["regime15_band_mult_crisis"] = cr
    name = f"regime_ro{int(ro*100)}_cr{int(cr*100)}"
    result = run_backtest(name, params)
    if result:
        results.append(result)
        print(f"  {name}: Sharpe={result['sharpe_mean']:.4f}")

# Phase 5: 최적 조합 테스트 (상위 파라미터 조합)
print("\n[Phase 5] 최적 조합 테스트...")

# 조합 1: icir_br55 + rebal_15
combo1_params = BASE_PARAMS.copy()
combo1_params["rebalance_days"] = 15
result = run_backtest("combo_br55_rebal15", combo1_params)
if result:
    results.append(result)
    print(f"  combo_br55_rebal15: Sharpe={result['sharpe_mean']:.4f}")

# 조합 2: icir_br60 + rebal_15
combo2_params = BASE_PARAMS.copy()
combo2_params["icir_blend_ratio"] = 0.60
combo2_params["rebalance_days"] = 15
result = run_backtest("combo_br60_rebal15", combo2_params)
if result:
    results.append(result)
    print(f"  combo_br60_rebal15: Sharpe={result['sharpe_mean']:.4f}")

# 조합 3: icir_br55 + rebal_15 + lb118
combo3_params = BASE_PARAMS.copy()
combo3_params["rebalance_days"] = 15
combo3_params["icir_lookback"] = 118
result = run_backtest("combo_br55_rebal15_lb118", combo3_params)
if result:
    results.append(result)
    print(f"  combo_br55_rebal15_lb118: Sharpe={result['sharpe_mean']:.4f}")

# Phase 6: icir_min_periods 조정
print("\n[Phase 6] icir_min_periods 조정...")
for mp in [40, 50, 70, 80]:
    params = BASE_PARAMS.copy()
    params["icir_min_periods"] = mp
    result = run_backtest(f"icir_mp{mp}", params)
    if result:
        results.append(result)
        print(f"  icir_mp{mp}: Sharpe={result['sharpe_mean']:.4f}")

# Phase 7: VIX Sizing 재테스트 (최적 설정과 함께)
print("\n[Phase 7] VIX Sizing 재테스트...")
vix_params = BASE_PARAMS.copy()
vix_params["enable_vix_sizing"] = 1
result = run_backtest("vix_with_best", vix_params)
if result:
    results.append(result)
    print(f"  vix_with_best: Sharpe={result['sharpe_mean']:.4f}")

# Phase 8: 최종 최적 조합 (모든 발견 통합)
print("\n[Phase 8] 최종 최적 조합...")

# 최고 성능 파라미터 찾기
if results:
    best_result = max(results, key=lambda x: x['sharpe_mean'])
    print(f"\n현재 최고 성능: {best_result['name']} (Sharpe={best_result['sharpe_mean']:.4f})")
    
    # 최종 조합 테스트
    final_tests = [
        # 최고 blend_ratio + 최적 리밸런싱
        {"icir_blend_ratio": 0.55, "rebalance_days": 15, "icir_lookback": 120},
        {"icir_blend_ratio": 0.56, "rebalance_days": 15, "icir_lookback": 120},
        {"icir_blend_ratio": 0.57, "rebalance_days": 14, "icir_lookback": 120},
        {"icir_blend_ratio": 0.55, "rebalance_days": 16, "icir_lookback": 118},
    ]
    
    for i, test in enumerate(final_tests, 1):
        params = BASE_PARAMS.copy()
        params.update(test)
        result = run_backtest(f"final_{i}", params)
        if result:
            results.append(result)
            print(f"  final_{i}: Sharpe={result['sharpe_mean']:.4f}")

# 결과 분석
print("\n[Phase 9] 결과 분석...")

# Composite Score 계산
for r in results:
    r['composite'] = calculate_composite(r['sharpe_mean'], r['sharpe_min'])

# 정렬
results_sorted = sorted(results, key=lambda x: x['composite'], reverse=True)

print("\n" + "=" * 80)
print("Stage 16 결과 (Composite Score 순)")
print("=" * 80)
print(f"{'순위':<5}{'이름':<35}{'Mean':<11}{'Min':<11}{'Composite':<10}")
print("-" * 80)

for i, r in enumerate(results_sorted[:25], 1):
    print(f"{i:<5}{r['name']:<35}{r['sharpe_mean']:<11.4f}{r['sharpe_min']:<11.4f}{r['composite']:<10.4f}")

# 최고 성능 출력
best = results_sorted[0]
print("\n" + "=" * 80)
print("최고 성능")
print("=" * 80)
print(f"  이름: {best['name']}")
print(f"  Sharpe Mean: {best['sharpe_mean']:.4f}")
print(f"  Sharpe Min: {best['sharpe_min']:.4f}")
print(f"  Composite: {best['composite']:.4f}")
print(f"목표 대비 진행률: {best['sharpe_mean']/3.0*100:.1f}%")
print(f"남은 개선 필요: +{3.0 - best['sharpe_mean']:.4f}")

# 결과 저장
output_file = RESULTS_DIR / f"stage16_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
with open(output_file, 'w') as f:
    json.dump({
        "stage": 16,
        "timestamp": datetime.now().isoformat(),
        "best": best,
        "all_results": results_sorted,
        "target": 3.0,
        "progress_pct": best['sharpe_mean']/3.0*100
    }, f, indent=2)

print(f"\n결과 저장: {output_file}")
print("=" * 60)
print("Stage 16 완료")
print("=" * 60)
