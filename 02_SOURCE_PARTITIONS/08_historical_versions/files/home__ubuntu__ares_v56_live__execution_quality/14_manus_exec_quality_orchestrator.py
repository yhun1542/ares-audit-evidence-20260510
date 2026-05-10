#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

ROOT_FILES = [
    "order-intent-executor.mjs",
    "live-trading-kis.mjs",
    "algo-maker-pegger.mjs",
    "polygon-ws-nbbo.mjs",
    "data-gateway.mjs",
]

def run(cmd: List[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    print("[RUN]", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=check)

def write_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def write_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def find_repo_root(start: Path) -> Path:
    candidates = [start] + list(start.parents)
    markers = [".git", "package.json", "ops", "ares_v56_live", "order-intent-executor.mjs"]
    best = start
    best_score = -1
    for base in candidates:
        score = sum((base / m).exists() for m in markers)
        if score > best_score:
            best_score = score
            best = base
    return best

def detect_files(root: Path) -> Dict[str, str]:
    found = {}
    for name in ROOT_FILES:
        hits = list(root.rglob(name))
        if hits:
            found[name] = str(hits[0].relative_to(root))
    return found

def grep_order_path(root: Path, files: Dict[str, str]) -> str:
    lines = []
    patterns = ["stream:peg_requests", "placeOrder", "submitOrder", "KIS", "polygon:quote", "stream:fills"]
    for name, rel in files.items():
        p = root / rel
        try:
            text = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(text, start=1):
            if any(x in line for x in patterns):
                lines.append(f"{rel}:{idx}: {line.strip()}")
    return "\n".join(lines[:200])

def render_root_cause(summary: Dict[str, Any]) -> str:
    return f"""# Root Cause Summary

## 관측
- mean_slippage_bps: {summary.get('mean_slippage_bps')}
- p50_slippage_bps: {summary.get('p50_slippage_bps')}
- p95_slippage_bps: {summary.get('p95_slippage_bps')}
- count: {summary.get('count')}

## 우선순위 가설
1. direct marketable execution 비중 과다
2. quote freshness 검증 미흡
3. spread gate 부재 또는 느슨함
4. maker-first routing 미적용
5. requote / escape 정책이 primary path가 아님

## 즉시 작업
- execution_router_policy.json 생성
- order-intent-executor -> router 위임 패치
- pegger shadow 모드 연결
- fill/quote telemetry schema 고정
"""

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--outdir", default="out/execution_quality")
    args = ap.parse_args()

    repo_root = find_repo_root(Path(args.repo_root).resolve())
    outdir = repo_root / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    files = detect_files(repo_root)
    inventory = {
        "repo_root": str(repo_root),
        "detected_files": files,
    }
    write_json(inventory, outdir / "repo_inventory.json")

    order_path = grep_order_path(repo_root, files)
    write_text("# Current Order Path\n\n```\n" + order_path + "\n```\n", outdir / "current_order_path.md")

    baseline_script = repo_root / "05_scripts" / "10_collect_execution_baseline.py"
    dataset_script = repo_root / "05_scripts" / "11_build_fill_quote_dataset.py"
    prereq_script = repo_root / "05_scripts" / "12_verify_execution_prereqs.sh"

    if baseline_script.exists():
        run(["python3", str(baseline_script), "--out", str(outdir / "baseline_snapshot.json")], cwd=repo_root, check=False)
    if dataset_script.exists():
        run([
            "python3", str(dataset_script),
            "--out-csv", str(outdir / "fill_quote_dataset.csv"),
            "--out-summary", str(outdir / "slippage_summary.json")
        ], cwd=repo_root, check=False)
    if prereq_script.exists():
        p = run(["bash", str(prereq_script), str(repo_root), str(outdir / "prereq_check.txt")], cwd=repo_root, check=False)
        write_text((p.stdout or "") + "\n" + (p.stderr or ""), outdir / "prereq_check.txt")

    summary_path = outdir / "slippage_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    write_text(render_root_cause(summary), outdir / "root_cause_summary.md")

    policy = {
        "router_mode": "shadow",
        "spread_max_bps": 15,
        "quote_max_age_ms": 1000,
        "maker_first": True,
        "pegger": {
            "max_requotes": 3,
            "requote_interval_ms": 15000,
            "escape_after_sec": 45,
            "sell_max_requotes": 2,
            "sell_escape_after_sec": 30
        },
        "canary": {
            "symbols": ["AAPL", "MSFT", "NVDA"],
            "notional_limit_usd": 500
        }
    }
    write_json(policy, outdir / "execution_router_policy.json")

    patch_plan = """# Patch Plan

1. order-intent-executor.mjs
   - direct broker path를 router publish path로 교체
   - stream:execution_router:intents or stream:peg_requests 사용
2. live-trading-kis.mjs
   - direct execution은 fallback only
   - telemetry 발행
3. algo-maker-pegger.mjs
   - policy-driven primary candidate
4. polygon-ws-nbbo.mjs
   - freshness timestamp 보장
5. docs/runbook
   - shadow -> canary -> live cutover 정리
"""
    write_text(patch_plan, outdir / "patch_plan.md")

    shadow_report = """# Shadow Report

이 파일은 마누스가 실제 shadow 실행 후 갱신해야 한다.
기본 체크:
- actual vs shadow slippage
- maker fill share
- requote count
- escape rate
- quote freshness
"""
    write_text(shadow_report, outdir / "shadow_report.md")
    write_json({"status": "PENDING_RUN"}, outdir / "shadow_comparison.json")

    canary_plan = """# Canary Plan

- Symbols: AAPL, MSFT, NVDA
- Notional cap: $500/order
- Router mode: canary
- rollback trigger:
  - DLQ 증가
  - mean slippage 미개선
  - quote freshness p95 > 1000ms
  - PM2 service issue
"""
    write_text(canary_plan, outdir / "canary_plan.md")

    final_reco = """# Final Recommendation

기본 권고:
1. immediate next step = shadow router
2. direct market path는 fallback only로 격하
3. pegger를 primary candidate로 승격
4. cutover는 EXEC_CUTOVER=YES 없이는 금지
"""
    write_text(final_reco, outdir / "final_recommendation.md")

    print(f"repo_root={repo_root}")
    print(f"inventory={outdir / 'repo_inventory.json'}")
    print(f"current_order_path={outdir / 'current_order_path.md'}")
    print(f"final_recommendation={outdir / 'final_recommendation.md'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
