#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAM26 Engine CLI Adapter v1

Purpose:
- Provide backward-compatible stdout JSON for tooling that expected:
  --config / --strategy-id / --stdout-json
- Calls the actual recovered RAM26 engine using its real argparse:
  --once --features-key --output-key --output-json --ssot-staged-key
- Optional Redis/staged publishing is controlled explicitly.

Never writes ssot:target:v2:current.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List

DEFAULT_ENGINE = "/home/ubuntu/ares_current/engines/ram26/ram26_alpha_engine_v1.py"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine-file", default=os.getenv("RAM26_ENGINE_FILE", DEFAULT_ENGINE))
    ap.add_argument("--config", default="")
    ap.add_argument("--strategy-id", default="RAM26_ALPHA_0001_b215c119ea23")
    ap.add_argument("--features-key", default="ram26:features:v1")
    ap.add_argument("--output-key", default="ram26:alpha:targets:v1:latest")
    ap.add_argument("--ssot-staged-key", default="ram26:ssot:target:v2:staged")
    ap.add_argument("--target-gross", default=os.getenv("RAM26_TARGET_GROSS", "0.75"))
    ap.add_argument("--max-weight", default=os.getenv("RAM26_MAX_WEIGHT", "0.10"))
    ap.add_argument("--min-active", default=os.getenv("RAM26_MIN_ACTIVE", "8"))
    ap.add_argument("--feature-max-age-sec", default=os.getenv("RAM26_FEATURE_MAX_AGE_SEC", "300"))
    ap.add_argument("--publish-redis", action="store_true")
    ap.add_argument("--publish-ssot-staged", action="store_true")
    ap.add_argument("--strict-freshness", action="store_true")
    ap.add_argument("--stdout-json", action="store_true")
    ap.add_argument("--output-json", default="")
    args = ap.parse_args()

    engine = Path(args.engine_file)
    if not engine.exists():
        print(json.dumps({"go": False, "error": f"engine missing: {engine}"}))
        return 2

    out_json = Path(args.output_json) if args.output_json else Path(tempfile.mktemp(prefix="ram26_engine_out_", suffix=".json"))

    cmd: List[str] = [
        sys.executable, str(engine),
        "--once",
        "--features-key", args.features_key,
        "--output-key", args.output_key,
        "--ssot-staged-key", args.ssot_staged_key,
        "--output-json", str(out_json),
        "--target-gross", str(args.target_gross),
        "--max-weight", str(args.max_weight),
        "--min-active", str(args.min_active),
        "--feature-max-age-sec", str(args.feature_max_age_sec),
    ]

    if args.publish_redis:
        cmd.append("--publish-redis")
    if args.publish_ssot_staged:
        cmd.append("--publish-ssot-staged")
    if args.strict_freshness:
        cmd.append("--strict-freshness")

    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)

    data = None
    if out_json.exists():
        try:
            data = json.loads(out_json.read_text())
        except Exception:
            data = None

    if data is None:
        # Some engines may print JSON to stdout.
        try:
            data = json.loads(proc.stdout.strip())
        except Exception:
            data = {
                "go": False,
                "error": "engine_output_parse_failed",
                "engine_rc": proc.returncode,
                "engine_stdout_preview": proc.stdout[-1000:],
                "engine_stderr_preview": proc.stderr[-1000:],
            }

    # Normalize strategy id if absent.
    if isinstance(data, dict) and not data.get("strategy_id"):
        data["strategy_id"] = args.strategy_id
    if isinstance(data, dict):
        data.setdefault("engine_adapter", "ram26_engine_cli_adapter_v1")
        data.setdefault("engine_rc", proc.returncode)
        data.setdefault("engine_stderr_preview", proc.stderr[-500:])

    print(json.dumps(data, ensure_ascii=False))
    return proc.returncode

if __name__ == "__main__":
    raise SystemExit(main())
