#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v40 기반 카테고리별 수만 가지 조합 파레토 테스트
================================================================================
v40 클린 베이스라인 + 카테고리 B, C, D, E 개선안 조합 테스트
- CPU 최적화: Numba JIT + 멀티프로세싱 + 캐싱
- 추가 정밀 로깅: 팩터별 IC, 레짐별 성과, 턴오버, 드로다운 이벤트
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
import json
import time
import os
from datetime import datetime
from typing import Tuple, Dict, List, Optional
from numba import jit, prange, njit
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp
import warnings
warnings.filterwarnings('ignore')

print("=" * 80)
print("ARES v40 카테고리별 수만 가지 조합 파레토 테스트")
print("=" * 80)
print(f"시작 시간: {datetime.now()}")
print(f"CPU 코어: {mp.cpu_count()}개")

# ============================================================================
# 글로벌 데이터
# ============================================================================
PRICES_NP = None
RETURNS_NP = None
FACTORS_NP = None
ORTHO_FACTORS_NP = None
REGIME_SCORES_NP = None
FAST_REGIME_NP = None
BREADTH_NP = None
ELIGIBILITY_NP = None
BETAS_NP = None
N_DAYS = 0
N_ASSETS = 0


def init_global_data():
    """데이터 초기화 - 카테고리 A 패치 + Orthogonal 팩터"""
    global PRICES_NP, RETURNS_NP, FACTORS_NP, ORTHO_FACTORS_NP, REGIME_SCORES_NP
    global FAST_REGIME_NP, BREADTH_NP, ELIGIBILITY_NP, BETAS_NP, N_DAYS, N_ASSETS
    
    print("\n[1] 데이터 로드 중...")
    
    # === 주식 데이터 로드 ===
    conn = sqlite3.connect('/home/ubuntu/ares_x_unified_database/ares_universal_v2.db', timeout=30)
    query = """
    SELECT date, symbol, close FROM daily_ohlcv
    WHERE date >= '2016-01-01' AND date <= '2024-12-31'
    ORDER BY date, symbol
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
    prices = df.pivot(index='date', columns='symbol', values='close')
    prices.index = pd.to_datetime(prices.index)
    
    # A1: bfill() 제거
    print("  [A1] bfill() 제거 적용...")
    valid_cols = prices.columns[prices.notna().mean() > 0.8]
    prices = prices[valid_cols].ffill()
    
    # A2: 포인트-인-타임 유니버스
    print("  [A2] 포인트-인-타임 유니버스 계산 중...")
    avail = prices.notna().astype(float)
    ratio = avail.rolling(252, min_periods=252).mean()
    eligibility = (ratio >= 0.95) & prices.shift(1).notna()
    eligibility.iloc[:252] = False
    ELIGIBILITY_NP = eligibility.values.astype(np.float64)
    
    PRICES_NP = prices.values.astype(np.float64)
    
    returns = prices.pct_change()
    RETURNS_NP = returns.values.astype(np.float64)
    RETURNS_NP[0, :] = 0
    RETURNS_NP = np.nan_to_num(RETURNS_NP, nan=0.0)
    
    N_DAYS, N_ASSETS = PRICES_NP.shape
    print(f"  주식: {N_ASSETS}종목, {N_DAYS}일")
    
    # === ETF 데이터 로드 ===
    conn = sqlite3.connect('/home/ubuntu/etf_data_s3.db', timeout=30)
    query = """
    SELECT date, symbol, close FROM daily_ohlcv
    WHERE symbol IN ('QQQ', 'DIA', 'HYG', 'LQD', 'SPY')
    AND date >= '2015-01-01' ORDER BY date, symbol
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    etf = df.pivot(index='date', columns='symbol', values='close')
    etf.index = pd.to_datetime(etf.index)
    etf = etf.ffill()
    
    common_dates = prices.index.intersection(etf.index)
    
    # === 레짐 점수 계산 ===
    print("\n[2] 레짐 점수 계산 중...")
    mkt = etf['QQQ'].reindex(common_dates) if 'QQQ' in etf.columns else etf['DIA'].reindex(common_dates)
    mkt_ret = mkt.pct_change()
    sma_20 = mkt.rolling(20).mean()
    sma_50 = mkt.rolling(50).mean()
    sma_200 = mkt.rolling(200).mean()
    
    # Trend Score
    trend_score = pd.Series(0.5, index=common_dates)
    trend_score[mkt > sma_200] += 0.20
    trend_score[sma_50 > sma_200] += 0.15
    trend_score[mkt > sma_50] += 0.10
    trend_score[mkt > sma_20] += 0.05
    trend_score[mkt < sma_200] -= 0.20
    trend_score[(mkt < sma_200) & (sma_50 < sma_200)] -= 0.15
    trend_score[mkt < sma_20] -= 0.05
    trend_score = trend_score.clip(0, 1)
    
    # Volatility Score
    realized_vol = mkt_ret.rolling(20).std() * np.sqrt(252)
    hist_vol = mkt_ret.rolling(252).std() * np.sqrt(252)
    vol_ratio = realized_vol / (hist_vol + 1e-8)
    vol_score = 1 - vol_ratio.clip(0.5, 2.0) / 2.0
    vol_score = vol_score.clip(0, 1)
    
    # Credit Score
    if 'HYG' in etf.columns and 'LQD' in etf.columns:
        hyg = etf['HYG'].reindex(common_dates)
        lqd = etf['LQD'].reindex(common_dates)
        credit_ratio = hyg / lqd
        credit_ma = credit_ratio.rolling(60).mean()
        credit_std = credit_ratio.rolling(252).std()
        credit_zscore = (credit_ratio - credit_ma) / (credit_std + 1e-8)
        credit_score = 0.5 + credit_zscore.clip(-2, 2) / 4
        credit_score = credit_score.clip(0, 1)
    else:
        credit_score = pd.Series(0.5, index=common_dates)
    
    # Fast Regime (C2)
    cum_ret_5d = (1 + mkt_ret).rolling(5).apply(lambda x: x.prod() - 1, raw=True)
    fast_regime = pd.Series(0.5, index=common_dates)
    fast_regime[cum_ret_5d < -0.05] = 0.1
    fast_regime[cum_ret_5d < -0.03] = 0.25
    fast_regime[cum_ret_5d < -0.01] = 0.35
    fast_regime[cum_ret_5d > 0.03] = 0.8
    fast_regime[cum_ret_5d > 0.05] = 0.9
    
    # Breadth (C3)
    sma_200_stk = prices.rolling(200).mean()
    breadth = (prices > sma_200_stk).mean(axis=1).clip(0, 1)
    
    regime_scores = pd.DataFrame({
        'trend': trend_score,
        'vol': vol_score,
        'credit': credit_score,
    }).reindex(prices.index).ffill().fillna(0.5)
    
    REGIME_SCORES_NP = regime_scores.values.astype(np.float64)
    
    fast_regime_aligned = fast_regime.reindex(prices.index).ffill().fillna(0.5)
    FAST_REGIME_NP = fast_regime_aligned.values.astype(np.float64)
    
    breadth_aligned = breadth.reindex(prices.index).ffill().fillna(0.5)
    BREADTH_NP = breadth_aligned.values.astype(np.float64)
    
    # === 기존 팩터 계산 ===
    print("\n[3] 기존 팩터 계산 중...")
    mom_12 = prices.pct_change(252)
    mom_1 = prices.pct_change(21)
    mom_6 = prices.pct_change(126)
    mom_3 = prices.pct_change(63)
    mom_5 = prices.pct_change(5)
    mom_10 = prices.pct_change(10)
    high_52w = prices.rolling(252).max()
    low_52w = prices.rolling(252).min()
    
    factors = {}
    factors['mom_12_1'] = (mom_12 - mom_1).rank(axis=1, pct=True)
    factors['mom_6_1'] = (mom_6 - mom_1).rank(axis=1, pct=True)
    factors['low_vol'] = (-returns.rolling(63).std()).rank(axis=1, pct=True)
    rolling_ret = returns.rolling(252).mean()
    rolling_vol = returns.rolling(252).std()
    factors['quality'] = (rolling_ret / (rolling_vol + 1e-8)).rank(axis=1, pct=True)
    factors['reversal'] = (-mom_5).rank(axis=1, pct=True)
    factors['near_high'] = (prices / high_52w).rank(axis=1, pct=True)
    mom_accel = mom_3 - mom_3.shift(21)
    factors['acceleration'] = mom_accel.rank(axis=1, pct=True)
    mkt_ret_series = returns.mean(axis=1)
    excess_ret = returns.sub(mkt_ret_series, axis=0).rolling(63).mean()
    factors['rel_strength'] = excess_ret.rank(axis=1, pct=True)
    factors['range_position'] = ((prices - low_52w) / (high_52w - low_52w + 1e-8)).rank(axis=1, pct=True)
    factors['mom_10'] = mom_10.rank(axis=1, pct=True)
    
    FACTORS_NP = np.stack([
        factors['mom_12_1'].values,
        factors['mom_6_1'].values,
        factors['low_vol'].values,
        factors['quality'].values,
        factors['reversal'].values,
        factors['near_high'].values,
        factors['acceleration'].values,
        factors['rel_strength'].values,
        factors['range_position'].values,
        factors['mom_10'].values
    ], axis=2).astype(np.float64)
    
    FACTORS_NP = np.nan_to_num(FACTORS_NP, nan=0.5)
    print(f"  기존 팩터: 10개")
    
    # === Orthogonal 팩터 계산 (E1, E2, E3) ===
    print("\n[4] Orthogonal 팩터 계산 중...")
    
    if 'SPY' in etf.columns:
        mkt_benchmark = etf['SPY'].reindex(prices.index).ffill()
    else:
        mkt_benchmark = etf['QQQ'].reindex(prices.index).ffill()
    mkt_benchmark_ret = mkt_benchmark.pct_change().fillna(0)
    
    print("  - 베타 계산...")
    betas = pd.DataFrame(index=prices.index, columns=prices.columns, dtype=float)
    for col in prices.columns:
        stock_ret = returns[col]
        for t in range(252, len(prices)):
            window_stock = stock_ret.iloc[t-252:t].values
            window_mkt = mkt_benchmark_ret.iloc[t-252:t].values
            
            cov = np.cov(window_stock, window_mkt)[0, 1]
            var_mkt = np.var(window_mkt)
            if var_mkt > 1e-10:
                betas.iloc[t, betas.columns.get_loc(col)] = cov / var_mkt
            else:
                betas.iloc[t, betas.columns.get_loc(col)] = 1.0
    
    betas = betas.ffill().fillna(1.0)
    BETAS_NP = betas.values.astype(np.float64)
    
    print("  - E1: Residual Momentum 계산...")
    mkt_ret_expanded = mkt_benchmark_ret.values.reshape(-1, 1)
    expected_returns = mkt_ret_expanded * betas.values
    residual_returns = returns - expected_returns
    residual_mom_12 = residual_returns.rolling(252).sum()
    residual_mom_1 = residual_returns.rolling(21).sum()
    e1_residual_mom = (residual_mom_12 - residual_mom_1).rank(axis=1, pct=True)
    
    print("  - E2: Idiosyncratic Volatility 계산...")
    idio_vol = residual_returns.rolling(63).std()
    e2_idio_vol = (-idio_vol).rank(axis=1, pct=True)
    
    print("  - E3: BAB 계산...")
    e3_bab = (-betas).rank(axis=1, pct=True)
    
    ORTHO_FACTORS_NP = np.stack([
        e1_residual_mom.values,
        e2_idio_vol.values,
        e3_bab.values
    ], axis=2).astype(np.float64)
    
    ORTHO_FACTORS_NP = np.nan_to_num(ORTHO_FACTORS_NP, nan=0.5)
    print(f"  Orthogonal 팩터: 3개 (E1, E2, E3)")
    
    print(f"\n  데이터 준비 완료!")
    
    return N_DAYS, N_ASSETS


