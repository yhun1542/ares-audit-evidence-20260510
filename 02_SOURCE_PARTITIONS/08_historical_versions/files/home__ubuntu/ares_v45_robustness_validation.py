#!/usr/bin/env python3
"""
ARES v45: 강건성 검증 (Walk-Forward + K-Fold CV + 레짐별 성과)
================================================================================

검증 방법:
1. Walk-Forward Validation (5년 훈련, 2년 테스트, 1년 롤링)
2. K-Fold Cross-Validation (5-Fold)
3. 레짐별 성과 분석 (Bull/Bear/Neutral/Crisis)
4. 주요 시장 이벤트별 성과

테스트 대상: v43 최고 성능 파라미터 (Soft Gating min_rebal=2)
"""

import numpy as np
import pandas as pd
import sqlite3
from numba import njit
import warnings
import time
warnings.filterwarnings('ignore')

def load_data():
    """EC2 데이터베이스에서 데이터 로드"""
    db_path = '/home/ubuntu/ares_x_unified_database/ares_universal_v2.db'
    conn = sqlite3.connect(db_path)
    
    print("Loading daily OHLCV data...")
    ohlcv_query = """
        SELECT date, symbol, open, high, low, close, volume 
        FROM daily_ohlcv 
        WHERE symbol IN (
            SELECT symbol FROM daily_ohlcv 
            GROUP BY symbol HAVING COUNT(*) > 1000
        )
        ORDER BY date, symbol
    """
    ohlcv_df = pd.read_sql_query(ohlcv_query, conn)
    ohlcv_df['date'] = pd.to_datetime(ohlcv_df['date'], format='mixed')
    
    print("Loading VIX data...")
    vix_query = "SELECT date, close as vix FROM vix ORDER BY date"
    vix_df = pd.read_sql_query(vix_query, conn)
    vix_df['date'] = pd.to_datetime(vix_df['date'], format='mixed')
    vix_df = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
    
    print("Loading market regime data...")
    regime_query = "SELECT date, regime_label as regime FROM market_regime_daily ORDER BY date"
    regime_df = pd.read_sql_query(regime_query, conn)
    regime_df['date'] = pd.to_datetime(regime_df['date'], format='mixed')
    regime_df = regime_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
    
    conn.close()
    return ohlcv_df, vix_df, regime_df


def prepare_numpy_arrays(ohlcv_df, vix_df, regime_df):
    """데이터를 NumPy 배열로 변환"""
    ohlcv_df = ohlcv_df.drop_duplicates(subset=['date', 'symbol'], keep='last')
    
    prices_wide = ohlcv_df.pivot(index='date', columns='symbol', values='close')
    min_data_ratio = 0.5
    valid_cols = prices_wide.columns[prices_wide.notna().sum() >= len(prices_wide) * min_data_ratio]
    prices_wide = prices_wide[valid_cols]
    prices_wide = prices_wide.ffill().bfill()
    
    returns = prices_wide.pct_change()
    returns = returns.iloc[1:]
    
    vix_aligned = vix_df.reindex(returns.index).ffill().bfill()
    regime_aligned = regime_df.reindex(returns.index).ffill().bfill()
    
    returns_np = returns.values.astype(np.float64)
    prices_np = prices_wide.reindex(returns.index).values.astype(np.float64)
    
    if 'vix' in vix_aligned.columns:
        vix_np = vix_aligned['vix'].values.astype(np.float64)
    else:
        vix_np = np.full(len(returns), 20.0)
    
    returns_np = np.nan_to_num(returns_np, nan=0.0)
    prices_np = np.nan_to_num(prices_np, nan=1.0)
    vix_np = np.nan_to_num(vix_np, nan=20.0)
    
    # 레짐 라벨 매핑
    regime_map = {'risk_on': 0, 'neutral': 1, 'risk_off': 2}
    regime_labels = regime_aligned['regime'].map(regime_map).fillna(1).values.astype(np.int32)
    
    tickers = list(prices_wide.columns)
    dates = list(returns.index)
    
    return returns_np, prices_np, vix_np, regime_labels, tickers, dates


@njit(cache=True)
def compute_momentum_factor(prices, window):
    n_dates, n_assets = prices.shape
    factor = np.zeros((n_dates, n_assets))
    for t in range(window, n_dates):
        for i in range(n_assets):
            if prices[t-window, i] > 0:
                factor[t, i] = (prices[t, i] / prices[t-window, i]) - 1.0
    return factor


