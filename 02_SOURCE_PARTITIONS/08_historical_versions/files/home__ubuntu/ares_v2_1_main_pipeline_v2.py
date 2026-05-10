#!/usr/bin/env python3
"""
ARES v2.1 Production-Level Main Pipeline V2
============================================
Claude 자문 기반 개선 버전:
1. 학습 데이터 확대: 798행 → 10,000행+
2. 라벨링 조건 완화: 26개 → 100개+ 심볼
3. 학습 속도 개선: 12분 → 2분
4. 피처 선택: 59개 → 상위 20개

목표:
- Sharpe Ratio 3.0+ (강세장/중립장/하락장 모두 강건)
- Look-ahead Bias 완전 제거
- 과적합 방지 (Purging/Embargo, Walk-Forward, DSR 검증)
- 실제 거래 비용 반영 (10 bps)
"""

import os
import sys
import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
import warnings
import logging
import json
from pathlib import Path
import time
from scipy import stats

warnings.filterwarnings('ignore')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/home/ubuntu/ares_v2_1_backtest/pipeline_v2.log')
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================
@dataclass
class PipelineConfigV2:
    """개선된 파이프라인 설정"""
    # 경로
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    output_dir: str = "/home/ubuntu/ares_v2_1_backtest"
    
    # 기간 설정 (확대)
    start_date: str = "2010-01-01"  # 2015 → 2010
    end_date: str = "2024-12-01"
    
    # 종목 설정 (완화)
    min_dollar_volume: float = 1e6  # 1e7 → 1e6
    min_price: float = 1.0  # 5.0 → 1.0
    max_symbols: int = 300
    
    # 리밸런싱 (주간)
    rebalance_freq: int = 5  # 21 → 5 (주간)
    
    # Walk-Forward 설정
    train_years: int = 3
    val_years: int = 1
    test_years: int = 1
    step_months: int = 3
    
    # 백테스트 설정
    top_k: int = 30
    cost_bps: float = 10.0
    vol_target: float = 0.15
    
    # Triple Barrier 설정 (완화)
    barrier_upper_mult: float = 2.0
    barrier_lower_mult: float = 2.0
    barrier_max_days: int = 10
    min_data_points: int = 20  # 50 → 20
    
    # 모델 설정
    n_bags: int = 3  # 5 → 3 (속도)
    n_estimators: int = 100  # 500 → 100 (속도)
    top_features: int = 25  # 피처 선택

