"""
Degenerate Guard - ASV5 레인용 검증 모듈
========================================
degenerate fold를 감지하고 INCONCLUSIVE/FAIL 처리

조건:
1. avg_exposure < 0.01 → INCONCLUSIVE
2. sharpe_status != "ok" → INCONCLUSIVE  
3. abs(oos_sharpe) > 100 → FAIL (샤프 폭발)
"""
import json
from typing import Dict, Any, List, Tuple

def check_degenerate_folds(result: Dict[str, Any]) -> Tuple[str, List[str], Dict[str, Any]]:
    """
    결과 파일에서 degenerate fold 검사
    
    Returns:
        (status, reasons, valid_summary)
        - status: "PASS", "INCONCLUSIVE", "FAIL"
        - reasons: 문제 사유 리스트
        - valid_summary: valid fold만으로 계산한 통계
    """
    folds = result.get('folds', [])
    reasons = []
    
    valid_folds = []
    degenerate_folds = []
    
    for i, fold in enumerate(folds):
        diag = fold.get('diagnostics', {})
        oos_sharpe = fold.get('oos_sharpe', 0)
        sharpe_status = fold.get('sharpe_status', 'ok')
        avg_exposure = diag.get('avg_exposure', 1.0)
        
        is_degenerate = False
        fold_reasons = []
        
        # 조건 1: avg_exposure < 0.01
        if avg_exposure < 0.01:
            is_degenerate = True
            fold_reasons.append(f"fold_{i+1}: avg_exposure={avg_exposure:.4f} < 0.01")
        
        # 조건 2: sharpe_status != "ok"
        if sharpe_status != 'ok':
            is_degenerate = True
            fold_reasons.append(f"fold_{i+1}: sharpe_status={sharpe_status}")
        
        # 조건 3: abs(oos_sharpe) > 100
        if abs(oos_sharpe) > 100:
            reasons.append(f"FAIL: fold_{i+1} sharpe={oos_sharpe:.2f} > 100 (폭발)")
            return "FAIL", reasons, {}
        
        if is_degenerate:
            degenerate_folds.append(i)
            reasons.extend(fold_reasons)
        else:
            valid_folds.append(fold)
    
    # Valid fold만으로 통계 재계산
    if len(valid_folds) == 0:
        reasons.append("FAIL: no valid folds")
        return "FAIL", reasons, {}
    
    valid_oos_sharpes = [f.get('oos_sharpe', 0) for f in valid_folds]
    valid_is_sharpes = [f.get('is_sharpe', 0) for f in valid_folds]
    
    import numpy as np
    valid_summary = {
        "valid_folds": len(valid_folds),
        "total_folds": len(folds),
        "degenerate_folds": len(degenerate_folds),
        "oos_sharpe_mean": float(np.mean(valid_oos_sharpes)),
        "oos_sharpe_min": float(np.min(valid_oos_sharpes)),
        "oos_sharpe_max": float(np.max(valid_oos_sharpes)),
        "positive_folds": sum(1 for s in valid_oos_sharpes if s > 0),
        "oos_is_ratio": float(np.mean(valid_oos_sharpes) / (np.mean(valid_is_sharpes) + 1e-12)),
    }
    
    # 최종 상태 결정
    if len(degenerate_folds) > 0:
        status = "INCONCLUSIVE"
        reasons.append(f"INCONCLUSIVE: {len(degenerate_folds)} degenerate folds detected")
    else:
        status = "PASS"
    
    return status, reasons, valid_summary


def validate_asv5_result(result_path: str) -> Dict[str, Any]:
    """
    ASV5 결과 파일 검증
    """
    with open(result_path) as f:
        result = json.load(f)
    
    status, reasons, valid_summary = check_degenerate_folds(result)
    
    # 4AI 기준 검증 (valid fold 기준)
    criteria = {}
    if valid_summary:
        criteria = {
            "oos_sharpe_mean_ge_1.5": valid_summary["oos_sharpe_mean"] >= 1.5,
            "positive_folds_ge_80%": valid_summary["positive_folds"] / valid_summary["valid_folds"] >= 0.8,
            "oos_sharpe_min_ge_-0.3": valid_summary["oos_sharpe_min"] >= -0.3,
            "oos_is_ratio_ge_0.7": valid_summary["oos_is_ratio"] >= 0.7,
            "valid_folds_ge_14": valid_summary["valid_folds"] >= 14,
        }
        all_pass = all(criteria.values())
    else:
        all_pass = False
    
    return {
        "status": status,
        "reasons": reasons,
        "valid_summary": valid_summary,
        "criteria": criteria,
        "all_pass": all_pass and status == "PASS",
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python degenerate_guard.py <result.json>")
        sys.exit(1)
    
    result = validate_asv5_result(sys.argv[1])
    print(json.dumps(result, indent=2))