# ============================================================================
# Numba JIT 최적화 함수들
# ============================================================================

@njit(cache=True, fastmath=True)
def calculate_sharpe_numba(returns_array: np.ndarray) -> float:
    """Numba 최적화 Sharpe 계산"""
    n = len(returns_array)
    if n < 10:
        return 0.0
    
    mean_ret = 0.0
    for i in range(n):
        mean_ret += returns_array[i]
    mean_ret /= n
    
    var_ret = 0.0
    for i in range(n):
        var_ret += (returns_array[i] - mean_ret) ** 2
    var_ret /= n
    
    std_ret = np.sqrt(var_ret)
    if std_ret < 1e-10:
        return 0.0
    
    return mean_ret / std_ret * np.sqrt(252)


@njit(cache=True, fastmath=True)
def calculate_mdd_numba(values: np.ndarray) -> float:
    """Numba 최적화 MDD 계산"""
    n = len(values)
    peak = values[0]
    max_dd = 0.0
    
    for i in range(n):
        if values[i] > peak:
            peak = values[i]
        dd = (values[i] - peak) / peak
        if dd < max_dd:
            max_dd = dd
    
    return max_dd


def fast_backtest_v40_with_logging(params: Dict) -> Dict:
    """
    v40 클린 베이스라인 백테스트 + 카테고리 B, C, D, E 패치 + 정밀 로깅
    """
    
    n_days, n_assets = N_DAYS, N_ASSETS
    
    # 파라미터 추출
    trend_w = params['trend_w']
    vol_w = params['vol_w']
    credit_w = params['credit_w']
    breadth_w = params.get('breadth_w', 0.0)
    fast_w = params.get('fast_w', 0.0)
    
    crisis_thresh = params['crisis_thresh']
    bear_thresh = params['bear_thresh']
    neutral_thresh = params['neutral_thresh']
    bull_thresh = params['bull_thresh']
    
    ultra_bull_exp = params['ultra_bull_exp']
    bull_exp = params['bull_exp']
    neutral_exp = params['neutral_exp']
    bear_exp = params['bear_exp']
    crisis_exp = params['crisis_exp']
    
    fw_ultra_bull = np.array(params['fw_ultra_bull'])
    fw_bull = np.array(params['fw_bull'])
    fw_neutral = np.array(params['fw_neutral'])
    fw_bear = np.array(params['fw_bear'])
    fw_crisis = np.array(params['fw_crisis'])
    
    ortho_w = np.array(params.get('ortho_weights', [0.0, 0.0, 0.0]))
    
    top_k = params['top_k']
    rebal_days = params['rebal_days']
    target_vol = params['target_vol']
    max_lev = params['max_lev']
    min_lev = params['min_lev']
    cost_rate = params['cost_rate']
    dd_warning = params['dd_warning']
    dd_stop = params['dd_stop']
    
    # 카테고리 B 패치 플래그
    use_graduated_dd = params.get('use_graduated_dd', False)
    use_emergency_riskoff = params.get('use_emergency_riskoff', False)
    use_cvar_limit = params.get('use_cvar_limit', False)
    use_real_port_vol = params.get('use_real_port_vol', False)
    
    # 카테고리 C 패치 플래그
    use_hysteresis = params.get('use_hysteresis', False)
    use_fast_regime = params.get('use_fast_regime', False)
    use_breadth = params.get('use_breadth', False)
    
    # 카테고리 D 패치 플래그
    use_adaptive_rebal = params.get('use_adaptive_rebal', False)
    use_regime_factor_weights = params.get('use_regime_factor_weights', False)
    
    # 카테고리 E 패치 플래그
    use_orthogonal_factors = params.get('use_orthogonal_factors', False)
    
    # 패치별 파라미터
    hysteresis_width = params.get('hysteresis_width', 0.03)
    emergency_crisis_trigger = params.get('emergency_crisis_trigger', 0.75)
    emergency_loss_trigger = params.get('emergency_loss_trigger', -0.04)
    emergency_max_exp = params.get('emergency_max_exp', 0.25)
    cvar_limit = params.get('cvar_limit', -0.03)
    
    # 상태 변수
    values = np.zeros(n_days)
    values[0] = 1.0
    peak = 1.0
    
    prev_weights = np.zeros(n_assets)
    current_weights = np.zeros(n_assets)
    
    daily_returns = np.zeros(n_days)
    
    port_ret_buffer = []
    realized_vol_current = 0.20
    
    current_regime_level = 2
    
    # =========================================================================
    # 정밀 로깅 변수
    # =========================================================================
    regime_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}  # 레짐별 일수
    regime_returns = {0: [], 1: [], 2: [], 3: [], 4: []}  # 레짐별 수익률
    total_turnover = 0.0
    total_cost = 0.0
    rebalance_count = 0
    dd_events = []  # 드로다운 이벤트
    in_drawdown = False
    dd_start_idx = 0
    dd_peak_value = 1.0
    exposure_history = []  # 노출도 히스토리
    emergency_riskoff_count = 0  # 응급 risk-off 발동 횟수
    
    for t in range(1, n_days):
        # 1. 레짐 분류
        composite = (REGIME_SCORES_NP[t-1, 0] * trend_w + 
                    REGIME_SCORES_NP[t-1, 1] * vol_w + 
                    REGIME_SCORES_NP[t-1, 2] * credit_w)
        
        if use_breadth and breadth_w > 0:
            composite += BREADTH_NP[t-1] * breadth_w
        
        if use_fast_regime and fast_w > 0:
            total_w = trend_w + vol_w + credit_w + (breadth_w if use_breadth else 0)
            composite = composite / total_w * (1 - fast_w) + FAST_REGIME_NP[t-1] * fast_w
        else:
            total_w = trend_w + vol_w + credit_w + (breadth_w if use_breadth else 0)
            composite = composite / total_w
        
        crisis_prob = 1 - composite
        
        # 레짐 결정
        raw_level = 2
        if crisis_prob >= crisis_thresh:
            raw_level = 4
        elif crisis_prob >= bear_thresh:
            raw_level = 3
        elif crisis_prob >= neutral_thresh:
            raw_level = 2
        elif crisis_prob >= bull_thresh:
            raw_level = 1
        else:
            raw_level = 0
        
        # C1: 히스테리시스
        if use_hysteresis:
            thresholds = [0.0, bull_thresh, neutral_thresh, bear_thresh, crisis_thresh]
            if raw_level > current_regime_level and current_regime_level < 4:
                next_thresh = thresholds[current_regime_level + 1]
                if crisis_prob >= next_thresh + hysteresis_width:
                    current_regime_level += 1
            elif raw_level < current_regime_level and current_regime_level > 0:
                curr_thresh = thresholds[current_regime_level]
                if crisis_prob < curr_thresh - hysteresis_width:
                    current_regime_level -= 1
        else:
            current_regime_level = raw_level
        
        # 레짐 카운트 로깅
        regime_counts[current_regime_level] += 1
        
        # 레짐별 설정
        if current_regime_level == 4:
            base_exp, fw = crisis_exp, fw_crisis.copy()
        elif current_regime_level == 3:
            base_exp, fw = bear_exp, fw_bear.copy()
        elif current_regime_level == 2:
            base_exp, fw = neutral_exp, fw_neutral.copy()
        elif current_regime_level == 1:
            base_exp, fw = bull_exp, fw_bull.copy()
        else:
            base_exp, fw = ultra_bull_exp, fw_ultra_bull.copy()
        
        # D3: 레짐별 팩터 가중치 강화
        if use_regime_factor_weights:
            if current_regime_level >= 3:
                fw[2] *= 1.3  # low_vol
                fw[3] *= 1.3  # quality
                fw = fw / fw.sum()
        
        # 2. 리밸런싱
        if use_adaptive_rebal:
            if realized_vol_current < 0.15:
                interval = rebal_days
            elif realized_vol_current < 0.25:
                interval = max(7, int(rebal_days * 0.7))
            else:
                interval = max(5, int(rebal_days * 0.5))
            should_rebalance = (t % interval == 0)
        else:
            should_rebalance = (t % rebal_days == 0)
        
        if should_rebalance:
            rebalance_count += 1
            
            scores = np.zeros(n_assets)
            for i in range(n_assets):
                if ELIGIBILITY_NP[t-1, i] < 0.5:
                    scores[i] = -999.0
                else:
                    base_score = np.sum(FACTORS_NP[t-1, i, :] * fw)
                    
                    # E: Orthogonal 팩터
                    if use_orthogonal_factors and np.sum(np.abs(ortho_w)) > 0:
                        ortho_score = np.sum(ORTHO_FACTORS_NP[t-1, i, :] * ortho_w)
                        total_base_w = np.sum(fw)
                        total_ortho_w = np.sum(np.abs(ortho_w))
                        scores[i] = (base_score * total_base_w + ortho_score * total_ortho_w) / (total_base_w + total_ortho_w)
                    else:
                        scores[i] = base_score
            
            sorted_idx = np.argsort(-scores)
            top_idx = sorted_idx[:top_k]
            
            # B4: 실제 포트폴리오 변동성
            if use_real_port_vol and len(port_ret_buffer) >= 20:
                rv = np.std(port_ret_buffer[-20:]) * np.sqrt(252)
                realized_vol_current = rv
                if rv > 0.01:
                    vol_adj = min(2.0, max(0.5, target_vol / rv))
                else:
                    vol_adj = 1.0
            else:
                vol_adj = 1.0
            
            # B3: CVaR 제한
            if use_cvar_limit and len(port_ret_buffer) >= 20:
                sorted_rets = np.sort(port_ret_buffer[-60:] if len(port_ret_buffer) >= 60 else port_ret_buffer)
                cutoff = max(1, int(len(sorted_rets) * 0.05))
                cvar = np.mean(sorted_rets[:cutoff])
                if cvar < cvar_limit:
                    reduction = min(0.5, (cvar_limit - cvar) / abs(cvar_limit))
                    base_exp = base_exp * (1 - reduction)
            
            exposure = min(max_lev, max(min_lev, base_exp * vol_adj))
            
            current_weights = np.zeros(n_assets)
            valid_count = 0
            for idx in top_idx:
                if scores[idx] > -900:
                    valid_count += 1
            
            if exposure > 0.05 and valid_count > 0:
                weight_per_stock = exposure / valid_count
                for idx in top_idx:
                    if scores[idx] > -900:
                        current_weights[idx] = weight_per_stock
        
        # 3. DD Control
        current_dd = (values[t-1] - peak) / peak if peak > 0 else 0
        
        # B1: 점진적 DD Control
        if use_graduated_dd:
            if current_dd >= 0:
                dd_scale = 1.0
            elif current_dd >= dd_warning:
                dd_scale = 1.0 - 0.3 * (current_dd / dd_warning)
            elif current_dd >= dd_stop:
                dd_scale = 0.7 - 0.4 * ((current_dd - dd_warning) / (dd_stop - dd_warning))
            else:
                dd_scale = 0.3
        else:
            if current_dd <= dd_stop:
                dd_scale = 0.3
            elif current_dd <= dd_warning:
                dd_scale = 0.7
            else:
                dd_scale = 1.0
        
        active_weights = current_weights * dd_scale
        
        # B2: 응급 Risk-off
        if use_emergency_riskoff:
            prev_ret = daily_returns[t-1] if t > 1 else 0
            if crisis_prob >= emergency_crisis_trigger or prev_ret <= emergency_loss_trigger:
                total_exp = np.sum(active_weights)
                if total_exp > emergency_max_exp:
                    active_weights = active_weights * (emergency_max_exp / total_exp)
                    emergency_riskoff_count += 1
        
        # 노출도 로깅
        current_exposure = np.sum(active_weights)
        exposure_history.append(current_exposure)
        
        # 4. 수익률 계산
        turnover = np.sum(np.abs(active_weights - prev_weights))
        cost = turnover * cost_rate
        total_turnover += turnover
        total_cost += cost
        
        port_ret = np.sum(active_weights * RETURNS_NP[t, :]) - cost
        
        port_ret_buffer.append(port_ret)
        if len(port_ret_buffer) > 60:
            port_ret_buffer.pop(0)
        
        daily_returns[t] = port_ret
        values[t] = values[t-1] * (1 + port_ret)
        
        # 레짐별 수익률 로깅
        regime_returns[current_regime_level].append(port_ret)
        
        # 드로다운 이벤트 추적
        if values[t] > peak:
            if in_drawdown:
                # 드로다운 종료
                dd_events.append({
                    'start_idx': dd_start_idx,
                    'end_idx': t,
                    'duration': t - dd_start_idx,
                    'depth': (values[dd_start_idx:t].min() - dd_peak_value) / dd_peak_value
                })
                in_drawdown = False
            peak = values[t]
            dd_peak_value = peak
        else:
            if not in_drawdown and current_dd < -0.05:  # 5% 이상 드로다운 시작
                in_drawdown = True
                dd_start_idx = t
        
        prev_weights = active_weights.copy()
    
    # =========================================================================
    # 성과 지표 계산
    # =========================================================================
    valid_returns = daily_returns[1:]
    sharpe = calculate_sharpe_numba(valid_returns)
    
    total_return = values[-1] / values[0] - 1
    n_years = n_days / 252.0
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
    
    max_dd = calculate_mdd_numba(values)
    
    # IS/OOS 분리
    is_end = int(n_days * 0.55)
    
    is_returns = daily_returns[1:is_end]
    is_sharpe = calculate_sharpe_numba(is_returns)
    
    oos_returns = daily_returns[is_end:]
    oos_sharpe = calculate_sharpe_numba(oos_returns)
    
    # =========================================================================
    # 정밀 로깅 결과
    # =========================================================================
    regime_sharpes = {}
    for level in range(5):
        if len(regime_returns[level]) > 10:
            regime_sharpes[level] = calculate_sharpe_numba(np.array(regime_returns[level]))
        else:
            regime_sharpes[level] = 0.0
    
    avg_exposure = np.mean(exposure_history) if exposure_history else 0.0
    avg_turnover_per_rebal = total_turnover / rebalance_count if rebalance_count > 0 else 0.0
    
    avg_dd_depth = np.mean([e['depth'] for e in dd_events]) if dd_events else 0.0
    avg_dd_duration = np.mean([e['duration'] for e in dd_events]) if dd_events else 0.0
    
    return {
        'sharpe': sharpe,
        'annual_return': annual_return,
        'mdd': max_dd,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'is_oos_gap': (is_sharpe - oos_sharpe) / is_sharpe if is_sharpe > 0 else 0,
        # 정밀 로깅
        'regime_counts': regime_counts,
        'regime_sharpes': regime_sharpes,
        'total_turnover': total_turnover,
        'total_cost': total_cost,
        'rebalance_count': rebalance_count,
        'avg_turnover_per_rebal': avg_turnover_per_rebal,
        'avg_exposure': avg_exposure,
        'dd_event_count': len(dd_events),
        'avg_dd_depth': avg_dd_depth,
        'avg_dd_duration': avg_dd_duration,
        'emergency_riskoff_count': emergency_riskoff_count,
    }


