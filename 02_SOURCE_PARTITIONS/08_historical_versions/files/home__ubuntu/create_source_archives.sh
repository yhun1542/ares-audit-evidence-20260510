#!/bin/bash
# Create partitioned source code archives
# Rules: max 10 files per archive, each file < 30MB, exclude DB/logs/node_modules/venv/.git/.bak

set -e
OUTDIR="/home/ubuntu/source_export"
rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

echo "=== Creating partitioned source archives ==="

# ─────────────────────────────────────────────
# Part 01: aub-trading-system - Core execution (largest/most critical .mjs files)
# ─────────────────────────────────────────────
echo "Part 01: Core execution engines"
tar czf "$OUTDIR/01_aub_core_execution.tar.gz" -C /home/ubuntu/aub-trading-system \
  order-intent-executor.mjs \
  live-trading-kis.mjs \
  kis-balance-sync.mjs \
  realtime-data-feed.mjs \
  execution-reconciler.mjs \
  go-nogo-judge-v2.mjs \
  stability_judge.mjs \
  ares-trade-logger.mjs \
  fill-logger.mjs \
  champion-config-sync-service.mjs \
  2>/dev/null || echo "WARN: some files missing in part 01"

# ─────────────────────────────────────────────
# Part 02: aub-trading-system - Order flow & risk
# ─────────────────────────────────────────────
echo "Part 02: Order flow & risk"
tar czf "$OUTDIR/02_aub_orderflow_risk.tar.gz" -C /home/ubuntu/aub-trading-system \
  order_flow_governor.py \
  order_flow_governor_v3_1.py \
  realtime-order-execution.mjs \
  smart-retry-engine.mjs \
  realtime-price-service.mjs \
  self_healer.mjs \
  watchdog-monitor.mjs \
  gap_reporter_v42.mjs \
  oeg-executor.mjs \
  ofg_canonical_alerts.py \
  2>/dev/null || echo "WARN: some files missing in part 02"

# ─────────────────────────────────────────────
# Part 03: aub-trading-system - Balance sync versions & paper trading
# ─────────────────────────────────────────────
echo "Part 03: Balance sync versions & paper trading"
tar czf "$OUTDIR/03_aub_balance_paper.tar.gz" -C /home/ubuntu/aub-trading-system \
  kis-balance-sync-v3.1.mjs \
  kis-balance-sync-v2.mjs \
  kis-balance-sync-v4.0-updated.mjs \
  kis-balance-sync-v4.0-pre-update.mjs \
  paper-trading-daemon-v2.mjs \
  paper-trading-daemon.mjs \
  order-intent-executor-v6.mjs \
  position-sync-service.mjs \
  open-orders-sync.mjs \
  stale-order-steward.mjs \
  2>/dev/null || echo "WARN: some files missing in part 03"

# ─────────────────────────────────────────────
# Part 04: aub-trading-system - Strategy & backtest Python
# ─────────────────────────────────────────────
echo "Part 04: Strategy & backtest Python"
tar czf "$OUTDIR/04_aub_strategy_backtest.tar.gz" -C /home/ubuntu/aub-trading-system \
  ares_v55_live_autopilot.py \
  ares_router_v12_3.py \
  backtest_walkforward_v12_3_1.py \
  backtest_walkforward_v12_3_1_patched.py \
  execution_sim_adapter.py \
  execution_sim_adapter_patched.py \
  phase3_walkforward_oos.py \
  xgb_riskflag_writer_v2.py \
  2>/dev/null || echo "WARN: some files missing in part 04"

# ─────────────────────────────────────────────
# Part 05: aub-trading-system - Remaining .mjs files (A-K)
# ─────────────────────────────────────────────
echo "Part 05: Remaining .mjs (A-K)"
cd /home/ubuntu/aub-trading-system
PART5_FILES=$(find . -maxdepth 1 -name '*.mjs' -printf '%f\n' | sort | awk '/^[a-kA-K]/' | grep -v -E 'order-intent-executor|live-trading-kis|kis-balance-sync|realtime-data-feed|execution-reconciler|go-nogo-judge|stability_judge|ares-trade-logger|fill-logger|champion-config|realtime-order|smart-retry|realtime-price|self_healer|watchdog|gap_reporter|oeg-executor|paper-trading|position-sync|open-orders|stale-order' | head -10)
if [ -n "$PART5_FILES" ]; then
  tar czf "$OUTDIR/05_aub_mjs_A_K.tar.gz" $PART5_FILES 2>/dev/null || echo "WARN: part 05"
fi

