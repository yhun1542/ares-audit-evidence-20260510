"""
ARES Unified Risk Manager v2.0

Consolidates all risk checks into a single coherent module:
- Session drawdown monitoring (replaces OFG circuit breaker)
- Rate limiting (replaces OFG rate_limit with context awareness)
- Anomaly detection (replaces SELL_STORM with rebalance-aware logic)
- Data freshness (replaces LATENCY_GUARD)
- Position sanity (replaces scattered position checks)

Key design (4-AI consensus):
- Rebalance-aware: knows when system is rebalancing, adjusts thresholds
- Context-based: different limits for NORMAL vs REBALANCE mode
- Single evaluation point: one check, one decision
- Auditable: every decision logged to stream

Author: ARES Architecture Team (4-AI Consensus Design)
Version: 2.0.0
"""

import asyncio
import json
import time
import logging
from dataclasses import dataclass, asdict
from typing import Dict, Optional, List
from enum import Enum

logger = logging.getLogger("ares.risk")


class RiskDecision(str, Enum):
    ALLOW = "ALLOW"
    THROTTLE = "THROTTLE"
    BLOCK = "BLOCK"
    HALT = "HALT"


class TradingContext(str, Enum):
    NORMAL = "NORMAL"
    REBALANCE = "REBALANCE"


@dataclass
class RiskCheckResult:
    decision: str
    context: str
    checks: Dict[str, dict]
    throttle_ms: int = 0
    block_reason: str = ""
    timestamp: str = ""


