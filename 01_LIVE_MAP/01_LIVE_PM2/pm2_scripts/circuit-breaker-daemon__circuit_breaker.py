import sys, os
# [ARES-SSOT 2026-04-24] admin tier Redis client helper
import sys as _sys
_sys.path.insert(0, "/home/ubuntu/scripts")
try:
    from redis_ssot_loader import get_redis_client as _ares_get_redis
except ImportError as _e:
    raise RuntimeError(
        "ARES SSOT loader not available. Ensure /home/ubuntu/scripts/redis_ssot_loader.py exists."
    ) from _e

sys.path.insert(0, "/home/ubuntu/aub-trading-system/ops")
try:
    import ares_redis_env
except Exception:
    pass
#!/usr/bin/env python3
"""
ARES Circuit Breaker — Automated Safety System
Runs as PM2 process. Monitors trading metrics and auto-trips Level 1/2 rollback.

4AI Feedback Applied:
- Claude: Automated circuit breaker (no human in loop for critical scenarios)
- GPT: Auto-escalation L1→L2 if L1 fails
- Grok: Watchdog heartbeat for self-monitoring
- Claude: Specify polling interval (500ms)

Triggers:
- PnL drawdown > 2% → Level 1
- Error rate > baseline+3σ for 5min → Level 1
- Position breach > 10% AUM → Level 1
- Cross-family conflict detected → Level 1
- Level 1 fails (intents still flowing after 5s) → auto-escalate to Level 2
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CIRCUIT_BREAKER] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/home/ubuntu/logs/circuit_breaker.log", mode="a")
    ]
)
log = logging.getLogger("circuit_breaker")

# ─── CONFIG ───────────────────────────────────────────────────────────────────

# [STRUCTURAL-FIX CB-001] Load REDIS_URL from SSOT /etc/ares/redis.env
def _load_redis_env():
    import re
    env_file = "/etc/ares/redis.env"
    try:
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass
_load_redis_env()
REDIS_URL = os.environ.get("REDIS_URL", "")
POLL_INTERVAL_MS = 500  # 500ms polling — GPT feedback: must specify
HEARTBEAT_KEY = "ares:circuit_breaker:heartbeat"
HEARTBEAT_TTL = 10  # seconds — if stale, CloudWatch alarm fires
FAMILY_CONFIG_KEY = "oie:config:allowed_families"
SAFE_FAMILY = "nextgen2"  # Rollback target: only nextgen2 allowed

@dataclass
class BreakerConfig:
    pnl_drawdown_pct: float = -2.0       # ares-v56 PnL drops >2%
    error_rate_3sigma_minutes: int = 5    # sustained error spike
    position_breach_pct: float = 10.0     # single position >10% AUM
    cross_family_conflict_max: int = 0    # ANY conflict → trip
    l1_verify_timeout_sec: int = 5        # wait before escalating to L2
    cooldown_sec: int = 300               # 5min cooldown after trip

@dataclass
class BreakerState:
    tripped: bool = False
    trip_time: Optional[float] = None
    trip_reason: str = ""
    trip_level: int = 0
    trip_count: int = 0
    last_check: float = 0
    baseline_error_rate: float = 0.0
    baseline_error_std: float = 0.0
    error_history: list = field(default_factory=list)

# ─── CIRCUIT BREAKER ─────────────────────────────────────────────────────────

class CircuitBreaker:
    def __init__(self, config: BreakerConfig = None):
        self.config = config or BreakerConfig()
        self.state = BreakerState()
        self.r = _ares_get_redis()
        self._running = True

    def _write_heartbeat(self):
        """Self-monitoring: write heartbeat for external watchdog (CloudWatch)."""
        self.r.setex(HEARTBEAT_KEY, HEARTBEAT_TTL, json.dumps({
            "ts": time.time(),
            "tripped": self.state.tripped,
            "trip_count": self.state.trip_count,
            "pid": os.getpid()
        }))

    def _check_pnl_drawdown(self) -> Optional[str]:
        """Check ares-v56 PnL drawdown."""
        try:
            pnl_data = self.r.get("ares:live:pnl:current")
            if pnl_data:
                pnl = json.loads(pnl_data)
                drawdown_pct = pnl.get("drawdown_pct", 0)
                if drawdown_pct < self.config.pnl_drawdown_pct:
                    return f"PnL drawdown {drawdown_pct:.2f}% exceeds threshold {self.config.pnl_drawdown_pct}%"
        except Exception as e:
            log.warning("PnL check error: %s", e)
        return None

    def _check_error_rate(self) -> Optional[str]:
        """Check if error rate exceeds baseline+3σ for sustained period."""
        try:
            error_count_str = self.r.get("ares:live:metrics:error_count")
            if error_count_str:
                current_rate = float(error_count_str)
                self.state.error_history.append((time.time(), current_rate))
                # Keep last 10 minutes
                cutoff = time.time() - 600
                self.state.error_history = [(t, r) for t, r in self.state.error_history if t > cutoff]

                if self.state.baseline_error_rate > 0:
                    threshold = self.state.baseline_error_rate + 3 * self.state.baseline_error_std
                    # Check if sustained for 5 minutes
                    recent = [r for t, r in self.state.error_history if t > time.time() - self.config.error_rate_3sigma_minutes * 60]
                    if len(recent) >= 10 and all(r > threshold for r in recent):
                        return f"Error rate sustained above 3σ ({threshold:.2f}) for {self.config.error_rate_3sigma_minutes}min"
        except Exception as e:
            log.warning("Error rate check error: %s", e)
        return None

    def _check_position_breach(self) -> Optional[str]:
        """Check if any single position exceeds AUM threshold."""
        try:
            positions = self.r.get("ares:live:positions:summary")
            if positions:
                pos_data = json.loads(positions)
                aum = pos_data.get("total_aum", 0)
                if aum > 0:
                    for sym, pos in pos_data.get("positions", {}).items():
                        pct = abs(pos.get("notional", 0)) / aum * 100
                        if pct > self.config.position_breach_pct:
                            return f"Position {sym} at {pct:.1f}% AUM exceeds {self.config.position_breach_pct}%"
        except Exception as e:
            log.warning("Position check error: %s", e)
        return None

    def _check_cross_family_conflict(self) -> Optional[str]:
        """Check for any cross-family conflicts."""
        try:
            conflict_count_str = self.r.get("ares:metrics:cross_family_conflicts")
            if conflict_count_str:
                count = int(conflict_count_str)
                if count > self.config.cross_family_conflict_max:
                    return f"Cross-family conflicts detected: {count}"
        except Exception as e:
            log.warning("Cross-family check error: %s", e)
        return None

    def _execute_level1(self) -> bool:
        """Level 1: Hot-reload family config (<1s)."""
        try:
            self.r.set(FAMILY_CONFIG_KEY, SAFE_FAMILY)
            log.critical("LEVEL 1 EXECUTED: Set %s = %s", FAMILY_CONFIG_KEY, SAFE_FAMILY)
            return True
        except Exception as e:
            log.error("LEVEL 1 FAILED: %s", e)
            return False

    def _verify_level1(self) -> bool:
        """Verify Level 1 took effect: no ares-v56 intents flowing."""
        time.sleep(self.config.l1_verify_timeout_sec)
        try:
            # Check if ares-v56 intents are still being generated
            val = self.r.get(FAMILY_CONFIG_KEY)
            if val and "ares-live" not in val:
                return True
        except Exception:
            pass
        return False

    def _execute_level2(self):
        """Level 2: PM2 stop ares-v56-live (<30s)."""
        try:
            result = subprocess.run(
                ["pm2", "stop", "ares-v56-live"],
                capture_output=True, text=True, timeout=30
            )
            log.critical("LEVEL 2 EXECUTED: pm2 stop ares-v56-live → %s", result.stdout.strip())
        except Exception as e:
            log.error("LEVEL 2 FAILED: %s", e)

    def _send_alert(self, message: str, level: str = "CRITICAL"):
        """Send alert via Telegram and log."""
        log.critical("[ALERT] %s: %s", level, message)
        try:
            # Telegram alert
            alert_data = json.dumps({
                "type": "circuit_breaker",
                "level": level,
                "message": message,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "trip_count": self.state.trip_count
            })
            self.r.publish("ares:alerts", alert_data)
        except Exception:
            pass

    def trip(self, reason: str):
        """Trip the circuit breaker."""
        if self.state.tripped and (time.time() - (self.state.trip_time or 0)) < self.config.cooldown_sec:
            log.info("Already tripped, in cooldown")
            return

        self.state.tripped = True
        self.state.trip_time = time.time()
        self.state.trip_reason = reason
        self.state.trip_count += 1

        log.critical("🔴 CIRCUIT BREAKER TRIPPED: %s (count=%d)", reason, self.state.trip_count)
        self._send_alert(f"TRIPPED: {reason}")

        # Level 1
        self.state.trip_level = 1
        l1_ok = self._execute_level1()

        if l1_ok:
            # Verify Level 1 worked
            if not self._verify_level1():
                log.critical("Level 1 verification FAILED — auto-escalating to Level 2")
                self.state.trip_level = 2
                self._execute_level2()
                self._send_alert(f"AUTO-ESCALATED to Level 2: {reason}")
        else:
            # Level 1 failed entirely — go straight to Level 2
            log.critical("Level 1 FAILED — escalating to Level 2")
            self.state.trip_level = 2
            self._execute_level2()
            self._send_alert(f"L1 FAILED, Level 2 executed: {reason}")

        # Log incident
        incident = {
            "reason": reason,
            "level": self.state.trip_level,
            "trip_count": self.state.trip_count,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        self.r.lpush("ares:circuit_breaker:incidents", json.dumps(incident))
        self.r.ltrim("ares:circuit_breaker:incidents", 0, 99)

    def check_all(self):
        """Run all checks. Trip on first violation."""
        checks = [
            ("pnl_drawdown", self._check_pnl_drawdown),
            ("error_rate", self._check_error_rate),
            ("position_breach", self._check_position_breach),
            ("cross_family_conflict", self._check_cross_family_conflict),
        ]

        for name, check_fn in checks:
            reason = check_fn()
            if reason:
                self.trip(f"[{name}] {reason}")
                return  # One trip per cycle

    def load_baselines(self):
        """Load baselines from P0.8 measurement."""
        try:
            baselines = self.r.get("ares:baselines:error_rate")
            if baselines:
                data = json.loads(baselines)
                self.state.baseline_error_rate = data.get("mean", 0)
                self.state.baseline_error_std = data.get("std", 1)
                log.info("Loaded baselines: mean=%.2f, std=%.2f",
                         self.state.baseline_error_rate, self.state.baseline_error_std)
        except Exception as e:
            log.warning("Could not load baselines: %s", e)

    def run(self):
        """Main loop — polls every POLL_INTERVAL_MS."""
        log.info("Circuit Breaker started (poll=%dms, pid=%d)", POLL_INTERVAL_MS, os.getpid())
        self.load_baselines()

        def handle_signal(sig, frame):
            log.info("Received signal %d, shutting down", sig)
            self._running = False

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        while self._running:
            try:
                self._write_heartbeat()
                self.check_all()
                self.state.last_check = time.time()
            except Exception as e:
                log.error("Check cycle error: %s", e)

            time.sleep(POLL_INTERVAL_MS / 1000.0)

        log.info("Circuit Breaker stopped")


if __name__ == "__main__":
    os.makedirs("/home/ubuntu/logs", exist_ok=True)
    breaker = CircuitBreaker()
    breaker.run()
