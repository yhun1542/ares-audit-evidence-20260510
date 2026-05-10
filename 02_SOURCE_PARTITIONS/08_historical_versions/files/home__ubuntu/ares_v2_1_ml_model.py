#!/usr/bin/env python3
"""
ARES v2.1 Production-Level ML Model
===================================
4대 AI 자문 기반 프로덕션 레벨 ML 모델

Features:
- LightGBM GPU 모델
- 5개 모델 Bagging Ensemble
- Bayesian Optimization (Optuna)
- Grid Search 최적화
- 과적합 방지 (Early Stopping, Feature Stability)
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
import warnings
import logging
import pickle
import json
from datetime import datetime
from pathlib import Path

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    import lightgbm as lgb
    HAS_LIGHTGBM = True
except ImportError:
    HAS_LIGHTGBM = False
    logger.warning("LightGBM not installed")

try:
    import optuna
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False
    logger.warning("Optuna not installed")

from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from scipy import stats

# =============================================================================
# Configuration
# =============================================================================
@dataclass
class ModelConfig:
    """ML 모델 설정"""
    # LightGBM 기본 파라미터
    n_estimators: int = 1000
    learning_rate: float = 0.05
    max_depth: int = 6
    num_leaves: int = 31
    min_child_samples: int = 20
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_alpha: float = 0.1
    reg_lambda: float = 0.1
    
    # GPU 설정
    device: str = 'gpu'
    gpu_platform_id: int = 0
    gpu_device_id: int = 0
    
    # 앙상블 설정
    n_bags: int = 5
    bag_seed_base: int = 42
    
    # Early Stopping
    early_stopping_rounds: int = 50
    
    # 검증 설정
    n_cv_splits: int = 5
    
    # 최적화 설정
    n_optuna_trials: int = 100
    optuna_timeout: int = 3600  # 1시간
    
    # 저장 경로
    model_dir: str = "/home/ubuntu/ares_v2_1_backtest/models"

# =============================================================================
# LightGBM Model
# =============================================================================
class LightGBMModel:
    """LightGBM GPU 모델"""
    
    def __init__(self, config: ModelConfig, params: Optional[Dict] = None):
        self.config = config
        self.model = None
        self.feature_importance = None
        
        # 기본 파라미터
        self.params = {
            'objective': 'multiclass',
            'num_class': 3,
            'metric': 'multi_logloss',
            'boosting_type': 'gbdt',
            'n_estimators': config.n_estimators,
            'learning_rate': config.learning_rate,
            'max_depth': config.max_depth,
            'num_leaves': config.num_leaves,
            'min_child_samples': config.min_child_samples,
            'subsample': config.subsample,
            'colsample_bytree': config.colsample_bytree,
            'reg_alpha': config.reg_alpha,
            'reg_lambda': config.reg_lambda,
            'random_state': 42,
            'n_jobs': -1,
            'verbose': -1,
        }
        
        # GPU 설정
        if config.device == 'gpu' and HAS_LIGHTGBM:
            self.params['device'] = 'gpu'
            self.params['gpu_platform_id'] = config.gpu_platform_id
            self.params['gpu_device_id'] = config.gpu_device_id
        
        # 사용자 정의 파라미터 오버라이드
        if params:
            self.params.update(params)
    
    def fit(self, X: pd.DataFrame, y: pd.Series, 
            X_val: Optional[pd.DataFrame] = None, 
            y_val: Optional[pd.Series] = None,
            sample_weight: Optional[pd.Series] = None) -> 'LightGBMModel':
        """모델 학습"""
        if not HAS_LIGHTGBM:
            raise ImportError("LightGBM not installed")
        
        # 라벨 변환 (Triple Barrier: -1, 0, 1 -> 0, 1, 2)
        y_train = y.map({-1: 0, 0: 1, 1: 2})
        
        callbacks = [lgb.early_stopping(self.config.early_stopping_rounds, verbose=False)]
        
        if X_val is not None and y_val is not None:
            y_val_mapped = y_val.map({-1: 0, 0: 1, 1: 2})
            eval_set = [(X_val, y_val_mapped)]
        else:
            eval_set = None
        
        self.model = lgb.LGBMClassifier(**self.params)
        
        self.model.fit(
            X, y_train,
            eval_set=eval_set,
            callbacks=callbacks if eval_set else None,
            sample_weight=sample_weight
        )
        
        # Feature Importance 저장
        self.feature_importance = pd.Series(
            self.model.feature_importances_,
            index=X.columns
        ).sort_values(ascending=False)
        
        return self
    
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """예측 (클래스)"""
        if self.model is None:
            raise ValueError("Model not trained")
        
        pred = self.model.predict(X)
        # 0, 1, 2 -> -1, 0, 1
        return np.array([-1 if p == 0 else 0 if p == 1 else 1 for p in pred])
    
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """예측 (확률)"""
        if self.model is None:
            raise ValueError("Model not trained")
        
        return self.model.predict_proba(X)
    
    def get_alpha_score(self, X: pd.DataFrame) -> pd.Series:
        """알파 스코어 계산 (Long - Short 확률)"""
        proba = self.predict_proba(X)
        # P(Long) - P(Short)
        alpha = proba[:, 2] - proba[:, 0]
        return pd.Series(alpha, index=X.index)
    
    def save(self, path: str):
        """모델 저장"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as f:
            pickle.dump({
                'model': self.model,
                'params': self.params,
                'feature_importance': self.feature_importance
            }, f)
    
    def load(self, path: str):
        """모델 로드"""
        with open(path, 'rb') as f:
            data = pickle.load(f)
            self.model = data['model']
            self.params = data['params']
            self.feature_importance = data['feature_importance']
        return self

