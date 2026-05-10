#!/usr/bin/env python3
"""
ARES Ultimate v4.1 - FIXED VERSION
===================================

v4.0 실패 원인 분석:
1. NEUTRAL/CRISIS에서 투자 → 대규모 손실
2. 변동성 96.90% → 통제 불가
3. 레짐 완화가 오히려 독

v4.1 수정 방향:
1. 보수적 레짐 전략 복귀 (BULL에서만 적극 투자)
2. 변동성 타겟팅 15% 적용
3. NEUTRAL: 저변동성 종목만, 소량
4. BEAR/CRISIS: 현금 또는 매우 소량
5. 포지션 사이징 강화
"""

import sys
import os
import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum
import logging
import json
import warnings
from scipy import stats

warnings.filterwarnings('ignore')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== 상수 ====================
DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
TRADING_DAYS_YEAR = 252
RISK_FREE_RATE = 0.05


class MarketRegime(Enum):
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"
    CRISIS = "CRISIS"


@dataclass
class FixedConfig:
    """수정된 보수적 설정"""
    # 종목 선택
    top_n: int = 5
    min_momentum: float = 0.005
    
    # 포지션 관리
    max_single_position: float = 0.20
    rebalance_threshold: float = 0.03
    transaction_cost: float = 0.001
    
    # 변동성 타겟
    target_volatility: float = 0.15  # 15% 연간 변동성 목표
    max_volatility: float = 0.20     # 20% 초과 시 포지션 축소
    
    # 레짐별 투자 비율 (보수적으로 복귀)
    regime_exposure: Dict[str, float] = field(default_factory=lambda: {
        'BULL': 0.90,      # 강세장: 90% 투자
        'NEUTRAL': 0.25,   # 중립장: 25%만 (저변동성 종목)
        'BEAR': 0.10,      # 약세장: 10%만 (최고 품질 종목)
        'CRISIS': 0.0      # 위기: 100% 현금
    })
    
    # 레짐 감지 임계값 (v3.0 기준 유지, 약간만 완화)
    vix_bull_max: float = 16.0      # VIX < 16: BULL
    vix_neutral_max: float = 22.0   # 16-22: NEUTRAL
    vix_bear_max: float = 28.0      # 22-28: BEAR, >28: CRISIS


# ==================== 데이터 로더 ====================
class DataLoader:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = None
        
    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        logger.info(f"Connected to: {self.db_path}")
        
    def close(self):
        if self.conn:
            self.conn.close()
            
    def load_daily_data(self, start_date: str, end_date: str, tickers: List[str]) -> pd.DataFrame:
        placeholders = ','.join(['?' for _ in tickers])
        query = f"""
        SELECT date, symbol as ticker, open, high, low, close, volume
        FROM daily_ohlcv
        WHERE date >= ? AND date <= ? AND symbol IN ({placeholders})
        ORDER BY date, symbol
        """
        df = pd.read_sql_query(query, self.conn, params=[start_date, end_date] + tickers)
        df['date'] = pd.to_datetime(df['date'])
        return df
    
    def load_vix_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        query = "SELECT date, close as vix FROM vix WHERE date >= ? AND date <= ? ORDER BY date"
        df = pd.read_sql_query(query, self.conn, params=[start_date, end_date])
        df['date'] = pd.to_datetime(df['date'])
        return df


