# RAM26 Daily Feature Publisher Install Report

## Verdict

RAM26_DAILY_FEATURE_PUBLISHER_INSTALLED

## Installed

- File: `/home/ubuntu/ares_current/engines/ram26/ram26_daily_feature_publisher_prod_v1.py`
- DB: `/home/ubuntu/ares_x_v11_0.db`
- PM2: `ram26-daily-feature-publisher installed_or_restarted`

## Safety

- No trading mode mutation.
- No champion mutation.
- No order path restart.
- No production fill stream write.
- No nextgen2-live / position-sync-service restart.

## Artifacts

- Self-test: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_pipeline_unblock/RAM26_DAILY_FEATURE_INSTALL_20260509T020657Z/self_test.json`
- Once stdout: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_pipeline_unblock/RAM26_DAILY_FEATURE_INSTALL_20260509T020657Z/once_stdout.json`
- Once stderr: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_pipeline_unblock/RAM26_DAILY_FEATURE_INSTALL_20260509T020657Z/once_stderr.txt`
- Backup: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/feature_pipeline_unblock/RAM26_DAILY_FEATURE_INSTALL_20260509T020657Z/backup`

## Verify

```bash
redis-cli GET ares:features:daily:latest
LATEST=$(redis-cli --raw GET ares:features:daily:latest)
redis-cli HLEN "$LATEST"
redis-cli GET ares:features:daily:meta
```
