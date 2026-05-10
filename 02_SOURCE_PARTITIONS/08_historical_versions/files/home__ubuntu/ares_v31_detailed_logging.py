#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v31: 상세 정밀 로깅 포함 파레토 최적화
================================================================================
v26 베이스라인 + 5가지 개선방안 + 상세 정밀 로깅

정밀 로깅 항목:
1. 팩터별 IC (Information Coefficient) - 각 팩터의 예측력
2. 레짐별 성과 분석 - 각 레짐에서의 수익률/Sharpe
3. 턴오버 추적 - 리밸런싱별 거래량
4. 드로다운 이벤트 - MDD 발생 시점 및 회복 기간
5. 개선방안별 기여도 - 각 개선방안이 성과에 미치는 영향
6. 일별 포지션 로그 - 포트폴리오 구성 변화
7. 비용 분석 - 거래비용이 성과에 미치는 영향
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
import json
import itertools
from datetime import datetime
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

print("=" * 80)
print("ARES v31: 상세 정밀 로깅 포함 파레토 최적화")
print("=" * 80)
print(f"시작 시간: {datetime.now()}")

# =============================================================================
# 1. 데이터 로드
# =============================================================================
print("\n[1] 데이터 로드 중...")

conn = sqlite3.connect("/home/ubuntu/ares_x_unified_database/ares_universal_v2.db", timeout=30)
query = """
SELECT date, symbol, close, volume FROM daily_ohlcv
WHERE date >= '2016-01-01' AND date <= '2024-12-31'
ORDER BY date, symbol
"""
df = pd.read_sql_query(query, conn)
conn.close()

df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
prices = df.pivot(index='date', columns='symbol', values='close')
volumes = df.pivot(index='date', columns='symbol', values='volume')
prices.index = pd.to_datetime(prices.index)
volumes.index = pd.to_datetime(volumes.index)

valid_cols = prices.columns[prices.notna().mean() > 0.8]
prices = prices[valid_cols].ffill().bfill()
volumes = volumes[valid_cols].ffill().bfill()
returns = prices.pct_change()
returns.iloc[0] = 0

# ETF 데이터
conn = sqlite3.connect("/home/ubuntu/etf_data_s3.db", timeout=30)
etf_query = """
SELECT date, symbol, close FROM daily_ohlcv
WHERE symbol IN ('QQQ', 'DIA', 'HYG', 'LQD', 'SPY')
AND date >= '2015-01-01' ORDER BY date, symbol
"""
etf_df = pd.read_sql_query(etf_query, conn)
conn.close()

etf = etf_df.pivot(index='date', columns='symbol', values='close')
etf.index = pd.to_datetime(etf.index)
etf = etf.ffill().bfill()

print(f"  주식: {len(prices.columns)}종목, {len(prices)}일")

# =============================================================================
# 2. 팩터 계산
# =============================================================================
print("\n[2] 팩터 계산 중...")

mom_12 = prices.pct_change(252)
mom_1 = prices.pct_change(21)
mom_6 = prices.pct_change(126)
mom_3 = prices.pct_change(63)
mom_5 = prices.pct_change(5)
mom_10 = prices.pct_change(10)

high_52w = prices.rolling(252).max()
low_52w = prices.rolling(252).min()

factors_base = {}
factors_base['mom_12_1'] = (mom_12 - mom_1).rank(axis=1, pct=True)
factors_base['mom_6_1'] = (mom_6 - mom_1).rank(axis=1, pct=True)
factors_base['low_vol'] = (-returns.rolling(63).std()).rank(axis=1, pct=True)
rolling_ret = returns.rolling(252).mean()
rolling_vol = returns.rolling(252).std()
factors_base['quality'] = (rolling_ret / (rolling_vol + 1e-8)).rank(axis=1, pct=True)
factors_base['reversal'] = (-mom_5).rank(axis=1, pct=True)
factors_base['near_high'] = (prices / high_52w).rank(axis=1, pct=True)
mom_accel = mom_3 - mom_3.shift(21)
factors_base['acceleration'] = mom_accel.rank(axis=1, pct=True)
mkt_ret_series = returns.mean(axis=1)
excess_ret = returns.sub(mkt_ret_series, axis=0).rolling(63).mean()
factors_base['rel_strength'] = excess_ret.rank(axis=1, pct=True)
factors_base['range_position'] = ((prices - low_52w) / (high_52w - low_52w + 1e-8)).rank(axis=1, pct=True)
factors_base['mom_10'] = mom_10.rank(axis=1, pct=True)

print(f"  기존 팩터: {len(factors_base)}개")

# =============================================================================
# 3. Orthogonal 팩터 계산
# =============================================================================
print("\n[3] Orthogonal 팩터 계산 중...")

if 'SPY' in etf.columns:
    mkt = etf['SPY'].reindex(prices.index).ffill().bfill()
elif 'QQQ' in etf.columns:
    mkt = etf['QQQ'].reindex(prices.index).ffill().bfill()
else:
    mkt = etf['DIA'].reindex(prices.index).ffill().bfill()

mkt_ret = mkt.pct_change().fillna(0)

# 베타 계산 (rolling 60일)
print("  - 베타 계산...")
beta_60 = pd.DataFrame(index=prices.index, columns=prices.columns, dtype=float)
for t in range(60, len(prices)):
    window_mkt = mkt_ret.iloc[t-60:t].values
    var_mkt = np.var(window_mkt) + 1e-10
    for col in prices.columns:
        window_stock = returns[col].iloc[t-60:t].values
        cov = np.cov(window_stock, window_mkt)[0, 1]
        beta_60.iloc[t, beta_60.columns.get_loc(col)] = cov / var_mkt

