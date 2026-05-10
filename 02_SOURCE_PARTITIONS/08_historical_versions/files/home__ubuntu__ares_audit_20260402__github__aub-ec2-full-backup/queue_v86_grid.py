#!/usr/bin/env python3

V8.6 Switching Grid Runner (Python)
Reads manifest.csv and spawns your runner command for each config.

Usage:
  python3 queue_v86_grid.py     --manifest /path/to/manifest.csv     --results_dir /path/to/results_dir     --runner "python3 run_backtest.py --mode_selector_config {config} --output {out}"     --max_workers 4

- runner template MUST contain "{config}" and "{out}" placeholders.
- Results are written to: results_dir/<config_id>.result.json

import argparse, os, subprocess, pandas as pd
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

def run_one(cmd: str) -> dict:
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return {"rc": p.returncode, "log": (p.stdout or "")[-4000:]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--results_dir", required=True)
    ap.add_argument("--runner", required=True, help="Template with {config} and {out}")
    ap.add_argument("--max_workers", type=int, default=4)
    args = ap.parse_args()

    df = pd.read_csv(args.manifest)
    out_dir = Path(args.results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    for _, r in df.iterrows():
        cfg = str(r["path"])
        cid = str(r["config_id"])
        out = str(out_dir / f"{cid}.result.json")
        cmd = args.runner.format(config=cfg, out=out)
        jobs.append((cid, cfg, out, cmd))

    results = []
    with ProcessPoolExecutor(max_workers=args.max_workers) as ex:
        futs = {ex.submit(run_one, j[3]): j for j in jobs}
        for fut in as_completed(futs):
            cid, cfg, out, cmd = futs[fut]
            r = fut.result()
            results.append({"config_id": cid, "config_path": cfg, "out_path": out, "rc": r["rc"]})
            # write per-job log
            (out_dir / f"{cid}.log.tail.txt").write_text(r["log"], encoding="utf-8")

    res_df = pd.DataFrame(results).sort_values(["rc","config_id"])
    res_df.to_csv(out_dir / "queue_status.csv", index=False)
    print(res_df["rc"].value_counts().to_string())
    print(f"queue_status.csv written to: {out_dir/'queue_status.csv'}")

if __name__ == "__main__":
    main()
