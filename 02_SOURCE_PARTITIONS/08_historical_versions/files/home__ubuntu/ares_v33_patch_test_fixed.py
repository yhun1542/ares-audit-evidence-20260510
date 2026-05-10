#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v33: CPU 최적화 패치 테스트 (디버깅 버전)
================================================================================
v26 베이스라인 위에 9개 패치 조합을 테스트 (512 조합)
순수 NumPy 벡터화로 안정적인 실행
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
import json
import time
import os
from datetime import datetime
from typing import Tuple, Dict, List, Optional
import itertools
import warnings
warnings.filterwarnings('ignore')

print("=" * 80)
print("ARES v33: CPU 최적화 패치 테스트 (Fixed)")
print("=" * 80)
print(f"시작 시간: {datetime.now()}")

# ============================================================================
# 글로벌 데이터
# ============================================================================
PRICES_NP = None
RETURNS_NP = None
FACTORS_NP = None
REGIME_SCORES_NP = None
FAST_REGIME_NP = None
ELIGIBILITY_NP = None
N_DAYS = 0
N_ASSETS = 0


def init_global_data():
    """데이터 초기화"""
    global PRICES_NP, RETURNS_NP, FACTORS_NP, REGIME_SCORES_NP, FAST_REGIME_NP, ELIGIBILITY_NP, N_DAYS, N_ASSETS
    
    print("\n[1] 데이터 로드 중...")
    
    # === 주식 데이터 로드 ===
    conn = sqlite3.connect('/home/ubuntu/ares_x_unified_database/ares_universal_v2.db', timeout=30)
    query = """
    SELECT date, symbol, close FROM daily_ohlcv
    WHERE date >= '2016-01-01' AND date <= '2024-12-31'
    ORDER BY date, symbol
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
    prices = df.pivot(index='date', columns='symbol', values='close')
    prices.index = pd.to_datetime(prices.index)
    
    # Patch A1: bfill 제거
    valid_cols = prices.columns[prices.notna().mean() > 0.8]
    prices = prices[valid_cols].ffill()
    
    # Patch A2: 포인트-인-타임 유니버스
    print("  포인트-인-타임 유니버스 계산 중...")
    avail = prices.notna().astype(float)
    ratio = avail.rolling(252, min_periods=252).mean()
    eligibility = (ratio >= 0.95) & prices.shift(1).notna()
    eligibility.iloc[:252] = False
    ELIGIBILITY_NP = eligibility.values.astype(np.float64)
    
    PRICES_NP = prices.values.astype(np.float64)
    
    returns = prices.pct_change()
    RETURNS_NP = returns.values.astype(np.float64)
    RETURNS_NP[0, :] = 0
    RETURNS_NP = np.nan_to_num(RETURNS_NP, nan=0.0)
    
    N_DAYS, N_ASSETS = PRICES_NP.shape
    print(f"  주식: {N_ASSETS}종목, {N_DAYS}일")
    
    # === ETF 데이터 로드 ===
    conn = sqlite3.connect('/home/ubuntu/etf_data_s3.db', timeout=30)
    query = """
    SELECT date, symbol, close FROM daily_ohlcv
    WHERE symbol IN ('QQQ', 'DIA', 'HYG', 'LQD')
    AND date >= '2015-01-01' ORDER BY date, symbol
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    etf = df.pivot(index='date', columns='symbol', values='close')
    etf.index = pd.to_datetime(etf.index)
    etf = etf.ffill()
    
    common_dates = prices.index.intersection(etf.index)
    
    # === 레짐 점수 계산 ===
    print("\n[2] 레짐 점수 계산 중...")
    mkt = etf['QQQ'].reindex(common_dates) if 'QQQ' in etf.columns else etf['DIA'].reindex(common_dates)
    mkt_ret = mkt.pct_change()
    sma_20 = mkt.rolling(20).mean()
    sma_50 = mkt.rolling(50).mean()
    sma_200 = mkt.rolling(200).mean()
    
    # Trend Score
    trend_score = pd.Series(0.5, index=common_dates)
    trend_score[mkt > sma_200] += 0.20
    trend_score[sma_50 > sma_200] += 0.15
    trend_score[mkt > sma_50] += 0.10
    trend_score[mkt > sma_20] += 0.05
    trend_score[mkt < sma_200] -= 0.20
    trend_score[(mkt < sma_200) & (sma_50 < sma_200)] -= 0.15
    trend_score[mkt < sma_20] -= 0.05
    trend_score = trend_score.clip(0, 1)
    
    # Volatility Score
    realized_vol = mkt_ret.rolling(20).std() * np.sqrt(252)
    hist_vol = mkt_ret.rolling(252).std() * np.sqrt(252)
    vol_ratio = realized_vol / (hist_vol + 1e-8)
    vol_score = 1 - vol_ratio.clip(0.5, 2.0) / 2.0
    vol_score = vol_score.clip(0, 1)
    
    # Credit Score
    if 'HYG' in etf.columns and 'LQD' in etf.columns:
        hyg = etf['HYG'].reindex(common_dates)
        lqd = etf['LQD'].reindex(common_dates)
        credit_ratio = hyg / lqd
        credit_ma = credit_ratio.rolling(60).mean()
        credit_std = credit_ratio.rolling(252).std()
        credit_zscore = (credit_ratio - credit_ma) / (credit_std + 1e-8)
        credit_score = 0.5 + credit_zscore.clip(-2, 2) / 4
        credit_score = credit_score.clip(0, 1)
    else:
        credit_score = pd.Series(0.5, index=common_dates)
    
    # Fast Regime (C2)
    cum_ret_5d = (1 + mkt_ret).rolling(5).apply(lambda x: x.prod() - 1, raw=True)
    fast_regime = pd.Series(0.5, index=common_dates)
    fast_regime[cum_ret_5d < -0.05] = 0.1
    fast_regime[cum_ret_5d < -0.03] = 0.25
    fast_regime[cum_ret_5d < -0.01] = 0.35
    fast_regime[cum_ret_5d > 0.03] = 0.8
    fast_regime[cum_ret_5d > 0.05] = 0.9
    
    # Breadth (C3)
    sma_200_stk = prices.rolling(200).mean()
    breadth = (prices > sma_200_stk).mean(axis=1).clip(0, 1)
    
    regime_scores = pd.DataFrame({
        'trend': trend_score,
        'vol': vol_score,
        'credit': credit_score,
        'breadth': breadth.reindex(common_dates)
    }).reindex(prices.index).ffill().fillna(0.5)
    
    REGIME_SCORES_NP = regime_scores.values.astype(np.float64)
    
    fast_regime_aligned = fast_regime.reindex(prices.index).ffill().fillna(0.5)
    FAST_REGIME_NP = fast_regime_aligned.values.astype(np.float64)
    
    # === 팩터 계산 ===
    print("\n[3] 팩터 계산 중...")
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
    
    FACTORS_NP = np.stack([
        factors['mom_12_1'].values,
        factors['mom_6_1'].values,
        factors['low_vol'].values,
        factors['quality'].values,
        factors['reversal'].values,
        factors['near_high'].values,
        factors['acceleration'].values,
        factors['rel_strength'].values,
        factors['range_position'].values,
        factors['mom_10'].values
    ], axis=2).astype(np.float64)
    
    FACTORS_NP = np.nan_to_num(FACTORS_NP, nan=0.5)
    
    print(f"  팩터: 10개")
    print(f"  데이터 준비 완료!")
    
    return N_DAYS, N_ASSETS


