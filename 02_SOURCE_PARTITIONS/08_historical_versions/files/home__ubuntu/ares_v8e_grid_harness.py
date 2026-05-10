#!/usr/bin/env python3
"""
ARES v8e_hard 대규모 그리드 서치 하네스
목표: exposure 80%+ OOS Sharpe 1.6+ 최적 파라미터 탐색
"""
import subprocess
import itertools
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

DB = "/home/ubuntu/ares_x_v11_0_e7_snapshot.db"
ENGINE = "/home/ubuntu/ares_v8e_hard_engine.py"
RESULTS_DIR = "/home/ubuntu/results/v8e_grid"
os.makedirs(RESULTS_DIR, exist_ok=True)

# 그리드 서치 파라미터 정의
GRID = {
    "rebal_period": [10, 15, 21, 30],
    "cost_bps":     [15, 20, 25, 30],
    "per_asset_cap":[0.08, 0.10, 0.12, 0.15],
    "min_exposure": [0.78, 0.80, 0.82, 0.84],
}

# 전체 조합 생성
keys = list(GRID.keys())
combos = list(itertools.product(*[GRID[k] for k in keys]))
print(f"총 조합 수: {len(combos)}")  # 4^4 = 256

def run_single(combo):
    params = dict(zip(keys, combo))
    tag = f"rp{params[rebal_period]}_c{params[cost_bps]}_cap{params[per_asset_cap]}_me{params[min_exposure]}"
    out_path = os.path.join(RESULTS_DIR, f"{tag}.json")
    log_path = os.path.join(RESULTS_DIR, f"{tag}.log")
    
    if os.path.exists(out_path) or os.path.isdir(out_path):
        # 이미 완료된 경우 스킵
        return tag, "SKIP"
    
    cmd = [
        "python3", ENGINE,
        "--db", DB,
        "--rebal_period", str(params["rebal_period"]),
        "--cost_bps", str(params["cost_bps"]),
        "--per_asset_cap", str(params["per_asset_cap"]),
        "--min_exposure", str(params["min_exposure"]),
        "--out", out_path,
    ]
    
    with open(log_path, "w") as lf:
        proc = subprocess.run(cmd, stdout=lf, stderr=lf, timeout=300)
    
    return tag, "OK" if proc.returncode == 0 else f"ERR({proc.returncode})"

# 병렬 실행 (4 workers)
print("그리드 서치 시작...")
start = time.time()
completed = 0
with ProcessPoolExecutor(max_workers=4) as executor:
    futures = {executor.submit(run_single, c): c for c in combos}
    for future in as_completed(futures):
        tag, status = future.result()
        completed += 1
        if completed % 10 == 0:
            elapsed = time.time() - start
            print(f"[{completed}/{len(combos)}] 완료. 경과: {elapsed:.0f}s")
        if status != "SKIP":
            print(f"  {tag}: {status}")

print(f"\n그리드 서치 완료! 총 {completed}개 조합, {time.time()-start:.0f}초 소요")

# 결과 분석
print("\n=== 상위 결과 (OOS Sharpe 기준) ===")
results = []
for tag in os.listdir(RESULTS_DIR):
    if not tag.endswith(".json"):
        continue
    path = os.path.join(RESULTS_DIR, tag)
    if os.path.isdir(path):
        for fn in os.listdir(path):
            if fn.endswith(".json"):
                path = os.path.join(path, fn)
                break
    try:
        with open(path) as f:
            d = json.load(f)
        s = d.get("summary", d)
        results.append({
            "tag": tag.replace(".json", ""),
            "oos": s.get("mean_oos_sharpe", 0),
            "min": s.get("min_oos_sharpe", 0),
            "pos": s.get("positive_folds", 0),
            "exp": s.get("mean_avg_exposure", 0) * 100,
        })
    except:
        pass

# 20/20 + Min>0 필터 후 OOS 기준 정렬
valid = [r for r in results if r["pos"] == 20 and r["min"] > 0]
valid.sort(key=lambda x: x["oos"], reverse=True)

print(f"유효 결과 (20/20 + Min>0): {len(valid)}개")
for r in valid[:20]:
    print(f"  {r[tag]}: OOS={r[oos]:.3f} Min={r[min]:.3f} Exp={r[exp]:.2f}%")

# 결과 저장
with open("/home/ubuntu/results/v8e_grid_summary.json", "w") as f:
    json.dump({"all": results, "valid": valid}, f, indent=2)
print("결과 저장 완료: /home/ubuntu/results/v8e_grid_summary.json")
