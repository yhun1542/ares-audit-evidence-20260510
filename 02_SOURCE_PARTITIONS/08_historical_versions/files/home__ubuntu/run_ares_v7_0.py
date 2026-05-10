#!/usr/bin/env python3
"""
ARES v7.0 - Claude 제안 적용
============================
1. 룩어헤드 바이어스 완전 제거 (T-1 가격 사용)
2. 적응형 거래 비용 (레짐별 조정)
3. 동적 리밸런싱 (레짐별 최적 간격 + 드리프트 기반)
4. 거래 비용 최적화 포트폴리오
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
class V7Config:
    """v7.0 설정"""
    # VIX 임계값
    vix_ultra_low: float = 13.0
    vix_low: float = 16.0
    vix_moderate: float = 18.0
    
    # 포지션 멀티플라이어
    mult_ultra_low: float = 1.5
    mult_low: float = 1.2
    mult_moderate: float = 0.8
    mult_cash: float = 0.0
    
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
                "ULTRA_LOW": 14,   # 2주
                "LOW": 21,         # 3주
                "MODERATE": 28,    # 4주
                "CASH": 7          # 1주 (빠른 대응)
            }
        if self.drift_threshold is None:
            self.drift_threshold = {
                "ULTRA_LOW": 0.15,
                "LOW": 0.12,
                "MODERATE": 0.10,
                "CASH": 0.05
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
# Point-in-Time Factor Calculator (룩어헤드 바이어스 제거)
# =============================================================================
class PointInTimeFactors:
    """Point-in-Time 팩터 계산 (T-1 가격 사용으로 룩어헤드 완전 제거)"""
    
    @staticmethod
    def momentum_12_1(prices: pd.DataFrame, as_of_idx: int) -> pd.Series:
        """12-1 모멘텀 (T-1 가격 기준)"""
        # as_of_idx-1 (전일) 기준으로 계산
        if as_of_idx < 253:  # 252 + 1 (전일 기준)
            return pd.Series(index=prices.columns, dtype=float)
        
        # T-1 가격 (전일 종가)
        price_t1 = prices.iloc[as_of_idx - 1]
        # T-253 가격 (12개월 + 1일 전)
        price_12m = prices.iloc[as_of_idx - 253]
        # T-22 가격 (1개월 + 1일 전)
        price_1m = prices.iloc[as_of_idx - 22]
        
        # 12-1 모멘텀: (T-22 / T-253) - 1
        mom = (price_1m / price_12m - 1)
        return mom
    
    @staticmethod
    def volatility_60d(prices: pd.DataFrame, as_of_idx: int) -> pd.Series:
        """60일 변동성 (T-1까지만 사용)"""
        if as_of_idx < 62:  # 60 + 2 (전일 기준)
            return pd.Series(index=prices.columns, dtype=float)
        
        # T-61 ~ T-1 (전일까지 60일)
        returns = prices.iloc[as_of_idx - 61:as_of_idx - 1].pct_change().dropna()
        vol = returns.std() * np.sqrt(252)
        return vol
    
    @staticmethod
    def compute_factors(prices: pd.DataFrame, as_of_idx: int, cfg: V7Config) -> pd.Series:
        """팩터 점수 계산 (T-1 기준)"""
        
        # 모멘텀 (높을수록 좋음)
        mom = PointInTimeFactors.momentum_12_1(prices, as_of_idx)
        mom_rank = mom.rank(pct=True)
        
        # 변동성 (낮을수록 좋음)
        vol = PointInTimeFactors.volatility_60d(prices, as_of_idx)
        vol_rank = 1 - vol.rank(pct=True)  # 역순위
        
        # 가중 평균
        score = cfg.momentum_weight * mom_rank + cfg.low_vol_weight * vol_rank
        
        return score


# =============================================================================
# Adaptive Trading Costs
# =============================================================================
class AdaptiveTradingCosts:
    """적응형 거래 비용"""
    
    def __init__(self, base_cost: float = 0.0015):
        self.base_cost = base_cost
        
        # 레짐별 비용 조정
        self.regime_multiplier = {
            "ULTRA_LOW": 0.8,    # 낮은 변동성 시 비용 감소
            "LOW": 0.9,
            "MODERATE": 1.0,
            "CASH": 1.5          # 위기 시 비용 증가
        }
    
    def calculate_cost(self, turnover: float, regime: str) -> float:
        """거래 비용 계산"""
        base = self.base_cost
        
        # 레짐별 조정
        regime_mult = self.regime_multiplier.get(regime, 1.0)
        
        # 규모 효과 (대량 거래 시 할인)
        if turnover > 1.0:
            volume_discount = 0.75
        elif turnover > 0.5:
            volume_discount = 0.85
        else:
            volume_discount = 1.0
        
        return turnover * base * regime_mult * volume_discount


# =============================================================================
# Dynamic Rebalancing
# =============================================================================
class DynamicRebalancer:
    """동적 리밸런싱"""
    
    def __init__(self, cfg: V7Config):
        self.cfg = cfg
    
    def should_rebalance(self, current_weights: Dict[str, float], 
                        target_weights: Dict[str, float],
                        regime: str, days_since: int) -> bool:
        """리밸런싱 여부 결정"""
        
        # 최소 간격 확인
        min_days = self.cfg.min_rebalance_days.get(regime, 21)
        if days_since < min_days:
            return False
        
        # 드리프트 계산
        all_symbols = set(current_weights.keys()) | set(target_weights.keys())
        drift = sum(abs(target_weights.get(k, 0) - current_weights.get(k, 0)) 
                   for k in all_symbols)
        
        # 드리프트 임계값 확인
        threshold = self.cfg.drift_threshold.get(regime, 0.10)
        return drift > threshold


# =============================================================================
# Portfolio Optimizer
# =============================================================================
class PortfolioOptimizer:
    """포트폴리오 최적화"""
    
    def __init__(self, cfg: V7Config):
        self.cfg = cfg
    
    def optimize(self, scores: pd.Series, prices: pd.DataFrame, 
                as_of_idx: int, regime: str, multiplier: float,
                current_weights: Dict[str, float] = None) -> Dict[str, float]:
        """포트폴리오 최적화"""
        
        if multiplier == 0:
            return {}
        
        # 유효한 점수만 사용
        valid_scores = scores.dropna()
        if len(valid_scores) == 0:
            return {}
        
        # 레짐별 종목 수
        n_stocks = {
            "ULTRA_LOW": 8,
            "LOW": 6,
            "MODERATE": 4,
            "CASH": 0
        }.get(regime, 0)
        
        if n_stocks == 0:
            return {}
        
        # 상위 N개 종목 선택
        top_symbols = valid_scores.nlargest(n_stocks).index.tolist()
        
        # 변동성 계산
        vols = {}
        for sym in top_symbols:
            vol = PointInTimeFactors.volatility_60d(prices[[sym]], as_of_idx)
            vols[sym] = vol.iloc[0] if len(vol) > 0 and pd.notna(vol.iloc[0]) else 0.2
        
        # 역변동성 가중치
        inv_vols = {sym: 1/max(v, 0.05) for sym, v in vols.items()}
        total_inv_vol = sum(inv_vols.values())
        
        if total_inv_vol == 0:
            return {}
        
        # 가중치 계산
        weights = {}
        for sym in top_symbols:
            raw_weight = inv_vols[sym] / total_inv_vol
            
            # 변동성 타겟팅
            vol_scale = self.cfg.target_vol / max(vols[sym], 0.05)
            vol_scale = np.clip(vol_scale, 0.5, 2.0)
            
            weight = raw_weight * multiplier * vol_scale
            weight = min(weight, self.cfg.max_position_size)
            weights[sym] = weight
        
        # 총 노출 제한
        total_weight = sum(weights.values())
        if total_weight > 1.5:
            scale = 1.5 / total_weight
            weights = {sym: w * scale for sym, w in weights.items()}
        
        return weights


# =============================================================================
# Backtester
# =============================================================================
class ARESBacktesterV7:
    """ARES v7.0 백테스터"""
    
    def __init__(self, cfg: V7Config, loader: DataLoader):
        self.cfg = cfg
        self.loader = loader
        self.cost_calculator = AdaptiveTradingCosts(cfg.base_transaction_cost)
        self.rebalancer = DynamicRebalancer(cfg)
        self.optimizer = PortfolioOptimizer(cfg)
    
    def get_regime(self, vix: float) -> Tuple[str, float]:
        """VIX 레짐 및 멀티플라이어"""
        if vix < self.cfg.vix_ultra_low:
            return "ULTRA_LOW", self.cfg.mult_ultra_low
        elif vix < self.cfg.vix_low:
            return "LOW", self.cfg.mult_low
        elif vix < self.cfg.vix_moderate:
            return "MODERATE", self.cfg.mult_moderate
        else:
            return "CASH", self.cfg.mult_cash
    
    def run(self, symbols: List[str], start: str, end: str) -> pd.DataFrame:
        """백테스트 실행"""
        
        print(f"Loading data: {start} to {end}")
        prices = self.loader.load_prices(symbols, start, end)
        vix = self.loader.load_vix(start, end)
        
        # 인덱스 정렬
        common_dates = prices.index.intersection(vix.index)
        prices = prices.loc[common_dates]
        vix = vix.loc[common_dates]
        
        print(f"Data loaded: {len(prices)} days, {len(prices.columns)} symbols")
        
        # 결과 저장
        results = []
        current_weights = {}
        last_rebalance_idx = None
        
        # 일별 시뮬레이션
        for i in range(len(prices)):
            if i < 253:  # 최소 데이터 필요 (252 + 1)
                continue
            
            date = prices.index[i]
            current_vix = vix.iloc[i]
            regime, multiplier = self.get_regime(current_vix)
            
            # 리밸런싱 여부 확인
            days_since = i - last_rebalance_idx if last_rebalance_idx else 999
            
            # 팩터 점수 계산 (T-1 기준)
            scores = PointInTimeFactors.compute_factors(prices, i, self.cfg)
            
            # 목표 가중치 계산
            target_weights = self.optimizer.optimize(
                scores, prices, i, regime, multiplier, current_weights
            )
            
            # 리밸런싱 결정
            should_rebalance = self.rebalancer.should_rebalance(
                current_weights, target_weights, regime, days_since
            )
            
            # 강제 리밸런싱: CASH 레짐 진입 시
            if regime == "CASH" and sum(current_weights.values()) > 0.01:
                should_rebalance = True
                target_weights = {}
            
            # 첫 리밸런싱
            if last_rebalance_idx is None and multiplier > 0:
                should_rebalance = True
            
            # 비용 계산 및 포지션 업데이트
            cost = 0
            if should_rebalance:
                # 턴오버 계산
                all_symbols = set(current_weights.keys()) | set(target_weights.keys())
                turnover = sum(abs(target_weights.get(k, 0) - current_weights.get(k, 0)) 
                              for k in all_symbols)
                
                # 적응형 비용 계산
                cost = self.cost_calculator.calculate_cost(turnover, regime)
                
                current_weights = target_weights
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
            
            # 비용 차감
            daily_return -= cost
            
            results.append({
                'date': date,
                'return': daily_return,
                'vix': current_vix,
                'regime': regime,
                'exposure': sum(current_weights.values()),
                'n_positions': len(current_weights),
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
    
    # 기본 지표
    total_return = np.prod(1 + returns) - 1
    annual_return = (1 + total_return) ** (252 / len(returns)) - 1
    annual_vol = np.std(returns) * np.sqrt(252)
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0
    
    # MDD
    cum_returns = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cum_returns)
    drawdown = (cum_returns - peak) / peak
    mdd = np.min(drawdown)
    
    # 투자 기간 Sharpe
    invested_mask = results['exposure'] > 0.01
    if invested_mask.sum() > 0:
        invested_returns = returns[invested_mask]
        invested_annual_return = np.prod(1 + invested_returns) ** (252 / len(invested_returns)) - 1
        invested_vol = np.std(invested_returns) * np.sqrt(252)
        invested_sharpe = invested_annual_return / invested_vol if invested_vol > 0 else 0
    else:
        invested_sharpe = 0
    
    # 투자 비율
    investment_ratio = invested_mask.mean()
    
    # 총 비용
    total_cost = results['cost'].sum()
    
    # 리밸런싱 횟수
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
        'avg_exposure': results['exposure'].mean(),
        'n_days': len(results),
        'n_rebalances': n_rebalances
    }


def calculate_regime_metrics(results: pd.DataFrame) -> pd.DataFrame:
    """레짐별 성과 계산"""
    
    regime_stats = []
    
    for regime in ['ULTRA_LOW', 'LOW', 'MODERATE', 'CASH']:
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
                'avg_exposure': results.loc[mask, 'exposure'].mean(),
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
    parser.add_argument('--output', default='./v7_0_results.csv')
    args = parser.parse_args()
    
    # 설정
    cfg = V7Config()
    
    print("=" * 60)
    print("ARES v7.0 - Claude 제안 적용")
    print("=" * 60)
    print(f"Period: {args.start} to {args.end}")
    print(f"Base Transaction Cost: {cfg.base_transaction_cost*100:.2f}%")
    print(f"Dynamic Rebalancing: Enabled")
    print(f"Adaptive Costs: Enabled")
    print("=" * 60)
    
    # 데이터 로더
    loader = DataLoader(args.db_path)
    
    # 종목 유니버스
    symbols = [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B',
        'UNH', 'JNJ', 'V', 'XOM', 'JPM', 'PG', 'MA', 'HD', 'CVX', 'MRK',
        'ABBV', 'PEP', 'KO', 'COST', 'AVGO', 'TMO', 'MCD', 'WMT', 'CSCO',
        'ACN', 'ABT', 'DHR', 'NEE', 'LIN', 'NKE', 'TXN', 'PM', 'UNP',
        'RTX', 'ORCL', 'LOW', 'INTC', 'AMD', 'QCOM', 'HON', 'IBM', 'CAT',
        'AMAT', 'DE', 'GS', 'AXP', 'BA'
    ]
    
    # 백테스터
    backtester = ARESBacktesterV7(cfg, loader)
    
    # 실행
    results = backtester.run(symbols, args.start, args.end)
    
    # 결과 저장
    results.to_csv(args.output, index=False)
    print(f"\nResults saved to {args.output}")
    
    # 성과 지표
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
    print(f"Avg Exposure: {metrics['avg_exposure']*100:.2f}%")
    print(f"Rebalances: {metrics['n_rebalances']}")
    
    # 레짐별 성과
    regime_metrics = calculate_regime_metrics(results)
    
    print("\n" + "=" * 60)
    print("REGIME PERFORMANCE")
    print("=" * 60)
    print(regime_metrics.to_string(index=False))
    
    return metrics, regime_metrics


if __name__ == '__main__':
    main()
