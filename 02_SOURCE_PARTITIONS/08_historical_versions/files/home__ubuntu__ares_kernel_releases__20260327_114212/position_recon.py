"""
ARES Position Reconciliation Engine v2.0

Replaces the broken position-sync-service with a provable reconciliation engine.

Design principles (4-AI consensus):
1. Broker is ALWAYS truth for quantity
2. Internal state is truth for intent/target
3. Every reconciliation produces an auditable snapshot
4. Auto-correction with configurable thresholds
5. Degraded mode for unreconcilable symbols

Keys:
- kis:broker:positions (HASH) - broker truth, updated by kis-balance-sync
- emarkos:v1:positions (JSON STRING) - internal state
- ares:recon:snapshot:{timestamp} - audit snapshots
- ares:recon:latest - latest snapshot reference
- ares:recon:mismatches - current mismatch set

Author: ARES Architecture Team (4-AI Consensus Design)
Version: 2.0.0
"""

import asyncio
import json
import time
import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum

logger = logging.getLogger("ares.recon")


class MismatchType(str, Enum):
    QTY_MISMATCH = "qty_mismatch"
    BROKER_ONLY = "broker_only"
    INTERNAL_ONLY = "internal_only"
    MATCH = "match"


@dataclass
class PositionEntry:
    symbol: str
    broker_qty: int
    internal_qty: int
    mismatch_type: str
    delta: int  # broker - internal
    broker_avg_price: float = 0.0
    internal_avg_price: float = 0.0


@dataclass
class ReconSnapshot:
    timestamp: str
    session_id: str
    total_symbols: int
    matched: int
    mismatched: int
    broker_only: int
    internal_only: int
    entries: List[dict]
    action_taken: str = "none"


