#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v26 베이스라인 + IC 기반 팩터 가중치 파레토 최적화
================================================================================
v26 베이스라인 (Sharpe 2.02)을 유지하면서 IC 기반 동적 팩터 가중치만 추가하여 테스트
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
import json
import itertools
from numba import njit
import warnings
warnings.filterwarnings('ignore')

print("=" * 70)
print("ARES v26 베이스라인 + IC 기반 팩터 가중치 파레토 최적화")
print("=" * 70)

# =============================================================================
# 데이터 로드 (v26과 동일)
# =============================================================================
print("\n[1] 데이터 로드 중...")

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

# ETF 데이터
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

# =============================================================================
# 팩터 계산 (v26과 동일)
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

# =============================================================================
# IC (Information Coefficient) 계산
# =============================================================================
print("\n[3] IC 계산 중...")

# 미래 수익률 (다음 21일)
fwd_returns = returns.shift(-21).rolling(21).mean()

# 각 팩터의 IC 계산 (rolling 63일)
ic_window = 63
factor_ics = {}

for fname in factor_names:
    factor_df = factors[fname]
    ic_series = pd.Series(index=factor_df.index, dtype=float)
    
    for i in range(ic_window, len(factor_df) - 21):
        # 과거 ic_window 동안의 평균 IC
        ics = []
        for j in range(i - ic_window, i):
            f_vals = factor_df.iloc[j].values
            r_vals = fwd_returns.iloc[j].values
            
            # NaN 제거
            mask = ~(np.isnan(f_vals) | np.isnan(r_vals))
            if mask.sum() > 10:
                corr = np.corrcoef(f_vals[mask], r_vals[mask])[0, 1]
                if not np.isnan(corr):
                    ics.append(corr)
        
        if len(ics) > 0:
            ic_series.iloc[i] = np.mean(ics)
    
    factor_ics[fname] = ic_series.fillna(0)

# IC를 NumPy 배열로 변환
IC_NP = np.stack([factor_ics[f].values for f in factor_names], axis=1).astype(np.float64)
IC_NP = np.nan_to_num(IC_NP, nan=0.0)

print(f"  IC 계산 완료: {len(factor_names)}개 팩터")

# =============================================================================
# 레짐 점수 계산 (v26과 동일)
# =============================================================================
print("\n[4] 레짐 점수 계산 중...")

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

REGIME_SCORES_NP = regime_scores.values.astype(np.float64)

# =============================================================================
# NumPy 배열 변환
# =============================================================================
FACTORS_NP = np.stack([factors[f].values for f in factor_names], axis=2)
FACTORS_NP = np.nan_to_num(FACTORS_NP, nan=0.5)

PRICES_NP = prices.values.astype(np.float64)
RETURNS_NP = returns.values.astype(np.float64)

n_days, n_assets = RETURNS_NP.shape
n_factors = len(factor_names)

print(f"\n[5] 데이터 준비 완료: {n_days}일, {n_assets}종목, {n_factors}팩터")

