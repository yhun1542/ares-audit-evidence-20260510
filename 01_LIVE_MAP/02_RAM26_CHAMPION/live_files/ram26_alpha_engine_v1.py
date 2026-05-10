#!/usr/bin/env python3
# RAM26_ENGINE_LEGACY_SHIM_V1
# Accepts both legacy bridge args (--config/--strategy-id/--stdout-json)
# and native RAM26 args, then forwards to the canonical engine.
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CANONICAL_ENGINE_DEFAULT = "/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/all_gates_go/RAM26_ENGINE_LEGACY_SHIM_20260509T065551Z/backup/ram26_alpha_engine_v1.canonical.bak_20260509T065551Z.py"

def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default="")
    parser.add_argument("--strategy-id", default=os.getenv("CHAMPION_ID", "RAM26_ALPHA_0001_b215c119ea23"))
    parser.add_argument("--stdout-json", action="store_true")
    parser.add_argument("--engine-canonical", default=os.getenv("RAM26_CANONICAL_ENGINE", CANONICAL_ENGINE_DEFAULT))
    known, rest = parser.parse_known_args()

    canonical = Path(known.engine_canonical)
    if not canonical.exists():
        print(json.dumps({"go": False, "pass": False, "error": f"canonical engine missing: {canonical}"}))
        return 2

    args = list(rest)
    # Legacy caller path: it supplies --config/--strategy-id/--stdout-json but not native args.
    if "--once" not in args and "--loop" not in args and "--self-test" not in args:
        args.insert(0, "--once")

    def hasopt(opt: str) -> bool:
        return opt in args

    tmp_out = None
    if not hasopt("--features-key"):
        args += ["--features-key", os.getenv("RAM26_FEATURES_KEY", "ram26:features:v1")]
    if not hasopt("--output-key"):
        args += ["--output-key", os.getenv("RAM26_ALPHA_TARGETS_KEY", "ram26:alpha:targets:v1:latest")]
    if not hasopt("--ssot-staged-key"):
        args += ["--ssot-staged-key", os.getenv("RAM26_SSOT_STAGED_KEY", "ram26:ssot:target:v2:staged")]
    if not hasopt("--target-gross"):
        args += ["--target-gross", os.getenv("RAM26_TARGET_GROSS", "0.75")]
    if not hasopt("--max-weight"):
        args += ["--max-weight", os.getenv("RAM26_MAX_WEIGHT", "0.10")]
    if not hasopt("--min-active"):
        args += ["--min-active", os.getenv("RAM26_MIN_ACTIVE", "8")]
    if not hasopt("--output-json"):
        tmp_out = tempfile.mktemp(prefix="ram26_engine_shim_", suffix=".json")
        args += ["--output-json", tmp_out]
    else:
        try:
            tmp_out = args[args.index("--output-json") + 1]
        except Exception:
            tmp_out = None

    cmd = [sys.executable, str(canonical)] + args
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=int(os.getenv("RAM26_ENGINE_TIMEOUT_SEC", "90")))

    data = None
    if tmp_out and Path(tmp_out).exists():
        try:
            data = json.loads(Path(tmp_out).read_text())
        except Exception:
            data = None
    if data is None:
        try:
            data = json.loads(proc.stdout.strip())
        except Exception:
            data = {
                "go": False,
                "pass": False,
                "error": "engine_output_parse_failed",
                "engine_rc": proc.returncode,
                "stdout_tail": proc.stdout[-1200:],
                "stderr_tail": proc.stderr[-1200:],
            }

    if isinstance(data, dict):
        data.setdefault("strategy_id", known.strategy_id)
        data.setdefault("engine_version", known.strategy_id)
        data.setdefault("engine_shim", "RAM26_ENGINE_LEGACY_SHIM_V1")
        data.setdefault("engine_rc", proc.returncode)
        data.setdefault("engine_stderr_tail", proc.stderr[-800:])

    if known.stdout_json or True:
        print(json.dumps(data, ensure_ascii=False))
    return proc.returncode

if __name__ == "__main__":
    raise SystemExit(main())
