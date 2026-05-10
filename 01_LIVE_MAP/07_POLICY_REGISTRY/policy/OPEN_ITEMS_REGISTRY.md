# ARES Open Items Registry — Post A1 Closure

> **Version**: 1.2
> **Created**: 2026-05-07 KST
> **Updated**: 2026-05-07 (v1.2 — OPEN-001 runtime halt naturally cleared; parent item resolved)
> **Created by**: A1 Phase 4 CLOSED 처리 직후 사용자 분리 지시
> **Scope**: A1 작업 종료 시점에 의도적으로 미해결로 남겨둔 후속 운영 contract 정리 항목
> **Companion**: `DECOMMISSION_REGISTRY.md` v1.1 (제거 등록부) ↔ 본 문서 (미결 등록부)
> **Reason for v1.2**: ofg:gate:alert_level=OK, ares:invariant:halt 자연 해소, ofg:status.state=OPEN, trading:enabled=true 상태가 확인되어 OPEN-001 parent item을 RESOLVED — NATURALLY-CLEARED로 transition. Sub-items(OPEN-001-C / OPEN-001-D)와 OPEN-002는 감사용으로 status 정착하여 보존.

---

## 0. 운영 원칙 (전 항목 공통)

1. 본 등록부의 모든 항목은 **즉시 조치 금지** 상태이다.
2. **수동 gate key 조작 금지**, **PM2 임의 종료 금지**.
3. 항목별로 명시된 별도 태스크에서만 진행한다.
4. 항목 변경은 `Status` / `Last-Reviewed-At` 필드를 수정하고 `History` 섹션에 추가한다.
5. v1.1 추가 — 진단으로 false-positive로 확정된 항목은 `RESOLVED — FALSE-POSITIVE` Status로 transition하되 감사용으로 본 등록부에서 절대 삭제하지 않는다.
6. v1.2 추가 — runtime 상태가 자연 회복된 항목은 `RESOLVED — NATURALLY-CLEARED` Status로 transition한다. 이때도 항목 자체와 sub-items는 본 등록부에서 절대 삭제하지 않으며, false-positive/contract drift 기록은 그대로 보존한다.

---

## 1. 미결 항목 등록

### Item OPEN-001 — `ares:invariant:halt` Manual Lock Consistency Review

| 필드 | 값 |
|---|---|
| **ID** | `OPEN-001` |
| **Class** | **P3-POLICY** |
| **Title** | `ares:invariant:halt = true` manual lock consistency review |
| **Status** | **`RESOLVED — NATURALLY-CLEARED`** |
| **Resolved-At** | 2026-05-07 (runtime 자연 회복 확인 후 사용자 옵션 3 결정) |
| **Created** | 2026-05-07 |
| **Last-Reviewed-At** | 2026-05-07 (v1.2 transition) |
| **Severity** | RESOLVED |
| **Trading Impact** | 0건 (전 기간) |

**v1.2 RESOLUTION (NATURALLY-CLEARED) — runtime 상태 (관찰 시각 2026-05-07T10:57Z)**:
- `ofg:gate:alert_level` = **`OK`** (이전 `CRIT` → 자연 회복)
- `ares:invariant:halt` = **empty / expired** (1시간 TTL 만료 후 새 violation 없이 통과)
- `ofg:status.state` = **OPEN**
- `trading:enabled` = **true**, `armed` = **true**, `mode` = **LIVE**
- `current_lifetime_dd` = **-0.011%** (현저한 개선)
- `ofg:equity:health.status` = **OK**, `warnings` = **[]**
- `policy:trade:halt_reason` = **`V42_AUTHORITY:FULL_LIVE:NORMAL`** (정상 정책 라벨)
- 거래 영향 = **0건**
- 자연 회복으로 parent incident closed; 어떤 수동 gate 조작도 수행하지 않음