def fast_backtest(params: Dict) -> Dict:
    """순수 NumPy 백테스트"""
    
    n_days, n_assets = N_DAYS, N_ASSETS
    n_factors = 10
    
    # 파라미터 추출
    trend_w = params['trend_w']
    vol_w = params['vol_w']
    credit_w = params['credit_w']
    breadth_w = params.get('breadth_w', 0.0)
    fast_w = params.get('fast_w', 0.0)
    
    crisis_thresh = params['crisis_thresh']
    bear_thresh = params['bear_thresh']
    neutral_thresh = params['neutral_thresh']
    bull_thresh = params['bull_thresh']
    
    ultra_bull_exp = params['ultra_bull_exp']
    bull_exp = params['bull_exp']
    neutral_exp = params['neutral_exp']
    bear_exp = params['bear_exp']
    crisis_exp = params['crisis_exp']
    
    fw_ultra_bull = np.array(params['fw_ultra_bull'])
    fw_bull = np.array(params['fw_bull'])
    fw_neutral = np.array(params['fw_neutral'])
    fw_bear = np.array(params['fw_bear'])
    fw_crisis = np.array(params['fw_crisis'])
    
    top_k = params['top_k']
    rebal_days = params['rebal_days']
    target_vol = params['target_vol']
    max_lev = params['max_lev']
    min_lev = params['min_lev']
    cost_rate = params['cost_rate']
    dd_warning = params['dd_warning']
    dd_stop = params['dd_stop']
    
    # 패치 플래그
    use_pit_universe = params.get('use_pit_universe', False)
    use_graduated_dd = params.get('use_graduated_dd', False)
    use_emergency_riskoff = params.get('use_emergency_riskoff', False)
    use_cvar_limit = params.get('use_cvar_limit', False)
    use_real_port_vol = params.get('use_real_port_vol', False)
    use_hysteresis = params.get('use_hysteresis', False)
    use_fast_regime = params.get('use_fast_regime', False)
    use_breadth = params.get('use_breadth', False)
    use_adaptive_rebal = params.get('use_adaptive_rebal', False)
    
    hysteresis_width = params.get('hysteresis_width', 0.03)
    emergency_crisis_trigger = params.get('emergency_crisis_trigger', 0.75)
    emergency_loss_trigger = params.get('emergency_loss_trigger', -0.04)
    emergency_max_exp = params.get('emergency_max_exp', 0.25)
    cvar_limit = params.get('cvar_limit', -0.03)
    
    # 상태 변수
    values = np.zeros(n_days)
    values[0] = 1.0
    peak = 1.0
    
    prev_weights = np.zeros(n_assets)
    current_weights = np.zeros(n_assets)
    
    daily_returns = np.zeros(n_days)
    
    port_ret_buffer = []
    realized_vol_current = 0.20
    
    current_regime_level = 2  # neutral
    
    for t in range(1, n_days):
        # 1. 레짐 분류
        composite = (REGIME_SCORES_NP[t-1, 0] * trend_w + 
                    REGIME_SCORES_NP[t-1, 1] * vol_w + 
                    REGIME_SCORES_NP[t-1, 2] * credit_w)
        
        if use_breadth and breadth_w > 0:
            composite += REGIME_SCORES_NP[t-1, 3] * breadth_w
        
        if use_fast_regime and fast_w > 0:
            total_w = trend_w + vol_w + credit_w + (breadth_w if use_breadth else 0)
            composite = composite / total_w * (1 - fast_w) + FAST_REGIME_NP[t-1] * fast_w
        else:
            total_w = trend_w + vol_w + credit_w + (breadth_w if use_breadth else 0)
            composite = composite / total_w
        
        crisis_prob = 1 - composite
        
        # 레짐 결정
        raw_level = 2
        if crisis_prob >= crisis_thresh:
            raw_level = 4
        elif crisis_prob >= bear_thresh:
            raw_level = 3
        elif crisis_prob >= neutral_thresh:
            raw_level = 2
        elif crisis_prob >= bull_thresh:
            raw_level = 1
        else:
            raw_level = 0
        
        if use_hysteresis:
            thresholds = [0.0, bull_thresh, neutral_thresh, bear_thresh, crisis_thresh]
            if raw_level > current_regime_level and current_regime_level < 4:
                next_thresh = thresholds[current_regime_level + 1]
                if crisis_prob >= next_thresh + hysteresis_width:
                    current_regime_level += 1
            elif raw_level < current_regime_level and current_regime_level > 0:
                curr_thresh = thresholds[current_regime_level]
                if crisis_prob < curr_thresh - hysteresis_width:
                    current_regime_level -= 1
        else:
            current_regime_level = raw_level
        
        # 레짐별 설정
        if current_regime_level == 4:
            base_exp, fw = crisis_exp, fw_crisis
        elif current_regime_level == 3:
            base_exp, fw = bear_exp, fw_bear
        elif current_regime_level == 2:
            base_exp, fw = neutral_exp, fw_neutral
        elif current_regime_level == 1:
            base_exp, fw = bull_exp, fw_bull
        else:
            base_exp, fw = ultra_bull_exp, fw_ultra_bull
        
        # 2. 리밸런싱
        if use_adaptive_rebal:
            if realized_vol_current < 0.15:
                interval = rebal_days
            elif realized_vol_current < 0.25:
                interval = max(7, int(rebal_days * 0.7))
            else:
                interval = max(5, int(rebal_days * 0.5))
            should_rebalance = (t % interval == 0)
        else:
            should_rebalance = (t % rebal_days == 0)
        
        if should_rebalance:
            # 팩터 점수
            scores = np.zeros(n_assets)
            for i in range(n_assets):
                if use_pit_universe and ELIGIBILITY_NP[t-1, i] < 0.5:
                    scores[i] = -999.0
                else:
                    scores[i] = np.sum(FACTORS_NP[t-1, i, :] * fw)
            
            # 상위 K 종목
            sorted_idx = np.argsort(-scores)
            top_idx = sorted_idx[:top_k]
            
            # 변동성 타겟
            if use_real_port_vol and len(port_ret_buffer) >= 20:
                rv = np.std(port_ret_buffer[-20:]) * np.sqrt(252)
                realized_vol_current = rv
                if rv > 0.01:
                    vol_adj = min(2.0, max(0.5, target_vol / rv))
                else:
                    vol_adj = 1.0
            else:
                vol_adj = 1.0
            
            # CVaR 제한
            if use_cvar_limit and len(port_ret_buffer) >= 20:
                sorted_rets = np.sort(port_ret_buffer[-60:])
                cutoff = max(1, int(len(sorted_rets) * 0.05))
                cvar = np.mean(sorted_rets[:cutoff])
                if cvar < cvar_limit:
                    reduction = min(0.5, (cvar_limit - cvar) / abs(cvar_limit))
                    base_exp = base_exp * (1 - reduction)
            
            exposure = min(max_lev, max(min_lev, base_exp * vol_adj))
            
            current_weights = np.zeros(n_assets)
            valid_count = 0
            for idx in top_idx:
                if scores[idx] > -900:
                    valid_count += 1
            
            if exposure > 0.05 and valid_count > 0:
                weight_per_stock = exposure / valid_count
                for idx in top_idx:
                    if scores[idx] > -900:
                        current_weights[idx] = weight_per_stock
        
        # 3. DD Control
        current_dd = (values[t-1] - peak) / peak if peak > 0 else 0
        
        if use_graduated_dd:
            if current_dd >= 0:
                dd_scale = 1.0
            elif current_dd >= dd_warning:
                dd_scale = 1.0 - 0.3 * (current_dd / dd_warning)
            elif current_dd >= dd_stop:
                dd_scale = 0.7 - 0.4 * ((current_dd - dd_warning) / (dd_stop - dd_warning))
            else:
                dd_scale = 0.3
        else:
            if current_dd <= dd_stop:
                dd_scale = 0.3
            elif current_dd <= dd_warning:
                dd_scale = 0.7
            else:
                dd_scale = 1.0
        
        active_weights = current_weights * dd_scale
        
        # 4. 응급 Risk-off
        if use_emergency_riskoff:
            prev_ret = daily_returns[t-1] if t > 1 else 0
            if crisis_prob >= emergency_crisis_trigger or prev_ret <= emergency_loss_trigger:
                total_exp = np.sum(active_weights)
                if total_exp > emergency_max_exp:
                    active_weights = active_weights * (emergency_max_exp / total_exp)
        
        # 5. 수익률 계산
        turnover = np.sum(np.abs(active_weights - prev_weights))
        cost = turnover * cost_rate
        
        port_ret = np.sum(active_weights * RETURNS_NP[t, :]) - cost
        
        port_ret_buffer.append(port_ret)
        if len(port_ret_buffer) > 60:
            port_ret_buffer.pop(0)
        
        daily_returns[t] = port_ret
        values[t] = values[t-1] * (1 + port_ret)
        
        if values[t] > peak:
            peak = values[t]
        
        prev_weights = active_weights.copy()
    
    # 6. 성과 지표
    valid_returns = daily_returns[1:]
    mean_ret = np.mean(valid_returns)
    std_ret = np.std(valid_returns)
    sharpe = mean_ret / std_ret * np.sqrt(252) if std_ret > 1e-10 else 0
    
    total_return = values[-1] / values[0] - 1
    n_years = n_days / 252.0
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
    
    # MDD
    running_max = np.maximum.accumulate(values)
    drawdowns = (values - running_max) / running_max
    max_dd = np.min(drawdowns)
    
    # IS/OOS
    is_end = int(n_days * 0.55)
    
    is_returns = daily_returns[1:is_end]
    if len(is_returns) > 10:
        is_mean = np.mean(is_returns)
        is_std = np.std(is_returns)
        is_sharpe = is_mean / is_std * np.sqrt(252) if is_std > 1e-10 else 0
    else:
        is_sharpe = 0
    
    oos_returns = daily_returns[is_end:]
    if len(oos_returns) > 10:
        oos_mean = np.mean(oos_returns)
        oos_std = np.std(oos_returns)
        oos_sharpe = oos_mean / oos_std * np.sqrt(252) if oos_std > 1e-10 else 0
    else:
        oos_sharpe = 0
    
    return {
        'sharpe': sharpe,
        'annual_return': annual_return,
        'mdd': max_dd,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'is_oos_gap': (is_sharpe - oos_sharpe) / is_sharpe if is_sharpe > 0 else 0
    }


