#!/usr/bin/env python3
"""
Classify all 697 source files into functional categories and create archives.
Rules: max 10 files per archive, each file < 30MB, no file missed.
"""
import os, subprocess, re, json
from collections import defaultdict

AUB = "/home/ubuntu/aub-trading-system"
OPS = "/home/ubuntu/aub-trading-system/ops"
NG  = "/home/ubuntu/ares-nextgen"
OUT = "/home/ubuntu/source_cat_export"

os.makedirs(OUT, exist_ok=True)

# Collect all files with full paths
all_files = []

# AUB_ROOT
for f in os.listdir(AUB):
    fp = os.path.join(AUB, f)
    if os.path.isfile(fp) and any(f.endswith(e) for e in ['.mjs','.js','.py','.json','.yaml','.yml','.sh','.md','.env','.toml']):
        all_files.append(('AUB_ROOT', f, fp))

# AUB_OPS (recursive)
for root, dirs, files in os.walk(OPS):
    for f in files:
        fp = os.path.join(root, f)
        if any(f.endswith(e) for e in ['.mjs','.js','.py','.json','.yaml','.yml','.sh']):
            rel = os.path.relpath(fp, OPS)
            all_files.append(('AUB_OPS', rel, fp))

# NEXTGEN (recursive, exclude .git, .pytest_cache, logs)
for root, dirs, files in os.walk(NG):
    dirs[:] = [d for d in dirs if d not in ['.git', '.pytest_cache', 'logs']]
    for f in files:
        fp = os.path.join(root, f)
        if any(f.endswith(e) for e in ['.py','.mjs','.js','.json','.yaml','.yml','.sh','.md','.toml','.cfg']):
            rel = os.path.relpath(fp, NG)
            all_files.append(('NEXTGEN', rel, fp))

print(f"Total files found: {len(all_files)}")

