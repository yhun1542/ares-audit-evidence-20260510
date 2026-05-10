#!/usr/bin/env python3
"""10_collect_execution_baseline.py — EC2-corrected version
Collects baseline snapshot from live EC2 Redis and PM2.
Fixes:
  - pm2 jlist → pm2 list --no-color (EPIPE fix)
  - premium:polygon:quote:{symbol} → premium:polygon:{symbol} (HASH type)
  - Redis auth via ARES_REDIS_URL env var
  - ares:exec:router:mode → policy:execution_router (single JSON blob)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

REDIS_URL = os.environ.get("ARES_REDIS_URL", "redis://localhost:6379/0")

def run(args: List[str]) -> str:
    p = subprocess.run(args, text=True, capture_output=True)
    if p.returncode != 0:
        return ""
    return p.stdout.strip()

def redis_raw(*parts: str) -> str:
    """Use redis-cli with auth from URL."""
    # Parse password from URL
    pw = ""
    if "@" in REDIS_URL:
        auth_part = REDIS_URL.split("@")[0]
        if ":/" in auth_part:
            pw = auth_part.split("://:")[1] if "://:@" not in auth_part else auth_part.split("://:")[1]
    cmd = ["redis-cli", "--raw"]
    if pw:
        cmd.extend(["-a", pw, "--no-auth-warning"])
    cmd.extend(parts)
    return run(cmd)

def parse_pm2_text() -> List[Dict[str, Any]]:
    """Parse PM2 status from text output instead of jlist (EPIPE fix)."""
    raw = run(["pm2", "list", "--no-color"])
    if not raw:
        return []
    results = []
    for line in raw.splitlines():
        if "│" not in line:
            continue
        parts = [p.strip() for p in line.split("│") if p.strip()]
        if len(parts) >= 8:
            try:
                int(parts[0])  # ID must be numeric
                results.append({
                    "name": parts[1],
                    "status": parts[7] if len(parts) > 7 else "unknown",
                    "pid": parts[5] if len(parts) > 5 else "",
                })
            except ValueError:
                continue
    return results

def scan_keys(pattern: str, limit: int = 200) -> List[str]:
    raw = redis_raw("SCAN", "0", "MATCH", pattern, "COUNT", str(limit))
    lines = raw.splitlines()
    if len(lines) <= 1:
        return []
    return [x for x in lines[1:] if x]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/execution_quality/baseline_snapshot.json")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    pm2_state = parse_pm2_text()

    # Corrected: premium:polygon:{symbol} (not premium:polygon:quote:{symbol})
    sample_quote_keys = scan_keys("premium:polygon:*", limit=100)
    # Filter out non-symbol keys
    sample_quote_keys = [k for k in sample_quote_keys if not k.endswith(":ws") and ":" not in k.split("premium:polygon:")[-1]]
    sample_fill_keys = scan_keys("slippage:daily:*", limit=20)

    # Corrected: policy:execution_router (single JSON blob, not ares:exec:router:mode)
    router_policy_raw = redis_raw("GET", "policy:execution_router")
    try:
        router_policy = json.loads(router_policy_raw) if router_policy_raw else {}
    except Exception:
        router_policy = {"raw": router_policy_raw}

    payload: Dict[str, Any] = {
        "ops": {
            "live_engine": redis_raw("GET", "ares:ops:live_engine"),
            "live_engine_path": redis_raw("GET", "ares:ops:live_engine_path"),
            "rollback_engine": redis_raw("GET", "ares:ops:rollback_engine"),
            "research_candidate": redis_raw("GET", "ares:ops:research_candidate"),
            "status": redis_raw("GET", "ares:ops:status"),
        },
        "runtime": {
            "trading_enabled": redis_raw("GET", "trading:enabled"),
            "trade_halt": redis_raw("GET", "trade:halt"),
            "policy_trade_halt": redis_raw("GET", "policy:trade:halt"),
            "budget_usd": redis_raw("GET", "emarkos:v1:budget_usd"),
            "equity_total": redis_raw("GET", "ares:equity:total"),
            "ofg_gate": redis_raw("GET", "ares:ofg:gate"),
            "exec_router_policy": router_policy,
        },
        "streams": {
            "fills_len": redis_raw("XLEN", "stream:fills"),
            "executions_len": redis_raw("XLEN", "stream:executions"),
            "peg_requests_len": redis_raw("XLEN", "stream:peg_requests"),
            "execution_quality_len": redis_raw("XLEN", "stream:execution_quality"),
            "order_intent_len": redis_raw("XLEN", "emarkos:v6:order:intent"),
            "execution_dlq_len": redis_raw("XLEN", "emarkos:v1:execution_dlq"),
            "order_intent_dlq_len": redis_raw("XLEN", "risk:dlq:order_intent"),
        },
        "samples": {
            "quote_keys": sample_quote_keys[:20],
            "slippage_keys": sample_fill_keys[:20],
        },
        "pm2": pm2_state,
    }

    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Baseline snapshot written to {out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
