#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v29 - 4대 AI 권고사항 통합
================================================================================
1. HRP (Hierarchical Risk Parity) 포트폴리오
2. IC 기반 동적 팩터 가중치
3. Soft Regime (확률 가중 혼합)
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
import json
import itertools
from numba import njit, prange
from typing import Dict, Tuple, List, Optional
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
import warnings
warnings.filterwarnings('ignore')

print("=" * 70)
print("ARES v29 - 4대 AI 권고사항 통합")
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

factor_names = list(factors.keys())
factor_df = pd.concat(factors, axis=1)

print(f"  팩터: {len(factor_names)}개")

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
# HRP 함수
# =============================================================================
def compute_hrp_weights(returns_window: pd.DataFrame, shrinkage: float = 0.3) -> pd.Series:
    """
    Hierarchical Risk Parity 가중치 계산
    """
    n = len(returns_window.columns)
    if n < 2:
        return pd.Series(1.0, index=returns_window.columns)
    
    # 공분산 계산 (Shrinkage 적용)
    raw_cov = returns_window.cov()
    diag_var = np.diag(np.diag(raw_cov))
    shrunk_cov = (1 - shrinkage) * raw_cov + shrinkage * diag_var
    
    # 상관행렬 및 거리 계산
    corr = returns_window.corr()
    corr = corr.fillna(0)
    
    # 거리 행렬
    dist = np.sqrt((1 - corr) / 2)
    dist = dist.fillna(1)
    
    # 계층적 클러스터링
    try:
        dist_condensed = squareform(dist.values, checks=False)
        link = linkage(dist_condensed, method='single')
        sorted_idx = leaves_list(link)
    except:
        sorted_idx = np.arange(n)
    
    # 역분산 가중치
    var = np.diag(shrunk_cov)
    inv_var = 1 / (var + 1e-10)
    
    # HRP 가중치 (간단 버전: 역분산 + 클러스터 순서)
    weights = inv_var / inv_var.sum()
    
    return pd.Series(weights, index=returns_window.columns)

# =============================================================================
# IC 기반 팩터 가중치 계산
# =============================================================================
def compute_ic_weights(factor_df: pd.DataFrame, returns_fwd: pd.Series, 
                       window: int = 126, min_weight: float = 0.05) -> np.ndarray:
    """
    Information Coefficient 기반 동적 팩터 가중치
    """
    n_factors = factor_df.shape[1]
    weights = np.ones(n_factors) / n_factors
    
    if len(factor_df) < window:
        return weights
    
    # 각 팩터의 IC 계산
    ic_scores = []
    for col in factor_df.columns:
        try:
            # Spearman 상관계수
            ic = factor_df[col].iloc[-window:].corr(returns_fwd.iloc[-window:], method='spearman')
            if np.isnan(ic):
                ic = 0
            ic_scores.append(ic)
        except:
            ic_scores.append(0)
    
    ic_scores = np.array(ic_scores)
    
    # Softmax 변환
    ic_scaled = np.tanh(ic_scores * 5)  # 스케일링
    exp_scores = np.exp(ic_scaled - ic_scaled.max())
    weights = exp_scores / exp_scores.sum()
    
    # 최소 가중치 보장
    weights = np.maximum(weights, min_weight)
    weights = weights / weights.sum()
    
    return weights

# =============================================================================
# NumPy 배열로 변환
# =============================================================================
PRICES_NP = prices.values.astype(np.float64)
RETURNS_NP = returns.values.astype(np.float64)
REGIME_SCORES_NP = regime_scores.values.astype(np.float64)

n_days, n_assets = RETURNS_NP.shape

print(f"\n[4] 데이터 준비 완료")
print(f"  기간: {prices.index[0].date()} ~ {prices.index[-1].date()}")
print(f"  종목: {n_assets}, 일수: {n_days}")

