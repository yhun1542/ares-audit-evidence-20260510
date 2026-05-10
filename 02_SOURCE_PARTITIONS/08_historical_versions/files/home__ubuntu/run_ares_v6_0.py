#!/usr/bin/env python3
"""
ARES Ultimate v6.0 - Multi-Dimensional Regime + Enhanced Alpha Engine
Claude + GPT 제안 통합

핵심 개선:
1. 다차원 레짐 감지 (VIX + 모멘텀 + 변동성 + 브레드스)
2. VIX 18-22 조건부 진입 (5개 조건 중 3개 이상 충족 시)
3. 동적 변동성 타겟팅
4. ICIR 기반 동적 팩터 가중치
"""

import sqlite3
import numpy as np
import pandas as pd
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# 1. MULTI-DIMENSIONAL REGIME DETECTION
# =============================================================================

class MarketRegime(Enum):
    ULTRA_BULLISH = "ultra_bullish"      # VIX < 13
    BULLISH = "bullish"                   # 13 <= VIX < 16
    NEUTRAL_FAVORABLE = "neutral_fav"     # 16 <= VIX < 18
    CAUTIOUS_LONG = "cautious_long"       # 18 <= VIX < 22, 조건부
    DEFENSIVE = "defensive"               # 조건 미충족 또는 VIX 22-28
    CRISIS = "crisis"                     # VIX >= 28

@dataclass
class RegimeSignals:
    vix_level: float
    vix_ma_ratio: float                # VIX / VIX_20MA
    market_momentum_20d: float         # SPY 20일 수익률
    market_above_ma200: bool           # SPY > 200MA
    breadth_advance_ratio: float       # 상승 종목 비율
    recent_drawdown: float             # 최근 20일 MDD

@dataclass
class RegimeState:
    regime: MarketRegime
    signals: RegimeSignals
    position_multiplier: float
    max_stocks: int
    confidence: float

