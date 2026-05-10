#!/usr/bin/env python3
"""
Flapping Detector & Self-Healer v2 — L8++ Safety Layer
======================================================
Expanded per 4-AI consensus to cover:
  - ssot-pipeline-guard (existing)
  - kis-token-service (new)
  - realtime-data-feed (new)

Redis Keys:
  flap:status       → JSON { process_count, alerts, healed_total, processes }
  flap:heartbeat    → timestamp
  flap:log          → STREAM of flapping events and healing actions
"""

import asyncio
import json
import time
import os
import signal
import subprocess
from datetime import datetime, timezone

# [ARES-SSOT 2026-04-24] admin tier Redis client helper
import sys as _sys
_sys.path.insert(0, "/home/ubuntu/scripts")
try:
    from redis_ssot_loader import get_redis_client as _ares_get_redis
except ImportError as _e:
    raise RuntimeError(
        "ARES SSOT loader not available. Ensure /home/ubuntu/scripts/redis_ssot_loader.py exists."
    ) from _e

REDIS_URL = os.environ["REDIS_URL"]
CHECK_INTERVAL_SEC = 15
HEARTBEAT_INTERVAL_SEC = 10

# ─── 4-AI Consensus: Service-Specific Healing Profiles ────────
SERVICE_PROFILES = {
    "ssot-pipeline-guard": {
        "restart_threshold_warn": 5,
        "restart_threshold_crit": 20,
        "restart_window_sec": 300,
        "backoff_min_sec": 10,
        "backoff_max_sec": 120,
        "stale_keys": [
            "ssot:pipeline:lock",
            "ssot:pipeline:guard:lock",
        ],
        "stale_key_check_type": {
            "ssot:pipeline:lock": "string",
            "ssot:pipeline:guard:lock": "string",
        },
        "health_check_key": None,
        "description": "SSOT pipeline guard - stale lock cleanup",
    },
    "kis-token-service": {
        "restart_threshold_warn": 5,
        "restart_threshold_crit": 15,
        "restart_window_sec": 300,
        "backoff_min_sec": 30,
        "backoff_max_sec": 300,
        "stale_keys": [
            "kis:token:lock",
            "kis:token:refresh_in_progress",
        ],
        "stale_key_check_type": {
            "kis:token:lock": "string",
            "kis:token:refresh_in_progress": "string",
        },
        "health_check_key": "kis:token:health",
        "description": "KIS token service - token lock/refresh cleanup",
    },
    "realtime-data-feed": {
        "restart_threshold_warn": 8,
        "restart_threshold_crit": 20,
        "restart_window_sec": 600,
        "backoff_min_sec": 60,
        "backoff_max_sec": 600,
        "stale_keys": [
            "rtfeed:lock",
            "rtfeed:heartbeat",
        ],
        "stale_key_check_type": {
            "rtfeed:lock": "string",
            "rtfeed:heartbeat": "string",
        },
        "health_check_key": None,
        "description": "Realtime data feed - connection lock cleanup",
    },
}