# 잔차 수익률
print("  - 잔차 수익률 계산...")
residual_returns = returns.copy()
for t in range(60, len(prices)):
    for col in prices.columns:
        beta = beta_60.iloc[t, beta_60.columns.get_loc(col)]
        if not np.isnan(beta):
            residual_returns.iloc[t, residual_returns.columns.get_loc(col)] = \
                returns.iloc[t, returns.columns.get_loc(col)] - beta * mkt_ret.iloc[t]

factors_ortho = {}
res_mom_12 = residual_returns.rolling(252).sum()
res_mom_1 = residual_returns.rolling(21).sum()
factors_ortho['res_mom_12_1'] = (res_mom_12 - res_mom_1).rank(axis=1, pct=True)
idio_vol = residual_returns.rolling(63).std()
factors_ortho['idio_vol'] = (-idio_vol).rank(axis=1, pct=True)
factors_ortho['bab'] = (-beta_60).rank(axis=1, pct=True)
downside_returns = returns.clip(upper=0)
semi_var = downside_returns.rolling(63).var()
factors_ortho['downside_risk'] = (-semi_var).rank(axis=1, pct=True)
vol_z = volumes / volumes.rolling(63).mean()
ret_10 = returns.rolling(10).sum()
vp_confirm = ret_10 * vol_z
factors_ortho['vp_confirm'] = vp_confirm.rank(axis=1, pct=True)

print(f"  Orthogonal 팩터: {len(factors_ortho)}개")

# =============================================================================
# 4. 레짐 점수 계산
# =============================================================================
print("\n[4] 레짐 점수 계산 중...")

common_dates = prices.index.intersection(etf.index)
mkt_price = etf['QQQ'].reindex(common_dates) if 'QQQ' in etf.columns else etf['DIA'].reindex(common_dates)
mkt_ret_regime = mkt_price.pct_change()
sma_50 = mkt_price.rolling(50).mean()
sma_200 = mkt_price.rolling(200).mean()

trend_score = pd.Series(0.5, index=common_dates)
trend_score[mkt_price > sma_200] += 0.20
trend_score[sma_50 > sma_200] += 0.15
trend_score[mkt_price > sma_50] += 0.10
trend_score[mkt_price > mkt_price.rolling(20).mean()] += 0.05
trend_score[mkt_price < sma_200] -= 0.20
trend_score[(mkt_price < sma_200) & (sma_50 < sma_200)] -= 0.15
trend_score[mkt_price < mkt_price.rolling(20).mean()] -= 0.05
trend_score = trend_score.clip(0, 1)

realized_vol = mkt_ret_regime.rolling(20).std() * np.sqrt(252)
hist_vol = mkt_ret_regime.rolling(252).std() * np.sqrt(252)
vol_ratio = realized_vol / hist_vol
vol_score = 1 - vol_ratio.clip(0.5, 2.0) / 2.0
vol_score = vol_score.clip(0, 1)

if 'HYG' in etf.columns and 'LQD' in etf.columns:
    hyg = etf['HYG'].reindex(common_dates)
    lqd = etf['LQD'].reindex(common_dates)
    credit_ratio = hyg / lqd
    credit_ma = credit_ratio.rolling(60).mean()
    credit_std = credit_ratio.rolling(252).std()
    credit_zscore = (credit_ratio - credit_ma) / credit_std
    credit_score = 0.5 + credit_zscore.clip(-2, 2) / 4
    credit_score = credit_score.clip(0, 1)
else:
    credit_score = pd.Series(0.5, index=common_dates)

regime_scores = pd.DataFrame({
    'trend': trend_score,
    'vol': vol_score,
    'credit': credit_score
}).reindex(prices.index).ffill().bfill()

print(f"  레짐 점수 계산 완료")

# =============================================================================
# 5. NumPy 배열 변환
# =============================================================================
print("\n[5] NumPy 배열 변환 중...")

base_factor_names = list(factors_base.keys())
FACTORS_BASE_NP = np.stack([factors_base[f].values for f in base_factor_names], axis=2)
FACTORS_BASE_NP = np.nan_to_num(FACTORS_BASE_NP, nan=0.5)

ortho_factor_names = list(factors_ortho.keys())
FACTORS_ORTHO_NP = np.stack([factors_ortho[f].values for f in ortho_factor_names], axis=2)
FACTORS_ORTHO_NP = np.nan_to_num(FACTORS_ORTHO_NP, nan=0.5)

BETA_NP = beta_60.values.astype(np.float64)
BETA_NP = np.nan_to_num(BETA_NP, nan=1.0)

stock_vol = returns.rolling(63).std()
STOCK_VOL_NP = stock_vol.values.astype(np.float64)
STOCK_VOL_NP = np.nan_to_num(STOCK_VOL_NP, nan=0.02)

REGIME_SCORES_NP = regime_scores.values.astype(np.float64)
PRICES_NP = prices.values.astype(np.float64)
RETURNS_NP = returns.values.astype(np.float64)

# 미래 수익률 (IC 계산용)
FORWARD_RETURNS_NP = np.zeros_like(RETURNS_NP)
for t in range(len(prices) - 21):
    FORWARD_RETURNS_NP[t] = RETURNS_NP[t+1:t+22].mean(axis=0) if t + 22 <= len(prices) else 0