def generate_patch_combinations():
    """패치 조합 생성"""
    
    # v26 베이스라인
    base_params = {
        'trend_w': 0.47, 'vol_w': 0.28, 'credit_w': 0.25,
        'crisis_thresh': 0.70, 'bear_thresh': 0.50, 'neutral_thresh': 0.30, 'bull_thresh': 0.13,
        'ultra_bull_exp': 2.0, 'bull_exp': 1.7, 'neutral_exp': 1.2, 'bear_exp': 0.6, 'crisis_exp': 0.24,
        'fw_ultra_bull': [0.22, 0.18, 0.05, 0.07, 0.07, 0.13, 0.09, 0.08, 0.06, 0.05],
        'fw_bull': [0.20, 0.16, 0.07, 0.09, 0.09, 0.11, 0.09, 0.09, 0.05, 0.05],
        'fw_neutral': [0.11, 0.11, 0.18, 0.16, 0.11, 0.07, 0.09, 0.07, 0.05, 0.05],
        'fw_bear': [0.07, 0.07, 0.26, 0.23, 0.14, 0.05, 0.06, 0.05, 0.04, 0.03],
        'fw_crisis': [0.05, 0.05, 0.30, 0.26, 0.14, 0.05, 0.05, 0.05, 0.03, 0.02],
        'top_k': 48, 'rebal_days': 14, 'target_vol': 0.29, 'max_lev': 2.0, 'min_lev': 0.3,
        'cost_rate': 0.002, 'dd_warning': -0.20, 'dd_stop': -0.35,
    }
    
    patches = [
        'use_pit_universe',       # A2
        'use_graduated_dd',       # B1
        'use_emergency_riskoff',  # B2
        'use_cvar_limit',         # B3
        'use_real_port_vol',      # B4
        'use_hysteresis',         # C1
        'use_fast_regime',        # C2
        'use_breadth',            # C3
        'use_adaptive_rebal',     # D1
    ]
    
    param_list = []
    
    for combo in itertools.product([False, True], repeat=len(patches)):
        params = base_params.copy()
        
        for i, patch in enumerate(patches):
            params[patch] = combo[i]
        
        # 패치별 추가 파라미터
        if params.get('use_emergency_riskoff'):
            params['emergency_crisis_trigger'] = 0.75
            params['emergency_loss_trigger'] = -0.04
            params['emergency_max_exp'] = 0.25
            params['min_lev'] = 0.0
        
        if params.get('use_cvar_limit'):
            params['cvar_limit'] = -0.03
        
        if params.get('use_hysteresis'):
            params['hysteresis_width'] = 0.03
        
        if params.get('use_fast_regime'):
            params['fast_w'] = 0.15
        
        if params.get('use_breadth'):
            params['breadth_w'] = 0.10
            params['trend_w'] = 0.42
            params['vol_w'] = 0.25
            params['credit_w'] = 0.23
        
        param_list.append(params)
    
    return param_list


