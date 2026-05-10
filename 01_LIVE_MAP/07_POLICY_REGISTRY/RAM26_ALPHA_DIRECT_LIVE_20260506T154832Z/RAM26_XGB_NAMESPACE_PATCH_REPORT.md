# RAM26 XGB Namespace Patch Report

## Verdict

RAM26_XGB_NAMESPACE_PATCH_APPLIED

## Actions

- Aggregator timestamp parser patched for `scored_at` / `generated_at` if missing.
- PM2 env set:
  - `RAM26_XGB_KEY_PREFIX=xgb:risk`
  - `RAM26_XGB_KEY_PATTERNS_CSV=xgb:risk:{symbol},xgb:v8:risk:{symbol}`
  - `RAM26_XGB_MAX_AGE_SEC=900`
  - `RAM26_MARKET_MOM_KEY=ram26:market:IWM:mom10`
  - `RAM26_MARKET_VOL_KEY=ram26:market:IWM:vol`

## PM2

ram26-feature-aggregator restarted

## Safety

- No trading mode mutation.
- No champion mutation.
- No order path restart.
- No production fill stream write.

## Artifacts

- Redis before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_pipeline_unblock/RAM26_XGB_NAMESPACE_PATCH_20260509T020320Z/redis_before.txt`
- Redis after: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_pipeline_unblock/RAM26_XGB_NAMESPACE_PATCH_20260509T020320Z/redis_after.txt`
- Backup: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_pipeline_unblock/RAM26_XGB_NAMESPACE_PATCH_20260509T020320Z/backup`
