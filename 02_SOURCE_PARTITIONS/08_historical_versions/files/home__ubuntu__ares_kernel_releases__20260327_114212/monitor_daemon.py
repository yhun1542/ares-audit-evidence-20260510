"""
ARES Monitoring Daemon v3.1 (V5.6.1b Hardened)
Standalone daemon that runs 24/7 and monitors all system components.
Designed to run as a PM2 process.

V5.6.1b additions:
- Leader Lease monitoring with split-brain alerts
- Kill Switch state change detection
- ConsumerGuard health tracking
- IncidentAssist event forwarding
- Enhanced daily summary with hardening metrics

Usage: python3 monitor_daemon.py
Author: ARES Architecture Team (4-AI Consensus Design)
Version: 3.1.0
"""
import asyncio
import json
import time
import os
import sys
import logging
# aiohttp removed - telegram sending delegated to telegram-alerts (P0-3)

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import redis.asyncio as aioredis
from monitoring import MonitoringService, HealthStatus
from kernel_client import KernelClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("ares.monitor.daemon")

REDIS_URL = os.environ.get("REDIS_URL", "")
if not REDIS_URL:
    print("FATAL: REDIS_URL environment variable is required", file=sys.stderr)
    sys.exit(1)
# TELEGRAM 발신은 ares-v56-telegram-alerts로 단일화됨 (P0-3)
CHECK_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "30"))


async def send_telegram(message: str):
    """Log notification (telegram sending delegated to ares-v56-telegram-alerts).
    
    [P0-3] 알림 발신 권한 단일화: monitor_daemon은 로그만 남기고,
    실제 텔레그램 발송은 ares-v56-telegram-alerts가 담당합니다.
    이벤트는 Redis pub/sub 또는 키 변경으로 telegram-alerts가 감지합니다.
    """
    logger.info(f"[EVENT] {message}")


async def main():
    logger.info("ARES Monitoring Daemon v3.1 (V5.6.1b Hardened) starting...")
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    monitor = MonitoringService(redis, telegram_fn=send_telegram)
    await monitor.initialize()

    # Track consecutive failures
    consecutive_critical = 0
    last_daily_report = ""
    def _leader_id(raw):
        if not raw or raw == "NONE":
            return "NONE"
        try:
            import json as _j
            d = _j.loads(raw)
            return f"{d.get('run_id', '?')}:{d.get('pid', '?')}"
        except Exception:
            return str(raw)
    prev_leader = None
    prev_leader_id = None
    prev_kill_switch = None

    logger.info(f"Monitoring started: interval={CHECK_INTERVAL}s")
    logger.info("V5.6.1b checks: Leader Lease, Kill Switch, ConsumerGuard, IncidentAssist")

    while True:
        try:
            # Run health check (now includes 8 checks)
            results = await monitor.run_health_check()

            # Collect metrics (now includes V5.6.1b data)
            metrics = await monitor.collect_trading_metrics()

            # ─── V5.6.1b: Leader Lease change detection ─────────
            current_leader = metrics.get("leader_holder", "NONE")
            current_leader_id = _leader_id(current_leader)
            if prev_leader_id is not None and current_leader_id != prev_leader_id:
                msg = f"LEADER CHANGE: {prev_leader_id} -> {current_leader_id}"
                logger.warning(msg)
                await send_telegram(f"\U0001f6a8 {msg}")
            prev_leader = current_leader
            prev_leader_id = current_leader_id

            # ─── V5.6.1b: Kill Switch change detection ──────────
            current_ks = metrics.get("kill_switch_enabled", False)
            if prev_kill_switch is not None and current_ks != prev_kill_switch:
                state = "ENABLED (trading allowed)" if current_ks else "DISABLED (trading blocked!)"
                msg = f"KILL SWITCH CHANGED: {state}"
                logger.warning(msg)
                await send_telegram(f"\U0001f6a8 {msg}")
            prev_kill_switch = current_ks

            # ─── V5.6.1b: Incident detection ────────────────────
            incident_raw = await redis.get("ares:v55:incident:last")
            if incident_raw:
                try:
                    incident = json.loads(incident_raw)
                    incident_ts = incident.get("ts", 0)
                    if time.time() - float(incident_ts) < CHECK_INTERVAL * 2:
                        # Recent incident - forward to telegram
                        await send_telegram(
                            f"\U0001f6a8 INCIDENT: {incident.get('type', 'UNKNOWN')}\n"
                            f"Detail: {incident.get('detail', 'N/A')}\n"
                            f"Bundle: {incident.get('bundle_path', 'N/A')}"
                        )
                except (json.JSONDecodeError, ValueError):
                    pass

            # Count critical
            critical_areas = [
                area
                for area, r in results.items()
                if r.get("status") == HealthStatus.CRITICAL
            ]

            if critical_areas:
                consecutive_critical += 1
                logger.warning(
                    f"HEALTH_CHECK: CRITICAL areas={critical_areas} "
                    f"consecutive={consecutive_critical}"
                )

                # Auto-degraded after 3 consecutive critical
                if consecutive_critical >= 3:
                    kernel = KernelClient(redis)
                    try:
                        await kernel.transition(
                            "DEGRADED",
                            f"monitor_auto_degraded:{','.join(critical_areas)}",
                        )
                        await send_telegram(
                            f"\U0001f534 AUTO-DEGRADED after {consecutive_critical} "
                            f"consecutive critical checks: {critical_areas}"
                        )
                    except Exception as e:
                        logger.error(f"Auto-degrade failed: {e}")
            else:
                if consecutive_critical > 0:
                    logger.info(
                        f"HEALTH_CHECK: recovered after {consecutive_critical} critical"
                    )
                    await send_telegram(
                        f"\U0001f7e2 RECOVERED after {consecutive_critical} consecutive critical checks"
                    )
                consecutive_critical = 0

            # Log summary with V5.6.1b data
            overall = "HEALTHY"
            for r in results.values():
                if r.get("status") == HealthStatus.CRITICAL:
                    overall = "CRITICAL"
                    break
                elif r.get("status") == HealthStatus.WARNING:
                    overall = "WARNING"

            logger.info(
                f"HEALTH_CHECK: overall={overall} "
                f"equity=${metrics.get('equity', 0):,.0f} "
                f"dd={metrics.get('session_dd', 0):.2%} "
                f"regime={metrics.get('regime', 'N/A')} "
                f"leader={current_leader} "
                f"ks={'ON' if current_ks else 'OFF'}"
            )

            # Daily report (at market close ET)
            today = time.strftime("%Y-%m-%d")
            if today != last_daily_report:
                # Check if it's after 4pm ET (21:00 UTC)
                utc_hour = int(time.strftime("%H"))
                if utc_hour >= 21:
                    await monitor.generate_daily_summary()
                    last_daily_report = today
                    logger.info("V5.6.1b daily summary generated and sent")

        except Exception as e:
            logger.error(f"Health check cycle failed: {e}", exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