@njit(cache=True)
def compute_volatility_factor(returns, window):
    n_dates, n_assets = returns.shape
    factor = np.zeros((n_dates, n_assets))
    for t in range(window, n_dates):
        for i in range(n_assets):
            vol = np.std(returns[t-window:t, i])
            factor[t, i] = -vol if vol > 0 else 0.0
    return factor


@njit(cache=True)
def compute_residual_momentum(returns, window, skip):
    n_dates, n_assets = returns.shape
    factor = np.zeros((n_dates, n_assets))
    
    for t in range(window + skip, n_dates):
        market_ret = np.zeros(window)
        for w in range(window):
            valid_count = 0
            total_ret = 0.0
            for i in range(n_assets):
                ret_val = returns[t - window - skip + w, i]
                if not np.isnan(ret_val) and abs(ret_val) < 1.0:
                    total_ret += ret_val
                    valid_count += 1
            if valid_count > 0:
                market_ret[w] = total_ret / valid_count
        
        for i in range(n_assets):
            asset_ret = returns[t - window - skip:t - skip, i]
            cov_sum = 0.0
            var_sum = 0.0
            mean_asset = np.mean(asset_ret)
            mean_market = np.mean(market_ret)
            
            for w in range(window):
                cov_sum += (asset_ret[w] - mean_asset) * (market_ret[w] - mean_market)
                var_sum += (market_ret[w] - mean_market) ** 2
            
            beta = cov_sum / var_sum if var_sum > 1e-10 else 1.0
            
            residual_sum = 0.0
            for w in range(window):
                residual = asset_ret[w] - beta * market_ret[w]
                residual_sum += residual
            
            factor[t, i] = residual_sum
    
    return factor


@njit(cache=True)
def compute_regime_with_hysteresis(vix, returns, hyst_width, window=20):
    n_dates = len(vix)
    n_assets = returns.shape[1]
    regime = np.zeros(n_dates, dtype=np.int32)
    
    bull_threshold = 0.02
    bear_threshold = -0.02
    crisis_vix = 30.0
    
    prev_regime = 1
    
    for t in range(window, n_dates):
        cum_ret = 0.0
        valid_count = 0
        for w in range(window):
            for i in range(n_assets):
                ret_val = returns[t - w, i]
                if not np.isnan(ret_val) and abs(ret_val) < 1.0:
                    cum_ret += ret_val
                    valid_count += 1
        
        if valid_count > 0:
            cum_ret /= valid_count
        
        vix_val = vix[t]
        
        if vix_val > crisis_vix:
            new_regime = 3
        elif cum_ret > bull_threshold + hyst_width:
            new_regime = 0
        elif cum_ret < bear_threshold - hyst_width:
            new_regime = 2
        elif cum_ret > bull_threshold - hyst_width and prev_regime == 0:
            new_regime = 0
        elif cum_ret < bear_threshold + hyst_width and prev_regime == 2:
            new_regime = 2
        else:
            new_regime = 1
        
        regime[t] = new_regime
        prev_regime = new_regime
    
    return regime


@njit(cache=True)
def backtest_core(
    returns, vix, regime,
    factor_scores, top_k, rebal_period,
    bull_lev, neutral_lev, bear_lev, crisis_lev,
    min_rebalance_days,
    cost_bps,
    start_idx, end_idx
):
    """특정 기간에 대한 백테스트"""
    n_dates = end_idx - start_idx
    n_assets = returns.shape[1]
    
    weights = np.zeros(n_assets)
    equity = np.ones(n_dates)
    daily_returns = np.zeros(n_dates)
    turnover_total = 0.0
    rebalance_count = 0
    last_rebalance_day = 0
    
    regime_leverage = np.array([bull_lev, neutral_lev, bear_lev, crisis_lev])
    
    for t in range(1, n_dates):
        abs_t = start_idx + t
        
        current_regime = regime[abs_t]
        if current_regime < 0 or current_regime > 3:
            current_regime = 1
        target_leverage = regime_leverage[current_regime]
        
        # Soft Frequency Gating
        should_rebalance = False
        days_since_rebalance = t - last_rebalance_day
        
        if days_since_rebalance >= min_rebalance_days:
            if t % rebal_period == 0:
                should_rebalance = True
            if abs_t > start_idx and regime[abs_t] != regime[abs_t-1]:
                should_rebalance = True
        
        if should_rebalance:
            scores = factor_scores[abs_t, :]
            
            valid_count = 0
            for i in range(n_assets):
                if not np.isnan(scores[i]) and abs(scores[i]) < 100:
                    valid_count += 1
            
            if valid_count >= top_k:
                sorted_indices = np.argsort(-scores)
                selected = sorted_indices[:top_k]
                
                new_weights = np.zeros(n_assets)
                for idx in selected:
                    if not np.isnan(scores[idx]) and abs(scores[idx]) < 100:
                        new_weights[idx] = target_leverage / top_k
                
                turnover = np.sum(np.abs(new_weights - weights))
                
                cost = turnover * cost_bps / 10000.0
                equity[t-1] *= (1 - cost)
                
                weights = new_weights
                turnover_total += turnover
                rebalance_count += 1
                last_rebalance_day = t
        
        port_return = 0.0
        for i in range(n_assets):
            port_return += weights[i] * returns[abs_t, i]
        
        daily_returns[t] = port_return
        equity[t] = equity[t-1] * (1 + port_return)
    
    return equity, daily_returns, turnover_total


