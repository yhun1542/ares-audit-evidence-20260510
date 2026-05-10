# ARES-KIS V10 Broker Truth Recovery Report

## Verdict

```
V10_RECONCILIATION_DRYRUN_COMPLETE
```

## Runtime

| 항목 | Before | After |
|---|---:|---:|
| emarkos:v1:mode | SAFE | SAFE |
| active_strategy_id | RAM26_ALPHA_0001_b215c119ea23 | RAM26_ALPHA_0001_b215c119ea23 |
| SAFE BUY guard applied | - | false |

## Reconciliation Dry-Run

| 항목 | 값 |
|---|---:|
| status | success |
| KIS unique fills | 20 |
| matched | 0 |
| missing | 20 |
| missing_rate_pct | 100 |
| production stream write | false |

## Diagnosis

- ares-kis-us-broker is not treated as an independent PM2 app.
- execution-reconciler KIS API_ERROR patterns are captured in `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/execution_reconciler_diagnosis.txt`.
- broker_truth and execution streams are sampled and classified in `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/stream_event_classification.txt`.
- BUY guard remains required until match rate >= 99.5%.

## Artifacts

- State JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/ARES_KIS_V10_STATE.json`
- WS Verify: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/ws_field_count_verify.json`
- Reconciler Diagnosis: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/execution_reconciler_diagnosis.txt`
- Env Diagnosis: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/env_diagnosis.txt`
- Stream State: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/redis_stream_state.txt`
- Stream Classification: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/stream_event_classification.txt`
- Backfill Dir: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/backfill_dryrun`
- Redis Before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/redis_before.txt`
- Redis After: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/redis_after.txt`
- PM2 Status: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/pm2_status.txt`
- PM2 Describe: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/pm2_describe_relevant.txt`
- PM2 Logs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V10_BROKER_TRUTH_20260507T164357Z/pm2_logs_relevant.txt`

## Guardrail

- No production backfill.
- No test order.
- No nextgen2-live restart.
- No position-sync-service restart.
- No Alpha Champion rollback.
