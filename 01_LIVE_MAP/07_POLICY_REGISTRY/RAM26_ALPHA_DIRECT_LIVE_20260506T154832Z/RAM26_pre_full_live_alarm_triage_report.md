# RAM26 Pre-FULL-LIVE Alarm Triage Report

## Verdict

```
FULL_LIVE_GO_DEFERRED_CRITICAL_RISK
```

## Current Champion

| 항목 | 값 |
|---|---|
| Champion ID | RAM26_ALPHA_0001_b215c119ea23 |
| Promotion ID | RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z |
| Redis Mode | SAFE |
| Active Strategy | RAM26_ALPHA_0001_b215c119ea23 |
| Order Routing | live |
| Shadow Only | false |
| Paper Trading | false |
| Dry Run | false |
| Trading Enabled | true |
| Kill Switch Active | false |

## Alarm Classification

| Alarm | Observed Classification | Action |
|---|---|---|
| R-GPU-CRASH | present_or_referenced | 코드 경로 확인 필요. risk/order gate 영향 있으면 BUY 금지 |
| F-EQUITY-STALE | missing_key | equity freshness missing이면 BUY 금지 |
| C-METRIC-VERSION-MISSING | missing_key | metric contract 필수 여부 확인 필요 |
| R-HEARTBEAT-SENTINEL-DOWN | present_or_referenced | sentinel watchdog이면 복구 전 BUY 금지 |

## Block Reasons

```text
equity_updated_at_missing manual_review_required_code_refs_present
```

## Artifacts

- Redis Snapshot: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incident_triage/RAM26_PRE_FULL_LIVE_TRIAGE_20260507T103035Z/RAM26_pre_full_live_redis_snapshot.txt`
- PM2 Snapshot: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incident_triage/RAM26_PRE_FULL_LIVE_TRIAGE_20260507T103035Z/RAM26_pre_full_live_pm2_snapshot.txt`
- Code Refs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incident_triage/RAM26_PRE_FULL_LIVE_TRIAGE_20260507T103035Z/RAM26_pre_full_live_alarm_code_refs.txt`
- Log Refs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incident_triage/RAM26_PRE_FULL_LIVE_TRIAGE_20260507T103035Z/RAM26_pre_full_live_log_refs.txt`
- State JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/incident_triage/RAM26_PRE_FULL_LIVE_TRIAGE_20260507T103035Z/RAM26_pre_full_live_alarm_triage_state.json`

## Required Manual Confirmation

FULL LIVE BUY 허용 전 아래를 명시 확인해야 한다.

1. GPU CRASH 알람이 실제 RAM26 risk/order path에 영향 없는지, 또는 fresh 상태로 복구됐는지
2. equity freshness가 broker/account source 기준으로 정상인지
3. metric_version missing이 필수 contract 위반인지 deprecated rule인지
4. sentinel heartbeat가 watchdog 필수 요소인지, 아니면 deprecated sensor인지
