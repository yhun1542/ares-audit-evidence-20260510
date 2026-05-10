# AUB System Status Report
**Generated**: 2025-12-31 09:21:21
**Champion**: V5 (regime15 + icir_v4 + vix_sizing + hedge + vix_crisis_v46)

## Summary
| Type | PASS | FAIL | INCONCLUSIVE | PENDING |
|------|------|------|--------------|--------|
| strategy | 5 | 0 | 0 | 14 |
| service | 10 | 1 | 0 | 0 |
| connector | 0 | 12 | 0 | 0 |

## Strategy Components

| Component | Verdict | Reason |
|-----------|---------|--------|
| momentum_engine | **PENDING** | ok |
| mean_reversion_engine | **PENDING** | ok |
| hybrid_engine | **PENDING** | ok |
| phoenix_engine | **PENDING** | ok |
| dual_model_router | **PENDING** | ok |
| regime15 | **PASS** | V5 Champion component |
| icir_v4 | **PASS** | V5 Champion component |
| vix_sizing | **PASS** | V5 Champion component |
| hedge | **PASS** | V5 Champion component |
| vix_crisis_v46 | **PASS** | V5 Champion component |
| regime_v11 | **PENDING** | not_tested |
| icir_ensemble | **PENDING** | not_tested |
| hedge_overlay | **PENDING** | not_tested |
| rate_shock_gate | **PENDING** | features_ready |
| cost_aware_portfolio_v1 | **PENDING** | ok |
| crash_protection_barrier_v1 | **PENDING** | ok |
| deflated_sharpe_guard_v1 | **PENDING** | ok |
| entropy_regime_filter_v1 | **PENDING** | ok |
| regime_adaptive_sizer_v1 | **PENDING** | ok |

## Service Components

| Component | Verdict | Reason |
|-----------|---------|--------|
| telegram_alerts | **PASS** | All gates passed |
| nrc_v5_1 | **PASS** | All gates passed |
| ml_training | **PASS** | All gates passed |
| alpha_orchestrator | **PASS** | All gates passed |
| data_orchestrator | **PASS** | All gates passed |
| v6_orchestrator | **PASS** | All gates passed |
| smart_exec | **PASS** | All gates passed |
| live_trading_manager | **FAIL** | Failed gates: api_contract_pass_rate, uptime_rate, data_sync_lag_seconds, degraded_mode_available |
| dashboard | **PASS** | All gates passed |
| canary_monitor | **PASS** | All gates passed |
| integrated_monitor | **PASS** | All gates passed |

## Connector Components

| Component | Verdict | Reason |
|-----------|---------|--------|
| polygon_feed | **FAIL** | Failed gates: reconnection_success_rate |
| fred_macro | **FAIL** | Failed gates: reconnection_success_rate |
| sec_filings | **FAIL** | Failed gates: reconnection_success_rate |
| options_chain | **FAIL** | Failed gates: reconnection_success_rate |
| vix_data | **FAIL** | Failed gates: reconnection_success_rate |
| trading_calendar | **FAIL** | Failed gates: reconnection_success_rate |
| fundamentals_pit | **FAIL** | Failed gates: reconnection_success_rate |
| sharadar | **FAIL** | Failed gates: reconnection_success_rate |
| ortex | **FAIL** | Failed gates: reconnection_success_rate |
| dark_pool | **FAIL** | Failed gates: reconnection_success_rate |
| ibkr_connector | **FAIL** | Failed gates: connection_success_rate, reconnection_success_rate |
| kis_connector | **FAIL** | Failed gates: connection_success_rate, reconnection_success_rate |

## Next Fix Tickets

### 1. polygon_feed
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: reconnection_success_rate
- **Failed Gates**: reconnection_success_rate

### 2. fred_macro
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: reconnection_success_rate
- **Failed Gates**: reconnection_success_rate

### 3. sec_filings
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: reconnection_success_rate
- **Failed Gates**: reconnection_success_rate

### 4. options_chain
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: reconnection_success_rate
- **Failed Gates**: reconnection_success_rate

### 5. vix_data
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: reconnection_success_rate
- **Failed Gates**: reconnection_success_rate

### 6. trading_calendar
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: reconnection_success_rate
- **Failed Gates**: reconnection_success_rate

### 7. fundamentals_pit
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: reconnection_success_rate
- **Failed Gates**: reconnection_success_rate

### 8. sharadar
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: reconnection_success_rate
- **Failed Gates**: reconnection_success_rate

### 9. ortex
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: reconnection_success_rate
- **Failed Gates**: reconnection_success_rate

### 10. dark_pool
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: reconnection_success_rate
- **Failed Gates**: reconnection_success_rate

### 11. ibkr_connector
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: connection_success_rate, reconnection_success_rate
- **Failed Gates**: connection_success_rate, reconnection_success_rate

### 12. kis_connector
- **Type**: connector
- **Verdict**: FAIL
- **Reason**: Failed gates: connection_success_rate, reconnection_success_rate
- **Failed Gates**: connection_success_rate, reconnection_success_rate

### 13. live_trading_manager
- **Type**: service
- **Verdict**: FAIL
- **Reason**: Failed gates: api_contract_pass_rate, uptime_rate, data_sync_lag_seconds, degraded_mode_available
- **Failed Gates**: api_contract_pass_rate, uptime_rate, data_sync_lag_seconds, degraded_mode_available