# =============================================================================
# Data Loader
# =============================================================================
class DataLoaderV2:
    """개선된 데이터 로더"""
    
    def __init__(self, config: PipelineConfigV2):
        self.config = config
        self.conn = None
    
    def connect(self):
        if self.conn is None:
            self.conn = sqlite3.connect(self.config.db_path)
        return self.conn
    
    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def get_symbols(self) -> List[str]:
        """유동성 기준 종목 선정 (완화된 조건)"""
        conn = self.connect()
        
        query = f"""
        SELECT symbol, AVG(COALESCE(dollar_volume, close * volume)) as avg_dv,
               AVG(close) as avg_price
        FROM daily_ohlcv
        WHERE date BETWEEN ? AND ?
          AND close > ?
        GROUP BY symbol
        HAVING AVG(COALESCE(dollar_volume, close * volume)) >= ?
        ORDER BY avg_dv DESC
        LIMIT ?
        """
        
        df = pd.read_sql_query(
            query, conn,
            params=[self.config.start_date, self.config.end_date,
                    self.config.min_price, self.config.min_dollar_volume,
                    self.config.max_symbols]
        )
        
        symbols = df['symbol'].tolist()
        logger.info(f"Selected {len(symbols)} symbols (min_price={self.config.min_price}, min_dv={self.config.min_dollar_volume/1e6:.1f}M)")
        return symbols
    
    def load_prices(self, symbols: List[str]) -> pd.DataFrame:
        """가격 데이터 로드"""
        conn = self.connect()
        ph = ",".join(["?"] * len(symbols))
        
        query = f"""
        SELECT date, symbol, open, high, low, close, volume,
               COALESCE(adjusted_close, close) as adj_close,
               COALESCE(dollar_volume, close * volume) as dollar_volume
        FROM daily_ohlcv
        WHERE symbol IN ({ph})
          AND date BETWEEN ? AND ?
        ORDER BY date, symbol
        """
        
        df = pd.read_sql_query(
            query, conn,
            params=[*symbols, self.config.start_date, self.config.end_date]
        )
        df['date'] = pd.to_datetime(df['date'])
        
        logger.info(f"Loaded {len(df):,} price records")
        return df
    
    def load_fundamentals(self, symbols: List[str]) -> pd.DataFrame:
        """펀더멘털 데이터 로드"""
        conn = self.connect()
        
        query = """
        SELECT date, ticker as symbol, bm, ey, fcfy, gp_a, leverage,
               value_z, quality_z, gross_margin, net_margin,
               revenue_growth, earnings_growth
        FROM fundamentals_pit_daily
        WHERE date BETWEEN ? AND ?
        ORDER BY date, ticker
        """
        
        df = pd.read_sql_query(
            query, conn,
            params=[self.config.start_date, self.config.end_date]
        )
        df['date'] = pd.to_datetime(df['date'])
        
        logger.info(f"Loaded {len(df):,} fundamental records")
        return df
    
    def load_vix(self) -> pd.Series:
        """VIX 데이터 로드"""
        conn = self.connect()
        
        df = pd.read_sql_query(
            "SELECT date, close as vix FROM vix ORDER BY date", conn
        )
        df['date'] = pd.to_datetime(df['date'], format='mixed')
        
        return df.set_index('date')['vix']
    
    def load_macro(self) -> pd.DataFrame:
        """매크로 지표 로드"""
        conn = self.connect()
        
        df = pd.read_sql_query("""
            SELECT date, indicator_name, value
            FROM macro_indicators
            WHERE indicator_name IN ('10Y-2Y Spread', 'High Yield Spread', 'VIX Close')
            ORDER BY date
        """, conn)
        df['date'] = pd.to_datetime(df['date'])
        
        pivot = df.pivot(index='date', columns='indicator_name', values='value')
        return pivot

# =============================================================================
# Feature Engineering (Simplified & Fast)
# =============================================================================
class FastFeatureEngine:
    """빠른 피처 엔지니어링"""
    
    def __init__(self, config: PipelineConfigV2):
        self.config = config
    
    def compute_features(self, price_df: pd.DataFrame, fund_df: pd.DataFrame) -> pd.DataFrame:
        """피처 계산 (벡터화)"""
        logger.info("Computing features...")
        
        # 가격 피벗
        price_df = price_df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        close_pivot = price_df.pivot(index='date', columns='symbol', values='adj_close')
        volume_pivot = price_df.pivot(index='date', columns='symbol', values='volume')
        
        features_list = []
        
        # 모멘텀 피처
        ret_1m = close_pivot.pct_change(21)
        ret_3m = close_pivot.pct_change(63)
        ret_6m = close_pivot.pct_change(126)
        ret_12m = close_pivot.pct_change(252)
        momentum_12_1 = ret_12m - ret_1m
        
        # 변동성 피처
        daily_ret = close_pivot.pct_change()
        vol_20d = daily_ret.rolling(20).std() * np.sqrt(252)
        vol_60d = daily_ret.rolling(60).std() * np.sqrt(252)
        
        # 기술적 지표
        sma_20 = close_pivot.rolling(20).mean()
        sma_50 = close_pivot.rolling(50).mean()
        sma_200 = close_pivot.rolling(200).mean()
        
        price_to_sma20 = close_pivot / sma_20 - 1
        price_to_sma50 = close_pivot / sma_50 - 1
        price_to_sma200 = close_pivot / sma_200 - 1
        
        # RSI
        delta = close_pivot.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        
        # 거래량 피처
        vol_ratio = volume_pivot / volume_pivot.rolling(20).mean()
        
        # 날짜별 피처 DataFrame 생성
        for date in close_pivot.index[252:]:  # 1년 워밍업
            date_features = pd.DataFrame(index=close_pivot.columns)
            
            date_features['ret_1m'] = ret_1m.loc[date]
            date_features['ret_3m'] = ret_3m.loc[date]
            date_features['ret_6m'] = ret_6m.loc[date]
            date_features['ret_12m'] = ret_12m.loc[date]
            date_features['momentum_12_1'] = momentum_12_1.loc[date]
            date_features['vol_20d'] = vol_20d.loc[date]
            date_features['vol_60d'] = vol_60d.loc[date]
            date_features['price_to_sma20'] = price_to_sma20.loc[date]
            date_features['price_to_sma50'] = price_to_sma50.loc[date]
            date_features['price_to_sma200'] = price_to_sma200.loc[date]
            date_features['rsi'] = rsi.loc[date]
            date_features['vol_ratio'] = vol_ratio.loc[date]
            
            date_features['date'] = date
            date_features['symbol'] = date_features.index
            
            features_list.append(date_features.reset_index(drop=True))
        
        all_features = pd.concat(features_list, ignore_index=True)
        
        # 펀더멘털 병합
        if not fund_df.empty:
            fund_df = fund_df.drop_duplicates(subset=['date', 'symbol'], keep='last')
            all_features = all_features.merge(
                fund_df, on=['date', 'symbol'], how='left'
            )
        
        logger.info(f"Computed {len(all_features):,} feature rows, {len(all_features.columns)} columns")
        return all_features

