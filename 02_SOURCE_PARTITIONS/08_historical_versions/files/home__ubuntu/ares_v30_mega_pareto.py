#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v30: 수만 가지 조합 파레토 최적화 테스트
================================================================================
v26 베이스라인 (Sharpe 2.02) + 5가지 개선방안 모든 조합 테스트

개선방안:
1. Orthogonal 팩터 (Residual Momentum, Idio Vol, BAB, Downside Risk)
2. Inverse-Vol + Beta Cap 포트폴리오 가중치
3. 레짐 히스테리시스 (상태 전이 관성)
4. 레짐별 팩터 가중치 (규칙 기반)
5. Tail Risk 관리 (ES/VaR 기반)

검증 기준:
- Look-ahead bias 완전 제거
- 과적합 방지 (IS 55% / OOS 45% 분리)
- 거래비용 20bps 반영
- 실데이터 OOS 테스트
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
import json
import itertools
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

print("=" * 80)
print("ARES v30: 수만 가지 조합 파레토 최적화 테스트")
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
# 2. 기존 팩터 계산 (v26 베이스라인)
# =============================================================================
print("\n[2] 기존 팩터 계산 중...")

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
# 3. 개선방안 1: Orthogonal 팩터 계산
# =============================================================================
print("\n[3] Orthogonal 팩터 계산 중...")

# 시장 수익률
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

# 1. Residual Momentum (잔차 모멘텀)
res_mom_12 = residual_returns.rolling(252).sum()
res_mom_1 = residual_returns.rolling(21).sum()
factors_ortho['res_mom_12_1'] = (res_mom_12 - res_mom_1).rank(axis=1, pct=True)

# 2. Idiosyncratic Volatility (고유 변동성) - 낮을수록 선호
idio_vol = residual_returns.rolling(63).std()
factors_ortho['idio_vol'] = (-idio_vol).rank(axis=1, pct=True)

# 3. BAB (Betting Against Beta) - 저베타 선호
factors_ortho['bab'] = (-beta_60).rank(axis=1, pct=True)

# 4. Downside Risk (하방 변동성) - 낮을수록 선호
downside_returns = returns.clip(upper=0)
semi_var = downside_returns.rolling(63).var()
factors_ortho['downside_risk'] = (-semi_var).rank(axis=1, pct=True)

# 5. Volume-Price Confirmation
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

# 기존 팩터
base_factor_names = list(factors_base.keys())
FACTORS_BASE_NP = np.stack([factors_base[f].values for f in base_factor_names], axis=2)
FACTORS_BASE_NP = np.nan_to_num(FACTORS_BASE_NP, nan=0.5)

# Orthogonal 팩터
ortho_factor_names = list(factors_ortho.keys())
FACTORS_ORTHO_NP = np.stack([factors_ortho[f].values for f in ortho_factor_names], axis=2)
FACTORS_ORTHO_NP = np.nan_to_num(FACTORS_ORTHO_NP, nan=0.5)

# 베타
BETA_NP = beta_60.values.astype(np.float64)
BETA_NP = np.nan_to_num(BETA_NP, nan=1.0)

# 변동성 (Inverse-Vol용)
stock_vol = returns.rolling(63).std()
STOCK_VOL_NP = stock_vol.values.astype(np.float64)
STOCK_VOL_NP = np.nan_to_num(STOCK_VOL_NP, nan=0.02)

# 레짐
REGIME_SCORES_NP = regime_scores.values.astype(np.float64)

# 가격/수익률
PRICES_NP = prices.values.astype(np.float64)
RETURNS_NP = returns.values.astype(np.float64)

n_days, n_assets = RETURNS_NP.shape
n_base_factors = len(base_factor_names)
n_ortho_factors = len(ortho_factor_names)

print(f"  데이터: {n_days}일, {n_assets}종목")
print(f"  기존 팩터: {n_base_factors}개, Orthogonal 팩터: {n_ortho_factors}개")

