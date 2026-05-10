# OPEN-003 Full Auto Recovery Report

## Verdict

```
OPEN003_FULLY_RESOLVED_AUTO_LIVE
```

## Summary

KIS official TTTS3035R fills were used as broker-truth SSOT.
Missing fills were backfilled into production canonical streams with dedupe.

## Runtime

| Item | Before | After |
|---|---:|---:|
| emarkos:v1:mode | SAFE | LIVE |
| active_strategy_id | RAM26_ALPHA_0001_b215c119ea23 | RAM26_ALPHA_0001_b215c119ea23 |
| LIVE performed | - | true |
| LIVE block reason | - | none |

## Reconciliation

| Metric | Value |
|---|---:|
| final_match_rate_pct | 100 |
| final_missing_rate_pct | 0 |
| written_to_production | 20 |
| production_stream_write | true |

## Streams Written

- `stream:fills`
- `ares:fills:canonical`
- `emarkos:v6:stream:fills`
- `ares:order_executions`
- `ares:broker_truth:events`
- `signal:guard:broker_truth`

## Non-Mutation Confirmation

- No test order.
- No nextgen2-live restart.
- No position-sync-service restart.
- No Alpha Champion rollback.
- No champion key mutation.

## Artifacts

- State JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/open003_full_recovery/OPEN003_FULL_AUTO_RECOVERY_20260508T062911Z/OPEN003_FULL_AUTO_STATE.json`
- Backfill Summary: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/open003_full_recovery/OPEN003_FULL_AUTO_RECOVERY_20260508T062911Z/canonical_backfill/summary.json`
- Redis Before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/open003_full_recovery/OPEN003_FULL_AUTO_RECOVERY_20260508T062911Z/redis_before.txt`
- Redis After: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/open003_full_recovery/OPEN003_FULL_AUTO_RECOVERY_20260508T062911Z/redis_after.txt`
- Stream Before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/open003_full_recovery/OPEN003_FULL_AUTO_RECOVERY_20260508T062911Z/stream_before.txt`
- Stream After: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/open003_full_recovery/OPEN003_FULL_AUTO_RECOVERY_20260508T062911Z/stream_after.txt`
- PM2 Status: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/open003_full_recovery/OPEN003_FULL_AUTO_RECOVERY_20260508T062911Z/pm2_status.txt`
- PM2 Logs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/open003_full_recovery/OPEN003_FULL_AUTO_RECOVERY_20260508T062911Z/pm2_logs_relevant.txt`
- V3.5 Audit: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/open003_full_recovery/OPEN003_FULL_AUTO_RECOVERY_20260508T062911Z/v35_module_audit.txt`
