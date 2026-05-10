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

import importlib.util
import importlib.machinery

# Optional (lazy): torch will be imported only when alpha_orchestrator is enabled
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

def _validate_db_tables(db_path: str, required_tables: list) -> dict:
    """Lightweight SQLite table existence check used by DataOrchestratorAdapter shadow mode."""
    out = {"db_path": db_path, "ok": False, "missing": [], "present": []}
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {r[0] for r in cur.fetchall()}
        conn.close()
        for t in required_tables:
            if t in names:
                out["present"].append(t)
            else:
                out["missing"].append(t)
        out["ok"] = (len(out["missing"]) == 0)
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out

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

enable_alpha_orchestrator: bool = False,
alpha_orchestrator_mode: str = "shadow",
alpha_orchestrator_module_path: str = "",
alpha_orchestrator_ckpt: str = "",
alpha_orchestrator_blend: float = 0.10,
alpha_orchestrator_device: str = "cuda",
enable_pead_overlay: bool = False,
pead_overlay_mode: str = "shadow",
pead_overlay_csv: str = "",
pead_overlay_budget: float = 0.05,
                 enable_alpha_sentinel: bool = False,
                 alpha_sentinel_mode: str = "shadow",
                 alpha_sentinel_module_path: str = "",
                 alpha_sentinel_ckpt: str = "",
                 alpha_sentinel_device: str = "cuda",
                 alpha_sentinel_blend: float = 0.20,
                 enable_data_orchestrator: bool = False,
                 data_orchestrator_mode: str = "shadow",
                 data_orchestrator_module_path: str = "",
                 data_orchestrator_snapshot_dir: str = "",
                 data_orchestrator_strict_fingerprint: bool = True,
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


        # AlphaOrchestrator / PEAD Overlay adapters (OFF=baseline 동일: enable=0이면 import/계산 없음)
        self.enable_alpha_orchestrator = bool(enable_alpha_orchestrator)
        self.alpha_orchestrator_mode = str(alpha_orchestrator_mode or "shadow")
        self.alpha_orchestrator = None
        if self.enable_alpha_orchestrator:
            try:
                self.alpha_orchestrator = AlphaOrchestratorAdapter(
                    enabled=True,
                    mode=self.alpha_orchestrator_mode,
                    module_path=str(alpha_orchestrator_module_path or ""),
                    ckpt_path=str(alpha_orchestrator_ckpt or ""),
                    device=str(alpha_orchestrator_device or "cuda"),
                    blend_weight=float(alpha_orchestrator_blend),
                )
            except Exception:
                self.enable_alpha_orchestrator = False
                self.alpha_orchestrator = None

        self.enable_pead_overlay = bool(enable_pead_overlay)
        self.pead_overlay_mode = str(pead_overlay_mode or "shadow")
        self.pead_overlay = None
        if self.enable_pead_overlay:
            try:
                self.pead_overlay = PEADOverlayAdapter(
                    enabled=True,
                    mode=self.pead_overlay_mode,
                    overlay_csv=str(pead_overlay_csv or ""),
                    budget=float(pead_overlay_budget),
                )
            except Exception:
                self.enable_pead_overlay = False
                self.pead_overlay = None

        # AlphaSentinel adapter
        self.enable_alpha_sentinel = bool(enable_alpha_sentinel)
        self.alpha_sentinel_mode = str(alpha_sentinel_mode or "shadow")
        self.alpha_sentinel = None
        if self.enable_alpha_sentinel:
            try:
                self.alpha_sentinel = AlphaSentinelAdapter(
                    enabled=True,
                    mode=self.alpha_sentinel_mode,
                    module_path=str(alpha_sentinel_module_path or ""),
                    ckpt_path=str(alpha_sentinel_ckpt or ""),
                    device=str(alpha_sentinel_device or "cuda"),
                    blend=float(alpha_sentinel_blend),
                )
            except Exception:
                self.enable_alpha_sentinel = False
                self.alpha_sentinel = None

        # DataOrchestrator adapter (shadow-only here)
        self.enable_data_orchestrator = bool(enable_data_orchestrator)
        self.data_orchestrator_mode = str(data_orchestrator_mode or "shadow")
        self.data_orchestrator = None
        if self.enable_data_orchestrator:
            try:
                self.data_orchestrator = DataOrchestratorAdapter(
                    enabled=True,
                    mode=self.data_orchestrator_mode,
                    module_path=str(data_orchestrator_module_path or ""),
                    snapshot_dir=str(data_orchestrator_snapshot_dir or ""),
                    strict_fingerprint=bool(data_orchestrator_strict_fingerprint),
                )
            except Exception:
                self.enable_data_orchestrator = False
                self.data_orchestrator = None

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
"alpha_orchestrator": {
    "enabled": bool(getattr(self, "enable_alpha_orchestrator", False)),
    "mode": str(getattr(self, "alpha_orchestrator_mode", "shadow")),
    "rebalance_calls": 0,
    "applied": 0,
    "skipped": 0,
    "last_reason": "",
},
"pead_overlay": {
    "enabled": bool(getattr(self, "enable_pead_overlay", False)),
    "mode": str(getattr(self, "pead_overlay_mode", "shadow")),
    "rebalance_calls": 0,
    "applied": 0,
    "skipped": 0,
    "last_reason": "",
},

            "alpha_sentinel": {
                "enabled": bool(getattr(self, "enable_alpha_sentinel", False)),
                "mode": str(getattr(self, "alpha_sentinel_mode", "shadow")),
                "rebalance_calls": 0,
                "applied": 0,
                "skipped": 0,
                "last_reason": "",
            },
            "data_orchestrator": {
                "enabled": bool(getattr(self, "enable_data_orchestrator", False)),
                "mode": str(getattr(self, "data_orchestrator_mode", "shadow")),
                "rebalance_calls": 0,
                "validated": 0,
                "skipped": 0,
                "last_reason": "",
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
                

                # AlphaOrchestrator (shadow/apply) - integrates on weights BEFORE VIX scaling
                if self.alpha_orchestrator is not None:
                    try:
                        addon["alpha_orchestrator"]["rebalance_calls"] += 1
                        lookback = 60
                        if hasattr(self, "returns") and t >= lookback:
                            win = self.returns[t-lookback:t, :].astype(np.float32, copy=False)
                            invest_total = float(np.sum(new_weights))
                            if invest_total > 1e-12:
                                new_weights, diag = self.alpha_orchestrator.apply(
                                    new_weights, win, invest_total=invest_total, per_asset_cap=float(self.per_asset_cap)
                                )
                                self._addon_last["alpha_orchestrator"] = diag
                                if diag.get("applied", False):
                                    addon["alpha_orchestrator"]["applied"] += 1
                                else:
                                    addon["alpha_orchestrator"]["skipped"] += 1
                                    addon["alpha_orchestrator"]["last_reason"] = str(diag.get("reason", diag.get("error","")))
                            else:
                                addon["alpha_orchestrator"]["skipped"] += 1
                                addon["alpha_orchestrator"]["last_reason"] = "invest_total_zero"
                        else:
                            addon["alpha_orchestrator"]["skipped"] += 1
                            addon["alpha_orchestrator"]["last_reason"] = "insufficient_history_or_missing_returns"
                    except Exception as e:
                        addon["alpha_orchestrator"]["skipped"] += 1
                        addon["alpha_orchestrator"]["last_reason"] = f"error:{type(e).__name__}"

                # PEAD Overlay (shadow/apply) - requires symbol list to map CSV scores
                if self.pead_overlay is not None:
                    try:
                        addon["pead_overlay"]["rebalance_calls"] += 1
                        dt = self.dates[t] if hasattr(self, 'dates') and t < len(self.dates) else t
                        symbols = getattr(self, "assets", None) or getattr(self, "tickers", None) or getattr(self, "symbols", None)
                        if symbols is None:
                            addon["pead_overlay"]["skipped"] += 1
                            addon["pead_overlay"]["last_reason"] = "no_symbol_list_in_engine"
                        else:
                            invest_total = float(np.sum(new_weights))
                            if invest_total > 1e-12:
                                new_weights, diag = self.pead_overlay.apply(
                                    dt, new_weights, symbols, invest_total=invest_total, per_asset_cap=float(self.per_asset_cap)
                                )
                                self._addon_last["pead_overlay"] = diag
                                if diag.get("applied", False):
                                    addon["pead_overlay"]["applied"] += 1
                                else:
                                    addon["pead_overlay"]["skipped"] += 1
                                    addon["pead_overlay"]["last_reason"] = str(diag.get("reason",""))
                            else:
                                addon["pead_overlay"]["skipped"] += 1
                                addon["pead_overlay"]["last_reason"] = "invest_total_zero"
                    except Exception as e:
                        addon["pead_overlay"]["skipped"] += 1
                        addon["pead_overlay"]["last_reason"] = f"error:{type(e).__name__}"
                # AlphaSentinel (shadow/apply)
                if self.alpha_sentinel is not None:
                    try:
                        addon["alpha_sentinel"]["rebalance_calls"] += 1
                        dt = self.dates[t] if hasattr(self, 'dates') and t < len(self.dates) else t
                        feat = {
                        "vix": float(self.vix[t]) if hasattr(self, "vix") and self.vix is not None and t < len(self.vix) else 20.0,
                        "vix_z": float(features.get("vix_z", [0.0])[t]) if isinstance(features, dict) else 0.0,
                        "vol_z": float(features.get("vol_z", [0.0])[t]) if isinstance(features, dict) else 0.0,
                        "dd_20": float(features.get("dd_20", [0.0])[t]) if isinstance(features, dict) else 0.0,
                        "dd_60": float(features.get("dd_60", [0.0])[t]) if isinstance(features, dict) else 0.0,
                    }
                        invest_total = float(np.sum(new_weights))
                        new_weights, diag = self.alpha_sentinel.apply(dt, new_weights, feat, invest_total=invest_total)
                        self._addon_last["alpha_sentinel"] = diag
                        if diag.get("applied", False):
                            addon["alpha_sentinel"]["applied"] += 1
                        else:
                            addon["alpha_sentinel"]["skipped"] += 1
                            addon["alpha_sentinel"]["last_reason"] = str(diag.get("reason", diag.get("error","")))
                    except Exception as e:
                        addon["alpha_sentinel"]["skipped"] += 1
                        addon["alpha_sentinel"]["last_reason"] = f"error:{type(e).__name__}"

                # DataOrchestrator (shadow-only)
                if self.data_orchestrator is not None:
                    try:
                        addon["data_orchestrator"]["rebalance_calls"] += 1
                        dt = self.dates[t] if hasattr(self, 'dates') and t < len(self.dates) else t
                        diag = self.data_orchestrator.apply(dt)
                        self._addon_last["data_orchestrator"] = diag
                        if diag.get("validated", False):
                            addon["data_orchestrator"]["validated"] += 1
                        else:
                            addon["data_orchestrator"]["skipped"] += 1
                            addon["data_orchestrator"]["last_reason"] = str(diag.get("reason", diag.get("error","")))
                    except Exception as e:
                        addon["data_orchestrator"]["skipped"] += 1
                        addon["data_orchestrator"]["last_reason"] = f"error:{type(e).__name__}"


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
    ap.add_argument("--enable_alpha_orchestrator", type=int, default=0)
    ap.add_argument("--alpha_orchestrator_mode", type=str, default="shadow", choices=["shadow","apply"])
    ap.add_argument("--alpha_orchestrator_module_path", type=str, default="")
    ap.add_argument("--alpha_orchestrator_ckpt", type=str, default="")
    ap.add_argument("--alpha_orchestrator_blend", type=float, default=0.10)
    ap.add_argument("--alpha_orchestrator_device", type=str, default="cuda")
    ap.add_argument("--enable_pead_overlay", type=int, default=0)
    ap.add_argument("--pead_overlay_mode", type=str, default="shadow", choices=["shadow","apply"])
    # aliases (compat): --enable_pead / --pead_mode
    ap.add_argument("--enable_pead", type=int, default=None, help="Alias of --enable_pead_overlay")
    ap.add_argument("--pead_mode", type=str, default=None, help="Alias of --pead_overlay_mode")

    ap.add_argument("--pead_overlay_csv", type=str, default="")
    ap.add_argument("--pead_overlay_budget", type=float, default=0.05)
    # integration: AlphaSentinel + DataOrchestrator
    ap.add_argument("--enable_alpha_sentinel", type=int, default=0)
    ap.add_argument("--alpha_sentinel_mode", type=str, default="shadow", choices=["shadow","apply"])
    ap.add_argument("--alpha_sentinel_module_path", type=str, default="")
    ap.add_argument("--alpha_sentinel_ckpt", type=str, default="")
    ap.add_argument("--alpha_sentinel_device", type=str, default="cuda")
    ap.add_argument("--alpha_sentinel_blend", type=float, default=0.20)

    ap.add_argument("--enable_data_orchestrator", type=int, default=0)
    ap.add_argument("--data_orchestrator_mode", type=str, default="shadow", choices=["shadow","apply"])
    ap.add_argument("--data_orchestrator_module_path", type=str, default="")
    ap.add_argument("--data_orchestrator_snapshot_dir", type=str, default="")
    ap.add_argument("--data_orchestrator_strict_fingerprint", type=int, default=1)


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
    # alias mapping
    if args.enable_pead is not None:
        args.enable_pead_overlay = args.enable_pead
    if args.pead_mode is not None:
        args.pead_overlay_mode = args.pead_mode


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
        enable_alpha_orchestrator=bool(args.enable_alpha_orchestrator),
        alpha_orchestrator_mode=args.alpha_orchestrator_mode,
        alpha_orchestrator_module_path=args.alpha_orchestrator_module_path,
        alpha_orchestrator_ckpt=args.alpha_orchestrator_ckpt,
        alpha_orchestrator_blend=float(args.alpha_orchestrator_blend),
        alpha_orchestrator_device=args.alpha_orchestrator_device,
        enable_pead_overlay=bool(args.enable_pead_overlay),
        pead_overlay_mode=args.pead_overlay_mode,
        pead_overlay_csv=args.pead_overlay_csv,
        pead_overlay_budget=float(args.pead_overlay_budget),
        enable_alpha_sentinel=bool(args.enable_alpha_sentinel),
        alpha_sentinel_mode=args.alpha_sentinel_mode,
        alpha_sentinel_module_path=args.alpha_sentinel_module_path,
        alpha_sentinel_ckpt=args.alpha_sentinel_ckpt,
        alpha_sentinel_device=args.alpha_sentinel_device,
        alpha_sentinel_blend=float(args.alpha_sentinel_blend),
        enable_data_orchestrator=bool(args.enable_data_orchestrator),
        data_orchestrator_mode=args.data_orchestrator_mode,
        data_orchestrator_module_path=args.data_orchestrator_module_path,
        data_orchestrator_snapshot_dir=args.data_orchestrator_snapshot_dir,
        data_orchestrator_strict_fingerprint=bool(args.data_orchestrator_strict_fingerprint),
    )

    res = engine.run_walk_forward(n_folds=args.n_folds)
    outpath = outdir / args.output
    outpath.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("[OK] wrote:", outpath)



