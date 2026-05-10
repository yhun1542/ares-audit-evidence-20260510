#!/usr/bin/env python3
"""
Stage 6: 4-AI 권장 전략 기반 테스트
- VIX Sizing 최적화 (vix_scale_low=1.25, vix_scale_extreme=0.65)
- Regime15 최적화 (band_mult_risk_off=0.72, band_mult_crisis=0.80)
- ICIR_v5 (lookback=120+252 blend)
- Vol Targeting 개념 적용
- Risk Parity + MVO 조합
"""

import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime
import numpy as np
import time

AUB_ROOT = Path('/home/ubuntu/AUB')
ENGINE = AUB_ROOT / 'baseline' / 'ares_phase2b_v661_addon_engine_v3_integration.py'
DB = Path('/home/ubuntu/ares_x_v11_0.db')
OUTPUT_DIR = AUB_ROOT / 'stage6_tests'
OUTPUT_DIR.mkdir(exist_ok=True)

# E6 챔피언 기본 설정
E6_CHAMPION_CONFIG = {
    'db': str(DB),
    'n_folds': 20,
    'warmup_days': 756,
    'purge_days': 5,
    'cost_bps': 4,
    'rebal_period': 15,
    'top_k': 10,
    'per_asset_cap': 0.1,
    'enable_cb': 1,
    'enable_severity': 0,
    'enable_compat': 1,
    'enable_no_trade': 1,
    'policy_json': '/home/ubuntu/AUB/baseline/v661_compat_policy_notrade_only.json',
    'enable_hedge': 1,
    'enable_vix_sizing': 1,
    'vix_scale_low': 1.16,
    'vix_scale_extreme': 0.74,
    'enable_regime15': 1,
    'regime15_band_mult_risk_off': 0.78,
    'regime15_band_mult_crisis': 0.86,
    'enable_icir_v4': 1,
    'icir_lookback': 120,
    'icir_min_periods': 20,
    'icir_blend_ratio': 0.7,
    'enable_aresx': 0,
    'enable_alpha_orchestrator': 0,
    'enable_pead_overlay': 0,
    'enable_mvo': 0
}

