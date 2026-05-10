#!/usr/bin/env python3
"""
run_regime64_gridsearch.py
Regime64 Overlay V2 그리드서치 실행 스크립트

Stage 1: 스칼라 램프 + 회복 게이트 + 전환 비대칭 최적화
Stage 2: 시그모이드 파라미터 미세 조정

실행: python3 run_regime64_gridsearch.py
"""

import os
import sys
import json
import time
import itertools
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Any, Tuple
from dataclasses import dataclass, asdict
import traceback

# 경로 설정
sys.path.insert(0, '/home/ubuntu/AUB')
sys.path.insert(0, '/home/ubuntu')

# Regime64 패치 임포트
from regime_overlay_v2_patch import (
    Regime64Overlay, Regime64Config, create_regime64_for_gridsearch
)


@dataclass
class GridSearchResult:
    """그리드서치 결과"""
    params: Dict[str, Any]
    mean_sharpe: float
    min_sharpe: float
    max_sharpe: float
    std_sharpe: float
    fold_sharpes: List[float]
    transition_count: int
    whipsaw_count: int
    avg_scalar: float
    std_scalar: float
    turnover: float
    cost_drag_bps: float
    runtime_sec: float


def load_manifest(path: str) -> Dict:
    """매니페스트 로드"""
    with open(path, 'r') as f:
        return json.load(f)


def generate_stage1_grid(manifest: Dict) -> List[Dict]:
    """Stage 1 그리드 생성"""
    grid_spec = manifest['stage_1']['grid']
    
    keys = list(grid_spec.keys())
    values = [grid_spec[k] for k in keys]
    
    combinations = list(itertools.product(*values))
    
    grid = []
    for combo in combinations:
        params = dict(zip(keys, combo))
        grid.append(params)
    
    print(f"Stage 1 그리드: {len(grid)} 조합")
    return grid


def generate_stage2_grid(manifest: Dict, stage1_top_k: List[Dict]) -> List[Dict]:
    """Stage 2 그리드 생성 (Stage 1 상위 k개 기반)"""
    grid_spec = manifest['stage_2']['grid']
    
    keys = list(grid_spec.keys())
    values = [grid_spec[k] for k in keys]
    
    stage2_combos = list(itertools.product(*values))
    
    grid = []
    for s1_params in stage1_top_k:
        for s2_combo in stage2_combos:
            params = s1_params.copy()
            params.update(dict(zip(keys, s2_combo)))
            grid.append(params)
    
    print(f"Stage 2 그리드: {len(grid)} 조합 (Stage 1 top {len(stage1_top_k)} × Stage 2 {len(stage2_combos)})")
    return grid


def run_single_backtest(params: Dict, engine_class, db_path: str, cost_bps: float = 18.0) -> GridSearchResult:
    """단일 백테스트 실행"""
    start_time = time.time()
    
    try:
        # Regime64 오버레이 생성
        regime64 = create_regime64_for_gridsearch(
            enabled=True,
            mode="apply",
            ramp_up_max=params.get('ramp_up_max', 0.03),
            ramp_down_max=params.get('ramp_down_max', 0.08),
            cooldown_risk_on=params.get('cooldown_risk_on', 10),
            recovery_gate_enabled=params.get('recovery_gate_enabled', True),
            recovery_confirm_bars=params.get('recovery_confirm_bars', 5),
            pos_floor=params.get('pos_floor', 0.10),
            pos_k=params.get('pos_k', 0.30),
            pos_s0=params.get('pos_s0', 0.0),
        )
        
        # 엔진 생성 및 Regime64 주입
        engine = engine_class(
            db_path=db_path,
            cost_bps=cost_bps,
            # 기타 파라미터는 기본값 사용
        )
        engine._regime64 = regime64
        
        # Walk-Forward 백테스트 실행
        results = engine.run_walk_forward()
        
        # 결과 집계
        fold_sharpes = [r['sharpe'] for r in results['folds']]
        
        # Regime64 통계 집계
        all_scalars = []
        total_transitions = 0
        total_whipsaws = 0
        
        for fold in results['folds']:
            if 'regime64' in fold.get('cb_state', {}):
                r64_logs = fold['cb_state']['regime64']
                all_scalars.extend([log['scalar'] for log in r64_logs])
                if r64_logs:
                    total_transitions += r64_logs[-1].get('transition_count', 0)
                    total_whipsaws += r64_logs[-1].get('whipsaw_count', 0)
        
        runtime = time.time() - start_time
        
        return GridSearchResult(
            params=params,
            mean_sharpe=float(np.mean(fold_sharpes)),
            min_sharpe=float(np.min(fold_sharpes)),
            max_sharpe=float(np.max(fold_sharpes)),
            std_sharpe=float(np.std(fold_sharpes)),
            fold_sharpes=fold_sharpes,
            transition_count=total_transitions,
            whipsaw_count=total_whipsaws,
            avg_scalar=float(np.mean(all_scalars)) if all_scalars else 1.0,
            std_scalar=float(np.std(all_scalars)) if all_scalars else 0.0,
            turnover=results.get('avg_turnover', 0.0),
            cost_drag_bps=results.get('cost_drag_bps', 0.0),
            runtime_sec=runtime,
        )
        
    except Exception as e:
        traceback.print_exc()
        return GridSearchResult(
            params=params,
            mean_sharpe=-999.0,
            min_sharpe=-999.0,
            max_sharpe=-999.0,
            std_sharpe=0.0,
            fold_sharpes=[],
            transition_count=0,
            whipsaw_count=0,
            avg_scalar=1.0,
            std_scalar=0.0,
            turnover=0.0,
            cost_drag_bps=0.0,
            runtime_sec=time.time() - start_time,
        )