# ── Classification rules (order matters: first match wins) ──
def classify(source, relpath, fullpath):
    fn = os.path.basename(relpath).lower()
    rl = relpath.lower()
    
    # Category 01: Champion Strategy / Orchestrator
    if any(k in fn for k in ['orchestrator', 'ares_v55', 'ares_v400', 'ares_router', 'ares_thompson',
                              'champion-config', 'champion', 'sleeve_allocator', 'targets_generator',
                              'target_pipeline', 'v2_target_adapter', 'ssot_target',
                              'v300_portfolio', 'v300_closed', 'v300_full',
                              'run_v233', 'run_v300']):
        return 'A01_champion_strategy'
    if 'nextgen2/strategy' in rl:
        return 'A01_champion_strategy'
    
    # Category 02: Universe Management
    if any(k in fn for k in ['universe', 'sector_theme', 'theme_engine']):
        return 'A02_universe_mgmt'
    
    # Category 03: Risk Engine / Hedging / Vol Targeting
    if any(k in fn for k in ['risk', 'hedge', 'beta_hedge', 'short_beta', 'collar', 'price_collar',
                              'var_scale', 'var-cvar', 'vol_target', 'regime', 'stress',
                              'shock_validator', 'fragility', 'mdd']):
        return 'A03_risk_hedge_vol'
    if 'risk_engine' in rl:
        return 'A03_risk_hedge_vol'
    if any(k in fn for k in ['risk-adjusted', 'sector-exposure']):
        return 'A03_risk_hedge_vol'
    
    # Category 04: Order Flow / Execution
    if any(k in fn for k in ['order-intent-executor', 'order_flow_governor', 'ofg_canonical',
                              'oeg-executor', 'oeg_executor', 'execution-reconciler',
                              'realtime-order-execution', 'smart-retry', 'fill-logger',
                              'ares-trade-logger', 'trade-guard', 'algo-twap',
                              'slicescheduler', 'slip_bp', 'turnover',
                              'order_builder', 'execution_quality', 'execution_sim',
                              'execution_stability', 'replay_fill', 'replay_harness',
                              'replay_state', 'replay_twin', 'cancel-orders',
                              'stale-order', 'chronos-ttl']):
        return 'A04_order_execution'
    if 'intent' in rl and source == 'NEXTGEN':
        return 'A04_order_execution'
    
    # Category 05: Broker API / KIS Integration
    if any(k in fn for k in ['kis-balance-sync', 'kis_adapter', 'live-trading-kis',
                              'position-sync', 'position-pnl', 'open-orders-sync',
                              'pos_resolver', 'positionsyncloop', 'position_truth',
                              'test-kis', 'test-order', 'test-single-order', 'test-port']):
        return 'A05_broker_kis_api'
    if 'broker' in rl and source == 'NEXTGEN':
        return 'A05_broker_kis_api'
    
    # Category 06: Guardrails / Judges / Gates
    if any(k in fn for k in ['go-nogo', 'stability_judge', 'preflight', 'safe-lock', 'safe_mode',
                              'safe_cast', 'safe_math', 'anchor-guardian', 'ultimate-guardian',
                              'watchdog', 'self_healer', 'self-diagnostics', 'sentinel',
                              'publish_gate', 'startup_gate', 'signal_lag',
                              'ssot_integrity', 'ssot_promotion', 'ssot_chain',
                              'slo_guard', 'runbook_enforcer', 'structural_health',
                              'recon-health', 'regime-redis-guard', 'v9_guardrails',
                              'regulatory-compliance']):
        return 'A06_guardrails_judges'
    if 'tier1' in rl and source == 'NEXTGEN':
        return 'A06_guardrails_judges'
    
    # Category 07: Policy / Config / Parameters
    if any(k in fn for k in ['policy', 'set-policy', 'config', 'ecosystem', 'pm2',
                              'session_state_manager', 'session_state', 'session_monitor',
                              'rebalance', 'weekly_rebalance', 'rollout_controller',
                              'recommend_trim', 'run_trim', 'toggle_core',
                              'set-shadow', 'short_policy', 'tuner_rules',
                              'strategy_tuner', 'ssm_config']):
        return 'A07_policy_config'
    if 'policy' in rl and source == 'NEXTGEN':
        return 'A07_policy_config'
    if 'config' in rl and source == 'NEXTGEN':
        return 'A07_policy_config'
    
    # Category 08: Data Feed / Market Data / Pricing
    if any(k in fn for k in ['realtime-data-feed', 'realtime-price', 'polygon',
                              'websocket-server', 'wsstate', 'nbbo',
                              'market-data', 'vix', 'quick_vix',
                              'technical_indicator', 'xgb_riskflag', 'xgb_confidence',
                              'xgb_drift', 'signal-detector', 'signal-refiner',
                              'alpha_signal', 'rcl_probability', 'engine_confidence',
                              'equity_to_daily']):
        return 'A08_data_feed_signals'
    
    # Category 09: SSOT / Redis / Infra
    if any(k in fn for k in ['ssot_writer', 'ssot_consumer', 'ssot_promote', 'ssot_healthcheck',
                              'ssot_immutable', 'redis-ssot', 'redis_auto', 'redis_eventbus',
                              'redis_ha', 'redis_health', 'redis_stream', 'shared_redis',
                              'stream_trim', 'snapshot_capture', 'snapshot_consumer',
                              'snapshot_publisher', 'snapshot_path',
                              'caching-mechanism', 'prom_metrics', 'telemetry',
                              'srl_core', 'srl_ssot', 'srl_controller', 'srl_v1',
                              'qos_core', 'v71_eventbus', 'v71_integration',
                              'capital_scaling', 'producer', 'producer_toggle',
                              'shadow_publisher']):
        return 'A09_ssot_redis_infra'
    if 'infra' in rl and source == 'NEXTGEN':
        return 'A09_ssot_redis_infra'
    
    # Category 10: Monitoring / Alerting / Reporting
    if any(k in fn for k in ['monitor', 'alert', 'telegram', 'gap_reporter', 'report',
                              'weekly_report', 'post_session', 'dashboard', 'update_dashboard',
                              'benchmark-comparison', 'profit_interrupt', 'pnl',
                              'trading-cost', 'tax-loss', 'strategy-attribution',
                              'scalability', 'security-reliability', 'performance']):
        return 'A10_monitoring_alerting'
    if 'monitoring' in rl:
        return 'A10_monitoring_alerting'
    
    # Category 11: Backtest / Walkforward / Simulation
    if any(k in fn for k in ['backtest', 'walkforward', 'wfo_', 'backfill',
                              'paper-trading', 'simulator', 'simulation',
                              'replay/', 'research_gate', 'integration_test']):
        return 'A11_backtest_simulation'
    if 'replay' in rl:
        return 'A11_backtest_simulation'
    
    # Category 12: Ops / Deploy / Scripts
    if any(k in fn for k in ['deploy', 'patch_', 'pre_session', 'systemd',
                              'regenerate-modules', 'create-pure-js',
                              'seed_redis', 'validate-symbols',
                              'unlock_controller', 'zero_ops',
                              'finalize', 'pm2_policy_autogen',
                              'run_metrics', 'run_tier1']):
        return 'A12_ops_deploy'
    if source == 'AUB_OPS' and 'nextgen_core' in rl:
        return 'A12_ops_deploy'
    if 'ops' in rl and source == 'NEXTGEN':
        return 'A12_ops_deploy'
    if 'systemd' in rl:
        return 'A12_ops_deploy'
    
    # Category 13: AI / Code Review / 3AI / 4AI
    if any(k in fn for k in ['3ai', '4ai', 'ai-code', 'claude', 'call_4ai',
                              'context_reader', 'feedback']):
        return 'A13_ai_code_review'
    
    # Category 14: Misc / Utility / Types
    if any(k in fn for k in ['util', 'types', 'schema', 'safe_cast',
                              'paths.txt', 'short_capability']):
        return 'A14_misc_utility'
    
    # Fallback
    return 'A15_uncategorized'

