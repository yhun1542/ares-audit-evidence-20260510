#!/usr/bin/env python3
"""
ARES v52 Add-on 백테스트 엔진
4대 AI 자문 기반 - Sharpe 3.0+ 달성 목표

Add-on 테스트 목록:
1. Baseline (현재 시스템)
2. +EWMA Vol Targeting
3. +IVOL Weighting
4. +DD -10%
5. +Orthogonal Momentum
6. +BAB Factor
7. +Combo1 (EWMA + IVOL + DD)
8. +Combo2 (All Add-ons)
"""

import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime
from collections import Counter
import warnings
warnings.filterwarnings('ignore')

print("=" * 80)
print("ARES v52 Add-on 백테스트 - 4대 AI 자문 기반")
print("=" * 80)

# =============================================================================
# 1. 데이터 로드
# =============================================================================
print("\n[1] 데이터 로드...")

conn = sqlite3.connect('/home/ubuntu/ares_x_unified_database/ares_universal_v2.db', timeout=30)
query = '''
SELECT date, symbol, close, volume FROM daily_ohlcv
WHERE date >= "2000-01-01" AND date <= "2024-12-31"
ORDER BY date, symbol
'''
df = pd.read_sql_query(query, conn)
conn.close()

df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
prices = df.pivot(index='date', columns='symbol', values='close')
prices.index = pd.to_datetime(prices.index)

# 유효 종목 필터링 (80% 이상 데이터)
valid_cols = prices.columns[prices.notna().mean() > 0.8]
prices = prices[valid_cols].ffill().bfill()
returns = prices.pct_change().fillna(0)

print(f"  종목 수: {len(prices.columns)}")
print(f"  거래일 수: {len(prices)}")
print(f"  기간: {prices.index[0].date()} ~ {prices.index[-1].date()}")

# 시장 수익률 (SPY proxy)
market_ret = returns.mean(axis=1)

# =============================================================================
# 2. 기본 팩터 계산
# =============================================================================
print("\n[2] 팩터 계산...")

# 기존 10개 팩터
mom_12 = prices.pct_change(252)
mom_1 = prices.pct_change(21)
mom_6 = prices.pct_change(126)
mom_3 = prices.pct_change(63)
mom_5 = prices.pct_change(5)
mom_10 = prices.pct_change(10)
high_52w = prices.rolling(252).max()
low_52w = prices.rolling(252).min()

factors = {}
factors['mom_12_1'] = (mom_12 - mom_1).rank(axis=1, pct=True)
factors['mom_6_1'] = (mom_6 - mom_1).rank(axis=1, pct=True)
factors['low_vol'] = (-returns.rolling(63).std()).rank(axis=1, pct=True)
rolling_ret = returns.rolling(252).mean()
rolling_vol = returns.rolling(252).std()
factors['quality'] = (rolling_ret / (rolling_vol + 1e-8)).rank(axis=1, pct=True)
factors['reversal'] = (-mom_5).rank(axis=1, pct=True)
factors['near_high'] = (prices / high_52w).rank(axis=1, pct=True)
mom_accel = mom_3 - mom_3.shift(21)
factors['acceleration'] = mom_accel.rank(axis=1, pct=True)
mkt_ret_series = returns.mean(axis=1)
excess_ret = returns.sub(mkt_ret_series, axis=0).rolling(63).mean()
factors['rel_strength'] = excess_ret.rank(axis=1, pct=True)
factors['range_position'] = ((prices - low_52w) / (high_52w - low_52w + 1e-8)).rank(axis=1, pct=True)
factors['mom_10'] = mom_10.rank(axis=1, pct=True)

print(f"  기본 팩터: {len(factors)}개")

# =============================================================================
# 3. Add-on 팩터 계산
# =============================================================================
print("\n[3] Add-on 팩터 계산...")

# 3.1 IVOL (Idiosyncratic Volatility)
print("  - IVOL 계산...")
residuals = returns.sub(market_ret, axis=0)
ivol = residuals.rolling(252).std()
factors['ivol'] = (-ivol).rank(axis=1, pct=True).reindex_like(prices).fillna(0.5)  # 낮은 IVOL = 높은 점수

# 3.2 Orthogonal Momentum (시장 요인 제거)
print("  - Orthogonal Momentum 계산...")
# 간소화된 버전: 시장 수익률 대비 잔차 모멘텀
market_mom_12 = market_ret.rolling(252).sum()
ortho_mom = mom_12.sub(market_mom_12, axis=0)
factors['ortho_mom'] = ortho_mom.rank(axis=1, pct=True).reindex_like(prices).fillna(0.5)

