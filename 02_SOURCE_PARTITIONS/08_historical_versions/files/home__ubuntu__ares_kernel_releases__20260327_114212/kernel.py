"""
ARES Trading Kernel v2.0 - Central State Machine

Replaces scattered Redis flag checks (REBAL_GATE, LATENCY_GUARD, trading:enabled,
trade:halt, ofg:gate, etc.) with a single, atomic state machine.

States: BOOT -> DATA_READY -> POS_READY -> TRADING <-> REBALANCING
        Any -> DEGRADED (partial trading) or HALTED (full stop)

All transitions are atomic via Redis Lua scripts.
Backward-compatible: writes old keys during transition period.

Author: ARES Architecture Team (4-AI Consensus Design)
Version: 2.0.0
"""

import asyncio
import json
import time
import hashlib
import os
import logging
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Set, Dict, List, Any
from pathlib import Path

logger = logging.getLogger("ares.kernel")


class KernelState(str, Enum):
    BOOT = "BOOT"
    DATA_READY = "DATA_READY"
    POS_READY = "POS_READY"
    TRADING = "TRADING"
    REBALANCING = "REBALANCING"
    DEGRADED = "DEGRADED"
    HALTED = "HALTED"


class TransitionReason(str, Enum):
    STARTUP = "startup"
    DATA_FEED_READY = "data_feed_ready"
    DATA_FEED_STALE = "data_feed_stale"
    POSITIONS_SYNCED = "positions_synced"
    POSITIONS_STALE = "positions_stale"
    ALL_READY = "all_subsystems_ready"
    REBAL_START = "rebalance_job_started"
    REBAL_COMPLETE = "rebalance_job_completed"
    REBAL_TIMEOUT = "rebalance_job_timeout"
    SYMBOL_DEGRADED = "symbol_degraded"
    SYMBOLS_RECOVERED = "symbols_recovered"
    MANUAL_HALT = "manual_halt"
    FATAL_ERROR = "fatal_error"
    RISK_BREACH = "risk_breach"
    SESSION_RESET = "session_reset"
    CIRCUIT_BREAKER = "circuit_breaker"


VALID_TRANSITIONS = {
    "BOOT": ["DATA_READY", "HALTED"],
    "DATA_READY": ["POS_READY", "DEGRADED", "HALTED", "BOOT"],
    "POS_READY": ["TRADING", "DEGRADED", "HALTED", "DATA_READY"],
    "TRADING": ["REBALANCING", "DEGRADED", "HALTED"],
    "REBALANCING": ["TRADING", "DEGRADED", "HALTED"],
    "DEGRADED": ["TRADING", "REBALANCING", "HALTED", "DATA_READY"],
    "HALTED": ["BOOT"],
}

DEFAULT_FLAGS = {
    "kernel_enforce": "1",
    "risk_enforce": "0",
    "rebalance_enforce": "0",
    "posrecon_enforce": "0",
    "degraded_allowed": "1",
    "shadow_write_compat_keys": "1",
}


@dataclass
class KernelStatus:
    state: str = "BOOT"
    prev_state: str = ""
    ts: int = 0
    reason: str = ""
    metadata: str = "{}"
    degraded_symbols: str = "[]"
    session_id: str = ""
    active_symbols: int = 0