**관찰된 사실 (v1.0 → v1.1 → v1.2 추적)**:
- v1.0 가설(USER_MANUAL_HALT_2026-05-06 잔존)은 **부정확함**으로 확정 (v1.1)
- v1.1 진짜 원인: **`ares-system-invariant-checker` (pm_id=67) + `ares-invariant-checker` (pm_id=83)** 두 체커가 매 30초마다 자동 평가 → 위반 시 `ares:invariant:halt=1`을 1시간 TTL로 publish
- 당시 violation 3종 동시 활성:
  - **INV-EQ-001 CRITICAL**: `total $223,283.01 vs cash+posMV=$217,045.87` → drift 2.79% ($6,237) — **`equity-calculator` 사용자 작업 영역**
  - **INV-CHAMP-002 HIGH**: false-positive (sub-item OPEN-001-C, 감사용 보존)
  - **INV-POS-001 HIGH**: false-positive (sub-item OPEN-001-D, 감사용 보존)
- v1.2 시점: `ares:invariant:halt`가 빈 값(만료)으로 회복되었고 `alert_level=OK`로 전환됨. INV-EQ-001/INV-CHAMP-002/INV-POS-001 sub-items는 별도 status로 보존.

**금지 사항 (v1.2에서도 영구 유지)**:
- `ares:invariant:halt` **수동 SET/DEL 금지**
- `ares:halt:locked` 자동 unset 금지
- `policy:unhalt:auto_approve` 우회 금지
- `equity-calculator` 영역 회피 (사용자 작업 중)
- `ofg:gate:*` 수동 조작 금지

**보존 의무 (RESOLVED 이후에도 항목 자체 + sub-items는 절대 삭제 금지)**:
- 본 OPEN-001 항목과 OPEN-001-C/OPEN-001-D sub-items는 감사용 영구 보존
- 향후 동일 패턴 재발 시 본 항목의 진단 이력을 즉시 참조 가능

---

### Item OPEN-001-C — INV-CHAMP-002 false-positive (champion engine version mismatch)

| 필드 | 값 |
|---|---|
| **ID** | `OPEN-001-C` (sub-item of OPEN-001) |
| **Class** | **P3-CONTRACT-ALIGNMENT** |
| **Title** | INV-CHAMP-002 invariant 비교 로직이 adapter 파일명과 strategy_name이라는 다른 layer를 비교하여 영구 false-positive |
| **Status** | **`FALSE-POSITIVE CONFIRMED — CONTRACT-ALIGNMENT-PENDING`** |
| **Resolved-At** | 2026-05-07 (false-positive 확정, C-3 패치 미적용 상태로 보존) |
| **Created** | 2026-05-07 |
| **Last-Reviewed-At** | 2026-05-07 (v1.2 transition) |
| **Severity** | LOW (information only, 거래 영향 0) |

**진단 결과 (Phase INV-CHAMP-002 evidence 참조)**:
- `champion:engine:version` = `engine_v65_c6_adapter.py` (**adapter 파일명**)
- `ares:ops:live_engine` = `E2E3fix_combo_B40S60_floor22` (**strategy_name**)
- `truth:champion:version` = `E2E3fix_combo_B40S60_floor22` (SSOT, strategy_name과 일치)
- `truth:champion:official_version` = `V65_C6_E2E3fix_B40S60_F22_20260403`
- `champion:status` = `ACTIVE_LIVE_B_TC6x` (정상)
- 결론: **두 키는 동일 champion(`V65_C6_E2E3fix_B40S60_F22_20260403`, RAM26 ALPHA promotion)을 가리키지만 다른 layer 라벨이 들어있어 영원히 substring 매칭 실패**

**대응 옵션 (사용자 결정 대기)**:
| 옵션 | 내용 | R 위반 |
|---|---|---|
| C-1 | publisher 정렬: `champion:engine:version`을 strategy_name으로 통일 | R4 (publisher 코드 수정) |
| C-2 | invariant logic에 sharedKeywords 추가 (`v65`, `c6`, `e2e3fix`) | R4 (invariant checker 수정) |
| C-3 | invariant compare를 `truth:champion:version` vs `ares:ops:live_engine`로 변경 ★Manus 권고 | R4 (invariant checker 수정) |
| C-4 | 그대로 유지 (DEFER) | 없음 |

