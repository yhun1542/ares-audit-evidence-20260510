#!/usr/bin/env python3
"""
================================================================================
ARES Phase 2b v6.6.1 - 헤지 베타 수정 버전
================================================================================

v6.1 핵심 수정 (GPT 지적 반영):
- 헤지 베타: port_ret vs mkt_ret → 자산별 beta_i 합
- 레짐 게이팅: BULL/STRONG_BULL에서 헤지 강제 OFF
- portfolio_beta = Σ w_i * beta_i (보유종목 베타 합)

기존 v6 기능 유지:
- 룩어헤드 차단 불변식
- Market Hedge (개선된 베타 계산)
- Risk Budget State Machine
- Deep Logging
- Add-on 스위치

================================================================================
"""

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path
import time
import os
from datetime import datetime
from copy import deepcopy
from typing import Dict, List, Tuple, Optional, Any
import warnings
warnings.filterwarnings('ignore')

import numpy as np
from functools import lru_cache

# =============================================================================
# Sharpe Explosion Guard (prevents 5e10 sharpe; FAIL-fast diagnostics)
# =============================================================================
def safe_sharpe_stats(daily_returns, ann=252.0, min_n=30, eps_std=1e-6, cap_abs_sharpe=20.0):
    """Return (sharpe_or_0, diag_dict).
    - Filters non-finite returns.
    - Fails if too short or near-zero volatility.
    - Caps absurd sharpe by marking invalid.
    """
    import numpy as _np
    r = _np.asarray(daily_returns, dtype=_np.float64)
    finite = _np.isfinite(r)
    r = r[finite]
    diag = {
        "n": int(r.size),
        "n_finite": int(finite.sum()),
    }
    if r.size == 0:
        diag.update({"ok": False, "reason": "empty"})
        return 0.0, diag
    diag.update({
        "mean": float(_np.mean(r)),
        "std": float(_np.std(r, ddof=1)) if r.size >= 2 else 0.0,
        "min": float(_np.min(r)),
        "max": float(_np.max(r)),
        "unique_rounded": int(len(_np.unique(_np.round(r, 12))))
    })
    if r.size < int(min_n):
        diag.update({"ok": False, "reason": "too_short"})
        return 0.0, diag
    sd = diag["std"]
    if (sd is None) or (sd < float(eps_std)):
        diag.update({"ok": False, "reason": "near_zero_vol"})
        return 0.0, diag
    sh = float((diag["mean"] * float(ann)) / (sd * (_np.sqrt(float(ann))) + 1e-12))
    if (not _np.isfinite(sh)) or (abs(sh) > float(cap_abs_sharpe)):
        diag.update({"ok": False, "reason": "exploded", "raw_sharpe": float(sh)})
        return 0.0, diag
    diag.update({"ok": True, "reason": "ok", "raw_sharpe": float(sh)})
    return float(sh), diag

import pandas as pd
from numba import njit
from scipy.stats import spearmanr
import sys; sys.path.insert(0, "/home/ubuntu/AUB"); from production_modules.features.rateshock_features import load_y2_series, compute_d2y_features, RateShockFeatureConfig


# =============================================================================
# 0.1 Scenario signature (WF Add-on 적용 증거 + 캐싱/미적용 사고 방지)
# =============================================================================
def _sha256_file(path: str) -> str:
    if (not path) or (not os.path.exists(path)):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


# 17키 고정 scenario.flags (통합 테스트 안정화용)
CANONICAL_FLAGS = [
    "hedge", "cb", "severity", "throttle", "compat", "no_trade", "impact",
    "icir", "icir_v4", "mvo", "macro_gate", "vix_sizing", "regime15",
    "alpha_orchestrator", "pead_overlay", "regime_v11", "rate_shock_gate",
]

def normalize_flags(flags_raw: dict) -> dict:
    """scenario.flags를 17키 고정 스키마로 정규화"""
    out = {}
    for k in CANONICAL_FLAGS:
        out[k] = bool(flags_raw.get(k, False))
    return out


def scenario_signature(flags: Dict[str, Any], policy_path: Optional[str], start_date: str = None) -> Dict[str, Any]:
    sig = {
        "flags": normalize_flags(flags or {}),
        "policy_sha": _sha256_file(policy_path) if policy_path else "",
        "start_date": start_date if start_date else ""
    }
    raw = json.dumps(sig, sort_keys=True, ensure_ascii=False).encode("utf-8")
    sig["signature"] = hashlib.sha256(raw).hexdigest()[:12]
    return sig

def wf_result_hash(folds: List[Dict[str, Any]]) -> str:
    payload = [(int(f.get("fold", -1)), float(f.get("oos_sharpe", 0.0))) for f in folds]
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


# =============================================================================
# 1. Numba 최적화 함수
# =============================================================================

@njit(cache=True, fastmath=True)
def fast_rolling_std(arr, window):
    n = len(arr)
    result = np.empty(n)
    result[:] = np.nan
    for i in range(window - 1, n):
        total = 0.0
        count = 0
        for j in range(i - window + 1, i + 1):
            if not np.isnan(arr[j]):
                total += arr[j]
                count += 1
        if count >= 2:
            mean = total / count
            var_sum = 0.0
            for j in range(i - window + 1, i + 1):
                if not np.isnan(arr[j]):
                    var_sum += (arr[j] - mean) ** 2
            result[i] = np.sqrt(var_sum / count)
    return result


@njit(cache=True, fastmath=True)
def fast_zscore(arr, window, min_periods):
    n = len(arr)
    result = np.empty(n)
    result[:] = np.nan
    for i in range(min_periods - 1, n):
        start_idx = max(0, i - window + 1)
        total = 0.0
        count = 0
        for j in range(start_idx, i + 1):
            if not np.isnan(arr[j]):
                total += arr[j]
                count += 1
        if count >= min_periods:
            mean = total / count
            var_sum = 0.0
            for j in range(start_idx, i + 1):
                if not np.isnan(arr[j]):
                    var_sum += (arr[j] - mean) ** 2
            if count > 0:
                std = np.sqrt(var_sum / count) + 1e-12
            else:
                std = 1e-12
            if np.isnan(arr[i]):
                result[i] = 0.0
            else:
                result[i] = (arr[i] - mean) / std
    return result