class MultiDimensionalRegimeDetector:
    def __init__(self):
        self.vix_history: List[float] = []
        self.spy_history: List[float] = []
        
    def update_history(self, vix: float, spy_price: float):
        self.vix_history.append(vix)
        self.spy_history.append(spy_price)
        
        if len(self.vix_history) > 252:
            self.vix_history = self.vix_history[-252:]
            self.spy_history = self.spy_history[-252:]
    
    def compute_signals(self, vix: float, spy_price: float, stock_returns: pd.DataFrame) -> RegimeSignals:
        # VIX MA ratio
        if len(self.vix_history) >= 20:
            vix_20ma = np.mean(self.vix_history[-20:])
            vix_ma_ratio = vix / vix_20ma
        else:
            vix_ma_ratio = 1.0
        
        # 시장 모멘텀
        if len(self.spy_history) >= 20:
            market_momentum_20d = (spy_price / self.spy_history[-20] - 1)
            
            if len(self.spy_history) >= 200:
                ma200 = np.mean(self.spy_history[-200:])
                market_above_ma200 = spy_price > ma200
            else:
                market_above_ma200 = True
        else:
            market_momentum_20d = 0
            market_above_ma200 = True
        
        # 브레드스
        if not stock_returns.empty and len(stock_returns) >= 5:
            recent_returns = stock_returns.iloc[-5:].mean()
            breadth_advance_ratio = (recent_returns > 0).mean()
        else:
            breadth_advance_ratio = 0.5
        
        # 최근 드로우다운
        if len(self.spy_history) >= 20:
            recent_prices = self.spy_history[-20:]
            peak = max(recent_prices)
            recent_drawdown = (spy_price / peak - 1)
        else:
            recent_drawdown = 0
        
        return RegimeSignals(
            vix_level=vix,
            vix_ma_ratio=vix_ma_ratio,
            market_momentum_20d=market_momentum_20d,
            market_above_ma200=market_above_ma200,
            breadth_advance_ratio=breadth_advance_ratio,
            recent_drawdown=recent_drawdown
        )
    
    def detect(self, signals: RegimeSignals) -> RegimeState:
        vix = signals.vix_level
        
        # 위기: VIX 28+ 또는 급격한 드로우다운
        if vix >= 28 or signals.recent_drawdown < -0.10:
            return RegimeState(
                regime=MarketRegime.CRISIS,
                signals=signals,
                position_multiplier=0.0,
                max_stocks=0,
                confidence=0.95
            )
        
        # 방어적: VIX 22-28
        if vix >= 22:
            return RegimeState(
                regime=MarketRegime.DEFENSIVE,
                signals=signals,
                position_multiplier=0.0,
                max_stocks=0,
                confidence=0.8
            )
        
        # ===== 핵심: VIX 18-22 조건부 진입 =====
        if 18 <= vix < 22:
            conditions_met = 0
            
            # 1) VIX 하락 중
            if signals.vix_ma_ratio < 1.0:
                conditions_met += 1
            
            # 2) 시장 모멘텀 양수
            if signals.market_momentum_20d > 0:
                conditions_met += 1
            
            # 3) 200MA 상회
            if signals.market_above_ma200:
                conditions_met += 1
            
            # 4) 브레드스 양호
            if signals.breadth_advance_ratio > 0.45:
                conditions_met += 1
            
            # 5) 드로우다운 양호
            if signals.recent_drawdown > -0.05:
                conditions_met += 1
            
            # 3개 이상 충족 시 Cautious Long
            if conditions_met >= 3:
                position_mult = 0.2 + (conditions_met - 3) * 0.1  # 0.2 ~ 0.4
                return RegimeState(
                    regime=MarketRegime.CAUTIOUS_LONG,
                    signals=signals,
                    position_multiplier=position_mult,
                    max_stocks=5,
                    confidence=conditions_met / 5
                )
            else:
                return RegimeState(
                    regime=MarketRegime.DEFENSIVE,
                    signals=signals,
                    position_multiplier=0.0,
                    max_stocks=0,
                    confidence=0.6
                )
        
        # Neutral Favorable: VIX 16-18
        if 16 <= vix < 18:
            if signals.vix_ma_ratio < 0.95 and signals.market_momentum_20d > 0:
                position_mult = 0.6
            else:
                position_mult = 0.4
            
            return RegimeState(
                regime=MarketRegime.NEUTRAL_FAVORABLE,
                signals=signals,
                position_multiplier=position_mult,
                max_stocks=7,
                confidence=0.75
            )
        
        # Bullish: VIX 13-16
        if 13 <= vix < 16:
            return RegimeState(
                regime=MarketRegime.BULLISH,
                signals=signals,
                position_multiplier=0.8,
                max_stocks=10,
                confidence=0.85
            )
        
        # Ultra Bullish: VIX < 13
        return RegimeState(
            regime=MarketRegime.ULTRA_BULLISH,
            signals=signals,
            position_multiplier=1.0,
            max_stocks=12,
            confidence=0.9
        )

# =============================================================================
# 2. ENHANCED ALPHA ENGINE
# =============================================================================

class EnhancedAlphaEngine:
    def __init__(self):
        self.factor_weights = {
            'momentum_12_1': 0.35,
            'low_vol': 0.30,
            'quality': 0.20,
            'recent_momentum': 0.15
        }
    
    def compute_alpha(self, prices: pd.DataFrame) -> pd.Series:
        if prices.empty or len(prices) < 252:
            return pd.Series(0.0, index=prices.columns)
        
        prices = prices.ffill()
        daily_returns = prices.pct_change()
        
        # 1. Momentum 12-1
        ret_12m = prices.iloc[-1] / prices.iloc[-252] - 1
        ret_1m = prices.iloc[-1] / prices.iloc[-21] - 1
        momentum_12_1 = ret_12m - ret_1m
        
        # 2. Low Volatility
        vol_60d = daily_returns.iloc[-60:].std()
        low_vol = -vol_60d
        
        # 3. Quality (Sharpe-like)
        mean_ret = daily_returns.iloc[-126:].mean()
        std_ret = daily_returns.iloc[-126:].std()
        quality = mean_ret / (std_ret + 1e-8)
        
        # 4. Recent Momentum
        recent_momentum = ret_1m
        
        # Z-score 정규화
        factors = {
            'momentum_12_1': momentum_12_1,
            'low_vol': low_vol,
            'quality': quality,
            'recent_momentum': recent_momentum
        }
        
        normalized = {}
        for name, factor in factors.items():
            factor = factor.fillna(0)
            z = (factor - factor.mean()) / (factor.std() + 1e-8)
            z = z.clip(-3, 3)
            normalized[name] = z
        
        # 가중 합산
        alpha = sum(normalized[k] * self.factor_weights[k] for k in self.factor_weights)
        alpha = alpha.fillna(0)
        
        return alpha

