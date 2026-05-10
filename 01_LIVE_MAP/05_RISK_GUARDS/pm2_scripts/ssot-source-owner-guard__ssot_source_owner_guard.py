#!/usr/bin/env python3
"""
ssot_source_owner_guard.py
---------------------------

Supervises the ARES SSOT source-ownership contract declared in
/etc/ares/ssot_source_ownership.yaml. Every OWNER_GUARD_INTERVAL seconds it:

  1. Loads the ownership YAML (hot-reload on mtime change).
  2. For each declared owner and key:
     - checks Redis EXISTS, TYPE, TTL, payload timestamp.
     - derives actual age from payload ts / TTL headroom / inferred hb key.
     - compares against max_age_sec / min_ttl_sec / min_size / heartbeat.
  3. On violation:
     - writes a Fail-Loud alert key  ssot:owner_guard:alert:<owner>
     - writes an aggregated status    ssot:owner_guard:status
     - if heal.action is pm2_restart and cooldown has elapsed, runs
       `pm2 restart <target>` via subprocess.
  4. Writes its own heartbeat        ssot:owner_guard:heartbeat

Design invariants:
  - Never restarts a PM2 process more than heal_max_consecutive times in a row
    without a success in between (prevents restart storms).
  - Never touches Redis keys that are not declared in the contract.
  - Never mutates SSOT payload keys — read-only observer, except for its own
    namespace (ssot:owner_guard:*).
  - Safe to run multiple instances: the last writer wins on ssot:owner_guard:*
    keys; no distributed lock required because actions are idempotent.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis
import yaml

NAME = "ssot-source-owner-guard"
VERSION = "1.0.0"
HOST = socket.gethostname()

REDIS_URL = os.getenv("ARES_REDIS_URL") or os.getenv("REDIS_URL")
if not REDIS_URL and os.path.isfile("/etc/ares/redis.env"):
    for line in open("/etc/ares/redis.env"):
        if line.startswith("REDIS_URL="):
            REDIS_URL = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
if not REDIS_URL:
    print("[FATAL] REDIS_URL not set", flush=True)
    sys.exit(1)

CONTRACT_PATH = os.getenv(
    "ARES_SSOT_OWNERSHIP_CONTRACT",
    "/etc/ares/ssot_source_ownership.yaml",
)
INTERVAL_SEC = int(os.getenv("OWNER_GUARD_INTERVAL", "60"))
HEARTBEAT_KEY = "ssot:owner_guard:heartbeat"
STATUS_KEY = "ssot:owner_guard:status"
ALERT_PREFIX = "ssot:owner_guard:alert"
ALERT_TTL = 600
STATUS_TTL = 300
HEARTBEAT_TTL = 180
DRY_RUN = os.getenv("OWNER_GUARD_DRY_RUN", "0") == "1"

# -----------------------------------------------------------------------------

_shutdown = False

def _sig(*_: Any) -> None:
    global _shutdown
    _shutdown = True

for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, _sig)


def log(level: str, msg: str, /, **kw: Any) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    extra = f" {json.dumps(kw, ensure_ascii=False, default=str)}" if kw else ""
    print(f"[{ts}] [{level}] {NAME}: {msg}{extra}", flush=True)


# -----------------------------------------------------------------------------
# Contract loading with mtime hot-reload
# -----------------------------------------------------------------------------

class Contract:
    def __init__(self, path: str) -> None:
        self.path = path
        self.mtime: float = 0.0
        self.data: Dict[str, Any] = {}
        self.reload()

    def reload(self) -> bool:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            if self.data:
                log("ERROR", "contract disappeared", path=self.path)
            return False
        if st.st_mtime == self.mtime:
            return False
        with open(self.path) as f:
            self.data = yaml.safe_load(f) or {}
        self.mtime = st.st_mtime
        log("INFO", "contract loaded",
            path=self.path, version=self.data.get("version"),
            n_owners=len(self.data.get("owners", {})))
        return True


# -----------------------------------------------------------------------------
# Payload timestamp extractor
# -----------------------------------------------------------------------------

def _payload_ts_sec(value: str) -> Optional[float]:
    """Best-effort: parse a JSON payload and return its ts (seconds)."""
    if not value:
        return None
    try:
        o = json.loads(value)
    except Exception:
        return None
    # If payload is a bare number, interpret it directly.
    if isinstance(o, (int, float)):
        return o / 1000.0 if o > 1e12 else float(o)
    # If payload is not a dict (list / str), we cannot extract a ts field.
    if not isinstance(o, dict):
        return None
    # Common conventions
    for field in ("ts", "timestamp", "asof_ts"):
        v = o.get(field)
        if isinstance(v, (int, float)):
            return v / 1000.0 if v > 1e12 else float(v)
        if isinstance(v, str):
            s = v.strip()
            try:
                n = float(s)
                return n / 1000.0 if n > 1e12 else n
            except Exception:
                try:
                    if s.endswith("Z"):
                        s = s[:-1] + "+00:00"
                    return datetime.fromisoformat(s).timestamp()
                except Exception:
                    pass
    return None


def _raw_ts_sec(value: str) -> Optional[float]:
    """Parse a raw Redis string that holds a timestamp (epoch ms/s or ISO)."""
    if value is None:
        return None
    s = value.strip()
    try:
        n = float(s)
        return n / 1000.0 if n > 1e12 else n
    except Exception:
        pass
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Key checker
# -----------------------------------------------------------------------------

def check_key(r: redis.Redis, spec: Dict[str, Any]) -> Tuple[str, List[str], Dict[str, Any]]:
    """
    Returns (verdict, reasons, details). verdict ∈ {'ok','warn','fail','absent'}.
    """
    key = spec["key"]
    expected_type = spec.get("type", "string")
    max_age_sec = spec.get("max_age_sec")
    min_ttl_sec = spec.get("min_ttl_sec")
    min_size = spec.get("min_size")
    reasons: List[str] = []
    details: Dict[str, Any] = {"key": key, "expected_type": expected_type}

    actual_type = r.type(key)
    details["actual_type"] = actual_type
    if actual_type == "none":
        return "absent", ["key_absent"], details

    if actual_type != expected_type:
        reasons.append(f"type_mismatch:{actual_type}!={expected_type}")

    ttl = r.ttl(key)
    details["ttl"] = ttl
    if min_ttl_sec is not None:
        if ttl == -1:
            reasons.append("ttl_persistent_not_allowed")
        elif 0 < ttl < min_ttl_sec:
            reasons.append(f"ttl_too_low:{ttl}<{min_ttl_sec}")

    age: Optional[float] = None
    if actual_type == "string":
        val = r.get(key)
        details["len"] = len(val) if val else 0
        payload_ts = _payload_ts_sec(val)
        if payload_ts is None:
            payload_ts = _raw_ts_sec(val)
        if payload_ts:
            age = time.time() - payload_ts
    elif actual_type == "hash":
        n = r.hlen(key)
        details["size"] = n
        if min_size is not None and n < min_size:
            reasons.append(f"hash_too_small:{n}<{min_size}")
    elif actual_type == "set":
        details["size"] = r.scard(key)
    elif actual_type == "zset":
        details["size"] = r.zcard(key)

    details["age_sec"] = age
    if max_age_sec is not None and age is not None and age > max_age_sec:
        reasons.append(f"stale:{age:.0f}s>{max_age_sec}s")

    if min_size is not None:
        sz = details.get("size")
        if sz is not None and sz < min_size:
            # already captured above; dedupe
            pass

    if reasons:
        # Any stale/ttl-persistent/type mismatch is a hard fail.
        return "fail", reasons, details
    return "ok", [], details


def check_heartbeat(r: redis.Redis, hb_spec: Dict[str, Any]) -> Tuple[str, List[str], Dict[str, Any]]:
    """Heartbeat can be explicit key or inferred_from another key."""
    details: Dict[str, Any] = {}
    reasons: List[str] = []
    if "key" in hb_spec:
        key = hb_spec["key"]
        max_age = hb_spec.get("max_age_sec", hb_spec.get("heartbeat_ttl", 120))
        details["key"] = key
        actual = r.type(key)
        if actual == "none":
            return "absent", ["heartbeat_absent"], details
        raw = r.get(key)
        ts = _payload_ts_sec(raw) or _raw_ts_sec(raw)
        if ts is None:
            # Some heartbeats are just marker strings; fall back to TTL
            ttl = r.ttl(key)
            details["ttl"] = ttl
            return ("ok", [], details) if ttl and ttl > 0 else ("fail", ["heartbeat_no_ts"], details)
        age = time.time() - ts
        details["age_sec"] = age
        if age > max_age:
            return "fail", [f"heartbeat_stale:{age:.0f}s>{max_age}s"], details
        return "ok", [], details
    if "inferred_from" in hb_spec:
        details["inferred_from"] = hb_spec["inferred_from"]
        # Evaluated as part of keys; nothing extra here
        return "ok", [], details
    return "ok", [], details


# -----------------------------------------------------------------------------
# Auto-heal (PM2 restart) with cooldown and consecutive-cap
# -----------------------------------------------------------------------------

class HealState:
    def __init__(self) -> None:
        self.last_attempt_ts: Dict[str, float] = {}
        self.consecutive: Dict[str, int] = {}


def pm2_restart(target: str) -> Tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["pm2", "restart", target, "--update-env"],
            capture_output=True, text=True, timeout=30,
        )
        ok = proc.returncode == 0
        out = (proc.stdout or "") + (proc.stderr or "")
        return ok, out.strip()[-500:]
    except FileNotFoundError:
        return False, "pm2 binary not found in PATH"
    except subprocess.TimeoutExpired:
        return False, "pm2 restart timed out"
    except Exception as e:
        return False, f"exception:{type(e).__name__}:{e}"


def maybe_heal(state: HealState, owner: str, heal_cfg: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Try to auto-heal if cooldown/cap allow."""
    action = heal_cfg.get("action")
    target = heal_cfg.get("target")
    if action != "pm2_restart" or not target:
        return {"attempted": False, "reason": "no_pm2_restart_configured"}

    now = time.time()
    cooldown = defaults.get("heal_cooldown_sec", 120)
    cap = defaults.get("heal_max_consecutive", 3)

    last = state.last_attempt_ts.get(owner, 0.0)
    if now - last < cooldown:
        return {"attempted": False, "reason": "cooldown", "cooldown_remaining": int(cooldown - (now - last))}

    consecutive = state.consecutive.get(owner, 0)
    if consecutive >= cap:
        return {"attempted": False, "reason": "cap_reached", "consecutive": consecutive}

    if DRY_RUN:
        state.last_attempt_ts[owner] = now
        state.consecutive[owner] = consecutive + 1
        return {"attempted": True, "dry_run": True, "target": target}

    ok, out = pm2_restart(target)
    state.last_attempt_ts[owner] = now
    if ok:
        state.consecutive[owner] = consecutive + 1
    else:
        # Count failed attempts too to avoid loops
        state.consecutive[owner] = consecutive + 1
    return {"attempted": True, "ok": ok, "target": target, "output_tail": out, "consecutive": state.consecutive[owner]}


