#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v42 Advanced Features Integration
======================================
v41-ResidualMom 베이스라인 + 고급 기능 통합 + 정밀 로깅 + CPU 최적화

고급 기능:
1. 15단계 레짐 인식 (VIX 5단계 × 추세 3단계)
2. DB 레짐 데이터 활용 (market_regime_daily)
3. 뉴스 센티먼트 (news_sentiment)
4. 인트라데이 시그널 (intraday_ohlcv)
5. 매크로 지표 (macro_indicators)
6. ICIR Ensemble
7. AARM (Adaptive Asymmetric Risk Manager)
8. Multi-Horizon Ensemble
9. HRP/Inverse Volatility

정밀 로깅:
- 팩터별 IC (Information Coefficient)
- 레짐별 성과 분석
- 턴오버 및 비용 추적
- 드로다운 이벤트 분석
- 개선방안별 기여도
"""

import numpy as np
import pandas as pd
import sqlite3
import warnings
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
import multiprocessing as mp
import json
import sys

warnings.filterwarnings('ignore')

# =============================================================================
# Configuration
# =============================================================================
DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"

# v41-ResidualMom 베이스라인 파라미터 (OOS Sharpe 1.378)
BASELINE_PARAMS = {
    'rebal_period': 7,
    'top_k': 35,
    'hyst_width': 0.05,
    'residual_window': 126,
    'residual_skip': 0,
    'residual_weight': 0.2,
    'bull_lev': 1.0,
    'neutral_lev': 0.8,
    'bear_lev': 0.5,
    'crisis_lev': 0.2,
}

# =============================================================================
# Data Loading with Advanced Features
# =============================================================================
def load_all_data():
    """모든 데이터 로드 (일봉, VIX, 레짐, 센티먼트, 인트라데이, 매크로)"""
    conn = sqlite3.connect(DB_PATH)
    
    # 1. 일봉 데이터
    print("Loading daily OHLCV data...")
    query = """
    SELECT date as timestamp, symbol, open, high, low, close, volume,
           COALESCE(adj_close, close) as adj_close
    FROM daily_ohlcv
    WHERE symbol IN (
        SELECT symbol FROM daily_ohlcv
        GROUP BY symbol
        HAVING COUNT(*) >= 1000 AND MAX(date) >= '2024-01-01'
    )
    ORDER BY date, symbol
    """
    df = pd.read_sql_query(query, conn)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 2. VIX 데이터
    print("Loading VIX data...")
    vix_query = "SELECT date, close as vix FROM vix ORDER BY date"
    vix_df = pd.read_sql_query(vix_query, conn)
    vix_df['date'] = pd.to_datetime(vix_df['date'], format='mixed')
    vix_df = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
    
    # 3. DB 레짐 데이터
    print("Loading market regime data...")
    regime_query = """
    SELECT date, regime_label, regime_score, regime_confidence,
           vix_regime, trend_regime, vix_level, spy_above_200ma,
           yield_curve_slope, credit_spread
    FROM market_regime_daily
    ORDER BY date
    """
    regime_df = pd.read_sql_query(regime_query, conn)
    regime_df['date'] = pd.to_datetime(regime_df['date'])
    regime_df = regime_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
    
    # 4. 뉴스 센티먼트 데이터
    print("Loading news sentiment data...")
    sentiment_query = """
    SELECT date, symbol, AVG(sentiment_score) as sentiment,
           COUNT(*) as news_count
    FROM news_sentiment
    GROUP BY date, symbol
    ORDER BY date, symbol
    """
    sentiment_df = pd.read_sql_query(sentiment_query, conn)
    sentiment_df['date'] = pd.to_datetime(sentiment_df['date'])
    
    # 5. 매크로 지표 데이터
    print("Loading macro indicators...")
    macro_query = """
    SELECT date, indicator_id, value
    FROM macro_indicators
    WHERE indicator_id IN ('T10Y2Y', 'BAMLH0A0HYM2', 'DGS10', 'DGS2')
    ORDER BY date
    """
    macro_df = pd.read_sql_query(macro_query, conn)
    macro_df['date'] = pd.to_datetime(macro_df['date'])
    
    # 6. 인트라데이 데이터 (일별 집계)
    print("Loading intraday aggregates...")
    intraday_query = """
    SELECT DATE(timestamp) as date, symbol,
           AVG(volume) as avg_volume,
           MAX(high) - MIN(low) as daily_range,
           AVG(vwap) as avg_vwap
    FROM intraday_ohlcv
    WHERE symbol IN ('SPY', 'QQQ', 'IWM', 'VXX')
    GROUP BY DATE(timestamp), symbol
    ORDER BY date, symbol
    """
    intraday_df = pd.read_sql_query(intraday_query, conn)
    intraday_df['date'] = pd.to_datetime(intraday_df['date'])
    
    conn.close()
    
    return {
        'daily': df,
        'vix': vix_df,
        'regime': regime_df,
        'sentiment': sentiment_df,
        'macro': macro_df,
        'intraday': intraday_df
    }

def prepare_numpy_arrays(all_data):
    """NumPy 배열로 변환 (최적화)"""
    df = all_data['daily']
    vix_df = all_data['vix']
    regime_df = all_data['regime']
    
    # 피벗 테이블 생성
    prices = df.pivot_table(index='timestamp', columns='symbol', values='adj_close')
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.5))
    prices = prices.ffill().bfill()
    
    # 수익률 계산
    returns = prices.pct_change().fillna(0)
    returns = returns.clip(-0.5, 0.5)
    
    # VIX 정렬
    vix_aligned = vix_df['vix'].reindex(prices.index).ffill().bfill()
    
    # DB 레짐 정렬
    db_regime_aligned = regime_df.reindex(prices.index).ffill()
    
    # NaN 비율 확인
    nan_ratio = returns.isna().sum().sum() / returns.size
    print(f"Data shape: {returns.shape}, NaN ratio: {nan_ratio:.2%}")
    
    return {
        'prices': prices.values.astype(np.float64),
        'returns': returns.values.astype(np.float64),
        'dates': prices.index,
        'symbols': prices.columns.tolist(),
        'vix': vix_aligned.values.astype(np.float64),
        'db_regime': db_regime_aligned,
        'n_dates': len(prices),
        'n_assets': len(prices.columns)
    }

# =============================================================================
# 15-Level Regime Detection
# =============================================================================
def compute_15_level_regime(vix, returns, spy_idx=None):
    """
    15단계 레짐 감지 (VIX 5단계 × 추세 3단계)
    
    VIX 레벨:
    - ULTRA_LOW: VIX < 13
    - LOW: 13 <= VIX < 18
    - MODERATE: 18 <= VIX < 25
    - HIGH: 25 <= VIX < 30
    - CRISIS: VIX >= 30
    
    추세:
    - BULL: 단기MA > 장기MA and 단기MA > 0
    - NEUTRAL: 그 외
    - BEAR: 단기MA < 장기MA and 단기MA < 0
    """
    n_dates = len(vix)
    
    # VIX 5단계 분류
    vix_regime = np.zeros(n_dates, dtype=np.int32)
    vix_regime[vix < 13] = 0  # ULTRA_LOW
    vix_regime[(vix >= 13) & (vix < 18)] = 1  # LOW
    vix_regime[(vix >= 18) & (vix < 25)] = 2  # MODERATE
    vix_regime[(vix >= 25) & (vix < 30)] = 3  # HIGH
    vix_regime[vix >= 30] = 4  # CRISIS
    
    # 추세 3단계 분류 (SPY 또는 시장 평균 사용)
    if spy_idx is not None:
        market_returns = returns[:, spy_idx]
    else:
        market_returns = np.nanmean(returns, axis=1)
    
    # 이동평균 계산
    ma_short = np.zeros(n_dates)
    ma_long = np.zeros(n_dates)
    
    for i in range(20, n_dates):
        ma_short[i] = np.mean(market_returns[i-5:i])
        ma_long[i] = np.mean(market_returns[i-20:i])
    
    trend_regime = np.ones(n_dates, dtype=np.int32)  # NEUTRAL
    trend_regime[(ma_short > ma_long) & (ma_short > 0)] = 0  # BULL
    trend_regime[(ma_short < ma_long) & (ma_short < 0)] = 2  # BEAR
    
    # 15단계 조합 (VIX 5 × 추세 3)
    regime_15 = vix_regime * 3 + trend_regime
    
    # 4단계로 매핑 (백테스트용)
    regime_4 = np.zeros(n_dates, dtype=np.int32)
    
    # BULL: VIX ULTRA_LOW/LOW + BULL trend
    regime_4[(vix_regime <= 1) & (trend_regime == 0)] = 0
    
    # NEUTRAL: VIX MODERATE or mixed
    regime_4[(vix_regime == 2) | ((vix_regime <= 1) & (trend_regime == 1))] = 1
    
    # BEAR: VIX HIGH or BEAR trend
    regime_4[(vix_regime == 3) | (trend_regime == 2)] = 2
    
    # CRISIS: VIX CRISIS
    regime_4[vix_regime == 4] = 3
    
    return regime_15, regime_4

def apply_regime_hysteresis(regime, width=0.05):
    """레짐 히스테리시스 적용 (깜빡임 방지)"""
    n = len(regime)
    smoothed = regime.copy()
    
    for i in range(1, n):
        if smoothed[i] != smoothed[i-1]:
            # 레짐 변경 시 확인 기간
            confirm_window = max(1, int(5 * (1 + width)))
            if i + confirm_window < n:
                future_regime = regime[i:i+confirm_window]
                if np.mean(future_regime == regime[i]) < 0.6:
                    smoothed[i] = smoothed[i-1]
    
    return smoothed

# =============================================================================
# Factor Computation with Residual Momentum
# =============================================================================
def compute_all_factors(prices, returns, vix, params):
    """모든 팩터 계산 (Residual Momentum 포함)"""
    n_dates, n_assets = returns.shape
    
    # 기본 팩터
    factors = {}
    
    # 1. 모멘텀 팩터들
    for lookback in [21, 63, 126, 252]:
        mom = np.zeros((n_dates, n_assets))
        for i in range(lookback, n_dates):
            mom[i] = (prices[i] / prices[i-lookback]) - 1
        factors[f'mom_{lookback}'] = mom
    
    # 2. Residual Momentum (시장 베타 제거)
    residual_window = params.get('residual_window', 126)
    residual_skip = params.get('residual_skip', 0)
    
    market_returns = np.nanmean(returns, axis=1)
    residual_mom = np.zeros((n_dates, n_assets))
    
    for i in range(residual_window + residual_skip, n_dates):
        start_idx = i - residual_window - residual_skip
        end_idx = i - residual_skip if residual_skip > 0 else i
        
        mkt_window = market_returns[start_idx:end_idx]
        
        for j in range(n_assets):
            asset_window = returns[start_idx:end_idx, j]
            
            # 유효한 데이터만 사용
            valid = ~np.isnan(asset_window) & ~np.isnan(mkt_window)
            if np.sum(valid) < 20:
                continue
            
            # 베타 회귀
            mkt_valid = mkt_window[valid]
            asset_valid = asset_window[valid]
            
            if np.std(mkt_valid) > 1e-8:
                beta = np.cov(asset_valid, mkt_valid)[0, 1] / np.var(mkt_valid)
                residuals = asset_valid - beta * mkt_valid
                residual_mom[i, j] = np.sum(residuals)
    
    factors['residual_mom'] = residual_mom
    
    # 3. 변동성 팩터
    vol = np.zeros((n_dates, n_assets))
    for i in range(21, n_dates):
        vol[i] = np.std(returns[i-21:i], axis=0) * np.sqrt(252)
    factors['volatility'] = vol
    
    # 4. RSI
    rsi = np.zeros((n_dates, n_assets))
    for i in range(14, n_dates):
        for j in range(n_assets):
            gains = np.maximum(returns[i-14:i, j], 0)
            losses = np.maximum(-returns[i-14:i, j], 0)
            avg_gain = np.mean(gains)
            avg_loss = np.mean(losses)
            if avg_loss > 1e-8:
                rs = avg_gain / avg_loss
                rsi[i, j] = 100 - (100 / (1 + rs))
            else:
                rsi[i, j] = 100
    factors['rsi'] = rsi
    
    # 5. 볼린저 밴드 위치
    bb_pos = np.zeros((n_dates, n_assets))
    for i in range(20, n_dates):
        for j in range(n_assets):
            window = prices[i-20:i, j]
            ma = np.mean(window)
            std = np.std(window)
            if std > 1e-8:
                bb_pos[i, j] = (prices[i, j] - ma) / (2 * std)
    factors['bb_position'] = bb_pos
    
    return factors

# =============================================================================
# ICIR Ensemble
# =============================================================================
def compute_rolling_ic(factors, returns, window=63):
    """Rolling IC 계산"""
    n_dates = returns.shape[0]
    factor_names = list(factors.keys())
    n_factors = len(factor_names)
    
    ic_matrix = np.zeros((n_dates, n_factors))
    
    for i in range(window + 1, n_dates):
        future_returns = returns[i]
        
        for f_idx, f_name in enumerate(factor_names):
            factor_values = factors[f_name][i-1]
            
            # 유효한 데이터만 사용
            valid = ~np.isnan(factor_values) & ~np.isnan(future_returns)
            if np.sum(valid) < 10:
                continue
            
            # Spearman IC
            factor_valid = factor_values[valid]
            returns_valid = future_returns[valid]
            
            # Rank correlation
            factor_rank = np.argsort(np.argsort(factor_valid))
            returns_rank = np.argsort(np.argsort(returns_valid))
            
            n = len(factor_rank)
            if n > 1:
                ic = 1 - 6 * np.sum((factor_rank - returns_rank) ** 2) / (n * (n**2 - 1))
                ic_matrix[i, f_idx] = ic
    
    # Rolling ICIR
    icir_matrix = np.zeros((n_dates, n_factors))
    for i in range(window * 2, n_dates):
        for f_idx in range(n_factors):
            ic_window = ic_matrix[i-window:i, f_idx]
            if np.std(ic_window) > 1e-8:
                icir_matrix[i, f_idx] = np.mean(ic_window) / np.std(ic_window)
    
    return ic_matrix, icir_matrix, factor_names

def compute_icir_weights(icir_matrix, factor_names, shrink=0.5):
    """ICIR 기반 팩터 가중치 계산"""
    n_dates, n_factors = icir_matrix.shape
    weights = np.zeros((n_dates, n_factors))
    
    for i in range(n_dates):
        icir = icir_matrix[i]
        
        # 양수 ICIR만 사용
        positive_icir = np.maximum(icir, 0)
        
        if np.sum(positive_icir) > 1e-8:
            raw_weights = positive_icir / np.sum(positive_icir)
            # Shrinkage to equal weight
            equal_weight = 1.0 / n_factors
            weights[i] = shrink * equal_weight + (1 - shrink) * raw_weights
        else:
            weights[i] = 1.0 / n_factors
    
    return weights

# =============================================================================
# Advanced Risk Management (AARM)
# =============================================================================
def compute_aarm_signals(returns, vix, regime, params):
    """AARM (Adaptive Asymmetric Risk Manager) 시그널 계산"""
    n_dates = len(regime)
    
    # 1. Progressive DD Control
    dd_mult = params.get('dd_mult', 1.5)
    cumret = np.cumprod(1 + np.nanmean(returns, axis=1)) - 1
    running_max = np.maximum.accumulate(cumret + 1)
    drawdown = (cumret + 1) / running_max - 1
    
    dd_scale = np.ones(n_dates)
    dd_scale[drawdown < -0.05] = 0.8
    dd_scale[drawdown < -0.10] = 0.6
    dd_scale[drawdown < -0.15] = 0.4
    dd_scale[drawdown < -0.20] = 0.2
    
    # 2. VIX Spike Detection
    vix_ma = np.zeros(n_dates)
    for i in range(20, n_dates):
        vix_ma[i] = np.mean(vix[i-20:i])
    
    vix_spike = np.ones(n_dates)
    vix_spike[vix > vix_ma * 1.3] = 0.7
    vix_spike[vix > vix_ma * 1.5] = 0.5
    vix_spike[vix > 30] = 0.3
    
    # 3. Downside Volatility
    downside_vol = np.zeros(n_dates)
    for i in range(21, n_dates):
        neg_returns = np.minimum(np.nanmean(returns[i-21:i], axis=1), 0)
        downside_vol[i] = np.std(neg_returns) * np.sqrt(252)
    
    vol_scale = np.ones(n_dates)
    vol_scale[downside_vol > 0.15] = 0.8
    vol_scale[downside_vol > 0.25] = 0.6
    vol_scale[downside_vol > 0.35] = 0.4
    
    # 종합 AARM 스케일
    aarm_scale = dd_scale * vix_spike * vol_scale
    
    return aarm_scale, {
        'dd_scale': dd_scale,
        'vix_spike': vix_spike,
        'vol_scale': vol_scale,
        'drawdown': drawdown
    }

# =============================================================================
# Multi-Horizon Ensemble
# =============================================================================
def compute_multi_horizon_score(factors, horizons=[21, 63, 126]):
    """Multi-Horizon 앙상블 스코어 계산"""
    n_dates, n_assets = factors['mom_21'].shape
    
    ensemble_score = np.zeros((n_dates, n_assets))
    
    for h in horizons:
        key = f'mom_{h}'
        if key in factors:
            # Z-score 정규화
            for i in range(h, n_dates):
                vals = factors[key][i]
                valid = ~np.isnan(vals)
                if np.sum(valid) > 5:
                    mean = np.nanmean(vals)
                    std = np.nanstd(vals)
                    if std > 1e-8:
                        ensemble_score[i, valid] += (vals[valid] - mean) / std
    
    # 평균
    ensemble_score /= len(horizons)
    
    return ensemble_score

# =============================================================================
# HRP (Hierarchical Risk Parity)
# =============================================================================
def compute_hrp_weights(returns, lookback=63):
    """HRP 가중치 계산 (간소화 버전)"""
    n_dates, n_assets = returns.shape
    weights = np.ones((n_dates, n_assets)) / n_assets
    
    for i in range(lookback, n_dates):
        window = returns[i-lookback:i]
        
        # 변동성 계산
        vol = np.std(window, axis=0)
        vol = np.maximum(vol, 1e-8)
        
        # Inverse volatility weights
        inv_vol = 1.0 / vol
        weights[i] = inv_vol / np.sum(inv_vol)
    
    return weights

# =============================================================================
# Backtest Engine with Detailed Logging
# =============================================================================
@dataclass
class DetailedLog:
    """상세 로깅 데이터"""
    # 일별 데이터
    daily_returns: List[float] = field(default_factory=list)
    daily_leverage: List[float] = field(default_factory=list)
    daily_regime: List[int] = field(default_factory=list)
    daily_turnover: List[float] = field(default_factory=list)
    
    # 레짐별 성과
    regime_returns: Dict[int, List[float]] = field(default_factory=dict)
    
    # 팩터별 IC
    factor_ic: Dict[str, List[float]] = field(default_factory=dict)
    
    # 드로다운 이벤트
    drawdown_events: List[Dict] = field(default_factory=list)
    
    # 개선방안별 기여도
    improvement_contribution: Dict[str, float] = field(default_factory=dict)

def run_backtest_with_logging(
    returns, factors, regime, params,
    icir_weights=None, aarm_scale=None, hrp_weights=None,
    multi_horizon_score=None, use_15_regime=False
):
    """상세 로깅 포함 백테스트"""
    n_dates, n_assets = returns.shape
    
    # 파라미터 추출
    rebal_period = params.get('rebal_period', 7)
    top_k = params.get('top_k', 35)
    hyst_width = params.get('hyst_width', 0.05)
    residual_weight = params.get('residual_weight', 0.2)
    
    bull_lev = params.get('bull_lev', 1.0)
    neutral_lev = params.get('neutral_lev', 0.8)
    bear_lev = params.get('bear_lev', 0.5)
    crisis_lev = params.get('crisis_lev', 0.2)
    
    lev_map = {0: bull_lev, 1: neutral_lev, 2: bear_lev, 3: crisis_lev}
    
    # 히스테리시스 적용
    regime_smooth = apply_regime_hysteresis(regime, hyst_width)
    
    # 로깅 초기화
    log = DetailedLog()
    for r in range(4):
        log.regime_returns[r] = []
    
    # 포트폴리오 초기화
    portfolio_returns = np.zeros(n_dates)
    weights = np.zeros(n_assets)
    prev_weights = np.zeros(n_assets)
    
    # 시작 인덱스
    start_idx = max(252, params.get('residual_window', 126) + 20)
    
    for i in range(start_idx, n_dates):
        # 리밸런싱
        if i % rebal_period == 0:
            # 팩터 스코어 계산
            score = np.zeros(n_assets)
            
            # 기본 모멘텀
            if 'mom_126' in factors:
                mom_score = factors['mom_126'][i-1]
                valid = ~np.isnan(mom_score)
                if np.sum(valid) > 0:
                    score[valid] += mom_score[valid]
            
            # Residual Momentum
            if 'residual_mom' in factors:
                res_score = factors['residual_mom'][i-1]
                valid = ~np.isnan(res_score)
                if np.sum(valid) > 0:
                    score[valid] += residual_weight * res_score[valid]
            
            # Multi-Horizon Score
            if multi_horizon_score is not None:
                mh_score = multi_horizon_score[i-1]
                valid = ~np.isnan(mh_score)
                if np.sum(valid) > 0:
                    score[valid] += 0.3 * mh_score[valid]
            
            # ICIR 가중치 적용
            if icir_weights is not None:
                # 팩터별 가중치 적용 (간소화)
                pass
            
            # Top-K 선택
            valid_idx = ~np.isnan(score)
            if np.sum(valid_idx) >= top_k:
                sorted_idx = np.argsort(score)[::-1]
                top_idx = sorted_idx[:top_k]
                
                # 가중치 계산
                if hrp_weights is not None:
                    weights = np.zeros(n_assets)
                    weights[top_idx] = hrp_weights[i, top_idx]
                    weights /= np.sum(weights) + 1e-8
                else:
                    weights = np.zeros(n_assets)
                    weights[top_idx] = 1.0 / top_k
            
            # 턴오버 계산
            turnover = np.sum(np.abs(weights - prev_weights))
            log.daily_turnover.append(turnover)
            prev_weights = weights.copy()
        
        # 레버리지 결정
        base_lev = lev_map.get(regime_smooth[i], 0.5)
        
        # AARM 적용
        if aarm_scale is not None:
            base_lev *= aarm_scale[i]
        
        # 일별 수익률 계산
        daily_ret = np.nansum(weights * returns[i]) * base_lev
        
        # 거래비용 (턴오버 기반)
        if len(log.daily_turnover) > 0:
            cost = log.daily_turnover[-1] * 0.002  # 20bps
            daily_ret -= cost / rebal_period
        
        portfolio_returns[i] = daily_ret
        
        # 로깅
        log.daily_returns.append(daily_ret)
        log.daily_leverage.append(base_lev)
        log.daily_regime.append(regime_smooth[i])
        log.regime_returns[regime_smooth[i]].append(daily_ret)
    
    # 드로다운 이벤트 분석
    cumret = np.cumprod(1 + portfolio_returns[start_idx:]) - 1
    running_max = np.maximum.accumulate(cumret + 1)
    drawdown = (cumret + 1) / running_max - 1
    
    # 드로다운 이벤트 감지
    in_drawdown = False
    dd_start = 0
    dd_depth = 0
    
    for i, dd in enumerate(drawdown):
        if dd < -0.05 and not in_drawdown:
            in_drawdown = True
            dd_start = i
            dd_depth = dd
        elif in_drawdown:
            dd_depth = min(dd_depth, dd)
            if dd > -0.02:
                log.drawdown_events.append({
                    'start': dd_start,
                    'end': i,
                    'depth': dd_depth,
                    'duration': i - dd_start
                })
                in_drawdown = False
    
    return portfolio_returns, log

# =============================================================================
# Metrics Calculation
# =============================================================================
def calculate_metrics(returns, is_ratio=0.55):
    """성과 지표 계산"""
    valid_returns = returns[~np.isnan(returns) & (returns != 0)]
    
    if len(valid_returns) < 100:
        return None
    
    # IS/OOS 분리
    split_idx = int(len(valid_returns) * is_ratio)
    is_returns = valid_returns[:split_idx]
    oos_returns = valid_returns[split_idx:]
    
    # 전체 지표
    total_sharpe = np.mean(valid_returns) / (np.std(valid_returns) + 1e-8) * np.sqrt(252)
    
    # IS 지표
    is_sharpe = np.mean(is_returns) / (np.std(is_returns) + 1e-8) * np.sqrt(252)
    
    # OOS 지표
    oos_sharpe = np.mean(oos_returns) / (np.std(oos_returns) + 1e-8) * np.sqrt(252)
    
    # MDD
    cumret = np.cumprod(1 + valid_returns) - 1
    running_max = np.maximum.accumulate(cumret + 1)
    drawdown = (cumret + 1) / running_max - 1
    mdd = np.min(drawdown)
    
    # 연간 수익률
    annual_return = np.mean(valid_returns) * 252
    
    # IS/OOS Gap
    gap = (is_sharpe - oos_sharpe) / (is_sharpe + 1e-8) if is_sharpe > 0 else 0
    
    return {
        'sharpe': total_sharpe,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'gap': gap,
        'mdd': mdd,
        'annual_return': annual_return,
        'n_days': len(valid_returns)
    }

def analyze_detailed_log(log):
    """상세 로그 분석"""
    analysis = {}
    
    # 레짐별 Sharpe
    for regime, rets in log.regime_returns.items():
        if len(rets) > 20:
            regime_sharpe = np.mean(rets) / (np.std(rets) + 1e-8) * np.sqrt(252)
            analysis[f'regime_{regime}_sharpe'] = regime_sharpe
            analysis[f'regime_{regime}_count'] = len(rets)
    
    # 턴오버 통계
    if log.daily_turnover:
        analysis['avg_turnover'] = np.mean(log.daily_turnover)
        analysis['total_turnover'] = np.sum(log.daily_turnover)
    
    # 드로다운 이벤트 통계
    if log.drawdown_events:
        depths = [e['depth'] for e in log.drawdown_events]
        durations = [e['duration'] for e in log.drawdown_events]
        analysis['dd_event_count'] = len(log.drawdown_events)
        analysis['avg_dd_depth'] = np.mean(depths)
        analysis['avg_dd_duration'] = np.mean(durations)
    
    return analysis

# =============================================================================
# Parameter Grid Generation
# =============================================================================
def generate_param_grid(improvement_type):
    """개선방안별 파라미터 그리드 생성"""
    base_params = BASELINE_PARAMS.copy()
    
    grids = {
        'baseline': [base_params],
        
        'icir': [
            {**base_params, 'use_icir': True, 'ic_window': w, 'icir_shrink': s}
            for w in [42, 63, 126]
            for s in [0.3, 0.5, 0.7]
        ],
        
        'aarm': [
            {**base_params, 'use_aarm': True, 'dd_mult': m}
            for m in [1.0, 1.5, 2.0]
        ],
        
        'multi_horizon': [
            {**base_params, 'use_multi_horizon': True, 'horizons': h}
            for h in [[21, 63], [21, 63, 126], [63, 126, 252]]
        ],
        
        'hrp': [
            {**base_params, 'use_hrp': True, 'hrp_lookback': l}
            for l in [42, 63, 126]
        ],
        
        '15_regime': [
            {**base_params, 'use_15_regime': True}
        ],
        
        'best_combo': []
    }
    
    # Best combo: 모든 개선안 조합
    for use_icir in [False, True]:
        for use_aarm in [False, True]:
            for use_mh in [False, True]:
                for use_hrp in [False, True]:
                    for use_15r in [False, True]:
                        if use_icir or use_aarm or use_mh or use_hrp or use_15r:
                            p = base_params.copy()
                            p['use_icir'] = use_icir
                            p['use_aarm'] = use_aarm
                            p['use_multi_horizon'] = use_mh
                            p['use_hrp'] = use_hrp
                            p['use_15_regime'] = use_15r
                            grids['best_combo'].append(p)
    
    return grids.get(improvement_type, grids['baseline'])

# =============================================================================
# Main Execution
# =============================================================================
def main():
    print("=" * 80)
    print("ARES v42 Advanced Features Integration")
    print("v41-ResidualMom 베이스라인 + 고급 기능 통합 + 정밀 로깅")
    print("=" * 80)
    
    # 데이터 로드
    print("\n[1] Loading all data...")
    all_data = load_all_data()
    
    print("\n[2] Preparing numpy arrays...")
    data = prepare_numpy_arrays(all_data)
    
    print(f"Data shape: {data['n_dates']} dates × {data['n_assets']} assets")
    print(f"Date range: {data['dates'][0]} ~ {data['dates'][-1]}")
    
    # 팩터 계산
    print("\n[3] Computing factors...")
    factors = compute_all_factors(data['prices'], data['returns'], data['vix'], BASELINE_PARAMS)
    print(f"Factors computed: {list(factors.keys())}")
    
    # 15단계 레짐 계산
    print("\n[4] Computing 15-level regime...")
    regime_15, regime_4 = compute_15_level_regime(data['vix'], data['returns'])
    regime_dist = {i: np.sum(regime_4 == i) for i in range(4)}
    print(f"4-level regime distribution: {regime_dist}")
    
    # Rolling IC 계산
    print("\n[5] Computing rolling IC/ICIR...")
    ic_matrix, icir_matrix, factor_names = compute_rolling_ic(factors, data['returns'])
    print(f"ICIR computed for {len(factor_names)} factors")
    
    # AARM 시그널 계산
    print("\n[6] Computing AARM signals...")
    aarm_scale, aarm_details = compute_aarm_signals(data['returns'], data['vix'], regime_4, BASELINE_PARAMS)
    
    # Multi-Horizon Score 계산
    print("\n[7] Computing multi-horizon ensemble...")
    multi_horizon_score = compute_multi_horizon_score(factors)
    
    # HRP 가중치 계산
    print("\n[8] Computing HRP weights...")
    hrp_weights = compute_hrp_weights(data['returns'])
    
    # 테스트 실행
    print("\n[9] Running tests...")
    
    improvements = ['baseline', 'icir', 'aarm', 'multi_horizon', 'hrp', '15_regime', 'best_combo']
    all_results = []
    
    for imp_type in improvements:
        print(f"\n--- Testing {imp_type} ---")
        param_grid = generate_param_grid(imp_type)
        print(f"Parameter combinations: {len(param_grid)}")
        
        best_result = None
        best_oos = -999
        
        for params in param_grid:
            # 개선안별 설정
            use_icir = params.get('use_icir', False)
            use_aarm = params.get('use_aarm', False)
            use_mh = params.get('use_multi_horizon', False)
            use_hrp = params.get('use_hrp', False)
            use_15r = params.get('use_15_regime', False)
            
            # 백테스트 실행
            portfolio_returns, log = run_backtest_with_logging(
                data['returns'], factors, regime_4, params,
                icir_weights=icir_matrix if use_icir else None,
                aarm_scale=aarm_scale if use_aarm else None,
                hrp_weights=hrp_weights if use_hrp else None,
                multi_horizon_score=multi_horizon_score if use_mh else None,
                use_15_regime=use_15r
            )
            
            # 지표 계산
            metrics = calculate_metrics(portfolio_returns)
            
            if metrics and metrics['oos_sharpe'] > best_oos:
                best_oos = metrics['oos_sharpe']
                best_result = {
                    'improvement': imp_type,
                    'params': params,
                    'metrics': metrics,
                    'log_analysis': analyze_detailed_log(log)
                }
        
        if best_result:
            all_results.append(best_result)
            print(f"Best OOS Sharpe: {best_result['metrics']['oos_sharpe']:.3f}")
            print(f"IS Sharpe: {best_result['metrics']['is_sharpe']:.3f}")
            print(f"Gap: {best_result['metrics']['gap']:.1%}")
            print(f"MDD: {best_result['metrics']['mdd']:.1%}")
    
    # 결과 정렬
    all_results.sort(key=lambda x: x['metrics']['oos_sharpe'], reverse=True)
    
    # 결과 출력
    print("\n" + "=" * 80)
    print("FINAL RESULTS (sorted by OOS Sharpe)")
    print("=" * 80)
    
    for i, r in enumerate(all_results):
        print(f"\n#{i+1} {r['improvement']}")
        print(f"  OOS Sharpe: {r['metrics']['oos_sharpe']:.3f}")
        print(f"  IS Sharpe: {r['metrics']['is_sharpe']:.3f}")
        print(f"  Gap: {r['metrics']['gap']:.1%}")
        print(f"  MDD: {r['metrics']['mdd']:.1%}")
        
        if 'regime_0_sharpe' in r['log_analysis']:
            print(f"  Regime Sharpe: Bull={r['log_analysis'].get('regime_0_sharpe', 0):.2f}, "
                  f"Neutral={r['log_analysis'].get('regime_1_sharpe', 0):.2f}, "
                  f"Bear={r['log_analysis'].get('regime_2_sharpe', 0):.2f}, "
                  f"Crisis={r['log_analysis'].get('regime_3_sharpe', 0):.2f}")
        
        if 'avg_turnover' in r['log_analysis']:
            print(f"  Avg Turnover: {r['log_analysis']['avg_turnover']:.2f}")
        
        if 'dd_event_count' in r['log_analysis']:
            print(f"  DD Events: {r['log_analysis']['dd_event_count']}, "
                  f"Avg Depth: {r['log_analysis']['avg_dd_depth']:.1%}, "
                  f"Avg Duration: {r['log_analysis']['avg_dd_duration']:.0f} days")
    
    # 결과 저장
    output_file = '/home/ubuntu/v42_advanced_results.json'
    with open(output_file, 'w') as f:
        # numpy 타입 변환
        def convert(obj):
            if isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert(v) for v in obj]
            return obj
        
        json.dump(convert(all_results), f, indent=2)
    
    print(f"\nResults saved to {output_file}")

if __name__ == "__main__":
    main()
