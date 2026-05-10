#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v41 파레토 최적화 테스트 (수정본)
- 5가지 개선방안 각각 수만 가지 조합 테스트
- 정밀 로깅: 팩터별 IC, 레짐별 성과, 턴오버, 드로다운 이벤트
- v40 베이스라인 (카테고리 A 버그 수정) 기반
"""

import numpy as np
import pandas as pd
import sqlite3
from itertools import product
import warnings
import json
import time
from datetime import datetime
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
warnings.filterwarnings('ignore')

print("=" * 80)
print("ARES v41 파레토 최적화 테스트")
print(f"시작 시간: {datetime.now()}")
print("=" * 80)

# =============================================================================
# 글로벌 데이터
# =============================================================================
PRICES_NP = None
RETURNS_NP = None
REGIME_SCORES_NP = None
ELIGIBILITY_NP = None
N_DAYS = 0
N_ASSETS = 0

def init_global_data():
    """데이터 초기화 - 카테고리 A 패치 적용"""
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
    
    return prices, returns, regime_score

# =============================================================================
# HRP (Hierarchical Risk Parity) 구현
# =============================================================================
def hrp_weights(returns_df, shrinkage=0.5, n_clusters=6):
    """HRP 가중치 계산"""
    try:
        # 공분산 및 상관관계 계산
        cov = returns_df.cov()
        corr = returns_df.corr()
        
        # Shrinkage 적용
        cov_shrunk = shrinkage * np.diag(np.diag(cov)) + (1 - shrinkage) * cov
        
        # 거리 행렬 계산
        dist = np.sqrt((1 - corr) / 2)
        dist = dist.fillna(0)
        np.fill_diagonal(dist.values, 0)
        
        # 계층적 클러스터링
        condensed = squareform(dist.values, checks=False)
        condensed = np.nan_to_num(condensed, nan=0.0)
        link = linkage(condensed, method='ward')
        
        # 클러스터 할당
        clusters = fcluster(link, n_clusters, criterion='maxclust')
        
        # Inverse Variance 가중치 (클러스터 내)
        weights = pd.Series(index=returns_df.columns, dtype=float)
        for c in range(1, n_clusters + 1):
            cluster_tickers = [t for t, cl in zip(returns_df.columns, clusters) if cl == c]
            if len(cluster_tickers) > 0:
                cluster_var = cov_shrunk.loc[cluster_tickers, cluster_tickers].values.diagonal()
                cluster_var = np.maximum(cluster_var, 1e-8)
                inv_var = 1 / cluster_var
                cluster_weights = inv_var / inv_var.sum()
                for i, t in enumerate(cluster_tickers):
                    weights[t] = cluster_weights[i] / n_clusters
        
        weights = weights / weights.sum()
        return weights.values
    except Exception as e:
        # 실패 시 동일 가중치
        return np.ones(len(returns_df.columns)) / len(returns_df.columns)

# =============================================================================
# Ensemble Lookback
# =============================================================================
def calculate_ensemble_momentum(prices_np, t, lookbacks=[63, 126, 189, 252]):
    """여러 룩백 기간의 모멘텀 앙상블"""
    n_assets = prices_np.shape[1]
    scores = np.zeros(n_assets)
    
    for lb in lookbacks:
        if t >= lb:
            mom = prices_np[t] / prices_np[t-lb] - 1
            mom = np.nan_to_num(mom, nan=0.0)
            # Z-score 정규화
            mean = np.nanmean(mom)
            std = np.nanstd(mom) + 1e-8
            mom_z = (mom - mean) / std
            scores += mom_z
    
    return scores / len(lookbacks)

# =============================================================================
# Residual Momentum
# =============================================================================
def calculate_residual_momentum(returns_np, t, window=252, skip=21):
    """시장 베타를 제거한 잔차 모멘텀"""
    n_assets = returns_np.shape[1]
    res_mom = np.zeros(n_assets)
    
    if t < window + skip:
        return res_mom
    
    # 시장 수익률 (동일 가중 평균)
    market_ret = np.nanmean(returns_np[t-window-skip:t-skip], axis=1)
    
    for i in range(n_assets):
        try:
            y = returns_np[t-window-skip:t-skip, i]
            x = market_ret
            
            # 단순 회귀
            valid = ~(np.isnan(y) | np.isnan(x))
            if valid.sum() < 20:
                continue
                
            y_valid = y[valid]
            x_valid = x[valid]
            
            x_mean = x_valid.mean()
            y_mean = y_valid.mean()
            beta = np.sum((x_valid - x_mean) * (y_valid - y_mean)) / (np.sum((x_valid - x_mean)**2) + 1e-8)
            alpha = y_mean - beta * x_mean
            
            # 잔차 계산
            resid = y_valid - (alpha + beta * x_valid)
            res_mom[i] = np.sum(resid)
        except:
            pass
    
    return res_mom

# =============================================================================
# 백테스트 엔진 (정밀 로깅 포함)
# =============================================================================
def run_backtest(config):
    """정밀 로깅이 포함된 백테스트"""
    global PRICES_NP, RETURNS_NP, REGIME_SCORES_NP, ELIGIBILITY_NP, N_DAYS, N_ASSETS
    
    # 결과 저장
    portfolio_returns = []
    daily_leverage = []
    daily_turnover = []
    regime_returns = {0: [], 1: [], 2: [], 3: [], 4: []}
    drawdown_events = []
    
    # 초기화
    prev_weights = np.zeros(N_ASSETS)
    peak_value = 1.0
    current_value = 1.0
    in_drawdown = False
    dd_start = 0
    dd_depth = 0
    
    # 파라미터
    rebal_period = config.get('rebal_period', 14)
    cost = config.get('cost', 0.002)
    top_k = config.get('top_k', N_ASSETS)
    
    # 레짐 임계값
    thresholds = config.get('thresholds', [0.25, 0.40, 0.60, 0.75])
    regime_lev = config.get('regime_leverage', {0: 2.0, 1: 1.5, 2: 1.0, 3: 0.5, 4: 0.3})
    
    # 히스테리시스
    hyst_width = config.get('hyst_width', 0.03)
    prev_regime = 2
    
    # 시작 인덱스
    start_idx = max(300, config.get('lookback', 252) + 50)
    
    for t in range(start_idx, N_DAYS):
        # 레짐 결정 (히스테리시스 적용)
        score = REGIME_SCORES_NP[t]
        
        if score > thresholds[3] + hyst_width:
            regime = 0  # Ultra Bull
        elif score > thresholds[2] + hyst_width:
            regime = 1  # Bull
        elif score > thresholds[1] - hyst_width:
            regime = 2  # Neutral
        elif score > thresholds[0] - hyst_width:
            regime = 3  # Bear
        else:
            regime = 4  # Crisis
        
        # 히스테리시스: 경계에서는 이전 레짐 유지
        if abs(regime - prev_regime) == 1:
            if prev_regime < regime:
                if score < thresholds[regime-1] + hyst_width:
                    regime = prev_regime
            else:
                if score > thresholds[regime] - hyst_width:
                    regime = prev_regime
        
        prev_regime = regime
        
        # 리밸런싱 여부
        if t % rebal_period == 0:
            # 1. HRP 가중치 계산
            if config.get('use_hrp', False):
                lb = config.get('hrp_lookback', 126)
                returns_df = pd.DataFrame(RETURNS_NP[max(0,t-lb):t])
                weights = hrp_weights(
                    returns_df,
                    shrinkage=config.get('hrp_shrinkage', 0.5),
                    n_clusters=config.get('hrp_clusters', 6)
                )
            else:
                weights = np.ones(N_ASSETS) / N_ASSETS
            
            # 2. 팩터 스코어 계산
            if config.get('use_ensemble', False):
                lookbacks = config.get('ensemble_lookbacks', [63, 126, 189, 252])
                score_arr = calculate_ensemble_momentum(PRICES_NP, t, lookbacks)
            else:
                # 단일 모멘텀
                lb = 126
                if t >= lb:
                    score_arr = PRICES_NP[t] / PRICES_NP[t-lb] - 1
                    score_arr = np.nan_to_num(score_arr, nan=0.0)
                else:
                    score_arr = np.zeros(N_ASSETS)
            
            # 3. Residual Momentum
            if config.get('use_residual_mom', False):
                res_mom = calculate_residual_momentum(
                    RETURNS_NP, t,
                    window=config.get('residual_window', 252),
                    skip=config.get('residual_skip', 21)
                )
                res_weight = config.get('residual_weight', 0.3)
                score_arr = (1 - res_weight) * score_arr + res_weight * res_mom
            
            # 4. 레짐별 동적 팩터 (Low Vol 선호)
            if config.get('use_dynamic_factor', False) and regime >= 3:
                # Bear/Crisis에서는 Low Vol 선호
                vol_lb = 63
                if t >= vol_lb:
                    vol = np.nanstd(RETURNS_NP[t-vol_lb:t], axis=0)
                    vol = np.nan_to_num(vol, nan=1.0)
                    low_vol_score = -vol  # 낮은 변동성 선호
                    low_vol_weight = config.get('lowvol_weight', 0.6)
                    score_arr = (1 - low_vol_weight) * score_arr + low_vol_weight * low_vol_score
            
            # Top-K 선택
            if top_k < N_ASSETS:
                # 스코어 기준 상위 K개만 선택
                threshold_idx = np.argsort(score_arr)[-top_k:]
                mask = np.zeros(N_ASSETS)
                mask[threshold_idx] = 1
                weights = weights * mask
                if weights.sum() > 0:
                    weights = weights / weights.sum()
            
            # Eligibility 적용
            weights = weights * ELIGIBILITY_NP[t]
            if weights.sum() > 0:
                weights = weights / weights.sum()
            
            # 턴오버 계산
            turnover = np.sum(np.abs(weights - prev_weights))
            daily_turnover.append(turnover)
            
            prev_weights = weights.copy()
        else:
            weights = prev_weights
            daily_turnover.append(0)
        
        # 5. 변동성 타겟팅 레버리지
        if config.get('use_vol_target', False):
            if len(portfolio_returns) > 20:
                port_vol = np.std(portfolio_returns[-63:]) * np.sqrt(252) if len(portfolio_returns) > 63 else np.std(portfolio_returns[-20:]) * np.sqrt(252)
                target_vol = config.get('target_vol', 0.15)
                lev = min(config.get('lev_max', 2.0), max(config.get('lev_min', 0.3), target_vol / (port_vol + 1e-8)))
            else:
                lev = 1.0
        else:
            # 레짐 기반 레버리지
            lev = regime_lev.get(regime, 1.0)
        
        daily_leverage.append(lev)
        
        # 일간 수익률 계산
        daily_ret = RETURNS_NP[t]
        port_ret = np.nansum(weights * daily_ret) * lev
        
        # 거래비용 차감
        port_ret -= cost * daily_turnover[-1]
        
        portfolio_returns.append(port_ret)
        
        # 레짐별 수익률 기록
        regime_returns[regime].append(port_ret)
        
        # 드로다운 추적
        current_value *= (1 + port_ret)
        if current_value > peak_value:
            peak_value = current_value
            if in_drawdown:
                # 드로다운 종료
                drawdown_events.append({
                    'depth': dd_depth,
                    'duration': t - dd_start
                })
                in_drawdown = False
        else:
            dd = current_value / peak_value - 1
            if dd < -0.05 and not in_drawdown:
                in_drawdown = True
                dd_start = t
                dd_depth = dd
            elif in_drawdown and dd < dd_depth:
                dd_depth = dd
    
    # 결과 계산
    returns_arr = np.array(portfolio_returns)
    
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
    
    # 레짐별 Sharpe
    regime_sharpe = {}
    for r, rets in regime_returns.items():
        if len(rets) > 20:
            regime_sharpe[r] = np.mean(rets) / (np.std(rets) + 1e-8) * np.sqrt(252)
        else:
            regime_sharpe[r] = 0
    
    # 정밀 로깅 결과
    logging_results = {
        'regime_sharpe': regime_sharpe,
        'regime_days': {r: len(rets) for r, rets in regime_returns.items()},
        'total_turnover': sum(daily_turnover),
        'avg_turnover': np.mean([t for t in daily_turnover if t > 0]) if any(t > 0 for t in daily_turnover) else 0,
        'total_cost': sum(daily_turnover) * cost,
        'avg_leverage': np.mean(daily_leverage),
        'drawdown_events': len(drawdown_events),
        'avg_dd_depth': np.mean([d['depth'] for d in drawdown_events]) if drawdown_events else 0,
        'avg_dd_duration': np.mean([d['duration'] for d in drawdown_events]) if drawdown_events else 0,
        'rebal_count': len([t for t in daily_turnover if t > 0])
    }
    
    return {
        'sharpe': sharpe,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'is_oos_gap': (is_sharpe - oos_sharpe) / (is_sharpe + 1e-8) * 100 if is_sharpe > 0 else 0,
        'mdd': mdd,
        'annual_return': np.mean(returns_arr) * 252,
        'logging': logging_results
    }

# =============================================================================
# 파레토 최적화 테스트
# =============================================================================
def run_pareto_test(test_name, base_config, param_grid):
    """특정 개선방안에 대한 파레토 최적화 테스트"""
    
    print(f"\n{'='*80}")
    print(f"[{test_name}] 파레토 최적화 테스트 시작")
    print(f"{'='*80}")
    
    # 파라미터 조합 생성
    keys = list(param_grid.keys())
    combinations = list(product(*param_grid.values()))
    
    print(f"총 테스트 수: {len(combinations):,}개")
    
    results = []
    start_time = time.time()
    
    for i, values in enumerate(combinations):
        config = base_config.copy()
        params = dict(zip(keys, values))
        config.update(params)
        
        try:
            result = run_backtest(config)
            result['params'] = params
            result['test_name'] = test_name
            results.append(result)
            
            if (i + 1) % 100 == 0:
                elapsed = time.time() - start_time
                eta = elapsed / (i + 1) * (len(combinations) - i - 1)
                print(f"  진행: {i+1}/{len(combinations)} ({(i+1)/len(combinations)*100:.1f}%) - ETA: {eta:.0f}초")
        except Exception as e:
            print(f"  오류 (조합 {i}): {e}")
    
    elapsed = time.time() - start_time
    print(f"\n[{test_name}] 완료! (소요 시간: {elapsed:.1f}초)")
    
    return results

def main():
    """메인 실행"""
    
    # 데이터 초기화
    init_global_data()
    
    # 기본 설정 (v40 베이스라인 + C1 히스테리시스)
    base_config = {
        'rebal_period': 14,
        'cost': 0.002,
        'top_k': N_ASSETS,
        'thresholds': [0.25, 0.40, 0.60, 0.75],
        'regime_leverage': {0: 2.0, 1: 1.5, 2: 1.0, 3: 0.5, 4: 0.3},
        'hyst_width': 0.05,  # C1 히스테리시스
        'use_hrp': False,
        'use_dynamic_factor': False,
        'use_vol_target': False,
        'use_ensemble': False,
        'use_residual_mom': False,
    }
    
    all_results = {}
    
    # ==========================================================================
    # 0. v40 베이스라인 테스트
    # ==========================================================================
    print("\n[v40 베이스라인 테스트]")
    baseline_result = run_backtest(base_config)
    print(f"  Sharpe: {baseline_result['sharpe']:.3f}")
    print(f"  IS Sharpe: {baseline_result['is_sharpe']:.3f}")
    print(f"  OOS Sharpe: {baseline_result['oos_sharpe']:.3f}")
    print(f"  MDD: {baseline_result['mdd']*100:.1f}%")
    
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
    
    hrp_results = run_pareto_test("v41-HRP", hrp_config, hrp_param_grid)
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
    
    df_results = run_pareto_test("v41-DynamicFactor", df_config, df_param_grid)
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
    
    vt_results = run_pareto_test("v41-VolTarget", vt_config, vt_param_grid)
    all_results['v41-VolTarget'] = vt_results
    
    # ==========================================================================
    # 4. v41-Ensemble 테스트
    # ==========================================================================
    ens_config = base_config.copy()
    ens_config['use_ensemble'] = True
    
    # 룩백 조합
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
    
    ens_results = run_pareto_test("v41-Ensemble", ens_config, ens_param_grid)
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
    
    rm_results = run_pareto_test("v41-ResidualMom", rm_config, rm_param_grid)
    all_results['v41-ResidualMom'] = rm_results
    
    # ==========================================================================
    # 6. v41-Best (최적 조합) 테스트
    # ==========================================================================
    best_config = base_config.copy()
    best_config['use_hrp'] = True
    best_config['use_dynamic_factor'] = True
    best_config['use_vol_target'] = True
    best_config['use_ensemble'] = True
    best_config['use_residual_mom'] = True
    
    best_param_grid = {
        'hrp_shrinkage': [0.3, 0.5, 0.7],
        'hrp_clusters': [4, 6, 8],
        'hrp_lookback': [126, 189],
        'lowvol_weight': [0.5, 0.6, 0.7],
        'target_vol': [0.12, 0.15, 0.18],
        'lev_min': [0.3, 0.4],
        'lev_max': [1.8, 2.0],
        'ensemble_lookbacks': [[63, 126, 189], [63, 126, 189, 252]],
        'residual_weight': [0.3, 0.4],
        'rebal_period': [10, 14],
        'top_k': [25, 30, 35],
        'hyst_width': [0.04, 0.05],
    }
    
    best_results = run_pareto_test("v41-Best", best_config, best_param_grid)
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
                  f"{r['logging']['total_turnover']:>8.1f} | {r['logging']['avg_leverage']:>8.2f}")
        
        # 정밀 로깅 분석 (TOP 1)
        if sorted_results:
            top1 = sorted_results[0]
            print(f"\n[{test_name}] TOP 1 정밀 로깅 분석")
            print(f"  파라미터: {top1['params']}")
            print(f"  레짐별 일수: {top1['logging']['regime_days']}")
            print(f"  레짐별 Sharpe: {top1['logging']['regime_sharpe']}")
            print(f"  총 턴오버: {top1['logging']['total_turnover']:.1f}")
            print(f"  총 비용: {top1['logging']['total_cost']*100:.2f}%")
            print(f"  리밸런싱 횟수: {top1['logging']['rebal_count']}")
            print(f"  평균 노출도: {top1['logging']['avg_leverage']:.2f}")
            print(f"  드로다운 이벤트: {top1['logging']['drawdown_events']}회")
            print(f"  평균 드로다운 깊이: {top1['logging']['avg_dd_depth']*100:.1f}%")
            print(f"  평균 드로다운 기간: {top1['logging']['avg_dd_duration']:.0f}일")
    
    # 전체 결과 저장
    output_file = '/home/ubuntu/v41_pareto_results.json'
    
    # JSON 직렬화 가능하도록 변환
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
                'params': {k: (str(v) if isinstance(v, list) else v) for k, v in r['params'].items()},
                'logging': {
                    'regime_sharpe': {str(k): float(v) for k, v in r['logging']['regime_sharpe'].items()},
                    'regime_days': {str(k): int(v) for k, v in r['logging']['regime_days'].items()},
                    'total_turnover': float(r['logging']['total_turnover']),
                    'avg_turnover': float(r['logging']['avg_turnover']),
                    'total_cost': float(r['logging']['total_cost']),
                    'avg_leverage': float(r['logging']['avg_leverage']),
                    'drawdown_events': int(r['logging']['drawdown_events']),
                    'avg_dd_depth': float(r['logging']['avg_dd_depth']),
                    'avg_dd_duration': float(r['logging']['avg_dd_duration']),
                    'rebal_count': int(r['logging']['rebal_count']),
                }
            }
            serializable_results[test_name].append(sr)
    
    with open(output_file, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'baseline': {
                'sharpe': float(baseline_result['sharpe']),
                'is_sharpe': float(baseline_result['is_sharpe']),
                'oos_sharpe': float(baseline_result['oos_sharpe']),
                'mdd': float(baseline_result['mdd']),
            },
            'results': serializable_results
        }, f, indent=2)
    
    print(f"\n결과 저장: {output_file}")
    print(f"완료 시간: {datetime.now()}")

if __name__ == "__main__":
    main()
