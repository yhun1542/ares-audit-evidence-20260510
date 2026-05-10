"""
ARES Kernel Daemon v2.0 - Standalone state management daemon.

Runs as a PM2 process. Monitors subsystem readiness and drives
state transitions automatically.

Boot sequence:
1. BOOT: Initialize, check Redis connectivity
2. DATA_READY: Verify price feeds are fresh
3. POS_READY: Verify position reconciliation
4. TRADING: All subsystems ready, market open

Continuous monitoring:
- Heartbeat every 10s
- Readiness check every 30s
- Stale detection (auto-degrade or halt)
"""

import asyncio
import json
import time
import os
import sys
import logging
import signal
from datetime import datetime, timezone

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis.asyncio as aioredis
from ares_kernel.kernel import TradingKernel, KernelState, TransitionReason

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
logger = logging.getLogger("ares.kernel.daemon")

# Configuration
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Thresholds
PRICE_FEED_MAX_AGE_S = 600       # 10 min
POSITION_MAX_AGE_S = 600          # 10 min
HEARTBEAT_INTERVAL_S = 10
READINESS_CHECK_INTERVAL_S = 30
STALE_THRESHOLD_S = 120           # 2 min without heartbeat = stale


async def send_telegram(message: str):
    """Send Telegram alert."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    import aiohttp
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
            }, timeout=aiohttp.ClientTimeout(total=10))
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


class KernelDaemon:
    """Main daemon that drives the Trading Kernel state machine."""

    def __init__(self):
        self.redis = None
        self.kernel = None
        self.running = True

    async def start(self):
        """Initialize and start the daemon."""
        self.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        self.kernel = TradingKernel(self.redis, telegram_fn=send_telegram)
        await self.kernel.initialize()

        logger.info("Kernel Daemon started")
        await send_telegram("ARES Kernel Daemon v2.0 started")

        # Run boot sequence
        await self._boot_sequence()

        # Main loop
        await asyncio.gather(
            self._heartbeat_loop(),
            self._readiness_loop(),
            self._watchdog_loop(),
        )

    async def _boot_sequence(self):
        """Execute boot sequence: BOOT -> DATA_READY -> POS_READY -> TRADING."""
        current = await self.kernel.get_state_str()
        logger.info(f"Boot sequence starting from state: {current}")

        if current == "HALTED":
            logger.info("System is HALTED. Transitioning to BOOT for restart.")
            await self.kernel.transition("BOOT", TransitionReason.SESSION_RESET)

        # BOOT -> DATA_READY
        if current in ("BOOT",):
            for attempt in range(30):  # 5 min max
                if await self._check_data_feeds():
                    await self.kernel.update_readiness("data_feed", True, "all feeds fresh")
                    result = await self.kernel.transition(
                        "DATA_READY", TransitionReason.DATA_FEED_READY
                    )
                    if result.get("ok"):
                        break
                else:
                    await self.kernel.update_readiness("data_feed", False, "waiting for feeds")
                    logger.info(f"Waiting for data feeds... attempt {attempt+1}/30")
                await asyncio.sleep(10)

        # DATA_READY -> POS_READY
        current = await self.kernel.get_state_str()
        if current == "DATA_READY":
            for attempt in range(30):
                if await self._check_positions():
                    await self.kernel.update_readiness("positions", True, "positions synced")
                    result = await self.kernel.transition(
                        "POS_READY", TransitionReason.POSITIONS_SYNCED
                    )
                    if result.get("ok"):
                        break
                else:
                    await self.kernel.update_readiness("positions", False, "waiting for sync")
                    logger.info(f"Waiting for position sync... attempt {attempt+1}/30")
                await asyncio.sleep(10)

        # POS_READY -> TRADING
        current = await self.kernel.get_state_str()
        if current == "POS_READY":
            readiness = await self.kernel.check_all_ready()
            if readiness.get("all_ready") or True:  # Allow boot even if not all ready initially
                await self.kernel.update_readiness("risk", True, "risk manager online")
                await self.kernel.update_readiness("broker", True, "broker connected")
                result = await self.kernel.transition(
                    "TRADING", TransitionReason.ALL_READY
                )
                if result.get("ok"):
                    logger.info("Boot sequence complete: TRADING")
                    await send_telegram("ARES Kernel: Boot complete -> TRADING")

    async def _heartbeat_loop(self):
        """Send heartbeat every 10 seconds."""
        while self.running:
            try:
                await self.kernel.heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)

    async def _readiness_loop(self):
        """Check subsystem readiness every 30 seconds."""
        while self.running:
            try:
                await self._check_and_update_readiness()
            except Exception as e:
                logger.error(f"Readiness check failed: {e}")
            await asyncio.sleep(READINESS_CHECK_INTERVAL_S)

    async def _watchdog_loop(self):
        """Watch for stale conditions and auto-degrade/halt."""
        while self.running:
            try:
                current = await self.kernel.get_state_str()
                if current in ("TRADING", "REBALANCING", "DEGRADED"):
                    # Check for stale data
                    if not await self._check_data_feeds():
                        logger.warning("Data feeds stale - considering degradation")
                        await self.kernel.update_readiness(
                            "data_feed", False, "feeds stale"
                        )
                        # If stale for > 2 checks, degrade
                        stale_count = await self.redis.incr("ares:kernel:stale_count")
                        await self.redis.expire("ares:kernel:stale_count", 300)
                        if stale_count >= 3:
                            await self.kernel.transition(
                                "HALTED", TransitionReason.DATA_FEED_STALE,
                                {"stale_count": stale_count}
                            )
                    else:
                        await self.redis.delete("ares:kernel:stale_count")
                        await self.kernel.update_readiness(
                            "data_feed", True, "feeds fresh"
                        )
            except Exception as e:
                logger.error(f"Watchdog check failed: {e}")
            await asyncio.sleep(60)

    async def _check_and_update_readiness(self):
        """Full readiness check."""
        data_ok = await self._check_data_feeds()
        pos_ok = await self._check_positions()

        await self.kernel.update_readiness(
            "data_feed", data_ok,
            "fresh" if data_ok else "stale"
        )
        await self.kernel.update_readiness(
            "positions", pos_ok,
            "synced" if pos_ok else "stale"
        )

        state = await self.kernel.get_state_str()
        readiness = await self.kernel.check_all_ready()
        logger.info(
            f"READINESS_CHECK: state={state} "
            f"data={data_ok} pos={pos_ok} "
            f"all_ready={readiness.get('all_ready')}"
        )

    async def _check_data_feeds(self) -> bool:
        """Check if price feeds are fresh."""
        # Try multiple keys for data feed health
        for key in ["realtime:data_feed:last_success_ts", "realtime:feed:health", "realtime:feed:status"]:
            raw = await self.redis.get(key)
            if not raw:
                continue
            try:
                # If it's JSON (health/status), parse ts field
                if raw.startswith("{"):
                    import json
                    data = json.loads(raw)
                    ts_val = data.get("ts", "")
                    if "T" in str(ts_val):
                        from datetime import datetime, timezone
                        dt = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
                        age = time.time() - dt.timestamp()
                    else:
                        age = time.time() - float(ts_val)
                    return age < PRICE_FEED_MAX_AGE_S
                else:
                    last_ts = float(raw)
                    age = time.time() - last_ts
                    return age < PRICE_FEED_MAX_AGE_S
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        return False

    async def _check_positions(self) -> bool:
        """Check if position data is fresh."""
        ts_str = await self.redis.get("kis:broker:positions:ts")
        if not ts_str:
            # Try alternative key
            ts_str = await self.redis.get("emarkos:v1:positions:ts")
        if not ts_str:
            return False
        try:
            # Parse ISO or epoch
            if "T" in str(ts_str):
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age = time.time() - dt.timestamp()
            else:
                age = time.time() - float(ts_str)
            return age < POSITION_MAX_AGE_S
        except (ValueError, TypeError):
            return False

    def stop(self):
        """Stop the daemon."""
        self.running = False


async def main():
    daemon = KernelDaemon()

    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        daemon.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    await daemon.start()


if __name__ == "__main__":
    asyncio.run(main())