# ==================== 팩터 계산기 ====================
class FactorEngine:
    """팩터 계산 엔진"""
    
    def calculate_momentum(self, prices: pd.DataFrame, window: int = 21) -> pd.Series:
        """
        모멘텀 계산 (최근 1주 제외 - 단기 반전 효과 제거)
        """
        if len(prices) < window + 5:
            return pd.Series(0.5, index=prices.columns)
        
        # window일 수익률 (최근 5일 제외)
        ret = prices.iloc[-6] / prices.iloc[-window-5] - 1
        return ret.rank(pct=True)
    
    def calculate_volatility(self, prices: pd.DataFrame, window: int = 21) -> pd.Series:
        """
        변동성 계산 (낮을수록 높은 점수)
        """
        returns = prices.pct_change()
        if len(returns) < window:
            return pd.Series(0.5, index=prices.columns)
        
        vol = returns.iloc[-window:].std() * np.sqrt(TRADING_DAYS_YEAR)
        # 변동성 역수 = 낮은 변동성이 높은 점수
        inv_vol = 1 / (vol + 0.01)
        return inv_vol.rank(pct=True)
    
    def calculate_quality(self, prices: pd.DataFrame, window: int = 63) -> pd.Series:
        """
        품질 점수 (안정성 + 일관성)
        """
        returns = prices.pct_change()
        if len(returns) < window:
            return pd.Series(0.5, index=prices.columns)
        
        recent = returns.iloc[-window:]
        
        # 변동성 역수
        vol_score = (1 / (recent.std() + 0.001)).rank(pct=True)
        
        # 양의 수익일 비율
        positive_ratio = (recent > 0).sum() / window
        consistency_score = positive_ratio.rank(pct=True)
        
        # MDD 역수
        cum = (1 + recent).cumprod()
        peak = cum.expanding().max()
        dd = (cum - peak) / peak
        mdd = dd.min()
        mdd_score = (1 / (abs(mdd) + 0.01)).rank(pct=True)
        
        quality = vol_score * 0.4 + consistency_score * 0.3 + mdd_score * 0.3
        return quality.rank(pct=True)
    
    def get_stock_volatility(self, prices: pd.DataFrame, window: int = 21) -> pd.Series:
        """개별 종목 변동성 반환 (연간화)"""
        returns = prices.pct_change()
        if len(returns) < window:
            return pd.Series(0.20, index=prices.columns)
        
        return returns.iloc[-window:].std() * np.sqrt(TRADING_DAYS_YEAR)


# ==================== 레짐 감지기 ====================
class RegimeDetector:
    def __init__(self, config: FixedConfig):
        self.config = config
        self.regime_history = []
        
    def detect(self, vix: float, market_momentum: float = 0) -> MarketRegime:
        """레짐 감지"""
        if vix >= self.config.vix_bear_max:
            return MarketRegime.CRISIS
        elif vix >= self.config.vix_neutral_max:
            return MarketRegime.BEAR
        elif vix >= self.config.vix_bull_max:
            return MarketRegime.NEUTRAL
        else:
            # VIX가 낮더라도 시장 모멘텀이 심하게 음수면 NEUTRAL
            if market_momentum < -0.05:
                return MarketRegime.NEUTRAL
            return MarketRegime.BULL
    
    def get_target_exposure(self, regime: MarketRegime) -> float:
        """레짐별 목표 노출도"""
        return self.config.regime_exposure.get(regime.value, 0.0)


# ==================== 변동성 타겟팅 ====================
class VolatilityTargeting:
    """
    변동성 타겟팅 - 포트폴리오 변동성을 목표 수준으로 유지
    """
    
    def __init__(self, target_vol: float = 0.15, max_vol: float = 0.20):
        self.target_vol = target_vol
        self.max_vol = max_vol
        self.vol_history = []
    
    def calculate_portfolio_vol(
        self, 
        positions: pd.Series, 
        returns: pd.DataFrame,
        window: int = 21
    ) -> float:
        """포트폴리오 변동성 계산"""
        if len(returns) < window:
            return self.target_vol
        
        active = positions[positions > 0.01]
        if len(active) == 0:
            return 0.0
        
        # 포트폴리오 수익률
        weights = active / active.sum()
        port_returns = (returns[active.index].iloc[-window:] * weights).sum(axis=1)
        
        return port_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
    
    def scale_positions(
        self,
        positions: pd.Series,
        returns: pd.DataFrame,
        regime: MarketRegime
    ) -> pd.Series:
        """변동성 기반 포지션 스케일링"""
        if positions.sum() < 0.01:
            return positions
        
        current_vol = self.calculate_portfolio_vol(positions, returns)
        self.vol_history.append(current_vol)
        
        if current_vol < 0.01:
            return positions
        
        # 레짐별 목표 변동성 조정
        regime_vol_target = {
            MarketRegime.BULL: self.target_vol,
            MarketRegime.NEUTRAL: self.target_vol * 0.7,
            MarketRegime.BEAR: self.target_vol * 0.5,
            MarketRegime.CRISIS: self.target_vol * 0.3
        }
        
        target = regime_vol_target.get(regime, self.target_vol)
        
        # 스케일 팩터 계산
        scale = target / current_vol
        scale = np.clip(scale, 0.3, 1.5)  # 극단적 조정 방지
        
        # 변동성이 max_vol 초과 시 강제 축소
        if current_vol > self.max_vol:
            scale = min(scale, self.max_vol / current_vol)
        
        scaled = positions * scale
        
        return scaled