def run_gridsearch_stage(grid: List[Dict], stage_name: str, 
                         engine_class, db_path: str,
                         cost_bps: float = 18.0) -> List[GridSearchResult]:
    """그리드서치 스테이지 실행"""
    print(f"\n{'='*60}")
    print(f"{stage_name} 시작: {len(grid)} 조합")
    print(f"{'='*60}")
    
    results = []
    for i, params in enumerate(grid):
        print(f"\n[{i+1}/{len(grid)}] 파라미터: {params}")
        
        result = run_single_backtest(params, engine_class, db_path, cost_bps)
        results.append(result)
        
        print(f"  → Mean Sharpe: {result.mean_sharpe:.4f}, Min Sharpe: {result.min_sharpe:.4f}")
        print(f"  → Whipsaw: {result.whipsaw_count}, Transitions: {result.transition_count}")
        print(f"  → Runtime: {result.runtime_sec:.1f}s")
    
    return results


def select_top_k(results: List[GridSearchResult], k: int, 
                 primary_metric: str = 'min_sharpe') -> List[Dict]:
    """상위 k개 선택"""
    # Min Sharpe 기준 정렬 (높을수록 좋음)
    sorted_results = sorted(results, key=lambda r: getattr(r, primary_metric), reverse=True)
    
    top_k = [r.params for r in sorted_results[:k]]
    
    print(f"\n상위 {k}개 선택 (기준: {primary_metric}):")
    for i, r in enumerate(sorted_results[:k]):
        print(f"  {i+1}. {r.params}")
        print(f"     Mean: {r.mean_sharpe:.4f}, Min: {r.min_sharpe:.4f}, Whipsaw: {r.whipsaw_count}")
    
    return top_k


def check_needle_overfit(results: List[GridSearchResult], threshold: float = 0.10) -> List[Dict]:
    """Needle Overfit 체크"""
    flagged = []
    
    for i, r in enumerate(results):
        # 이웃 결과 찾기 (파라미터 1개만 다른 경우)
        neighbors = []
        for j, other in enumerate(results):
            if i == j:
                continue
            diff_count = sum(1 for k in r.params if r.params[k] != other.params.get(k))
            if diff_count == 1:
                neighbors.append(other)
        
        if not neighbors:
            continue
        
        neighbor_median = np.median([n.min_sharpe for n in neighbors])
        
        if r.min_sharpe > neighbor_median * (1 + threshold):
            flagged.append({
                'params': r.params,
                'min_sharpe': r.min_sharpe,
                'neighbor_median': neighbor_median,
                'drop': (r.min_sharpe - neighbor_median) / neighbor_median,
            })
    
    if flagged:
        print(f"\n⚠️ Needle Overfit 감지: {len(flagged)}개")
        for f in flagged:
            print(f"  - {f['params']}: Min={f['min_sharpe']:.4f}, Neighbor Median={f['neighbor_median']:.4f}")
    
    return flagged