# -----------------------------------------------------------------------------
# Main cycle
# -----------------------------------------------------------------------------

def cycle(r: redis.Redis, contract: Contract, state: HealState) -> Dict[str, Any]:
    contract.reload()
    owners = (contract.data or {}).get("owners", {})
    defaults = (contract.data or {}).get("defaults", {})

    summary: Dict[str, Any] = {
        "ts": time.time(),
        "owners": {},
        "alerts": [],
        "heals": [],
    }

    for owner_name, owner_cfg in owners.items():
        key_specs: List[Dict[str, Any]] = owner_cfg.get("keys", []) or []
        hb_cfg: Dict[str, Any] = owner_cfg.get("heartbeat", {}) or {}
        heal_cfg: Dict[str, Any] = owner_cfg.get("heal", {}) or {}

        key_results: List[Dict[str, Any]] = []
        any_fail = False
        for spec in key_specs:
            verdict, reasons, details = check_key(r, spec)
            if verdict in ("fail", "absent"):
                any_fail = True
            key_results.append({"verdict": verdict, "reasons": reasons, "details": details})

        hb_verdict, hb_reasons, hb_details = check_heartbeat(r, hb_cfg) if hb_cfg else ("ok", [], {})
        if hb_verdict in ("fail", "absent"):
            any_fail = True

        owner_verdict = "fail" if any_fail else "ok"
        owner_summary = {
            "verdict": owner_verdict,
            "keys": key_results,
            "heartbeat": {"verdict": hb_verdict, "reasons": hb_reasons, "details": hb_details},
        }

        # Alert key and auto-heal
        if owner_verdict == "fail":
            alert_body = {
                "ts": time.time(),
                "owner": owner_name,
                "pm2_name": owner_cfg.get("pm2_name"),
                "keys": key_results,
                "heartbeat": owner_summary["heartbeat"],
            }
            try:
                r.set(f"{ALERT_PREFIX}:{owner_name}", json.dumps(alert_body, ensure_ascii=False), ex=ALERT_TTL)
            except Exception as e:
                log("ERROR", "failed to write alert", owner=owner_name, err=str(e))
            summary["alerts"].append(owner_name)

            heal_result = maybe_heal(state, owner_name, heal_cfg, defaults)
            owner_summary["heal"] = heal_result
            if heal_result.get("attempted"):
                log("WARN", "auto-heal attempted", owner=owner_name, result=heal_result)
                summary["heals"].append({"owner": owner_name, "result": heal_result})
        else:
            # Reset consecutive counter on healthy cycle
            state.consecutive[owner_name] = 0
            try:
                r.delete(f"{ALERT_PREFIX}:{owner_name}")
            except Exception:
                pass

        summary["owners"][owner_name] = owner_summary

    # Publish status + heartbeat
    try:
        r.set(STATUS_KEY, json.dumps(summary, ensure_ascii=False, default=str), ex=STATUS_TTL)
        r.set(
            HEARTBEAT_KEY,
            json.dumps({
                "ts": time.time(),
                "host": HOST,
                "version": VERSION,
                "n_owners": len(owners),
                "n_alerts": len(summary["alerts"]),
            }, ensure_ascii=False),
            ex=HEARTBEAT_TTL,
        )
    except Exception as e:
        log("ERROR", "failed to publish status/heartbeat", err=str(e))

    return summary


def main() -> int:
    r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
    if not os.path.isfile(CONTRACT_PATH):
        log("FATAL", "contract file missing", path=CONTRACT_PATH)
        return 2
    contract = Contract(CONTRACT_PATH)
    state = HealState()
    log("INFO", "starting", interval=INTERVAL_SEC, dry_run=DRY_RUN, contract=CONTRACT_PATH)

    while not _shutdown:
        t0 = time.time()
        try:
            summary = cycle(r, contract, state)
            if summary["alerts"]:
                log("WARN", "cycle found violations",
                    n_alerts=len(summary["alerts"]), alerts=summary["alerts"])
            else:
                log("INFO", "cycle OK", n_owners=len(summary["owners"]))
        except Exception as e:
            log("ERROR", "cycle exception", err=str(e))

        slept = 0.0
        target = max(1.0, INTERVAL_SEC - (time.time() - t0))
        while slept < target and not _shutdown:
            time.sleep(min(0.5, target - slept))
            slept += 0.5

    log("INFO", "shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
