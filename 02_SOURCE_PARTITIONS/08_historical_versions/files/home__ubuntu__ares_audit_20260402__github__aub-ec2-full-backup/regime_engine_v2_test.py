#!/usr/bin/env python3
"""
레짐 엔진 v2 테스트 - 튜닝된 파라미터
"""
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
from intraday_regime_engine_v1 import IntradayRegimeEngine, RegimeConfig

print("=== 레짐 엔진 v2 테스트 (튜닝된 파라미터) ===")

# 데이터 로드
conn = sqlite3.connect('/home/ubuntu/etf_data_s3.db')

# QQQ 전체 기간 (2016-2022)
query = """
    SELECT * FROM minute_ohlcv 
    WHERE symbol = 'QQQ' 
    ORDER BY datetime
"""
df = pd.read_sql(query, conn)
conn.close()

print(f"로드된 데이터: {len(df):,} rows")
print(f"기간: {df['datetime'].min()} ~ {df['datetime'].max()}")

# 튜닝된 파라미터로 엔진 실행
config = RegimeConfig(
    hysteresis_minutes=3,
    cooldown_minutes=5,
    off_threshold=-0.5,  # 튜닝: -1.5 → -0.5
    on_threshold=0.5,    # 튜닝: 1.0 → 0.5
    shock_return_threshold=-0.015,  # 튜닝: -2% → -1.5%
    shock_vol_multiplier=2.5  # 튜닝: 3.0 → 2.5
)
engine = IntradayRegimeEngine(config)

result = engine.backtest(df)

# 결과 분석
print("\n=== 결과 분석 (v2) ===")
print(f"총 레코드: {len(result):,}")

# 상태 분포
state_counts = result['regime_state'].value_counts()
print(f"\n상태 분포:")
for state, count in sorted(state_counts.items()):
    pct = count / len(result) * 100
    state_name = {-1: 'RISK_OFF', 0: 'NEUTRAL', 1: 'RISK_ON'}[state]
    print(f"  {state_name}: {count:,} ({pct:.1f}%)")

# 전환 횟수
transitions = result['regime_transition'].sum()
print(f"\n전환 횟수: {transitions:,}")

# 일별 분석
result['date'] = pd.to_datetime(result['datetime']).dt.date
daily_transitions = result.groupby('date')['regime_transition'].sum()
print(f"일평균 전환: {daily_transitions.mean():.1f}")

# 연도별 분석
result['year'] = pd.to_datetime(result['datetime']).dt.year
yearly_stats = result.groupby('year').agg({
    'regime_state': 'mean',
    'regime_transition': 'sum'
}).reset_index()
print("\n연도별 분석:")
for _, row in yearly_stats.iterrows():
    print(f"  {row['year']}: avg_state={row['regime_state']:.2f}, transitions={row['regime_transition']:.0f}")

# 코로나 폭락 기간 분석
crash_df = result[(result['datetime'] >= '2020-02-20') & (result['datetime'] <= '2020-03-23')]
print(f"\n코로나 폭락 기간 (2020-02-20 ~ 2020-03-23):")
crash_states = crash_df['regime_state'].value_counts()
for state, count in sorted(crash_states.items()):
    pct = count / len(crash_df) * 100
    state_name = {-1: 'RISK_OFF', 0: 'NEUTRAL', 1: 'RISK_ON'}[state]
    print(f"  {state_name}: {count:,} ({pct:.1f}%)")

# 결과 저장
result.to_csv('regime_backtest_v2_result.csv', index=False)
print("\n결과 저장: regime_backtest_v2_result.csv")

# ORTEX 백필 상태 확인
print("\n=== ORTEX 백필 상태 ===")
import sqlite3
conn = sqlite3.connect('data/ares_x_v11_0_restored.db')
cursor = conn.execute("SELECT COUNT(*), COUNT(DISTINCT symbol) FROM ortex_short_interest")
row = cursor.fetchone()
print(f"ORTEX: {row[0]:,} records, {row[1]} symbols")
conn.close()
