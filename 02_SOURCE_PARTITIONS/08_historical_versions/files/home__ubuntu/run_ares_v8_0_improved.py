#!/usr/bin/env python3
"""
ARES v8.0 - Log Analysis Based Improvements
================================================================================

Key Findings from Log Analysis:
1. Only Momentum has positive ICIR (0.66)
2. Low_vol, Value, Mean_rev have negative IC - REMOVE
3. High transaction costs (9.49%) - Reduce rebalancing
4. CRISIS regime losing money - Cash only
5. Low investment ratio (29%) - Expand VIX thresholds

Improvements:
1. Momentum-only or Momentum+Quality
2. Longer rebalancing (21 days)
3. CRISIS = Cash only (no short)
4. Expand VIX thresholds for more investment
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple
from dataclasses import dataclass, field
import warnings
warnings.filterwarnings('ignore')

@dataclass
class ImprovedConfig:
    """개선된 설정"""
    # 팩터 가중치 - 모멘텀 중심
    factor_weights: Dict[str, float] = field(default_factory=lambda: {
        'momentum': 0.85, 'quality': 0.15
    })
    
    # VIX 임계값 - 확장
    vix_ultra_low: float = 14.0
    vix_low: float = 18.0
    vix_moderate: float = 22.0  # 확장
    vix_high: float = 28.0
    
    # 레짐별 포지션 - CRISIS 현금
    regime_config: Dict[str, dict] = field(default_factory=lambda: {
        'ULTRA_LOW': {'long_mult': 1.5, 'short_mult': 0.0},
        'LOW': {'long_mult': 1.2, 'short_mult': 0.0},
        'MODERATE': {'long_mult': 0.6, 'short_mult': 0.0},  # 소량 롱
        'HIGH': {'long_mult': 0.0, 'short_mult': 0.0},  # 현금
        'CRISIS': {'long_mult': 0.0, 'short_mult': 0.0},  # 현금
    })
    
    # 포트폴리오 설정
    n_stocks: int = 8
    rebalance_days: int = 21  # 21일로 연장 (비용 절감)
    transaction_cost: float = 0.002

def load_data(db_path: str, start_date: str = '2018-01-01'):
    """데이터 로드"""
    conn = sqlite3.connect(db_path)
    
    # 일별 OHLCV
    query = f"""
    SELECT symbol, date, close, volume
    FROM daily_ohlcv 
    WHERE date >= '{start_date}'
    ORDER BY symbol, date
    """
    df = pd.read_sql_query(query, conn)
    df['date'] = pd.to_datetime(df['date'])
    df = df.drop_duplicates(subset=['symbol', 'date'], keep='last')
    
    # VIX
    vix_query = f"""
    SELECT date, close as vix
    FROM vix 
    WHERE date >= '{start_date}'
    ORDER BY date
    """
    vix_df = pd.read_sql_query(vix_query, conn)
    vix_df['date'] = pd.to_datetime(vix_df['date'])
    
    conn.close()
    
    # 피벗
    prices = df.pivot(index='date', columns='symbol', values='close')
    
    # 충분한 데이터가 있는 종목만
    valid_mask = prices.notna().sum() > 500
    symbols = prices.columns[valid_mask].tolist()
    prices = prices[symbols]
    
    # VIX
    vix = vix_df.set_index('date')['vix']
    
    return prices, vix

def calculate_momentum_factor(prices: pd.DataFrame, lookback: int = 252, skip: int = 21) -> pd.Series:
    """12-1 모멘텀 팩터 (T-1 데이터만 사용)"""
    if len(prices) < lookback + skip:
        return pd.Series()
    
    # T-skip ~ T-lookback 수익률
    price_recent = prices.iloc[-skip-1]  # T-21
    price_old = prices.iloc[-lookback-1]  # T-252
    
    momentum = (price_recent / price_old - 1).replace([np.inf, -np.inf], np.nan)
    return momentum

def calculate_quality_factor(prices: pd.DataFrame) -> pd.Series:
    """품질 팩터 - 수익률 안정성"""
    if len(prices) < 120:
        return pd.Series()
    
    returns = prices.pct_change()
    
    # 단기/장기 변동성 비율
    short_vol = returns.iloc[-60:].std()
    long_vol = returns.iloc[-120:].std()
    
    quality = 1 - (short_vol / long_vol).replace([np.inf, -np.inf], np.nan)
    return quality

def detect_regime(vix: float, config: ImprovedConfig) -> str:
    """레짐 감지"""
    if pd.isna(vix):
        vix = 20.0
    
    if vix < config.vix_ultra_low:
        return 'ULTRA_LOW'
    elif vix < config.vix_low:
        return 'LOW'
    elif vix < config.vix_moderate:
        return 'MODERATE'
    elif vix < config.vix_high:
        return 'HIGH'
    else:
        return 'CRISIS'

def select_stocks(factors: Dict[str, pd.Series], config: ImprovedConfig) -> List[str]:
    """종목 선택"""
    if not factors or 'momentum' not in factors:
        return []
    
    # 복합 점수 계산
    scores = pd.Series(0.0, index=factors['momentum'].index)
    
    for factor_name, weight in config.factor_weights.items():
        if factor_name in factors and weight > 0:
            factor_values = factors[factor_name]
            # Z-score 정규화
            z_scores = (factor_values - factor_values.mean()) / factor_values.std()
            scores += weight * z_scores.fillna(0)
    
    # ETF/인덱스 제외
    exclude = ['VIX', 'SPY', 'QQQ', 'IWM', 'DIA', 'TLT', 'GLD', 'SLV', 'IEF', 'LQD', 'HYG']
    scores = scores.drop(exclude, errors='ignore')
    
    # NaN 제거
    scores = scores.dropna()
    
    if len(scores) < config.n_stocks:
        return []
    
    # 상위 종목 선택
    return scores.nlargest(config.n_stocks).index.tolist()

def run_backtest(prices: pd.DataFrame, vix: pd.Series, config: ImprovedConfig,
                 start_date: str, end_date: str) -> dict:
    """백테스트 실행"""
    
    # 기간 필터
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    mask = (prices.index >= start) & (prices.index <= end)
    test_dates = prices.index[mask]
    
    if len(test_dates) < 252:
        return {'error': 'Insufficient data'}
    
    returns = prices.pct_change()
    
    # 결과 저장
    portfolio_values = [1.0]
    positions = {}
    last_rebalance = None
    total_costs = 0
    
    regime_stats = {}
    for regime in ['ULTRA_LOW', 'LOW', 'MODERATE', 'HIGH', 'CRISIS']:
        regime_stats[regime] = {'days': 0, 'returns': []}
    
    for i, date in enumerate(test_dates):
        if i == 0:
            continue
        
        prev_date = test_dates[i-1]
        
        # T-1 VIX로 레짐 감지
        prev_vix = vix.get(prev_date, 20)
        if pd.isna(prev_vix):
            prev_vix = 20
        
        regime = detect_regime(prev_vix, config)
        regime_config = config.regime_config[regime]
        long_mult = regime_config['long_mult']
        
        # 리밸런싱 체크
        should_rebalance = last_rebalance is None or (date - last_rebalance).days >= config.rebalance_days
        
        if should_rebalance:
            old_positions = positions.copy()
            
            if long_mult > 0:
                # 팩터 계산 (T-1 데이터만 사용)
                hist_prices = prices.loc[prices.index < date]
                
                if len(hist_prices) >= 252:
                    factors = {
                        'momentum': calculate_momentum_factor(hist_prices),
                        'quality': calculate_quality_factor(hist_prices)
                    }
                    
                    # 종목 선택
                    selected = select_stocks(factors, config)
                    
                    if selected:
                        weight = long_mult / len(selected)
                        positions = {s: weight for s in selected}
                    else:
                        positions = {}
                else:
                    positions = {}
            else:
                positions = {}
            
            # 거래 비용 계산
            turnover = sum(abs(positions.get(s, 0) - old_positions.get(s, 0)) 
                          for s in set(list(positions.keys()) + list(old_positions.keys())))
            cost = turnover * config.transaction_cost
            total_costs += cost
            
            last_rebalance = date
        else:
            cost = 0
        
        # 일일 수익률 계산
        daily_return = 0
        for sym, weight in positions.items():
            if sym in returns.columns and date in returns.index:
                ret = returns.loc[date, sym]
                if not pd.isna(ret):
                    daily_return += weight * ret
        
        daily_return -= cost
        
        # 포트폴리오 가치 업데이트
        new_value = portfolio_values[-1] * (1 + daily_return)
        portfolio_values.append(new_value)
        
        # 레짐별 통계
        regime_stats[regime]['days'] += 1
        regime_stats[regime]['returns'].append(daily_return)
    
    # 최종 성과 계산
    portfolio_values = np.array(portfolio_values)
    daily_returns = np.diff(portfolio_values) / portfolio_values[:-1]
    
    total_return = portfolio_values[-1] / portfolio_values[0] - 1
    years = len(daily_returns) / 252
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
    annual_vol = np.std(daily_returns) * np.sqrt(252)
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0
    
    # MDD
    peak = np.maximum.accumulate(portfolio_values)
    drawdown = (portfolio_values - peak) / peak
    mdd = np.min(drawdown)
    
    # 투자 비율
    invested_days = sum(1 for r in daily_returns if abs(r) > 0.0001)
    investment_ratio = invested_days / len(daily_returns)
    
    # 투자 기간 Sharpe
    invested_returns = [r for r in daily_returns if abs(r) > 0.0001]
    invested_sharpe = np.mean(invested_returns) / np.std(invested_returns) * np.sqrt(252) if len(invested_returns) > 20 and np.std(invested_returns) > 0 else 0
    
    # 레짐별 성과
    regime_performance = {}
    for regime, stats in regime_stats.items():
        if stats['days'] > 10:
            rets = np.array(stats['returns'])
            regime_performance[regime] = {
                'days': stats['days'],
                'annual_return': round(np.mean(rets) * 252, 4),
                'sharpe': round(np.mean(rets) / np.std(rets) * np.sqrt(252), 4) if np.std(rets) > 0 else 0,
                'win_rate': round(np.mean(rets > 0), 4)
            }
    
    return {
        'sharpe': sharpe,
        'invested_sharpe': invested_sharpe,
        'annual_return': annual_return,
        'total_return': total_return,
        'mdd': mdd,
        'annual_vol': annual_vol,
        'investment_ratio': investment_ratio,
        'total_costs': total_costs,
        'regime_performance': regime_performance
    }

def run_parameter_sweep():
    """파라미터 스윕"""
    
    print("=" * 80)
    print("ARES v8.0 - Log Analysis Based Improvements")
    print("=" * 80)
    
    # 데이터 로드
    print("\n[1/3] Loading data...")
    prices, vix = load_data('/home/ubuntu/ares_x_unified_database/ares_universal_v2.db')
    print(f"  Loaded {len(prices)} dates, {len(prices.columns)} symbols")
    
    # 파라미터 그리드
    configs = []
    
    # 1. 모멘텀 100%
    configs.append(('momentum_100', ImprovedConfig(
        factor_weights={'momentum': 1.0},
        vix_ultra_low=14, vix_low=18, vix_moderate=22, vix_high=28,
        rebalance_days=21
    )))
    
    # 2. 모멘텀 85% + Quality 15%
    configs.append(('momentum_85_quality_15', ImprovedConfig(
        factor_weights={'momentum': 0.85, 'quality': 0.15},
        vix_ultra_low=14, vix_low=18, vix_moderate=22, vix_high=28,
        rebalance_days=21
    )))
    
    # 3. VIX 임계값 확장 (더 많은 투자)
    configs.append(('expanded_vix', ImprovedConfig(
        factor_weights={'momentum': 0.85, 'quality': 0.15},
        vix_ultra_low=15, vix_low=20, vix_moderate=25, vix_high=30,
        rebalance_days=21
    )))
    
    # 4. 리밸런싱 28일
    configs.append(('rebal_28d', ImprovedConfig(
        factor_weights={'momentum': 0.85, 'quality': 0.15},
        vix_ultra_low=14, vix_low=18, vix_moderate=22, vix_high=28,
        rebalance_days=28
    )))
    
    # 5. MODERATE에서도 풀 롱
    configs.append(('moderate_full_long', ImprovedConfig(
        factor_weights={'momentum': 0.85, 'quality': 0.15},
        vix_ultra_low=14, vix_low=18, vix_moderate=22, vix_high=28,
        regime_config={
            'ULTRA_LOW': {'long_mult': 1.5, 'short_mult': 0.0},
            'LOW': {'long_mult': 1.2, 'short_mult': 0.0},
            'MODERATE': {'long_mult': 1.0, 'short_mult': 0.0},  # 풀 롱
            'HIGH': {'long_mult': 0.0, 'short_mult': 0.0},
            'CRISIS': {'long_mult': 0.0, 'short_mult': 0.0},
        },
        rebalance_days=21
    )))
    
    # 6. 집중 투자 (6종목)
    configs.append(('concentrated_6', ImprovedConfig(
        factor_weights={'momentum': 0.85, 'quality': 0.15},
        vix_ultra_low=14, vix_low=18, vix_moderate=22, vix_high=28,
        n_stocks=6,
        rebalance_days=21
    )))
    
    # 7. 분산 투자 (12종목)
    configs.append(('diversified_12', ImprovedConfig(
        factor_weights={'momentum': 0.85, 'quality': 0.15},
        vix_ultra_low=14, vix_low=18, vix_moderate=22, vix_high=28,
        n_stocks=12,
        rebalance_days=21
    )))
    
    # 8. 공격적 레버리지
    configs.append(('aggressive', ImprovedConfig(
        factor_weights={'momentum': 1.0},
        vix_ultra_low=14, vix_low=18, vix_moderate=22, vix_high=28,
        regime_config={
            'ULTRA_LOW': {'long_mult': 2.0, 'short_mult': 0.0},
            'LOW': {'long_mult': 1.5, 'short_mult': 0.0},
            'MODERATE': {'long_mult': 0.8, 'short_mult': 0.0},
            'HIGH': {'long_mult': 0.0, 'short_mult': 0.0},
            'CRISIS': {'long_mult': 0.0, 'short_mult': 0.0},
        },
        n_stocks=6,
        rebalance_days=21
    )))
    
    # 백테스트 실행
    print("\n[2/3] Running backtests...")
    results = []
    
    for name, config in configs:
        result = run_backtest(prices, vix, config, '2020-01-01', '2024-12-20')
        result['name'] = name
        results.append(result)
        
        print(f"\n  {name}:")
        print(f"    Sharpe: {result['sharpe']:.3f}, Invested Sharpe: {result['invested_sharpe']:.3f}")
        print(f"    Annual Return: {result['annual_return']*100:.2f}%, MDD: {result['mdd']*100:.2f}%")
        print(f"    Investment Ratio: {result['investment_ratio']*100:.1f}%, Costs: {result['total_costs']*100:.2f}%")
    
    # 결과 정렬
    print("\n[3/3] Results Summary...")
    results.sort(key=lambda x: x['sharpe'], reverse=True)
    
    print("\n" + "=" * 80)
    print("RESULTS RANKED BY SHARPE")
    print("=" * 80)
    print(f"{'Rank':<5} {'Name':<25} {'Sharpe':<8} {'Inv.Sharpe':<10} {'Return':<10} {'MDD':<10} {'Inv.Ratio':<10}")
    print("-" * 80)
    
    for i, r in enumerate(results):
        print(f"{i+1:<5} {r['name']:<25} {r['sharpe']:.3f}    {r['invested_sharpe']:.3f}      {r['annual_return']*100:.1f}%      {r['mdd']*100:.1f}%      {r['investment_ratio']*100:.1f}%")
    
    # 최고 성과 상세
    best = results[0]
    print("\n" + "=" * 80)
    print(f"BEST STRATEGY: {best['name']}")
    print("=" * 80)
    print(f"  Sharpe: {best['sharpe']:.4f}")
    print(f"  Invested Sharpe: {best['invested_sharpe']:.4f}")
    print(f"  Annual Return: {best['annual_return']*100:.2f}%")
    print(f"  Total Return: {best['total_return']*100:.2f}%")
    print(f"  MDD: {best['mdd']*100:.2f}%")
    print(f"  Investment Ratio: {best['investment_ratio']*100:.2f}%")
    print(f"  Total Costs: {best['total_costs']*100:.2f}%")
    
    print("\n  Regime Performance:")
    for regime, perf in best['regime_performance'].items():
        print(f"    {regime}: Days={perf['days']}, Return={perf['annual_return']*100:.1f}%, Sharpe={perf['sharpe']:.2f}, WinRate={perf['win_rate']*100:.1f}%")
    
    return results

if __name__ == '__main__':
    run_parameter_sweep()
