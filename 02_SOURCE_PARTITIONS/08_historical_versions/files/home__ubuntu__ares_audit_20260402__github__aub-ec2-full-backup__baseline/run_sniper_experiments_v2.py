#!/usr/bin/env python3
"""
Sniper Alpha 실험 실행 스크립트 v2
"""
import subprocess
import json
import os
from datetime import datetime

ENGINE = "ares_phase2b_v661_addon_engine_v3_integration.py"
DB_PATH = "/home/ubuntu/AUB/data/etf_data_s3.db"
REPORT_DIR = f"reports/sniper_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
os.makedirs(REPORT_DIR, exist_ok=True)

# Champion 기준 (oos_sharpe_mean 기준)
CHAMPION = {
    "oos_sharpe_mean": 1.88,
    "lockbox_mean": 1.79,
    "lockbox_min": 1.13,
}

# 실험 정의
EXPERIMENTS = [
    {
        "id": "exp_sb_corr_filter",
        "args": ["--enable_sb_corr", "1", "--sb_corr_window", "60", "--sb_corr_threshold", "0.4", "--sb_corr_penalty", "0.15"],
    },
    {
        "id": "exp_vix_ts_brake",
        "args": ["--enable_vix_ts", "1", "--vix_ts_penalty", "0.20"],
    },
    {
        "id": "exp_intra_mom",
        "args": ["--enable_intra_mom", "1", "--intra_mom_window", "20", "--intra_mom_tilt", "0.10"],
    },
    {
        "id": "exp_sniper_combo",
        "args": [
            "--enable_sb_corr", "1", "--sb_corr_penalty", "0.12",
            "--enable_vix_ts", "1", "--vix_ts_penalty", "0.15",
        ],
    },
    {
        "id": "exp_sniper_full",
        "args": [
            "--enable_sb_corr", "1", "--sb_corr_penalty", "0.10",
            "--enable_vix_ts", "1", "--vix_ts_penalty", "0.12",
            "--enable_intra_mom", "1", "--intra_mom_tilt", "0.08",
        ],
    },
]

def run_experiment(exp_id, extra_args, n_folds=20):
    output_file = f"{REPORT_DIR}/{exp_id}.json"
    cmd = [
        "python3", ENGINE,
        "--db", DB_PATH,
        "--n_folds", str(n_folds),
        "--output", output_file,
    ] + extra_args
    
    print(f"\n=== Running {exp_id} ===")
    print(f"Command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"Error: {result.stderr[:500]}")
            return None
        
        with open(output_file) as f:
            data = json.load(f)
        
        # summary에서 값 추출
        summary = data.get("summary", {})
        oos_mean = summary.get("oos_sharpe_mean", 0)
        oos_min = summary.get("oos_sharpe_min", 0)
        positive_folds = summary.get("positive_folds", 0)
        total_folds = summary.get("total_folds", 20)
        
        # Champion 비교
        status = "PASS" if oos_mean >= CHAMPION["oos_sharpe_mean"] else "FAIL"
        
        print(f"OOS Sharpe Mean: {oos_mean:.4f} (Champion: {CHAMPION['oos_sharpe_mean']:.4f})")
        print(f"OOS Sharpe Min: {oos_min:.4f}")
        print(f"Positive Folds: {positive_folds}/{total_folds}")
        print(f"Status: {status}")
        
        return {
            "id": exp_id,
            "oos_sharpe_mean": oos_mean,
            "oos_sharpe_min": oos_min,
            "positive_folds": positive_folds,
            "total_folds": total_folds,
            "status": status,
        }
    except Exception as e:
        print(f"Exception: {e}")
        return None

def main():
    results = []
    
    for exp in EXPERIMENTS:
        result = run_experiment(exp["id"], exp["args"])
        if result:
            results.append(result)
    
    # 결과 요약
    print("\n" + "="*70)
    print("=== SNIPER ALPHA EXPERIMENT RESULTS ===")
    print("="*70)
    print(f"{'Experiment':<25} {'OOS Mean':>12} {'OOS Min':>12} {'Pos Folds':>12} {'Status':>8}")
    print("-"*70)
    for r in results:
        print(f"{r['id']:<25} {r['oos_sharpe_mean']:>12.4f} {r['oos_sharpe_min']:>12.4f} {r['positive_folds']:>8}/{r['total_folds']:<3} {r['status']:>8}")
    print("-"*70)
    print(f"{'Champion':<25} {CHAMPION['oos_sharpe_mean']:>12.4f}")
    
    # 결과 저장
    with open(f"{REPORT_DIR}/summary.json", "w") as f:
        json.dump({"experiments": results, "champion": CHAMPION}, f, indent=2)
    
    print(f"\nResults saved to {REPORT_DIR}/")

if __name__ == "__main__":
    main()
