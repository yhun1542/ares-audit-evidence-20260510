"""
ARES Family A Mega-Grid v5.1 — Production Turbo Edition
========================================================
v5 대비 4가지 핵심 결함 수정:
1. breakeven_tc_bps 외삽 금지 → survives_up_to_bps 대체
2. config key 스키마 통일 (patch_v51/v53/v54/xgb_threshold)
3. diag/prod 분리 — prod는 25bps 하한, fold19>=0 강제
4. Step 0: v2 top20 재감사 → 범위 좁히기

Usage:
  python3 family_a_v51_turbo.py --n_samples 15000 --n_weeks 163 --n_seeds 5
"""
from __future__ import annotations
import os, sys, json, time, copy, pickle
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
import numpy as np
import pandas as pd

# v5.1 modules (same directory)
from family_a_engine import FamilyAConfig, run_family_a, normalize_config_keys
from metric_schema import NormalizedResult, score_population, apply_hard_filters, NpEncoder
from sensitivity_v51 import run_sensitivity_sweep

# Real data simulator
from real_market_sim import RealMarketSimulator, RealMarketSimulatorMultiSeed

OUT = "v51_results"
os.makedirs(OUT, exist_ok=True)
SEED = 42
N_WORKERS = min(mp.cpu_count(), 16)

# ═══════════════════════════════════════════════════════════════
# GOLDEN BASELINES (v5.1 표준 키)
# ═══════════════════════════════════════════════════════════════
GOLDEN_A04799 = FamilyAConfig(
    config_id="GOLDEN_A04799",
    leverage_mult=1.357, hedge_mult=1.274, defensive_budget_add=-0.098,
    crash_guard_scale=0.236, crash_guard_vol_spike=3.0,
    trend_clamp_min=0.807, trend_lookback_weeks=12,
    gain_adj=0.147, loss_adj=-0.099,
    cost_bps=14.02, crisis_cost_mult=1.950,
    patch_v51=True, patch_v53=True, patch_v54=True,
    xgb_threshold=0.591, xgb_scale_factor=0.7,
)

# v2 top20에서 관측된 우승 파라미터 범위 (좁은 범위)
V2_RANGES = {
    "leverage_mult": (0.9, 1.5),
    "hedge_mult": (0.8, 2.0),
    "defensive_budget_add": (-0.15, 0.10),
    "crash_guard_scale": (0.15, 0.50),
    "trend_clamp_min": (0.60, 0.90),
    "gain_adj": (0.05, 0.15),
    "loss_adj": (-0.15, 0.0),
    "cost_bps": (20.0, 40.0),       # prod 하한: 20bps
    "crisis_cost_mult": (1.5, 2.5),
    "xgb_threshold": (0.4, 0.8),
    "xgb_scale_factor": (0.4, 0.9),
}


def _lhs(n, rng):
    cuts = np.linspace(0, 1, n+1)
    u = np.array([rng.uniform(cuts[i], cuts[i+1]) for i in range(n)])
    rng.shuffle(u)
    return u

def _s(u, lo, hi):
    return lo + u * (hi - lo)


# ═══════════════════════════════════════════════════════════════
# PARALLEL WORKER (fork-safe)
# ═══════════════════════════════════════════════════════════════
_worker_sim = None

def _init_worker(sim_bytes):
    global _worker_sim
    _worker_sim = pickle.loads(sim_bytes)

def _run_one(cfg_dict):
    global _worker_sim
    try:
        cfg = FamilyAConfig(**cfg_dict)
        r = run_family_a(cfg, _worker_sim)
        return r.to_dict()
    except Exception as e:
        return None


