"""
ARES V5.6.1b Telegram Alert Service v3.0
─────────────────────────────────────────
Redis Polling 기반 실시간 텔레그램 알림 서비스 (단일 발신 권한).

모니터링 이벤트:
1. Leader Lease 변경/소멸 (split-brain 방지)
2. Kill Switch (trading:enabled) 상태 변경
3. HALT / DEFER / FLATTEN 이벤트 (명시적 전환 추적)
4. ConsumerGuard heartbeat stale (market-hours-aware)
5. IncidentAssist 자동 진단 번들 생성 (incident_id dedupe)
6. 레짐 변경 알림
7. 장 전/중/후 3회 자동 검증 보고 (스팸 방지)

v3.0 변경사항 (v2.0 대비):
- [FIX-1] ET 고정 오프셋(UTC-4) → zoneinfo("America/New_York") 적용 (DST 자동 처리)
- [FIX-2] incident critical=True 시 cooldown 우회 → incident_id 기반 dedupe 추가
- [FIX-3] HALT만 감시 → DEFER 전환 추적 + FLATTEN 감시 명시적 구현
- [FIX-4] _alert()의 critical 파라미터가 cooldown을 완전 우회하지 않도록 최소 간격 적용

Author: ARES Architecture Team (4-AI Consensus Design)
Version: 3.0.0
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

# Startup validation
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
    "incident": 60,
    "regime_change": 300,
    "order_executed": 10,
    "daily_summary": 86400,
    "session_report": 3600,
}

# Minimum cooldown for critical alerts (prevents rapid-fire even for critical events)
CRITICAL_MIN_COOLDOWN = 15  # seconds

# US Eastern timezone (DST-aware via zoneinfo)
ET = ZoneInfo("America/New_York")

# Market hours (ET)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN = 0

# 장 전/중/후 보고 시각 (ET)
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


class TelegramAlertService:
    def __init__(self):
        self.redis = None
        self._cooldowns: dict[str, float] = {}
        self._prev_state = {
            "leader": None,
            "kill_switch": None,
            "regime": None,
            "halt": None,
            "flatten": None,
            "engine_state": None,  # DEFER/EXECUTE/HOLD 등 추적
            "readiness": None,
        }
        self._report_sent_today: dict[str, str] = {}
        self._seen_incident_ids: set[str] = set()  # incident dedupe

    async def start(self):
        """Initialize Redis connection and start polling."""
        logger.info("ARES V5.6.1b Telegram Alert Service v3.0 starting...")
        self.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            await self.redis.ping()
            logger.info("Redis connection OK")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            sys.exit(1)

        await self._send(
            "ARES V5.6.1b Telegram Alert Service v3.0 started\n"
            "Monitoring: Leader, KillSwitch, HALT/DEFER/FLATTEN, Consumer, Incidents, Regime\n"
            "Reports: Pre-Open(09:20ET), Mid-Day(12:00ET), Post-Close(16:10ET)\n"
            f"Timezone: {ET} (DST-aware)"
        )
        await self._poll_loop()

    async def _poll_loop(self):
        """Poll Redis keys for changes every 10 seconds."""
        POLL_INTERVAL = 10
        logger.info(f"Polling loop started: interval={POLL_INTERVAL}s")
        while True:
            try:
                await self._check_leader_lease()
                await self._check_kill_switch()
                await self._check_halt()
                await self._check_flatten()
                await self._check_defer()
                await self._check_consumer_guard()
                await self._check_incident()
                await self._check_regime_change()
                await self._check_v56_state()
                await self._check_session_reports()
            except Exception as e:
                logger.error(f"Poll cycle error: {e}", exc_info=True)
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
        """Check Leader Lease status - compare by run_id+pid only (ignore ts renewal)."""
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
                await self._alert(
                    "leader_change",
                    "\U0001f6a8 LEADER LEASE LOST!\n"
                    f"Previous holder: {prev_id}\n"
                    f"No active engine - orders will NOT be processed!",
                    critical=True,
                )
            else:
                await self._alert(
                    "leader_change",
                    f"\U0001f504 Leader Lease Changed\n"
                    f"{prev_id} \u2192 {current_id}\n"
                    f"TTL: {ttl}s",
                )
        elif current_id == "NONE" and prev_id is None:
            await self._alert(
                "leader_change",
                "\U0001f6a8 No Leader Lease holder detected on startup!",
                critical=True,
            )
        self._prev_state["leader"] = current_raw

    # ─── Kill Switch ──────────────────────────────────────────────
    async def _check_kill_switch(self):
        """Check Kill Switch state."""
        enabled_raw = await self.redis.get(KEYS["trading_enabled"])
        enabled = str(enabled_raw).lower() in ("true", "1", "yes") if enabled_raw else False

        if self._prev_state["kill_switch"] is not None and enabled != self._prev_state["kill_switch"]:
            if not enabled:
                await self._alert(
                    "kill_switch",
                    "\U0001f534 KILL SWITCH ACTIVATED!\n"
                    f"trading:enabled = {enabled_raw}\n"
                    "All order execution is BLOCKED.",
                    critical=True,
                )
            else:
                await self._alert(
                    "kill_switch",
                    "\U0001f7e2 Kill Switch Released\n"
                    f"trading:enabled = {enabled_raw}\n"
                    "Order execution is now allowed.",
                )
        self._prev_state["kill_switch"] = enabled

    # ─── HALT ─────────────────────────────────────────────────────
    async def _check_halt(self):
        """Check HALT flag explicitly."""
        halt = await self.redis.get(KEYS["halt_flag"])
        current_halt = bool(halt)

        if self._prev_state["halt"] is not None and current_halt != self._prev_state["halt"]:
            if current_halt:
                await self._alert(
                    "halt",
                    "\U0001f534 HALT ACTIVATED\n"
                    "All trading suspended. Check emergency loss guard or manual halt.",
                    critical=True,
                )
            else:
                await self._alert(
                    "halt",
                    "\U0001f7e2 HALT Cleared\n"
                    "Trading suspension lifted.",
                )
        self._prev_state["halt"] = current_halt

    # ─── FLATTEN ──────────────────────────────────────────────────
    async def _check_flatten(self):
        """Check FLATTEN flag explicitly."""
        flatten = await self.redis.get(KEYS["flatten_flag"])
        current_flatten = bool(flatten)

        if self._prev_state["flatten"] is not None and current_flatten != self._prev_state["flatten"]:
            if current_flatten:
                await self._alert(
                    "flatten",
                    "\U0001f534 FLATTEN ACTIVATED\n"
                    "Engine is flattening all positions. Emergency de-risk in progress.",
                    critical=True,
                )
            else:
                await self._alert(
                    "flatten",
                    "\U0001f7e2 FLATTEN Cleared\n"
                    "Position flattening completed or cancelled.",
                )
        self._prev_state["flatten"] = current_flatten

    # ─── DEFER (engine_state tracking) ────────────────────────────
    async def _check_defer(self):
        """Track engine_state transitions (EXECUTE ↔ DEFER ↔ HOLD etc.)."""
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
            # Only alert on significant transitions
            if engine_state == "DEFER" and prev_engine != "DEFER":
                if _is_market_hours():
                    await self._alert(
                        "defer",
                        f"\U0001f7e1 Engine entered DEFER\n"
                        f"Previous state: {prev_engine}\n"
                        f"Consumer heartbeat may be stale. Check order-intent-executor.",
                    )
                else:
                    logger.info(f"Engine entered DEFER outside market hours (normal): {prev_engine} -> {engine_state}")
            elif prev_engine == "DEFER" and engine_state != "DEFER":
                await self._alert(
                    "defer",
                    f"\U0001f7e2 Engine exited DEFER\n"
                    f"New state: {engine_state}\n"
                    f"Order processing resumed.",
                )

        self._prev_state["engine_state"] = engine_state

    # ─── Consumer Guard ───────────────────────────────────────────
    async def _check_consumer_guard(self):
        """Check ConsumerGuard heartbeat (market-hours-aware)."""
        hb_raw = await self.redis.get(KEYS["consumer_heartbeat"])
        if hb_raw:
            try:
                age = time.time() - float(hb_raw)
            except (ValueError, TypeError):
                return

            if age > 120:
                if _is_market_hours():
                    await self._alert(
                        "consumer_stale",
                        f"\U0001f7e1 ConsumerGuard: Heartbeat stale ({age:.0f}s)\n"
                        "Consumer may need restart:\n"
                        "  pm2 restart order-intent-executor",
                    )
                else:
                    logger.debug(f"Consumer heartbeat stale ({age:.0f}s) - outside market hours, suppressed")
        else:
            if _is_market_hours():
                await self._alert(
                    "consumer_stale",
                    "\U0001f7e1 ConsumerGuard: No heartbeat key\n"
                    "Consumer may not be running during market hours!",
                )

    # ─── Incident (with incident_id dedupe) ───────────────────────
    async def _check_incident(self):
        """Check for new IncidentAssist events with incident_id-based deduplication."""
        incident_raw = await self.redis.get(KEYS["incident_last"])
        if not incident_raw:
            return

        try:
            incident = json.loads(incident_raw)
        except (json.JSONDecodeError, ValueError):
            return

        ts = float(incident.get("ts", 0))
        # Only process recent incidents (within last 60 seconds)
        if time.time() - ts > 60:
            return

        # Build a unique incident ID for deduplication
        incident_id = (
            f"{incident.get('type', 'UNKNOWN')}"
            f":{incident.get('detail', '')}"
            f":{int(ts)}"
        )

        # Skip if we've already seen this incident
        if incident_id in self._seen_incident_ids:
            return

        self._seen_incident_ids.add(incident_id)

        # Prune old incident IDs (keep last 100)
        if len(self._seen_incident_ids) > 100:
            self._seen_incident_ids = set(list(self._seen_incident_ids)[-50:])

        await self._alert(
            "incident",
            f"\U0001f6a8 INCIDENT DETECTED\n"
            f"Type: {incident.get('type', 'UNKNOWN')}\n"
            f"Detail: {incident.get('detail', 'N/A')}\n"
            f"Bundle: {incident.get('bundle_path', 'N/A')}\n"
            f"ID: {incident_id}",
            critical=True,
        )

    # ─── Regime Change ────────────────────────────────────────────
    async def _check_regime_change(self):
        """Check for regime changes."""
        regime_data = await self.redis.hgetall(KEYS["regime_final"])
        current_regime = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"

        if (
            self._prev_state["regime"] is not None
            and current_regime != self._prev_state["regime"]
            and current_regime != "UNKNOWN"
        ):
            await self._alert(
                "regime_change",
                f"\U0001f504 Regime Changed\n"
                f"{self._prev_state['regime']} \u2192 {current_regime}\n"
                f"Source: {regime_data.get('source', 'N/A')}",
            )
        self._prev_state["regime"] = current_regime

    # ─── V5.6.1b State ───────────────────────────────────────────
    async def _check_v56_state(self):
        """Check V5.6.1b engine state for readiness changes."""
        readiness = await self.redis.get(KEYS["readiness"])
        current = str(readiness).lower() in ("true", "1") if readiness else False

        if self._prev_state["readiness"] is not None and current != self._prev_state["readiness"]:
            if current:
                await self._alert(
                    "halt",
                    "\U0001f7e2 V5.6.1b Engine READY\n"
                    "Readiness check passed. Engine is operational.",
                )
            else:
                await self._alert(
                    "halt",
                    "\U0001f7e1 V5.6.1b Engine NOT READY\n"
                    "Readiness check failed. Check system status.",
                )
        self._prev_state["readiness"] = current

    # ─── 장 전/중/후 3회 자동 검증 보고 ──────────────────────────
    async def _check_session_reports(self):
        """Generate pre-open, mid-day, post-close reports at scheduled times."""
        et = _now_et()
        today_str = et.strftime("%Y-%m-%d")

        if et.weekday() >= 5:
            return

        current_hour = et.hour
        current_min = et.minute

        for report_type, (target_hour, target_min) in REPORT_TIMES.items():
            sent_date = self._report_sent_today.get(report_type)
            if sent_date == today_str:
                continue

            target_minutes = target_hour * 60 + target_min
            current_minutes = current_hour * 60 + current_min

            if current_minutes >= target_minutes and current_minutes <= target_minutes + 5:
                report = await self._generate_session_report(report_type)
                if report:
                    await self._send(report)
                    self._report_sent_today[report_type] = today_str
                    logger.info(f"Session report sent: {report_type}")

    async def _generate_session_report(self, report_type: str) -> str:
        """Generate a comprehensive session report."""
        et = _now_et()

        # Collect all metrics from Redis
        leader_raw = await self.redis.get(KEYS["leader_lease"])
        leader_id = self._leader_identity(leader_raw)
        leader_ttl = await self.redis.ttl(KEYS["leader_lease"])

        trading_enabled = await self.redis.get(KEYS["trading_enabled"])
        ks_status = str(trading_enabled).lower() in ("true", "1", "yes") if trading_enabled else False

        readiness = await self.redis.get(KEYS["readiness"])
        ready = str(readiness).lower() in ("true", "1") if readiness else False

        regime_data = await self.redis.hgetall(KEYS["regime_final"])
        regime = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"

        equity_raw = await self.redis.get(KEYS["equity_verified"])
        equity = 0.0
        try:
            equity = float(equity_raw)
        except (ValueError, TypeError):
            try:
                equity = float(json.loads(equity_raw).get("total", 0))
            except Exception:
                pass

        hb_raw = await self.redis.get(KEYS["consumer_heartbeat"])
        hb_age = "N/A"
        if hb_raw:
            try:
                hb_age = f"{time.time() - float(hb_raw):.0f}s"
            except (ValueError, TypeError):
                pass

        ack_raw = await self.redis.get(KEYS["consumer_last_ack"])
        ack_age = "N/A"
        if ack_raw:
            try:
                ack_age = f"{time.time() - float(ack_raw):.0f}s"
            except (ValueError, TypeError):
                pass

        session = await self.redis.hgetall(KEYS["session_current"])
        pnl = float(session.get("pnl", 0)) if session else 0
        session_dd = float(session.get("session_dd", 0)) if session else 0
        max_dd = float(session.get("max_dd", 0)) if session else 0
        orders_count = session.get("orders_count", 0) if session else 0
        fills_count = session.get("fills_count", 0) if session else 0

        state_raw = await self.redis.get(KEYS["state_json"])
        engine_state = "UNKNOWN"
        decision = "UNKNOWN"
        if state_raw:
            try:
                state = json.loads(state_raw)
                engine_state = state.get("engine_state", "UNKNOWN")
                decision = state.get("decision", "UNKNOWN")
            except Exception:
                pass

        autotune = await self.redis.get(KEYS["autotune_active"])
        autotune_status = str(autotune) if autotune else "NOT_SET"

        halt = await self.redis.get(KEYS["halt_flag"])
        flatten = await self.redis.get(KEYS["flatten_flag"])

        if report_type == "pre_open":
            header = "\U0001f305 PRE-OPEN REPORT (T-10min)"
            extra = (
                f"\n\n\U0001f50d Pre-Open Checklist:\n"
                f"  \u2022 Leader Lease: {'OK' if leader_id != 'NONE' else 'MISSING!'} ({leader_id}, TTL={leader_ttl}s)\n"
                f"  \u2022 Kill Switch: {'ON (trading allowed)' if ks_status else 'OFF (BLOCKED!)'}\n"
                f"  \u2022 Readiness: {'READY' if ready else 'NOT READY'}\n"
                f"  \u2022 HALT Flag: {'ACTIVE!' if halt else 'Clear'}\n"
                f"  \u2022 FLATTEN Flag: {'ACTIVE!' if flatten else 'Clear'}\n"
                f"  \u2022 AutoTune Gate: {autotune_status}\n"
                f"  \u2022 Engine State: {engine_state}\n"
                f"  \u2022 Regime: {regime}\n"
                f"  \u2022 Equity: ${equity:,.2f}\n"
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
                f"  \u2022 Consumer HB: {hb_age} / ACK: {ack_age}\n"
                f"  \u2022 HALT: {'ACTIVE!' if halt else 'Clear'}\n"
                f"  \u2022 FLATTEN: {'ACTIVE!' if flatten else 'Clear'}\n"
                f"  \u2022 Regime: {regime}\n"
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
                f"  \u2022 HALT: {'ACTIVE!' if halt else 'Clear'}\n"
                f"  \u2022 FLATTEN: {'ACTIVE!' if flatten else 'Clear'}\n"
                f"  \u2022 AutoTune Gate: {autotune_status}\n"
            )
        else:
            return ""

        report = (
            f"{'=' * 35}\n"
            f"{header}\n"
            f"{et.strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"{'=' * 35}"
            f"{extra}"
        )
        return report

    # ─── Alert & Send ─────────────────────────────────────────────
    async def _alert(self, event_type: str, message: str, critical: bool = False):
        """Send alert with cooldown. Critical alerts have a minimum cooldown instead of bypass."""
        now = time.time()
        last = self._cooldowns.get(event_type, 0)

        if critical:
            # Critical events still have a minimum cooldown to prevent rapid-fire
            if now - last < CRITICAL_MIN_COOLDOWN:
                return
        else:
            cooldown = COOLDOWN.get(event_type, 300)
            if now - last < cooldown:
                return

        self._cooldowns[event_type] = now
        await self._send(message)
        logger.info(f"Alert sent [{event_type}]: {message[:80]}...")

    async def _send(self, message: str):
        """Send telegram message."""
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
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Telegram API error {resp.status}: {body}")
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")


async def main():
    service = TelegramAlertService()
    await service.start()


if __name__ == "__main__":
    asyncio.run(main())
