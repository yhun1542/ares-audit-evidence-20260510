# RAM26 Feature Aggregator Production Install Report

## Verdict

`RAM26_FEATURE_AGGREGATOR_PROD_INSTALLED`

## Installed

- File: `/home/ubuntu/ares_current/engines/ram26/ram26_feature_aggregator_prod_v1.py`
- PM2 start/restart: true

## Safety

- No order created.
- No trading mode mutation.
- No champion mutation.
- No production fill stream write.
- No nextgen2-live restart.
- No position-sync-service restart.

## Artifacts

- State: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_aggregator/RAM26_FEATURE_AGG_PROD_INSTALL_20260509T010643Z/RAM26_FEATURE_AGG_INSTALL_STATE.json`
- Self-test: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_aggregator/RAM26_FEATURE_AGG_PROD_INSTALL_20260509T010643Z/self_test.json`
- Once stdout: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_aggregator/RAM26_FEATURE_AGG_PROD_INSTALL_20260509T010643Z/once_stdout.txt`
- Once stderr: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_aggregator/RAM26_FEATURE_AGG_PROD_INSTALL_20260509T010643Z/once_stderr.txt`
- Backup dir: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_aggregator/RAM26_FEATURE_AGG_PROD_INSTALL_20260509T010643Z/backup`

## Verification Commands

```bash
redis-cli TYPE ram26:features:v1
redis-cli HLEN ram26:features:v1
redis-cli GET ram26:features:v1:meta
pm2 status | grep ram26-feature-aggregator
pm2 logs ram26-feature-aggregator --lines 50 --nostream
```