# 3.3 BAB (Betting Against Beta)
print("  - BAB 계산...")
# Rolling beta 계산
beta = returns.rolling(252).cov(market_ret) / (market_ret.rolling(252).var() + 1e-8)
factors['bab'] = (-beta).rank(axis=1, pct=True).reindex_like(prices).fillna(0.5)  # 낮은 beta = 높은 점수

print(f"  총 팩터: {len(factors)}개")

# =============================================================================
# 4. NumPy 변환
# =============================================================================
print("\n[4] NumPy 변환...")

# 팩터 가중치 (기존 + 새 팩터)
factor_weights_base = {
    'mom_12_1': 0.12, 'mom_6_1': 0.12, 'low_vol': 0.10, 'quality': 0.10,
    'reversal': 0.08, 'near_high': 0.10, 'acceleration': 0.10,
    'rel_strength': 0.10, 'range_position': 0.08, 'mom_10': 0.10
}

factor_weights_addon = {
    'mom_12_1': 0.10, 'mom_6_1': 0.10, 'low_vol': 0.08, 'quality': 0.08,
    'reversal': 0.06, 'near_high': 0.08, 'acceleration': 0.08,
    'rel_strength': 0.08, 'range_position': 0.06, 'mom_10': 0.08,
    'ivol': 0.08, 'ortho_mom': 0.08, 'bab': 0.04
}

factor_names = list(factors.keys())
FACTORS_NP = np.stack([factors[f].values for f in factor_names], axis=2)
FACTORS_NP = np.nan_to_num(FACTORS_NP, nan=0.5)
RETURNS_NP = returns.values
MARKET_RET_NP = market_ret.values

n_days, n_assets = RETURNS_NP.shape
print(f"  데이터: {n_days}일, {n_assets}종목, {len(factor_names)}팩터")