# ─────────────────────────────────────────────
# Part 06: aub-trading-system - Remaining .mjs files (L-Z)
# ─────────────────────────────────────────────
echo "Part 06: Remaining .mjs (L-Z)"
PART6_FILES=$(find . -maxdepth 1 -name '*.mjs' -printf '%f\n' | sort | awk '/^[l-zL-Z]/' | grep -v -E 'order-intent-executor|live-trading-kis|kis-balance-sync|realtime-data-feed|execution-reconciler|go-nogo-judge|stability_judge|ares-trade-logger|fill-logger|champion-config|realtime-order|smart-retry|realtime-price|self_healer|watchdog|gap_reporter|oeg-executor|paper-trading|position-sync|open-orders|stale-order' | head -10)
if [ -n "$PART6_FILES" ]; then
  tar czf "$OUTDIR/06_aub_mjs_L_Z.tar.gz" $PART6_FILES 2>/dev/null || echo "WARN: part 06"
fi

# ─────────────────────────────────────────────
# Part 07: aub-trading-system - More remaining .mjs (overflow)
# ─────────────────────────────────────────────
echo "Part 07: Remaining .mjs overflow"
ALL_USED="order-intent-executor|live-trading-kis|kis-balance-sync|realtime-data-feed|execution-reconciler|go-nogo-judge|stability_judge|ares-trade-logger|fill-logger|champion-config|realtime-order|smart-retry|realtime-price|self_healer|watchdog|gap_reporter|oeg-executor|paper-trading|position-sync|open-orders|stale-order|order_flow_governor|ofg_canonical|ares_v55|ares_router|backtest_walkforward|execution_sim|phase3_walkforward|xgb_riskflag"
PART7_FILES=$(find . -maxdepth 1 -name '*.mjs' -printf '%f\n' | sort | grep -v -E "$ALL_USED" | sed -n '21,30p')
if [ -n "$PART7_FILES" ]; then
  tar czf "$OUTDIR/07_aub_mjs_overflow1.tar.gz" $PART7_FILES 2>/dev/null || echo "WARN: part 07"
fi

# ─────────────────────────────────────────────
# Part 08: aub-trading-system - JSON/YAML/SH/MD config files
# ─────────────────────────────────────────────
echo "Part 08: Config files (json/yaml/sh/md/env)"
CONFIG_FILES=$(find . -maxdepth 1 -type f \( -name '*.json' -o -name '*.yaml' -o -name '*.yml' -o -name '*.sh' -o -name '*.md' -o -name '*.env' \) -printf '%f\n' | sort | head -10)
if [ -n "$CONFIG_FILES" ]; then
  tar czf "$OUTDIR/08_aub_config1.tar.gz" $CONFIG_FILES 2>/dev/null || echo "WARN: part 08"
fi

# ─────────────────────────────────────────────
# Part 09: aub-trading-system - More config files (overflow)
# ─────────────────────────────────────────────
echo "Part 09: Config files overflow"
CONFIG_FILES2=$(find . -maxdepth 1 -type f \( -name '*.json' -o -name '*.yaml' -o -name '*.yml' -o -name '*.sh' -o -name '*.md' -o -name '*.env' \) -printf '%f\n' | sort | sed -n '11,20p')
if [ -n "$CONFIG_FILES2" ]; then
  tar czf "$OUTDIR/09_aub_config2.tar.gz" $CONFIG_FILES2 2>/dev/null || echo "WARN: part 09"
fi

cd /home/ubuntu

# ─────────────────────────────────────────────
# Part 10: aub-trading-system/ops - Operations scripts
# ─────────────────────────────────────────────
echo "Part 10: ops scripts"
tar czf "$OUTDIR/10_aub_ops.tar.gz" -C /home/ubuntu/aub-trading-system/ops \
  $(find /home/ubuntu/aub-trading-system/ops/ -maxdepth 1 -type f \( -name '*.py' -o -name '*.mjs' -o -name '*.sh' -o -name '*.js' \) -printf '%f\n' | sort | head -10) \
  2>/dev/null || echo "WARN: part 10"

# ─────────────────────────────────────────────
# Part 11: aub-trading-system/ops - More ops + replay
# ─────────────────────────────────────────────
echo "Part 11: ops overflow + replay"
tar czf "$OUTDIR/11_aub_ops_replay.tar.gz" \
  -C /home/ubuntu/aub-trading-system \
  $(find /home/ubuntu/aub-trading-system/ops/ -type f \( -name '*.py' -o -name '*.mjs' -o -name '*.sh' \) -printf 'ops/%P\n' | sort | sed -n '11,20p') \
  2>/dev/null || echo "WARN: part 11"