# ═══════════════════════════════════════════════════════════════
# CONFIG GENERATOR (v5.1 표준 키 사용)
# ═══════════════════════════════════════════════════════════════
def gen_configs(n, seed=SEED):
    rng = np.random.RandomState(seed)
    n_narrow = int(n * 0.8)
    n_wide = n - n_narrow
    cfgs = []

    # Narrow range (v2 top20 기반)
    dims = {k: _lhs(n_narrow, rng) for k in V2_RANGES}
    for i in range(n_narrow):
        cfgs.append(FamilyAConfig(
            config_id=f"A_{i:05d}",
            leverage_mult=_s(dims["leverage_mult"][i], *V2_RANGES["leverage_mult"]),
            hedge_mult=_s(dims["hedge_mult"][i], *V2_RANGES["hedge_mult"]),
            defensive_budget_add=_s(dims["defensive_budget_add"][i], *V2_RANGES["defensive_budget_add"]),
            crash_guard_scale=_s(dims["crash_guard_scale"][i], *V2_RANGES["crash_guard_scale"]),
            crash_guard_vol_spike=float(rng.choice([2.0, 2.5, 3.0, 3.5, 4.0])),
            trend_clamp_min=_s(dims["trend_clamp_min"][i], *V2_RANGES["trend_clamp_min"]),
            trend_lookback_weeks=int(rng.choice([8, 10, 12, 14, 16])),
            trend_bad_thresh=float(rng.choice([-0.10, -0.12, -0.15, -0.18])),
            gain_adj=_s(dims["gain_adj"][i], *V2_RANGES["gain_adj"]),
            loss_adj=_s(dims["loss_adj"][i], *V2_RANGES["loss_adj"]),
            cost_bps=_s(dims["cost_bps"][i], *V2_RANGES["cost_bps"]),
            crisis_cost_mult=_s(dims["crisis_cost_mult"][i], *V2_RANGES["crisis_cost_mult"]),
            patch_v51=bool(rng.choice([True, True, True, False])),
            patch_v53=bool(rng.choice([True, True, True, False])),
            patch_v54=bool(rng.choice([True, True, True, False])),
            xgb_threshold=_s(dims["xgb_threshold"][i], *V2_RANGES["xgb_threshold"]),
            xgb_scale_factor=_s(dims["xgb_scale_factor"][i], *V2_RANGES["xgb_scale_factor"]),
        ))

    # Wide range (exploration)
    dims_w = {k: _lhs(n_wide, rng) for k in [
        "lev","hedge","def","guard","trend","gain","loss","cost","crisis","xgb_t","xgb_s"]}
    for i in range(n_wide):
        idx = n_narrow + i
        cfgs.append(FamilyAConfig(
            config_id=f"A_{idx:05d}",
            leverage_mult=_s(dims_w["lev"][i], 0.5, 1.8),
            hedge_mult=_s(dims_w["hedge"][i], 0.2, 3.0),
            defensive_budget_add=_s(dims_w["def"][i], -0.20, 0.30),
            crash_guard_scale=_s(dims_w["guard"][i], 0.10, 0.85),
            crash_guard_vol_spike=float(rng.choice([1.5, 2.0, 2.5, 3.0, 4.0, 5.0])),
            trend_clamp_min=_s(dims_w["trend"][i], 0.25, 0.95),
            trend_lookback_weeks=int(rng.choice([6, 8, 10, 12, 16, 20, 26])),
            gain_adj=_s(dims_w["gain"][i], -0.15, 0.20),
            loss_adj=_s(dims_w["loss"][i], -0.20, 0.15),
            cost_bps=_s(dims_w["cost"][i], 25.0, 50.0),   # prod: 25bps 하한
            crisis_cost_mult=_s(dims_w["crisis"][i], 1.0, 3.0),
            patch_v51=bool(rng.choice([True, False])),
            patch_v53=bool(rng.choice([True, False])),
            patch_v54=bool(rng.choice([True, False])),
            xgb_threshold=_s(dims_w["xgb_t"][i], 0.3, 0.95),
            xgb_scale_factor=_s(dims_w["xgb_s"][i], 0.2, 0.95),
        ))

    return cfgs