class FlappingDetectorV2:
    def __init__(self):
        self.redis = None
        self.running = True
        self.alerts = 0
        self.healed_total = 0
        self.heal_backoff = {}  # service -> next_heal_ts
        self.heal_attempts = {}  # service -> count

    async def connect_redis(self):
        import redis.asyncio as aioredis
        from redis_ssot_loader import load_redis_config_from_ssot
        import ssl as _ssl
        cfg = load_redis_config_from_ssot()
        redis_kwargs = dict(
            host=cfg["host"], port=cfg["port"],
            username=cfg["user"], password=cfg["pw"],
            db=cfg["db"], decode_responses=True,
            socket_timeout=5.0, socket_connect_timeout=5.0,
        )
        if cfg.get("ssl"):
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            redis_kwargs["ssl"] = True
            redis_kwargs["ssl_cert_reqs"] = None
        for attempt in range(10):
            try:
                self.redis = aioredis.Redis(**redis_kwargs)
                await self.redis.ping()
                self.log("INFO", "Redis connected (async SSOT)")
                return
            except Exception as e:
                self.log("WARN", f"Redis connect attempt {attempt+1}: {e}")
                await asyncio.sleep(2 ** min(attempt, 4))
        raise RuntimeError("Failed to connect to Redis")

    def log(self, level, message, data=None):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "component": "flapping-detector",
            "message": message,
        }
        if data:
            entry.update(data)
        print(json.dumps(entry), flush=True)

    def get_pm2_processes(self):
        """Get PM2 process list via CLI."""
        try:
            result = subprocess.run(
                ["pm2", "jlist"], capture_output=True, text=True, timeout=10
            )
            return json.loads(result.stdout)
        except Exception as e:
            self.log("ERROR", f"PM2 list error: {e}")
            return []

    async def check_all_processes(self):
        """Check all PM2 processes and detect flapping."""
        procs = self.get_pm2_processes()
        if not procs:
            return

        process_status = {}
        for p in procs:
            name = p.get("name", "unknown")
            env = p.get("pm2_env", {})
            restarts = env.get("restart_time", 0)
            status = env.get("status", "unknown")
            pid = p.get("pid", 0)

            process_status[name] = {
                "restarts": restarts,
                "status": status,
                "pid": pid,
            }

            # Check if this is a monitored service
            if name in SERVICE_PROFILES:
                profile = SERVICE_PROFILES[name]
                await self._check_service_health(name, profile, restarts, status)

        # Write consolidated status
        flap_status = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "process_count": len(procs),
            "alerts": self.alerts,
            "healed_total": self.healed_total,
            "monitored_services": list(SERVICE_PROFILES.keys()),
            "processes": process_status,
        }
        try:
            await self.redis.set("flap:status", json.dumps(flap_status))
        except Exception as e:
            self.log("ERROR", f"Status write error: {e}")

    async def _check_service_health(self, name, profile, restarts, status):
        """Check a specific monitored service for flapping."""
        now = time.time()

        if status == "errored":
            self.alerts += 1
            self.log("WARN", f"{name} is in ERRORED state", {"restarts": restarts})
            await self._attempt_heal(name, profile, "errored_state")
            return

        if restarts >= profile["restart_threshold_crit"]:
            self.alerts += 1
            self.log("CRITICAL", f"{name} critical flapping: {restarts} restarts", {
                "threshold": profile["restart_threshold_crit"],
                "action": "STOP_AND_HEAL",
            })
            await self._attempt_heal(name, profile, "critical_flapping", stop_first=True)
            return

        if restarts >= profile["restart_threshold_warn"]:
            self.alerts += 1
            self.log("WARN", f"{name} flapping detected: {restarts} restarts", {
                "threshold": profile["restart_threshold_warn"],
                "action": "HEAL",
            })
            await self._attempt_heal(name, profile, "warning_flapping")

    async def _attempt_heal(self, name, profile, reason, stop_first=False):
        """Attempt to heal a flapping service with exponential backoff."""
        now = time.time()

        # Check backoff
        next_heal = self.heal_backoff.get(name, 0)
        if now < next_heal:
            remaining = int(next_heal - now)
            self.log("INFO", f"{name} heal skipped (backoff {remaining}s remaining)")
            return

        attempts = self.heal_attempts.get(name, 0)
        backoff = min(
            profile["backoff_min_sec"] * (2 ** attempts),
            profile["backoff_max_sec"]
        )
        self.heal_backoff[name] = now + backoff
        self.heal_attempts[name] = attempts + 1

        self.log("INFO", f"Healing {name} (attempt {attempts+1}, next backoff {backoff}s)", {
            "reason": reason, "profile": profile["description"],
        })

        # Step 1: Clear stale Redis keys
        keys_cleared = 0
        for key in profile["stale_keys"]:
            try:
                key_exists = await self.redis.exists(key)
                if key_exists:
                    ttl = await self.redis.ttl(key)
                    await self.redis.delete(key)
                    keys_cleared += 1
                    self.log("INFO", f"Cleared stale key: {key} (TTL was {ttl})")
            except Exception as e:
                self.log("WARN", f"Failed to clear key {key}: {e}")

        # Step 2: Check and clear health key if stale
        if profile.get("health_check_key"):
            try:
                health_raw = await self.redis.get(profile["health_check_key"])
                if health_raw:
                    health = json.loads(health_raw)
                    health_ts = health.get("ts") or health.get("timestamp")
                    if health_ts:
                        # If health data is older than 5 minutes, clear it
                        import dateutil.parser as dp
                        try:
                            age = now - dp.parse(health_ts).timestamp()
                            if age > 300:
                                await self.redis.delete(profile["health_check_key"])
                                self.log("INFO", f"Cleared stale health key: {profile['health_check_key']} (age {age:.0f}s)")
                                keys_cleared += 1
                        except Exception:
                            pass
            except Exception as e:
                self.log("WARN", f"Health key check error: {e}")

        # Step 3: Stop process if critical
        if stop_first:
            try:
                subprocess.run(["pm2", "stop", name], capture_output=True, timeout=10)
                self.log("INFO", f"Stopped {name} for healing")
                await asyncio.sleep(2)
            except Exception as e:
                self.log("ERROR", f"Failed to stop {name}: {e}")

        # Step 4: Reset restart counter and restart
        try:
            subprocess.run(["pm2", "reset", name], capture_output=True, timeout=10)
            subprocess.run(["pm2", "restart", name], capture_output=True, timeout=10)
            self.log("INFO", f"Restarted {name} (cleared {keys_cleared} stale keys)")
            self.healed_total += 1
        except Exception as e:
            self.log("ERROR", f"Failed to restart {name}: {e}")

        # Log healing event to Redis stream
        try:
            await self.redis.xadd("flap:log", {
                "ts": datetime.now(timezone.utc).isoformat(),
                "service": name,
                "reason": reason,
                "keys_cleared": str(keys_cleared),
                "attempt": str(attempts + 1),
                "backoff_sec": str(backoff),
            })
            await self.redis.xtrim("flap:log", maxlen=500)
        except Exception:
            pass

    async def write_heartbeat(self):
        try:
            await self.redis.set("flap:heartbeat", str(int(time.time())), ex=60)
        except Exception:
            pass

    async def run(self):
        self.log("INFO", "=" * 60)
        self.log("INFO", "Flapping Detector v2 starting — L8++ Safety Layer")
        self.log("INFO", f"Monitored services: {list(SERVICE_PROFILES.keys())}")
        for name, profile in SERVICE_PROFILES.items():
            self.log("INFO", f"  {name}: warn@{profile['restart_threshold_warn']}, "
                     f"crit@{profile['restart_threshold_crit']}, "
                     f"window={profile['restart_window_sec']}s")
        self.log("INFO", "4-AI Consensus: Grok-4 + GPT-5.2 Pro + Gemini 2.5 Flash")
        self.log("INFO", "=" * 60)

        await self.connect_redis()
        await self.write_heartbeat()

        last_heartbeat = 0

        while self.running:
            try:
                now = time.time()

                await self.check_all_processes()

                if now - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
                    await self.write_heartbeat()
                    last_heartbeat = now

                await asyncio.sleep(CHECK_INTERVAL_SEC)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log("ERROR", f"Main loop error: {e}")
                await asyncio.sleep(10)

        self.log("INFO", "Flapping Detector v2 shutting down")
        if self.redis:
            await self.redis.close()


async def main():
    fd = FlappingDetectorV2()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: setattr(fd, 'running', False))
    await fd.run()


if __name__ == "__main__":
    asyncio.run(main())
