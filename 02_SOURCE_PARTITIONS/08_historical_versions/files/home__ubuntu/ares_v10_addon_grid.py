#!/usr/bin/env python3
"""
ARES v10 Addon Grid Search Harness (Optimized)
================================================
v8e 챔피언(rp21, c15, cap0.1, me0.84, Sharpe 1.5335) 위에
v10 addon 3개를 on/off + 파라미터 조합으로 그리드 서치.

v10 Addon:
  A) confidence_allocator: 팩터 신뢰도 기반 가중 배분
  B) dynamic_top_k: 레짐별 동적 top_k 조정
  C) regime_bonus_in_sleeves: 레짐별 슬리브 보너스 가중

~46초/조합이므로 핵심 조합만 탐색 (약 100개 → ~77분)
"""
import subprocess, itertools, json, os, time, sys
from concurrent.futures import ProcessPoolExecutor, as_completed

DB = "/home/ubuntu/ares_x_v11_0_e7_snapshot.db"
ENGINE = "/home/ubuntu/ares_v10_addon_engine.py"
RESULTS_DIR = "/home/ubuntu/results/v10_addon_grid"
os.makedirs(RESULTS_DIR, exist_ok=True)

# ── v8e 챔피언 base 파라미터 (고정) ──
BASE = {
    "rebal_period": 21,
    "cost_bps": 15,
    "per_asset_cap": 0.10,
    "min_exposure": 0.84,
    "top_k": 8,
}

# ── 조합 생성 (효율적) ──
combos = []

# 0. Baseline (addon 없음) - 이미 실행됨, skip 가능
combos.append({"conf_en": 0, "conf_lb": 60, "conf_mp": 20,
               "dyn_en": 0, "dyn_bull": 0, "dyn_crisis": 0,
               "rb_en": 0, "rb_str": 0.0, "tag": "baseline"})

# 1. Addon A만 (confidence_allocator)
for lb, mp in [(40, 15), (60, 20), (90, 20), (60, 30)]:
    combos.append({"conf_en": 1, "conf_lb": lb, "conf_mp": mp,
                   "dyn_en": 0, "dyn_bull": 0, "dyn_crisis": 0,
                   "rb_en": 0, "rb_str": 0.0,
                   "tag": f"A_conf{lb}_{mp}"})

# 2. Addon B만 (dynamic_top_k)
for bull, crisis in [(1, 0), (2, 0), (3, 0), (0, 1), (0, 2), (1, 1), (2, 1), (2, 2), (3, 2)]:
    combos.append({"conf_en": 0, "conf_lb": 60, "conf_mp": 20,
                   "dyn_en": 1, "dyn_bull": bull, "dyn_crisis": crisis,
                   "rb_en": 0, "rb_str": 0.0,
                   "tag": f"B_dyn+{bull}-{crisis}"})

# 3. Addon C만 (regime_bonus)
for strength in [0.05, 0.1, 0.15, 0.2, 0.3, 0.5]:
    combos.append({"conf_en": 0, "conf_lb": 60, "conf_mp": 20,
                   "dyn_en": 0, "dyn_bull": 0, "dyn_crisis": 0,
                   "rb_en": 1, "rb_str": strength,
                   "tag": f"C_rb{strength}"})

# 4. A+B 조합 (best A × best B 후보)
for (lb, mp), (bull, crisis) in itertools.product(
    [(40, 15), (60, 20), (90, 20)],
    [(1, 1), (2, 1), (2, 2)]
):
    combos.append({"conf_en": 1, "conf_lb": lb, "conf_mp": mp,
                   "dyn_en": 1, "dyn_bull": bull, "dyn_crisis": crisis,
                   "rb_en": 0, "rb_str": 0.0,
                   "tag": f"AB_conf{lb}_{mp}_dyn+{bull}-{crisis}"})

# 5. A+C 조합
for (lb, mp), strength in itertools.product(
    [(40, 15), (60, 20), (90, 20)],
    [0.1, 0.2, 0.3]
):
    combos.append({"conf_en": 1, "conf_lb": lb, "conf_mp": mp,
                   "dyn_en": 0, "dyn_bull": 0, "dyn_crisis": 0,
                   "rb_en": 1, "rb_str": strength,
                   "tag": f"AC_conf{lb}_{mp}_rb{strength}"})

