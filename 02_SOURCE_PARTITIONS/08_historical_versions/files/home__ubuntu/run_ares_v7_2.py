#!/usr/bin/env python3
"""
ARES v7.2 - 롱/숏 전환 전략
============================
핵심 아이디어: 같은 종목 선택 로직으로 롱/숏 전환
- VIX < 18: 상위 종목 롱 (모멘텀 + 저변동성)
- VIX >= 18: 
  - 옵션 A: 상위 종목 숏 (모멘텀 반전 기대)
  - 옵션 B: 하위 종목 숏 (약한 종목이 더 하락)
  - 옵션 C: 고변동성 종목 숏 (하락장에서 더 하락)
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
class V72Config:
    """v7.2 설정"""
    # VIX 임계값
    vix_ultra_low: float = 13.0
    vix_low: float = 16.0
    vix_moderate: float = 18.0
    vix_high: float = 25.0
    vix_crisis: float = 35.0
    
    # 롱 포지션 멀티플라이어
    mult_ultra_low: float = 1.5
    mult_low: float = 1.2
    mult_moderate: float = 0.0
    
    # 숏 포지션 멀티플라이어 (양수로 표현, 실제 적용 시 음수)
    short_moderate: float = 0.3    # 16-18: 30% 숏
    short_high: float = 0.5        # 18-25: 50% 숏
    short_crisis: float = 0.7      # 25-35: 70% 숏
    short_extreme: float = 0.3     # 35+: 30% 숏 (반등 대비)
    
    # 숏 전략 선택
    # "top": 상위 종목 숏 (모멘텀 반전)
    # "bottom": 하위 종목 숏 (약한 종목 더 하락)
    # "high_vol": 고변동성 종목 숏
    short_strategy: str = "high_vol"
    
    # 팩터 가중치
    momentum_weight: float = 0.70
    low_vol_weight: float = 0.30
    
    # 포지션 사이징
    max_position_size: float = 0.25
    max_positions: int = 8
    target_vol: float = 0.18
    
    # 거래 비용
    base_transaction_cost: float = 0.0015
    short_borrow_cost: float = 0.0001  # 일일 차입 비용 (연 2.5%)
    
    # 동적 리밸런싱
    min_rebalance_days: Dict[str, int] = None
    
    def __post_init__(self):
        if self.min_rebalance_days is None:
            self.min_rebalance_days = {
                "ULTRA_LOW": 14,
                "LOW": 21,
                "MODERATE": 7,
                "HIGH": 5,
                "CRISIS": 3,
                "EXTREME": 3
            }


# =============================================================================
# Data Loader
# =============================================================================
class DataLoader:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = None
        
    def connect(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
        return self._conn
    
    def load_prices(self, symbols: List[str], start: str, end: str) -> pd.DataFrame:
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
# Factor Calculator
# =============================================================================
class PointInTimeFactors:
    @staticmethod
    def momentum_12_1(prices: pd.DataFrame, as_of_idx: int) -> pd.Series:
        if as_of_idx < 253:
            return pd.Series(index=prices.columns, dtype=float)
        price_12m = prices.iloc[as_of_idx - 253]
        price_1m = prices.iloc[as_of_idx - 22]
        mom = (price_1m / price_12m - 1)
        return mom
    
    @staticmethod
    def volatility_60d(prices: pd.DataFrame, as_of_idx: int) -> pd.Series:
        if as_of_idx < 62:
            return pd.Series(index=prices.columns, dtype=float)
        returns = prices.iloc[as_of_idx - 61:as_of_idx - 1].pct_change().dropna()
        vol = returns.std() * np.sqrt(252)
        return vol
    
    @staticmethod
    def compute_long_scores(prices: pd.DataFrame, as_of_idx: int, cfg: V72Config) -> pd.Series:
        """롱 점수: 높은 모멘텀 + 낮은 변동성"""
        mom = PointInTimeFactors.momentum_12_1(prices, as_of_idx)
        mom_rank = mom.rank(pct=True)
        
        vol = PointInTimeFactors.volatility_60d(prices, as_of_idx)
        vol_rank = 1 - vol.rank(pct=True)  # 낮은 변동성 선호
        
        score = cfg.momentum_weight * mom_rank + cfg.low_vol_weight * vol_rank
        return score
    
    @staticmethod
    def compute_short_scores(prices: pd.DataFrame, as_of_idx: int, 
                            cfg: V72Config, strategy: str) -> pd.Series:
        """숏 점수: 전략에 따라 다름"""
        mom = PointInTimeFactors.momentum_12_1(prices, as_of_idx)
        vol = PointInTimeFactors.volatility_60d(prices, as_of_idx)
        
        if strategy == "top":
            # 상위 모멘텀 종목 숏 (모멘텀 반전 기대)
            mom_rank = mom.rank(pct=True)
            vol_rank = 1 - vol.rank(pct=True)
            score = cfg.momentum_weight * mom_rank + cfg.low_vol_weight * vol_rank
            
        elif strategy == "bottom":
            # 하위 모멘텀 종목 숏 (약한 종목 더 하락)
            mom_rank = 1 - mom.rank(pct=True)  # 낮은 모멘텀 선호
            vol_rank = vol.rank(pct=True)       # 높은 변동성 선호
            score = cfg.momentum_weight * mom_rank + cfg.low_vol_weight * vol_rank
            
        elif strategy == "high_vol":
            # 고변동성 종목 숏 (하락장에서 더 하락)
            vol_rank = vol.rank(pct=True)       # 높은 변동성 선호
            mom_rank = 1 - mom.rank(pct=True)   # 낮은 모멘텀 선호
            score = 0.5 * vol_rank + 0.5 * mom_rank
            
        else:
            score = pd.Series(0.0, index=prices.columns)
        
        return score


# =============================================================================
# Portfolio Optimizer
# =============================================================================
class LongShortOptimizer:
    def __init__(self, cfg: V72Config):
        self.cfg = cfg
    
    def get_regime(self, vix: float) -> Tuple[str, float, float]:
        """레짐, 롱 멀티플라이어, 숏 멀티플라이어"""
        if vix < self.cfg.vix_ultra_low:
            return "ULTRA_LOW", self.cfg.mult_ultra_low, 0.0
        elif vix < self.cfg.vix_low:
            return "LOW", self.cfg.mult_low, 0.0
        elif vix < self.cfg.vix_moderate:
            return "MODERATE", self.cfg.mult_moderate, self.cfg.short_moderate
        elif vix < self.cfg.vix_high:
            return "HIGH", 0.0, self.cfg.short_high
        elif vix < self.cfg.vix_crisis:
            return "CRISIS", 0.0, self.cfg.short_crisis
        else:
            return "EXTREME", 0.0, self.cfg.short_extreme
    
    def optimize_long(self, scores: pd.Series, prices: pd.DataFrame, 
                     as_of_idx: int, multiplier: float) -> Dict[str, float]:
        """롱 포지션 최적화"""
        if multiplier == 0:
            return {}
        
        valid_scores = scores.dropna()
        if len(valid_scores) == 0:
            return {}
        
        n_stocks = self.cfg.max_positions
        top_symbols = valid_scores.nlargest(n_stocks).index.tolist()
        
        # 역변동성 가중
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
    
    def optimize_short(self, scores: pd.Series, prices: pd.DataFrame, 
                      as_of_idx: int, multiplier: float) -> Dict[str, float]:
        """숏 포지션 최적화"""
        if multiplier == 0:
            return {}
        
        valid_scores = scores.dropna()
        if len(valid_scores) == 0:
            return {}
        
        # 숏은 더 적은 종목에 집중
        n_stocks = min(self.cfg.max_positions, 6)
        top_symbols = valid_scores.nlargest(n_stocks).index.tolist()
        
        # 변동성 비례 가중 (고변동성에 더 많이 숏)
        vols = {}
        for sym in top_symbols:
            vol = PointInTimeFactors.volatility_60d(prices[[sym]], as_of_idx)
            vols[sym] = vol.iloc[0] if len(vol) > 0 and pd.notna(vol.iloc[0]) else 0.2
        
        total_vol = sum(vols.values())
        if total_vol == 0:
            return {}
        
        weights = {}
        for sym in top_symbols:
            raw_weight = vols[sym] / total_vol
            weight = raw_weight * multiplier
            weight = min(weight, self.cfg.max_position_size)
            weights[sym] = -weight  # 음수 (숏)
        
        return weights


# =============================================================================
# Trading Costs
# =============================================================================
class TradingCosts:
    def __init__(self, cfg: V72Config):
        self.cfg = cfg
        self.regime_multiplier = {
            "ULTRA_LOW": 0.8,
            "LOW": 0.9,
            "MODERATE": 1.0,
            "HIGH": 1.2,
            "CRISIS": 1.5,
            "EXTREME": 2.0
        }
    
    def calculate_cost(self, turnover: float, regime: str, 
                      short_exposure: float = 0) -> float:
        """거래 비용 + 숏 차입 비용"""
        base = self.cfg.base_transaction_cost
        regime_mult = self.regime_multiplier.get(regime, 1.0)
        
        # 거래 비용
        trade_cost = turnover * base * regime_mult
        
        # 숏 차입 비용 (일일)
        borrow_cost = abs(short_exposure) * self.cfg.short_borrow_cost
        
        return trade_cost + borrow_cost


# =============================================================================
# Backtester
# =============================================================================
class ARESBacktesterV72:
    def __init__(self, cfg: V72Config, loader: DataLoader):
        self.cfg = cfg
        self.loader = loader
        self.optimizer = LongShortOptimizer(cfg)
        self.cost_calculator = TradingCosts(cfg)
    
    def run(self, symbols: List[str], start: str, end: str) -> pd.DataFrame:
        print(f"Loading data: {start} to {end}")
        prices = self.loader.load_prices(symbols, start, end)
        vix = self.loader.load_vix(start, end)
        
        common_dates = prices.index.intersection(vix.index)
        prices = prices.loc[common_dates]
        vix = vix.loc[common_dates]
        
        print(f"Data loaded: {len(prices)} days, {len(prices.columns)} symbols")
        print(f"Short strategy: {self.cfg.short_strategy}")
        
        results = []
        current_weights = {}  # 롱: 양수, 숏: 음수
        last_rebalance_idx = None
        
        for i in range(len(prices)):
            if i < 253:
                continue
            
            date = prices.index[i]
            current_vix = vix.iloc[i]
            regime, long_mult, short_mult = self.optimizer.get_regime(current_vix)
            
            days_since = i - last_rebalance_idx if last_rebalance_idx else 999
            min_days = self.cfg.min_rebalance_days.get(regime, 21)
            
            should_rebalance = (days_since >= min_days) or (last_rebalance_idx is None)
            
            # 레짐 전환 시 강제 리밸런싱
            if last_rebalance_idx is not None:
                prev_vix = vix.iloc[last_rebalance_idx]
                prev_regime, _, _ = self.optimizer.get_regime(prev_vix)
                if regime != prev_regime:
                    should_rebalance = True
            
            cost = 0
            if should_rebalance:
                # 롱 점수 계산
                long_scores = PointInTimeFactors.compute_long_scores(prices, i, self.cfg)
                
                # 숏 점수 계산
                short_scores = PointInTimeFactors.compute_short_scores(
                    prices, i, self.cfg, self.cfg.short_strategy
                )
                
                # 포지션 최적화
                new_long = self.optimizer.optimize_long(long_scores, prices, i, long_mult)
                new_short = self.optimizer.optimize_short(short_scores, prices, i, short_mult)
                
                # 새 포지션 통합
                new_weights = {}
                for sym, w in new_long.items():
                    new_weights[sym] = new_weights.get(sym, 0) + w
                for sym, w in new_short.items():
                    new_weights[sym] = new_weights.get(sym, 0) + w
                
                # 턴오버 계산
                all_symbols = set(current_weights.keys()) | set(new_weights.keys())
                turnover = sum(abs(new_weights.get(k, 0) - current_weights.get(k, 0)) 
                              for k in all_symbols)
                
                # 숏 노출
                short_exposure = sum(w for w in new_weights.values() if w < 0)
                
                # 비용 계산
                cost = self.cost_calculator.calculate_cost(turnover, regime, short_exposure)
                
                current_weights = new_weights
                last_rebalance_idx = i
            
            # 일별 수익률 계산
            daily_return = 0
            if i > 0:
                for sym, weight in current_weights.items():
                    if sym in prices.columns:
                        price_today = prices.iloc[i][sym]
                        price_prev = prices.iloc[i-1][sym]
                        if pd.notna(price_today) and pd.notna(price_prev) and price_prev > 0:
                            sym_return = (price_today / price_prev - 1)
                            daily_return += weight * sym_return
            
            # 숏 차입 비용 (일일)
            short_exposure = sum(w for w in current_weights.values() if w < 0)
            daily_borrow_cost = abs(short_exposure) * self.cfg.short_borrow_cost
            
            daily_return -= cost + daily_borrow_cost
            
            # 노출 계산
            long_exposure = sum(w for w in current_weights.values() if w > 0)
            short_exposure = sum(w for w in current_weights.values() if w < 0)
            
            results.append({
                'date': date,
                'return': daily_return,
                'vix': current_vix,
                'regime': regime,
                'long_exposure': long_exposure,
                'short_exposure': short_exposure,
                'net_exposure': long_exposure + short_exposure,
                'gross_exposure': long_exposure - short_exposure,
                'n_long': sum(1 for w in current_weights.values() if w > 0),
                'n_short': sum(1 for w in current_weights.values() if w < 0),
                'cost': cost,
                'rebalanced': should_rebalance
            })
        
        return pd.DataFrame(results)


# =============================================================================
# Performance Metrics
# =============================================================================
def calculate_metrics(results: pd.DataFrame) -> Dict:
    returns = results['return'].values
    
    total_return = np.prod(1 + returns) - 1
    annual_return = (1 + total_return) ** (252 / len(returns)) - 1
    annual_vol = np.std(returns) * np.sqrt(252)
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0
    
    cum_returns = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cum_returns)
    drawdown = (cum_returns - peak) / peak
    mdd = np.min(drawdown)
    
    invested_mask = results['gross_exposure'].abs() > 0.01
    if invested_mask.sum() > 0:
        invested_returns = returns[invested_mask]
        invested_annual_return = np.prod(1 + invested_returns) ** (252 / len(invested_returns)) - 1
        invested_vol = np.std(invested_returns) * np.sqrt(252)
        invested_sharpe = invested_annual_return / invested_vol if invested_vol > 0 else 0
    else:
        invested_sharpe = 0
    
    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'annual_vol': annual_vol,
        'sharpe': sharpe,
        'mdd': mdd,
        'invested_sharpe': invested_sharpe,
        'investment_ratio': invested_mask.mean(),
        'total_cost': results['cost'].sum(),
        'avg_long': results['long_exposure'].mean(),
        'avg_short': results['short_exposure'].mean(),
        'avg_net': results['net_exposure'].mean(),
        'avg_gross': results['gross_exposure'].mean(),
        'n_rebalances': results['rebalanced'].sum()
    }


def calculate_regime_metrics(results: pd.DataFrame) -> pd.DataFrame:
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
                'avg_short': results.loc[mask, 'short_exposure'].mean(),
                'avg_net': results.loc[mask, 'net_exposure'].mean()
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
    parser.add_argument('--short_strategy', default='high_vol', 
                       choices=['top', 'bottom', 'high_vol'])
    parser.add_argument('--output', default='./v7_2_results.csv')
    args = parser.parse_args()
    
    cfg = V72Config()
    cfg.short_strategy = args.short_strategy
    
    print("=" * 60)
    print("ARES v7.2 - 롱/숏 전환 전략")
    print("=" * 60)
    print(f"Period: {args.start} to {args.end}")
    print(f"Short Strategy: {cfg.short_strategy}")
    print(f"Short Multipliers: MODERATE={cfg.short_moderate}, HIGH={cfg.short_high}, CRISIS={cfg.short_crisis}")
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
    
    backtester = ARESBacktesterV72(cfg, loader)
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
    print(f"Avg Long: {metrics['avg_long']*100:.2f}%")
    print(f"Avg Short: {metrics['avg_short']*100:.2f}%")
    print(f"Avg Net: {metrics['avg_net']*100:.2f}%")
    print(f"Avg Gross: {metrics['avg_gross']*100:.2f}%")
    print(f"Rebalances: {metrics['n_rebalances']}")
    
    regime_metrics = calculate_regime_metrics(results)
    
    print("\n" + "=" * 60)
    print("REGIME PERFORMANCE")
    print("=" * 60)
    print(regime_metrics.to_string(index=False))
    
    return metrics, regime_metrics


if __name__ == '__main__':
    main()
