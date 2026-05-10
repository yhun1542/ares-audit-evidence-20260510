# ARES RAM26 — Current Champion Version Briefing

작성 시각 UTC: 2026-05-07T10:52:06Z  
작성 시각 KST: 2026-05-07 19:52:06 KST

## 1. Source-of-Truth Champion

| 항목 | 값 |
|---|---|
| Expected Alpha Champion | `RAM26_ALPHA_0001_b215c119ea23` |
| Previous Champion / Rollback Target | `BASELINE_1ca1b110d384` / `p9B_106` |
| Promotion ID | `RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z` |
| Live Config | `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/live_config/ram26_champion_RAM26_ALPHA_0001_b215c119ea23.json` |
| Rollback Script | `/opt/ares/RAM26_alpha_last_rollback.sh` |

## 2. Latest Runtime Check

| Redis Key | Value |
|---|---|
| `emarkos:v1:mode` | `LIVE` |
| `emarkos:v1:ram26:active_strategy_id` | `RAM26_ALPHA_0001_b215c119ea23` |
| `emarkos:v1:ram26:champion_params_file` | `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/live_config/ram26_champion_RAM26_ALPHA_0001_b215c119ea23.json` |
| `emarkos:v1:ram26:order_routing` | `live` |
| `emarkos:v1:ram26:paper_trading` | `false` |
| `emarkos:v1:ram26:shadow_only` | `false` |
| `emarkos:v1:ram26:dry_run` | `false` |
| `trading:enabled` | `true` |
| `kill-switch:active` | `false` |

## 3. Latest Incident Verdict

```
RAM26_STATE_MISMATCH_FORENSIC_ONLY
```

## 4. Critical Note

CHAMPION_ID를 BASELINE으로 override하여 probe를 통과시키면 안 된다.  
Expected production champion은 `RAM26_ALPHA_0001_b215c119ea23`다.  
`BASELINE_1ca1b110d384`는 이전 챔피언 및 rollback target이다.

## 5. Artifacts

- Report: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/state_mismatch_report.md`
- State JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/state_mismatch_state.json`
- Decision: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/state_mismatch_decision.md`

---

## Latest Runtime Update — 2026-05-07T11:37:15Z

A FULL LIVE GO run was recorded:

`RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z` (verdict: `FULL_LIVE_ALPHA_GO_SUCCESS`)

As of the latest forensic + Alpha-based v17 probe + 15-min drift watch, Redis runtime is aligned with the Alpha Champion:

- `emarkos:v1:mode` = `LIVE`
- `emarkos:v1:ram26:active_strategy_id` = `RAM26_ALPHA_0001_b215c119ea23`
- `emarkos:v1:ram26:champion_params_file` = `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/live_config/ram26_champion_RAM26_ALPHA_0001_b215c119ea23.json`
- `emarkos:v1:ram26:order_routing` = `live`
- `paper_trading` = `false`
- `shadow_only` = `false`
- `dry_run` = `false`
- `trading:enabled` = `true`
- `kill-switch:active` = `false`
- `emarkos:v1:ram26:full_live_go_id` = `RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z`
- `emarkos:v1:ram26:full_live_go_ts_utc` = `2026-05-07T10:38:24Z`

The previous observation of `active_strategy_id = BASELINE_1ca1b110d384` is classified as transient drift (RUN0 partial cutover failure) and was corrected by RUN1. It must not be normalized by overriding probe expectations to Baseline.

See `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_TRANSIENT_DRIFT_RESOLVED_REPORT.md` for the full Transient Drift Resolved report.

