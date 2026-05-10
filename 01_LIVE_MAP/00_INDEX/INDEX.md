# ARES Live System Map

Created: 2026-05-10T00:02:13Z

## Runtime summary
- mode: LIVE
- trading:enabled: true
- active RAM26 strategy: RAM26_ALPHA_0001_b215c119ea23
- G8: READY
- bridge active: LIVE_FINAL_TO_CHAMPION
- champion:targets:ssot HLEN: 30
- champion:target HLEN: 30

## Directory layout
- 01_LIVE_PM2: active PM2 scripts and status
- 02_RAM26_CHAMPION: RAM26 engine/feature/bridge/projector/guard
- 03_EXECUTION_ORDER_PATH: order-intent/execution/live trade gates
- 04_KIS_BROKER_TRUTH: KIS broker, fills, positions, truth writers
- 05_RISK_GUARDS: sleeve/regime/ATR/kill/halt/risk
- 06_FEATURES_DATA: feature publishers and DB pointers
- 07_POLICY_REGISTRY: policy/registry/promotions
- 08_OBSERVABILITY: SLO/playbooks/log pointers
- 09_HISTORICAL_POINTERS: old versions and archives
- 10_DOWNLOAD_PACKS: generated bundles

## Important rule
This map is symlink/index based. It does not move production files.