# =============================================================================
# 5. 백테스트 함수
# =============================================================================
def run_backtest(config):
    """
    Add-on 백테스트 실행
    
    config: dict with keys:
        - name: 테스트 이름
        - use_ewma_vol: EWMA 변동성 타겟팅 사용
        - use_ivol_weight: IVOL 가중치 사용
        - dd_threshold: DD 임계값 (-0.15 or -0.10)
        - use_ortho: Orthogonal 팩터 사용
        - use_bab: BAB 팩터 사용
        - vol_target: 변동성 목표 (0.12 or 0.10)
        - top_k: 상위 종목 수
    """
    name = config.get('name', 'Test')
    use_ewma_vol = config.get('use_ewma_vol', False)
    use_ivol_weight = config.get('use_ivol_weight', False)
    dd_threshold = config.get('dd_threshold', -0.15)
    use_ortho = config.get('use_ortho', False)
    use_bab = config.get('use_bab', False)
    vol_target = config.get('vol_target', 0.12)
    top_k = config.get('top_k', 35)
    
    # 팩터 가중치 설정
    if use_ortho or use_bab:
        fw = np.array([factor_weights_addon.get(f, 0.05) for f in factor_names])
    else:
        fw = np.array([factor_weights_base.get(f, 0.05) for f in factor_names[:10]] + [0.0] * (len(factor_names) - 10))
    
    fw = fw / fw.sum()
    
    # 파라미터
    params = {
        'SLOW60': -0.03,
        'SLOW120': -0.08,
        'BULL_CAP': 1.0,
        'CAP_SLOW': 0.2,
        'EXPOSURE_BULL': 1.0,
        'EXPOSURE_NEUTRAL': 0.7,
        'EXPOSURE_BEAR': 0.5,
        'EXPOSURE_CRISIS': 0.2,
        'DD_THRESHOLD': dd_threshold,
        'DD_REDUCTION': 0.5 if dd_threshold == -0.10 else 0.3,
        'VOL_TARGET': vol_target,
        'TOP_K': top_k
    }
    
    # 백테스트 실행
    portfolio_value = 1.0
    peak = 1.0
    current_weights = np.zeros(n_assets)
    daily_returns = []
    regime_history = []
    
    rebal_period = 14
    cost_bps = 0.002
    
    # EWMA 변동성 추적
    ewma_vol = 0.15
    ewma_alpha = 0.94
    
    for t in range(252, n_days):
        # SLOW_RISK 감지
        ret60 = np.sum(MARKET_RET_NP[t-60:t])
        ret120 = np.sum(MARKET_RET_NP[t-120:t])
        
        is_slow_risk = (ret60 <= params['SLOW60']) and (ret120 <= params['SLOW120'])
        
        # 변동성 기반 레짐
        vol20 = np.std(MARKET_RET_NP[t-20:t]) * np.sqrt(252)
        
        if is_slow_risk:
            regime = 'slow_risk'
        elif vol20 > 0.30:
            regime = 'crisis'
        elif vol20 > 0.20:
            regime = 'bear'
        elif vol20 < 0.12:
            regime = 'bull'
        else:
            regime = 'neutral'
        
        regime_history.append(regime)
        
        # 레짐별 노출도
        if regime == 'slow_risk':
            base_exposure = params['CAP_SLOW']
        elif regime == 'bull':
            base_exposure = min(params['EXPOSURE_BULL'], params['BULL_CAP'])
        elif regime == 'neutral':
            base_exposure = params['EXPOSURE_NEUTRAL']
        elif regime == 'bear':
            base_exposure = params['EXPOSURE_BEAR']
        else:  # crisis
            base_exposure = params['EXPOSURE_CRISIS']
        
        # DD Control
        if portfolio_value > peak:
            peak = portfolio_value
        dd = (portfolio_value - peak) / peak
        
        if dd < params['DD_THRESHOLD']:
            base_exposure *= (1 - params['DD_REDUCTION'])
        
        # EWMA Vol Targeting
        if use_ewma_vol and len(daily_returns) > 20:
            recent_ret = np.array(daily_returns[-20:])
            ewma_vol = ewma_alpha * ewma_vol + (1 - ewma_alpha) * np.std(recent_ret) * np.sqrt(252)
            vol_adj = params['VOL_TARGET'] / (ewma_vol + 0.01)
            vol_adj = min(1.5, max(0.2, vol_adj))
            base_exposure *= vol_adj
        
        # 리밸런싱
        if t % rebal_period == 0:
            scores = np.zeros(n_assets)
            for i in range(n_assets):
                for f in range(len(factor_names)):
                    scores[i] += FACTORS_NP[t-1, i, f] * fw[f]
            
            valid_mask = ~np.isnan(scores)
            scores[~valid_mask] = -999
            top_indices = np.argsort(-scores)[:params['TOP_K']]
            
            # 변동성 조정 (기존 방식)
            if not use_ewma_vol:
                port_ret_recent = daily_returns[-20:] if len(daily_returns) >= 20 else daily_returns
                if len(port_ret_recent) > 5:
                    port_vol = np.std(port_ret_recent) * np.sqrt(252)
                    vol_adj = params['VOL_TARGET'] / (port_vol + 0.01)
                    vol_adj = min(1.5, max(0.5, vol_adj))
                else:
                    vol_adj = 1.0
                
                exposure = base_exposure * vol_adj
            else:
                exposure = base_exposure
            
            exposure = min(2.0, max(0.1, exposure))
            
            # 가중치 계산
            new_weights = np.zeros(n_assets)
            
            if use_ivol_weight:
                # IVOL 기반 가중치 (낮은 IVOL = 높은 가중치)
                ivol_scores = FACTORS_NP[t-1, top_indices, factor_names.index('ivol')]
                ivol_weights = ivol_scores / (ivol_scores.sum() + 1e-8)
                for idx, w in zip(top_indices, ivol_weights):
                    new_weights[idx] = exposure * w
            else:
                # 동일 가중치
                for idx in top_indices:
                    new_weights[idx] = exposure / params['TOP_K']
            
            current_weights = new_weights
        
        # 수익률 계산
        port_ret = np.sum(current_weights * RETURNS_NP[t, :])
        
        if t % rebal_period == 0:
            port_ret -= cost_bps * 0.5
        
        daily_returns.append(port_ret)
        portfolio_value *= (1 + port_ret)
    
    # 성과 계산
    daily_returns = np.array(daily_returns)
    valid_returns = daily_returns[daily_returns != 0]
    
    if len(valid_returns) == 0:
        return {'name': name, 'sharpe': 0, 'mdd': 0, 'annual_ret': 0, 'total_ret': 0}
    
    mean_ret = np.mean(valid_returns)
    std_ret = np.std(valid_returns)
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 1e-10 else 0
    annual_return = mean_ret * 252
    
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
    
    return {
        'name': name,
        'sharpe': sharpe,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'mdd': max_dd * 100,
        'annual_ret': annual_return * 100,
        'total_ret': (cum_value - 1) * 100,
        'regime_dist': Counter(regime_history)
    }

# =============================================================================
# 6. Add-on 테스트 실행
# =============================================================================
print("\n[5] Add-on 백테스트 실행...")

