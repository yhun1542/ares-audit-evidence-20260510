#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v661_addon Engine (v3) — ICIR_v4 로직 수정 + MVO 최적화
=============================================================
v2 대비 변경사항:
1. ICIR_v4: 가중치가 실제로 포트폴리오에 반영되도록 수정
   - MVO OFF일 때도 ICIR 가중치로 sleeve별 점수 재조합
   - 자산별 최종 점수 = sum(sleeve_score * icir_weight)
   
2. MVO 최적화:
   - window: 60 → 120 (더 안정적인 공분산 추정)
   - ridge: 1e-3 → 1e-2 (정규화 강화)
   - 적용 조건: regime >= 2 (NEUTRAL 이상에서만)
   - 변동성 필터: vol_z < 1.5일 때만 적용
   - blend_ratio: MVO 100% 적용 대신 기존 가중치와 블렌딩

3. 진단 로그 강화:
   - ICIR 적용 여부 및 효과 추적
   - MVO 스킵 사유 기록

Author: AUB Automation System
Date: 2025-12-27
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from sklearn.covariance import LedoitWolf
except Exception:
    LedoitWolf = None

from ares_phase2b_v661_production_hotfix_compat import ARESv6Production as CoreEngine  # type: ignore


# =============================================================================
# helpers
# =============================================================================
def _sha12_file(path: str) -> str:
    if (not path) or (not os.path.exists(path)):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def scenario_signature(flags: Dict[str, Any], policy_path: Optional[str]) -> Dict[str, Any]:
    sig = {"flags": {k: bool(v) for k, v in (flags or {}).items()}, "policy_sha": _sha12_file(policy_path or "")}
    raw = json.dumps(sig, sort_keys=True, ensure_ascii=False).encode("utf-8")
    sig["signature"] = hashlib.sha256(raw).hexdigest()[:12]
    return sig


def mean_dict_weights(records: List[Dict[str, float]]) -> Dict[str, float]:
    if not records:
        return {}
    keys = sorted({k for r in records for k in r.keys()})
    out = {}
    for k in keys:
        vals = [float(r.get(k, 0.0)) for r in records if k in r]
        out[k] = float(sum(vals) / max(1, len(vals)))
    return out


# =============================================================================
# MacroGate (band multiplier only)
# =============================================================================
class MacroGate:
    def __init__(self,
                 enabled: bool,
                 db_path: str,
                 table: str = "macro_regime_daily",
                 risk_on_values: Optional[List[str]] = None,
                 risk_off_values: Optional[List[str]] = None,
                 band_mult_risk_on: float = 0.85,
                 band_mult_risk_off: float = 1.15):
        self.enabled = bool(enabled)
        self.db_path = db_path
        self.table = table
        self.risk_on_values = [x.upper() for x in (risk_on_values or ["RISK_ON", "ON", "BULL", "EXPANSION"])]
        self.risk_off_values = [x.upper() for x in (risk_off_values or ["RISK_OFF", "OFF", "BEAR", "CRISIS", "RECESSION"])]
        self.band_mult_risk_on = float(band_mult_risk_on)
        self.band_mult_risk_off = float(band_mult_risk_off)
        self._state: Optional[np.ndarray] = None

    def load(self, dates: List[Any]) -> None:
        if not self.enabled:
            self._state = None
            return
        if (not self.db_path) or (not os.path.exists(self.db_path)):
            self._state = None
            return
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            df = pd.read_sql_query(f"SELECT * FROM {self.table} ORDER BY date", conn)
            conn.close()
            if "date" not in df.columns:
                self._state = None
                return
            df["date"] = pd.to_datetime(df["date"], format="mixed")
            df = df.drop_duplicates(subset=["date"], keep="last").set_index("date")

            col = None
            for c in ["risk_state", "macro_regime", "regime", "state", "label", "signal"]:
                if c in df.columns:
                    col = c
                    break
            if col is None:
                for c in df.columns:
                    col = c
                    break
            s = df[col]

            if np.issubdtype(s.dtype, np.number):
                mapped = (s.astype(float) > 0).astype(int)
            else:
                up = s.astype(str).str.upper()
                mapped = pd.Series(-1, index=up.index, dtype=int)
                mapped[up.apply(lambda x: any(v in x for v in self.risk_on_values))] = 1
                mapped[up.apply(lambda x: any(v in x for v in self.risk_off_values))] = 0

            idx = pd.to_datetime(dates)
            aligned = mapped.reindex(idx).ffill()
            self._state = aligned.fillna(-1).astype(int).values
        except Exception:
            self._state = None

    def state_at(self, t: int) -> int:
        if (not self.enabled) or (self._state is None):
            return -1
        j = t - 1
        if j < 0 or j >= len(self._state):
            return -1
        return int(self._state[j])

    def band_multiplier(self, t: int) -> float:
        st = self.state_at(t)
        if st == 1:
            return self.band_mult_risk_on
        if st == 0:
            return self.band_mult_risk_off
        return 1.0


