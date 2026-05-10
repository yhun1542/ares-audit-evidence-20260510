# RAM26 Market Signal Adapter Install Report

## Verdict

`RAM26_MARKET_SIGNAL_ADAPTER_INSTALLED`

## Installed

- Adapter: `/home/ubuntu/ares_current/engines/ram26/ram26_market_signal_adapter_prod_v1.py`
- Probe: `/home/ubuntu/ares_current/engines/ram26/ram26_feature_source_contract_probe_v1.py`
- RAW_MODE: `change_aware`
- Adapter once rc: `0`
- Probe rc: `0`
- PM2 actions: `market_adapter_started_or_restarted,feature_aggregator_restarted_with_wrapper_keys`

## Output Keys

- `ram26:market:IWM:mom10`
- `ram26:market:IWM:vol`
- `ram26:market:adapter:meta`
- `ram26:market:adapter:events`

## Feature Aggregator Env

If restarted, `ram26-feature-aggregator` now reads:
- `RAM26_MARKET_MOM_KEY=ram26:market:IWM:mom10`
- `RAM26_MARKET_VOL_KEY=ram26:market:IWM:vol`

## Safety

- Existing `signal:IWM:mom10` and `signal:IWM:vol` are not modified.
- No trading mode mutation.
- No champion mutation.
- No order-intent-executor restart.
- No nextgen2-live restart.
- No position-sync-service restart.
- No production fill stream write.

## Artifacts

- State: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/market_signal_adapter/RAM26_MARKET_ADAPTER_INSTALL_20260509T012257Z/RAM26_MARKET_ADAPTER_INSTALL_STATE.json`
- Adapter self-test: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/market_signal_adapter/RAM26_MARKET_ADAPTER_INSTALL_20260509T012257Z/adapter_self_test.json`
- Adapter once stdout: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/market_signal_adapter/RAM26_MARKET_ADAPTER_INSTALL_20260509T012257Z/adapter_once_stdout.txt`
- Adapter once stderr: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/market_signal_adapter/RAM26_MARKET_ADAPTER_INSTALL_20260509T012257Z/adapter_once_stderr.txt`
- Probe JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/market_signal_adapter/RAM26_MARKET_ADAPTER_INSTALL_20260509T012257Z/feature_source_contract_probe.json`
- Backup dir: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/market_signal_adapter/RAM26_MARKET_ADAPTER_INSTALL_20260509T012257Z/backup`

## Verification

```bash
redis-cli GET ram26:market:IWM:mom10
redis-cli GET ram26:market:IWM:vol
redis-cli GET ram26:market:adapter:meta
redis-cli GET ram26:features:v1:meta
pm2 status | egrep 'ram26-market-signal-adapter|ram26-feature-aggregator'
```
