#!/usr/bin/env python3
"""
ARES v2.1 Production-Level Backtest Pipeline V3
개선 사항:
1. 롱/숏 전략 (시장 중립)
2. 레짐 필터 (VIX 기반)
3. 최근 기간 테스트 (2018-2024)
4. 피처 IC 검증
5. 거래 비용 반영 (10 bps)
6. Purging/Embargo 강화
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
import json
import warnings
warnings.filterwarnings('ignore')

# LightGBM
import lightgbm as lgb
from scipy import stats

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database path
DB_PATH = '/home/ubuntu/ares_x_unified_database/ares_universal_v2.db'
OUTPUT_DIR = '/home/ubuntu/ares_v2_1_backtest'

# Configuration
CONFIG = {
    'start_date': '2015-01-01',  # 더 최근 기간
    'end_date': '2024-12-31',
    'train_years': 3,
    'val_years': 1,
    'test_years': 1,
    'step_months': 3,
    'purge_days': 10,  # 강화
    'embargo_days': 10,  # 강화
    'n_folds': 10,  # 더 많은 폴드
    'n_bags': 5,  # 더 많은 앙상블
    'transaction_cost': 0.0010,  # 10 bps
    'target_volatility': 0.15,  # 연 15%
    'min_price': 5.0,
    'min_dollar_volume': 5_000_000,  # 더 높은 유동성
    'top_n_long': 20,  # 롱 포지션 수
    'top_n_short': 20,  # 숏 포지션 수
    'vix_threshold': 25,  # 레짐 필터 임계값
}


def load_data():
    """데이터 로드 및 전처리"""
    conn = sqlite3.connect(DB_PATH)
    
    # 1. 가격 데이터 로드
    price_query = f"""
    SELECT symbol, date, open, high, low, close, volume
    FROM daily_ohlcv
    WHERE date >= '{CONFIG['start_date']}' 
    AND date <= '{CONFIG['end_date']}'
    ORDER BY symbol, date
    """
    price_df = pd.read_sql_query(price_query, conn)
    price_df['date'] = pd.to_datetime(price_df['date'])
    
    # 유동성 필터
    price_df['dollar_volume'] = price_df['close'] * price_df['volume']
    avg_dv = price_df.groupby('symbol')['dollar_volume'].mean()
    avg_price = price_df.groupby('symbol')['close'].mean()
    
    valid_symbols = avg_dv[(avg_dv >= CONFIG['min_dollar_volume']) & 
                           (avg_price >= CONFIG['min_price'])].index.tolist()
    
    price_df = price_df[price_df['symbol'].isin(valid_symbols)]
    logger.info(f"Selected {len(valid_symbols)} symbols (min_price={CONFIG['min_price']}, min_dv={CONFIG['min_dollar_volume']/1e6:.1f}M)")
    logger.info(f"Loaded {len(price_df):,} price records")
    
    # 2. 펀더멘털 데이터 로드
    if len(valid_symbols) > 0:
        fund_query = f"""
        SELECT ticker as symbol, date, 
               bm, ey, fcfy, gp_a, leverage, value_z, quality_z,
               roe, roic, gross_margin, net_margin
        FROM fundamentals_pit_daily
        WHERE date >= '{CONFIG['start_date']}' 
        AND date <= '{CONFIG['end_date']}'
        AND ticker IN ({','.join([f"'{s}'" for s in valid_symbols[:500]])})
        """
        fund_df = pd.read_sql_query(fund_query, conn)
        fund_df['date'] = pd.to_datetime(fund_df['date'])
        # roe, roic가 TEXT 타입이므로 숫자로 변환
        fund_df['roe'] = pd.to_numeric(fund_df['roe'], errors='coerce')
        fund_df['roic'] = pd.to_numeric(fund_df['roic'], errors='coerce')
    else:
        fund_df = pd.DataFrame()
    logger.info(f"Loaded {len(fund_df):,} fundamental records")
    
    # 3. VIX 데이터 로드
    vix_query = f"""
    SELECT date, close as vix
    FROM vix
    WHERE date >= '{CONFIG['start_date']}' 
    AND date <= '{CONFIG['end_date']}'
    """
    vix_df = pd.read_sql_query(vix_query, conn)
    vix_df['date'] = pd.to_datetime(vix_df['date'])
    logger.info(f"Loaded {len(vix_df):,} VIX records")
    
    # 4. 매크로 데이터 로드
    macro_query = f"""
    SELECT date, indicator_id as indicator, value
    FROM macro_indicators
    WHERE date >= '{CONFIG['start_date']}' 
    AND date <= '{CONFIG['end_date']}'
    AND indicator_id IN ('T10Y2Y', 'BAMLH0A0HYM2', 'FEDFUNDS')
    """
    macro_df = pd.read_sql_query(macro_query, conn)
    if len(macro_df) > 0:
        macro_df['date'] = pd.to_datetime(macro_df['date'])
        macro_pivot = macro_df.pivot_table(index='date', columns='indicator', values='value', aggfunc='first').reset_index()
    else:
        macro_pivot = pd.DataFrame()
    logger.info(f"Loaded {len(macro_df)} macro indicator records")
    
    conn.close()
    
    return price_df, fund_df, vix_df, macro_pivot


def compute_features(price_df, fund_df, vix_df, macro_df):
    """피처 엔지니어링 - IC 검증 포함"""
    logger.info("Computing features...")
    
    features_list = []
    
    for symbol in price_df['symbol'].unique():
        sym_price = price_df[price_df['symbol'] == symbol].copy().sort_values('date')
        sym_fund = fund_df[fund_df['symbol'] == symbol].copy() if symbol in fund_df['symbol'].values else pd.DataFrame()
        
        if len(sym_price) < 252:
            continue
        
        # 가격 기반 피처
        sym_price['returns'] = sym_price['close'].pct_change()
        sym_price['log_returns'] = np.log(sym_price['close'] / sym_price['close'].shift(1))
        
        # 모멘텀 피처
        sym_price['mom_1m'] = sym_price['close'].pct_change(21)
        sym_price['mom_3m'] = sym_price['close'].pct_change(63)
        sym_price['mom_6m'] = sym_price['close'].pct_change(126)
        sym_price['mom_12m'] = sym_price['close'].pct_change(252)
        sym_price['mom_12_1'] = (sym_price['close'] / sym_price['close'].shift(252)) / \
                                (sym_price['close'] / sym_price['close'].shift(21)) - 1
        
        # 변동성 피처
        sym_price['volatility_20d'] = sym_price['returns'].rolling(20).std() * np.sqrt(252)
        sym_price['volatility_60d'] = sym_price['returns'].rolling(60).std() * np.sqrt(252)
        sym_price['vol_ratio'] = sym_price['volatility_20d'] / sym_price['volatility_60d']
        
        # 기술적 피처
        sym_price['sma_20'] = sym_price['close'].rolling(20).mean()
        sym_price['sma_50'] = sym_price['close'].rolling(50).mean()
        sym_price['sma_200'] = sym_price['close'].rolling(200).mean()
        sym_price['price_sma20_ratio'] = sym_price['close'] / sym_price['sma_20']
        sym_price['price_sma50_ratio'] = sym_price['close'] / sym_price['sma_50']
        sym_price['price_sma200_ratio'] = sym_price['close'] / sym_price['sma_200']
        sym_price['sma_20_50_ratio'] = sym_price['sma_20'] / sym_price['sma_50']
        
        # RSI
        delta = sym_price['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        sym_price['rsi_14'] = 100 - (100 / (1 + rs))
        
        # Bollinger Bands
        sym_price['bb_middle'] = sym_price['close'].rolling(20).mean()
        sym_price['bb_std'] = sym_price['close'].rolling(20).std()
        sym_price['bb_upper'] = sym_price['bb_middle'] + 2 * sym_price['bb_std']
        sym_price['bb_lower'] = sym_price['bb_middle'] - 2 * sym_price['bb_std']
        sym_price['bb_position'] = (sym_price['close'] - sym_price['bb_lower']) / \
                                   (sym_price['bb_upper'] - sym_price['bb_lower'])
        
        # MACD
        sym_price['ema_12'] = sym_price['close'].ewm(span=12).mean()
        sym_price['ema_26'] = sym_price['close'].ewm(span=26).mean()
        sym_price['macd'] = sym_price['ema_12'] - sym_price['ema_26']
        sym_price['macd_signal'] = sym_price['macd'].ewm(span=9).mean()
        sym_price['macd_hist'] = sym_price['macd'] - sym_price['macd_signal']
        
        # 거래량 피처
        sym_price['volume_sma_20'] = sym_price['volume'].rolling(20).mean()
        sym_price['volume_ratio'] = sym_price['volume'] / sym_price['volume_sma_20']
        sym_price['dollar_volume'] = sym_price['close'] * sym_price['volume']
        sym_price['dollar_volume_sma_20'] = sym_price['dollar_volume'].rolling(20).mean()
        
        # Amihud Illiquidity
        sym_price['amihud'] = np.abs(sym_price['returns']) / (sym_price['dollar_volume'] / 1e6)
        sym_price['amihud_20d'] = sym_price['amihud'].rolling(20).mean()
        
        # 펀더멘털 피처 병합
        if len(sym_fund) > 0:
            sym_fund = sym_fund.sort_values('date')
            fund_cols = ['date']
            for col in ['roe', 'roic', 'gross_margin', 'net_margin', 'bm', 'ey', 'fcfy', 'value_z', 'quality_z']:
                if col in sym_fund.columns:
                    fund_cols.append(col)
            sym_price = pd.merge_asof(
                sym_price.sort_values('date'),
                sym_fund[fund_cols],
                on='date',
                direction='backward'
            )
        
        sym_price['symbol'] = symbol
        features_list.append(sym_price)
    
    features_df = pd.concat(features_list, ignore_index=True)
    
    # VIX 병합
    features_df = pd.merge_asof(
        features_df.sort_values('date'),
        vix_df.sort_values('date'),
        on='date',
        direction='backward'
    )
    
    # 매크로 병합
    if len(macro_df) > 0:
        features_df = pd.merge_asof(
            features_df.sort_values('date'),
            macro_df.sort_values('date'),
            on='date',
            direction='backward'
        )
    
    # 레짐 필터
    features_df['high_vix_regime'] = (features_df['vix'] > CONFIG['vix_threshold']).astype(int)
    
    # NaN 처리
    features_df = features_df.dropna(subset=['mom_12_1', 'volatility_20d'])
    
    logger.info(f"Computed {len(features_df):,} feature rows")
    
    return features_df


def compute_ic(features_df, feature_cols):
    """피처 IC (Information Coefficient) 계산"""
    logger.info("Computing feature ICs...")
    
    # 미래 수익률 계산 (10일)
    features_df = features_df.sort_values(['symbol', 'date'])
    features_df['fwd_return_10d'] = features_df.groupby('symbol')['close'].pct_change(10).shift(-10)
    
    ic_results = {}
    for col in feature_cols:
        if col in features_df.columns:
            valid_data = features_df[[col, 'fwd_return_10d']].dropna()
            if len(valid_data) > 100:
                ic, _ = stats.spearmanr(valid_data[col], valid_data['fwd_return_10d'])
                ic_results[col] = ic
    
    # IC 기준으로 정렬
    ic_sorted = sorted(ic_results.items(), key=lambda x: abs(x[1]), reverse=True)
    
    logger.info("Top 10 features by IC:")
    for feat, ic in ic_sorted[:10]:
        logger.info(f"  {feat}: {ic:.4f}")
    
    # 상위 피처 선택 (|IC| > 0.02)
    selected_features = [feat for feat, ic in ic_sorted if abs(ic) > 0.02][:25]
    
    return selected_features, ic_results


def generate_triple_barrier_labels(features_df, upper_mult=2.0, lower_mult=2.0, max_holding=10):
    """Triple Barrier 라벨링"""
    logger.info("Generating Triple Barrier labels...")
    
    labels_list = []
    
    for symbol in features_df['symbol'].unique():
        sym_data = features_df[features_df['symbol'] == symbol].copy().sort_values('date')
        
        if len(sym_data) < 50:
            continue
        
        # 변동성 기반 배리어
        sym_data['vol'] = sym_data['returns'].rolling(20).std()
        sym_data['upper_barrier'] = upper_mult * sym_data['vol']
        sym_data['lower_barrier'] = lower_mult * sym_data['vol']
        
        labels = []
        for i in range(len(sym_data) - max_holding):
            entry_price = sym_data.iloc[i]['close']
            upper = sym_data.iloc[i]['upper_barrier']
            lower = sym_data.iloc[i]['lower_barrier']
            
            if pd.isna(upper) or pd.isna(lower):
                labels.append(0)
                continue
            
            label = 0
            for j in range(1, max_holding + 1):
                if i + j >= len(sym_data):
                    break
                future_price = sym_data.iloc[i + j]['close']
                ret = (future_price - entry_price) / entry_price
                
                if ret >= upper:
                    label = 1
                    break
                elif ret <= -lower:
                    label = -1
                    break
            
            labels.append(label)
        
        sym_data = sym_data.iloc[:len(labels)].copy()
        sym_data['label'] = labels
        labels_list.append(sym_data)
    
    labeled_df = pd.concat(labels_list, ignore_index=True)
    
    label_dist = labeled_df['label'].value_counts().to_dict()
    logger.info(f"Label distribution: {label_dist}")
    logger.info(f"Generated {len(labeled_df):,} labels for {labeled_df['symbol'].nunique()} symbols")
    
    return labeled_df


def create_walk_forward_splits(data, n_folds=10):
    """Walk-Forward 분할 생성"""
    logger.info("Creating Walk-Forward splits...")
    
    dates = sorted(data['date'].unique())
    min_date = dates[0]
    max_date = dates[-1]
    
    train_days = CONFIG['train_years'] * 252
    val_days = CONFIG['val_years'] * 252
    test_days = CONFIG['test_years'] * 252
    step_days = CONFIG['step_months'] * 21
    
    splits = []
    current_start = 0
    
    while current_start + train_days + val_days + test_days < len(dates) and len(splits) < n_folds:
        train_start = dates[current_start]
        train_end = dates[min(current_start + train_days - 1, len(dates) - 1)]
        
        val_start = dates[min(current_start + train_days + CONFIG['purge_days'], len(dates) - 1)]
        val_end = dates[min(current_start + train_days + val_days - 1, len(dates) - 1)]
        
        test_start = dates[min(current_start + train_days + val_days + CONFIG['embargo_days'], len(dates) - 1)]
        test_end = dates[min(current_start + train_days + val_days + test_days - 1, len(dates) - 1)]
        
        splits.append({
            'train': (train_start, train_end),
            'val': (val_start, val_end),
            'test': (test_start, test_end)
        })
        
        current_start += step_days
    
    logger.info(f"Generated {len(splits)} walk-forward folds")
    return splits


def train_model(X_train, y_train, X_val, y_val, bag_idx):
    """LightGBM 모델 학습"""
    
    # 클래스 가중치 계산
    class_weights = {
        -1: len(y_train) / (3 * (y_train == -1).sum() + 1),
        0: len(y_train) / (3 * (y_train == 0).sum() + 1),
        1: len(y_train) / (3 * (y_train == 1).sum() + 1)
    }
    sample_weights = np.array([class_weights[y] for y in y_train])
    
    params = {
        'objective': 'multiclass',
        'num_class': 3,
        'metric': 'multi_logloss',
        'boosting_type': 'gbdt',
        'num_leaves': 31 + bag_idx * 5,
        'learning_rate': 0.05,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'min_child_samples': 20,
        'reg_alpha': 0.1,
        'reg_lambda': 0.1,
        'n_estimators': 200,
        'verbose': -1,
        'seed': 42 + bag_idx,
        'device': 'gpu',
        'gpu_platform_id': 0,
        'gpu_device_id': 0,
    }
    
    train_data = lgb.Dataset(X_train, label=y_train + 1, weight=sample_weights)
    val_data = lgb.Dataset(X_val, label=y_val + 1, reference=train_data)
    
    model = lgb.train(
        params,
        train_data,
        valid_sets=[val_data],
        callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False)]
    )
    
    return model


def backtest_long_short(test_data, predictions, transaction_cost=0.001):
    """롱/숏 백테스트"""
    
    results = []
    dates = sorted(test_data['date'].unique())
    
    prev_long_positions = set()
    prev_short_positions = set()
    
    for date in dates:
        day_data = test_data[test_data['date'] == date].copy()
        
        if len(day_data) < CONFIG['top_n_long'] + CONFIG['top_n_short']:
            continue
        
        # VIX 레짐 체크
        vix = day_data['vix'].iloc[0] if 'vix' in day_data.columns else 15
        if vix > CONFIG['vix_threshold']:
            # 고변동성 레짐: 포지션 축소
            n_long = CONFIG['top_n_long'] // 2
            n_short = CONFIG['top_n_short'] // 2
        else:
            n_long = CONFIG['top_n_long']
            n_short = CONFIG['top_n_short']
        
        # 예측 확률 기반 정렬
        day_data['pred_score'] = predictions.loc[day_data.index, 2] - predictions.loc[day_data.index, 0]  # P(up) - P(down)
        
        # 롱/숏 포지션 선택
        sorted_data = day_data.sort_values('pred_score', ascending=False)
        long_symbols = set(sorted_data.head(n_long)['symbol'].tolist())
        short_symbols = set(sorted_data.tail(n_short)['symbol'].tolist())
        
        # 거래 비용 계산
        new_longs = long_symbols - prev_long_positions
        closed_longs = prev_long_positions - long_symbols
        new_shorts = short_symbols - prev_short_positions
        closed_shorts = prev_short_positions - short_symbols
        
        turnover = (len(new_longs) + len(closed_longs) + len(new_shorts) + len(closed_shorts)) / \
                   (2 * (n_long + n_short))
        
        # 수익률 계산
        long_returns = day_data[day_data['symbol'].isin(long_symbols)]['returns'].mean()
        short_returns = day_data[day_data['symbol'].isin(short_symbols)]['returns'].mean()
        
        # 롱/숏 포트폴리오 수익률 (시장 중립)
        portfolio_return = (long_returns - short_returns) / 2 - turnover * transaction_cost
        
        results.append({
            'date': date,
            'return': portfolio_return if not pd.isna(portfolio_return) else 0,
            'long_return': long_returns if not pd.isna(long_returns) else 0,
            'short_return': short_returns if not pd.isna(short_returns) else 0,
            'turnover': turnover,
            'vix': vix,
            'n_long': len(long_symbols),
            'n_short': len(short_symbols)
        })
        
        prev_long_positions = long_symbols
        prev_short_positions = short_symbols
    
    return pd.DataFrame(results)


def calculate_metrics(returns):
    """성과 지표 계산"""
    if len(returns) == 0:
        return {'sharpe': 0, 'annual_return': 0, 'max_drawdown': 0, 'win_rate': 0}
    
    returns = np.array(returns)
    
    # Sharpe Ratio
    mean_return = np.mean(returns)
    std_return = np.std(returns)
    sharpe = np.sqrt(252) * mean_return / (std_return + 1e-8)
    
    # Annual Return
    annual_return = np.mean(returns) * 252
    
    # Max Drawdown
    cumulative = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = np.min(drawdown)
    
    # Win Rate
    win_rate = np.mean(returns > 0)
    
    return {
        'sharpe': sharpe,
        'annual_return': annual_return,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate
    }


def main():
    """메인 파이프라인"""
    logger.info("=" * 80)
    logger.info("ARES v2.1 Production-Level Backtest Pipeline V3")
    logger.info(f"Start: {datetime.now()}")
    logger.info("=" * 80)
    
    # 1. 데이터 로드
    logger.info("\n[1/7] Data Loading")
    price_df, fund_df, vix_df, macro_df = load_data()
    
    # 2. 피처 엔지니어링
    logger.info("\n[2/7] Feature Engineering")
    features_df = compute_features(price_df, fund_df, vix_df, macro_df)
    
    # 3. 피처 IC 검증
    logger.info("\n[3/7] Feature IC Validation")
    feature_cols = ['mom_1m', 'mom_3m', 'mom_6m', 'mom_12m', 'mom_12_1',
                    'volatility_20d', 'volatility_60d', 'vol_ratio',
                    'price_sma20_ratio', 'price_sma50_ratio', 'price_sma200_ratio',
                    'sma_20_50_ratio', 'rsi_14', 'bb_position', 'macd_hist',
                    'volume_ratio', 'amihud_20d',
                    'roe', 'roic', 'gross_margin', 'net_margin',
                    'bm', 'ey', 'fcfy', 'value_z', 'quality_z']
    
    selected_features, ic_results = compute_ic(features_df, feature_cols)
    logger.info(f"Selected {len(selected_features)} features with |IC| > 0.02")
    
    # 4. Triple Barrier 라벨링
    logger.info("\n[4/7] Triple Barrier Labeling")
    labeled_df = generate_triple_barrier_labels(features_df)
    
    # 5. Walk-Forward 분할
    logger.info("\n[5/7] Walk-Forward Split")
    splits = create_walk_forward_splits(labeled_df, n_folds=CONFIG['n_folds'])
    
    # 6. 모델 학습 및 백테스트
    logger.info("\n[6/7] Model Training & Backtesting")
    
    all_results = []
    fold_metrics = []
    
    for fold_idx, split in enumerate(splits):
        logger.info(f"\n  Fold {fold_idx}:")
        logger.info(f"    Train: {split['train'][0].strftime('%Y-%m-%d')} to {split['train'][1].strftime('%Y-%m-%d')}")
        logger.info(f"    Val: {split['val'][0].strftime('%Y-%m-%d')} to {split['val'][1].strftime('%Y-%m-%d')}")
        logger.info(f"    Test: {split['test'][0].strftime('%Y-%m-%d')} to {split['test'][1].strftime('%Y-%m-%d')}")
        
        # 데이터 분할
        train_data = labeled_df[(labeled_df['date'] >= split['train'][0]) & 
                                 (labeled_df['date'] <= split['train'][1])]
        val_data = labeled_df[(labeled_df['date'] >= split['val'][0]) & 
                               (labeled_df['date'] <= split['val'][1])]
        test_data = labeled_df[(labeled_df['date'] >= split['test'][0]) & 
                                (labeled_df['date'] <= split['test'][1])]
        
        if len(train_data) < 1000 or len(test_data) < 100:
            logger.info(f"    Skipping fold {fold_idx} due to insufficient data")
            continue
        
        # 피처 선택
        available_features = [f for f in selected_features if f in train_data.columns]
        
        X_train = train_data[available_features].fillna(0)
        y_train = train_data['label'].values
        X_val = val_data[available_features].fillna(0)
        y_val = val_data['label'].values
        X_test = test_data[available_features].fillna(0)
        
        logger.info(f"    Training data: {len(X_train):,} rows, {len(available_features)} features")
        
        # 앙상블 학습
        predictions = np.zeros((len(X_test), 3))
        
        for bag_idx in range(CONFIG['n_bags']):
            logger.info(f"    Training bag {bag_idx + 1}/{CONFIG['n_bags']}")
            model = train_model(X_train, y_train, X_val, y_val, bag_idx)
            predictions += model.predict(X_test) / CONFIG['n_bags']
        
        # 예측 결과를 DataFrame으로 변환
        pred_df = pd.DataFrame(predictions, index=test_data.index, columns=[0, 1, 2])
        
        # 백테스트
        backtest_results = backtest_long_short(test_data, pred_df, CONFIG['transaction_cost'])
        
        if len(backtest_results) > 0:
            metrics = calculate_metrics(backtest_results['return'].values)
            logger.info(f"    Sharpe: {metrics['sharpe']:.2f}, Return: {metrics['annual_return']*100:.1f}%, MDD: {metrics['max_drawdown']*100:.1f}%")
            
            fold_metrics.append({
                'fold': fold_idx,
                'sharpe': metrics['sharpe'],
                'annual_return': metrics['annual_return'],
                'max_drawdown': metrics['max_drawdown'],
                'win_rate': metrics['win_rate']
            })
            
            backtest_results['fold'] = fold_idx
            all_results.append(backtest_results)
    
    # 7. 결과 집계
    logger.info("\n[7/7] Results Aggregation")
    
    if len(fold_metrics) > 0:
        avg_sharpe = np.mean([m['sharpe'] for m in fold_metrics])
        avg_return = np.mean([m['annual_return'] for m in fold_metrics])
        avg_mdd = np.mean([m['max_drawdown'] for m in fold_metrics])
        avg_win_rate = np.mean([m['win_rate'] for m in fold_metrics])
        
        logger.info(f"\n  Average Metrics ({len(fold_metrics)} folds):")
        logger.info(f"    Sharpe Ratio: {avg_sharpe:.2f}")
        logger.info(f"    Annual Return: {avg_return*100:.1f}%")
        logger.info(f"    Max Drawdown: {avg_mdd*100:.1f}%")
        logger.info(f"    Win Rate: {avg_win_rate*100:.1f}%")
        
        # 결과 저장
        results_dict = {
            'config': CONFIG,
            'fold_metrics': fold_metrics,
            'average_metrics': {
                'sharpe': avg_sharpe,
                'annual_return': avg_return,
                'max_drawdown': avg_mdd,
                'win_rate': avg_win_rate
            },
            'selected_features': selected_features,
            'ic_results': {k: float(v) for k, v in ic_results.items()}
        }
        
        with open(f'{OUTPUT_DIR}/pipeline_v3_results.json', 'w') as f:
            json.dump(results_dict, f, indent=2, default=str)
        
        logger.info(f"\n  Results saved to {OUTPUT_DIR}/pipeline_v3_results.json")
        
        # 전체 백테스트 결과 저장
        if len(all_results) > 0:
            all_results_df = pd.concat(all_results, ignore_index=True)
            all_results_df.to_csv(f'{OUTPUT_DIR}/pipeline_v3_backtest.csv', index=False)
            logger.info(f"  Backtest results saved to {OUTPUT_DIR}/pipeline_v3_backtest.csv")
        
        # 성과 평가
        if avg_sharpe >= 3.0:
            logger.info(f"\n🎉 Target achieved! Sharpe Ratio: {avg_sharpe:.2f}")
        elif avg_sharpe >= 1.5:
            logger.info(f"\n✅ Good performance. Sharpe Ratio: {avg_sharpe:.2f}")
        elif avg_sharpe >= 0.5:
            logger.info(f"\n⚠️ Moderate performance. Sharpe Ratio: {avg_sharpe:.2f}")
        else:
            logger.info(f"\n❌ Needs improvement. Sharpe Ratio: {avg_sharpe:.2f}")
    else:
        logger.info("\n  No valid folds completed")
    
    logger.info(f"\nEnd: {datetime.now()}")


if __name__ == '__main__':
    main()
