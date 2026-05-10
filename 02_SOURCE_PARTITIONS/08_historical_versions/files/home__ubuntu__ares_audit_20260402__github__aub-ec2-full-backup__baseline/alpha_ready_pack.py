"""alpha_ready_pack.py

READY 7-pack alpha overlays (minimal, production-friendly).

Goal:
  - Provide 7 "READY" alpha sources that can be toggled via CLI overrides and
    actually change weights (applied_count>0) with robust diagnostics.

Overlays:
  - CS momentum (lookback L, cross-sectional zscore)
  - CS reversal (lookback L, cross-sectional zscore, contrarian)
  - CS low-vol (lookback L, cross-sectional zscore of vol, prefer low vol)
  - Vol-conditioned momentum (only when vol_z in [low, high])
  - Drawdown recovery (tilt risk when dd_20 in [low, high])
  - Yield curve regime (zscore of 10y-2y spread)
  - Yield curve momentum tilt (short-term spread change)

Design principles:
  - Never normalize to sum=1 here. The engine handles target normalization.
  - Always long-only multipliers; cap via multiplier_clip.
  - If data not available, returns no-op but records last_reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def _safe_zscore(x: np.ndarray, clip: float = 2.0) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    m = np.nanmean(x)
    s = np.nanstd(x)
    if not np.isfinite(s) or s < 1e-12:
        return np.zeros_like(x)
    z = (x - m) / s
    if clip is not None:
        z = np.clip(z, -clip, clip)
    z[~np.isfinite(z)] = 0.0
    return z


def _get_ts_value(features: Dict[str, Any], key: str, t: int, default: float = 0.0) -> float:
    v = features.get(key, None)
    try:
        if v is None:
            return float(default)
        if isinstance(v, (list, tuple, np.ndarray)):
            return float(v[t])
        return float(v)
    except Exception:
        return float(default)


def _find_ret_matrix(engine: Any, features: Dict[str, Any]) -> Optional[np.ndarray]:
    """Try to locate an asset returns matrix with shape (T, N)."""
    for name in ("returns", "ret_mat", "rets", "asset_returns", "R"):
        if hasattr(engine, name):
            arr = getattr(engine, name)
            if isinstance(arr, np.ndarray) and arr.ndim == 2:
                return arr
    for key in ("ret_mat", "rets", "returns", "asset_returns"):
        arr = features.get(key, None)
        if isinstance(arr, np.ndarray) and arr.ndim == 2:
            return arr
    return None


def _heuristic_risk_bucket(symbols: List[str]) -> Tuple[np.ndarray, np.ndarray]:
    """Crude risk vs defensive bucket for macro tilts."""
    syms = [s.upper() for s in symbols]
    risk = []
    defensive = []
    for s in syms:
        if s in ("SPY","QQQ","IWM","DIA","EFA","EEM") or s.startswith("X") or "TECH" in s:
            risk.append(True)
        else:
            risk.append(False)
        if s in ("TLT","IEF","SHY","AGG","LQD","BND","TIP","GLD") or "BOND" in s or "TREAS" in s:
            defensive.append(True)
        else:
            defensive.append(False)
    risk = np.array(risk, dtype=bool)
    defensive = np.array(defensive, dtype=bool)
    if defensive.sum() == 0:
        defensive = ~risk
    if risk.sum() == 0:
        risk = ~defensive
    return risk, defensive


@dataclass
class AlphaReadyPack:
    # toggles
    enable_cs_momentum: int = 0
    enable_cs_reversal: int = 0
    enable_cs_lowvol: int = 0
    enable_vol_cond_mom: int = 0
    # regime conditional (risk_on only)
    enable_regime_conditional: int = 0
    regime_min_for_alpha: int = 2  # NEUTRAL(2) 이상에서만 alpha 적용
    enable_dd_recovery: int = 0
    enable_yield_curve: int = 0
    enable_yc_momtilt: int = 0

    # params
    cs_mom_lookback: int = 20
    cs_mom_tilt: float = 0.10
    cs_mom_clip_z: float = 2.0

    cs_rev_lookback: int = 5
    cs_rev_tilt: float = 0.10
    cs_rev_clip_z: float = 2.0

    cs_lv_lookback: int = 60
    cs_lv_tilt: float = 0.10
    cs_lv_clip_z: float = 2.0

    vcm_lookback: int = 20
    vcm_tilt: float = 0.10
    vcm_volz_low: float = -0.5
    vcm_volz_high: float = 0.5

    ddrec_range_low: float = -0.08
    ddrec_range_high: float = -0.04
    ddrec_tilt: float = 0.12

    yc_lookback: int = 63
    yc_smooth: int = 5
    yc_tilt: float = 0.15
    yc_z_hi: float = 0.5
    yc_z_lo: float = -0.5

    yc_mom_lb: int = 20
    yc_mom_tilt: float = 0.10

    multiplier_clip: float = 1.50

    # last state / diagnostics
    applied_count: int = 0
    effect_sum: float = 0.0
    effect_avg: float = 0.0
    weights_changed: bool = False
    last_reason: str = ""
    last_effect_l1: float = 0.0

    def is_enabled(self) -> bool:
        return any(int(getattr(self, k)) == 1 for k in (
            "enable_cs_momentum","enable_cs_reversal","enable_cs_lowvol","enable_vol_cond_mom",
            "enable_dd_recovery","enable_yield_curve","enable_yc_momtilt"
        ))

    @classmethod
    def from_engine(cls, engine: Any) -> "AlphaReadyPack":
        kwargs = {}
        for f in cls.__dataclass_fields__.keys():
            if hasattr(engine, f):
                kwargs[f] = getattr(engine, f)
        # Backward-compatible alias: CLI uses --alpha_multiplier_clip, pack uses multiplier_clip
        if hasattr(engine, "alpha_multiplier_clip"):
            try:
                kwargs["multiplier_clip"] = float(getattr(engine, "alpha_multiplier_clip"))
            except Exception:
                pass
        return cls(**kwargs)

    def apply(self, weights: np.ndarray, prev_weights: np.ndarray, symbols: List[str],
              features: Dict[str, Any], t: int, engine: Any) -> Tuple[np.ndarray, Dict[str, Any]]:
        diag: Dict[str, Any] = {
            "enabled": bool(self.is_enabled()),
            "applied_count": 0,
            "effect_sum": 0.0,
            "effect_avg": 0.0,
            "weights_changed": False,
            "last_reason": "",
            "modules": {},
            "multiplier_clip": float(self.multiplier_clip),
        }
        if not self.is_enabled():
            diag["last_reason"] = "disabled"
            return weights, diag
        # === REGIME CONDITIONAL CHECK ===
        # risk_off/crisis에서는 alpha 적용 안함 (Mean 보호)
        if int(self.enable_regime_conditional) == 1:
            current_regime = getattr(engine, "_current_regime", 3)  # default RISK_ON
            if current_regime < self.regime_min_for_alpha:
                diag["last_reason"] = f"regime_skip:{current_regime}<{self.regime_min_for_alpha}"
                diag["regime_skipped"] = True
                return weights, diag

        w = np.asarray(weights, dtype=float).copy()
        if w.size == 0:
            diag["last_reason"] = "empty_weights"
            return w, diag

        N = w.shape[0]
        mult = np.ones(N, dtype=float)
        ret_mat = _find_ret_matrix(engine, features)

        # CS momentum
        if int(self.enable_cs_momentum) == 1:
            if ret_mat is None or t < self.cs_mom_lookback:
                diag["modules"]["cs_momentum"] = {"applied": 0, "reason": "missing_ret_mat"}
            else:
                r = np.sum(ret_mat[t-self.cs_mom_lookback+1:t+1, :], axis=0)
                z = _safe_zscore(r, clip=self.cs_mom_clip_z)
                mult *= np.exp(self.cs_mom_tilt * z)
                diag["modules"]["cs_momentum"] = {"applied": 1, "lookback": self.cs_mom_lookback, "clip_z": float(self.cs_mom_clip_z)}

        # CS reversal
        if int(self.enable_cs_reversal) == 1:
            if ret_mat is None or t < self.cs_rev_lookback:
                diag["modules"]["cs_reversal"] = {"applied": 0, "reason": "missing_ret_mat"}
            else:
                r = np.sum(ret_mat[t-self.cs_rev_lookback+1:t+1, :], axis=0)
                z = _safe_zscore(r, clip=self.cs_rev_clip_z)
                mult *= np.exp(-self.cs_rev_tilt * z)
                diag["modules"]["cs_reversal"] = {"applied": 1, "lookback": self.cs_rev_lookback, "clip_z": float(self.cs_rev_clip_z)}

        # CS low-vol
        if int(self.enable_cs_lowvol) == 1:
            if ret_mat is None or t < self.cs_lv_lookback:
                diag["modules"]["cs_lowvol"] = {"applied": 0, "reason": "missing_ret_mat"}
            else:
                window = ret_mat[t-self.cs_lv_lookback+1:t+1, :]
                vol = np.nanstd(window, axis=0)
                z = _safe_zscore(vol, clip=self.cs_lv_clip_z)
                mult *= np.exp(-self.cs_lv_tilt * z)
                diag["modules"]["cs_lowvol"] = {"applied": 1, "lookback": self.cs_lv_lookback, "clip_z": float(self.cs_lv_clip_z)}

        # Vol-conditioned momentum
        if int(self.enable_vol_cond_mom) == 1:
            vol_z = _get_ts_value(features, "vol_z", t, 0.0)
            if ret_mat is None or t < self.vcm_lookback:
                diag["modules"]["vol_cond_mom"] = {"applied": 0, "reason": "missing_ret_mat"}
            else:
                if (vol_z >= self.vcm_volz_low) and (vol_z <= self.vcm_volz_high):
                    r = np.sum(ret_mat[t-self.vcm_lookback+1:t+1, :], axis=0)
                    z = _safe_zscore(r, clip=float(self.cs_mom_clip_z))
                    mult *= np.exp(self.vcm_tilt * z)
                    diag["modules"]["vol_cond_mom"] = {"applied": 1, "vol_z": vol_z}
                else:
                    diag["modules"]["vol_cond_mom"] = {"applied": 0, "reason": "vol_z_out_of_band", "vol_z": vol_z}

        # Drawdown recovery tilt
        if int(self.enable_dd_recovery) == 1:
            dd_20 = _get_ts_value(features, "dd_20", t, 0.0)
            risk_mask, def_mask = _heuristic_risk_bucket(symbols)
            if self.ddrec_range_low <= dd_20 <= self.ddrec_range_high:
                m = np.ones(N, float)
                m[risk_mask] *= (1.0 + self.ddrec_tilt)
                m[def_mask]  *= max(0.0, (1.0 - 0.5*self.ddrec_tilt))
                mult *= m
                diag["modules"]["dd_recovery"] = {"applied": 1, "dd_20": dd_20}
            else:
                diag["modules"]["dd_recovery"] = {"applied": 0, "dd_20": dd_20}

        # Yield curve regime
        if int(self.enable_yield_curve) == 1:
            if t < self.yc_lookback:
                diag["modules"]["yield_curve"] = {"applied": 0, "reason": "insufficient_history"}
            else:
                s_hist = []
                for k in range(t-self.yc_lookback+1, t+1):
                    s_hist.append(_get_ts_value(features, "DGS10", k, 0.0) - _get_ts_value(features, "DGS2", k, 0.0))
                s_hist = np.array(s_hist, dtype=float)
                z = float(_safe_zscore(s_hist, clip=None)[-1])
                risk_mask, def_mask = _heuristic_risk_bucket(symbols)
                m = np.ones(N, float)
                if z > self.yc_z_hi:
                    m[risk_mask] *= (1.0 + self.yc_tilt)
                    m[def_mask]  *= max(0.0, (1.0 - 0.5*self.yc_tilt))
                elif z < self.yc_z_lo:
                    m[def_mask]  *= (1.0 + self.yc_tilt)
                    m[risk_mask] *= max(0.0, (1.0 - 0.5*self.yc_tilt))
                mult *= m
                diag["modules"]["yield_curve"] = {"applied": 1, "z": z}

        # Yield curve momentum tilt
        if int(self.enable_yc_momtilt) == 1:
            if t < self.yc_mom_lb:
                diag["modules"]["yc_momtilt"] = {"applied": 0, "reason": "insufficient_history"}
            else:
                s0 = _get_ts_value(features, "DGS10", t, 0.0) - _get_ts_value(features, "DGS2", t, 0.0)
                s1 = _get_ts_value(features, "DGS10", t-self.yc_mom_lb, 0.0) - _get_ts_value(features, "DGS2", t-self.yc_mom_lb, 0.0)
                ds = float(s0 - s1)
                risk_mask, def_mask = _heuristic_risk_bucket(symbols)
                m = np.ones(N, float)
                if ds > 0:
                    m[risk_mask] *= (1.0 + self.yc_mom_tilt)
                else:
                    m[def_mask]  *= (1.0 + self.yc_mom_tilt)
                mult *= m
                diag["modules"]["yc_momtilt"] = {"applied": 1, "ds": ds}

        # Clip multipliers
        if self.multiplier_clip and self.multiplier_clip > 1.0:
            mult = np.clip(mult, 1.0/self.multiplier_clip, self.multiplier_clip)

        w2 = w * mult
        l1 = float(np.sum(np.abs(w2 - w)))
        changed = bool(l1 > 1e-12)

        applied_any = any((m.get("applied",0)==1) for m in diag["modules"].values())
        diag["applied_count"] = 1 if (applied_any or changed) else 0
        diag["weights_changed"] = changed
        diag["effect_sum"] = float(np.sum(np.abs(mult - 1.0)))
        diag["effect_avg"] = float(np.mean(np.abs(mult - 1.0)))
        diag["last_reason"] = "ok" if diag["applied_count"] else "no_effect"

        # update pack stats
        self.applied_count += int(diag["applied_count"])
        self.effect_sum += float(diag["effect_sum"])
        self.effect_avg = self.effect_sum / max(1, self.applied_count)
        self.weights_changed = self.weights_changed or changed
        self.last_reason = diag["last_reason"]
        self.last_effect_l1 = l1

        return w2, diag

# === CLIP PARAMETERS EXTENSION ===
# 기존 apply() 함수에서 clip 적용
def apply_with_clip(self, weights, features, t, regime):
    """Apply alpha with clipping for tail risk control"""
    result = self.apply(weights, features, t, regime)
    
    # alpha_multiplier_clip 적용
    clip_val = getattr(self.engine, 'alpha_multiplier_clip', 1.50)
    if result['weights_changed']:
        # multiplier를 clip_val로 제한
        for i in range(len(result.get('new_weights', []))):
            if weights[i] > 0:
                ratio = result['new_weights'][i] / weights[i]
                if ratio > clip_val:
                    result['new_weights'][i] = weights[i] * clip_val
                elif ratio < 1/clip_val:
                    result['new_weights'][i] = weights[i] / clip_val
    
    return result
