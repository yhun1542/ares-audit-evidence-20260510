"""
Alpha Scorer V5 - 레짐 조건부 멀티팩터 스코어러
팩터: mom, rev, breakout, lowvol, liquidity
레짐별 틸트: trend, mean_revert, high_vol, risk_off, risk_on, neutral
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

def _robust_zscore(x: np.ndarray, clip: float = 3.0) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    m = np.nanmean(x)
    s = np.nanstd(x)
    if not np.isfinite(s) or s < 1e-12:
        return np.zeros_like(x)
    z = (x - m) / s
    z = np.clip(z, -clip, clip)
    z[~np.isfinite(z)] = 0.0
    return z

@dataclass
class AlphaScorerV5Config:
    enabled: bool = False
    lookback_fast: int = 5
    lookback_mid: int = 20
    lookback_slow: int = 60
    vol_lookback: int = 20
    z_clip: float = 3.0
    conf_min: float = 0.55
    min_symbols: int = 8
    regime_impact: float = 1.0
    tilt_strength: float = 0.15
    # Fallback 파라미터
    min_scored_assets: int = 5  # 최소 스코어 생성 자산 수
    min_exposure_floor: float = 0.10  # 최소 노출 비율

class AlphaScorerV5:
    """레짐 조건부 멀티팩터 스코어러"""
    
    NAME = "alpha_scorer_v5"
    VERSION = "1.0.0"
    
    def __init__(self, cfg: AlphaScorerV5Config = None):
        self.cfg = cfg or AlphaScorerV5Config()
        self._prev_regime: Optional[str] = None
        
        # 기본 팩터 가중치
        self.base_weights = {
            "mom": 1.00,
            "rev": 0.70,
            "breakout": 0.60,
            "lowvol": 0.50,
            "liquidity": 0.25,
        }
        
        # 레짐별 팩터 틸트
        self.regime_tilts = {
            "trend":       {"mom": 1.35, "rev": 0.50, "breakout": 1.25, "lowvol": 0.80, "liquidity": 1.00},
            "mean_revert": {"mom": 0.75, "rev": 1.45, "breakout": 0.70, "lowvol": 1.00, "liquidity": 1.05},
            "high_vol":    {"mom": 0.80, "rev": 0.85, "breakout": 0.70, "lowvol": 1.55, "liquidity": 1.15},
            "risk_off":    {"mom": 0.75, "rev": 0.65, "breakout": 0.60, "lowvol": 1.75, "liquidity": 1.10},
            "risk_on":     {"mom": 1.20, "rev": 0.80, "breakout": 1.10, "lowvol": 0.85, "liquidity": 1.05},
            "neutral":     {"mom": 1.00, "rev": 1.00, "breakout": 1.00, "lowvol": 1.00, "liquidity": 1.00},
        }
    
    def _compute_factors(self, rets: np.ndarray) -> Dict[str, np.ndarray]:
        """Returns 기반 팩터 계산"""
        T, N = rets.shape
        cfg = self.cfg
        
        def _sum_last(lb: int) -> np.ndarray:
            if T < lb + 1:
                return np.full(N, np.nan)
            return np.nansum(rets[-lb:, :], axis=0)
        
        mom = _sum_last(cfg.lookback_mid)
        rev = -_sum_last(cfg.lookback_fast)
        slow = _sum_last(cfg.lookback_slow)
        breakout = mom - slow
        
        if T >= cfg.vol_lookback + 1:
            vol = np.nanstd(rets[-cfg.vol_lookback:, :], axis=0)
        else:
            vol = np.nanstd(rets, axis=0)
        lowvol = -vol
        
        dr = np.diff(np.nan_to_num(rets, nan=0.0), axis=0)
        liq = -np.nanstd(dr, axis=0)
        
        return {"mom": mom, "rev": rev, "breakout": breakout, "lowvol": lowvol, "liquidity": liq}
    
    def _regime_weights(self, regime: str) -> Dict[str, float]:
        """레짐에 따른 팩터 가중치 조정"""
        base = self.base_weights.copy()
        tilt = self.regime_tilts.get(regime, self.regime_tilts["neutral"])
        
        out = {}
        for k, w in base.items():
            t = tilt.get(k, 1.0)
            out[k] = w * (1.0 + self.cfg.regime_impact * (t - 1.0))
        
        s = sum(abs(v) for v in out.values()) + 1e-12
        for k in out:
            out[k] /= s
        return out
    
    def apply(
        self,
        w: np.ndarray,
        w_base: np.ndarray,
        symbols: List[str],
        features: Dict[str, Any],
        t: int,
        engine: Any = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """메인 실행 함수"""
        
        if not self.cfg.enabled:
            return w, {"enabled": False}
        
        # Returns matrix 찾기
        ret_mat = None
        for name in ("returns", "ret_mat", "rets", "asset_returns", "R"):
            if hasattr(engine, name):
                arr = getattr(engine, name)
                if isinstance(arr, np.ndarray) and arr.ndim == 2:
                    ret_mat = arr
                    break
        
        if ret_mat is None:
            for key in ("ret_mat", "rets", "returns", "asset_returns"):
                arr = features.get(key, None)
                if isinstance(arr, np.ndarray) and arr.ndim == 2:
                    ret_mat = arr
                    break
        
        if ret_mat is None:
            return w, {"enabled": True, "applied": False, "reason": "no_ret_mat"}
        
        if t < self.cfg.lookback_slow + 1:
            return w, {"enabled": True, "applied": False, "reason": "insufficient_history"}
        
        # 현재까지의 returns만 사용
        rets = ret_mat[:t, :]
        
        # 레짐 조회
        regime = "neutral"
        if hasattr(engine, "_current_regime"):
            r = engine._current_regime
            if r == 0:
                regime = "risk_off"
            elif r == 1:
                regime = "risk_off"
            elif r == 2:
                regime = "neutral"
            elif r >= 3:
                regime = "risk_on"
        
        # 팩터 계산
        factors_raw = self._compute_factors(rets)
        factors_z = {k: _robust_zscore(v, self.cfg.z_clip) for k, v in factors_raw.items()}
        
        # 레짐 가중치
        fw = self._regime_weights(regime)
        
        # 종합 스코어
        score = np.zeros(len(symbols), dtype=float)
        for k, wk in fw.items():
            score += wk * factors_z[k]
        
        # 스코어를 multiplier로 변환
        score_norm = _robust_zscore(score, 2.0)
        multiplier = 1.0 + self.cfg.tilt_strength * score_norm
        multiplier = np.clip(multiplier, 0.8, 1.2)
        
        # === Fallback 체크 1: 스코어 생성 자산 수 ===
        scored_assets = int(np.sum(np.isfinite(score) & (np.abs(score) > 1e-6)))
        if scored_assets < self.cfg.min_scored_assets:
            return w, {
                "enabled": True,
                "applied": False,
                "fallback": True,
                "fallback_reason": "coverage_low",
                "scored_assets": scored_assets,
                "min_required": self.cfg.min_scored_assets,
                "regime": regime,
            }
        
        # 적용
        w_new = w * multiplier
        w_new = np.maximum(w_new, 0.0)
        
        # Renormalize
        total = np.sum(w_new)
        base_total = np.sum(w)
        
        if total > 1e-6:
            w_new = w_new / total * base_total
        
        # === Fallback 체크 2: 최소 노출 비율 ===
        exposure_ratio = np.sum(w_new) / max(base_total, 1e-6)
        if exposure_ratio < self.cfg.min_exposure_floor:
            return w, {
                "enabled": True,
                "applied": False,
                "fallback": True,
                "fallback_reason": "exposure_low",
                "exposure_ratio": float(exposure_ratio),
                "min_required": self.cfg.min_exposure_floor,
                "regime": regime,
            }
        
        # Diagnostics
        effect = float(np.sum(np.abs(w_new - w)))
        weights_changed = effect > 1e-6
        
        diag = {
            "enabled": True,
            "applied": True,
            "fallback": False,
            "regime": regime,
            "effect": effect,
            "weights_changed": weights_changed,
            "score_mean": float(np.nanmean(score)),
            "score_std": float(np.nanstd(score)),
            "scored_assets": scored_assets,
            "exposure_ratio": float(exposure_ratio),
        }
        
        return w_new, diag
    
    @classmethod
    def from_engine(cls, engine: Any) -> "AlphaScorerV5":
        enabled = int(getattr(engine, "enable_alpha_scorer_v5", 0))
        cfg = AlphaScorerV5Config(
            enabled=bool(enabled),
            lookback_fast=int(getattr(engine, "asv5_lookback_fast", 5)),
            lookback_mid=int(getattr(engine, "asv5_lookback_mid", 20)),
            lookback_slow=int(getattr(engine, "asv5_lookback_slow", 60)),
            vol_lookback=int(getattr(engine, "asv5_vol_lookback", 20)),
            z_clip=float(getattr(engine, "asv5_z_clip", 3.0)),
            conf_min=float(getattr(engine, "asv5_conf_min", 0.55)),
            min_symbols=int(getattr(engine, "asv5_min_symbols", 8)),
            regime_impact=float(getattr(engine, "asv5_regime_impact", 1.0)),
            tilt_strength=float(getattr(engine, "asv5_tilt_strength", 0.15)),
            min_scored_assets=int(getattr(engine, "asv5_min_scored_assets", 5)),
            min_exposure_floor=float(getattr(engine, "asv5_min_exposure_floor", 0.10)),
        )
        return cls(cfg)
