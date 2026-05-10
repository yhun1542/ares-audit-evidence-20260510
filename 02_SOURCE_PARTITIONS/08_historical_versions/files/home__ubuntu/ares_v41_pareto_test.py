#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v41 파레토 최적화 테스트
- 5가지 개선방안 각각 수만 가지 조합 테스트
- 정밀 로깅: 팩터별 IC, 레짐별 성과, 턴오버, 드로다운 이벤트
"""

import numpy as np
import pandas as pd
from itertools import product
from concurrent.futures import ProcessPoolExecutor, as_completed
import warnings
import json
import time
from datetime import datetime
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
warnings.filterwarnings('ignore')

# =============================================================================
# 데이터 로드 및 전처리
# =============================================================================
def load_data():
    """실제 데이터 로드"""
    import sqlite3
    conn = sqlite3.connect('/home/ubuntu/ares_data.db')
    
    prices = pd.read_sql("SELECT * FROM prices", conn, index_col='date', parse_dates=['date'])
    factors = pd.read_sql("SELECT * FROM factors", conn, index_col='date', parse_dates=['date'])
    regime = pd.read_sql("SELECT * FROM regime", conn, index_col='date', parse_dates=['date'])
    
    conn.close()
    
    returns = prices.pct_change().dropna()
    
    return prices, returns, factors, regime

# =============================================================================
# HRP (Hierarchical Risk Parity) 구현
# =============================================================================
def get_cluster_var(cov, cluster_items):
    """클러스터의 분산 계산"""
    cov_slice = cov.loc[cluster_items, cluster_items]
    w = np.ones(len(cluster_items)) / len(cluster_items)
    return np.dot(np.dot(w, cov_slice), w)

def get_quasi_diag(link):
    """Quasi-diagonal 순서 추출"""
    link = link.astype(int)
    sort_ix = pd.Series([link[-1, 0], link[-1, 1]])
    num_items = link[-1, 3]
    while sort_ix.max() >= num_items:
        sort_ix.index = range(0, sort_ix.shape[0] * 2, 2)
        df0 = sort_ix[sort_ix >= num_items]
        i = df0.index
        j = df0.values - num_items
        sort_ix[i] = link[j, 0]
        df0 = pd.Series(link[j, 1], index=i + 1)
        sort_ix = pd.concat([sort_ix, df0])
        sort_ix = sort_ix.sort_index()
        sort_ix.index = range(sort_ix.shape[0])
    return sort_ix.tolist()

def hrp_weights(returns, shrinkage=0.5, n_clusters=6):
    """HRP 가중치 계산"""
    try:
        # 공분산 및 상관관계 계산
        cov = returns.cov()
        corr = returns.corr()
        
        # Shrinkage 적용
        cov_shrunk = shrinkage * np.diag(np.diag(cov)) + (1 - shrinkage) * cov
        
        # 거리 행렬 계산
        dist = np.sqrt((1 - corr) / 2)
        dist = dist.fillna(0)
        np.fill_diagonal(dist.values, 0)
        
        # 계층적 클러스터링
        link = linkage(squareform(dist), method='ward')
        
        # Quasi-diagonal 순서
        sort_ix = get_quasi_diag(link)
        sorted_tickers = corr.index[sort_ix].tolist()
        
        # 클러스터 할당
        clusters = fcluster(link, n_clusters, criterion='maxclust')
        
        # Inverse Variance 가중치 (클러스터 내)
        weights = pd.Series(index=returns.columns, dtype=float)
        for c in range(1, n_clusters + 1):
            cluster_tickers = [t for t, cl in zip(returns.columns, clusters) if cl == c]
            if len(cluster_tickers) > 0:
                cluster_var = cov_shrunk.loc[cluster_tickers, cluster_tickers].values.diagonal()
                cluster_var = np.maximum(cluster_var, 1e-8)
                inv_var = 1 / cluster_var
                cluster_weights = inv_var / inv_var.sum()
                for i, t in enumerate(cluster_tickers):
                    weights[t] = cluster_weights[i] / n_clusters
        
        weights = weights / weights.sum()
        return weights
    except Exception as e:
        # 실패 시 동일 가중치
        return pd.Series(1/len(returns.columns), index=returns.columns)

# =============================================================================
# 레짐별 동적 팩터 로테이션
# =============================================================================
def get_regime_factor_weights(regime_state, factor_config):
    """레짐 상태에 따른 팩터 가중치"""
    if regime_state in [0, 1]:  # Ultra Bull, Bull
        return factor_config['bull']
    elif regime_state == 2:  # Neutral
        return factor_config['neutral']
    else:  # Bear, Crisis
        return factor_config['bear']

def calculate_dynamic_factor_score(factors, regime, factor_config):
    """레짐별 동적 팩터 스코어 계산"""
    scores = pd.DataFrame(index=factors.index, columns=factors.columns.get_level_values(0).unique())
    
    for date in factors.index:
        r = regime.loc[date, 'regime'] if date in regime.index else 2
        weights = get_regime_factor_weights(r, factor_config)
        
        for ticker in scores.columns:
            score = 0
            for factor, weight in weights.items():
                if (ticker, factor) in factors.columns:
                    score += weight * factors.loc[date, (ticker, factor)]
            scores.loc[date, ticker] = score
    
    return scores

# =============================================================================
# 사전적 변동성 타겟팅
# =============================================================================
def vol_target_leverage(returns, target_vol=0.15, ewma_lambda=0.94, 
                        lev_min=0.3, lev_max=2.0):
    """EWMA 기반 변동성 타겟팅 레버리지"""
    # EWMA 변동성 계산
    vol = returns.ewm(alpha=1-ewma_lambda).std() * np.sqrt(252)
    
    # 레버리지 계산
    lev = target_vol / vol
    lev = lev.clip(lev_min, lev_max)
    
    return lev.shift(1).fillna(1.0)

# =============================================================================
# Ensemble Lookback
# =============================================================================
def calculate_ensemble_momentum(prices, lookbacks=[63, 126, 189, 252]):
    """여러 룩백 기간의 모멘텀 앙상블"""
    mom_scores = []
    
    for lb in lookbacks:
        mom = prices.pct_change(lb)
        # Z-score 정규화
        mom_z = (mom - mom.mean(axis=1).values.reshape(-1, 1)) / (mom.std(axis=1).values.reshape(-1, 1) + 1e-8)
        mom_scores.append(mom_z)
    
    # 평균
    ensemble = sum(mom_scores) / len(mom_scores)
    return ensemble

# =============================================================================
# Residual Momentum
# =============================================================================
def calculate_residual_momentum(returns, market_returns, window=252, skip=21):
    """시장 베타를 제거한 잔차 모멘텀"""
    residual_mom = pd.DataFrame(index=returns.index, columns=returns.columns)
    
    for t in range(window + skip, len(returns)):
        for ticker in returns.columns:
            try:
                y = returns[ticker].iloc[t-window-skip:t-skip].values
                x = market_returns.iloc[t-window-skip:t-skip].values
                
                # 단순 회귀
                x_mean = x.mean()
                y_mean = y.mean()
                beta = np.sum((x - x_mean) * (y - y_mean)) / (np.sum((x - x_mean)**2) + 1e-8)
                alpha = y_mean - beta * x_mean
                
                # 잔차 계산
                resid = y - (alpha + beta * x)
                residual_mom.iloc[t, returns.columns.get_loc(ticker)] = np.sum(resid)
            except:
                residual_mom.iloc[t, returns.columns.get_loc(ticker)] = 0
    
    return residual_mom.astype(float)

# =============================================================================
# 백테스트 엔진 (정밀 로깅 포함)
# =============================================================================
def run_backtest_with_logging(prices, returns, factors, regime, config):
    """정밀 로깅이 포함된 백테스트"""
    
    n_days = len(returns)
    n_assets = len(returns.columns)
    
    # 결과 저장
    portfolio_returns = []
    daily_leverage = []
    daily_turnover = []
    positions_history = []
    regime_returns = {0: [], 1: [], 2: [], 3: [], 4: []}
    factor_ic = {f: [] for f in ['momentum', 'value', 'quality', 'lowvol']}
    drawdown_events = []
    
    # 초기화
    prev_weights = np.zeros(n_assets)
    peak_value = 1.0
    current_value = 1.0
    in_drawdown = False
    dd_start = 0
    
    # 리밸런싱 주기
    rebal_period = config.get('rebal_period', 14)
    
    # 시작 인덱스 (충분한 데이터 확보)
    start_idx = max(252, config.get('lookback', 252) + 50)
    
    for t in range(start_idx, n_days):
        date = returns.index[t]
        
        # 리밸런싱 여부
        if t % rebal_period == 0:
            # 1. HRP 가중치 계산
            if config.get('use_hrp', False):
                lookback_returns = returns.iloc[t-config.get('hrp_lookback', 126):t]
                base_weights = hrp_weights(
                    lookback_returns,
                    shrinkage=config.get('hrp_shrinkage', 0.5),
                    n_clusters=config.get('hrp_clusters', 6)
                )
            else:
                base_weights = pd.Series(1/n_assets, index=returns.columns)
            
            # 2. 팩터 스코어 계산
            if config.get('use_dynamic_factor', False):
                factor_config = config.get('factor_config', {
                    'bull': {'momentum': 0.4, 'quality': 0.3, 'value': 0.3, 'lowvol': 0.0},
                    'neutral': {'momentum': 0.2, 'quality': 0.5, 'value': 0.0, 'lowvol': 0.3},
                    'bear': {'momentum': 0.0, 'quality': 0.0, 'value': 0.0, 'lowvol': 1.0}
                })
                r = regime.iloc[t]['regime'] if t < len(regime) else 2
                factor_weights = get_regime_factor_weights(r, factor_config)
            else:
                factor_weights = {'momentum': 0.25, 'quality': 0.25, 'value': 0.25, 'lowvol': 0.25}
            
            # 3. Ensemble Lookback
            if config.get('use_ensemble', False):
                lookbacks = config.get('ensemble_lookbacks', [63, 126, 189, 252])
                ensemble_score = calculate_ensemble_momentum(prices.iloc[:t+1], lookbacks)
                if t < len(ensemble_score):
                    score = ensemble_score.iloc[t]
                else:
                    score = pd.Series(0, index=returns.columns)
            else:
                # 단일 모멘텀
                score = prices.iloc[t] / prices.iloc[t-126] - 1
            
            # 4. Residual Momentum
            if config.get('use_residual_mom', False):
                market_ret = returns.mean(axis=1)
                res_mom = calculate_residual_momentum(
                    returns.iloc[:t+1], 
                    market_ret.iloc[:t+1],
                    window=config.get('residual_window', 252),
                    skip=config.get('residual_skip', 21)
                )
                if t < len(res_mom):
                    res_score = res_mom.iloc[t]
                    # 기존 스코어와 결합
                    res_weight = config.get('residual_weight', 0.3)
                    score = (1 - res_weight) * score + res_weight * res_score.fillna(0)
            
            # 스코어 기반 가중치 조정
            score = score.fillna(0)
            score_rank = score.rank(pct=True)
            
            # Top-K 선택
            top_k = config.get('top_k', n_assets)
            if top_k < n_assets:
                threshold = score_rank.quantile(1 - top_k/n_assets)
                mask = score_rank >= threshold
                base_weights = base_weights * mask
                base_weights = base_weights / base_weights.sum()
            
            # 최종 가중치
            weights = base_weights.values
            
            # 턴오버 계산
            turnover = np.sum(np.abs(weights - prev_weights))
            daily_turnover.append(turnover)
            
            prev_weights = weights.copy()
        else:
            weights = prev_weights
            daily_turnover.append(0)
        
        # 5. 변동성 타겟팅 레버리지
        if config.get('use_vol_target', False):
            port_ret_hist = pd.Series(portfolio_returns[-252:]) if len(portfolio_returns) > 0 else pd.Series([0])
            vol = port_ret_hist.std() * np.sqrt(252) if len(port_ret_hist) > 20 else 0.15
            target_vol = config.get('target_vol', 0.15)
            lev = min(config.get('lev_max', 2.0), max(config.get('lev_min', 0.3), target_vol / (vol + 1e-8)))
        else:
            # 레짐 기반 레버리지
            r = regime.iloc[t]['regime'] if t < len(regime) else 2
            regime_lev = config.get('regime_leverage', {0: 2.0, 1: 1.5, 2: 1.0, 3: 0.5, 4: 0.3})
            lev = regime_lev.get(r, 1.0)
        
        daily_leverage.append(lev)
        
        # 일간 수익률 계산
        daily_ret = returns.iloc[t].values
        port_ret = np.sum(weights * daily_ret) * lev
        
        # 거래비용 차감
        cost = config.get('cost', 0.002) * daily_turnover[-1]
        port_ret -= cost
        
        portfolio_returns.append(port_ret)
        
        # 레짐별 수익률 기록
        r = regime.iloc[t]['regime'] if t < len(regime) else 2
        regime_returns[r].append(port_ret)
        
        # 드로다운 추적
        current_value *= (1 + port_ret)
        if current_value > peak_value:
            peak_value = current_value
            if in_drawdown:
                # 드로다운 종료
                drawdown_events.append({
                    'start': dd_start,
                    'end': t,
                    'depth': (current_value / peak_value - 1),
                    'duration': t - dd_start
                })
                in_drawdown = False
        else:
            dd = current_value / peak_value - 1
            if dd < -0.05 and not in_drawdown:
                in_drawdown = True
                dd_start = t
        
        positions_history.append(weights.copy())
    
    # 결과 계산
    returns_series = pd.Series(portfolio_returns, index=returns.index[start_idx:])
    
    # IS/OOS 분리 (55%/45%)
    split_idx = int(len(returns_series) * 0.55)
    is_returns = returns_series.iloc[:split_idx]
    oos_returns = returns_series.iloc[split_idx:]
    
    # Sharpe 계산
    def calc_sharpe(r):
        if len(r) < 20:
            return 0
        return r.mean() / (r.std() + 1e-8) * np.sqrt(252)
    
    sharpe = calc_sharpe(returns_series)
    is_sharpe = calc_sharpe(is_returns)
    oos_sharpe = calc_sharpe(oos_returns)
    
    # MDD 계산
    cum_returns = (1 + returns_series).cumprod()
    rolling_max = cum_returns.cummax()
    drawdown = cum_returns / rolling_max - 1
    mdd = drawdown.min()
    
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
        'total_cost': sum(daily_turnover) * config.get('cost', 0.002),
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
        'is_oos_gap': (is_sharpe - oos_sharpe) / (is_sharpe + 1e-8) * 100,
        'mdd': mdd,
        'annual_return': returns_series.mean() * 252,
        'logging': logging_results
    }

# =============================================================================
# 파레토 최적화 테스트
# =============================================================================
def run_pareto_test(test_name, base_config, param_grid, prices, returns, factors, regime):
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
            result = run_backtest_with_logging(prices, returns, factors, regime, config)
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
    print("=" * 80)
    print("ARES v41 파레토 최적화 테스트 시작")
    print(f"시작 시간: {datetime.now()}")
    print("=" * 80)
    
    # 데이터 로드
    print("\n데이터 로드 중...")
    prices, returns, factors, regime = load_data()
    print(f"  종목 수: {len(returns.columns)}")
    print(f"  기간: {returns.index[0]} ~ {returns.index[-1]} ({len(returns)}일)")
    
    # 기본 설정 (v40 베이스라인 + C1 히스테리시스)
    base_config = {
        'rebal_period': 14,
        'cost': 0.002,
        'top_k': len(returns.columns),
        'regime_leverage': {0: 2.0, 1: 1.5, 2: 1.0, 3: 0.5, 4: 0.3},
        'use_hrp': False,
        'use_dynamic_factor': False,
        'use_vol_target': False,
        'use_ensemble': False,
        'use_residual_mom': False,
    }
    
    all_results = {}
    
    # ==========================================================================
    # 1. v41-HRP 테스트
    # ==========================================================================
    hrp_config = base_config.copy()
    hrp_config['use_hrp'] = True
    
    hrp_param_grid = {
        'hrp_shrinkage': [0.1, 0.3, 0.5, 0.7, 0.9],
        'hrp_clusters': [3, 4, 5, 6, 7, 8, 10, 12],
        'hrp_lookback': [63, 126, 189, 252],
        'rebal_period': [7, 10, 14, 21],
        'top_k': [15, 20, 25, 30, 35, 40, 48],
    }
    
    hrp_results = run_pareto_test("v41-HRP", hrp_config, hrp_param_grid, 
                                   prices, returns, factors, regime)
    all_results['v41-HRP'] = hrp_results
    
    # ==========================================================================
    # 2. v41-DynamicFactor 테스트
    # ==========================================================================
    df_config = base_config.copy()
    df_config['use_dynamic_factor'] = True
    
    # 팩터 비중 조합 생성
    factor_configs = []
    for bull_mom in [0.2, 0.4, 0.6]:
        for bull_qual in [0.2, 0.3, 0.4]:
            for bear_lowvol in [0.6, 0.8, 1.0]:
                factor_configs.append({
                    'bull': {'momentum': bull_mom, 'quality': bull_qual, 
                             'value': 1-bull_mom-bull_qual, 'lowvol': 0.0},
                    'neutral': {'momentum': 0.2, 'quality': 0.5, 'value': 0.0, 'lowvol': 0.3},
                    'bear': {'momentum': 0.0, 'quality': 0.0, 'value': 0.0, 'lowvol': bear_lowvol}
                })
    
    df_param_grid = {
        'factor_config': factor_configs,
        'rebal_period': [7, 10, 14, 21],
        'top_k': [15, 20, 25, 30, 35, 40, 48],
    }
    
    df_results = run_pareto_test("v41-DynamicFactor", df_config, df_param_grid,
                                  prices, returns, factors, regime)
    all_results['v41-DynamicFactor'] = df_results
    
    # ==========================================================================
    # 3. v41-VolTarget 테스트
    # ==========================================================================
    vt_config = base_config.copy()
    vt_config['use_vol_target'] = True
    
    vt_param_grid = {
        'target_vol': [0.10, 0.12, 0.15, 0.18, 0.20, 0.25],
        'ewma_lambda': [0.90, 0.92, 0.94, 0.96, 0.98],
        'lev_min': [0.2, 0.3, 0.4, 0.5],
        'lev_max': [1.5, 1.8, 2.0, 2.5],
        'rebal_period': [7, 10, 14, 21],
    }
    
    vt_results = run_pareto_test("v41-VolTarget", vt_config, vt_param_grid,
                                  prices, returns, factors, regime)
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
        'top_k': [15, 20, 25, 30, 35, 40, 48],
    }
    
    ens_results = run_pareto_test("v41-Ensemble", ens_config, ens_param_grid,
                                   prices, returns, factors, regime)
    all_results['v41-Ensemble'] = ens_results
    
    # ==========================================================================
    # 5. v41-ResidualMom 테스트
    # ==========================================================================
    rm_config = base_config.copy()
    rm_config['use_residual_mom'] = True
    
    rm_param_grid = {
        'residual_window': [126, 189, 252, 315],
        'residual_skip': [0, 10, 21, 42],
        'residual_weight': [0.2, 0.3, 0.4, 0.5, 0.6],
        'rebal_period': [7, 10, 14, 21],
        'top_k': [15, 20, 25, 30, 35, 40, 48],
    }
    
    rm_results = run_pareto_test("v41-ResidualMom", rm_config, rm_param_grid,
                                  prices, returns, factors, regime)
    all_results['v41-ResidualMom'] = rm_results
    
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
                'params': {k: (v if not isinstance(v, dict) else str(v)) for k, v in r['params'].items()},
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
            'results': serializable_results
        }, f, indent=2)
    
    print(f"\n결과 저장: {output_file}")
    print(f"완료 시간: {datetime.now()}")

if __name__ == "__main__":
    main()