**금지 사항**:
- `champion:*` 키 수동 조작 금지
- champion registry 파일 (`/home/ubuntu/ssot/champion_registry.json`) 수정 금지

---

### Item OPEN-001-D — INV-POS-001 false-positive (position sync contract aged)

| 필드 | 값 |
|---|---|
| **ID** | `OPEN-001-D` (sub-item of OPEN-001) |
| **Class** | **P3-CONTRACT-ALIGNMENT** |
| **Title** | INV-POS-001 invariant가 옛 contract 키(`broker:positions:synced_at`)를 가정하지만 position-sync-service v3.1.0은 새 contract(`kis:broker:positions`)로 마이그레이션됨 |
| **Status** | **`FALSE-POSITIVE CONFIRMED — DEFER / D-1 MAINTAINED`** |
| **Resolved-At** | 2026-05-07 (false-positive 확정, D-1 옵션 유지) |
| **Created** | 2026-05-07 |
| **Last-Reviewed-At** | 2026-05-07 (v1.2 transition) |
| **Severity** | LOW (information only, 거래 영향 0) |
| **Operational Note** | position-sync-service v3.1.0 정상 작동, pm_id=19 restart 절대 금지, kis:broker:positions ↔ emarkos:v1:positions 43↔43 matched 유지 중 |

**진단 결과 (OPEN-001-D evidence 참조)**:
- `position-sync-service` (pm_id=19, v3.1.0) **완벽히 정상 작동 중**:
  - uptime 11h 1m, restarts=0, unstable=0
  - **매 60초마다** sync_complete_no_mismatch
  - 마지막 sync (09:29:13Z): `broker_count=43, internal_count=43, matched=43` 100% 일치
  - 새 contract: `kis:broker:positions` ↔ `emarkos:v1:positions`
  - 옛 키 `kis:live:positions`는 1002시간 동안 0 reads로 안전한 deprecated_key_deleted 처리 중
- INV-POS-001 violation의 진짜 원인: **invariant-checker가 옛 키 `broker:positions:synced_at`을 GET하려 함**, 그러나 새 service가 옛 키를 publish하지 않으므로 영원히 stale로 판정
- INV-POS-001 메시지의 `synced_at=2026-04-23T01:09:57.973Z, age_hours=343.5`는 **옛 키의 마지막 publish 시점** (4/23 마이그레이션 시점)

**대응 옵션 (사용자 결정 대기)**:
| 옵션 | 내용 | R 위반 |
|---|---|---|
| D-1 | **그대로 유지 (DEFER) ★Manus 강력 권고** — service는 완벽 동작, restart 무의미 | 없음 |
| D-2 | invariant-checker가 보는 키를 새 contract로 갱신 | R4 |
| D-3 | position-sync-service v3.1.0이 옛 contract 키도 같이 publish (legacy 호환) | R4 |
| D-4 | restart pm_id=19 — **무의미하고 R2 위반, 절대 권장 안 함** | R2 ★불권장 |

**금지 사항**:
- `position-sync-service` (pm_id=19) **kill / restart 금지** (D-4 절대 금지)
- `kis:broker:positions`, `emarkos:v1:positions` 키 수동 조작 금지
- `kis:live:positions` 옛 키 **수동 복구 금지** (deprecated_key_deleted 진행 중)

---

### Item OPEN-002 — Active Gate Process Root Inventory 재검증

| 필드 | 값 |
|---|---|
| **ID** | `OPEN-002` |
| **Class** | **P2-CONTRACT** |
| **Title** | active gate process root inventory 재검증 |
| **Status** | **`RESOLVED — FALSE-POSITIVE (shadow vs primary contract 정상 분리 확인)`** |
| **Resolved-At** | 2026-05-07 (Phase 2A/2B/2C 진단 완료, 사용자 옵션 B 승인) |
| **Created** | 2026-05-07 |
| **Severity** | RESOLVED |
| **Trading Impact** | 없음 (현재 gate state OPEN 유지) |

