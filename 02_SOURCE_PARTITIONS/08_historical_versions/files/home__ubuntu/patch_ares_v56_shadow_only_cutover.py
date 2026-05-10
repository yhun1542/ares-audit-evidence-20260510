#!/usr/bin/env python3
"""
ARES-v56 shadow-only cutover patch
──────────────────────────────────
목적:
- AOA full-live cutover 시 ares-v56 direct emit을 env flag로 억제
- caller가 no-ack/halt로 오인하지 않도록 ACKED + shadow_only 응답 반환
- desired-book mirror는 유지

env:
  AOA_DISABLE_ARES_DIRECT_EMIT=true  -> direct emit skip, shadow_only ack
  AOA_DISABLE_ARES_DIRECT_EMIT=false -> 기존 동작

[FIX] CANDIDATES 순서 변경 - 실제 PM2 exec_path를 최우선으로
"""
from __future__ import annotations

import ast
import datetime as dt
import os
import shutil
import sys
from pathlib import Path

# [FIX] 실제 PM2 exec_path를 최우선 후보로 추가
CANDIDATES = [
    Path(os.environ.get("ARES_AUTOPILOT_FILE", "/home/ubuntu/ares_v56_live/current/ares_v55_live_autopilot.py")),
    Path("/home/ubuntu/ares_v56_live/releases/20260327_072504/ares_v55_live_autopilot.py"),
    Path("/home/ubuntu/aub-trading-system/ares_v55_live_autopilot.py"),
    Path("/home/ubuntu/ares_releases/2026-02-17/ares_v55_live_autopilot.py"),
]
BACKUP_DIR = Path(os.environ.get(
    "ARES_AOA_CUTOVER_BACKUP_DIR",
    f"/home/ubuntu/backup_ares_aoa_cutover_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S')}"
))
ANCHOR = '        exec_id = intent["exec_id"]\n        idem_key = f"{LiveKeys.EXECUTION_IDEMP}:{exec_id}"\n'
PATCHED_MARK = 'AOA_ARES_DIRECT_EMIT_DISABLED'
REPLACEMENT = '''        exec_id = intent["exec_id"]
        idem_key = f"{LiveKeys.EXECUTION_IDEMP}:{exec_id}"

        if str(os.getenv("AOA_DISABLE_ARES_DIRECT_EMIT", "false")).lower() in {"1", "true", "yes", "on"}:
            self.store.set_json("ares:aoa:ares_v56_shadow_submit", {
                "ts": time.time(),
                "exec_id": exec_id,
                "intent_id": str(intent.get("intent_id", exec_id)),
                "status": "shadow_only",
                "reason": "AOA_ARES_DIRECT_EMIT_DISABLED",
            }, ex=3600)
            return {
                "status": "acked",
                "exec_id": exec_id,
                "shadow_only": True,
                "reason": "AOA_ARES_DIRECT_EMIT_DISABLED",
            }
'''


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def patch_one(target: Path) -> None:
    text = target.read_text(encoding="utf-8")
    if PATCHED_MARK in text:
        print(f"SKIP: already patched: {target}")
        return
    if ANCHOR not in text:
        fail(f"submit anchor not found in {target}")
    backup = BACKUP_DIR / target.name
    shutil.copy2(target, backup)
    print(f"OK: backup: {backup}")
    text = text.replace(ANCHOR, REPLACEMENT, 1)
    target.write_text(text, encoding="utf-8")
    try:
        ast.parse(target.read_text(encoding="utf-8"))
    except SyntaxError as e:
        shutil.copy2(backup, target)
        fail(f"syntax error after patch in {target}; rolled back: {e}")
    patched = target.read_text(encoding="utf-8")
    checks = {
        "env_flag": 'AOA_DISABLE_ARES_DIRECT_EMIT' in patched,
        "telemetry": 'ares:aoa:ares_v56_shadow_submit' in patched,
        "shadow_only": '"shadow_only": True' in patched,
        "acked": '"status": "acked"' in patched,
    }
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        shutil.copy2(backup, target)
        fail(f"keyword verification failed in {target}: {bad}")
    print(f"OK: patched: {target}")
    print("OK: syntax ok")


def main() -> None:
    existing = []
    seen = set()
    for p in CANDIDATES:
        if p.exists() and str(p) not in seen:
            existing.append(p)
            seen.add(str(p))
    if not existing:
        fail("no candidate autopilot file found")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for t in existing:
        patch_one(t)


if __name__ == "__main__":
    main()