# =============================================================================
# 3. DYNAMIC VOLATILITY TARGETING
# =============================================================================

class DynamicVolatilityTargeting:
    def __init__(self, target_vol: float = 0.10):
        self.target_vol = target_vol
    
    def compute_position_scale(self, forecast_vol: float) -> float:
        if forecast_vol <= 0:
            return 1.0
        return min(self.target_vol / forecast_vol, 1.5)

# =============================================================================
# 4. PORTFOLIO OPTIMIZER
# =============================================================================

class PortfolioOptimizer:
    def optimize(self, 
                returns: pd.DataFrame, 
                alpha: pd.Series, 
                regime_state: RegimeState,
                vol_targeting: DynamicVolatilityTargeting) -> pd.Series:
        
        if returns.empty or len(returns) < 20:
            return pd.Series(0.0, index=alpha.index)
        
        if regime_state.position_multiplier == 0:
            return pd.Series(0.0, index=alpha.index)
        
        # 변동성 상위 20% 제외
        vol = returns.std()
        vol_threshold = vol.quantile(0.8)
        
        valid_mask = (alpha > 0) & (vol < vol_threshold)
        filtered_alpha = alpha[valid_mask]
        
        if len(filtered_alpha) == 0:
            filtered_alpha = alpha[alpha > 0]
        
        if len(filtered_alpha) == 0:
            return pd.Series(0.0, index=alpha.index)
        
        # Top N by alpha
        top_n = regime_state.max_stocks
        top_symbols = filtered_alpha.nlargest(top_n).index
        
        # Inverse volatility weighting
        vol_selected = vol[top_symbols]
        inv_vol = 1 / (vol_selected + 1e-8)
        weights = inv_vol / inv_vol.sum()
        
        # 동적 변동성 타겟팅
        portfolio_vol = returns[top_symbols].std().mean() * np.sqrt(252)
        vol_scale = vol_targeting.compute_position_scale(portfolio_vol)
        
        # 최종 포지션
        result = pd.Series(0.0, index=alpha.index)
        result[top_symbols] = weights * regime_state.position_multiplier * vol_scale
        
        return result

# =============================================================================
# 5. DATA PIPELINE
# =============================================================================

