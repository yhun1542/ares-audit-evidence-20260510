# RAM26 Transient Champion Drift Resolved Report

## Verdict

```
RAM26_STATE_MISMATCH_TRANSIENT_DRIFT_CORRECTED_BY_FULL_LIVE_GO
```

## Generated

| 항목 | 값 |
|---|---|
| Timestamp UTC | 2026-05-07T11:37:15Z |
| Timestamp KST | 2026-05-07 20:37:15 KST |
| Promotion ID | RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z |
| Expected Alpha Champion | RAM26_ALPHA_0001_b215c119ea23 |
| Previous Champion / Rollback Target | BASELINE_1ca1b110d384 / p9B_106 |

## Incident Timeline

| KST | UTC | Event | active_strategy_id |
|---|---|---|---|
| 19:34:52 | 10:34:52Z | RUN0 `RAM26_ALPHA_FULL_LIVE_GO_20260507T103452Z` started | (BASELINE) |
| 19:35:05 | 10:35:05Z | RUN0 wrote `full_live_go_id=RUN0`; mode=LIVE accepted but **active pointer remained BASELINE** (RUN0 partial failure) | BASELINE |
| 19:37:xx | 10:37:xx | First operator inspection observed mismatch: mode=LIVE + active=BASELINE → reported to user as state mismatch | BASELINE |
| 19:38:11 | 10:38:11Z | RUN1 `RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z` started (operator-initiated retry) | BASELINE |
| 19:38:24 | 10:38:24Z | RUN1 wrote `full_live_go_id=RUN1` and corrected `active_strategy_id` BASELINE → Alpha | RAM26_ALPHA_0001 |
| 19:39:06 | 10:39:06Z | RUN1 verdict: `FULL_LIVE_ALPHA_GO_SUCCESS` (critical_log_hits.txt = 0 bytes) | RAM26_ALPHA_0001 |
| 19:51:34 | 10:51:34Z | Forensic snapshot already shows Alpha aligned (mismatch self-resolved) | RAM26_ALPHA_0001 |
| 19:52:06 | 10:52:06Z | Forensic verdict: `RAM26_STATE_MISMATCH_FORENSIC_ONLY` (no mismatch at runtime, no SAFE freeze, no Alpha repair triggered) | RAM26_ALPHA_0001 |
| 20:22:35 | 11:22:35Z | Alpha-based v17 probe verdict: `V17_MICRO_PATCH_PASS_WITH_GATED_NOTES` (16/16 PASS) | RAM26_ALPHA_0001 |

## Root Cause

The 19:37 KST observation of `active_strategy_id=BASELINE_1ca1b110d384` was a **transient drift window** between two FULL_LIVE_GO executions:

- RUN0 (`RAM26_ALPHA_FULL_LIVE_GO_20260507T103452Z`) successfully set mode=LIVE, order_routing=live, paper/shadow/dry=false, trading=true, kill=false, but failed to overwrite `active_strategy_id` to Alpha; the runtime was left in a partial-cutover state with the previous champion (BASELINE) still indexed as active.
- The operator-initiated RUN1 (`RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z`) detected and corrected this drift, writing `active_strategy_id=RAM26_ALPHA_0001_b215c119ea23` and emitting verdict `FULL_LIVE_ALPHA_GO_SUCCESS`.

There is **no evidence** of:
- Manual rollback execution between RUN0 and RUN1 (shell history empty for relevant commands at that window),
- Self-healer auto-fix overwriting Alpha back to BASELINE (subsequent 30-sample drift watch is stable on Alpha),
- AOF/RDB restore (Redis persistence info nominal),
- Cron/systemd-driven mutation,
- Manual `redis-cli SET emarkos:v1:ram26:active_strategy_id BASELINE_1ca1b110d384` invocation.

Forensic classification flags `auto_writer_suspect` and `rollback_execution_suspect` are codebase-grep false positives because BASELINE_1ca1b110d384 appears in source as the documented previous-champion alias and rollback target, not because any process actually mutated the runtime in that window.

## Current Runtime (verified at 2026-05-07T11:37:15Z)

| Redis Key | Value | Expected | OK |
|---|---|---|---|
| `emarkos:v1:mode` | `LIVE` | `LIVE` | ✅ |
| `emarkos:v1:ram26:active_strategy_id` | `RAM26_ALPHA_0001_b215c119ea23` | `RAM26_ALPHA_0001_b215c119ea23` | ✅ |
| `emarkos:v1:ram26:champion_params_file` | `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/live_config/ram26_champion_RAM26_ALPHA_0001_b215c119ea23.json` | Alpha live_config | ✅ |
| `emarkos:v1:ram26:order_routing` | `live` | `live` | ✅ |
| `emarkos:v1:ram26:paper_trading` | `false` | `false` | ✅ |
| `emarkos:v1:ram26:shadow_only` | `false` | `false` | ✅ |
| `emarkos:v1:ram26:dry_run` | `false` | `false` | ✅ |
| `trading:enabled` | `true` | `true` | ✅ |
| `kill-switch:active` | `false` | `false` | ✅ |
| `emarkos:v1:ram26:live_cutover_id` | `RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z` | `RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z` | ✅ |
| `emarkos:v1:ram26:full_live_go_id` | `RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z` | `RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z` | ✅ |
| `emarkos:v1:ram26:full_live_go_ts_utc` | `2026-05-07T10:38:24Z` | `2026-05-07T10:38:24Z` | ✅ |

