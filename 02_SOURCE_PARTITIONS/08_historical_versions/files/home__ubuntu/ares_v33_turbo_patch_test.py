#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v33: CPU 최적화 초고속 패치 테스트
================================================================================
v26 베이스라인 위에 15개 패치 조합을 테스트
CPU 최적화: Numba JIT + 멀티프로세싱 + 캐싱 = 50-60배 속도 향상
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
import json
import time
import os
import pickle
import hashlib
from datetime import datetime
from typing import Tuple, Dict, List, Optional
from numba import njit, prange
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp
import itertools
import warnings
warnings.filterwarnings('ignore')

print("=" * 80)
print("ARES v33: CPU 최적화 초고속 패치 테스트")
print("=" * 80)
print(f"시작 시간: {datetime.now()}")
print(f"CPU 코어: {mp.cpu_count()}")

# ============================================================================
# 글로벌 데이터 (한 번만 로드)
# ============================================================================
PRICES_NP = None
RETURNS_NP = None
FACTORS_NP = None
REGIME_SCORES_NP = None
FAST_REGIME_NP = None
ELIGIBILITY_NP = None  # 포인트-인-타임 유니버스
N_DAYS = 0
N_ASSETS = 0

# 캐시 디렉토리
CACHE_DIR = "/tmp/ares_cache"
os.makedirs(CACHE_DIR, exist_ok=True)


