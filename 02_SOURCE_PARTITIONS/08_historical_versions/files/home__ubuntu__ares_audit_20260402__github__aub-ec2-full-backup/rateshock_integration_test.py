#!/usr/bin/env python3
"""
RateShockGate 모듈 통합 테스트
- 패치된 모든 기능의 체계적 검증
"""
import subprocess
import json
import sys
from datetime import datetime

# Base command
BASE_CMD = [
    "python3", "/home/ubuntu/AUB/baseline/ares_phase2b_v661_addon_engine_v3_integration.py",
    "--db", "/home/ubuntu/ares_x_v11_0.db",
    "--policy_json", "/home/ubuntu/AUB/baseline/v661_compat_policy_notrade_only.json",
    "--enable_hedge", "1", "--enable_cb", "1", "--enable_severity", "0", "--enable_throttle", "1",
    "--enable_compat", "1", "--enable_no_trade", "1", "--enable_icir", "0",
    "--enable_regime15", "1", "--regime15_band_mult_risk_off", "0.78", "--regime15_band_mult_crisis", "0.86",
    "--enable_vix_sizing", "1", "--vix_scale_low", "1.16", "--vix_scale_extreme", "0.74",
    "--enable_icir_v4", "1", "--icir_lookback", "120", "--icir_blend_ratio", "0.7",
    "--cost_bps", "4", "--per_asset_cap", "0.1", "--rebal_period", "15", "--top_k", "10"
]

