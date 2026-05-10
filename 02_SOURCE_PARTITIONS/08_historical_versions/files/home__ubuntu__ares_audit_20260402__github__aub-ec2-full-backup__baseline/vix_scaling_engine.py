#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vix_scaling_engine.py
=====================
VIX 기반 동적 포지션 스케일링 엔진.

- 히스테리시스 임계값 (entry/exit)
- 스무딩(EMA 스타일) + max_daily_scale_change(변화폭 제한)
- 상세 로깅 + diagnostics 요약

주의:
- 엔진이 OFF일 때 baseline과 완전 동일을 보장하려면,
  상위 엔진에서 enable 플래그가 0이면 이 모듈을 import/사용하지 않아야 함.
  (권장: 엔진 내부에서 lazy import)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
from collections import defaultdict
import math
import time


@dataclass
class VIXScalingConfig:
    enabled: bool = True

    # VIX thresholds (hysteresis)
    # higher regimes: enter when VIX >= *_entry, exit when VIX <= *_exit
    # low regime: enter when VIX <= vix_low_exit, exit when VIX >= vix_low_entry
    vix_low_entry: float = 15.0
    vix_low_exit: float = 13.0

    vix_elevated_entry: float = 20.0
    vix_elevated_exit: float = 18.0

    vix_high_entry: float = 25.0
    vix_high_exit: float = 23.0

    vix_extreme_entry: float = 35.0
    vix_extreme_exit: float = 32.0

    # scale factors
    scale_low: float = 1.10
    scale_normal: float = 1.00
    scale_elevated: float = 0.85
    scale_high: float = 0.65
    scale_extreme: float = 0.40

    # smoothing / change limit
    smoothing_enabled: bool = True
    smoothing_window: int = 3
    smoothing_weight: float = 0.6
    max_daily_scale_change: float = 0.10

    verbose_logging: bool = True


def create_vix_scaling_config_from_dict(config_dict: Dict[str, Any]) -> VIXScalingConfig:
    if not isinstance(config_dict, dict):
        return VIXScalingConfig(enabled=False)
    cfg = VIXScalingConfig()
    for k, v in config_dict.items():
        if hasattr(cfg, k):
            try:
                setattr(cfg, k, type(getattr(cfg, k))(v))
            except Exception:
                setattr(cfg, k, v)
    return cfg


class VIXScalingEngine:
    """Stateful hysteresis scaling engine."""

    def __init__(self, config: Optional[VIXScalingConfig] = None):
        self.config = config or VIXScalingConfig()
        self.reset()

    def reset(self):
        self._state = "NORMAL"
        self._last_scale = 1.0
        self._ema_scale = 1.0
        self._last_vix = None
        self._last_date = None

        self._counts_state = defaultdict(int)
        self._clamp_events = 0
        self._rebalances = 0
        self._scale_sum = 0.0
        self._scale_min = None
        self._scale_max = None
        self._logs: List[Dict[str, Any]] = []

    def _state_from_vix_hysteresis(self, vix: float) -> str:
        c = self.config
        st = self._state

        # downgrade first (hysteresis)
        if st == "EXTREME":
            if vix <= c.vix_extreme_exit:
                st = "HIGH"
            else:
                return "EXTREME"
        if st == "HIGH":
            if vix >= c.vix_extreme_entry:
                return "EXTREME"
            if vix <= c.vix_high_exit:
                st = "ELEVATED"
            else:
                return "HIGH"
        if st == "ELEVATED":
            if vix >= c.vix_high_entry:
                return "HIGH"
            if vix <= c.vix_elevated_exit:
                st = "NORMAL"
            else:
                return "ELEVATED"

        # NORMAL or LOW entry rules
        if st == "NORMAL":
            if vix >= c.vix_extreme_entry:
                return "EXTREME"
            if vix >= c.vix_high_entry:
                return "HIGH"
            if vix >= c.vix_elevated_entry:
                return "ELEVATED"
            if vix <= c.vix_low_exit:
                return "LOW"
            return "NORMAL"

        if st == "LOW":
            if vix >= c.vix_low_entry:
                return "NORMAL"
            return "LOW"

        return st

    def _raw_scale_for_state(self, st: str) -> float:
        c = self.config
        if st == "LOW":
            return float(c.scale_low)
        if st == "ELEVATED":
            return float(c.scale_elevated)
        if st == "HIGH":
            return float(c.scale_high)
        if st == "EXTREME":
            return float(c.scale_extreme)
        return float(c.scale_normal)

    def _apply_smoothing_and_clamp(self, raw: float) -> float:
        c = self.config
        s = float(raw)

        if c.smoothing_enabled:
            w = max(0.0, min(1.0, float(c.smoothing_weight)))
            self._ema_scale = w * s + (1.0 - w) * self._ema_scale
            s = float(self._ema_scale)

        max_delta = float(c.max_daily_scale_change)
        if max_delta > 0:
            delta = s - float(self._last_scale)
            if abs(delta) > max_delta:
                s = float(self._last_scale) + math.copysign(max_delta, delta)
                self._clamp_events += 1

        self._last_scale = float(s)
        return float(s)

    def get_scale_factor(self, current_vix: float, date: Any = None) -> float:
        if not self.config.enabled:
            return 1.0

        self._rebalances += 1
        v = float(current_vix)

        self._state = self._state_from_vix_hysteresis(v)
        raw = self._raw_scale_for_state(self._state)
        scale = self._apply_smoothing_and_clamp(raw)

        self._counts_state[self._state] += 1
        self._scale_sum += float(scale)
        self._scale_min = scale if self._scale_min is None else min(self._scale_min, scale)
        self._scale_max = scale if self._scale_max is None else max(self._scale_max, scale)

        self._last_vix = v
        self._last_date = date
        return float(scale)

    def log_rebalance(self, date, raw_vix, scale_factor, exposure_before, exposure_after):
        if (not self.config.enabled) or (not self.config.verbose_logging):
            return
        self._logs.append({
            "ts": time.time(),
            "date": str(date),
            "vix": float(raw_vix),
            "state": self._state,
            "scale": float(scale_factor),
            "exp_before": float(exposure_before),
            "exp_after": float(exposure_after),
            "clamp_events": int(self._clamp_events),
        })
        if len(self._logs) > 5000:
            self._logs = self._logs[-2000:]

    def get_diagnostics(self) -> Dict[str, Any]:
        if not self.config.enabled:
            return {"enabled": False}
        avg = (self._scale_sum / self._rebalances) if self._rebalances > 0 else None
        return {
            "enabled": True,
            "state": self._state,
            "rebalances": int(self._rebalances),
            "avg_scale": float(avg) if avg is not None else None,
            "min_scale": float(self._scale_min) if self._scale_min is not None else None,
            "max_scale": float(self._scale_max) if self._scale_max is not None else None,
            "clamp_events": int(self._clamp_events),
            "counts_state": dict(self._counts_state),
            "last_vix": self._last_vix,
            "last_date": str(self._last_date),
            "config": asdict(self.config),
        }

    def get_logs(self) -> List[Dict[str, Any]]:
        return list(self._logs)