# ─────────────────────────────────────────────
# Part 12: ares-nextgen/nextgen2 - Core orchestrator + broker
# ─────────────────────────────────────────────
echo "Part 12: nextgen2 core"
tar czf "$OUTDIR/12_nextgen2_core.tar.gz" \
  -C /home/ubuntu/ares-nextgen \
  nextgen2/orchestrator.py \
  nextgen2/broker/kis_adapter.py \
  nextgen2/intent/order_builder.py \
  nextgen2/policy/session_state_manager.py \
  nextgen2/tier1/preflight_check.py \
  nextgen2/tier1/execution_stability.py \
  nextgen2/tier1/monitoring_alert.py \
  nextgen2/risk_engine/price_collar_guard.py \
  nextgen2/strategy/ares_v400_integrated_patch.py \
  2>/dev/null || echo "WARN: part 12"

# ─────────────────────────────────────────────
# Part 13: ares-nextgen/nextgen2 - Infra layer
# ─────────────────────────────────────────────
echo "Part 13: nextgen2 infra"
tar czf "$OUTDIR/13_nextgen2_infra.tar.gz" \
  -C /home/ubuntu/ares-nextgen \
  $(find /home/ubuntu/ares-nextgen/nextgen2/infra/ -type f -name '*.py' -printf 'nextgen2/infra/%f\n' | sort | head -10) \
  2>/dev/null || echo "WARN: part 13"

# ─────────────────────────────────────────────
# Part 14: ares-nextgen - Root level Python + ops
# ─────────────────────────────────────────────
echo "Part 14: ares-nextgen root + ops"
tar czf "$OUTDIR/14_nextgen_root_ops.tar.gz" \
  -C /home/ubuntu/ares-nextgen \
  $(find /home/ubuntu/ares-nextgen/ -maxdepth 1 -type f -name '*.py' -printf '%f\n' | sort | head -5) \
  $(find /home/ubuntu/ares-nextgen/ops/ -type f \( -name '*.py' -o -name '*.sh' \) -printf 'ops/%P\n' | sort | head -5) \
  2>/dev/null || echo "WARN: part 14"

# ─────────────────────────────────────────────
# Part 15: ares-nextgen - Remaining dirs (config, strategy, risk, policy, etc.)
# ─────────────────────────────────────────────
echo "Part 15: nextgen remaining"
tar czf "$OUTDIR/15_nextgen_remaining.tar.gz" \
  -C /home/ubuntu/ares-nextgen \
  $(find /home/ubuntu/ares-nextgen/ -type f \( -name '*.py' -o -name '*.json' -o -name '*.yaml' -o -name '*.yml' -o -name '*.sh' -o -name '*.md' -o -name '*.toml' \) \
    ! -path '*/nextgen2/orchestrator.py' \
    ! -path '*/nextgen2/broker/*' \
    ! -path '*/nextgen2/intent/*' \
    ! -path '*/nextgen2/policy/*' \
    ! -path '*/nextgen2/tier1/*' \
    ! -path '*/nextgen2/risk_engine/*' \
    ! -path '*/nextgen2/strategy/*' \
    ! -path '*/nextgen2/infra/*' \
    ! -path '*/.git/*' \
    ! -path '*/.pytest_cache/*' \
    ! -path '*/logs/*' \
    -printf '%P\n' | sort | head -10) \
  2>/dev/null || echo "WARN: part 15"

# ─────────────────────────────────────────────
# Part 16: ares-nextgen - More remaining
# ─────────────────────────────────────────────
echo "Part 16: nextgen remaining overflow"
tar czf "$OUTDIR/16_nextgen_remaining2.tar.gz" \
  -C /home/ubuntu/ares-nextgen \
  $(find /home/ubuntu/ares-nextgen/ -type f \( -name '*.py' -o -name '*.json' -o -name '*.yaml' -o -name '*.yml' -o -name '*.sh' -o -name '*.md' -o -name '*.toml' \) \
    ! -path '*/nextgen2/orchestrator.py' \
    ! -path '*/nextgen2/broker/*' \
    ! -path '*/nextgen2/intent/*' \
    ! -path '*/nextgen2/policy/*' \
    ! -path '*/nextgen2/tier1/*' \
    ! -path '*/nextgen2/risk_engine/*' \
    ! -path '*/nextgen2/strategy/*' \
    ! -path '*/nextgen2/infra/*' \
    ! -path '*/.git/*' \
    ! -path '*/.pytest_cache/*' \
    ! -path '*/logs/*' \
    -printf '%P\n' | sort | sed -n '11,20p') \
  2>/dev/null || echo "WARN: part 16"

# ─────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────
echo ""
echo "=== Archive Summary ==="
ls -lhS "$OUTDIR"/*.tar.gz 2>/dev/null
echo ""
echo "=== File counts per archive ==="
for f in "$OUTDIR"/*.tar.gz; do
  echo "$(basename $f): $(tar tzf $f 2>/dev/null | wc -l) files, $(du -h $f | cut -f1)"
done