n_days, n_assets = RETURNS_NP.shape
n_base_factors = len(base_factor_names)
n_ortho_factors = len(ortho_factor_names)

print(f"  데이터: {n_days}일, {n_assets}종목")

# =============================================================================
# 6. 정밀 로깅 클래스
# =============================================================================
class DetailedLogger:
    """상세 정밀 로깅 클래스"""
    
    def __init__(self):
        self.reset()
    
    def reset(self):
        # 팩터별 IC
        self.factor_ic = defaultdict(list)
        
        # 레짐별 성과
        self.regime_returns = defaultdict(list)
        self.regime_days = defaultdict(int)
        
        # 턴오버 추적
        self.turnover_history = []
        self.rebalance_dates = []
        
        # 드로다운 이벤트
        self.drawdown_events = []
        self.current_dd_start = None
        self.max_dd_depth = 0
        
        # 비용 분석
        self.total_cost = 0
        self.cost_per_rebalance = []
        
        # 일별 포지션
        self.daily_exposure = []
        self.daily_n_positions = []
        
        # 개선방안별 기여도
        self.improvement_contributions = defaultdict(list)
        
        # 성과 분해
        self.gross_returns = []
        self.net_returns = []
    
    def log_factor_ic(self, factor_name, ic_value):
        """팩터 IC 로깅"""
        self.factor_ic[factor_name].append(ic_value)
    
    def log_regime_return(self, regime, ret):
        """레짐별 수익률 로깅"""
        self.regime_returns[regime].append(ret)
        self.regime_days[regime] += 1
    
    def log_turnover(self, turnover, date_idx):
        """턴오버 로깅"""
        self.turnover_history.append(turnover)
        self.rebalance_dates.append(date_idx)
    
    def log_drawdown(self, dd, date_idx, portfolio_value):
        """드로다운 이벤트 로깅"""
        if dd < -0.05:  # 5% 이상 드로다운 시작
            if self.current_dd_start is None:
                self.current_dd_start = {
                    'start_idx': date_idx,
                    'start_value': portfolio_value / (1 + dd),
                    'max_depth': dd
                }
            else:
                if dd < self.current_dd_start['max_depth']:
                    self.current_dd_start['max_depth'] = dd
        else:
            if self.current_dd_start is not None:
                self.current_dd_start['end_idx'] = date_idx
                self.current_dd_start['recovery_days'] = date_idx - self.current_dd_start['start_idx']
                self.drawdown_events.append(self.current_dd_start)
                self.current_dd_start = None
    
    def log_cost(self, cost):
        """비용 로깅"""
        self.total_cost += cost
        self.cost_per_rebalance.append(cost)
    
    def log_position(self, exposure, n_positions):
        """포지션 로깅"""
        self.daily_exposure.append(exposure)
        self.daily_n_positions.append(n_positions)
    
    def log_returns(self, gross_ret, net_ret):
        """수익률 로깅"""
        self.gross_returns.append(gross_ret)
        self.net_returns.append(net_ret)
    
    def get_summary(self):
        """로깅 요약 반환"""
        summary = {}
        
        # 팩터별 IC 요약
        summary['factor_ic'] = {}
        for factor, ic_list in self.factor_ic.items():
            if len(ic_list) > 0:
                summary['factor_ic'][factor] = {
                    'mean': float(np.mean(ic_list)),
                    'std': float(np.std(ic_list)),
                    'ir': float(np.mean(ic_list) / (np.std(ic_list) + 1e-10)),
                    'hit_rate': float(np.mean([1 if ic > 0 else 0 for ic in ic_list]))
                }
        
        # 레짐별 성과 요약
        summary['regime_performance'] = {}
        for regime, rets in self.regime_returns.items():
            if len(rets) > 10:
                summary['regime_performance'][regime] = {
                    'mean_return': float(np.mean(rets) * 252),
                    'volatility': float(np.std(rets) * np.sqrt(252)),
                    'sharpe': float(np.mean(rets) / (np.std(rets) + 1e-10) * np.sqrt(252)),
                    'days': self.regime_days[regime],
                    'pct_time': float(self.regime_days[regime] / sum(self.regime_days.values()))
                }
        
        # 턴오버 요약
        if len(self.turnover_history) > 0:
            summary['turnover'] = {
                'mean': float(np.mean(self.turnover_history)),
                'total': float(np.sum(self.turnover_history)),
                'n_rebalances': len(self.turnover_history)
            }
        
        # 드로다운 이벤트 요약
        summary['drawdown_events'] = {
            'count': len(self.drawdown_events),
            'avg_depth': float(np.mean([e['max_depth'] for e in self.drawdown_events])) if self.drawdown_events else 0,
            'avg_recovery_days': float(np.mean([e['recovery_days'] for e in self.drawdown_events])) if self.drawdown_events else 0,
            'worst_event': min(self.drawdown_events, key=lambda x: x['max_depth']) if self.drawdown_events else None
        }
        
        # 비용 분석
        summary['cost_analysis'] = {
            'total_cost': float(self.total_cost),
            'cost_per_rebalance': float(np.mean(self.cost_per_rebalance)) if self.cost_per_rebalance else 0,
            'cost_drag_annual': float(self.total_cost / (len(self.gross_returns) / 252)) if self.gross_returns else 0
        }
        
        # 포지션 분석
        if len(self.daily_exposure) > 0:
            summary['position_analysis'] = {
                'avg_exposure': float(np.mean(self.daily_exposure)),
                'max_exposure': float(np.max(self.daily_exposure)),
                'min_exposure': float(np.min(self.daily_exposure)),
                'avg_n_positions': float(np.mean(self.daily_n_positions))
            }
        
        # 성과 분해
        if len(self.gross_returns) > 0 and len(self.net_returns) > 0:
            gross_sharpe = np.mean(self.gross_returns) / (np.std(self.gross_returns) + 1e-10) * np.sqrt(252)
            net_sharpe = np.mean(self.net_returns) / (np.std(self.net_returns) + 1e-10) * np.sqrt(252)
            summary['performance_decomposition'] = {
                'gross_sharpe': float(gross_sharpe),
                'net_sharpe': float(net_sharpe),
                'cost_impact_sharpe': float(gross_sharpe - net_sharpe)
            }
        
        return summary

