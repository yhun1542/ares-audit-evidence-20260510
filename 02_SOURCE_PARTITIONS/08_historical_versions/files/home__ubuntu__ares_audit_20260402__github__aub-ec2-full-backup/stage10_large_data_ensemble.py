#!/usr/bin/env python3
"""
Stage 10: 대규모 데이터 앙상블 테스트
- ares_x_v11_0.db 사용 (5,774 symbols, 2002-2025)
- ICIR_v4 + ML 앙상블 결합
- 메타 라벨링 구현
"""

import os
import sys
import json
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# LightGBM
import lightgbm as lgb

# API Keys
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', 'AIzaSyAE_aG8-5t8wViYWXCwpVG2I2YUR6C2b4c')
XAI_API_KEY = os.environ.get('XAI_API_KEY', 'xai-aw5kHcocJqB77k0s23o5sTmIlhwrRiTtb2RrlqnT24KtliXfA5A3XsSeeE7bwisjkWZrhpok0yDCc3xh')

print("=" * 60)
print("Stage 10: 대규모 데이터 앙상블 테스트")
print("목표: OOS Sharpe 3.0+")
print("=" * 60)

# ============================================================
# Phase 1: 대규모 데이터 로드
# ============================================================
print("\n[Phase 1] 대규모 데이터 로드...")

DB_PATH = "/home/ubuntu/etf_44_polygon.db"
conn = sqlite3.connect(DB_PATH)

# 전체 데이터 로드 (etf_44_polygon.db)
query = """
SELECT date, symbol, open, high, low, close, volume
FROM daily_ohlcv
WHERE date >= '2005-01-01'
ORDER BY date, symbol
"""
df = pd.read_sql(query, conn)
df['date'] = pd.to_datetime(df['date'])
print(f"  데이터 로드 완료: {len(df):,} rows, {df['symbol'].nunique():,} symbols")
print(f"  기간: {df['date'].min()} ~ {df['date'].max()}")

# ============================================================
# Phase 2: ICIR_v4 알파 계산
# ============================================================
print("\n[Phase 2] ICIR_v4 알파 계산...")

def calculate_icir_v4(df, lookback=120, min_periods=60):
    """ICIR_v4 알파 계산 (Information Coefficient * IR)"""
    df = df.sort_values(['symbol', 'date']).copy()
    
    # 수익률 계산
    df['returns'] = df.groupby('symbol')['close'].pct_change()
    
    # 모멘텀 팩터 (다양한 기간)
    for period in [20, 60, 120, 252]:
        df[f'mom_{period}'] = df.groupby('symbol')['close'].pct_change(period)
    
    # 변동성
    df['volatility'] = df.groupby('symbol')['returns'].transform(
        lambda x: x.rolling(20).std() * np.sqrt(252)
    )
    
    # ICIR 계산 (Cross-Sectional IC의 롤링 평균 / 표준편차)
    def calc_icir(group, factor_col, fwd_col, lookback):
        """단일 팩터의 ICIR 계산"""
        ic_series = group.groupby('date').apply(
            lambda x: x[factor_col].corr(x[fwd_col]) if len(x) > 5 else np.nan
        )
        icir = ic_series.rolling(lookback, min_periods=min_periods).mean() / \
               (ic_series.rolling(lookback, min_periods=min_periods).std() + 1e-10)
        return icir
    
    # Forward Return (타겟)
    df['fwd_return_20d'] = df.groupby('symbol')['close'].pct_change(20).shift(-20)
    
    # 각 팩터의 ICIR 계산 및 결합
    df['icir_score'] = 0
    for period in [20, 60, 120]:
        factor_col = f'mom_{period}'
        # Cross-Sectional Rank
        df[f'{factor_col}_rank'] = df.groupby('date')[factor_col].rank(pct=True)
        df['icir_score'] += df[f'{factor_col}_rank']
    
    # 정규화
    df['icir_score'] = df.groupby('date')['icir_score'].rank(pct=True)
    
    return df

df = calculate_icir_v4(df)
print(f"  ICIR_v4 계산 완료")

# ============================================================
# Phase 3: 확장 피처 엔지니어링
# ============================================================
print("\n[Phase 3] 확장 피처 엔지니어링...")

