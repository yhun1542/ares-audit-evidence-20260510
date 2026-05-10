#!/usr/bin/env python3
"""
두 모드(rebal_enable=0/1)의 리밸 시점 동일성 검증
- rebal_ts_hash가 같으면 리밸 타이밍 동일 확정
"""
import sys
sys.path.insert(0, "/home/ubuntu/AUB/baseline")

from ares_phase2b_v661_addon_engine_v3_integration import ARESv661Addon

# 공통 설정
common_args = {
    "db_path": "/home/ubuntu/ares_x_v11_0.db",
    "start_date": "2020-01-01",
    "enable_rate_shock_gate": True,
    "rate_shock_mode": "apply",
    "rate_shock_auto_th": True,  # auto_calibrate -> auto_th
    "rebal_period": 5,
}

print("="*60)
print("Rate Shock Gate 리밸 시점 동일성 검증")
print("="*60)

# Mode 0: 스케일링만 (기본)
print("\n[Mode 0] rate_shock_rebal_enable=0 (스케일링만)")
engine0 = ARESv661Addon(**common_args, rate_shock_rebal_enable=False)
result0 = engine0.run_walk_forward(n_folds=16)

# Mode 1: 스케일링 + 리밸 단축
print("\n[Mode 1] rate_shock_rebal_enable=1 (스케일링 + 리밸 단축)")
engine1 = ARESv661Addon(**common_args, rate_shock_rebal_enable=True, rate_shock_rebal_period=10)
result1 = engine1.run_walk_forward(n_folds=16)

print("\n" + "="*60)
print("리밸 시점 해시 비교")
print("="*60)

folds0 = result0.get("folds", [])
folds1 = result1.get("folds", [])

hash_match = 0
hash_diff = 0
count_match = 0
count_diff = 0

for i, (f0, f1) in enumerate(zip(folds0, folds1)):
    fold_num = f0.get("fold", i+1)
    
    # rebal_ts_hash 비교
    hash0 = f0.get("rebal_ts_hash", "N/A")
    hash1 = f1.get("rebal_ts_hash", "N/A")
    
    # rebal_count 비교
    count0 = f0.get("rebal_count", -1)
    count1 = f1.get("rebal_count", -1)
    
    # trigger/active days 비교
    diag0 = f0.get("diagnostics", {}).get("addon", {}).get("rate_shock_gate", {})
    diag1 = f1.get("diagnostics", {}).get("addon", {}).get("rate_shock_gate", {})
    
    trigger0 = diag0.get("trigger_days", 0)
    trigger1 = diag1.get("trigger_days", 0)
    active0 = diag0.get("active_days", 0)
    active1 = diag1.get("active_days", 0)
    
    hash_ok = "✅" if hash0 == hash1 else "❌"
    count_ok = "✅" if count0 == count1 else "❌"
    
    if hash0 == hash1:
        hash_match += 1
    else:
        hash_diff += 1
    
    if count0 == count1:
        count_match += 1
    else:
        count_diff += 1
    
    print(f"Fold {fold_num:2d}: hash={hash_ok} ({hash0} vs {hash1}), count={count_ok} ({count0} vs {count1}), trigger={trigger0}, active={active0}")

print("\n" + "="*60)
print("요약")
print("="*60)
print(f"해시 일치: {hash_match}/{len(folds0)} folds")
print(f"해시 불일치: {hash_diff}/{len(folds0)} folds")
print(f"카운트 일치: {count_match}/{len(folds0)} folds")
print(f"카운트 불일치: {count_diff}/{len(folds0)} folds")

# OOS Sharpe 비교
oos0 = [f.get("oos_sharpe", 0) for f in folds0]
oos1 = [f.get("oos_sharpe", 0) for f in folds1]
import numpy as np
mean0 = np.mean(oos0)
mean1 = np.mean(oos1)
print(f"\nOOS Sharpe Mean: Mode0={mean0:.4f}, Mode1={mean1:.4f}, Diff={mean1-mean0:.4f}")

if hash_diff == 0:
    print("\n✅ 결론: 두 모드의 리밸 시점이 100% 동일합니다.")
    print("   미세 차이는 부동소수점/누적 순서의 차이로 설명 가능합니다.")
else:
    print(f"\n⚠️ 결론: {hash_diff}개 fold에서 리밸 시점이 다릅니다.")
    print("   active=True일 때 rebal_enable=1은 cur_rebal을 단축하므로 차이가 발생합니다.")
