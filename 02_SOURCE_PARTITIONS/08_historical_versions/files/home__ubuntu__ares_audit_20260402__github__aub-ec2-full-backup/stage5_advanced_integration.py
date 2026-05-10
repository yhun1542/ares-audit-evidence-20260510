#!/usr/bin/env python3
"""
Stage 5: 고급 모듈 통합 테스트
- MVO (Mean-Variance Optimization)
- Alpha Orchestrator
- PEAD Overlay
- 다중 모듈 조합
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
OUTPUT_DIR = AUB_ROOT / 'stage5_tests'
OUTPUT_DIR.mkdir(exist_ok=True)

# E6 챔피언 기본 설정 (최적 Composite Score)
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
    print("Stage 5: 고급 모듈 통합 테스트")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("="*60)
    
    results = []
    
    # Test 0: E6 Champion Baseline (참조용)
    print("\n[Test 0] E6 Champion Baseline")
    result = run_test(E6_CHAMPION_CONFIG, 'e6_baseline')
    results.append(result)
    baseline_composite = result.get('composite_score', 0) if result.get('success') else 0
    
    # Test 1-3: MVO 테스트
    print("\n[Test 1-3] MVO (Mean-Variance Optimization)")
    mvo_configs = [
        {'enable_mvo': 1, 'mvo_risk_aversion': 1.0},
        {'enable_mvo': 1, 'mvo_risk_aversion': 1.5},
        {'enable_mvo': 1, 'mvo_risk_aversion': 2.0},
    ]
    for i, cfg in enumerate(mvo_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'mvo_ra{cfg["mvo_risk_aversion"]}')
        results.append(result)
    
    # Test 4-6: Alpha Orchestrator 테스트
    print("\n[Test 4-6] Alpha Orchestrator")
    ao_configs = [
        {'enable_alpha_orchestrator': 1},
        {'enable_alpha_orchestrator': 1, 'icir_lookback': 252, 'icir_blend_ratio': 0.6},
        {'enable_alpha_orchestrator': 1, 'icir_lookback': 180, 'icir_blend_ratio': 0.65},
    ]
    for i, cfg in enumerate(ao_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'alpha_orch_{i+1}')
        results.append(result)
    
    # Test 7-9: PEAD Overlay 테스트
    print("\n[Test 7-9] PEAD Overlay")
    pead_configs = [
        {'enable_pead_overlay': 1},
        {'enable_pead_overlay': 1, 'icir_lookback': 252, 'icir_blend_ratio': 0.6},
        {'enable_pead_overlay': 1, 'enable_alpha_orchestrator': 1},
    ]
    for i, cfg in enumerate(pead_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'pead_{i+1}')
        results.append(result)
    
    # Test 10-12: 다중 모듈 조합
    print("\n[Test 10-12] 다중 모듈 조합")
    combo_configs = [
        # MVO + Alpha Orchestrator
        {'enable_mvo': 1, 'mvo_risk_aversion': 1.5, 'enable_alpha_orchestrator': 1},
        # MVO + ARESX
        {
            'enable_mvo': 1, 'mvo_risk_aversion': 1.5,
            'enable_aresx': 1, 'aresx_mode': 'apply', 'aresx_blend_weight': 0.05,
            'aresx_snapshot_path': '/home/ubuntu/AUB/data/aresx_snapshot.json'
        },
        # 전체 통합
        {
            'enable_mvo': 1, 'mvo_risk_aversion': 1.5,
            'enable_alpha_orchestrator': 1,
            'enable_aresx': 1, 'aresx_mode': 'apply', 'aresx_blend_weight': 0.05,
            'aresx_snapshot_path': '/home/ubuntu/AUB/data/aresx_snapshot.json'
        },
    ]
    for i, cfg in enumerate(combo_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'multi_combo_{i+1}')
        results.append(result)
    
    # Test 13-15: 비용 민감도 분석
    print("\n[Test 13-15] 비용 민감도 분석")
    cost_configs = [
        {'cost_bps': 2},  # 낮은 비용
        {'cost_bps': 6},  # 높은 비용
        {'cost_bps': 0},  # 비용 없음 (이론적 최대)
    ]
    for i, cfg in enumerate(cost_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'cost_bps{cfg["cost_bps"]}')
        results.append(result)
    
    # Test 16-18: 리밸런싱 주기 최적화
    print("\n[Test 16-18] 리밸런싱 주기 최적화")
    rebal_configs = [
        {'rebal_period': 10},
        {'rebal_period': 20},
        {'rebal_period': 30},
    ]
    for i, cfg in enumerate(rebal_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'rebal_{cfg["rebal_period"]}d')
        results.append(result)
    
    # Test 19-21: Top-K 최적화
    print("\n[Test 19-21] Top-K 최적화")
    topk_configs = [
        {'top_k': 8},
        {'top_k': 12},
        {'top_k': 15},
    ]
    for i, cfg in enumerate(topk_configs):
        config = {**E6_CHAMPION_CONFIG, **cfg}
        result = run_test(config, f'topk_{cfg["top_k"]}')
        results.append(result)
    
    # 결과 요약
    print("\n" + "="*60)
    print("Stage 5 테스트 결과 요약")
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