# =============================================================================
# v26 + IC 기반 팩터 가중치 백테스트 (Numba JIT)
# =============================================================================
@njit(cache=True)
def fast_backtest_v26_ic(
    prices: np.ndarray,
    returns: np.ndarray,
    factors: np.ndarray,
    regime_scores: np.ndarray,
    ic_scores: np.ndarray,
    # v26 베이스라인 파라미터
    target_vol: float,
    max_lev: float,
    min_lev: float,
    rebal_days: int,
    top_k: int,
    trend_w: float,
    vol_w: float,
    credit_w: float,
    ultra_bull_exp: float,
    bull_exp: float,
    neutral_exp: float,
    bear_exp: float,
    crisis_exp: float,
    # IC 파라미터
    use_ic_weights: bool,
    ic_blend: float,  # IC 가중치 혼합 비율 (0=고정, 1=IC only)
    ic_floor: float,  # IC 최소값 (음수 IC 처리)
    # 고정 파라미터
    cost_rate: float = 0.002,
    dd_warning: float = -0.20,
    dd_stop: float = -0.35,
) -> tuple:
    """v26 베이스라인 + IC 기반 팩터 가중치 백테스트"""
    
    n_days, n_assets = returns.shape
    n_factors = factors.shape[2]
    
    # v26 고정 팩터 가중치
    base_fw = np.array([0.12, 0.12, 0.10, 0.10, 0.08, 0.10, 0.10, 0.10, 0.08, 0.10])
    
    # 초기화
    portfolio_value = 1.0
    peak = 1.0
    is_halted = False
    halt_counter = 0
    
    current_weights = np.zeros(n_assets)
    daily_returns = np.zeros(n_days - 252)
    
    vol_window = 20
    port_ret_sum = 0.0
    port_ret_sq_sum = 0.0
    port_ret_count = 0
    
    for t in range(252, n_days):
        # 레짐 점수
        trend = regime_scores[t-1, 0]
        vol = regime_scores[t-1, 1]
        credit = regime_scores[t-1, 2]
        
        composite = trend * trend_w + vol * vol_w + credit * credit_w
        
        # 레짐별 노출도 (v26과 동일)
        if composite >= 0.87:
            base_exposure = ultra_bull_exp
        elif composite >= 0.70:
            base_exposure = bull_exp
        elif composite >= 0.50:
            base_exposure = neutral_exp
        elif composite >= 0.30:
            base_exposure = bear_exp
        else:
            base_exposure = crisis_exp
        
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
        
        # 변동성 타겟
        if port_ret_count >= vol_window:
            mean_ret = port_ret_sum / vol_window
            var_ret = port_ret_sq_sum / vol_window - mean_ret ** 2
            rv = np.sqrt(max(0, var_ret)) * np.sqrt(252)
            if rv > 0.01:
                vol_adj = target_vol / rv
                vol_adj = min(2.0, max(0.5, vol_adj))
            else:
                vol_adj = 1.0
        else:
            vol_adj = 1.0
        
        # 리밸런싱
        if t % rebal_days == 0:
            # IC 기반 팩터 가중치 계산
            if use_ic_weights:
                ic_vals = ic_scores[t-1, :]
                
                # IC를 가중치로 변환 (양수만 사용)
                ic_weights = np.zeros(n_factors)
                for f in range(n_factors):
                    ic_weights[f] = max(ic_floor, ic_vals[f])
                
                # 정규화
                ic_sum = ic_weights.sum()
                if ic_sum > 0.01:
                    ic_weights = ic_weights / ic_sum
                else:
                    ic_weights = base_fw.copy()
                
                # 고정 가중치와 혼합
                fw = np.zeros(n_factors)
                for f in range(n_factors):
                    fw[f] = (1 - ic_blend) * base_fw[f] + ic_blend * ic_weights[f]
            else:
                fw = base_fw.copy()
            
            # 팩터 점수 계산
            scores = np.zeros(n_assets)
            for i in range(n_assets):
                for f in range(n_factors):
                    scores[i] += factors[t-1, i, f] * fw[f]
            
            # 상위 K 종목
            top_indices = np.argsort(-scores)[:top_k]
            
            # 노출도
            exposure = base_exposure * vol_adj
            exposure = min(max_lev, max(min_lev, exposure))
            exposure *= dd_scale
            
            # 새 가중치
            new_weights = np.zeros(n_assets)
            for idx in top_indices:
                new_weights[idx] = exposure / top_k
            
            current_weights = new_weights
        
        # 수익률 계산
        port_ret = 0.0
        for i in range(n_assets):
            port_ret += current_weights[i] * returns[t, i]
        
        # 비용
        if t % rebal_days == 0:
            port_ret -= cost_rate * 0.5
        
        daily_returns[t - 252] = port_ret
        portfolio_value *= (1 + port_ret)
        
        # 변동성 버퍼 업데이트
        if port_ret_count < vol_window:
            port_ret_sum += port_ret
            port_ret_sq_sum += port_ret ** 2
            port_ret_count += 1
        else:
            port_ret_sum += port_ret
            port_ret_sq_sum += port_ret ** 2
    
    # 성과 계산
    valid_returns = daily_returns[daily_returns != 0]
    if len(valid_returns) < 100:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    
    mean_ret = np.mean(valid_returns)
    std_ret = np.std(valid_returns)
    
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 1e-10 else 0
    annual_return = mean_ret * 252
    
    # MDD
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
    
    # IS/OOS
    is_end = int(len(valid_returns) * 0.55)
    is_returns = valid_returns[:is_end]
    oos_returns = valid_returns[is_end:]
    
    is_sharpe = (np.mean(is_returns) / np.std(is_returns)) * np.sqrt(252) if np.std(is_returns) > 1e-10 else 0
    oos_sharpe = (np.mean(oos_returns) / np.std(oos_returns)) * np.sqrt(252) if np.std(oos_returns) > 1e-10 else 0
    
    return sharpe, is_sharpe, oos_sharpe, annual_return, max_dd

# =============================================================================
# 파레토 최적화
# =============================================================================
print("\n[6] 파레토 최적화 시작...")

# v26 베이스라인 파라미터 (고정)
v26_baseline = {
    'trend_w': 0.47,
    'vol_w': 0.28,
    'credit_w': 0.25,
    'cost_rate': 0.002,
    'dd_warning': -0.20,
    'dd_stop': -0.35,
    'min_lev': 0.3,
}

# 테스트할 파라미터 (v26 최적값 주변 + IC 파라미터)
param_grid = {
    'target_vol': [0.27, 0.29, 0.31],
    'max_lev': [1.9, 2.0],
    'rebal_days': [13, 14, 15],
    'top_k': [45, 48],
    'ultra_bull_exp': [1.9, 2.0, 2.1],
    'bull_exp': [1.6, 1.7, 1.8],
    'neutral_exp': [1.1, 1.2, 1.3],
    'bear_exp': [0.5, 0.6, 0.7],
    'crisis_exp': [0.2, 0.24, 0.3],
    # IC 파라미터
    'use_ic_weights': [True, False],
    'ic_blend': [0.2, 0.4, 0.6, 0.8],
    'ic_floor': [0.0, 0.02, 0.05],
}

