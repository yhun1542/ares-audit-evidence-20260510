"""
ARES V5.6.1b Telegram Alert Service v3.3
─────────────────────────────────────────
Redis Polling 기반 실시간 텔레그램 알림 서비스 (단일 발신 권한).

v3.3 변경사항 (v3.2 대비):
- [P1-FIX] Edge alert 전송 실패 시 영구 유실 방지.
           _alert()가 False를 반환하면 호출부에서 _prev_state를 갱신하지 않음.
           → 다음 poll에서 동일 상태 변화를 재감지하여 재시도 (at-least-once).
           startup_health_check도 _send() 실패 시 retry queue에 적재.
- [LOW-FIX] _is_flag_active() 통일.
           trading_enabled, readiness 파싱도 _is_flag_active()를 사용하도록 변경.
           "on", "yes", "true", "1" 등 모든 truthy 값을 일관되게 처리.

Author: ARES Architecture Team (4-AI Consensus Design)
Version: 3.3.0
"""
import asyncio
import json
import time
import os
import sys
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import redis.asyncio as aioredis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("ares.telegram")

# ─── Configuration (환경변수 필수) ────────────────────────────────
REDIS_URL = os.environ.get("REDIS_URL", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

_missing = []
if not REDIS_URL:
    _missing.append("REDIS_URL")
if not TELEGRAM_BOT_TOKEN:
    _missing.append("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_CHAT_ID:
    _missing.append("TELEGRAM_CHAT_ID")
if _missing:
    print(f"FATAL: Missing required environment variables: {', '.join(_missing)}", file=sys.stderr)
    print("Set them in PM2 ecosystem config or .env file.", file=sys.stderr)
    sys.exit(1)

# Alert cooldown (seconds) per event type
COOLDOWN = {
    "leader_change": 60,
    "kill_switch": 30,
    "halt": 120,
    "defer": 120,
    "flatten": 120,
    "consumer_stale": 300,
    "regime_change": 300,
    "order_executed": 10,
    "daily_summary": 86400,
    "session_report": 3600,
    "readiness_change": 120,
}

# Per-incident cooldown (separate from event-type cooldown)
INCIDENT_COOLDOWN = 300  # 5 minutes per unique incident signature

# Minimum cooldown for critical alerts
CRITICAL_MIN_COOLDOWN = 15

# Retry queue settings
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 30  # seconds

# US Eastern timezone (DST-aware)
ET = ZoneInfo("America/New_York")

MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN = 0

REPORT_TIMES = {
    "pre_open": (9, 20),
    "mid_day": (12, 0),
    "post_close": (16, 10),
}

# V5.6.1b Redis keys
KEYS = {
    "leader_lease": "ares:v56:leader:lease",
    "trading_enabled": "trading:enabled",
    "trade_halt": "policy:trade_halt",
    "consumer_heartbeat": "ares:v55:consumer:heartbeat",
    "consumer_last_ack": "ares:v55:consumer:last_ack",
    "readiness": "ares:v55:live:readiness",
    "heartbeat": "ares:v55:live:heartbeat",
    "halt_flag": "ares:v55:live:control:halt",
    "flatten_flag": "ares:v55:live:control:flatten",
    "incident_last": "ares:v55:incident:last",
    "state_json": "ares:v55:state:summary",
    "execution_stream": "ares:v55:execution:intents",
    "canonical_stream": "emarkos:v6:order:intent",
    "decision_last": "ares:v55:decision:last",
    "regime_final": "regime:final:current",
    "autotune_active": "ares:v55:autotune:active_override",
    "equity_verified": "ofg:equity:verified",
    "session_current": "ares:session:current",
    "monitor_metrics": "ares:monitor:metrics",
}

# Redis keys for state persistence
PERSIST_KEYS = {
    "cooldowns": "ares:telegram:cooldowns",
    "incidents": "ares:telegram:seen_incidents",
    "reports": "ares:telegram:reports_sent",
    "retry_queue": "ares:telegram:retry_queue",
}
PERSIST_TTL = 86400  # 24 hours


# ─── Helper Functions ─────────────────────────────────────────────

def _now_et() -> datetime:
    """Get current time in US Eastern (DST-aware)."""
    return datetime.now(ET)


def _is_market_hours() -> bool:
    """Check if current time is within US market hours (ET 09:30-16:00, Mon-Fri)."""
    et = _now_et()
    if et.weekday() >= 5:
        return False
    market_open = et.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0)
    market_close = et.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0)
    return market_open <= et <= market_close


def _is_extended_hours() -> bool:
    """Check if within extended trading window (ET 08:00-18:00, Mon-Fri)."""
    et = _now_et()
    if et.weekday() >= 5:
        return False
    return 8 <= et.hour < 18