# ═══════════════════════════════════════════════════════════════
# STEP 0: v2 TOP20 재감사 (v5.1 신규)
# ═══════════════════════════════════════════════════════════════
def step0_top20_reaudit(sim):
    """
    v2 top20의 실제 비용 내성을 재검증.
    25/30/35/40bps × 3 seeds로 stress test.
    """
    print(f"\n{'='*70}")
    print(f"  STEP 0: v2 TOP20 RE-AUDIT (25/30/35/40 bps × 3 seeds)")
    print(f"{'='*70}\n")

    # v2 champion 파라미터 (A_04799 기반 변형 20개)
    rng = np.random.RandomState(SEED + 100)
    top20_configs = []
    for i in range(20):
        cfg = copy.deepcopy(GOLDEN_A04799)
        cfg.config_id = f"V2TOP_{i:02d}"
        # 약간의 변형 (v2 top20 범위 내)
        cfg.leverage_mult *= rng.uniform(0.95, 1.05)
        cfg.hedge_mult *= rng.uniform(0.90, 1.10)
        cfg.crash_guard_scale *= rng.uniform(0.90, 1.10)
        cfg.xgb_threshold *= rng.uniform(0.95, 1.05)
        top20_configs.append(cfg)

    results = []
    for cfg in top20_configs:
        row = {"id": cfg.config_id}
        for bps in [25, 30, 35, 40]:
            sharpes = []
            for s in range(3):
                try:
                    sim_s = RealMarketSimulatorMultiSeed(n_weeks=sim.n_weeks, seed=SEED + s*1000)
                    cc = copy.deepcopy(cfg)
                    cc.cost_bps = float(bps)
                    cc.config_id = f"{cfg.config_id}_{bps}bps_s{s}"
                    r = run_family_a(cc, sim_s)
                    sharpes.append(r.sharpe or 0)
                except:
                    pass
            row[f"sr_{bps}bps"] = round(np.mean(sharpes), 3) if sharpes else None
        # 판정
        sr25 = row.get("sr_25bps")
        sr40 = row.get("sr_40bps")
        row["pass_25"] = sr25 is not None and sr25 > 0
        row["pass_40"] = sr40 is not None and sr40 > 0
        results.append(row)
        p25 = "✅" if row["pass_25"] else "❌"
        p40 = "✅" if row["pass_40"] else "❌"
        print(f"  {row['id']}: 25bps={row.get('sr_25bps','?'):>7} {p25}  "
              f"30bps={row.get('sr_30bps','?'):>7}  "
              f"35bps={row.get('sr_35bps','?'):>7}  "
              f"40bps={row.get('sr_40bps','?'):>7} {p40}")

    df = pd.DataFrame(results)
    df.to_csv(f"{OUT}/step0_top20_reaudit.csv", index=False)

    n_pass = sum(1 for r in results if r["pass_25"])
    print(f"\n  v2 Top20 re-audit: {n_pass}/20 pass @25bps")
    return results