# ==================== 전략 관리자 ====================
class StrategyManager:
    def __init__(self, config: FixedConfig):
        self.config = config
        self.factor_engine = FactorEngine()
        self.regime_detector = RegimeDetector(config)
        self.vol_targeting = VolatilityTargeting(
            target_vol=config.target_volatility,
            max_vol=config.max_volatility
        )
        self.positions = None
        self.prev_regime = None
        
    def calculate_composite_score(
        self, 
        prices: pd.DataFrame, 
        regime: MarketRegime
    ) -> pd.Series:
        """
        레짐별 복합 점수 계산
        """
        momentum = self.factor_engine.calculate_momentum(prices)
        volatility = self.factor_engine.calculate_volatility(prices)
        quality = self.factor_engine.calculate_quality(prices)
        
        # 레짐별 가중치
        weights = {
            MarketRegime.BULL: {'momentum': 0.50, 'volatility': 0.20, 'quality': 0.30},
            MarketRegime.NEUTRAL: {'momentum': 0.15, 'volatility': 0.50, 'quality': 0.35},
            MarketRegime.BEAR: {'momentum': 0.10, 'volatility': 0.40, 'quality': 0.50},
            MarketRegime.CRISIS: {'momentum': 0.0, 'volatility': 0.50, 'quality': 0.50}
        }
        
        w = weights.get(regime, weights[MarketRegime.NEUTRAL])
        
        composite = (
            momentum * w['momentum'] + 
            volatility * w['volatility'] + 
            quality * w['quality']
        )
        
        return composite
    
    def select_stocks(
        self, 
        composite: pd.Series, 
        prices: pd.DataFrame,
        regime: MarketRegime
    ) -> List[str]:
        """종목 선택"""
        # 레짐별 선택 종목 수
        n_stocks = {
            MarketRegime.BULL: self.config.top_n,
            MarketRegime.NEUTRAL: 3,
            MarketRegime.BEAR: 2,
            MarketRegime.CRISIS: 0
        }
        
        n = n_stocks.get(regime, 3)
        if n == 0:
            return []
        
        # 변동성 필터 (NEUTRAL/BEAR에서는 저변동성 종목만)
        if regime in [MarketRegime.NEUTRAL, MarketRegime.BEAR]:
            stock_vol = self.factor_engine.get_stock_volatility(prices)
            # 변동성 하위 50%만 후보
            vol_threshold = stock_vol.quantile(0.5)
            eligible = stock_vol[stock_vol <= vol_threshold].index.tolist()
            composite = composite[composite.index.isin(eligible)]
        
        if len(composite) == 0:
            return []
        
        # 상위 N개 선택
        return composite.nlargest(n).index.tolist()
    
    def calculate_positions(
        self,
        selected: List[str],
        composite: pd.Series,
        prices: pd.DataFrame,
        regime: MarketRegime
    ) -> pd.Series:
        """포지션 계산"""
        positions = pd.Series(0.0, index=prices.columns)
        
        if not selected:
            return positions
        
        target_exposure = self.regime_detector.get_target_exposure(regime)
        
        if target_exposure < 0.01:
            return positions
        
        # 점수 기반 가중치
        scores = composite[selected]
        if scores.sum() > 0:
            weights = scores / scores.sum()
        else:
            weights = pd.Series(1/len(selected), index=selected)
        
        # 목표 노출도 적용
        for ticker in selected:
            positions[ticker] = weights[ticker] * target_exposure
        
        # 최대 단일 포지션 제한
        positions = positions.clip(upper=self.config.max_single_position)
        
        # 재정규화
        if positions.sum() > target_exposure:
            positions = positions / positions.sum() * target_exposure
        
        return positions
    
    def update(
        self,
        prices: pd.DataFrame,
        vix: float,
        date: pd.Timestamp
    ) -> Dict:
        """포트폴리오 업데이트"""
        # 시장 모멘텀 계산
        if len(prices) >= 10:
            market = prices.mean(axis=1)
            market_momentum = market.iloc[-1] / market.iloc[-6] - 1 if len(market) >= 6 else 0
        else:
            market_momentum = 0
        
        # 레짐 감지
        regime = self.regime_detector.detect(vix, market_momentum)
        
        # 레짐 변경 로깅
        if self.prev_regime != regime:
            logger.info(f"Regime change: {self.prev_regime} -> {regime.value} (VIX: {vix:.1f})")
            self.prev_regime = regime
        
        # CRISIS면 즉시 현금
        if regime == MarketRegime.CRISIS:
            new_positions = pd.Series(0.0, index=prices.columns)
        else:
            # 복합 점수 계산
            composite = self.calculate_composite_score(prices, regime)
            
            # 종목 선택
            selected = self.select_stocks(composite, prices, regime)
            
            # 포지션 계산
            new_positions = self.calculate_positions(
                selected, composite, prices, regime
            )
            
            # 변동성 타겟팅
            if len(prices) >= 21:
                returns = prices.pct_change()
                new_positions = self.vol_targeting.scale_positions(
                    new_positions, returns, regime
                )
        
        # 거래 비용 계산
        if self.positions is not None:
            turnover = (new_positions - self.positions).abs().sum()
            
            # 리밸런싱 임계값 체크
            if turnover < self.config.rebalance_threshold * 2:
                max_change = (new_positions - self.positions).abs().max()
                if max_change < self.config.rebalance_threshold:
                    new_positions = self.positions.copy()
                    turnover = 0
            
            cost = turnover * self.config.transaction_cost
        else:
            cost = 0.0
        
        self.positions = new_positions.copy()
        
        return {
            'date': date,
            'regime': regime.value,
            'vix': vix,
            'market_momentum': market_momentum,
            'positions': new_positions.to_dict(),
            'equity_weight': new_positions.sum(),
            'transaction_cost': cost,
            'num_positions': (new_positions > 0.01).sum()
        }