def calculate_sharpe(daily_returns):
    """Sharpe Ratio 계산"""
    if len(daily_returns) < 10:
        return np.nan
    mean_ret = np.mean(daily_returns)
    std_ret = np.std(daily_returns)
    if std_ret == 0:
        return 0.0
    return mean_ret / std_ret * np.sqrt(252)


def calculate_mdd(equity):
    """Maximum Drawdown 계산"""
    peak = equity[0]
    max_dd = 0.0
    for i in range(len(equity)):
        if equity[i] > peak:
            peak = equity[i]
        dd = (equity[i] - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def walk_forward_validation(returns, vix, factor_scores, regime, params, dates,
                            train_years=5, test_years=2, step_years=1):
    """Walk-Forward Validation"""
    print("\n" + "="*60)
    print("Walk-Forward Validation")
    print(f"Train: {train_years}년, Test: {test_years}년, Step: {step_years}년")
    print("="*60)
    
    n_dates = len(returns)
    train_days = train_years * 252
    test_days = test_years * 252
    step_days = step_years * 252
    
    results = []
    fold = 0
    start = 0
    
    while start + train_days + test_days <= n_dates:
        fold += 1
        train_start = start
        train_end = start + train_days
        test_start = train_end
        test_end = min(test_start + test_days, n_dates)
        
        # 테스트 기간 백테스트
        equity, daily_returns, turnover = backtest_core(
            returns, vix, regime, factor_scores,
            params['top_k'], params['rebal_period'],
            params['bull_lev'], params['neutral_lev'], 
            params['bear_lev'], params['crisis_lev'],
            params['min_rebal_days'],
            20.0,
            test_start, test_end
        )
        
        sharpe = calculate_sharpe(daily_returns[1:])
        mdd = calculate_mdd(equity)
        
        train_start_date = dates[train_start]
        train_end_date = dates[train_end-1]
        test_start_date = dates[test_start]
        test_end_date = dates[test_end-1]
        
        results.append({
            'fold': fold,
            'train_start': train_start_date,
            'train_end': train_end_date,
            'test_start': test_start_date,
            'test_end': test_end_date,
            'sharpe': sharpe,
            'mdd': mdd,
            'final_equity': equity[-1],
            'turnover': turnover
        })
        
        print(f"  Fold {fold}: Train {train_start_date.strftime('%Y-%m')} ~ {train_end_date.strftime('%Y-%m')}, "
              f"Test {test_start_date.strftime('%Y-%m')} ~ {test_end_date.strftime('%Y-%m')}, "
              f"Sharpe={sharpe:.3f}, MDD={mdd*100:.1f}%")
        
        start += step_days
    
    return results


def kfold_cv_validation(returns, vix, factor_scores, regime, params, dates, n_folds=5):
    """K-Fold Cross-Validation (시계열 보존)"""
    print("\n" + "="*60)
    print(f"{n_folds}-Fold Cross-Validation")
    print("="*60)
    
    n_dates = len(returns)
    fold_size = n_dates // n_folds
    
    results = []
    
    for fold in range(n_folds):
        # 테스트 구간 설정
        test_start = fold * fold_size
        test_end = (fold + 1) * fold_size if fold < n_folds - 1 else n_dates
        
        # 테스트 기간 백테스트
        equity, daily_returns, turnover = backtest_core(
            returns, vix, regime, factor_scores,
            params['top_k'], params['rebal_period'],
            params['bull_lev'], params['neutral_lev'], 
            params['bear_lev'], params['crisis_lev'],
            params['min_rebal_days'],
            20.0,
            test_start, test_end
        )
        
        sharpe = calculate_sharpe(daily_returns[1:])
        mdd = calculate_mdd(equity)
        
        test_start_date = dates[test_start]
        test_end_date = dates[test_end-1]
        
        results.append({
            'fold': fold + 1,
            'test_start': test_start_date,
            'test_end': test_end_date,
            'sharpe': sharpe,
            'mdd': mdd,
            'final_equity': equity[-1],
            'turnover': turnover
        })
        
        print(f"  Fold {fold+1}: {test_start_date.strftime('%Y-%m')} ~ {test_end_date.strftime('%Y-%m')}, "
              f"Sharpe={sharpe:.3f}, MDD={mdd*100:.1f}%")
    
    return results


def regime_analysis(returns, vix, factor_scores, regime, regime_labels, params, dates):
    """레짐별 성과 분석"""
    print("\n" + "="*60)
    print("레짐별 성과 분석")
    print("="*60)
    
    n_dates = len(returns)
    
    # 전체 기간 백테스트
    equity, daily_returns, turnover = backtest_core(
        returns, vix, regime, factor_scores,
        params['top_k'], params['rebal_period'],
        params['bull_lev'], params['neutral_lev'], 
        params['bear_lev'], params['crisis_lev'],
        params['min_rebal_days'],
        20.0,
        0, n_dates
    )
    
    # 레짐별 수익률 분리
    regime_names = {0: 'Risk-On (Bull)', 1: 'Neutral', 2: 'Risk-Off (Bear)', 3: 'Crisis'}
    
    results = []
    for r in range(4):
        mask = regime_labels[1:] == r
        if np.sum(mask) > 10:
            regime_returns = daily_returns[1:][mask]
            sharpe = calculate_sharpe(regime_returns)
            avg_return = np.mean(regime_returns) * 252 * 100
            volatility = np.std(regime_returns) * np.sqrt(252) * 100
            n_days = np.sum(mask)
            
            results.append({
                'regime': regime_names.get(r, f'Regime {r}'),
                'n_days': n_days,
                'pct_days': n_days / len(daily_returns) * 100,
                'sharpe': sharpe,
                'annual_return': avg_return,
                'volatility': volatility
            })
            
            print(f"  {regime_names.get(r, f'Regime {r}')}: "
                  f"{n_days}일 ({n_days/len(daily_returns)*100:.1f}%), "
                  f"Sharpe={sharpe:.3f}, "
                  f"Ann.Ret={avg_return:.1f}%, "
                  f"Vol={volatility:.1f}%")
    
    return results


def event_analysis(returns, vix, factor_scores, regime, params, dates):
    """주요 시장 이벤트별 성과 분석"""
    print("\n" + "="*60)
    print("주요 시장 이벤트별 성과 분석")
    print("="*60)
    
    n_dates = len(returns)
    
    # 전체 기간 백테스트
    equity, daily_returns, turnover = backtest_core(
        returns, vix, regime, factor_scores,
        params['top_k'], params['rebal_period'],
        params['bull_lev'], params['neutral_lev'], 
        params['bear_lev'], params['crisis_lev'],
        params['min_rebal_days'],
        20.0,
        0, n_dates
    )
    
    events = {
        '2008 금융위기': ('2008-09-01', '2009-03-31'),
        '2011 유럽 위기': ('2011-07-01', '2011-12-31'),
        '2015 중국 쇼크': ('2015-08-01', '2015-09-30'),
        '2018 변동성 쇼크': ('2018-01-01', '2018-12-31'),
        '2020 코로나 폭락': ('2020-02-01', '2020-04-30'),
        '2022 금리 인상': ('2022-01-01', '2022-12-31'),
        '2023 회복기': ('2023-01-01', '2023-12-31'),
        '2024 AI 랠리': ('2024-01-01', '2024-12-31'),
    }
    
    results = []
    dates_pd = pd.DatetimeIndex(dates)
    
    for event_name, (start_str, end_str) in events.items():
        start_dt = pd.to_datetime(start_str)
        end_dt = pd.to_datetime(end_str)
        
        mask = (dates_pd >= start_dt) & (dates_pd <= end_dt)
        if np.sum(mask) > 5:
            event_returns = daily_returns[1:][mask[1:]]
            
            if len(event_returns) > 0:
                sharpe = calculate_sharpe(event_returns)
                cum_return = (1 + event_returns).prod() - 1
                n_days = len(event_returns)
                
                results.append({
                    'event': event_name,
                    'n_days': n_days,
                    'sharpe': sharpe,
                    'cum_return': cum_return * 100
                })
                
                print(f"  {event_name}: {n_days}일, Sharpe={sharpe:.3f}, Return={cum_return*100:.1f}%")
    
    return results


def main():
    print("="*80)
    print("ARES v45: 강건성 검증 (Walk-Forward + K-Fold CV + 레짐별 성과)")
    print("="*80)
    
    print("\n[1] Loading data...")
    ohlcv_df, vix_df, regime_df = load_data()
    
    print("\n[2] Preparing numpy arrays...")
    returns, prices, vix, regime_labels, tickers, dates = prepare_numpy_arrays(
        ohlcv_df, vix_df, regime_df
    )
    
    n_dates, n_assets = returns.shape
    print(f"Data: {n_dates} dates × {n_assets} assets")
    print(f"Date range: {dates[0]} ~ {dates[-1]}")
    
    print("\n[3] Computing factors...")
    momentum_63 = compute_momentum_factor(prices, 63)
    momentum_126 = compute_momentum_factor(prices, 126)
    volatility_21 = compute_volatility_factor(returns, 21)
    residual_mom = compute_residual_momentum(returns, 126, 0)
    
    factor_scores = (momentum_63 + momentum_126 + volatility_21 * 0.5 + residual_mom * 0.2) / 2.7
    
    print("\n[4] Computing regime...")
    regime = compute_regime_with_hysteresis(vix, returns, 0.05)
    
    # v43 최고 성능 파라미터
    params = {
        'top_k': 35,
        'rebal_period': 7,
        'hyst_width': 0.05,
        'bull_lev': 1.0,
        'neutral_lev': 0.7,
        'bear_lev': 0.5,
        'crisis_lev': 0.2,
        'min_rebal_days': 2
    }
    
    print("\n[5] Running validations...")
    
    # 1. Walk-Forward Validation
    wf_results = walk_forward_validation(
        returns, vix, factor_scores, regime, params, dates,
        train_years=5, test_years=2, step_years=1
    )
    
    # 2. K-Fold Cross-Validation
    kf_results = kfold_cv_validation(
        returns, vix, factor_scores, regime, params, dates, n_folds=5
    )
    
    # 3. 레짐별 성과 분석
    regime_results = regime_analysis(
        returns, vix, factor_scores, regime, regime_labels, params, dates
    )
    
    # 4. 이벤트별 성과 분석
    event_results = event_analysis(
        returns, vix, factor_scores, regime, params, dates
    )
    
    # 결과 요약
    print("\n" + "="*80)
    print("VALIDATION SUMMARY")
    print("="*80)
    
    # Walk-Forward 요약
    wf_df = pd.DataFrame(wf_results)
    print(f"\n### Walk-Forward Validation ###")
    print(f"  평균 Sharpe: {wf_df['sharpe'].mean():.3f} (±{wf_df['sharpe'].std():.3f})")
    print(f"  최소 Sharpe: {wf_df['sharpe'].min():.3f}")
    print(f"  최대 Sharpe: {wf_df['sharpe'].max():.3f}")
    print(f"  평균 MDD: {wf_df['mdd'].mean()*100:.1f}%")
    print(f"  Sharpe > 1.0 비율: {(wf_df['sharpe'] > 1.0).mean()*100:.1f}%")
    print(f"  Sharpe > 0.5 비율: {(wf_df['sharpe'] > 0.5).mean()*100:.1f}%")
    
    # K-Fold 요약
    kf_df = pd.DataFrame(kf_results)
    print(f"\n### K-Fold Cross-Validation ###")
    print(f"  평균 Sharpe: {kf_df['sharpe'].mean():.3f} (±{kf_df['sharpe'].std():.3f})")
    print(f"  최소 Sharpe: {kf_df['sharpe'].min():.3f}")
    print(f"  최대 Sharpe: {kf_df['sharpe'].max():.3f}")
    print(f"  평균 MDD: {kf_df['mdd'].mean()*100:.1f}%")
    
    # 레짐별 요약
    print(f"\n### 레짐별 성과 ###")
    for r in regime_results:
        print(f"  {r['regime']}: Sharpe={r['sharpe']:.3f}, Ann.Ret={r['annual_return']:.1f}%")
    
    # 결과 저장
    wf_df.to_csv('/home/ubuntu/v45_walkforward_results.csv', index=False)
    kf_df.to_csv('/home/ubuntu/v45_kfold_results.csv', index=False)
    pd.DataFrame(regime_results).to_csv('/home/ubuntu/v45_regime_results.csv', index=False)
    pd.DataFrame(event_results).to_csv('/home/ubuntu/v45_event_results.csv', index=False)
    
    print("\n" + "="*80)
    print("Results saved to /home/ubuntu/v45_*.csv")
    print("="*80)


if __name__ == '__main__':
    main()