keys = list(param_grid.keys())
values = list(param_grid.values())
all_combinations = list(itertools.product(*values))

print(f"  총 조합 수: {len(all_combinations)}")

results = []

for i, combo in enumerate(all_combinations):
    params = dict(zip(keys, combo))
    
    # IC 미사용 시 ic_blend, ic_floor 무시
    if not params['use_ic_weights']:
        if params['ic_blend'] != 0.2 or params['ic_floor'] != 0.0:
            continue
    
    try:
        sharpe, is_sharpe, oos_sharpe, annual_return, mdd = fast_backtest_v26_ic(
            PRICES_NP, RETURNS_NP, FACTORS_NP, REGIME_SCORES_NP, IC_NP,
            target_vol=params['target_vol'],
            max_lev=params['max_lev'],
            min_lev=v26_baseline['min_lev'],
            rebal_days=params['rebal_days'],
            top_k=params['top_k'],
            trend_w=v26_baseline['trend_w'],
            vol_w=v26_baseline['vol_w'],
            credit_w=v26_baseline['credit_w'],
            ultra_bull_exp=params['ultra_bull_exp'],
            bull_exp=params['bull_exp'],
            neutral_exp=params['neutral_exp'],
            bear_exp=params['bear_exp'],
            crisis_exp=params['crisis_exp'],
            use_ic_weights=params['use_ic_weights'],
            ic_blend=params['ic_blend'],
            ic_floor=params['ic_floor'],
            cost_rate=v26_baseline['cost_rate'],
            dd_warning=v26_baseline['dd_warning'],
            dd_stop=v26_baseline['dd_stop'],
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
    except Exception as e:
        pass
    
    if (i + 1) % 1000 == 0:
        print(f"  진행: {i+1}/{len(all_combinations)} ({100*(i+1)/len(all_combinations):.1f}%)")

print(f"\n  유효 결과: {len(results)}")

# 결과 정렬
if len(results) > 0:
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('sharpe', ascending=False)
    
    print("\n[7] 상위 20개 결과:")
    print("-" * 120)
    
    for idx, row in results_df.head(20).iterrows():
        print(f"  Sharpe: {row['sharpe']:.4f} | IS: {row['is_sharpe']:.4f} | OOS: {row['oos_sharpe']:.4f} | "
              f"AR: {row['annual_return']*100:.1f}% | MDD: {row['mdd']*100:.1f}% | "
              f"IC: {row['use_ic_weights']} | blend: {row['ic_blend']:.1f}")
    
    # v26 베이스라인 대비 비교
    print("\n" + "=" * 70)
    print("v26 베이스라인 대비 비교")
    print("=" * 70)
    print(f"  v26 베이스라인: Sharpe 2.02, OOS 1.40, MDD -29.3%")
    
    best = results_df.iloc[0]
    print(f"\n  최고 성과 (IC={best['use_ic_weights']}, blend={best['ic_blend']:.1f}):")
    print(f"    Sharpe: {best['sharpe']:.4f} ({(best['sharpe']/2.02-1)*100:+.1f}%)")
    print(f"    OOS Sharpe: {best['oos_sharpe']:.4f} ({(best['oos_sharpe']/1.40-1)*100:+.1f}%)")
    print(f"    MDD: {best['mdd']*100:.2f}% ({(best['mdd']/(-0.293)-1)*100:+.1f}%)")
    
    # IC 효과 분석
    ic_true = results_df[results_df['use_ic_weights'] == True]
    ic_false = results_df[results_df['use_ic_weights'] == False]
    
    if len(ic_true) > 0 and len(ic_false) > 0:
        print(f"\n  IC 효과 분석:")
        print(f"    IC=True 평균 Sharpe: {ic_true['sharpe'].mean():.4f}")
        print(f"    IC=False 평균 Sharpe: {ic_false['sharpe'].mean():.4f}")
        print(f"    IC 효과: {(ic_true['sharpe'].mean()/ic_false['sharpe'].mean()-1)*100:+.2f}%")
        
        # IC blend별 분석
        print(f"\n  IC blend별 평균 Sharpe:")
        for blend in [0.2, 0.4, 0.6, 0.8]:
            blend_df = ic_true[ic_true['ic_blend'] == blend]
            if len(blend_df) > 0:
                print(f"    blend={blend:.1f}: {blend_df['sharpe'].mean():.4f}")
    
    # 결과 저장
    output = {
        'baseline': {'sharpe': 2.02, 'oos_sharpe': 1.40, 'mdd': -0.293},
        'total_combinations': len(all_combinations),
        'valid_results': len(results),
        'best_result': best.to_dict(),
        'ic_effect': {
            'ic_true_avg_sharpe': ic_true['sharpe'].mean() if len(ic_true) > 0 else 0,
            'ic_false_avg_sharpe': ic_false['sharpe'].mean() if len(ic_false) > 0 else 0,
        },
        'top_results': results_df.head(30).to_dict('records')
    }
    
    with open('/home/ubuntu/ares_v26_plus_ic_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n결과 저장: /home/ubuntu/ares_v26_plus_ic_results.json")

print("\n완료!")
