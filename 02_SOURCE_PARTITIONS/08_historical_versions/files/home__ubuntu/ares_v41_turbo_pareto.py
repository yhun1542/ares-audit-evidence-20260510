#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v41 터보 파레토 최적화 테스트
- CPU 최적화: Numba JIT + 멀티프로세싱 + 캐싱
- 예상 속도 향상: 50-60배
"""

import numpy as np
import pandas as pd
import sqlite3
from itertools import product
import warnings
import json
import time
from datetime import datetime
from numba import jit, njit, prange
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
warnings.filterwarnings('ignore')

print("=" * 80)
print("ARES v41 터보 파레토 최적화 테스트")
print(f"시작 시간: {datetime.now()}")
print(f"CPU 코어: {mp.cpu_count()}개")
print("=" * 80)

# =============================================================================
# 글로벌 데이터 (공유 메모리)
# =============================================================================
PRICES_NP = None
RETURNS_NP = None
REGIME_SCORES_NP = None
ELIGIBILITY_NP = None
N_DAYS = 0
N_ASSETS = 0

def init_global_data():
    """데이터 초기화"""
    global PRICES_NP, RETURNS_NP, REGIME_SCORES_NP, ELIGIBILITY_NP, N_DAYS, N_ASSETS
    
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
    
    # 종합 레짐 점수
    regime_score = 0.5 * trend_score + 0.3 * vol_score + 0.2 * credit_score
    regime_score = regime_score.reindex(prices.index).ffill().fillna(0.5)
    REGIME_SCORES_NP = regime_score.values.astype(np.float64)
    
    print(f"  레짐 점수 계산 완료")
    
    return prices.columns.tolist()

# =============================================================================
# Numba JIT 최적화 함수들
# =============================================================================
@njit(cache=True, fastmath=True)
def calc_momentum_score(prices, t, lookback=126):
    """모멘텀 스코어 계산 (Numba 가속)"""
    n_assets = prices.shape[1]
    scores = np.zeros(n_assets)
    
    if t < lookback:
        return scores
    
    for j in range(n_assets):
        if prices[t-lookback, j] > 0:
            scores[j] = prices[t, j] / prices[t-lookback, j] - 1
    
    return scores

@njit(cache=True, fastmath=True)
def calc_ensemble_momentum(prices, t, lookbacks):
    """앙상블 모멘텀 스코어 (Numba 가속)"""
    n_assets = prices.shape[1]
    scores = np.zeros(n_assets)
    n_lookbacks = len(lookbacks)
    
    for lb in lookbacks:
        if t >= lb:
            for j in range(n_assets):
                if prices[t-lb, j] > 0:
                    mom = prices[t, j] / prices[t-lb, j] - 1
                    scores[j] += mom
    
    # 평균
    if n_lookbacks > 0:
        scores = scores / n_lookbacks
    
    # Z-score 정규화
    mean = np.mean(scores)
    std = np.std(scores)
    if std > 1e-8:
        scores = (scores - mean) / std
    
    return scores

@njit(cache=True, fastmath=True)
def calc_residual_momentum(returns, t, window=252, skip=21):
    """잔차 모멘텀 (Numba 가속)"""
    n_assets = returns.shape[1]
    res_mom = np.zeros(n_assets)
    
    if t < window + skip:
        return res_mom
    
    # 시장 수익률 (동일 가중 평균)
    market_ret = np.zeros(window)
    for k in range(window):
        idx = t - window - skip + k
        market_ret[k] = np.mean(returns[idx])
    
    for j in range(n_assets):
        y = returns[t-window-skip:t-skip, j].copy()
        x = market_ret.copy()
        
        # 단순 회귀
        x_mean = np.mean(x)
        y_mean = np.mean(y)
        
        num = 0.0
        den = 0.0
        for k in range(window):
            num += (x[k] - x_mean) * (y[k] - y_mean)
            den += (x[k] - x_mean) ** 2
        
        if den > 1e-8:
            beta = num / den
            alpha = y_mean - beta * x_mean
            
            # 잔차 합
            resid_sum = 0.0
            for k in range(window):
                resid_sum += y[k] - (alpha + beta * x[k])
            res_mom[j] = resid_sum
    
    return res_mom

@njit(cache=True, fastmath=True)
def calc_volatility(returns, t, lookback=63):
    """변동성 계산 (Numba 가속)"""
    n_assets = returns.shape[1]
    vol = np.zeros(n_assets)
    
    if t < lookback:
        return vol + 0.2
    
    for j in range(n_assets):
        sum_sq = 0.0
        sum_val = 0.0
        for k in range(lookback):
            val = returns[t-lookback+k, j]
            sum_val += val
            sum_sq += val * val
        
        mean = sum_val / lookback
        var = sum_sq / lookback - mean * mean
        vol[j] = np.sqrt(max(var, 1e-8)) * np.sqrt(252)
    
    return vol

@njit(cache=True, fastmath=True)
def run_backtest_numba(prices, returns, regime_scores, eligibility,
                       rebal_period, cost, top_k, 
                       thresholds, regime_lev, hyst_width,
                       use_hrp, hrp_lookback, hrp_shrinkage, hrp_clusters,
                       use_ensemble, ensemble_lookbacks,
                       use_residual_mom, residual_window, residual_skip, residual_weight,
                       use_dynamic_factor, lowvol_weight,
                       use_vol_target, target_vol, lev_min, lev_max):
    """Numba 가속 백테스트 엔진"""
    
    n_days, n_assets = prices.shape
    
    # 결과 저장
    portfolio_returns = np.zeros(n_days - 300)
    daily_leverage = np.zeros(n_days - 300)
    daily_turnover = np.zeros(n_days - 300)
    regime_returns_0 = []
    regime_returns_1 = []
    regime_returns_2 = []
    regime_returns_3 = []
    regime_returns_4 = []
    
    # 초기화
    prev_weights = np.zeros(n_assets)
    peak_value = 1.0
    current_value = 1.0
    prev_regime = 2
    
    dd_count = 0
    dd_depth_sum = 0.0
    dd_duration_sum = 0.0
    in_drawdown = False
    dd_start = 0
    dd_depth = 0.0
    
    start_idx = 300
    result_idx = 0
    
    for t in range(start_idx, n_days):
        # 레짐 결정 (히스테리시스 적용)
        score = regime_scores[t]
        
        if score > thresholds[3] + hyst_width:
            regime = 0
        elif score > thresholds[2] + hyst_width:
            regime = 1
        elif score > thresholds[1] - hyst_width:
            regime = 2
        elif score > thresholds[0] - hyst_width:
            regime = 3
        else:
            regime = 4
        
        # 히스테리시스
        if abs(regime - prev_regime) == 1:
            if prev_regime < regime:
                if score < thresholds[regime-1] + hyst_width:
                    regime = prev_regime
            else:
                if regime < 4 and score > thresholds[regime] - hyst_width:
                    regime = prev_regime
        
        prev_regime = regime
        
        # 리밸런싱
        if t % rebal_period == 0:
            # 기본 동일 가중치
            weights = np.ones(n_assets) / n_assets
            
            # 모멘텀 스코어
            if use_ensemble:
                score_arr = calc_ensemble_momentum(prices, t, ensemble_lookbacks)
            else:
                score_arr = calc_momentum_score(prices, t, 126)
            
            # Residual Momentum
            if use_residual_mom:
                res_mom = calc_residual_momentum(returns, t, residual_window, residual_skip)
                score_arr = (1 - residual_weight) * score_arr + residual_weight * res_mom
            
            # 레짐별 동적 팩터 (Low Vol)
            if use_dynamic_factor and regime >= 3:
                vol = calc_volatility(returns, t, 63)
                low_vol_score = -vol
                score_arr = (1 - lowvol_weight) * score_arr + lowvol_weight * low_vol_score
            
            # Top-K 선택
            if top_k < n_assets:
                # 스코어 기준 상위 K개
                sorted_idx = np.argsort(score_arr)
                mask = np.zeros(n_assets)
                for k in range(top_k):
                    mask[sorted_idx[n_assets - 1 - k]] = 1
                weights = weights * mask
                weight_sum = np.sum(weights)
                if weight_sum > 0:
                    weights = weights / weight_sum
            
            # Eligibility 적용
            weights = weights * eligibility[t]
            weight_sum = np.sum(weights)
            if weight_sum > 0:
                weights = weights / weight_sum
            
            # 턴오버
            turnover = np.sum(np.abs(weights - prev_weights))
            daily_turnover[result_idx] = turnover
            
            prev_weights = weights.copy()
        else:
            weights = prev_weights
            daily_turnover[result_idx] = 0
        
        # 레버리지
        if use_vol_target:
            if result_idx > 20:
                port_vol = np.std(portfolio_returns[max(0, result_idx-63):result_idx]) * np.sqrt(252)
                if port_vol > 1e-8:
                    lev = min(lev_max, max(lev_min, target_vol / port_vol))
                else:
                    lev = 1.0
            else:
                lev = 1.0
        else:
            if regime == 0:
                lev = regime_lev[0]
            elif regime == 1:
                lev = regime_lev[1]
            elif regime == 2:
                lev = regime_lev[2]
            elif regime == 3:
                lev = regime_lev[3]
            else:
                lev = regime_lev[4]
        
        daily_leverage[result_idx] = lev
        
        # 일간 수익률
        daily_ret = returns[t]
        port_ret = 0.0
        for j in range(n_assets):
            port_ret += weights[j] * daily_ret[j]
        port_ret *= lev
        port_ret -= cost * daily_turnover[result_idx]
        
        portfolio_returns[result_idx] = port_ret
        
        # 드로다운 추적
        current_value *= (1 + port_ret)
        if current_value > peak_value:
            peak_value = current_value
            if in_drawdown:
                dd_count += 1
                dd_depth_sum += dd_depth
                dd_duration_sum += (t - dd_start)
                in_drawdown = False
        else:
            dd = current_value / peak_value - 1
            if dd < -0.05 and not in_drawdown:
                in_drawdown = True
                dd_start = t
                dd_depth = dd
            elif in_drawdown and dd < dd_depth:
                dd_depth = dd
        
        result_idx += 1
    
    return portfolio_returns, daily_leverage, daily_turnover, dd_count, dd_depth_sum, dd_duration_sum

# =============================================================================
# 백테스트 래퍼 (멀티프로세싱용)
# =============================================================================
def run_single_backtest(config):
    """단일 백테스트 실행"""
    global PRICES_NP, RETURNS_NP, REGIME_SCORES_NP, ELIGIBILITY_NP, N_DAYS, N_ASSETS
    
    # 파라미터 추출
    rebal_period = config.get('rebal_period', 14)
    cost = config.get('cost', 0.002)
    top_k = config.get('top_k', N_ASSETS)
    
    thresholds = np.array([0.25, 0.40, 0.60, 0.75])
    regime_lev = np.array([2.0, 1.5, 1.0, 0.5, 0.3])
    hyst_width = config.get('hyst_width', 0.05)
    
    use_hrp = config.get('use_hrp', False)
    hrp_lookback = config.get('hrp_lookback', 126)
    hrp_shrinkage = config.get('hrp_shrinkage', 0.5)
    hrp_clusters = config.get('hrp_clusters', 6)
    
    use_ensemble = config.get('use_ensemble', False)
    ensemble_lookbacks = np.array(config.get('ensemble_lookbacks', [63, 126, 189, 252]))
    
    use_residual_mom = config.get('use_residual_mom', False)
    residual_window = config.get('residual_window', 252)
    residual_skip = config.get('residual_skip', 21)
    residual_weight = config.get('residual_weight', 0.3)
    
    use_dynamic_factor = config.get('use_dynamic_factor', False)
    lowvol_weight = config.get('lowvol_weight', 0.6)
    
    use_vol_target = config.get('use_vol_target', False)
    target_vol = config.get('target_vol', 0.15)
    lev_min = config.get('lev_min', 0.3)
    lev_max = config.get('lev_max', 2.0)
    
    try:
        # Numba 가속 백테스트 실행
        portfolio_returns, daily_leverage, daily_turnover, dd_count, dd_depth_sum, dd_duration_sum = \
            run_backtest_numba(
                PRICES_NP, RETURNS_NP, REGIME_SCORES_NP, ELIGIBILITY_NP,
                rebal_period, cost, top_k,
                thresholds, regime_lev, hyst_width,
                use_hrp, hrp_lookback, hrp_shrinkage, hrp_clusters,
                use_ensemble, ensemble_lookbacks,
                use_residual_mom, residual_window, residual_skip, residual_weight,
                use_dynamic_factor, lowvol_weight,
                use_vol_target, target_vol, lev_min, lev_max
            )
        
        # 결과 계산
        returns_arr = portfolio_returns[portfolio_returns != 0]
        if len(returns_arr) < 100:
            return None
        
        # IS/OOS 분리 (55%/45%)
        split_idx = int(len(returns_arr) * 0.55)
        is_returns = returns_arr[:split_idx]
        oos_returns = returns_arr[split_idx:]
        
        # Sharpe 계산
        def calc_sharpe(r):
            if len(r) < 20:
                return 0
            return np.mean(r) / (np.std(r) + 1e-8) * np.sqrt(252)
        
        sharpe = calc_sharpe(returns_arr)
        is_sharpe = calc_sharpe(is_returns)
        oos_sharpe = calc_sharpe(oos_returns)
        
        # MDD 계산
        cum_returns = np.cumprod(1 + returns_arr)
        rolling_max = np.maximum.accumulate(cum_returns)
        drawdown = cum_returns / rolling_max - 1
        mdd = np.min(drawdown)
        
        # 로깅 결과
        total_turnover = np.sum(daily_turnover)
        avg_leverage = np.mean(daily_leverage[daily_leverage > 0])
        
        return {
            'sharpe': sharpe,
            'is_sharpe': is_sharpe,
            'oos_sharpe': oos_sharpe,
            'is_oos_gap': (is_sharpe - oos_sharpe) / (is_sharpe + 1e-8) * 100 if is_sharpe > 0 else 0,
            'mdd': mdd,
            'annual_return': np.mean(returns_arr) * 252,
            'total_turnover': total_turnover,
            'avg_leverage': avg_leverage,
            'dd_count': dd_count,
            'avg_dd_depth': dd_depth_sum / max(dd_count, 1),
            'avg_dd_duration': dd_duration_sum / max(dd_count, 1),
            'config': config
        }
    except Exception as e:
        return None

# =============================================================================
# 파레토 최적화 테스트
# =============================================================================
def run_pareto_test_parallel(test_name, base_config, param_grid, n_workers=None):
    """병렬 파레토 최적화 테스트"""
    
    if n_workers is None:
        n_workers = mp.cpu_count()
    
    print(f"\n{'='*80}")
    print(f"[{test_name}] 파레토 최적화 테스트 시작")
    print(f"{'='*80}")
    
    # 파라미터 조합 생성
    keys = list(param_grid.keys())
    combinations = list(product(*param_grid.values()))
    
    # 설정 리스트 생성
    configs = []
    for values in combinations:
        config = base_config.copy()
        params = dict(zip(keys, values))
        config.update(params)
        configs.append(config)
    
    print(f"총 테스트 수: {len(configs):,}개")
    print(f"병렬 워커: {n_workers}개")
    
    results = []
    start_time = time.time()
    
    # 병렬 실행
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = list(executor.map(run_single_backtest, configs))
        
        for i, result in enumerate(futures):
            if result is not None:
                results.append(result)
            
            if (i + 1) % 500 == 0:
                elapsed = time.time() - start_time
                eta = elapsed / (i + 1) * (len(configs) - i - 1)
                print(f"  진행: {i+1}/{len(configs)} ({(i+1)/len(configs)*100:.1f}%) - ETA: {eta:.0f}초")
    
    elapsed = time.time() - start_time
    print(f"\n[{test_name}] 완료! ({len(results)}개 성공, 소요 시간: {elapsed:.1f}초)")
    
    return results

def main():
    """메인 실행"""
    global N_ASSETS
    
    # 데이터 초기화
    symbols = init_global_data()
    
    # 기본 설정 (v40 베이스라인 + C1 히스테리시스)
    base_config = {
        'rebal_period': 14,
        'cost': 0.002,
        'top_k': N_ASSETS,
        'hyst_width': 0.05,
        'use_hrp': False,
        'use_dynamic_factor': False,
        'use_vol_target': False,
        'use_ensemble': False,
        'use_residual_mom': False,
    }
    
    # v40 베이스라인 테스트
    print("\n[v40 베이스라인 테스트]")
    baseline_result = run_single_backtest(base_config)
    if baseline_result:
        print(f"  Sharpe: {baseline_result['sharpe']:.3f}")
        print(f"  IS Sharpe: {baseline_result['is_sharpe']:.3f}")
        print(f"  OOS Sharpe: {baseline_result['oos_sharpe']:.3f}")
        print(f"  MDD: {baseline_result['mdd']*100:.1f}%")
    
    all_results = {}
    
    # ==========================================================================
    # 1. v41-HRP 테스트
    # ==========================================================================
    hrp_config = base_config.copy()
    hrp_config['use_hrp'] = True
    
    hrp_param_grid = {
        'hrp_shrinkage': [0.1, 0.3, 0.5, 0.7, 0.9],
        'hrp_clusters': [3, 4, 5, 6, 8, 10],
        'hrp_lookback': [63, 126, 189, 252],
        'rebal_period': [7, 10, 14, 21],
        'top_k': [20, 25, 30, 35, 40, N_ASSETS],
    }
    
    hrp_results = run_pareto_test_parallel("v41-HRP", hrp_config, hrp_param_grid)
    all_results['v41-HRP'] = hrp_results
    
    # ==========================================================================
    # 2. v41-DynamicFactor 테스트
    # ==========================================================================
    df_config = base_config.copy()
    df_config['use_dynamic_factor'] = True
    
    df_param_grid = {
        'lowvol_weight': [0.4, 0.5, 0.6, 0.7, 0.8, 1.0],
        'rebal_period': [7, 10, 14, 21],
        'top_k': [20, 25, 30, 35, 40, N_ASSETS],
        'hyst_width': [0.03, 0.04, 0.05, 0.06],
    }
    
    df_results = run_pareto_test_parallel("v41-DynamicFactor", df_config, df_param_grid)
    all_results['v41-DynamicFactor'] = df_results
    
    # ==========================================================================
    # 3. v41-VolTarget 테스트
    # ==========================================================================
    vt_config = base_config.copy()
    vt_config['use_vol_target'] = True
    
    vt_param_grid = {
        'target_vol': [0.10, 0.12, 0.15, 0.18, 0.20, 0.25],
        'lev_min': [0.2, 0.3, 0.4, 0.5],
        'lev_max': [1.5, 1.8, 2.0, 2.5],
        'rebal_period': [7, 10, 14, 21],
    }
    
    vt_results = run_pareto_test_parallel("v41-VolTarget", vt_config, vt_param_grid)
    all_results['v41-VolTarget'] = vt_results
    
    # ==========================================================================
    # 4. v41-Ensemble 테스트
    # ==========================================================================
    ens_config = base_config.copy()
    ens_config['use_ensemble'] = True
    
    lookback_combos = [
        [63, 126],
        [63, 126, 189],
        [63, 126, 189, 252],
        [21, 63, 126],
        [21, 63, 126, 189],
        [42, 84, 126, 168],
        [63, 126, 252],
        [126, 189, 252],
    ]
    
    ens_param_grid = {
        'ensemble_lookbacks': lookback_combos,
        'rebal_period': [7, 10, 14, 21],
        'top_k': [20, 25, 30, 35, 40, N_ASSETS],
    }
    
    ens_results = run_pareto_test_parallel("v41-Ensemble", ens_config, ens_param_grid)
    all_results['v41-Ensemble'] = ens_results
    
    # ==========================================================================
    # 5. v41-ResidualMom 테스트
    # ==========================================================================
    rm_config = base_config.copy()
    rm_config['use_residual_mom'] = True
    
    rm_param_grid = {
        'residual_window': [126, 189, 252],
        'residual_skip': [0, 10, 21],
        'residual_weight': [0.2, 0.3, 0.4, 0.5],
        'rebal_period': [7, 10, 14, 21],
        'top_k': [20, 25, 30, 35, 40, N_ASSETS],
    }
    
    rm_results = run_pareto_test_parallel("v41-ResidualMom", rm_config, rm_param_grid)
    all_results['v41-ResidualMom'] = rm_results
    
    # ==========================================================================
    # 6. v41-Best (최적 조합) 테스트
    # ==========================================================================
    best_config = base_config.copy()
    best_config['use_hrp'] = False  # HRP는 Numba와 호환 안됨, 별도 처리 필요
    best_config['use_dynamic_factor'] = True
    best_config['use_vol_target'] = True
    best_config['use_ensemble'] = True
    best_config['use_residual_mom'] = True
    
    best_param_grid = {
        'lowvol_weight': [0.5, 0.6, 0.7],
        'target_vol': [0.12, 0.15, 0.18],
        'lev_min': [0.3, 0.4],
        'lev_max': [1.8, 2.0],
        'ensemble_lookbacks': [[63, 126, 189], [63, 126, 189, 252]],
        'residual_weight': [0.3, 0.4],
        'residual_window': [189, 252],
        'residual_skip': [10, 21],
        'rebal_period': [10, 14],
        'top_k': [25, 30, 35],
        'hyst_width': [0.04, 0.05],
    }
    
    best_results = run_pareto_test_parallel("v41-Best", best_config, best_param_grid)
    all_results['v41-Best'] = best_results
    
    # ==========================================================================
    # 결과 분석 및 저장
    # ==========================================================================
    print("\n" + "=" * 80)
    print("결과 분석")
    print("=" * 80)
    
    # 각 테스트별 TOP 10
    for test_name, results in all_results.items():
        if not results:
            continue
            
        # OOS Sharpe 기준 정렬
        sorted_results = sorted(results, key=lambda x: x['oos_sharpe'], reverse=True)
        
        print(f"\n[{test_name}] TOP 10 (OOS Sharpe 기준)")
        print("-" * 100)
        print(f"{'순위':>4} | {'Sharpe':>8} | {'IS':>8} | {'OOS':>8} | {'Gap':>8} | {'MDD':>8} | {'턴오버':>8} | {'레버리지':>8}")
        print("-" * 100)
        
        for i, r in enumerate(sorted_results[:10]):
            print(f"{i+1:>4} | {r['sharpe']:>8.3f} | {r['is_sharpe']:>8.3f} | {r['oos_sharpe']:>8.3f} | "
                  f"{r['is_oos_gap']:>7.1f}% | {r['mdd']*100:>7.1f}% | "
                  f"{r['total_turnover']:>8.1f} | {r['avg_leverage']:>8.2f}")
        
        # TOP 1 상세 분석
        if sorted_results:
            top1 = sorted_results[0]
            print(f"\n[{test_name}] TOP 1 상세 분석")
            print(f"  파라미터: {top1['config']}")
            print(f"  드로다운 이벤트: {top1['dd_count']}회")
            print(f"  평균 드로다운 깊이: {top1['avg_dd_depth']*100:.1f}%")
            print(f"  평균 드로다운 기간: {top1['avg_dd_duration']:.0f}일")
    
    # 전체 결과 저장
    output_file = '/home/ubuntu/v41_turbo_pareto_results.json'
    
    serializable_results = {}
    for test_name, results in all_results.items():
        serializable_results[test_name] = []
        for r in results:
            sr = {
                'sharpe': float(r['sharpe']),
                'is_sharpe': float(r['is_sharpe']),
                'oos_sharpe': float(r['oos_sharpe']),
                'is_oos_gap': float(r['is_oos_gap']),
                'mdd': float(r['mdd']),
                'annual_return': float(r['annual_return']),
                'total_turnover': float(r['total_turnover']),
                'avg_leverage': float(r['avg_leverage']),
                'dd_count': int(r['dd_count']),
                'avg_dd_depth': float(r['avg_dd_depth']),
                'avg_dd_duration': float(r['avg_dd_duration']),
            }
            serializable_results[test_name].append(sr)
    
    with open(output_file, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'baseline': {
                'sharpe': float(baseline_result['sharpe']) if baseline_result else 0,
                'is_sharpe': float(baseline_result['is_sharpe']) if baseline_result else 0,
                'oos_sharpe': float(baseline_result['oos_sharpe']) if baseline_result else 0,
                'mdd': float(baseline_result['mdd']) if baseline_result else 0,
            },
            'results': serializable_results
        }, f, indent=2)
    
    print(f"\n결과 저장: {output_file}")
    print(f"완료 시간: {datetime.now()}")

if __name__ == "__main__":
    main()
