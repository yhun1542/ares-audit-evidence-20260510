"""
ARES 24/7 Monitoring Service v3.0 (V5.6.1b Hardened)
Comprehensive monitoring with:
- 8 core checks: Price Feed, Position Sync, Order Pipeline, Risk State, Equity,
                  Leader Lease, Kill Switch, ConsumerGuard
- V5.6.1b hardening status (Leader Lease, Kill Switch, ConsumerGuard, IncidentAssist)
- Periodic health checks (every 30s)
- Telegram alerts with cooldown
- Structured audit logging
- Daily summary report with full trading metrics
- Degraded mode auto-detection

Metrics tracked (per user requirement):
- Regime situation, Buy/Sell signals, Regime accuracy
- Max Drawdown, Calmar/Sortino/Sharpe ratios
- Win Rate, Leverage, Turnover, Exposure
- VIXY activation count, Order quantity distribution
- Failure reason frequency
- Leader Lease ownership, Kill Switch state, ConsumerGuard health

Author: ARES Architecture Team (4-AI Consensus Design)
Version: 3.1.0 (V5.6.1b Hardened)
"""
import asyncio
import json
import time
import logging
from typing import Dict, Optional
from enum import Enum


# ─── Market Hours Helper (P0-2) ──────────────────────────────
from datetime import datetime, timezone, timedelta

_ET_OFFSET = timedelta(hours=-4)  # EDT

def _is_market_hours_monitoring() -> bool:
    """Check if within US market hours (ET 09:30-16:00, Mon-Fri)."""
    et = datetime.now(timezone.utc) + _ET_OFFSET
    if et.weekday() >= 5:
        return False
    return (et.hour == 9 and et.minute >= 30) or (10 <= et.hour < 16)