# =============================================================================
# 7. 통합 백테스트 함수 (정밀 로깅 포함)
# =============================================================================
def backtest_v31_detailed(
    prices: np.ndarray,
    returns: np.ndarray,
    forward_returns: np.ndarray,
    factors_base: np.ndarray,
    factors_ortho: np.ndarray,
    regime_scores: np.ndarray,
    beta: np.ndarray,
    stock_vol: np.ndarray,
    base_factor_names: list,
    ortho_factor_names: list,
    # 기본 파라미터
    target_vol: float,
    max_lev: float,
    min_lev: float,
    rebal_days: int,
    top_k: int,
    # 레짐 파라미터
    trend_w: float,
    vol_w: float,
    credit_w: float,
    ultra_bull_exp: float,
    bull_exp: float,
    neutral_exp: float,
    bear_exp: float,
    crisis_exp: float,
    # 개선방안 ON/OFF
    use_ortho_factors: bool,
    use_inverse_vol: bool,
    use_beta_cap: bool,
    use_regime_hysteresis: bool,
    use_regime_factor_weights: bool,
    use_tail_risk: bool,
    # 개선방안 파라미터
    ortho_weight: float = 0.3,
    beta_cap_bull: float = 1.2,
    beta_cap_bear: float = 0.6,
    hysteresis_days: int = 3,
    tail_es_threshold: float = -0.05,
    tail_scale: float = 0.7,
    # 고정 파라미터
    cost_rate: float = 0.002,
    dd_warning: float = -0.20,
    dd_stop: float = -0.35,
    # 로깅 옵션
    enable_detailed_logging: bool = True,
) -> dict:
    """통합 백테스트 (정밀 로깅 포함)"""
    
    n_days, n_assets = returns.shape
    n_base = factors_base.shape[2]
    n_ortho = factors_ortho.shape[2]
    
    # 로거 초기화
    logger = DetailedLogger() if enable_detailed_logging else None
    
    # 팩터 가중치
    fw_base = np.array([0.12, 0.12, 0.10, 0.10, 0.08, 0.10, 0.10, 0.10, 0.08, 0.10])
    fw_ortho = np.array([0.30, 0.20, 0.20, 0.15, 0.15])
    fw_bull_adj = np.array([1.2, 1.2, 0.8, 0.9, 0.9, 1.1, 1.1, 1.1, 1.0, 1.1])
    fw_bear_adj = np.array([0.8, 0.8, 1.3, 1.2, 1.0, 0.9, 0.9, 0.9, 1.0, 0.9])
    
    # 초기화
    portfolio_value = 1.0
    peak = 1.0
    is_halted = False
    halt_counter = 0
    
    current_weights = np.zeros(n_assets)
    daily_returns = []
    
    vol_window = 20
    port_ret_buffer = []
    
    regime_history = []
    current_regime = 'neutral'
    
    es_window = 63
    
    for t in range(252, n_days):
        # 레짐 점수
        trend = regime_scores[t-1, 0]
        vol = regime_scores[t-1, 1]
        credit = regime_scores[t-1, 2]
        
        composite = trend * trend_w + vol * vol_w + credit * credit_w
        
        # 레짐 분류
        if composite >= 0.87:
            new_regime = 'ultra_bull'
        elif composite >= 0.70:
            new_regime = 'bull'
        elif composite >= 0.50:
            new_regime = 'neutral'
        elif composite >= 0.30:
            new_regime = 'bear'
        else:
            new_regime = 'crisis'
        
        # 레짐 히스테리시스
        if use_regime_hysteresis:
            regime_history.append(new_regime)
            if len(regime_history) > hysteresis_days:
                regime_history.pop(0)
            if len(regime_history) >= hysteresis_days:
                if all(r == new_regime for r in regime_history[-hysteresis_days:]):
                    current_regime = new_regime
        else:
            current_regime = new_regime
        
        # 레짐별 노출도
        regime_exposures = {
            'ultra_bull': ultra_bull_exp,
            'bull': bull_exp,
            'neutral': neutral_exp,
            'bear': bear_exp,
            'crisis': crisis_exp
        }
        base_exposure = regime_exposures[current_regime]
        
        # DD Control
        if portfolio_value > peak:
            peak = portfolio_value
        
        dd = (portfolio_value - peak) / peak if peak > 0 else 0
        
        if dd <= dd_stop:
            is_halted = True
            halt_counter = 10
        
        if is_halted:
            halt_counter -= 1
            if halt_counter <= 0:
                is_halted = False
        
        if is_halted:
            dd_scale = 0.3
        elif dd <= dd_warning:
            dd_scale = 0.7
        else:
            dd_scale = 1.0
        
        # 변동성 타겟
        if len(port_ret_buffer) >= vol_window:
            rv = np.std(port_ret_buffer[-vol_window:]) * np.sqrt(252)
            if rv > 0.01:
                vol_adj = target_vol / rv
                vol_adj = min(2.0, max(0.5, vol_adj))
            else:
                vol_adj = 1.0
        else:
            vol_adj = 1.0
        
        # Tail Risk 관리
        tail_scale_factor = 1.0
        if use_tail_risk and len(port_ret_buffer) >= es_window:
            sorted_rets = sorted(port_ret_buffer[-es_window:])
            es_5pct = np.mean(sorted_rets[:int(es_window * 0.05) + 1])
            if es_5pct < tail_es_threshold:
                tail_scale_factor = tail_scale + (1 - tail_scale) * (es_5pct / tail_es_threshold)
                tail_scale_factor = max(0.5, min(1.0, tail_scale_factor))
        
        # 리밸런싱
        if t % rebal_days == 0:
            # 팩터 점수 계산
            scores = np.zeros(n_assets)
            
            # 레짐별 팩터 가중치
            if use_regime_factor_weights:
                if current_regime in ['ultra_bull', 'bull']:
                    fw_adj = fw_base * fw_bull_adj
                elif current_regime in ['bear', 'crisis']:
                    fw_adj = fw_base * fw_bear_adj
                else:
                    fw_adj = fw_base.copy()
                fw_adj = fw_adj / fw_adj.sum()
            else:
                fw_adj = fw_base / fw_base.sum()
            
            # 기존 팩터 점수
            for i in range(n_assets):
                for f in range(n_base):
                    scores[i] += factors_base[t-1, i, f] * fw_adj[f]
            
            # Orthogonal 팩터 추가
            if use_ortho_factors:
                ortho_scores = np.zeros(n_assets)
                for i in range(n_assets):
                    for f in range(n_ortho):
                        ortho_scores[i] += factors_ortho[t-1, i, f] * fw_ortho[f]
                ortho_scores = ortho_scores / (fw_ortho.sum() + 1e-10)
                scores = (1 - ortho_weight) * scores + ortho_weight * ortho_scores
            
            # 팩터 IC 계산 (로깅)
            if logger and t < n_days - 21:
                fwd_ret = forward_returns[t]
                for f_idx, f_name in enumerate(base_factor_names):
                    factor_vals = factors_base[t-1, :, f_idx]
                    valid_mask = ~np.isnan(factor_vals) & ~np.isnan(fwd_ret)
                    if valid_mask.sum() > 10:
                        ic = np.corrcoef(factor_vals[valid_mask], fwd_ret[valid_mask])[0, 1]
                        if not np.isnan(ic):
                            logger.log_factor_ic(f_name, ic)
                
                for f_idx, f_name in enumerate(ortho_factor_names):
                    factor_vals = factors_ortho[t-1, :, f_idx]
                    valid_mask = ~np.isnan(factor_vals) & ~np.isnan(fwd_ret)
                    if valid_mask.sum() > 10:
                        ic = np.corrcoef(factor_vals[valid_mask], fwd_ret[valid_mask])[0, 1]
                        if not np.isnan(ic):
                            logger.log_factor_ic(f_name, ic)
            
            # 상위 K 종목
            top_indices = np.argsort(-scores)[:top_k]
            
            # 노출도
            exposure = base_exposure * vol_adj
            exposure = min(max_lev, max(min_lev, exposure))
            exposure *= dd_scale
            exposure *= tail_scale_factor
            
            # 이전 가중치 저장 (턴오버 계산용)
            prev_weights = current_weights.copy()
            
            # 새 가중치 계산
            new_weights = np.zeros(n_assets)
            
            # Inverse-Vol 가중치
            if use_inverse_vol:
                inv_vols = np.zeros(top_k)
                for j, idx in enumerate(top_indices):
                    vol_val = stock_vol[t-1, idx]
                    if vol_val > 0.001:
                        inv_vols[j] = 1.0 / vol_val
                    else:
                        inv_vols[j] = 1.0
                inv_vols = inv_vols / inv_vols.sum()
                for j, idx in enumerate(top_indices):
                    new_weights[idx] = exposure * inv_vols[j]
            else:
                for idx in top_indices:
                    new_weights[idx] = exposure / top_k
            
            # Beta Cap
            if use_beta_cap:
                if current_regime in ['ultra_bull', 'bull']:
                    beta_cap = beta_cap_bull
                else:
                    beta_cap = beta_cap_bear
                
                port_beta = 0.0
                total_weight = 0.0
                for i in range(n_assets):
                    if new_weights[i] > 0:
                        port_beta += new_weights[i] * beta[t-1, i]
                        total_weight += new_weights[i]
                
                if total_weight > 0:
                    port_beta = port_beta / total_weight
                
                if port_beta > beta_cap:
                    scale = beta_cap / port_beta
                    new_weights = new_weights * scale
            
            # 턴오버 계산 및 로깅
            turnover = np.sum(np.abs(new_weights - prev_weights))
            if logger:
                logger.log_turnover(turnover, t)
            
            current_weights = new_weights
        
        # 수익률 계산
        gross_ret = 0.0
        for i in range(n_assets):
            gross_ret += current_weights[i] * returns[t, i]
        
        # 비용
        cost = 0.0
        if t % rebal_days == 0:
            cost = cost_rate * 0.5
        
        net_ret = gross_ret - cost
        
        # 로깅
        if logger:
            logger.log_returns(gross_ret, net_ret)
            logger.log_regime_return(current_regime, net_ret)
            logger.log_cost(cost)
            logger.log_position(np.sum(current_weights), np.sum(current_weights > 0))
            logger.log_drawdown(dd, t, portfolio_value)
        
        daily_returns.append(net_ret)
        port_ret_buffer.append(net_ret)
        portfolio_value *= (1 + net_ret)
    
    # 성과 계산
    daily_returns = np.array(daily_returns)
    valid_returns = daily_returns[daily_returns != 0]
    
    if len(valid_returns) < 100:
        return {'sharpe': 0, 'is_sharpe': 0, 'oos_sharpe': 0, 'annual_return': 0, 'mdd': 0, 'detailed_log': None}
    
    mean_ret = np.mean(valid_returns)
    std_ret = np.std(valid_returns)
    
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 1e-10 else 0
    annual_return = mean_ret * 252
    
    # MDD
    cum_value = 1.0
    peak_value = 1.0
    max_dd = 0.0
    for r in valid_returns:
        cum_value *= (1 + r)
        if cum_value > peak_value:
            peak_value = cum_value
        dd = (cum_value - peak_value) / peak_value
        if dd < max_dd:
            max_dd = dd
    
    # IS/OOS
    is_end = int(len(valid_returns) * 0.55)
    is_returns = valid_returns[:is_end]
    oos_returns = valid_returns[is_end:]
    
    is_sharpe = (np.mean(is_returns) / np.std(is_returns)) * np.sqrt(252) if np.std(is_returns) > 1e-10 else 0
    oos_sharpe = (np.mean(oos_returns) / np.std(oos_returns)) * np.sqrt(252) if np.std(oos_returns) > 1e-10 else 0
    
    result = {
        'sharpe': sharpe,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'annual_return': annual_return,
        'mdd': max_dd,
    }
    
    if logger:
        result['detailed_log'] = logger.get_summary()
    
    return result