@njit(cache=True, fastmath=True)
def fast_max_drawdown(returns):
    n = len(returns)
    if n == 0:
        return 0.0
    cumret = 1.0
    peak = 1.0
    max_dd = 0.0
    for i in range(n):
        cumret *= (1.0 + returns[i])
        if cumret > peak:
            peak = cumret
        dd = (cumret - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


@njit(cache=True, fastmath=True)
def compute_regime_score(vix_z, vol_z, mom_z, dd20):
    score = 50.0
    
    if vix_z > 2.0: score -= 25.0
    elif vix_z > 1.0: score -= 12.0
    elif vix_z < -2.0: score += 20.0
    elif vix_z < -1.0: score += 12.0
    
    if vol_z > 2.0: score -= 15.0
    elif vol_z > 1.0: score -= 8.0
    elif vol_z < -1.0: score += 8.0
    
    if mom_z < -2.0: score -= 12.0
    elif mom_z < -1.0: score -= 6.0
    elif mom_z > 1.0: score += 8.0
    
    if dd20 < -0.15: score -= 8.0
    elif dd20 < -0.10: score -= 4.0
    
    return score


@njit(cache=True, fastmath=True)
def score_to_regime(score, prev_regime, hysteresis=0.08):
    crisis_th, bear_th, neutral_th, bull_th = 25.0, 40.0, 55.0, 70.0
    h = hysteresis * 100
    
    if prev_regime == 0:
        if score > crisis_th + h:
            if score > bear_th + h:
                if score > neutral_th + h:
                    return 4 if score > bull_th + h else 3
                return 2
            return 1
        return 0
    elif prev_regime == 1:
        if score < crisis_th - h:
            return 0
        if score > bear_th + h:
            if score > neutral_th + h:
                return 4 if score > bull_th + h else 3
            return 2
        return 1
    elif prev_regime == 2:
        if score < bear_th - h:
            return 0 if score < crisis_th - h else 1
        if score > neutral_th + h:
            return 4 if score > bull_th + h else 3
        return 2
    elif prev_regime == 3:
        if score < neutral_th - h:
            if score < bear_th - h:
                return 0 if score < crisis_th - h else 1
            return 2
        return 4 if score > bull_th + h else 3
    else:
        if score < bull_th - h:
            if score < neutral_th - h:
                if score < bear_th - h:
                    return 0 if score < crisis_th - h else 1
                return 2
            return 3
        return 4


@njit(cache=True, fastmath=True)
def fast_rolling_beta(port_ret, mkt_ret, window):
    """Rolling beta 계산"""
    n = len(port_ret)
    result = np.empty(n)
    result[:] = np.nan
    
    for i in range(window - 1, n):
        # 공분산과 분산 계산
        port_slice = port_ret[i - window + 1:i + 1]
        mkt_slice = mkt_ret[i - window + 1:i + 1]
        
        port_mean = np.mean(port_slice)
        mkt_mean = np.mean(mkt_slice)
        
        cov = 0.0
        var_mkt = 0.0
        for j in range(window):
            cov += (port_slice[j] - port_mean) * (mkt_slice[j] - mkt_mean)
            var_mkt += (mkt_slice[j] - mkt_mean) ** 2
        
        if var_mkt > 1e-12:
            result[i] = cov / var_mkt
        else:
            result[i] = 1.0
    
    return result


@njit(cache=True, fastmath=True)
def compute_asset_betas(returns, mkt_ret, window):
    """
    v6.1: 각 자산의 시장 대비 베타를 rolling으로 계산
    
    Args:
        returns: (n_days, n_assets) 자산별 수익률
        mkt_ret: (n_days,) 시장 수익률
        window: rolling window (기본 63일 = 3개월)
    
    Returns:
        betas: (n_days, n_assets) 자산별 베타
    """
    n_days, n_assets = returns.shape
    betas = np.ones((n_days, n_assets))  # 기본값 1.0
    
    for t in range(window, n_days):
        mkt_slice = mkt_ret[t - window:t]
        mkt_mean = np.mean(mkt_slice)
        mkt_var = 0.0
        for j in range(window):
            mkt_var += (mkt_slice[j] - mkt_mean) ** 2
        mkt_var = mkt_var / window + 1e-12
        
        for i in range(n_assets):
            asset_slice = returns[t - window:t, i]
            
            # NaN 체크
            valid_count = 0
            asset_mean = 0.0
            for j in range(window):
                if not np.isnan(asset_slice[j]):
                    asset_mean += asset_slice[j]
                    valid_count += 1
            
            if valid_count < window // 2:
                betas[t, i] = 1.0
                continue
            
            asset_mean = asset_mean / valid_count
            
            # 공분산 계산
            cov = 0.0
            for j in range(window):
                if not np.isnan(asset_slice[j]):
                    cov += (asset_slice[j] - asset_mean) * (mkt_slice[j] - mkt_mean)
            cov = cov / valid_count
            
            # 베타 = cov / var
            beta = cov / mkt_var
            
            # 합리적 범위로 클리핑 (-0.5 ~ 3.0)
            betas[t, i] = max(-0.5, min(3.0, beta))
    
    return betas


# =============================================================================
# 2. v6 정책 설정 (4AI 합의)
# =============================================================================

# 레짐별 슬리브 믹스 (4AI 합의: CASH 완화)
SLEEVE_MIX_V6 = {
    0: {"TREND": 0.15, "TREND_FAST": 0.10, "MR": 0.05, "DEF_LOWVOL": 0.20, "CASH": 0.50},  # CRISIS: 50%
    1: {"TREND": 0.20, "TREND_FAST": 0.15, "MR": 0.10, "DEF_LOWVOL": 0.20, "CASH": 0.35},  # BEAR: 35%
    2: {"TREND": 0.30, "TREND_FAST": 0.15, "MR": 0.15, "DEF_LOWVOL": 0.20, "CASH": 0.20},  # NORMAL: 20%
    3: {"TREND": 0.35, "TREND_FAST": 0.15, "MR": 0.20, "DEF_LOWVOL": 0.20, "CASH": 0.10},  # BULL: 10%
    4: {"TREND": 0.40, "TREND_FAST": 0.20, "MR": 0.20, "DEF_LOWVOL": 0.15, "CASH": 0.05},  # STRONG_BULL: 5%
}

# Circuit Breaker (4AI 합의: 완화)
CB_CONFIG_V6 = {
    "cooldown_days": 20,
    "latch_max_days": 60,
    "early_release_dd": -0.06,
    "early_release_min_days": 10,
    # 3단계(100% reduction)는 기본 OFF. CLI로 켤 때만 활성화됨.
    "enable_full_stop": 0,
    "levels": [
        {"threshold": -0.08, "reduction": 0.30},
        {"threshold": -0.12, "reduction": 0.50},
        {"threshold": -0.18, "reduction": 1.00},  # full stop (disabled by default)
    ]
}

def get_cb_config_v6(engine):
    """
    Effective CB config builder.
    - 기본은 CB_CONFIG_V6(기존 동작 유지)
    - cb_v6_* 인스턴스 속성으로 threshold/reduction/기간을 override 가능
    - enable_full_stop=0이면 reduction>=0.99 레벨은 제거(기본 2단계 유지)
    """
    cfg = deepcopy(CB_CONFIG_V6)

    # enable_full_stop: 기본 0(비활성)
    enable_full_stop = int(getattr(engine, "cb_v6_enable_full_stop", cfg.get("enable_full_stop", 0)))
    levels = list(cfg.get("levels", []))
    if not enable_full_stop:
        levels = [lv for lv in levels if float(lv.get("reduction", 0.0)) < 0.99]

    # top-level overrides
    for k in ("cooldown_days", "latch_max_days", "early_release_dd", "early_release_min_days"):
        v = getattr(engine, f"cb_v6_{k}", None)
        if v is not None:
            cfg[k] = float(v) if "dd" in k else int(v)

    # level overrides (1..3)
    for i, lv in enumerate(levels, start=1):
        t = getattr(engine, f"cb_v6_level{i}_threshold", None)
        r = getattr(engine, f"cb_v6_level{i}_reduction", None)
        if t is not None:
            lv["threshold"] = float(t)
        if r is not None:
            rr = float(r)
            lv["reduction"] = max(0.0, min(1.0, rr))
    # threshold 내림차순 정렬 (가장 깊은 DD부터 체크)
    # -0.18 → -0.12 → -0.08 순서로 체크해야 100% reduction이 먼저 적용됨
    levels.sort(key=lambda x: x.get("threshold", 0))  # 오름차순: -0.18, -0.12, -0.08
    cfg["levels"] = levels
    cfg["enable_full_stop"] = enable_full_stop
    return cfg

# Severity 설정
SEVERITY_CONFIG_V6 = {
    "crisis": {
        "vix_z": 2.0,
        "vol_z": 1.6,
        "dd_20": -0.10,
        "required_count": 2,
        "override_cash": 0.60,
        "latch_days": 20
    },
    "slow_risk": {
        "vix_z": 1.5,
        "vol_z": 1.5,
        "dd_20": -0.08,
        "required_count": 2,
        "override_cash": 0.40,
        "latch_days": 15
    }
}

# Risk Throttle
THROTTLE_CONFIG_V6 = {
    "vix_z_soft": 1.2,
    "vol_z_soft": 1.0,
    "max_cut": 0.50,
    "slope": 0.15,
    "max_turnover": 0.35
}

# Hedge 설정 (4AI 합의)
# Compat Add-on (v6.8.1에서 검증된 실행모듈): no-trade band + (옵션) sqrt impact cost
COMPAT_CONFIG_V6 = {
    "enabled": False,
    "no_trade_band": 0.025,          # per-asset weight delta threshold
    "hedge_no_trade_band": 0.02,     # hedge notional delta threshold
    "enable_impact": False,
    "capital_usd": 100_000_000,      # impact participation 계산용 가정 자본
    "impact_k_bps": 0.02,            # impact_bps = k * sqrt(participation)
    "cap_bps": 4.0,                  # per-asset impact cap
    "participation_cap": 0.07,
    "adv_lookback": 20,
    "min_adv_usd": 10_000_000,
}

HEDGE_CONFIG_V6 = {
    "enabled": False,
    "beta_window": 63,
    "cost_bps": 2.0,
    "max_hedge_ratio": 0.80,
    "min_change_interval": 5,
    "target_beta": {
        0: 0.25,   # CRISIS
        1: 0.45,   # BEAR
        2: 0.70,   # NORMAL
        3: 0.85,   # BULL
        4: 1.00,   # STRONG_BULL (no hedge)
    }
}

# 팩터 가중치
FACTOR_WEIGHTS = {
    'momentum_12_1': 0.25,
    'momentum_6_1': 0.15,
    'momentum_3_1': 0.10,
    'low_volatility': 0.25,
    'mean_reversion': 0.15,
    'quality': 0.10,
}


# =============================================================================
# 3. ICIR 앙상블
# =============================================================================

class ICIREnsemble:
    def __init__(self, lookback: int = 60, min_periods: int = 20):
        self.lookback = lookback
        self.min_periods = min_periods
        self.ic_history: Dict[str, List[float]] = {}
    
    def update_ic(self, factor_scores: Dict[str, np.ndarray], 
                  realized_returns: np.ndarray) -> Dict[str, float]:
        ic_values = {}
        for name, scores in factor_scores.items():
            if name not in self.ic_history:
                self.ic_history[name] = []
            
            valid = ~(np.isnan(scores) | np.isnan(realized_returns))
            if np.sum(valid) < 20:
                ic = 0.0
            else:
                try:
                    ic, _ = spearmanr(scores[valid], realized_returns[valid])
                    if np.isnan(ic):
                        ic = 0.0
                except:
                    ic = 0.0
            
            ic = np.clip(ic, -0.5, 0.5)
            self.ic_history[name].append(ic)
            if len(self.ic_history[name]) > self.lookback * 2:
                self.ic_history[name] = self.ic_history[name][-self.lookback:]
            ic_values[name] = ic
        
        return ic_values
    
    def get_weights(self) -> Dict[str, float]:
        icir = {}
        for name, history in self.ic_history.items():
            if len(history) >= self.min_periods:
                recent = history[-self.lookback:]
                ic_mean = np.mean(recent)
                ic_std = np.std(recent) + 1e-6
                icir[name] = ic_mean / ic_std
            else:
                icir[name] = 0.0
        
        positive = {k: max(0, v) for k, v in icir.items()}
        total = sum(positive.values()) + 1e-6
        
        weights = {}
        for k, v in positive.items():
            w = min(max(v / total, 0.05), 0.40)
            weights[k] = w
        
        total_w = sum(weights.values())
        if total_w > 0:
            weights = {k: v / total_w for k, v in weights.items()}
        else:
            weights = FACTOR_WEIGHTS.copy()
        
        return weights


# =============================================================================
# 4. 다중 팩터 계산기
# =============================================================================

class MultiFactorCalculator:
    def __init__(self, returns: np.ndarray, prices: np.ndarray):
        self.returns = returns
        self.prices = prices
        self.n_dates, self.n_assets = returns.shape
        # ---- precomputed rolling caches (filled lazily) ----
        self._prep_done = False
        self._cum_ret = None
        self._cum_ret_sq = None
        self._trend = None
        self._trend_fast = None
    
    def _ensure_precomputed(self):
        """Precompute arrays used by compute_* so per-t calls become O(1)."""
        if self._prep_done:
            return
        
        # returns: (T, n_assets)
        r = np.nan_to_num(self.returns, nan=0.0)
        # cumulative sums for rolling sum/var
        self._cum_ret = np.cumsum(r, axis=0, dtype=np.float64)
        self._cum_ret_sq = np.cumsum(r * r, axis=0, dtype=np.float64)
        
        # trend/trend_fast are simple ratios; precompute once (vectorized)
        T = self.prices.shape[0]
        self._trend = np.full((T, self.n_assets), np.nan, dtype=np.float64)
        self._trend_fast = np.full((T, self.n_assets), np.nan, dtype=np.float64)
        
        # Vectorized computation
        if T > 252:
            self._trend[252:] = self.prices[252-21:-21] / self.prices[:T-252] - 1.0
        if T > 63:
            self._trend_fast[63:] = self.prices[63-21:-21] / self.prices[:T-63] - 1.0
        
        # MR/LowVol 전체 벡터 precompute (O(T*N) 1회)
        N = self.n_assets
        self._mr_all = np.full((T, N), np.nan, dtype=np.float64)
        self._lowvol_all = np.full((T, N), np.nan, dtype=np.float64)
        
        # Padded cumsum for vectorized rolling: C0[t] = sum_{i<t} r[i]
        C0 = np.vstack([np.zeros((1, N), dtype=np.float64), self._cum_ret])
        C20 = np.vstack([np.zeros((1, N), dtype=np.float64), self._cum_ret_sq])
        
        # MR (window=5): -sum(r[t-5:t]) for t>=5
        mr_w = 5
        if T > mr_w:
            sums = C0[mr_w:T] - C0[0:T-mr_w]
            self._mr_all[mr_w:T] = -sums
        
        # LowVol (window=21): -std(r[t-21:t]) * sqrt(252) for t>=21
        lv_w = 21
        if T > lv_w:
            ann = np.sqrt(252.0)
            sum_r = C0[lv_w:T] - C0[0:T-lv_w]
            sum_sq = C20[lv_w:T] - C20[0:T-lv_w]
            mean = sum_r / float(lv_w)
            var = sum_sq / float(lv_w) - mean * mean
            var = np.maximum(var, 1e-12)
            self._lowvol_all[lv_w:T] = -np.sqrt(var) * ann
        
        self._prep_done = True
        print("[PRECOMPUTE] Rolling caches initialized (MR/LowVol vectorized)")
    
    def _rolling_sum(self, t: int, window: int) -> np.ndarray:
        self._ensure_precomputed()
        if t < window:
            return np.full(self.n_assets, np.nan)
        if t == window:
            return self._cum_ret[t-1].copy()
        return (self._cum_ret[t-1] - self._cum_ret[t-window-1]).copy()
    
    def _rolling_std_annualized(self, t: int, window: int) -> np.ndarray:
        self._ensure_precomputed()
        if t < window:
            return np.full(self.n_assets, np.nan)
        if t == window:
            sum_r = self._cum_ret[t-1]
            sum_sq = self._cum_ret_sq[t-1]
        else:
            sum_r = self._cum_ret[t-1] - self._cum_ret[t-window-1]
            sum_sq = self._cum_ret_sq[t-1] - self._cum_ret_sq[t-window-1]
        mean = sum_r / float(window)
        var = sum_sq / float(window) - mean * mean
        var = np.maximum(var, 1e-12)
        return np.sqrt(var) * np.sqrt(252.0)

    def compute_trend(self, t: int) -> np.ndarray:
        self._ensure_precomputed()
        if t < 252:
            return np.full(self.n_assets, np.nan)
        return self._trend[t].copy()
    
    def compute_trend_fast(self, t: int) -> np.ndarray:
        self._ensure_precomputed()
        if t < 63:
            return np.full(self.n_assets, np.nan)
        return self._trend_fast[t].copy()
    
    def compute_mean_reversion(self, t: int, window: int = 5) -> np.ndarray:
        self._ensure_precomputed()
        if t < window:
            return np.full(self.n_assets, np.nan)
        # 기본 window=5일 때 사전계산 배열에서 O(1) 조회
        if window == 5:
            return self._mr_all[t].copy()
        # 다른 window는 기존 O(1) 경로
        cum_ret = self._rolling_sum(t, window)
        return -cum_ret
    
    def compute_defensive_lowvol(self, t: int, window: int = 21) -> np.ndarray:
        self._ensure_precomputed()
        if t < window:
            return np.full(self.n_assets, np.nan)
        # 기본 window=21일 때 사전계산 배열에서 O(1) 조회
        if window == 21:
            return self._lowvol_all[t].copy()
        # 다른 window는 기존 O(1) 경로
        vol = self._rolling_std_annualized(t, window)
        return -vol
    

    @lru_cache(maxsize=4096)
    def _get_base_sleeve_scores_cached(self, t: int) -> tuple:
        """캐싱된 기본 슬리브 점수"""
        return (
            tuple(self.compute_trend(t).tolist()),
            tuple(self.compute_trend_fast(t).tolist()),
            tuple(self.compute_mean_reversion(t).tolist()),
            tuple(self.compute_defensive_lowvol(t).tolist()),
        )
    
    def _get_base_sleeve_scores(self, t: int) -> Dict[str, np.ndarray]:
        """캐싱된 점수를 dict로 변환"""
        cached = self._get_base_sleeve_scores_cached(t)
        return {
            'TREND': np.array(cached[0]),
            'TREND_FAST': np.array(cached[1]),
            'MR': np.array(cached[2]),
            'DEF_LOWVOL': np.array(cached[3]),
        }

    def compute_sleeve_scores(self, t: int, regime: int) -> Dict[str, np.ndarray]:
        # OPTIMIZED: 캐싱된 기본 점수 사용
        scores = self._get_base_sleeve_scores(t)
        
        # 레짐별 조정 (4AI 합의: MR 제한)
        if regime == 0:  # CRISIS
            # MR은 위기에서 knife-catching 위험
            scores['MR'] = scores['MR'] * 0.3
            scores['DEF_LOWVOL'] = scores['DEF_LOWVOL'] * 1.5
        elif regime == 1:  # BEAR
            scores['MR'] = scores['MR'] * 0.5
            scores['DEF_LOWVOL'] = scores['DEF_LOWVOL'] * 1.3
        elif regime == 4:  # STRONG_BULL
            scores['TREND'] = scores['TREND'] * 1.3
            scores['TREND_FAST'] = scores['TREND_FAST'] * 1.2
            scores['DEF_LOWVOL'] = scores['DEF_LOWVOL'] * 0.7
        
        return scores


# =============================================================================
# 5. v6 프로덕션 백테스터
# =============================================================================


# ========== ENGINE OPTIMIZATION: Data Cache ==========
import pickle
import hashlib
_ENGINE_CACHE_DIR = Path("/tmp/aub_engine_cache")
_ENGINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _data_cache_key(db_path: str, start_date: str) -> str:
    key_str = f"{db_path}|{start_date}"
    return hashlib.md5(key_str.encode()).hexdigest()[:16]

def _get_cached_data(db_path: str, start_date: str):
    key = _data_cache_key(db_path, start_date)
    cache_file = _ENGINE_CACHE_DIR / f"data_{key}.pkl"
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    return None

def _save_cached_data(db_path: str, start_date: str, data: dict):
    key = _data_cache_key(db_path, start_date)
    cache_file = _ENGINE_CACHE_DIR / f"data_{key}.pkl"
    try:
        with open(cache_file, "wb") as f:
            pickle.dump(data, f, protocol=4)
    except Exception:
        pass
# ========== END ENGINE OPTIMIZATION ==========


# ========== 4AI Council: DB Cache Optimization ==========
_DB_CACHE_DIR = Path("/tmp/aub_engine_cache")
_DB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _db_cache_key(db_path: str, start_date: str) -> str:
    """캐시 키 생성"""
    key_str = f"{db_path}|{start_date}"
    return hashlib.md5(key_str.encode()).hexdigest()[:16]

def _load_from_cache(db_path: str, start_date: str):
    """캐시에서 데이터 로드"""
    key = _db_cache_key(db_path, start_date)
    cache_file = _DB_CACHE_DIR / f"data_{key}.pkl"
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                data = pickle.load(f)
            print(f"[CACHE HIT] Loaded from {cache_file.name}")
            return data
        except Exception as e:
            print(f"[CACHE ERROR] {e}")
    return None

def _save_to_cache(db_path: str, start_date: str, data: dict):
    """캐시에 데이터 저장"""
    key = _db_cache_key(db_path, start_date)
    cache_file = _DB_CACHE_DIR / f"data_{key}.pkl"
    try:
        with open(cache_file, "wb") as f:
            pickle.dump(data, f, protocol=4)
        print(f"[CACHE SAVE] Saved to {cache_file.name}")
    except Exception as e:
        print(f"[CACHE ERROR] Failed to save: {e}")
# ========== END 4AI Council Optimization ==========


# ----------------------------
# Ensemble policy (dynamic)
# ----------------------------
def _load_ensemble_policy(path: str) -> dict:
    """Load ensemble policy JSON file."""
    try:
        if not path:
            return {}
        p = Path(path)
        if not p.is_absolute():
            p = Path("/home/ubuntu/AUB") / p
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

class ARESv6Production:
    """
    ARES Phase 2b v6 - 4AI 합의 기반 프로덕션 엔진
    
    핵심 특징:
    - 룩어헤드 차단 불변식
    - Market Hedge (완전 구현)
    - Risk Budget State Machine
    - Deep Logging
    - Add-on 스위치
    """
    
    def __init__(self, db_path: str, 
                 warmup_days: int = 756,
                 purge_days: int = 5,
                 cost_bps: float = 30.0,
                 hedge_cost_bps: float = 2.0,
                 top_k_per_sleeve: int = 6,
                 per_asset_cap: float = 0.10,
                 rebal_period: int = 5,
                 enable_hedge: bool = False,
                 enable_cb: bool = True,
                 enable_severity: bool = True,
                 enable_throttle: bool = True,
                 enable_icir: bool = False,
                 policy_json: str = None,
                 save_daily_logs: str = None,
                 enable_compat: bool = False,
                 enable_no_trade: bool = True,
                 enable_impact: bool = False,
                 enable_macro_gate: bool = False,
                 macro_db_path: str = None,
                 start_date: str = None,
                 enable_ensemble: bool = False,
                 ensemble_policy_path: str = None):
        self.db_path = db_path
        self.warmup_days = warmup_days
        self.purge_days = purge_days
        self.cost_bps = cost_bps
        self.hedge_cost_bps = hedge_cost_bps
        self.top_k = top_k_per_sleeve
        self.per_asset_cap = per_asset_cap
        self.rebal_period = rebal_period
        
        # Add-on 스위치
        self.enable_hedge = enable_hedge
        self.enable_cb = enable_cb
        self.enable_severity = enable_severity
        self.enable_throttle = enable_throttle
        self.enable_icir = enable_icir
        
        # Compat Add-on 스위치
        self.enable_compat = bool(enable_compat)
        self.enable_no_trade = bool(enable_no_trade)
        self.enable_impact = bool(enable_impact)
        self.save_daily_logs = save_daily_logs
        
        # Macro regime gate
        self.enable_macro_gate = bool(enable_macro_gate)
        self.macro_db_path = macro_db_path
        
        # P1: start_date (ProdStart 트랙용)
        self.start_date = start_date[:10] if start_date else None
        
        
        # Dynamic Ensemble (Champion + RS-Mix Specialist)
        self.enable_ensemble = bool(enable_ensemble)
        self.ensemble_policy_path = ensemble_policy_path or "artifacts/ensemble/ensemble_policy.json"
        self._ensemble_policy = _load_ensemble_policy(self.ensemble_policy_path) if self.enable_ensemble else {}
        self._ens_w_spec_prev = 0.0
        self._ens_last_turnover = None
        self._ens_last_cost_pressure = None
        self._ens_last_w_spec_target = None
        self._ens_last_w_spec = None
        self._ens_last_shock_strength = None

        
        # 정책 로드
        self.sleeve_mix = SLEEVE_MIX_V6.copy()
        self.cb_min_exposure_floor = 0.0  # 최소 노출 floor (기본값, 엔진에서 덮어씀)
        self._cb_config = None  # lazy init (CLI override 지원)
        self.severity_config = SEVERITY_CONFIG_V6.copy()
        self.throttle_config = THROTTLE_CONFIG_V6.copy()
        self.hedge_config = HEDGE_CONFIG_V6.copy()
        self.hedge_config["enabled"] = bool(self.enable_hedge)  # fingerprint/설정 일치
        
        # Compat 기본 설정
        self.compat_config = {
            'no_trade_band': 0.025,
            'hedge_no_trade_band': 0.02,
            'enable_impact': False,
            'impact_k_bps': 0.02,
            'cap_bps': 4.0,
            'participation_cap': 0.07,
            'capital_usd': 1e8,
            'adv_lookback': 20,
            'min_adv_usd': 1e7,
        }
        
        self.policy_json_path = policy_json  # scenario_signature용
        if policy_json and os.path.exists(policy_json):
            self._load_policy(policy_json)
        
        self.returns = None
        self.prices = None
        self.vix = None
        self.dates = None
        self.rf_daily = None
        self.mkt_ret = None
        
        self.factor_calc = None
        self.icir_ensemble = ICIREnsemble(lookback=60)
        
        self._load_data()
    
    def _load_policy(self, policy_json: str):
        """외부 정책 파일 로드"""
        with open(policy_json, 'r') as f:
            policy = json.load(f)
        
        if 'SLEEVE_MIX' in policy:
            for k, v in policy['SLEEVE_MIX'].items():
                self.sleeve_mix[int(k)] = v
        
        if 'CB_POLICY' in policy:
            self._cb_config.update(policy['CB_POLICY'])
        
        if 'SEVERITY' in policy:
            self.severity_config.update(policy['SEVERITY'])
        
        if 'THROTTLE' in policy:
            self.throttle_config.update(policy['THROTTLE'])
        
        if 'HEDGE' in policy:
            self.hedge_config.update(policy['HEDGE'])

        if 'COMPAT' in policy:
            self.compat_config.update(policy['COMPAT'])
        if 'IMPACT' in policy:
            # IMPACT는 compat_config 안의 impact 파라미터로 병합
            self.compat_config.update(policy['IMPACT'])
        
        print(f"Loaded policy from {policy_json}")
    
    def _load_data(self):
        print(f"Loading data from {self.db_path}...")
        
        # 4AI Council: 캐시 확인
        cached = _load_from_cache(self.db_path, self.start_date)
        if cached is not None:
            self.dates = cached['dates']
            self.symbols = cached['symbols']
            self.prices = cached['prices']
            self.returns = cached['returns']
            self.mkt_ret = cached['mkt_ret']
            self.vix = cached.get('vix')
            self.volume = cached.get('volume')
            self.dollar_volume = cached.get('dollar_volume')
            self.rf_daily = cached.get('rf_daily')
            self.factor_calc = MultiFactorCalculator(self.returns, self.prices)
            print(f"Loaded: {len(self.dates)} days, {len(self.symbols)} assets (from cache)")
            return
        
        conn = sqlite3.connect(self.db_path, timeout=60)
        
        try:
            df = pd.read_sql_query("""
                SELECT date, symbol, close, volume
                FROM daily_ohlcv
                WHERE symbol IN (
                    SELECT symbol FROM daily_ohlcv
                    GROUP BY symbol HAVING COUNT(*) > 1000
                )
                ORDER BY date, symbol
            """, conn)
        except Exception:
            df = pd.read_sql_query("""
                SELECT date, symbol, close
                FROM daily_ohlcv
                WHERE symbol IN (
                    SELECT symbol FROM daily_ohlcv
                    GROUP BY symbol HAVING COUNT(*) > 1000
                )
                ORDER BY date, symbol
            """, conn)
            df['volume'] = np.nan
        
        df['date'] = pd.to_datetime(df['date'], format='mixed')
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        prices_df = df.pivot(index='date', columns='symbol', values='close').sort_index()
        
        coverage = prices_df.notna().sum(axis=1)
        min_assets = 35
        
        for date, cov in coverage.items():
            if cov >= min_assets:
                prices_df = prices_df.loc[date:]
                break
        
        data_ratio = prices_df.notna().mean()
        keep = data_ratio[data_ratio >= 0.5].index.tolist()
        if len(keep) < min_assets:
            keep = data_ratio.nlargest(min_assets).index.tolist()
        
        prices_df = prices_df[keep]
        prices_df = prices_df.ffill(limit=5)
        
        returns_df = prices_df.pct_change()
        
        self.dates = returns_df.index[1:].tolist()
        self.symbols = prices_df.columns.tolist()  # symbols 먼저 설정
        self.prices = prices_df.values[1:].astype(np.float64)
        self.returns = np.nan_to_num(returns_df.values[1:].astype(np.float64), nan=0.0)

        # volume(impact용) 정렬: close와 동일 pivot을 사용
        try:
            vol_df = df.pivot(index='date', columns='symbol', values='volume').sort_index()
            vol_df = vol_df.loc[prices_df.index]
            vol_df = vol_df[self.symbols]  # symbols로 정렬
        except Exception:
            vol_df = None
        if vol_df is not None:
            self.volume = np.nan_to_num(vol_df.values[1:].astype(np.float64), nan=0.0)
            self.dollar_volume = self.volume * np.maximum(self.prices, 1.0)
        else:
            self.volume = None
            self.dollar_volume = None
        
        # 시장 수익률 (동일가중)
        self.mkt_ret = np.nanmean(self.returns, axis=1)
        
        try:
            vix_df = pd.read_sql_query("SELECT date, close as vix FROM vix ORDER BY date", conn)
            vix_df['date'] = pd.to_datetime(vix_df['date'], format='mixed')
            vix_df = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
            self.vix = vix_df['vix'].reindex(returns_df.index[1:]).ffill().fillna(20).values.astype(np.float64)
        except:
            self.vix = np.full(len(self.returns), 20.0)
        
        self.rf_daily = np.full(len(self.returns), 0.05 / 252)
        
        conn.close()
        
        self.factor_calc = MultiFactorCalculator(self.returns, self.prices)
        
        # Macro regime 데이터 로드 (2015~ 존재)
        self.macro_regime = None
        if self.enable_macro_gate and self.macro_db_path:
            try:
                macro_conn = sqlite3.connect(self.macro_db_path, timeout=60)
                macro_df = pd.read_sql_query("""
                    SELECT date, regime_label, risk_off_score
                    FROM macro_regime_daily
                    ORDER BY date
                """, macro_conn)
                macro_conn.close()
                
                macro_df['date'] = pd.to_datetime(macro_df['date'], format='mixed')
                macro_df = macro_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
                
                # returns의 dates에 맞춰 정렬
                dates_idx = pd.DatetimeIndex(self.dates)
                self.macro_regime = macro_df.reindex(dates_idx)
                print(f"Macro regime loaded: {self.macro_regime['regime_label'].notna().sum()} days with data")
            except Exception as e:
                print(f"Warning: Failed to load macro_regime: {e}")
                self.macro_regime = None
        
        # 4AI Council: 캐시 저장
        _save_to_cache(self.db_path, self.start_date, {
            'dates': self.dates,
            'symbols': self.symbols,
            'prices': self.prices,
            'returns': self.returns,
            'mkt_ret': self.mkt_ret,
            'vix': self.vix,
            'volume': getattr(self, 'volume', None),
            'dollar_volume': getattr(self, 'dollar_volume', None),
            'rf_daily': getattr(self, 'rf_daily', None),
        })
        
        print(f"Loaded: {len(self.returns)} days, {self.returns.shape[1]} assets")
        print(f"Date range: {self.dates[0]} ~ {self.dates[-1]}")
    
    def _adv_usd_at(self, t_feat: int) -> np.ndarray:
        """t-1 기준 ADV($) 추정. volume 미존재 시 큰 값으로 반환하여 impact≈0."""
        n_assets = self.returns.shape[1]
        if self.dollar_volume is None:
            return np.full(n_assets, 1e18, dtype=np.float64)
        look = int(self.compat_config.get('adv_lookback', 20))
        s = max(0, t_feat - look + 1)
        window = self.dollar_volume[s:t_feat+1, :]
        adv = np.nanmean(window, axis=0)
        adv = np.nan_to_num(adv, nan=1e18, posinf=1e18, neginf=1e18)
        adv = np.maximum(adv, float(self.compat_config.get('min_adv_usd', 1e7)))
        return adv

    def _compute_regime_features(self) -> Dict[str, np.ndarray]:
        port_ret = np.nanmean(self.returns, axis=1)
        port_ret_clean = np.nan_to_num(port_ret, nan=0.0)
        
        vix_clean = np.nan_to_num(self.vix, nan=np.nanmean(self.vix))
        vix_z = fast_zscore(vix_clean, 252, 100)
        vix_z = np.nan_to_num(vix_z, nan=0.0)
        
        vol_21 = fast_rolling_std(port_ret_clean, 21) * np.sqrt(252)
        vol_21 = np.nan_to_num(vol_21, nan=0.15)
        vol_z = fast_zscore(vol_21, 252, 100)
        vol_z = np.nan_to_num(vol_z, nan=0.0)
        
        mom_126 = pd.Series(port_ret_clean).rolling(126, min_periods=20).sum().values
        mom_126 = np.nan_to_num(mom_126, nan=0.0)
        mom_z = fast_zscore(mom_126, 252, 100)
        mom_z = np.nan_to_num(mom_z, nan=0.0)
        
        dd_20 = np.zeros(len(port_ret))
        for i in range(20, len(port_ret)):
            dd_20[i] = fast_max_drawdown(port_ret_clean[i-20:i])
        
        # v6.1: 자산별 베타 계산 (보유종목 베타 합 방식)
        asset_betas = compute_asset_betas(
            self.returns, 
            self.mkt_ret, 
            self.hedge_config.get('beta_window', 63)
        )
        
        # === RateShock features (auto-patched) ===
        try:
            _cfg = RateShockFeatureConfig(db_path=getattr(self, 'db_path', '/home/ubuntu/ares_x_v11_0.db'))
            _start = self.dates[0] - pd.Timedelta(days=60) if hasattr(self, 'dates') and len(self.dates) > 0 else pd.Timestamp('2000-01-01')
            _end = self.dates[-1] if hasattr(self, 'dates') and len(self.dates) > 0 else pd.Timestamp('2025-12-31')
            _y2 = load_y2_series(_start, _end, cfg=_cfg)
            _td = pd.to_datetime(self.dates) if hasattr(self, 'dates') else pd.date_range('2000-01-01', periods=len(vix_z), freq='B')
            _d2y_df = compute_d2y_features(_y2, trading_dates=_td, cfg=_cfg)
            d2y_5d_bp = _d2y_df['d2y_5d_bp'].values
            d2y_20d_bp = _d2y_df['d2y_20d_bp'].values
        except Exception as _e:
            print(f"[WARNING] Failed to compute d2y features: {_e}")
            d2y_5d_bp = np.zeros(len(vix_z))
            d2y_20d_bp = np.zeros(len(vix_z))
        # ========================================
        
        return {
            'vix_z': vix_z, 
            'vol_z': vol_z, 
            'mom_z': mom_z, 
            'dd_20': dd_20,
            'asset_betas': asset_betas,  # v6.1: (n_days, n_assets)
            'd2y_5d_bp': d2y_5d_bp,  # RateShock feature
            'd2y_20d_bp': d2y_20d_bp,  # RateShock feature
            'VIX': vix_clean,  # FSA용 VIX 원본 값
            'DGS2': d2y_5d_bp / 100.0,  # FSA용 DGS2 (bp -> %)
        }
    
    def _get_feature_value(self, features: Dict, key: str, t: int) -> float:
        idx = t - 1
        if idx < 0:
            return 0.0
        val = features[key][idx]
        return val if not np.isnan(val) else 0.0
    
    def _get_asset_betas(self, features: Dict, t: int) -> np.ndarray:
        """v6.1: t-1 시점의 자산별 베타 반환"""
        idx = max(0, t - 1)
        return features['asset_betas'][idx]
    
    def _detect_regime(self, features: Dict, t: int, prev_regime: int) -> int:
        vix_z = self._get_feature_value(features, 'vix_z', t)
        vol_z = self._get_feature_value(features, 'vol_z', t)
        mom_z = self._get_feature_value(features, 'mom_z', t)
        dd_20 = self._get_feature_value(features, 'dd_20', t)
        
        score = compute_regime_score(vix_z, vol_z, mom_z, dd_20)
        regime = score_to_regime(score, prev_regime, hysteresis=0.08)

        # Hard clamp
        if dd_20 < -0.12 or vix_z > 2.0 or vol_z > 2.0:
            return 0  # CRISIS
        if dd_20 < -0.08 or vix_z > 1.4 or vol_z > 1.6:
            regime = min(regime, 1)  # BEAR 이하
        elif dd_20 < -0.05 or vix_z > 1.2 or vol_z > 1.2:
            regime = min(regime, 2)  # NORMAL 이하

        return regime
    
    def _check_severity(self, features: Dict, t: int, regime: int, 
                        severity_state: Dict) -> Tuple[bool, float, Dict]:
        """Severity 체크 with latch"""
        vix_z = self._get_feature_value(features, 'vix_z', t)
        vol_z = self._get_feature_value(features, 'vol_z', t)
        dd_20 = self._get_feature_value(features, 'dd_20', t)
        
        triggered = False
        override_cash = 0.0
        
        # Latch 체크
        if severity_state.get('latch_until', 0) > t:
            return True, severity_state.get('override_cash', 0.0), severity_state
        
        # CRISIS severity
        if regime == 0:
            cfg = self.severity_config['crisis']
            count = 0
            if vix_z > cfg['vix_z']: count += 1
            if vol_z > cfg['vol_z']: count += 1
            if dd_20 < cfg['dd_20']: count += 1
            
            if count >= cfg['required_count']:
                triggered = True
                override_cash = cfg['override_cash']
                severity_state = {
                    'latch_until': t + cfg['latch_days'],
                    'override_cash': override_cash
                }
        
        # SLOW_RISK severity
        elif regime == 2:
            cfg = self.severity_config['slow_risk']
            count = 0
            if vix_z > cfg['vix_z']: count += 1
            if vol_z > cfg['vol_z']: count += 1
            if dd_20 < cfg['dd_20']: count += 1
            
            if count >= cfg['required_count']:
                triggered = True
                override_cash = cfg['override_cash']
                severity_state = {
                    'latch_until': t + cfg['latch_days'],
                    'override_cash': override_cash
                }
        
        return triggered, override_cash, severity_state
    
    def _apply_circuit_breaker(self, features: Dict, t: int, 
                                base_weight: float, cb_state: Dict) -> Tuple[float, Dict]:
        """Circuit Breaker with cooldown and latch"""
        if not self.enable_cb:
            return base_weight, cb_state
        
        # Lazy init cb_config (CLI override 지원)
        if self._cb_config is None:
            self._cb_config = get_cb_config_v6(self)
        
        dd_20 = self._get_feature_value(features, 'dd_20', t)
        
        # Latch 체크 (조기 해제 가능)
        if cb_state.get('latch_until', 0) > t:
            # 조기 해제 조건
            if (dd_20 > self._cb_config['early_release_dd'] and 
                t - cb_state.get('trigger_day', 0) >= self._cb_config['early_release_min_days']):
                cb_state = {'cooldown_until': t + 5}  # 짧은 쿨다운 후 해제
            else:
                reduced = base_weight * (1 - cb_state.get('reduction', 0))
                return max(reduced, self.cb_min_exposure_floor), cb_state
        
        # 쿨다운 체크
        if cb_state.get('cooldown_until', 0) > t:
            return base_weight, cb_state
        
        # CB 레벨 체크
        for level in self._cb_config['levels']:
            if dd_20 < level['threshold']:
                cb_state = {
                    'trigger_day': t,
                    'latch_until': t + self._cb_config['latch_max_days'],
                    'cooldown_until': t + self._cb_config['cooldown_days'],
                    'reduction': level['reduction']
                }
                reduced = base_weight * (1 - level['reduction'])
                return max(reduced, self.cb_min_exposure_floor), cb_state
        
        return base_weight, cb_state
    
    def _apply_throttle(self, features: Dict, t: int, invest_weight: float) -> float:
        """Risk Throttle"""
        if not self.enable_throttle:
            return invest_weight
        
        vix_z = self._get_feature_value(features, 'vix_z', t)
        vol_z = self._get_feature_value(features, 'vol_z', t)
        
        vix_excess = max(0.0, vix_z - self.throttle_config['vix_z_soft'])
        vol_excess = max(0.0, vol_z - self.throttle_config['vol_z_soft'])
        
        extra_cut = min(
            self.throttle_config['max_cut'], 
            self.throttle_config['slope'] * (vix_excess + vol_excess)
        )
        
        return invest_weight * (1.0 - extra_cut)
    
    def _compute_hedge(self, features: Dict, t: int, regime: int, 
                       weights: np.ndarray, hedge_state: Dict) -> Tuple[float, Dict]:
        """
        v6.1: Market Hedge 계산 (자산별 베타 합 방식)
        
        변경사항:
        - port_beta = Σ w_i * beta_i (보유종목 베타 합)
        - 레짐 게이팅: BULL/STRONG_BULL에서 헤지 OFF
        """
        if not self.enable_hedge:
            return 0.0, hedge_state
        
        # v6.1: 레짐 게이팅 - BULL/STRONG_BULL에서는 헤지 OFF
        # NOTE: hedge_state를 통째로 교체하면 prev_hedge/last_change_day가 유실되어 비용/게이팅이 왜곡될 수 있음.
        if regime >= 3:
            hedge_state = dict(hedge_state)
            hedge_state['hedge_ratio'] = 0.0
            hedge_state['regime_gated'] = True
            hedge_state['target_beta'] = 1.0
            hedge_state['port_beta'] = 1.0
            # last_change_day는 업데이트하지 않음(하락장 진입 시 헤지 지연 방지)
            return 0.0, hedge_state
        
        # v6.1: 자산별 베타로 포트폴리오 베타 계산
        asset_betas = self._get_asset_betas(features, t)
        
        # portfolio_beta = Σ w_i * beta_i
        valid = ~(np.isnan(weights) | np.isnan(asset_betas))
        if np.sum(valid) > 0:
            port_beta = np.sum(weights[valid] * asset_betas[valid])
            port_beta = max(0.0, min(3.0, port_beta))
        else:
            port_beta = 1.0
        
        target_beta = self.hedge_config['target_beta'].get(regime, 1.0)
        
        # 현재 익스포저
        current_exposure = np.sum(np.abs(weights))
        if current_exposure < 0.01:
            return 0.0, hedge_state
        
        # v6.1: 헤지 notional 계산 (excess beta notional)
        # hedge_notional = max(0, port_beta_notional - target_beta * exposure)
        excess_beta = port_beta - target_beta * current_exposure
        if excess_beta > 0:
            hedge_ratio = excess_beta
            hedge_ratio = min(hedge_ratio, self.hedge_config['max_hedge_ratio'] * current_exposure)
        else:
            hedge_ratio = 0.0

        # ===== v6.3: Trend-aware Hedge Budget (Claude + GPT 통합) =====
        # 목적:
        # - Fold 7/8처럼 '변동성 높지만 시장이 버티거나 반등'하는 구간에서 과헤지로 알파를 깎는 문제 제거
        # - Fold 16처럼 '진짜 하락장'에서는 헤지를 더 강하게/더 오래 유지(레짐 혼합 희석 완화)

        idx = t - 1
        mom_z = float(features.get('mom_z')[idx]) if (idx >= 0 and 'mom_z' in features) else 0.0
        dd_20 = float(features.get('dd_20')[idx]) if (idx >= 0 and 'dd_20' in features) else 0.0
        vix_z = float(features.get('vix_z')[idx]) if (idx >= 0 and 'vix_z' in features) else 0.0
        vol_z = float(features.get('vol_z')[idx]) if (idx >= 0 and 'vol_z' in features) else 0.0

        # "회복/반등"과 "진짜 하락"을 구분하는 방향성 필터
        # - fold8 같은 '급락 후 반등(DD는 아직 -이지만 개선 중)'을 잡기 위해 dd_slope를 추가
        dd_prev = float(features.get('dd_20')[idx-5]) if (idx >= 5 and 'dd_20' in features) else dd_20
        dd_slope_5 = dd_20 - dd_prev  # (+)면 최근 5일간 DD 개선
        rebound = (dd_slope_5 > 0.015) and (mom_z > -0.05)

        recovery = ((mom_z > 0.20) and (dd_20 > -0.10)) or ((mom_z > 0.10) and rebound)
        downtrend = (mom_z < -0.05) or (dd_20 < -0.08)

        # (A) 레짐별 cap/floor (notional, total capital 기준)
        # - SLOW_RISK: 기본은 거의 헤지 안 함. 단, 하락 추세일 때만 소량 허용
        cap_slow = (0.00 if recovery else (0.08 if downtrend else 0.02)) * current_exposure

        # - BEAR: 하락장에선 최소/최대 범위를 부여. 회복 구간이면 cap만 작게(=short가 오히려 손실)
        floor_bear = max(0.25 * current_exposure, 0.15)
        cap_bear = 0.40 * current_exposure

        # - CRISIS: 절대 floor(현금 비중 커져도 최소 헤지 유지) + 회복 구간이면 floor 해제
        floor_crisis = max(0.40 * current_exposure, 0.20)
        cap_crisis = self.hedge_config['max_hedge_ratio'] * current_exposure

        # (B) 회복/반등 구간에서의 과헤지 제거(특히 Fold 7/8 대응)
        if recovery and (vix_z < 3.2 and vol_z < 3.2):
            if regime in [0, 1, 2]:
                # risk-off로 분류돼도 반등이면 헤지는 최소화
                if regime == 2:
                    cap_slow = 0.00
                if regime == 1:
                    floor_bear = 0.0
                    cap_bear = 0.15 * current_exposure
                if regime == 0:
                    # CRISIS는 '진짜 위기'일 때 헤지가 유효한 경우가 많다.
                    # 강한 회복 시그널에서만 floor를 해제하여 Fold16 같은 rebound-hedge 손실을 줄인다.
                    strong_recovery = ((mom_z > 0.60) and (dd_20 > -0.05)) or (rebound and dd_20 > -0.12)
                    if strong_recovery:
                        floor_crisis = 0.0
                        cap_crisis = 0.15 * current_exposure


        # (C) 적용
        if regime == 2:
            hedge_ratio = min(hedge_ratio, cap_slow)
        elif regime == 1:
            if floor_bear > 0:
                hedge_ratio = max(hedge_ratio, floor_bear)
            hedge_ratio = min(hedge_ratio, cap_bear)
        elif regime == 0:
            if floor_crisis > 0:
                hedge_ratio = max(hedge_ratio, floor_crisis)
            hedge_ratio = min(hedge_ratio, cap_crisis)


        # 최소 변경 간격 체크(중요):
        # - 헤지를 "늘리는" 방향은 즉시 허용(위기 진입 시 지연 방지)
        # - 헤지를 "줄이는" 방향만 interval로 제한
        prev_ratio = float(hedge_state.get('hedge_ratio', 0.0))
        last_day = int(hedge_state.get('last_change_day', 0))
        min_int = int(self.hedge_config['min_change_interval'])

        if (last_day + min_int > t) and (hedge_ratio <= prev_ratio + 1e-12) and (not recovery):
            return prev_ratio, hedge_state

        # 헤지 비율 변경 (state를 보존/갱신)
        new_state = dict(hedge_state)
        if abs(hedge_ratio - prev_ratio) > 1e-12:
            new_state['last_change_day'] = t
        new_state.update({
            'hedge_ratio': float(hedge_ratio),
            'target_beta': float(target_beta),
            'port_beta': float(port_beta),
            'regime_gated': False
        })

        return float(hedge_ratio), new_state
    
    def _build_portfolio(self, t: int, regime: int, features: Dict,
                         severity_state: Dict, cb_state: Dict) -> Tuple[np.ndarray, float, Dict, Dict]:
        """포트폴리오 구성"""
        n_assets = self.returns.shape[1]
        
        mix = self.sleeve_mix[regime].copy()
        
        # ------------------------------------------------------------
        # RS Active Sleeve Mix Adjustment (minimal, production-safe)
        # - Only when RS active AND rate_shock_mix_enable=1
        # - Multiply sleeve weights and renormalize among sleeves (cash unchanged)
        # ------------------------------------------------------------
        shock_on_for_mix = False
        if getattr(self, "enable_rate_shock_gate", 0) and getattr(self, "_rate_shock_signals", None) is not None:
            sig = getattr(self, "_rate_shock_signals", None)
            try:
                active = sig.get("active")
                if active is not None and hasattr(active, "iloc") and t < len(active):
                    shock_on_for_mix = bool(active.iloc[t])
                # Cache RS today values for ensemble shock_strength calculation
                thr_20d = sig.get("thr_20d_bp")
                shock_20d = sig.get("shock_20d_bp")
                if thr_20d is not None and hasattr(thr_20d, "iloc") and t < len(thr_20d):
                    self._rs_thr20_bp_today = float(thr_20d.iloc[t]) if pd.notna(thr_20d.iloc[t]) else None
                if shock_20d is not None and hasattr(shock_20d, "iloc") and t < len(shock_20d):
                    self._rs_shock20_bp_today = float(shock_20d.iloc[t]) if pd.notna(shock_20d.iloc[t]) else None
            except Exception:
                pass
        
        if shock_on_for_mix and int(getattr(self, "rate_shock_mix_enable", 0)) == 1:
            try:
                cash = float(mix.get("CASH", 0.0))
                # multipliers
                m_def = float(getattr(self, "rs_mix_def_mult", 1.0))
                m_mr = float(getattr(self, "rs_mix_mr_mult", 1.0))
                m_tr = float(getattr(self, "rs_mix_trend_mult", 1.0))
                m_tf = float(getattr(self, "rs_mix_trend_fast_mult", 1.0))

                # apply multipliers (only if keys exist)
                if "DEF_LOWVOL" in mix: mix["DEF_LOWVOL"] = float(mix["DEF_LOWVOL"]) * m_def
                if "MR" in mix:         mix["MR"] = float(mix["MR"]) * m_mr
                if "TREND" in mix:      mix["TREND"] = float(mix["TREND"]) * m_tr
                if "TREND_FAST" in mix: mix["TREND_FAST"] = float(mix["TREND_FAST"]) * m_tf

                # renormalize sleeves to (1 - CASH)
                sleeves = ["TREND", "TREND_FAST", "MR", "DEF_LOWVOL"]
                ssum = 0.0
                for k in sleeves:
                    if k in mix and mix[k] is not None:
                        ssum += max(0.0, float(mix[k]))
                target = max(0.0, 1.0 - cash)
                if ssum > 1e-12:
                    for k in sleeves:
                        if k in mix and mix[k] is not None:
                            mix[k] = max(0.0, float(mix[k])) / ssum * target
            except Exception:
                pass
        
        # Macro regime gate: risk-on이면 CB/Severity 완화 (노출 증가)
        macro_risk_on = False
        if self.enable_macro_gate and self.macro_regime is not None:
            # t-1 매크로 레짐 사용 (룩어헤드 방지)
            t_feat = t - 1
            if t_feat >= 0 and t_feat < len(self.macro_regime):
                macro_label = self.macro_regime.iloc[t_feat]['regime_label']
                if pd.notna(macro_label) and macro_label == 'RISK_ON':
                    macro_risk_on = True
        
        # Severity 오버라이드 (macro_risk_on이면 완화)
        if self.enable_severity and not macro_risk_on:
            triggered, override_cash, severity_state = self._check_severity(
                features, t, regime, severity_state
            )
            if triggered and override_cash > mix["CASH"]:
                increase = override_cash - mix["CASH"]
                non_cash_total = 1 - mix["CASH"]
                for sleeve in ["TREND", "TREND_FAST", "MR", "DEF_LOWVOL"]:
                    if non_cash_total > 0:
                        ratio = mix[sleeve] / non_cash_total
                        mix[sleeve] = max(0, mix[sleeve] - increase * ratio)
                mix["CASH"] = override_cash
        
        # Circuit Breaker (macro_risk_on이면 완화)
        invest_total = 1 - mix["CASH"]
        if not macro_risk_on:
            invest_after_cb, cb_state = self._apply_circuit_breaker(
                features, t, invest_total, cb_state
            )
            
            if invest_after_cb < invest_total:
                scale = invest_after_cb / (invest_total + 1e-10)
                for sleeve in ["TREND", "TREND_FAST", "MR", "DEF_LOWVOL"]:
                    mix[sleeve] *= scale
                mix["CASH"] = 1 - invest_after_cb
        else:
            # macro_risk_on일 때 CB 스킵
            invest_after_cb = invest_total

        # Risk Throttle
        invest_now = 1.0 - mix["CASH"]
        invest_throttled = self._apply_throttle(features, t, invest_now)
        
        if invest_throttled < invest_now:
            scale = invest_throttled / (invest_now + 1e-10)
            for sleeve in ["TREND", "TREND_FAST", "MR", "DEF_LOWVOL"]:
                mix[sleeve] *= scale
            mix["CASH"] = 1.0 - invest_throttled

        # 슬리브별 점수
        sleeve_scores = self.factor_calc.compute_sleeve_scores(t, regime)
        
        # ICIR 가중치 적용 (활성화 시)
        if self.enable_icir:
            icir_weights = self.icir_ensemble.get_weights()
            # ICIR 가중치로 슬리브 배분 조정
            sleeve_map = {'TREND': 'TREND', 'TREND_FAST': 'TREND_FAST', 'MR': 'MR', 'DEF_LOWVOL': 'DEF_LOWVOL'}
            for sleeve_name in sleeve_map:
                if sleeve_name in icir_weights:
                    # ICIR이 높으면 해당 슬리브 가중치 증가
                    icir_factor = 0.5 + icir_weights.get(sleeve_name, 0.25) * 2  # 0.5~1.3 범위
                    mix[sleeve_name] *= icir_factor
            # 재정규화
            total_sleeve = sum(mix[s] for s in ["TREND", "TREND_FAST", "MR", "DEF_LOWVOL"])
            if total_sleeve > 0:
                scale = (1 - mix["CASH"]) / total_sleeve
                for s in ["TREND", "TREND_FAST", "MR", "DEF_LOWVOL"]:
                    mix[s] *= scale
        
        weights = np.zeros(n_assets)
        
        for sleeve_name in ["TREND", "TREND_FAST", "MR", "DEF_LOWVOL"]:
            sleeve_weight = mix[sleeve_name]
            if sleeve_weight < 0.01:
                continue
            
            scores = sleeve_scores[sleeve_name]
            scores_clean = np.nan_to_num(scores, nan=-np.inf)
            top_k = min(self.top_k, n_assets)
            
            if top_k == 0:
                continue
            
            scores_clean = np.nan_to_num(scores, nan=-np.inf)
            # faster top-k selection: argpartition (no full sort)
            if top_k <= 0:
                continue
            if top_k >= n_assets:
                top_idx = np.arange(n_assets)
            else:
                idx = np.argpartition(-scores_clean, kth=top_k-1)[:top_k]
                idx = idx[np.isfinite(scores_clean[idx])]
                if idx.size < top_k:
                    top_idx = np.argsort(-scores_clean)[:top_k]
                    top_idx = top_idx[np.isfinite(scores_clean[top_idx])]
                else:
                    top_idx = idx
            
            per_stock_weight = sleeve_weight / float(len(top_idx)) if len(top_idx) > 0 else 0.0
            weights[top_idx] += per_stock_weight
        
        total = np.sum(weights)
        if total > 0:
            weights = weights / total * (1 - mix["CASH"])

        # 종목별 상한
        weights = np.clip(weights, 0.0, self.per_asset_cap)
        ssum = np.sum(weights)
        if ssum > 0:
            weights = weights / ssum * (1 - mix["CASH"])

        # ------------------------------------------------------------
        # Dynamic Ensemble: Champion + RS-Mix Specialist
        # - Only when enable_ensemble=1 AND RS active
        # - Specialist weights = same sleeve_scores but with RS mix multipliers
        # - Dynamic weighting based on shock_strength + ramp + cost/turnover cap
        # ------------------------------------------------------------
        if self.enable_ensemble and isinstance(self._ensemble_policy, dict) and shock_on_for_mix:
            try:
                dyn = self._ensemble_policy.get("dynamic_weighting") or {}
                spec = self._ensemble_policy.get("specialist") or {}
                rs = spec.get("rs") or {}
                mix_enable = int((rs.get("mix_enable") or 0))
                
                if mix_enable == 1:
                    # Build specialist weights using RS mix multipliers
                    mix2 = self.sleeve_mix[regime].copy()
                    mm = (rs.get("mix") or {})
                    cash2 = float(mix2.get("CASH", 0.0))
                    
                    # Apply multipliers
                    if "DEF_LOWVOL" in mix2: mix2["DEF_LOWVOL"] = float(mix2.get("DEF_LOWVOL", 0.0)) * float(mm.get("DEF", 1.0))
                    if "MR" in mix2:         mix2["MR"] = float(mix2.get("MR", 0.0)) * float(mm.get("MR", 1.0))
                    if "TREND" in mix2:      mix2["TREND"] = float(mix2.get("TREND", 0.0)) * float(mm.get("TR", 1.0))
                    if "TREND_FAST" in mix2: mix2["TREND_FAST"] = float(mix2.get("TREND_FAST", 0.0)) * float(mm.get("TF", 1.0))
                    
                    # Renormalize sleeves to (1 - CASH)
                    sleeves = ["TREND", "TREND_FAST", "MR", "DEF_LOWVOL"]
                    ssum = sum(max(0.0, float(mix2.get(k, 0.0))) for k in sleeves)
                    target = max(0.0, 1.0 - cash2)
                    if ssum > 1e-12:
                        for k in sleeves:
                            if k in mix2: mix2[k] = max(0.0, float(mix2.get(k, 0.0))) / ssum * target
                    
                    # Build specialist weights
                    w_spec = np.zeros(n_assets)
                    for sleeve_name in sleeves:
                        sleeve_weight = float(mix2.get(sleeve_name, 0.0))
                        if sleeve_weight < 0.01:
                            continue
                        scores = sleeve_scores[sleeve_name]
                        scores_clean = np.nan_to_num(scores, nan=-np.inf)
                        top_k = min(self.top_k, n_assets)
                        if top_k <= 0:
                            continue
                        if top_k >= n_assets:
                            top_idx = np.arange(n_assets)
                        else:
                            idx = np.argpartition(-scores_clean, kth=top_k-1)[:top_k]
                            idx = idx[np.isfinite(scores_clean[idx])]
                            if idx.size < top_k:
                                top_idx = np.argsort(-scores_clean)[:top_k]
                                top_idx = top_idx[np.isfinite(scores_clean[top_idx])]
                            else:
                                top_idx = idx
                        per_stock_weight = sleeve_weight / float(len(top_idx)) if len(top_idx) > 0 else 0.0
                        w_spec[top_idx] += per_stock_weight
                    
                    total_spec = np.sum(w_spec)
                    if total_spec > 0:
                        w_spec = w_spec / total_spec * (1 - cash2)
                    w_spec = np.clip(w_spec, 0.0, self.per_asset_cap)
                    ssum_spec = np.sum(w_spec)
                    if ssum_spec > 0:
                        w_spec = w_spec / ssum_spec * (1 - cash2)
                    
                    # Compute shock_strength using RS diagnostics today
                    thr20 = getattr(self, "_rs_thr20_bp_today", None)
                    sh20 = getattr(self, "_rs_shock20_bp_today", None)
                    shock_strength = 0.0
                    if thr20 is not None and sh20 is not None and float(thr20) > 1e-9:
                        shock_strength = min(float(dyn.get("shock_strength_cap", 1.0)), abs(float(sh20)) / float(thr20))
                    
                    # Compute target specialist weight
                    wmin = float(dyn.get("spec_weight_min_active", 0.20))
                    wmax = float(dyn.get("spec_weight_max_active", 0.55))
                    w_target = wmin + (wmax - wmin) * min(1.0, shock_strength)
                    
                    # Cost/turnover cap
                    cap_bad = float(dyn.get("cap_when_cost_turnover_bad", 0.25))
                    to_cap_pct = float(dyn.get("turnover_cap_pct", 0.10))
                    cp_cap_pct = float(dyn.get("costpressure_cap_pct", 0.10))
                    if self._ens_last_turnover is not None and self._ens_last_turnover > (1.0 + to_cap_pct):
                        w_target = min(w_target, cap_bad)
                    if self._ens_last_cost_pressure is not None and self._ens_last_cost_pressure > (1.0 + cp_cap_pct):
                        w_target = min(w_target, cap_bad)
                    
                    # Apply ramp (EWMA)
                    lam = float(dyn.get("ramp_lambda", 0.15))
                    w_spec_final = (1.0 - lam) * float(self._ens_w_spec_prev) + lam * float(w_target)
                    w_spec_final = max(0.0, min(0.60, w_spec_final))
                    self._ens_w_spec_prev = w_spec_final
                    
                    # Blend weights
                    weights = (1.0 - w_spec_final) * weights + w_spec_final * w_spec
                    
                    # Store diagnostics
                    self._ens_last_w_spec_target = float(w_target)
                    self._ens_last_w_spec = float(w_spec_final)
                    self._ens_last_shock_strength = float(shock_strength)
            except Exception:
                pass
        
        return weights, mix["CASH"], severity_state, cb_state
    
    def run_single_period(self, start: int, end: int, features: Dict) -> Dict:
        """단일 기간 백테스트 with Deep Logging"""
        n_assets = self.returns.shape[1]
        
        daily_returns = []
        regime_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
        prev_regime = 3
        prev_weights = np.zeros(n_assets)
        
        # State machines
        severity_state = {}
        cb_state = {}
        hedge_state = {}
        
        # Deep logging
        exposure_sum = 0.0
        hedge_sum = 0.0
        pnl_asset_sum = 0.0
        pnl_hedge_sum = 0.0
        pnl_cost_sum = 0.0
        cb_triggers = 0
        severity_triggers = 0
        
        daily_logs = []
        rebal_timestamps = []  # 리밸 시점 기록 (해시용)
        
        for t in range(start, end):
            regime = self._detect_regime(features, t, prev_regime)
            regime_counts[regime] += 1
            
            if (t - start) % self.rebal_period == 0:
                rebal_timestamps.append(t)  # 리밸 시점 기록
                new_weights, cash_weight, severity_state, cb_state = self._build_portfolio(
                    t, regime, features, severity_state, cb_state
                )
                
                # CB/Severity 트리거 카운트
                if cb_state.get('trigger_day') == t:
                    cb_triggers += 1
                if severity_state.get('latch_until', 0) == t + self.severity_config['crisis']['latch_days']:
                    severity_triggers += 1
                
                # Compat: No-trade band (weight delta 기준)
                if self.enable_compat and self.enable_no_trade:
                    band = float(self.compat_config.get('no_trade_band', 0.0))
                    if band > 0:
                        delta = new_weights - prev_weights
                        mask = np.abs(delta) < band
                        delta[mask] = 0.0
                        new_weights = prev_weights + delta
                        # 상한/하한 재적용 + 노출 상한(초과 시 축소)
                        new_weights = np.clip(new_weights, 0.0, self.per_asset_cap)
                        ssum = np.sum(new_weights)
                        # 목표 노출은 '기존 설계상 new_weights 합'에 맞추되, 밴드로 인해 부족하면 현금으로 둔다.
                        target = np.sum(prev_weights) + np.sum(delta)
                        if ssum > target + 1e-12 and ssum > 0:
                            new_weights = new_weights / ssum * target

                turnover = np.sum(np.abs(new_weights - prev_weights))

                # Compat: Impact cost (sqrt participation) — 리밸런싱일에만 적용
                impact_cost = 0.0
                if self.enable_compat and self.enable_impact and turnover > 0:
                    t_feat = t - 1
                    if t_feat >= 0:
                        adv = self._adv_usd_at(t_feat)
                        cap_bps = float(self.compat_config.get('cap_bps', 4.0))
                        k_bps = float(self.compat_config.get('impact_k_bps', 0.02))
                        part_cap = float(self.compat_config.get('participation_cap', 0.07))
                        capital = float(self.compat_config.get('capital_usd', 1e8))

                        delta_w = np.abs(new_weights - prev_weights)
                        trade_notional = delta_w * capital
                        participation = np.minimum(part_cap, trade_notional / (adv + 1e-12))
                        impact_bps = np.minimum(cap_bps, k_bps * np.sqrt(np.maximum(participation, 0.0)))
                        impact_cost = float(np.sum(delta_w * (impact_bps / 10000.0)))

                # Throttle: 턴오버 제한
                if self.enable_throttle and turnover > self.throttle_config['max_turnover']:
                    scale = self.throttle_config['max_turnover'] / turnover
                    new_weights = prev_weights + (new_weights - prev_weights) * scale
                    turnover = self.throttle_config['max_turnover']
                
                trade_cost = turnover * self.cost_bps / 10000
                prev_weights = new_weights
            else:
                trade_cost = 0.0
                turnover = 0.0
                impact_cost = 0.0
            
            # 노출 계산
            current_exposure = np.sum(prev_weights)
            exposure_sum += current_exposure
            
            # v6.1: 헤지 계산 (자산별 베타 합 방식)
            # IMPORTANT: 헤지 비용은 '_compute_hedge' 호출 전 상태를 기준으로 계산해야 함
            prev_hedge_notional = float(hedge_state.get('prev_hedge', hedge_state.get('hedge_ratio', 0.0)))

            hedge_notional, hedge_state = self._compute_hedge(
                features, t, regime, prev_weights, hedge_state  # weights 전달
            )

            # Compat: hedge no-trade band (미세 헤지 변경 억제)
            if self.enable_compat and self.enable_no_trade:
                hband = float(self.compat_config.get('hedge_no_trade_band', 0.0))
                if hband > 0 and abs(hedge_notional - prev_hedge_notional) < hband:
                    hedge_notional = prev_hedge_notional

            hedge_sum += hedge_notional

            # 헤지 비용
            hedge_cost = abs(hedge_notional - prev_hedge_notional) * self.hedge_cost_bps / 10000
            hedge_state['prev_hedge'] = hedge_notional
            
            # PnL 분해
            asset_ret = np.sum(prev_weights * self.returns[t])
            cash_ret = (1.0 - current_exposure) * self.rf_daily[t]
            hedge_ret = -hedge_notional * self.mkt_ret[t]  # 숟 포지션
            
            pnl_asset_sum += asset_ret
            pnl_hedge_sum += hedge_ret
            pnl_cost_sum += trade_cost + hedge_cost + impact_cost
            
            # ICIR 업데이트 (활성화 시)
            if self.enable_icir and t > 0:
                # 슬리브별 팩터 점수와 실현 수익률의 IC 계산
                factor_scores = self.factor_calc.compute_sleeve_scores(t-1, prev_regime)
                realized = self.returns[t]  # 다음 날 수익률
                self.icir_ensemble.update_ic(factor_scores, realized)
            
            port_ret = asset_ret + cash_ret + hedge_ret - trade_cost - hedge_cost - impact_cost
            daily_returns.append(port_ret)
            
            # Daily log
            if self.save_daily_logs:
                daily_logs.append({
                    't': t,
                    'date': str(self.dates[t]) if t < len(self.dates) else None,
                    'regime': regime,
                    'exposure': current_exposure,
                    'hedge': hedge_notional,
                    'pnl_asset': asset_ret,
                    'pnl_hedge': hedge_ret,
                    'pnl_cost': trade_cost + hedge_cost + impact_cost,
                    'impact_cost': impact_cost,
                    'pnl_total': port_ret,
                    'vix_z': self._get_feature_value(features, 'vix_z', t),
                    'vol_z': self._get_feature_value(features, 'vol_z', t),
                    'dd_20': self._get_feature_value(features, 'dd_20', t),
                })
            
            prev_regime = regime
        
        daily_returns = np.array(daily_returns)
        total_return = np.prod(1 + daily_returns) - 1
        vol = np.std(daily_returns) * np.sqrt(252)
        sharpe, _shdiag = safe_sharpe_stats(daily_returns, ann=252.0, min_n=30, eps_std=1e-6, cap_abs_sharpe=20.0)
        mdd = fast_max_drawdown(daily_returns)
        
        n_days = end - start
        avg_exposure = exposure_sum / n_days if n_days > 0 else 0
        avg_hedge = hedge_sum / n_days if n_days > 0 else 0
        
        return {
            'sharpe': sharpe,
            'return': total_return,
            'volatility': vol,
            'mdd': mdd,
            'regime_counts': regime_counts,
            'n_days': n_days,
            'rebal_count': len(rebal_timestamps),
            'rebal_ts_hash': hashlib.sha1(','.join(str(t) for t in rebal_timestamps[:50]).encode()).hexdigest()[:12],
            'diagnostics': {
                'avg_exposure': avg_exposure,
                'avg_hedge': avg_hedge,
                'pnl_asset_sum': pnl_asset_sum,
                'pnl_hedge_sum': pnl_hedge_sum,
                'pnl_cost_sum': pnl_cost_sum,
                'cb_triggers': cb_triggers,
                'severity_triggers': severity_triggers,
                'sharpe_guard': _shdiag,
                'degenerate_sharpe': _shdiag.get('degenerate', False) if isinstance(_shdiag, dict) else False,
                # Ensemble diagnostics (dynamic alpha + turnover cap)
                'ens_w_spec_target': getattr(self, '_ens_last_w_spec_target', None),
                'ens_w_spec': getattr(self, '_ens_last_w_spec', None),
                'ens_turnover_est': getattr(self, '_ens_last_turnover', None),
                'ens_turnover_cap': getattr(self, '_ens_last_turnover_cap', None),
                'ens_shock_strength': getattr(self, '_ens_last_shock_strength', None),
            },
            'inactive_reason': (
                'zero_variance' if (isinstance(_shdiag, dict) and _shdiag.get('degenerate', False)) else
                'zero_exposure' if avg_exposure < 0.01 else
                'ok'
            ),
            'daily_logs': daily_logs if self.save_daily_logs else None
        }
    
    def run_walk_forward(self, n_folds: int = 20) -> Dict:
        n_days = len(self.returns)
        effective_days = n_days - self.warmup_days
        fold_size = effective_days // n_folds
        
        # Scenario signature 생성
        flags = {
            'hedge': self.enable_hedge,
            'cb': self.enable_cb,
            'severity': self.enable_severity,
            'throttle': self.enable_throttle,
            'icir': self.enable_icir,
            'compat': self.enable_compat,
            'no_trade': self.enable_no_trade,
            'impact': self.enable_impact,
            'macro_gate': self.enable_macro_gate,
        }
        scenario_sig = scenario_signature(flags, getattr(self, 'policy_json_path', None), self.start_date)
        
        print(f"\n{'='*70}")
        print(f"ARES Phase 2b v6.6.1 - 헤지 베타 수정 버전")
        print(f"{'='*70}")
        print(f"v6.1 핵심 변경:")
        print(f"  - 헤지 베타: 자산별 beta_i 합 (보유종목 베타)")
        print(f"  - 레짐 게이팅: BULL/STRONG_BULL에서 헤지 OFF")
        print(f"  - scenario_signature: {scenario_sig.get('signature', '')}")
        print(f"Add-on 설정:")
        print(f"  - Hedge: {'ON' if self.enable_hedge else 'OFF'}")
        print(f"  - CB: {'ON' if self.enable_cb else 'OFF'}")
        print(f"  - Severity: {'ON' if self.enable_severity else 'OFF'}")
        print(f"  - Throttle: {'ON' if self.enable_throttle else 'OFF'}")
        print(f"  - ICIR: {'ON' if self.enable_icir else 'OFF'}")
        print(f"  - Compat: {'ON' if self.enable_compat else 'OFF'}")
        print(f"  - NoTrade: {'ON' if self.enable_no_trade else 'OFF'}")
        print(f"  - Impact: {'ON' if self.enable_impact else 'OFF'}")
        print(f"  - MacroGate: {'ON' if self.enable_macro_gate else 'OFF'}")
        print(f"Total {n_folds} folds, {fold_size} days each")
        print(f"{'='*70}")
        
        features = self._compute_regime_features()
        import os as _os
        if _os.getenv('AUB_DEBUG', '0') == '1':
            print(f'[DEBUG] run_walk_forward features_keys={list(features.keys())}')
        
        results = []
        worst_folds = []
        
        for fold in range(n_folds):
            is_start = 0
            is_end = self.warmup_days + fold * fold_size
            
            oos_start = is_end + self.purge_days
            oos_end = min(oos_start + fold_size, n_days)
            
            if oos_end <= oos_start:
                continue
            
            oos_start_date = self.dates[oos_start] if oos_start < len(self.dates) else None
            oos_end_date = self.dates[oos_end - 1] if oos_end - 1 < len(self.dates) else None
            
            # P1: start_date 이전 fold 스킵
            if self.start_date and oos_start_date:
                if str(oos_start_date)[:10] < self.start_date:
                    continue
            

            
            is_result = self.run_single_period(
                max(0, is_end - fold_size * 2), is_end, features
            )
            
            oos_result = self.run_single_period(oos_start, oos_end, features)
            
            oos_diag = oos_result.get('diagnostics', {})
            
            fold_result = {
                'fold': fold + 1,
                'scenario_signature': scenario_sig.get('signature', ''),
                'applied_addons': flags,

                'is_sharpe': is_result['sharpe'],
                'is_sharpe_status': is_result.get('sharpe_status', 'ok'),
                'is_inactive_reason': is_result.get('inactive_reason', ''),
                'oos_sharpe': oos_result['sharpe'],
                'oos_sharpe_status': oos_result.get('sharpe_status', 'ok'),
                'oos_inactive_reason': oos_result.get('inactive_reason', ''),
                'oos_return': oos_result['return'],
                'oos_mdd': oos_result['mdd'],
                'regime_counts': oos_result['regime_counts'],
                'is_start_date': str(self.dates[max(0, is_end - fold_size * 2)]) if max(0, is_end - fold_size * 2) < len(self.dates) else None,
                'is_end_date': str(self.dates[is_end - 1]) if is_end - 1 < len(self.dates) else None,
                'is_start_idx': int(max(0, is_end - fold_size * 2)),
                'is_end_idx': int(is_end),
                'oos_start_date': str(oos_start_date) if oos_start_date else None,
                'oos_end_date': str(oos_end_date) if oos_end_date else None,
                'rebal_count': oos_result.get('rebal_count', -1),
                'rebal_ts_hash': oos_result.get('rebal_ts_hash', 'N/A'),
                'diagnostics': oos_diag
            }
            
            results.append(fold_result)
            
            # Worst fold 추적
            if oos_result['sharpe'] < 0:
                worst_folds.append((fold + 1, oos_result['sharpe'], oos_result.get('daily_logs')))
            
            print(f"Fold {fold+1:2d}: IS={is_result['sharpe']:6.3f}, "
                  f"OOS={oos_result['sharpe']:6.3f}, "
                  f"Exp={oos_diag.get('avg_exposure', 0)*100:5.1f}%, "
                  f"Hdg={oos_diag.get('avg_hedge', 0)*100:5.1f}%, "
                  f"CB={oos_diag.get('cb_triggers', 0)}, "
                  f"Sev={oos_diag.get('severity_triggers', 0)} "
                  f"[{str(oos_start_date)[:10] if oos_start_date else 'N/A'}]")
        
        # 요약
        oos_sharpes = [r['oos_sharpe'] for r in results]
        is_sharpes = [r['is_sharpe'] for r in results]
        
        # Degenerate fold 분리 (avg_exposure < 0.01 또는 degenerate_sharpe=True)
        degenerate_folds = []
        valid_oos_sharpes = []
        valid_is_sharpes = []
        for i, r in enumerate(results):
            diag = r.get('diagnostics', {})
            avg_exp = diag.get('avg_exposure', 1.0)
            is_degenerate = diag.get('degenerate_sharpe', False) or avg_exp < 0.01
            if is_degenerate:
                degenerate_folds.append(i + 1)  # 1-indexed fold number
            else:
                valid_oos_sharpes.append(r['oos_sharpe'])
                valid_is_sharpes.append(r['is_sharpe'])
        
        # 유효한 폴드가 없으면 전체 사용
        if not valid_oos_sharpes:
            valid_oos_sharpes = oos_sharpes
            valid_is_sharpes = is_sharpes
        
        summary = {
            'oos_sharpe_mean': np.mean(valid_oos_sharpes),
            'oos_sharpe_std': np.std(valid_oos_sharpes),
            'oos_sharpe_min': np.min(valid_oos_sharpes),
            'oos_sharpe_max': np.max(valid_oos_sharpes),
            'positive_folds': sum(1 for s in valid_oos_sharpes if s > 0),
            'total_folds': len(valid_oos_sharpes),
            'is_sharpe_mean': np.mean(valid_is_sharpes),
            'oos_is_ratio': np.mean(valid_oos_sharpes) / (np.mean(valid_is_sharpes) + 1e-10),
            'degenerate_folds': degenerate_folds,
            'valid_folds_count': len(valid_oos_sharpes),
            'total_folds_original': len(results),
        }
        
        print(f"\n{'='*70}")
        print("Summary:")
        print(f"  OOS Sharpe Mean: {summary['oos_sharpe_mean']:.4f}")
        print(f"  OOS Sharpe Min:  {summary['oos_sharpe_min']:.4f}")
        print(f"  OOS Sharpe Max:  {summary['oos_sharpe_max']:.4f}")
        print(f"  Positive Folds:  {summary['positive_folds']}/{summary['total_folds']} "
              f"({summary['positive_folds']/summary['total_folds']*100:.1f}%)")
        print(f"  OOS/IS Ratio:    {summary['oos_is_ratio']:.4f}")
        
        # 4AI 기준
        print(f"\n{'='*70}")
        print("4AI 기준 검증:")
        
        # Valid folds 하한 체크 (degenerate가 너무 많으면 INCONCLUSIVE)
        min_valid_folds = 14
        is_inconclusive = summary['valid_folds_count'] < min_valid_folds
        
        criteria = {
            'oos_sharpe_mean_ge_1.5': summary['oos_sharpe_mean'] >= 1.5,
            'positive_folds_ge_80%': summary['positive_folds'] / summary['total_folds'] >= 0.8,
            'oos_sharpe_min_ge_-0.3': summary['oos_sharpe_min'] >= -0.3,
            'oos_is_ratio_ge_0.7': summary['oos_is_ratio'] >= 0.7,
            'valid_folds_ge_14': summary['valid_folds_count'] >= min_valid_folds,
        }
        
        for name, passed in criteria.items():
            status = '✅ PASS' if passed else '❌ FAIL'
            print(f"  {name}: {status}")
        
        all_passed = all(criteria.values())
        
        # INCONCLUSIVE 판정
        if is_inconclusive:
            print(f"\n⚠️ INCONCLUSIVE: Valid folds ({summary['valid_folds_count']}) < {min_valid_folds}")
            print(f"   Degenerate folds: {degenerate_folds}")
        print(f"\n4AI 기준: {'✅ ALL PASSED' if all_passed else '⚠️ SOME FAILED'}")
        print(f"{'='*70}")
        
        # Config fingerprint
        config_fingerprint = {
            'version': 'v6.6.1',
            'hedge_method': 'asset_beta_sum',  # v6.1 핵심 변경
            'regime_gating': True,  # v6.2 + hedge caps/floor  # BULL/STRONG_BULL 헤지 OFF
            'enable_hedge': self.enable_hedge,
            'enable_cb': self.enable_cb,
            'enable_severity': self.enable_severity,
            'enable_throttle': self.enable_throttle,
            'enable_icir': self.enable_icir,
            'warmup_days': self.warmup_days,
            'purge_days': self.purge_days,
            'cost_bps': self.cost_bps,
            'hedge_cost_bps': self.hedge_cost_bps,
            'top_k_per_sleeve': self.top_k,
            'per_asset_cap': self.per_asset_cap,
            'rebal_period': self.rebal_period,
            'cb_config': self._cb_config,
            'hedge_config': self.hedge_config,
        }
        
        result_hash = wf_result_hash(results)

        return {
            'scenario': scenario_sig,
            'result_hash': result_hash,
            'folds': results,
            'summary': summary,
            'criteria': criteria,
            'all_passed': all_passed,
            'config_fingerprint': config_fingerprint,
            'worst_folds': [(f, s) for f, s, _ in sorted(worst_folds, key=lambda x: x[1])[:3]]
        }


def main():
    parser = argparse.ArgumentParser(description='ARES Phase 2b v6 - 4AI 합의 기반 프로덕션 엔진')
    parser.add_argument('--db', type=str, required=True)
    parser.add_argument('--n_folds', type=int, default=20)
    parser.add_argument('--warmup', type=int, default=756)
    parser.add_argument('--purge', type=int, default=5)
    parser.add_argument('--cost_bps', type=float, default=30.0)
    parser.add_argument('--hedge_cost_bps', type=float, default=2.0)
    parser.add_argument('--top_k', type=int, default=6)
    parser.add_argument('--per_asset_cap', type=float, default=0.10)
    parser.add_argument('--rebal_period', type=int, default=5)
    
    # Add-on 스위치
    parser.add_argument('--enable_hedge', type=int, default=0)
    parser.add_argument('--enable_compat', type=int, default=0)
    parser.add_argument('--enable_no_trade', type=int, default=1)
    parser.add_argument('--enable_impact', type=int, default=0)
    parser.add_argument('--enable_cb', type=int, default=1)
    parser.add_argument('--enable_severity', type=int, default=1)
    parser.add_argument('--enable_throttle', type=int, default=1)
    parser.add_argument('--enable_icir', type=int, default=0)
    parser.add_argument('--enable_macro_gate', type=int, default=0)
    parser.add_argument('--macro_db', type=str, default=None, help='Path to macro regime DB (ares_x_v11_0.db)')
    
    # 정책 파일
    parser.add_argument('--policy_json', type=str, default=None)
    
    # 로깅
    parser.add_argument('--save_daily_logs', type=str, default=None,
                        help='Save daily logs: "all" or "worst3"')
    parser.add_argument('--out', type=str, default='./phase2b_v6_runs')
    parser.add_argument('--output', type=str, default='phase2b_v6_results.json')
    
    # P1: start_date 파라미터 (ProdStart 트랙용)
    parser.add_argument('--start_date', type=str, default=None,
                        help='Start date for production track (YYYY-MM-DD). Folds before this date are skipped.')
    
    # Dynamic Ensemble (Champion + RS-Mix Specialist)
    parser.add_argument('--enable_ensemble', type=int, default=0,
                        help='동적 앙상블 활성화 (1=on)')
    parser.add_argument('--ensemble_policy_path', type=str, 
                        default='artifacts/ensemble/ensemble_policy.json',
                        help='앙상블 정책 파일 경로')
    
    args = parser.parse_args()
    
    print(f"\n🚀 ARES Phase 2b v6.6.1 - 헤지 베타 수정 버전")
    print(f"💻 Using {os.cpu_count()} CPU cores")
    
    start_time = time.time()
    
    backtester = ARESv6Production(
        args.db,
        warmup_days=args.warmup,
        purge_days=args.purge,
        cost_bps=args.cost_bps,
        hedge_cost_bps=args.hedge_cost_bps,
        top_k_per_sleeve=args.top_k,
        per_asset_cap=args.per_asset_cap,
        rebal_period=args.rebal_period,
        enable_hedge=bool(args.enable_hedge),
        enable_cb=bool(args.enable_cb),
        enable_severity=bool(args.enable_severity),
        enable_throttle=bool(args.enable_throttle),
        enable_icir=bool(args.enable_icir),
        policy_json=args.policy_json,
        save_daily_logs=args.save_daily_logs,
        enable_compat=bool(args.enable_compat),
        enable_no_trade=bool(args.enable_no_trade),
        enable_impact=bool(args.enable_impact),
        enable_macro_gate=bool(args.enable_macro_gate),
        macro_db_path=args.macro_db,
        start_date=args.start_date,
        enable_ensemble=bool(args.enable_ensemble),
        ensemble_policy_path=args.ensemble_policy_path
    )
    
    results = backtester.run_walk_forward(n_folds=args.n_folds)
    
    elapsed = time.time() - start_time
    print(f"\n⏱️ Execution time: {elapsed:.1f}s")
    
    # 결과 저장
    os.makedirs(args.out, exist_ok=True)
    output_path = os.path.join(args.out, args.output)
    
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\nResults saved: {output_path}")


if __name__ == "__main__":
    main()