# ==================== 백테스터 ====================
class WalkForwardBacktester:
    def __init__(
        self,
        config: FixedConfig,
        train_months: int = 12,
        test_months: int = 3
    ):
        self.config = config
        self.train_months = train_months
        self.test_months = test_months
        
    def run(self, prices: pd.DataFrame, vix: pd.Series) -> Dict:
        """백테스트 실행"""
        results = {
            'dates': [],
            'returns': [],
            'regimes': [],
            'vix_values': [],
            'equity_weights': [],
            'portfolio_values': [],
            'invested': [],
            'num_positions': [],
            'transaction_costs': []
        }
        
        dates = prices.index.unique().sort_values()
        train_days = self.train_months * 21
        test_days = self.test_months * 21
        total_days = len(dates)
        
        logger.info(f"Total days: {total_days}, Train: {train_days}, Test: {test_days}")
        
        strategy = StrategyManager(self.config)
        
        portfolio_value = 1.0
        prev_positions = pd.Series(0.0, index=prices.columns)
        prev_equity_weight = 0.0
        
        for start_idx in range(train_days, total_days - test_days, test_days):
            test_start = dates[start_idx]
            test_end = dates[min(start_idx + test_days - 1, total_days - 1)]
            test_dates = dates[(dates >= test_start) & (dates <= test_end)]
            
            logger.info(f"Testing: {test_start.date()} ~ {test_end.date()}")
            
            for i, date in enumerate(test_dates):
                current_prices = prices.loc[date]
                current_vix = vix.loc[date] if date in vix.index else 20.0
                
                # 포트폴리오 업데이트 (date까지 데이터만 사용)
                signal_prices = prices.loc[:date]
                update = strategy.update(signal_prices, current_vix, date)
                
                positions = pd.Series(update['positions'])
                equity_weight = update['equity_weight']
                regime = update['regime']
                invested = equity_weight > 0.05
                
                # 수익률 계산
                if i > 0:
                    prev_date = test_dates[i-1]
                    prev_prices = prices.loc[prev_date]
                    
                    if prev_equity_weight > 0.01:
                        price_returns = (current_prices / prev_prices - 1).fillna(0)
                        stock_return = (prev_positions * price_returns).sum()
                    else:
                        stock_return = 0.0
                    
                    cash_weight = max(0, 1.0 - prev_equity_weight)
                    cash_return = cash_weight * (RISK_FREE_RATE / TRADING_DAYS_YEAR)
                    
                    cost = update['transaction_cost']
                    net_return = stock_return + cash_return - cost
                    portfolio_value *= (1 + net_return)
                    
                    results['dates'].append(date)
                    results['returns'].append(net_return)
                    results['regimes'].append(regime)
                    results['vix_values'].append(current_vix)
                    results['equity_weights'].append(equity_weight)
                    results['portfolio_values'].append(portfolio_value)
                    results['invested'].append(invested)
                    results['num_positions'].append(update['num_positions'])
                    results['transaction_costs'].append(cost)
                
                prev_positions = positions.copy()
                prev_equity_weight = equity_weight
        
        return results
    
    def calculate_metrics(self, results: Dict) -> Dict:
        """성과 지표 계산"""
        if len(results['returns']) == 0:
            return {'sharpe_ratio': 0}
        
        returns = pd.Series(results['returns'], index=results['dates'])
        invested = pd.Series(results['invested'], index=results['dates'])
        regimes = pd.Series(results['regimes'], index=results['dates'])
        equity_weights = pd.Series(results['equity_weights'], index=results['dates'])
        
        # 전체 지표
        total_return = (1 + returns).prod() - 1
        n_years = len(returns) / TRADING_DAYS_YEAR
        annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
        annual_vol = returns.std() * np.sqrt(TRADING_DAYS_YEAR)
        sharpe = (annual_return - RISK_FREE_RATE) / annual_vol if annual_vol > 0 else 0
        
        # MDD
        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        max_drawdown = drawdown.min()
        
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0
        win_rate = (returns > 0).sum() / len(returns)
        
        # Sortino
        downside = returns[returns < 0]
        downside_std = downside.std() * np.sqrt(TRADING_DAYS_YEAR) if len(downside) > 0 else 0.01
        sortino = (annual_return - RISK_FREE_RATE) / downside_std
        
        avg_equity = equity_weights.mean()
        
        # 기간별 지표
        period_metrics = {}
        
        invested_returns = returns[invested]
        if len(invested_returns) > 10:
            i_total = (1 + invested_returns).prod() - 1
            i_years = len(invested_returns) / TRADING_DAYS_YEAR
            i_annual = (1 + i_total) ** (1 / i_years) - 1 if i_years > 0 else 0
            i_vol = invested_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
            i_sharpe = (i_annual - RISK_FREE_RATE) / i_vol if i_vol > 0 else 0
            
            period_metrics['invested'] = {
                'count': len(invested_returns),
                'annual_return': i_annual,
                'annual_volatility': i_vol,
                'sharpe': i_sharpe,
                'win_rate': (invested_returns > 0).sum() / len(invested_returns)
            }
        
        cash_returns = returns[~invested]
        if len(cash_returns) > 10:
            c_total = (1 + cash_returns).prod() - 1
            c_years = len(cash_returns) / TRADING_DAYS_YEAR
            c_annual = (1 + c_total) ** (1 / c_years) - 1 if c_years > 0 else 0
            c_vol = cash_returns.std() * np.sqrt(TRADING_DAYS_YEAR) if cash_returns.std() > 0 else 0.01
            c_sharpe = (c_annual - RISK_FREE_RATE) / c_vol
            
            period_metrics['cash'] = {
                'count': len(cash_returns),
                'annual_return': c_annual,
                'sharpe': c_sharpe,
                'win_rate': (cash_returns > 0).sum() / len(cash_returns) if len(cash_returns) > 0 else 0
            }
        
        # 레짐별 지표
        regime_metrics = {}
        for regime in ['BULL', 'NEUTRAL', 'BEAR', 'CRISIS']:
            r_returns = returns[regimes == regime]
            if len(r_returns) > 5:
                r_total = (1 + r_returns).prod() - 1
                r_years = len(r_returns) / TRADING_DAYS_YEAR
                r_annual = (1 + r_total) ** (1 / r_years) - 1 if r_years > 0 else 0
                r_vol = r_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
                r_sharpe = (r_annual - RISK_FREE_RATE) / r_vol if r_vol > 0 else 0
                
                regime_metrics[regime] = {
                    'count': len(r_returns),
                    'pct': len(r_returns) / len(returns),
                    'annual_return': r_annual,
                    'sharpe': r_sharpe,
                    'win_rate': (r_returns > 0).sum() / len(r_returns)
                }
        
        # 연도별 지표
        yearly_metrics = {}
        returns_df = pd.DataFrame({'returns': returns})
        returns_df['year'] = returns_df.index.year
        
        for year in returns_df['year'].unique():
            y_returns = returns_df[returns_df['year'] == year]['returns']
            if len(y_returns) > 20:
                y_total = (1 + y_returns).prod() - 1
                y_vol = y_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
                y_sharpe = (y_total - RISK_FREE_RATE * len(y_returns) / TRADING_DAYS_YEAR) / y_vol if y_vol > 0 else 0
                
                yearly_metrics[int(year)] = {
                    'return': y_total,
                    'volatility': y_vol,
                    'sharpe': y_sharpe,
                    'win_rate': (y_returns > 0).sum() / len(y_returns)
                }
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'annual_volatility': annual_vol,
            'sharpe_ratio': sharpe,
            'sortino_ratio': sortino,
            'calmar_ratio': calmar,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'avg_equity_weight': avg_equity,
            'total_days': len(returns),
            'invested_days': int(invested.sum()),
            'cash_days': int((~invested).sum()),
            'investment_ratio': invested.sum() / len(returns),
            'total_transaction_costs': sum(results['transaction_costs']),
            'period_metrics': period_metrics,
            'regime_metrics': regime_metrics,
            'yearly_metrics': yearly_metrics
        }


