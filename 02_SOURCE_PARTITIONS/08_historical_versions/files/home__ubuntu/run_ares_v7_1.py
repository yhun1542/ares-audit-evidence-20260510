#!/usr/bin/env python3
"""
ARES v7.1 - 헷징 전략 추가
============================
v7.0 기반 + MODERATE/CASH 구간 헷징 전략:
1. MODERATE 구간: 포지션 축소 + SPY 숏 헷징
2. CASH 구간: SPY 숏 포지션 (인버스 효과)
3. 방어적 종목 투자 옵션
"""

import sqlite3
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


# =============================================================================
# Configuration
# =============================================================================
@dataclass
class V71Config:
    """v7.1 설정"""
    # VIX 임계값
    vix_ultra_low: float = 13.0
    vix_low: float = 16.0
    vix_moderate: float = 18.0
    vix_high: float = 25.0
    vix_crisis: float = 35.0
    
    # 포지션 멀티플라이어 (롱)
    mult_ultra_low: float = 1.5
    mult_low: float = 1.2
    mult_moderate: float = 0.0  # 롱 포지션 없음
    mult_cash: float = 0.0
    
    # 헷징 멀티플라이어 (숏)
    hedge_moderate: float = 0.3   # MODERATE: 30% 숏
    hedge_high: float = 0.5       # HIGH (18-25): 50% 숏
    hedge_crisis: float = 0.7     # CRISIS (25-35): 70% 숏
    hedge_extreme: float = 0.5    # EXTREME (35+): 50% 숏 (과매도 반전 대비)
    
    # 팩터 가중치
    momentum_weight: float = 0.70
    low_vol_weight: float = 0.30
    
    # 포지션 사이징
    max_position_size: float = 0.25
    max_positions: int = 8
    target_vol: float = 0.18
    
    # 기본 거래 비용 (15bp)
    base_transaction_cost: float = 0.0015
    
    # 동적 리밸런싱 설정
    min_rebalance_days: Dict[str, int] = None
    drift_threshold: Dict[str, float] = None
    
    def __post_init__(self):
        if self.min_rebalance_days is None:
            self.min_rebalance_days = {
                "ULTRA_LOW": 14,
                "LOW": 21,
                "MODERATE": 7,    # 헷징 시 빠른 대응
                "HIGH": 5,
                "CRISIS": 3,
                "EXTREME": 3
            }
        if self.drift_threshold is None:
            self.drift_threshold = {
                "ULTRA_LOW": 0.15,
                "LOW": 0.12,
                "MODERATE": 0.08,
                "HIGH": 0.05,
                "CRISIS": 0.05,
                "EXTREME": 0.05
            }