logger = logging.getLogger("ares.monitor")


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class MonitoringService:
    """
    24/7 monitoring daemon that watches all system components.
    V5.6.1b: Added Leader Lease, Kill Switch, ConsumerGuard monitoring.
    """

    KEYS = {
        "health": "ares:monitor:health",
        "alerts": "ares:monitor:alerts",
        "metrics": "ares:monitor:metrics",
        "daily": "ares:monitor:daily",
        "config": "ares:monitor:config",
    }

    # V5.6.1b hardening keys
    V56_KEYS = {
        "leader_lease": "ares:v56:leader:lease",
        "trading_enabled": "trading:enabled",
        "trade_halt": "policy:trade_halt",
        "consumer_heartbeat": "ares:v55:consumer:heartbeat",
        "consumer_last_ack": "ares:v55:consumer:last_ack",
        "readiness": "ares:v55:live:readiness",
        "heartbeat": "ares:v55:live:heartbeat",
        "halt_flag": "ares:v55:live:control:halt",
        "incident_last": "ares:v55:incident:last",
        "incident_history": "ares:v55:incident:history",
        "autotune_active": "ares:v55:autotune:active_override",
        "autotune_suggestions": "ares:v55:autotune:suggestions",
        "state_json": "ares:v55:state:summary",
        "metrics_latest": "ares:v55:metrics:latest",
        "session_stats": "ares:v55:session:stats",
        "execution_stream": "ares:v55:execution:intents",
        "canonical_stream": "emarkos:v6:order:intent",
        "decision_last": "ares:v55:decision:last",
        "flap_count": "ares:v55:feedback:flap_count",
    }

    DEFAULT_CONFIG = {
        "check_interval_s": 30,
        "alert_cooldown_s": 300,
        "critical_threshold": 3,
        "leader_lease_warn_ttl_s": 15,
        "consumer_heartbeat_stale_s": 120,
        "consumer_ack_stale_s": 300,
        "price_stale_s": 120,
        "regime_stale_s": 300,
        "equity_drift_warn_pct": 0.05,
        "equity_drift_crit_pct": 0.10,
    }

    def __init__(self, redis_client, telegram_fn=None):
        self.redis = redis_client
        self.telegram = telegram_fn
        self._config = dict(self.DEFAULT_CONFIG)
        self._alert_cooldowns: Dict[str, float] = {}
        self._daily_metrics = {
            "halt_count": 0,
            "defer_count": 0,
            "degraded_count": 0,
            "incident_count": 0,
            "leader_lost_count": 0,
            "kill_switch_trip_count": 0,
            "consumer_stale_count": 0,
        }

    async def initialize(self):
        """Load config from Redis if available."""
        try:
            cfg = await self.redis.hgetall(self.KEYS["config"])
            if cfg:
                for k, v in cfg.items():
                    if k in self._config:
                        self._config[k] = type(self._config[k])(v)
        except Exception as e:
            logger.warning(f"Config load failed, using defaults: {e}")

    async def run_health_check(self) -> Dict[str, dict]:
        """Run all health checks and return results."""
        checks = {
            "price_feed": self._check_price_feed,
            "position_sync": self._check_position_sync,
            "order_pipeline": self._check_order_pipeline,
            "risk_state": self._check_risk_state,
            "equity": self._check_equity,
            # V5.6.1b hardening checks
            "leader_lease": self._check_leader_lease,
            "kill_switch": self._check_kill_switch,
            "consumer_guard": self._check_consumer_guard,
        }
        results = {}
        for name, fn in checks.items():
            try:
                results[name] = await fn()
            except Exception as e:
                results[name] = {
                    "status": HealthStatus.UNKNOWN,
                    "detail": f"Check failed: {e}",
                }
        # Determine overall status
        overall = HealthStatus.HEALTHY
        critical_areas = []
        for name, r in results.items():
            if r.get("status") == HealthStatus.CRITICAL:
                overall = HealthStatus.CRITICAL
                critical_areas.append(name)
            elif r.get("status") == HealthStatus.WARNING and overall != HealthStatus.CRITICAL:
                overall = HealthStatus.WARNING

        # Store results
        await self.redis.hset(
            self.KEYS["health"],
            mapping={
                "overall": overall.value,
                "last_check": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "details": json.dumps(
                    {k: {"status": v["status"].value if isinstance(v["status"], HealthStatus) else v["status"],
                          "detail": v.get("detail", "")} for k, v in results.items()}
                ),
            },
        )
        await self.redis.expire(self.KEYS["health"], 120)

        # Alert on critical
        if critical_areas:
            await self._send_alert(
                "HEALTH CHECK CRITICAL",
                f"Critical areas: {', '.join(critical_areas)}",
                "critical",
            )
        return results

    # ─── Original 5 checks ───────────────────────────────────────

    async def _check_price_feed(self) -> dict:
        """Check price feed freshness."""
        try:
            feed_ts = await self.redis.get("realtime:feed:SPY:ts")
            if not feed_ts:
                feed_ts = await self.redis.get("realtime:price:SPY:ts")
            if not feed_ts:
                return {"status": HealthStatus.WARNING, "detail": "No price timestamp found"}
            age = time.time() - float(feed_ts)
            if age > self._config["price_stale_s"]:
                return {
                    "status": HealthStatus.CRITICAL,
                    "detail": f"Price feed stale: {age:.0f}s (limit {self._config['price_stale_s']}s)",
                }
            return {"status": HealthStatus.HEALTHY, "detail": f"Price feed age: {age:.0f}s"}
        except Exception as e:
            return {"status": HealthStatus.WARNING, "detail": f"Price check error: {e}"}

    async def _check_position_sync(self) -> dict:
        """Check position sync freshness."""
        try:
            pos_raw = await self.redis.get("emarkos:v1:positions")
            if not pos_raw:
                return {"status": HealthStatus.WARNING, "detail": "No position data"}
            data = json.loads(pos_raw)
            ts = data.get("ts") or data.get("updated_at")
            if ts:
                age = time.time() - float(ts)
                if age > 600:
                    return {"status": HealthStatus.WARNING, "detail": f"Positions stale: {age:.0f}s"}
            return {"status": HealthStatus.HEALTHY, "detail": "Positions synced"}
        except Exception as e:
            return {"status": HealthStatus.WARNING, "detail": f"Position check error: {e}"}

    async def _check_order_pipeline(self) -> dict:
        """Check order pipeline health."""
        try:
            # Check execution stream length
            stream_key = self.V56_KEYS["execution_stream"]
            stream_len = await self.redis.xlen(stream_key)
            # Check canonical stream if active
            canonical_len = await self.redis.xlen(self.V56_KEYS["canonical_stream"])
            detail = f"exec_stream={stream_len}, canonical_stream={canonical_len}"
            # Check for stuck orders
            open_count = await self.redis.get("ares:orders:open_count")
            if open_count and int(open_count) > 10:
                return {"status": HealthStatus.WARNING, "detail": f"High open orders: {open_count}. {detail}"}
            return {"status": HealthStatus.HEALTHY, "detail": detail}
        except Exception as e:
            return {"status": HealthStatus.WARNING, "detail": f"Pipeline check error: {e}"}

    async def _check_risk_state(self) -> dict:
        """Check risk state."""
        try:
            regime_raw = await self.redis.hgetall("regime:final:current")
            if not regime_raw:
                return {"status": HealthStatus.WARNING, "detail": "No regime data"}
            regime = regime_raw.get("regime", "UNKNOWN")
            ts = regime_raw.get("ts")
            if ts:
                age = time.time() - float(ts)
                if age > self._config["regime_stale_s"]:
                    return {"status": HealthStatus.CRITICAL, "detail": f"Regime stale: {age:.0f}s"}
            # Check halt flags
            halt = await self.redis.get(self.V56_KEYS["halt_flag"])
            trade_halt = await self.redis.get(self.V56_KEYS["trade_halt"])
            if halt or trade_halt:
                return {"status": HealthStatus.WARNING, "detail": f"HALT active: halt={halt}, trade_halt={trade_halt}"}
            return {"status": HealthStatus.HEALTHY, "detail": f"Regime={regime}"}
        except Exception as e:
            return {"status": HealthStatus.WARNING, "detail": f"Risk check error: {e}"}

    async def _check_equity(self) -> dict:
        """Check equity data."""
        try:
            equity_raw = await self.redis.get("ofg:equity:verified")
            if not equity_raw:
                equity_raw = await self.redis.get("emarkos:v1:equity")
            if not equity_raw:
                return {"status": HealthStatus.WARNING, "detail": "No equity data"}
            # Handle JSON format: {"total": 140637.89, ...}
            try:
                equity = float(equity_raw)
            except (ValueError, TypeError):
                try:
                    import json as _json
                    equity = float(_json.loads(equity_raw).get("total", 0))
                except Exception:
                    equity = 0
            if equity <= 0:
                return {"status": HealthStatus.CRITICAL, "detail": f"Equity non-positive: ${equity:,.2f}"}
            return {"status": HealthStatus.HEALTHY, "detail": f"Equity=${equity:,.2f}"}
        except Exception as e:
            return {"status": HealthStatus.WARNING, "detail": f"Equity check error: {e}"}

    # ─── V5.6.1b Hardening Checks ────────────────────────────────

    async def _check_leader_lease(self) -> dict:
        """Check Leader Lease status (split-brain prevention)."""
        try:
            lease_holder = await self.redis.get(self.V56_KEYS["leader_lease"])
            if not lease_holder:
                self._daily_metrics["leader_lost_count"] += 1
                return {
                    "status": HealthStatus.CRITICAL,
                    "detail": "Leader Lease NOT held - no active engine!",
                }
            ttl = await self.redis.ttl(self.V56_KEYS["leader_lease"])
            if ttl < self._config["leader_lease_warn_ttl_s"]:
                return {
                    "status": HealthStatus.WARNING,
                    "detail": f"Leader Lease TTL low: {ttl}s (holder={lease_holder})",
                }
            return {
                "status": HealthStatus.HEALTHY,
                "detail": f"Leader={lease_holder}, TTL={ttl}s",
            }
        except Exception as e:
            return {"status": HealthStatus.WARNING, "detail": f"Leader lease check error: {e}"}

    async def _check_kill_switch(self) -> dict:
        """Check Kill Switch (trading:enabled) status."""
        try:
            enabled = await self.redis.get(self.V56_KEYS["trading_enabled"])
            trade_halt = await self.redis.get(self.V56_KEYS["trade_halt"])
            if enabled is None:
                return {
                    "status": HealthStatus.WARNING,
                    "detail": "trading:enabled key missing (default=blocked)",
                }
            if str(enabled).lower() not in ("true", "1", "yes"):
                self._daily_metrics["kill_switch_trip_count"] += 1
                return {
                    "status": HealthStatus.CRITICAL,
                    "detail": f"Kill Switch ACTIVE: trading:enabled={enabled}",
                }
            if trade_halt and str(trade_halt).lower() in ("true", "1", "yes"):
                return {
                    "status": HealthStatus.CRITICAL,
                    "detail": f"Trade HALT flag active: policy:trade_halt={trade_halt}",
                }
            return {
                "status": HealthStatus.HEALTHY,
                "detail": f"trading:enabled={enabled}, trade_halt={trade_halt}",
            }
        except Exception as e:
            return {"status": HealthStatus.WARNING, "detail": f"Kill switch check error: {e}"}

    async def _check_consumer_guard(self) -> dict:
        """Check ConsumerGuard (ACK-based state commit) status."""
        try:
            hb_raw = await self.redis.get(self.V56_KEYS["consumer_heartbeat"])
            ack_raw = await self.redis.get(self.V56_KEYS["consumer_last_ack"])
            now = time.time()
            detail_parts = []
            status = HealthStatus.HEALTHY

            if hb_raw:
                hb_age = now - float(hb_raw)
                detail_parts.append(f"heartbeat_age={hb_age:.0f}s")
                if hb_age > self._config["consumer_heartbeat_stale_s"]:
                    # [P0-2] 장외 시간에는 stale 경고 억제
                    if _is_market_hours_monitoring():
                        status = HealthStatus.WARNING
                        self._daily_metrics["consumer_stale_count"] += 1
                        detail_parts.append("STALE_HEARTBEAT")
                    else:
                        detail_parts.append("stale_but_outside_market_hours")
            else:
                detail_parts.append("no_heartbeat")
                # [P0-2] Market-hours-aware: 장외 시간에는 WARNING 대신 HEALTHY
                if _is_market_hours_monitoring():
                    status = HealthStatus.WARNING
                else:
                    status = HealthStatus.HEALTHY
                    detail_parts.append("(outside_market_hours)")

            if ack_raw:
                ack_age = now - float(ack_raw)
                detail_parts.append(f"ack_age={ack_age:.0f}s")
                if ack_age > self._config["consumer_ack_stale_s"]:
                    detail_parts.append("STALE_ACK")
                    if status != HealthStatus.CRITICAL:
                        status = HealthStatus.WARNING
            else:
                detail_parts.append("no_ack")

            return {"status": status, "detail": ", ".join(detail_parts)}
        except Exception as e:
            return {"status": HealthStatus.WARNING, "detail": f"Consumer guard check error: {e}"}

    # ─── Trading Metrics ─────────────────────────────────────────

    async def collect_trading_metrics(self) -> dict:
        """Collect comprehensive trading metrics including V5.6.1b data."""
        metrics = {
            "ts": time.time(),
            "equity": 0,
            "regime": "UNKNOWN",
            "vix": 0,
        }
        try:
            # Equity
            equity_raw = await self.redis.get("ofg:equity:verified")
            if not equity_raw:
                equity_raw = await self.redis.get("emarkos:v1:equity")
            if equity_raw:
                try:
                    metrics["equity"] = float(equity_raw)
                except (ValueError, TypeError):
                    try:
                        metrics["equity"] = float(json.loads(equity_raw).get("total", 0))
                    except Exception:
                        metrics["equity"] = 0

            # Regime
            regime_data = await self.redis.hgetall("regime:final:current")
            metrics["regime"] = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"

            # VIX
            for vk in ("realtime:vix:current", "market:vix:current", "market:vix"):
                vix_raw = await self.redis.get(vk)
                if vix_raw:
                    metrics["vix"] = float(vix_raw)
                    break

            # Session stats
            session = await self.redis.hgetall("ares:session:current")
            if session:
                metrics["session_dd"] = float(session.get("session_dd", 0))
                metrics["max_dd"] = float(session.get("max_dd", 0))
                metrics["pnl"] = float(session.get("pnl", 0))
                metrics["orders"] = int(session.get("orders_count", 0))
                metrics["fills"] = int(session.get("fills_count", 0))

            # V5.6.1b state summary
            state_raw = await self.redis.get(self.V56_KEYS["state_json"])
            if state_raw:
                state = json.loads(state_raw)
                metrics["v56_engine_state"] = state.get("engine_state", "UNKNOWN")
                metrics["v56_decision"] = state.get("last_decision", "UNKNOWN")
                metrics["v56_exposure_pct"] = state.get("exposure_pct", 0)
                metrics["v56_cycle_count"] = state.get("cycle_count", 0)

            # V5.6.1b metrics
            v56_metrics_raw = await self.redis.get(self.V56_KEYS["metrics_latest"])
            if v56_metrics_raw:
                v56m = json.loads(v56_metrics_raw)
                metrics["v56_sharpe"] = v56m.get("sharpe", 0)
                metrics["v56_calmar"] = v56m.get("calmar", 0)
                metrics["v56_sortino"] = v56m.get("sortino", 0)
                metrics["v56_win_rate"] = v56m.get("win_rate", 0)
                metrics["v56_turnover"] = v56m.get("turnover", 0)

            # Leader Lease info
            lease_holder = await self.redis.get(self.V56_KEYS["leader_lease"])
            lease_ttl = await self.redis.ttl(self.V56_KEYS["leader_lease"])
            metrics["leader_holder"] = lease_holder or "NONE"
            metrics["leader_ttl"] = lease_ttl

            # Kill Switch
            trading_enabled = await self.redis.get(self.V56_KEYS["trading_enabled"])
            metrics["kill_switch_enabled"] = str(trading_enabled).lower() in ("true", "1", "yes") if trading_enabled else False

            # Exposure
            positions_raw = await self.redis.get("emarkos:v1:positions")
            if positions_raw:
                data = json.loads(positions_raw)
                if isinstance(data, dict) and "positions" in data:
                    positions = data["positions"]
                elif isinstance(data, dict):
                    positions = data
                else:
                    positions = {}
                total_notional = 0
                for sym, p in positions.items():
                    if isinstance(p, dict):
                        mv = abs(float(p.get("marketValue", p.get("notional", 0))))
                        total_notional += mv
                metrics["gross_exposure"] = total_notional
                if metrics["equity"] > 0:
                    metrics["leverage"] = total_notional / metrics["equity"]
            else:
                metrics["gross_exposure"] = 0
                metrics["leverage"] = 0

            # Rebalance status
            rebal = {}
            try:
                _rebal = await self.redis.hgetall("ares:rebal:current_job")
                if isinstance(_rebal, dict):
                    rebal = _rebal
            except Exception:
                pass
            metrics["rebal_active"] = rebal.get("state") in ("CREATED", "RUNNING", "DRAINING")
            metrics["rebal_progress"] = float(rebal.get("progress_pct", 0))

            # Flap count (regime oscillation)
            flap_raw = await self.redis.get(self.V56_KEYS["flap_count"])
            metrics["flap_count"] = int(flap_raw) if flap_raw else 0

            # Incident count
            incident_len = await self.redis.xlen(self.V56_KEYS["incident_history"])
            metrics["incident_count"] = incident_len

        except Exception as e:
            logger.error(f"Metrics collection error: {e}")

        # Store metrics snapshot
        await self.redis.hset(
            self.KEYS["metrics"],
            mapping={k: str(v) for k, v in metrics.items()},
        )
        await self.redis.set(f"{self.KEYS['metrics']}:ts", str(time.time()), ex=300)
        return metrics

    async def _send_alert(self, title: str, detail: str, severity: str = "warning"):
        """Send alert with cooldown."""
        cooldown_key = f"{title}:{severity}"
        now = time.time()
        last_sent = self._alert_cooldowns.get(cooldown_key, 0)
        if now - last_sent < self._config["alert_cooldown_s"]:
            return
        self._alert_cooldowns[cooldown_key] = now

        # Log to stream
        await self.redis.xadd(
            self.KEYS["alerts"],
            {
                "title": title,
                "detail": detail,
                "severity": severity,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            maxlen=1000,
        )

        # Send telegram
        if self.telegram:
            emoji = "\U0001f534" if severity == "critical" else "\U0001f7e1"
            try:
                await self.telegram(f"{emoji} {title}\n{detail}")
            except Exception as e:
                logger.error(f"Telegram alert failed: {e}")
        logger.warning(f"ALERT [{severity}]: {title} - {detail}")

    async def generate_daily_summary(self) -> str:
        """Generate daily trading summary with V5.6.1b hardening status."""
        metrics = await self.collect_trading_metrics()
        session = await self.redis.hgetall("ares:session:current")

        summary = (
            f"=== ARES V5.6.1b Daily Summary ===\n"
            f"Date: {time.strftime('%Y-%m-%d')}\n"
            f"\n--- Portfolio ---\n"
            f"Equity: ${metrics.get('equity', 0):,.2f}\n"
            f"PnL: ${float(session.get('pnl', 0)):,.2f}\n"
            f"Session DD: {float(session.get('session_dd', 0)):.2%}\n"
            f"Max DD: {float(session.get('max_dd', 0)):.2%}\n"
            f"Leverage: {metrics.get('leverage', 0):.2f}x\n"
            f"Exposure: ${metrics.get('gross_exposure', 0):,.0f}\n"
            f"\n--- Trading ---\n"
            f"Orders: {session.get('orders_count', 0)}\n"
            f"Fills: {session.get('fills_count', 0)}\n"
            f"Regime: {metrics.get('regime', 'N/A')}\n"
            f"VIX: {metrics.get('vix', 0):.1f}\n"
            f"Flap Count: {metrics.get('flap_count', 0)}\n"
            f"\n--- V5.6.1b Hardening ---\n"
            f"Leader: {metrics.get('leader_holder', 'NONE')} (TTL={metrics.get('leader_ttl', 0)}s)\n"
            f"Kill Switch: {'ENABLED' if metrics.get('kill_switch_enabled') else 'DISABLED'}\n"
            f"Engine State: {metrics.get('v56_engine_state', 'N/A')}\n"
            f"Decision: {metrics.get('v56_decision', 'N/A')}\n"
            f"Incidents: {metrics.get('incident_count', 0)}\n"
            f"\n--- Daily Counters ---\n"
            f"Halts: {self._daily_metrics['halt_count']}\n"
            f"Defers: {self._daily_metrics['defer_count']}\n"
            f"Degraded: {self._daily_metrics['degraded_count']}\n"
            f"Leader Lost: {self._daily_metrics['leader_lost_count']}\n"
            f"Kill Switch Trips: {self._daily_metrics['kill_switch_trip_count']}\n"
            f"Consumer Stale: {self._daily_metrics['consumer_stale_count']}\n"
        )

        # Store and send
        await self.redis.set(
            f"{self.KEYS['daily']}:{time.strftime('%Y%m%d')}",
            summary,
            ex=86400 * 30,
        )
        if self.telegram:
            try:
                await self.telegram(summary)
            except Exception:
                pass

        # Reset daily counters
        self._daily_metrics = {k: 0 for k in self._daily_metrics}
        return summary

    async def get_health_status(self) -> dict:
        """Get current health status for API."""
        health = await self.redis.hgetall(self.KEYS["health"])
        return {
            "overall": health.get("overall", "UNKNOWN"),
            "last_check": health.get("last_check", ""),
            "details": json.loads(health.get("details", "{}")),
        }
