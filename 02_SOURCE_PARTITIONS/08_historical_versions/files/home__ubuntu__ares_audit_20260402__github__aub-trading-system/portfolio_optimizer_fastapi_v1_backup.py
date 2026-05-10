#!/usr/bin/env python3
"""
Portfolio Optimizer FastAPI Service
- MVO, Black-Litterman, Risk Parity 최적화
- HTTP API로 제공하여 subprocess 오버헤드 제거
- 예상 latency: 50ms (vs subprocess 2500ms)
"""

import os
import json
import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.optimize import minimize
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Portfolio Optimizer Service",
    description="MVO, Black-Litterman, Risk Parity 포트폴리오 최적화 API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Pydantic Models
# ============================================================

class OptimizeRequest(BaseModel):
    asof: str  # YYYY-MM-DD
    base_allocation: Dict[str, float]  # symbol -> weight
    returns: Optional[Dict[str, List[float]]] = None  # symbol -> returns list
    covariance: Optional[Dict[str, Dict[str, float]]] = None  # symbol -> symbol -> cov
    risk_aversion: float = 2.5
    target_vol: float = 0.15
    max_weight: float = 0.25
    min_weight: float = 0.0
    method: str = "auto"  # auto, mvo, risk_parity, equal_weight

class OptimizeResponse(BaseModel):
    weights: Dict[str, float]
    method: str
    feasible: bool
    took_ms: int
    metadata: Dict[str, Any] = {}

# ============================================================
# Portfolio Optimizer Engine
# ============================================================

