#!/usr/bin/env python3
"""ARES v8e_hard 256개 그리드 서치 하네스 v2"""
import subprocess, itertools, json, os, time
from concurrent.futures import ProcessPoolExecutor, as_completed

DB = "/home/ubuntu/ares_x_v11_0_e7_snapshot.db"
ENGINE = "/home/ubuntu/ares_v8e_hard_engine.py"
RESULTS_DIR = "/home/ubuntu/results/v8e_grid"
os.makedirs(RESULTS_DIR, exist_ok=True)

GRID_REBAL = [10, 15, 21, 30]
GRID_COST  = [15, 20, 25, 30]
GRID_CAP   = [0.08, 0.10, 0.12, 0.15]
GRID_MINEX = [0.78, 0.80, 0.82, 0.84]

combos = list(itertools.product(GRID_REBAL, GRID_COST, GRID_CAP, GRID_MINEX))
print(f"총 조합 수: {len(combos)}")

def run_single(args):
    rp, cb, cap, me = args
    tag = f"rp{rp}_c{cb}_cap{cap}_me{me}"
    out_path = os.path.join(RESULTS_DIR, f"{tag}.json")
    log_path = os.path.join(RESULTS_DIR, f"{tag}.log")
    if os.path.exists(out_path) or os.path.isdir(out_path):
        return tag, "SKIP"
    cmd = [
        "python3", ENGINE,
        "--db", DB,
        "--rebal_period", str(rp),
        "--cost_bps", str(cb),
        "--per_asset_cap", str(cap),
        "--min_exposure", str(me),
        "--out", out_path,
    ]
    with open(log_path, "w") as lf:
        proc = subprocess.run(cmd, stdout=lf, stderr=lf, timeout=300)
    return tag, "OK" if proc.returncode == 0 else f"ERR({proc.returncode})"

print("그리드 서치 시작 (4 workers)...")
start = time.time()
completed = 0
with ProcessPoolExecutor(max_workers=4) as executor:
    futures = {executor.submit(run_single, c): c for c in combos}
    for future in as_completed(futures):
        tag, status = future.result()
        completed += 1
        if completed % 20 == 0:
            elapsed = time.time() - start
            print(f"[{completed}/{len(combos)}] 경과: {elapsed:.0f}s")
        if status not in ("SKIP", "OK"):
            print(f"  {tag}: {status}")

print(f"\n완료! {completed}개, {time.time()-start:.0f}초")

# 결과 분석
results = []
for fn in os.listdir(RESULTS_DIR):
    if not fn.endswith(".json"):
        continue
    path = os.path.join(RESULTS_DIR, fn)
    if os.path.isdir(path):
        inner = [x for x in os.listdir(path) if x.endswith(".json")]
        if inner:
            path = os.path.join(path, inner[0])
        else:
            continue
    try:
        with open(path) as f:
            d = json.load(f)
        s = d.get("summary", d)
        results.append({
            "tag": fn.replace(".json", ""),
            "oos": s.get("mean_oos_sharpe", 0),
            "min": s.get("min_oos_sharpe", 0),
            "pos": s.get("positive_folds", 0),
            "exp": s.get("mean_avg_exposure", 0) * 100,
        })
    except:
        pass

valid = [r for r in results if r["pos"] == 20 and r["min"] > 0]
valid.sort(key=lambda x: x["oos"], reverse=True)
print(f"\n유효 결과 (20/20 + Min>0): {len(valid)}개")
for r in valid[:20]:
    print(f"  {r[tag]}: OOS={r[oos]:.3f} Min={r[min]:.3f} Exp={r[exp]:.2f}%")

with open("/home/ubuntu/results/v8e_grid_summary.json", "w") as f:
    json.dump({"all": results, "valid": valid}, f, indent=2)
print("결과 저장 완료")
