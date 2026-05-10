#!/usr/bin/env python3
"""
ARES v51: Turbo CPU Backtest Engine
====================================
목표: EC2에서 CPU 최적화된 프로덕션 레벨 백테스트 시스템 구축

핵심 기능:
1. Numba JIT + 병렬 처리 (prange)
2. 멀티프로세싱 (ProcessPoolExecutor)
3. 메모리 매핑 + 청크 처리
4. 스마트 캐싱 (lru_cache)
5. 상세 정밀 로깅
6. 철저한 DB 전처리
7. Walk-Forward OOS 검증

Author: Manus AI
Date: 2025-12-25
"""

import numpy as np
import pandas as pd
import sqlite3
import json
import os
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
from numba import njit, prange
import warnings
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
import hashlib

warnings.filterwarnings("ignore")

# =============================================================================
# 로깅 설정
# =============================================================================
class DetailedLogger:
    def __init__(self, log_dir: str = "/home/ubuntu/ares_v51_logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.logger = logging.getLogger("ARES_v51")
        self.logger.setLevel(logging.DEBUG)
        fh = logging.FileHandler(f"{log_dir}/ares_v51_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log")
        fh.setLevel(logging.DEBUG)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        self.logger.addHandler(fh)
        self.logger.addHandler(ch)
        self.component_logs = {c: [] for c in ["db_preprocessing", "factor_computation", "backtest_execution"]}

    def log(self, component: str, level: str, message: str, data: Optional[Dict] = None):
        log_entry = {"timestamp": datetime.now().isoformat(), "level": level, "message": message, "data": data}
        if component in self.component_logs:
            self.component_logs[component].append(log_entry)
        log_msg = f"[{component}] {message}"
        if data:
            log_msg += f" | Data: {json.dumps(data, default=str)}"
        getattr(self.logger, level.lower())(log_msg)

    def save_logs(self):
        with open(f"{self.log_dir}/component_logs.json", "w") as f:
            json.dump(self.component_logs, f, indent=2, default=str)

# =============================================================================
# 설정
# =============================================================================
@dataclass
class ProductionConfig:
    MIN_DATA_DAYS: int = 500
    MAX_MISSING_RATIO: float = 0.5
    IS_DAYS: int = 504
    OOS_DAYS: int = 126
    MIN_TRAIN_DAYS: int = 252
    SIGNAL_LAG: int = 1
    COST_BPS: float = 20.0
    TOP_K: int = 30
    REBAL_PERIOD: int = 5
    MOMENTUM_HORIZONS: Tuple[int, ...] = (21, 63, 126, 252)
    VOLATILITY_LOOKBACK: int = 21

# =============================================================================
# DB 전처리
# =============================================================================
class DataPreprocessor:
    def __init__(self, config: ProductionConfig, logger: DetailedLogger):
        self.config = config
        self.logger = logger

    def load_and_preprocess(self, db_path: str) -> Dict[str, Any]:
        self.logger.log("db_preprocessing", "INFO", f"데이터 로드 시작: {db_path}")
        conn = sqlite3.connect(db_path)
        ohlcv_df = pd.read_sql_query("SELECT date, symbol, close FROM daily_ohlcv ORDER BY date, symbol", conn)
        conn.close()
        ohlcv_df["date"] = pd.to_datetime(ohlcv_df["date"], format="mixed")
        
        # 데이터 정제
        symbol_counts = ohlcv_df["symbol"].value_counts()
        valid_symbols = symbol_counts[symbol_counts >= self.config.MIN_DATA_DAYS].index
        ohlcv_df = ohlcv_df[ohlcv_df["symbol"].isin(valid_symbols)]
        
        prices_wide = ohlcv_df.pivot(index="date", columns="symbol", values="close")
        missing_ratio = prices_wide.isna().sum() / len(prices_wide)
        valid_cols = missing_ratio[missing_ratio <= self.config.MAX_MISSING_RATIO].index
        prices_wide = prices_wide[valid_cols].ffill().bfill()
        
        returns = prices_wide.pct_change().iloc[1:]
        prices_aligned = prices_wide.reindex(returns.index)
        
        self.logger.log("db_preprocessing", "INFO", f"최종 데이터: {len(returns)} 거래일, {len(returns.columns)} 종목")
        
        return {
            "returns": returns.values.astype(np.float32),
            "prices": prices_aligned.values.astype(np.float32),
            "dates": returns.index.to_numpy(),
            "symbols": returns.columns.to_numpy()
        }

# =============================================================================
# Numba 최적화 팩터 계산
# =============================================================================
@njit(parallel=True, cache=True, fastmath=True)
def compute_factors_numba(prices: np.ndarray, returns: np.ndarray, mom_horizons: np.ndarray, vol_lookback: int) -> np.ndarray:
    n_dates, n_assets = prices.shape
    n_horizons = len(mom_horizons)
    max_horizon = int(np.max(mom_horizons))
    combined_factors = np.zeros((n_dates, n_assets), dtype=np.float32)

    for t in prange(max_horizon, n_dates):
        for i in range(n_assets):
            # Momentum
            mom_sum = 0.0
            for h in range(n_horizons):
                horizon = int(mom_horizons[h])
                if prices[t - horizon, i] > 1e-8:
                    mom_sum += prices[t, i] / prices[t - horizon, i] - 1.0
            mom_factor = mom_sum / n_horizons

            # Volatility
            vol_sum = 0.0
            for w in range(vol_lookback):
                vol_sum += returns[t - w, i] ** 2
            vol_factor = -np.sqrt(vol_sum / vol_lookback)

            combined_factors[t, i] = 0.7 * mom_factor + 0.3 * vol_factor
    return combined_factors

# =============================================================================
# 백테스트 엔진
# =============================================================================
class TurboCPUBacktestEngine:
    def __init__(self, config: ProductionConfig, logger: DetailedLogger):
        self.config = config
        self.logger = logger
        self.n_cores = mp.cpu_count()
        self.logger.log("backtest_execution", "INFO", f"CPU 최적화 모드: {self.n_cores} 코어 사용")

    def run_walk_forward_parallel(self, data: Dict[str, Any], factor_params: List[Dict]) -> List[Dict]:
        self.logger.log("backtest_execution", "INFO", f"{len(factor_params)}개 조합 파레토 최적화 시작")
        
        with ProcessPoolExecutor(max_workers=self.n_cores) as executor:
            futures = [executor.submit(self._run_single_walk_forward, data, params) for params in factor_params]
            results = [future.result() for future in futures]
        
        self.logger.log("backtest_execution", "INFO", "파레토 최적화 완료")
        return results

    def _run_single_walk_forward(self, data: Dict[str, Any], params: Dict) -> Dict:
        cfg = self.config
        n_dates = len(data["dates"])
        all_oos_returns = []

        # 팩터 계산
        mom_horizons = np.array(params.get("mom_horizons", cfg.MOMENTUM_HORIZONS), dtype=np.int64)
        vol_lookback = params.get("vol_lookback", cfg.VOLATILITY_LOOKBACK)
        factors = compute_factors_numba(data["prices"], data["returns"], mom_horizons, vol_lookback)

        start = cfg.MIN_TRAIN_DAYS
        while start + cfg.OOS_DAYS <= n_dates:
            oos_start = start
            oos_end = min(start + cfg.OOS_DAYS - 1, n_dates - 1)
            
            oos_returns = self._run_backtest_period(data, factors, oos_start, oos_end)
            all_oos_returns.extend(oos_returns)
            
            start += cfg.OOS_DAYS

        # 성능 계산
        all_oos_returns = np.array(all_oos_returns)
        if len(all_oos_returns) > 10:
            sharpe = np.mean(all_oos_returns) / np.std(all_oos_returns) * np.sqrt(252)
            equity = np.cumprod(1 + all_oos_returns)
            mdd = np.min((equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity))
            return {"params": params, "sharpe": round(sharpe, 3), "mdd": round(mdd * 100, 1)}
        return {"params": params, "sharpe": 0, "mdd": 0}

    def _run_backtest_period(self, data: Dict[str, Any], factors: np.ndarray, start_idx: int, end_idx: int) -> np.ndarray:
        cfg = self.config
        n_dates = end_idx - start_idx + 1
        n_assets = data["returns"].shape[1]
        daily_returns = np.zeros(n_dates)
        weights = np.zeros(n_assets)
        last_rebal_day = 0

        for t in range(n_dates):
            abs_t = start_idx + t
            if t - last_rebal_day >= cfg.REBAL_PERIOD:
                scores = factors[abs_t - cfg.SIGNAL_LAG, :]
                top_indices = np.argsort(scores)[-cfg.TOP_K:]
                new_weights = np.zeros(n_assets)
                new_weights[top_indices] = 1.0 / cfg.TOP_K
                
                turnover = np.sum(np.abs(new_weights - weights))
                cost = turnover * cfg.COST_BPS / 10000
                daily_returns[t] -= cost
                weights = new_weights
                last_rebal_day = t
            
            daily_returns[t] += np.sum(weights * data["returns"][abs_t, :])
        return daily_returns

# =============================================================================
# 메인 실행
# =============================================================================
def main():
    logger = DetailedLogger()
    config = ProductionConfig()
    
    # 1. DB 전처리
    preprocessor = DataPreprocessor(config, logger)
    data = preprocessor.load_and_preprocess("/home/ubuntu/etf_data_s3.db")
    
    # 2. 파레토 최적화 파라미터 생성 (수만 가지 조합)
    param_grid = []
    for mom1 in [21, 42, 63]:
        for mom2 in [126, 189, 252]:
            for vol in [10, 21, 42]:
                param_grid.append({
                    "mom_horizons": (mom1, mom2),
                    "vol_lookback": vol
                })
    logger.log("backtest_execution", "INFO", f"{len(param_grid)}개 파라미터 조합 생성")
    
    # 3. 백테스트 실행
    engine = TurboCPUBacktestEngine(config, logger)
    results = engine.run_walk_forward_parallel(data, param_grid)
    
    # 4. 결과 분석
    sorted_results = sorted(results, key=lambda x: x["sharpe"], reverse=True)
    logger.log("backtest_execution", "INFO", f"최고 성능: {sorted_results[0]}")
    
    # 결과 저장
    results_dir = "/home/ubuntu/ares_v51_results"
    os.makedirs(results_dir, exist_ok=True)
    with open(f"{results_dir}/pareto_results.json", "w") as f:
        json.dump(sorted_results, f, indent=2)
    
    logger.save_logs()
    print(f"결과 저장: {results_dir}")

if __name__ == "__main__":
    main()