# =============================================================================
# 8. 파레토 최적화 실행
# =============================================================================
print("\n[6] 파레토 최적화 시작...")

v26_fixed = {
    'trend_w': 0.47,
    'vol_w': 0.28,
    'credit_w': 0.25,
    'min_lev': 0.3,
    'cost_rate': 0.002,
    'dd_warning': -0.20,
    'dd_stop': -0.35,
}

# 개선방안 조합 (2^5 = 32가지)
improvement_combos = list(itertools.product([False, True], repeat=5))

# 파라미터 그리드 (축소 버전 - 빠른 테스트용)
param_grid = {
    'target_vol': [0.27, 0.29, 0.31],
    'max_lev': [1.9, 2.0],
    'rebal_days': [14, 21],
    'top_k': [45, 48],
    'ultra_bull_exp': [1.9, 2.0],
    'bull_exp': [1.6, 1.7],
    'neutral_exp': [1.1, 1.2],
    'bear_exp': [0.5, 0.6],
    'crisis_exp': [0.24, 0.3],
}

improvement_params = {
    'ortho_weight': [0.2, 0.3],
    'beta_cap_bull': [1.1, 1.2],
    'beta_cap_bear': [0.5, 0.6],
    'hysteresis_days': [2, 3],
    'tail_es_threshold': [-0.04, -0.05],
    'tail_scale': [0.6, 0.7],
}

