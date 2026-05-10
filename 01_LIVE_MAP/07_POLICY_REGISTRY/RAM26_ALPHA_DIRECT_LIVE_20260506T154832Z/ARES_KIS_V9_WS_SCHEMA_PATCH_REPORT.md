# ARES-KIS-US-AUTOPILOT V9 WS Schema Patch Report

## Verdict

```
ARES_KIS_V9_WS_SCHEMA_PATCH_COMPLETE
```

## Runtime

| 항목 | Before | After |
|---|---:|---:|
| emarkos:v1:mode | SAFE | SAFE |
| active_strategy_id | RAM26_ALPHA_0001_b215c119ea23 | RAM26_ALPHA_0001_b215c119ea23 |
| BUY guard applied | - | false |
| targeted restart performed | - | false |
| restart deferred reason | - | RESTART_NOT_REQUESTED |

## Patched Values

| TR_ID | Expected |
|---|---:|
| HDFSCNT0 | 25 |
| H0GSCNI0 | 25 |
| H0GSCNI9 | 25 |
| HDFSASP0 | 16 |
| HDFSASP1 | 16 |

## Artifacts

- Patched files: `/home/ubuntu/kis-us-broker-v7/core/tr_ids.mjs`
- Verify JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/patches/ARES_KIS_V9_WS_SCHEMA_20260507T131125Z/verify.json`
- Diff: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/patches/ARES_KIS_V9_WS_SCHEMA_20260507T131125Z/tr_ids_patch.diff`
- Redis before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/patches/ARES_KIS_V9_WS_SCHEMA_20260507T131125Z/redis_before.txt`
- Redis after: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/patches/ARES_KIS_V9_WS_SCHEMA_20260507T131125Z/redis_after.txt`
- PM2 before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/patches/ARES_KIS_V9_WS_SCHEMA_20260507T131125Z/pm2_before.txt`
- PM2 after: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/patches/ARES_KIS_V9_WS_SCHEMA_20260507T131125Z/pm2_after.txt`
- Backup dir: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/patches/ARES_KIS_V9_WS_SCHEMA_20260507T131125Z/backup`

## Non-Mutation Confirmation

- No production fill stream backfill.
- No test order.
- No champion key mutation.
- No nextgen2-live restart.
- No PM2 global restart.