class UnifiedRiskManager:
    """
    Single risk evaluation point for the entire trading system.
    
    Replaces: OFG circuit_breaker, OFG rate_limit, OFG SELL_STORM,
              LATENCY_GUARD, REBAL_GATE
    """

    KEYS = {
        "config": "ares:risk:config",
        "state": "ares:risk:state",
        "decisions": "ares:risk:decisions",
        "session": "ares:risk:session",
        "rate_window": "ares:risk:rate_window",
    }

    DEFAULT_CONFIG = {
        # Session drawdown
        "session_dd_limit": -0.03,           # -3% session drawdown halt
        "session_dd_warn": -0.015,           # -1.5% warning
        # Rate limits by context
        "normal_rate_per_sec": 5,
        "rebalance_rate_per_sec": 3,         # Lower during rebalance (paced)
        # Anomaly thresholds by context
        "normal_sell_storm_threshold": 30,
        "rebalance_sell_storm_threshold": 200,  # Higher during rebalance
        # Data freshness
        "price_max_age_s": 600,
        "position_max_age_s": 600,
        # Throttle delays
        "normal_throttle_ms": 500,
        "rebalance_throttle_ms": 250,        # Faster during rebalance
    }

    def __init__(self, redis_client, kernel_client=None, telegram_fn=None):
        self.redis = redis_client
        self.kernel = kernel_client
        self.telegram = telegram_fn
        self._config = dict(self.DEFAULT_CONFIG)
        self._session_start_equity = None
        self._order_timestamps = []

    async def initialize(self):
        """Load config and session state."""
        # Load config from Redis
        raw = await self.redis.hgetall(self.KEYS["config"])
        if raw:
            for k, v in raw.items():
                if k in self._config:
                    try:
                        self._config[k] = type(self._config[k])(v)
                    except (ValueError, TypeError):
                        pass

        # Load session state
        session = await self.redis.hgetall(self.KEYS["session"])
        if session:
            self._session_start_equity = float(session.get("start_equity", 0))

        logger.info(f"Risk Manager initialized: config={json.dumps(self._config)}")

    async def evaluate(self, order_side: str = None, symbol: str = None) -> RiskCheckResult:
        """
        Single evaluation point for all risk checks.
        
        Returns RiskCheckResult with decision and details.
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        
        # Determine context
        context = TradingContext.NORMAL
        if self.kernel:
            try:
                if await self.kernel.is_rebalancing():
                    context = TradingContext.REBALANCE
            except Exception:
                pass

        checks = {}

        # 1. Session Drawdown Check
        dd_result = await self._check_session_drawdown()
        checks["session_dd"] = dd_result

        # 2. Rate Limit Check
        rate_result = await self._check_rate_limit(context)
        checks["rate_limit"] = rate_result

        # 3. Anomaly Check (SELL_STORM)
        if order_side:
            anomaly_result = await self._check_anomaly(order_side, context)
            checks["anomaly"] = anomaly_result

        # 4. Data Freshness Check
        freshness_result = await self._check_data_freshness()
        checks["data_freshness"] = freshness_result

        # Determine overall decision (worst wins)
        decision = RiskDecision.ALLOW
        block_reason = ""
        throttle_ms = 0

        for name, check in checks.items():
            if check.get("decision") == "HALT":
                decision = RiskDecision.HALT
                block_reason = f"{name}:{check.get('reason', '')}"
                break
            elif check.get("decision") == "BLOCK":
                decision = RiskDecision.BLOCK
                block_reason = f"{name}:{check.get('reason', '')}"
            elif check.get("decision") == "THROTTLE" and decision == RiskDecision.ALLOW:
                decision = RiskDecision.THROTTLE
                throttle_ms = check.get("throttle_ms", 250)

        result = RiskCheckResult(
            decision=decision,
            context=context,
            checks=checks,
            throttle_ms=throttle_ms,
            block_reason=block_reason,
            timestamp=ts,
        )

        # Log decision to stream
        await self.redis.xadd(
            self.KEYS["decisions"],
            {
                "decision": decision,
                "context": context,
                "block_reason": block_reason,
                "symbol": symbol or "",
                "side": order_side or "",
                "ts": ts,
            },
            maxlen=5000,
        )

        if decision in (RiskDecision.HALT, RiskDecision.BLOCK):
            logger.warning(
                f"RISK_{decision}: context={context} reason={block_reason} "
                f"symbol={symbol}"
            )

        return result

    async def _check_session_drawdown(self) -> dict:
        """Check session drawdown against limits."""
        try:
            equity_str = await self.redis.get("ofg:verified_equity")
            if not equity_str:
                equity_str = await self.redis.get("emarkos:v1:equity:current")
            if not equity_str:
                return {"decision": "ALLOW", "reason": "no_equity_data"}

            current_equity = float(equity_str)

            if not self._session_start_equity or self._session_start_equity <= 0:
                # Initialize session
                self._session_start_equity = current_equity
                await self.redis.hset(self.KEYS["session"], mapping={
                    "start_equity": str(current_equity),
                    "start_ts": str(time.time()),
                })
                return {"decision": "ALLOW", "reason": "session_initialized"}

            dd = (current_equity - self._session_start_equity) / self._session_start_equity

            if dd <= self._config["session_dd_limit"]:
                return {
                    "decision": "HALT",
                    "reason": f"session_dd={dd:.4f}<=limit={self._config['session_dd_limit']}",
                    "dd": dd,
                    "equity": current_equity,
                    "start_equity": self._session_start_equity,
                }
            elif dd <= self._config["session_dd_warn"]:
                return {
                    "decision": "THROTTLE",
                    "reason": f"session_dd_warning={dd:.4f}",
                    "throttle_ms": 1000,
                    "dd": dd,
                }
            else:
                return {"decision": "ALLOW", "dd": dd}

        except Exception as e:
            logger.error(f"Session DD check failed: {e}")
            return {"decision": "ALLOW", "reason": f"check_error:{e}"}

    async def _check_rate_limit(self, context: TradingContext) -> dict:
        """Check order rate against context-appropriate limits."""
        now = time.time()
        # Clean old timestamps
        self._order_timestamps = [t for t in self._order_timestamps if now - t < 1.0]
        self._order_timestamps.append(now)

        rate = len(self._order_timestamps)
        limit = (
            self._config["rebalance_rate_per_sec"]
            if context == TradingContext.REBALANCE
            else self._config["normal_rate_per_sec"]
        )

        if rate > limit * 2:
            return {
                "decision": "BLOCK",
                "reason": f"rate={rate}/sec>hard_limit={limit*2}",
                "rate": rate,
                "limit": limit,
            }
        elif rate > limit:
            throttle = (
                self._config["rebalance_throttle_ms"]
                if context == TradingContext.REBALANCE
                else self._config["normal_throttle_ms"]
            )
            return {
                "decision": "THROTTLE",
                "reason": f"rate={rate}/sec>soft_limit={limit}",
                "throttle_ms": throttle,
                "rate": rate,
                "limit": limit,
            }
        else:
            return {"decision": "ALLOW", "rate": rate, "limit": limit}

    async def _check_anomaly(self, side: str, context: TradingContext) -> dict:
        """Check for anomalous order patterns."""
        try:
            # Count recent orders by side
            sell_count_str = await self.redis.get("ares:risk:session_sell_count")
            sell_count = int(sell_count_str) if sell_count_str else 0

            if side.upper() == "SELL":
                sell_count += 1
                await self.redis.set("ares:risk:session_sell_count", str(sell_count), ex=3600)

            threshold = (
                self._config["rebalance_sell_storm_threshold"]
                if context == TradingContext.REBALANCE
                else self._config["normal_sell_storm_threshold"]
            )

            if sell_count > threshold:
                return {
                    "decision": "BLOCK",
                    "reason": f"sell_storm:{sell_count}>{threshold}",
                    "sell_count": sell_count,
                    "threshold": threshold,
                    "context": context,
                }
            elif sell_count > threshold * 0.8:
                return {
                    "decision": "THROTTLE",
                    "reason": f"sell_storm_warning:{sell_count}>{threshold*0.8:.0f}",
                    "throttle_ms": 500,
                }
            else:
                return {"decision": "ALLOW", "sell_count": sell_count}

        except Exception as e:
            return {"decision": "ALLOW", "reason": f"check_error:{e}"}

    async def _check_data_freshness(self) -> dict:
        """Check if price and position data are fresh."""
        try:
            # Price feed freshness
            price_ts = await self.redis.get("realtime:data_feed:last_success_ts")
            price_age = None
            if price_ts:
                price_age = time.time() - float(price_ts)

            # Position freshness
            pos_ts = await self.redis.get("kis:broker:positions:ts")
            pos_age = None
            if pos_ts:
                if "T" in str(pos_ts):
                    from datetime import datetime
                    dt = datetime.fromisoformat(pos_ts.replace("Z", "+00:00"))
                    pos_age = time.time() - dt.timestamp()
                else:
                    pos_age = time.time() - float(pos_ts)

            issues = []
            if price_age and price_age > self._config["price_max_age_s"]:
                issues.append(f"price_stale:{price_age:.0f}s")
            if pos_age and pos_age > self._config["position_max_age_s"]:
                issues.append(f"position_stale:{pos_age:.0f}s")

            if issues:
                return {
                    "decision": "BLOCK",
                    "reason": ",".join(issues),
                    "price_age": price_age,
                    "position_age": pos_age,
                }
            else:
                return {
                    "decision": "ALLOW",
                    "price_age": price_age,
                    "position_age": pos_age,
                }

        except Exception as e:
            return {"decision": "ALLOW", "reason": f"check_error:{e}"}

    async def reset_session(self, new_equity: float = None):
        """Reset session for new trading day or manual reset."""
        if new_equity is None:
            equity_str = await self.redis.get("ofg:verified_equity")
            if equity_str:
                new_equity = float(equity_str)
            else:
                logger.error("Cannot reset session: no equity data")
                return

        self._session_start_equity = new_equity
        await self.redis.hset(self.KEYS["session"], mapping={
            "start_equity": str(new_equity),
            "start_ts": str(time.time()),
            "reset_reason": "manual_or_daily",
        })
        await self.redis.delete("ares:risk:session_sell_count")
        self._order_timestamps.clear()

        logger.info(f"RISK_SESSION_RESET: equity={new_equity}")
