# Current Champion Status (v1.2 — Tonight GO-2 Manual)

**Updated**: `2026-04-20T12:55:00Z`
**Schema**: `v1.2` (governance: `v2_3slot_with_runtime_overlay`)
**Tonight mode**: `B_MAIN_A_OVERLAY` — Manual arming path only

---

## 1. Stable Baseline (Policy / Overlay) — A

| 항목 | 값 |
|---|---|
| **Strategy ID** | `v7.0_LOCKED_20260130` |
| **Engine version** | `v7.0_PRODUCTION-FINAL` |
| **Snapshot path** | `/home/ubuntu/champion_snapshots/v7.0_LOCKED_20260130_000221` |
| **Locked at** | 2026-01-30T00:02:21Z |
| **WF Sharpe (mean)** | **7.166** (재현 7.16, -0.13%) |
| **CAGR / MDD / Sortino / Calmar** | 119.5% / -6.83% / 13.41 / 17.5 |
| **Win rate** | 78.9% (15/19 folds) |
| **Cost / Target vol / Rebal** | 30 bps / 0.16 / 10일 |
| **Object type** | Macro portfolio overlay (asset weights, not stock-level intents) |
| **Tonight 역할** | **Risk discipline / vol-targeting / drawdown reference** (실제 실행은 B 엔진 내부 Stage3+floor22가 담당) |

> A는 **stable baseline policy**로 그대로 보존됩니다. v1.2에서도 stable에서 삭제·교체하지 않습니다. 단 객체 유형이 매크로 portfolio overlay이므로 **tonight runtime trading champion으로 직접 사용하지 않고**, runtime champion(B)에 적용되는 risk discipline reference로만 사용합니다.

---

## 2. Tonight Trading Champion (Runtime) — B

| 항목 | 값 |
|---|---|
| **Strategy ID** | `V65_C6_E2E3fix_B40S60_F22_20260403` |
| **Engine path** | `/home/ubuntu/ares_v56_live/current/E2E3fix_combo_B40S60_floor22_engine.py` |
| **Engine hash** | `d8a415b778c666524a68ea2dd16de6b1d018f53a75ba47a858f15edc47bf89f8` |
| **Engine mtime** | 2026-04-13T16:18:52Z |
| **Promoted at** | 2026-04-03 |
| **Tonight runtime at** | 2026-04-20T12:50:00Z (사용자 명시 결정) |
| **Promotion baseline metrics** | Sharpe **6.233** / CAGR **108.2%** / MDD **-7.29%** / Sortino **9.13** / Calmar **14.83** / Win **68.8%** / Exposure **73.9%** / Avg positions **22.1** |
| **Bootstrap CI (95%)** | [5.743, 6.742] |
| **Floor sweep / 3D grid** | [6.127, 6.290] / [5.967, 6.294] |
| **AI consensus** | 3/4 APPROVED (Grok10, Gemini10, GPT9, Claude6) |
| **Mega validation** | 10T PASS |
| **Reproduced 2026-04-20 (full period)** | SR 5.31 / CAGR 82.9% / MDD -7.99% (LIVE_UNI 53-asset) |
| **Reproduced 2025 / 2026 YTD** | SR 0.70 / -0.13 (성능 약화 신호 → NAV drift -1.5% guard로 보호) |

> B는 **tonight runtime trading champion**으로 명시 추가됩니다. SSOT v1.2의 새 구조(`runtime_trading_champion` slot)에 등록됩니다. autopilot은 건드리지 않으며 manual arming path로만 진입합니다.

### Tonight Decision Constraints (사용자 결정 잠금)

| # | 항목 | 값 |
|---|---|---|
| **D1** | 17~27 legacy 매도 | **허용** |
| **D2** | Cap | **broker buying_power 한도 내** + per_symbol 4.8~5% |
| **D3** | BIL 14.72% 매수 | **허용** |
| **D4** | Anchor + quarantine 해제 | **Manus 위임** |
| 추가 | TWAP slice | 5분 5-slice |
| 추가 | NAV drift guard | **-1.5% → 즉시 SAFE 복귀** |
| 추가 | First session 감독 | **30분** |
| 추가 | Autopilot | **HOLD 유지 (manual path만 사용)** |