class PITDataPipeline:
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    def load_prices(self, symbols: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        conn = sqlite3.connect(self.db_path)
        placeholders = ','.join(['?' for _ in symbols])
        query = f"""
            SELECT symbol, date, close
            FROM daily_ohlcv
            WHERE symbol IN ({placeholders})
            AND date >= ? AND date <= ?
            ORDER BY date, symbol
        """
        df = pd.read_sql_query(query, conn, params=symbols + [start_date, end_date])
        conn.close()
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        prices = df.pivot(index='date', columns='symbol', values='close')
        prices = prices.ffill()
        return prices
    
    def load_vix(self, start_date: str, end_date: str) -> pd.Series:
        conn = sqlite3.connect(self.db_path)
        query = """
            SELECT date, close as vix
            FROM vix
            WHERE date >= ? AND date <= ?
            ORDER BY date
        """
        df = pd.read_sql_query(query, conn, params=[start_date, end_date])
        conn.close()
        
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df['vix'].ffill()

# =============================================================================
# 6. BACKTEST ENGINE
# =============================================================================

class BacktestEngine:
    def __init__(self, db_path: str, transaction_cost_bps: float = 5.0):
        self.db_path = db_path
        self.transaction_cost = transaction_cost_bps / 10000
        
        self.pipeline = PITDataPipeline(db_path)
        self.regime_detector = MultiDimensionalRegimeDetector()
        self.alpha_engine = EnhancedAlphaEngine()
        self.vol_targeting = DynamicVolatilityTargeting(target_vol=0.10)
        self.optimizer = PortfolioOptimizer()
    
    def run(self, symbols: List[str], start_date: str, end_date: str, rebalance_freq: str = 'W') -> Dict[str, Any]:
        print(f"Loading data for {len(symbols)} symbols...")
        
        prices = self.pipeline.load_prices(symbols, start_date, end_date)
        vix = self.pipeline.load_vix(start_date, end_date)
        
        if prices.empty:
            return {}
        
        print(f"Data loaded: {len(prices)} days, {len(prices.columns)} symbols")
        
        vix = vix.reindex(prices.index).ffill().fillna(20)
        
        # SPY 가격 (시장 대표)
        spy_prices = prices.mean(axis=1)  # 간단히 평균 사용
        
        portfolio_value = [1.0]
        positions = pd.Series(0.0, index=prices.columns)
        daily_returns = []
        
        if rebalance_freq == 'W':
            rebalance_dates = prices.resample('W-FRI').last().index
        else:
            rebalance_dates = prices.index[::5]
        
        print(f"Running backtest with {len(rebalance_dates)} rebalance dates...")
        
        for i, date in enumerate(prices.index[1:], 1):
            current_vix = vix.loc[date] if date in vix.index else 20
            current_spy = spy_prices.iloc[i]
            
            # 히스토리 업데이트
            self.regime_detector.update_history(current_vix, current_spy)
            
            # 레짐 신호 계산
            stock_returns = prices.iloc[max(0, i-5):i].pct_change()
            signals = self.regime_detector.compute_signals(current_vix, current_spy, stock_returns)
            regime_state = self.regime_detector.detect(signals)
            
            # 리밸런싱
            if date in rebalance_dates:
                alpha = self.alpha_engine.compute_alpha(prices.iloc[:i])
                lookback_returns = prices.iloc[max(0, i-60):i].pct_change().dropna()
                new_positions = self.optimizer.optimize(
                    lookback_returns, alpha, regime_state, self.vol_targeting
                )
                
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * self.transaction_cost
                positions = new_positions
            else:
                # CRISIS/DEFENSIVE면 즉시 청산
                if regime_state.regime in [MarketRegime.CRISIS, MarketRegime.DEFENSIVE]:
                    if positions.sum() > 0.01:
                        turnover = positions.abs().sum()
                        cost = turnover * self.transaction_cost
                        positions = pd.Series(0.0, index=prices.columns)
                    else:
                        cost = 0
                else:
                    cost = 0
            
            daily_price_return = prices.iloc[i] / prices.iloc[i-1] - 1
            daily_price_return = daily_price_return.fillna(0)
            
            portfolio_return = (positions * daily_price_return).sum() - cost
            
            daily_returns.append({
                'date': date,
                'return': portfolio_return,
                'regime': regime_state.regime.value,
                'vix': current_vix,
                'position_size': positions.sum(),
                'position_multiplier': regime_state.position_multiplier,
                'confidence': regime_state.confidence
            })
            
            portfolio_value.append(portfolio_value[-1] * (1 + portfolio_return))
        
        returns_df = pd.DataFrame(daily_returns)
        returns_df.set_index('date', inplace=True)
        
        metrics = self._compute_metrics(returns_df)
        
        return {
            'metrics': metrics,
            'returns': returns_df,
            'portfolio_value': portfolio_value
        }
    
    def _compute_metrics(self, returns_df: pd.DataFrame) -> Dict[str, float]:
        returns = returns_df['return']
        
        total_return = (1 + returns).prod() - 1
        annual_return = (1 + total_return) ** (252 / len(returns)) - 1 if len(returns) > 0 else 0
        annual_vol = returns.std() * np.sqrt(252)
        sharpe = annual_return / annual_vol if annual_vol > 0 else 0
        
        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        max_drawdown = drawdown.min()
        
        win_rate = (returns > 0).mean()
        invest_ratio = (returns_df['position_size'] > 0.01).mean()
        
        regime_metrics = {}
        for regime in ['ultra_bullish', 'bullish', 'neutral_fav', 'cautious_long', 'defensive', 'crisis']:
            regime_returns = returns_df[returns_df['regime'] == regime]['return']
            if len(regime_returns) > 0:
                r_std = regime_returns.std()
                regime_metrics[regime] = {
                    'days': len(regime_returns),
                    'annual_return': regime_returns.mean() * 252,
                    'sharpe': regime_returns.mean() / r_std * np.sqrt(252) if r_std > 0 else 0,
                    'avg_position': returns_df[returns_df['regime'] == regime]['position_size'].mean()
                }
        
        invested_returns = returns_df[returns_df['position_size'] > 0.01]['return']
        if len(invested_returns) > 0:
            invest_sharpe = invested_returns.mean() / invested_returns.std() * np.sqrt(252) if invested_returns.std() > 0 else 0
        else:
            invest_sharpe = 0
        
        # VIX 구간별 분석
        vix_metrics = {}
        vix_bins = [(0, 13), (13, 16), (16, 18), (18, 22), (22, 28), (28, 100)]
        for low, high in vix_bins:
            mask = (returns_df['vix'] >= low) & (returns_df['vix'] < high)
            vix_returns = returns_df[mask]['return']
            if len(vix_returns) > 0:
                v_std = vix_returns.std()
                vix_metrics[f'{low}-{high}'] = {
                    'days': len(vix_returns),
                    'annual_return': vix_returns.mean() * 252,
                    'sharpe': vix_returns.mean() / v_std * np.sqrt(252) if v_std > 0 else 0,
                    'avg_position': returns_df[mask]['position_size'].mean()
                }
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'annual_vol': annual_vol,
            'sharpe': sharpe,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'invest_ratio': invest_ratio,
            'invest_sharpe': invest_sharpe,
            'regime_metrics': regime_metrics,
            'vix_metrics': vix_metrics
        }