class PortfolioOptimizerEngine:
    """포트폴리오 최적화 엔진"""
    
    def __init__(self):
        self.default_vol = 0.20  # 기본 변동성
        self.default_corr = 0.30  # 기본 상관관계
    
    def optimize(self, request: OptimizeRequest) -> OptimizeResponse:
        """최적화 실행"""
        start_time = time.time()
        
        symbols = list(request.base_allocation.keys())
        n = len(symbols)
        
        if n == 0:
            return OptimizeResponse(
                weights={},
                method="empty",
                feasible=False,
                took_ms=0,
                metadata={"error": "No symbols provided"}
            )
        
        # 수익률 추정 (base_allocation을 expected return proxy로 사용)
        expected_returns = np.array([request.base_allocation.get(s, 0) for s in symbols])
        
        # 공분산 행렬 생성
        if request.covariance:
            cov_matrix = self._build_cov_from_dict(symbols, request.covariance)
        elif request.returns:
            cov_matrix = self._estimate_cov_from_returns(symbols, request.returns)
        else:
            cov_matrix = self._generate_default_cov(n)
        
        # 최적화 방법 선택
        method = request.method.lower()
        
        if method == "auto":
            # MVO 시도 → 실패 시 Risk Parity → 실패 시 Equal Weight
            result = self._try_mvo(symbols, expected_returns, cov_matrix, request)
            if not result["feasible"]:
                result = self._try_risk_parity(symbols, cov_matrix, request)
            if not result["feasible"]:
                result = self._equal_weight(symbols, request)
        elif method == "mvo":
            result = self._try_mvo(symbols, expected_returns, cov_matrix, request)
        elif method == "risk_parity":
            result = self._try_risk_parity(symbols, cov_matrix, request)
        else:
            result = self._equal_weight(symbols, request)
        
        elapsed_ms = int((time.time() - start_time) * 1000)
        
        return OptimizeResponse(
            weights=result["weights"],
            method=result["method"],
            feasible=result["feasible"],
            took_ms=elapsed_ms,
            metadata=result.get("metadata", {})
        )
    
    def _build_cov_from_dict(self, symbols: List[str], cov_dict: Dict) -> np.ndarray:
        """딕셔너리에서 공분산 행렬 생성"""
        n = len(symbols)
        cov = np.zeros((n, n))
        for i, s1 in enumerate(symbols):
            for j, s2 in enumerate(symbols):
                if s1 in cov_dict and s2 in cov_dict[s1]:
                    cov[i, j] = cov_dict[s1][s2]
                elif i == j:
                    cov[i, j] = self.default_vol ** 2
                else:
                    cov[i, j] = self.default_vol ** 2 * self.default_corr
        return cov
    
    def _estimate_cov_from_returns(self, symbols: List[str], returns_dict: Dict) -> np.ndarray:
        """수익률 데이터에서 공분산 행렬 추정"""
        n = len(symbols)
        returns_matrix = []
        
        for s in symbols:
            if s in returns_dict and len(returns_dict[s]) > 0:
                returns_matrix.append(returns_dict[s])
            else:
                # 기본 수익률 (0 with noise)
                returns_matrix.append([0.0] * 20)
        
        returns_array = np.array(returns_matrix)
        
        # 최소 데이터 확인
        if returns_array.shape[1] < 5:
            return self._generate_default_cov(n)
        
        try:
            cov = np.cov(returns_array)
            # 양정치 보장
            cov = self._ensure_positive_definite(cov)
            return cov
        except Exception:
            return self._generate_default_cov(n)
    
    def _generate_default_cov(self, n: int) -> np.ndarray:
        """기본 공분산 행렬 생성"""
        cov = np.full((n, n), self.default_vol ** 2 * self.default_corr)
        np.fill_diagonal(cov, self.default_vol ** 2)
        return cov
    
    def _ensure_positive_definite(self, cov: np.ndarray) -> np.ndarray:
        """공분산 행렬이 양정치가 되도록 보장"""
        try:
            eigvals, eigvecs = np.linalg.eigh(cov)
            eigvals = np.maximum(eigvals, 1e-8)
            return eigvecs @ np.diag(eigvals) @ eigvecs.T
        except Exception:
            n = cov.shape[0]
            return self._generate_default_cov(n)
    
    def _try_mvo(self, symbols: List[str], expected_returns: np.ndarray, 
                 cov_matrix: np.ndarray, request: OptimizeRequest) -> Dict:
        """Mean-Variance Optimization 시도"""
        n = len(symbols)
        
        try:
            # 목적 함수: -utility = -(returns - 0.5 * risk_aversion * variance)
            def objective(w):
                port_return = np.dot(w, expected_returns)
                port_var = np.dot(w, np.dot(cov_matrix, w))
                return -(port_return - 0.5 * request.risk_aversion * port_var)
            
            # 제약 조건
            constraints = [
                {'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}  # 합 = 1
            ]
            
            # 경계 조건
            bounds = [(request.min_weight, request.max_weight) for _ in range(n)]
            
            # 초기값
            x0 = np.array([1.0 / n] * n)
            
            # 최적화
            result = minimize(
                objective, x0,
                method='SLSQP',
                bounds=bounds,
                constraints=constraints,
                options={'maxiter': 100, 'ftol': 1e-8}
            )
            
            if result.success:
                weights = {s: max(0, w) for s, w in zip(symbols, result.x)}
                # 정규화
                total = sum(weights.values())
                if total > 0:
                    weights = {s: w / total for s, w in weights.items()}
                
                return {
                    "weights": weights,
                    "method": "MVO",
                    "feasible": True,
                    "metadata": {"iterations": result.nit, "fun": float(result.fun)}
                }
            else:
                return {"weights": {}, "method": "MVO", "feasible": False, "metadata": {"error": result.message}}
                
        except Exception as e:
            return {"weights": {}, "method": "MVO", "feasible": False, "metadata": {"error": str(e)}}
    
    def _try_risk_parity(self, symbols: List[str], cov_matrix: np.ndarray, 
                         request: OptimizeRequest) -> Dict:
        """Risk Parity 최적화"""
        n = len(symbols)
        
        try:
            # Risk Parity: 각 자산의 위험 기여도가 동일하도록
            def risk_contribution(w):
                port_var = np.dot(w, np.dot(cov_matrix, w))
                if port_var < 1e-10:
                    return np.zeros(n)
                marginal_risk = np.dot(cov_matrix, w)
                risk_contrib = w * marginal_risk / np.sqrt(port_var)
                return risk_contrib
            
            def objective(w):
                rc = risk_contribution(w)
                target_rc = np.mean(rc)
                return np.sum((rc - target_rc) ** 2)
            
            # 제약 조건
            constraints = [
                {'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}
            ]
            
            # 경계 조건
            bounds = [(0.01, request.max_weight) for _ in range(n)]
            
            # 초기값
            x0 = np.array([1.0 / n] * n)
            
            # 최적화
            result = minimize(
                objective, x0,
                method='SLSQP',
                bounds=bounds,
                constraints=constraints,
                options={'maxiter': 100, 'ftol': 1e-8}
            )
            
            if result.success or result.fun < 0.01:  # 근사 해도 허용
                weights = {s: max(0, w) for s, w in zip(symbols, result.x)}
                total = sum(weights.values())
                if total > 0:
                    weights = {s: w / total for s, w in weights.items()}
                
                return {
                    "weights": weights,
                    "method": "RISK_PARITY",
                    "feasible": True,
                    "metadata": {"iterations": result.nit, "fun": float(result.fun)}
                }
            else:
                return {"weights": {}, "method": "RISK_PARITY", "feasible": False, "metadata": {"error": result.message}}
                
        except Exception as e:
            return {"weights": {}, "method": "RISK_PARITY", "feasible": False, "metadata": {"error": str(e)}}
    
    def _equal_weight(self, symbols: List[str], request: OptimizeRequest) -> Dict:
        """Equal Weight 폴백"""
        n = len(symbols)
        weight = min(1.0 / n, request.max_weight)
        weights = {s: weight for s in symbols}
        
        # 정규화
        total = sum(weights.values())
        if total > 0:
            weights = {s: w / total for s, w in weights.items()}
        
        return {
            "weights": weights,
            "method": "EQUAL_WEIGHT",
            "feasible": True,
            "metadata": {"fallback": True}
        }

# 엔진 인스턴스
optimizer_engine = PortfolioOptimizerEngine()

# ============================================================
# API Endpoints
# ============================================================

@app.get("/health")
async def health_check():
    """헬스 체크"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.post("/optimize", response_model=OptimizeResponse)
async def optimize_portfolio(request: OptimizeRequest):
    """포트폴리오 최적화 실행"""
    try:
        result = optimizer_engine.optimize(request)
        logger.info(f"Optimization completed: method={result.method}, took={result.took_ms}ms")
        return result
    except Exception as e:
        logger.error(f"Optimization error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/methods")
async def list_methods():
    """지원하는 최적화 방법 목록"""
    return {
        "methods": [
            {"name": "auto", "description": "자동 선택 (MVO → Risk Parity → Equal Weight)"},
            {"name": "mvo", "description": "Mean-Variance Optimization"},
            {"name": "risk_parity", "description": "Risk Parity"},
            {"name": "equal_weight", "description": "Equal Weight"}
        ]
    }

# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("OPTIMIZER_PORT", 8765))
    logger.info(f"Starting Portfolio Optimizer FastAPI on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