# =============================================================================
# Triple Barrier Labeling (Simplified)
# =============================================================================
class FastTripleBarrier:
    """빠른 Triple Barrier 라벨링"""
    
    def __init__(self, config: PipelineConfigV2):
        self.config = config
    
    def label_all(self, price_df: pd.DataFrame) -> pd.DataFrame:
        """전체 라벨링"""
        logger.info("Generating Triple Barrier labels...")
        
        price_df = price_df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        close_pivot = price_df.pivot(index='date', columns='symbol', values='adj_close')
        
        labels_list = []
        
        for symbol in close_pivot.columns:
            prices = close_pivot[symbol].dropna()
            
            if len(prices) < self.config.min_data_points:
                continue
            
            returns = prices.pct_change()
            volatility = returns.rolling(20).std()
            
            for i in range(len(prices) - self.config.barrier_max_days):
                t0 = prices.index[i]
                t0_price = prices.iloc[i]
                t0_vol = volatility.iloc[i]
                
                if pd.isna(t0_vol) or t0_vol == 0:
                    continue
                
                upper = t0_price * (1 + self.config.barrier_upper_mult * t0_vol)
                lower = t0_price * (1 - self.config.barrier_lower_mult * t0_vol)
                
                # 배리어 도달 확인
                label = 0  # 기본: 중립
                
                for j in range(1, self.config.barrier_max_days + 1):
                    if i + j >= len(prices):
                        break
                    
                    future_price = prices.iloc[i + j]
                    
                    if future_price >= upper:
                        label = 1  # 상승
                        break
                    elif future_price <= lower:
                        label = -1  # 하락
                        break
                
                labels_list.append({
                    'date': t0,
                    'symbol': symbol,
                    'label': label,
                    'volatility': t0_vol
                })
        
        labels_df = pd.DataFrame(labels_list)
        
        # 라벨 분포 확인
        if not labels_df.empty:
            label_dist = labels_df['label'].value_counts()
            logger.info(f"Label distribution: {dict(label_dist)}")
            logger.info(f"Generated {len(labels_df):,} labels for {labels_df['symbol'].nunique()} symbols")
        
        return labels_df

# =============================================================================
# Walk-Forward Splitter
# =============================================================================
class WalkForwardSplitterV2:
    """Walk-Forward 분할"""
    
    def __init__(self, config: PipelineConfigV2):
        self.config = config
        self.train_days = config.train_years * 252
        self.val_days = config.val_years * 252
        self.test_days = config.test_years * 252
        self.step_days = config.step_months * 21
        self.purge_days = 5
        self.embargo_days = 5
    
    def split(self, dates: pd.DatetimeIndex) -> List[Dict]:
        """분할 생성"""
        splits = []
        fold_id = 0
        
        start_idx = 0
        
        while True:
            train_end = start_idx + self.train_days
            val_end = train_end + self.purge_days + self.val_days
            test_end = val_end + self.embargo_days + self.test_days
            
            if test_end > len(dates):
                break
            
            splits.append({
                'fold_id': fold_id,
                'train': dates[start_idx:train_end],
                'val': dates[train_end + self.purge_days:val_end],
                'test': dates[val_end + self.embargo_days:test_end]
            })
            
            fold_id += 1
            start_idx += self.step_days
        
        logger.info(f"Generated {len(splits)} walk-forward folds")
        return splits

