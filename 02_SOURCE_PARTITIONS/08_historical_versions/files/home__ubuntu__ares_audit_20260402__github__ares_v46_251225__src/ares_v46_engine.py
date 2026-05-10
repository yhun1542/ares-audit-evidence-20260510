#!/usr/bin/env python3
"""
ARES v46 - Ultimate Quant Strategy Engine
================================================================================
Version: v46 (Baseline)
Date: 2024-12-25
Performance:
  - Sharpe: 1.688
  - MDD: -14.6%
  - MaxRecovery: 350 days

This is the production-ready baseline version with all optimized parameters.
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
from numba import njit
import warnings
from typing import Tuple, Dict, Any
from dataclasses import dataclass

warnings.filterwarnings('ignore')


@dataclass
class AresV46Config:
    """v46 최적화된 파라미터 설정"""
    
    # 저강도 하락장 감지 (SLOW_RISK)
    SLOW60: float = -0.04          # 60일 수익률 ≤ -4%
    SLOW120: float = -0.08         # 120일 수익률 ≤ -8%
    
    # 노출 제한
    BULL_CAP: float = 0.8          # Bull 레짐 노출 상한
    CAP_SLOW: float = 0.35         # SLOW_RISK 노출 상한
    
    # EXIT 조건 (cap 해제)
    EXIT_RET20: float = 0.02       # 20일 수익률 ≥ +2%
    EXIT_CONFIRM: int = 1          # 확인 기간 1일
    
    # Crisis 감지 (A+C 로직)
    CRISIS_VIX_LEVEL: float = 30.0     # VIX ≥ 30
    CRISIS_VIX_CHANGE: float = 0.20    # VIX 일간 변화율 ≥ +20%
    
    # 히스테리시스
    HYST_WIDTH: float = 0.05
    
    # 레짐별 기본 노출
    EXPOSURE_BULL: float = 1.0
    EXPOSURE_NEUTRAL: float = 0.7
    EXPOSURE_BEAR: float = 0.5
    EXPOSURE_CRISIS: float = 0.2
    
    # 백테스트 파라미터
    TOP_K: int = 35
    REBAL_PERIOD: int = 7
    MIN_REBAL_DAYS: int = 2
    COST_BPS: float = 20.0


class AresV46Engine:
    """ARES v46 백테스트 엔진"""
    
    def __init__(self, config: AresV46Config = None):
        self.config = config or AresV46Config()
        self.returns_np = None
        self.prices_np = None
        self.vix_lagged = None
        self.vix_change = None
        self.dates = None
        self.factor_scores = None
        self.cumret_20 = None
        self.cumret_60 = None
        self.cumret_120 = None
    
    def load_data(self, db_path: str) -> None:
        """데이터베이스에서 데이터 로드"""
        conn = sqlite3.connect(db_path)
        
        # OHLCV 데이터
        ohlcv_df = pd.read_sql_query('''
            SELECT date, symbol, close FROM daily_ohlcv 
            WHERE symbol IN (SELECT symbol FROM daily_ohlcv GROUP BY symbol HAVING COUNT(*) > 1000)
            ORDER BY date, symbol
        ''', conn)
        ohlcv_df['date'] = pd.to_datetime(ohlcv_df['date'], format='mixed')
        ohlcv_df = ohlcv_df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        # VIX 데이터
        vix_df = pd.read_sql_query('SELECT date, close as vix FROM vix ORDER BY date', conn)
        vix_df['date'] = pd.to_datetime(vix_df['date'], format='mixed')
        vix_df = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
        
        conn.close()
        
        # 데이터 준비
        prices_wide = ohlcv_df.pivot(index='date', columns='symbol', values='close')
        valid_cols = prices_wide.columns[prices_wide.notna().sum() >= len(prices_wide) * 0.5]
        prices_wide = prices_wide[valid_cols].ffill().bfill()
        returns = prices_wide.pct_change().iloc[1:]
        vix_aligned = vix_df.reindex(returns.index).ffill().bfill()
        
        self.returns_np = np.nan_to_num(returns.values.astype(np.float64), nan=0.0)
        self.prices_np = np.nan_to_num(prices_wide.reindex(returns.index).values.astype(np.float64), nan=1.0)
        
        vix_np = np.nan_to_num(vix_aligned['vix'].values.astype(np.float64), nan=20.0)
        self.vix_lagged = np.roll(vix_np, 1)
        self.vix_lagged[0] = vix_np[0]
        self.vix_change = np.zeros(len(vix_np))
        self.vix_change[1:] = (vix_np[1:] - vix_np[:-1]) / vix_np[:-1]
        
        self.dates = list(returns.index)
        
        print(f'데이터 로드 완료: {len(self.dates)} 거래일, {self.returns_np.shape[1]} 종목')
    
    def compute_factors(self) -> None:
        """팩터 계산"""
        momentum_63 = self._compute_momentum_factor(self.prices_np, 63)
        momentum_126 = self._compute_momentum_factor(self.prices_np, 126)
        volatility_21 = self._compute_volatility_factor(self.returns_np, 21)
        self.factor_scores = (momentum_63 + momentum_126 + volatility_21 * 0.5) / 2.5
        print('팩터 계산 완료')
    
    def compute_cumulative_returns(self) -> None:
        """누적 수익률 계산"""
        n_dates = len(self.dates)
        n_assets = self.returns_np.shape[1]
        
        self.cumret_20 = np.zeros(n_dates)
        self.cumret_60 = np.zeros(n_dates)
        self.cumret_120 = np.zeros(n_dates)
        
        for t in range(120, n_dates):
            # 20일
            total, count = 0.0, 0
            for w in range(20):
                for i in range(n_assets):
                    ret_val = self.returns_np[t - w, i]
                    if not np.isnan(ret_val) and abs(ret_val) < 1.0:
                        total += ret_val
                        count += 1
            if count > 0:
                self.cumret_20[t] = total / count * 20
            
            # 60일
            total, count = 0.0, 0
            for w in range(60):
                for i in range(n_assets):
                    ret_val = self.returns_np[t - w, i]
                    if not np.isnan(ret_val) and abs(ret_val) < 1.0:
                        total += ret_val
                        count += 1
            if count > 0:
                self.cumret_60[t] = total / count * 60
            
            # 120일
            total, count = 0.0, 0
            for w in range(120):
                for i in range(n_assets):
                    ret_val = self.returns_np[t - w, i]
                    if not np.isnan(ret_val) and abs(ret_val) < 1.0:
                        total += ret_val
                        count += 1
            if count > 0:
                self.cumret_120[t] = total / count * 120
        
        print('누적 수익률 계산 완료')
    
    def compute_regime_and_leverage(self) -> Tuple[np.ndarray, np.ndarray]:
        """레짐 및 레버리지 계산 (v46 로직)"""
        cfg = self.config
        n_dates = len(self.dates)
        
        regime = np.zeros(n_dates, dtype=np.int32)
        leverage = np.zeros(n_dates)
        prev_base_regime = 1
        in_slow_risk = False
        exit_confirm_count = 0
        
        for t in range(120, n_dates):
            cum_ret = self.cumret_20[t]
            vix_val = self.vix_lagged[t]
            vix_spike = self.vix_change[t]
            
            # Crisis 조건 (A+C 로직)
            is_crisis = (vix_val > cfg.CRISIS_VIX_LEVEL) or (vix_spike > cfg.CRISIS_VIX_CHANGE)
            
            # SLOW_RISK 진입 조건
            is_slow_condition = (self.cumret_60[t] < cfg.SLOW60) or (self.cumret_120[t] < cfg.SLOW120)
            
            # base_regime 계산
            if is_crisis:
                base_regime = 3  # Crisis
            elif cum_ret > 0.02 + cfg.HYST_WIDTH:
                base_regime = 0  # Bull
            elif cum_ret < -0.02 - cfg.HYST_WIDTH:
                base_regime = 2  # Bear
            elif cum_ret > 0.02 - cfg.HYST_WIDTH and prev_base_regime == 0:
                base_regime = 0  # Bull 유지
            elif cum_ret < -0.02 + cfg.HYST_WIDTH and prev_base_regime == 2:
                base_regime = 2  # Bear 유지
            else:
                base_regime = 1  # Neutral
            
            # Bull 가드레일 (CAP)
            bull_capped = is_slow_condition and base_regime == 0
            
            # SLOW_RISK 상태 머신
            if is_crisis:
                new_regime = 3
                in_slow_risk = False
                exit_confirm_count = 0
            else:
                if in_slow_risk:
                    # EXIT 조건 확인
                    if self.cumret_20[t] > cfg.EXIT_RET20:
                        exit_confirm_count += 1
                        if exit_confirm_count >= cfg.EXIT_CONFIRM:
                            in_slow_risk = False
                            exit_confirm_count = 0
                    else:
                        exit_confirm_count = 0
                else:
                    if is_slow_condition:
                        in_slow_risk = True
                        exit_confirm_count = 0
                
                if in_slow_risk:
                    new_regime = 4  # SLOW_RISK
                else:
                    new_regime = base_regime
            
            regime[t] = new_regime
            
            # 레버리지 계산
            base_leverages = np.array([
                cfg.EXPOSURE_BULL,
                cfg.EXPOSURE_NEUTRAL,
                cfg.EXPOSURE_BEAR,
                cfg.EXPOSURE_CRISIS,
                cfg.CAP_SLOW
            ])
            lev = base_leverages[new_regime]
            
            # Bull CAP 적용
            if bull_capped and new_regime == 0:
                lev = min(lev, cfg.BULL_CAP)
            
            leverage[t] = lev
            prev_base_regime = base_regime
        
        return regime, leverage
    
    def run_backtest(self) -> Tuple[np.ndarray, np.ndarray]:
        """백테스트 실행"""
        regime, leverage = self.compute_regime_and_leverage()
        equity, daily_returns = self._backtest_with_leverage(
            self.returns_np, regime, leverage, self.factor_scores,
            self.config.TOP_K, self.config.REBAL_PERIOD,
            self.config.MIN_REBAL_DAYS, self.config.COST_BPS
        )
        return equity, daily_returns
    
    def calculate_metrics(self, equity: np.ndarray, daily_returns: np.ndarray) -> Dict[str, Any]:
        """성과 지표 계산"""
        sharpe = self._calculate_sharpe(daily_returns[1:])
        mdd = self._calculate_mdd(equity)
        mdd_window = self._find_max_drawdown_window(equity)
        
        return {
            'sharpe': sharpe,
            'mdd': mdd,
            'max_recovery_days': mdd_window['duration_to_recovery'],
            'total_return': (equity[-1] / equity[0] - 1) * 100,
            'annual_return': ((equity[-1] / equity[0]) ** (252 / len(equity)) - 1) * 100
        }
    
    # ========== Static Methods (Numba 최적화) ==========
    
    @staticmethod
    @njit
    def _compute_momentum_factor(prices, window):
        n_dates, n_assets = prices.shape
        factor = np.zeros((n_dates, n_assets))
        for t in range(window, n_dates):
            for i in range(n_assets):
                if prices[t-window, i] > 0:
                    factor[t, i] = (prices[t, i] / prices[t-window, i]) - 1.0
        return factor
    
    @staticmethod
    @njit
    def _compute_volatility_factor(returns, window):
        n_dates, n_assets = returns.shape
        factor = np.zeros((n_dates, n_assets))
        for t in range(window, n_dates):
            for i in range(n_assets):
                vol = np.std(returns[t-window:t, i])
                factor[t, i] = -vol if vol > 0 else 0.0
        return factor
    
    @staticmethod
    @njit
    def _backtest_with_leverage(returns, regime, leverage, factor_scores, top_k, rebal_period,
                                 min_rebalance_days, cost_bps):
        n_dates, n_assets = returns.shape
        weights = np.zeros(n_assets)
        equity = np.ones(n_dates)
        daily_returns = np.zeros(n_dates)
        last_rebalance_day = 0
        
        for t in range(1, n_dates):
            target_leverage = leverage[t]
            
            should_rebalance = False
            days_since_rebalance = t - last_rebalance_day
            if days_since_rebalance >= min_rebalance_days:
                if t % rebal_period == 0:
                    should_rebalance = True
                if t > 0 and regime[t] != regime[t-1]:
                    should_rebalance = True
            
            if should_rebalance:
                scores = factor_scores[t, :]
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
                    last_rebalance_day = t
            
            port_return = 0.0
            for i in range(n_assets):
                port_return += weights[i] * returns[t, i]
            daily_returns[t] = port_return
            equity[t] = equity[t-1] * (1 + port_return)
        
        return equity, daily_returns
    
    @staticmethod
    def _calculate_sharpe(daily_returns):
        if len(daily_returns) < 10:
            return np.nan
        mean_ret = np.mean(daily_returns)
        std_ret = np.std(daily_returns)
        if std_ret == 0:
            return 0.0
        return mean_ret / std_ret * np.sqrt(252)
    
    @staticmethod
    def _calculate_mdd(equity):
        peak = equity[0]
        max_dd = 0.0
        for i in range(len(equity)):
            if equity[i] > peak:
                peak = equity[i]
            dd = (equity[i] - peak) / peak
            if dd < max_dd:
                max_dd = dd
        return max_dd
    
    def _find_max_drawdown_window(self, equity):
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak
        
        trough_idx = np.argmin(drawdown)
        peak_idx = np.argmax(equity[:trough_idx+1])
        
        recovery_idx = trough_idx
        for i in range(trough_idx, len(equity)):
            if equity[i] >= equity[peak_idx]:
                recovery_idx = i
                break
        else:
            recovery_idx = len(equity) - 1
        
        return {
            'peak_date': self.dates[peak_idx],
            'trough_date': self.dates[trough_idx],
            'recovery_date': self.dates[recovery_idx],
            'max_dd': drawdown[trough_idx],
            'duration_to_trough': trough_idx - peak_idx,
            'duration_to_recovery': recovery_idx - peak_idx
        }


def main():
    """메인 실행 함수"""
    print('='*80)
    print('ARES v46 - Ultimate Quant Strategy Engine')
    print('='*80)
    
    # 설정
    config = AresV46Config()
    engine = AresV46Engine(config)
    
    # 데이터 로드
    db_path = '/home/ubuntu/ares_x_unified_database/ares_universal_v2.db'
    engine.load_data(db_path)
    
    # 팩터 및 누적 수익률 계산
    engine.compute_factors()
    engine.compute_cumulative_returns()
    
    # 백테스트 실행
    print('\n백테스트 실행 중...')
    equity, daily_returns = engine.run_backtest()
    
    # 성과 지표 계산
    metrics = engine.calculate_metrics(equity, daily_returns)
    
    print('\n' + '='*80)
    print('ARES v46 백테스트 결과')
    print('='*80)
    print(f"Sharpe Ratio: {metrics['sharpe']:.3f}")
    print(f"Max Drawdown: {metrics['mdd']*100:.1f}%")
    print(f"Max Recovery Days: {metrics['max_recovery_days']}")
    print(f"Total Return: {metrics['total_return']:.1f}%")
    print(f"Annual Return: {metrics['annual_return']:.1f}%")
    print('='*80)
    
    return engine, equity, daily_returns, metrics


if __name__ == '__main__':
    main()