def generate_category_combinations():
    """카테고리별 수만 가지 조합 생성"""
    
    # v40 베이스라인 파라미터
    base_params = {
        'trend_w': 0.47, 'vol_w': 0.28, 'credit_w': 0.25,
        'crisis_thresh': 0.70, 'bear_thresh': 0.50, 'neutral_thresh': 0.30, 'bull_thresh': 0.13,
        'ultra_bull_exp': 2.0, 'bull_exp': 1.7, 'neutral_exp': 1.2, 'bear_exp': 0.6, 'crisis_exp': 0.24,
        'fw_ultra_bull': [0.22, 0.18, 0.05, 0.07, 0.07, 0.13, 0.09, 0.08, 0.06, 0.05],
        'fw_bull': [0.20, 0.16, 0.07, 0.09, 0.09, 0.11, 0.09, 0.09, 0.05, 0.05],
        'fw_neutral': [0.11, 0.11, 0.18, 0.16, 0.11, 0.07, 0.09, 0.07, 0.05, 0.05],
        'fw_bear': [0.07, 0.07, 0.26, 0.23, 0.14, 0.05, 0.06, 0.05, 0.04, 0.03],
        'fw_crisis': [0.05, 0.05, 0.30, 0.26, 0.14, 0.05, 0.05, 0.05, 0.03, 0.02],
        'top_k': 48, 'rebal_days': 14, 'target_vol': 0.29, 'max_lev': 2.0, 'min_lev': 0.3,
        'cost_rate': 0.002, 'dd_warning': -0.20, 'dd_stop': -0.35,
        'ortho_weights': [0.0, 0.0, 0.0],
    }
    
    param_list = []
    
    # =========================================================================
    # 1. v40 베이스라인 (카테고리 A만 적용)
    # =========================================================================
    baseline = base_params.copy()
    baseline['category'] = 'v40_baseline'
    param_list.append(baseline)
    
    # =========================================================================
    # 2. v40 + 카테고리 B (리스크 관리 강화)
    # =========================================================================
    # B1: 점진적 DD Control
    for dd_warning in [-0.15, -0.20, -0.25]:
        for dd_stop in [-0.30, -0.35, -0.40]:
            p = base_params.copy()
            p['use_graduated_dd'] = True
            p['dd_warning'] = dd_warning
            p['dd_stop'] = dd_stop
            p['category'] = 'B1_graduated_dd'
            param_list.append(p)
    
    # B2: 응급 Risk-off
    for crisis_trigger in [0.70, 0.75, 0.80]:
        for loss_trigger in [-0.03, -0.04, -0.05]:
            for max_exp in [0.20, 0.25, 0.30]:
                p = base_params.copy()
                p['use_emergency_riskoff'] = True
                p['emergency_crisis_trigger'] = crisis_trigger
                p['emergency_loss_trigger'] = loss_trigger
                p['emergency_max_exp'] = max_exp
                p['min_lev'] = 0.0
                p['category'] = 'B2_emergency_riskoff'
                param_list.append(p)
    
    # B3: CVaR Tail Risk
    for cvar_limit in [-0.02, -0.03, -0.04]:
        p = base_params.copy()
        p['use_cvar_limit'] = True
        p['cvar_limit'] = cvar_limit
        p['category'] = 'B3_cvar_limit'
        param_list.append(p)
    
    # B4: 실제 포트폴리오 변동성
    for target_vol in [0.25, 0.29, 0.33]:
        p = base_params.copy()
        p['use_real_port_vol'] = True
        p['target_vol'] = target_vol
        p['category'] = 'B4_real_port_vol'
        param_list.append(p)
    
    # B 전체 조합
    for dd_warning in [-0.15, -0.20]:
        for crisis_trigger in [0.75, 0.80]:
            for cvar_limit in [-0.03, -0.04]:
                for target_vol in [0.29, 0.33]:
                    p = base_params.copy()
                    p['use_graduated_dd'] = True
                    p['dd_warning'] = dd_warning
                    p['dd_stop'] = dd_warning - 0.15
                    p['use_emergency_riskoff'] = True
                    p['emergency_crisis_trigger'] = crisis_trigger
                    p['emergency_loss_trigger'] = -0.04
                    p['emergency_max_exp'] = 0.25
                    p['min_lev'] = 0.0
                    p['use_cvar_limit'] = True
                    p['cvar_limit'] = cvar_limit
                    p['use_real_port_vol'] = True
                    p['target_vol'] = target_vol
                    p['category'] = 'B_all'
                    param_list.append(p)
    
    # =========================================================================
    # 3. v40 + 카테고리 C (레짐 대응 강화)
    # =========================================================================
    # C1: 히스테리시스
    for width in [0.02, 0.03, 0.04, 0.05]:
        p = base_params.copy()
        p['use_hysteresis'] = True
        p['hysteresis_width'] = width
        p['category'] = 'C1_hysteresis'
        param_list.append(p)
    
    # C2: Fast Regime
    for fast_w in [0.10, 0.15, 0.20, 0.25]:
        p = base_params.copy()
        p['use_fast_regime'] = True
        p['fast_w'] = fast_w
        p['category'] = 'C2_fast_regime'
        param_list.append(p)
    
    # C3: Breadth
    for breadth_w in [0.05, 0.10, 0.15, 0.20]:
        p = base_params.copy()
        p['use_breadth'] = True
        p['breadth_w'] = breadth_w
        p['trend_w'] = 0.47 - breadth_w / 2
        p['vol_w'] = 0.28 - breadth_w / 4
        p['credit_w'] = 0.25 - breadth_w / 4
        p['category'] = 'C3_breadth'
        param_list.append(p)
    
    # C 전체 조합
    for width in [0.02, 0.03, 0.04]:
        for fast_w in [0.10, 0.15, 0.20]:
            for breadth_w in [0.05, 0.10, 0.15]:
                p = base_params.copy()
                p['use_hysteresis'] = True
                p['hysteresis_width'] = width
                p['use_fast_regime'] = True
                p['fast_w'] = fast_w
                p['use_breadth'] = True
                p['breadth_w'] = breadth_w
                p['trend_w'] = 0.47 - breadth_w / 2
                p['vol_w'] = 0.28 - breadth_w / 4
                p['credit_w'] = 0.25 - breadth_w / 4
                p['category'] = 'C_all'
                param_list.append(p)
    
    # =========================================================================
    # 4. v40 + 카테고리 D (포트폴리오 구성)
    # =========================================================================
    # D1: 적응형 리밸런싱
    for rebal_days in [7, 10, 14, 21]:
        p = base_params.copy()
        p['use_adaptive_rebal'] = True
        p['rebal_days'] = rebal_days
        p['category'] = 'D1_adaptive_rebal'
        param_list.append(p)
    
    # D3: 레짐별 팩터 가중치
    p = base_params.copy()
    p['use_regime_factor_weights'] = True
    p['category'] = 'D3_regime_factor_weights'
    param_list.append(p)
    
    # D 전체 조합
    for rebal_days in [7, 10, 14]:
        p = base_params.copy()
        p['use_adaptive_rebal'] = True
        p['rebal_days'] = rebal_days
        p['use_regime_factor_weights'] = True
        p['category'] = 'D_all'
        param_list.append(p)
    
    # =========================================================================
    # 5. v40 + 카테고리 E (Orthogonal 팩터)
    # =========================================================================
    ortho_options = [
        [0.10, 0.10, 0.10],
        [0.15, 0.10, 0.05],
        [0.05, 0.15, 0.10],
        [0.05, 0.10, 0.15],
        [0.20, 0.15, 0.15],
        [0.25, 0.20, 0.20],
        [0.30, 0.10, 0.10],
    ]
    
    for ortho_w in ortho_options:
        p = base_params.copy()
        p['use_orthogonal_factors'] = True
        p['ortho_weights'] = ortho_w
        p['category'] = 'E_orthogonal'
        param_list.append(p)
    
    # =========================================================================
    # 6. v40 + 최적 조합 (각 카테고리에서 효과 있는 패치 선별)
    # =========================================================================
    # 이전 테스트에서 효과가 있었던 조합들
    best_combos = [
        # B4 + C1 + C3
        {'use_real_port_vol': True, 'target_vol': 0.29,
         'use_hysteresis': True, 'hysteresis_width': 0.03,
         'use_breadth': True, 'breadth_w': 0.10,
         'trend_w': 0.42, 'vol_w': 0.25, 'credit_w': 0.23},
        
        # B1 + B2 + C1 + C3
        {'use_graduated_dd': True, 'dd_warning': -0.20, 'dd_stop': -0.35,
         'use_emergency_riskoff': True, 'emergency_crisis_trigger': 0.75,
         'emergency_loss_trigger': -0.04, 'emergency_max_exp': 0.25, 'min_lev': 0.0,
         'use_hysteresis': True, 'hysteresis_width': 0.03,
         'use_breadth': True, 'breadth_w': 0.10,
         'trend_w': 0.42, 'vol_w': 0.25, 'credit_w': 0.23},
        
        # 전체 조합
        {'use_graduated_dd': True, 'dd_warning': -0.20, 'dd_stop': -0.35,
         'use_emergency_riskoff': True, 'emergency_crisis_trigger': 0.75,
         'emergency_loss_trigger': -0.04, 'emergency_max_exp': 0.25, 'min_lev': 0.0,
         'use_cvar_limit': True, 'cvar_limit': -0.03,
         'use_real_port_vol': True, 'target_vol': 0.29,
         'use_hysteresis': True, 'hysteresis_width': 0.03,
         'use_fast_regime': True, 'fast_w': 0.15,
         'use_breadth': True, 'breadth_w': 0.10,
         'trend_w': 0.42, 'vol_w': 0.25, 'credit_w': 0.23,
         'use_adaptive_rebal': True, 'rebal_days': 14,
         'use_regime_factor_weights': True,
         'use_orthogonal_factors': True, 'ortho_weights': [0.15, 0.10, 0.05]},
    ]
    
    for combo in best_combos:
        p = base_params.copy()
        p.update(combo)
        p['category'] = 'best_combo'
        param_list.append(p)
    
    return param_list


