# trading:enabled Reset Forensic Report

## Verdict

TRADING_ENABLED_RESET_FORENSIC_COMPLETE

## Artifacts

- Redis before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/trading_reset_forensic/TRADING_ENABLED_RESET_FORENSIC_20260509T021040Z/redis_before.txt`
- Redis after: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/trading_reset_forensic/TRADING_ENABLED_RESET_FORENSIC_20260509T021040Z/redis_after.txt`
- Redis monitor: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/trading_reset_forensic/TRADING_ENABLED_RESET_FORENSIC_20260509T021040Z/redis_monitor_180s.txt`
- Code refs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/trading_reset_forensic/TRADING_ENABLED_RESET_FORENSIC_20260509T021040Z/code_refs_trading_enabled.txt`
- PM2 status: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/trading_reset_forensic/TRADING_ENABLED_RESET_FORENSIC_20260509T021040Z/pm2_status.txt`

## Safety

- Read-only.
- No trading mode mutation.
- No champion mutation.
- No PM2 restart.
