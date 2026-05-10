#!/usr/bin/env python3
"""
레짐 기반 숏 전략 시뮬레이션
- PSQ (1x 인버스): RISK_OFF에서 ON
- SQQQ (3x 인버스): 강화 조건에서만 짧게 ON
"""
import pandas as pd
import numpy as np
from datetime import datetime

print("=== 레짐 기반 숏 전략 시뮬레이션 ===")

# 결과 로드
df = pd.read_csv('regime_backtest_v2_result.csv')
df['datetime'] = pd.to_datetime(df['datetime'])
df['date'] = df['datetime'].dt.date

print(f"데이터: {len(df):,} rows ({df['datetime'].min()} ~ {df['datetime'].max()})")

# QQQ 수익률 계산
df['qqq_return'] = df['close'].pct_change()

# PSQ 수익률 (QQQ의 -1배)
df['psq_return'] = -df['qqq_return']

# SQQQ 수익률 (QQQ의 -3배, 레버리지 decay 고려)
df['sqqq_return'] = -3 * df['qqq_return']

# 전략 1: Buy & Hold QQQ
df['bh_qqq_value'] = (1 + df['qqq_return']).cumprod()

# 전략 2: 레짐 기반 PSQ 헤지
# RISK_OFF: 50% QQQ + 50% PSQ
# NEUTRAL: 100% QQQ
# RISK_ON: 100% QQQ
def regime_psq_return(row):
    if row['regime_state'] == -1:  # RISK_OFF
        return 0.5 * row['qqq_return'] + 0.5 * row['psq_return']
    else:
        return row['qqq_return']

df['regime_psq_return'] = df.apply(regime_psq_return, axis=1)
df['regime_psq_value'] = (1 + df['regime_psq_return']).cumprod()

# 전략 3: 레짐 기반 PSQ + SQQQ (강화)
# RISK_OFF (score < -1.5): 30% QQQ + 50% PSQ + 20% SQQQ
# RISK_OFF (score >= -1.5): 50% QQQ + 50% PSQ
# NEUTRAL/ON: 100% QQQ
def regime_enhanced_return(row):
    if row['regime_state'] == -1:
        if row['regime_score'] < -1.5:  # 강화 조건
            return 0.3 * row['qqq_return'] + 0.5 * row['psq_return'] + 0.2 * row['sqqq_return']
        else:
            return 0.5 * row['qqq_return'] + 0.5 * row['psq_return']
    else:
        return row['qqq_return']

df['regime_enhanced_return'] = df.apply(regime_enhanced_return, axis=1)
df['regime_enhanced_value'] = (1 + df['regime_enhanced_return']).cumprod()

# 일별 수익률로 변환
daily = df.groupby('date').agg({
    'bh_qqq_value': 'last',
    'regime_psq_value': 'last',
    'regime_enhanced_value': 'last',
    'regime_state': 'mean',
    'regime_transition': 'sum'
}).reset_index()

daily['bh_qqq_return'] = daily['bh_qqq_value'].pct_change()
daily['regime_psq_return'] = daily['regime_psq_value'].pct_change()
daily['regime_enhanced_return'] = daily['regime_enhanced_value'].pct_change()

# 성과 지표 계산
def calc_metrics(returns, name):
    returns = returns.dropna()
    total_return = (1 + returns).prod() - 1
    annual_return = (1 + total_return) ** (252 / len(returns)) - 1
    volatility = returns.std() * np.sqrt(252)
    sharpe = annual_return / volatility if volatility > 0 else 0
    max_dd = (returns.cumsum() - returns.cumsum().cummax()).min()
    
    print(f"\n{name}:")
    print(f"  총 수익률: {total_return*100:.1f}%")
    print(f"  연환산 수익률: {annual_return*100:.1f}%")
    print(f"  연환산 변동성: {volatility*100:.1f}%")
    print(f"  샤프 비율: {sharpe:.2f}")
    print(f"  최대 낙폭: {max_dd*100:.1f}%")
    
    return {
        'name': name,
        'total_return': total_return,
        'annual_return': annual_return,
        'volatility': volatility,
        'sharpe': sharpe,
        'max_dd': max_dd
    }

print("\n=== 전체 기간 성과 (2016-2022) ===")
metrics = []
metrics.append(calc_metrics(daily['bh_qqq_return'], 'Buy & Hold QQQ'))
metrics.append(calc_metrics(daily['regime_psq_return'], 'Regime + PSQ 헤지'))
metrics.append(calc_metrics(daily['regime_enhanced_return'], 'Regime + PSQ + SQQQ 강화'))

# 코로나 폭락 기간 분석
crash_daily = daily[(daily['date'] >= pd.to_datetime('2020-02-20').date()) & 
                    (daily['date'] <= pd.to_datetime('2020-03-23').date())]

print("\n=== 코로나 폭락 기간 성과 (2020-02-20 ~ 2020-03-23) ===")
calc_metrics(crash_daily['bh_qqq_return'], 'Buy & Hold QQQ')
calc_metrics(crash_daily['regime_psq_return'], 'Regime + PSQ 헤지')
calc_metrics(crash_daily['regime_enhanced_return'], 'Regime + PSQ + SQQQ 강화')

# 전환 비용 분석
print("\n=== 전환 비용 분석 ===")
total_transitions = df['regime_transition'].sum()
print(f"총 전환 횟수: {total_transitions:,}")
print(f"일평균 전환: {total_transitions / len(daily):.1f}")

# 전환 비용 가정: 0.1% per transition
transition_cost = total_transitions * 0.001
print(f"예상 전환 비용 (0.1%/회): {transition_cost*100:.1f}%")

print("\n완료!")