# ==================== 결과 출력 ====================
def print_results(metrics: Dict, config: FixedConfig):
    """결과 출력"""
    print("\n" + "=" * 80)
    print("🚀 ARES ULTIMATE v4.1 FIXED - BACKTEST RESULTS")
    print("=" * 80)
    
    print("\n📊 CONFIGURATION (CONSERVATIVE)")
    print("-" * 40)
    print(f"  Target Volatility:   {config.target_volatility:.0%}")
    print(f"  Max Volatility:      {config.max_volatility:.0%}")
    print(f"  VIX Thresholds:      Bull<{config.vix_bull_max}, Neutral<{config.vix_neutral_max}, Bear<{config.vix_bear_max}")
    print(f"  Regime Exposures:")
    print(f"    BULL:    {config.regime_exposure['BULL']:.0%}")
    print(f"    NEUTRAL: {config.regime_exposure['NEUTRAL']:.0%}")
    print(f"    BEAR:    {config.regime_exposure['BEAR']:.0%}")
    print(f"    CRISIS:  {config.regime_exposure['CRISIS']:.0%}")
    
    print("\n📈 OVERALL PERFORMANCE")
    print("-" * 40)
    print(f"  Total Return:        {metrics['total_return']:>10.2%}")
    print(f"  Annual Return:       {metrics['annual_return']:>10.2%}")
    print(f"  Annual Volatility:   {metrics['annual_volatility']:>10.2%}")
    print(f"  Sharpe Ratio:        {metrics['sharpe_ratio']:>10.2f}")
    print(f"  Sortino Ratio:       {metrics['sortino_ratio']:>10.2f}")
    print(f"  Calmar Ratio:        {metrics['calmar_ratio']:>10.2f}")
    print(f"  Max Drawdown:        {metrics['max_drawdown']:>10.2%}")
    print(f"  Win Rate:            {metrics['win_rate']:>10.2%}")
    
    print("\n📊 INVESTMENT STATISTICS")
    print("-" * 40)
    print(f"  Total Days:          {metrics['total_days']:>10}")
    print(f"  Invested Days:       {metrics['invested_days']:>10}")
    print(f"  Cash Days:           {metrics['cash_days']:>10}")
    print(f"  Investment Ratio:    {metrics['investment_ratio']:>10.2%}")
    print(f"  Avg Equity Weight:   {metrics['avg_equity_weight']:>10.2%}")
    
    print("\n⏱️ PERIOD PERFORMANCE")
    print("-" * 40)
    for period, pm in metrics.get('period_metrics', {}).items():
        print(f"  {period.upper():12} | Days: {pm['count']:4} | "
              f"Return: {pm['annual_return']:7.2%} | "
              f"Sharpe: {pm['sharpe']:5.2f} | "
              f"WinRate: {pm['win_rate']:6.2%}")
    
    print("\n🎯 REGIME PERFORMANCE")
    print("-" * 40)
    for regime, rm in metrics.get('regime_metrics', {}).items():
        pct = rm.get('pct', 0)
        print(f"  {regime:12} | Days: {rm['count']:4} ({pct:5.1%}) | "
              f"Return: {rm['annual_return']:7.2%} | "
              f"Sharpe: {rm['sharpe']:5.2f} | "
              f"WinRate: {rm['win_rate']:6.2%}")
    
    print("\n📅 YEARLY PERFORMANCE")
    print("-" * 40)
    for year, ym in sorted(metrics.get('yearly_metrics', {}).items()):
        print(f"  {year} | Return: {ym['return']:7.2%} | "
              f"Vol: {ym['volatility']:6.2%} | "
              f"Sharpe: {ym['sharpe']:5.2f}")
    
    # 목표 체크
    print("\n" + "=" * 80)
    print("🎯 TARGET CHECK")
    print("=" * 80)
    
    sharpe = metrics['sharpe_ratio']
    inv_ratio = metrics['investment_ratio']
    mdd = metrics['max_drawdown']
    vol = metrics['annual_volatility']
    
    targets = [
        ("Sharpe >= 2.0", sharpe >= 2.0, f"{sharpe:.2f}"),
        ("Investment >= 30%", inv_ratio >= 0.30, f"{inv_ratio:.1%}"),
        ("MDD >= -15%", mdd >= -0.15, f"{mdd:.2%}"),
        ("Volatility <= 20%", vol <= 0.20, f"{vol:.1%}")
    ]
    
    for target, achieved, value in targets:
        status = "✅" if achieved else "❌"
        print(f"  {status} {target:25} | Actual: {value}")
    
    print("=" * 80)


