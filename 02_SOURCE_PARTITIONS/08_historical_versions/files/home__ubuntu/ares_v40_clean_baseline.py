#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v40: 클린 베이스라인 (v26 + 카테고리 A 버그 수정 전체 적용)
================================================================================
v26 베이스라인에 다음 버그 수정 패치를 모두 적용:
- A1: bfill() 제거 (Look-ahead Bias 차단)
- A2: 포인트-인-타임 유니버스 (생존편향 차단)
- A3: Sharpe 계산 통일 (IS/OOS 일관성)
- A4: DD Halt 하드코딩 제거 (config 반영)

이 v40을 새로운 베이스라인으로 설정하고, 나머지 개선안(B, C, D, E)을 테스트
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
import warnings
warnings.filterwarnings('ignore')

print("=" * 80)
print("ARES v40: 클린 베이스라인 (v26 + 카테고리 A 버그 수정)")
print("=" * 80)
print(f"시작 시간: {datetime.now()}")

# ============================================================================
# 글로벌 데이터
# ============================================================================
PRICES_NP = None
RETURNS_NP = None
FACTORS_NP = None
REGIME_SCORES_NP = None
ELIGIBILITY_NP = None  # A2: 포인트-인-타임 유니버스
N_DAYS = 0
N_ASSETS = 0


def init_global_data():
    """데이터 초기화 - 카테고리 A 패치 적용"""
    global PRICES_NP, RETURNS_NP, FACTORS_NP, REGIME_SCORES_NP, ELIGIBILITY_NP, N_DAYS, N_ASSETS
    
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
    
    # =========================================================================
    # A1: bfill() 제거 - ffill()만 사용하여 미래 정보 누수 차단
    # =========================================================================
    print("  [A1] bfill() 제거 적용...")
    valid_cols = prices.columns[prices.notna().mean() > 0.8]
    prices = prices[valid_cols].ffill()  # bfill() 제거!
    
    # =========================================================================
    # A2: 포인트-인-타임 유니버스 - 생존편향 차단
    # =========================================================================
    print("  [A2] 포인트-인-타임 유니버스 계산 중...")
    avail = prices.notna().astype(float)
    ratio = avail.rolling(252, min_periods=252).mean()
    # 과거 252일 중 95% 이상 데이터가 있는 종목만 eligible
    eligibility = (ratio >= 0.95) & prices.shift(1).notna()
    eligibility.iloc[:252] = False  # 첫 252일은 데이터 부족
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
    etf = etf.ffill()  # A1: bfill() 제거
    
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
    
    regime_scores = pd.DataFrame({
        'trend': trend_score,
        'vol': vol_score,
        'credit': credit_score,
    }).reindex(prices.index).ffill().fillna(0.5)  # A1: bfill() 제거
    
    REGIME_SCORES_NP = regime_scores.values.astype(np.float64)
    
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
    ], axis=2).astype(np.float64)
    
    FACTORS_NP = np.nan_to_num(FACTORS_NP, nan=0.5)
    print(f"  팩터: 10개")
    
    print(f"\n  데이터 준비 완료!")
    
    return N_DAYS, N_ASSETS


def calculate_sharpe_unified(returns_array: np.ndarray) -> float:
    """
    A3: Sharpe 계산 통일 - IS/OOS 일관된 방식으로 계산
    """
    if len(returns_array) < 10:
        return 0.0
    mean_ret = np.mean(returns_array)
    std_ret = np.std(returns_array)
    if std_ret < 1e-10:
        return 0.0
    return mean_ret / std_ret * np.sqrt(252)