# =============================================================================
# LightGBM Model (Simplified)
# =============================================================================
class FastLightGBM:
    """빠른 LightGBM 모델"""
    
    def __init__(self, config: PipelineConfigV2):
        self.config = config
        self.models = []
        self.feature_importance = None
    
    def fit(self, X: pd.DataFrame, y: pd.Series):
        """학습"""
        import lightgbm as lgb
        
        # 숫자형만 선택
        numeric_cols = X.select_dtypes(include=[np.number]).columns.tolist()
        X_numeric = X[numeric_cols].fillna(0)
        
        # 피처 선택 (상관관계 기반)
        if len(numeric_cols) > self.config.top_features:
            correlations = X_numeric.corrwith(y).abs()
            top_cols = correlations.nlargest(self.config.top_features).index.tolist()
            X_numeric = X_numeric[top_cols]
        
        self.feature_cols = X_numeric.columns.tolist()
        
        # 라벨 변환 (0, 1, 2)
        y_encoded = y.map({-1: 0, 0: 1, 1: 2})
        
        params = {
            'objective': 'multiclass',
            'num_class': 3,
            'boosting_type': 'gbdt',
            'n_estimators': self.config.n_estimators,
            'learning_rate': 0.05,
            'max_depth': 6,
            'num_leaves': 31,
            'min_child_samples': 20,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'reg_alpha': 0.1,
            'reg_lambda': 0.1,
            'random_state': 42,
            'verbose': -1,
            'n_jobs': -1
        }
        
        # Bagging
        self.models = []
        for i in range(self.config.n_bags):
            logger.info(f"Training bag {i+1}/{self.config.n_bags}")
            
            # 부트스트랩 샘플링
            idx = np.random.choice(len(X_numeric), size=len(X_numeric), replace=True)
            X_bag = X_numeric.iloc[idx]
            y_bag = y_encoded.iloc[idx]
            
            model = lgb.LGBMClassifier(**params)
            model.fit(X_bag, y_bag)
            self.models.append(model)
        
        # 피처 중요도
        self.feature_importance = pd.Series(
            np.mean([m.feature_importances_ for m in self.models], axis=0),
            index=self.feature_cols
        ).sort_values(ascending=False)
    
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """확률 예측"""
        X_numeric = X[self.feature_cols].fillna(0)
        
        probas = []
        for model in self.models:
            probas.append(model.predict_proba(X_numeric))
        
        return np.mean(probas, axis=0)
    
    def get_alpha_score(self, X: pd.DataFrame) -> pd.Series:
        """알파 스코어 (Long 확률 - Short 확률)"""
        probas = self.predict_proba(X)
        alpha = probas[:, 2] - probas[:, 0]  # P(up) - P(down)
        return pd.Series(alpha, index=X.index)

