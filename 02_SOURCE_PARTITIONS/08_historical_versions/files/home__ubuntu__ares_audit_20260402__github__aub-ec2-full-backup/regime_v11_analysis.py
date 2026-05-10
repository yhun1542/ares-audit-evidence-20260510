import json

# V6와 V7 결과 파일 로드
v6_file = "results/one_truth_verify_20260105_062217.json"
v7_file = "results/onetruth_regime_v11_20260105_065306.json"

with open(v6_file) as f:
    v6_data = json.load(f)
with open(v7_file) as f:
    v7_data = json.load(f)

print("=" * 80)
print("보강 옵션 3: Regime_v11 행동 변화 분석 (Crisis Fold 16)")
print("=" * 80)

# Crisis Fold (Fold 16) 찾기
v6_fold16 = None
v7_fold16 = None

for f in v6_data["folds"]:
    if f["fold"] == 16:
        v6_fold16 = f
        break

for f in v7_data["folds"]:
    if f["fold"] == 16:
        v7_fold16 = f
        break

if v6_fold16 and v7_fold16:
    print("\n[Crisis Fold 16 비교]")
    print("-" * 80)
    
    metrics = [
        ("OOS Sharpe", "oos_sharpe", ".4f", ""),
        ("OOS Return", "oos_return", ".2f", "%"),
        ("Max Drawdown", "oos_mdd", ".2f", "%"),
        ("Avg Exposure", "avg_exposure", ".2f", "%"),
        ("Avg Turnover", "avg_turnover", ".4f", ""),
        ("Avg Hedge", "avg_hedge", ".2f", "%"),
    ]
    
    print(f"{'Metric':<25} | {'V6':>15} | {'V7':>15} | {'Delta':>15}")
    print("-" * 80)
    
    for name, key, fmt, suffix in metrics:
        v6_val = v6_fold16.get(key, 0)
        v7_val = v7_fold16.get(key, 0)
        delta = v7_val - v6_val
        print(f"{name:<25} | {v6_val:>14{fmt}}{suffix} | {v7_val:>14{fmt}}{suffix} | {delta:>+14{fmt}}{suffix}")
    
    print("-" * 80)
    
    # 요약 문장
    v6_sharpe = v6_fold16.get("oos_sharpe", 0)
    v7_sharpe = v7_fold16.get("oos_sharpe", 0)
    v6_mdd = v6_fold16.get("oos_mdd", 0)
    v7_mdd = v7_fold16.get("oos_mdd", 0)
    v6_exp = v6_fold16.get("avg_exposure", 0)
    v7_exp = v7_fold16.get("avg_exposure", 0)
    
    print("\n[승격 보고서에 추가할 문장]")
    print("-" * 80)
    mdd_improvement = abs(v7_mdd) - abs(v6_mdd)
    exp_change = v7_exp - v6_exp
    print(f"Crisis 구간(Fold 16)에서 regime_v11 적용 결과:")
    print(f"  - OOS Sharpe: {v6_sharpe:.2f} -> {v7_sharpe:.2f} ({v7_sharpe - v6_sharpe:+.2f})")
    print(f"  - Max Drawdown: {abs(v6_mdd):.2f}% -> {abs(v7_mdd):.2f}% ({mdd_improvement:+.2f}%p 개선)")
    print(f"  - Avg Exposure: {v6_exp:.1f}% -> {v7_exp:.1f}% ({exp_change:+.1f}%p)")

# 전체 Fold 평균 비교
print("\n" + "=" * 80)
print("[전체 20 Folds 평균 비교]")
print("=" * 80)

v6_avg_exp = sum(f.get("avg_exposure", 0) for f in v6_data["folds"]) / len(v6_data["folds"])
v7_avg_exp = sum(f.get("avg_exposure", 0) for f in v7_data["folds"]) / len(v7_data["folds"])

v6_avg_mdd = sum(f.get("oos_mdd", 0) for f in v6_data["folds"]) / len(v6_data["folds"])
v7_avg_mdd = sum(f.get("oos_mdd", 0) for f in v7_data["folds"]) / len(v7_data["folds"])

v6_avg_turn = sum(f.get("avg_turnover", 0) for f in v6_data["folds"]) / len(v6_data["folds"])
v7_avg_turn = sum(f.get("avg_turnover", 0) for f in v7_data["folds"]) / len(v7_data["folds"])

print(f"{'Metric':<25} | {'V6':>15} | {'V7':>15} | {'Delta':>15}")
print("-" * 80)
print(f"{'Avg Exposure (All)':<25} | {v6_avg_exp:>14.2f}% | {v7_avg_exp:>14.2f}% | {v7_avg_exp - v6_avg_exp:>+14.2f}%")
print(f"{'Avg MDD (All)':<25} | {v6_avg_mdd:>14.2f}% | {v7_avg_mdd:>14.2f}% | {v7_avg_mdd - v6_avg_mdd:>+14.2f}%")
print(f"{'Avg Turnover (All)':<25} | {v6_avg_turn:>15.4f} | {v7_avg_turn:>15.4f} | {v7_avg_turn - v6_avg_turn:>+15.4f}")