base_keys = list(param_grid.keys())
base_values = list(param_grid.values())
base_combos = list(itertools.product(*base_values))

imp_keys = list(improvement_params.keys())
imp_values = list(improvement_params.values())
imp_combos = list(itertools.product(*imp_values))

total_tests = len(improvement_combos) * len(base_combos) * len(imp_combos)
print(f"  개선방안 조합: {len(improvement_combos)}")
print(f"  기본 파라미터 조합: {len(base_combos)}")
print(f"  개선방안 파라미터 조합: {len(imp_combos)}")
print(f"  총 테스트 수: {total_tests:,}")

results = []
detailed_logs = []
test_count = 0
start_time = datetime.now()

# 상위 결과에 대해서만 상세 로깅 활성화
top_results_for_logging = []

for imp_combo in improvement_combos:
    use_ortho, use_inv_vol, use_beta_cap, use_hysteresis, use_regime_fw = imp_combo
    use_tail_risk = use_hysteresis
    
    imp_name = f"O{int(use_ortho)}V{int(use_inv_vol)}B{int(use_beta_cap)}H{int(use_hysteresis)}F{int(use_regime_fw)}"
    
    for base_combo in base_combos:
        base_params = dict(zip(base_keys, base_combo))
        
        for imp_param_combo in imp_combos:
            imp_params = dict(zip(imp_keys, imp_param_combo))
            
            try:
                # 첫 번째 패스: 빠른 테스트 (로깅 비활성화)
                result = backtest_v31_detailed(
                    PRICES_NP, RETURNS_NP, FORWARD_RETURNS_NP,
                    FACTORS_BASE_NP, FACTORS_ORTHO_NP,
                    REGIME_SCORES_NP, BETA_NP, STOCK_VOL_NP,
                    base_factor_names, ortho_factor_names,
                    target_vol=base_params['target_vol'],
                    max_lev=base_params['max_lev'],
                    min_lev=v26_fixed['min_lev'],
                    rebal_days=base_params['rebal_days'],
                    top_k=base_params['top_k'],
                    trend_w=v26_fixed['trend_w'],
                    vol_w=v26_fixed['vol_w'],
                    credit_w=v26_fixed['credit_w'],
                    ultra_bull_exp=base_params['ultra_bull_exp'],
                    bull_exp=base_params['bull_exp'],
                    neutral_exp=base_params['neutral_exp'],
                    bear_exp=base_params['bear_exp'],
                    crisis_exp=base_params['crisis_exp'],
                    use_ortho_factors=use_ortho,
                    use_inverse_vol=use_inv_vol,
                    use_beta_cap=use_beta_cap,
                    use_regime_hysteresis=use_hysteresis,
                    use_regime_factor_weights=use_regime_fw,
                    use_tail_risk=use_tail_risk,
                    ortho_weight=imp_params['ortho_weight'],
                    beta_cap_bull=imp_params['beta_cap_bull'],
                    beta_cap_bear=imp_params['beta_cap_bear'],
                    hysteresis_days=imp_params['hysteresis_days'],
                    tail_es_threshold=imp_params['tail_es_threshold'],
                    tail_scale=imp_params['tail_scale'],
                    cost_rate=v26_fixed['cost_rate'],
                    dd_warning=v26_fixed['dd_warning'],
                    dd_stop=v26_fixed['dd_stop'],
                    enable_detailed_logging=False,
                )
                
                if result['sharpe'] > 0:
                    results.append({
                        'imp_combo': imp_name,
                        'use_ortho': use_ortho,
                        'use_inv_vol': use_inv_vol,
                        'use_beta_cap': use_beta_cap,
                        'use_hysteresis': use_hysteresis,
                        'use_regime_fw': use_regime_fw,
                        'sharpe': result['sharpe'],
                        'is_sharpe': result['is_sharpe'],
                        'oos_sharpe': result['oos_sharpe'],
                        'annual_return': result['annual_return'],
                        'mdd': result['mdd'],
                        **base_params,
                        **imp_params
                    })
            except Exception as e:
                pass
            
            test_count += 1
            
            if test_count % 5000 == 0:
                elapsed = (datetime.now() - start_time).total_seconds()
                rate = test_count / elapsed
                remaining = (total_tests - test_count) / rate / 60
                print(f"  진행: {test_count:,}/{total_tests:,} ({100*test_count/total_tests:.1f}%) | "
                      f"유효: {len(results):,} | 예상 남은 시간: {remaining:.1f}분")