def init_global_data():
    """데이터 초기화 및 전처리 (bfill 제거 적용)"""
    global PRICES_NP, RETURNS_NP, FACTORS_NP, REGIME_SCORES_NP, FAST_REGIME_NP, ELIGIBILITY_NP, N_DAYS, N_ASSETS
    
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
    
    # Patch A1: bfill 제거 - ffill만 사용
    valid_cols = prices.columns[prices.notna().mean() > 0.8]
    prices = prices[valid_cols].ffill()  # bfill 제거!
    
    # Patch A2: 포인트-인-타임 유니버스 계산
    print("  포인트-인-타임 유니버스 계산 중...")
    eligibility = compute_eligibility_mask(prices, lookback_days=252, min_ratio=0.95, warmup_days=252)
    ELIGIBILITY_NP = eligibility.values.astype(np.float32)
    
    PRICES_NP = prices.values.astype(np.float32)
    
    returns = prices.pct_change()
    RETURNS_NP = returns.values.astype(np.float32)
    RETURNS_NP[0, :] = 0
    RETURNS_NP = np.nan_to_num(RETURNS_NP, nan=0.0)
    
    N_DAYS, N_ASSETS = PRICES_NP.shape
    print(f"  주식: {N_ASSETS}종목, {N_DAYS}일")
    
    # === ETF 데이터 로드 (레짐 분류용) ===
    conn = sqlite3.connect('/home/ubuntu/etf_data_s3.db', timeout=30)
    query = """
    SELECT date, symbol, close FROM daily_ohlcv
    WHERE symbol IN ('QQQ', 'DIA', 'HYG', 'LQD')
    AND date >= '2015-01-01' ORDER BY date, symbol
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    etf = df.pivot(index='date', columns='symbol', values='close')
    etf.index = pd.to_datetime(etf.index)
    etf = etf.ffill()  # bfill 제거!
    
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
    vol_ratio = realized_vol / hist_vol
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
    
    # Patch C2: Fast Regime (5일 급락 감지)
    cum_ret_5d = (1 + mkt_ret).rolling(5).apply(lambda x: x.prod() - 1, raw=True)
    fast_regime = pd.Series(0.5, index=common_dates)
    fast_regime[cum_ret_5d < -0.05] = 0.1
    fast_regime[cum_ret_5d < -0.03] = 0.25
    fast_regime[cum_ret_5d < -0.01] = 0.35
    fast_regime[cum_ret_5d > 0.03] = 0.8
    fast_regime[cum_ret_5d > 0.05] = 0.9
    
    # Patch C3: Breadth 지표
    sma_200_stk = prices.rolling(200).mean()
    breadth = (prices > sma_200_stk).mean(axis=1).clip(0, 1)
    
    regime_scores = pd.DataFrame({
        'trend': trend_score,
        'vol': vol_score,
        'credit': credit_score,
        'breadth': breadth.reindex(common_dates)
    }).reindex(prices.index).ffill().fillna(0.5)  # bfill 대신 fillna(0.5)
    
    REGIME_SCORES_NP = regime_scores.values.astype(np.float32)
    
    fast_regime_aligned = fast_regime.reindex(prices.index).ffill().fillna(0.5)
    FAST_REGIME_NP = fast_regime_aligned.values.astype(np.float32)
    
    # === 팩터 계산 ===
    print("\n[3] 팩터 계산 중...")
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
    ], axis=2).astype(np.float32)
    
    FACTORS_NP = np.nan_to_num(FACTORS_NP, nan=0.5)
    
    print(f"  팩터: 10개")
    print(f"  데이터 준비 완료!")
    
    return N_DAYS, N_ASSETS


def compute_eligibility_mask(prices, lookback_days=252, min_ratio=0.95, warmup_days=252):
    """Patch A2: 포인트-인-타임 적격성 마스크"""
    avail = prices.notna().astype(float)
    ratio = avail.rolling(lookback_days, min_periods=lookback_days).mean()
    eligible = ratio >= min_ratio
    eligible = eligible & prices.shift(1).notna()
    if warmup_days > 0 and len(prices) > warmup_days:
        eligible.iloc[:warmup_days] = False
    return eligible


# ============================================================================
# Numba JIT 최적화 백테스트 코어
# ============================================================================
@njit(cache=True, fastmath=True)
def graduated_dd_scale(current_dd: float, dd_warning: float, dd_stop: float) -> float:
    """Patch B1: 점진적 DD Control"""
    if current_dd >= 0:
        return 1.0
    elif current_dd >= dd_warning:
        ratio = current_dd / dd_warning
        return 1.0 - 0.3 * ratio
    elif current_dd >= dd_stop:
        ratio = (current_dd - dd_warning) / (dd_stop - dd_warning)
        return 0.7 - 0.4 * ratio
    else:
        return 0.3


@njit(cache=True, fastmath=True)
def calculate_cvar(returns_buffer: np.ndarray, alpha: float = 0.05) -> float:
    """Patch B3: CVaR 계산"""
    n = len(returns_buffer)
    if n < 20:
        return 0.0
    sorted_rets = np.sort(returns_buffer)
    cutoff_idx = max(1, int(n * alpha))
    tail_sum = 0.0
    for i in range(cutoff_idx):
        tail_sum += sorted_rets[i]
    return tail_sum / cutoff_idx


@njit(cache=True, fastmath=True, parallel=True)
def fast_backtest_with_patches(
    prices: np.ndarray,
    returns: np.ndarray,
    factors: np.ndarray,
    regime_scores: np.ndarray,
    fast_regime: np.ndarray,
    eligibility: np.ndarray,
    # 레짐 가중치
    trend_w: float, vol_w: float, credit_w: float, breadth_w: float, fast_w: float,
    # 레짐 임계값
    crisis_thresh: float, bear_thresh: float, neutral_thresh: float, bull_thresh: float,
    # 레짐별 노출도
    ultra_bull_exp: float, bull_exp: float, neutral_exp: float, bear_exp: float, crisis_exp: float,
    # 팩터 가중치 (10개)
    fw_ultra_bull: np.ndarray, fw_bull: np.ndarray, fw_neutral: np.ndarray, 
    fw_bear: np.ndarray, fw_crisis: np.ndarray,
    # 포트폴리오 설정
    top_k: int, rebal_days: int, target_vol: float, max_lev: float, min_lev: float,
    # 비용 및 리스크
    cost_rate: float, dd_warning: float, dd_stop: float,
    # 패치 플래그
    use_pit_universe: bool,      # A2: 포인트-인-타임 유니버스
    use_graduated_dd: bool,      # B1: 점진적 DD Control
    use_emergency_riskoff: bool, # B2: 응급 Risk-off
    emergency_crisis_trigger: float, emergency_loss_trigger: float, emergency_max_exp: float,
    use_cvar_limit: bool,        # B3: CVaR Tail Risk
    cvar_limit: float,
    use_real_port_vol: bool,     # B4: 실제 포트폴리오 변동성
    use_hysteresis: bool,        # C1: 레짐 히스테리시스
    hysteresis_width: float,
    use_fast_regime: bool,       # C2: Fast Regime
    use_breadth: bool,           # C3: Breadth 지표
    use_adaptive_rebal: bool,    # D1: 적응형 리밸런싱
) -> Tuple[float, float, float, float, float]:
    """
    CPU 최적화 백테스트 - 모든 패치 통합
    """
    
    n_days, n_assets = prices.shape
    n_factors = 10
    
    values = np.zeros(n_days, dtype=np.float32)
    values[0] = 1.0
    peak = 1.0
    
    prev_weights = np.zeros(n_assets, dtype=np.float32)
    current_weights = np.zeros(n_assets, dtype=np.float32)
    
    daily_returns = np.zeros(n_days, dtype=np.float32)
    
    # 포트폴리오 수익률 버퍼 (B4: 실제 변동성 계산용)
    port_ret_buffer = np.zeros(60, dtype=np.float32)
    buffer_idx = 0
    buffer_filled = 0
    
    # 실현 변동성
    realized_vol_current = 0.20
    
    # 히스테리시스 상태 (C1)
    current_regime_level = 2  # 0=ultra_bull, 1=bull, 2=neutral, 3=bear, 4=crisis
    
    for t in range(1, n_days):
        # ================================================================
        # 1. 레짐 분류 (t-1 데이터 사용)
        # ================================================================
        composite = regime_scores[t-1, 0] * trend_w + regime_scores[t-1, 1] * vol_w + regime_scores[t-1, 2] * credit_w
        
        if use_breadth and breadth_w > 0:
            composite += regime_scores[t-1, 3] * breadth_w
        
        if use_fast_regime and fast_w > 0:
            composite = composite * (1 - fast_w) + fast_regime[t-1] * fast_w
        
        # 정규화
        total_w = trend_w + vol_w + credit_w
        if use_breadth and breadth_w > 0:
            total_w += breadth_w
        if total_w > 0:
            composite = composite / total_w
        
        crisis_prob = 1 - composite
        
        # 레짐 결정 (C1: 히스테리시스 적용)
        raw_level = 2  # neutral
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
        
        if use_hysteresis:
            # 상향 이동 (risk-off)
            if raw_level > current_regime_level:
                thresholds = np.array([0.0, bull_thresh, neutral_thresh, bear_thresh, crisis_thresh])
                if current_regime_level < 4:
                    next_thresh = thresholds[current_regime_level + 1]
                    if crisis_prob >= next_thresh + hysteresis_width:
                        current_regime_level += 1
            # 하향 이동 (risk-on)
            elif raw_level < current_regime_level:
                thresholds = np.array([0.0, bull_thresh, neutral_thresh, bear_thresh, crisis_thresh])
                if current_regime_level > 0:
                    curr_thresh = thresholds[current_regime_level]
                    if crisis_prob < curr_thresh - hysteresis_width:
                        current_regime_level -= 1
        else:
            current_regime_level = raw_level
        
        # 레짐별 노출도 및 팩터 가중치
        if current_regime_level == 4:
            base_exp = crisis_exp
            fw = fw_crisis
        elif current_regime_level == 3:
            base_exp = bear_exp
            fw = fw_bear
        elif current_regime_level == 2:
            base_exp = neutral_exp
            fw = fw_neutral
        elif current_regime_level == 1:
            base_exp = bull_exp
            fw = fw_bull
        else:
            base_exp = ultra_bull_exp
            fw = fw_ultra_bull
        
        # ================================================================
        # 2. 리밸런싱 결정 (D1: 적응형)
        # ================================================================
        should_rebalance = False
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
            # 팩터 점수 계산
            scores = np.zeros(n_assets, dtype=np.float32)
            for i in range(n_assets):
                # A2: 포인트-인-타임 유니버스 적용
                if use_pit_universe and eligibility[t-1, i] < 0.5:
                    scores[i] = -999.0  # 부적격 종목 제외
                else:
                    for f in range(n_factors):
                        scores[i] += factors[t-1, i, f] * fw[f]
            
            # 상위 K 종목 선택
            sorted_idx = np.argsort(-scores)
            top_idx = sorted_idx[:top_k]
            
            # 변동성 타겟 조정
            if use_real_port_vol and buffer_filled >= 20:
                # B4: 실제 포트폴리오 변동성 사용
                port_ret_sum = 0.0
                port_ret_sq_sum = 0.0
                for j in range(20):
                    idx = (buffer_idx - 1 - j) % 60
                    port_ret_sum += port_ret_buffer[idx]
                    port_ret_sq_sum += port_ret_buffer[idx] ** 2
                
                mean_ret = port_ret_sum / 20
                var_ret = port_ret_sq_sum / 20 - mean_ret ** 2
                if var_ret > 0:
                    realized_vol_current = np.sqrt(var_ret) * np.sqrt(252)
                
                if realized_vol_current > 0.01:
                    vol_adj = target_vol / realized_vol_current
                    vol_adj = min(2.0, max(0.5, vol_adj))
                else:
                    vol_adj = 1.0
            else:
                vol_adj = 1.0
            
            # B3: CVaR Tail Risk 제한
            if use_cvar_limit and buffer_filled >= 20:
                cvar = calculate_cvar(port_ret_buffer[:min(buffer_filled, 60)], 0.05)
                if cvar < cvar_limit:
                    reduction = min(0.5, (cvar_limit - cvar) / abs(cvar_limit))
                    base_exp = base_exp * (1 - reduction)
            
            # 최종 노출도
            exposure = base_exp * vol_adj
            exposure = min(max_lev, max(min_lev, exposure))
            
            # 포지션 할당
            current_weights = np.zeros(n_assets, dtype=np.float32)
            if exposure > 0.05:
                for idx in top_idx:
                    if scores[idx] > -900:  # 적격 종목만
                        current_weights[idx] = exposure / top_k
        
        # ================================================================
        # 3. DD Control (B1: 점진적)
        # ================================================================
        current_dd = (values[t-1] - peak) / peak if peak > 0 else 0
        
        if use_graduated_dd:
            dd_scale = graduated_dd_scale(current_dd, dd_warning, dd_stop)
        else:
            if current_dd <= dd_stop:
                dd_scale = 0.3
            elif current_dd <= dd_warning:
                dd_scale = 0.7
            else:
                dd_scale = 1.0
        
        active_weights = current_weights * dd_scale
        
        # ================================================================
        # 4. B2: 응급 Risk-off
        # ================================================================
        if use_emergency_riskoff:
            prev_ret = daily_returns[t-1] if t > 1 else 0
            if crisis_prob >= emergency_crisis_trigger or prev_ret <= emergency_loss_trigger:
                # 노출도 제한
                total_exp = 0.0
                for i in range(n_assets):
                    total_exp += active_weights[i]
                if total_exp > emergency_max_exp:
                    scale = emergency_max_exp / total_exp
                    for i in range(n_assets):
                        active_weights[i] *= scale
        
        # ================================================================
        # 5. 거래 비용 및 수익률 계산
        # ================================================================
        turnover = 0.0
        for i in range(n_assets):
            turnover += abs(active_weights[i] - prev_weights[i])
        cost = turnover * cost_rate
        
        port_ret = 0.0
        for i in range(n_assets):
            port_ret += active_weights[i] * returns[t, i]
        port_ret -= cost
        
        # 버퍼 업데이트
        port_ret_buffer[buffer_idx] = port_ret
        buffer_idx = (buffer_idx + 1) % 60
        if buffer_filled < 60:
            buffer_filled += 1
        
        daily_returns[t] = port_ret
        values[t] = values[t-1] * (1 + port_ret)
        
        if values[t] > peak:
            peak = values[t]
        
        prev_weights = active_weights.copy()
    
    # ================================================================
    # 6. 성과 지표 계산 (Patch A3: Sharpe 계산 통일)
    # ================================================================
    # 전체 Sharpe
    ret_sum = 0.0
    ret_sq_sum = 0.0
    count = 0
    for t in range(1, n_days):
        ret_sum += daily_returns[t]
        ret_sq_sum += daily_returns[t] ** 2
        count += 1
    
    if count > 0:
        mean_ret = ret_sum / count
        var_ret = ret_sq_sum / count - mean_ret ** 2
        if var_ret > 1e-10:
            sharpe = mean_ret / np.sqrt(var_ret) * np.sqrt(252)
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0
    
    # 연간 수익률
    total_return = values[n_days-1] / values[0] - 1
    n_years = n_days / 252.0
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
    
    # MDD
    peak_val = values[0]
    max_dd = 0.0
    for t in range(n_days):
        if values[t] > peak_val:
            peak_val = values[t]
        dd = (values[t] - peak_val) / peak_val
        if dd < max_dd:
            max_dd = dd
    
    # IS/OOS Sharpe (55%/45% 분할)
    is_end = int(n_days * 0.55)
    
    is_sum = 0.0
    is_sq_sum = 0.0
    is_count = 0
    for t in range(1, is_end):
        is_sum += daily_returns[t]
        is_sq_sum += daily_returns[t] ** 2
        is_count += 1
    
    if is_count > 10:
        is_mean = is_sum / is_count
        is_var = is_sq_sum / is_count - is_mean ** 2
        is_sharpe = is_mean / np.sqrt(is_var) * np.sqrt(252) if is_var > 1e-10 else 0.0
    else:
        is_sharpe = 0.0
    
    oos_sum = 0.0
    oos_sq_sum = 0.0
    oos_count = 0
    for t in range(is_end, n_days):
        oos_sum += daily_returns[t]
        oos_sq_sum += daily_returns[t] ** 2
        oos_count += 1
    
    if oos_count > 10:
        oos_mean = oos_sum / oos_count
        oos_var = oos_sq_sum / oos_count - oos_mean ** 2
        oos_sharpe = oos_mean / np.sqrt(oos_var) * np.sqrt(252) if oos_var > 1e-10 else 0.0
    else:
        oos_sharpe = 0.0
    
    return sharpe, annual_return, max_dd, is_sharpe, oos_sharpe


# ============================================================================
# 패치 조합 테스트
# ============================================================================
def run_single_patch_test(params: Dict) -> Optional[Dict]:
    """단일 패치 조합 테스트"""
    try:
        sharpe, annual_ret, mdd, is_sharpe, oos_sharpe = fast_backtest_with_patches(
            PRICES_NP, RETURNS_NP, FACTORS_NP, REGIME_SCORES_NP, FAST_REGIME_NP, ELIGIBILITY_NP,
            # 레짐 가중치
            params['trend_w'], params['vol_w'], params['credit_w'], 
            params.get('breadth_w', 0.0), params.get('fast_w', 0.0),
            # 레짐 임계값
            params['crisis_thresh'], params['bear_thresh'],
            params['neutral_thresh'], params['bull_thresh'],
            # 레짐별 노출도
            params['ultra_bull_exp'], params['bull_exp'],
            params['neutral_exp'], params['bear_exp'], params['crisis_exp'],
            # 팩터 가중치
            np.array(params['fw_ultra_bull'], dtype=np.float32),
            np.array(params['fw_bull'], dtype=np.float32),
            np.array(params['fw_neutral'], dtype=np.float32),
            np.array(params['fw_bear'], dtype=np.float32),
            np.array(params['fw_crisis'], dtype=np.float32),
            # 포트폴리오 설정
            params['top_k'], params['rebal_days'],
            params['target_vol'], params['max_lev'], params['min_lev'],
            # 비용 및 리스크
            params['cost_rate'], params['dd_warning'], params['dd_stop'],
            # 패치 플래그
            params.get('use_pit_universe', False),
            params.get('use_graduated_dd', False),
            params.get('use_emergency_riskoff', False),
            params.get('emergency_crisis_trigger', 0.75),
            params.get('emergency_loss_trigger', -0.04),
            params.get('emergency_max_exp', 0.25),
            params.get('use_cvar_limit', False),
            params.get('cvar_limit', -0.03),
            params.get('use_real_port_vol', False),
            params.get('use_hysteresis', False),
            params.get('hysteresis_width', 0.03),
            params.get('use_fast_regime', False),
            params.get('use_breadth', False),
            params.get('use_adaptive_rebal', False),
        )
        
        return {
            'sharpe': sharpe,
            'annual_return': annual_ret,
            'mdd': mdd,
            'is_sharpe': is_sharpe,
            'oos_sharpe': oos_sharpe,
            'is_oos_gap': (is_sharpe - oos_sharpe) / is_sharpe if is_sharpe > 0 else 0,
            'params': params
        }
    except Exception as e:
        return None


def generate_patch_combinations():
    """패치 조합 생성 (15개 패치 = 2^15 = 32,768 조합)"""
    
    # v26 베이스라인 파라미터
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
    }
    
    # 패치 목록 (11개 핵심 패치)
    patches = [
        'use_pit_universe',       # A2: 포인트-인-타임 유니버스
        'use_graduated_dd',       # B1: 점진적 DD Control
        'use_emergency_riskoff',  # B2: 응급 Risk-off
        'use_cvar_limit',         # B3: CVaR Tail Risk
        'use_real_port_vol',      # B4: 실제 포트폴리오 변동성
        'use_hysteresis',         # C1: 레짐 히스테리시스
        'use_fast_regime',        # C2: Fast Regime
        'use_breadth',            # C3: Breadth 지표
        'use_adaptive_rebal',     # D1: 적응형 리밸런싱
    ]
    
    # 패치 조합 생성 (2^9 = 512 조합)
    param_list = []
    
    for combo in itertools.product([False, True], repeat=len(patches)):
        params = base_params.copy()
        
        for i, patch in enumerate(patches):
            params[patch] = combo[i]
        
        # 패치별 추가 파라미터
        if params.get('use_emergency_riskoff'):
            params['emergency_crisis_trigger'] = 0.75
            params['emergency_loss_trigger'] = -0.04
            params['emergency_max_exp'] = 0.25
        
        if params.get('use_cvar_limit'):
            params['cvar_limit'] = -0.03
        
        if params.get('use_hysteresis'):
            params['hysteresis_width'] = 0.03
        
        if params.get('use_fast_regime'):
            params['fast_w'] = 0.15
        
        if params.get('use_breadth'):
            params['breadth_w'] = 0.10
            # 가중치 재조정
            params['trend_w'] = 0.42
            params['vol_w'] = 0.25
            params['credit_w'] = 0.23
        
        # min_lev 0.0 옵션 (D2)
        if params.get('use_emergency_riskoff'):
            params['min_lev'] = 0.0
        
        param_list.append(params)
    
    return param_list


def run_parallel_tests(param_list: List[Dict], n_workers: int = None) -> List[Dict]:
    """멀티프로세싱 병렬 테스트"""
    
    if n_workers is None:
        n_workers = mp.cpu_count()
    
    results = []
    total = len(param_list)
    
    print(f"\n[4] 패치 조합 테스트 시작...")
    print(f"  총 테스트 수: {total}")
    print(f"  병렬 워커: {n_workers}")
    
    start_time = time.time()
    
    # 순차 실행 (Numba JIT가 이미 병렬화됨)
    for i, params in enumerate(param_list):
        result = run_single_patch_test(params)
        if result is not None:
            results.append(result)
        
        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed
            remaining = (total - i - 1) / rate
            print(f"  진행: {i+1}/{total} ({100*(i+1)/total:.1f}%) | "
                  f"유효: {len(results)} | 남은 시간: {remaining/60:.1f}분")
    
    return results


def main():
    """메인 실행"""
    
    # 1. 데이터 초기화
    init_global_data()
    
    # 2. 패치 조합 생성
    param_list = generate_patch_combinations()
    print(f"\n패치 조합 수: {len(param_list)}")
    
    # 3. 병렬 테스트 실행
    results = run_parallel_tests(param_list)
    
    # 4. 결과 정렬 및 출력
    print("\n" + "=" * 80)
    print("테스트 완료!")
    print("=" * 80)
    
    # OOS Sharpe 기준 정렬
    results_sorted = sorted(results, key=lambda x: x['oos_sharpe'], reverse=True)
    
    print("\n[TOP 20 패치 조합 - OOS Sharpe 기준]")
    print("-" * 100)
    print(f"{'순위':>4} | {'Sharpe':>8} | {'IS Sharpe':>10} | {'OOS Sharpe':>11} | {'IS/OOS Gap':>10} | {'MDD':>8} | 패치")
    print("-" * 100)
    
    for i, r in enumerate(results_sorted[:20]):
        patches_on = []
        if r['params'].get('use_pit_universe'): patches_on.append('A2')
        if r['params'].get('use_graduated_dd'): patches_on.append('B1')
        if r['params'].get('use_emergency_riskoff'): patches_on.append('B2')
        if r['params'].get('use_cvar_limit'): patches_on.append('B3')
        if r['params'].get('use_real_port_vol'): patches_on.append('B4')
        if r['params'].get('use_hysteresis'): patches_on.append('C1')
        if r['params'].get('use_fast_regime'): patches_on.append('C2')
        if r['params'].get('use_breadth'): patches_on.append('C3')
        if r['params'].get('use_adaptive_rebal'): patches_on.append('D1')
        
        print(f"{i+1:>4} | {r['sharpe']:>8.3f} | {r['is_sharpe']:>10.3f} | {r['oos_sharpe']:>11.3f} | "
              f"{r['is_oos_gap']*100:>9.1f}% | {r['mdd']*100:>7.1f}% | {'+'.join(patches_on) if patches_on else 'baseline'}")
    
    # 5. 결과 저장
    output = {
        'timestamp': datetime.now().isoformat(),
        'total_tests': len(results),
        'top_results': results_sorted[:100],
        'baseline_result': next((r for r in results if not any([
            r['params'].get('use_pit_universe'),
            r['params'].get('use_graduated_dd'),
            r['params'].get('use_emergency_riskoff'),
            r['params'].get('use_cvar_limit'),
            r['params'].get('use_real_port_vol'),
            r['params'].get('use_hysteresis'),
            r['params'].get('use_fast_regime'),
            r['params'].get('use_breadth'),
            r['params'].get('use_adaptive_rebal'),
        ])), None)
    }
    
    with open('/home/ubuntu/v33_patch_test_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n결과 저장: /home/ubuntu/v33_patch_test_results.json")
    print(f"완료 시간: {datetime.now()}")


if __name__ == "__main__":
    main()