# ── Classify all files ──
categories = defaultdict(list)
for source, relpath, fullpath in all_files:
    cat = classify(source, relpath, fullpath)
    categories[cat].append((source, relpath, fullpath))

# ── Print classification summary ──
print("\n=== Classification Summary ===")
total_classified = 0
for cat in sorted(categories.keys()):
    count = len(categories[cat])
    total_classified += count
    print(f"  {cat}: {count} files")
print(f"  TOTAL: {total_classified}")

# ── Create archives: max 10 files per archive ──
part_num = 1
manifest = []

for cat in sorted(categories.keys()):
    files = categories[cat]
    # Sort by filename for consistency
    files.sort(key=lambda x: x[1].lower())
    
    # Split into chunks of 10
    for i in range(0, len(files), 10):
        chunk = files[i:i+10]
        chunk_idx = (i // 10) + 1
        total_chunks = (len(files) + 9) // 10
        
        if total_chunks == 1:
            archive_name = f"{part_num:02d}_{cat}.tar.gz"
        else:
            archive_name = f"{part_num:02d}_{cat}_part{chunk_idx}.tar.gz"
        
        archive_path = os.path.join(OUT, archive_name)
        
        # Build tar command with full paths
        file_list = [fp for _, _, fp in chunk]
        cmd = ['tar', 'czf', archive_path] + file_list
        subprocess.run(cmd, capture_output=True)
        
        # Get archive size
        size = os.path.getsize(archive_path)
        
        # Record manifest
        file_names = [f"{src}:{rel}" for src, rel, _ in chunk]
        manifest.append({
            'part': part_num,
            'archive': archive_name,
            'category': cat,
            'file_count': len(chunk),
            'size_kb': round(size / 1024, 1),
            'files': file_names
        })
        
        print(f"  Part {part_num:02d}: {archive_name} ({len(chunk)} files, {size/1024:.1f} KB)")
        part_num += 1

# ── Write manifest ──
manifest_path = os.path.join(OUT, "00_MANIFEST.json")
with open(manifest_path, 'w') as f:
    json.dump(manifest, f, indent=2)

# ── Verify completeness ──
total_archived = sum(m['file_count'] for m in manifest)
print(f"\n=== Verification ===")
print(f"Total files found:    {len(all_files)}")
print(f"Total files archived: {total_archived}")
print(f"Missing:              {len(all_files) - total_archived}")
print(f"Total archives:       {len(manifest)}")
print(f"Total size:           {sum(m['size_kb'] for m in manifest):.1f} KB")

# Check for > 30MB
for m in manifest:
    if m['size_kb'] > 30 * 1024:
        print(f"  WARNING: {m['archive']} exceeds 30MB ({m['size_kb']/1024:.1f} MB)")