class TradingKernel:
    """
    Central state coordinator for ARES trading system.

    Owns: ares:kernel:state, ares:kernel:status, ares:kernel:transitions,
          ares:kernel:readiness, ares:kernel:flags

    All state transitions are atomic via Lua scripts.
    """

    KEYS = {
        "state": "ares:kernel:state",
        "status": "ares:kernel:status",
        "transitions": "ares:kernel:transitions",
        "readiness": "ares:kernel:readiness",
        "flags": "ares:kernel:flags",
        "heartbeat": "ares:kernel:heartbeat",
    }

    COMPAT_KEYS = {
        "TRADING": {"trading:enabled": "true", "trade:halt": "", "ofg:gate": "OPEN"},
        "REBALANCING": {"trading:enabled": "true", "trade:halt": "", "ofg:gate": "OPEN"},
        "DEGRADED": {"trading:enabled": "true", "trade:halt": "", "ofg:gate": "OPEN"},
        "HALTED": {"trading:enabled": "false", "trade:halt": "KERNEL_HALT", "ofg:gate": "HALT"},
        "BOOT": {"trading:enabled": "false"},
        "DATA_READY": {"trading:enabled": "false"},
        "POS_READY": {"trading:enabled": "false"},
    }

    def __init__(self, redis_client, telegram_fn=None):
        self.redis = redis_client
        self.telegram = telegram_fn
        self._lua_script = None
        self._session_id = ""
        self._degraded_symbols: Set[str] = set()

    async def initialize(self):
        """Load Lua script and set initial state if not exists."""
        lua_path = Path(__file__).parent / "scripts" / "atomic_transition.lua"
        with open(lua_path) as f:
            lua_code = f.read()
        self._lua_script = self.redis.register_script(lua_code)

        for k, v in DEFAULT_FLAGS.items():
            await self.redis.hsetnx(self.KEYS["flags"], k, v)

        current = await self.redis.get(self.KEYS["state"])
        if not current:
            await self.redis.set(self.KEYS["state"], "BOOT")
            await self.redis.hset(self.KEYS["status"], mapping={
                "state": "BOOT",
                "ts": str(int(time.time() * 1000)),
                "reason": "initial_boot",
                "degraded_symbols": "[]",
                "session_id": "",
            })
            logger.info("Kernel initialized in BOOT state")

        self._session_id = (
            f"session-{int(time.time())}-"
            f"{hashlib.md5(str(time.time()).encode()).hexdigest()[:8]}"
        )
        logger.info(f"Kernel session: {self._session_id}")

    async def get_state(self) -> KernelStatus:
        """Get current kernel state atomically."""
        data = await self.redis.hgetall(self.KEYS["status"])
        if not data:
            return KernelStatus()
        return KernelStatus(
            state=data.get("state", "BOOT"),
            prev_state=data.get("prev_state", ""),
            ts=int(data.get("ts", 0)),
            reason=data.get("reason", ""),
            metadata=data.get("metadata", "{}"),
            degraded_symbols=data.get("degraded_symbols", "[]"),
            session_id=data.get("session_id", ""),
        )

    async def get_state_str(self) -> str:
        """Get just the state string."""
        s = await self.redis.get(self.KEYS["state"])
        return s or "BOOT"

    async def transition(self, target: str, reason: str, metadata: dict = None) -> dict:
        """
        Atomically validate and execute state transition.
        Returns: {"ok": True/False, "from": str, "to": str, "error": str}
        """
        if metadata is None:
            metadata = {}

        ts = str(int(time.time() * 1000))
        degraded_json = json.dumps(sorted(self._degraded_symbols))

        result_raw = await self._lua_script(
            keys=[
                self.KEYS["state"],
                self.KEYS["status"],
                self.KEYS["transitions"],
                self.KEYS["readiness"],
            ],
            args=[
                target,
                reason,
                json.dumps(metadata),
                ts,
                json.dumps(VALID_TRANSITIONS),
                degraded_json,
                self._session_id,
            ]
        )

        result = json.loads(result_raw)

        if result.get("ok"):
            logger.info(
                f"KERNEL_TRANSITION: {result['from']} -> {result['to']} "
                f"reason={reason}"
            )
            if await self._flag_enabled("shadow_write_compat_keys"):
                await self._write_compat_keys(target)
            await self.redis.set(self.KEYS["heartbeat"], ts, ex=120)
            if target in ("HALTED", "DEGRADED"):
                msg = (
                    f"KERNEL: {result['from']} -> {target}\n"
                    f"Reason: {reason}"
                )
                if self.telegram:
                    try:
                        await self.telegram(msg)
                    except Exception as e:
                        logger.error(f"Telegram alert failed: {e}")
        else:
            logger.warning(
                f"KERNEL_TRANSITION_REJECTED: {result.get('error')} "
                f"current={result.get('current')} target={target}"
            )

        return result

    async def can_trade(self, symbol: str = None) -> bool:
        """Check if trading is allowed, optionally for a specific symbol."""
        state = await self.get_state_str()
        if state not in ("TRADING", "REBALANCING", "DEGRADED"):
            return False
        if symbol and state == "DEGRADED":
            return symbol not in self._degraded_symbols
        return True

    async def is_rebalancing(self) -> bool:
        """Check if currently in rebalancing state."""
        return await self.get_state_str() == "REBALANCING"

    async def enter_degraded(self, symbols: List[str], reason: str):
        """Move to DEGRADED and record excluded symbols."""
        self._degraded_symbols.update(symbols)
        await self.transition("DEGRADED", reason, {"affected_symbols": symbols})
        for sym in symbols:
            await self.redis.set(f"ares:kernel:degraded:{sym}", "1", ex=3600)

    async def recover_symbols(self, symbols: List[str]):
        """Remove symbols from degraded list."""
        self._degraded_symbols -= set(symbols)
        for sym in symbols:
            await self.redis.delete(f"ares:kernel:degraded:{sym}")
        if not self._degraded_symbols:
            current = await self.get_state_str()
            if current == "DEGRADED":
                await self.transition(
                    "TRADING", TransitionReason.SYMBOLS_RECOVERED
                )

    async def update_readiness(self, subsystem: str, ready: bool, detail: str = ""):
        """Update readiness status for a subsystem."""
        await self.redis.hset(
            self.KEYS["readiness"],
            mapping={
                f"{subsystem}_ready": "1" if ready else "0",
                f"{subsystem}_detail": detail,
                f"{subsystem}_ts": str(int(time.time() * 1000)),
            }
        )

    async def check_all_ready(self) -> dict:
        """Check if all subsystems are ready."""
        data = await self.redis.hgetall(self.KEYS["readiness"])
        subsystems = ["data_feed", "positions", "risk", "broker"]
        result = {}
        for sub in subsystems:
            result[sub] = data.get(f"{sub}_ready", "0") == "1"
        result["all_ready"] = all(result.values())
        return result

    async def get_flags(self) -> dict:
        """Get all feature flags."""
        return await self.redis.hgetall(self.KEYS["flags"])

    async def set_flag(self, flag: str, value: str):
        """Set a feature flag."""
        await self.redis.hset(self.KEYS["flags"], flag, value)
        logger.info(f"KERNEL_FLAG_SET: {flag}={value}")

    async def _flag_enabled(self, flag: str) -> bool:
        """Check if a feature flag is enabled."""
        val = await self.redis.hget(self.KEYS["flags"], flag)
        return val == "1"

    async def _write_compat_keys(self, state: str):
        """Write backward-compatible Redis keys for transition period."""
        compat = self.COMPAT_KEYS.get(state, {})
        pipe = self.redis.pipeline()
        for key, value in compat.items():
            if value:
                pipe.set(key, value)
            else:
                pipe.delete(key)
        await pipe.execute()

    async def heartbeat(self):
        """Update heartbeat timestamp."""
        ts = str(int(time.time() * 1000))
        await self.redis.set(self.KEYS["heartbeat"], ts, ex=120)
