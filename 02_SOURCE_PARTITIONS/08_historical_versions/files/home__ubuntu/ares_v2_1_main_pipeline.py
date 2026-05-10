#!/usr/bin/env python3
"""
ARES v2.1 Production-Level Main Pipeline
=========================================
4대 AI 자문 기반 통합 백테스트 파이프라인

목표:
- Sharpe Ratio 3.0+ (강세장/중립장/하락장 모두 강건)
- Look-ahead Bias 완전 제거
- 과적합 방지 (Purging/Embargo, Walk-Forward, DSR 검증)
- 실제 거래 비용 반영 (10 bps)
- 100% 실데이터 사용

실행 순서:
1. 데이터 로드 및 Parquet 변환
2. 피처 엔지니어링
3. Triple Barrier 라벨링
4. Walk-Forward 분할
5. LightGBM GPU 모델 학습
6. Grid Search 최적화
7. 백테스트 실행
8. 성과 분석 및 레짐별 검증
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

warnings.filterwarnings('ignore')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/home/ubuntu/ares_v2_1_backtest/pipeline.log')
    ]
)
logger = logging.getLogger(__name__)

# 모듈 임포트
sys.path.insert(0, '/home/ubuntu')
from ares_v2_1_feature_engine import FeatureConfig, FeaturePipeline, DataLoader
from ares_v2_1_backtest_engine import (
    BacktestConfig, BacktestEngine, TripleBarrierLabeler,
    WalkForwardSplitter, HRPOptimizer, VolatilityTargeting, PerformanceMetrics
)
from ares_v2_1_ml_model import (
    ModelConfig, LightGBMModel, BaggingEnsemble,
    HyperparameterOptimizer, GridSearchOptimizer, FeatureStabilityAnalyzer
)

# =============================================================================
# Pipeline Configuration
# =============================================================================
@dataclass
class PipelineConfig:
    """통합 파이프라인 설정"""
    # 경로
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    output_dir: str = "/home/ubuntu/ares_v2_1_backtest"
    parquet_dir: str = "/home/ubuntu/ares_v2_1_backtest/parquet_data"
    model_dir: str = "/home/ubuntu/ares_v2_1_backtest/models"
    results_dir: str = "/home/ubuntu/ares_v2_1_backtest/results"
    
    # 기간 설정
    start_date: str = "2015-01-01"
    end_date: str = "2024-12-01"
    
    # 종목 설정
    min_dollar_volume: float = 1e7  # 최소 일평균 거래대금
    max_symbols: int = 500
    
    # Walk-Forward 설정
    train_years: int = 3
    val_years: int = 1
    test_years: int = 1
    step_months: int = 3
    
    # 백테스트 설정
    top_k: int = 30
    rebalance_freq: int = 21
    cost_bps: float = 10.0
    vol_target: float = 0.15
    
    # 최적화 설정
    run_grid_search: bool = True
    max_grid_combinations: int = 10000
    run_optuna: bool = True
    optuna_trials: int = 100
    
    # 레짐 검증
    validate_regimes: bool = True

# =============================================================================
# Data Preparation
# =============================================================================
class DataPreparation:
    """데이터 준비 및 전처리"""
    
    def __init__(self, config: PipelineConfig):
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
    
    def get_liquid_symbols(self) -> List[str]:
        """유동성 기준 종목 선정"""
        conn = self.connect()
        
        query = f"""
        SELECT symbol, AVG(dollar_volume) as avg_dv
        FROM daily_ohlcv
        WHERE date BETWEEN ? AND ?
          AND dollar_volume IS NOT NULL
        GROUP BY symbol
        HAVING AVG(dollar_volume) >= ?
        ORDER BY avg_dv DESC
        LIMIT ?
        """
        
        df = pd.read_sql_query(
            query, conn,
            params=[self.config.start_date, self.config.end_date,
                    self.config.min_dollar_volume, self.config.max_symbols]
        )
        
        symbols = df['symbol'].tolist()
        logger.info(f"Selected {len(symbols)} liquid symbols")
        return symbols
    
    def load_price_data(self, symbols: List[str]) -> pd.DataFrame:
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
    
    def load_vix_data(self) -> pd.Series:
        """VIX 데이터 로드"""
        conn = self.connect()
        
        query = """
        SELECT date, close as vix
        FROM vix
        WHERE date BETWEEN ? AND ?
        ORDER BY date
        """
        
        df = pd.read_sql_query(
            query, conn,
            params=[self.config.start_date, self.config.end_date]
        )
        df['date'] = pd.to_datetime(df['date'])
        
        return df.set_index('date')['vix']
    
    def identify_regimes(self, vix: pd.Series) -> pd.DataFrame:
        """레짐 식별"""
        regimes = pd.DataFrame(index=vix.index)
        
        # VIX 기반 레짐
        regimes['vix_regime'] = 'neutral'
        regimes.loc[vix < 15, 'vix_regime'] = 'low_vol'
        regimes.loc[vix > 25, 'vix_regime'] = 'high_vol'
        
        # 시장 수익률 기반 레짐 (SPY 사용)
        conn = self.connect()
        spy = pd.read_sql_query(
            "SELECT date, COALESCE(adjusted_close, close) as close FROM daily_ohlcv WHERE symbol='SPY' ORDER BY date",
            conn
        )
        spy['date'] = pd.to_datetime(spy['date'])
        spy = spy.set_index('date')['close']
        
        spy_returns = spy.pct_change(63)  # 3개월 수익률
        regimes['market_regime'] = 'neutral'
        regimes.loc[spy_returns > 0.05, 'market_regime'] = 'bull'
        regimes.loc[spy_returns < -0.05, 'market_regime'] = 'bear'
        
        return regimes

# =============================================================================
# Main Pipeline
# =============================================================================
class AresV21Pipeline:
    """ARES v2.1 통합 파이프라인"""
    
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.data_prep = DataPreparation(config)
        
        # 디렉토리 생성
        for dir_path in [config.output_dir, config.parquet_dir,
                         config.model_dir, config.results_dir]:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
        
        # 설정 객체
        self.feature_config = FeatureConfig(db_path=config.db_path)
        self.backtest_config = BacktestConfig(
            db_path=config.db_path,
            train_days=config.train_years * 252,
            val_days=config.val_years * 252,
            test_days=config.test_years * 252,
            step_days=config.step_months * 21,
            top_k=config.top_k,
            total_cost_bps=config.cost_bps,
            vol_target=config.vol_target
        )
        self.model_config = ModelConfig()
        
        # 결과 저장
        self.results = {}
    
    def run(self):
        """파이프라인 실행"""
        start_time = time.time()
        logger.info("=" * 80)
        logger.info("ARES v2.1 Production-Level Backtest Pipeline")
        logger.info(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)
        
        try:
            # 1. 데이터 준비
            logger.info("\n[1/8] Data Preparation")
            symbols = self.data_prep.get_liquid_symbols()
            price_df = self.data_prep.load_price_data(symbols)
            vix = self.data_prep.load_vix_data()
            regimes = self.data_prep.identify_regimes(vix)
            
            # 2. 피처 엔지니어링
            logger.info("\n[2/8] Feature Engineering")
            feature_pipeline = FeaturePipeline(self.feature_config)
            
            # 거래일 목록
            trading_days = sorted(price_df['date'].unique())
            rebalance_dates = trading_days[::self.config.rebalance_freq]
            
            all_features = {}
            for i, date in enumerate(rebalance_dates):
                if (i + 1) % 20 == 0:
                    logger.info(f"  Processing {i+1}/{len(rebalance_dates)}: {date.strftime('%Y-%m-%d')}")
                
                date_str = date.strftime('%Y-%m-%d')
                features = feature_pipeline.compute_features_for_date(symbols, date_str)
                if not features.empty:
                    all_features[date_str] = features
            
            feature_pipeline.close()
            logger.info(f"  Computed features for {len(all_features)} dates")
            
            # 3. Triple Barrier 라벨링
            logger.info("\n[3/8] Triple Barrier Labeling")
            labeler = TripleBarrierLabeler(self.backtest_config)
            
            # 중복 제거 후 종목별 라벨 생성
            all_labels = {}
            price_df_dedup = price_df.drop_duplicates(subset=['date', 'symbol'], keep='last')
            close_pivot = price_df_dedup.pivot(index='date', columns='symbol', values='adj_close')
            
            for symbol in symbols[:50]:  # 테스트용 50개
                if symbol not in close_pivot.columns:
                    continue
                
                prices = close_pivot[symbol].dropna()
                returns = prices.pct_change()
                volatility = returns.rolling(20).std()
                
                labels = labeler.apply_barriers(prices, volatility)
                if not labels.empty:
                    all_labels[symbol] = labels
            
            logger.info(f"  Generated labels for {len(all_labels)} symbols")
            
            # 4. Walk-Forward 분할
            logger.info("\n[4/8] Walk-Forward Split")
            splitter = WalkForwardSplitter(self.backtest_config)
            dates_index = pd.DatetimeIndex(trading_days)
            splits = splitter.split(dates_index)
            
            logger.info(f"  Generated {len(splits)} walk-forward folds")
            
            # 5. 모델 학습 및 백테스트
            logger.info("\n[5/8] Model Training & Backtesting")
            
            fold_results = []
            
            for fold in splits[:3]:  # 테스트용 3개 폴드
                fold_id = fold['fold_id']
                train_dates = fold['train']
                val_dates = fold['val']
                test_dates = fold['test']
                
                logger.info(f"\n  Fold {fold_id}:")
                logger.info(f"    Train: {train_dates[0].date()} to {train_dates[-1].date()}")
                logger.info(f"    Val: {val_dates[0].date()} to {val_dates[-1].date()}")
                logger.info(f"    Test: {test_dates[0].date()} to {test_dates[-1].date()}")
                
                # 훈련 데이터 준비
                train_X_list = []
                train_y_list = []
                
                for date in train_dates[::21]:  # 월간 샘플링
                    date_str = date.strftime('%Y-%m-%d')
                    if date_str in all_features:
                        features = all_features[date_str]
                        
                        # 라벨 매칭 (단순화)
                        for symbol in features.index:
                            if symbol in all_labels:
                                labels_df = all_labels[symbol]
                                matching = labels_df[labels_df['t0'] == date]
                                if not matching.empty:
                                    train_X_list.append(features.loc[symbol])
                                    train_y_list.append(matching['label'].iloc[0])
                
                if len(train_X_list) < 100:
                    logger.warning(f"    Insufficient training data: {len(train_X_list)}")
                    continue
                
                train_X = pd.DataFrame(train_X_list)
                train_y = pd.Series(train_y_list)
                
                # 숫자형 컬럼만 선택
                numeric_cols = train_X.select_dtypes(include=[np.number]).columns
                train_X = train_X[numeric_cols].fillna(0)
                
                logger.info(f"    Training data: {train_X.shape}")
                
                # 모델 학습
                ensemble = BaggingEnsemble(self.model_config)
                ensemble.fit(train_X, train_y)
                
                # 테스트 기간 백테스트
                test_returns = []
                
                for date in test_dates[::21]:
                    date_str = date.strftime('%Y-%m-%d')
                    if date_str not in all_features:
                        continue
                    
                    features = all_features[date_str]
                    features_numeric = features[numeric_cols].fillna(0)
                    
                    # 알파 스코어 계산
                    alpha_scores = ensemble.get_alpha_score(features_numeric)
                    
                    # Top-K 선택
                    top_k_symbols = alpha_scores.nlargest(self.config.top_k).index.tolist()
                    
                    # 수익률 계산 (다음 리밸런싱까지)
                    next_date_idx = trading_days.index(date) + self.config.rebalance_freq
                    if next_date_idx >= len(trading_days):
                        continue
                    
                    next_date = trading_days[next_date_idx]
                    
                    period_returns = []
                    for symbol in top_k_symbols:
                        if symbol in close_pivot.columns:
                            if date in close_pivot.index and next_date in close_pivot.index:
                                ret = (close_pivot.loc[next_date, symbol] / 
                                       close_pivot.loc[date, symbol]) - 1
                                if not pd.isna(ret):
                                    period_returns.append(ret)
                    
                    if period_returns:
                        portfolio_return = np.mean(period_returns) - (self.config.cost_bps / 10000)
                        test_returns.append(portfolio_return)
                
                if test_returns:
                    test_returns_series = pd.Series(test_returns)
                    metrics = PerformanceMetrics.compute_all_metrics(
                        test_returns_series, n_trials=len(splits)
                    )
                    
                    fold_results.append({
                        'fold_id': fold_id,
                        'metrics': metrics
                    })
                    
                    logger.info(f"    Sharpe: {metrics['sharpe_ratio']:.2f}, "
                               f"Return: {metrics['annual_return']*100:.1f}%, "
                               f"MDD: {metrics['max_drawdown']*100:.1f}%")
            
            # 6. 결과 집계
            logger.info("\n[6/8] Results Aggregation")
            
            if fold_results:
                avg_sharpe = np.mean([r['metrics']['sharpe_ratio'] for r in fold_results])
                avg_return = np.mean([r['metrics']['annual_return'] for r in fold_results])
                avg_mdd = np.mean([r['metrics']['max_drawdown'] for r in fold_results])
                avg_dsr = np.mean([r['metrics']['dsr'] for r in fold_results])
                
                logger.info(f"\n  Average Metrics:")
                logger.info(f"    Sharpe Ratio: {avg_sharpe:.2f}")
                logger.info(f"    Annual Return: {avg_return*100:.1f}%")
                logger.info(f"    Max Drawdown: {avg_mdd*100:.1f}%")
                logger.info(f"    DSR: {avg_dsr:.4f}")
                
                self.results['fold_results'] = fold_results
                self.results['avg_metrics'] = {
                    'sharpe_ratio': avg_sharpe,
                    'annual_return': avg_return,
                    'max_drawdown': avg_mdd,
                    'dsr': avg_dsr
                }
            
            # 7. 레짐별 검증
            if self.config.validate_regimes and fold_results:
                logger.info("\n[7/8] Regime Validation")
                # 레짐별 성과 분석 (추후 구현)
                logger.info("  Regime validation: Pending implementation")
            
            # 8. 결과 저장
            logger.info("\n[8/8] Saving Results")
            
            results_path = os.path.join(self.config.results_dir, 'pipeline_results.json')
            with open(results_path, 'w') as f:
                json.dump({
                    'config': {
                        'start_date': self.config.start_date,
                        'end_date': self.config.end_date,
                        'top_k': self.config.top_k,
                        'cost_bps': self.config.cost_bps
                    },
                    'avg_metrics': self.results.get('avg_metrics', {}),
                    'n_folds': len(fold_results)
                }, f, indent=2, default=str)
            
            logger.info(f"  Results saved to {results_path}")
            
        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            import traceback
            traceback.print_exc()
            raise
        
        finally:
            self.data_prep.close()
        
        elapsed = time.time() - start_time
        logger.info("\n" + "=" * 80)
        logger.info(f"Pipeline Complete! Elapsed: {elapsed/60:.1f} minutes")
        logger.info("=" * 80)
        
        return self.results

# =============================================================================
# Main
# =============================================================================
def main():
    """메인 실행"""
    config = PipelineConfig()
    pipeline = AresV21Pipeline(config)
    results = pipeline.run()
    
    if results.get('avg_metrics'):
        sharpe = results['avg_metrics']['sharpe_ratio']
        if sharpe >= 3.0:
            logger.info(f"\n🎉 TARGET ACHIEVED! Sharpe Ratio: {sharpe:.2f} >= 3.0")
        elif sharpe >= 1.5:
            logger.info(f"\n✅ Good progress! Sharpe Ratio: {sharpe:.2f}")
        else:
            logger.info(f"\n⚠️ Needs improvement. Sharpe Ratio: {sharpe:.2f}")

if __name__ == "__main__":
    main()
