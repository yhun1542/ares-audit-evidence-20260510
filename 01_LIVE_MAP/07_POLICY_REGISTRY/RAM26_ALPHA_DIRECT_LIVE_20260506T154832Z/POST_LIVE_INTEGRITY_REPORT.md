# Post-LIVE Integrity Repair Report

## Verdict

```
POST_LIVE_INTEGRITY_REPAIR_SUCCESS
```

## Runtime

| Item | Before | After |
|---|---:|---:|
| emarkos:v1:mode | LIVE | LIVE |
| active_strategy_id | RAM26_ALPHA_0001_b215c119ea23 | RAM26_ALPHA_0001_b215c119ea23 |
| kill-switch:active | false | false |

## OPEN-003 Proof

| Metric | Value |
|---|---:|
| summary status | found |
| kis_total | 540 |
| kis_unique | 20 |
| written_to_production | 20 |
| final_match_rate_pct | 100 |
| final_missing_rate_pct | 0 |
| scope note | none |

## Code Repair

| Module | Patched This Run |
|---|---:|
| atr_hard_stop.py | false |
| kis_ws_self_healing.py | false |
| sleeve_allocator.py | env reasserted if PM2 app exists |

## Key Fixes Verified

- `atr_hard_stop.py` contains `kill-switch:active`.
- `atr_hard_stop.py` contains `"reduce_only": True`.
- `kis_ws_self_healing.py` contains `NO_FILLS_RESTART_ENABLE`.
- no-fill stale is alert-only by default under single-WS-owner policy.

## Artifacts

- State JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/post_live_integrity/POST_LIVE_INTEGRITY_REPAIR_20260508T064448Z/POST_LIVE_INTEGRITY_STATE.json`
- Redis Before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/post_live_integrity/POST_LIVE_INTEGRITY_REPAIR_20260508T064448Z/redis_before.txt`
- Redis After: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/post_live_integrity/POST_LIVE_INTEGRITY_REPAIR_20260508T064448Z/redis_after.txt`
- Stream State: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/post_live_integrity/POST_LIVE_INTEGRITY_REPAIR_20260508T064448Z/stream_state.txt`
- Code Before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/post_live_integrity/POST_LIVE_INTEGRITY_REPAIR_20260508T064448Z/code_before.txt`
- Code After: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/post_live_integrity/POST_LIVE_INTEGRITY_REPAIR_20260508T064448Z/code_after.txt`
- PM2 Before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/post_live_integrity/POST_LIVE_INTEGRITY_REPAIR_20260508T064448Z/pm2_before.txt`
- PM2 After: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/post_live_integrity/POST_LIVE_INTEGRITY_REPAIR_20260508T064448Z/pm2_after.txt`
- PM2 Logs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/post_live_integrity/POST_LIVE_INTEGRITY_REPAIR_20260508T064448Z/pm2_logs.txt`
- OPEN003 Summary Copy: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/post_live_integrity/POST_LIVE_INTEGRITY_REPAIR_20260508T064448Z/open003_summary_latest.json`

## Forbidden Actions

- No nextgen2-live restart.
- No position-sync-service restart.
- No PM2 global restart.
- No test order.
- No champion mutation.
- No additional production backfill.