# =============================================================================
# ICIR sleeve tracker (v4-style) - 수정됨
# =============================================================================
class ICIRSleeveTracker:
    def __init__(self, lookback: int = 60, min_periods: int = 20):
        self.lookback = int(lookback)
        self.min_periods = int(min_periods)
        self.hist: Dict[str, List[float]] = {}

    @staticmethod
    def _spearman(x: np.ndarray, y: np.ndarray) -> float:
        mask = np.isfinite(x) & np.isfinite(y)
        if np.sum(mask) < 20:
            return 0.0
        xx = x[mask]
        yy = y[mask]
        rx = xx.argsort().argsort().astype(np.float64)
        ry = yy.argsort().argsort().astype(np.float64)
        rx -= rx.mean()
        ry -= ry.mean()
        denom = (np.sqrt((rx * rx).sum()) * np.sqrt((ry * ry).sum())) + 1e-12
        return float((rx * ry).sum() / denom)

    def update(self, sleeve_scores: Dict[str, np.ndarray], realized_returns: np.ndarray) -> None:
        for k, sc in sleeve_scores.items():
            ic = self._spearman(sc, realized_returns)
            self.hist.setdefault(k, []).append(float(np.clip(ic, -0.5, 0.5)))
            if len(self.hist[k]) > self.lookback * 2:
                self.hist[k] = self.hist[k][-self.lookback:]

    def weights(self, base: Dict[str, float]) -> Dict[str, float]:
        """ICIR 기반 가중치 계산 - 충분한 데이터가 없으면 base 반환"""
        icir = {}
        has_data = False
        
        for k in base.keys():
            h = self.hist.get(k, [])
            if len(h) < self.min_periods:
                icir[k] = 0.0
            else:
                has_data = True
                r = np.asarray(h[-self.lookback:], dtype=np.float64)
                mu = float(np.mean(r))
                sd = float(np.std(r, ddof=1)) + 1e-9
                icir[k] = max(0.0, mu / sd)

        tot = float(sum(icir.values()))
        if tot <= 0 or not has_data:
            # 데이터 부족 시 base 가중치 정규화하여 반환
            s = float(sum(base.values())) + 1e-12
            return {k: float(v) / s for k, v in base.items()}

        w = {}
        for k, v in icir.items():
            ww = float(v / tot)
            # 극단적 가중치 방지: 5% ~ 40% 범위로 제한
            ww = float(min(max(ww, 0.05), 0.40))
            w[k] = ww
        s = float(sum(w.values())) + 1e-12
        return {k: float(v) / s for k, v in w.items()}
    
    def has_sufficient_data(self) -> bool:
        """충분한 데이터가 있는지 확인"""
        if not self.hist:
            return False
        return all(len(h) >= self.min_periods for h in self.hist.values())


# =============================================================================
# MVO (Top-K subset) - 최적화됨
# =============================================================================
class MVO:
    def __init__(self, 
                 enabled: bool, 
                 window: int = 120,  # v3: 60 → 120
                 ridge: float = 1e-2,  # v3: 1e-3 → 1e-2
                 blend_ratio: float = 0.5):  # v3: 새 파라미터
        self.enabled = bool(enabled)
        self.window = int(window)
        self.ridge = float(ridge)
        self.blend_ratio = float(blend_ratio)  # MVO 결과 비중 (나머지는 기존 가중치)

    def optimize(self,
                 trailing_returns: np.ndarray,
                 expected: np.ndarray,
                 invest_total: float,
                 per_asset_cap: float) -> Optional[np.ndarray]:
        if (not self.enabled) or trailing_returns.shape[0] < 20 or trailing_returns.shape[1] < 2:
            return None
        X = np.asarray(trailing_returns, dtype=np.float64)
        mu = np.asarray(expected, dtype=np.float64)

        # 공분산 추정
        if LedoitWolf is not None:
            try:
                cov = LedoitWolf().fit(X).covariance_
            except Exception:
                cov = np.cov(X, rowvar=False)
        else:
            cov = np.cov(X, rowvar=False)
        
        cov = np.asarray(cov, dtype=np.float64) + self.ridge * np.eye(X.shape[1])

        try:
            inv = np.linalg.pinv(cov)
            w = inv @ mu
            w = np.maximum(w, 0.0)
            if np.sum(w) <= 1e-12:
                return None
            w = w / (np.sum(w) + 1e-12) * float(invest_total)
            w = np.clip(w, 0.0, float(per_asset_cap))
            s = float(np.sum(w))
            if s > 1e-12:
                w = w / s * float(invest_total)
            return w
        except Exception:
            return None


# =============================================================================
# v11 regime clamp (defensive only)
# =============================================================================
def v11_regime_clamp(base_regime: int, features: Dict[str, Any], t: int) -> int:
    try:
        vix_z = float(features.get("vix_z", [0.0])[t])
        vol_z = float(features.get("vol_z", [0.0])[t])
        dd_20 = float(features.get("dd_20", [0.0])[t])
    except Exception:
        return int(base_regime)

    if dd_20 < -0.12 or vix_z > 2.2 or vol_z > 2.0:
        return 0
    if dd_20 < -0.08 or vix_z > 1.6 or vol_z > 1.6:
        return min(base_regime, 1)
    if dd_20 < -0.05 or vix_z > 1.2 or vol_z > 1.2:
        return min(base_regime, 2)
    return int(base_regime)