test_configs = [
    # 1. Baseline
    {'name': 'Baseline', 'use_ewma_vol': False, 'use_ivol_weight': False, 
     'dd_threshold': -0.15, 'use_ortho': False, 'use_bab': False, 
     'vol_target': 0.12, 'top_k': 35},
    
    # 2. +EWMA Vol Targeting
    {'name': '+EWMA_Vol', 'use_ewma_vol': True, 'use_ivol_weight': False,
     'dd_threshold': -0.15, 'use_ortho': False, 'use_bab': False,
     'vol_target': 0.10, 'top_k': 35},
    
    # 3. +IVOL Weighting
    {'name': '+IVOL_Weight', 'use_ewma_vol': False, 'use_ivol_weight': True,
     'dd_threshold': -0.15, 'use_ortho': False, 'use_bab': False,
     'vol_target': 0.12, 'top_k': 35},
    
    # 4. +DD -10%
    {'name': '+DD_10pct', 'use_ewma_vol': False, 'use_ivol_weight': False,
     'dd_threshold': -0.10, 'use_ortho': False, 'use_bab': False,
     'vol_target': 0.12, 'top_k': 35},
    
    # 5. +Orthogonal Momentum
    {'name': '+Ortho_Mom', 'use_ewma_vol': False, 'use_ivol_weight': False,
     'dd_threshold': -0.15, 'use_ortho': True, 'use_bab': False,
     'vol_target': 0.12, 'top_k': 35},
    
    # 6. +BAB
    {'name': '+BAB', 'use_ewma_vol': False, 'use_ivol_weight': False,
     'dd_threshold': -0.15, 'use_ortho': False, 'use_bab': True,
     'vol_target': 0.12, 'top_k': 35},
    
    # 7. Combo1: EWMA + IVOL + DD -10%
    {'name': 'Combo1', 'use_ewma_vol': True, 'use_ivol_weight': True,
     'dd_threshold': -0.10, 'use_ortho': False, 'use_bab': False,
     'vol_target': 0.10, 'top_k': 25},
    
    # 8. Combo2: All Add-ons
    {'name': 'Combo2_All', 'use_ewma_vol': True, 'use_ivol_weight': True,
     'dd_threshold': -0.10, 'use_ortho': True, 'use_bab': True,
     'vol_target': 0.10, 'top_k': 25},
    
    # 9. Combo3: Aggressive (TOP_K=15)
    {'name': 'Combo3_Aggr', 'use_ewma_vol': True, 'use_ivol_weight': True,
     'dd_threshold': -0.10, 'use_ortho': True, 'use_bab': True,
     'vol_target': 0.08, 'top_k': 15},
    
    # 10. Combo4: Conservative (TOP_K=40)
    {'name': 'Combo4_Cons', 'use_ewma_vol': True, 'use_ivol_weight': True,
     'dd_threshold': -0.12, 'use_ortho': True, 'use_bab': True,
     'vol_target': 0.10, 'top_k': 40},
]

results = []
for i, config in enumerate(test_configs):
    print(f"  [{i+1}/{len(test_configs)}] {config['name']}...")
    result = run_backtest(config)
    results.append(result)
    print(f"       Sharpe: {result['sharpe']:.4f}, MDD: {result['mdd']:.2f}%, Return: {result['annual_ret']:.2f}%")

# =============================================================================
# 7. 결과 출력
# =============================================================================
print("\n" + "=" * 80)
print("ARES v52 Add-on 백테스트 결과")
print("=" * 80)

# 정렬 (Sharpe 기준)
results_sorted = sorted(results, key=lambda x: x['sharpe'], reverse=True)

print(f"\n{'순위':<4} {'이름':<20} {'Sharpe':<10} {'IS Sharpe':<10} {'OOS Sharpe':<10} {'MDD':<10} {'Annual':<10}")
print("-" * 84)

for i, r in enumerate(results_sorted):
    print(f"{i+1:<4} {r['name']:<20} {r['sharpe']:<10.4f} {r['is_sharpe']:<10.4f} {r['oos_sharpe']:<10.4f} {r['mdd']:<10.2f}% {r['annual_ret']:<10.2f}%")

# 최고 성과 상세
best = results_sorted[0]
print(f"\n최고 성과: {best['name']}")
print(f"  Sharpe: {best['sharpe']:.4f}")
print(f"  IS Sharpe: {best['is_sharpe']:.4f}")
print(f"  OOS Sharpe: {best['oos_sharpe']:.4f}")
print(f"  MDD: {best['mdd']:.2f}%")
print(f"  Annual Return: {best['annual_ret']:.2f}%")
print(f"  Total Return: {best['total_ret']:.2f}%")

# Baseline 대비 개선
baseline = next(r for r in results if r['name'] == 'Baseline')
print(f"\nBaseline 대비 개선:")
print(f"  Sharpe: {baseline['sharpe']:.4f} → {best['sharpe']:.4f} ({(best['sharpe']/baseline['sharpe']-1)*100:+.1f}%)")
print(f"  MDD: {baseline['mdd']:.2f}% → {best['mdd']:.2f}% ({best['mdd']-baseline['mdd']:+.2f}%)")

# 결과 저장
results_df = pd.DataFrame(results_sorted)
results_df.to_csv('/home/ubuntu/ares_v52_addon_results.csv', index=False)
print(f"\n결과 저장: /home/ubuntu/ares_v52_addon_results.csv")

print("\n" + "=" * 80)
print("백테스트 완료!")
print("=" * 80)
