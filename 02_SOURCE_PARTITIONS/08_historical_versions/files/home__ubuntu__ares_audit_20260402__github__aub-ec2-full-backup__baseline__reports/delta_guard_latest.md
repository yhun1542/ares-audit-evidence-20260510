=== AUB No-Delta Diagnoser ===
Champion OOS Mean: 2.6276
Found 9 experiments
Generated diagnosis report: /home/ubuntu/AUB/baseline/reports/no_delta_diagnosis.md
Diagnosis complete
=== AUB Rebalance Pinpoint ===
Base: --base-log
Candidate: baseline/reports/run_champion_v5_20251231/rebalance.csv
Error loading --base-log: [Errno 2] No such file or directory: '--base-log'
Error loading baseline/reports/run_champion_v5_20251231/rebalance.csv: Expecting value: line 1 column 1 (char 0)
No rebalance data found in either file
Note: Rebalance data may not be included in current JSON format
Generated pinpoint report: /home/ubuntu/AUB/baseline/reports/rebalance_pinpoint.md
[CMD] python3 experiment_system/tools/aub_no_delta_diagnoser.py --base baseline/champion_v5_latest.json --cand /home/ubuntu/AUB/artifacts/autopilot_20260102_084021/exp_fsa_only_scale_090_hedge_025.json
[CMD] python3 experiment_system/tools/aub_rebalance_pinpoint.py --base-log baseline/reports/run_champion_v5_20251231/rebalance.csv --cand-log baseline/reports/run_champion_v5_20251231/rebalance.jsonl
