"""
ARES V5.6.1b Telegram Alert Service v2.0
─────────────────────────────────────────
Redis Polling 기반 실시간 텔레그램 알림 서비스 (단일 발신 권한).

모니터링 이벤트:
1. Leader Lease 변경/소멸 (split-brain 방지)
2. Kill Switch (trading:enabled) 상태 변경
3. HALT / DEFER / FLATTEN 이벤트
4. ConsumerGuard heartbeat stale (market-hours-aware)
5. IncidentAssist 자동 진단 번들 생성
6. 레짐 변경 알림
7. 장 전/중/후 3회 자동 검증 보고 (스팸 방지)

v2.0 변경사항:
- [P0-1] 토큰 하드코딩 제거 → 환경변수 필수, 없으면 시작 실패
- [P0-2] Consumer stale에 market-hours-aware 예외 처리 추가
- [P0-3] 알림 발신 권한 단일화 (이 서비스만 텔레그램 발송)
- [P0-4] REDIS_URL 환경변수 필수화
- [P1-5] 장 전(ET 09:20)/장 중(ET 12:00)/장 후(ET 16:10) 3회 자동 요약 보고

Author: ARES Architecture Team (4-AI Consensus Design)
Version: 2.0.0
"""
import asyncio
import json
import time
import os
import sys
import logging
from datetime import datetime, timezone, timedelta
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
    "halt_defer": 120,
    "consumer_stale": 300,
    "incident": 60,
    "regime_change": 300,
    "order_executed": 10,
    "daily_summary": 86400,
    "session_report": 3600,  # 장 전/중/후 보고 최소 간격
}

# US Eastern timezone (UTC-4 EDT / UTC-5 EST)
# 간단히 UTC-4 사용 (EDT, 3월~11월)
ET_OFFSET = timedelta(hours=-4)

# Market hours (ET)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN = 0

