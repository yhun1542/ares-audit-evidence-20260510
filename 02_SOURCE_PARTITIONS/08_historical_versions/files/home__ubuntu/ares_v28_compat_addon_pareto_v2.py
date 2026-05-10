#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v28 v2 - Compat Add-on 통합 파레토 최적화 (수정)
================================================================================
v26 베이스라인 + 레짐별 rVol 정책 + 완화된 no-trade/스텝 캡
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
import json
import itertools
from numba import njit, prange
from typing import Dict, Tuple, List, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')

print("=" * 70)
print("ARES v28 v2 - Compat Add-on 통합 파레토 최적화 (수정)")
print("=" * 70)

# =============================================================================
# 데이터 로드
# =============================================================================
print("\n[1] 데이터 로드 중...")

# 주식 데이터
conn = sqlite3.connect("/home/ubuntu/ares_x_unified_database/ares_universal_v2.db", timeout=30)
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
valid_cols = prices.columns[prices.notna().mean() > 0.8]
prices = prices[valid_cols].ffill().bfill()
returns = prices.pct_change()
returns.iloc[0] = 0

# ETF 데이터 (레짐 분류용)
conn = sqlite3.connect("/home/ubuntu/etf_data_s3.db", timeout=30)
etf_query = """
SELECT date, symbol, close FROM daily_ohlcv
WHERE symbol IN ('QQQ', 'DIA', 'HYG', 'LQD')
AND date >= '2015-01-01' ORDER BY date, symbol
"""
etf_df = pd.read_sql_query(etf_query, conn)
conn.close()

etf = etf_df.pivot(index='date', columns='symbol', values='close')
etf.index = pd.to_datetime(etf.index)
etf = etf.ffill().bfill()

print(f"  주식: {len(prices.columns)}종목, {len(prices)}일")
print(f"  ETF: {list(etf.columns)}")

# =============================================================================
# 팩터 계산
# =============================================================================
print("\n[2] 팩터 계산 중...")

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