class PositionReconciliationEngine:
    """
    Provable position reconciliation with audit trail.
    
    Runs every 30 seconds. Compares broker positions (kis:broker:positions HASH)
    against internal positions (emarkos:v1:positions JSON).
    
    On mismatch:
    - Small delta (<=2 shares): auto-correct internal to match broker
    - Large delta (>2 shares): flag for manual review, enter DEGRADED for symbol
    - Broker-only: add to internal with broker values
    - Internal-only: flag as potential stale position
    """

    KEYS = {
        "broker_positions": "kis:broker:positions",
        "internal_positions": "emarkos:v1:positions",
        "latest_snapshot": "ares:recon:latest",
        "mismatches": "ares:recon:mismatches",
        "auto_correct_log": "ares:recon:auto_corrections",
        "config": "ares:recon:config",
    }

    DEFAULT_CONFIG = {
        "auto_correct_threshold": 5,   # auto-correct if delta <= this
        "check_interval_s": 30,
        "alert_cooldown_s": 300,
        "enabled": True,
    }

    def __init__(self, redis_client, kernel_client=None, telegram_fn=None):
        self.redis = redis_client
        self.kernel = kernel_client
        self.telegram = telegram_fn
        self._last_alert_ts = 0

    async def reconcile(self) -> ReconSnapshot:
        """
        Execute one reconciliation cycle.
        Returns an auditable snapshot.
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # 1. Load broker positions (HASH: symbol -> qty string)
        broker_raw = await self.redis.hgetall(self.KEYS["broker_positions"])
        broker_positions = {}
        for sym, val in broker_raw.items():
            try:
                broker_positions[sym] = int(float(val))
            except (ValueError, TypeError):
                continue

        # 2. Load internal positions (JSON string)
        internal_raw = await self.redis.get(self.KEYS["internal_positions"])
        internal_positions = {}
        if internal_raw:
            try:
                data = json.loads(internal_raw)
                if isinstance(data, dict):
                    for sym, info in data.items():
                        if isinstance(info, dict):
                            qty = info.get("shares", info.get("qty", 0))
                            internal_positions[sym] = int(float(qty))
                        elif isinstance(info, (int, float)):
                            internal_positions[sym] = int(info)
            except (json.JSONDecodeError, TypeError):
                logger.error("Failed to parse internal positions")

        # 3. Compare
        all_symbols = set(broker_positions.keys()) | set(internal_positions.keys())
        entries = []
        matched = 0
        mismatched = 0
        broker_only_count = 0
        internal_only_count = 0
        mismatch_symbols = []

        for sym in sorted(all_symbols):
            b_qty = broker_positions.get(sym, 0)
            i_qty = internal_positions.get(sym, 0)
            delta = b_qty - i_qty

            if sym in broker_positions and sym not in internal_positions:
                mtype = MismatchType.BROKER_ONLY
                broker_only_count += 1
                mismatch_symbols.append(sym)
            elif sym not in broker_positions and sym in internal_positions:
                mtype = MismatchType.INTERNAL_ONLY
                internal_only_count += 1
                mismatch_symbols.append(sym)
            elif delta != 0:
                mtype = MismatchType.QTY_MISMATCH
                mismatched += 1
                mismatch_symbols.append(sym)
            else:
                mtype = MismatchType.MATCH
                matched += 1

            entries.append(asdict(PositionEntry(
                symbol=sym,
                broker_qty=b_qty,
                internal_qty=i_qty,
                mismatch_type=mtype,
                delta=delta,
            )))

        # 4. Create snapshot
        snapshot = ReconSnapshot(
            timestamp=ts,
            session_id=await self.redis.get("ares:kernel:status") or "",
            total_symbols=len(all_symbols),
            matched=matched,
            mismatched=mismatched + broker_only_count + internal_only_count,
            broker_only=broker_only_count,
            internal_only=internal_only_count,
            entries=entries,
        )

        # 5. Store snapshot
        snapshot_key = f"ares:recon:snapshot:{ts}"
        await self.redis.set(snapshot_key, json.dumps(asdict(snapshot)), ex=86400)
        await self.redis.set(self.KEYS["latest_snapshot"], ts)

        # 6. Handle mismatches
        if mismatch_symbols:
            await self._handle_mismatches(entries, snapshot)
            # Update mismatch set
            pipe = self.redis.pipeline()
            pipe.delete(self.KEYS["mismatches"])
            for sym in mismatch_symbols:
                pipe.sadd(self.KEYS["mismatches"], sym)
            pipe.expire(self.KEYS["mismatches"], 3600)
            await pipe.execute()
        else:
            await self.redis.delete(self.KEYS["mismatches"])

        # 7. Log
        logger.info(
            f"RECON_CYCLE: total={snapshot.total_symbols} "
            f"matched={matched} mismatched={snapshot.mismatched} "
            f"broker_only={broker_only_count} internal_only={internal_only_count} "
            f"action={snapshot.action_taken}"
        )

        return snapshot

    async def _handle_mismatches(self, entries: List[dict], snapshot: ReconSnapshot):
        """Handle detected mismatches with auto-correction or degradation."""
        config = await self._get_config()
        threshold = config.get("auto_correct_threshold", 5)
        corrections = []
        degraded_symbols = []

        for entry in entries:
            if entry["mismatch_type"] == MismatchType.MATCH:
                continue

            sym = entry["symbol"]
            delta = abs(entry["delta"])

            if entry["mismatch_type"] == MismatchType.BROKER_ONLY:
                # Broker has it, internal doesn't -> add to internal
                corrections.append({
                    "symbol": sym,
                    "action": "add_to_internal",
                    "broker_qty": entry["broker_qty"],
                    "reason": "broker_only",
                })
            elif entry["mismatch_type"] == MismatchType.INTERNAL_ONLY:
                if entry["internal_qty"] > 0:
                    # Internal has position but broker doesn't -> flag
                    degraded_symbols.append(sym)
                    logger.warning(
                        f"RECON_INTERNAL_ONLY: {sym} internal={entry['internal_qty']} "
                        f"broker=0 -> flagging for review"
                    )
            elif entry["mismatch_type"] == MismatchType.QTY_MISMATCH:
                if delta <= threshold:
                    # Small delta -> auto-correct
                    corrections.append({
                        "symbol": sym,
                        "action": "auto_correct",
                        "from_qty": entry["internal_qty"],
                        "to_qty": entry["broker_qty"],
                        "delta": entry["delta"],
                        "reason": "small_delta_auto_correct",
                    })
                else:
                    # Large delta -> degrade symbol
                    degraded_symbols.append(sym)
                    logger.warning(
                        f"RECON_LARGE_DELTA: {sym} broker={entry['broker_qty']} "
                        f"internal={entry['internal_qty']} delta={entry['delta']} "
                        f"-> degrading symbol"
                    )

        # Apply auto-corrections
        if corrections:
            await self._apply_corrections(corrections)
            snapshot.action_taken = f"auto_corrected:{len(corrections)}"

        # Degrade symbols if kernel is available
        if degraded_symbols and self.kernel:
            try:
                await self.kernel.enter_degraded(
                    degraded_symbols,
                    f"position_mismatch:{','.join(degraded_symbols)}"
                )
                snapshot.action_taken += f",degraded:{len(degraded_symbols)}"
            except Exception as e:
                logger.error(f"Failed to degrade symbols: {e}")

        # Alert
        if corrections or degraded_symbols:
            await self._send_alert(corrections, degraded_symbols)

    async def _apply_corrections(self, corrections: List[dict]):
        """Apply auto-corrections to internal positions."""
        internal_raw = await self.redis.get(self.KEYS["internal_positions"])
        if not internal_raw:
            return

        try:
            positions = json.loads(internal_raw)
        except json.JSONDecodeError:
            return

        for corr in corrections:
            sym = corr["symbol"]
            if corr["action"] == "auto_correct":
                if sym in positions and isinstance(positions[sym], dict):
                    old_qty = positions[sym].get("shares", positions[sym].get("qty", 0))
                    positions[sym]["shares"] = corr["to_qty"]
                    if "qty" in positions[sym]:
                        positions[sym]["qty"] = corr["to_qty"]
                    logger.info(
                        f"RECON_AUTO_CORRECT: {sym} {old_qty} -> {corr['to_qty']}"
                    )
            elif corr["action"] == "add_to_internal":
                positions[sym] = {
                    "shares": corr["broker_qty"],
                    "qty": corr["broker_qty"],
                    "source": "recon_auto_add",
                }
                logger.info(
                    f"RECON_AUTO_ADD: {sym} qty={corr['broker_qty']}"
                )

        # Write back
        await self.redis.set(
            self.KEYS["internal_positions"],
            json.dumps(positions)
        )

        # Audit log
        await self.redis.xadd(
            self.KEYS["auto_correct_log"],
            {"corrections": json.dumps(corrections), "ts": str(time.time())},
            maxlen=1000,
        )

    async def _send_alert(self, corrections: List[dict], degraded: List[str]):
        """Send alert with cooldown."""
        now = time.time()
        if now - self._last_alert_ts < 300:
            return
        self._last_alert_ts = now

        lines = ["POSITION RECON ALERT"]
        if corrections:
            lines.append(f"\nAuto-corrected ({len(corrections)}):")
            for c in corrections[:10]:
                lines.append(f"  {c['symbol']}: {c.get('from_qty','?')} -> {c.get('to_qty', c.get('broker_qty','?'))}")
        if degraded:
            lines.append(f"\nDegraded ({len(degraded)}):")
            for s in degraded[:10]:
                lines.append(f"  {s}")

        msg = "\n".join(lines)
        if self.telegram:
            try:
                await self.telegram(msg)
            except Exception as e:
                logger.error(f"Alert send failed: {e}")

    async def _get_config(self) -> dict:
        """Get reconciliation config."""
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