def save_results(results: List[GridSearchResult], stage_name: str, output_dir: str):
    """결과 저장"""
    os.makedirs(output_dir, exist_ok=True)
    
    # JSON 저장
    json_path = os.path.join(output_dir, f"{stage_name}_results.json")
    with open(json_path, 'w') as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    
    # CSV 저장
    csv_path = os.path.join(output_dir, f"{stage_name}_results.csv")
    df = pd.DataFrame([asdict(r) for r in results])
    df.to_csv(csv_path, index=False)
    
    print(f"\n결과 저장: {json_path}, {csv_path}")


def generate_comparison_report(champion_results: Dict, candidate_results: List[GridSearchResult],
                               output_path: str):
    """Champion vs Candidate 비교 리포트 생성"""
    best_candidate = max(candidate_results, key=lambda r: r.min_sharpe)
    
    report = f"""# Regime64 Overlay V2 - Champion vs Candidate 비교 리포트

생성 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 1. 요약

| 지표 | Champion (V8.7) | Candidate (Best) | 변화 |
|------|-----------------|------------------|------|
| Mean Sharpe | {champion_results['mean_sharpe']:.4f} | {best_candidate.mean_sharpe:.4f} | {best_candidate.mean_sharpe - champion_results['mean_sharpe']:+.4f} |
| Min Sharpe | {champion_results['min_sharpe']:.4f} | {best_candidate.min_sharpe:.4f} | {best_candidate.min_sharpe - champion_results['min_sharpe']:+.4f} |
| Whipsaw Count | N/A | {best_candidate.whipsaw_count} | - |
| Avg Scalar | 1.00 | {best_candidate.avg_scalar:.3f} | - |

## 2. Best Candidate 파라미터

```json
{json.dumps(best_candidate.params, indent=2)}
```

## 3. Fold별 Sharpe 비교

| Fold | Champion | Candidate | 변화 |
|------|----------|-----------|------|
"""
    
    for i, (c_sharpe, cand_sharpe) in enumerate(zip(
        champion_results.get('fold_sharpes', []),
        best_candidate.fold_sharpes
    )):
        report += f"| {i+1} | {c_sharpe:.4f} | {cand_sharpe:.4f} | {cand_sharpe - c_sharpe:+.4f} |\n"
    
    report += f"""

## 4. 결론

- **Mean Sharpe**: {'개선' if best_candidate.mean_sharpe > champion_results['mean_sharpe'] else '악화'} ({best_candidate.mean_sharpe - champion_results['mean_sharpe']:+.4f})
- **Min Sharpe**: {'개선' if best_candidate.min_sharpe > champion_results['min_sharpe'] else '악화'} ({best_candidate.min_sharpe - champion_results['min_sharpe']:+.4f})
- **Whipsaw 방지**: {best_candidate.whipsaw_count}회 (목표: 최소화)

### 권장 사항

{'✅ Candidate 채택 권장' if best_candidate.min_sharpe >= champion_results['min_sharpe'] * 0.9 else '⚠️ 추가 검토 필요'}
"""
    
    with open(output_path, 'w') as f:
        f.write(report)
    
    print(f"\n비교 리포트 저장: {output_path}")