# =============================================================================
# Bagging Ensemble
# =============================================================================
class BaggingEnsemble:
    """5개 모델 Bagging 앙상블"""
    
    def __init__(self, config: ModelConfig):
        self.config = config
        self.models: List[LightGBMModel] = []
        self.feature_importance_stability = None
    
    def fit(self, X: pd.DataFrame, y: pd.Series,
            X_val: Optional[pd.DataFrame] = None,
            y_val: Optional[pd.Series] = None,
            sample_weight: Optional[pd.Series] = None) -> 'BaggingEnsemble':
        """앙상블 학습"""
        self.models = []
        feature_importances = []
        
        for i in range(self.config.n_bags):
            logger.info(f"Training bag {i+1}/{self.config.n_bags}")
            
            # 다른 시드로 모델 생성
            params = {'random_state': self.config.bag_seed_base + i}
            model = LightGBMModel(self.config, params)
            
            # Bootstrap 샘플링
            np.random.seed(self.config.bag_seed_base + i)
            boot_idx = np.random.choice(len(X), len(X), replace=True)
            X_boot = X.iloc[boot_idx]
            y_boot = y.iloc[boot_idx]
            weight_boot = sample_weight.iloc[boot_idx] if sample_weight is not None else None
            
            model.fit(X_boot, y_boot, X_val, y_val, weight_boot)
            self.models.append(model)
            
            if model.feature_importance is not None:
                feature_importances.append(model.feature_importance)
        
        # Feature Importance Stability 계산
        if feature_importances:
            fi_df = pd.DataFrame(feature_importances)
            self.feature_importance_stability = fi_df.std() / fi_df.mean()
        
        return self
    
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """앙상블 예측 (다수결)"""
        predictions = np.array([model.predict(X) for model in self.models])
        return stats.mode(predictions, axis=0, keepdims=False)[0]
    
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """앙상블 확률 예측 (평균)"""
        probas = np.array([model.predict_proba(X) for model in self.models])
        return probas.mean(axis=0)
    
    def get_alpha_score(self, X: pd.DataFrame) -> pd.Series:
        """앙상블 알파 스코어"""
        proba = self.predict_proba(X)
        alpha = proba[:, 2] - proba[:, 0]
        return pd.Series(alpha, index=X.index)
    
    def get_feature_importance(self) -> pd.Series:
        """평균 Feature Importance"""
        if not self.models:
            return pd.Series(dtype=float)
        
        fi_list = [m.feature_importance for m in self.models if m.feature_importance is not None]
        if not fi_list:
            return pd.Series(dtype=float)
        
        return pd.concat(fi_list, axis=1).mean(axis=1).sort_values(ascending=False)