# =============================================================================
# v661_addon engine (v3)
# =============================================================================
class ARESv661Addon(CoreEngine):
    def __init__(self,
                 *args,
                 enable_mvo: bool = False,
                 mvo_window: int = 120,
                 mvo_ridge: float = 1e-2,
                 mvo_blend_ratio: float = 0.5,
                 mvo_min_regime: int = 2,  # v3: NEUTRAL 이상에서만 MVO
                 mvo_max_vol_z: float = 1.5,  # v3: 변동성 필터
                 enable_macro_gate: bool = False,
                 macro_db_path: str = "",
                 macro_band_mult_risk_on: float = 0.85,
                 macro_band_mult_risk_off: float = 1.15,
                 enable_regime_v11: bool = False,
                 regime_v11_mode: str = "min",
                 enable_vix_scaling: bool = False,
                 vix_scaling_config: dict = None,
                 enable_regime15: bool = False,
                 regime15_db_path: str = "",
                 regime15_band_mult_risk_off: float = 0.5,
                 regime15_band_mult_crisis: float = 0.3,
                 enable_icir_v4: bool = False,
                 icir_lookback: int = 60,
                 icir_min_periods: int = 20,
                 icir_blend_ratio: float = 0.7,  # v3: ICIR 가중치 블렌딩 비율
                 **kwargs):
        super().__init__(*args, **kwargs)

        self.enable_mvo = bool(enable_mvo)
        self.enable_macro_gate = bool(enable_macro_gate)
        self.enable_regime_v11 = bool(enable_regime_v11)
        self.regime_v11_mode = str(regime_v11_mode or "min").lower()
        self.enable_icir_v4 = bool(enable_icir_v4) or bool(getattr(self, "enable_icir", False))

        # MVO 파라미터 (v3 최적화)
        self.mvo_min_regime = int(mvo_min_regime)
        self.mvo_max_vol_z = float(mvo_max_vol_z)
        self.mvo_blend_ratio = float(mvo_blend_ratio)
        
        # ICIR 블렌딩 비율
        self.icir_blend_ratio = float(icir_blend_ratio)

        dbp = macro_db_path or ""
        self.macro_gate = MacroGate(
            enabled=self.enable_macro_gate,
            db_path=dbp,
            band_mult_risk_on=float(macro_band_mult_risk_on),
            band_mult_risk_off=float(macro_band_mult_risk_off),
        )
        self.mvo = MVO(
            enabled=self.enable_mvo, 
            window=int(mvo_window), 
            ridge=float(mvo_ridge),
            blend_ratio=float(mvo_blend_ratio)
        )
        self.icir_tracker = ICIRSleeveTracker(lookback=int(icir_lookback), min_periods=int(icir_min_periods))

        try:
            self.macro_gate.load(getattr(self, "dates", []))
        except Exception:
            pass

        # VIX Scaling Engine (lazy import for OFF=baseline identical)
        self.enable_vix_scaling = bool(enable_vix_scaling)
        self.vix_scaler = None
        if self.enable_vix_scaling:
            try:
                from vix_scaling_engine import VIXScalingEngine, create_vix_scaling_config_from_dict
                vix_cfg = create_vix_scaling_config_from_dict(vix_scaling_config or {})
                self.vix_scaler = VIXScalingEngine(vix_cfg)
            except ImportError:
                self.enable_vix_scaling = False
                self.vix_scaler = None

        # Regime15 Adapter (lazy import for OFF=baseline identical)
        self.enable_regime15 = bool(enable_regime15)
        self.regime15_adapter = None
        if self.enable_regime15:
            try:
                from regime15_adapter_db import create_regime15_adapter
                self.regime15_adapter = create_regime15_adapter(
                    enabled=True,
                    db_path=regime15_db_path or getattr(self, 'db_path', '/home/ubuntu/ares_x_v11_0.db'),
                    band_mult_risk_off=float(regime15_band_mult_risk_off),
                    band_mult_crisis=float(regime15_band_mult_crisis),
                )
            except ImportError:
                self.enable_regime15 = False
                self.regime15_adapter = None

        self._addon_last: Dict[str, Any] = {}
        self._current_vol_z: float = 0.0  # 현재 변동성 z-score

    def _detect_regime(self, features: Dict, t: int, prev_regime: int) -> int:
        base = super()._detect_regime(features, t, prev_regime)
        if not self.enable_regime_v11:
            return base
        v11 = v11_regime_clamp(int(base), features, t)
        if self.regime_v11_mode == "override":
            return int(v11)
        return int(min(int(base), int(v11)))

    def _build_portfolio(self, t: int, regime: int, features: Dict,
                         severity_state: Dict, cb_state: Dict):
        """v3: ICIR_v4 및 MVO 로직 수정"""
        weights, cash_w, severity_state, cb_state = super()._build_portfolio(t, regime, features, severity_state, cb_state)

        # 진단용 스냅샷 초기화
        self._addon_last = {
            "mvo_attempted": False, 
            "mvo_applied": False, 
            "mvo_skipped_reason": "",
            "mvo_k": 0, 
            "icir_weights": {},
            "icir_applied": False,
            "icir_effect": 0.0,  # ICIR로 인한 가중치 변화량
        }

        # 현재 변동성 저장 (MVO 조건 체크용)
        try:
            self._current_vol_z = float(features.get("vol_z", [0.0])[t])
        except Exception:
            self._current_vol_z = 0.0

        if (not self.enable_icir_v4) and (not self.enable_mvo):
            return weights, cash_w, severity_state, cb_state

        invest_total = float(np.sum(weights))
        if invest_total <= 1e-12:
            return weights, cash_w, severity_state, cb_state

        # sleeve scores 계산
        try:
            sleeve_scores = self.factor_calc.compute_sleeve_scores(t, regime)
        except Exception:
            sleeve_scores = None

        if sleeve_scores is None:
            return weights, cash_w, severity_state, cb_state

        # ============================================
        # v3: ICIR_v4 - 가중치가 실제로 적용되도록 수정
        # ============================================
        icir_w = None
        base_weights = {"TREND": 0.35, "TREND_FAST": 0.15, "MR": 0.20, "DEF_LOWVOL": 0.30}
        
        if self.enable_icir_v4:
            icir_w = self.icir_tracker.weights(base_weights)
            self._addon_last["icir_weights"] = {k: float(v) for k, v in icir_w.items()}
            
            # ICIR 가중치가 base와 다른지 확인 (실제로 적용되었는지)
            if self.icir_tracker.has_sufficient_data():
                self._addon_last["icir_applied"] = True
                
                # v3 핵심: ICIR 가중치로 자산별 점수 재계산
                # MVO OFF일 때도 ICIR가 포트폴리오에 영향을 미침
                if not self.enable_mvo:
                    weights = self._apply_icir_weights(weights, sleeve_scores, icir_w, base_weights, invest_total)

        # ============================================
        # v3: MVO - 조건부 적용 및 블렌딩
        # ============================================
        if self.enable_mvo and sleeve_scores is not None:
            self._addon_last["mvo_attempted"] = True
            
            # v3: MVO 적용 조건 체크
            skip_reason = self._check_mvo_conditions(regime)
            if skip_reason:
                self._addon_last["mvo_skipped_reason"] = skip_reason
            else:
                idx = np.where(weights > 0)[0]
                self._addon_last["mvo_k"] = int(len(idx))
                
                if len(idx) >= 2:
                    # expected return 계산 (ICIR 가중치 사용)
                    exp = np.zeros(len(idx), dtype=np.float64)
                    for sname in ["TREND", "TREND_FAST", "MR", "DEF_LOWVOL"]:
                        sc = sleeve_scores.get(sname)
                        if sc is None:
                            continue
                        x = np.nan_to_num(sc[idx], nan=-1e9)
                        ranks = x.argsort().argsort().astype(np.float64)
                        ranks = (ranks / max(1, len(ranks) - 1)) - 0.5
                        
                        # ICIR 가중치 적용
                        w_sleeve = 1.0
                        if icir_w is not None and sname in icir_w:
                            w_sleeve = float(icir_w[sname]) * 4.0  # 스케일 조정
                        exp += w_sleeve * ranks

                    w = int(self.mvo.window)
                    end = t - 1
                    s = max(0, end - w)
                    if end > s:
                        R = self.returns[s:end, :][:, idx]
                        if R.shape[0] >= 20:
                            w_opt = self.mvo.optimize(R, exp, invest_total, float(self.per_asset_cap))
                            if w_opt is not None:
                                self._addon_last["mvo_applied"] = True
                                
                                # v3: 블렌딩 - MVO 결과와 기존 가중치 혼합
                                new = np.zeros_like(weights)
                                new[idx] = w_opt
                                
                                blend = self.mvo_blend_ratio
                                weights = blend * new + (1 - blend) * weights
                                
                                # 정규화
                                s_sum = float(np.sum(weights))
                                if s_sum > 1e-12:
                                    weights = weights / s_sum * invest_total

        return weights, cash_w, severity_state, cb_state

    def _apply_icir_weights(self, weights: np.ndarray, sleeve_scores: Dict, 
                            icir_w: Dict[str, float], base_w: Dict[str, float],
                            invest_total: float) -> np.ndarray:
        """v3: ICIR 가중치를 포트폴리오에 직접 적용"""
        idx = np.where(weights > 0)[0]
        if len(idx) < 2:
            return weights
        
        # 각 자산의 종합 점수 계산 (ICIR 가중치 적용)
        combined_scores = np.zeros(len(idx), dtype=np.float64)
        
        for sname in ["TREND", "TREND_FAST", "MR", "DEF_LOWVOL"]:
            sc = sleeve_scores.get(sname)
            if sc is None:
                continue
            x = np.nan_to_num(sc[idx], nan=-1e9)
            ranks = x.argsort().argsort().astype(np.float64)
            ranks = (ranks / max(1, len(ranks) - 1))  # 0~1 정규화
            
            # ICIR 가중치 vs base 가중치 블렌딩
            w_icir = float(icir_w.get(sname, 0.25))
            w_base = float(base_w.get(sname, 0.25))
            w_final = self.icir_blend_ratio * w_icir + (1 - self.icir_blend_ratio) * w_base
            
            combined_scores += w_final * ranks
        
        # 점수 기반 가중치 재조정
        if np.sum(combined_scores) > 1e-12:
            # 점수를 가중치로 변환 (softmax-like)
            combined_scores = np.maximum(combined_scores, 0)
            score_weights = combined_scores / (np.sum(combined_scores) + 1e-12)
            
            # 기존 가중치와 블렌딩
            old_weights = weights[idx] / (np.sum(weights[idx]) + 1e-12)
            new_weights = self.icir_blend_ratio * score_weights + (1 - self.icir_blend_ratio) * old_weights
            
            # 정규화 및 적용
            new_weights = new_weights / (np.sum(new_weights) + 1e-12) * invest_total
            new_weights = np.clip(new_weights, 0, float(self.per_asset_cap))
            new_weights = new_weights / (np.sum(new_weights) + 1e-12) * invest_total
            
            # 효과 측정
            effect = float(np.sum(np.abs(new_weights - weights[idx])))
            self._addon_last["icir_effect"] = effect
            
            result = np.zeros_like(weights)
            result[idx] = new_weights
            return result
        
        return weights

    def _check_mvo_conditions(self, regime: int) -> str:
        """v3: MVO 적용 조건 체크"""
        # 레짐 조건
        if regime < self.mvo_min_regime:
            return f"regime={regime}<{self.mvo_min_regime}"
        
        # 변동성 조건
        if self._current_vol_z > self.mvo_max_vol_z:
            return f"vol_z={self._current_vol_z:.2f}>{self.mvo_max_vol_z}"
        
        return ""  # 조건 통과

    def run_single_period(self, start: int, end: int, features: Dict) -> Dict:
        # ALL OFF => exact baseline
        if (not self.enable_macro_gate) and (not self.enable_icir_v4) and (not self.enable_mvo) and (not self.enable_regime_v11) and (not self.enable_vix_scaling) and (not self.enable_regime15):
            return super().run_single_period(start, end, features)

        n_assets = self.returns.shape[1]
        daily_returns = []
        regime_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
        base_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}

        prev_regime_base = 3
        prev_regime_final = 3

        prev_weights = np.zeros(n_assets)

        severity_state = {}
        cb_state = {}
        hedge_state = {}

        exposure_sum = 0.0
        hedge_sum = 0.0
        pnl_asset_sum = 0.0
        pnl_hedge_sum = 0.0
        pnl_cost_sum = 0.0
        cb_triggers = 0
        severity_triggers = 0
        daily_logs = []

        pending_scores = None

        base_band = float(self.compat_config.get("no_trade_band", 0.0))

        addon = {
            "mvo": {
                "rebalance_calls": 0, "attempted": 0, "applied": 0, 
                "skipped_by_regime": 0, "skipped_by_vol": 0, "skipped_other": 0,
                "applied_k_sum": 0, "applied_k_avg": 0.0,
                "window": int(self.mvo.window), "ridge": float(self.mvo.ridge),
                "blend_ratio": float(self.mvo_blend_ratio),
                "min_regime": int(self.mvo_min_regime), "max_vol_z": float(self.mvo_max_vol_z),
            },
            "icir_v4": {
                "enabled": bool(self.enable_icir_v4), "rebalance_calls": 0, "updates": 0,
                "applied_count": 0, "effect_sum": 0.0, "effect_avg": 0.0,
                "weights_records": [], "weights_last": {}, "weights_mean": {},
                "blend_ratio": float(self.icir_blend_ratio),
            },
            "macro_gate": {
                "enabled": bool(self.enable_macro_gate), "rebalance_calls": 0,
                "risk_on": 0, "risk_off": 0, "unknown": 0,
                "mult_sum": 0.0, "mult_avg": 0.0,
                "band_base": float(base_band), "band_used_sum": 0.0, 
                "band_used_min": None, "band_used_max": None, "band_used_avg": 0.0
            },
            "regime_v11": {
                "enabled": bool(self.enable_regime_v11), "mode": str(self.regime_v11_mode),
                "clamp_count": 0, "base_counts": base_counts, "final_counts": regime_counts
            },
            "vix_scaling": {
                "enabled": bool(self.enable_vix_scaling), "rebalance_calls": 0,
                "scale_sum": 0.0, "scale_min": None, "scale_max": None, "scale_avg": 0.0,
                "state_counts": {}, "clamp_events": 0,
            },
            "regime15": {
                "enabled": bool(self.enable_regime15), "rebalance_calls": 0,
                "regime_changes": 0, "risk_on_count": 0, "risk_off_count": 0,
                "crisis_count": 0, "transition_count": 0,
                "band_mult_sum": 0.0, "band_mult_min": None, "band_mult_max": None,
            },
        }

        for t in range(start, end):
            base_regime = super()._detect_regime(features, t, prev_regime_base)
            prev_regime_base = base_regime
            base_counts[int(base_regime)] += 1

            if self.enable_regime_v11:
                v11 = v11_regime_clamp(int(base_regime), features, t)
                if self.regime_v11_mode == "override":
                    regime = int(v11)
                else:
                    regime = int(min(int(base_regime), int(v11)))
                if regime < int(base_regime):
                    addon["regime_v11"]["clamp_count"] += 1
            else:
                regime = int(base_regime)

            prev_regime_final = regime
            regime_counts[int(regime)] += 1

            trade_cost = 0.0
            turnover = 0.0

            if (t - start) % self.rebal_period == 0:
                new_weights, cash_weight, severity_state, cb_state = self._build_portfolio(t, regime, features, severity_state, cb_state)

                if cb_state.get("trigger_day") == t:
                    cb_triggers += 1
                if severity_state.get("latch_until", 0) == t + self.severity_config["crisis"]["latch_days"]:
                    severity_triggers += 1
                
                # Regime15 band multiplier (apply before VIX Scaling and MacroGate)
                if self.regime15_adapter is not None:
                    try:
                        dt_str = str(self.dates[t])[:10] if hasattr(self, 'dates') and t < len(self.dates) else str(t)
                        r15_result = self.regime15_adapter.pre_rebalance(dt_str)
                        
                        if r15_result.get("enabled", False):
                            r15_mult = float(r15_result.get("band_multiplier", 1.0))
                            r15_regime = r15_result.get("regime", "risk_on")
                            
                            # Update diagnostics
                            addon["regime15"]["rebalance_calls"] += 1
                            addon["regime15"]["band_mult_sum"] += r15_mult
                            addon["regime15"]["band_mult_min"] = r15_mult if addon["regime15"]["band_mult_min"] is None else min(addon["regime15"]["band_mult_min"], r15_mult)
                            addon["regime15"]["band_mult_max"] = r15_mult if addon["regime15"]["band_mult_max"] is None else max(addon["regime15"]["band_mult_max"], r15_mult)
                            
                            if r15_regime == "risk_on":
                                addon["regime15"]["risk_on_count"] += 1
                            elif r15_regime == "risk_off":
                                addon["regime15"]["risk_off_count"] += 1
                            elif r15_regime == "crisis":
                                addon["regime15"]["crisis_count"] += 1
                            else:
                                addon["regime15"]["transition_count"] += 1
                            
                            # Apply position scaling (like VIX Scaling) instead of band multiplier
                            if r15_mult < 1.0:
                                gross_before = float(np.sum(new_weights))
                                new_weights = new_weights * r15_mult
                                gross_after = float(np.sum(new_weights))
                                
                                # Protect against over-reduction
                                if gross_after < 0.1 * gross_before:
                                    new_weights = new_weights / r15_mult * 0.1
                                    gross_after = 0.1 * gross_before
                    except Exception:
                        pass  # Silently skip on error to maintain baseline behavior

                # MacroGate
                
                # VIX Scaling (apply before no_trade_band)
                if self.vix_scaler is not None:
                    try:
                        vix_today = float(self.vix[t]) if hasattr(self, 'vix') and self.vix is not None and t < len(self.vix) else 20.0
                        dt = self.dates[t] if hasattr(self, 'dates') and t < len(self.dates) else t
                        
                        gross_before = float(np.sum(new_weights))
                        vix_scale = self.vix_scaler.get_scale_factor(vix_today, date=dt)
                        new_weights = new_weights * vix_scale
                        gross_after = float(np.sum(new_weights))
                        
                        # Protect against leverage (cap at 1.0)
                        if gross_after > 1.0:
                            new_weights = new_weights / gross_after
                            gross_after = 1.0
                        
                        # Log rebalance
                        self.vix_scaler.log_rebalance(dt, vix_today, vix_scale, gross_before, gross_after)
                        
                        # Update diagnostics
                        addon["vix_scaling"]["rebalance_calls"] += 1
                        addon["vix_scaling"]["scale_sum"] += float(vix_scale)
                        addon["vix_scaling"]["scale_min"] = vix_scale if addon["vix_scaling"]["scale_min"] is None else min(addon["vix_scaling"]["scale_min"], vix_scale)
                        addon["vix_scaling"]["scale_max"] = vix_scale if addon["vix_scaling"]["scale_max"] is None else max(addon["vix_scaling"]["scale_max"], vix_scale)
                        
                        state = self.vix_scaler._state
                        addon["vix_scaling"]["state_counts"][state] = addon["vix_scaling"]["state_counts"].get(state, 0) + 1
                    except Exception as e:
                        pass  # Silently skip on error to maintain baseline behavior

                # MacroGate
                if self.enable_macro_gate and self.enable_compat and self.enable_no_trade:
                    mult = float(self.macro_gate.band_multiplier(t))
                    st = int(self.macro_gate.state_at(t))
                    addon["macro_gate"]["rebalance_calls"] += 1
                    addon["macro_gate"]["mult_sum"] += float(mult)

                    if st == 1:
                        addon["macro_gate"]["risk_on"] += 1
                    elif st == 0:
                        addon["macro_gate"]["risk_off"] += 1
                    else:
                        addon["macro_gate"]["unknown"] += 1

                    band_used = float(base_band) * float(mult)
                    addon["macro_gate"]["band_used_sum"] += band_used
                    addon["macro_gate"]["band_used_min"] = band_used if addon["macro_gate"]["band_used_min"] is None else min(addon["macro_gate"]["band_used_min"], band_used)
                    addon["macro_gate"]["band_used_max"] = band_used if addon["macro_gate"]["band_used_max"] is None else max(addon["macro_gate"]["band_used_max"], band_used)

                    self.compat_config["no_trade_band"] = band_used

                # No-trade band
                if self.enable_compat and self.enable_no_trade:
                    band = float(self.compat_config.get("no_trade_band", 0.0))
                    if band > 0:
                        delta = new_weights - prev_weights
                        mask = np.abs(delta) < band
                        delta[mask] = 0.0
                        new_weights = prev_weights + delta
                        new_weights = np.clip(new_weights, 0.0, self.per_asset_cap)
                        ssum = float(np.sum(new_weights))
                        target = float(np.sum(prev_weights) + np.sum(delta))
                        if ssum > target + 1e-12 and ssum > 0:
                            new_weights = new_weights / ssum * target

                if self.enable_macro_gate:
                    self.compat_config["no_trade_band"] = base_band

                turnover = float(np.sum(np.abs(new_weights - prev_weights)))

                if self.enable_throttle and turnover > float(self.throttle_config.get("max_turnover", 1.0)):
                    max_to = float(self.throttle_config.get("max_turnover"))
                    scale = max_to / (turnover + 1e-12)
                    new_weights = prev_weights + (new_weights - prev_weights) * scale
                    turnover = max_to

                trade_cost = turnover * self.cost_bps / 10000.0
                prev_weights = new_weights

                # ICIR diagnostics
                if self.enable_icir_v4:
                    addon["icir_v4"]["rebalance_calls"] += 1
                    w = self.icir_tracker.weights({"TREND": 0.35, "TREND_FAST": 0.15, "MR": 0.20, "DEF_LOWVOL": 0.30})
                    w = {k: float(v) for k, v in w.items()}
                    addon["icir_v4"]["weights_last"] = w
                    addon["icir_v4"]["weights_records"].append(w)
                    
                    # v3: ICIR 적용 효과 기록
                    if self._addon_last.get("icir_applied", False):
                        addon["icir_v4"]["applied_count"] += 1
                        addon["icir_v4"]["effect_sum"] += float(self._addon_last.get("icir_effect", 0.0))

                    try:
                        sc = self.factor_calc.compute_sleeve_scores(t, regime)
                        pending_scores = {
                            "TREND": np.nan_to_num(sc["TREND"], nan=np.nan),
                            "TREND_FAST": np.nan_to_num(sc["TREND_FAST"], nan=np.nan),
                            "MR": np.nan_to_num(sc["MR"], nan=np.nan),
                            "DEF_LOWVOL": np.nan_to_num(sc["DEF_LOWVOL"], nan=np.nan),
                        }
                    except Exception:
                        pending_scores = None

                # MVO diagnostics
                addon["mvo"]["rebalance_calls"] += 1
                if bool(self._addon_last.get("mvo_attempted", False)):
                    addon["mvo"]["attempted"] += 1
                    
                    skip_reason = self._addon_last.get("mvo_skipped_reason", "")
                    if skip_reason:
                        if "regime" in skip_reason:
                            addon["mvo"]["skipped_by_regime"] += 1
                        elif "vol_z" in skip_reason:
                            addon["mvo"]["skipped_by_vol"] += 1
                        else:
                            addon["mvo"]["skipped_other"] += 1
                    
                if bool(self._addon_last.get("mvo_applied", False)):
                    addon["mvo"]["applied"] += 1
                    k = int(self._addon_last.get("mvo_k", 0))
                    addon["mvo"]["applied_k_sum"] += k

            # hedge
            hedge_notional, hedge_state = self._compute_hedge(features, t, regime, prev_weights, hedge_state)
            hedge_sum += float(hedge_notional)
            prev_hedge = float(hedge_state.get("prev_hedge", 0.0))

            if self.enable_compat and self.enable_no_trade:
                hband = float(self.compat_config.get("hedge_no_trade_band", 0.0))
                if hband > 0 and abs(hedge_notional - prev_hedge) < hband:
                    hedge_notional = prev_hedge

            hedge_cost = abs(hedge_notional - prev_hedge) * self.hedge_cost_bps / 10000.0
            hedge_state["prev_hedge"] = hedge_notional

            # daily pnl
            asset_ret = float(np.sum(prev_weights * self.returns[t, :]))
            current_exposure = float(np.sum(prev_weights))
            exposure_sum += current_exposure

            hedge_ret = -float(hedge_notional) * float(self.mkt_ret[t]) if self.enable_hedge else 0.0
            cash_ret = (1.0 - current_exposure) * float(self.rf_daily[t]) if hasattr(self, "rf_daily") else 0.0
            total_ret = asset_ret + cash_ret + hedge_ret - trade_cost - hedge_cost

            pnl_asset_sum += asset_ret
            pnl_hedge_sum += hedge_ret
            pnl_cost_sum += (trade_cost + hedge_cost)

            daily_returns.append(total_ret)

            # ICIR update
            if self.enable_icir_v4 and pending_scores is not None:
                self.icir_tracker.update(pending_scores, self.returns[t, :])
                addon["icir_v4"]["updates"] += 1
                pending_scores = None

            if self.save_daily_logs:
                daily_logs.append({
                    "t": t,
                    "date": str(self.dates[t]) if hasattr(self, "dates") else "",
                    "regime": int(regime),
                    "base_regime": int(base_regime),
                    "exposure": current_exposure,
                    "hedge": float(hedge_notional),
                    "turnover": float(turnover),
                    "pnl_asset": float(asset_ret),
                    "pnl_hedge": float(hedge_ret),
                    "pnl_cost": float(trade_cost + hedge_cost),
                    "pnl_total": float(total_ret),
                })

        daily_returns = np.asarray(daily_returns, dtype=np.float64)
        if len(daily_returns) == 0:
            return {"sharpe": 0.0, "return": 0.0, "mdd": 0.0, "regime_counts": regime_counts, "diagnostics": {"addon": addon}}

        sh = float((np.mean(daily_returns) * 252.0) / ((np.std(daily_returns) * np.sqrt(252.0)) + 1e-12))
        tot_ret = float(np.prod(1.0 + daily_returns) - 1.0)

        eq = np.cumprod(1.0 + daily_returns)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / (peak + 1e-12)
        mdd = float(np.min(dd))

        n_days = int(len(daily_returns))
        avg_exposure = exposure_sum / max(1, n_days)
        avg_hedge = hedge_sum / max(1, n_days)

        # finalize addon stats
        if addon["mvo"]["applied"] > 0:
            addon["mvo"]["applied_k_avg"] = float(addon["mvo"]["applied_k_sum"]) / float(addon["mvo"]["applied"])
        if addon["macro_gate"]["rebalance_calls"] > 0:
            addon["macro_gate"]["mult_avg"] = float(addon["macro_gate"]["mult_sum"]) / float(addon["macro_gate"]["rebalance_calls"])
            addon["macro_gate"]["band_used_avg"] = float(addon["macro_gate"]["band_used_sum"]) / float(addon["macro_gate"]["rebalance_calls"])
        addon["icir_v4"]["weights_mean"] = mean_dict_weights(addon["icir_v4"]["weights_records"])
        if addon["icir_v4"]["applied_count"] > 0:
            addon["icir_v4"]["effect_avg"] = float(addon["icir_v4"]["effect_sum"]) / float(addon["icir_v4"]["applied_count"])

        return {
            "sharpe": sh,
            "return": tot_ret,
            "mdd": mdd,
            "regime_counts": regime_counts,
            "diagnostics": {
                "avg_exposure": float(avg_exposure),
                "avg_hedge": float(avg_hedge),
                "pnl_asset_sum": float(pnl_asset_sum),
                "pnl_hedge_sum": float(pnl_hedge_sum),
                "pnl_cost_sum": float(pnl_cost_sum),
                "cb_triggers": int(cb_triggers),
                "severity_triggers": int(severity_triggers),
                "addon": addon,
            },
            "daily_logs": daily_logs if self.save_daily_logs else None,
        }

    def run_walk_forward(self, n_folds: int = 20) -> Dict[str, Any]:
        res = super().run_walk_forward(n_folds=n_folds)

        flags_addon = {
            "enable_mvo": bool(self.enable_mvo),
            "enable_macro_gate": bool(self.enable_macro_gate),
            "enable_regime_v11": bool(self.enable_regime_v11),
            "regime_v11_mode": str(self.regime_v11_mode),
            "enable_icir_v4": bool(self.enable_icir_v4),
        }
        pol = getattr(self, "policy_json", None)
        scen = scenario_signature(flags_addon, pol)
        res["scenario_addon"] = scen

        try:
            for f in res.get("folds", []):
                if not isinstance(f, dict):
                    continue
                diag = f.get("diagnostics", {})
                if isinstance(diag, dict):
                    diag.setdefault("addon_flags", flags_addon)
                    diag.setdefault("addon_scenario_sig", scen.get("signature", ""))
                    f["diagnostics"] = diag
        except Exception:
            pass

        # aggregate summary
        summary = {
            "regime_v11_clamp_total": 0, 
            "mvo_applied_total": 0, "mvo_applied_k_avg": 0.0,
            "mvo_skipped_by_regime": 0, "mvo_skipped_by_vol": 0,
            "icir_updates_total": 0, "icir_applied_total": 0, "icir_effect_avg": 0.0,
            "macro_mult_avg": 0.0
        }
        mvo_k_sum = 0
        mvo_applied = 0
        macro_sum = 0.0
        macro_n = 0
        icir_effect_sum = 0.0
        icir_applied = 0
        
        for f in res.get("folds", []):
            diag = (f.get("diagnostics") or {}) if isinstance(f, dict) else {}
            addon = diag.get("addon") if isinstance(diag, dict) else None
            if isinstance(addon, dict):
                rv11 = addon.get("regime_v11", {})
                if isinstance(rv11, dict):
                    summary["regime_v11_clamp_total"] += int(rv11.get("clamp_count", 0))
                mvo = addon.get("mvo", {})
                if isinstance(mvo, dict):
                    mvo_applied += int(mvo.get("applied", 0))
                    mvo_k_sum += int(mvo.get("applied_k_sum", 0))
                    summary["mvo_skipped_by_regime"] += int(mvo.get("skipped_by_regime", 0))
                    summary["mvo_skipped_by_vol"] += int(mvo.get("skipped_by_vol", 0))
                ic = addon.get("icir_v4", {})
                if isinstance(ic, dict):
                    summary["icir_updates_total"] += int(ic.get("updates", 0))
                    icir_applied += int(ic.get("applied_count", 0))
                    icir_effect_sum += float(ic.get("effect_sum", 0.0))
                mg = addon.get("macro_gate", {})
                if isinstance(mg, dict):
                    macro_sum += float(mg.get("mult_sum", 0.0))
                    macro_n += int(mg.get("rebalance_calls", 0))
                    
        if mvo_applied > 0:
            summary["mvo_applied_total"] = int(mvo_applied)
            summary["mvo_applied_k_avg"] = float(mvo_k_sum) / float(mvo_applied)
        if macro_n > 0:
            summary["macro_mult_avg"] = float(macro_sum) / float(macro_n)
        if icir_applied > 0:
            summary["icir_applied_total"] = int(icir_applied)
            summary["icir_effect_avg"] = float(icir_effect_sum) / float(icir_applied)

        res["addons_summary"] = summary
        res.setdefault("config_fingerprint", {})
        if isinstance(res["config_fingerprint"], dict):
            res["config_fingerprint"]["flags_addon"] = flags_addon

        return res


