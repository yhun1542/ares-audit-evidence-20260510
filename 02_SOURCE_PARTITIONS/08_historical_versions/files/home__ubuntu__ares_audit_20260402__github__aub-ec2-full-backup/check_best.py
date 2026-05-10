#!/usr/bin/env python3
import json
from pathlib import Path

results = []
artifacts = Path("artifacts")

for exp_dir in artifacts.iterdir():
    if not exp_dir.is_dir():
        continue
    for f in exp_dir.glob("exp_*.json"):
        try:
            d = json.loads(f.read_text())
            s = d.get("summary", {})
            oos_mean = s.get("oos_sharpe_mean") or s.get("OOS_sharpe_mean") or s.get("sharpe_mean")
            oos_max = s.get("oos_sharpe_max") or s.get("OOS_sharpe_max") or s.get("sharpe_max")
            pos = s.get("positive_folds") or s.get("pos_folds")
            if oos_mean and float(oos_mean) > 0:
                results.append({
                    "name": f.stem,
                    "dir": exp_dir.name,
                    "oos_mean": float(oos_mean),
                    "oos_max": float(oos_max) if oos_max else 0,
                    "pos_folds": pos
                })
        except:
            pass

results.sort(key=lambda x: x["oos_mean"], reverse=True)

print("=== TOP 15 (OOS Sharpe Mean) ===")
print("Rank | OOS_Mean | OOS_Max | Pos | Experiment | Directory")
print("-" * 90)
for i, r in enumerate(results[:15], 1):
    print(f"{i:4} | {r['oos_mean']:8.4f} | {r['oos_max']:7.4f} | {str(r['pos_folds']):3} | {r['name'][:30]:30} | {r['dir']}")

print()
if results:
    best = results[0]
    print("=== BEST RECORD ===")
    print(f"OOS Sharpe Mean: {best['oos_mean']:.4f}")
    print(f"OOS Sharpe Max:  {best['oos_max']:.4f}")
    print(f"Positive Folds:  {best['pos_folds']}")
    print(f"Experiment:      {best['name']}")
    print(f"Directory:       {best['dir']}")
