"""
ARES Rebalance Job Manager v2.0

Replaces the broken REBAL_GATE + EMIT_LIMIT + last_ts mechanism with
a proper job-based rebalancing system.

Design principles (4-AI consensus):
1. Rebalance is a JOB with lifecycle: CREATED -> RUNNING -> DRAINING -> COMPLETED/FAILED
2. batch_id is a first-class entity, not a side effect
3. Progress-based completion (not time-based)
4. BUY reserved slots guaranteed
5. Idempotency by (batch_id, symbol, side), NOT by state_decision_id

Author: ARES Architecture Team (4-AI Consensus Design)
Version: 2.0.0
"""

import asyncio
import json
import time
import hashlib
import logging
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger("ares.rebalance")


class JobState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class IntentStatus(str, Enum):
    PENDING = "PENDING"
    EMITTED = "EMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


class RebalanceJobManager:
    """
    Manages rebalance jobs as first-class entities.

    Replaces: REBAL_GATE, EMIT_LIMIT, champion:rebalance:last_ts,
              champion:rebalance:force, state_decision_id idempotency
    """

    KEYS = {
        "current_job": "ares:rebal:current_job",
        "history": "ares:rebal:history",
        "config": "ares:rebal:config",
    }

    DEFAULT_CONFIG = {
        "emit_per_cycle": 10,
        "buy_reserved_slots": 2,
        "job_timeout_s": 900,
        "min_rebal_interval_s": 3600,
        "max_concurrent_jobs": 1,
    }

    def __init__(self, redis_client, kernel=None, telegram_fn=None):
        self.redis = redis_client
        self.kernel = kernel
        self.telegram = telegram_fn

    async def create_job(self, intents: List[dict], targets_hash: str = "") -> Optional[str]:
        """Create a new rebalance job from a list of intents."""
        current = await self._get_current_job()
        if current and current.get("state") in ("CREATED", "RUNNING", "DRAINING"):
            logger.info(
                f"REBAL_JOB_EXISTS: batch_id={current.get('batch_id')} "
                f"state={current.get('state')} progress={current.get('progress_pct')}%"
            )
            return current.get("batch_id")

        batch_id = (
            f"rbatch-{hashlib.md5(f'{time.time()}-{targets_hash}'.encode()).hexdigest()[:16]}"
        )

        buys = [i for i in intents if i.get("side", "").upper() == "BUY"]
        sells = [i for i in intents if i.get("side", "").upper() == "SELL"]
        sells.sort(key=lambda x: x.get("notional", 0), reverse=True)
        buys.sort(key=lambda x: x.get("notional", 0), reverse=True)

        config = await self._get_config()

        job = {
            "batch_id": batch_id,
            "state": JobState.CREATED,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "targets_hash": targets_hash,
            "total_intents": len(intents),
            "completed_intents": 0,
            "buy_count": len(buys),
            "sell_count": len(sells),
            "buy_completed": 0,
            "sell_completed": 0,
            "emit_per_cycle": config["emit_per_cycle"],
            "buy_reserved_slots": config["buy_reserved_slots"],
            "timeout_s": config["job_timeout_s"],
            "progress_pct": 0.0,
        }

        await self.redis.hset(
            self.KEYS["current_job"],
            mapping={k: str(v) for k, v in job.items()},
        )

        intent_key = f"ares:rebal:intent_status:{batch_id}"
        pipe = self.redis.pipeline()
        for intent in intents:
            sym = intent["symbol"]
            side = intent["side"].upper()
            pipe.hset(intent_key, f"{sym}:{side}", IntentStatus.PENDING)
        pipe.expire(intent_key, 7200)
        await pipe.execute()

        await self.redis.set(
            f"ares:rebal:intents:{batch_id}",
            json.dumps(intents),
            ex=7200,
        )

        logger.info(
            f"REBAL_JOB_CREATED: batch_id={batch_id} "
            f"total={len(intents)} sells={len(sells)} buys={len(buys)}"
        )

        if self.kernel:
            try:
                await self.kernel.transition(
                    "REBALANCING", "rebalance_job_started",
                    {"batch_id": batch_id, "total": len(intents)},
                )
            except Exception as e:
                logger.error(f"Kernel transition failed: {e}")

        return batch_id

    async def get_next_batch(self) -> Tuple[List[dict], str]:
        """
        Get the next batch of intents to emit.
        Guarantees BUY reserved slots.
        Returns (intents_to_emit, batch_id).
        """
        job = await self._get_current_job()
        if not job or job.get("state") not in ("CREATED", "RUNNING", "DRAINING"):
            return [], ""

        batch_id = job["batch_id"]
        emit_limit = int(job.get("emit_per_cycle", 10))
        buy_reserved = int(job.get("buy_reserved_slots", 2))

        intents_raw = await self.redis.get(f"ares:rebal:intents:{batch_id}")
        if not intents_raw:
            return [], batch_id

        all_intents = json.loads(intents_raw)
        intent_key = f"ares:rebal:intent_status:{batch_id}"
        statuses = await self.redis.hgetall(intent_key)

        pending_sells = []
        pending_buys = []
        for intent in all_intents:
            sym = intent["symbol"]
            side = intent["side"].upper()
            status = statuses.get(f"{sym}:{side}", "PENDING")
            if status == "PENDING":
                if side == "SELL":
                    pending_sells.append(intent)
                else:
                    pending_buys.append(intent)

        # Allocate slots: BUY reserved, SELL gets the rest
        actual_buy_slots = min(buy_reserved, len(pending_buys))
        sell_slots = emit_limit - actual_buy_slots

        batch = []
        for intent in pending_sells[:sell_slots]:
            batch.append(intent)
        for intent in pending_buys[:actual_buy_slots]:
            batch.append(intent)

        # If SELL didn't fill all slots, give remaining to BUY
        remaining = emit_limit - len(batch)
        if remaining > 0:
            extra_buys = pending_buys[actual_buy_slots:actual_buy_slots + remaining]
            batch.extend(extra_buys)

        # If BUY didn't fill reserved slots, give remaining to SELL
        remaining = emit_limit - len(batch)
        if remaining > 0:
            extra_sells = pending_sells[sell_slots:sell_slots + remaining]
            batch.extend(extra_sells)

        # Update job state to RUNNING
        if job.get("state") == "CREATED" and batch:
            await self.redis.hset(self.KEYS["current_job"], "state", JobState.RUNNING)

        logger.info(
            f"REBAL_BATCH: batch_id={batch_id} emit={len(batch)} "
            f"pending_sell={len(pending_sells)} pending_buy={len(pending_buys)}"
        )

        return batch, batch_id

    async def mark_emitted(self, batch_id: str, symbol: str, side: str):
        """Mark an intent as emitted."""
        intent_key = f"ares:rebal:intent_status:{batch_id}"
        await self.redis.hset(intent_key, f"{symbol}:{side}", IntentStatus.EMITTED)

    async def mark_filled(self, batch_id: str, symbol: str, side: str):
        """Mark an intent as filled (order executed)."""
        intent_key = f"ares:rebal:intent_status:{batch_id}"
        await self.redis.hset(intent_key, f"{symbol}:{side}", IntentStatus.FILLED)

        # Update progress
        await self._update_progress(batch_id)

    async def mark_rejected(self, batch_id: str, symbol: str, side: str, reason: str = ""):
        """Mark an intent as rejected."""
        intent_key = f"ares:rebal:intent_status:{batch_id}"
        await self.redis.hset(intent_key, f"{symbol}:{side}", IntentStatus.REJECTED)
        logger.warning(f"REBAL_INTENT_REJECTED: {symbol} {side} reason={reason}")
        await self._update_progress(batch_id)

    async def _update_progress(self, batch_id: str):
        """Update job progress and check for completion."""
        intent_key = f"ares:rebal:intent_status:{batch_id}"
        statuses = await self.redis.hgetall(intent_key)

        total = len(statuses)
        completed = sum(
            1 for s in statuses.values()
            if s in (IntentStatus.FILLED, IntentStatus.REJECTED, IntentStatus.SKIPPED)
        )
        emitted = sum(1 for s in statuses.values() if s == IntentStatus.EMITTED)
        pending = sum(1 for s in statuses.values() if s == IntentStatus.PENDING)

        progress = (completed / total * 100) if total > 0 else 0

        await self.redis.hset(self.KEYS["current_job"], mapping={
            "completed_intents": str(completed),
            "progress_pct": f"{progress:.1f}",
        })

        logger.info(
            f"REBAL_PROGRESS: batch_id={batch_id} "
            f"total={total} completed={completed} emitted={emitted} "
            f"pending={pending} progress={progress:.1f}%"
        )

        # Check completion
        if pending == 0 and emitted == 0:
            await self._complete_job(batch_id, "all_intents_processed")
        elif pending == 0:
            # All pending done, waiting for emitted to fill
            await self.redis.hset(
                self.KEYS["current_job"], "state", JobState.DRAINING
            )

    async def check_timeout(self):
        """Check if current job has timed out."""
        job = await self._get_current_job()
        if not job or job.get("state") not in ("CREATED", "RUNNING", "DRAINING"):
            return

        created_at = job.get("created_at", "")
        timeout_s = int(job.get("timeout_s", 900))

        if created_at:
            from datetime import datetime, timezone
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age = time.time() - dt.timestamp()
                if age > timeout_s:
                    logger.warning(
                        f"REBAL_JOB_TIMEOUT: batch_id={job['batch_id']} "
                        f"age={age:.0f}s > timeout={timeout_s}s"
                    )
                    await self._complete_job(
                        job["batch_id"], "timeout",
                        state=JobState.FAILED,
                    )
            except (ValueError, TypeError):
                pass

    async def _complete_job(self, batch_id: str, reason: str, state: str = None):
        """Complete or fail a rebalance job."""
        if state is None:
            state = JobState.COMPLETED

        job = await self._get_current_job()
        if not job:
            return

        # Archive to history stream
        await self.redis.xadd(
            self.KEYS["history"],
            {
                "batch_id": batch_id,
                "state": state,
                "reason": reason,
                "total": job.get("total_intents", "0"),
                "completed": job.get("completed_intents", "0"),
                "progress": job.get("progress_pct", "0"),
                "created_at": job.get("created_at", ""),
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            maxlen=1000,
        )

        # Clear current job
        await self.redis.hset(self.KEYS["current_job"], mapping={
            "state": state,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "completion_reason": reason,
        })

        logger.info(
            f"REBAL_JOB_{state}: batch_id={batch_id} reason={reason} "
            f"progress={job.get('progress_pct', '0')}%"
        )

        # Transition kernel back to TRADING
        if self.kernel:
            try:
                await self.kernel.transition(
                    "TRADING", f"rebalance_job_{state.lower()}",
                    {"batch_id": batch_id, "reason": reason},
                )
            except Exception as e:
                logger.error(f"Kernel transition failed: {e}")

        if self.telegram:
            try:
                await self.telegram(
                    f"Rebalance Job {state}\n"
                    f"batch: {batch_id}\n"
                    f"reason: {reason}\n"
                    f"progress: {job.get('progress_pct', '0')}%"
                )
            except Exception:
                pass

    async def force_complete(self, reason: str = "manual"):
        """Force-complete the current job."""
        job = await self._get_current_job()
        if job and job.get("batch_id"):
            await self._complete_job(job["batch_id"], f"force:{reason}")

    async def _get_current_job(self) -> Optional[dict]:
        """Get current job state."""
        return await self.redis.hgetall(self.KEYS["current_job"])

    async def _get_config(self) -> dict:
        """Get rebalance config."""
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
        """Get current job status for monitoring."""
        job = await self._get_current_job()
        if not job:
            return {"active": False}
        return {
            "active": job.get("state") in ("CREATED", "RUNNING", "DRAINING"),
            "batch_id": job.get("batch_id", ""),
            "state": job.get("state", ""),
            "progress_pct": float(job.get("progress_pct", 0)),
            "total": int(job.get("total_intents", 0)),
            "completed": int(job.get("completed_intents", 0)),
        }