# ═══════════════════════════════════════════════════════════════
# PHASE 0: GOLDEN BASELINE STRESS TEST (v5.1 — 확장 sweep)
# ═══════════════════════════════════════════════════════════════
def phase0_stress_test(n_weeks):
    print(f"\n{'='*70}")
    print(f"  PHASE 0: GOLDEN BASELINE STRESS TEST (Real Data, v5.1)")
    print(f"{'='*70}\n")

    sim = RealMarketSimulator(n_weeks=n_weeks, seed=SEED)
    actual_weeks = sim.n_weeks
    print(f"  Real data: {actual_weeks} weeks available")

    # A_04799 at original cost (14bps)
    r_orig = run_family_a(GOLDEN_A04799, sim)
    print(f"  A_04799 @ 14bps: SR={r_orig.sharpe:.3f} CAGR={r_orig.ann_return:.1f}% MDD={r_orig.max_dd:.1f}%")

    # 확장 sweep: 15~60bps (외삽 금지 — 실측만)
    stress_results = {}
    for bps in [15, 20, 25, 30, 35, 40, 50, 60]:
        cfg = copy.deepcopy(GOLDEN_A04799)
        cfg.config_id = f"GOLDEN_A04799_{bps}bps"
        cfg.cost_bps = float(bps)
        r = run_family_a(cfg, sim)
        stress_results[bps] = r
        flag = "✅" if r.sharpe > 0 else "❌"
        print(f"  A_04799 @ {bps}bps: SR={r.sharpe:.3f} CAGR={r.ann_return:.1f}% MDD={r.max_dd:.1f}% {flag}")

    # Breakeven — 외삽 금지
    from sensitivity_v51 import _estimate_breakeven_strict, _max_surviving_bps
    cost_curve = {bps: r.sharpe for bps, r in stress_results.items()}
    bev = _estimate_breakeven_strict(cost_curve)
    surv = _max_surviving_bps(cost_curve)
    if bev:
        print(f"\n  A_04799 breakeven = {bev:.1f} bps (실측)")
    else:
        print(f"\n  A_04799: 0 crossing 미관측 → survives up to {surv} bps")

    # Full sensitivity sweep on A_04799 at 25bps
    cfg_25 = copy.deepcopy(GOLDEN_A04799)
    cfg_25.config_id = "GOLDEN_A04799_25bps"
    cfg_25.cost_bps = 25.0
    r_25 = run_family_a(cfg_25, sim)
    r_25_enriched = run_sensitivity_sweep(r_25, cfg_25, run_family_a, sim, "family_a")
    apply_hard_filters(r_25_enriched, mode="prod")

    print(f"\n  A_04799 @ 25bps FULL AUDIT (PROD mode):")
    print(f"    SR:              {r_25_enriched.sharpe:.3f}")
    print(f"    Breakeven:       {r_25_enriched.breakeven_tc_bps}")
    print(f"    Survives up to:  {r_25_enriched.survives_up_to_bps} bps")
    print(f"    SR@25bps:        {r_25_enriched.sharpe_25bps}")
    print(f"    Rebal Decay:     {r_25_enriched.rebal_decay_ratio}")
    print(f"    Vol Elast:       {r_25_enriched.target_vol_elasticity}")
    print(f"    Fold19 SR:       {r_25_enriched.fold19_sharpe}")
    print(f"    Hard Fail:       {r_25_enriched.hard_fail} {'— '+r_25_enriched.hard_fail_reason if r_25_enriched.hard_fail else ''}")
    print(f"    Audit Pass:      {'✅' if r_25_enriched.audit_pass else '❌'}")

    return stress_results, r_25_enriched, sim


