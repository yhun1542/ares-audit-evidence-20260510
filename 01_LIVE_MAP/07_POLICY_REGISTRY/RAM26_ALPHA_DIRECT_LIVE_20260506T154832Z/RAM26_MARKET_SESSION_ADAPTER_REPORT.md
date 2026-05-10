# RAM26 Session-Aware Market Adapter Report

## Verdict

RAM26_MARKET_SESSION_ADAPTER_INSTALLED

## Runtime

- File: `/home/ubuntu/ares_current/engines/ram26/ram26_market_signal_adapter_session_v2.py`
- PM2: `ram26-market-signal-adapter replaced_with_session_v2,ram26-feature-aggregator restarted`

## Safety

- Existing raw keys are not modified.
- No trading mode mutation.
- No champion mutation.
- No order path restart.
- No production fill stream write.

## Artifacts

- Self-test: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/market_session_adapter/RAM26_MARKET_SESSION_ADAPTER_INSTALL_20260509T030709Z/self_test.json`
- Once stdout: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/market_session_adapter/RAM26_MARKET_SESSION_ADAPTER_INSTALL_20260509T030709Z/once_stdout.json`
- Once stderr: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/market_session_adapter/RAM26_MARKET_SESSION_ADAPTER_INSTALL_20260509T030709Z/once_stderr.txt`
- Backup: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/market_session_adapter/RAM26_MARKET_SESSION_ADAPTER_INSTALL_20260509T030709Z/backup`