# Test cases
TEST_CASES = [
    # 1. Baseline (RateShockGate OFF)
    {
        "name": "T01_baseline_off",
        "description": "Baseline - RateShockGate 비활성화",
        "args": {"enable_rate_shock_gate": 0},
        "expected": {"mean_sharpe_range": (1.85, 1.95), "min_sharpe_range": (0.30, 0.35)}
    },
    # 2. RateShockGate ON, mode=shadow (결과 동일해야 함)
    {
        "name": "T02_shadow_mode",
        "description": "Shadow 모드 - 결과가 baseline과 동일해야 함",
        "args": {
            "enable_rate_shock_gate": 1,
            "rate_shock_mode": "shadow",
            "rate_shock_thr_2y_5d_bp": 40,
            "rate_shock_thr_2y_20d_bp": 80
        },
        "expected": {"mean_sharpe_range": (1.85, 1.95), "min_sharpe_range": (0.30, 0.35)}
    },
    # 3. RateShockGate ON, mode=apply, 원래 임계값 (거의 안 울림)
    {
        "name": "T03_original_threshold",
        "description": "원래 임계값(80/150bp) - baseline과 거의 동일해야 함",
        "args": {
            "enable_rate_shock_gate": 1,
            "rate_shock_mode": "apply",
            "rate_shock_thr_2y_5d_bp": 80,
            "rate_shock_thr_2y_20d_bp": 150,
            "rate_shock_min_scale": 0.85
        },
        "expected": {"mean_sharpe_range": (1.85, 1.95), "min_sharpe_range": (0.30, 0.35)}
    },
    # 4. RateShockGate ON, mode=apply, 현실적 임계값
    {
        "name": "T04_realistic_threshold",
        "description": "현실적 임계값(40/80bp) - 소폭 개선 예상",
        "args": {
            "enable_rate_shock_gate": 1,
            "rate_shock_mode": "apply",
            "rate_shock_thr_2y_5d_bp": 40,
            "rate_shock_thr_2y_20d_bp": 80,
            "rate_shock_min_scale": 0.80,
            "rate_shock_risk_off_only": 0
        },
        "expected": {"mean_sharpe_range": (1.85, 1.95), "min_sharpe_range": (0.30, 0.35)}
    },
    # 5. 듀얼게이트 ON (risk_off_only=1)
    {
        "name": "T05_dualgate_on",
        "description": "듀얼게이트 ON - baseline과 동일해야 함 (대부분 차단)",
        "args": {
            "enable_rate_shock_gate": 1,
            "rate_shock_mode": "apply",
            "rate_shock_thr_2y_5d_bp": 40,
            "rate_shock_thr_2y_20d_bp": 80,
            "rate_shock_min_scale": 0.80,
            "rate_shock_risk_off_only": 1
        },
        "expected": {"mean_sharpe_range": (1.85, 1.95), "min_sharpe_range": (0.30, 0.35)}
    },
    # 6. 강제 트리거 (scale only)
    {
        "name": "T06_force_trigger_scale",
        "description": "강제 트리거(5/10bp) - Mean 상승, Min 하락 예상",
        "args": {
            "enable_rate_shock_gate": 1,
            "rate_shock_mode": "apply",
            "rate_shock_thr_2y_5d_bp": 5,
            "rate_shock_thr_2y_20d_bp": 10,
            "rate_shock_min_scale": 0.85,
            "rate_shock_risk_off_only": 0,
            "rate_shock_enable_hedge": 0
        },
        "expected": {"mean_sharpe_range": (1.95, 2.10), "min_sharpe_range": (-0.15, 0.0)}
    },
    # 7. 강제 트리거 + 헤지 10%
    {
        "name": "T07_force_trigger_hedge_10",
        "description": "강제 트리거 + 헤지 10% - 헤지 효과 확인",
        "args": {
            "enable_rate_shock_gate": 1,
            "rate_shock_mode": "apply",
            "rate_shock_thr_2y_5d_bp": 5,
            "rate_shock_thr_2y_20d_bp": 10,
            "rate_shock_min_scale": 0.85,
            "rate_shock_risk_off_only": 0,
            "rate_shock_enable_hedge": 1,
            "rate_shock_hedge_weight": 0.10,
            "rate_shock_hedge_type": "short_spy"
        },
        "expected": {"mean_sharpe_range": (1.65, 1.85), "min_sharpe_range": (0.05, 0.20)}
    },
    # 8. 강제 트리거 + 헤지 20%
    {
        "name": "T08_force_trigger_hedge_20",
        "description": "강제 트리거 + 헤지 20% - 더 강한 헤지 효과",
        "args": {
            "enable_rate_shock_gate": 1,
            "rate_shock_mode": "apply",
            "rate_shock_thr_2y_5d_bp": 5,
            "rate_shock_thr_2y_20d_bp": 10,
            "rate_shock_min_scale": 0.85,
            "rate_shock_risk_off_only": 0,
            "rate_shock_enable_hedge": 1,
            "rate_shock_hedge_weight": 0.20,
            "rate_shock_hedge_type": "short_spy"
        },
        "expected": {"mean_sharpe_range": (1.65, 1.80), "min_sharpe_range": (0.10, 0.20)}
    },
    # 9. 현실적 임계값 + 헤지 10%
    {
        "name": "T09_realistic_hedge_10",
        "description": "현실적 임계값 + 헤지 10% - 최적 조합 예상",
        "args": {
            "enable_rate_shock_gate": 1,
            "rate_shock_mode": "apply",
            "rate_shock_thr_2y_5d_bp": 40,
            "rate_shock_thr_2y_20d_bp": 80,
            "rate_shock_min_scale": 0.80,
            "rate_shock_risk_off_only": 0,
            "rate_shock_enable_hedge": 1,
            "rate_shock_hedge_weight": 0.10,
            "rate_shock_hedge_type": "short_spy"
        },
        "expected": {"mean_sharpe_range": (1.85, 1.95), "min_sharpe_range": (0.35, 0.45)}
    },
    # 10. TLT 헤지 테스트
    {
        "name": "T10_tlt_hedge",
        "description": "TLT 헤지 - long_symbol 모드 테스트",
        "args": {
            "enable_rate_shock_gate": 1,
            "rate_shock_mode": "apply",
            "rate_shock_thr_2y_5d_bp": 5,
            "rate_shock_thr_2y_20d_bp": 10,
            "rate_shock_min_scale": 0.85,
            "rate_shock_risk_off_only": 0,
            "rate_shock_enable_hedge": 1,
            "rate_shock_hedge_weight": 0.10,
            "rate_shock_hedge_type": "long_symbol",
            "rate_shock_hedge_symbol": "TLT"
        },
        "expected": {"mean_sharpe_range": (1.50, 2.00), "min_sharpe_range": (-0.20, 0.30)}
    },
]