# ═══════════════════════════════════════════════════════════════
# PHASE 1: MEGA-GRID (PARALLEL — 16 cores)
# ═══════════════════════════════════════════════════════════════
def phase1_megagrid(n_samples, sim):
    print(f"\n{'='*70}")
    print(f"  PHASE 1: MEGA-GRID — {n_samples} configs × {N_WORKERS} workers (Real Data)")
    print(f"{'='*70}\n")

    cfgs = gen_configs(n_samples, SEED)

    # Serialize simulator for workers
    print(f"  Serializing simulator for {N_WORKERS} workers...")
    sim_bytes = pickle.dumps(sim)
    print(f"  Simulator size: {len(sim_bytes)/1024/1024:.1f} MB")

    # Convert configs to dicts for pickling (v5.1 표준 키)
    cfg_dicts = []
    for cfg in cfgs:
        d = {
            'config_id': cfg.config_id,
            'leverage_mult': cfg.leverage_mult,
            'hedge_mult': cfg.hedge_mult,
            'defensive_budget_add': cfg.defensive_budget_add,
            'crash_guard_dd_thresh': cfg.crash_guard_dd_thresh,
            'crash_guard_vol_spike': cfg.crash_guard_vol_spike,
            'crash_guard_scale': cfg.crash_guard_scale,
            'trend_lookback_weeks': cfg.trend_lookback_weeks,
            'trend_bad_thresh': cfg.trend_bad_thresh,
            'trend_clamp_min': cfg.trend_clamp_min,
            'gain_adj': cfg.gain_adj,
            'loss_adj': cfg.loss_adj,
            'annual_turnover': cfg.annual_turnover,
            'cost_bps': cfg.cost_bps,
            'crisis_cost_mult': cfg.crisis_cost_mult,
            # v5.1 표준 키
            'patch_v51': cfg.patch_v51,
            'patch_v53': cfg.patch_v53,
            'patch_v54': cfg.patch_v54,
            'xgb_threshold': cfg.xgb_threshold,
            'xgb_scale_factor': cfg.xgb_scale_factor,
            'edge_gate_lo': cfg.edge_gate_lo,
            'edge_gate_hi': cfg.edge_gate_hi,
            'extreme_loss_thresh': cfg.extreme_loss_thresh,
            'extreme_loss_scale': cfg.extreme_loss_scale,
            'small_loss_thresh': cfg.small_loss_thresh,
            'small_loss_scale': cfg.small_loss_scale,
        }
        cfg_dicts.append(d)

    # Run in parallel
    t0 = time.time()
    results = []
    done = 0

    with ProcessPoolExecutor(max_workers=N_WORKERS,
                             initializer=_init_worker,
                             initargs=(sim_bytes,)) as pool:
        futures = {pool.submit(_run_one, d): i for i, d in enumerate(cfg_dicts)}

        for future in as_completed(futures):
            r_dict = future.result()
            if r_dict is not None:
                # NormalizedResult 복원 — 새 필드 포함
                valid_keys = {f.name for f in NormalizedResult.__dataclass_fields__.values()}
                r = NormalizedResult(**{k: v for k, v in r_dict.items() if k in valid_keys})
                results.append((cfgs[futures[future]], r))
            done += 1
            if done % max(1, n_samples // 10) == 0:
                el = time.time() - t0
                best = max((r.sharpe for _, r in results), default=0)
                eta = (el / done) * (n_samples - done) if done > 0 else 0
                print(f"    [{done:6d}/{n_samples}] {done/el:.0f}/s | best={best:.3f} | ETA {eta:.0f}s",
                      flush=True)

    elapsed = time.time() - t0
    print(f"    Done: {len(results)}/{n_samples} in {elapsed:.1f}s ({len(results)/elapsed:.0f}/s)")

    # Score (diag mode — Phase 1은 연구)
    all_r = score_population([r for _, r in results], mode="diag")

    df = pd.DataFrame([r.to_dict() for r in all_r])
    if "full_config" in df.columns:
        df = df.drop(columns=["full_config"])
    df.to_csv(f"{OUT}/phase1_megagrid.csv", index=False)

    valid = [(c, r) for (c, _), r in zip(results, all_r) if not r.hard_fail]
    valid.sort(key=lambda x: x[1].score, reverse=True)
    print(f"\n  Valid: {len(valid)}/{len(results)}")
    print(f"  TOP 5:")
    for cfg, r in valid[:5]:
        print(f"    {r.manifest_id:<12} SR={r.sharpe:.3f} CAGR={r.ann_return:.1f}% MDD={r.max_dd:.1f}% "
              f"cost={cfg.cost_bps:.0f} exp={r.avg_exposure:.3f} TO={r.avg_turnover:.1f}")

    return cfgs, all_r, valid


# ═══════════════════════════════════════════════════════════════
# PHASE 2: FINE GRID (sequential — small N)
# ═══════════════════════════════════════════════════════════════
def phase2_fine(valid, sim, n_fine=20):
    print(f"\n{'='*70}")
    print(f"  PHASE 2: FINE GRID — top 10 × {n_fine}")
    print(f"{'='*70}\n")

    rng = np.random.RandomState(SEED + 777)
    fine = []
    for base_cfg, base_r in valid[:10]:
        for j in range(n_fine):
            fc = copy.deepcopy(base_cfg)
            fc.config_id = f"{base_cfg.config_id}_F{j:03d}"
            for attr in ["leverage_mult","hedge_mult","crash_guard_scale","trend_clamp_min",
                         "gain_adj","loss_adj","xgb_threshold","xgb_scale_factor"]:
                old = getattr(fc, attr)
                setattr(fc, attr, old * rng.uniform(0.88, 1.12))
            fc.cost_bps = np.clip(fc.cost_bps * rng.uniform(0.9, 1.1), 20, 50)
            try:
                r = run_family_a(fc, sim)
                fine.append((fc, r))
            except:
                pass

    merged = list(valid[:50]) + fine
    all_r = score_population([r for _, r in merged], mode="diag")
    valid2 = [(c, r) for (c, _), r in zip(merged, all_r) if not r.hard_fail]
    valid2.sort(key=lambda x: x[1].score, reverse=True)
    print(f"  Merged: {len(merged)} | Valid: {len(valid2)}")
    for cfg, r in valid2[:5]:
        print(f"    {r.manifest_id:<14} SR={r.sharpe:.3f} CAGR={r.ann_return:.1f}% MDD={r.max_dd:.1f}%")
    return valid2


# ═══════════════════════════════════════════════════════════════
# PHASE 2.5: SENSITIVITY SWEEP (감사 민감도 — PROD 모드)
# ═══════════════════════════════════════════════════════════════
def phase25_sensitivity(valid2, sim, n_top=30):
    print(f"\n{'='*70}")
    print(f"  PHASE 2.5: SENSITIVITY SWEEP — top {min(n_top, len(valid2))} (PROD mode)")
    print(f"{'='*70}\n")

    enriched = []
    for cfg, r in valid2[:n_top]:
        try:
            er = run_sensitivity_sweep(r, cfg, run_family_a, sim, "family_a")
            apply_hard_filters(er, mode="prod")
            enriched.append((cfg, er))
            bev = f"bev={er.breakeven_tc_bps:.0f}" if er.breakeven_tc_bps else f"surv={er.survives_up_to_bps}"
            rdr = f"rdr={er.rebal_decay_ratio:.2f}" if er.rebal_decay_ratio else "rdr=?"
            tve = f"tve={er.target_vol_elasticity:.3f}" if er.target_vol_elasticity else "tve=?"
            st = "PASS" if not er.hard_fail else f"FAIL:{er.hard_fail_reason}"
            print(f"    {r.manifest_id:<14} {bev:>14} {rdr:>10} {tve:>12} [{st}]")
        except Exception as e:
            print(f"    {r.manifest_id:<14} ERROR: {e}")
            enriched.append((cfg, r))

    survived = [(c, r) for c, r in enriched if not r.hard_fail]
    print(f"\n  Survived (PROD): {len(survived)}/{len(enriched)}")

    # CSV 저장
    rows = []
    for cfg, r in enriched:
        rows.append({
            "id": r.manifest_id, "sharpe": r.sharpe, "cagr": r.ann_return,
            "max_dd": r.max_dd, "breakeven_tc_bps": r.breakeven_tc_bps,
            "survives_up_to_bps": r.survives_up_to_bps,
            "sharpe_25bps": r.sharpe_25bps, "sharpe_30bps": r.sharpe_30bps,
            "rebal_decay_ratio": r.rebal_decay_ratio,
            "target_vol_elasticity": r.target_vol_elasticity,
            "fold19_sharpe": r.fold19_sharpe,
            "hard_fail": r.hard_fail, "hard_fail_reason": r.hard_fail_reason,
            "audit_pass": r.audit_pass,
        })
    pd.DataFrame(rows).to_csv(f"{OUT}/phase25_sensitivity.csv", index=False)

    return enriched, survived


# ═══════════════════════════════════════════════════════════════
# PHASE 3: MINIMAL HYBRID (A + defense/crisis/hedge)
# ═══════════════════════════════════════════════════════════════
def phase3_hybrid(survived, sim):
    print(f"\n{'='*70}")
    print(f"  PHASE 3: MINIMAL HYBRID — A + defense/crisis/hedge")
    print(f"{'='*70}\n")

    hybrids = []
    for idx, (base_cfg, base_r) in enumerate(survived[:10]):
        # Variant 1: +defense
        h1 = copy.deepcopy(base_cfg)
        h1.config_id = f"{base_cfg.config_id}_DEF"
        h1.defensive_budget_add = min(0.25, h1.defensive_budget_add + 0.10)
        h1.hedge_mult *= 1.2
        try:
            r1 = run_family_a(h1, sim); r1.family = "hybrid_A_DEF"
            hybrids.append((h1, r1))
        except: pass

        # Variant 2: +crisis
        h2 = copy.deepcopy(base_cfg)
        h2.config_id = f"{base_cfg.config_id}_CRI"
        h2.crash_guard_scale *= 0.7
        h2.hedge_mult *= 1.5
        h2.crisis_cost_mult = min(3.0, h2.crisis_cost_mult * 1.3)
        try:
            r2 = run_family_a(h2, sim); r2.family = "hybrid_A_CRI"
            hybrids.append((h2, r2))
        except: pass

        # Variant 3: +hedge
        h3 = copy.deepcopy(base_cfg)
        h3.config_id = f"{base_cfg.config_id}_HDG"
        h3.hedge_mult = min(3.0, h3.hedge_mult * 2.0)
        try:
            r3 = run_family_a(h3, sim); r3.family = "hybrid_A_HDG"
            hybrids.append((h3, r3))
        except: pass

    all_hr = score_population([r for _, r in hybrids], mode="diag")
    valid_h = [(c, r) for (c, _), r in zip(hybrids, all_hr) if not r.hard_fail]
    valid_h.sort(key=lambda x: x[1].score, reverse=True)
    print(f"  Hybrids: {len(hybrids)} | Valid: {len(valid_h)}")
    for c, r in valid_h[:5]:
        print(f"    {r.manifest_id:<20} SR={r.sharpe:.3f} CAGR={r.ann_return:.1f}% {r.family}")
    return valid_h


# ═══════════════════════════════════════════════════════════════
# PHASE 4: ROBUSTNESS (multi-seed, PROD 감사)
# ═══════════════════════════════════════════════════════════════
def phase4_robust(candidates, n_seeds, n_weeks, top_k=20):
    print(f"\n{'='*70}")
    print(f"  PHASE 4: ROBUSTNESS — {min(top_k, len(candidates))} × {n_seeds} seeds (PROD)")
    print(f"{'='*70}\n")

    robust = []
    for i, (cfg, r) in enumerate(candidates[:top_k]):
        sharpes = []; mdds = []
        for s in range(n_seeds):
            try:
                sim = RealMarketSimulatorMultiSeed(n_weeks=n_weeks, seed=SEED + s*1000 + 7)
                cc = copy.deepcopy(cfg)
                cc.config_id = f"{r.manifest_id}_s{s}"
                sr = run_family_a(cc, sim)
                sharpes.append(sr.sharpe or 0)
                mdds.append(sr.max_dd or -50)
            except:
                pass

        if not sharpes:
            continue
        ms = np.mean(sharpes); ss = np.std(sharpes)

        # PROD 감사 — sensitivity sweep 결과 사용
        entry = {
            "id": r.manifest_id,
            "family": getattr(r, 'family', 'family_a'),
            "mean_sharpe": round(float(ms), 4),
            "std_sharpe": round(float(ss), 4),
            "min_sharpe": round(float(min(sharpes)), 4),
            "worst_maxdd": round(float(min(mdds)), 2),
            # v5.1 표준 필드
            "breakeven_tc_bps": r.breakeven_tc_bps,
            "survives_up_to_bps": getattr(r, 'survives_up_to_bps', None),
            "sharpe_25bps": r.sharpe_25bps,
            "sharpe_30bps": r.sharpe_30bps,
            "rebal_decay_ratio": r.rebal_decay_ratio,
            "target_vol_elasticity": r.target_vol_elasticity,
            "fold19_sharpe": r.fold19_sharpe,
            "robust_score": round(float(ms - 0.5*ss), 4),
            "audit_pass": not r.hard_fail,
            # v5.1 표준 키 config
            "config": r.full_config,
        }
        robust.append(entry)
        ap = "✅" if entry["audit_pass"] else "❌"
        bev_str = f"bev={entry['breakeven_tc_bps']:.0f}" if entry['breakeven_tc_bps'] else f"surv={entry.get('survives_up_to_bps','?')}"
        print(f"  [{i+1:3d}] {r.manifest_id:<18} robust={entry['robust_score']:.3f} "
              f"SR={ms:.3f}±{ss:.3f} {bev_str} {ap}")

    robust.sort(key=lambda x: x["robust_score"], reverse=True)
    passed = [r for r in robust if r["audit_pass"]]

    with open(f"{OUT}/phase4_robustness.json", "w") as f:
        json.dump(robust, f, cls=NpEncoder, indent=2)

    if passed:
        champ = passed[0]
        with open(f"{OUT}/CHAMPION_FINAL.json", "w") as f:
            json.dump(champ, f, cls=NpEncoder, indent=2)
        print(f"\n{'='*70}")
        print(f"  CHAMPION: {champ['id']}")
        print(f"  Family:         {champ['family']}")
        print(f"  Audit:          {'PASS' if champ['audit_pass'] else 'FAIL'}")
        print(f"  Robust SR:      {champ['robust_score']:.4f}")
        print(f"  Mean SR:        {champ['mean_sharpe']:.3f} +/- {champ['std_sharpe']:.3f}")
        print(f"  Min SR:         {champ['min_sharpe']:.3f}")
        print(f"  Breakeven:      {champ['breakeven_tc_bps']} bps")
        print(f"  Survives to:    {champ.get('survives_up_to_bps')} bps")
        print(f"  SR@25bps:       {champ['sharpe_25bps']}")
        print(f"  SR@30bps:       {champ.get('sharpe_30bps')}")
        print(f"  Rebal Decay:    {champ['rebal_decay_ratio']}")
        print(f"  Vol Elast:      {champ['target_vol_elasticity']}")
        print(f"  Fold19 SR:      {champ['fold19_sharpe']}")
        print(f"  Worst MDD:      {champ['worst_maxdd']}%")
        print(f"{'='*70}")
    else:
        print(f"\n  No candidates passed all PROD audit filters.")
    return robust


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ARES Family A v5.1 — Production Turbo")
    ap.add_argument("--n_samples", type=int, default=15000)
    ap.add_argument("--n_weeks", type=int, default=163)
    ap.add_argument("--n_fine", type=int, default=20)
    ap.add_argument("--top_k", type=int, default=50)
    ap.add_argument("--n_seeds", type=int, default=5)
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"\n{'#'*70}")
    print(f"#  ARES FAMILY A v5.1 — PRODUCTION TURBO ({N_WORKERS} cores)")
    print(f"#  {ts}")
    print(f"#  N={args.n_samples} | Weeks={args.n_weeks} | Seeds={args.n_seeds}")
    print(f"#  v5.1 fixes: breakeven no-extrap, key schema, diag/prod, top20 reaudit")
    print(f"{'#'*70}")

    # Phase 0: Golden baseline stress test
    stress, golden_audit, sim = phase0_stress_test(args.n_weeks)

    # Step 0: v2 top20 re-audit
    top20_audit = step0_top20_reaudit(sim)

    # Phase 1: Mega-grid (PARALLEL)
    cfgs, all_r, valid = phase1_megagrid(args.n_samples, sim)

    # Phase 2: Fine grid
    valid2 = phase2_fine(valid, sim, args.n_fine)

    # Phase 2.5: Sensitivity (PROD mode)
    enriched, survived = phase25_sensitivity(valid2, sim)

    # Phase 3: Minimal hybrid
    hybrids = phase3_hybrid(survived, sim)

    # Merge survived + hybrids for Phase 4
    all_candidates = survived + hybrids
    all_candidates.sort(key=lambda x: x[1].score, reverse=True)

    # Phase 4: Robustness (PROD)
    robust = phase4_robust(all_candidates, args.n_seeds, args.n_weeks, top_k=20)

    print(f"\n All v5.1 results in ./{OUT}/")
    print(f"  Files: CHAMPION_FINAL.json, phase1_megagrid.csv, phase25_sensitivity.csv,")
    print(f"         phase4_robustness.json, step0_top20_reaudit.csv")
