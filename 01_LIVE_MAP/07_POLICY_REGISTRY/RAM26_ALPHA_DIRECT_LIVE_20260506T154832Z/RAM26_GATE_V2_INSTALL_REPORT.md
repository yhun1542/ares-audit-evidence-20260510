# RAM26 Gate v2 Install Report

## Verdict

RAM26_GATE_V2_PATCH_INSTALLED

## Installed

- Aggregator v2: `/home/ubuntu/ares_current/engines/ram26/ram26_feature_aggregator_prod_v2.py`
- Engine CLI adapter: `/home/ubuntu/ares_current/engines/ram26/ram26_engine_cli_adapter_v1.py`
- PM2 actions: `ram26-feature-aggregator replaced_with_prod_v2`

## Key change

- XGB blocked symbols are classified as `risk_excluded`, not data failures.
- eligible_ratio denominator = universe - risk_excluded.
- Engine CLI adapter translates old bridge-gate style args into real engine args.

## Safety

- No LIVE mode mutation.
- No trading enable.
- No champion manual mutation.
- No current SSOT write.
- No order path restart.