# =============================================================================
# Backtester
# =============================================================================
class Backtester:
    """백테스터"""
    
    def __init__(self, config: PipelineConfigV2):
        self.config = config
    
    def run(self, alpha_scores: pd.DataFrame, price_df: pd.DataFrame) -> Dict:
        """백테스트 실행"""
        price_df = price_df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        close_pivot = price_df.pivot(index='date', columns='symbol', values='adj_close')
        
        dates = sorted(alpha_scores['date'].unique())
        
        portfolio_returns = []
        
        for i, date in enumerate(dates[:-1]):
            next_date = dates[i + 1]
            
            # 해당 날짜 알파 스코어
            date_scores = alpha_scores[alpha_scores['date'] == date]
            date_scores = date_scores.set_index('symbol')['alpha']
            
            # Top-K 선택
            top_k = date_scores.nlargest(self.config.top_k).index.tolist()
            
            # 수익률 계산
            period_returns = []
            for symbol in top_k:
                if symbol in close_pivot.columns:
                    if date in close_pivot.index and next_date in close_pivot.index:
                        p0 = close_pivot.loc[date, symbol]
                        p1 = close_pivot.loc[next_date, symbol]
                        if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                            ret = (p1 / p0) - 1
                            period_returns.append(ret)
            
            if period_returns:
                portfolio_ret = np.mean(period_returns) - (self.config.cost_bps / 10000)
                portfolio_returns.append({
                    'date': date,
                    'return': portfolio_ret
                })
        
        returns_df = pd.DataFrame(portfolio_returns)
        
        # 성과 지표 계산
        if not returns_df.empty:
            returns = returns_df['return']
            
            # 연환산
            n_periods = len(returns)
            periods_per_year = 252 / self.config.rebalance_freq
            
            total_return = (1 + returns).prod() - 1
            annual_return = (1 + total_return) ** (periods_per_year / n_periods) - 1
            
            volatility = returns.std() * np.sqrt(periods_per_year)
            sharpe = annual_return / volatility if volatility > 0 else 0
            
            # 최대 낙폭
            cumulative = (1 + returns).cumprod()
            rolling_max = cumulative.cummax()
            drawdown = (cumulative - rolling_max) / rolling_max
            max_drawdown = drawdown.min()
            
            # 승률
            win_rate = (returns > 0).mean()
            
            return {
                'total_return': total_return,
                'annual_return': annual_return,
                'volatility': volatility,
                'sharpe_ratio': sharpe,
                'max_drawdown': max_drawdown,
                'win_rate': win_rate,
                'n_trades': n_periods,
                'returns_df': returns_df
            }
        
        return {}

