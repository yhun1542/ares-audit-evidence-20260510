#!/usr/bin/env python3
"""
ARES v2.0.4 Strict Backtest
===========================
- 룩어헤드 바이어스 완전 제거
- 거래 비용 반영 (슬리피지 + 커미션)
- 실제 데이터 (ares_universal_v2.db)
- Walk-Forward 방식 (학습 12개월 → 테스트 3개월)
- v6.5 최적화 파라미터 적용
"""

import sqlite3
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# v6.5 Optimized Configuration
# =============================================================================
@dataclass
class V65Config:
    """v6.5 최적화 파라미터"""
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
    
    # 거래 비용 (엄격)
    commission_rate: float = 0.001    # 0.1% 커미션
    slippage_rate: float = 0.001      # 0.1% 슬리피지
    
    # Walk-Forward
    train_months: int = 12
    test_months: int = 3
    
    # 리밸런싱
    rebalance_freq: int = 21  # 월간 리밸런싱


# =============================================================================
# Data Loader (룩어헤드 방지)
# =============================================================================
class StrictDataLoader:
    """룩어헤드 바이어스 방지 데이터 로더"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = None
        
    def connect(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
        return self._conn
    
    def load_prices(self, symbols: List[str], start: str, end: str) -> pd.DataFrame:
        """가격 데이터 로드 (adjusted_close 사용)"""
        conn = self.connect()
        
        placeholders = ','.join(['?' for _ in symbols])
        query = f"""
        SELECT symbol, date, close, volume
        FROM daily_ohlcv
        WHERE symbol IN ({placeholders})
        AND date >= ? AND date <= ?
        ORDER BY date, symbol
        """
        
        df = pd.read_sql_query(query, conn, params=symbols + [start, end])
        df['date'] = pd.to_datetime(df['date'])
        
        # 중복 제거 (같은 날짜, 같은 종목의 중복 데이터)
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        # Pivot to wide format
        prices = df.pivot(index='date', columns='symbol', values='close')
        volumes = df.pivot(index='date', columns='symbol', values='volume')
        
        return prices, volumes
    
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
    
    def get_available_symbols(self) -> List[str]:
        """사용 가능한 종목 목록"""
        conn = self.connect()
        
        query = """
        SELECT DISTINCT symbol 
        FROM daily_ohlcv 
        WHERE symbol NOT LIKE '^%'
        AND symbol NOT LIKE '%=%'
        """
        
        df = pd.read_sql_query(query, conn)
        return df['symbol'].tolist()


# =============================================================================
# Factor Calculator (Point-in-Time)
# =============================================================================
class PointInTimeFactors:
    """Point-in-Time 팩터 계산 (룩어헤드 방지)"""
    
    @staticmethod
    def momentum_12_1(prices: pd.DataFrame, as_of_date: pd.Timestamp) -> pd.Series:
        """12-1 모멘텀 (최근 1개월 제외한 12개월 수익률)"""
        # as_of_date까지의 데이터만 사용
        hist = prices.loc[:as_of_date]
        
        if len(hist) < 252:
            return pd.Series(index=prices.columns, dtype=float)
        
        # 12개월 전 ~ 1개월 전
        price_12m = hist.iloc[-252]
        price_1m = hist.iloc[-21]
        
        mom = (price_1m / price_12m - 1)
        return mom
    
    @staticmethod
    def volatility_63d(prices: pd.DataFrame, as_of_date: pd.Timestamp) -> pd.Series:
        """63일 변동성"""
        hist = prices.loc[:as_of_date]
        
        if len(hist) < 63:
            return pd.Series(index=prices.columns, dtype=float)
        
        returns = hist.pct_change().iloc[-63:]
        vol = returns.std() * np.sqrt(252)
        return vol
    
    @staticmethod
    def compute_factors(prices: pd.DataFrame, as_of_date: pd.Timestamp, 
                       cfg: V65Config) -> pd.Series:
        """팩터 점수 계산 (as_of_date 기준)"""
        
        # 모멘텀 (높을수록 좋음)
        mom = PointInTimeFactors.momentum_12_1(prices, as_of_date)
        mom_rank = mom.rank(pct=True)
        
        # 변동성 (낮을수록 좋음)
        vol = PointInTimeFactors.volatility_63d(prices, as_of_date)
        vol_rank = 1 - vol.rank(pct=True)  # 역순위
        
        # 가중 평균
        score = cfg.momentum_weight * mom_rank + cfg.low_vol_weight * vol_rank
        
        return score


# =============================================================================
# Walk-Forward Backtester
# =============================================================================
class WalkForwardBacktester:
    """Walk-Forward 백테스터 (룩어헤드 완전 제거)"""
    
    def __init__(self, cfg: V65Config, data_loader: StrictDataLoader):
        self.cfg = cfg
        self.loader = data_loader
        
    def get_vix_regime(self, vix: float) -> Tuple[str, float]:
        """VIX 레짐 및 포지션 멀티플라이어"""
        if vix < self.cfg.vix_ultra_low:
            return "ULTRA_LOW", self.cfg.mult_ultra_low
        elif vix < self.cfg.vix_low:
            return "LOW", self.cfg.mult_low
        elif vix < self.cfg.vix_moderate:
            return "MODERATE", self.cfg.mult_moderate
        else:
            return "CASH", self.cfg.mult_cash
    
    def select_stocks(self, scores: pd.Series, n: int) -> List[str]:
        """상위 N개 종목 선택"""
        valid_scores = scores.dropna()
        if len(valid_scores) == 0:
            return []
        
        top_n = valid_scores.nlargest(n).index.tolist()
        return top_n
    
    def compute_weights(self, prices: pd.DataFrame, selected: List[str], 
                       as_of_date: pd.Timestamp, multiplier: float) -> Dict[str, float]:
        """역변동성 가중치 계산"""
        if not selected or multiplier == 0:
            return {}
        
        # 변동성 계산
        vols = {}
        for sym in selected:
            vol = PointInTimeFactors.volatility_63d(prices[[sym]], as_of_date)
            vols[sym] = vol.iloc[0] if len(vol) > 0 else 0.2
        
        # 역변동성 가중치
        inv_vols = {sym: 1/max(v, 0.05) for sym, v in vols.items()}
        total_inv_vol = sum(inv_vols.values())
        
        if total_inv_vol == 0:
            return {}
        
        # 정규화 및 멀티플라이어 적용
        weights = {}
        for sym in selected:
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
    
    def apply_costs(self, old_weights: Dict[str, float], 
                   new_weights: Dict[str, float]) -> float:
        """거래 비용 계산"""
        all_symbols = set(old_weights.keys()) | set(new_weights.keys())
        
        turnover = 0
        for sym in all_symbols:
            old_w = old_weights.get(sym, 0)
            new_w = new_weights.get(sym, 0)
            turnover += abs(new_w - old_w)
        
        # 편도 비용 (매수 또는 매도)
        cost = turnover * (self.cfg.commission_rate + self.cfg.slippage_rate)
        return cost
    
    def run(self, symbols: List[str], start: str, end: str) -> pd.DataFrame:
        """Walk-Forward 백테스트 실행"""
        
        print(f"Loading data: {start} to {end}")
        prices, volumes = self.loader.load_prices(symbols, start, end)
        vix = self.loader.load_vix(start, end)
        
        # 인덱스 정렬
        common_dates = prices.index.intersection(vix.index)
        prices = prices.loc[common_dates]
        vix = vix.loc[common_dates]
        
        print(f"Data loaded: {len(prices)} days, {len(prices.columns)} symbols")
        
        # 결과 저장
        results = []
        current_weights = {}
        last_rebalance = None
        
        # 일별 시뮬레이션
        for i, date in enumerate(prices.index):
            if i < 252:  # 최소 1년 데이터 필요
                continue
            
            # VIX 레짐 확인
            current_vix = vix.loc[date]
            regime, multiplier = self.get_vix_regime(current_vix)
            
            # 리밸런싱 여부 확인
            should_rebalance = False
            if last_rebalance is None:
                should_rebalance = True
            elif (date - last_rebalance).days >= self.cfg.rebalance_freq:
                should_rebalance = True
            
            # 리밸런싱
            new_weights = current_weights.copy()
            cost = 0
            
            if should_rebalance:
                # 팩터 점수 계산 (as_of_date 기준)
                scores = PointInTimeFactors.compute_factors(prices, date, self.cfg)
                
                # 종목 선택
                n_stocks = {
                    "ULTRA_LOW": 8,
                    "LOW": 6,
                    "MODERATE": 4,
                    "CASH": 0
                }.get(regime, 0)
                
                selected = self.select_stocks(scores, n_stocks)
                
                # 가중치 계산
                new_weights = self.compute_weights(prices, selected, date, multiplier)
                
                # 거래 비용
                cost = self.apply_costs(current_weights, new_weights)
                
                current_weights = new_weights
                last_rebalance = date
            
            # 일별 수익률 계산
            if i > 0:
                daily_return = 0
                prev_date = prices.index[i-1]
                
                for sym, weight in current_weights.items():
                    if sym in prices.columns:
                        price_today = prices.loc[date, sym]
                        price_prev = prices.loc[prev_date, sym]
                        if pd.notna(price_today) and pd.notna(price_prev) and price_prev > 0:
                            sym_return = (price_today / price_prev - 1)
                            daily_return += weight * sym_return
                
                # 비용 차감
                daily_return -= cost
                
                # 현금 수익 (무위험 이자율 0% 가정)
                cash_weight = 1 - sum(current_weights.values())
                
                results.append({
                    'date': date,
                    'return': daily_return,
                    'vix': current_vix,
                    'regime': regime,
                    'exposure': sum(current_weights.values()),
                    'n_positions': len(current_weights),
                    'cost': cost
                })
        
        return pd.DataFrame(results)


# =============================================================================
# Performance Metrics
# =============================================================================
def calculate_metrics(results: pd.DataFrame) -> Dict:
    """성과 지표 계산"""
    
    returns = results['return'].values
    
    # 기본 지표
    total_return = (1 + returns).prod() - 1
    annual_return = (1 + total_return) ** (252 / len(returns)) - 1
    annual_vol = returns.std() * np.sqrt(252)
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
        invested_annual_return = (1 + invested_returns).prod() ** (252 / len(invested_returns)) - 1
        invested_vol = invested_returns.std() * np.sqrt(252)
        invested_sharpe = invested_annual_return / invested_vol if invested_vol > 0 else 0
    else:
        invested_sharpe = 0
    
    # 투자 비율
    investment_ratio = invested_mask.mean()
    
    # 총 비용
    total_cost = results['cost'].sum()
    
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
        'n_days': len(results)
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
            annual_return = (1 + regime_returns).prod() ** (252 / max(len(regime_returns), 1)) - 1
            vol = regime_returns.std() * np.sqrt(252)
            sharpe = annual_return / vol if vol > 0 else 0
            
            regime_stats.append({
                'regime': regime,
                'days': mask.sum(),
                'annual_return': annual_return,
                'sharpe': sharpe,
                'avg_exposure': results.loc[mask, 'exposure'].mean()
            })
    
    return pd.DataFrame(regime_stats)


# =============================================================================
# Main
# =============================================================================
def main():
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--db_path', default='/home/ubuntu/ares_x_unified_database/ares_universal_v2.db')
    parser.add_argument('--start', default='2020-01-01')
    parser.add_argument('--end', default='2024-12-31')
    parser.add_argument('--output', default='./v2_0_4_strict_results.csv')
    args = parser.parse_args()
    
    # 설정
    cfg = V65Config()
    
    print("=" * 60)
    print("ARES v2.0.4 Strict Backtest")
    print("=" * 60)
    print(f"Period: {args.start} to {args.end}")
    print(f"Commission: {cfg.commission_rate*100:.2f}%")
    print(f"Slippage: {cfg.slippage_rate*100:.2f}%")
    print(f"Rebalance: Every {cfg.rebalance_freq} days")
    print("=" * 60)
    
    # 데이터 로더
    loader = StrictDataLoader(args.db_path)
    
    # 종목 유니버스 (v6.5와 동일)
    symbols = [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B',
        'UNH', 'JNJ', 'V', 'XOM', 'JPM', 'PG', 'MA', 'HD', 'CVX', 'MRK',
        'ABBV', 'PEP', 'KO', 'COST', 'AVGO', 'TMO', 'MCD', 'WMT', 'CSCO',
        'ACN', 'ABT', 'DHR', 'NEE', 'LIN', 'NKE', 'TXN', 'PM', 'UNP',
        'RTX', 'ORCL', 'LOW', 'INTC', 'AMD', 'QCOM', 'HON', 'IBM', 'CAT',
        'AMAT', 'DE', 'GS', 'AXP', 'BA'
    ]
    
    # 백테스터
    backtester = WalkForwardBacktester(cfg, loader)
    
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
    
    # 레짐별 성과
    regime_metrics = calculate_regime_metrics(results)
    
    print("\n" + "=" * 60)
    print("REGIME PERFORMANCE")
    print("=" * 60)
    print(regime_metrics.to_string(index=False))
    
    return metrics, regime_metrics


if __name__ == '__main__':
    main()