def main():
    """메인 실행"""
    
    # 1. 데이터 초기화
    init_global_data()
    
    # 2. 조합 생성
    param_list = generate_category_combinations()
    print(f"\n전체 조합 수: {len(param_list)}")
    
    # 카테고리별 조합 수
    categories = {}
    for p in param_list:
        cat = p.get('category', 'unknown')
        categories[cat] = categories.get(cat, 0) + 1
    
    print("\n카테고리별 조합 수:")
    for cat, count in sorted(categories.items()):
        print(f"  {cat}: {count}")
    
    # 3. 테스트 실행
    print("\n[5] 카테고리별 조합 테스트 시작...")
    
    results = []
    start_time = time.time()
    
    for i, params in enumerate(param_list):
        try:
            result = fast_backtest_v40_with_logging(params)
            result['params'] = params
            result['category'] = params.get('category', 'unknown')
            results.append(result)
        except Exception as e:
            print(f"  Error at {i}: {e}")
        
        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed
            remaining = (len(param_list) - i - 1) / rate
            print(f"  진행: {i+1}/{len(param_list)} ({100*(i+1)/len(param_list):.1f}%) | "
                  f"유효: {len(results)} | 남은 시간: {remaining/60:.1f}분")
    
    # 4. 결과 정렬 및 출력
    print("\n" + "=" * 80)
    print("테스트 완료!")
    print("=" * 80)
    
    results_oos = sorted(results, key=lambda x: x['oos_sharpe'], reverse=True)
    
    print("\n[TOP 30 - OOS Sharpe 기준]")
    print("-" * 200)
    print(f"{'순위':>4} | {'Sharpe':>8} | {'IS':>8} | {'OOS':>8} | {'Gap':>8} | {'MDD':>8} | {'Annual':>8} | {'Turnover':>10} | {'AvgExp':>8} | {'DD Events':>10} | 카테고리")
    print("-" * 200)
    
    for i, r in enumerate(results_oos[:30]):
        print(f"{i+1:>4} | {r['sharpe']:>8.3f} | {r['is_sharpe']:>8.3f} | {r['oos_sharpe']:>8.3f} | "
              f"{r['is_oos_gap']*100:>7.1f}% | {r['mdd']*100:>7.1f}% | {r['annual_return']*100:>7.1f}% | "
              f"{r['total_turnover']:>10.1f} | {r['avg_exposure']:>8.2f} | {r['dd_event_count']:>10} | {r['category']}")
    
    # 5. 카테고리별 분석
    print("\n[카테고리별 평균 OOS Sharpe]")
    print("-" * 80)
    for cat in sorted(categories.keys()):
        cat_results = [r for r in results if r['category'] == cat]
        if cat_results:
            avg_oos = np.mean([r['oos_sharpe'] for r in cat_results])
            max_oos = max([r['oos_sharpe'] for r in cat_results])
            avg_mdd = np.mean([r['mdd'] for r in cat_results])
            print(f"  {cat:30}: 평균 OOS={avg_oos:.3f}, 최대 OOS={max_oos:.3f}, 평균 MDD={avg_mdd*100:.1f}%")
    
    # 6. v40 베이스라인 결과
    baseline = next((r for r in results if r['category'] == 'v40_baseline'), None)
    if baseline:
        print("\n[v40 베이스라인]")
        print(f"  Sharpe: {baseline['sharpe']:.3f} | IS: {baseline['is_sharpe']:.3f} | "
              f"OOS: {baseline['oos_sharpe']:.3f} | MDD: {baseline['mdd']*100:.1f}%")
    
    # 7. 정밀 로깅 분석 (TOP 1)
    if results_oos:
        top1 = results_oos[0]
        print("\n[TOP 1 정밀 로깅 분석]")
        print(f"  카테고리: {top1['category']}")
        print(f"  레짐별 일수: {top1['regime_counts']}")
        print(f"  레짐별 Sharpe: {top1['regime_sharpes']}")
        print(f"  총 턴오버: {top1['total_turnover']:.1f}")
        print(f"  총 비용: {top1['total_cost']*100:.2f}%")
        print(f"  리밸런싱 횟수: {top1['rebalance_count']}")
        print(f"  평균 노출도: {top1['avg_exposure']:.2f}")
        print(f"  드로다운 이벤트: {top1['dd_event_count']}회")
        print(f"  평균 드로다운 깊이: {top1['avg_dd_depth']*100:.1f}%")
        print(f"  평균 드로다운 기간: {top1['avg_dd_duration']:.0f}일")
        print(f"  응급 Risk-off 발동: {top1['emergency_riskoff_count']}회")
    
    # 8. 결과 저장
    output = {
        'timestamp': datetime.now().isoformat(),
        'total_tests': len(results),
        'categories': categories,
        'top_oos': results_oos[:100],
        'baseline': baseline,
    }
    
    with open('/home/ubuntu/v40_category_pareto_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n결과 저장: /home/ubuntu/v40_category_pareto_results.json")
    print(f"완료 시간: {datetime.now()}")
    print(f"총 소요 시간: {(time.time() - start_time)/60:.1f}분")


if __name__ == "__main__":
    main()