# =============================================================================
# Main Pipeline
# =============================================================================
class AresV21PipelineV2:
    """ARES v2.1 개선된 파이프라인"""
    
    def __init__(self, config: PipelineConfigV2):
        self.config = config
        self.loader = DataLoaderV2(config)
        self.feature_engine = FastFeatureEngine(config)
        self.labeler = FastTripleBarrier(config)
        self.splitter = WalkForwardSplitterV2(config)
        self.backtester = Backtester(config)
        
        # 디렉토리 생성
        Path(config.output_dir).mkdir(parents=True, exist_ok=True)
    
    def run(self):
        """파이프라인 실행"""
        start_time = time.time()
        logger.info("=" * 80)
        logger.info("ARES v2.1 Production-Level Backtest Pipeline V2")
        logger.info(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)
        
        try:
            # 1. 데이터 로드
            logger.info("\n[1/6] Data Loading")
            symbols = self.loader.get_symbols()
            price_df = self.loader.load_prices(symbols)
            fund_df = self.loader.load_fundamentals(symbols)
            
            # 2. 피처 엔지니어링
            logger.info("\n[2/6] Feature Engineering")
            features_df = self.feature_engine.compute_features(price_df, fund_df)
            
            # 3. 라벨링
            logger.info("\n[3/6] Triple Barrier Labeling")
            labels_df = self.labeler.label_all(price_df)
            
            # 피처-라벨 병합
            merged = features_df.merge(
                labels_df[['date', 'symbol', 'label']],
                on=['date', 'symbol'],
                how='inner'
            )
            logger.info(f"Merged dataset: {len(merged):,} rows")
            
            # 4. Walk-Forward 분할
            logger.info("\n[4/6] Walk-Forward Split")
            trading_days = pd.DatetimeIndex(sorted(price_df['date'].unique()))
            splits = self.splitter.split(trading_days)
            
            # 5. 모델 학습 및 백테스트
            logger.info("\n[5/6] Model Training & Backtesting")
            
            all_results = []
            
            for fold in splits[:5]:  # 5개 폴드
                fold_id = fold['fold_id']
                train_dates = fold['train']
                test_dates = fold['test']
                
                logger.info(f"\n  Fold {fold_id}:")
                logger.info(f"    Train: {train_dates[0].date()} to {train_dates[-1].date()}")
                logger.info(f"    Test: {test_dates[0].date()} to {test_dates[-1].date()}")
                
                # 훈련 데이터
                train_mask = merged['date'].isin(train_dates)
                train_data = merged[train_mask]
                
                if len(train_data) < 500:
                    logger.warning(f"    Insufficient training data: {len(train_data)}")
                    continue
                
                logger.info(f"    Training data: {len(train_data):,} rows")
                
                # 피처/라벨 분리
                feature_cols = [c for c in train_data.columns 
                               if c not in ['date', 'symbol', 'label']]
                X_train = train_data[feature_cols]
                y_train = train_data['label']
                
                # 모델 학습
                model = FastLightGBM(self.config)
                model.fit(X_train, y_train)
                
                # 테스트 기간 예측
                test_mask = merged['date'].isin(test_dates)
                test_data = merged[test_mask]
                
                if test_data.empty:
                    continue
                
                X_test = test_data[feature_cols]
                alpha_scores = model.get_alpha_score(X_test)
                
                test_data = test_data.copy()
                test_data['alpha'] = alpha_scores.values
                
                # 백테스트
                results = self.backtester.run(
                    test_data[['date', 'symbol', 'alpha']],
                    price_df
                )
                
                if results:
                    logger.info(f"    Sharpe: {results['sharpe_ratio']:.2f}, "
                               f"Return: {results['annual_return']*100:.1f}%, "
                               f"MDD: {results['max_drawdown']*100:.1f}%")
                    
                    all_results.append({
                        'fold_id': fold_id,
                        **{k: v for k, v in results.items() if k != 'returns_df'}
                    })
            
            # 6. 결과 집계
            logger.info("\n[6/6] Results Aggregation")
            
            if all_results:
                avg_sharpe = np.mean([r['sharpe_ratio'] for r in all_results])
                avg_return = np.mean([r['annual_return'] for r in all_results])
                avg_mdd = np.mean([r['max_drawdown'] for r in all_results])
                avg_win_rate = np.mean([r['win_rate'] for r in all_results])
                
                logger.info(f"\n  Average Metrics ({len(all_results)} folds):")
                logger.info(f"    Sharpe Ratio: {avg_sharpe:.2f}")
                logger.info(f"    Annual Return: {avg_return*100:.1f}%")
                logger.info(f"    Max Drawdown: {avg_mdd*100:.1f}%")
                logger.info(f"    Win Rate: {avg_win_rate*100:.1f}%")
                
                # 결과 저장
                results_path = os.path.join(self.config.output_dir, 'pipeline_v2_results.json')
                with open(results_path, 'w') as f:
                    json.dump({
                        'config': {
                            'start_date': self.config.start_date,
                            'end_date': self.config.end_date,
                            'top_k': self.config.top_k,
                            'cost_bps': self.config.cost_bps,
                            'rebalance_freq': self.config.rebalance_freq
                        },
                        'avg_metrics': {
                            'sharpe_ratio': avg_sharpe,
                            'annual_return': avg_return,
                            'max_drawdown': avg_mdd,
                            'win_rate': avg_win_rate
                        },
                        'fold_results': all_results,
                        'n_folds': len(all_results)
                    }, f, indent=2, default=str)
                
                logger.info(f"\n  Results saved to {results_path}")
                
                # 목표 달성 여부
                if avg_sharpe >= 3.0:
                    logger.info(f"\n🎉 TARGET ACHIEVED! Sharpe Ratio: {avg_sharpe:.2f} >= 3.0")
                elif avg_sharpe >= 1.5:
                    logger.info(f"\n✅ Good progress! Sharpe Ratio: {avg_sharpe:.2f}")
                else:
                    logger.info(f"\n⚠️ Needs improvement. Sharpe Ratio: {avg_sharpe:.2f}")
                
                return {
                    'avg_metrics': {
                        'sharpe_ratio': avg_sharpe,
                        'annual_return': avg_return,
                        'max_drawdown': avg_mdd,
                        'win_rate': avg_win_rate
                    },
                    'fold_results': all_results
                }
        
        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            import traceback
            traceback.print_exc()
            raise
        
        finally:
            self.loader.close()
        
        elapsed = time.time() - start_time
        logger.info("\n" + "=" * 80)
        logger.info(f"Pipeline Complete! Elapsed: {elapsed/60:.1f} minutes")
        logger.info("=" * 80)

# =============================================================================
# Main
# =============================================================================
def main():
    """메인 실행"""
    config = PipelineConfigV2()
    pipeline = AresV21PipelineV2(config)
    results = pipeline.run()
    return results

if __name__ == "__main__":
    main()