def main():
    """메인 실행"""
    
    # 1. 데이터 초기화
    init_global_data()
    
    # 2. 패치 조합 생성
    param_list = generate_patch_combinations()
    print(f"\n패치 조합 수: {len(param_list)}")
    
    # 3. 테스트 실행
    print("\n[4] 패치 조합 테스트 시작...")
    
    results = []
    start_time = time.time()
    
    for i, params in enumerate(param_list):
        try:
            result = fast_backtest(params)
            result['params'] = params
            results.append(result)
        except Exception as e:
            print(f"  Error at {i}: {e}")
        
        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed
            remaining = (len(param_list) - i - 1) / rate
            print(f"  진행: {i+1}/{len(param_list)} ({100*(i+1)/len(param_list):.1f}%) | "
                  f"유효: {len(results)} | 남은 시간: {remaining/60:.1f}분")
    
    # 4. 결과 정렬
    print("\n" + "=" * 80)
    print("테스트 완료!")
    print("=" * 80)
    
    results_sorted = sorted(results, key=lambda x: x['oos_sharpe'], reverse=True)
    
    print("\n[TOP 30 패치 조합 - OOS Sharpe 기준]")
    print("-" * 120)
    print(f"{'순위':>4} | {'Sharpe':>8} | {'IS Sharpe':>10} | {'OOS Sharpe':>11} | {'IS/OOS Gap':>10} | {'MDD':>8} | {'Annual':>8} | 패치")
    print("-" * 120)
    
    for i, r in enumerate(results_sorted[:30]):
        patches_on = []
        if r['params'].get('use_pit_universe'): patches_on.append('A2')
        if r['params'].get('use_graduated_dd'): patches_on.append('B1')
        if r['params'].get('use_emergency_riskoff'): patches_on.append('B2')
        if r['params'].get('use_cvar_limit'): patches_on.append('B3')
        if r['params'].get('use_real_port_vol'): patches_on.append('B4')
        if r['params'].get('use_hysteresis'): patches_on.append('C1')
        if r['params'].get('use_fast_regime'): patches_on.append('C2')
        if r['params'].get('use_breadth'): patches_on.append('C3')
        if r['params'].get('use_adaptive_rebal'): patches_on.append('D1')
        
        print(f"{i+1:>4} | {r['sharpe']:>8.3f} | {r['is_sharpe']:>10.3f} | {r['oos_sharpe']:>11.3f} | "
              f"{r['is_oos_gap']*100:>9.1f}% | {r['mdd']*100:>7.1f}% | {r['annual_return']*100:>7.1f}% | "
              f"{'+'.join(patches_on) if patches_on else 'baseline'}")
    
    # 베이스라인 찾기
    baseline = next((r for r in results if not any([
        r['params'].get('use_pit_universe'),
        r['params'].get('use_graduated_dd'),
        r['params'].get('use_emergency_riskoff'),
        r['params'].get('use_cvar_limit'),
        r['params'].get('use_real_port_vol'),
        r['params'].get('use_hysteresis'),
        r['params'].get('use_fast_regime'),
        r['params'].get('use_breadth'),
        r['params'].get('use_adaptive_rebal'),
    ])), None)
    
    if baseline:
        print("\n[베이스라인 (패치 없음)]")
        print(f"  Sharpe: {baseline['sharpe']:.3f} | IS: {baseline['is_sharpe']:.3f} | "
              f"OOS: {baseline['oos_sharpe']:.3f} | MDD: {baseline['mdd']*100:.1f}%")
    
    # 5. 결과 저장
    output = {
        'timestamp': datetime.now().isoformat(),
        'total_tests': len(results),
        'top_results': results_sorted[:100],
        'baseline_result': baseline
    }
    
    with open('/home/ubuntu/v33_patch_test_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n결과 저장: /home/ubuntu/v33_patch_test_results.json")
    print(f"완료 시간: {datetime.now()}")


if __name__ == "__main__":
    main()