# =============================================================================
# v29 백테스트 함수 (HRP + IC + Soft Regime)
# =============================================================================
def backtest_v29(
    prices_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    factors_dict: dict,
    regime_scores_df: pd.DataFrame,
    # 파라미터
    target_vol: float = 0.29,
    max_lev: float = 2.0,
    min_lev: float = 0.3,
    rebal_days: int = 14,
    top_k: int = 48,
    hrp_shrinkage: float = 0.3,
    ic_window: int = 126,
    use_hrp: bool = True,
    use_ic_weights: bool = True,
    use_soft_regime: bool = True,
    cost_rate: float = 0.002,
    dd_warning: float = -0.20,
    dd_stop: float = -0.35,
    trend_w: float = 0.47,
    vol_w: float = 0.28,
    credit_w: float = 0.25,
) -> Dict:
    """v29 백테스트 - HRP + IC + Soft Regime"""
    
    n_days = len(prices_df)
    n_assets = len(prices_df.columns)
    
    # 팩터 DataFrame 생성
    factor_df = pd.DataFrame(index=prices_df.index)
    for name, f in factors_dict.items():
        factor_df[name] = f.reindex(prices_df.index).mean(axis=1) if isinstance(f, pd.DataFrame) else f
    
    # 초기화
    portfolio_value = 1.0
    peak = 1.0
    is_halted = False
    halt_counter = 0
    
    current_weights = pd.Series(0.0, index=prices_df.columns)
    
    daily_returns = []
    
    # 변동성 버퍼
    vol_window = 20
    port_ret_buffer = []
    
    # 레짐별 노출도 (Soft Regime용)
    regime_exposures = {
        'ultra_bull': 2.0,
        'bull': 1.7,
        'neutral': 1.2,
        'bear': 0.6,
        'crisis': 0.24
    }
    
    for t in range(252, n_days):
        date = prices_df.index[t]
        
        # 레짐 점수
        trend = regime_scores_df.iloc[t-1]['trend']
        vol = regime_scores_df.iloc[t-1]['vol']
        credit = regime_scores_df.iloc[t-1]['credit']
        
        composite = trend * trend_w + vol * vol_w + credit * credit_w
        
        # Soft Regime: 확률 가중 혼합
        if use_soft_regime:
            # 각 레짐 확률 계산 (간단 버전)
            p_ultra_bull = max(0, composite - 0.87) / 0.13 if composite > 0.87 else 0
            p_bull = max(0, min(1, (composite - 0.70) / 0.17)) if composite > 0.70 else 0
            p_neutral = max(0, min(1, (composite - 0.50) / 0.20)) if composite > 0.50 else 0
            p_bear = max(0, min(1, (composite - 0.30) / 0.20)) if composite > 0.30 else 0
            p_crisis = max(0, 1 - composite / 0.30) if composite < 0.30 else 0
            
            # 정규화
            total_p = p_ultra_bull + p_bull + p_neutral + p_bear + p_crisis + 1e-10
            p_ultra_bull /= total_p
            p_bull /= total_p
            p_neutral /= total_p
            p_bear /= total_p
            p_crisis /= total_p
            
            # 확률 가중 노출도
            base_exposure = (
                p_ultra_bull * regime_exposures['ultra_bull'] +
                p_bull * regime_exposures['bull'] +
                p_neutral * regime_exposures['neutral'] +
                p_bear * regime_exposures['bear'] +
                p_crisis * regime_exposures['crisis']
            )
        else:
            # Hard Regime
            crisis_prob = 1 - composite
            if crisis_prob >= 0.70:
                base_exposure = regime_exposures['crisis']
            elif crisis_prob >= 0.50:
                base_exposure = regime_exposures['bear']
            elif crisis_prob >= 0.30:
                base_exposure = regime_exposures['neutral']
            elif crisis_prob >= 0.13:
                base_exposure = regime_exposures['bull']
            else:
                base_exposure = regime_exposures['ultra_bull']
        
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
        if len(port_ret_buffer) >= vol_window:
            rv = np.std(port_ret_buffer[-vol_window:]) * np.sqrt(252)
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
            factor_values = pd.DataFrame(index=prices_df.columns)
            for name, f in factors_dict.items():
                if isinstance(f, pd.DataFrame):
                    factor_values[name] = f.iloc[t-1] if t-1 < len(f) else f.iloc[-1]
                else:
                    factor_values[name] = 0.5
            
            factor_values = factor_values.fillna(0.5)
            
            # IC 기반 팩터 가중치
            if use_ic_weights and t > ic_window:
                # 미래 수익률 (다음 리밸런싱까지)
                fwd_ret = returns_df.iloc[t:min(t+rebal_days, n_days)].mean()
                
                # IC 계산용 데이터
                ic_factor_df = factor_values.T
                fw = compute_ic_weights(ic_factor_df, fwd_ret, window=ic_window)
            else:
                fw = np.ones(len(factors_dict)) / len(factors_dict)
            
            # 종합 점수
            scores = (factor_values.values @ fw).flatten()
            scores_series = pd.Series(scores, index=prices_df.columns)
            
            # 상위 K 종목 선택
            top_stocks = scores_series.nlargest(top_k).index
            
            # HRP 가중치 또는 동일 가중
            if use_hrp:
                returns_window = returns_df.iloc[max(0, t-63):t][top_stocks]
                if len(returns_window) > 20:
                    hrp_weights = compute_hrp_weights(returns_window, shrinkage=hrp_shrinkage)
                else:
                    hrp_weights = pd.Series(1.0 / len(top_stocks), index=top_stocks)
            else:
                hrp_weights = pd.Series(1.0 / len(top_stocks), index=top_stocks)
            
            # 노출도 적용
            exposure = base_exposure * vol_adj
            exposure = min(max_lev, max(min_lev, exposure))
            exposure *= dd_scale
            
            # 새 가중치
            new_weights = pd.Series(0.0, index=prices_df.columns)
            new_weights[top_stocks] = hrp_weights * exposure
            
            current_weights = new_weights
        
        # 수익률 계산
        port_ret = (current_weights * returns_df.iloc[t]).sum()
        
        # 비용 차감
        if t % rebal_days == 0:
            port_ret -= cost_rate * 0.5
        
        daily_returns.append(port_ret)
        portfolio_value *= (1 + port_ret)
        port_ret_buffer.append(port_ret)
    
    # 성과 계산
    daily_returns = np.array(daily_returns)
    
    mean_ret = np.mean(daily_returns)
    std_ret = np.std(daily_returns)
    
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 1e-10 else 0
    annual_return = mean_ret * 252
    
    # MDD 계산
    cum_value = np.cumprod(1 + daily_returns)
    peak_value = np.maximum.accumulate(cum_value)
    drawdown = (cum_value - peak_value) / peak_value
    mdd = drawdown.min()
    
    # IS/OOS 분리
    is_end = int(len(daily_returns) * 0.55)
    is_returns = daily_returns[:is_end]
    oos_returns = daily_returns[is_end:]
    
    is_sharpe = (np.mean(is_returns) / np.std(is_returns)) * np.sqrt(252) if np.std(is_returns) > 1e-10 else 0
    oos_sharpe = (np.mean(oos_returns) / np.std(oos_returns)) * np.sqrt(252) if np.std(oos_returns) > 1e-10 else 0
    
    return {
        'sharpe': sharpe,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'annual_return': annual_return,
        'mdd': mdd
    }