def _is_flag_active(value) -> bool:
    """
    [LOW-FIX] Unified flag parser for ALL Redis boolean flags.
    None, "", "false", "0", "no", "off", "none" → False. Else → True.
    Used for: trading_enabled, readiness, halt_flag, flatten_flag, autotune, etc.
    """
    if value is None:
        return False
    s = str(value).strip().lower()
    if s in ("", "false", "0", "no", "off", "none"):
        return False
    return True


def _safe_float(value, default: float = 0.0) -> float:
    """
    Safe float parser for Redis values.
    Handles None, empty string, non-numeric strings, and JSON-wrapped values.
    Never raises an exception.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        pass
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            for key in ("total", "value", "equity", "amount"):
                if key in parsed:
                    return float(parsed[key])
        elif isinstance(parsed, (int, float)):
            return float(parsed)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return default


def _safe_ts_age(value) -> tuple[float, str]:
    """
    Safe timestamp age parser.
    Returns (age_seconds, display_string). Never raises.
    """
    if value is None:
        return (-1.0, "N/A")
    try:
        age = time.time() - float(value)
        return (age, f"{age:.0f}s")
    except (ValueError, TypeError):
        return (-1.0, "N/A")


# ─── Main Service Class ──────────────────────────────────────────

class TelegramAlertService:
    def __init__(self):
        self.redis = None
        self._cooldowns: dict[str, float] = {}
        self._incident_cooldowns: dict[str, float] = {}
        self._prev_state = {
            "leader": None,
            "kill_switch": None,
            "regime": None,
            "halt": None,
            "flatten": None,
            "engine_state": None,
            "readiness": None,
        }
        self._report_sent_today: dict[str, str] = {}
        # [P1-FIX] Retry queue for failed edge alerts
        self._retry_queue: list[dict] = []

    async def start(self):
        """Initialize Redis connection, restore state, run startup check, start polling."""
        logger.info("ARES V5.6.1b Telegram Alert Service v3.3 starting...")
        self.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            await self.redis.ping()
            logger.info("Redis connection OK")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            sys.exit(1)

        # Restore persisted state (including retry queue)
        await self._restore_state()

        # Send startup message
        await self._send(
            "ARES V5.6.1b Telegram Alert Service v3.3 started\n"
            "Monitoring: Leader, KillSwitch, HALT/DEFER/FLATTEN, Consumer, Incidents, Regime, Readiness\n"
            "Reports: Pre-Open(09:20ET), Mid-Day(12:00ET), Post-Close(16:10ET)\n"
            f"Timezone: {ET} (DST-aware)\n"
            "Edge alert delivery: at-least-once (retry queue enabled)"
        )

        # Startup health check
        await self._startup_health_check()

        await self._poll_loop()

    # ─── [P1-FIX] Startup Health Check (retry-aware) ────────────
    async def _startup_health_check(self):
        """
        Scan all monitored keys on startup and immediately alert
        if any danger state already exists. On send failure, enqueue for retry.
        """
        warnings = []

        # Leader
        leader_raw = await self.redis.get(KEYS["leader_lease"])
        leader_id = self._leader_identity(leader_raw)
        if leader_id == "NONE":
            warnings.append("\U0001f6a8 Leader Lease: MISSING (no active engine)")
        self._prev_state["leader"] = leader_raw or "NONE"

        # Kill Switch - [LOW-FIX] unified parser
        ks_raw = await self.redis.get(KEYS["trading_enabled"])
        ks_enabled = _is_flag_active(ks_raw)
        if not ks_enabled:
            warnings.append("\U0001f534 Kill Switch: OFF (trading BLOCKED)")
        self._prev_state["kill_switch"] = ks_enabled

        # HALT
        halt_raw = await self.redis.get(KEYS["halt_flag"])
        halt_active = _is_flag_active(halt_raw)
        if halt_active:
            warnings.append(f"\U0001f534 HALT Flag: ACTIVE (raw={halt_raw!r})")
        self._prev_state["halt"] = halt_active

        # FLATTEN
        flatten_raw = await self.redis.get(KEYS["flatten_flag"])
        flatten_active = _is_flag_active(flatten_raw)
        if flatten_active:
            warnings.append(f"\U0001f534 FLATTEN Flag: ACTIVE (raw={flatten_raw!r})")
        self._prev_state["flatten"] = flatten_active

        # Engine state (DEFER)
        state_raw = await self.redis.get(KEYS["state_json"])
        engine_state = "UNKNOWN"
        if state_raw:
            try:
                state = json.loads(state_raw)
                engine_state = state.get("engine_state", "UNKNOWN")
            except (json.JSONDecodeError, ValueError):
                pass
        if engine_state == "DEFER" and _is_market_hours():
            warnings.append(f"\U0001f7e1 Engine State: DEFER during market hours")
        self._prev_state["engine_state"] = engine_state

        # Readiness - [LOW-FIX] unified parser
        readiness_raw = await self.redis.get(KEYS["readiness"])
        ready = _is_flag_active(readiness_raw)
        if not ready:
            warnings.append("\U0001f7e1 Readiness: NOT READY")
        self._prev_state["readiness"] = ready

        # Regime
        regime_data = await self.redis.hgetall(KEYS["regime_final"])
        regime = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"
        self._prev_state["regime"] = regime

        # Consumer heartbeat
        hb_age, hb_display = _safe_ts_age(await self.redis.get(KEYS["consumer_heartbeat"]))
        if _is_market_hours() and (hb_age < 0 or hb_age > 120):
            warnings.append(f"\U0001f7e1 Consumer Heartbeat: stale ({hb_display})")

        if warnings:
            msg = (
                "\U0001f50d STARTUP HEALTH CHECK\n"
                "The following danger states were detected on boot:\n\n"
                + "\n".join(warnings)
                + "\n\nThese states existed BEFORE this service started. "
                "Investigate immediately if unexpected."
            )
            # [P1-FIX] If startup alert fails, enqueue for retry
            sent = await self._send(msg)
            if sent:
                logger.warning(f"Startup health check: {len(warnings)} warning(s) sent")
            else:
                logger.error(f"Startup health check FAILED to send, enqueueing for retry")
                self._enqueue_retry(msg, "startup_health_check")
        else:
            logger.info("Startup health check: all clear")

    # ─── State Persistence ────────────────────────────────────────
    async def _restore_state(self):
        """Restore cooldowns, incident history, report records, and retry queue from Redis."""
        try:
            cd_raw = await self.redis.hgetall(PERSIST_KEYS["cooldowns"])
            if cd_raw:
                self._cooldowns = {k: _safe_float(v) for k, v in cd_raw.items()}
                logger.info(f"Restored {len(self._cooldowns)} cooldown entries")

            inc_raw = await self.redis.hgetall(PERSIST_KEYS["incidents"])
            if inc_raw:
                self._incident_cooldowns = {k: _safe_float(v) for k, v in inc_raw.items()}
                logger.info(f"Restored {len(self._incident_cooldowns)} incident entries")

            rpt_raw = await self.redis.hgetall(PERSIST_KEYS["reports"])
            if rpt_raw:
                self._report_sent_today = dict(rpt_raw)
                logger.info(f"Restored {len(self._report_sent_today)} report entries")

            # [P1-FIX] Restore retry queue
            retry_raw = await self.redis.get(PERSIST_KEYS["retry_queue"])
            if retry_raw:
                try:
                    self._retry_queue = json.loads(retry_raw)
                    logger.info(f"Restored {len(self._retry_queue)} retry queue entries")
                except (json.JSONDecodeError, ValueError):
                    self._retry_queue = []
        except Exception as e:
            logger.warning(f"State restoration failed (starting fresh): {e}")

    async def _persist_cooldowns_immediate(self):
        """Immediately persist cooldowns after successful alert send."""
        try:
            if self._cooldowns:
                mapping = {k: str(v) for k, v in self._cooldowns.items()}
                await self.redis.hset(PERSIST_KEYS["cooldowns"], mapping=mapping)
                await self.redis.expire(PERSIST_KEYS["cooldowns"], PERSIST_TTL)
        except Exception as e:
            logger.debug(f"Cooldown immediate persist failed: {e}")

    async def _persist_incidents_immediate(self):
        """Immediately persist incident cooldowns after successful send."""
        try:
            now = time.time()
            self._incident_cooldowns = {
                k: v for k, v in self._incident_cooldowns.items()
                if now - v < INCIDENT_COOLDOWN * 2
            }
            if self._incident_cooldowns:
                mapping = {k: str(v) for k, v in self._incident_cooldowns.items()}
                await self.redis.delete(PERSIST_KEYS["incidents"])
                await self.redis.hset(PERSIST_KEYS["incidents"], mapping=mapping)
                await self.redis.expire(PERSIST_KEYS["incidents"], PERSIST_TTL)
        except Exception as e:
            logger.debug(f"Incident immediate persist failed: {e}")

    async def _persist_reports_immediate(self):
        """Immediately persist report records after successful send."""
        try:
            if self._report_sent_today:
                await self.redis.hset(PERSIST_KEYS["reports"], mapping=self._report_sent_today)
                await self.redis.expire(PERSIST_KEYS["reports"], PERSIST_TTL)
        except Exception as e:
            logger.debug(f"Report immediate persist failed: {e}")

    async def _persist_retry_queue(self):
        """[P1-FIX] Persist retry queue to Redis for crash recovery."""
        try:
            if self._retry_queue:
                await self.redis.set(
                    PERSIST_KEYS["retry_queue"],
                    json.dumps(self._retry_queue),
                    ex=PERSIST_TTL,
                )
            else:
                await self.redis.delete(PERSIST_KEYS["retry_queue"])
        except Exception as e:
            logger.debug(f"Retry queue persist failed: {e}")

    # ─── [P1-FIX] Retry Queue ────────────────────────────────────
    def _enqueue_retry(self, message: str, context: str = ""):
        """Add a failed message to the retry queue."""
        entry = {
            "message": message,
            "context": context,
            "enqueued_at": time.time(),
            "attempts": 0,
            "next_retry_at": time.time() + RETRY_BACKOFF_BASE,
        }
        self._retry_queue.append(entry)
        logger.info(f"Enqueued for retry: context={context}, queue_size={len(self._retry_queue)}")

    async def _process_retry_queue(self):
        """
        [P1-FIX] Process pending retries with exponential backoff.
        Called once per poll cycle. Removes entries that succeed or exceed max attempts.
        """
        if not self._retry_queue:
            return

        now = time.time()
        remaining = []
        processed = 0

        for entry in self._retry_queue:
            # Skip if not yet time to retry
            if now < entry.get("next_retry_at", 0):
                remaining.append(entry)
                continue

            # Skip if too old (> 1 hour)
            if now - entry.get("enqueued_at", 0) > 3600:
                logger.warning(
                    f"Retry entry expired (>1h): context={entry.get('context', '?')}"
                )
                continue

            # Attempt retry
            entry["attempts"] = entry.get("attempts", 0) + 1
            sent = await self._send(entry["message"])

            if sent:
                logger.info(
                    f"Retry succeeded: context={entry.get('context', '?')}, "
                    f"attempt={entry['attempts']}"
                )
                processed += 1
            elif entry["attempts"] >= RETRY_MAX_ATTEMPTS:
                logger.error(
                    f"Retry EXHAUSTED ({RETRY_MAX_ATTEMPTS} attempts): "
                    f"context={entry.get('context', '?')}, "
                    f"message={entry['message'][:80]}..."
                )
            else:
                # Exponential backoff: 30s, 60s, 120s
                backoff = RETRY_BACKOFF_BASE * (2 ** (entry["attempts"] - 1))
                entry["next_retry_at"] = now + backoff
                remaining.append(entry)
                logger.info(
                    f"Retry failed, will retry in {backoff}s: "
                    f"context={entry.get('context', '?')}, attempt={entry['attempts']}"
                )

        self._retry_queue = remaining
        if processed > 0 or len(remaining) != len(self._retry_queue):
            await self._persist_retry_queue()

    # ─── Poll Loop ────────────────────────────────────────────────
    async def _poll_loop(self):
        """Poll Redis keys for changes every 10 seconds."""
        POLL_INTERVAL = 10
        logger.info(f"Polling loop started: interval={POLL_INTERVAL}s")

        while True:
            try:
                await self._check_leader_lease()
            except Exception as e:
                logger.error(f"leader_lease check error: {e}", exc_info=True)

            try:
                await self._check_kill_switch()
            except Exception as e:
                logger.error(f"kill_switch check error: {e}", exc_info=True)

            try:
                await self._check_halt()
            except Exception as e:
                logger.error(f"halt check error: {e}", exc_info=True)

            try:
                await self._check_flatten()
            except Exception as e:
                logger.error(f"flatten check error: {e}", exc_info=True)

            try:
                await self._check_defer()
            except Exception as e:
                logger.error(f"defer check error: {e}", exc_info=True)

            try:
                await self._check_consumer_guard()
            except Exception as e:
                logger.error(f"consumer_guard check error: {e}", exc_info=True)

            try:
                await self._check_incident()
            except Exception as e:
                logger.error(f"incident check error: {e}", exc_info=True)

            try:
                await self._check_regime_change()
            except Exception as e:
                logger.error(f"regime_change check error: {e}", exc_info=True)

            try:
                await self._check_v56_state()
            except Exception as e:
                logger.error(f"v56_state check error: {e}", exc_info=True)

            try:
                await self._check_session_reports()
            except Exception as e:
                logger.error(f"session_reports check error: {e}", exc_info=True)

            # [P1-FIX] Process retry queue each cycle
            try:
                await self._process_retry_queue()
            except Exception as e:
                logger.error(f"retry_queue processing error: {e}", exc_info=True)

            await asyncio.sleep(POLL_INTERVAL)

    # ─── Leader Lease ─────────────────────────────────────────────
    def _leader_identity(self, raw):
        """Extract stable identity (run_id+pid) from leader lease JSON, ignoring ts."""
        if not raw or raw == "NONE":
            return "NONE"
        try:
            d = json.loads(raw)
            return f"{d.get('run_id', '?')}:{d.get('pid', '?')}"
        except Exception:
            return str(raw)

    async def _check_leader_lease(self):
        holder = await self.redis.get(KEYS["leader_lease"])
        ttl = await self.redis.ttl(KEYS["leader_lease"])
        current_raw = holder or "NONE"
        current_id = self._leader_identity(current_raw)
        prev_id = (
            self._leader_identity(self._prev_state["leader"])
            if self._prev_state["leader"] is not None
            else None
        )

        if prev_id is not None and current_id != prev_id:
            if current_id == "NONE":
                # [P1-FIX] Only update _prev_state if alert was delivered
                delivered = await self._alert(
                    "leader_change",
                    "\U0001f6a8 LEADER LEASE LOST!\n"
                    f"Previous holder: {prev_id}\n"
                    f"No active engine - orders will NOT be processed!",
                    critical=True,
                )
                if delivered:
                    self._prev_state["leader"] = current_raw
                # else: _prev_state NOT updated → next poll will re-detect and retry
            else:
                delivered = await self._alert(
                    "leader_change",
                    f"\U0001f504 Leader Lease Changed\n"
                    f"{prev_id} \u2192 {current_id}\n"
                    f"TTL: {ttl}s",
                )
                if delivered:
                    self._prev_state["leader"] = current_raw
        else:
            # No change, or first poll — always update
            self._prev_state["leader"] = current_raw

    # ─── Kill Switch ──────────────────────────────────────────────
    async def _check_kill_switch(self):
        enabled_raw = await self.redis.get(KEYS["trading_enabled"])
        # [LOW-FIX] Unified parser
        enabled = _is_flag_active(enabled_raw)

        if self._prev_state["kill_switch"] is not None and enabled != self._prev_state["kill_switch"]:
            if not enabled:
                delivered = await self._alert(
                    "kill_switch",
                    "\U0001f534 KILL SWITCH ACTIVATED!\n"
                    f"trading:enabled = {enabled_raw}\n"
                    "All order execution is BLOCKED.",
                    critical=True,
                )
            else:
                delivered = await self._alert(
                    "kill_switch",
                    "\U0001f7e2 Kill Switch Released\n"
                    f"trading:enabled = {enabled_raw}\n"
                    "Order execution is now allowed.",
                )
            # [P1-FIX] Only update state if delivered
            if delivered:
                self._prev_state["kill_switch"] = enabled
        else:
            self._prev_state["kill_switch"] = enabled

    # ─── HALT ─────────────────────────────────────────────────────
    async def _check_halt(self):
        halt_raw = await self.redis.get(KEYS["halt_flag"])
        current_halt = _is_flag_active(halt_raw)

        if self._prev_state["halt"] is not None and current_halt != self._prev_state["halt"]:
            if current_halt:
                delivered = await self._alert(
                    "halt",
                    f"\U0001f534 HALT ACTIVATED\n"
                    f"Raw value: {halt_raw!r}\n"
                    "All trading suspended.",
                    critical=True,
                )
            else:
                delivered = await self._alert(
                    "halt",
                    "\U0001f7e2 HALT Cleared\n"
                    "Trading suspension lifted.",
                )
            if delivered:
                self._prev_state["halt"] = current_halt
        else:
            self._prev_state["halt"] = current_halt

    # ─── FLATTEN ──────────────────────────────────────────────────
    async def _check_flatten(self):
        flatten_raw = await self.redis.get(KEYS["flatten_flag"])
        current_flatten = _is_flag_active(flatten_raw)

        if self._prev_state["flatten"] is not None and current_flatten != self._prev_state["flatten"]:
            if current_flatten:
                delivered = await self._alert(
                    "flatten",
                    f"\U0001f534 FLATTEN ACTIVATED\n"
                    f"Raw value: {flatten_raw!r}\n"
                    "Engine is flattening all positions.",
                    critical=True,
                )
            else:
                delivered = await self._alert(
                    "flatten",
                    "\U0001f7e2 FLATTEN Cleared\n"
                    "Position flattening completed or cancelled.",
                )
            if delivered:
                self._prev_state["flatten"] = current_flatten
        else:
            self._prev_state["flatten"] = current_flatten

    # ─── DEFER ────────────────────────────────────────────────────
    async def _check_defer(self):
        state_raw = await self.redis.get(KEYS["state_json"])
        engine_state = "UNKNOWN"
        if state_raw:
            try:
                state = json.loads(state_raw)
                engine_state = state.get("engine_state", "UNKNOWN")
            except (json.JSONDecodeError, ValueError):
                pass

        prev_engine = self._prev_state["engine_state"]
        if prev_engine is not None and engine_state != prev_engine and engine_state != "UNKNOWN":
            delivered = False
            if engine_state == "DEFER" and prev_engine != "DEFER":
                if _is_market_hours():
                    delivered = await self._alert(
                        "defer",
                        f"\U0001f7e1 Engine entered DEFER\n"
                        f"Previous state: {prev_engine}\n"
                        f"Consumer heartbeat may be stale.",
                    )
                else:
                    logger.info(f"DEFER outside market hours (normal): {prev_engine} -> {engine_state}")
                    delivered = True  # Not a failure, just suppressed
            elif prev_engine == "DEFER" and engine_state != "DEFER":
                delivered = await self._alert(
                    "defer",
                    f"\U0001f7e2 Engine exited DEFER\n"
                    f"New state: {engine_state}\n"
                    f"Order processing resumed.",
                )
            else:
                # Other state transitions (e.g., ACTIVE -> EXECUTE) — just track
                delivered = True

            if delivered:
                self._prev_state["engine_state"] = engine_state
        else:
            self._prev_state["engine_state"] = engine_state

    # ─── Consumer Guard ───────────────────────────────────────────
    async def _check_consumer_guard(self):
        hb_raw = await self.redis.get(KEYS["consumer_heartbeat"])
        hb_age, hb_display = _safe_ts_age(hb_raw)

        if hb_age < 0:
            if _is_market_hours():
                await self._alert(
                    "consumer_stale",
                    "\U0001f7e1 ConsumerGuard: No heartbeat key\n"
                    "Consumer may not be running during market hours!",
                )
        elif hb_age > 120:
            if _is_market_hours():
                await self._alert(
                    "consumer_stale",
                    f"\U0001f7e1 ConsumerGuard: Heartbeat stale ({hb_display})\n"
                    "Consumer may need restart:\n"
                    "  pm2 restart order-intent-executor",
                )
            else:
                logger.debug(f"Consumer heartbeat stale ({hb_display}) - outside market hours, suppressed")

    # ─── Incident (type+detail dedupe + per-incident cooldown) ────
    async def _check_incident(self):
        incident_raw = await self.redis.get(KEYS["incident_last"])
        if not incident_raw:
            return

        try:
            incident = json.loads(incident_raw)
        except (json.JSONDecodeError, ValueError):
            return

        ts = _safe_float(incident.get("ts", 0))
        if time.time() - ts > 120:
            return

        incident_sig = f"{incident.get('type', 'UNKNOWN')}:{incident.get('detail', '')}"

        now = time.time()
        last_sent = self._incident_cooldowns.get(incident_sig, 0)
        if now - last_sent < INCIDENT_COOLDOWN:
            return

        msg = (
            f"\U0001f6a8 INCIDENT DETECTED\n"
            f"Type: {incident.get('type', 'UNKNOWN')}\n"
            f"Detail: {incident.get('detail', 'N/A')}\n"
            f"Bundle: {incident.get('bundle_path', 'N/A')}\n"
            f"Signature: {incident_sig}"
        )
        sent = await self._send(msg)
        if sent:
            self._incident_cooldowns[incident_sig] = now
            await self._persist_incidents_immediate()
            logger.info(f"Incident alert sent: {incident_sig}")
        else:
            logger.warning(f"Incident alert FAILED to send: {incident_sig}")
            # Incidents are re-checked each poll, so no explicit retry needed

    # ─── Regime Change ────────────────────────────────────────────
    async def _check_regime_change(self):
        regime_data = await self.redis.hgetall(KEYS["regime_final"])
        current_regime = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"

        if (
            self._prev_state["regime"] is not None
            and current_regime != self._prev_state["regime"]
            and current_regime != "UNKNOWN"
        ):
            delivered = await self._alert(
                "regime_change",
                f"\U0001f504 Regime Changed\n"
                f"{self._prev_state['regime']} \u2192 {current_regime}\n"
                f"Source: {regime_data.get('source', 'N/A')}",
            )
            if delivered:
                self._prev_state["regime"] = current_regime
        else:
            self._prev_state["regime"] = current_regime

    # ─── Readiness (별도 이벤트 타입) ────────────────────────────
    async def _check_v56_state(self):
        readiness_raw = await self.redis.get(KEYS["readiness"])
        # [LOW-FIX] Unified parser
        current = _is_flag_active(readiness_raw)

        if self._prev_state["readiness"] is not None and current != self._prev_state["readiness"]:
            if current:
                delivered = await self._alert(
                    "readiness_change",
                    "\U0001f7e2 V5.6.1b Engine READY\n"
                    "Readiness check passed. Engine is operational.",
                )
            else:
                delivered = await self._alert(
                    "readiness_change",
                    "\U0001f7e1 V5.6.1b Engine NOT READY\n"
                    "Readiness check failed. Check system status.",
                )
            if delivered:
                self._prev_state["readiness"] = current
        else:
            self._prev_state["readiness"] = current

    # ─── 장 전/중/후 3회 자동 검증 보고 ──────────────────────────
    async def _check_session_reports(self):
        et = _now_et()
        today_str = et.strftime("%Y-%m-%d")

        if et.weekday() >= 5:
            return

        current_minutes = et.hour * 60 + et.minute

        for report_type, (target_hour, target_min) in REPORT_TIMES.items():
            sent_date = self._report_sent_today.get(report_type)
            if sent_date == today_str:
                continue

            target_minutes = target_hour * 60 + target_min
            if current_minutes >= target_minutes and current_minutes <= target_minutes + 5:
                report = await self._generate_session_report(report_type)
                if report:
                    sent = await self._send(report)
                    if sent:
                        self._report_sent_today[report_type] = today_str
                        await self._persist_reports_immediate()
                        logger.info(f"Session report sent: {report_type}")
                    else:
                        # [P1-FIX] Enqueue for retry instead of silently losing
                        logger.warning(f"Session report FAILED to send: {report_type}, enqueueing retry")
                        self._enqueue_retry(report, f"session_report:{report_type}")
                        await self._persist_retry_queue()

    async def _generate_session_report(self, report_type: str) -> str:
        et = _now_et()

        leader_raw = await self.redis.get(KEYS["leader_lease"])
        leader_id = self._leader_identity(leader_raw)
        leader_ttl = await self.redis.ttl(KEYS["leader_lease"])

        trading_enabled = await self.redis.get(KEYS["trading_enabled"])
        # [LOW-FIX] Unified parser
        ks_status = _is_flag_active(trading_enabled)

        readiness = await self.redis.get(KEYS["readiness"])
        # [LOW-FIX] Unified parser
        ready = _is_flag_active(readiness)

        regime_data = await self.redis.hgetall(KEYS["regime_final"])
        regime = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"

        equity = _safe_float(await self.redis.get(KEYS["equity_verified"]))

        hb_age, hb_display = _safe_ts_age(await self.redis.get(KEYS["consumer_heartbeat"]))
        ack_age, ack_display = _safe_ts_age(await self.redis.get(KEYS["consumer_last_ack"]))

        session = await self.redis.hgetall(KEYS["session_current"])
        pnl = _safe_float(session.get("pnl")) if session else 0.0
        session_dd = _safe_float(session.get("session_dd")) if session else 0.0
        max_dd = _safe_float(session.get("max_dd")) if session else 0.0
        orders_count = session.get("orders_count", "0") if session else "0"
        fills_count = session.get("fills_count", "0") if session else "0"

        state_raw = await self.redis.get(KEYS["state_json"])
        engine_state = "UNKNOWN"
        decision = "UNKNOWN"
        if state_raw:
            try:
                state = json.loads(state_raw)
                engine_state = state.get("engine_state", "UNKNOWN")
                decision = state.get("decision", "UNKNOWN")
            except (json.JSONDecodeError, ValueError):
                pass

        autotune = await self.redis.get(KEYS["autotune_active"])
        autotune_status = str(autotune) if autotune else "NOT_SET"

        halt_active = _is_flag_active(await self.redis.get(KEYS["halt_flag"]))
        flatten_active = _is_flag_active(await self.redis.get(KEYS["flatten_flag"]))

        # Retry queue status
        retry_info = f"Retry Queue: {len(self._retry_queue)} pending" if self._retry_queue else "Retry Queue: empty"

        if report_type == "pre_open":
            header = "\U0001f305 PRE-OPEN REPORT (T-10min)"
            extra = (
                f"\n\n\U0001f50d Pre-Open Checklist:\n"
                f"  \u2022 Leader Lease: {'OK' if leader_id != 'NONE' else 'MISSING!'} ({leader_id}, TTL={leader_ttl}s)\n"
                f"  \u2022 Kill Switch: {'ON (trading allowed)' if ks_status else 'OFF (BLOCKED!)'}\n"
                f"  \u2022 Readiness: {'READY' if ready else 'NOT READY'}\n"
                f"  \u2022 HALT Flag: {'ACTIVE!' if halt_active else 'Clear'}\n"
                f"  \u2022 FLATTEN Flag: {'ACTIVE!' if flatten_active else 'Clear'}\n"
                f"  \u2022 AutoTune Gate: {autotune_status}\n"
                f"  \u2022 Engine State: {engine_state}\n"
                f"  \u2022 Regime: {regime}\n"
                f"  \u2022 Equity: ${equity:,.2f}\n"
                f"  \u2022 {retry_info}\n"
            )
        elif report_type == "mid_day":
            header = "\u2600\ufe0f MID-DAY REPORT (12:00 ET)"
            extra = (
                f"\n\n\U0001f4ca Trading Status:\n"
                f"  \u2022 Engine State: {engine_state}\n"
                f"  \u2022 Decision: {decision}\n"
                f"  \u2022 Orders: {orders_count} / Fills: {fills_count}\n"
                f"  \u2022 PnL: ${pnl:,.2f}\n"
                f"  \u2022 Session DD: {session_dd:.2%}\n"
                f"  \u2022 Max DD: {max_dd:.2%}\n"
                f"  \u2022 Consumer HB: {hb_display} / ACK: {ack_display}\n"
                f"  \u2022 HALT: {'ACTIVE!' if halt_active else 'Clear'}\n"
                f"  \u2022 FLATTEN: {'ACTIVE!' if flatten_active else 'Clear'}\n"
                f"  \u2022 Regime: {regime}\n"
                f"  \u2022 {retry_info}\n"
            )
        elif report_type == "post_close":
            header = "\U0001f307 POST-CLOSE REPORT (16:10 ET)"
            extra = (
                f"\n\n\U0001f4cb End-of-Day Summary:\n"
                f"  \u2022 Final Equity: ${equity:,.2f}\n"
                f"  \u2022 Day PnL: ${pnl:,.2f}\n"
                f"  \u2022 Session DD: {session_dd:.2%}\n"
                f"  \u2022 Max DD: {max_dd:.2%}\n"
                f"  \u2022 Total Orders: {orders_count} / Fills: {fills_count}\n"
                f"  \u2022 Final Regime: {regime}\n"
                f"  \u2022 Leader: {leader_id} (TTL={leader_ttl}s)\n"
                f"  \u2022 Kill Switch: {'ON' if ks_status else 'OFF'}\n"
                f"  \u2022 HALT: {'ACTIVE!' if halt_active else 'Clear'}\n"
                f"  \u2022 FLATTEN: {'ACTIVE!' if flatten_active else 'Clear'}\n"
                f"  \u2022 AutoTune Gate: {autotune_status}\n"
                f"  \u2022 {retry_info}\n"
            )
        else:
            return ""

        return (
            f"{'=' * 35}\n"
            f"{header}\n"
            f"{et.strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"{'=' * 35}"
            f"{extra}"
        )

    # ─── [P1-FIX] Alert & Send (at-least-once delivery) ─────────
    async def _alert(self, event_type: str, message: str, critical: bool = False) -> bool:
        """
        Send alert with cooldown.
        Returns True if the message was successfully delivered.
        Returns False if send failed (caller should NOT update _prev_state).
        Cooldown is recorded ONLY after successful send.
        """
        now = time.time()
        last = self._cooldowns.get(event_type, 0)

        if critical:
            if now - last < CRITICAL_MIN_COOLDOWN:
                return True  # Suppressed by cooldown, not a failure
        else:
            cooldown = COOLDOWN.get(event_type, 300)
            if now - last < cooldown:
                return True  # Suppressed by cooldown, not a failure

        sent = await self._send(message)
        if sent:
            self._cooldowns[event_type] = now
            await self._persist_cooldowns_immediate()
            logger.info(f"Alert sent [{event_type}]: {message[:80]}...")
            return True
        else:
            logger.warning(f"Alert FAILED [{event_type}]: {message[:80]}...")
            # [P1-FIX] Enqueue critical alerts for retry
            if critical:
                self._enqueue_retry(message, f"critical:{event_type}")
                await self._persist_retry_queue()
            return False

    async def _send(self, message: str) -> bool:
        """
        Send telegram message. Returns True on success, False on failure.
        """
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    url,
                    json={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": message,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                if resp.status == 200:
                    return True
                else:
                    body = await resp.text()
                    logger.error(f"Telegram API error {resp.status}: {body}")
                    return False
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False


async def main():
    service = TelegramAlertService()
    await service.start()


if __name__ == "__main__":
    asyncio.run(main())