# 장 전/중/후 보고 시각 (ET)
REPORT_TIMES = {
    "pre_open": (9, 20),    # ET 09:20 - 장 시작 10분 전
    "mid_day": (12, 0),     # ET 12:00 - 장 중간
    "post_close": (16, 10), # ET 16:10 - 장 마감 10분 후
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
    """Get current time in US Eastern."""
    return datetime.now(timezone.utc) + ET_OFFSET


def _is_market_hours() -> bool:
    """Check if current time is within US market hours (ET 09:30-16:00, Mon-Fri)."""
    et = _now_et()
    if et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = et.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0)
    market_close = et.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0)
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
            "readiness": None,
        }
        self._report_sent_today: dict[str, str] = {}  # report_type -> date_str

    async def start(self):
        """Initialize and run the alert service."""
        logger.info("ARES V5.6.1b Telegram Alert Service v2.0 starting...")
        self.redis = aioredis.from_url(REDIS_URL, decode_responses=True)

        # Test Redis connection
        try:
            await self.redis.ping()
            logger.info("Redis connection OK")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            sys.exit(1)

        # Startup notification
        await self._send(
            "\U0001f680 ARES V5.6.1b Alert Service v2.0 started\n"
            f"Monitoring: Leader, KillSwitch, HALT/DEFER, Consumer, Incidents, Regime\n"
            f"Reports: Pre-Open(09:20ET), Mid-Day(12:00ET), Post-Close(16:10ET)"
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
                await self._check_halt_defer()
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
        prev_id = self._leader_identity(self._prev_state["leader"]) if self._prev_state["leader"] is not None else None
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

    # ─── HALT / DEFER ─────────────────────────────────────────────
    async def _check_halt_defer(self):
        """Check HALT and DEFER flags."""
        halt = await self.redis.get(KEYS["halt_flag"])
        state_raw = await self.redis.get(KEYS["state_json"])
        current_halt = bool(halt)
        engine_state = "UNKNOWN"
        if state_raw:
            try:
                state = json.loads(state_raw)
                engine_state = state.get("engine_state", "UNKNOWN")
            except (json.JSONDecodeError, ValueError):
                pass
        if self._prev_state["halt"] is not None and current_halt != self._prev_state["halt"]:
            if current_halt:
                await self._alert(
                    "halt_defer",
                    f"\U0001f534 HALT ACTIVATED\n"
                    f"Engine state: {engine_state}\n"
                    f"All trading suspended.",
                    critical=True,
                )
            else:
                await self._alert(
                    "halt_defer",
                    f"\U0001f7e2 HALT Cleared\n"
                    f"Engine state: {engine_state}\n"
                    f"Trading may resume.",
                )
        self._prev_state["halt"] = current_halt

    # ─── ConsumerGuard (market-hours-aware) ───────────────────────
    async def _check_consumer_guard(self):
        """Check ConsumerGuard heartbeat freshness.
        
        [P0-2] Market-hours-aware: 장 마감 시간에는 heartbeat stale을 경고하지 않음.
        장중에만 consumer stale 경고를 발생시킴.
        """
        hb_raw = await self.redis.get(KEYS["consumer_heartbeat"])
        if hb_raw:
            age = time.time() - float(hb_raw)
            if age > 120:
                # 장중에만 경고 발생
                if _is_market_hours():
                    await self._alert(
                        "consumer_stale",
                        f"\U0001f7e1 ConsumerGuard: Heartbeat stale ({age:.0f}s)\n"
                        f"Engine will enter DEFER mode.\n"
                        f"Check order-intent-executor process.",
                    )
                else:
                    # 장외 시간 - 로그만 남기고 알림 안 보냄
                    logger.debug(f"Consumer heartbeat stale ({age:.0f}s) - outside market hours, suppressed")
        else:
            # heartbeat key 자체가 없음
            if _is_market_hours():
                await self._alert(
                    "consumer_stale",
                    "\U0001f7e1 ConsumerGuard: No heartbeat key\n"
                    "Consumer may not be running during market hours!",
                )

    # ─── Incident ─────────────────────────────────────────────────
    async def _check_incident(self):
        """Check for new IncidentAssist events."""
        incident_raw = await self.redis.get(KEYS["incident_last"])
        if incident_raw:
            try:
                incident = json.loads(incident_raw)
                ts = float(incident.get("ts", 0))
                if time.time() - ts < 30:
                    await self._alert(
                        "incident",
                        f"\U0001f6a8 INCIDENT DETECTED\n"
                        f"Type: {incident.get('type', 'UNKNOWN')}\n"
                        f"Detail: {incident.get('detail', 'N/A')}\n"
                        f"Bundle: {incident.get('bundle_path', 'N/A')}",
                        critical=True,
                    )
            except (json.JSONDecodeError, ValueError):
                pass

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
                    "halt_defer",
                    "\U0001f7e2 V5.6.1b Engine READY\n"
                    "Readiness check passed. Engine is operational.",
                )
            else:
                await self._alert(
                    "halt_defer",
                    "\U0001f7e1 V5.6.1b Engine NOT READY\n"
                    "Readiness check failed. Check system status.",
                )
        self._prev_state["readiness"] = current

    # ─── 장 전/중/후 3회 자동 검증 보고 ──────────────────────────
    async def _check_session_reports(self):
        """Generate pre-open, mid-day, post-close reports at scheduled times.
        
        [P1-5] 하루 3회 자동 요약 보고. 각 보고는 하루에 1회만 발송.
        스팸 방지: report_type + date 조합으로 dedupe.
        """
        et = _now_et()
        today_str = et.strftime("%Y-%m-%d")
        weekday = et.weekday()

        # 주말은 보고 안 함
        if weekday >= 5:
            return

        current_hour = et.hour
        current_min = et.minute

        for report_type, (target_hour, target_min) in REPORT_TIMES.items():
            # 이미 오늘 보낸 보고인지 확인
            sent_date = self._report_sent_today.get(report_type)
            if sent_date == today_str:
                continue

            # 보고 시각이 지났는지 확인 (±5분 윈도우)
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

        hb_raw = await self.redis.get(KEYS["consumer_heartbeat"])
        hb_age = f"{time.time() - float(hb_raw):.0f}s" if hb_raw else "N/A"

        ack_raw = await self.redis.get(KEYS["consumer_last_ack"])
        ack_age = f"{time.time() - float(ack_raw):.0f}s" if ack_raw else "N/A"

        regime_data = await self.redis.hgetall(KEYS["regime_final"])
        regime = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"

        # Equity
        equity_raw = await self.redis.get(KEYS["equity_verified"])
        equity = 0
        try:
            equity = float(equity_raw)
        except (ValueError, TypeError):
            try:
                equity = float(json.loads(equity_raw).get("total", 0))
            except Exception:
                pass

        # Session stats
        session = await self.redis.hgetall(KEYS["session_current"])
        pnl = float(session.get("pnl", 0)) if session else 0
        session_dd = float(session.get("session_dd", 0)) if session else 0
        max_dd = float(session.get("max_dd", 0)) if session else 0
        orders_count = session.get("orders_count", 0) if session else 0
        fills_count = session.get("fills_count", 0) if session else 0

        # Engine state
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

        # AutoTune Gate
        autotune = await self.redis.get(KEYS["autotune_active"])
        autotune_status = str(autotune) if autotune else "NOT_SET"

        # Halt flag
        halt = await self.redis.get(KEYS["halt_flag"])

        # PM2 process check via monitor metrics
        monitor_metrics = await self.redis.hgetall(KEYS["monitor_metrics"])

        # Report header
        if report_type == "pre_open":
            header = "\U0001f305 PRE-OPEN REPORT (T-10min)"
            extra = (
                f"\n\n\U0001f50d Pre-Open Checklist:\n"
                f"  \u2022 Leader Lease: {'OK' if leader_id != 'NONE' else 'MISSING!'} ({leader_id}, TTL={leader_ttl}s)\n"
                f"  \u2022 Kill Switch: {'ON (trading allowed)' if ks_status else 'OFF (BLOCKED!)'}\n"
                f"  \u2022 Readiness: {'READY' if ready else 'NOT READY'}\n"
                f"  \u2022 HALT Flag: {'ACTIVE!' if halt else 'Clear'}\n"
                f"  \u2022 AutoTune Gate: {autotune_status}\n"
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
                f"  \u2022 AutoTune Gate: {autotune_status}\n"
            )
        else:
            return ""

        report = (
            f"{'=' * 35}\n"
            f"{header}\n"
            f"{et.strftime('%Y-%m-%d %H:%M ET')}\n"
            f"{'=' * 35}"
            f"{extra}"
        )
        return report

    # ─── Alert & Send ─────────────────────────────────────────────
    async def _alert(self, event_type: str, message: str, critical: bool = False):
        """Send alert with cooldown."""
        now = time.time()
        cooldown = COOLDOWN.get(event_type, 300)
        last = self._cooldowns.get(event_type, 0)
        if now - last < cooldown and not critical:
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
                        "parse_mode": "HTML",
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
