#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibrate_thresholds_v24_gpu.py
TRUE GPU Batch Processing - 전체 그리드를 GPU에서 병렬 처리

핵심 최적화:
1. 모든 설정을 GPU 메모리에 로드
2. 전체 시계열 데이터를 GPU에서 한 번에 처리
3. 설정별 레짐 분류를 GPU 병렬로 수행
4. CPU는 결과 집계만 담당
"""

import argparse
import json
import time
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import multiprocessing as mp

import numpy as np

# GPU 라이브러리
try:
    import cupy as cp
    from cupyx import jit as cp_jit
    HAS_CUPY = True
    # GPU 메모리 풀 설정
    mempool = cp.get_default_memory_pool()
    pinned_mempool = cp.get_default_pinned_memory_pool()
    print(f"[GPU] CuPy {cp.__version__} - CUDA devices: {cp.cuda.runtime.getDeviceCount()}")
except ImportError:
    HAS_CUPY = False
    print("[CPU] CuPy not available")

try:
    from numba import njit, prange
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False

warnings.filterwarnings('ignore')

# ============================================================
# Config & Helpers
# ============================================================
def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def quantile(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    idx = int(round(q * (len(xs) - 1)))
    return float(xs[idx])

@dataclass(frozen=True)
class CalibConfig:
    T1: float
    T2: float
    shock_vix_z: float
    shock_spread_z: float
    shock_rv_ratio: float
    shock_dvix1: float
    shock_dvix5: float
    shock_avix1: float
    rec_vix_z: float
    rec_spread_z: float
    rec_mom30_nonneg: int
    hysteresis_min: int
    cooldown_min: int
    hi_vol_vix_z: float
    hi_vol_hysteresis: int
    hi_vol_cooldown: int
    fwd_mins: int = 30
    fwd_ret_min: float = 0.0
    mae_limit: Optional[float] = None
    mfe_min: Optional[float] = None
    pnl_window_mins: int = 20
    pnl_min: float = 0.0
    pnl_mae_limit: Optional[float] = None

# ============================================================
# Data Loading
# ============================================================
def load_from_jsonl(path: str, date_from: Optional[str], date_to: Optional[str]) -> Tuple[List[Dict], Dict]:
    bars, feats, exec_by_ts, pnl_by_ts = {}, {}, {}, {}
    dt_from = parse_ts(date_from + "T00:00:00Z") if date_from else None
    dt_to = parse_ts(date_to + "T23:59:59Z") if date_to else None

    def in_range(ts):
        try:
            t = parse_ts(ts)
            if dt_from and t < dt_from: return False
            if dt_to and t > dt_to: return False
            return True
        except: return False

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            ev = json.loads(line)
            ts, et = ev.get("timestamp"), ev.get("event_type")
            if not ts or not in_range(ts): continue

            if et == "bar_1m":
                bars[ts] = {"timestamp": ts, "mid": float(ev.get("mid")),
                           "vwap_1m": float(ev.get("vwap_1m", ev.get("mid"))),
                           "spread": float(ev.get("spread", 0.0)),
                           "volume": float(ev.get("volume", 0.0))}
            elif et == "feature_snapshot":
                feats[ts] = {"timestamp": ts, "mom_30": float(ev.get("mom_30", 0.0)),
                            "mom_120": float(ev.get("mom_120", 0.0)),
                            "rv_5": float(ev.get("rv_5", 0.0)),
                            "rv_30": float(ev.get("rv_30", 0.0)),
                            "vov": float(ev.get("vov", 0.0)),
                            "vix_z": float(ev.get("vix_z", 0.0)),
                            "spread_z": float(ev.get("spread_z", 0.0)),
                            "slip_proxy": float(ev.get("slip_proxy", 0.0))}
            elif et == "portfolio_pnl_1m":
                pnl_by_ts[ts] = {"pnl_total_1m": ev.get("pnl_total_1m")}
            elif et == "order_intent":
                d = exec_by_ts.setdefault(ts, {"orders": 0, "fills": 0, "target_qty": 0.0,
                                               "filled_qty": 0.0, "reprices": 0, "slip_mid": []})
                d["orders"] += 1
                d["target_qty"] += float(ev.get("target_qty", 0.0))
            elif et == "order_update" and ev.get("action", "").startswith("REPRICE"):
                d = exec_by_ts.setdefault(ts, {"orders": 0, "fills": 0, "target_qty": 0.0,
                                               "filled_qty": 0.0, "reprices": 0, "slip_mid": []})
                d["reprices"] += 1
            elif et == "fill":
                d = exec_by_ts.setdefault(ts, {"orders": 0, "fills": 0, "target_qty": 0.0,
                                               "filled_qty": 0.0, "reprices": 0, "slip_mid": []})
                d["fills"] += 1
                d["filled_qty"] += float(ev.get("fill_qty", 0.0))
                slip = ev.get("slippage", {})
                if "vs_mid_bps" in slip: d["slip_mid"].append(float(slip["vs_mid_bps"]))

    all_ts = sorted(set(bars.keys()) & set(feats.keys()))
    series = []
    for ts in all_ts:
        row = {**bars[ts], **feats[ts]}
        if ts in pnl_by_ts: row["pnl_total_1m"] = pnl_by_ts[ts].get("pnl_total_1m")
        if ts in exec_by_ts:
            ex = exec_by_ts[ts]
            row.update({"ex_orders": ex["orders"], "ex_fills": ex["fills"],
                       "ex_target_qty": ex["target_qty"], "ex_filled_qty": ex["filled_qty"],
                       "ex_reprices": ex["reprices"],
                       "ex_slip_mid_p95": quantile(ex["slip_mid"], 0.95) if ex["slip_mid"] else None})
        series.append(row)

    exec_meta = {"has_execution_logs": any("ex_orders" in r for r in series),
                 "has_pnl_logs": any("pnl_total_1m" in r for r in series)}
    return series, exec_meta

# ============================================================
# GPU Batch Processing Core
# ============================================================
def prepare_gpu_data(series: List[Dict]):
    """시계열 데이터를 GPU 배열로 변환"""
    n = len(series)
    
    # NumPy 배열로 변환
    data = {
        'mid': np.array([r["mid"] for r in series], dtype=np.float32),
        'mom_30': np.array([r["mom_30"] for r in series], dtype=np.float32),
        'mom_120': np.array([r["mom_120"] for r in series], dtype=np.float32),
        'rv_5': np.array([r["rv_5"] for r in series], dtype=np.float32),
        'rv_30': np.array([r["rv_30"] for r in series], dtype=np.float32),
        'vov': np.array([r["vov"] for r in series], dtype=np.float32),
        'vix_z': np.array([r["vix_z"] for r in series], dtype=np.float32),
        'spread_z': np.array([r["spread_z"] for r in series], dtype=np.float32),
        'slip_proxy': np.array([r["slip_proxy"] for r in series], dtype=np.float32),
    }
    
    if HAS_CUPY:
        # GPU로 전송
        gpu_data = {k: cp.asarray(v) for k, v in data.items()}
        return gpu_data, n
    else:
        return data, n

def prepare_config_arrays(grid: List[CalibConfig]):
    """설정들을 배열로 변환"""
    n_configs = len(grid)
    
    # 설정 파라미터 배열
    configs = {
        'T1': np.array([c.T1 for c in grid], dtype=np.float32),
        'T2': np.array([c.T2 for c in grid], dtype=np.float32),
        'shock_vix_z': np.array([c.shock_vix_z for c in grid], dtype=np.float32),
        'shock_spread_z': np.array([c.shock_spread_z for c in grid], dtype=np.float32),
        'shock_rv_ratio': np.array([c.shock_rv_ratio for c in grid], dtype=np.float32),
        'shock_dvix1': np.array([c.shock_dvix1 for c in grid], dtype=np.float32),
        'shock_dvix5': np.array([c.shock_dvix5 for c in grid], dtype=np.float32),
        'shock_avix1': np.array([c.shock_avix1 for c in grid], dtype=np.float32),
        'rec_vix_z': np.array([c.rec_vix_z for c in grid], dtype=np.float32),
        'rec_spread_z': np.array([c.rec_spread_z for c in grid], dtype=np.float32),
        'hysteresis_min': np.array([c.hysteresis_min for c in grid], dtype=np.int32),
        'hi_vol_vix_z': np.array([c.hi_vol_vix_z for c in grid], dtype=np.float32),
        'hi_vol_hysteresis': np.array([c.hi_vol_hysteresis for c in grid], dtype=np.int32),
    }
    
    if HAS_CUPY:
        gpu_configs = {k: cp.asarray(v) for k, v in configs.items()}
        return gpu_configs, n_configs
    else:
        return configs, n_configs

def gpu_batch_classify(gpu_data, gpu_configs, n_time, n_configs, batch_size=10000):
    """GPU에서 배치 단위로 레짐 분류"""
    
    if not HAS_CUPY:
        return cpu_batch_classify(gpu_data, gpu_configs, n_time, n_configs)
    
    xp = cp
    
    # 시계열 데이터 (GPU)
    mom_30 = gpu_data['mom_30']
    mom_120 = gpu_data['mom_120']
    rv_5 = gpu_data['rv_5']
    rv_30 = gpu_data['rv_30']
    vov = gpu_data['vov']
    vix_z = gpu_data['vix_z']
    spread_z = gpu_data['spread_z']
    slip_proxy = gpu_data['slip_proxy']
    
    # 레짐 점수 계산 (벡터화) - 모든 시점에 대해 한 번에
    sign_mom30 = xp.sign(mom_30)
    sign_mom120 = xp.sign(mom_120)
    trend_score = sign_mom30 + sign_mom120
    vol_score = xp.clip(rv_30 * 1000, 0, 3) + xp.clip(vov * 5000, 0, 2)
    stress_score = vix_z + 0.5 * spread_z + 0.5 * xp.clip(slip_proxy, 0, 3)
    regime_score = trend_score - vol_score - stress_score  # shape: (n_time,)
    
    # VIX 변화율
    dvix_z_1 = xp.zeros_like(vix_z)
    dvix_z_5 = xp.zeros_like(vix_z)
    avix_z_1 = xp.zeros_like(vix_z)
    dvix_z_1[1:] = vix_z[1:] - vix_z[:-1]
    dvix_z_5[5:] = vix_z[5:] - vix_z[:-5]
    avix_z_1[2:] = dvix_z_1[2:] - dvix_z_1[1:-1]
    
    # 결과 저장 (배치 처리)
    all_results = []
    
    for batch_start in range(0, n_configs, batch_size):
        batch_end = min(batch_start + batch_size, n_configs)
        batch_n = batch_end - batch_start
        
        # 배치 설정 추출
        T1 = gpu_configs['T1'][batch_start:batch_end]
        T2 = gpu_configs['T2'][batch_start:batch_end]
        shock_vix = gpu_configs['shock_vix_z'][batch_start:batch_end]
        shock_spread = gpu_configs['shock_spread_z'][batch_start:batch_end]
        shock_rv = gpu_configs['shock_rv_ratio'][batch_start:batch_end]
        shock_dv1 = gpu_configs['shock_dvix1'][batch_start:batch_end]
        shock_dv5 = gpu_configs['shock_dvix5'][batch_start:batch_end]
        shock_av1 = gpu_configs['shock_avix1'][batch_start:batch_end]
        rec_vix = gpu_configs['rec_vix_z'][batch_start:batch_end]
        rec_spread = gpu_configs['rec_spread_z'][batch_start:batch_end]
        hys = gpu_configs['hysteresis_min'][batch_start:batch_end]
        hi_vol_vix = gpu_configs['hi_vol_vix_z'][batch_start:batch_end]
        hi_vol_hys = gpu_configs['hi_vol_hysteresis'][batch_start:batch_end]
        
        # 브로드캐스팅을 위해 reshape: (n_time, 1) vs (1, batch_n)
        regime_score_2d = regime_score[:, None]  # (n_time, 1)
        vix_z_2d = vix_z[:, None]
        spread_z_2d = spread_z[:, None]
        rv_5_2d = rv_5[:, None]
        rv_30_2d = rv_30[:, None]
        dvix_z_1_2d = dvix_z_1[:, None]
        dvix_z_5_2d = dvix_z_5[:, None]
        avix_z_1_2d = avix_z_1[:, None]
        sign_mom30_2d = sign_mom30[:, None]
        
        # 쇼크 감지 (n_time, batch_n)
        shock_level = (vix_z_2d > shock_vix[None, :]) | (spread_z_2d > shock_spread[None, :])
        shock_rv_cond = xp.where(rv_30_2d > 0, rv_5_2d > rv_30_2d * shock_rv[None, :], False)
        shock_delta = ((dvix_z_1_2d > shock_dv1[None, :]) | 
                       (dvix_z_5_2d > shock_dv5[None, :]) | 
                       (avix_z_1_2d > shock_av1[None, :]))
        shock_flag = shock_level | shock_rv_cond | shock_delta
        
        # 회복 게이트
        recovery_gate = ((vix_z_2d <= rec_vix[None, :]) & 
                        (spread_z_2d <= rec_spread[None, :]) & 
                        (sign_mom30_2d >= 0))
        
        # 상태 결정 (벡터화 - 히스테리시스 없이 순간 상태)
        # 0: NEUTRAL, 1: RISK_OFF, 2: RISK_ON
        instant_state = xp.zeros((n_time, batch_n), dtype=xp.int8)
        instant_state[shock_flag] = 1  # RISK_OFF
        instant_state[(regime_score_2d <= -T1[None, :]) & ~shock_flag] = 1  # RISK_OFF
        instant_state[(regime_score_2d >= T2[None, :]) & recovery_gate & ~shock_flag & (regime_score_2d > -T1[None, :])] = 2  # RISK_ON
        
        # 메트릭 계산 (GPU에서)
        # 전환 횟수
        transitions = xp.sum(instant_state[1:] != instant_state[:-1], axis=0)
        
        # 상태별 비율
        off_count = xp.sum(instant_state == 1, axis=0)
        on_count = xp.sum(instant_state == 2, axis=0)
        
        # CPU로 전송
        transitions_cpu = cp.asnumpy(transitions)
        off_count_cpu = cp.asnumpy(off_count)
        on_count_cpu = cp.asnumpy(on_count)
        instant_state_cpu = cp.asnumpy(instant_state)
        
        # 배치 결과 저장
        for i in range(batch_n):
            all_results.append({
                'config_idx': batch_start + i,
                'transitions': int(transitions_cpu[i]),
                'off_count': int(off_count_cpu[i]),
                'on_count': int(on_count_cpu[i]),
                'states': instant_state_cpu[:, i],
            })
        
        # GPU 메모리 정리
        del shock_level, shock_rv_cond, shock_delta, shock_flag, recovery_gate, instant_state
        mempool.free_all_blocks()
    
    return all_results

def cpu_batch_classify(data, configs, n_time, n_configs):
    """CPU 폴백 버전"""
    xp = np
    
    mom_30 = data['mom_30']
    mom_120 = data['mom_120']
    rv_5 = data['rv_5']
    rv_30 = data['rv_30']
    vov = data['vov']
    vix_z = data['vix_z']
    spread_z = data['spread_z']
    slip_proxy = data['slip_proxy']
    
    sign_mom30 = np.sign(mom_30)
    sign_mom120 = np.sign(mom_120)
    trend_score = sign_mom30 + sign_mom120
    vol_score = np.clip(rv_30 * 1000, 0, 3) + np.clip(vov * 5000, 0, 2)
    stress_score = vix_z + 0.5 * spread_z + 0.5 * np.clip(slip_proxy, 0, 3)
    regime_score = trend_score - vol_score - stress_score
    
    dvix_z_1 = np.zeros_like(vix_z)
    dvix_z_5 = np.zeros_like(vix_z)
    avix_z_1 = np.zeros_like(vix_z)
    dvix_z_1[1:] = vix_z[1:] - vix_z[:-1]
    dvix_z_5[5:] = vix_z[5:] - vix_z[:-5]
    avix_z_1[2:] = dvix_z_1[2:] - dvix_z_1[1:-1]
    
    all_results = []
    
    for cfg_idx in range(n_configs):
        T1 = configs['T1'][cfg_idx]
        T2 = configs['T2'][cfg_idx]
        shock_vix = configs['shock_vix_z'][cfg_idx]
        shock_spread = configs['shock_spread_z'][cfg_idx]
        shock_rv = configs['shock_rv_ratio'][cfg_idx]
        shock_dv1 = configs['shock_dvix1'][cfg_idx]
        shock_dv5 = configs['shock_dvix5'][cfg_idx]
        shock_av1 = configs['shock_avix1'][cfg_idx]
        rec_vix = configs['rec_vix_z'][cfg_idx]
        rec_spread = configs['rec_spread_z'][cfg_idx]
        
        shock_flag = ((vix_z > shock_vix) | (spread_z > shock_spread) |
                     np.where(rv_30 > 0, rv_5 > rv_30 * shock_rv, False) |
                     (dvix_z_1 > shock_dv1) | (dvix_z_5 > shock_dv5) | (avix_z_1 > shock_av1))
        
        recovery_gate = (vix_z <= rec_vix) & (spread_z <= rec_spread) & (sign_mom30 >= 0)
        
        instant_state = np.zeros(n_time, dtype=np.int8)
        instant_state[shock_flag] = 1
        instant_state[(regime_score <= -T1) & ~shock_flag] = 1
        instant_state[(regime_score >= T2) & recovery_gate & ~shock_flag & (regime_score > -T1)] = 2
        
        transitions = np.sum(instant_state[1:] != instant_state[:-1])
        off_count = np.sum(instant_state == 1)
        on_count = np.sum(instant_state == 2)
        
        all_results.append({
            'config_idx': cfg_idx,
            'transitions': int(transitions),
            'off_count': int(off_count),
            'on_count': int(on_count),
            'states': instant_state,
        })
    
    return all_results

# ============================================================
# Metrics & Objective
# ============================================================
def compute_metrics_fast(series, states, cfg, exec_meta):
    """빠른 메트릭 계산"""
    n = len(series)
    if n == 0:
        return {"minutes": 0, "transitions": 0, "transition_rate": 0.0, "whipsaw_rate": 0.0,
                "quality_success_rate": 0.0, "quality_fail_rate": 0.0, "mae_p95": None, "mfe_p50": None,
                "pnl_success_rate": 0.0, "pnl_fail_rate": 0.0, "missed_recovery_rate": 0.0,
                "microstructure_penalty": 0.0, "max_off_streak_min": 0,
                "execution": {"has_execution_logs": False, "has_pnl_logs": False,
                             "unfilled_ratio": None, "slip_mid_p95": None, "reprice_rate": None}}
    
    # 상태 매핑: 0=NEUTRAL, 1=RISK_OFF, 2=RISK_ON
    transitions = sum(1 for i in range(1, n) if states[i] != states[i-1])
    
    on_entries = 0
    whipsaws = 0
    for i in range(1, n):
        if states[i] == 2 and states[i-1] != 2:
            on_entries += 1
            for j in range(i+1, min(i+6, n)):
                if states[j] == 1:
                    whipsaws += 1
                    break
    
    whipsaw_rate = 0.0 if on_entries == 0 else whipsaws / on_entries
    
    # 품질 메트릭 (간소화)
    quality_success = quality_fail = 0
    maes, mfes = [], []
    
    for i in range(1, n):
        if states[i] == 2 and states[i-1] != 2:
            end_idx = min(i + cfg.fwd_mins, n)
            if end_idx > i:
                prices = [series[j]["mid"] for j in range(i, end_idx)]
                if prices and series[i]["mid"] > 0:
                    base = series[i]["mid"]
                    rets = [(p - base) / base for p in prices]
                    fwd_ret = rets[-1] if rets else 0.0
                    mae, mfe = min(rets) if rets else 0.0, max(rets) if rets else 0.0
                    maes.append(mae)
                    mfes.append(mfe)
                    
                    qual_ok = fwd_ret >= cfg.fwd_ret_min
                    if cfg.mae_limit is not None: qual_ok = qual_ok and mae >= cfg.mae_limit
                    if cfg.mfe_min is not None: qual_ok = qual_ok and mfe >= cfg.mfe_min
                    
                    if qual_ok: quality_success += 1
                    else: quality_fail += 1
    
    total_quality = quality_success + quality_fail
    quality_success_rate = 0.0 if total_quality == 0 else quality_success / total_quality
    quality_fail_rate = 0.0 if total_quality == 0 else quality_fail / total_quality
    
    # PnL sanity
    pnl_success = pnl_fail = 0
    if exec_meta.get("has_pnl_logs", False):
        for i in range(1, n):
            if states[i] == 2 and states[i-1] != 2:
                end_idx = min(i + cfg.pnl_window_mins, n)
                cum_pnl = pnl_mae = 0.0
                has_pnl = False
                for j in range(i, end_idx):
                    pnl = series[j].get("pnl_total_1m")
                    if pnl is not None:
                        has_pnl = True
                        cum_pnl += pnl
                        pnl_mae = min(pnl_mae, cum_pnl)
                if has_pnl:
                    pnl_ok = cum_pnl >= cfg.pnl_min
                    if cfg.pnl_mae_limit is not None: pnl_ok = pnl_ok and pnl_mae >= cfg.pnl_mae_limit
                    if pnl_ok: pnl_success += 1
                    else: pnl_fail += 1
    
    total_pnl = pnl_success + pnl_fail
    pnl_success_rate = 0.0 if total_pnl == 0 else pnl_success / total_pnl
    pnl_fail_rate = 0.0 if total_pnl == 0 else pnl_fail / total_pnl
    
    # Missed recovery
    missed = candidates = 0
    for i in range(1, n):
        vix_z, spread_z, mom_30 = series[i]["vix_z"], series[i]["spread_z"], series[i]["mom_30"]
        cond = (vix_z <= cfg.rec_vix_z and spread_z <= cfg.rec_spread_z)
        if cfg.rec_mom30_nonneg == 1: cond = cond and (mom_30 >= 0)
        if cond:
            candidates += 1
            if states[i] != 2: missed += 1
    
    missed_recovery_rate = 0.0 if candidates == 0 else missed / candidates
    
    # Microstructure
    micro_pen = sum(max(0.0, series[i]["spread_z"]) + max(0.0, series[i]["slip_proxy"]) 
                   for i in range(n) if states[i] == 1) / max(1, n)
    
    # Max OFF streak
    off_streak = max_off_streak = 0
    for st in states:
        if st == 1:
            off_streak += 1
            max_off_streak = max(max_off_streak, off_streak)
        else:
            off_streak = 0
    
    # Execution
    orders = sum(r.get("ex_orders", 0) for r in series)
    reprices = sum(r.get("ex_reprices", 0) for r in series)
    target_qty = sum(r.get("ex_target_qty", 0.0) for r in series)
    filled_qty = sum(r.get("ex_filled_qty", 0.0) for r in series)
    unfilled_ratio = clamp(1.0 - (filled_qty / target_qty), 0.0, 1.0) if target_qty > 0 else None
    slip_mid_p95s = [r["ex_slip_mid_p95"] for r in series if r.get("ex_slip_mid_p95") is not None]
    slip_mid_p95 = quantile(slip_mid_p95s, 0.95)
    reprice_rate = None if orders == 0 else reprices / max(1, orders)
    
    return {
        "minutes": n, "transitions": transitions, "transition_rate": transitions / max(1, n),
        "on_entries": on_entries, "whipsaw_rate": whipsaw_rate,
        "quality_success_rate": quality_success_rate, "quality_fail_rate": quality_fail_rate,
        "mae_p95": quantile(maes, 0.95), "mfe_p50": quantile(mfes, 0.50),
        "pnl_success_rate": pnl_success_rate, "pnl_fail_rate": pnl_fail_rate,
        "missed_recovery_rate": missed_recovery_rate, "microstructure_penalty": micro_pen,
        "max_off_streak_min": max_off_streak,
        "execution": {"has_execution_logs": bool(exec_meta.get("has_execution_logs", False)),
                     "has_pnl_logs": bool(exec_meta.get("has_pnl_logs", False)),
                     "unfilled_ratio": unfilled_ratio, "slip_mid_p95": slip_mid_p95,
                     "reprice_rate": reprice_rate}
    }

def objective(m):
    base = (3.0 * m["whipsaw_rate"] + 2.0 * m["transition_rate"] * 100.0 +
            2.0 * m["microstructure_penalty"] / 10.0 + 2.0 * m["missed_recovery_rate"] +
            2.0 * m["quality_fail_rate"] - 0.5 * m["quality_success_rate"] +
            2.0 * m["pnl_fail_rate"] - 0.5 * m["pnl_success_rate"] +
            0.01 * m["max_off_streak_min"])
    ex = m.get("execution", {})
    if ex.get("has_execution_logs", False):
        if ex.get("unfilled_ratio") is not None: base += 2.0 * ex["unfilled_ratio"]
        if ex.get("slip_mid_p95") is not None: base += 0.02 * abs(ex["slip_mid_p95"])
        if ex.get("reprice_rate") is not None: base += 0.5 * ex["reprice_rate"]
    return float(base)

def pareto_vector(m):
    ex = m.get("execution", {})
    return {
        "whipsaw_rate": float(m["whipsaw_rate"]),
        "transition_rate": float(m["transition_rate"]),
        "missed_recovery_rate": float(m["missed_recovery_rate"]),
        "quality_fail_rate": float(m["quality_fail_rate"]),
        "pnl_fail_rate": float(m["pnl_fail_rate"]),
        "microstructure_penalty": float(m["microstructure_penalty"]),
        "unfilled_ratio": float(ex["unfilled_ratio"]) if ex.get("unfilled_ratio") is not None else 0.0,
        "slip_mid_p95_abs": float(abs(ex["slip_mid_p95"])) if ex.get("slip_mid_p95") is not None else 0.0,
        "reprice_rate": float(ex.get("reprice_rate") or 0.0),
    }

# ============================================================
# Grid Building
# ============================================================
def build_grid():
    grid = []
    for T1 in [0.8, 1.0, 1.2, 1.5]:
        for T2 in [0.8, 1.0, 1.2, 1.5]:
            for shock_vix_z in [1.8, 2.0, 2.2]:
                for shock_spread_z in [1.8, 2.0, 2.2]:
                    for shock_rv_ratio in [2.0, 2.5, 3.0]:
                        for shock_dvix1 in [0.15, 0.25, 0.35]:
                            for shock_dvix5 in [0.20, 0.35, 0.50]:
                                for shock_avix1 in [0.10, 0.20, 0.30]:
                                    for rec_vix_z in [0.3, 0.5, 0.7]:
                                        for rec_spread_z in [0.8, 1.0, 1.2]:
                                            for hys in [3, 4, 5]:
                                                for cd in [5, 8, 10]:
                                                    for hi_vol_vix_z in [1.0, 1.2]:
                                                        for hi_hys in [5, 7]:
                                                            for hi_cd in [10, 15]:
                                                                for fwd_ret_min in [0.0, 0.0005]:
                                                                    for mae_limit in [None, -0.003]:
                                                                        for mfe_min in [None, 0.001]:
                                                                            for pnl_win in [10, 20, 30]:
                                                                                grid.append(CalibConfig(
                                                                                    T1=T1, T2=T2,
                                                                                    shock_vix_z=shock_vix_z,
                                                                                    shock_spread_z=shock_spread_z,
                                                                                    shock_rv_ratio=shock_rv_ratio,
                                                                                    shock_dvix1=shock_dvix1,
                                                                                    shock_dvix5=shock_dvix5,
                                                                                    shock_avix1=shock_avix1,
                                                                                    rec_vix_z=rec_vix_z,
                                                                                    rec_spread_z=rec_spread_z,
                                                                                    rec_mom30_nonneg=1,
                                                                                    hysteresis_min=hys,
                                                                                    cooldown_min=cd,
                                                                                    hi_vol_vix_z=hi_vol_vix_z,
                                                                                    hi_vol_hysteresis=hi_hys,
                                                                                    hi_vol_cooldown=hi_cd,
                                                                                    fwd_mins=30,
                                                                                    fwd_ret_min=fwd_ret_min,
                                                                                    mae_limit=mae_limit,
                                                                                    mfe_min=mfe_min,
                                                                                    pnl_window_mins=pnl_win,
                                                                                    pnl_min=0.0,
                                                                                    pnl_mae_limit=None,
                                                                                ))
    return grid

def dominates(a, b, keys):
    le_all, lt_any = True, False
    for k in keys:
        if a[k] > b[k]: le_all = False; break
        if a[k] < b[k]: lt_any = True
    return le_all and lt_any

def pareto_front(items, keys):
    front = []
    for i, it in enumerate(items):
        v_i = it["pareto"]
        dominated = False
        for j, jt in enumerate(items):
            if i != j and dominates(jt["pareto"], v_i, keys):
                dominated = True
                break
        if not dominated:
            front.append(it)
    front.sort(key=lambda x: x["objective"])
    return front

# ============================================================
# Main
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--date_from", default=None)
    ap.add_argument("--date_to", default=None)
    ap.add_argument("--top_n", type=int, default=20)
    ap.add_argument("--write_ranked", default=None)
    ap.add_argument("--pareto_out", default=None)
    ap.add_argument("--gpu_batch_size", type=int, default=50000)
    args = ap.parse_args()
    
    print("=" * 60)
    print("calibrate_thresholds_v24_gpu.py")
    print("TRUE GPU Batch Processing")
    print("=" * 60)
    print(f"[INFO] GPU Mode: {HAS_CUPY}")
    if HAS_CUPY:
        print(f"[INFO] GPU Memory: {cp.cuda.Device(0).mem_info[1] / 1e9:.1f} GB")
    
    # 데이터 로드
    print(f"\n[1/4] Loading data...")
    start = time.time()
    series, exec_meta = load_from_jsonl(args.log, args.date_from, args.date_to)
    print(f"      {len(series)} records in {time.time()-start:.2f}s")
    
    # 그리드 생성
    print(f"\n[2/4] Building grid...")
    grid = build_grid()
    print(f"      {len(grid):,} configurations")
    
    # GPU 데이터 준비
    print(f"\n[3/4] GPU batch processing...")
    start = time.time()
    
    gpu_data, n_time = prepare_gpu_data(series)
    gpu_configs, n_configs = prepare_config_arrays(grid)
    
    # GPU 배치 분류
    gpu_results = gpu_batch_classify(gpu_data, gpu_configs, n_time, n_configs, args.gpu_batch_size)
    
    gpu_time = time.time() - start
    print(f"      GPU classification: {gpu_time:.2f}s ({n_configs/gpu_time:.0f} cfg/s)")
    
    # 메트릭 계산 (CPU)
    print(f"\n[4/4] Computing metrics...")
    start = time.time()
    
    ranked = []
    packed = []
    
    for res in gpu_results:
        cfg_idx = res['config_idx']
        cfg = grid[cfg_idx]
        states = res['states']
        
        m = compute_metrics_fast(series, states, cfg, exec_meta)
        obj = objective(m)
        
        ranked.append((obj, cfg, m))
        packed.append({"objective": obj, "config": asdict(cfg), "metrics": m, "pareto": pareto_vector(m)})
    
    metrics_time = time.time() - start
    print(f"      Metrics: {metrics_time:.2f}s")
    
    # 정렬 및 저장
    ranked.sort(key=lambda x: x[0])
    best_obj, best_cfg, best_m = ranked[0]
    
    pareto_keys = ["whipsaw_rate", "transition_rate", "missed_recovery_rate",
                   "quality_fail_rate", "pnl_fail_rate", "microstructure_penalty",
                   "unfilled_ratio", "slip_mid_p95_abs", "reprice_rate"]
    
    out_obj = {
        "best_config": asdict(best_cfg),
        "metrics": best_m,
        "objective": best_obj,
        "exec_meta": exec_meta,
        "notes": {"total_configs": len(grid), "gpu_time_sec": gpu_time, "metrics_time_sec": metrics_time}
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_obj, f, ensure_ascii=False, indent=2)
    
    if args.write_ranked:
        top = [{"objective": obj, "config": asdict(cfg), "metrics": m, "pareto": pareto_vector(m)}
               for obj, cfg, m in ranked[:args.top_n]]
        with open(args.write_ranked, "w", encoding="utf-8") as f:
            json.dump(top, f, ensure_ascii=False, indent=2)
    
    if args.pareto_out:
        front = pareto_front(packed, pareto_keys)
        with open(args.pareto_out, "w", encoding="utf-8") as f:
            json.dump(front, f, ensure_ascii=False, indent=2)
    
    total_time = gpu_time + metrics_time
    print("\n" + "=" * 60)
    print(f"[OK] Best objective: {best_obj:.4f}")
    print(f"[OK] Total time: {total_time:.1f}s ({len(grid)/total_time:.0f} cfg/s)")
    print(f"[OK] Wrote: {args.out}")

if __name__ == "__main__":
    main()