# =============================================================================
# Advanced Integration Adapters (AlphaOrchestrator / PEAD Overlay)
# OFF=baseline 동일: enable 플래그가 0이면 import/계산/JSON 키 추가 없음 (가능한 범위)
# =============================================================================

class AlphaOrchestratorAdapter:
    def __init__(self, enabled: bool, mode: str, module_path: str, ckpt_path: str = "", device: str = "cuda",
                 blend_weight: float = 0.10, horizons=(60,30,15)):
        self.enabled = bool(enabled)
        self.mode = str(mode or "shadow")
        self.module_path = str(module_path or "")
        self.ckpt_path = str(ckpt_path or "")
        self.device = str(device or "cuda")
        self.blend_weight = float(blend_weight)
        self.horizons = tuple(horizons)
        self._orch = None
        self._model_loaded = False
        self._last_error = ""
        self._last_diag = {}

    def _lazy_import(self):
        if not self.module_path:
            raise RuntimeError("alpha_orchestrator module_path empty")
        loader = importlib.machinery.SourceFileLoader("alpha_orchestrator_mod", self.module_path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)  # type: ignore
        return mod

    def _load_model(self, mod):
        # apply mode requires model; shadow mode can run without
        if not self.ckpt_path:
            return None
        # Try TorchScript first; then state_dict with model class if available.
        import torch
        p = self.ckpt_path
        try:
            if p.endswith(".pt") or p.endswith(".pth") or p.endswith(".jit"):
                # Try jit
                try:
                    m = torch.jit.load(p, map_location=self.device)
                    return m
                except Exception:
                    pass
            obj = torch.load(p, map_location="cpu")
            # If it's already a module
            if hasattr(obj, "eval"):
                return obj
            # If state_dict: try construct AlphaSentinelV12Apex from sibling file if importable
            if isinstance(obj, dict):
                # attempt: mod may provide AlphaSentinelSystem or AlphaSentinelV12Apex via relative imports
                cls = getattr(mod, "AlphaSentinelV12Apex", None)
                if cls is not None:
                    m = cls()
                    m.load_state_dict(obj, strict=False)
                    return m
        except Exception as e:
            self._last_error = f"model_load_failed: {type(e).__name__}: {e}"
        return None

    def ensure_ready(self):
        if not self.enabled:
            return
        if self._orch is not None:
            return
        mod = self._lazy_import()
        model = self._load_model(mod)
        if model is None:
            self._model_loaded = False
            # shadow can proceed without model? no, infer needs model; we will skip compute but keep diag.
            self._orch = None
            return
        self._model_loaded = True
        # Create orchestrator
        create_fn = getattr(mod, "create_orchestrator", None)
        if create_fn is None:
            raise RuntimeError("create_orchestrator not found in alpha_orchestrator module")
        self._orch = create_fn(model, device=self.device)

    def compute_positions(self, returns_window_np):
        """returns_window_np: (T,N) float64/float32. Uses only returns feature (F=1)."""
        if not self.enabled:
            return None
        self.ensure_ready()
        if self._orch is None:
            self._last_diag = {"enabled": True, "mode": self.mode, "applied": False, "skipped": True,
                              "reason": "model_missing_or_orchestrator_not_ready", "error": self._last_error}
            return None
        import torch
        T, N = returns_window_np.shape
        panel = torch.from_numpy(returns_window_np.astype("float32")).view(1, T, N, 1)
        # orchestrator pads features internally to 50
        out = self._orch.infer(panel, regime_dict=None, horizons=self.horizons)
        pos = out.get("position", None)
        if pos is None:
            raise RuntimeError("alpha_orchestrator infer returned no position")
        pos = pos.detach().float().cpu().numpy().reshape(-1)  # (N,)
        self._last_diag = {
            "enabled": True,
            "mode": self.mode,
            "model_loaded": True,
            "applied": False,
            "pos_mean": float(pos.mean()),
            "pos_std": float(pos.std()),
            "pos_min": float(pos.min()),
            "pos_max": float(pos.max()),
        }
        return pos

    def apply(self, weights_np, returns_window_np, invest_total, per_asset_cap):
        """weights_np: (N,) current target weights (non-negative)."""
        if not self.enabled:
            return weights_np, {"enabled": False}

        pos = None
        try:
            pos = self.compute_positions(returns_window_np)
        except Exception as e:
            self._last_diag = {"enabled": True, "mode": self.mode, "applied": False,
                              "error": f"{type(e).__name__}: {e}"}
            return weights_np, self._last_diag

        if pos is None:
            return weights_np, self._last_diag

        if self.mode != "apply":
            # shadow only
            return weights_np, self._last_diag

        # Long-only score from position
        score = (pos + 1.0) / 2.0
        score = np.clip(score, 0.0, None)

        idx = np.where(weights_np > 0)[0]
        if len(idx) == 0 or score[idx].sum() <= 1e-12:
            d = dict(self._last_diag)
            d["applied"] = False
            d["reason"] = "no_investable_assets_or_zero_score"
            return weights_np, d

        # normalize within invested assets
        w_norm = weights_np[idx] / (weights_np[idx].sum() + 1e-12)
        s_norm = score[idx] / (score[idx].sum() + 1e-12)

        lam = float(np.clip(self.blend_weight, 0.0, 1.0))
        blended = (1.0 - lam) * w_norm + lam * s_norm
        blended = blended / (blended.sum() + 1e-12)

        new_w = weights_np.copy()
        new_w[:] = 0.0
        new_w[idx] = blended * float(invest_total)

        # cap enforcement
        if per_asset_cap is not None:
            cap = float(per_asset_cap)
            if cap > 0:
                new_w = np.minimum(new_w, cap)
                # renorm invested total
                s = new_w.sum()
                if s > 1e-12:
                    new_w = new_w / s * float(invest_total)

        d = dict(self._last_diag)
        d["applied"] = True
        d["blend_weight"] = lam
        d["invest_total"] = float(invest_total)
        return new_w, d