**진단 결과 (Phase 2A/2B/2C evidence 참조)**:

| pm_id | name | script | Role | Writer 키 prefix |
|---|---|---|---|---|
| **1** | `ares-final-trade-gate` | `aub-trading-system/ops/ares-final-trade-gate.mjs` | **SHADOW probe** (4605 bytes, 116줄) | `ops:final_tradeable:shadow:ares:*` 만 |
| **2** | `final-trade-gate` | `ares_current/guards/final-trade-gate.mjs` | **PRIMARY** (37706 bytes, 835줄) | `ofg:gate:*`, `final:tradeable:*`, `ares:final_trade_gate:*` |
| **47** | `ares-final-trade-gate-v50-active` | `ares_current/guards/lib/ares_v50/final_trade_gate_v50_runner.mjs` | v50 active runner (lib delegation only) | redis writes 0건 |
| **48** | `ares-final-trade-gate-v50-shadow` | 같은 파일, shadow 모드 | v50 shadow runner (lib delegation only) | redis writes 0건 |

**충돌 검증 (Phase 2C 60초 sampling, 13 samples)**:
- shadow writer: `ops:final_tradeable:shadow:ares:*` 5초 간격 갱신 ✅
- primary writer: `ofg:gate:*` 15초 간격 갱신 ✅
- **prefix 완전 분리, 동시 write 0건, 충돌 0건**

**해석**:
- aub-trading-system 측은 `ares_current/lib/ares_final_gate_lib.mjs`를 import하여 평가하지만, 결과를 자기만의 격리된 `ops:final_tradeable:shadow:ares:*` prefix에만 mirror
- v50 runner 47/48은 redis write가 없으므로 prefix 충돌 자체가 불가능
- 기존 KSE v2의 "동일 키 동시 write 충돌" 가설은 **사실이 아닌 것으로 확정**

**RESOLUTION (사용자 옵션 B 승인)**:
- aub-trading-system shadow probe는 **의도된 회귀 안전장치**로 영구 보존
- Decommission Registry로 이관하지 않음 (active component)
- 본 항목 Status는 `RESOLVED — FALSE-POSITIVE`로 transition, 향후 참조용으로 등록부에 영구 보존

**보존 의무**:
- `aub-trading-system/ops/ares-final-trade-gate.mjs` **kill / pm2 stop / pm2 delete 절대 금지**
- shadow prefix `ops:final_tradeable:shadow:ares:*` 수동 삭제 금지
- v50 runner 47/48 절대 종료 금지

---

## 2. 항목 추가/변경 절차

| 작업 | 절차 |
|---|---|
| 신규 항목 추가 | OPEN-NNN ID 부여 → 본 문서 § 1에 추가 → md5 갱신 → EC2 동기화 |
| Sub-item 추가 | OPEN-NNN-X ID 부여 (parent의 sub로 명시) → 본 문서 § 1에 추가 |
| Status 변경 | 항목 내 `Status` + `Last-Reviewed-At` 수정 → § 3 History에 1줄 추가 → md5 갱신 → EC2 동기화 |
| 항목 종결 (해소/이관) | Status를 `RESOLVED` / `RESOLVED — FALSE-POSITIVE` / `MIGRATED-TO-DECOMMISSION` 으로 변경, 본 등록부에서 직접 삭제 금지 (감사용 보존) |

---

## 3. History