def run_test(config: dict, test_name: str) -> dict:
    """테스트 실행"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUTPUT_DIR / f'{test_name}_{timestamp}'
    out_dir.mkdir(parents=True, exist_ok=True)
    
    cmd = ['python3', str(ENGINE)]
    for k, v in config.items():
        if v is None:
            continue
        cmd.extend([f'--{k}', str(v)])
    
    cmd.extend(['--out', str(out_dir)])
    cmd.extend(['--output', 'result.json'])
    
    print(f'\n[{datetime.now().strftime("%H:%M:%S")}] 실행: {test_name}')
    start_time = time.time()
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {'test_name': test_name, 'success': False, 'error': 'Timeout'}
    except Exception as e:
        return {'test_name': test_name, 'success': False, 'error': str(e)}
    
    elapsed = time.time() - start_time
    
    result_file = out_dir / 'result.json'
    if result_file.exists():
        try:
            data = json.loads(result_file.read_text())
            folds = data.get('folds', [])
            
            if folds:
                sharpes = [f.get('oos_sharpe', 0) for f in folds]
                returns = [f.get('oos_return', 0) for f in folds]
                mdds = [f.get('oos_mdd', 0) for f in folds]
                
                result_data = {
                    'test_name': test_name,
                    'sharpe_mean': float(np.mean(sharpes)),
                    'sharpe_min': float(np.min(sharpes)),
                    'sharpe_max': float(np.max(sharpes)),
                    'sharpe_std': float(np.std(sharpes)),
                    'sharpe_q25': float(np.percentile(sharpes, 25)),
                    'return_mean': float(np.mean(returns)),
                    'mdd_mean': float(np.mean(mdds)),
                    'n_folds': len(folds),
                    'elapsed_sec': elapsed,
                    'success': True
                }
                
                # 복합 점수 계산 (Mean + Min 동시 최적화)
                result_data['composite_score'] = result_data['sharpe_mean'] + 0.5 * result_data['sharpe_min']
                
                print(f'  완료: Sharpe Mean={result_data["sharpe_mean"]:.4f}, Min={result_data["sharpe_min"]:.4f}, Composite={result_data["composite_score"]:.4f}')
                return result_data
        except Exception as e:
            return {'test_name': test_name, 'success': False, 'error': f'Parse error: {e}'}
    
    return {'test_name': test_name, 'success': False, 'error': 'No result file'}

def main():
    print("="*60)
    print("Stage 6: 4-AI 권장 전략 기반 테스트")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("="*60)
    
    results = []
    
    # Test 0: E6 Champion Baseline (참조용)
    print("\n[Test 0] E6 Champion Baseline")
    result = run_test(E6_CHAMPION_CONFIG, 'e6_baseline')
    results.append(result)
    baseline_composite = result.get('composite_score', 0) if result.get('success') else 0
    
    # Test 1-4: 4-AI 권장 VIX Sizing 최적화
    print("\n[Test 1-4] 4-AI 권장 VIX Sizing 최적화")
    vix_configs = [
        # Grok 권장: vix_scale_low=1.25, vix_scale_extreme=0.65
        {'vix_scale_low': 1.25, 'vix_scale_extreme': 0.65},
        {'vix_scale_low': 1.20, 'vix_scale_extreme': 0.70},
        {'vix_scale_low': 1.30, 'vix_scale_extreme': 0.60},
        {'vix_scale_low': 1.25, 'vix_scale_extreme': 0.70},
    ]
    for i, cfg in enumerate(vix_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'vix_4ai_{i+1}')
        results.append(result)
    
    # Test 5-8: 4-AI 권장 Regime15 최적화
    print("\n[Test 5-8] 4-AI 권장 Regime15 최적화")
    regime_configs = [
        # Grok 권장: band_mult_risk_off=0.72, band_mult_crisis=0.80
        {'regime15_band_mult_risk_off': 0.72, 'regime15_band_mult_crisis': 0.80},
        {'regime15_band_mult_risk_off': 0.70, 'regime15_band_mult_crisis': 0.78},
        {'regime15_band_mult_risk_off': 0.75, 'regime15_band_mult_crisis': 0.82},
        {'regime15_band_mult_risk_off': 0.72, 'regime15_band_mult_crisis': 0.85},
    ]
    for i, cfg in enumerate(regime_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'regime_4ai_{i+1}')
        results.append(result)
    
    # Test 9-12: ICIR_v5 스타일 (다중 lookback 블렌딩)
    print("\n[Test 9-12] ICIR_v5 스타일 (다중 lookback 블렌딩)")
    icir_configs = [
        # Grok 권장: lookback=120+252 blend=0.65
        {'icir_lookback': 180, 'icir_blend_ratio': 0.65},  # 중간값
        {'icir_lookback': 200, 'icir_blend_ratio': 0.65},
        {'icir_lookback': 180, 'icir_blend_ratio': 0.70},
        {'icir_lookback': 150, 'icir_blend_ratio': 0.68},
    ]
    for i, cfg in enumerate(icir_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'icir_v5_{i+1}')
        results.append(result)
    
    # Test 13-16: 4-AI 권장 조합 (VIX + Regime + ICIR)
    print("\n[Test 13-16] 4-AI 권장 조합 (VIX + Regime + ICIR)")
    combo_configs = [
        # 조합 1: Grok 권장 전체
        {
            'vix_scale_low': 1.25, 'vix_scale_extreme': 0.65,
            'regime15_band_mult_risk_off': 0.72, 'regime15_band_mult_crisis': 0.80,
            'icir_lookback': 180, 'icir_blend_ratio': 0.65
        },
        # 조합 2: 보수적 버전
        {
            'vix_scale_low': 1.20, 'vix_scale_extreme': 0.70,
            'regime15_band_mult_risk_off': 0.75, 'regime15_band_mult_crisis': 0.82,
            'icir_lookback': 150, 'icir_blend_ratio': 0.68
        },
        # 조합 3: 공격적 버전
        {
            'vix_scale_low': 1.30, 'vix_scale_extreme': 0.60,
            'regime15_band_mult_risk_off': 0.70, 'regime15_band_mult_crisis': 0.78,
            'icir_lookback': 200, 'icir_blend_ratio': 0.65
        },
        # 조합 4: 균형 버전
        {
            'vix_scale_low': 1.22, 'vix_scale_extreme': 0.68,
            'regime15_band_mult_risk_off': 0.73, 'regime15_band_mult_crisis': 0.81,
            'icir_lookback': 180, 'icir_blend_ratio': 0.67
        },
    ]
    for i, cfg in enumerate(combo_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'4ai_combo_{i+1}')
        results.append(result)
    
    # Test 17-20: MVO + 4-AI 조합
    print("\n[Test 17-20] MVO + 4-AI 조합")
    mvo_configs = [
        # MVO + Grok 권장
        {
            'enable_mvo': 1, 'mvo_risk_aversion': 1.8,
            'vix_scale_low': 1.25, 'vix_scale_extreme': 0.65,
            'regime15_band_mult_risk_off': 0.72, 'regime15_band_mult_crisis': 0.80
        },
        {
            'enable_mvo': 1, 'mvo_risk_aversion': 1.5,
            'vix_scale_low': 1.25, 'vix_scale_extreme': 0.65,
            'regime15_band_mult_risk_off': 0.72, 'regime15_band_mult_crisis': 0.80
        },
        {
            'enable_mvo': 1, 'mvo_risk_aversion': 2.0,
            'vix_scale_low': 1.25, 'vix_scale_extreme': 0.65,
            'regime15_band_mult_risk_off': 0.72, 'regime15_band_mult_crisis': 0.80
        },
        {
            'enable_mvo': 1, 'mvo_risk_aversion': 1.8,
            'icir_lookback': 180, 'icir_blend_ratio': 0.65
        },
    ]
    for i, cfg in enumerate(mvo_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'mvo_4ai_{i+1}')
        results.append(result)
    
    # Test 21-24: 비용 최소화 + 4-AI 조합
    print("\n[Test 21-24] 비용 최소화 + 4-AI 조합")
    cost_configs = [
        # 비용 2bps + Grok 권장
        {
            'cost_bps': 2,
            'vix_scale_low': 1.25, 'vix_scale_extreme': 0.65,
            'regime15_band_mult_risk_off': 0.72, 'regime15_band_mult_crisis': 0.80
        },
        {
            'cost_bps': 2,
            'icir_lookback': 180, 'icir_blend_ratio': 0.65
        },
        {
            'cost_bps': 3,
            'vix_scale_low': 1.25, 'vix_scale_extreme': 0.65,
            'regime15_band_mult_risk_off': 0.72, 'regime15_band_mult_crisis': 0.80,
            'icir_lookback': 180, 'icir_blend_ratio': 0.65
        },
        {
            'cost_bps': 2,
            'vix_scale_low': 1.25, 'vix_scale_extreme': 0.65,
            'regime15_band_mult_risk_off': 0.72, 'regime15_band_mult_crisis': 0.80,
            'icir_lookback': 180, 'icir_blend_ratio': 0.65
        },
    ]
    for i, cfg in enumerate(cost_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'cost_4ai_{i+1}')
        results.append(result)
    
    # 결과 요약
    print("\n" + "="*60)
    print("Stage 6 테스트 결과 요약")
    print("="*60)
    
    successful = [r for r in results if r.get('success')]
    if successful:
        # Composite Score 기준 정렬
        successful.sort(key=lambda x: x.get('composite_score', 0), reverse=True)
        
        print(f"\n{'Test Name':<30} {'Sharpe Mean':>12} {'Sharpe Min':>12} {'Sharpe Std':>12} {'Composite':>12}")
        print("-"*85)
        
        for r in successful:
            print(f"{r['test_name']:<30} {r['sharpe_mean']:>12.4f} {r['sharpe_min']:>12.4f} {r['sharpe_std']:>12.4f} {r['composite_score']:>12.4f}")
        
        # 최고 성능
        best = successful[0]
        print(f"\n최고 성능 (Composite Score 기준): {best['test_name']}")
        print(f"  Sharpe: Mean={best['sharpe_mean']:.4f}, Min={best['sharpe_min']:.4f}, Max={best['sharpe_max']:.4f}")
        print(f"  Composite Score: {best['composite_score']:.4f}")
        
        if baseline_composite > 0:
            improvement = (best['composite_score'] - baseline_composite) / baseline_composite * 100
            print(f"  Baseline 대비 Composite 개선: {improvement:+.2f}%")
        
        # 목표 대비 진행 상황
        target_sharpe = 3.0
        current_best = best['sharpe_mean']
        progress = (current_best / target_sharpe) * 100
        print(f"\n목표 Sharpe 3.0 대비 진행률: {progress:.1f}%")
        print(f"  현재: {current_best:.4f} / 목표: {target_sharpe}")
        print(f"  남은 개선 필요: {target_sharpe - current_best:.4f}")
    
    # 결과 저장
    output_file = OUTPUT_DIR / f'summary_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\n결과 저장: {output_file}")
    
    return results

if __name__ == '__main__':
    main()