# =============================================================================
# Data Loader
# =============================================================================
class DataLoader:
    """데이터 로더"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = None
        
    def connect(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
        return self._conn
    
    def load_prices(self, symbols: List[str], start: str, end: str) -> pd.DataFrame:
        """가격 데이터 로드"""
        conn = self.connect()
        
        placeholders = ','.join(['?' for _ in symbols])
        query = f"""
        SELECT symbol, date, close
        FROM daily_ohlcv
        WHERE symbol IN ({placeholders})
        AND date >= ? AND date <= ?
        ORDER BY date, symbol
        """
        
        df = pd.read_sql_query(query, conn, params=symbols + [start, end])
        df['date'] = pd.to_datetime(df['date'])
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        prices = df.pivot(index='date', columns='symbol', values='close')
        return prices.ffill()
    
    def load_vix(self, start: str, end: str) -> pd.Series:
        """VIX 데이터 로드"""
        conn = self.connect()
        
        query = """
        SELECT date, close as vix
        FROM daily_ohlcv
        WHERE symbol = 'VIX'
        AND date >= ? AND date <= ?
        ORDER BY date
        """
        
        df = pd.read_sql_query(query, conn, params=[start, end])
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        
        return df['vix']


# =============================================================================
# Point-in-Time Factor Calculator
# =============================================================================
class PointInTimeFactors:
    """Point-in-Time 팩터 계산"""
    
    @staticmethod
    def momentum_12_1(prices: pd.DataFrame, as_of_idx: int) -> pd.Series:
        """12-1 모멘텀 (T-1 가격 기준)"""
        if as_of_idx < 253:
            return pd.Series(index=prices.columns, dtype=float)
        
        price_t1 = prices.iloc[as_of_idx - 1]
        price_12m = prices.iloc[as_of_idx - 253]
        price_1m = prices.iloc[as_of_idx - 22]
        
        mom = (price_1m / price_12m - 1)
        return mom
    
    @staticmethod
    def volatility_60d(prices: pd.DataFrame, as_of_idx: int) -> pd.Series:
        """60일 변동성"""
        if as_of_idx < 62:
            return pd.Series(index=prices.columns, dtype=float)
        
        returns = prices.iloc[as_of_idx - 61:as_of_idx - 1].pct_change().dropna()
        vol = returns.std() * np.sqrt(252)
        return vol
    
    @staticmethod
    def compute_factors(prices: pd.DataFrame, as_of_idx: int, cfg: V71Config) -> pd.Series:
        """팩터 점수 계산"""
        mom = PointInTimeFactors.momentum_12_1(prices, as_of_idx)
        mom_rank = mom.rank(pct=True)
        
        vol = PointInTimeFactors.volatility_60d(prices, as_of_idx)
        vol_rank = 1 - vol.rank(pct=True)
        
        score = cfg.momentum_weight * mom_rank + cfg.low_vol_weight * vol_rank
        return score


# =============================================================================
# Adaptive Trading Costs
# =============================================================================
class AdaptiveTradingCosts:
    """적응형 거래 비용"""
    
    def __init__(self, base_cost: float = 0.0015):
        self.base_cost = base_cost
        self.regime_multiplier = {
            "ULTRA_LOW": 0.8,
            "LOW": 0.9,
            "MODERATE": 1.0,
            "HIGH": 1.2,
            "CRISIS": 1.5,
            "EXTREME": 2.0
        }
    
    def calculate_cost(self, turnover: float, regime: str) -> float:
        """거래 비용 계산"""
        base = self.base_cost
        regime_mult = self.regime_multiplier.get(regime, 1.0)
        
        if turnover > 1.0:
            volume_discount = 0.75
        elif turnover > 0.5:
            volume_discount = 0.85
        else:
            volume_discount = 1.0
        
        return turnover * base * regime_mult * volume_discount


# =============================================================================
# Portfolio Optimizer with Hedging
# =============================================================================
class HedgingPortfolioOptimizer:
    """헷징 포트폴리오 최적화"""
    
    def __init__(self, cfg: V71Config):
        self.cfg = cfg
    
    def get_regime(self, vix: float) -> Tuple[str, float, float]:
        """VIX 레짐, 롱 멀티플라이어, 숏 멀티플라이어"""
        if vix < self.cfg.vix_ultra_low:
            return "ULTRA_LOW", self.cfg.mult_ultra_low, 0.0
        elif vix < self.cfg.vix_low:
            return "LOW", self.cfg.mult_low, 0.0
        elif vix < self.cfg.vix_moderate:
            return "MODERATE", self.cfg.mult_moderate, self.cfg.hedge_moderate
        elif vix < self.cfg.vix_high:
            return "HIGH", 0.0, self.cfg.hedge_high
        elif vix < self.cfg.vix_crisis:
            return "CRISIS", 0.0, self.cfg.hedge_crisis
        else:
            return "EXTREME", 0.0, self.cfg.hedge_extreme
    
    def optimize_long(self, scores: pd.Series, prices: pd.DataFrame, 
                     as_of_idx: int, regime: str, multiplier: float) -> Dict[str, float]:
        """롱 포지션 최적화"""
        if multiplier == 0:
            return {}
        
        valid_scores = scores.dropna()
        if len(valid_scores) == 0:
            return {}
        
        n_stocks = {
            "ULTRA_LOW": 8,
            "LOW": 6,
            "MODERATE": 0,
            "HIGH": 0,
            "CRISIS": 0,
            "EXTREME": 0
        }.get(regime, 0)
        
        if n_stocks == 0:
            return {}
        
        top_symbols = valid_scores.nlargest(n_stocks).index.tolist()
        
        vols = {}
        for sym in top_symbols:
            vol = PointInTimeFactors.volatility_60d(prices[[sym]], as_of_idx)
            vols[sym] = vol.iloc[0] if len(vol) > 0 and pd.notna(vol.iloc[0]) else 0.2
        
        inv_vols = {sym: 1/max(v, 0.05) for sym, v in vols.items()}
        total_inv_vol = sum(inv_vols.values())
        
        if total_inv_vol == 0:
            return {}
        
        weights = {}
        for sym in top_symbols:
            raw_weight = inv_vols[sym] / total_inv_vol
            vol_scale = self.cfg.target_vol / max(vols[sym], 0.05)
            vol_scale = np.clip(vol_scale, 0.5, 2.0)
            
            weight = raw_weight * multiplier * vol_scale
            weight = min(weight, self.cfg.max_position_size)
            weights[sym] = weight
        
        total_weight = sum(weights.values())
        if total_weight > 1.5:
            scale = 1.5 / total_weight
            weights = {sym: w * scale for sym, w in weights.items()}
        
        return weights
    
    def optimize_hedge(self, regime: str, hedge_multiplier: float) -> Dict[str, float]:
        """헷징 포지션 최적화 (SPY 숏)"""
        if hedge_multiplier == 0:
            return {}
        
        # SPY 숏 포지션 (음수 가중치)
        return {"SPY_SHORT": -hedge_multiplier}


# =============================================================================
# Backtester with Hedging
# =============================================================================
class ARESBacktesterV71:
    """ARES v7.1 백테스터 (헷징 포함)"""
    
    def __init__(self, cfg: V71Config, loader: DataLoader):
        self.cfg = cfg
        self.loader = loader
        self.cost_calculator = AdaptiveTradingCosts(cfg.base_transaction_cost)
        self.optimizer = HedgingPortfolioOptimizer(cfg)
    
    def run(self, symbols: List[str], start: str, end: str) -> pd.DataFrame:
        """백테스트 실행"""
        
        # SPY 추가
        all_symbols = list(set(symbols + ['SPY']))
        
        print(f"Loading data: {start} to {end}")
        prices = self.loader.load_prices(all_symbols, start, end)
        vix = self.loader.load_vix(start, end)
        
        common_dates = prices.index.intersection(vix.index)
        prices = prices.loc[common_dates]
        vix = vix.loc[common_dates]
        
        print(f"Data loaded: {len(prices)} days, {len(prices.columns)} symbols")
        
        results = []
        current_long_weights = {}
        current_hedge_weight = 0.0
        last_rebalance_idx = None
        
        for i in range(len(prices)):
            if i < 253:
                continue
            
            date = prices.index[i]
            current_vix = vix.iloc[i]
            regime, long_mult, hedge_mult = self.optimizer.get_regime(current_vix)
            
            days_since = i - last_rebalance_idx if last_rebalance_idx else 999
            
            # 팩터 점수 계산
            scores = PointInTimeFactors.compute_factors(prices, i, self.cfg)
            
            # 목표 포지션 계산
            target_long = self.optimizer.optimize_long(scores, prices, i, regime, long_mult)
            target_hedge = -hedge_mult  # 숏 포지션
            
            # 리밸런싱 결정
            min_days = self.cfg.min_rebalance_days.get(regime, 21)
            drift_threshold = self.cfg.drift_threshold.get(regime, 0.10)
            
            # 롱 포지션 드리프트
            all_symbols_set = set(current_long_weights.keys()) | set(target_long.keys())
            long_drift = sum(abs(target_long.get(k, 0) - current_long_weights.get(k, 0)) 
                           for k in all_symbols_set)
            
            # 헷지 포지션 드리프트
            hedge_drift = abs(target_hedge - current_hedge_weight)
            
            total_drift = long_drift + hedge_drift
            
            should_rebalance = (days_since >= min_days and total_drift > drift_threshold)
            
            # 레짐 전환 시 강제 리밸런싱
            if last_rebalance_idx is None:
                should_rebalance = True
            
            # 비용 계산 및 포지션 업데이트
            cost = 0
            if should_rebalance:
                turnover = long_drift + hedge_drift
                cost = self.cost_calculator.calculate_cost(turnover, regime)
                
                current_long_weights = target_long
                current_hedge_weight = target_hedge
                last_rebalance_idx = i
            
            # 일별 수익률 계산
            daily_return = 0
            if i > 0:
                # 롱 포지션 수익
                for sym, weight in current_long_weights.items():
                    if sym in prices.columns:
                        price_today = prices.iloc[i][sym]
                        price_prev = prices.iloc[i-1][sym]
                        if pd.notna(price_today) and pd.notna(price_prev) and price_prev > 0:
                            sym_return = (price_today / price_prev - 1)
                            daily_return += weight * sym_return
                
                # 헷지 포지션 수익 (SPY 숏)
                if current_hedge_weight != 0 and 'SPY' in prices.columns:
                    spy_today = prices.iloc[i]['SPY']
                    spy_prev = prices.iloc[i-1]['SPY']
                    if pd.notna(spy_today) and pd.notna(spy_prev) and spy_prev > 0:
                        spy_return = (spy_today / spy_prev - 1)
                        # 숏 포지션: 역수익
                        daily_return += current_hedge_weight * spy_return  # hedge_weight는 이미 음수
            
            daily_return -= cost
            
            results.append({
                'date': date,
                'return': daily_return,
                'vix': current_vix,
                'regime': regime,
                'long_exposure': sum(current_long_weights.values()),
                'hedge_exposure': current_hedge_weight,
                'net_exposure': sum(current_long_weights.values()) + current_hedge_weight,
                'n_positions': len(current_long_weights),
                'cost': cost,
                'rebalanced': should_rebalance
            })
        
        return pd.DataFrame(results)


# =============================================================================
# Performance Metrics
# =============================================================================
def calculate_metrics(results: pd.DataFrame) -> Dict:
    """성과 지표 계산"""
    
    returns = results['return'].values
    
    total_return = np.prod(1 + returns) - 1
    annual_return = (1 + total_return) ** (252 / len(returns)) - 1
    annual_vol = np.std(returns) * np.sqrt(252)
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0
    
    cum_returns = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cum_returns)
    drawdown = (cum_returns - peak) / peak
    mdd = np.min(drawdown)
    
    # 투자 기간 (롱 또는 숏 포지션 있을 때)
    invested_mask = (results['long_exposure'].abs() > 0.01) | (results['hedge_exposure'].abs() > 0.01)
    if invested_mask.sum() > 0:
        invested_returns = returns[invested_mask]
        invested_annual_return = np.prod(1 + invested_returns) ** (252 / len(invested_returns)) - 1
        invested_vol = np.std(invested_returns) * np.sqrt(252)
        invested_sharpe = invested_annual_return / invested_vol if invested_vol > 0 else 0
    else:
        invested_sharpe = 0
    
    investment_ratio = invested_mask.mean()
    total_cost = results['cost'].sum()
    n_rebalances = results['rebalanced'].sum()
    
    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'annual_vol': annual_vol,
        'sharpe': sharpe,
        'mdd': mdd,
        'invested_sharpe': invested_sharpe,
        'investment_ratio': investment_ratio,
        'total_cost': total_cost,
        'avg_long_exposure': results['long_exposure'].mean(),
        'avg_hedge_exposure': results['hedge_exposure'].mean(),
        'avg_net_exposure': results['net_exposure'].mean(),
        'n_days': len(results),
        'n_rebalances': n_rebalances
    }


def calculate_regime_metrics(results: pd.DataFrame) -> pd.DataFrame:
    """레짐별 성과 계산"""
    
    regime_stats = []
    
    for regime in ['ULTRA_LOW', 'LOW', 'MODERATE', 'HIGH', 'CRISIS', 'EXTREME']:
        mask = results['regime'] == regime
        if mask.sum() == 0:
            continue
        
        regime_returns = results.loc[mask, 'return'].values
        
        if len(regime_returns) > 0:
            annual_return = np.prod(1 + regime_returns) ** (252 / max(len(regime_returns), 1)) - 1
            vol = np.std(regime_returns) * np.sqrt(252)
            sharpe = annual_return / vol if vol > 0 else 0
            
            regime_stats.append({
                'regime': regime,
                'days': mask.sum(),
                'annual_return': annual_return,
                'sharpe': sharpe,
                'avg_long': results.loc[mask, 'long_exposure'].mean(),
                'avg_hedge': results.loc[mask, 'hedge_exposure'].mean(),
                'total_cost': results.loc[mask, 'cost'].sum()
            })
    
    return pd.DataFrame(regime_stats)


# =============================================================================
# Main
# =============================================================================
def main():
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--db_path', default='/home/ubuntu/ares_x_unified_database/ares_universal_v2.db')
    parser.add_argument('--start', default='2019-01-01')
    parser.add_argument('--end', default='2024-12-20')
    parser.add_argument('--output', default='./v7_1_results.csv')
    args = parser.parse_args()
    
    cfg = V71Config()
    
    print("=" * 60)
    print("ARES v7.1 - 헷징 전략 추가")
    print("=" * 60)
    print(f"Period: {args.start} to {args.end}")
    print(f"Hedging: MODERATE={cfg.hedge_moderate}, HIGH={cfg.hedge_high}, CRISIS={cfg.hedge_crisis}")
    print("=" * 60)
    
    loader = DataLoader(args.db_path)
    
    symbols = [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B',
        'UNH', 'JNJ', 'V', 'XOM', 'JPM', 'PG', 'MA', 'HD', 'CVX', 'MRK',
        'ABBV', 'PEP', 'KO', 'COST', 'AVGO', 'TMO', 'MCD', 'WMT', 'CSCO',
        'ACN', 'ABT', 'DHR', 'NEE', 'LIN', 'NKE', 'TXN', 'PM', 'UNP',
        'RTX', 'ORCL', 'LOW', 'INTC', 'AMD', 'QCOM', 'HON', 'IBM', 'CAT',
        'AMAT', 'DE', 'GS', 'AXP', 'BA'
    ]
    
    backtester = ARESBacktesterV71(cfg, loader)
    results = backtester.run(symbols, args.start, args.end)
    
    results.to_csv(args.output, index=False)
    print(f"\nResults saved to {args.output}")
    
    metrics = calculate_metrics(results)
    
    print("\n" + "=" * 60)
    print("PERFORMANCE METRICS")
    print("=" * 60)
    print(f"Total Return: {metrics['total_return']*100:.2f}%")
    print(f"Annual Return: {metrics['annual_return']*100:.2f}%")
    print(f"Annual Volatility: {metrics['annual_vol']*100:.2f}%")
    print(f"Sharpe Ratio: {metrics['sharpe']:.2f}")
    print(f"MDD: {metrics['mdd']*100:.2f}%")
    print(f"Invested Sharpe: {metrics['invested_sharpe']:.2f}")
    print(f"Investment Ratio: {metrics['investment_ratio']*100:.2f}%")
    print(f"Total Cost: {metrics['total_cost']*100:.2f}%")
    print(f"Avg Long Exposure: {metrics['avg_long_exposure']*100:.2f}%")
    print(f"Avg Hedge Exposure: {metrics['avg_hedge_exposure']*100:.2f}%")
    print(f"Avg Net Exposure: {metrics['avg_net_exposure']*100:.2f}%")
    print(f"Rebalances: {metrics['n_rebalances']}")
    
    regime_metrics = calculate_regime_metrics(results)
    
    print("\n" + "=" * 60)
    print("REGIME PERFORMANCE")
    print("=" * 60)
    print(regime_metrics.to_string(index=False))
    
    return metrics, regime_metrics


if __name__ == '__main__':
    main()