factor_array = np.stack([
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
factor_array = np.nan_to_num(factor_array, nan=0.5)

print(f"  팩터: {factor_array.shape}")

# =============================================================================
# 레짐 점수 계산
# =============================================================================
print("\n[3] 레짐 점수 계산 중...")

common_dates = prices.index.intersection(etf.index)
mkt = etf['QQQ'].reindex(common_dates) if 'QQQ' in etf.columns else etf['DIA'].reindex(common_dates)
mkt_ret = mkt.pct_change()
sma_50 = mkt.rolling(50).mean()
sma_200 = mkt.rolling(200).mean()

trend_score = pd.Series(0.5, index=common_dates)
trend_score[mkt > sma_200] += 0.20
trend_score[sma_50 > sma_200] += 0.15
trend_score[mkt > sma_50] += 0.10
trend_score[mkt > mkt.rolling(20).mean()] += 0.05
trend_score[mkt < sma_200] -= 0.20
trend_score[(mkt < sma_200) & (sma_50 < sma_200)] -= 0.15
trend_score[mkt < mkt.rolling(20).mean()] -= 0.05
trend_score = trend_score.clip(0, 1)

realized_vol = mkt_ret.rolling(20).std() * np.sqrt(252)
hist_vol = mkt_ret.rolling(252).std() * np.sqrt(252)
vol_ratio = realized_vol / hist_vol
vol_score = 1 - vol_ratio.clip(0.5, 2.0) / 2.0
vol_score = vol_score.clip(0, 1)

if 'HYG' in etf.columns and 'LQD' in etf.columns:
    hyg = etf['HYG'].reindex(common_dates)
    lqd = etf['LQD'].reindex(common_dates)
    credit_ratio = hyg / lqd
    credit_ma = credit_ratio.rolling(60).mean()
    credit_std = credit_ratio.rolling(252).std()
    credit_zscore = (credit_ratio - credit_ma) / credit_std
    credit_score = 0.5 + credit_zscore.clip(-2, 2) / 4
    credit_score = credit_score.clip(0, 1)
else:
    credit_score = pd.Series(0.5, index=common_dates)

regime_scores = pd.DataFrame({
    'trend': trend_score,
    'vol': vol_score,
    'credit': credit_score
}).reindex(prices.index).ffill().bfill()

print(f"  레짐 점수: {regime_scores.shape}")

# =============================================================================
# NumPy 배열로 변환
# =============================================================================
PRICES_NP = prices.values.astype(np.float64)
RETURNS_NP = returns.values.astype(np.float64)
FACTORS_NP = factor_array
REGIME_SCORES_NP = regime_scores.values.astype(np.float64)

n_days, n_assets = RETURNS_NP.shape
n_factors = FACTORS_NP.shape[2]

print(f"\n[4] 데이터 준비 완료")
print(f"  기간: {prices.index[0].date()} ~ {prices.index[-1].date()}")
print(f"  종목: {n_assets}, 일수: {n_days}, 팩터: {n_factors}")

# =============================================================================
# Numba 최적화 백테스트 함수 (v26 기반 + 레짐별 rVol)
# =============================================================================
@njit(cache=True)
def fast_backtest_v28_v2(
    prices: np.ndarray,
    returns: np.ndarray,
    factors: np.ndarray,
    regime_scores: np.ndarray,
    # 레짐별 rVol 정책
    rvol_ultra_bull: float,
    rvol_bull: float,
    rvol_neutral: float,
    rvol_bear: float,
    rvol_crisis: float,
    # 레짐별 노출도
    exp_ultra_bull: float,
    exp_bull: float,
    exp_neutral: float,
    exp_bear: float,
    exp_crisis: float,
    # 리밸런싱
    rebal_days: int,
    top_k: int,
    # 리스크 관리
    max_lev: float,
    min_lev: float,
    # 비용
    cost_rate: float,
    # 레짐 임계값
    crisis_thresh: float,
    bear_thresh: float,
    neutral_thresh: float,
    bull_thresh: float,
    # 지표 가중치
    trend_w: float,
    vol_w: float,
    credit_w: float,
    # DD Control
    dd_warning: float,
    dd_stop: float,
    # 팩터 가중치
    fw: np.ndarray
) -> Tuple[float, float, float, float, float]:
    """v28 v2 백테스트 - v26 기반 + 레짐별 rVol"""
    
    n_days, n_assets = returns.shape
    
    # 초기화
    portfolio_value = 1.0
    peak = 1.0
    is_halted = False
    halt_counter = 0
    
    current_weights = np.zeros(n_assets)
    
    daily_returns = np.zeros(n_days)
    
    # 변동성 버퍼
    vol_window = 20
    port_ret_buffer = np.zeros(vol_window)
    buffer_idx = 0
    buffer_filled = False
    
    for t in range(252, n_days):
        # 레짐 분류
        composite = (
            regime_scores[t-1, 0] * trend_w +
            regime_scores[t-1, 1] * vol_w +
            regime_scores[t-1, 2] * credit_w
        )
        crisis_prob = 1 - composite
        
        # 레짐 결정 및 정책 적용
        if crisis_prob >= crisis_thresh:
            regime = 0  # crisis
            base_exposure = exp_crisis
            target_vol = rvol_crisis
        elif crisis_prob >= bear_thresh:
            regime = 1  # bear
            base_exposure = exp_bear
            target_vol = rvol_bear
        elif crisis_prob >= neutral_thresh:
            regime = 2  # neutral
            base_exposure = exp_neutral
            target_vol = rvol_neutral
        elif crisis_prob >= bull_thresh:
            regime = 3  # bull
            base_exposure = exp_bull
            target_vol = rvol_bull
        else:
            regime = 4  # ultra_bull
            base_exposure = exp_ultra_bull
            target_vol = rvol_ultra_bull
        
        # DD Control
        if portfolio_value > peak:
            peak = portfolio_value
        
        dd = (portfolio_value - peak) / peak if peak > 0 else 0
        
        if dd <= dd_stop:
            is_halted = True
            halt_counter = 10
        
        if is_halted:
            halt_counter -= 1
            if halt_counter <= 0:
                is_halted = False
        
        if is_halted:
            dd_scale = 0.3
        elif dd <= dd_warning:
            dd_scale = 0.7
        else:
            dd_scale = 1.0
        
        # 변동성 타겟 조정
        if buffer_filled:
            port_ret_sum = 0.0
            port_ret_sq_sum = 0.0
            for i in range(vol_window):
                port_ret_sum += port_ret_buffer[i]
                port_ret_sq_sum += port_ret_buffer[i] ** 2
            
            rv = np.sqrt(max(0, port_ret_sq_sum / vol_window - (port_ret_sum / vol_window) ** 2)) * np.sqrt(252)
            
            if rv > 0.01:
                vol_adj = target_vol / rv
                vol_adj = min(2.0, max(0.5, vol_adj))
            else:
                vol_adj = 1.0
        else:
            vol_adj = 1.0
        
        # 리밸런싱
        if t % rebal_days == 0:
            # 팩터 점수 계산
            scores = np.zeros(n_assets)
            for i in range(n_assets):
                for f in range(len(fw)):
                    scores[i] += factors[t-1, i, f] * fw[f]
            
            # 상위 K 종목 선택
            sorted_idx = np.argsort(-scores)
            
            # 노출도 계산
            exposure = base_exposure * vol_adj
            exposure = min(max_lev, max(min_lev, exposure))
            exposure *= dd_scale
            
            # 새 가중치 계산
            for i in range(n_assets):
                current_weights[i] = 0.0
            
            for i in range(min(top_k, n_assets)):
                idx = sorted_idx[i]
                current_weights[idx] = exposure / top_k
        
        # 수익률 계산
        port_ret = 0.0
        for i in range(n_assets):
            port_ret += current_weights[i] * returns[t, i]
        
        # 비용 차감
        if t % rebal_days == 0:
            port_ret -= cost_rate * 0.5  # 평균 턴오버 가정
        
        daily_returns[t] = port_ret
        portfolio_value *= (1 + port_ret)
        
        # 변동성 버퍼 업데이트
        port_ret_buffer[buffer_idx] = port_ret
        buffer_idx = (buffer_idx + 1) % vol_window
        if buffer_idx == 0:
            buffer_filled = True
    
    # 성과 계산
    valid_returns = daily_returns[252:]
    
    mean_ret = np.mean(valid_returns)
    std_ret = np.std(valid_returns)
    
    if std_ret > 1e-10:
        sharpe = (mean_ret / std_ret) * np.sqrt(252)
    else:
        sharpe = 0.0
    
    annual_return = mean_ret * 252
    
    # MDD 계산
    cum_value = 1.0
    peak_value = 1.0
    max_dd = 0.0
    for r in valid_returns:
        cum_value *= (1 + r)
        if cum_value > peak_value:
            peak_value = cum_value
        dd = (cum_value - peak_value) / peak_value
        if dd < max_dd:
            max_dd = dd
    
    # IS/OOS 분리
    is_end = int(len(valid_returns) * 0.55)
    is_returns = valid_returns[:is_end]
    oos_returns = valid_returns[is_end:]
    
    is_mean = np.mean(is_returns)
    is_std = np.std(is_returns)
    is_sharpe = (is_mean / is_std) * np.sqrt(252) if is_std > 1e-10 else 0.0
    
    oos_mean = np.mean(oos_returns)
    oos_std = np.std(oos_returns)
    oos_sharpe = (oos_mean / oos_std) * np.sqrt(252) if oos_std > 1e-10 else 0.0
    
    return sharpe, is_sharpe, oos_sharpe, annual_return, max_dd

# =============================================================================
# 파레토 최적화
# =============================================================================
print("\n[5] 파레토 최적화 시작...")

# 파라미터 그리드 정의 (레짐별 rVol + 기본 파라미터)
param_grid = {
    # 레짐별 rVol
    'rvol_ultra_bull': [0.26, 0.28, 0.30, 0.32],
    'rvol_bull': [0.24, 0.26, 0.28, 0.30],
    'rvol_neutral': [0.22, 0.24, 0.26, 0.28],
    'rvol_bear': [0.20, 0.22, 0.24],
    'rvol_crisis': [0.18, 0.20, 0.22],
    # 레짐별 노출도
    'exp_ultra_bull': [1.8, 2.0, 2.2],
    'exp_bull': [1.5, 1.7, 1.9],
    # 리밸런싱 주기
    'rebal_days': [12, 14, 16],
    # 레버리지
    'max_lev': [1.8, 2.0],
    # top_k
    'top_k': [40, 45, 48],
}

# 고정 파라미터
fixed_params = {
    'exp_neutral': 1.2,
    'exp_bear': 0.6,
    'exp_crisis': 0.24,
    'min_lev': 0.3,
    'cost_rate': 0.002,
    'crisis_thresh': 0.70,
    'bear_thresh': 0.50,
    'neutral_thresh': 0.30,
    'bull_thresh': 0.13,
    'trend_w': 0.47,
    'vol_w': 0.28,
    'credit_w': 0.25,
    'dd_warning': -0.20,
    'dd_stop': -0.35,
}

# 팩터 가중치
fw = np.array([0.15, 0.15, 0.10, 0.10, 0.10, 0.10, 0.10, 0.08, 0.07, 0.05])

# 조합 생성
keys = list(param_grid.keys())
values = list(param_grid.values())
all_combinations = list(itertools.product(*values))

print(f"  총 조합 수: {len(all_combinations):,}")

# 결과 저장
results = []

# 백테스트 실행
for i, combo in enumerate(all_combinations):
    params = dict(zip(keys, combo))
    
    try:
        sharpe, is_sharpe, oos_sharpe, annual_return, mdd = fast_backtest_v28_v2(
            PRICES_NP, RETURNS_NP, FACTORS_NP, REGIME_SCORES_NP,
            params['rvol_ultra_bull'], params['rvol_bull'], params['rvol_neutral'],
            params['rvol_bear'], params['rvol_crisis'],
            params['exp_ultra_bull'], params['exp_bull'],
            fixed_params['exp_neutral'], fixed_params['exp_bear'], fixed_params['exp_crisis'],
            params['rebal_days'], params['top_k'],
            params['max_lev'], fixed_params['min_lev'],
            fixed_params['cost_rate'],
            fixed_params['crisis_thresh'], fixed_params['bear_thresh'],
            fixed_params['neutral_thresh'], fixed_params['bull_thresh'],
            fixed_params['trend_w'], fixed_params['vol_w'], fixed_params['credit_w'],
            fixed_params['dd_warning'], fixed_params['dd_stop'],
            fw
        )
        
        if not np.isnan(sharpe) and sharpe > 0:
            results.append({
                'sharpe': sharpe,
                'is_sharpe': is_sharpe,
                'oos_sharpe': oos_sharpe,
                'annual_return': annual_return,
                'mdd': mdd,
                **params
            })
    except:
        pass
    
    if (i + 1) % 5000 == 0:
        print(f"  진행: {i+1:,}/{len(all_combinations):,} ({100*(i+1)/len(all_combinations):.1f}%)")

print(f"\n  유효 결과: {len(results):,}")

# 파레토 최적 찾기
if len(results) > 0:
    results_df = pd.DataFrame(results)
    
    # 상위 1000개만 파레토 계산
    top_results = results_df.nlargest(1000, 'sharpe')
    
    pareto_mask = np.ones(len(top_results), dtype=bool)
    
    for i in range(len(top_results)):
        for j in range(len(top_results)):
            if i != j:
                if (top_results.iloc[j]['sharpe'] >= top_results.iloc[i]['sharpe'] and
                    top_results.iloc[j]['mdd'] >= top_results.iloc[i]['mdd'] and
                    (top_results.iloc[j]['sharpe'] > top_results.iloc[i]['sharpe'] or
                     top_results.iloc[j]['mdd'] > top_results.iloc[i]['mdd'])):
                    pareto_mask[i] = False
                    break
    
    pareto_df = top_results[pareto_mask].sort_values('sharpe', ascending=False)
    
    print(f"\n  파레토 최적: {len(pareto_df)}개")
    print("\n[6] 상위 10개 결과:")
    print("-" * 100)
    
    for idx, row in pareto_df.head(10).iterrows():
        print(f"  Sharpe: {row['sharpe']:.4f} | IS: {row['is_sharpe']:.4f} | OOS: {row['oos_sharpe']:.4f} | "
              f"AR: {row['annual_return']*100:.1f}% | MDD: {row['mdd']*100:.1f}%")
    
    # 결과 저장
    output = {
        'total_combinations': len(all_combinations),
        'valid_results': len(results),
        'pareto_optimal': len(pareto_df),
        'best_sharpe': pareto_df.iloc[0].to_dict() if len(pareto_df) > 0 else None,
        'pareto_results': pareto_df.head(50).to_dict('records')
    }
    
    with open('/home/ubuntu/ares_v28_pareto_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n결과 저장: /home/ubuntu/ares_v28_pareto_results.json")
    
    # 최고 성과 출력
    if len(pareto_df) > 0:
        best = pareto_df.iloc[0]
        print("\n" + "=" * 70)
        print("최고 성과 조합")
        print("=" * 70)
        print(f"  Sharpe: {best['sharpe']:.4f}")
        print(f"  IS Sharpe: {best['is_sharpe']:.4f}")
        print(f"  OOS Sharpe: {best['oos_sharpe']:.4f}")
        print(f"  Annual Return: {best['annual_return']*100:.2f}%")
        print(f"  MDD: {best['mdd']*100:.2f}%")
        print("\n파라미터:")
        for key in param_grid.keys():
            print(f"  {key}: {best[key]}")

print("\n완료!")
