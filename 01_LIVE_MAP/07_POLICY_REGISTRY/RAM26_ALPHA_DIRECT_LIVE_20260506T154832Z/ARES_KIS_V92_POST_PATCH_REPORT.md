# ARES-KIS V9.2 Post-Patch Validation Report

## Verdict

```
V92_POST_PATCH_REVIEW_REQUIRED
```

## Runtime

| 항목 | Before | After |
|---|---:|---:|
| emarkos:v1:mode | LIVE | SAFE |
| active_strategy_id | RAM26_ALPHA_0001_b215c119ea23 | RAM26_ALPHA_0001_b215c119ea23 |
| SAFE guard applied | - | true |

## PM2

| 항목 | 값 |
|---|---:|
| ares-kis-us-broker online | false |
| execution-reconciler-v4.1 online | false |
| critical log hits | 0 |

## KIS Official Reconciliation Dry-Run

| 항목 | 값 |
|---|---:|
| backfill dry-run status | success |
| matched | 0 |
| missing | 553 |
| missing_rate_pct | 100 |
| production stream write | false |

## Artifacts

- State JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V92_POST_PATCH_20260507T144903Z/ARES_KIS_V92_STATE.json`
- WS Verify: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V92_POST_PATCH_20260507T144903Z/ws_field_count_verify.json`
- PM2 Status: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V92_POST_PATCH_20260507T144903Z/pm2_status.txt`
- PM2 Describe: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V92_POST_PATCH_20260507T144903Z/pm2_describe_relevant.txt`
- PM2 Logs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V92_POST_PATCH_20260507T144903Z/pm2_logs_relevant.txt`
- Stream State: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V92_POST_PATCH_20260507T144903Z/redis_stream_state.txt`
- Stream Classification: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V92_POST_PATCH_20260507T144903Z/stream_event_classification.txt`
- Backfill Dir: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V92_POST_PATCH_20260507T144903Z/backfill_dryrun`
- Redis Before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V92_POST_PATCH_20260507T144903Z/redis_before.txt`
- Redis After: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incidents/ARES_KIS_V92_POST_PATCH_20260507T144903Z/redis_after.txt`

## Decision

- BUY guard remains required until KIS official vs ARES canonical match rate >= 99.5%.
- No production backfill was performed.
- No order was placed.
- No nextgen2-live restart was performed.
- No position-sync-service restart was performed.