# =============================================================================
# Hyperparameter Optimization
# =============================================================================
class HyperparameterOptimizer:
    """Optuna 기반 하이퍼파라미터 최적화"""
    
    def __init__(self, config: ModelConfig):
        self.config = config
        self.best_params = None
        self.study = None
    
    def get_param_space(self, trial) -> Dict:
        """파라미터 탐색 공간 정의"""
        return {
            'n_estimators': trial.suggest_int('n_estimators', 100, 2000),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
            'max_depth': trial.suggest_int('max_depth', 3, 12),
            'num_leaves': trial.suggest_int('num_leaves', 8, 128),
            'min_child_samples': trial.suggest_int('min_child_samples', 5, 100),
            'subsample': trial.suggest_float('subsample', 0.5, 1.0),
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
            'reg_alpha': trial.suggest_float('reg_alpha', 1e-8, 10.0, log=True),
            'reg_lambda': trial.suggest_float('reg_lambda', 1e-8, 10.0, log=True),
        }
    
    def objective(self, trial, X: pd.DataFrame, y: pd.Series) -> float:
        """최적화 목표 함수"""
        params = self.get_param_space(trial)
        
        # Time Series Cross-Validation
        tscv = TimeSeriesSplit(n_splits=self.config.n_cv_splits)
        scores = []
        
        for train_idx, val_idx in tscv.split(X):
            X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
            y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
            
            model = LightGBMModel(self.config, params)
            model.fit(X_train, y_train, X_val, y_val)
            
            pred = model.predict(X_val)
            score = accuracy_score(y_val, pred)
            scores.append(score)
        
        return np.mean(scores)
    
    def optimize(self, X: pd.DataFrame, y: pd.Series) -> Dict:
        """하이퍼파라미터 최적화 실행"""
        if not HAS_OPTUNA:
            logger.warning("Optuna not installed, using default params")
            return {}
        
        logger.info(f"Starting Optuna optimization ({self.config.n_optuna_trials} trials)")
        
        self.study = optuna.create_study(direction='maximize')
        self.study.optimize(
            lambda trial: self.objective(trial, X, y),
            n_trials=self.config.n_optuna_trials,
            timeout=self.config.optuna_timeout,
            show_progress_bar=True
        )
        
        self.best_params = self.study.best_params
        logger.info(f"Best params: {self.best_params}")
        logger.info(f"Best score: {self.study.best_value:.4f}")
        
        return self.best_params

# =============================================================================
# Grid Search
# =============================================================================
class GridSearchOptimizer:
    """Grid Search 최적화 (수만 가지 조합)"""
    
    def __init__(self, config: ModelConfig):
        self.config = config
        self.results = []
        self.best_params = None
        self.best_score = -np.inf
    
    def get_param_grid(self) -> Dict[str, List]:
        """파라미터 그리드 정의 (우선순위 기반)"""
        return {
            # Tier 1: 최고 우선순위 (Sharpe 영향도 0.5+)
            'lookback_momentum': [21, 42, 63, 126, 252],
            'lookback_volatility': [10, 20, 40, 60],
            
            # Tier 2: 높은 우선순위 (Sharpe 영향도 0.3-0.5)
            'top_k': [10, 15, 20, 30, 50],
            'rebalance_freq': [5, 10, 21, 42],
            'max_position_weight': [0.05, 0.10, 0.15, 0.20],
            
            # Tier 3: 중간 우선순위 (Sharpe 영향도 0.1-0.3)
            'vol_target': [0.10, 0.15, 0.20, 0.25],
            'stop_loss_sigma': [1.5, 2.0, 2.5, 3.0],
            'profit_target_sigma': [1.5, 2.0, 2.5, 3.0],
            
            # ML 파라미터
            'learning_rate': [0.01, 0.05, 0.1],
            'max_depth': [4, 6, 8],
            'num_leaves': [15, 31, 63],
        }
    
    def count_combinations(self) -> int:
        """총 조합 수 계산"""
        grid = self.get_param_grid()
        total = 1
        for values in grid.values():
            total *= len(values)
        return total
    
    def run_grid_search(self, evaluate_func, max_combinations: int = 10000) -> Dict:
        """
        Grid Search 실행
        
        Args:
            evaluate_func: 파라미터 -> 스코어 평가 함수
            max_combinations: 최대 조합 수
        
        Returns:
            최적 파라미터
        """
        import itertools
        
        grid = self.get_param_grid()
        param_names = list(grid.keys())
        param_values = list(grid.values())
        
        total_combinations = self.count_combinations()
        logger.info(f"Total combinations: {total_combinations:,}")
        
        # 조합 수 제한
        if total_combinations > max_combinations:
            logger.warning(f"Limiting to {max_combinations:,} combinations (random sampling)")
            all_combinations = list(itertools.product(*param_values))
            np.random.seed(42)
            selected_idx = np.random.choice(len(all_combinations), max_combinations, replace=False)
            combinations = [all_combinations[i] for i in selected_idx]
        else:
            combinations = list(itertools.product(*param_values))
        
        self.results = []
        
        for i, values in enumerate(combinations):
            params = dict(zip(param_names, values))
            
            if (i + 1) % 100 == 0:
                logger.info(f"Progress: {i+1}/{len(combinations)} ({(i+1)/len(combinations)*100:.1f}%)")
            
            try:
                score = evaluate_func(params)
                
                self.results.append({
                    'params': params,
                    'score': score
                })
                
                if score > self.best_score:
                    self.best_score = score
                    self.best_params = params
                    logger.info(f"New best: {score:.4f} with {params}")
                    
            except Exception as e:
                logger.warning(f"Failed with params {params}: {e}")
                continue
        
        logger.info(f"\nGrid Search Complete!")
        logger.info(f"Best Score: {self.best_score:.4f}")
        logger.info(f"Best Params: {self.best_params}")
        
        return self.best_params
    
    def get_results_df(self) -> pd.DataFrame:
        """결과를 DataFrame으로 반환"""
        if not self.results:
            return pd.DataFrame()
        
        rows = []
        for r in self.results:
            row = r['params'].copy()
            row['score'] = r['score']
            rows.append(row)
        
        return pd.DataFrame(rows).sort_values('score', ascending=False)