def run_test(test_case):
    """Run a single test case"""
    name = test_case["name"]
    args = test_case["args"]
    
    cmd = BASE_CMD.copy()
    for k, v in args.items():
        cmd.extend([f"--{k}", str(v)])
    
    print(f"\n{'='*60}")
    print(f"[{name}] {test_case['description']}")
    print(f"{'='*60}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    
    # Parse results
    mean_sharpe = None
    min_sharpe = None
    fold16_oos = None
    
    for line in result.stdout.split('\n'):
        if 'OOS Sharpe Mean:' in line:
            mean_sharpe = float(line.split(':')[1].strip())
        if 'OOS Sharpe Min:' in line:
            min_sharpe = float(line.split(':')[1].strip())
        if 'Fold 16:' in line:
            parts = line.split('OOS=')
            if len(parts) > 1:
                fold16_oos = float(parts[1].split(',')[0].strip())
    
    # Check expectations
    expected = test_case["expected"]
    mean_ok = expected["mean_sharpe_range"][0] <= (mean_sharpe or 0) <= expected["mean_sharpe_range"][1]
    min_ok = expected["min_sharpe_range"][0] <= (min_sharpe or 0) <= expected["min_sharpe_range"][1]
    
    status = "✅ PASS" if (mean_ok and min_ok) else "❌ FAIL"
    
    return {
        "name": name,
        "description": test_case["description"],
        "mean_sharpe": mean_sharpe,
        "min_sharpe": min_sharpe,
        "fold16_oos": fold16_oos,
        "mean_ok": mean_ok,
        "min_ok": min_ok,
        "status": status,
        "expected": expected
    }

def main():
    print("="*80)
    print("RateShockGate 모듈 통합 테스트")
    print(f"실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    
    results = []
    passed = 0
    failed = 0
    
    for test_case in TEST_CASES:
        try:
            r = run_test(test_case)
            results.append(r)
            
            if r["status"] == "✅ PASS":
                passed += 1
            else:
                failed += 1
            
            print(f"  Mean Sharpe: {r['mean_sharpe']:.4f} (expected: {r['expected']['mean_sharpe_range']}) {'✅' if r['mean_ok'] else '❌'}")
            print(f"  Min Sharpe:  {r['min_sharpe']:.4f} (expected: {r['expected']['min_sharpe_range']}) {'✅' if r['min_ok'] else '❌'}")
            print(f"  Fold16 OOS:  {r['fold16_oos']}")
            print(f"  Status: {r['status']}")
            
        except Exception as e:
            print(f"  ❌ ERROR: {str(e)}")
            failed += 1
            results.append({
                "name": test_case["name"],
                "description": test_case["description"],
                "status": "❌ ERROR",
                "error": str(e)
            })
    
    # Summary
    print("\n" + "="*80)
    print("테스트 결과 요약")
    print("="*80)
    print(f"{'Name':<30} {'Mean':<10} {'Min':<10} {'Fold16':<10} {'Status':<10}")
    print("-"*80)
    
    for r in results:
        mean = f"{r.get('mean_sharpe', 0):.4f}" if r.get('mean_sharpe') else "N/A"
        min_s = f"{r.get('min_sharpe', 0):.4f}" if r.get('min_sharpe') else "N/A"
        fold16 = f"{r.get('fold16_oos', 0):.4f}" if r.get('fold16_oos') else "N/A"
        print(f"{r['name']:<30} {mean:<10} {min_s:<10} {fold16:<10} {r['status']:<10}")
    
    print("-"*80)
    print(f"Total: {len(results)} tests, {passed} passed, {failed} failed")
    print("="*80)
    
    # Save results
    with open('/tmp/rateshock_integration_test_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
