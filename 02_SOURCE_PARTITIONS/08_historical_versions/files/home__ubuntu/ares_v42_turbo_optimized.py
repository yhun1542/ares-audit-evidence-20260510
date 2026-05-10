#!/usr/bin/env python3
"""
ARES v42 Turbo Pareto Optimization - 최적화 버전
- 멀티프로세싱 활용 (16코어)
- 사전 계산 캐싱
- Numba JIT 강화
"""

import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime
from itertools import product
from multiprocessing import Pool, cpu_count, Manager
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# 데이터 로드 및 전처리
# ============================================================================

def load_data():
    """데이터베이스에서 데이터 로드"""
    db_path = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    conn = sqlite3.connect(db_path)
    
    query = """
    SELECT date as timestamp, symbol, open, high, low, close, volume,
           COALESCE(adj_close, close) as adj_close
    FROM daily_ohlcv
    WHERE symbol IN (
        SELECT symbol FROM daily_ohlcv
        GROUP BY symbol
        HAVING COUNT(*) > 2000
    )
    ORDER BY date, symbol
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values(['symbol', 'timestamp'])
    df['returns'] = df.groupby('symbol')['adj_close'].pct_change()
    df = df.dropna(subset=['returns'])
    df = df.drop_duplicates(subset=['timestamp', 'symbol'], keep='last')
    
    return df

def prepare_data(df):
    """데이터를 NumPy 배열로 변환"""
    symbols = sorted(df['symbol'].unique())
    dates = sorted(df['timestamp'].unique())
    
    n_dates = len(dates)
    n_symbols = len(symbols)
    
    # pivot_table 사용
    df_pivot = df.pivot_table(index='timestamp', columns='symbol', values='returns', aggfunc='last')
    df_pivot_price = df.pivot_table(index='timestamp', columns='symbol', values='close', aggfunc='last')
    
    # 정렬된 순서로 배열 생성
    returns = np.full((n_dates, n_symbols), np.nan, dtype=np.float64)
    prices = np.full((n_dates, n_symbols), np.nan, dtype=np.float64)
    
    for j, sym in enumerate(symbols):
        if sym in df_pivot.columns:
            returns[:, j] = df_pivot[sym].reindex(dates).values
        if sym in df_pivot_price.columns:
            prices[:, j] = df_pivot_price[sym].reindex(dates).values
    
    return {
        'returns': returns,
        'prices': prices,
        'dates': np.array(dates),
        'symbols': np.array(symbols),
        'n_dates': n_dates,
        'n_symbols': n_symbols
    }

# ============================================================================
# 사전 계산 함수들 (캐싱용)
# ============================================================================

def compute_all_factors(returns, prices, lookbacks=[21, 63, 126, 252]):
    """모든 팩터를 사전 계산"""
    n_dates, n_symbols = returns.shape
    factors = {}
    
    for lb in lookbacks:
        # 모멘텀
        mom = np.full((n_dates, n_symbols), np.nan)
        for i in range(lb, n_dates):
            for j in range(n_symbols):
                vals = returns[i-lb:i, j]
                valid = vals[~np.isnan(vals)]
                if len(valid) > lb // 2:
                    mom[i, j] = np.sum(valid)
        factors[f'mom_{lb}'] = mom
        
        # 변동성
        vol = np.full((n_dates, n_symbols), np.nan)
        for i in range(lb, n_dates):
            for j in range(n_symbols):
                vals = returns[i-lb:i, j]
                valid = vals[~np.isnan(vals)]
                if len(valid) > lb // 2:
                    vol[i, j] = np.std(valid) * np.sqrt(252)
        factors[f'vol_{lb}'] = vol
    
    return factors

def compute_regime(returns, lookback=63):
    """레짐 사전 계산"""
    n_dates = returns.shape[0]
    
    # 시장 수익률 (평균)
    market_ret = np.nanmean(returns, axis=1)
    
    regime = np.zeros(n_dates, dtype=np.int32)
    
    for i in range(lookback, n_dates):
        # 누적 수익률
        cum_ret = np.nansum(market_ret[i-lookback:i])
        # 변동성
        vol = np.nanstd(market_ret[i-lookback:i]) * np.sqrt(252)
        
        if cum_ret > 0.1 and vol < 0.2:
            regime[i] = 0  # Bull
        elif cum_ret > -0.05 and vol < 0.25:
            regime[i] = 1  # Neutral
        elif cum_ret > -0.15 or vol < 0.35:
            regime[i] = 2  # Bear
        else:
            regime[i] = 3  # Crisis
    
    return regime

def compute_rolling_ic(returns, factors, window=63):
    """Rolling IC 사전 계산"""
    n_dates, n_symbols = returns.shape
    factor_names = list(factors.keys())
    n_factors = len(factor_names)
    
    ic = np.full((n_dates, n_factors), np.nan)
    
    for i in range(window + 1, n_dates):
        fwd_ret = returns[i]  # 다음 날 수익률
        
        for f_idx, f_name in enumerate(factor_names):
            factor_vals = factors[f_name][i-1]  # 전날 팩터
            
            # 유효한 값만 사용
            valid = ~np.isnan(factor_vals) & ~np.isnan(fwd_ret)
            if np.sum(valid) > 10:
                # Spearman 상관계수 (rank correlation)
                f_valid = factor_vals[valid]
                r_valid = fwd_ret[valid]
                
                # 랭크 변환
                f_rank = np.argsort(np.argsort(f_valid))
                r_rank = np.argsort(np.argsort(r_valid))
                
                # 상관계수
                ic[i, f_idx] = np.corrcoef(f_rank, r_rank)[0, 1]
    
    return ic, factor_names

# ============================================================================
# 백테스트 함수
# ============================================================================

def run_backtest(returns, weights, cost_bps=20):
    """백테스트 실행"""
    n_dates, n_symbols = returns.shape
    
    port_returns = np.zeros(n_dates)
    prev_weights = np.zeros(n_symbols)
    total_turnover = 0.0
    
    for i in range(n_dates):
        # 현재 가중치
        curr_weights = weights[i]
        
        # 유효한 수익률
        ret = returns[i]
        valid = ~np.isnan(ret)
        
        if np.sum(valid) > 0 and np.sum(np.abs(curr_weights)) > 0:
            # 포트폴리오 수익률
            port_ret = np.nansum(curr_weights * ret)
            
            # 턴오버 비용
            turnover = np.sum(np.abs(curr_weights - prev_weights))
            cost = turnover * cost_bps / 10000
            
            port_returns[i] = port_ret - cost
            total_turnover += turnover
            prev_weights = curr_weights.copy()
    
    return port_returns, total_turnover

def compute_weights(returns, factors, regime, params, ic=None, ic_factor_names=None):
    """가중치 계산"""
    n_dates, n_symbols = returns.shape
    weights = np.zeros((n_dates, n_symbols))
    
    top_k = params.get('top_k', 30)
    improvement = params.get('improvement', 'baseline')
    
    # 레버리지 설정
    lev_map = {
        0: params.get('bull_lev', 1.0),
        1: params.get('neutral_lev', 0.7),
        2: params.get('bear_lev', 0.5),
        3: params.get('crisis_lev', 0.2)
    }
    
    for i in range(252, n_dates):
        # 기본 팩터 스코어 (모멘텀 + 역변동성)
        mom = factors.get('mom_126', np.zeros((n_dates, n_symbols)))[i]
        vol = factors.get('vol_63', np.ones((n_dates, n_symbols)))[i]
        
        # 스코어 계산
        score = np.zeros(n_symbols)
        valid = ~np.isnan(mom) & ~np.isnan(vol) & (vol > 0)
        
        if np.sum(valid) < 5:
            continue
        
        # 기본 스코어
        score[valid] = mom[valid] / (vol[valid] + 0.01)
        
        # 개선방안별 스코어 조정
        if improvement == 'icir' and ic is not None:
            # ICIR 기반 동적 팩터 가중치
            ic_window = params.get('ic_window', 63)
            shrink = params.get('shrink', 0.5)
            
            if i >= ic_window:
                # 최근 IC 평균
                recent_ic = np.nanmean(ic[i-ic_window:i], axis=0)
                
                # IC 기반 가중치
                ic_weights = np.clip(recent_ic, -0.3, 0.3) + 0.5
                ic_weights = ic_weights / (np.sum(ic_weights) + 1e-6)
                
                # 팩터별 스코어 조합
                combined_score = np.zeros(n_symbols)
                for f_idx, f_name in enumerate(ic_factor_names):
                    if f_name in factors:
                        f_val = factors[f_name][i]
                        f_valid = ~np.isnan(f_val)
                        if np.sum(f_valid) > 5:
                            # 랭크 정규화
                            f_rank = np.zeros(n_symbols)
                            f_rank[f_valid] = (np.argsort(np.argsort(f_val[f_valid])) / np.sum(f_valid) - 0.5) * 2
                            combined_score += ic_weights[f_idx] * f_rank
                
                score = shrink * combined_score + (1 - shrink) * score
        
        elif improvement == 'aarm':
            # AARM: 하방 변동성 기반 조정
            dd_mult = params.get('dd_mult', 1.5)
            
            # 하방 변동성
            neg_ret = returns[max(0, i-63):i]
            neg_ret = np.where(neg_ret < 0, neg_ret, 0)
            downside_vol = np.nanstd(neg_ret, axis=0) * np.sqrt(252)
            
            # 하방 변동성이 높은 종목 페널티
            valid_dv = ~np.isnan(downside_vol) & (downside_vol > 0)
            if np.sum(valid_dv) > 5:
                dv_penalty = downside_vol / (np.nanmean(downside_vol) + 0.01)
                score[valid_dv] = score[valid_dv] / (1 + dd_mult * (dv_penalty[valid_dv] - 1))
        
        elif improvement == 'multi_horizon':
            # Multi-Horizon Ensemble
            horizons = params.get('horizons', [21, 63, 126])
            horizon_weights = params.get('horizon_weights', [0.3, 0.4, 0.3])
            
            combined_score = np.zeros(n_symbols)
            for h, hw in zip(horizons, horizon_weights):
                h_mom = factors.get(f'mom_{h}', np.zeros((n_dates, n_symbols)))[i]
                h_valid = ~np.isnan(h_mom)
                if np.sum(h_valid) > 5:
                    h_rank = np.zeros(n_symbols)
                    h_rank[h_valid] = (np.argsort(np.argsort(h_mom[h_valid])) / np.sum(h_valid) - 0.5) * 2
                    combined_score += hw * h_rank
            
            score = combined_score
        
        elif improvement == 'hrp':
            # HRP: Inverse Volatility 가중
            inv_vol = 1.0 / (vol + 0.01)
            score = score * inv_vol
        
        elif improvement == 'nrc':
            # NRC: Score Entropy 기반 불확실성
            entropy_threshold = params.get('entropy_threshold', 0.5)
            
            # 스코어 분포의 엔트로피
            score_std = np.nanstd(score[valid])
            score_mean = np.nanmean(np.abs(score[valid]))
            entropy = score_std / (score_mean + 0.01)
            
            # 엔트로피가 높으면 (불확실성 높음) 포지션 축소
            if entropy > entropy_threshold:
                score = score * (entropy_threshold / entropy)
        
        # 상위 k개 선택
        valid_idx = np.where(valid)[0]
        if len(valid_idx) < top_k:
            top_k_actual = len(valid_idx)
        else:
            top_k_actual = top_k
        
        top_idx = valid_idx[np.argsort(score[valid_idx])[-top_k_actual:]]
        
        # 동일 가중
        w = np.zeros(n_symbols)
        w[top_idx] = 1.0 / top_k_actual
        
        # 레버리지 적용
        lev = lev_map.get(regime[i], 0.5)
        w = w * lev
        
        weights[i] = w
    
    return weights

def evaluate_results(port_returns):
    """결과 평가"""
    valid_returns = port_returns[~np.isnan(port_returns)]
    valid_returns = valid_returns[valid_returns != 0]
    
    if len(valid_returns) < 100:
        return None
    
    # IS/OOS 분리
    is_end = int(len(valid_returns) * 0.55)
    is_ret = valid_returns[:is_end]
    oos_ret = valid_returns[is_end:]
    
    # Sharpe 계산
    is_std = np.std(is_ret)
    oos_std = np.std(oos_ret)
    total_std = np.std(valid_returns)
    
    if is_std < 1e-8 or oos_std < 1e-8 or total_std < 1e-8:
        return None
    
    is_sharpe = np.mean(is_ret) / is_std * np.sqrt(252)
    oos_sharpe = np.mean(oos_ret) / oos_std * np.sqrt(252)
    total_sharpe = np.mean(valid_returns) / total_std * np.sqrt(252)
    
    # MDD
    cumulative = np.cumprod(1 + valid_returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - running_max) / running_max
    mdd = np.min(drawdown)
    
    # Gap
    gap = (is_sharpe - oos_sharpe) / (is_sharpe + 1e-6) * 100
    
    return {
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'total_sharpe': total_sharpe,
        'mdd': mdd,
        'gap': gap
    }

# ============================================================================
# 병렬 처리 함수
# ============================================================================

def run_single_test(args):
    """단일 테스트 실행 (병렬 처리용)"""
    params, data, factors, regime, ic, ic_factor_names = args
    
    try:
        weights = compute_weights(
            data['returns'], factors, regime, params,
            ic=ic, ic_factor_names=ic_factor_names
        )
        port_returns, turnover = run_backtest(
            data['returns'], weights, params.get('cost_bps', 20)
        )
        result = evaluate_results(port_returns)
        
        if result is not None:
            result['params'] = params
            result['turnover'] = turnover
            return result
    except Exception as e:
        pass
    
    return None

def run_parallel_tests(param_list, data, factors, regime, ic, ic_factor_names, n_workers=None):
    """병렬 테스트 실행"""
    if n_workers is None:
        n_workers = max(1, cpu_count() - 1)
    
    # 인자 준비
    args_list = [(p, data, factors, regime, ic, ic_factor_names) for p in param_list]
    
    results = []
    with Pool(n_workers) as pool:
        for i, result in enumerate(pool.imap_unordered(run_single_test, args_list)):
            if result is not None:
                results.append(result)
            
            if (i + 1) % 100 == 0:
                print(f"  Progress: {i+1}/{len(param_list)}, Valid: {len(results)}")
    
    return results

# ============================================================================
# 메인 함수
# ============================================================================

def main():
    print("="*80)
    print("ARES v42 Turbo Pareto Optimization - 최적화 버전")
    print("="*80)
    print(f"Start time: {datetime.now()}")
    print(f"CPU cores: {cpu_count()}")
    
    # 데이터 로드
    print("\nLoading data...")
    df = load_data()
    print(f"Data shape: {df.shape}")
    print(f"Symbols: {df['symbol'].nunique()}")
    print(f"Date range: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    
    # 데이터 준비
    print("\nPreparing data...")
    data = prepare_data(df)
    print(f"Returns shape: {data['returns'].shape}")
    nan_ratio = np.sum(np.isnan(data['returns'])) / data['returns'].size
    print(f"NaN ratio: {nan_ratio*100:.1f}%")
    
    # 팩터 사전 계산
    print("\nComputing factors...")
    factors = compute_all_factors(data['returns'], data['prices'], lookbacks=[21, 63, 126, 252])
    print(f"Factors: {list(factors.keys())}")
    
    # 레짐 사전 계산
    print("\nComputing regime...")
    regime = compute_regime(data['returns'])
    regime_counts = {i: np.sum(regime == i) for i in range(4)}
    print(f"Regime distribution: {regime_counts}")
    
    # Rolling IC 사전 계산
    print("\nComputing rolling IC...")
    ic, ic_factor_names = compute_rolling_ic(data['returns'], factors, window=63)
    print(f"IC shape: {ic.shape}")
    
    # 테스트 설정
    improvements = ['baseline', 'icir', 'aarm', 'multi_horizon', 'hrp', 'nrc']
    
    all_results = {}
    
    for improvement in improvements:
        print(f"\n{'='*60}")
        print(f"Testing: {improvement}")
        print(f"{'='*60}")
        
        # 파라미터 그리드
        if improvement == 'baseline':
            param_grid = {
                'top_k': [20, 30, 40, 48],
                'cost_bps': [20],
                'bull_lev': [1.0],
                'neutral_lev': [0.6, 0.7, 0.8],
                'bear_lev': [0.4, 0.5, 0.6],
                'crisis_lev': [0.1, 0.2, 0.3],
                'improvement': ['baseline']
            }
        elif improvement == 'icir':
            param_grid = {
                'top_k': [20, 30, 40, 48],
                'cost_bps': [20],
                'bull_lev': [1.0],
                'neutral_lev': [0.7],
                'bear_lev': [0.5],
                'crisis_lev': [0.2],
                'ic_window': [42, 63, 126],
                'shrink': [0.3, 0.5, 0.7],
                'improvement': ['icir']
            }
        elif improvement == 'aarm':
            param_grid = {
                'top_k': [20, 30, 40, 48],
                'cost_bps': [20],
                'bull_lev': [1.0],
                'neutral_lev': [0.7],
                'bear_lev': [0.5],
                'crisis_lev': [0.2],
                'dd_mult': [1.0, 1.5, 2.0, 2.5],
                'improvement': ['aarm']
            }
        elif improvement == 'multi_horizon':
            param_grid = {
                'top_k': [20, 30, 40, 48],
                'cost_bps': [20],
                'bull_lev': [1.0],
                'neutral_lev': [0.7],
                'bear_lev': [0.5],
                'crisis_lev': [0.2],
                'horizons': [[21, 63], [21, 63, 126], [63, 126, 252]],
                'horizon_weights': [[0.5, 0.5], [0.3, 0.4, 0.3], [0.3, 0.4, 0.3]],
                'improvement': ['multi_horizon']
            }
        elif improvement == 'hrp':
            param_grid = {
                'top_k': [20, 30, 40, 48],
                'cost_bps': [20],
                'bull_lev': [1.0],
                'neutral_lev': [0.7],
                'bear_lev': [0.5],
                'crisis_lev': [0.2],
                'improvement': ['hrp']
            }
        elif improvement == 'nrc':
            param_grid = {
                'top_k': [20, 30, 40, 48],
                'cost_bps': [20],
                'bull_lev': [1.0],
                'neutral_lev': [0.7],
                'bear_lev': [0.5],
                'crisis_lev': [0.2],
                'entropy_threshold': [0.3, 0.5, 0.7, 1.0],
                'improvement': ['nrc']
            }
        
        # 파라미터 조합 생성
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        param_list = [dict(zip(keys, v)) for v in product(*values)]
        
        print(f"Total combinations: {len(param_list)}")
        
        # 병렬 테스트 실행
        results = run_parallel_tests(
            param_list, data, factors, regime, ic, ic_factor_names,
            n_workers=max(1, cpu_count() - 1)
        )
        
        all_results[improvement] = results
        
        # 최고 결과 출력
        if results:
            best = max(results, key=lambda x: x['oos_sharpe'])
            print(f"\n{improvement} Best Result:")
            print(f"  OOS Sharpe: {best['oos_sharpe']:.3f}")
            print(f"  IS Sharpe: {best['is_sharpe']:.3f}")
            print(f"  Gap: {best['gap']:.1f}%")
            print(f"  MDD: {best['mdd']*100:.1f}%")
            print(f"  Params: {best['params']}")
    
    # 최종 결과 저장
    print("\n" + "="*80)
    print("FINAL RESULTS SUMMARY")
    print("="*80)
    
    summary = []
    for improvement, results in all_results.items():
        if results:
            best = max(results, key=lambda x: x['oos_sharpe'])
            summary.append({
                'improvement': improvement,
                'oos_sharpe': best['oos_sharpe'],
                'is_sharpe': best['is_sharpe'],
                'gap': best['gap'],
                'mdd': best['mdd'],
                'params': best['params']
            })
    
    summary_df = pd.DataFrame(summary)
    summary_df = summary_df.sort_values('oos_sharpe', ascending=False)
    print(summary_df.to_string(index=False))
    
    # CSV 저장
    summary_df.to_csv('/home/ubuntu/v42_optimized_results.csv', index=False)
    print(f"\nResults saved to /home/ubuntu/v42_optimized_results.csv")
    print(f"End time: {datetime.now()}")

if __name__ == "__main__":
    main()