# 6. B+C 조합
for (bull, crisis), strength in itertools.product(
    [(1, 1), (2, 1), (2, 2)],
    [0.1, 0.2, 0.3]
):
    combos.append({"conf_en": 0, "conf_lb": 60, "conf_mp": 20,
                   "dyn_en": 1, "dyn_bull": bull, "dyn_crisis": crisis,
                   "rb_en": 1, "rb_str": strength,
                   "tag": f"BC_dyn+{bull}-{crisis}_rb{strength}"})

# 7. A+B+C 전부 (핵심 조합)
for (lb, mp), (bull, crisis), strength in itertools.product(
    [(40, 15), (60, 20), (90, 20)],
    [(1, 1), (2, 1), (2, 2)],
    [0.1, 0.2, 0.3]
):
    combos.append({"conf_en": 1, "conf_lb": lb, "conf_mp": mp,
                   "dyn_en": 1, "dyn_bull": bull, "dyn_crisis": crisis,
                   "rb_en": 1, "rb_str": strength,
                   "tag": f"ABC_conf{lb}_{mp}_dyn+{bull}-{crisis}_rb{strength}"})

print(f"총 조합 수: {len(combos)}")
print(f"예상 소요 시간: {len(combos) * 46 / 60:.0f}분 (4 workers: {len(combos) * 46 / 60 / 4:.0f}분)")

def run_single(combo):
    tag = combo["tag"]
    out_path = os.path.join(RESULTS_DIR, f"{tag}.json")
    log_path = os.path.join(RESULTS_DIR, f"{tag}.log")
    
    if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
        return tag, "SKIP"
    
    cmd = [
        "python3", ENGINE,
        "--db", DB,
        "--rebal_period", str(BASE["rebal_period"]),
        "--cost_bps", str(BASE["cost_bps"]),
        "--per_asset_cap", str(BASE["per_asset_cap"]),
        "--min_exposure", str(BASE["min_exposure"]),
        "--top_k", str(BASE["top_k"]),
        "--enable_confidence_allocator", str(combo["conf_en"]),
        "--conf_lookback", str(combo["conf_lb"]),
        "--conf_min_periods", str(combo["conf_mp"]),
        "--enable_dynamic_top_k", str(combo["dyn_en"]),
        "--dyn_topk_bull_extra", str(combo["dyn_bull"]),
        "--dyn_topk_crisis_reduce", str(combo["dyn_crisis"]),
        "--enable_regime_bonus", str(combo["rb_en"]),
        "--regime_bonus_strength", str(combo["rb_str"]),
        "--out", out_path,
    ]
    
    try:
        with open(log_path, "w") as lf:
            proc = subprocess.run(cmd, stdout=lf, stderr=lf, timeout=180)
        return tag, "OK" if proc.returncode == 0 else f"ERR({proc.returncode})"
    except subprocess.TimeoutExpired:
        return tag, "TIMEOUT"
    except Exception as e:
        return tag, f"EXC({e})"

print(f"\nv10 Addon Grid Search 시작 (4 workers)...")
start = time.time()
completed = 0
errors = []

with ProcessPoolExecutor(max_workers=4) as executor:
    futures = {executor.submit(run_single, c): c for c in combos}
    for future in as_completed(futures):
        tag, status = future.result()
        completed += 1
        elapsed = time.time() - start
        rate = completed / elapsed if elapsed > 0 else 0
        eta = (len(combos) - completed) / rate / 60 if rate > 0 else 0
        if completed % 10 == 0 or status not in ("SKIP", "OK"):
            print(f"[{completed}/{len(combos)}] {tag}: {status} | {elapsed:.0f}s | ETA {eta:.1f}min")
        if status not in ("SKIP", "OK"):
            errors.append((tag, status))