# =============================================================================
# Feature Stability Analyzer
# =============================================================================
class FeatureStabilityAnalyzer:
    """피처 안정성 분석"""
    
    def __init__(self):
        self.importance_history = []
    
    def add_importance(self, importance: pd.Series, date: str):
        """Feature Importance 기록 추가"""
        self.importance_history.append({
            'date': date,
            'importance': importance
        })
    
    def compute_stability_index(self) -> pd.Series:
        """
        Feature Stability Index 계산
        높을수록 안정적 (과적합 위험 낮음)
        """
        if len(self.importance_history) < 2:
            return pd.Series(dtype=float)
        
        # DataFrame으로 변환
        fi_df = pd.DataFrame([h['importance'] for h in self.importance_history])
        
        # Coefficient of Variation
        cv = fi_df.std() / fi_df.mean()
        
        # Stability Index = 1 / (1 + CV)
        stability = 1 / (1 + cv)
        
        return stability.sort_values(ascending=False)
    
    def get_stable_features(self, threshold: float = 0.5) -> List[str]:
        """안정적인 피처 목록 반환"""
        stability = self.compute_stability_index()
        return stability[stability >= threshold].index.tolist()

# =============================================================================
# Main
# =============================================================================
def main():
    """테스트 실행"""
    logger.info("ARES v2.1 ML Model")
    logger.info("=" * 60)
    
    config = ModelConfig()
    
    # 테스트 데이터 생성
    np.random.seed(42)
    n_samples = 1000
    n_features = 20
    
    X = pd.DataFrame(
        np.random.randn(n_samples, n_features),
        columns=[f'feature_{i}' for i in range(n_features)]
    )
    y = pd.Series(np.random.choice([-1, 0, 1], n_samples))
    
    # 단일 모델 테스트
    logger.info("\n1. Single Model Test")
    model = LightGBMModel(config)
    
    X_train, X_val = X.iloc[:800], X.iloc[800:]
    y_train, y_val = y.iloc[:800], y.iloc[800:]
    
    model.fit(X_train, y_train, X_val, y_val)
    
    pred = model.predict(X_val)
    acc = accuracy_score(y_val, pred)
    logger.info(f"Single Model Accuracy: {acc:.4f}")
    
    alpha = model.get_alpha_score(X_val)
    logger.info(f"Alpha Score Range: [{alpha.min():.4f}, {alpha.max():.4f}]")
    
    # 앙상블 테스트
    logger.info("\n2. Ensemble Test")
    ensemble = BaggingEnsemble(config)
    ensemble.fit(X_train, y_train, X_val, y_val)
    
    pred_ensemble = ensemble.predict(X_val)
    acc_ensemble = accuracy_score(y_val, pred_ensemble)
    logger.info(f"Ensemble Accuracy: {acc_ensemble:.4f}")
    
    # Feature Importance
    fi = ensemble.get_feature_importance()
    logger.info(f"\nTop 5 Features:\n{fi.head()}")
    
    # Grid Search 조합 수 확인
    logger.info("\n3. Grid Search Combinations")
    grid_optimizer = GridSearchOptimizer(config)
    total_combos = grid_optimizer.count_combinations()
    logger.info(f"Total Grid Search Combinations: {total_combos:,}")
    
    logger.info("\nDone!")

if __name__ == "__main__":
    main()