| Date | Item | Change | Reviewed-By |
|---|---|---|---|
| 2026-05-07 | OPEN-001 | Created (deferred from A1 Phase 4 CLOSED) | user-instruction |
| 2026-05-07 | OPEN-002 | Created (deferred from A1 Phase 4 CLOSED) | user-instruction |
| 2026-05-07 | OPEN-001 | Status: DEFERRED → IN-DIAGNOSIS (Phase 1A/1B/1C 진단 완료, root cause: invariant-checker auto evaluation, 3 active violations) | manus-diagnosis |
| 2026-05-07 | OPEN-001-C | Created (sub-item, INV-CHAMP-002 false-positive: adapter ↔ strategy_name layer mismatch) | manus-diagnosis |
| 2026-05-07 | OPEN-001-D | Created (sub-item, INV-POS-001 false-positive: invariant이 옛 contract 키 가정, position-sync v3.1.0은 새 contract로 정상 작동 중) | manus-diagnosis |
| 2026-05-07 | OPEN-002 | **Status: DEFERRED → RESOLVED — FALSE-POSITIVE** (Phase 2A/2B/2C 진단 + 사용자 옵션 B 승인, shadow vs primary contract 정상 분리 확정) | user-decision-B |
| 2026-05-07 | OPEN-001 | **Status: IN-DIAGNOSIS → RESOLVED — NATURALLY-CLEARED** (runtime halt and gate alert naturally cleared with no trading impact; runtime snapshot at 10:57Z confirms ofg:gate:alert_level=OK, ares:invariant:halt=empty, ofg:status.state=OPEN, trading:enabled=true) | user-decision-option-3 |
| 2026-05-07 | OPEN-001-C | **Status: IN-DIAGNOSIS → FALSE-POSITIVE CONFIRMED — CONTRACT-ALIGNMENT-PENDING** (감사용 보존, C-3 패치 미적용 상태로 결정 기록만) | v1.2-transition |
| 2026-05-07 | OPEN-001-D | **Status: IN-DIAGNOSIS → FALSE-POSITIVE CONFIRMED — DEFER / D-1 MAINTAINED** (position-sync-service v3.1.0 정상, restart 금지) | v1.2-transition |
| 2026-05-07 | CLOSURE_v2 | **Official A1 closure v2 generated and synced to ops/policy + archive** (`A1_CLOSURE_v2.md`) | user-decision-option-2 |

---

## 4. 산출물 위치

| 위치 | 경로 |
|---|---|
| Sandbox | `/home/ubuntu/ops_archive_a1_20260507/OPEN_ITEMS_REGISTRY.md` |
| EC2 운영 정책 | `/home/ubuntu/ops/policy/OPEN_ITEMS_REGISTRY.md` |
| EC2 archive | `/home/ubuntu/ops_archive_a1_20260507/OPEN_ITEMS_REGISTRY.md` |
| Evidence (sandbox + EC2) | `/home/ubuntu/ops_archive_a1_20260507/open_items_evidence/` |

본 등록부는 **`DECOMMISSION_REGISTRY.md`(제거 등록부)와 짝**을 이루어 동일 디렉토리에 보존된다.

---

## 5. v1.2 최종 상태 요약 (모든 항목 transition 완료)

| 항목 | Status | Resolution |
|---|---|---|
| **OPEN-001** | **RESOLVED — NATURALLY-CLEARED** | runtime 자연 회복 + 사용자 옵션 3 결정 |
| **OPEN-001-C (INV-CHAMP-002)** | **FALSE-POSITIVE CONFIRMED — CONTRACT-ALIGNMENT-PENDING** | 감사용 보존 (C-3 패치 미적용) |
| **OPEN-001-D (INV-POS-001)** | **FALSE-POSITIVE CONFIRMED — DEFER / D-1 MAINTAINED** | service 정상, 옵션 D-1 유지 |
| **OPEN-002** | **RESOLVED — FALSE-POSITIVE** | shadow vs primary contract 정상 분리 |

**v1.2 핵심 원칙**:
- runtime 상태가 자연 회복되었으므로 parent OPEN-001은 닫는다.
- 하지만 false-positive 원인과 contract drift 기록은 절대 삭제하지 않는다.
- 등록부 항목은 status transition만 수행하며, 항목 자체는 감사용으로 영구 보존한다.
- 어떤 runtime 변경(redis gate key, PM2 process, position-sync-service, champion key, trading mode, equity-calculator, live order path)도 수행하지 않았다.

---

**End of Document**