## Alpha-Based v17 Probe

```
V17_MICRO_PATCH_PASS_WITH_GATED_NOTES
```

| Stat | Value |
|---|---|
| Total checks | 16 |
| PASS | 16 |
| FAIL | 0 |

GATED notes (acceptable under closed US session at the time of probe):
- v15 lua `replay_intent_atomic_v15.lua` cached lazily (file present, will EVALSHA-load on first replay request)
- `authority:evidence` stream xlen=0 (no triggered evidence emitted yet during current closed-session window)

These two are PASS_WITH_GATED_NOTES because no replay traffic and no evidence emission have been requested under the post-cutover window; both will activate on first qualifying live event.

Probe log: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/v17_alpha_probe_20260507T112234Z.log`

## Drift Watch (30 samples × 30s ≈ 15 minutes)

| Stat | Value |
|---|---|
| Sample count | 30 |
| DRIFT_DETECTED | 0
0 |
| WATCH_COMPLETE marker | 1 |
| Watch log | `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/alpha_drift_watch_20260507T111952Z.log` |

All sampled values were stable on Alpha + LIVE; no recurrence of BASELINE pointer was observed.

## Interpretation

The previous BASELINE observation at 19:37 KST is treated as **transient runtime drift / pre-FULL-LIVE state**.
The later FULL_LIVE_GO RUN1 execution aligned the runtime pointer to the Alpha Champion.
Subsequent 15-minute drift watch confirms no recurring drift.

## Required Follow-up (executed)

1. ✅ Forensic artifacts preserved at `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z`
2. ✅ Alpha-based v17 probe ran (CHAMPION_ID=RAM26_ALPHA_0001_b215c119ea23, mode=LIVE)
3. ✅ Confirmed no recurring drift over 15 minutes (30 samples, 0 DRIFT_DETECTED)
4. ✅ CHAMPION_ID was NOT overridden to Baseline at any point
5. ✅ SAFE freeze was NOT triggered (mismatch had self-resolved before forensic ran)
6. ✅ Alpha repair was NOT triggered (pointer already aligned)
7. ✅ No PM2 mass restart, no nextgen2-live restart, no order tests, no ACL changes

## Final Operating Champion

`RAM26_ALPHA_0001_b215c119ea23`

## Backtest Reference Metrics (RAM26 Alpha Champion)

| Metric | Value |
|---|---:|
| CAGR | 143.55 ~ 143.59% |
| MDD | -9.06% |
| Sharpe | 6.91 |
| WF Min SR | 5.99 |
| Calmar | 15.85 |
| Win Rate | 71.7% |
| Exposure / Leverage | 71.6% |
| Turnover | 0.7162 |
| Cost (bps) | 9.31 |
| OOS Verdict | STRONG_ALPHA_CHAMPION_VALIDATED (FOLDS=10) |

## Artifacts

- This report: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_TRANSIENT_DRIFT_RESOLVED_REPORT.md`
- State JSON: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_TRANSIENT_DRIFT_RESOLVED_STATE.json`
- Forensic decision: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/RAM26_STATE_MISMATCH_20260507T105134Z/state_mismatch_decision.md`
- Drift watch log: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/alpha_drift_watch_20260507T111952Z.log`
- v17 alpha probe log: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/state_mismatch/v17_alpha_probe_20260507T112234Z.log`
- FULL_LIVE_GO RUN1 report: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_go_report.md`
- FULL_LIVE_GO RUN1 runtime state: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_runtime_state.json`
- FULL_LIVE_GO RUN1 redis_before: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_redis_before.txt`
- FULL_LIVE_GO RUN1 redis_after: `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103811Z/RAM26_alpha_full_live_redis_after.txt`
- FULL_LIVE_GO RUN0 (partial failure): `/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/full_live_go/RAM26_ALPHA_FULL_LIVE_GO_20260507T103452Z/RAM26_alpha_full_live_go.log`
- ARES Round 2 v16 audit report: `/home/ubuntu/ARES_Round2_v16_Final_Audit_Report.md` (sandbox)

## Operational Guardrails (Reminder)

- DO NOT override `CHAMPION_ID` to BASELINE_1ca1b110d384 in any future probe or script.
- DO NOT execute SAFE freeze unless `active_strategy_id` drifts again while `mode=LIVE`.
- DO NOT execute Alpha repair if pointer is already on Alpha.
- DO NOT restart `nextgen2-live` for cosmetic reasons.
- DO NOT change `order_routing`.
- DO NOT execute PM2 mass restart.
- DO NOT execute unit cutover.
- DO NOT toggle ACL / default user.
- DO NOT inject test BUY orders.