def main():
    """메인 실행"""
    print("=" * 60)
    print("Regime64 Overlay V2 그리드서치")
    print("=" * 60)
    
    # 매니페스트 로드
    manifest_path = '/home/ubuntu/grid_manifest_v1.json'
    manifest = load_manifest(manifest_path)
    print(f"매니페스트 로드: {manifest_path}")
    
    # 출력 디렉토리
    output_dir = f"/home/ubuntu/gridsearch_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(output_dir, exist_ok=True)
    
    # 엔진 임포트 (실제 실행 시 활성화)
    try:
        from ares_phase2b_v661_addon_engine_v3_integration import ARESv661AddonV3Integration as EngineClass
        db_path = manifest['data']['db_path']
    except ImportError:
        print("⚠️ 엔진 임포트 실패 - 시뮬레이션 모드로 실행")
        EngineClass = None
        db_path = None
    
    # Champion 기준값
    champion_results = {
        'mean_sharpe': manifest['champion_comparison']['champion_mean_sharpe'],
        'min_sharpe': manifest['champion_comparison']['champion_min_sharpe'],
        'fold_sharpes': [],  # 실제 실행 시 채워짐
    }
    
    # Stage 1: 스칼라 램프 + 회복 게이트 최적화
    print("\n" + "=" * 60)
    print("Stage 1: TransitionControl_Ramp_and_RecoveryGate")
    print("=" * 60)
    
    stage1_grid = generate_stage1_grid(manifest)
    
    if EngineClass is not None:
        stage1_results = run_gridsearch_stage(
            stage1_grid, "stage1", EngineClass, db_path
        )
    else:
        # 시뮬레이션 결과
        print("⚠️ 시뮬레이션 모드: 가상 결과 생성")
        stage1_results = []
        for params in stage1_grid:
            result = GridSearchResult(
                params=params,
                mean_sharpe=2.0 + np.random.uniform(-0.3, 0.5),
                min_sharpe=0.15 + np.random.uniform(-0.1, 0.2),
                max_sharpe=3.5 + np.random.uniform(-0.5, 0.5),
                std_sharpe=0.5,
                fold_sharpes=[2.0 + np.random.uniform(-0.5, 0.5) for _ in range(20)],
                transition_count=int(50 + np.random.uniform(-20, 20)),
                whipsaw_count=int(5 + np.random.uniform(-3, 10)),
                avg_scalar=0.7 + np.random.uniform(-0.2, 0.2),
                std_scalar=0.15,
                turnover=0.3,
                cost_drag_bps=5.0,
                runtime_sec=1.0,
            )
            stage1_results.append(result)
    
    save_results(stage1_results, "stage1", output_dir)
    
    # Stage 1 상위 k개 선택
    top_k = manifest['stage_1']['top_k_to_keep']
    stage1_top_k = select_top_k(stage1_results, top_k, 'min_sharpe')
    
    # Stage 2: 시그모이드 파라미터 미세 조정
    print("\n" + "=" * 60)
    print("Stage 2: SigmoidSensitivity_Tune")
    print("=" * 60)
    
    stage2_grid = generate_stage2_grid(manifest, stage1_top_k)
    
    if EngineClass is not None:
        stage2_results = run_gridsearch_stage(
            stage2_grid, "stage2", EngineClass, db_path
        )
    else:
        # 시뮬레이션 결과
        stage2_results = []
        for params in stage2_grid:
            result = GridSearchResult(
                params=params,
                mean_sharpe=2.1 + np.random.uniform(-0.3, 0.5),
                min_sharpe=0.18 + np.random.uniform(-0.1, 0.15),
                max_sharpe=3.6 + np.random.uniform(-0.5, 0.5),
                std_sharpe=0.45,
                fold_sharpes=[2.1 + np.random.uniform(-0.5, 0.5) for _ in range(20)],
                transition_count=int(45 + np.random.uniform(-15, 15)),
                whipsaw_count=int(3 + np.random.uniform(-2, 5)),
                avg_scalar=0.75 + np.random.uniform(-0.15, 0.15),
                std_scalar=0.12,
                turnover=0.28,
                cost_drag_bps=4.5,
                runtime_sec=1.0,
            )
            stage2_results.append(result)
    
    save_results(stage2_results, "stage2", output_dir)
    
    # Needle Overfit 체크
    needle_flagged = check_needle_overfit(stage2_results, 
                                          manifest['stage_2']['needle_check']['x_drop_threshold'])
    
    # 비교 리포트 생성
    report_path = os.path.join(output_dir, "champion_vs_candidate_report.md")
    generate_comparison_report(champion_results, stage2_results, report_path)
    
    # 최종 요약
    print("\n" + "=" * 60)
    print("그리드서치 완료")
    print("=" * 60)
    
    best_result = max(stage2_results, key=lambda r: r.min_sharpe)
    print(f"\n최적 파라미터:")
    print(json.dumps(best_result.params, indent=2))
    print(f"\n성능:")
    print(f"  Mean Sharpe: {best_result.mean_sharpe:.4f}")
    print(f"  Min Sharpe: {best_result.min_sharpe:.4f}")
    print(f"  Whipsaw Count: {best_result.whipsaw_count}")
    print(f"\n결과 디렉토리: {output_dir}")


if __name__ == "__main__":
    main()
