#!/usr/bin/env python3
"""
ARES Ultimate v9.2 - 검증된 v8.0 기반 + 4대 AI 핵심 개선
=========================================================

v8.0 (Sharpe 0.93, Invested 1.20) 기반으로 4대 AI 핵심 제안 적용:

[핵심 개선]
1. LOW 레짐 방어 강화 (GPT/Grok: quality 팩터 부스트)
2. Vol Targeting (GPT: 10% 목표 변동성)
3. Drawdown Control (Grok: -8% 손절, 4% 회복)
4. 주간 리밸런싱 (더 빠른 레짐 대응)
5. 모멘텀 집중 (IC 0.66 검증됨)

목표: Sharpe 2.2+, Invested Sharpe 3.1+, MDD -8%
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# 1. CONFIGURATION
# =============================================================================

@dataclass
class AresV92Config:
    """v9.2 최적화 설정"""
    
    # Database
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    # VIX Regime Thresholds (v8.0 검증됨)
    vix_ultra_low: float = 13.0
    vix_low: float = 16.0
    vix_moderate: float = 20.0
    vix_high: float = 25.0
    vix_crisis: float = 30.0
    
    # Factor Weights (IC 분석 기반)
    momentum_weight: float = 0.80  # ICIR 0.66 (유일하게 유효)
    quality_weight: float = 0.20
    
    # LOW 레짐 특별 설정 (4대 AI 공통 제안)
    low_regime_quality_boost: float = 1.5
    low_regime_momentum_reduce: float = 0.7
    
    # Vol Targeting (GPT 제안)
    target_vol: float = 0.10
    max_leverage: float = 1.5
    min_leverage: float = 0.3
    
    # Drawdown Control (Grok 제안)
    dd_halt_threshold: float = -0.08
    dd_recovery_threshold: float = 0.04
    
    # Regime Leverage (4대 AI 통합)
    leverage_ultra_low: float = 1.5
    leverage_low: float = 0.5  # 방어적
    leverage_moderate: float = 1.0
    leverage_high: float = 0.3
    leverage_crisis: float = 0.0
    
    # Portfolio
    n_stocks: int = 10
    max_position: float = 0.20
    
    # Backtest
    rebalance_days: int = 7  # 주간
    transaction_cost: float = 0.002

# =============================================================================
# 2. REGIME DETECTION
# =============================================================================

class RegimeDetector:
    """VIX 기반 레짐 감지 (v8.0 검증됨)"""
    
    def __init__(self, config: AresV92Config):
        self.config = config
    
    def detect(self, vix: float) -> Tuple[str, float]:
        """레짐 감지"""
        if vix < self.config.vix_ultra_low:
            return 'ULTRA_LOW', self.config.leverage_ultra_low
        elif vix < self.config.vix_low:
            return 'LOW', self.config.leverage_low
        elif vix < self.config.vix_moderate:
            return 'MODERATE', self.config.leverage_moderate
        elif vix < self.config.vix_high:
            return 'HIGH', self.config.leverage_high
        else:
            return 'CRISIS', self.config.leverage_crisis

# =============================================================================
# 3. FACTOR ENGINE
# =============================================================================

class FactorEngine:
    """팩터 계산 (T-1 데이터만 사용)"""
    
    def __init__(self, config: AresV92Config):
        self.config = config
    
    def calculate_momentum(self, prices: pd.DataFrame) -> pd.Series:
        """12-1 모멘텀"""
        if len(prices) < 252:
            return pd.Series(0, index=prices.columns)
        
        ret_12m = prices.iloc[-1] / prices.iloc[-252] - 1
        ret_1m = prices.iloc[-1] / prices.iloc[-21] - 1
        mom = ret_12m - ret_1m
        
        # Z-score
        mean, std = mom.mean(), mom.std()
        if std > 0:
            mom = (mom - mean) / std
        
        return mom
    
    def calculate_quality(self, prices: pd.DataFrame) -> pd.Series:
        """수익률 안정성 (Sharpe-like)"""
        if len(prices) < 252:
            return pd.Series(0, index=prices.columns)
        
        returns = prices.pct_change().iloc[-252:]
        quality = returns.mean() / (returns.std() + 1e-6)
        
        # Z-score
        mean, std = quality.mean(), quality.std()
        if std > 0:
            quality = (quality - mean) / std
        
        return quality
    
    def calculate_volatility(self, prices: pd.DataFrame) -> pd.Series:
        """60일 변동성"""
        if len(prices) < 60:
            return pd.Series(0.15, index=prices.columns)
        
        returns = prices.pct_change().iloc[-60:]
        vol = returns.std() * np.sqrt(252)
        
        return vol
    
    def get_combined_score(self, prices: pd.DataFrame, regime: str) -> pd.Series:
        """레짐별 종합 점수"""
        mom = self.calculate_momentum(prices)
        qual = self.calculate_quality(prices)
        
        # 기본 가중치
        mom_w = self.config.momentum_weight
        qual_w = self.config.quality_weight
        
        # LOW 레짐 특별 처리 (4대 AI 공통 제안)
        if regime == 'LOW':
            mom_w *= self.config.low_regime_momentum_reduce
            qual_w *= self.config.low_regime_quality_boost
            # 재정규화
            total = mom_w + qual_w
            mom_w /= total
            qual_w /= total
        
        score = mom * mom_w + qual * qual_w
        
        return score

# =============================================================================
# 4. PORTFOLIO OPTIMIZER
# =============================================================================

class PortfolioOptimizer:
    """포트폴리오 최적화"""
    
    def __init__(self, config: AresV92Config):
        self.config = config
        self.peak_value = 1.0
        self.in_halt = False
    
    def select_stocks(self, scores: pd.Series, n: int) -> List[str]:
        """상위 N개 종목 선택"""
        valid_scores = scores.dropna()
        if len(valid_scores) == 0:
            return []
        return valid_scores.nlargest(n).index.tolist()
    
    def calculate_weights(self, selected: List[str], volatilities: pd.Series, leverage: float) -> pd.Series:
        """역변동성 가중"""
        if not selected:
            return pd.Series()
        
        weights = pd.Series(0.0, index=selected)
        
        for sym in selected:
            vol = volatilities.get(sym, 0.15)
            weights[sym] = 1 / (vol + 0.10)  # 변동성 바닥 설정
        
        # 정규화
        weights = weights / weights.sum()
        
        # 레버리지 적용
        weights = weights * leverage
        
        # 포지션 제한
        weights = np.clip(weights, 0, self.config.max_position)
        
        return weights
    
    def apply_vol_target(self, weights: pd.Series, returns: pd.DataFrame, current_vol: float) -> pd.Series:
        """Vol Targeting (GPT 제안)"""
        if current_vol < 0.01:
            return weights
        
        scale = self.config.target_vol / current_vol
        scale = np.clip(scale, self.config.min_leverage, self.config.max_leverage)
        
        return weights * scale
    
    def apply_drawdown_control(self, weights: pd.Series, current_value: float) -> pd.Series:
        """Drawdown Control (Grok 제안)"""
        # Peak 업데이트
        if current_value > self.peak_value:
            self.peak_value = current_value
        
        # Drawdown 계산
        dd = (current_value / self.peak_value) - 1
        
        # Halt 조건
        if dd < self.config.dd_halt_threshold:
            self.in_halt = True
        
        # Recovery 조건
        recovery = (current_value / (self.peak_value * 0.92)) - 1
        if recovery > self.config.dd_recovery_threshold:
            self.in_halt = False
        
        # Halt 중이면 50% 현금
        if self.in_halt:
            weights = weights * 0.5
        
        return weights

# =============================================================================
# 5. MAIN ENGINE
# =============================================================================

class AresV92Engine:
    """ARES v9.2 엔진"""
    
    def __init__(self, config: Optional[AresV92Config] = None):
        self.config = config or AresV92Config()
        
        self.regime_detector = RegimeDetector(self.config)
        self.factor_engine = FactorEngine(self.config)
        self.portfolio_optimizer = PortfolioOptimizer(self.config)
        
        self.portfolio_value = 1.0
        self.values = [1.0]
        self.regime_stats = {}
    
    def load_data(self, start: str, end: str) -> Tuple[pd.DataFrame, pd.Series]:
        """데이터 로드"""
        conn = sqlite3.connect(self.config.db_path)
        
        symbols = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'AVGO', 'ADBE',
            'NFLX', 'CRM', 'INTC', 'QCOM', 'TXN', 'AMAT', 'MU', 'LRCX', 'KLAC', 'SNPS',
            'NOW', 'PANW', 'CRWD', 'DDOG', 'ZS', 'FTNT', 'NET', 'SNOW', 'PLTR', 'COIN',
            'V', 'MA', 'JPM', 'BAC', 'GS', 'MS', 'BLK', 'SCHW', 'AXP', 'C',
            'UNH', 'JNJ', 'PFE', 'ABBV', 'MRK', 'LLY', 'TMO', 'ABT', 'DHR', 'BMY'
        ]
        
        symbols_str = ','.join([f"'{s}'" for s in symbols])
        
        query = f"""
        SELECT date, symbol, close
        FROM daily_ohlcv
        WHERE symbol IN ({symbols_str})
        AND date BETWEEN '{start}' AND '{end}'
        ORDER BY date, symbol
        """
        df = pd.read_sql(query, conn)
        df['date'] = pd.to_datetime(df['date'])
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        prices = df.pivot(index='date', columns='symbol', values='close')
        prices = prices.ffill().dropna(axis=1, how='all')
        
        vix_query = f"""
        SELECT date, close as vix
        FROM daily_ohlcv
        WHERE symbol = 'VIX'
        AND date BETWEEN '{start}' AND '{end}'
        ORDER BY date
        """
        vix_df = pd.read_sql(vix_query, conn)
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        vix_df = vix_df.drop_duplicates(subset=['date'], keep='last')
        vix = vix_df.set_index('date')['vix']
        
        conn.close()
        
        common_dates = prices.index.intersection(vix.index)
        prices = prices.loc[common_dates]
        vix = vix.loc[common_dates]
        
        return prices, vix
    
    def backtest(self, start: str = '2020-01-01', end: str = '2024-12-31') -> Dict:
        """백테스트"""
        print(f"Loading data from {start} to {end}...")
        prices, vix = self.load_data(start, end)
        print(f"Loaded {len(prices)} days, {len(prices.columns)} symbols")
        
        warmup = 252
        dates = prices.index[warmup:]
        
        self.portfolio_value = 1.0
        self.values = [1.0]
        self.regime_stats = {}
        positions = pd.Series(0.0, index=prices.columns)
        total_costs = 0
        last_rebal = None
        
        for i, date in enumerate(dates):
            # 리밸런싱 여부
            should_rebal = (
                last_rebal is None or
                (date - last_rebal).days >= self.config.rebalance_days
            )
            
            if should_rebal:
                # T-1 데이터
                hist_prices = prices.loc[:date - timedelta(days=1)]
                current_vix = vix.loc[:date].iloc[-1] if len(vix.loc[:date]) > 0 else 20
                
                # 레짐 감지
                regime, leverage = self.regime_detector.detect(current_vix)
                
                # CRISIS면 현금
                if regime == 'CRISIS':
                    new_positions = pd.Series(0.0, index=prices.columns)
                else:
                    # 팩터 점수
                    scores = self.factor_engine.get_combined_score(hist_prices, regime)
                    
                    # 종목 선택
                    selected = self.portfolio_optimizer.select_stocks(scores, self.config.n_stocks)
                    
                    if selected:
                        # 변동성
                        vols = self.factor_engine.calculate_volatility(hist_prices)
                        
                        # 가중치
                        new_positions = self.portfolio_optimizer.calculate_weights(selected, vols, leverage)
                        
                        # Vol Targeting
                        port_returns = (prices.pct_change() * positions).sum(axis=1)
                        current_vol = port_returns.iloc[-60:].std() * np.sqrt(252) if len(port_returns) > 60 else 0.15
                        new_positions = self.portfolio_optimizer.apply_vol_target(new_positions, prices.pct_change(), current_vol)
                        
                        # Drawdown Control
                        new_positions = self.portfolio_optimizer.apply_drawdown_control(new_positions, self.portfolio_value)
                        
                        # 전체 종목에 맞춤
                        full_positions = pd.Series(0.0, index=prices.columns)
                        for sym, w in new_positions.items():
                            if sym in full_positions.index:
                                full_positions[sym] = w
                        new_positions = full_positions
                    else:
                        new_positions = pd.Series(0.0, index=prices.columns)
                
                # 거래 비용
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * self.config.transaction_cost
                total_costs += cost
                
                positions = new_positions
                last_rebal = date
                
                # 레짐 통계
                if regime not in self.regime_stats:
                    self.regime_stats[regime] = {'days': 0, 'returns': []}
                self.regime_stats[regime]['days'] += 1
            
            # 일일 수익률
            if i > 0:
                prev_date = dates[i - 1]
                daily_returns = prices.loc[date] / prices.loc[prev_date] - 1
                daily_ret = (positions * daily_returns).sum()
                
                if should_rebal:
                    daily_ret -= cost
                
                self.portfolio_value *= (1 + daily_ret)
                self.values.append(self.portfolio_value)
                
                if regime in self.regime_stats:
                    self.regime_stats[regime]['returns'].append(daily_ret)
            
            if (i + 1) % 200 == 0:
                print(f"Progress: {i+1}/{len(dates)} days, Value: {self.portfolio_value:.4f}")
        
        # 성과 계산
        returns = pd.Series(np.diff(self.values) / self.values[:-1])
        
        total_return = self.portfolio_value - 1
        years = len(dates) / 252
        annual_return = (1 + total_return) ** (1 / years) - 1
        
        rf_daily = 0.05 / 252
        excess_returns = returns - rf_daily
        sharpe = np.sqrt(252) * excess_returns.mean() / (excess_returns.std() + 1e-6)
        
        invested_returns = returns[returns.abs() > 1e-6]
        invested_sharpe = np.sqrt(252) * invested_returns.mean() / (invested_returns.std() + 1e-6) if len(invested_returns) > 0 else 0
        
        cumulative = np.cumprod(1 + returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = (cumulative - running_max) / running_max
        mdd = np.min(drawdown)
        
        invested_days = sum(1 for r in returns if abs(r) > 1e-6)
        investment_ratio = invested_days / len(returns)
        
        regime_performance = {}
        for regime, stats in self.regime_stats.items():
            if len(stats['returns']) > 0:
                rets = np.array(stats['returns'])
                regime_performance[regime] = {
                    'days': stats['days'],
                    'annual_return': np.mean(rets) * 252,
                    'sharpe': np.sqrt(252) * np.mean(rets) / (np.std(rets) + 1e-6),
                    'win_rate': np.mean(rets > 0)
                }
        
        return {
            'sharpe': sharpe,
            'invested_sharpe': invested_sharpe,
            'annual_return': annual_return,
            'total_return': total_return,
            'mdd': mdd,
            'investment_ratio': investment_ratio,
            'total_costs': total_costs,
            'regime_performance': regime_performance
        }

# =============================================================================
# 6. MAIN
# =============================================================================

if __name__ == "__main__":
    print("="*60)
    print("ARES Ultimate v9.2 - 검증된 v8.0 + 4대 AI 개선")
    print("="*60)
    
    config = AresV92Config()
    engine = AresV92Engine(config)
    
    results = engine.backtest('2020-01-01', '2024-12-31')
    
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    print(f"Sharpe Ratio: {results['sharpe']:.2f} (Target: 2.2)")
    print(f"Invested Sharpe: {results['invested_sharpe']:.2f} (Target: 3.1)")
    print(f"Annual Return: {results['annual_return']*100:.1f}%")
    print(f"Total Return: {results['total_return']*100:.1f}%")
    print(f"MDD: {results['mdd']*100:.1f}%")
    print(f"Investment Ratio: {results['investment_ratio']*100:.1f}%")
    print(f"Total Costs: {results['total_costs']*100:.2f}%")
    
    print("\n" + "="*60)
    print("REGIME PERFORMANCE")
    print("="*60)
    for regime, perf in sorted(results['regime_performance'].items()):
        print(f"{regime:12} | Days: {perf['days']:4} | Return: {perf['annual_return']*100:6.1f}% | Sharpe: {perf['sharpe']:5.2f} | Win: {perf['win_rate']*100:4.1f}%")
