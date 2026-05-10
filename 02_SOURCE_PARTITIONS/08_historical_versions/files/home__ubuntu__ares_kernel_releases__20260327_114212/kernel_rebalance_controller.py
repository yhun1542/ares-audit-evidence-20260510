"""
ARES v3.0 - Kernel-Native Rebalance Controller
================================================
Replaces legacy REBAL_GATE (orchestrator.py lines 2973-3084).

Design consensus from 4 AI models (Grok-4.20, Claude-4.5, GPT-5.2-Pro, Gemini-2.5):
  - Regime-aware, context-sensitive rebalance decisions
  - Kernel as sole authority (SoT) for rebalance decisions
  - Fail-safe: Kernel down -> conservative/no-rebalance default
  - Drift-based trigger instead of time-based
  - Full decision audit trail in Redis
"""
from __future__ import annotations

import json
import time
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("ares.kernel.rebalance")


class RebalanceDecision(str, Enum):
    EXECUTE = "EXECUTE"
    CONSERVATIVE_ONLY = "CONSERVATIVE_ONLY"
    DE_RISK_ONLY = "DE_RISK_ONLY"
    DEFER = "DEFER"
    EMERGENCY_HALT = "EMERGENCY_HALT"


class EffectiveRegime(str, Enum):
    BULL = "BULL"
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    BEAR = "BEAR"
    CRISIS = "CRISIS"


@dataclass
class RegimeConfig:
    drift_threshold: float
    cooldown_minutes: int
    max_turnover_pct: float
    exposure_target: float
    sizing_scalar: float
    allow_new_longs: bool
    allow_new_shorts: bool
    vix_ceiling: float
    urgency_base: float


REGIME_CONFIGS: Dict[EffectiveRegime, RegimeConfig] = {
    EffectiveRegime.BULL: RegimeConfig(
        drift_threshold=0.05, cooldown_minutes=240, max_turnover_pct=0.15,
        exposure_target=0.95, sizing_scalar=1.0,
        allow_new_longs=True, allow_new_shorts=True,
        vix_ceiling=25.0, urgency_base=0.3,
    ),
    EffectiveRegime.NORMAL: RegimeConfig(
        drift_threshold=0.04, cooldown_minutes=360, max_turnover_pct=0.12,
        exposure_target=0.80, sizing_scalar=0.85,
        allow_new_longs=True, allow_new_shorts=True,
        vix_ceiling=22.0, urgency_base=0.4,
    ),
    EffectiveRegime.CAUTION: RegimeConfig(
        drift_threshold=0.03, cooldown_minutes=480, max_turnover_pct=0.08,
        exposure_target=0.65, sizing_scalar=0.60,
        allow_new_longs=True, allow_new_shorts=False,
        vix_ceiling=28.0, urgency_base=0.5,
    ),
    EffectiveRegime.BEAR: RegimeConfig(
        drift_threshold=0.02, cooldown_minutes=120, max_turnover_pct=0.10,
        exposure_target=0.40, sizing_scalar=0.40,
        allow_new_longs=False, allow_new_shorts=True,
        vix_ceiling=35.0, urgency_base=0.7,
    ),
    EffectiveRegime.CRISIS: RegimeConfig(
        drift_threshold=0.01, cooldown_minutes=60, max_turnover_pct=0.20,
        exposure_target=0.20, sizing_scalar=0.15,
        allow_new_longs=False, allow_new_shorts=False,
        vix_ceiling=50.0, urgency_base=1.0,
    ),
}

FAILSAFE_CONFIG = RegimeConfig(
    drift_threshold=0.02, cooldown_minutes=720, max_turnover_pct=0.05,
    exposure_target=0.40, sizing_scalar=0.30,
    allow_new_longs=False, allow_new_shorts=False,
    vix_ceiling=20.0, urgency_base=0.0,
)


@dataclass
class MarketContext:
    vix: float = 20.0
    vix_change_1h: float = 0.0
    spy_change_1d: float = 0.0
    regime_confidence: float = 0.5
    regime_flap_count_24h: int = 0


@dataclass
class PortfolioContext:
    equity: float = 0.0
    position_count: int = 0
    universe_size: int = 68
    gross_exposure: float = 0.0
    max_weight_drift: float = 0.0
    turnover_today: float = 0.0
    drawdown: float = 0.0
    leverage: float = 0.0
    open_orders: int = 0
    session_pnl_pct: float = 0.0


