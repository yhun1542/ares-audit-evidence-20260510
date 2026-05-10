# RAM26 Promote Staged to Current Report

## Verdict

RAM26_STAGED_PROMOTED_TO_CURRENT

## What changed

- `ssot:target:v2:current` written from `ram26:ssot:target:v2:staged`
- `policy:champion:active` written as RAM26 bridge-owned payload
- `champion:targets:ssot` HASH populated
- `champion:target` HASH populated for v8.5 bridge compatibility
- `ares:bridge:active=ram26_final_to_champion_bridge_v2`

## What did NOT change

- No `trading:enabled=true`
- No `emarkos:v1:mode=LIVE`
- No order path restart
- No fill stream write

## Artifacts

- Backup dir: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_approval/RAM26_PROMOTE_STAGED_TO_CURRENT_20260509T063215Z/backup`
- Promotion payloads: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_approval/RAM26_PROMOTE_STAGED_TO_CURRENT_20260509T063215Z/promote_payloads.json`
- Redis after: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_approval/RAM26_PROMOTE_STAGED_TO_CURRENT_20260509T063215Z/redis_after.txt`
