# RAM26 Projector v3 Install Report

## Verdict
RAM26_PROJECTOR_V3_INSTALLED

## Changes
- Handles SSOT_TARGET_V2 envelope `targets.positions[]`.
- Propagates _meta_marker and phase9_b1_marker to champion hashes.
- Allows projection during LIVE if source is valid and kill-switch is clear.
- Does not write ssot:target:v2:current, policy:champion:active, mode, trading, or fill streams.

## Artifacts
- self_test.json
- oneshot_stdout.json
- verify_after.txt
- backup: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/contract_runtime_hardening/20260509T224650Z_projector_v3/backup`
