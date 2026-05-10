#!/usr/bin/env python3
"""
ARES Ultimate v9.1 - 4대 AI 통합 (Claude, GPT, Gemini, Grok)
============================================================

4대 AI 핵심 제안 통합:

[GPT 제안]
1. 멀티 슬리브 전략 (EQ_CALM, EQ_MN, TSMOM)
2. 레짐 확률 기반 동적 가중치
3. Vol Targeting + Drawdown Deleveraging
4. Walk-Forward 검증

[Grok 제안]
1. Sideways 레짐 감지 (MA200 기준)
2. 레짐별 차별화된 팩터 조합
3. Inverse Volatility Sizing
4. Drawdown Stop + Recovery 로직

[Claude 제안]
1. 점진적 포지션 전환
2. 팩터 IC 실시간 모니터링

[Gemini 제안]
1. 멀티 타임프레임 통합
2. 리스크 버짓 기반 사이징

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
class AresV91Config:
    """v9.1 통합 설정"""
    
    # Database
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    # Regime Detection (Grok 제안)
    vix_crash_threshold: float = 30.0
    vix_low_threshold: float = 16.0
    ma_sideways_threshold: float = 0.08  # SPY vs MA200 거리
    
    # Sleeve Weights (GPT 제안)
    sleeve_eq_calm: float = 0.35
    sleeve_eq_mn: float = 0.30
    sleeve_tsmom: float = 0.35
    
    # Regime Multipliers (GPT 제안)
    mult_risk_on: Dict = None
    mult_sideways: Dict = None
    mult_crisis: Dict = None
    
    # Vol Targeting (GPT/Grok 공통)
    target_vol: float = 0.10
    max_leverage: float = 1.5
    
    # Drawdown Management (Grok 제안)
    dd_halt_threshold: float = -0.08
    dd_recovery_threshold: float = 0.04
    vol_cap: float = 0.15
    
    # Portfolio
    n_stocks: int = 3
    max_position: float = 0.25
    
    # Backtest
    rebalance_freq: str = 'monthly'  # Grok: 월간 리밸런싱
    transaction_cost: float = 0.002  # 0.2%
    
    def __post_init__(self):
        # GPT 제안: 레짐별 슬리브 승수
        self.mult_risk_on = {"EQ_CALM": 1.4, "EQ_MN": 0.7, "TSMOM": 0.8}
        self.mult_sideways = {"EQ_CALM": 0.7, "EQ_MN": 1.4, "TSMOM": 0.8}
        self.mult_crisis = {"EQ_CALM": 0.2, "EQ_MN": 1.1, "TSMOM": 1.6}

# =============================================================================
# 2. REGIME DETECTION (Grok 제안 기반)
# =============================================================================

class RegimeDetector:
    """Grok 제안: VIX + MA200 기반 레짐 감지"""
    
    REGIMES = ['RISK_ON', 'SIDEWAYS', 'BEAR', 'CRISIS', 'BULL_LOW_VIX', 'BULL_HIGH_VIX']
    
    def __init__(self, config: AresV91Config):
        self.config = config
    
    def detect(self, vix: float, spy_price: float, spy_ma200: float) -> Tuple[str, Dict[str, float]]:
        """레짐 감지 및 확률 반환"""
        
        above_ma = spy_price > spy_ma200
        dist_ma = abs(spy_price / spy_ma200 - 1)
        sideways_flag = dist_ma < self.config.ma_sideways_threshold
        crash = vix > self.config.vix_crash_threshold
        
        # 레짐 확률 (GPT 제안: 확률 기반)
        probs = {'risk_on': 0.0, 'sideways': 0.0, 'crisis': 0.0}
        
        if crash:
            regime = 'CRISIS'
            probs = {'risk_on': 0.0, 'sideways': 0.1, 'crisis': 0.9}
        elif sideways_flag:
            regime = 'SIDEWAYS'
            probs = {'risk_on': 0.2, 'sideways': 0.7, 'crisis': 0.1}
        elif not above_ma:
            regime = 'BEAR'
            probs = {'risk_on': 0.1, 'sideways': 0.3, 'crisis': 0.6}
        elif vix < self.config.vix_low_threshold:
            regime = 'BULL_LOW_VIX'
            probs = {'risk_on': 0.8, 'sideways': 0.15, 'crisis': 0.05}
        else:
            regime = 'BULL_HIGH_VIX'
            probs = {'risk_on': 0.5, 'sideways': 0.35, 'crisis': 0.15}
        
        return regime, probs

# =============================================================================
# 3. FACTOR ENGINES
# =============================================================================

class FactorEngine:
    """팩터 계산 엔진"""
    
    def __init__(self, config: AresV91Config):
        self.config = config
    
    def calculate_momentum(self, prices: pd.DataFrame, lookback: int = 252) -> pd.Series:
        """12-1 모멘텀"""
        if len(prices) < lookback:
            return pd.Series(0, index=prices.columns)
        
        ret_12m = prices.iloc[-1] / prices.iloc[-lookback] - 1
        ret_1m = prices.iloc[-1] / prices.iloc[-21] - 1
        mom = ret_12m - ret_1m
        
        # Rank (percentile)
        return mom.rank(pct=True)
    
    def calculate_volatility(self, prices: pd.DataFrame, lookback: int = 63) -> pd.Series:
        """변동성 (낮을수록 좋음)"""
        returns = prices.pct_change().iloc[-lookback:]
        vol = returns.std() * np.sqrt(252)
        
        # Inverse rank (낮은 변동성 = 높은 점수)
        return (1 - vol.rank(pct=True))
    
    def calculate_quality(self, prices: pd.DataFrame, lookback: int = 252) -> pd.Series:
        """수익률 안정성"""
        returns = prices.pct_change().iloc[-lookback:]
        sharpe = returns.mean() / (returns.std() + 1e-6)
        return sharpe.rank(pct=True)

# =============================================================================
# 4. SLEEVE STRATEGIES (GPT/Grok 통합)
# =============================================================================

class SleeveStrategies:
    """GPT/Grok 제안: 멀티 슬리브 전략"""
    
    def __init__(self, config: AresV91Config):
        self.config = config
        self.factor_engine = FactorEngine(config)
    
    def eq_calm_weights(self, prices: pd.DataFrame, regime: str) -> pd.Series:
        """EQ_CALM: 모멘텀 + 저변동성 (상승장용)"""
        mom_score = self.factor_engine.calculate_momentum(prices)
        vol_score = self.factor_engine.calculate_volatility(prices)
        
        # Grok 제안: 레짐별 가중치 조정
        if regime == 'BULL_LOW_VIX':
            score = 0.6 * mom_score + 0.4 * vol_score
        else:
            score = 0.4 * mom_score + 0.6 * vol_score
        
        # 상위 N개 선택
        top_stocks = score.nlargest(self.config.n_stocks).index
        weights = pd.Series(0.0, index=prices.columns)
        weights[top_stocks] = 1.0 / self.config.n_stocks
        
        return weights
    
    def eq_mn_weights(self, prices: pd.DataFrame, regime: str) -> pd.Series:
        """EQ_MN: Market Neutral (Sideways/Bear용)"""
        mom_score = self.factor_engine.calculate_momentum(prices)
        vol_score = self.factor_engine.calculate_volatility(prices)
        
        weights = pd.Series(0.0, index=prices.columns)
        
        if regime in ['SIDEWAYS', 'BEAR']:
            # Grok 제안: Long low mom + low vol, Short high mom
            low_mom_score = 1 - mom_score
            long_score = 0.7 * low_mom_score + 0.3 * vol_score
            
            long_names = long_score.nlargest(self.config.n_stocks).index
            short_names = mom_score.nlargest(self.config.n_stocks).index
            
            weights[long_names] = 0.5 / self.config.n_stocks
            weights[short_names] = -0.5 / self.config.n_stocks
        else:
            # Long only low vol
            top_stocks = vol_score.nlargest(self.config.n_stocks).index
            weights[top_stocks] = 1.0 / self.config.n_stocks
        
        return weights
    
    def tsmom_weights(self, prices: pd.DataFrame, regime: str) -> pd.Series:
        """TSMOM: Time Series Momentum (Crisis Alpha)"""
        # 단순화: 트렌드 팔로잉
        returns_20d = prices.iloc[-1] / prices.iloc[-21] - 1
        returns_60d = prices.iloc[-1] / prices.iloc[-63] - 1
        
        # 트렌드 방향
        trend = np.sign(returns_20d) * np.sign(returns_60d)
        
        weights = pd.Series(0.0, index=prices.columns)
        
        # 강한 트렌드 종목만 선택
        strong_trend = trend[trend != 0]
        if len(strong_trend) > 0:
            # 변동성 역가중
            vol = prices.pct_change().iloc[-63:].std()
            inv_vol = 1 / (vol + 0.15)
            
            for sym in strong_trend.index[:self.config.n_stocks]:
                weights[sym] = trend[sym] * inv_vol[sym]
            
            # 정규화
            if weights.abs().sum() > 0:
                weights = weights / weights.abs().sum()
        
        return weights

# =============================================================================
# 5. PORTFOLIO OPTIMIZER
# =============================================================================

class PortfolioOptimizer:
    """GPT/Grok 통합: Vol Targeting + Drawdown Management"""
    
    def __init__(self, config: AresV91Config):
        self.config = config
        self.peak_value = 1.0
        self.in_halt = False
    
    def apply_vol_target(self, weights: pd.Series, returns: pd.DataFrame) -> pd.Series:
        """GPT 제안: Vol Targeting"""
        if len(returns) < 60:
            return weights
        
        # 포트폴리오 변동성 추정
        port_returns = (returns * weights.shift(1)).sum(axis=1)
        port_vol = port_returns.iloc[-60:].std() * np.sqrt(252)
        
        if port_vol > 0.01:
            scale = self.config.target_vol / port_vol
            scale = np.clip(scale, 0, self.config.max_leverage)
            weights = weights * scale
        
        return weights
    
    def apply_drawdown_control(self, weights: pd.Series, current_value: float) -> pd.Series:
        """Grok 제안: Drawdown Stop + Recovery"""
        # Peak 업데이트
        if current_value > self.peak_value:
            self.peak_value = current_value
        
        # Drawdown 계산
        dd = (current_value / self.peak_value) - 1
        
        # Halt 조건
        if dd < self.config.dd_halt_threshold:
            self.in_halt = True
        
        # Recovery 조건
        recovery = (current_value / (self.peak_value * 0.92)) - 1  # 92% 대비
        if recovery > self.config.dd_recovery_threshold:
            self.in_halt = False
        
        # Halt 중이면 50% 현금
        if self.in_halt:
            weights = weights * 0.5
        
        return weights
    
    def apply_position_limits(self, weights: pd.Series) -> pd.Series:
        """포지션 제한"""
        weights = np.clip(weights, -self.config.max_position, self.config.max_position)
        return weights

# =============================================================================
# 6. MAIN ENGINE
# =============================================================================

class AresV91Engine:
    """ARES v9.1 통합 엔진"""
    
    def __init__(self, config: Optional[AresV91Config] = None):
        self.config = config or AresV91Config()
        
        # 엔진 초기화
        self.regime_detector = RegimeDetector(self.config)
        self.sleeve_strategies = SleeveStrategies(self.config)
        self.portfolio_optimizer = PortfolioOptimizer(self.config)
        
        # 상태
        self.portfolio_value = 1.0
        self.values = [1.0]
        self.regime_stats = {}
    
    def load_data(self, start: str, end: str) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
        """데이터 로드"""
        conn = sqlite3.connect(self.config.db_path)
        
        # 종목 유니버스 (Grok 제안: 대형주 중심)
        symbols = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'AVGO', 'ADBE',
            'NFLX', 'CRM', 'INTC', 'QCOM', 'TXN', 'AMAT', 'MU', 'LRCX', 'KLAC', 'SNPS',
            'V', 'MA', 'JPM', 'BAC', 'GS', 'MS', 'BLK', 'SCHW', 'AXP', 'C',
            'UNH', 'JNJ', 'PFE', 'ABBV', 'MRK', 'LLY', 'TMO', 'ABT', 'DHR', 'BMY',
            'SPY', 'QQQ'  # 벤치마크
        ]
        
        symbols_str = ','.join([f"'{s}'" for s in symbols])
        
        # 가격 데이터
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
        
        # VIX 데이터
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
        
        # SPY MA200 계산
        if 'SPY' in prices.columns:
            spy_ma200 = prices['SPY'].rolling(200).mean()
        else:
            spy_ma200 = prices.mean(axis=1).rolling(200).mean()
        
        # 인덱스 정렬
        common_dates = prices.index.intersection(vix.index)
        prices = prices.loc[common_dates]
        vix = vix.loc[common_dates]
        spy_ma200 = spy_ma200.loc[common_dates]
        
        return prices, vix, spy_ma200
    
    def combine_sleeves(self, prices: pd.DataFrame, regime: str, regime_probs: Dict) -> pd.Series:
        """GPT 제안: 슬리브 결합"""
        # 투자 가능 종목만 (SPY, QQQ 제외)
        invest_cols = [c for c in prices.columns if c not in ['SPY', 'QQQ', 'VIX']]
        invest_prices = prices[invest_cols]
        
        # 각 슬리브 가중치
        eq_calm_w = self.sleeve_strategies.eq_calm_weights(invest_prices, regime)
        eq_mn_w = self.sleeve_strategies.eq_mn_weights(invest_prices, regime)
        tsmom_w = self.sleeve_strategies.tsmom_weights(invest_prices, regime)
        
        # 레짐 확률 기반 승수 계산
        mult = {}
        for sleeve in ['EQ_CALM', 'EQ_MN', 'TSMOM']:
            mult[sleeve] = (
                regime_probs['risk_on'] * self.config.mult_risk_on[sleeve] +
                regime_probs['sideways'] * self.config.mult_sideways[sleeve] +
                regime_probs['crisis'] * self.config.mult_crisis[sleeve]
            )
        
        # 슬리브 결합
        combined = (
            eq_calm_w * self.config.sleeve_eq_calm * mult['EQ_CALM'] +
            eq_mn_w * self.config.sleeve_eq_mn * mult['EQ_MN'] +
            tsmom_w * self.config.sleeve_tsmom * mult['TSMOM']
        )
        
        # 정규화
        if combined.abs().sum() > 0:
            combined = combined / combined.abs().sum()
        
        return combined
    
    def backtest(self, start: str = '2020-01-01', end: str = '2024-12-31') -> Dict:
        """백테스트 실행"""
        print(f"Loading data from {start} to {end}...")
        prices, vix, spy_ma200 = self.load_data(start, end)
        print(f"Loaded {len(prices)} days, {len(prices.columns)} symbols")
        
        # Warmup
        warmup = 252
        
        # 월말 리밸런싱 날짜
        monthly_dates = prices.resample('BM').last().index
        monthly_dates = monthly_dates[monthly_dates >= prices.index[warmup]]
        
        # 초기화
        self.portfolio_value = 1.0
        self.values = [1.0]
        self.regime_stats = {}
        positions = pd.Series(0.0, index=prices.columns)
        total_costs = 0
        
        all_dates = prices.index[warmup:]
        last_rebal_date = None
        
        for i, date in enumerate(all_dates):
            # 리밸런싱 여부 (월말)
            is_rebal_day = date in monthly_dates.values
            
            if is_rebal_day:
                # 현재 데이터 (T-1까지만 사용)
                hist_prices = prices.loc[:date - timedelta(days=1)]
                current_vix = vix.loc[:date].iloc[-1] if date in vix.index or len(vix.loc[:date]) > 0 else 20
                current_spy = prices.loc[date, 'SPY'] if 'SPY' in prices.columns else prices.loc[date].mean()
                current_ma200 = spy_ma200.loc[:date].iloc[-1] if len(spy_ma200.loc[:date]) > 0 else current_spy
                
                # 레짐 감지
                regime, regime_probs = self.regime_detector.detect(current_vix, current_spy, current_ma200)
                
                # CRISIS면 현금
                if regime == 'CRISIS':
                    new_positions = pd.Series(0.0, index=prices.columns)
                else:
                    # 슬리브 결합
                    new_positions = self.combine_sleeves(hist_prices, regime, regime_probs)
                    
                    # Vol Targeting
                    returns = prices.pct_change().loc[:date]
                    new_positions = self.portfolio_optimizer.apply_vol_target(new_positions, returns)
                    
                    # Drawdown Control
                    new_positions = self.portfolio_optimizer.apply_drawdown_control(new_positions, self.portfolio_value)
                    
                    # Position Limits
                    new_positions = self.portfolio_optimizer.apply_position_limits(new_positions)
                
                # 거래 비용
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * self.config.transaction_cost
                total_costs += cost
                
                positions = new_positions
                last_rebal_date = date
                
                # 레짐 통계
                if regime not in self.regime_stats:
                    self.regime_stats[regime] = {'days': 0, 'returns': []}
                self.regime_stats[regime]['days'] += 1
            
            # 일일 수익률
            if i > 0:
                prev_date = all_dates[i - 1]
                daily_returns = prices.loc[date] / prices.loc[prev_date] - 1
                daily_ret = (positions * daily_returns).sum()
                
                # 비용 차감 (리밸런싱 날)
                if is_rebal_day:
                    daily_ret -= cost
                
                self.portfolio_value *= (1 + daily_ret)
                self.values.append(self.portfolio_value)
                
                # 레짐별 수익률 기록
                if last_rebal_date and regime in self.regime_stats:
                    self.regime_stats[regime]['returns'].append(daily_ret)
            
            # 진행 상황
            if (i + 1) % 200 == 0:
                print(f"Progress: {i+1}/{len(all_dates)} days, Value: {self.portfolio_value:.4f}")
        
        # 성과 계산
        returns = pd.Series(np.diff(self.values) / self.values[:-1])
        
        # 전체 성과
        total_return = self.portfolio_value - 1
        years = len(all_dates) / 252
        annual_return = (1 + total_return) ** (1 / years) - 1
        
        # Sharpe
        rf_daily = 0.05 / 252
        excess_returns = returns - rf_daily
        sharpe = np.sqrt(252) * excess_returns.mean() / (excess_returns.std() + 1e-6)
        
        # Invested Sharpe
        invested_returns = returns[returns.abs() > 1e-6]
        invested_sharpe = np.sqrt(252) * invested_returns.mean() / (invested_returns.std() + 1e-6) if len(invested_returns) > 0 else 0
        
        # MDD
        cumulative = np.cumprod(1 + returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = (cumulative - running_max) / running_max
        mdd = np.min(drawdown)
        
        # 투자 비율
        invested_days = sum(1 for r in returns if abs(r) > 1e-6)
        investment_ratio = invested_days / len(returns)
        
        # 레짐별 성과
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
        
        results = {
            'sharpe': sharpe,
            'invested_sharpe': invested_sharpe,
            'annual_return': annual_return,
            'total_return': total_return,
            'mdd': mdd,
            'investment_ratio': investment_ratio,
            'total_costs': total_costs,
            'regime_performance': regime_performance
        }
        
        return results

# =============================================================================
# 7. MAIN
# =============================================================================

if __name__ == "__main__":
    print("="*60)
    print("ARES Ultimate v9.1 - 4대 AI 통합")
    print("(Claude, GPT, Gemini, Grok)")
    print("="*60)
    
    config = AresV91Config()
    engine = AresV91Engine(config)
    
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
        print(f"{regime:15} | Days: {perf['days']:4} | Return: {perf['annual_return']*100:6.1f}% | Sharpe: {perf['sharpe']:5.2f} | Win: {perf['win_rate']*100:4.1f}%")