class PEADOverlayAdapter:
    def __init__(self, enabled: bool, mode: str, overlay_csv: str = "", budget: float = 0.05):
        self.enabled = bool(enabled)
        self.mode = str(mode or "shadow")
        self.overlay_csv = str(overlay_csv or "")
        self.budget = float(budget)
        self._df = None
        self._last_diag = {"enabled": False}

    def _load_overlay(self):
        if not self.overlay_csv:
            return None
        p = Path(self.overlay_csv)
        if not p.exists():
            return None
        import pandas as pd
        df = pd.read_csv(p)
        # Expect columns: date, symbol, score (best-effort)
        # Normalize column names
        cols = {c.lower(): c for c in df.columns}
        date_c = cols.get("date") or cols.get("dt") or cols.get("timestamp")
        sym_c = cols.get("symbol") or cols.get("ticker") or cols.get("asset")
        score_c = cols.get("score") or cols.get("signal") or cols.get("overlay") or cols.get("value")
        if not date_c or not sym_c or not score_c:
            return None
        df = df[[date_c, sym_c, score_c]].rename(columns={date_c:"date", sym_c:"symbol", score_c:"score"})
        df["date"] = pd.to_datetime(df["date"])
        return df

    def ensure_ready(self):
        if not self.enabled:
            return
        if self._df is not None:
            return
        self._df = self._load_overlay()

    def apply(self, dt, weights_np, symbols, invest_total, per_asset_cap):
        if not self.enabled:
            return weights_np, {"enabled": False}
        self.ensure_ready()
        if self._df is None:
            self._last_diag = {"enabled": True, "mode": self.mode, "applied": False, "reason": "overlay_csv_missing_or_invalid"}
            return weights_np, self._last_diag

        import numpy as np
        import pandas as pd
        d = pd.to_datetime(dt)
        sub = self._df[self._df["date"] == d]
        if sub.empty:
            self._last_diag = {"enabled": True, "mode": self.mode, "applied": False, "reason": "no_overlay_for_date"}
            return weights_np, self._last_diag

        m = dict(zip(sub["symbol"].astype(str), sub["score"].astype(float)))
        score = np.array([m.get(str(s), 0.0) for s in symbols], dtype=float)
        # long-only overlay: positive scores only
        score = np.clip(score, 0.0, None)
        if score.sum() <= 1e-12:
            self._last_diag = {"enabled": True, "mode": self.mode, "applied": False, "reason": "all_zero_score"}
            return weights_np, self._last_diag

        if self.mode != "apply":
            self._last_diag = {"enabled": True, "mode": self.mode, "applied": False, "score_sum": float(score.sum())}
            return weights_np, self._last_diag

        # allocate overlay budget proportionally to signal on top of existing weights
        base = weights_np.copy()
        idx = np.where(base > 0)[0]
        if len(idx) == 0:
            self._last_diag = {"enabled": True, "mode": self.mode, "applied": False, "reason": "no_investable_assets"}
            return weights_np, self._last_diag

        overlay = np.zeros_like(base)
        overlay[idx] = score[idx] / (score[idx].sum() + 1e-12)

        bud = float(np.clip(self.budget, 0.0, 1.0))
        # Blend: (1-bud) base + bud overlay
        base_norm = base[idx] / (base[idx].sum() + 1e-12)
        blended = (1.0 - bud) * base_norm + bud * overlay[idx]
        blended = blended / (blended.sum() + 1e-12)

        new_w = np.zeros_like(base)
        new_w[idx] = blended * float(invest_total)

        if per_asset_cap is not None:
            cap = float(per_asset_cap)
            if cap > 0:
                new_w = np.minimum(new_w, cap)
                s = new_w.sum()
                if s > 1e-12:
                    new_w = new_w / s * float(invest_total)

        self._last_diag = {"enabled": True, "mode": self.mode, "applied": True, "budget": bud,
                           "score_sum": float(score.sum()), "n_sig": int((score>0).sum())}
        return new_w, self._last_diag