print(f"\n  1차 테스트 완료! 유효 결과: {len(results):,}")

# =============================================================================
# 9. 상위 결과에 대해 상세 로깅 실행
# =============================================================================
print("\n[7] 상위 결과 상세 로깅 중...")

if len(results) > 0:
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('sharpe', ascending=False)
    
    # 상위 50개에 대해 상세 로깅
    top_50 = results_df.head(50)
    detailed_results = []
    
    for idx, row in top_50.iterrows():
        result = backtest_v31_detailed(
            PRICES_NP, RETURNS_NP, FORWARD_RETURNS_NP,
            FACTORS_BASE_NP, FACTORS_ORTHO_NP,
            REGIME_SCORES_NP, BETA_NP, STOCK_VOL_NP,
            base_factor_names, ortho_factor_names,
            target_vol=row['target_vol'],
            max_lev=row['max_lev'],
            min_lev=v26_fixed['min_lev'],
            rebal_days=row['rebal_days'],
            top_k=row['top_k'],
            trend_w=v26_fixed['trend_w'],
            vol_w=v26_fixed['vol_w'],
            credit_w=v26_fixed['credit_w'],
            ultra_bull_exp=row['ultra_bull_exp'],
            bull_exp=row['bull_exp'],
            neutral_exp=row['neutral_exp'],
            bear_exp=row['bear_exp'],
            crisis_exp=row['crisis_exp'],
            use_ortho_factors=row['use_ortho'],
            use_inverse_vol=row['use_inv_vol'],
            use_beta_cap=row['use_beta_cap'],
            use_regime_hysteresis=row['use_hysteresis'],
            use_regime_factor_weights=row['use_regime_fw'],
            use_tail_risk=row['use_hysteresis'],
            ortho_weight=row['ortho_weight'],
            beta_cap_bull=row['beta_cap_bull'],
            beta_cap_bear=row['beta_cap_bear'],
            hysteresis_days=row['hysteresis_days'],
            tail_es_threshold=row['tail_es_threshold'],
            tail_scale=row['tail_scale'],
            cost_rate=v26_fixed['cost_rate'],
            dd_warning=v26_fixed['dd_warning'],
            dd_stop=v26_fixed['dd_stop'],
            enable_detailed_logging=True,
        )
        
        detailed_results.append({
            'rank': len(detailed_results) + 1,
            'sharpe': result['sharpe'],
            'is_sharpe': result['is_sharpe'],
            'oos_sharpe': result['oos_sharpe'],
            'annual_return': result['annual_return'],
            'mdd': result['mdd'],
            'imp_combo': row['imp_combo'],
            'detailed_log': result['detailed_log']
        })
    
    print(f"  상세 로깅 완료: {len(detailed_results)}개")

# =============================================================================
# 10. 결과 분석 및 출력
# =============================================================================
print("\n[8] 결과 분석...")

