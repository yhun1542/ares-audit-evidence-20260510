# ARES-KIS Phase C — Dry-Run Report

- RUN_ID: `ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z`
- VERDICT: **PHASE_C_DEDUPE_REVIEW_REQUIRED**
- production stream write: **false** (no canonical/order_executions/legacy/mirror writes)
- execution-reconciler: kept ONLINE (no stop, no restart)
- publishCanonicalFill: kept ENABLED (no SYNTHETIC_FILLS_DISABLED guard)

## Runtime Snapshot
| 항목 | Before | After |
|---|---|---|
| emarkos:v1:mode | SAFE | SAFE |
| active_strategy_id | RAM26_ALPHA_0001_b215c119ea23 | RAM26_ALPHA_0001_b215c119ea23 |
| BUY guard applied this run | - | false |

## Reconciliation
| 항목 | 값 |
|---|---:|
| KIS official unique fills | 964 |
| matched (canonical or legacy) | 53 |
| missing (not found in ARES streams) | 911 |
| missing_rate_pct | 94.50% |
| canonical duplicate keys detected | 927 |

## Artifacts
- State JSON      : `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z/PHASE_C_STATE.json`
- Summary         : `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z/summary.json`
- Dedupe Analysis : `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z/dup_analysis.json`
- Source Scan     : `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z/execution_reconciler_publishCanonicalFill_scan.txt`
- Stream State    : `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z/stream_state.txt`
- PM2 Status      : `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z/pm2_status.txt`
- PM2 Describe    : `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z/pm2_describe_execution_reconciler.txt`
- PM2 Logs        : `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z/pm2_logs_execution_reconciler.txt`
- Redis Before    : `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z/redis_before.txt`
- Redis After     : `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_PHASE_C_DRYRUN_20260507T173458Z/redis_after.txt`

## Next Step
1. If `missing_rate_pct` > 0.5 → run `ares_kis_phase_c_quarantine_write.sh` (writes to `backfill:kis:fills:quarantine` only).
2. If `canonical duplicate keys` > 0 → review dedupe analysis; consider option 2 guard before allowing further canonical writes.
3. Do not release BUY guard until KIS_official vs ARES_canonical match rate ≥ 99.5% AND next 1~3 live fills are confirmed.