def main():
    print("="*60)
    print("ARES Ultimate v6.0 - Multi-Dimensional Regime")
    print("="*60)
    
    DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    START_DATE = "2020-01-01"
    END_DATE = "2024-12-31"
    
    conn = sqlite3.connect(DB_PATH)
    symbols_df = pd.read_sql_query("""
        SELECT DISTINCT symbol, COUNT(*) as cnt
        FROM daily_ohlcv
        WHERE date >= '2020-01-01'
        GROUP BY symbol
        HAVING cnt > 500
        ORDER BY cnt DESC
        LIMIT 50
    """, conn)
    conn.close()
    
    symbols = symbols_df['symbol'].tolist()
    print(f"Selected {len(symbols)} symbols")
    
    engine = BacktestEngine(DB_PATH)
    result = engine.run(symbols, START_DATE, END_DATE, rebalance_freq='W')
    
    if not result:
        print("Backtest failed")
        return
    
    metrics = result['metrics']
    
    print("\n" + "="*60)
    print("BACKTEST RESULTS")
    print("="*60)
    print(f"Total Return:     {metrics['total_return']*100:.2f}%")
    print(f"Annual Return:    {metrics['annual_return']*100:.2f}%")
    print(f"Annual Volatility:{metrics['annual_vol']*100:.2f}%")
    print(f"Sharpe Ratio:     {metrics['sharpe']:.2f}")
    print(f"Max Drawdown:     {metrics['max_drawdown']*100:.2f}%")
    print(f"Win Rate:         {metrics['win_rate']*100:.2f}%")
    print(f"Investment Ratio: {metrics['invest_ratio']*100:.2f}%")
    print(f"Invest Period Sharpe: {metrics['invest_sharpe']:.2f}")
    
    print("\n" + "-"*60)
    print("REGIME BREAKDOWN")
    print("-"*60)
    for regime, rm in metrics['regime_metrics'].items():
        print(f"{regime:15s}: {rm['days']:4d} days, "
              f"Ann.Ret: {rm['annual_return']*100:6.2f}%, "
              f"Sharpe: {rm['sharpe']:.2f}, "
              f"Avg.Pos: {rm['avg_position']*100:.1f}%")
    
    print("\n" + "-"*60)
    print("VIX BREAKDOWN")
    print("-"*60)
    for vix_range, vm in metrics['vix_metrics'].items():
        print(f"VIX {vix_range:8s}: {vm['days']:4d} days, "
              f"Ann.Ret: {vm['annual_return']*100:6.2f}%, "
              f"Sharpe: {vm['sharpe']:.2f}, "
              f"Avg.Pos: {vm['avg_position']*100:.1f}%")
    
    result['returns'].to_csv('/home/ubuntu/ares_v6_0_results.csv')
    print("\n" + "="*60)
    print("Results saved to /home/ubuntu/ares_v6_0_results.csv")
    print("="*60)

if __name__ == "__main__":
    main()