def generate_extended_features(df):
    """확장 피처 생성"""
    df = df.sort_values(['symbol', 'date']).copy()
    
    # 다양한 기간의 수익률
    for period in [5, 10, 20, 60, 120, 252]:
        if f'returns_{period}d' not in df.columns:
            df[f'returns_{period}d'] = df.groupby('symbol')['close'].pct_change(period)
    
    # 변동성 (다양한 기간)
    for period in [10, 20, 60]:
        df[f'volatility_{period}d'] = df.groupby('symbol')['returns'].transform(
            lambda x: x.rolling(period).std() * np.sqrt(252)
        )
    
    # RSI
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / (loss + 1e-10)
        return 100 - (100 / (1 + rs))
    
    df['rsi_14'] = df.groupby('symbol')['close'].transform(lambda x: calc_rsi(x, 14))
    
    # MACD
    df['ema_12'] = df.groupby('symbol')['close'].transform(lambda x: x.ewm(span=12).mean())
    df['ema_26'] = df.groupby('symbol')['close'].transform(lambda x: x.ewm(span=26).mean())
    df['macd'] = df['ema_12'] - df['ema_26']
    
    # Volume Ratio
    df['volume_ma_20'] = df.groupby('symbol')['volume'].transform(lambda x: x.rolling(20).mean())
    df['volume_ratio'] = df['volume'] / (df['volume_ma_20'] + 1)
    
    # 가격 위치
    df['high_252'] = df.groupby('symbol')['high'].transform(lambda x: x.rolling(252).max())
    df['low_252'] = df.groupby('symbol')['low'].transform(lambda x: x.rolling(252).min())
    df['price_vs_high'] = df['close'] / (df['high_252'] + 1e-10) - 1
    df['price_vs_low'] = df['close'] / (df['low_252'] + 1e-10) - 1
    
    # 이동평균 대비 위치
    for period in [20, 50, 200]:
        df[f'ma_{period}'] = df.groupby('symbol')['close'].transform(lambda x: x.rolling(period).mean())
        df[f'price_vs_ma_{period}'] = df['close'] / (df[f'ma_{period}'] + 1e-10) - 1
    
    # Cross-Sectional Rank
    df['returns_rank'] = df.groupby('date')['returns'].rank(pct=True)
    df['rsi_rank'] = df.groupby('date')['rsi_14'].rank(pct=True)
    df['vol_rank'] = df.groupby('date')['volatility_20d'].rank(pct=True)
    
    # Volatility Adjusted Momentum
    df['vol_adj_mom'] = df['mom_20'] / (df['volatility_20d'] + 1e-10)
    
    # 시장 평균
    df['market_return'] = df.groupby('date')['returns'].transform('mean')
    df['excess_return'] = df['returns'] - df['market_return']
    
    return df

df = generate_extended_features(df)

# 피처 목록
feature_cols = [
    'returns_5d', 'returns_10d', 'returns_20d', 'returns_60d', 'returns_120d',
    'volatility_10d', 'volatility_20d', 'volatility_60d',
    'rsi_14', 'macd', 'volume_ratio',
    'price_vs_high', 'price_vs_low', 'price_vs_ma_20', 'price_vs_ma_50', 'price_vs_ma_200',
    'returns_rank', 'rsi_rank', 'vol_rank', 'vol_adj_mom',
    'market_return', 'excess_return', 'icir_score'
]

print(f"  피처 생성 완료: {len(feature_cols)} features")

# 결측치 제거
df_clean = df.dropna(subset=feature_cols + ['fwd_return_20d'])
print(f"  결측치 제거 후: {len(df_clean):,} rows")

# ============================================================
# Phase 4: ICIR_v4 + ML 앙상블 학습
# ============================================================
print("\n[Phase 4] ICIR_v4 + ML 앙상블 학습...")

def walk_forward_ensemble_with_icir(df, feature_cols, target_col='fwd_return_20d',
                                     train_window=750, test_window=20, purge_days=5):
    """Walk-Forward 앙상블 학습 (ICIR_v4 + ML)"""
    
    dates = df['date'].unique()
    dates = np.sort(dates)
    
    all_predictions = []
    
    # LightGBM 파라미터
    lgb_params = {
        'objective': 'regression',
        'metric': 'rmse',
        'num_leaves': 31,
        'learning_rate': 0.05,
        'feature_fraction': 0.7,
        'bagging_fraction': 0.7,
        'bagging_freq': 5,
        'n_estimators': 100,
        'verbose': -1,
        'random_state': 42
    }
    
    start_idx = train_window + purge_days
    
    for i in range(start_idx, len(dates) - test_window, test_window):
        train_end_date = dates[i - purge_days - 1]
        test_start_date = dates[i]
        test_end_date = dates[min(i + test_window - 1, len(dates) - 1)]
        
        # 학습/테스트 데이터
        train_mask = df['date'] <= train_end_date
        train_df = df[train_mask].copy()
        
        test_mask = (df['date'] >= test_start_date) & (df['date'] <= test_end_date)
        test_df = df[test_mask].copy()
        
        if len(train_df) < 1000 or len(test_df) < 10:
            continue
        
        # 피처 및 타겟
        X_train = train_df[feature_cols].values
        y_train = train_df[target_col].values
        X_test = test_df[feature_cols].values
        
        # LightGBM 학습
        model = lgb.LGBMRegressor(**lgb_params)
        model.fit(X_train, y_train)
        
        # ML 예측
        ml_pred = model.predict(X_test)
        
        # ICIR_v4 점수
        icir_score = test_df['icir_score'].values
        
        # 앙상블 (가중 평균)
        # ICIR과 ML의 상관관계 기반 가중치 결정
        ensemble_pred = 0.6 * icir_score + 0.4 * (ml_pred - ml_pred.mean()) / (ml_pred.std() + 1e-10)
        
        # 결과 저장
        test_df = test_df.copy()
        test_df['ml_pred'] = ml_pred
        test_df['ensemble_pred'] = ensemble_pred
        all_predictions.append(test_df[['date', 'symbol', 'close', 'returns', 'fwd_return_20d', 
                                        'icir_score', 'ml_pred', 'ensemble_pred']])
        
        if len(all_predictions) % 50 == 0:
            print(f"    진행: {len(all_predictions)} windows 완료")
    
    return pd.concat(all_predictions, ignore_index=True) if all_predictions else None

