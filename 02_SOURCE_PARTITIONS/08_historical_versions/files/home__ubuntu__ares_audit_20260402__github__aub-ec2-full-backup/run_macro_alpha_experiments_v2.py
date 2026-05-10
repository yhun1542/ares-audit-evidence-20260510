#!/usr/bin/env python3
"""
MacroAlphaAddon 3가지 실험 (MRD 완화 / hedge-only / FSA-only)
V5 챔피언 20fold/락박스 조건
"""

import sys
import json
import time
from datetime import datetime

sys.path.insert(0, '/home/ubuntu/AUB/baseline')
from ares_phase2b_v661_addon_engine_v3_integration import ARESv661Addon

# V5 챔피언 설정
V5_CONFIG = {
    'db_path': '/home/ubuntu/etf_data_s3.db',
    'warmup_days': 756,
    'rebal_period': 15,
    'cost_bps': 4.0,
    'enable_hedge': True,
    'enable_cb': True,
    'enable_severity': True,
    'enable_throttle': True,
    'enable_icir_v4': True,
    'enable_no_trade': True,
}

# 실험 설정
EXPERIMENTS = [
    {
        'name': 'baseline_v5',
        'description': 'V5 Champion (macro_alpha OFF)',
        'macro_alpha': {
            'enable_macro_alpha': 0,
        }
    },
    {
        'name': 'mrd_scale_075',
        'description': 'MRD 강도 완화 (scale 0.75)',
        'macro_alpha': {
            'enable_macro_alpha': 1,
            'macro_alpha_mode': 'apply',
            'macro_alpha_apply': 'scale',
            'macro_alpha_enable_mrd': 1,
            'macro_alpha_mrd_scale': 0.75,
        }
    },
    {
        'name': 'mrd_hedge_only',
        'description': 'MRD hedge-only (scale 0.95)',
        'macro_alpha': {
            'enable_macro_alpha': 1,
            'macro_alpha_mode': 'apply',
            'macro_alpha_apply': 'scale_and_hedge',
            'macro_alpha_enable_mrd': 1,
            'macro_alpha_mrd_scale': 0.95,
        }
    },
    {
        'name': 'fsa_only',
        'description': 'FSA-only (금리/테이퍼링 대응)',
        'macro_alpha': {
            'enable_macro_alpha': 1,
            'macro_alpha_mode': 'apply',
            'macro_alpha_apply': 'scale_and_hedge',
            'macro_alpha_enable_mrd': 0,
            'macro_alpha_enable_fsa': 1,
            'macro_alpha_fsa_scale': 0.8,
        }
    },
]

def run_experiment(exp_config):
    """단일 실험 실행"""
    engine_config = {**V5_CONFIG, **exp_config['macro_alpha']}
    engine = ARESv661Addon(**engine_config)
    result = engine.run_walk_forward(n_folds=20)
    return result

