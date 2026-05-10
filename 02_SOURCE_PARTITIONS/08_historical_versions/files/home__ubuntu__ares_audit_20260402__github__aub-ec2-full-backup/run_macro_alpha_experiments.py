"""
V5 챔피언 조건으로 MacroAlpha 4가지 실험 수행
1. baseline (V5 그대로)
2. macro_alpha_shadow
3. macro_alpha_apply_scale
4. macro_alpha_apply_hedge
"""

import sys
import json
import time
from datetime import datetime

sys.path.insert(0, '/home/ubuntu/AUB/baseline')

from ares_phase2b_v661_addon_engine_v3_integration import ARESv661Addon

# V5 챔피언 기본 설정
V5_CONFIG = {
    'db_path': '/home/ubuntu/etf_data_s3.db',
    'warmup_days': 756,
    'rebal_period': 15,
    'cost_bps': 4.0,
    'hedge_cost_bps': 2.0,
    'enable_hedge': True,
    'enable_cb': True,
    'enable_severity': False,
    'enable_throttle': True,
    'enable_icir_v4': True,
    'enable_vix_scaling': True,
    'enable_regime15': True,
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
        'name': 'macro_alpha_shadow',
        'description': 'MacroAlpha Shadow Mode (no portfolio impact)',
        'macro_alpha': {
            'enable_macro_alpha': 1,
            'macro_alpha_mode': 'shadow',
            'macro_alpha_apply': 'scale',
            'macro_alpha_enable_mrd': 1,
        }
    },
    {
        'name': 'macro_alpha_apply_scale',
        'description': 'MacroAlpha Apply Scale (position scaling)',
        'macro_alpha': {
            'enable_macro_alpha': 1,
            'macro_alpha_mode': 'apply',
            'macro_alpha_apply': 'scale',
            'macro_alpha_enable_mrd': 1,
        }
    },
    {
        'name': 'macro_alpha_apply_hedge',
        'description': 'MacroAlpha Apply Hedge (scale + hedge)',
        'macro_alpha': {
            'enable_macro_alpha': 1,
            'macro_alpha_mode': 'apply',
            'macro_alpha_apply': 'hedge',
            'macro_alpha_enable_mrd': 1,
        }
    },
]

def run_experiment(exp_config, n_folds=20):
    """단일 실험 실행"""
    print(f"\n{'='*60}")
    print(f"Running: {exp_config['name']}")
    print(f"Description: {exp_config['description']}")
    print(f"{'='*60}")
    
    # 엔진 설정 병합
    engine_config = {**V5_CONFIG, **exp_config['macro_alpha']}
    
    start_time = time.time()
    
    try:
        engine = ARESv661Addon(**engine_config)
        result = engine.run_walk_forward(n_folds=n_folds)
        
        elapsed = time.time() - start_time
        
        # 결과 추출
        summary = result.get('summary', {})
        folds = result.get('folds', [])
        
        # macro_alpha diagnostics 추출 (모든 fold 집계)
        ma_diag = {}
        total_rebalance_calls = 0
        total_mrd_triggered = 0
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
        ma_diag['position_scale_avg'] = position_scale_sum / position_scale_count if position_scale_count > 0 else 1.0
        ma_diag['position_scale_min'] = position_scale_min
        ma_diag['position_scale_max'] = position_scale_max
        
        # 적용 검증 게이트
        apply_gate_passed = True
        gate_reason = ""
        
        if exp_config['macro_alpha'].get('macro_alpha_mode') == 'apply':
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
        
        # 턴오버 계산 (평균)
        avg_turnover = 0.0
        turnover_count = 0
        for fold in folds:
            fold_turnover = fold.get('oos_turnover', 0.0)
            if fold_turnover > 0:
                avg_turnover += fold_turnover
                turnover_count += 1
        if turnover_count > 0:
            avg_turnover /= turnover_count
        
        exp_result = {
            'name': exp_config['name'],
            'description': exp_config['description'],
            'oos_sharpe_mean': summary.get('oos_sharpe_mean', 0.0),
            'oos_sharpe_min': summary.get('oos_sharpe_min', 0.0),
            'oos_sharpe_max': summary.get('oos_sharpe_max', 0.0),
            'positive_folds': summary.get('positive_folds', 0),
            'total_folds': summary.get('total_folds', 0),
            'worst_mdd': worst_mdd,
            'avg_turnover': avg_turnover,
            'macro_alpha_diag': {
                'enabled': ma_diag.get('enabled', False),
                'mode': ma_diag.get('mode', ''),
                'rebalance_calls': ma_diag.get('rebalance_calls', 0),
                'mrd_triggered_count': ma_diag.get('mrd_triggered_count', 0),
                'position_scale_avg': ma_diag.get('position_scale_avg', 1.0),
                'position_scale_min': ma_diag.get('position_scale_min', 1.0),
                'position_scale_max': ma_diag.get('position_scale_max', 1.0),
            },
            'apply_gate_passed': apply_gate_passed,
            'gate_reason': gate_reason,
            'elapsed_seconds': elapsed,
        }
        
        print(f"\nResults:")
        print(f"  OOS Sharpe Mean: {exp_result['oos_sharpe_mean']:.4f}")
        print(f"  OOS Sharpe Min:  {exp_result['oos_sharpe_min']:.4f}")
        print(f"  Positive Folds:  {exp_result['positive_folds']}/{exp_result['total_folds']}")
        print(f"  Worst MDD:       {exp_result['worst_mdd']:.4f}")
        print(f"  Avg Turnover:    {exp_result['avg_turnover']:.4f}")
        print(f"  Apply Gate:      {'PASS' if apply_gate_passed else 'FAIL'} {gate_reason}")
        print(f"  Elapsed:         {elapsed:.1f}s")
        
        if ma_diag.get('enabled'):
            print(f"\n  MacroAlpha Diagnostics:")
            print(f"    mode:               {ma_diag.get('mode', '')}")
            print(f"    rebalance_calls:    {ma_diag.get('rebalance_calls', 0)}")
            print(f"    mrd_triggered:      {ma_diag.get('mrd_triggered_count', 0)}")
            print(f"    position_scale_avg: {ma_diag.get('position_scale_avg', 1.0):.4f}")
        
        return exp_result
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {
            'name': exp_config['name'],
            'error': str(e),
            'apply_gate_passed': False,
            'gate_reason': f'Exception: {e}',
        }