elapsed = time.time() - start
print(f"\n완료! {completed}개, {elapsed:.0f}초 ({elapsed/60:.1f}분), 에러: {len(errors)}개")
for tag, status in errors:
    print(f"  ERR: {tag} → {status}")

# ── 결과 분석 ──
results = []
for fn in os.listdir(RESULTS_DIR):
    if not fn.endswith(".json") or fn == "grid_summary.json":
        continue
    path = os.path.join(RESULTS_DIR, fn)
    if os.path.getsize(path) < 50:
        continue
    try:
        with open(path) as f:
            d = json.load(f)
        s = d.get("summary", d)
        results.append({
            "tag": fn.replace(".json", ""),
            "oos_sharpe": s.get("mean_oos_sharpe", s.get("oos_sharpe_mean", 0)),
            "min_sharpe": s.get("min_oos_sharpe", s.get("oos_sharpe_min", 0)),
            "pos_folds": s.get("positive_folds", 0),
            "total_folds": s.get("total_folds", 20),
            "exposure": s.get("mean_avg_exposure", s.get("avg_exposure", 0)),
            "oos_is_ratio": s.get("oos_is_ratio", 0),
            "all_passed": d.get("all_passed", False),
        })
    except Exception as e:
        pass

results.sort(key=lambda x: x["oos_sharpe"], reverse=True)

# baseline 찾기
baseline = [r for r in results if r["tag"] == "baseline"]
bl_sharpe = baseline[0]["oos_sharpe"] if baseline else 1.5335

print(f"\n{'='*90}")
print(f"v10 Addon Grid Search 결과 ({len(results)}개)")
print(f"{'='*90}")
if baseline:
    bl = baseline[0]
    print(f"\n[BASELINE] OOS={bl['oos_sharpe']:.4f} Min={bl['min_sharpe']:.4f} "
          f"Pos={bl['pos_folds']}/{bl['total_folds']} Exp={bl['exposure']:.4f}")

print(f"\n{'Tag':<50} {'OOS':>8} {'Delta':>8} {'Min':>8} {'Pos':>6} {'Exp':>8} {'Pass':>5}")
print("-" * 90)
for r in results[:30]:
    exp_pct = r['exposure'] * 100 if r['exposure'] < 2 else r['exposure']
    delta = r['oos_sharpe'] - bl_sharpe
    print(f"{r['tag']:<50} {r['oos_sharpe']:>8.4f} {delta:>+8.4f} {r['min_sharpe']:>8.4f} "
          f"{r['pos_folds']:>3}/{r['total_folds']:<2} {exp_pct:>7.1f}% "
          f"{'PASS' if r['all_passed'] else 'FAIL':>5}")

# 20/20 + min>0 필터
valid = [r for r in results if r["pos_folds"] == 20 and r["min_sharpe"] > 0]
valid.sort(key=lambda x: x["oos_sharpe"], reverse=True)

print(f"\n[Valid (20/20 + Min>0): {len(valid)}개]")
for r in valid[:15]:
    exp_pct = r['exposure'] * 100 if r['exposure'] < 2 else r['exposure']
    delta = r['oos_sharpe'] - bl_sharpe
    print(f"  {r['tag']:<50} OOS={r['oos_sharpe']:.4f} (Δ{delta:+.4f}) "
          f"Min={r['min_sharpe']:.4f} Exp={exp_pct:.1f}%")

# 개선된 것만
improved = [r for r in valid if r['oos_sharpe'] > bl_sharpe]
print(f"\n[Improved over baseline: {len(improved)}개]")
for r in improved:
    delta = r['oos_sharpe'] - bl_sharpe
    print(f"  ✅ {r['tag']:<48} OOS={r['oos_sharpe']:.4f} (Δ{delta:+.4f})")

# 저장
with open(os.path.join(RESULTS_DIR, "grid_summary.json"), "w") as f:
    json.dump({"all": results, "valid": valid, "improved": improved,
               "errors": errors, "baseline_sharpe": bl_sharpe,
               "total_combos": len(combos), "elapsed_sec": elapsed}, f, indent=2)

print(f"\n결과 저장: {RESULTS_DIR}/grid_summary.json")