# ==================== 메인 ====================
def main():
    print("=" * 80)
    print("ARES ULTIMATE v4.1 FIXED - Conservative Strategy")
    print("=" * 80)
    
    # 보수적 설정
    config = FixedConfig()
    
    # 종목 리스트
    tickers = [
        'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'NVDA', 'TSLA', 'META', 'AMD',
        'AVGO', 'ADBE', 'CRM', 'ORCL', 'CSCO', 'INTC', 'QCOM',
        'ASML', 'MU', 'LRCX', 'AMAT', 'KLAC', 'TXN', 'MRVL',
        'CRWD', 'DDOG', 'CDNS', 'SNPS', 'PANW', 'ZS', 'NOW', 'WDAY',
        'ISRG', 'VRTX', 'REGN', 'MRNA', 'GILD', 'AMGN', 'BIIB',
        'BKNG', 'ACN', 'COST', 'PEP', 'KO', 'NKE', 'SBUX'
    ]
    
    start_date = "2019-01-01"
    end_date = "2025-01-01"
    
    # 데이터 로딩
    loader = DataLoader(DB_PATH)
    loader.connect()
    
    try:
        logger.info(f"Loading data: {start_date} ~ {end_date}")
        
        daily_df = loader.load_daily_data(start_date, end_date, tickers)
        daily_df = daily_df.drop_duplicates(subset=['date', 'ticker'], keep='last')
        prices = daily_df.pivot(index='date', columns='ticker', values='close').ffill()
        
        valid_tickers = [t for t in tickers if t in prices.columns]
        prices = prices[valid_tickers]
        
        logger.info(f"Price matrix: {prices.shape}")
        
        vix_df = loader.load_vix_data(start_date, end_date)
        vix = vix_df.set_index('date')['vix']
        vix = vix.reindex(prices.index).ffill().fillna(20.0)
        
        # 백테스트 실행
        backtester = WalkForwardBacktester(
            config=config,
            train_months=12,
            test_months=3
        )
        
        logger.info("Running Walk-Forward backtest...")
        results = backtester.run(prices, vix)
        metrics = backtester.calculate_metrics(results)
        
        # 결과 출력
        print_results(metrics, config)
        
        # 결과 저장
        results_df = pd.DataFrame(results)
        results_df.to_csv('/home/ubuntu/ares_v41_fixed_results.csv', index=False)
        
        with open('/home/ubuntu/ares_v41_fixed_metrics.json', 'w') as f:
            def convert(obj):
                if isinstance(obj, (np.floating, np.integer)):
                    return float(obj)
                elif isinstance(obj, dict):
                    return {k: convert(v) for k, v in obj.items()}
                return obj
            json.dump(convert(metrics), f, indent=2)
        
        logger.info("Results saved to /home/ubuntu/ares_v41_fixed_*.csv/json")
        
        return metrics
        
    finally:
        loader.close()


if __name__ == "__main__":
    main()