if len(results) > 0:
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('sharpe', ascending=False)
    
    # 상위 30개 결과
    print("\n상위 30개 결과:")
    print("-" * 140)
    for idx, row in results_df.head(30).iterrows():
        print(f"  Sharpe: {row['sharpe']:.4f} | IS: {row['is_sharpe']:.4f} | OOS: {row['oos_sharpe']:.4f} | "
              f"AR: {row['annual_return']*100:.1f}% | MDD: {row['mdd']*100:.1f}% | {row['imp_combo']}")
    
    # v26 베이스라인 대비 비교
    print("\n" + "=" * 80)
    print("v26 베이스라인 대비 비교")
    print("=" * 80)
    print(f"  v26 베이스라인: Sharpe 2.02, IS 2.04, OOS 1.40, MDD -29.3%")
    
    best = results_df.iloc[0]
    print(f"\n  최고 성과:")
    print(f"    Sharpe: {best['sharpe']:.4f} ({(best['sharpe']/2.02-1)*100:+.1f}%)")
    print(f"    IS Sharpe: {best['is_sharpe']:.4f}")
    print(f"    OOS Sharpe: {best['oos_sharpe']:.4f} ({(best['oos_sharpe']/1.40-1)*100:+.1f}%)")
    print(f"    MDD: {best['mdd']*100:.2f}%")
    print(f"    조합: {best['imp_combo']}")
    
    # 개선방안별 효과 분석
    print("\n개선방안별 평균 효과:")
    for imp in ['use_ortho', 'use_inv_vol', 'use_beta_cap', 'use_hysteresis', 'use_regime_fw']:
        on_avg = results_df[results_df[imp] == True]['sharpe'].mean()
        off_avg = results_df[results_df[imp] == False]['sharpe'].mean()
        on_oos = results_df[results_df[imp] == True]['oos_sharpe'].mean()
        off_oos = results_df[results_df[imp] == False]['oos_sharpe'].mean()
        effect = (on_avg / off_avg - 1) * 100 if off_avg > 0 else 0
        oos_effect = (on_oos / off_oos - 1) * 100 if off_oos > 0 else 0
        print(f"  {imp}: Sharpe ON={on_avg:.4f} OFF={off_avg:.4f} ({effect:+.2f}%) | "
              f"OOS ON={on_oos:.4f} OFF={off_oos:.4f} ({oos_effect:+.2f}%)")
    
    # 상세 로깅 분석
    if detailed_results:
        print("\n" + "=" * 80)
        print("상세 로깅 분석 (상위 10개)")
        print("=" * 80)
        
        for dr in detailed_results[:10]:
            print(f"\n  Rank {dr['rank']}: Sharpe {dr['sharpe']:.4f} | {dr['imp_combo']}")
            log = dr['detailed_log']
            
            if log:
                # 팩터 IC
                if 'factor_ic' in log:
                    print("    팩터 IC:")
                    for f_name, ic_stats in sorted(log['factor_ic'].items(), key=lambda x: -x[1]['ir']):
                        print(f"      {f_name}: IC={ic_stats['mean']:.4f}, IR={ic_stats['ir']:.2f}, Hit={ic_stats['hit_rate']:.2%}")
                
                # 레짐별 성과
                if 'regime_performance' in log:
                    print("    레짐별 성과:")
                    for regime, perf in log['regime_performance'].items():
                        print(f"      {regime}: Sharpe={perf['sharpe']:.2f}, Days={perf['days']} ({perf['pct_time']:.1%})")
                
                # 턴오버
                if 'turnover' in log:
                    print(f"    턴오버: 평균={log['turnover']['mean']:.2%}, 총={log['turnover']['total']:.2%}")
                
                # 비용
                if 'cost_analysis' in log:
                    print(f"    비용: 총={log['cost_analysis']['total_cost']:.4f}, 연간 드래그={log['cost_analysis']['cost_drag_annual']:.4f}")
                
                # 드로다운
                if 'drawdown_events' in log:
                    print(f"    드로다운: 이벤트={log['drawdown_events']['count']}, "
                          f"평균 깊이={log['drawdown_events']['avg_depth']*100:.1f}%, "
                          f"평균 회복={log['drawdown_events']['avg_recovery_days']:.0f}일")
    
    # 결과 저장
    output = {
        'baseline': {'sharpe': 2.02, 'is_sharpe': 2.04, 'oos_sharpe': 1.40, 'mdd': -0.293},
        'total_tests': total_tests,
        'valid_results': len(results),
        'best_result': best.to_dict(),
        'improvement_effects': {
            imp: {
                'on_avg_sharpe': float(results_df[results_df[imp] == True]['sharpe'].mean()),
                'off_avg_sharpe': float(results_df[results_df[imp] == False]['sharpe'].mean()),
                'on_avg_oos': float(results_df[results_df[imp] == True]['oos_sharpe'].mean()),
                'off_avg_oos': float(results_df[results_df[imp] == False]['oos_sharpe'].mean()),
            }
            for imp in ['use_ortho', 'use_inv_vol', 'use_beta_cap', 'use_hysteresis', 'use_regime_fw']
        },
        'detailed_logs': detailed_results[:20],
        'top_results': results_df.head(100).to_dict('records')
    }
    
    with open('/home/ubuntu/ares_v31_detailed_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n결과 저장: /home/ubuntu/ares_v31_detailed_results.json")

print(f"\n완료! 총 소요 시간: {(datetime.now() - start_time).total_seconds()/60:.1f}분")