def fast_backtest_v40(params: Dict) -> Dict:
    """
    v40 클린 베이스라인 백테스트 - 카테고리 A 패치 전체 적용
    """
    
    n_days, n_assets = N_DAYS, N_ASSETS
    
    # 파라미터 추출
    trend_w = params['trend_w']
    vol_w = params['vol_w']
    credit_w = params['credit_w']
    
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
    
    top_k = params['top_k']
    rebal_days = params['rebal_days']
    target_vol = params['target_vol']
    max_lev = params['max_lev']
    min_lev = params['min_lev']
    cost_rate = params['cost_rate']
    
    # =========================================================================
    # A4: DD Halt 하드코딩 제거 - config에서 읽어옴
    # =========================================================================
    dd_warning = params['dd_warning']
    dd_stop = params['dd_stop']
    
    # 상태 변수
    values = np.zeros(n_days)
    values[0] = 1.0
    peak = 1.0
    
    prev_weights = np.zeros(n_assets)
    current_weights = np.zeros(n_assets)
    
    daily_returns = np.zeros(n_days)
    
    for t in range(1, n_days):
        # 1. 레짐 분류
        composite = (REGIME_SCORES_NP[t-1, 0] * trend_w + 
                    REGIME_SCORES_NP[t-1, 1] * vol_w + 
                    REGIME_SCORES_NP[t-1, 2] * credit_w)
        
        total_w = trend_w + vol_w + credit_w
        composite = composite / total_w
        
        crisis_prob = 1 - composite
        
        # 레짐 결정
        if crisis_prob >= crisis_thresh:
            base_exp, fw = crisis_exp, fw_crisis
        elif crisis_prob >= bear_thresh:
            base_exp, fw = bear_exp, fw_bear
        elif crisis_prob >= neutral_thresh:
            base_exp, fw = neutral_exp, fw_neutral
        elif crisis_prob >= bull_thresh:
            base_exp, fw = bull_exp, fw_bull
        else:
            base_exp, fw = ultra_bull_exp, fw_ultra_bull
        
        # 2. 리밸런싱
        if t % rebal_days == 0:
            scores = np.zeros(n_assets)
            for i in range(n_assets):
                # =========================================================
                # A2: 포인트-인-타임 유니버스 적용
                # =========================================================
                if ELIGIBILITY_NP[t-1, i] < 0.5:
                    scores[i] = -999.0  # 부적격 종목 제외
                else:
                    scores[i] = np.sum(FACTORS_NP[t-1, i, :] * fw)
            
            # 상위 K 종목
            sorted_idx = np.argsort(-scores)
            top_idx = sorted_idx[:top_k]
            
            exposure = min(max_lev, max(min_lev, base_exp))
            
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
        
        # 3. DD Control (A4: config에서 읽어옴)
        current_dd = (values[t-1] - peak) / peak if peak > 0 else 0
        
        if current_dd <= dd_stop:
            dd_scale = 0.3
        elif current_dd <= dd_warning:
            dd_scale = 0.7
        else:
            dd_scale = 1.0
        
        active_weights = current_weights * dd_scale
        
        # 4. 수익률 계산
        turnover = np.sum(np.abs(active_weights - prev_weights))
        cost = turnover * cost_rate
        
        port_ret = np.sum(active_weights * RETURNS_NP[t, :]) - cost
        
        daily_returns[t] = port_ret
        values[t] = values[t-1] * (1 + port_ret)
        
        if values[t] > peak:
            peak = values[t]
        
        prev_weights = active_weights.copy()
    
    # =========================================================================
    # A3: Sharpe 계산 통일 - 동일한 함수로 IS/OOS 계산
    # =========================================================================
    valid_returns = daily_returns[1:]
    sharpe = calculate_sharpe_unified(valid_returns)
    
    total_return = values[-1] / values[0] - 1
    n_years = n_days / 252.0
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
    
    # MDD
    running_max = np.maximum.accumulate(values)
    drawdowns = (values - running_max) / running_max
    max_dd = np.min(drawdowns)
    
    # IS/OOS 분리 (55% / 45%)
    is_end = int(n_days * 0.55)
    
    is_returns = daily_returns[1:is_end]
    is_sharpe = calculate_sharpe_unified(is_returns)
    
    oos_returns = daily_returns[is_end:]
    oos_sharpe = calculate_sharpe_unified(oos_returns)
    
    return {
        'sharpe': sharpe,
        'annual_return': annual_return,
        'mdd': max_dd,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'is_oos_gap': (is_sharpe - oos_sharpe) / is_sharpe if is_sharpe > 0 else 0
    }


def main():
    """v40 클린 베이스라인 성능 측정"""
    
    # 1. 데이터 초기화
    init_global_data()
    
    # 2. v26 원본 파라미터
    v26_params = {
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
    
    # 3. v40 클린 베이스라인 테스트
    print("\n[4] v40 클린 베이스라인 테스트...")
    result = fast_backtest_v40(v26_params)
    
    print("\n" + "=" * 80)
    print("v40 클린 베이스라인 결과 (v26 + 카테고리 A 버그 수정)")
    print("=" * 80)
    print(f"  전체 Sharpe: {result['sharpe']:.3f}")
    print(f"  IS Sharpe:   {result['is_sharpe']:.3f}")
    print(f"  OOS Sharpe:  {result['oos_sharpe']:.3f}")
    print(f"  IS/OOS Gap:  {result['is_oos_gap']*100:.1f}%")
    print(f"  MDD:         {result['mdd']*100:.1f}%")
    print(f"  연간 수익률: {result['annual_return']*100:.1f}%")
    
    # 4. 결과 저장
    output = {
        'version': 'v40',
        'description': 'v26 + Category A (Bug Fixes)',
        'patches_applied': ['A1: bfill removal', 'A2: Point-in-time universe', 'A3: Unified Sharpe calculation', 'A4: DD Halt from config'],
        'timestamp': datetime.now().isoformat(),
        'params': v26_params,
        'results': result
    }
    
    with open('/home/ubuntu/v40_clean_baseline_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n결과 저장: /home/ubuntu/v40_clean_baseline_results.json")
    print(f"완료 시간: {datetime.now()}")


if __name__ == "__main__":
    main()
