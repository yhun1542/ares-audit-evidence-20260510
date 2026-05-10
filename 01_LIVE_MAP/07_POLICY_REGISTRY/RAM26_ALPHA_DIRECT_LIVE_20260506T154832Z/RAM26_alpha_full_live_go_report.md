# ARES RAM26 Alpha Champion FULL LIVE GO Report

## Verdict

```
FULL_LIVE_ALPHA_GO_SUCCESS
```

## Summary

ARES RAM26 Alpha Champion이 BUY까지 허용되는 FULL LIVE 상태로 전환되었다.

| 항목 | 값 |
|---|---|
| Run ID | RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z |
| Promotion ID | RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z |
| Runtime | ARES RAM26 v8.8A |
| Active Champion | RAM26_ALPHA_0001_b215c119ea23 |
| Previous Champion | BASELINE_1ca1b110d384 / p9B_106 |
| Mode | LIVE |
| Order Routing | live |
| Paper Trading | false |
| Shadow Only | false |
| Dry Run | false |
| Trading Enabled | true |
| Kill Switch Active | false |
| PM2 App | nextgen2-live |

## Config

- Live Config: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/live_config/ram26_champion_RAM26_ALPHA_0001_b215c119ea23.json`
- Rollback Script: `/opt/ares/RAM26_alpha_last_rollback.sh`

## Redis Target State

```text
emarkos:v1:mode=LIVE
emarkos:v1:ram26:active_strategy_id=RAM26_ALPHA_0001_b215c119ea23
emarkos:v1:ram26:champion_params_file=/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/live_config/ram26_champion_RAM26_ALPHA_0001_b215c119ea23.json
emarkos:v1:ram26:order_routing=live
emarkos:v1:ram26:shadow_only=false
emarkos:v1:ram26:paper_trading=false
emarkos:v1:ram26:dry_run=false
trading:enabled=true
kill-switch:active=false
```

## Backtest Reference Metrics

| Metric | Value |
|---|---:|
| CAGR | 143.55~143.59% |
| MDD | -9.06% |
| Sharpe | 6.91 |
| WF Min SR | 5.99 |
| Turnover | 0.7162 |
| Cost bps | 9.31 |

## Verification Artifacts

- Redis Before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_redis_before.txt`
- Redis After: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_redis_after.txt`
- PM2 Before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_pm2_before.txt`
- PM2 After: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_pm2_status.txt`
- Log Tail: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_log_tail.txt`
- Risk Scan: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_risk_scan.txt`
- Runtime JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_runtime_state.json`
- Champion Briefing: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/CHAMPION_VERSION_BRIEFING.md`

## Rollback Command

```bash
sudo bash /opt/ares/RAM26_alpha_last_rollback.sh
```

## Notes

이 스크립트는 실제 테스트 주문을 생성하지 않았다.
FULL LIVE 상태는 Redis runtime gate, champion pointer, config, PM2 status, order routing, paper/shadow/dry flags, trading enabled, kill-switch inactive 상태로 검증했다.
