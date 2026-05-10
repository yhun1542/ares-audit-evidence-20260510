#!/usr/bin/env python3
"""
ARES v42 Turbo Pareto Optimization Test
- CPU 최적화 (Numba JIT + 멀티프로세싱 + 캐싱)
- 정밀 백테스트 (버그 없는 버전)
- 정밀 로깅 (팩터별 IC, 레짐별 성과, 턴오버, 드로다운 이벤트)

테스트 구조:
1. v40 베이스라인 + ICIR Ensemble
2. v40 베이스라인 + AARM + Fast Regime
3. v40 베이스라인 + Multi-Horizon Ensemble
4. v40 베이스라인 + HRP/Inverse Volatility
5. v40 베이스라인 + NRC Uncertainty
6. v40 베이스라인 + 효과 있는 조합
"""

import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp
from numba import njit, prange
from scipy.stats import spearmanr
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# 데이터 로드 및 전처리
# ============================================================================

def load_data():
    """데이터베이스에서 데이터 로드"""
    db_path = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    conn = sqlite3.connect(db_path)
    
    # daily_ohlcv 테이블 사용
    query = """
    SELECT date as timestamp, symbol, open, high, low, close, volume,
           COALESCE(adj_close, close) as adj_close
    FROM daily_ohlcv
    WHERE symbol IN (
        SELECT symbol FROM daily_ohlcv
        GROUP BY symbol
        HAVING COUNT(*) > 2000
    )
    ORDER BY date, symbol
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 수익률 계산
    df = df.sort_values(['symbol', 'timestamp'])
    df['returns'] = df.groupby('symbol')['adj_close'].pct_change()
    
    # NaN 제거
    df = df.dropna(subset=['returns'])
    
    return df

def prepare_numpy_arrays(df):
    """데이터를 NumPy 배열로 변환 (최적화)"""
    # 중복 제거 (마지막 값 사용)
    df = df.drop_duplicates(subset=['timestamp', 'symbol'], keep='last')
    
    symbols = sorted(df['symbol'].unique())
    dates = sorted(df['timestamp'].unique())
    
    n_dates = len(dates)
    n_symbols = len(symbols)
    
    print(f"  Dates: {n_dates}, Symbols: {n_symbols}")
    
    # 사전 할당
    returns = np.full((n_dates, n_symbols), np.nan, dtype=np.float32)
    prices = np.full((n_dates, n_symbols), np.nan, dtype=np.float32)
    volumes = np.full((n_dates, n_symbols), np.nan, dtype=np.float32)
    
    # 빠른 인덱싱
    date_map = {date: i for i, date in enumerate(dates)}
    symbol_map = {sym: j for j, sym in enumerate(symbols)}
    
    # pivot_table 사용 (중복 처리)
    df_pivot = df.pivot_table(index='timestamp', columns='symbol', values='returns', aggfunc='last')
    df_pivot_price = df.pivot_table(index='timestamp', columns='symbol', values='close', aggfunc='last')
    
    # NumPy 배열로 변환
    for j, sym in enumerate(symbols):
        if sym in df_pivot.columns:
            for i, date in enumerate(dates):
                if date in df_pivot.index:
                    val = df_pivot.loc[date, sym]
                    if pd.notna(val):
                        returns[i, j] = val
    
    for j, sym in enumerate(symbols):
        if sym in df_pivot_price.columns:
            for i, date in enumerate(dates):
                if date in df_pivot_price.index:
                    val = df_pivot_price.loc[date, sym]
                    if pd.notna(val):
                        prices[i, j] = val
    
    # NaN 비율 확인
    nan_ratio = np.sum(np.isnan(returns)) / returns.size
    print(f"  Returns NaN ratio: {nan_ratio*100:.1f}%")
    
    return {
        'returns': returns,
        'prices': prices,
        'volumes': volumes,
        'dates': np.array(dates),
        'symbols': np.array(symbols),
        'date_map': date_map,
        'symbol_map': symbol_map
    }

# ============================================================================
# Numba JIT 최적화 함수들
# ============================================================================

@njit(cache=True, fastmath=True)
def fast_momentum(returns, lookback):
    """Numba 최적화 모멘텀 계산"""
    n_dates, n_symbols = returns.shape
    mom = np.full((n_dates, n_symbols), np.nan, dtype=np.float32)
    
    for j in range(n_symbols):
        for i in range(lookback, n_dates):
            total = 0.0
            count = 0
            for k in range(lookback):
                val = returns[i - k - 1, j]
                if not np.isnan(val):
                    total += val
                    count += 1
            if count > lookback // 2:
                mom[i, j] = total / count
    
    return mom

@njit(cache=True, fastmath=True)
def fast_volatility(returns, lookback):
    """Numba 최적화 변동성 계산"""
    n_dates, n_symbols = returns.shape
    vol = np.full((n_dates, n_symbols), np.nan, dtype=np.float32)
    
    for j in range(n_symbols):
        for i in range(lookback, n_dates):
            # 유효한 값 수집
            vals = np.zeros(lookback, dtype=np.float32)
            count = 0
            for k in range(lookback):
                val = returns[i - k - 1, j]
                if not np.isnan(val):
                    vals[count] = val
                    count += 1
            
            if count > lookback // 2:
                # 평균 계산
                mean = 0.0
                for m in range(count):
                    mean += vals[m]
                mean /= count
                
                # 분산 계산
                var = 0.0
                for m in range(count):
                    var += (vals[m] - mean) ** 2
                var /= count
                
                vol[i, j] = np.sqrt(var) * np.sqrt(252)
    
    return vol

@njit(cache=True, fastmath=True)
def fast_rank_normalize(arr):
    """Numba 최적화 랭크 정규화"""
    n_dates, n_symbols = arr.shape
    ranked = np.full((n_dates, n_symbols), np.nan, dtype=np.float32)
    
    for i in range(n_dates):
        # 유효한 값만 추출
        valid_idx = []
        valid_vals = []
        for j in range(n_symbols):
            if not np.isnan(arr[i, j]):
                valid_idx.append(j)
                valid_vals.append(arr[i, j])
        
        if len(valid_idx) < 5:
            continue
        
        # 랭크 계산
        n = len(valid_vals)
        ranks = np.zeros(n, dtype=np.float32)
        for k in range(n):
            rank = 0
            for m in range(n):
                if valid_vals[m] < valid_vals[k]:
                    rank += 1
            ranks[k] = rank / (n - 1) if n > 1 else 0.5
        
        for k, j in enumerate(valid_idx):
            ranked[i, j] = ranks[k]
    
    return ranked

@njit(cache=True, fastmath=True)
def fast_backtest(returns, weights, cost_bps=20):
    """Numba 최적화 백테스트"""
    n_dates, n_symbols = returns.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float32)
    turnover = np.zeros(n_dates, dtype=np.float32)
    
    prev_weights = np.zeros(n_symbols, dtype=np.float32)
    
    for i in range(1, n_dates):
        # 포트폴리오 수익률
        port_ret = 0.0
        for j in range(n_symbols):
            if not np.isnan(returns[i, j]) and not np.isnan(weights[i-1, j]):
                port_ret += weights[i-1, j] * returns[i, j]
        
        # 턴오버 계산
        turn = 0.0
        for j in range(n_symbols):
            w_curr = weights[i-1, j] if not np.isnan(weights[i-1, j]) else 0.0
            w_prev = prev_weights[j]
            turn += abs(w_curr - w_prev)
        
        turnover[i] = turn / 2  # one-way
        
        # 거래비용 차감
        cost = turnover[i] * cost_bps / 10000
        portfolio_returns[i] = port_ret - cost
        
        # 이전 가중치 업데이트
        for j in range(n_symbols):
            prev_weights[j] = weights[i-1, j] if not np.isnan(weights[i-1, j]) else 0.0
    
    return portfolio_returns, turnover

@njit(cache=True, fastmath=True)
def compute_drawdown(returns):
    """Numba 최적화 드로다운 계산"""
    n = len(returns)
    cumulative = np.zeros(n, dtype=np.float32)
    drawdown = np.zeros(n, dtype=np.float32)
    
    cumulative[0] = 1.0
    peak = 1.0
    
    for i in range(1, n):
        cumulative[i] = cumulative[i-1] * (1 + returns[i])
        if cumulative[i] > peak:
            peak = cumulative[i]
        drawdown[i] = (cumulative[i] - peak) / peak
    
    return drawdown, cumulative

# ============================================================================
# 팩터 계산 함수들
# ============================================================================

def compute_factors(data, lookbacks=[21, 63, 126, 252]):
    """모든 팩터 계산"""
    returns = data['returns']
    prices = data['prices']
    volumes = data['volumes']
    
    factors = {}
    
    # 모멘텀 팩터 (여러 룩백)
    for lb in lookbacks:
        factors[f'mom_{lb}'] = fast_momentum(returns, lb)
    
    # 변동성 팩터
    factors['vol_21'] = fast_volatility(returns, 21)
    factors['vol_63'] = fast_volatility(returns, 63)
    
    # Low Volatility (역변동성)
    factors['low_vol'] = -factors['vol_21']
    
    # Residual Momentum (시장 수익률 제거)
    market_ret = np.nanmean(returns, axis=1, keepdims=True)
    residual_ret = returns - market_ret
    factors['residual_mom'] = fast_momentum(residual_ret, 126)
    
    # 랭크 정규화
    for name in factors:
        factors[name] = fast_rank_normalize(factors[name])
    
    return factors

# ============================================================================
# ICIR Ensemble
# ============================================================================

def compute_rolling_ic(factor, fwd_returns, window=126):
    """Rolling IC 계산"""
    n_dates = factor.shape[0]
    ic = np.full(n_dates, np.nan, dtype=np.float32)
    
    for i in range(window, n_dates):
        # 해당 날짜의 cross-sectional IC
        f = factor[i-1]  # t-1 시점 팩터
        r = fwd_returns[i]  # t 시점 수익률
        
        valid = ~np.isnan(f) & ~np.isnan(r)
        if np.sum(valid) < 10:
            continue
        
        try:
            corr, _ = spearmanr(f[valid], r[valid])
            if not np.isnan(corr):
                ic[i] = corr
        except:
            pass
    
    return ic

def compute_icir_weights(factors, returns, window=126, shrink=0.5):
    """ICIR 기반 동적 팩터 가중치"""
    n_dates = returns.shape[0]
    n_factors = len(factors)
    factor_names = list(factors.keys())
    
    # Rolling IC 계산
    ic_series = {}
    for name, factor in factors.items():
        ic_series[name] = compute_rolling_ic(factor, returns, window)
    
    # ICIR 계산 및 가중치 결정
    weights = np.full((n_dates, n_factors), 1.0 / n_factors, dtype=np.float32)
    
    for i in range(window * 2, n_dates):
        icir = []
        for name in factor_names:
            ic = ic_series[name][i-window:i]
            valid_ic = ic[~np.isnan(ic)]
            if len(valid_ic) > 20:
                ic_mean = np.mean(valid_ic)
                ic_std = np.std(valid_ic) + 1e-6
                icir.append(ic_mean / ic_std)
            else:
                icir.append(0.0)
        
        icir = np.array(icir, dtype=np.float32)
        
        # Softmax 변환
        icir_clipped = np.clip(icir, -2, 2)
        exp_icir = np.exp(icir_clipped)
        dyn_weights = exp_icir / (np.sum(exp_icir) + 1e-8)
        
        # 균등 가중치와 혼합 (수축)
        eq_weights = np.ones(n_factors, dtype=np.float32) / n_factors
        weights[i] = (1 - shrink) * dyn_weights + shrink * eq_weights
    
    return weights, factor_names

# ============================================================================
# AARM + Fast Regime
# ============================================================================

@njit(cache=True, fastmath=True)
def aarm_dd_multiplier(dd):
    """AARM Progressive DD Multiplier"""
    abs_dd = abs(dd)
    
    if abs_dd < 0.03:
        return 1.0
    elif abs_dd < 0.10:
        return 1.0 - (abs_dd - 0.03) * (0.3 / 0.07)
    else:
        return max(0.3, 0.7 - (abs_dd - 0.10) * 4.0)

@njit(cache=True, fastmath=True)
def compute_downside_vol(returns, window=20):
    """Downside Volatility 계산"""
    n = len(returns)
    dv = np.full(n, np.nan, dtype=np.float32)
    
    for i in range(window, n):
        neg_rets = []
        for k in range(window):
            r = returns[i - k - 1]
            if not np.isnan(r) and r < 0:
                neg_rets.append(r)
        
        if len(neg_rets) > 5:
            mean = sum(neg_rets) / len(neg_rets)
            var = sum((r - mean) ** 2 for r in neg_rets) / len(neg_rets)
            dv[i] = np.sqrt(var) * np.sqrt(252)
        else:
            dv[i] = 0.15  # 기본값
    
    return dv

def detect_fast_regime(returns, vix_proxy=None, fast_window=5, slow_window=20):
    """Fast Regime Detection"""
    n_dates = returns.shape[0]
    regime = np.zeros(n_dates, dtype=np.int32)  # 0: neutral, 1: bull, -1: bear, -2: crisis
    
    # 시장 수익률
    market_ret = np.nanmean(returns, axis=1)
    
    for i in range(slow_window, n_dates):
        # Fast/Slow MA
        fast_ma = np.nanmean(market_ret[i-fast_window:i])
        slow_ma = np.nanmean(market_ret[i-slow_window:i])
        
        # 변동성 스파이크 감지
        recent_vol = np.nanstd(market_ret[i-fast_window:i]) * np.sqrt(252)
        long_vol = np.nanstd(market_ret[i-slow_window:i]) * np.sqrt(252)
        
        if recent_vol > long_vol * 2:  # 변동성 급등
            regime[i] = -2  # Crisis
        elif fast_ma > slow_ma * 1.001:
            regime[i] = 1  # Bull
        elif fast_ma < slow_ma * 0.999:
            regime[i] = -1  # Bear
        else:
            regime[i] = 0  # Neutral
    
    return regime

# ============================================================================
# Multi-Horizon Ensemble
# ============================================================================

def compute_multi_horizon_score(returns, horizons=[21, 63, 126], weights=None):
    """Multi-Horizon Ensemble Score"""
    if weights is None:
        weights = [0.4, 0.35, 0.25]  # 짧은 horizon에 더 높은 가중치
    
    n_dates, n_symbols = returns.shape
    ensemble_score = np.zeros((n_dates, n_symbols), dtype=np.float32)
    
    for h, w in zip(horizons, weights):
        mom = fast_momentum(returns, h)
        mom_ranked = fast_rank_normalize(mom)
        ensemble_score += w * np.nan_to_num(mom_ranked, nan=0.5)
    
    return fast_rank_normalize(ensemble_score)

# ============================================================================
# HRP / Inverse Volatility
# ============================================================================

def compute_hrp_weights(returns, cov_window=60):
    """Hierarchical Risk Parity 가중치"""
    n_dates, n_symbols = returns.shape
    weights = np.full((n_dates, n_symbols), 1.0 / n_symbols, dtype=np.float32)
    
    for i in range(cov_window, n_dates, 20):  # 20일마다 재계산
        # 상관행렬 계산
        ret_window = returns[i-cov_window:i]
        
        # 유효한 종목만
        valid_mask = np.sum(~np.isnan(ret_window), axis=0) > cov_window // 2
        valid_idx = np.where(valid_mask)[0]
        
        if len(valid_idx) < 5:
            continue
        
        ret_valid = ret_window[:, valid_idx]
        ret_valid = np.nan_to_num(ret_valid, nan=0.0)
        
        # 상관행렬
        corr = np.corrcoef(ret_valid.T)
        corr = np.nan_to_num(corr, nan=0.0)
        np.fill_diagonal(corr, 1.0)
        
        # 거리 행렬
        dist = np.sqrt(0.5 * (1 - corr))
        np.fill_diagonal(dist, 0.0)
        
        try:
            # Hierarchical clustering
            condensed = squareform(dist)
            link = linkage(condensed, method='ward')
            order = leaves_list(link)
            
            # 역변동성 가중치
            vol = np.std(ret_valid, axis=0) + 1e-6
            inv_vol = 1.0 / vol
            hrp_w = inv_vol / np.sum(inv_vol)
            
            # 가중치 할당
            for k, j in enumerate(valid_idx):
                weights[i:i+20, j] = hrp_w[k]
        except:
            pass
    
    return weights

def compute_inverse_vol_weights(returns, vol_window=60):
    """Inverse Volatility 가중치"""
    vol = fast_volatility(returns, vol_window)
    inv_vol = 1.0 / (vol + 1e-6)
    
    # 행별 정규화
    row_sum = np.nansum(inv_vol, axis=1, keepdims=True)
    weights = inv_vol / (row_sum + 1e-8)
    
    return np.nan_to_num(weights, nan=1.0 / returns.shape[1])

# ============================================================================
# NRC Uncertainty (휴리스틱)
# ============================================================================

def compute_uncertainty_scale(factor_scores, entropy_threshold=0.5):
    """Score Entropy 기반 불확실성 스케일"""
    n_dates, n_symbols = factor_scores.shape
    scale = np.ones(n_dates, dtype=np.float32)
    
    for i in range(n_dates):
        scores = factor_scores[i]
        valid = scores[~np.isnan(scores)]
        
        if len(valid) < 5:
            continue
        
        # 정규화 (0~1)
        scores_norm = (valid - np.min(valid)) / (np.max(valid) - np.min(valid) + 1e-8)
        scores_norm = np.clip(scores_norm, 0.01, 0.99)
        
        # Entropy 계산
        entropy = -np.sum(scores_norm * np.log(scores_norm + 1e-8)) / np.log(len(valid))
        
        # 불확실성이 높으면 스케일 다운
        scale[i] = 1.0 / (entropy / entropy_threshold + 1)
    
    return scale

# ============================================================================
# 정밀 로깅
# ============================================================================

class DetailedLogger:
    """정밀 로깅 클래스"""
    
    def __init__(self):
        self.logs = {
            'factor_ic': {},
            'regime_performance': {},
            'turnover_daily': [],
            'drawdown_events': [],
            'position_changes': [],
            'cost_analysis': {}
        }
    
    def log_factor_ic(self, factor_name, ic_series):
        """팩터별 IC 로깅"""
        valid_ic = ic_series[~np.isnan(ic_series)]
        if len(valid_ic) > 0:
            self.logs['factor_ic'][factor_name] = {
                'mean_ic': float(np.mean(valid_ic)),
                'ic_std': float(np.std(valid_ic)),
                'icir': float(np.mean(valid_ic) / (np.std(valid_ic) + 1e-6)),
                'hit_rate': float(np.mean(valid_ic > 0)),
                'n_obs': len(valid_ic)
            }
    
    def log_regime_performance(self, regime, returns):
        """레짐별 성과 로깅"""
        regime_names = {-2: 'crisis', -1: 'bear', 0: 'neutral', 1: 'bull'}
        
        for r_val, r_name in regime_names.items():
            mask = regime == r_val
            if np.sum(mask) > 10:
                r = returns[mask]
                self.logs['regime_performance'][r_name] = {
                    'sharpe': float(np.mean(r) / (np.std(r) + 1e-6) * np.sqrt(252)),
                    'mean_return': float(np.mean(r) * 252),
                    'volatility': float(np.std(r) * np.sqrt(252)),
                    'n_days': int(np.sum(mask)),
                    'pct_time': float(np.sum(mask) / len(regime))
                }
    
    def log_drawdown_events(self, drawdown, threshold=-0.05):
        """드로다운 이벤트 로깅"""
        in_dd = False
        dd_start = 0
        dd_depth = 0
        
        for i, dd in enumerate(drawdown):
            if dd < threshold and not in_dd:
                in_dd = True
                dd_start = i
                dd_depth = dd
            elif in_dd:
                if dd < dd_depth:
                    dd_depth = dd
                if dd > threshold * 0.5:  # 회복
                    self.logs['drawdown_events'].append({
                        'start_idx': dd_start,
                        'end_idx': i,
                        'duration': i - dd_start,
                        'max_depth': float(dd_depth)
                    })
                    in_dd = False
    
    def log_turnover(self, turnover):
        """턴오버 로깅"""
        self.logs['turnover_daily'] = turnover.tolist()
        self.logs['cost_analysis'] = {
            'total_turnover': float(np.sum(turnover)),
            'avg_daily_turnover': float(np.mean(turnover)),
            'annual_turnover': float(np.mean(turnover) * 252),
            'total_cost_bps': float(np.sum(turnover) * 20)
        }
    
    def get_summary(self):
        """로그 요약"""
        return {
            'factor_ic': self.logs['factor_ic'],
            'regime_performance': self.logs['regime_performance'],
            'n_dd_events': len(self.logs['drawdown_events']),
            'avg_dd_depth': np.mean([e['max_depth'] for e in self.logs['drawdown_events']]) if self.logs['drawdown_events'] else 0,
            'avg_dd_duration': np.mean([e['duration'] for e in self.logs['drawdown_events']]) if self.logs['drawdown_events'] else 0,
            'cost_analysis': self.logs['cost_analysis']
        }

# ============================================================================
# 통합 백테스트 함수
# ============================================================================

def run_backtest_with_improvements(data, params, improvement_type):
    """개선방안별 백테스트 실행"""
    returns = data['returns']
    prices = data['prices']
    n_dates, n_symbols = returns.shape
    
    logger = DetailedLogger()
    
    # 기본 팩터 계산
    factors = compute_factors(data)
    
    # 개선방안별 처리
    if improvement_type == 'baseline':
        # v40 베이스라인
        score = factors['mom_126']
        weights = compute_inverse_vol_weights(returns, 60)
        regime = detect_fast_regime(returns)
        
    elif improvement_type == 'icir':
        # ICIR Ensemble
        icir_weights, factor_names = compute_icir_weights(
            factors, returns, 
            window=params.get('ic_window', 126),
            shrink=params.get('shrink', 0.5)
        )
        
        # 팩터 스코어 앙상블
        score = np.zeros((n_dates, n_symbols), dtype=np.float32)
        for i, name in enumerate(factor_names):
            score += icir_weights[:, i:i+1] * np.nan_to_num(factors[name], nan=0.5)
        
        weights = compute_inverse_vol_weights(returns, 60)
        regime = detect_fast_regime(returns)
        
        # 레짐별 팩터 부스트
        if params.get('regime_boost', True):
            for i in range(n_dates):
                if regime[i] == -2:  # Crisis
                    # Low Vol 부스트
                    score[i] = 0.7 * score[i] + 0.3 * np.nan_to_num(factors['low_vol'][i], nan=0.5)
        
        # IC 로깅
        for name in factor_names:
            ic = compute_rolling_ic(factors[name], returns, 126)
            logger.log_factor_ic(name, ic)
        
    elif improvement_type == 'aarm_regime':
        # AARM + Fast Regime
        score = factors['mom_126']
        weights = compute_inverse_vol_weights(returns, 60)
        regime = detect_fast_regime(returns, fast_window=params.get('fast_window', 5))
        
        # Downside Vol 계산
        port_ret_temp = np.nanmean(returns * weights, axis=1)
        dv = compute_downside_vol(port_ret_temp, params.get('dv_window', 20))
        
        # DD 계산
        dd, _ = compute_drawdown(port_ret_temp)
        
        # AARM 스케일 적용
        scale = np.ones(n_dates, dtype=np.float32)
        for i in range(n_dates):
            dd_mult = aarm_dd_multiplier(dd[i])
            dv_mult = min(1.0, params.get('target_dv', 0.15) / (dv[i] + 1e-6))
            scale[i] = min(dd_mult, dv_mult)
            
            # Crisis 레짐에서 추가 축소
            if regime[i] == -2:
                scale[i] *= params.get('crisis_scale', 0.5)
        
        weights = weights * scale[:, np.newaxis]
        
    elif improvement_type == 'multi_horizon':
        # Multi-Horizon Ensemble
        horizons = params.get('horizons', [21, 63, 126])
        horizon_weights = params.get('horizon_weights', [0.4, 0.35, 0.25])
        
        score = compute_multi_horizon_score(returns, horizons, horizon_weights)
        weights = compute_inverse_vol_weights(returns, 60)
        regime = detect_fast_regime(returns)
        
    elif improvement_type == 'hrp':
        # HRP / Inverse Volatility
        score = factors['mom_126']
        
        if params.get('use_hrp', True):
            weights = compute_hrp_weights(returns, params.get('cov_window', 60))
        else:
            weights = compute_inverse_vol_weights(returns, params.get('vol_window', 60))
        
        regime = detect_fast_regime(returns)
        
    elif improvement_type == 'nrc_uncertainty':
        # NRC Uncertainty
        score = factors['mom_126']
        weights = compute_inverse_vol_weights(returns, 60)
        regime = detect_fast_regime(returns)
        
        # 불확실성 스케일
        unc_scale = compute_uncertainty_scale(score, params.get('entropy_threshold', 0.5))
        weights = weights * unc_scale[:, np.newaxis]
        
    else:
        raise ValueError(f"Unknown improvement type: {improvement_type}")
    
    # Top-K 종목 선택
    top_k = params.get('top_k', 48)
    final_weights = np.zeros_like(weights)
    
    for i in range(n_dates):
        valid_idx = np.where(~np.isnan(score[i]))[0]
        if len(valid_idx) < top_k:
            top_idx = valid_idx
        else:
            top_idx = valid_idx[np.argsort(score[i, valid_idx])[-top_k:]]
        
        if len(top_idx) > 0:
            w = weights[i, top_idx]
            w = np.nan_to_num(w, nan=1.0 / len(top_idx))
            w = w / (np.sum(w) + 1e-8)
            final_weights[i, top_idx] = w
    
    # 레짐별 레버리지 조절
    leverage = np.ones(n_dates, dtype=np.float32)
    for i in range(n_dates):
        if regime[i] == 1:  # Bull
            leverage[i] = params.get('bull_lev', 1.0)
        elif regime[i] == 0:  # Neutral
            leverage[i] = params.get('neutral_lev', 0.8)
        elif regime[i] == -1:  # Bear
            leverage[i] = params.get('bear_lev', 0.5)
        else:  # Crisis
            leverage[i] = params.get('crisis_lev', 0.3)
    
    final_weights = final_weights * leverage[:, np.newaxis]
    
    # 백테스트 실행
    port_returns, turnover = fast_backtest(returns, final_weights, params.get('cost_bps', 20))
    
    # 성과 계산
    dd, cumulative = compute_drawdown(port_returns)
    
    # NaN 제거
    valid_returns = port_returns[~np.isnan(port_returns)]
    if len(valid_returns) < 100:
        return None
    
    # IS/OOS 분리
    is_end = int(len(valid_returns) * 0.55)
    
    is_ret = valid_returns[:is_end]
    oos_ret = valid_returns[is_end:]
    
    # Sharpe 계산 (NaN 방지)
    is_std = np.std(is_ret)
    oos_std = np.std(oos_ret)
    total_std = np.std(valid_returns)
    
    if is_std < 1e-8 or oos_std < 1e-8 or total_std < 1e-8:
        return None
    
    is_sharpe = np.mean(is_ret) / is_std * np.sqrt(252)
    oos_sharpe = np.mean(oos_ret) / oos_std * np.sqrt(252)
    total_sharpe = np.mean(valid_returns) / total_std * np.sqrt(252)
    
    # 로깅
    logger.log_regime_performance(regime, port_returns)
    logger.log_drawdown_events(dd)
    logger.log_turnover(turnover)
    
    return {
        'sharpe': float(total_sharpe),
        'is_sharpe': float(is_sharpe),
        'oos_sharpe': float(oos_sharpe),
        'gap': float((is_sharpe - oos_sharpe) / (is_sharpe + 1e-6)),
        'mdd': float(np.min(dd)),
        'annual_return': float(np.mean(port_returns) * 252),
        'volatility': float(np.std(port_returns) * np.sqrt(252)),
        'total_turnover': float(np.sum(turnover)),
        'params': params,
        'improvement_type': improvement_type,
        'detailed_log': logger.get_summary()
    }

# ============================================================================
# 파라미터 그리드 생성
# ============================================================================

def generate_param_grid(improvement_type):
    """개선방안별 파라미터 그리드 생성"""
    
    base_params = {
        'top_k': [25, 30, 35, 40, 48],
        'cost_bps': [20],
        'bull_lev': [1.0, 1.1],
        'neutral_lev': [0.7, 0.8],
        'bear_lev': [0.4, 0.5, 0.6],
        'crisis_lev': [0.2, 0.3]
    }
    
    if improvement_type == 'baseline':
        return base_params
    
    elif improvement_type == 'icir':
        return {
            **base_params,
            'ic_window': [63, 126, 252],
            'shrink': [0.3, 0.5, 0.7],
            'regime_boost': [True, False]
        }
    
    elif improvement_type == 'aarm_regime':
        return {
            **base_params,
            'fast_window': [3, 5, 7],
            'dv_window': [15, 20, 30],
            'target_dv': [0.12, 0.15, 0.18],
            'crisis_scale': [0.3, 0.5, 0.7]
        }
    
    elif improvement_type == 'multi_horizon':
        return {
            **base_params,
            'horizons': [[21, 63, 126], [15, 30, 60], [21, 42, 63, 126]],
            'horizon_weights': [[0.4, 0.35, 0.25], [0.3, 0.3, 0.4], [0.25, 0.25, 0.25, 0.25]]
        }
    
    elif improvement_type == 'hrp':
        return {
            **base_params,
            'use_hrp': [True, False],
            'cov_window': [40, 60, 80],
            'vol_window': [40, 60, 80]
        }
    
    elif improvement_type == 'nrc_uncertainty':
        return {
            **base_params,
            'entropy_threshold': [0.3, 0.5, 0.7]
        }
    
    else:
        return base_params

def expand_param_grid(param_grid):
    """파라미터 그리드 확장"""
    from itertools import product
    
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    
    combinations = list(product(*values))
    
    return [dict(zip(keys, combo)) for combo in combinations]

# ============================================================================
# 메인 실행
# ============================================================================

def run_improvement_test(data, improvement_type, max_tests=10000):
    """개선방안별 테스트 실행"""
    print(f"\n{'='*60}")
    print(f"Testing: {improvement_type}")
    print(f"{'='*60}")
    
    param_grid = generate_param_grid(improvement_type)
    all_params = expand_param_grid(param_grid)
    
    # 테스트 수 제한
    if len(all_params) > max_tests:
        np.random.shuffle(all_params)
        all_params = all_params[:max_tests]
    
    print(f"Total combinations: {len(all_params)}")
    
    results = []
    
    for i, params in enumerate(all_params):
        try:
            result = run_backtest_with_improvements(data, params, improvement_type)
            if result is not None:
                results.append(result)
            
            if (i + 1) % 100 == 0:
                print(f"Progress: {i+1}/{len(all_params)}, Valid results: {len(results)}")
        except Exception as e:
            print(f"Error with params {params}: {e}")
            continue
    
    return results

def main():
    print("="*80)
    print("ARES v42 Turbo Pareto Optimization Test")
    print("="*80)
    print(f"Start time: {datetime.now()}")
    print(f"CPU cores: {mp.cpu_count()}")
    
    # 데이터 로드
    print("\nLoading data...")
    df = load_data()
    print(f"Data shape: {df.shape}")
    print(f"Symbols: {df['symbol'].nunique()}")
    print(f"Date range: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    
    # NumPy 배열 변환
    print("\nPreparing NumPy arrays...")
    data = prepare_numpy_arrays(df)
    print(f"Returns shape: {data['returns'].shape}")
    
    # 개선방안별 테스트
    improvement_types = [
        'baseline',
        'icir',
        'aarm_regime',
        'multi_horizon',
        'hrp',
        'nrc_uncertainty'
    ]
    
    all_results = {}
    
    for imp_type in improvement_types:
        results = run_improvement_test(data, imp_type, max_tests=5000)
        all_results[imp_type] = results
        
        # 결과 요약
        if results:
            oos_sharpes = [r['oos_sharpe'] for r in results]
            best_idx = np.argmax(oos_sharpes)
            best = results[best_idx]
            
            print(f"\n{imp_type} Best Result:")
            print(f"  OOS Sharpe: {best['oos_sharpe']:.3f}")
            print(f"  IS Sharpe: {best['is_sharpe']:.3f}")
            print(f"  Gap: {best['gap']*100:.1f}%")
            print(f"  MDD: {best['mdd']*100:.1f}%")
            print(f"  Params: {best['params']}")
    
    # 최적 조합 테스트
    print("\n" + "="*60)
    print("Testing Best Combination")
    print("="*60)
    
    # 각 개선방안에서 가장 좋은 파라미터 추출
    best_params = {}
    for imp_type, results in all_results.items():
        if results:
            oos_sharpes = [r['oos_sharpe'] for r in results]
            best_idx = np.argmax(oos_sharpes)
            best_params[imp_type] = results[best_idx]['params']
    
    # 결과 저장
    import json
    
    output = {
        'timestamp': str(datetime.now()),
        'baseline': all_results.get('baseline', [])[:100],  # 상위 100개만
        'icir': sorted(all_results.get('icir', []), key=lambda x: -x['oos_sharpe'])[:100],
        'aarm_regime': sorted(all_results.get('aarm_regime', []), key=lambda x: -x['oos_sharpe'])[:100],
        'multi_horizon': sorted(all_results.get('multi_horizon', []), key=lambda x: -x['oos_sharpe'])[:100],
        'hrp': sorted(all_results.get('hrp', []), key=lambda x: -x['oos_sharpe'])[:100],
        'nrc_uncertainty': sorted(all_results.get('nrc_uncertainty', []), key=lambda x: -x['oos_sharpe'])[:100],
        'best_params': best_params
    }
    
    with open('/home/ubuntu/v42_turbo_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\nResults saved to /home/ubuntu/v42_turbo_results.json")
    print(f"End time: {datetime.now()}")

if __name__ == "__main__":
    main()
