"""
ARES Kernel Client v2.0 - Lightweight read-only client for other services.

Used by OIE, OFG, realtime-data-feed, etc. to check kernel state
without needing the full TradingKernel class.

Usage:
    client = KernelClient(redis_client)
    if await client.can_trade("AAPL"):
        # proceed with order
"""

import json
import logging
from typing import Optional, List

logger = logging.getLogger("ares.kernel.client")


class KernelClient:
    """Read-only client for checking kernel state from any service."""

    KEYS = {
        "state": "ares:kernel:state",
        "status": "ares:kernel:status",
        "flags": "ares:kernel:flags",
        "readiness": "ares:kernel:readiness",
    }

    TRADEABLE_STATES = {"TRADING", "REBALANCING", "DEGRADED"}

    def __init__(self, redis_client):
        self.redis = redis_client

    async def get_state(self) -> str:
        """Get current kernel state string."""
        s = await self.redis.get(self.KEYS["state"])
        return s or "BOOT"

    async def can_trade(self, symbol: str = None) -> bool:
        """Check if trading is allowed."""
        state = await self.get_state()
        if state not in self.TRADEABLE_STATES:
            return False
        if symbol and state == "DEGRADED":
            degraded = await self.redis.get(f"ares:kernel:degraded:{symbol}")
            return not degraded
        return True

    async def is_rebalancing(self) -> bool:
        """Check if in rebalancing mode."""
        return await self.get_state() == "REBALANCING"

    async def get_degraded_symbols(self) -> List[str]:
        """Get list of degraded symbols."""
        data = await self.redis.hget(self.KEYS["status"], "degraded_symbols")
        if data:
            return json.loads(data)
        return []

    async def get_context(self) -> str:
        """Get trading context: REBALANCE or NORMAL."""
        state = await self.get_state()
        return "REBALANCE" if state == "REBALANCING" else "NORMAL"

    async def flag_enabled(self, flag: str) -> bool:
        """Check if a feature flag is enabled."""
        val = await self.redis.hget(self.KEYS["flags"], flag)
        return val == "1"

    async def get_full_status(self) -> dict:
        """Get full kernel status hash."""
        return await self.redis.hgetall(self.KEYS["status"])