class AlphaSentinelAdapter:
    """AlphaSentinel v12 Apex adapter (shadow/apply)."""
    def __init__(self, enabled: bool, mode: str, module_path: str, ckpt_path: str = "", device: str = "cuda", blend: float = 0.20):
        self.enabled = bool(enabled)
        self.mode = str(mode or "shadow")
        self.module_path = str(module_path or "")
        self.ckpt_path = str(ckpt_path or "")
        self.device = str(device or "cuda")
        self.blend = float(blend)
        self._mod = None
        self._model = None

    def _lazy_import(self):
        if not self.module_path:
            raise RuntimeError("alpha_sentinel module_path empty")
        loader = importlib.machinery.SourceFileLoader("alpha_sentinel_mod", self.module_path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)  # type: ignore
        return mod

    def ensure_ready(self):
        if not self.enabled:
            return
        if self._mod is None:
            self._mod = self._lazy_import()
        if self.ckpt_path and self._model is None:
            import torch
            self._model = torch.load(self.ckpt_path, map_location=self.device)

    def apply(self, dt, weights_np, features_dict: dict, invest_total: float):
        if not self.enabled:
            return weights_np, {"enabled": False}
        try:
            self.ensure_ready()
        except Exception as e:
            return weights_np, {"enabled": True, "mode": self.mode, "applied": False, "skipped": True,
                                "reason": "init_failed", "error": f"{type(e).__name__}: {e}"}

        if self.mode != "apply":
            return weights_np, {"enabled": True, "mode": self.mode, "applied": False, "ready": True,
                                "has_ckpt": bool(self.ckpt_path), "has_model": self._model is not None}

        pred_fn = getattr(self._mod, "predict_risk", None) if self._mod is not None else None
        if pred_fn is None:
            return weights_np, {"enabled": True, "mode": self.mode, "applied": False, "skipped": True,
                                "reason": "predict_risk_not_found"}

        try:
            risk = float(pred_fn(dt, features_dict, model=self._model))
            risk = max(0.0, min(1.0, risk))
            lam = max(0.0, min(1.0, float(self.blend)))
            scale = 1.0 - lam * risk
            return weights_np * scale, {"enabled": True, "mode": self.mode, "applied": True, "risk": risk, "blend": lam, "scale": scale}
        except Exception as e:
            return weights_np, {"enabled": True, "mode": self.mode, "applied": False, "skipped": True,
                                "reason": "predict_failed", "error": f"{type(e).__name__}: {e}"}


