# RAM26 Production State Mismatch Forensic Report

## Verdict

```
RAM26_STATE_MISMATCH_FORENSIC_ONLY
```

## Run

| 항목 | 값 |
|---|---|
| Run ID | RAM26_STATE_MISMATCH_20260507T105134Z |
| Promotion ID | RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z |
| Expected Alpha Champion | RAM26_ALPHA_0001_b215c119ea23 |
| Baseline / Rollback Target | BASELINE_1ca1b110d384 / p9B_106 |
| Timestamp UTC | 2026-05-07T10:52:06Z |
| Timestamp KST | 2026-05-07 19:52:06 KST |

## Runtime Before

| Key | Value |
|---|---|
| emarkos:v1:mode | LIVE |
| emarkos:v1:ram26:active_strategy_id | RAM26_ALPHA_0001_b215c119ea23 |
| emarkos:v1:ram26:champion_params_file | /home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/live_config/ram26_champion_RAM26_ALPHA_0001_b215c119ea23.json |
| emarkos:v1:ram26:order_routing | live |
| emarkos:v1:ram26:paper_trading | false |
| emarkos:v1:ram26:shadow_only | false |
| emarkos:v1:ram26:dry_run | false |
| trading:enabled | true |
| kill-switch:active | false |

## Runtime After

| Key | Value |
|---|---|
| emarkos:v1:mode | LIVE |
| emarkos:v1:ram26:active_strategy_id | RAM26_ALPHA_0001_b215c119ea23 |
| emarkos:v1:ram26:champion_params_file | /home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/live_config/ram26_champion_RAM26_ALPHA_0001_b215c119ea23.json |
| emarkos:v1:ram26:order_routing | live |
| emarkos:v1:ram26:paper_trading | false |
| emarkos:v1:ram26:shadow_only | false |
| emarkos:v1:ram26:dry_run | false |
| trading:enabled | true |
| kill-switch:active | false |

## Classification

| 항목 | 값 |
|---|---|
| Alpha config OK | true |
| Rollback script exists | true |
| Rollback execution suspect | true |
| Auto writer suspect | true |
| Manual history suspect | false |
| Repair eligible | true |
| Cause summary | auto_writer_or_fallback_logic_suspected |
| Repair performed | false |
| Full LIVE performed | false |
| v17 probe result | not_run |

## Decision

- CHAMPION_ID를 BASELINE으로 override하지 않았다.
- 상태 불일치를 정상으로 인정하지 않았다.
- mismatch가 있을 경우 SAFE freeze를 우선 적용할 수 있도록 했다.
- Alpha 복구는 `ALLOW_ALPHA_REPAIR=YES_REPAIR_ALPHA_POINTER`일 때만 수행된다.
- FULL LIVE 복귀는 `ALLOW_FULL_LIVE=YES_FULL_LIVE_AFTER_CLEAR`일 때만 수행된다.

## Artifacts

- Redis Before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/redis_before.txt`
- Redis After: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/redis_after.txt`
- PM2 Snapshot: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/pm2_snapshot.txt`
- Code Refs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/code_refs.txt`
- Log Refs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/log_refs.txt`
- History Refs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/history_refs.txt`
- System Refs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/system_refs.txt`
- File Refs: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/file_refs.txt`
- State JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/state_mismatch_state.json`
- Repair Actions: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/repair_actions.txt`
- Run Log: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/state_mismatch_run.log`