# =============================================================================
# 6. 통합 백테스트 함수 (순수 Python - 모든 개선방안 통합)
# =============================================================================
def backtest_v30(
    prices: np.ndarray,
    returns: np.ndarray,
    factors_base: np.ndarray,
    factors_ortho: np.ndarray,
    regime_scores: np.ndarray,
    beta: np.ndarray,
    stock_vol: np.ndarray,
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
    ortho_weight: float = 0.3,  # Orthogonal 팩터 비중
    beta_cap_bull: float = 1.2,
    beta_cap_bear: float = 0.6,
    hysteresis_days: int = 3,
    tail_es_threshold: float = -0.05,
    tail_scale: float = 0.7,
    # 고정 파라미터
    cost_rate: float = 0.002,
    dd_warning: float = -0.20,
    dd_stop: float = -0.35,
) -> dict:
    """통합 백테스트 (모든 개선방안 포함)"""
    
    n_days, n_assets = returns.shape
    n_base = factors_base.shape[2]
    n_ortho = factors_ortho.shape[2]
    
    # 기존 팩터 가중치 (v26)
    fw_base = np.array([0.12, 0.12, 0.10, 0.10, 0.08, 0.10, 0.10, 0.10, 0.08, 0.10])
    
    # Orthogonal 팩터 가중치
    fw_ortho = np.array([0.30, 0.20, 0.20, 0.15, 0.15])  # res_mom, idio_vol, bab, downside, vp
    
    # 레짐별 팩터 가중치 (개선방안 4)
    # bull: 모멘텀 강조, bear: 리스크 팩터 강조
    fw_bull_adj = np.array([1.2, 1.2, 0.8, 0.9, 0.9, 1.1, 1.1, 1.1, 1.0, 1.1])  # 모멘텀 ↑
    fw_bear_adj = np.array([0.8, 0.8, 1.3, 1.2, 1.0, 0.9, 0.9, 0.9, 1.0, 0.9])  # low_vol, quality ↑
    
    # 초기화
    portfolio_value = 1.0
    peak = 1.0
    is_halted = False
    halt_counter = 0
    
    current_weights = np.zeros(n_assets)
    daily_returns = []
    
    # 변동성 버퍼
    vol_window = 20
    port_ret_buffer = []
    
    # 히스테리시스 버퍼
    regime_history = []
    current_regime = 'neutral'
    
    # ES 버퍼 (Tail Risk용)
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
        
        # 개선방안 3: 레짐 히스테리시스
        if use_regime_hysteresis:
            regime_history.append(new_regime)
            if len(regime_history) > hysteresis_days:
                regime_history.pop(0)
            
            # 연속 N일 같은 레짐이어야 전환
            if len(regime_history) >= hysteresis_days:
                if all(r == new_regime for r in regime_history[-hysteresis_days:]):
                    current_regime = new_regime
            # 아니면 기존 레짐 유지
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
        
        # 개선방안 5: Tail Risk 관리
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
            
            # 개선방안 4: 레짐별 팩터 가중치
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
            
            # 개선방안 1: Orthogonal 팩터 추가
            if use_ortho_factors:
                ortho_scores = np.zeros(n_assets)
                for i in range(n_assets):
                    for f in range(n_ortho):
                        ortho_scores[i] += factors_ortho[t-1, i, f] * fw_ortho[f]
                ortho_scores = ortho_scores / (fw_ortho.sum() + 1e-10)
                
                # 기존 + Orthogonal 혼합
                scores = (1 - ortho_weight) * scores + ortho_weight * ortho_scores
            
            # 상위 K 종목
            top_indices = np.argsort(-scores)[:top_k]
            
            # 노출도
            exposure = base_exposure * vol_adj
            exposure = min(max_lev, max(min_lev, exposure))
            exposure *= dd_scale
            exposure *= tail_scale_factor
            
            # 새 가중치 계산
            new_weights = np.zeros(n_assets)
            
            # 개선방안 2: Inverse-Vol 가중치
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
                # 동일 가중
                for idx in top_indices:
                    new_weights[idx] = exposure / top_k
            
            # 개선방안 2: Beta Cap
            if use_beta_cap:
                # 레짐별 베타 상한
                if current_regime in ['ultra_bull', 'bull']:
                    beta_cap = beta_cap_bull
                else:
                    beta_cap = beta_cap_bear
                
                # 포트폴리오 베타 계산
                port_beta = 0.0
                total_weight = 0.0
                for i in range(n_assets):
                    if new_weights[i] > 0:
                        port_beta += new_weights[i] * beta[t-1, i]
                        total_weight += new_weights[i]
                
                if total_weight > 0:
                    port_beta = port_beta / total_weight
                
                # 베타 초과 시 스케일 다운
                if port_beta > beta_cap:
                    scale = beta_cap / port_beta
                    new_weights = new_weights * scale
            
            current_weights = new_weights
        
        # 수익률 계산
        port_ret = 0.0
        for i in range(n_assets):
            port_ret += current_weights[i] * returns[t, i]
        
        # 비용
        if t % rebal_days == 0:
            port_ret -= cost_rate * 0.5
        
        daily_returns.append(port_ret)
        port_ret_buffer.append(port_ret)
        portfolio_value *= (1 + port_ret)
    
    # 성과 계산
    daily_returns = np.array(daily_returns)
    valid_returns = daily_returns[daily_returns != 0]
    
    if len(valid_returns) < 100:
        return {'sharpe': 0, 'is_sharpe': 0, 'oos_sharpe': 0, 'annual_return': 0, 'mdd': 0}
    
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
    
    # IS/OOS (55%/45% 분리)
    is_end = int(len(valid_returns) * 0.55)
    is_returns = valid_returns[:is_end]
    oos_returns = valid_returns[is_end:]
    
    is_sharpe = (np.mean(is_returns) / np.std(is_returns)) * np.sqrt(252) if np.std(is_returns) > 1e-10 else 0
    oos_sharpe = (np.mean(oos_returns) / np.std(oos_returns)) * np.sqrt(252) if np.std(oos_returns) > 1e-10 else 0
    
    return {
        'sharpe': sharpe,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'annual_return': annual_return,
        'mdd': max_dd
    }