@dataclass
class RebalanceResult:
    decision: RebalanceDecision
    regime: EffectiveRegime
    config: RegimeConfig
    urgency: float
    reason: str
    execution_params: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class RebalKeys:
    KERNEL_STATE = "ares:kernel:state"
    KERNEL_HEARTBEAT = "ares:kernel:heartbeat"
    KERNEL_READINESS = "ares:kernel:readiness"
    REBAL_DECISION = "ares:rebal:v3:decision"
    REBAL_LAST_TS = "ares:rebal:v3:last_execution_ts"
    REBAL_COOLDOWN_UNTIL = "ares:rebal:v3:cooldown_until"
    REBAL_LOCK = "ares:rebal:v3:lock"
    REBAL_HISTORY = "ares:rebal:v3:decision_history"
    REBAL_FORCE_FLAG = "ares:rebal:v3:force_flag"
    REBAL_CONFIG_OVERRIDE = "ares:rebal:v3:config_override"
    REBAL_STATS = "ares:rebal:v3:stats"
    REGIME_FINAL = "regime:final:current"
    LEGACY_GATE_STATE = "nextgen2:rebal_gate:state"
    LEGACY_GATE_REASON = "nextgen2:rebal_gate:reason"


class KernelRebalanceController:
    """
    Kernel-Native Rebalance Controller.
    Replaces legacy REBAL_GATE with regime-aware, context-sensitive control.
    """

    KERNEL_HEARTBEAT_MAX_AGE = 30

    def __init__(self, redis_client, logger_override=None):
        self.r = redis_client
        self.log = logger_override or logger

    def evaluate(self, market: MarketContext, portfolio: PortfolioContext) -> RebalanceResult:
        """Main entry point: evaluate whether to rebalance."""
        try:
            # 1. Check kernel health
            if not self._is_kernel_alive():
                return RebalanceResult(
                    decision=RebalanceDecision.DEFER,
                    regime=EffectiveRegime.CAUTION,
                    config=FAILSAFE_CONFIG,
                    urgency=0.0,
                    reason="Kernel heartbeat stale - failsafe DEFER",
                )

            # 2. Determine effective regime
            regime = self._determine_regime()
            config = REGIME_CONFIGS.get(regime, FAILSAFE_CONFIG)

            # 3. Check force flag
            force = self._check_force_flag()
            if force:
                result = RebalanceResult(
                    decision=RebalanceDecision.EXECUTE,
                    regime=regime,
                    config=config,
                    urgency=1.0,
                    reason="Force flag set",
                )
                self._log_decision(result)
                return result

            # 4. Check cooldown
            if self._in_cooldown(config):
                return RebalanceResult(
                    decision=RebalanceDecision.DEFER,
                    regime=regime,
                    config=config,
                    urgency=0.0,
                    reason="Cooldown active",
                )

            # 5. Check VIX ceiling
            if market.vix > config.vix_ceiling:
                if regime in (EffectiveRegime.BEAR, EffectiveRegime.CRISIS):
                    result = RebalanceResult(
                        decision=RebalanceDecision.DE_RISK_ONLY,
                        regime=regime,
                        config=config,
                        urgency=0.9,
                        reason=f"VIX {market.vix:.1f} > ceiling {config.vix_ceiling} in {regime.value}",
                    )
                else:
                    result = RebalanceResult(
                        decision=RebalanceDecision.CONSERVATIVE_ONLY,
                        regime=regime,
                        config=config,
                        urgency=0.6,
                        reason=f"VIX {market.vix:.1f} > ceiling {config.vix_ceiling}",
                    )
                self._log_decision(result)
                return result

            # 6. Check drawdown
            if portfolio.drawdown > 0.05:
                result = RebalanceResult(
                    decision=RebalanceDecision.DE_RISK_ONLY,
                    regime=regime,
                    config=config,
                    urgency=0.95,
                    reason=f"Drawdown {portfolio.drawdown:.1%} > 5%",
                )
                self._log_decision(result)
                return result

            # 7. Check drift threshold
            urgency = self._calculate_urgency(market, portfolio, config)
            if portfolio.max_weight_drift >= config.drift_threshold or urgency > 0.7:
                result = RebalanceResult(
                    decision=RebalanceDecision.EXECUTE,
                    regime=regime,
                    config=config,
                    urgency=urgency,
                    reason=f"Drift {portfolio.max_weight_drift:.3f} >= threshold {config.drift_threshold}",
                    execution_params={
                        "max_turnover": config.max_turnover_pct,
                        "sizing_scalar": config.sizing_scalar,
                        "allow_new_longs": config.allow_new_longs,
                        "allow_new_shorts": config.allow_new_shorts,
                        "exposure_target": config.exposure_target,
                    },
                )
                self._set_cooldown(config)
                self._log_decision(result)
                return result

            # 8. Default: DEFER
            return RebalanceResult(
                decision=RebalanceDecision.DEFER,
                regime=regime,
                config=config,
                urgency=urgency,
                reason=f"Drift {portfolio.max_weight_drift:.3f} < threshold {config.drift_threshold}",
            )

        except Exception as e:
            self.log.error("Rebalance evaluation error: %s", e)
            return RebalanceResult(
                decision=RebalanceDecision.DEFER,
                regime=EffectiveRegime.CAUTION,
                config=FAILSAFE_CONFIG,
                urgency=0.0,
                reason=f"Error: {e}",
            )

    def _is_kernel_alive(self) -> bool:
        try:
            hb = self.r.get(RebalKeys.KERNEL_HEARTBEAT)
            if not hb:
                return False
            hb_val = float(hb.decode() if isinstance(hb, bytes) else hb)
            if hb_val > 1e12:
                hb_val = hb_val / 1000.0
            return (time.time() - hb_val) < self.KERNEL_HEARTBEAT_MAX_AGE
        except Exception:
            return False

    def _determine_regime(self) -> EffectiveRegime:
        try:
            raw = self.r.hgetall(RebalKeys.REGIME_FINAL)
            if raw:
                data = {}
                for k, v in raw.items():
                    kk = k.decode() if isinstance(k, bytes) else k
                    vv = v.decode() if isinstance(v, bytes) else v
                    data[kk] = vv
                regime_str = data.get("effective", data.get("ms_regime", "CAUTION")).upper()
                mapping = {
                    "BULL": EffectiveRegime.BULL, "BULLISH": EffectiveRegime.BULL,
                    "NORMAL": EffectiveRegime.NORMAL,
                    "CAUTION": EffectiveRegime.CAUTION, "TRANSITION": EffectiveRegime.CAUTION,
                    "BEAR": EffectiveRegime.BEAR, "BEARISH": EffectiveRegime.BEAR,
                    "CRISIS": EffectiveRegime.CRISIS, "CRASH": EffectiveRegime.CRISIS,
                }
                return mapping.get(regime_str, EffectiveRegime.CAUTION)

            # Fallback: read individual keys
            for key in ["realtime:regime:ms_regime", "ares:kernel:regime"]:
                v = self.r.get(key)
                if v:
                    val = (v.decode() if isinstance(v, bytes) else v).upper()
                    if val in ("BULL", "BULLISH"):
                        return EffectiveRegime.BULL
                    elif val == "NORMAL":
                        return EffectiveRegime.NORMAL
                    elif val in ("BEAR", "BEARISH"):
                        return EffectiveRegime.BEAR
                    elif val in ("CRISIS", "CRASH"):
                        return EffectiveRegime.CRISIS
        except Exception as e:
            self.log.error("Regime determination error: %s", e)
        return EffectiveRegime.CAUTION

    def _check_force_flag(self) -> bool:
        try:
            v = self.r.get(RebalKeys.REBAL_FORCE_FLAG)
            if v:
                self.r.delete(RebalKeys.REBAL_FORCE_FLAG)
                return True
        except Exception:
            pass
        return False

    def _in_cooldown(self, config: RegimeConfig) -> bool:
        try:
            v = self.r.get(RebalKeys.REBAL_COOLDOWN_UNTIL)
            if v:
                until = float(v.decode() if isinstance(v, bytes) else v)
                return time.time() < until
        except Exception:
            pass
        return False

    def _set_cooldown(self, config: RegimeConfig):
        try:
            until = time.time() + config.cooldown_minutes * 60
            self.r.set(RebalKeys.REBAL_COOLDOWN_UNTIL, str(until))
            self.r.set(RebalKeys.REBAL_LAST_TS, str(time.time()))
        except Exception as e:
            self.log.error("Set cooldown error: %s", e)

    def _calculate_urgency(self, market: MarketContext, portfolio: PortfolioContext,
                           config: RegimeConfig) -> float:
        urgency = config.urgency_base
        if portfolio.max_weight_drift > 0 and config.drift_threshold > 0:
            drift_ratio = portfolio.max_weight_drift / config.drift_threshold
            urgency += min(0.3, drift_ratio * 0.15)
        if portfolio.drawdown > 0.03:
            urgency += 0.2
        if market.vix > config.vix_ceiling * 0.8:
            urgency += 0.1
        return min(1.0, urgency)

    def _log_decision(self, result: RebalanceResult):
        try:
            record = {
                "ts": time.time(),
                "decision": result.decision.value,
                "regime": result.regime.value,
                "urgency": result.urgency,
                "reason": result.reason,
            }
            self.r.lpush(RebalKeys.REBAL_HISTORY, json.dumps(record))
            self.r.ltrim(RebalKeys.REBAL_HISTORY, 0, 999)
            self.r.hset(RebalKeys.REBAL_DECISION, mapping={
                k: str(v) for k, v in record.items()
            })
        except Exception as e:
            self.log.error("Decision logging error: %s", e)