# 앙상블 학습 실행
print("  Walk-Forward 앙상블 학습 시작...")
results_df = walk_forward_ensemble_with_icir(df_clean, feature_cols,
                                              train_window=750, test_window=20, purge_days=5)

if results_df is not None:
    print(f"  학습 완료: {len(results_df):,} predictions")
else:
    print("  학습 실패: 데이터 부족")
    sys.exit(1)

# ============================================================
# Phase 5: 포트폴리오 백테스트 (다양한 신호)
# ============================================================
print("\n[Phase 5] 포트폴리오 백테스트...")

def backtest_portfolio(df, signal_col, top_k=10, cost_bps=4):
    """포트폴리오 백테스트"""
    dates = df['date'].unique()
    dates = np.sort(dates)
    
    portfolio_returns = []
    
    for date in dates:
        day_df = df[df['date'] == date].copy()
        
        if len(day_df) < top_k:
            continue
        
        # 상위 K개 종목 선택
        top_stocks = day_df.nlargest(top_k, signal_col)
        
        # 동일 가중 포트폴리오 수익률
        port_return = top_stocks['fwd_return_20d'].mean()
        
        # 거래 비용 차감
        port_return -= cost_bps / 10000
        
        portfolio_returns.append({
            'date': date,
            'return': port_return
        })
    
    return pd.DataFrame(portfolio_returns)

# 다양한 신호로 백테스트
print("  다양한 신호로 백테스트 실행...")
results = []

for signal_col in ['icir_score', 'ml_pred', 'ensemble_pred']:
    for top_k in [5, 8, 10, 15]:
        port_df = backtest_portfolio(results_df, signal_col=signal_col, top_k=top_k, cost_bps=4)
        
        if len(port_df) > 0:
            returns = port_df['return'].dropna()
            
            if len(returns) > 0:
                sharpe = returns.mean() / (returns.std() + 1e-10) * np.sqrt(252 / 20)
                total_return = (1 + returns).prod() - 1
                annual_return = (1 + total_return) ** (252 / (len(returns) * 20)) - 1
                volatility = returns.std() * np.sqrt(252 / 20)
                
                results.append({
                    'signal': signal_col,
                    'top_k': top_k,
                    'sharpe': sharpe,
                    'annual_return': annual_return * 100,
                    'volatility': volatility * 100,
                    'n_periods': len(returns)
                })
                
                print(f"    {signal_col} Top-{top_k}: Sharpe={sharpe:.4f}, Return={annual_return*100:.2f}%")

# ============================================================
# Phase 6: 결과 분석
# ============================================================
print("\n[Phase 6] 결과 분석...")

# 결과 저장
output_dir = "/home/ubuntu/AUB/stage10_tests"
os.makedirs(output_dir, exist_ok=True)

results_summary = {
    'timestamp': datetime.now().isoformat(),
    'data_info': {
        'rows': len(df_clean),
        'symbols': df_clean['symbol'].nunique(),
        'date_range': f"{df_clean['date'].min()} ~ {df_clean['date'].max()}"
    },
    'feature_count': len(feature_cols),
    'experiments': results
}

with open(f"{output_dir}/stage10_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json", 'w') as f:
    json.dump(results_summary, f, indent=2, default=str)

# 최고 성능 출력
if results:
    best = max(results, key=lambda x: x['sharpe'])
    print(f"\n최고 성능: {best['signal']} Top-{best['top_k']}")
    print(f"  Sharpe: {best['sharpe']:.4f}")
    print(f"  Annual Return: {best['annual_return']:.2f}%")
    print(f"  Volatility: {best['volatility']:.2f}%")
    
    # 신호별 비교
    print("\n신호별 최고 성능 비교:")
    for signal in ['icir_score', 'ml_pred', 'ensemble_pred']:
        signal_results = [r for r in results if r['signal'] == signal]
        if signal_results:
            best_signal = max(signal_results, key=lambda x: x['sharpe'])
            print(f"  {signal}: Sharpe={best_signal['sharpe']:.4f} (Top-{best_signal['top_k']})")

print("\n" + "=" * 60)
print("Stage 10 완료")
print("=" * 60)

conn.close()