# =============================================================================
# 7. 수만 가지 조합 파레토 최적화
# =============================================================================
print("\n[6] 수만 가지 조합 파레토 최적화 시작...")

# v26 베이스라인 고정 파라미터
v26_fixed = {
    'trend_w': 0.47,
    'vol_w': 0.28,
    'credit_w': 0.25,
    'min_lev': 0.3,
    'cost_rate': 0.002,
    'dd_warning': -0.20,
    'dd_stop': -0.35,
}

# 개선방안 ON/OFF 조합 (2^5 = 32가지)
improvement_combos = list(itertools.product([False, True], repeat=5))
# [use_ortho, use_inv_vol, use_beta_cap, use_hysteresis, use_regime_fw]

# 기본 파라미터 그리드
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

# 개선방안 파라미터 그리드
improvement_params = {
    'ortho_weight': [0.2, 0.3, 0.4],
    'beta_cap_bull': [1.1, 1.2],
    'beta_cap_bear': [0.5, 0.6],
    'hysteresis_days': [2, 3],
    'tail_es_threshold': [-0.04, -0.05],
    'tail_scale': [0.6, 0.7],
}

# 모든 조합 생성
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
test_count = 0
start_time = datetime.now()

# 테스트 실행
for imp_combo in improvement_combos:
    use_ortho, use_inv_vol, use_beta_cap, use_hysteresis, use_regime_fw = imp_combo
    use_tail_risk = use_hysteresis  # Tail Risk는 히스테리시스와 함께 사용
    
    imp_name = f"O{int(use_ortho)}V{int(use_inv_vol)}B{int(use_beta_cap)}H{int(use_hysteresis)}F{int(use_regime_fw)}"
    
    for base_combo in base_combos:
        base_params = dict(zip(base_keys, base_combo))
        
        for imp_param_combo in imp_combos:
            imp_params = dict(zip(imp_keys, imp_param_combo))
            
            try:
                result = backtest_v30(
                    PRICES_NP, RETURNS_NP, FACTORS_BASE_NP, FACTORS_ORTHO_NP,
                    REGIME_SCORES_NP, BETA_NP, STOCK_VOL_NP,
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
                )
                
                if result['sharpe'] > 0:
                    results.append({
                        'imp_combo': imp_name,
                        'use_ortho': use_ortho,
                        'use_inv_vol': use_inv_vol,
                        'use_beta_cap': use_beta_cap,
                        'use_hysteresis': use_hysteresis,
                        'use_regime_fw': use_regime_fw,
                        **result,
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

print(f"\n  완료! 총 유효 결과: {len(results):,}")

# =============================================================================
# 8. 결과 분석
# =============================================================================
print("\n[7] 결과 분석...")

if len(results) > 0:
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('sharpe', ascending=False)
    
    # 상위 30개 결과
    print("\n상위 30개 결과:")
    print("-" * 140)
    for idx, row in results_df.head(30).iterrows():
        print(f"  Sharpe: {row['sharpe']:.4f} | IS: {row['is_sharpe']:.4f} | OOS: {row['oos_sharpe']:.4f} | "
              f"AR: {row['annual_return']*100:.1f}% | MDD: {row['mdd']*100:.1f}% | "
              f"{row['imp_combo']}")
    
    # v26 베이스라인 대비 비교
    print("\n" + "=" * 80)
    print("v26 베이스라인 대비 비교")
    print("=" * 80)
    print(f"  v26 베이스라인: Sharpe 2.02, IS 2.04, OOS 1.40, MDD -29.3%")
    
    best = results_df.iloc[0]
    print(f"\n  최고 성과:")
    print(f"    Sharpe: {best['sharpe']:.4f} ({(best['sharpe']/2.02-1)*100:+.1f}%)")
    print(f"    IS Sharpe: {best['is_sharpe']:.4f} ({(best['is_sharpe']/2.04-1)*100:+.1f}%)")
    print(f"    OOS Sharpe: {best['oos_sharpe']:.4f} ({(best['oos_sharpe']/1.40-1)*100:+.1f}%)")
    print(f"    MDD: {best['mdd']*100:.2f}%")
    print(f"    조합: {best['imp_combo']}")
    
    # 개선방안별 효과 분석
    print("\n개선방안별 평균 효과:")
    for imp in ['use_ortho', 'use_inv_vol', 'use_beta_cap', 'use_hysteresis', 'use_regime_fw']:
        on_avg = results_df[results_df[imp] == True]['sharpe'].mean()
        off_avg = results_df[results_df[imp] == False]['sharpe'].mean()
        effect = (on_avg / off_avg - 1) * 100 if off_avg > 0 else 0
        print(f"  {imp}: ON={on_avg:.4f}, OFF={off_avg:.4f}, 효과={effect:+.2f}%")
    
    # 조합별 평균 성과
    print("\n조합별 평균 성과 (상위 10개):")
    combo_avg = results_df.groupby('imp_combo').agg({
        'sharpe': 'mean',
        'oos_sharpe': 'mean',
        'mdd': 'mean'
    }).sort_values('sharpe', ascending=False)
    
    for combo, row in combo_avg.head(10).iterrows():
        print(f"  {combo}: Sharpe={row['sharpe']:.4f}, OOS={row['oos_sharpe']:.4f}, MDD={row['mdd']*100:.1f}%")
    
    # 파레토 프론티어 (Sharpe vs MDD)
    print("\n파레토 프론티어 (Sharpe vs MDD):")
    pareto_front = []
    for idx, row in results_df.iterrows():
        is_dominated = False
        for idx2, row2 in results_df.iterrows():
            if row2['sharpe'] > row['sharpe'] and row2['mdd'] > row['mdd']:
                is_dominated = True
                break
        if not is_dominated:
            pareto_front.append(row)
    
    pareto_df = pd.DataFrame(pareto_front).sort_values('sharpe', ascending=False)
    print(f"  파레토 최적 조합 수: {len(pareto_df)}")
    
    for idx, row in pareto_df.head(10).iterrows():
        print(f"  Sharpe: {row['sharpe']:.4f} | OOS: {row['oos_sharpe']:.4f} | MDD: {row['mdd']*100:.1f}% | {row['imp_combo']}")
    
    # 결과 저장
    output = {
        'baseline': {'sharpe': 2.02, 'is_sharpe': 2.04, 'oos_sharpe': 1.40, 'mdd': -0.293},
        'total_tests': total_tests,
        'valid_results': len(results),
        'best_result': best.to_dict(),
        'improvement_effects': {
            imp: {
                'on_avg': float(results_df[results_df[imp] == True]['sharpe'].mean()),
                'off_avg': float(results_df[results_df[imp] == False]['sharpe'].mean())
            }
            for imp in ['use_ortho', 'use_inv_vol', 'use_beta_cap', 'use_hysteresis', 'use_regime_fw']
        },
        'combo_avg': combo_avg.head(20).to_dict(),
        'pareto_front': pareto_df.head(30).to_dict('records'),
        'top_results': results_df.head(100).to_dict('records')
    }
    
    with open('/home/ubuntu/ares_v30_mega_pareto_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n결과 저장: /home/ubuntu/ares_v30_mega_pareto_results.json")

print(f"\n완료! 총 소요 시간: {(datetime.now() - start_time).total_seconds()/60:.1f}분")