class DataOrchestratorAdapter:
    """DataOrchestrator adapter (shadow-only in integration track)."""
    def __init__(self, enabled: bool, mode: str, module_path: str, snapshot_dir: str = "", strict_fingerprint: bool = True):
        self.enabled = bool(enabled)
        self.mode = str(mode or "shadow")
        self.module_path = str(module_path or "")
        self.snapshot_dir = str(snapshot_dir or "")
        self.strict_fingerprint = bool(strict_fingerprint)
        self._mod = None

    def _lazy_import(self):
        if not self.module_path:
            raise RuntimeError("data_orchestrator module_path empty")
        loader = importlib.machinery.SourceFileLoader("data_orchestrator_mod", self.module_path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)  # type: ignore
        return mod

    def fingerprint(self) -> dict:
        return {"module_sha": _sha12_file(self.module_path), "snapshot_dir": self.snapshot_dir, "mode": self.mode}

    def apply(self, dt):
        if not self.enabled:
            return {"enabled": False}
        try:
            if self._mod is None:
                self._mod = self._lazy_import()
        except Exception as e:
            return {"enabled": True, "mode": self.mode, "applied": False, "skipped": True,
                    "reason": "import_failed", "error": f"{type(e).__name__}: {e}", "fingerprint": self.fingerprint()}

        if self.mode != "shadow":
            return {"enabled": True, "mode": self.mode, "applied": False, "skipped": True,
                    "reason": "apply_not_allowed_in_integration_track", "fingerprint": self.fingerprint()}

        validate = getattr(self._mod, "validate", None)
        if callable(validate):
            try:
                v = validate(self.snapshot_dir) if self.snapshot_dir else validate()
                return {"enabled": True, "mode": self.mode, "applied": False, "validated": True,
                        "validate_result": str(v), "fingerprint": self.fingerprint()}
            except Exception as e:
                return {"enabled": True, "mode": self.mode, "applied": False, "validated": False,
                        "reason": "validate_failed", "error": f"{type(e).__name__}: {e}", "fingerprint": self.fingerprint()}

        db_path = self.snapshot_dir if self.snapshot_dir else os.getenv("AUB_DB_PATH", "/home/ubuntu/ares_x_v11_0.db")
        db_check = _validate_db_tables(db_path, ["daily_features","sec_daily_features","sf1_pit_features"])
        return {
            "enabled": True,
            "mode": self.mode,
            "applied": False,
            "validated": bool(db_check.get("ok", False)),
            "reason": "no_validate_function",
            "db_check": db_check,
            "fingerprint": self.fingerprint(),
        }


if __name__ == "__main__":
    main()