---

## 3. Staged Candidate / Research

| Slot | 상태 |
|---|---|
| **Staged candidate** | **없음** (F22가 4/3 promoted 이후 후속 promote 없음) |
| **Candidate research** | **공석 (null)** |
| Crash Fix V2 (UC1~UC5 + 조합) | 8개 실험 전부 promotion gate 실패 (4/3) |
| panic_override | research_only (bounded promotion gate 실패) |
| confirm_3 | retired (R9 bounded verification에서 CAGR 붕괴) |

---

## 4. Quarantine 해석 (중요)

```
champion:live:performance:status         = quarantined
audit:quarantine:performance:2026-04-17T06:21:47.322Z = {
  "status": "quarantined",
  "reason": "anchor_contamination_confirmed_phantom_returns",
  "operator": "manus_p0"
}
```

> **B는 전략 불량으로 quarantine된 것이 아닙니다.**
> Quarantine 사유는 **anchor contamination → phantom returns**로 정리된 **회계/검증 표식**입니다.
> NAV anchor가 오염되어 가짜 수익률이 산출됐고, 그것이 검증되어 manus_p0가 4/17 06:21 UTC에 명시적으로 quarantine을 걸었습니다.
> 따라서 이는 **회계 quarantine**이며 **anchor 재확인 후 해제 가능**한 운영 표식입니다.

### Tonight Pre-flight 시점 해제 계획 (별도 단계)

- 현재 anchor: `champion:equity_anchor:usd = 219826.36`
- 현재 NAV: `$219,818` → **anchor 정확히 일치 (재고정 불필요, 재확인만)**
- Tonight pre-flight (Phase 3)에서 `champion:live:performance:status = active` 전환 + audit `quarantine_revalidated` 항목 발행 예정
- **본 v1.2 docs-only 갱신에서는 status 변경 없음**

---

## 5. Runtime Tag 현황 (참고, write 없음)

```
strategy:active_version       = V65_C6_E2E3fix_B40S60_F22_20260403   (이미 B)
champion:version              = V65_C6_E2E3fix_B40S60_F22_20260403   (이미 B)
champion:engine:base          = E2E3fix_combo_B40S60_floor22_engine.py (이미 B)
ssot:current.engine_version   = E2E3fix_combo_B40S60_floor22_engine.py (이미 B)
ssot:engine:autopilot_unlock_state.phase = HOLD                       (autopilot OFF 유지; manual arming 시에도 동일 키에 manual:true JSON을 SETEX 900s로 주입, autopilot을 켜는 것이 아니라 OIE가 읽는 same wire-format을 수동으로 채우는 것)
trading:enabled               = true                                   (이미 열림)
emarkos:v1:mode               = SAFE                                   (LIVE 전환은 별도 단계)
oie:phase                     = (미사용 키)                          (OIE는 이 키를 읽지 않음 — ssot:engine:autopilot_unlock_state.phase 필드를 읽음)
```

---

## 6. Governance Note

> Runtime trading champion(B)이 stable baseline policy(A)와 다른 것은 **설계상 의도적**입니다.
> A는 SSOT의 역사적 stable baseline (P2 fair comparison winner)이고, B는 운영 검증을 통과한 actual trading champion입니다.
> A를 stable에서 삭제하지 않고, B를 runtime으로 추가하는 v1.2 구조는 두 layer를 모두 보존합니다.

---

## 7. Truth Files (모두 v1.2 갱신 대상)

- `/home/ubuntu/ssot/champion_registry.json` (schema_version v1.2)
- `/home/ubuntu/ssot/live_manifest.json` (live_engine_role = LIVE_CHAMPION)
- `/home/ubuntu/ssot/CHAMPION_STATUS.md` (본 파일)
- `/home/ubuntu/ares_work/live_activation/TONIGHT_LIVE_SCOPE_v1.2.md`

---

_v1.2 patched at 2026-04-20T12:55:00Z by Manus per user explicit decision (TONIGHT_GO2_USER_DECISION_v1.2_DOCS_ONLY). Scope: docs-only. No Redis runtime key writes. autopilot HOLD preserved._
