import json
import glob
import os

results = []

# 모든 결과 파일 검색
for pattern in ["results/*.json", "baseline/results/*.json", "aub_runs_integration/*.json", "stage*/**/result.json"]:
    for f in glob.glob(pattern, recursive=True):
        try:
            with open(f) as fp:
                data = json.load(fp)
            summary = data.get("summary", {})
            sharpe = summary.get("oos_sharpe_mean", 0)
            
            # 비정상적인 Sharpe 제외
            if sharpe > 10 or sharpe < 0:
                continue
            
            # Exposure 확인
            folds = data.get("folds", [])
            if not folds:
                continue
                
            exposures = []
            valid_folds = 0
            for fold in folds:
                exp = fold.get("diagnostics", {}).get("avg_exposure", 0)
                if exp > 0.3:  # 30% 이상
                    valid_folds += 1
                exposures.append(exp)
            
            avg_exp = sum(exposures) / len(exposures) * 100 if exposures else 0
            
            # 평균 Exposure 30% 이상만
            if avg_exp < 30:
                continue
            
            results.append({
                "file": f,
                "sharpe": sharpe,
                "avg_exposure": avg_exp,
                "valid_folds": valid_folds,
                "total_folds": len(folds)
            })
        except Exception as e:
            pass

results.sort(key=lambda x: x["sharpe"], reverse=True)

print("정상 Exposure(>30%)를 가진 높은 Sharpe 결과:")
print("| Sharpe | Avg Exp | Valid/Total | 파일 |")
print("|--------|---------|-------------|------|")
for r in results[:15]:
    fname = r["file"][:50]
    print(f"| {r['sharpe']:.4f} | {r['avg_exposure']:.1f}% | {r['valid_folds']}/{r['total_folds']} | {fname} |")
