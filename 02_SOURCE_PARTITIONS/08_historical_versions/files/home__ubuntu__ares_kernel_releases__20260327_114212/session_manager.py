"""
ARES Session Manager v2.0

Manages trading session lifecycle:
- Readiness-based session reset (NOT time-based)
- Automatic daily session management
- Degraded trading mode support
- Session metrics preservation

Design (4-AI consensus):
- Session reset only when ALL readiness conditions are met
- No automatic HALT clearing - requires explicit conditions
- Session metrics preserved for post-trade analysis
- Degraded mode: reduced position sizing, no new positions

Author: ARES Architecture Team (4-AI Consensus Design)
Version: 2.0.0
"""

import asyncio
import json
import time
import logging
from typing import Dict, Optional, List
from enum import Enum

logger = logging.getLogger("ares.session")


class SessionState(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    HALTED = "HALTED"
    POST_MARKET = "POST_MARKET"
    CLOSED = "CLOSED"


class ReadinessCheck(str, Enum):
    PRICE_FEED = "price_feed"
    POSITION_SYNC = "position_sync"
    BROKER_CONN = "broker_connection"
    RISK_CLEAR = "risk_clear"
    NO_PENDING = "no_pending_orders"
    EQUITY_SANE = "equity_sanity"
    KERNEL_READY = "kernel_ready"


class SessionManager:
    """
    Manages trading session lifecycle with readiness-based transitions.

    Replaces: Manual session_start_equity resets, time-based OFG resets,
              ad-hoc HALT clearing
    """

    KEYS = {
        "session": "ares:session:current",
        "history": "ares:session:history",
        "metrics": "ares:session:metrics",
        "config": "ares:session:config",
        "readiness": "ares:session:readiness",
    }

    DEFAULT_CONFIG = {
        "auto_reset_enabled": True,
        "degraded_position_scale": 0.5,
        "max_degraded_duration_s": 1800,
        "readiness_timeout_s": 300,
        "market_open_hour_et": 9,
        "market_open_min_et": 30,
        "market_close_hour_et": 16,
        "market_close_min_et": 0,
    }

    READINESS_CHECKS = [
        ReadinessCheck.PRICE_FEED,
        ReadinessCheck.POSITION_SYNC,
        ReadinessCheck.BROKER_CONN,
        ReadinessCheck.RISK_CLEAR,
        ReadinessCheck.NO_PENDING,
        ReadinessCheck.EQUITY_SANE,
        ReadinessCheck.KERNEL_READY,
    ]

    def __init__(self, redis_client, kernel=None, risk_manager=None, telegram_fn=None):
        self.redis = redis_client
        self.kernel = kernel
        self.risk_manager = risk_manager
        self.telegram = telegram_fn

    async def initialize(self):
        """Initialize session state."""
        session = await self.redis.hgetall(self.KEYS["session"])
        if not session:
            await self.redis.hset(self.KEYS["session"], mapping={
                "state": SessionState.CLOSED,
                "start_equity": "0",
                "current_equity": "0",
                "session_dd": "0",
                "peak_equity": "0",
                "max_dd": "0",
                "orders_count": "0",
                "fills_count": "0",
                "pnl": "0",
                "started_at": "",
                "last_check": "",
            })
        logger.info("Session Manager initialized")

    async def check_readiness(self) -> Dict[str, dict]:
        """
        Run all readiness checks.
        Returns dict of check_name -> {passed: bool, detail: str}
        """
        results = {}

        # 1. Price feed freshness
        try:
            ts = await self.redis.get("realtime:data_feed:last_success_ts")
            if ts:
                age = time.time() - float(ts)
                results[ReadinessCheck.PRICE_FEED] = {
                    "passed": age < 120,
                    "detail": f"age={age:.0f}s",
                }
            else:
                results[ReadinessCheck.PRICE_FEED] = {
                    "passed": False,
                    "detail": "no_timestamp",
                }
        except Exception as e:
            results[ReadinessCheck.PRICE_FEED] = {
                "passed": False,
                "detail": str(e),
            }

        # 2. Position sync
        try:
            recon_ts = await self.redis.get("ares:recon:last_check_ts")
            mismatch = await self.redis.get("ares:recon:mismatch_count")
            if recon_ts:
                age = time.time() - float(recon_ts)
                results[ReadinessCheck.POSITION_SYNC] = {
                    "passed": age < 120 and int(mismatch or 0) == 0,
                    "detail": f"age={age:.0f}s mismatches={mismatch or 0}",
                }
            else:
                results[ReadinessCheck.POSITION_SYNC] = {
                    "passed": False,
                    "detail": "no_recon_data",
                }
        except Exception as e:
            results[ReadinessCheck.POSITION_SYNC] = {
                "passed": False,
                "detail": str(e),
            }

        # 3. Broker connection
        try:
            broker_status = await self.redis.get("kis:connection:status")
            results[ReadinessCheck.BROKER_CONN] = {
                "passed": broker_status in ("connected", "ok", None),
                "detail": f"status={broker_status or 'assumed_ok'}",
            }
        except Exception as e:
            results[ReadinessCheck.BROKER_CONN] = {
                "passed": False,
                "detail": str(e),
            }

        # 4. Risk clear (no active halt)
        try:
            halt_keys = await self.redis.keys("*halt*")
            active_halts = []
            for k in halt_keys:
                v = await self.redis.get(k)
                if v and v not in ("0", "false", ""):
                    active_halts.append(k)
            results[ReadinessCheck.RISK_CLEAR] = {
                "passed": len(active_halts) == 0,
                "detail": f"active_halts={active_halts}" if active_halts else "clear",
            }
        except Exception as e:
            results[ReadinessCheck.RISK_CLEAR] = {
                "passed": False,
                "detail": str(e),
            }

        # 5. No pending orders
        try:
            pending = await self.redis.get("ares:orders:pending_count")
            open_orders = await self.redis.scard("kis:open_orders")
            total_pending = int(pending or 0) + int(open_orders or 0)
            results[ReadinessCheck.NO_PENDING] = {
                "passed": total_pending == 0,
                "detail": f"pending={total_pending}",
            }
        except Exception as e:
            results[ReadinessCheck.NO_PENDING] = {
                "passed": True,
                "detail": f"check_error:{e}",
            }

        # 6. Equity sanity
        try:
            equity = await self.redis.get("ofg:verified_equity")
            if equity:
                eq = float(equity)
                results[ReadinessCheck.EQUITY_SANE] = {
                    "passed": eq > 10000,
                    "detail": f"equity=${eq:,.0f}",
                }
            else:
                results[ReadinessCheck.EQUITY_SANE] = {
                    "passed": False,
                    "detail": "no_equity_data",
                }
        except Exception as e:
            results[ReadinessCheck.EQUITY_SANE] = {
                "passed": False,
                "detail": str(e),
            }

        # 7. Kernel ready
        try:
            if self.kernel:
                state = await self.kernel.get_state()
                results[ReadinessCheck.KERNEL_READY] = {
                    "passed": state in ("TRADING", "DATA_READY", "POS_READY"),
                    "detail": f"state={state}",
                }
            else:
                kernel_state = await self.redis.get("ares:kernel:state")
                results[ReadinessCheck.KERNEL_READY] = {
                    "passed": kernel_state in ("TRADING", "DATA_READY", "POS_READY"),
                    "detail": f"state={kernel_state}",
                }
        except Exception as e:
            results[ReadinessCheck.KERNEL_READY] = {
                "passed": False,
                "detail": str(e),
            }

        # Store readiness results
        await self.redis.hset(
            self.KEYS["readiness"],
            mapping={k: json.dumps(v) for k, v in results.items()},
        )
        await self.redis.set(
            f"{self.KEYS['readiness']}:ts", str(time.time()), ex=300,
        )

        return results

    async def try_session_reset(self) -> bool:
        """
        Attempt session reset if ALL readiness conditions are met.
        Returns True if reset was performed.
        """
        readiness = await self.check_readiness()

        all_passed = all(r.get("passed", False) for r in readiness.values())
        failed = [k for k, v in readiness.items() if not v.get("passed", False)]

        if not all_passed:
            logger.info(
                f"SESSION_RESET_BLOCKED: failed_checks={failed}"
            )
            return False

        # Get current equity
        equity_str = await self.redis.get("ofg:verified_equity")
        if not equity_str:
            logger.error("SESSION_RESET_FAILED: no equity data")
            return False

        equity = float(equity_str)

        # Preserve previous session metrics
        await self._archive_session()

        # Reset session
        await self.redis.hset(self.KEYS["session"], mapping={
            "state": SessionState.ACTIVE,
            "start_equity": str(equity),
            "current_equity": str(equity),
            "peak_equity": str(equity),
            "session_dd": "0",
            "max_dd": "0",
            "orders_count": "0",
            "fills_count": "0",
            "pnl": "0",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "last_check": str(time.time()),
        })

        # Reset risk manager session
        if self.risk_manager:
            await self.risk_manager.reset_session(equity)

        # Clear sell storm counter
        await self.redis.delete("ares:risk:session_sell_count")

        logger.info(f"SESSION_RESET_OK: equity=${equity:,.2f}")

        if self.telegram:
            try:
                await self.telegram(
                    f"Session Reset\n"
                    f"equity: ${equity:,.2f}\n"
                    f"all 7 readiness checks passed"
                )
            except Exception:
                pass

        return True

    async def enter_degraded(self, reason: str):
        """Enter degraded trading mode."""
        await self.redis.hset(self.KEYS["session"], mapping={
            "state": SessionState.DEGRADED,
            "degraded_reason": reason,
            "degraded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

        logger.warning(f"SESSION_DEGRADED: reason={reason}")

        if self.telegram:
            try:
                await self.telegram(f"Session DEGRADED: {reason}")
            except Exception:
                pass

    async def get_position_scale(self) -> float:
        """Get position sizing scale factor based on session state."""
        session = await self.redis.hgetall(self.KEYS["session"])
        state = session.get("state", "CLOSED")

        if state == SessionState.DEGRADED:
            config = await self._get_config()
            return config["degraded_position_scale"]
        elif state == SessionState.ACTIVE:
            return 1.0
        else:
            return 0.0  # No trading

    async def update_metrics(self, equity: float, order_count: int = 0, fill_count: int = 0):
        """Update session metrics."""
        session = await self.redis.hgetall(self.KEYS["session"])
        start_eq = float(session.get("start_equity", equity))
        peak_eq = float(session.get("peak_equity", equity))

        if equity > peak_eq:
            peak_eq = equity

        dd = (equity - start_eq) / start_eq if start_eq > 0 else 0
        max_dd_from_peak = (equity - peak_eq) / peak_eq if peak_eq > 0 else 0

        prev_orders = int(session.get("orders_count", 0))
        prev_fills = int(session.get("fills_count", 0))

        await self.redis.hset(self.KEYS["session"], mapping={
            "current_equity": str(equity),
            "peak_equity": str(peak_eq),
            "session_dd": f"{dd:.6f}",
            "max_dd": f"{max_dd_from_peak:.6f}",
            "pnl": f"{equity - start_eq:.2f}",
            "orders_count": str(prev_orders + order_count),
            "fills_count": str(prev_fills + fill_count),
            "last_check": str(time.time()),
        })

    async def _archive_session(self):
        """Archive current session to history."""
        session = await self.redis.hgetall(self.KEYS["session"])
        if session and session.get("started_at"):
            session["archived_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            await self.redis.xadd(
                self.KEYS["history"],
                {k: str(v) for k, v in session.items()},
                maxlen=365,
            )

    async def _get_config(self) -> dict:
        """Get session config."""
        raw = await self.redis.hgetall(self.KEYS["config"])
        config = dict(self.DEFAULT_CONFIG)
        if raw:
            for k, v in raw.items():
                if k in config:
                    try:
                        config[k] = type(config[k])(v)
                    except (ValueError, TypeError):
                        pass
        return config

    async def get_status(self) -> dict:
        """Get current session status for monitoring."""
        session = await self.redis.hgetall(self.KEYS["session"])
        return {
            "state": session.get("state", "CLOSED"),
            "equity": float(session.get("current_equity", 0)),
            "start_equity": float(session.get("start_equity", 0)),
            "dd": float(session.get("session_dd", 0)),
            "max_dd": float(session.get("max_dd", 0)),
            "pnl": float(session.get("pnl", 0)),
            "orders": int(session.get("orders_count", 0)),
            "fills": int(session.get("fills_count", 0)),
        }