# =============================================================================
# CLI
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="ARES v661_addon wrapper (v3 - ICIR fix + MVO optimization)")

    # core args
    ap.add_argument("--db", required=True)
    ap.add_argument("--warmup_days", type=int, default=756)
    ap.add_argument("--purge_days", type=int, default=5)
    ap.add_argument("--cost_bps", type=float, default=18.0)
    ap.add_argument("--hedge_cost_bps", type=float, default=2.0)
    ap.add_argument("--top_k", type=int, default=10)
    ap.add_argument("--per_asset_cap", type=float, default=0.10)
    ap.add_argument("--rebal_period", type=int, default=5)

    ap.add_argument("--enable_hedge", type=int, default=0)
    ap.add_argument("--enable_cb", type=int, default=1)
    ap.add_argument("--enable_severity", type=int, default=1)
    ap.add_argument("--enable_throttle", type=int, default=1)

    ap.add_argument("--enable_compat", type=int, default=1)
    ap.add_argument("--enable_no_trade", type=int, default=1)
    ap.add_argument("--enable_impact", type=int, default=0)

    ap.add_argument("--enable_icir", type=int, default=0)
    ap.add_argument("--policy_json", type=str, default="")
    ap.add_argument("--save_daily_logs", type=str, default="")

    # addon flags (v3)
    ap.add_argument("--enable_icir_v4", type=int, default=0)
    ap.add_argument("--icir_lookback", type=int, default=60)
    ap.add_argument("--icir_min_periods", type=int, default=20)
    ap.add_argument("--icir_blend_ratio", type=float, default=0.7)
    
    ap.add_argument("--enable_mvo", type=int, default=0)
    ap.add_argument("--mvo_window", type=int, default=120)
    ap.add_argument("--mvo_ridge", type=float, default=1e-2)
    ap.add_argument("--mvo_blend_ratio", type=float, default=0.5)
    ap.add_argument("--mvo_min_regime", type=int, default=2)
    ap.add_argument("--mvo_max_vol_z", type=float, default=1.5)

    ap.add_argument("--enable_macro_gate", type=int, default=0)
    
    ap.add_argument("--enable_vix_sizing", type=int, default=0,
                    help="Enable VIX-based position sizing (0=off, 1=on)")
    ap.add_argument("--vix_scale_low", type=float, default=1.10,
                    help="Scale factor for low VIX (<15)")
    ap.add_argument("--vix_scale_elevated", type=float, default=0.85,
                    help="Scale factor for elevated VIX (20-25)")
    ap.add_argument("--vix_scale_high", type=float, default=0.65,
                    help="Scale factor for high VIX (25-35)")
    ap.add_argument("--vix_scale_extreme", type=float, default=0.40,
                    help="Scale factor for extreme VIX (>35)")
    ap.add_argument("--vix_scaling_json", type=str, default="",
                    help="JSON string for VIX scaling config override")
    ap.add_argument("--enable_regime15", type=int, default=0,
                    help="Enable Regime15 band multiplier (0=OFF, 1=ON)")
    ap.add_argument("--regime15_band_mult_risk_off", type=float, default=0.5,
                    help="Regime15 band multiplier for RISK_OFF state")
    ap.add_argument("--regime15_band_mult_crisis", type=float, default=0.3,
                    help="Regime15 band multiplier for CRISIS state")

    ap.add_argument("--macro_db_path", type=str, default="")
    ap.add_argument("--macro_band_mult_risk_on", type=float, default=0.85)
    ap.add_argument("--macro_band_mult_risk_off", type=float, default=1.15)

    ap.add_argument("--enable_regime_v11", type=int, default=0)
    ap.add_argument("--regime_v11_mode", type=str, default="min")

    # output
    ap.add_argument("--n_folds", type=int, default=20)
    ap.add_argument("--out", type=str, default="./")
    ap.add_argument("--output", type=str, default="results.json")

    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    engine = ARESv661Addon(
        db_path=args.db,
        warmup_days=args.warmup_days,
        purge_days=args.purge_days,
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
        policy_json=args.policy_json if args.policy_json else None,
        save_daily_logs=args.save_daily_logs if args.save_daily_logs else None,
        enable_compat=bool(args.enable_compat),
        enable_no_trade=bool(args.enable_no_trade),
        enable_impact=bool(args.enable_impact),

        enable_icir_v4=bool(args.enable_icir_v4),
        icir_lookback=args.icir_lookback,
        icir_min_periods=args.icir_min_periods,
        icir_blend_ratio=args.icir_blend_ratio,
        
        enable_mvo=bool(args.enable_mvo),
        mvo_window=args.mvo_window,
        mvo_ridge=args.mvo_ridge,
        mvo_blend_ratio=args.mvo_blend_ratio,
        mvo_min_regime=args.mvo_min_regime,
        mvo_max_vol_z=args.mvo_max_vol_z,
        
        enable_macro_gate=bool(args.enable_macro_gate),
        macro_db_path=args.macro_db_path,
        macro_band_mult_risk_on=args.macro_band_mult_risk_on,
        macro_band_mult_risk_off=args.macro_band_mult_risk_off,
        enable_regime_v11=bool(args.enable_regime_v11),
        regime_v11_mode=args.regime_v11_mode,
        enable_vix_scaling=bool(args.enable_vix_sizing),
        vix_scaling_config={
            "enabled": bool(args.enable_vix_sizing),
            "scale_low": float(args.vix_scale_low),
            "scale_elevated": float(args.vix_scale_elevated),
            "scale_high": float(args.vix_scale_high),
            "scale_extreme": float(args.vix_scale_extreme),
        } if args.enable_vix_sizing else None,
        enable_regime15=bool(args.enable_regime15),
        regime15_db_path=args.db,
        regime15_band_mult_risk_off=float(args.regime15_band_mult_risk_off),
        regime15_band_mult_crisis=float(args.regime15_band_mult_crisis),
    )

    res = engine.run_walk_forward(n_folds=args.n_folds)
    outpath = outdir / args.output
    outpath.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("[OK] wrote:", outpath)


if __name__ == "__main__":
    main()