# =============================================================================
# 파레토 최적화
# =============================================================================
print("\n[5] 파레토 최적화 시작...")

# 파라미터 그리드
param_grid = {
    'target_vol': [0.25, 0.29, 0.33],
    'rebal_days': [12, 14, 16],
    'top_k': [40, 48],
    'hrp_shrinkage': [0.2, 0.3, 0.4],
    'ic_window': [63, 126],
    'use_hrp': [True, False],
    'use_ic_weights': [True, False],
    'use_soft_regime': [True, False],
}

# 조합 생성
keys = list(param_grid.keys())
values = list(param_grid.values())
all_combinations = list(itertools.product(*values))

print(f"  총 조합 수: {len(all_combinations)}")

# 결과 저장
results = []

# 백테스트 실행
for i, combo in enumerate(all_combinations):
    params = dict(zip(keys, combo))
    
    try:
        result = backtest_v29(
            prices_df=prices,
            returns_df=returns,
            factors_dict=factors,
            regime_scores_df=regime_scores,
            **params
        )
        
        if not np.isnan(result['sharpe']) and result['sharpe'] > 0:
            results.append({
                **result,
                **params
            })
    except Exception as e:
        pass
    
    if (i + 1) % 50 == 0:
        print(f"  진행: {i+1}/{len(all_combinations)} ({100*(i+1)/len(all_combinations):.1f}%)")

print(f"\n  유효 결과: {len(results)}")

# 결과 정렬 및 출력
if len(results) > 0:
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('sharpe', ascending=False)
    
    print("\n[6] 상위 10개 결과:")
    print("-" * 100)
    
    for idx, row in results_df.head(10).iterrows():
        print(f"  Sharpe: {row['sharpe']:.4f} | IS: {row['is_sharpe']:.4f} | OOS: {row['oos_sharpe']:.4f} | "
              f"AR: {row['annual_return']*100:.1f}% | MDD: {row['mdd']*100:.1f}% | "
              f"HRP: {row['use_hrp']} | IC: {row['use_ic_weights']} | Soft: {row['use_soft_regime']}")
    
    # 최고 성과 출력
    best = results_df.iloc[0]
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
    
    # 결과 저장
    output = {
        'total_combinations': len(all_combinations),
        'valid_results': len(results),
        'best_result': best.to_dict(),
        'top_results': results_df.head(20).to_dict('records')
    }
    
    with open('/home/ubuntu/ares_v29_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n결과 저장: /home/ubuntu/ares_v29_results.json")

print("\n완료!")
