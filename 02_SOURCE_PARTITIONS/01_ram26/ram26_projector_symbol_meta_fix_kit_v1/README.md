# RAM26 Projector Symbol + Meta-Marker Fix Kit v1

## Scope (strictly limited)
Replace ONLY the file:
```
/home/ubuntu/ares_current/bridges/ram26_target_ssot_projector_v1.mjs
```
and restart ONLY the PM2 process `ram26-target-ssot-projector`.

## Hard prohibitions (enforced, refused, or untouched)
- No write to `ssot:target:v2:current`
- No write to `policy:champion:active`
- No change to `emarkos:v1:mode`
- No change to `trading:enabled`
- No global PM2 restart, no order-path or fill-stream restart
- No BUY/SELL test orders

## Bug fixes
1. **Symbol map extraction** — old code did `Object.entries(ssot.targets || {})` which yielded `["0", obj], ["1", obj], ...` when targets is an array, producing numeric hash keys. New code reads `entry.symbol` for arrays and falls back to `weights` dict if needed.
2. **`_meta_marker` propagation** — old code never propagated the upstream `phase9_b1_marker` into `champion:targets:ssot`, causing `ares-upstream-ssot-router` to FailClosed and publish endless `ops:halt:request`. New code reads marker from 4 sources in priority order and HSETs `_meta_marker` field.
3. **Strict marker validation** — refuses to publish if no genuine upstream marker is found (no synthesis).
4. **Legacy `champion:target` HASH** — also refreshed alongside `champion:targets:ssot` so SLO guards that monitor the legacy hash do not alarm.

## Run
```bash
RAM26_PROJECTOR_FIX_CONFIRM=YES_PATCH_RAM26_PROJECTOR_SYMBOL_META \
  bash ./install_ram26_projector_symbol_meta_fix.sh

RAM26_PROJECTOR_VERIFY_CONFIRM=YES_VERIFY_RAM26_PROJECTOR_SYMBOL_META \
  bash ./verify_ram26_projector_symbol_meta_fix.sh
```

## Success criteria
- `RAM26_PROJECTOR_SYMBOL_META_FIX_PASS` printed by install
- `champion:targets:ssot` HLEN >= 9
- `champion:target` HLEN >= 9
- No numeric keys in either hash
- Ticker keys (AAPL, MSFT, ...) present
- `_meta_marker` field present
- `ram26:target_ssot_projector:last.pass = true`
- `trading:enabled` unchanged
- `emarkos:v1:mode` unchanged

## Rollback
```bash
cp /home/ubuntu/ares_current/bridges/backups/ram26_target_ssot_projector_v1.mjs.<TS>.bak \
   /home/ubuntu/ares_current/bridges/ram26_target_ssot_projector_v1.mjs
pm2 restart ram26-target-ssot-projector --update-env
```
