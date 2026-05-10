# ARES V3.5 Hotfix Hardening Report

## Verdict

ARES_V35_HOTFIX_APPLIED

## Changes

- ATR hard stop checks both `system:kill_switch` and `kill-switch:active`.
- ATR hard stop SELL payload now includes `reduce_only=true`.
- KIS WS watchdog no-fill condition is alert-only by default under single-WS-owner constraint.
- Targeted PM2 restart only:
  - atr-hard-stop
  - kis-ws-watchdog
  - sleeve-allocator
  - regime-smoother

## Backup

`/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/v35_hotfix/ARES_V35_HOTFIX_20260508T062817Z/backup`