def main():
    print("="*60)
    print("MacroAlpha Experiments - V5 Champion Baseline")
    print(f"Started at: {datetime.now().isoformat()}")
    print("="*60)
    
    results = []
    
    for exp in EXPERIMENTS:
        result = run_experiment(exp, n_folds=20)
        results.append(result)
    
    # 결과 저장
    output_file = f'/home/ubuntu/AUB/baseline/macro_alpha_experiments_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    # 비교 테이블 출력
    print(f"\n{'Experiment':<30} {'OOS Mean':>10} {'OOS Min':>10} {'Worst MDD':>10} {'Gate':>6}")
    print("-"*70)
    
    baseline_sharpe = None
    for r in results:
        if 'error' in r:
            print(f"{r['name']:<30} {'ERROR':>10}")
            continue
        
        if r['name'] == 'baseline_v5':
            baseline_sharpe = r['oos_sharpe_mean']
        
        gate_str = 'PASS' if r['apply_gate_passed'] else 'FAIL'
        print(f"{r['name']:<30} {r['oos_sharpe_mean']:>10.4f} {r['oos_sharpe_min']:>10.4f} {r['worst_mdd']:>10.4f} {gate_str:>6}")
    
    # 승격 판단
    print("\n" + "="*60)
    print("PROMOTION DECISION")
    print("="*60)
    
    for r in results:
        if 'error' in r or r['name'] == 'baseline_v5':
            continue
        
        if not r['apply_gate_passed']:
            print(f"\n{r['name']}: FAIL (Gate failed: {r['gate_reason']})")
            continue
        
        if baseline_sharpe is None:
            print(f"\n{r['name']}: SKIP (No baseline for comparison)")
            continue
        
        sharpe_diff = r['oos_sharpe_mean'] - baseline_sharpe
        sharpe_pct = (sharpe_diff / baseline_sharpe) * 100 if baseline_sharpe != 0 else 0
        
        # 승격 조건: OOS Sharpe Mean >= baseline AND OOS Min >= baseline min
        baseline_result = next((x for x in results if x['name'] == 'baseline_v5'), None)
        if baseline_result:
            min_improved = r['oos_sharpe_min'] >= baseline_result['oos_sharpe_min']
            mean_improved = r['oos_sharpe_mean'] >= baseline_sharpe
            
            if mean_improved and min_improved:
                print(f"\n{r['name']}: CANDIDATE FOR PROMOTION")
                print(f"  OOS Mean: {baseline_sharpe:.4f} -> {r['oos_sharpe_mean']:.4f} ({sharpe_pct:+.2f}%)")
                print(f"  OOS Min:  {baseline_result['oos_sharpe_min']:.4f} -> {r['oos_sharpe_min']:.4f}")
            else:
                print(f"\n{r['name']}: NO IMPROVEMENT")
                print(f"  OOS Mean: {baseline_sharpe:.4f} -> {r['oos_sharpe_mean']:.4f} ({sharpe_pct:+.2f}%)")
    
    print(f"\nResults saved to: {output_file}")
    
    return results


if __name__ == '__main__':
    main()