def main():
    results = []
    
    for exp_config in EXPERIMENTS:
        print(f"\n{'='*60}")
        print(f"Running: {exp_config['name']}")
        print(f"Description: {exp_config['description']}")
        print(f"{'='*60}")
        
        start_time = time.time()
        result = run_experiment(exp_config)
        elapsed = time.time() - start_time
        
        summary = result.get('summary', {})
        folds = result.get('folds', [])
        
        # macro_alpha diagnostics 추출 (모든 fold 집계)
        ma_diag = {}
        total_rebalance_calls = 0
        total_mrd_triggered = 0
        total_fsa_triggered = 0
        position_scale_sum = 0.0
        position_scale_count = 0
        position_scale_min = 1.0
        position_scale_max = 1.0
        
        for fold in folds:
            diag = fold.get('diagnostics', {})
            addon = diag.get('addon', {})
            fold_ma = addon.get('macro_alpha', {})
            
            total_rebalance_calls += fold_ma.get('rebalance_calls', 0)
            total_mrd_triggered += fold_ma.get('mrd_triggered_count', 0)
            total_fsa_triggered += fold_ma.get('fsa_triggered_count', 0)
            
            ps_avg = fold_ma.get('position_scale_avg', 1.0)
            if ps_avg != 1.0:
                position_scale_sum += ps_avg
                position_scale_count += 1
            
            ps_min = fold_ma.get('position_scale_min', 1.0)
            ps_max = fold_ma.get('position_scale_max', 1.0)
            if ps_min < position_scale_min:
                position_scale_min = ps_min
            if ps_max > position_scale_max:
                position_scale_max = ps_max
        
        # 첫 번째 fold에서 기본 정보 추출
        if folds:
            diag = folds[0].get('diagnostics', {})
            addon = diag.get('addon', {})
            ma_diag = addon.get('macro_alpha', {}).copy()
        
        # 집계된 값으로 업데이트
        ma_diag['rebalance_calls'] = total_rebalance_calls
        ma_diag['mrd_triggered_count'] = total_mrd_triggered
        ma_diag['fsa_triggered_count'] = total_fsa_triggered
        ma_diag['position_scale_avg'] = position_scale_sum / position_scale_count if position_scale_count > 0 else 1.0
        ma_diag['position_scale_min'] = position_scale_min
        ma_diag['position_scale_max'] = position_scale_max
        
        # 적용 검증 게이트
        apply_gate_passed = True
        gate_reason = ""
        
        if exp_config['macro_alpha'].get('enable_macro_alpha', 0) == 1:
            mode = exp_config['macro_alpha'].get('macro_alpha_mode', 'shadow')
            if mode == 'apply':
                position_scale_avg = ma_diag.get('position_scale_avg', 1.0)
                if position_scale_avg == 1.0:
                    apply_gate_passed = False
                    gate_reason = "position_scale_avg == 1.0 (override/호출 누락)"
        
        # worst MDD 계산 (각 fold에서)
        worst_mdd = 0.0
        for fold in folds:
            fold_mdd = fold.get('oos_mdd', 0.0)
            if fold_mdd < worst_mdd:
                worst_mdd = fold_mdd
        
        exp_result = {
            'name': exp_config['name'],
            'description': exp_config['description'],
            'oos_sharpe_mean': summary.get('oos_sharpe_mean', 0.0),
            'oos_sharpe_min': summary.get('oos_sharpe_min', 0.0),
            'oos_sharpe_max': summary.get('oos_sharpe_max', 0.0),
            'worst_mdd': worst_mdd,
            'apply_gate_passed': apply_gate_passed,
            'gate_reason': gate_reason,
            'elapsed': elapsed,
            'macro_alpha_diag': ma_diag,
        }
        
        results.append(exp_result)
        
        # 결과 출력
        print(f"\nResults:")
        print(f"  OOS Sharpe Mean: {summary.get('oos_sharpe_mean', 0.0):.4f}")
        print(f"  OOS Sharpe Min:  {summary.get('oos_sharpe_min', 0.0):.4f}")
        print(f"  Worst MDD:       {worst_mdd:.4f}")
        print(f"  Apply Gate:      {'PASS' if apply_gate_passed else 'FAIL'} {gate_reason}")
        print(f"  Elapsed:         {elapsed:.1f}s")
        print(f"  MacroAlpha Diagnostics:")
        print(f"    mode:               {ma_diag.get('mode', 'N/A')}")
        print(f"    rebalance_calls:    {ma_diag.get('rebalance_calls', 0)}")
        print(f"    mrd_triggered:      {ma_diag.get('mrd_triggered_count', 0)}")
        print(f"    fsa_triggered:      {ma_diag.get('fsa_triggered_count', 0)}")
        print(f"    position_scale_avg: {ma_diag.get('position_scale_avg', 1.0):.4f}")
        print(f"    position_scale_min: {ma_diag.get('position_scale_min', 1.0):.4f}")
    
    # 요약 테이블
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Experiment':<25} {'OOS Mean':>10} {'OOS Min':>10} {'Worst MDD':>10} {'Gate':>6}")
    print("-" * 70)
    
    baseline_oos = results[0]['oos_sharpe_mean']
    
    for r in results:
        gate_str = "PASS" if r['apply_gate_passed'] else "FAIL"
        print(f"{r['name']:<25} {r['oos_sharpe_mean']:>10.4f} {r['oos_sharpe_min']:>10.4f} {r['worst_mdd']:>10.4f} {gate_str:>6}")
    
    # 승격 판단
    print(f"\n{'='*60}")
    print("PROMOTION DECISION")
    print(f"{'='*60}")
    
    for r in results[1:]:  # baseline 제외
        if not r['apply_gate_passed']:
            print(f"{r['name']}: FAIL (Gate failed: {r['gate_reason']})")
        else:
            delta = (r['oos_sharpe_mean'] - baseline_oos) / baseline_oos * 100
            if delta > 0:
                print(f"{r['name']}: CANDIDATE FOR PROMOTION")
                print(f"  OOS Mean: {baseline_oos:.4f} -> {r['oos_sharpe_mean']:.4f} ({delta:+.2f}%)")
                print(f"  OOS Min:  {results[0]['oos_sharpe_min']:.4f} -> {r['oos_sharpe_min']:.4f}")
            elif delta == 0:
                print(f"{r['name']}: NO CHANGE")
            else:
                print(f"{r['name']}: NO IMPROVEMENT")
                print(f"  OOS Mean: {baseline_oos:.4f} -> {r['oos_sharpe_mean']:.4f} ({delta:+.2f}%)")
    
    # JSON 저장
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"/home/ubuntu/AUB/baseline/macro_alpha_experiments_v2_{timestamp}.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_file}")

if __name__ == "__main__":
    main()
