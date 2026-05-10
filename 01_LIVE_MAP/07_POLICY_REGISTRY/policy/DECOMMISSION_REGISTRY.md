# ARES Decommission Registry — 자동 복구 금지 목록

> **목적**: 본 등록부에 기재된 컴포넌트는 **운영자가 의도적으로 영구 제거한 legacy component** 입니다.
> 차후 운영 에이전트(Manus, guardian, self_healer, 또는 사람 운영자)는 본 등록부를 참조하여
> "사라진 프로세스니까 자동 복구해야 한다"는 잘못된 판단을 하지 않아야 합니다.
>
> **버전**: 1.1 (2026-05-07 추가: §1.7 redis_cli_link.sh 등록)
> **작성일**: 2026-05-07 (KST)
> **결정**: 옵션 A1 (옵션 A 회귀, B 중단)
> **사유 분류 키**: `intentionally_removed_legacy_component`

---

## 1. 등록부 — Decommissioned Components

### 1.1 ares-broker-truth-reconciler-v3

| 필드 | 값 |
|---|---|
| `name` | `ares-broker-truth-reconciler-v3` |
| `classification` | **`intentionally_removed_legacy_component`** |
| `removed_at` | 2026-05-07 01:33~01:34 KST (3회 SIGKILL 후 영구 제거) |
| `removed_by` | 운영자 (사용자 의도) |
| `evidence` | (1) `/home/ubuntu/ares_current/structural_patch_20260424/lib/broker_truth_reconciler_v3.mjs` 소스 파일 삭제 (2) `/home/ubuntu/.pm2/dump.pm2`에서 entry 제거 (3) `/home/ubuntu/.pm2/pm2.log`에 3회 연속 `Stopping → SIGKILL` 이력 |
| `restore_policy` | **`manual_explicit_approval_only`** |
| `pm2_resurrect` | **`forbidden`** (dump.pm2에 다시 등록 금지) |
| `ecosystem_autoregister` | **`forbidden`** (ecosystem.master.cjs 또는 generated에 자동 추가 금지) |
| `guardian_critical` | **`forbidden`** (guardian critical list에서 제외) |
| `alert_missing` | **`disabled`** (이 프로세스가 없다는 이유로 alert 발생 금지) |
| `superseded_by` | broker-truth-writer-v2 (단독 publisher로 충분), 추정상 nextgen2-live / v50 라인의 신규 reconciliation flow |
| `last_known_good_md5` | `8f1d08f2...` (P0 배포 직전 백업본, `ops_backups/p0_20260506T161412Z/`에 보존) |
| `redis_keys_no_longer_published` | `truth:broker:total_usd`, `truth:broker:total_usd:ts`, `truth:broker:reconciler:heartbeat`, `truth:broker:reconciler:last_success` |

### 1.2 reconciler-heartbeat-sentinel (P1-3, 우리가 추가했던 것)

| 필드 | 값 |
|---|---|
| `name` | `reconciler-heartbeat-sentinel` |
| `classification` | `removed_after_decommission_of_target` |
| `removed_at` | 2026-05-07 01:34 KST 부근 (reconciler-v3 제거와 동시) |
| `restore_policy` | `forbidden_unless_reconciler_v3_returns` |
| 사유 | 대상 (reconciler-v3) 자체가 제거되어 감시 의의 상실 |

### 1.3 runtime-drift-monitor (P1-4)

| 필드 | 값 |
|---|---|
| `name` | `runtime-drift-monitor` |
| `classification` | `intentionally_removed_legacy_component` |
| `removed_at` | 2026-05-07 01:34 KST 부근 |
| `restore_policy` | `manual_explicit_approval_only` |
| `superseded_by` | 추정: 사용자가 도입한 별도 drift watcher 또는 v50 라인의 통합 모니터링 |

### 1.4 baseline-consistency-sentinel (P2-2)

| 필드 | 값 |
|---|---|
| `name` | `baseline-consistency-sentinel` |
| `classification` | `intentionally_removed_legacy_component` |
| `removed_at` | 2026-05-07 01:34 KST 부근 |
| `restore_policy` | `manual_explicit_approval_only` |
| 비고 | session_start_equity ONE_MISSING과 lifetime_peak DIVERGENCE 1.19%를 매 cycle 정확히 검출했지만, 운영자 정책상 제거됨 |

### 1.5 lifetime-peak-authority-sentinel (P2-3)

| 필드 | 값 |
|---|---|
| `name` | `lifetime-peak-authority-sentinel` |
| `classification` | `intentionally_removed_legacy_component` |
| `removed_at` | 2026-05-07 01:34 KST 부근 |
| `restore_policy` | `manual_explicit_approval_only` |

### 1.6 equity-decomposition-sentinel (P2-4)

| 필드 | 값 |
|---|---|
| `name` | `equity-decomposition-sentinel` |
| `classification` | `intentionally_removed_legacy_component` |
| `removed_at` | 2026-05-07 01:34 KST 부근 |
| `restore_policy` | `manual_explicit_approval_only` |

### 1.7 redis_cli_link.sh (Redis CLI wrapper, v50 마이그레이션 시 폐기)

| 필드 | 값 |
|---|---|
| `name` | `redis_cli_link.sh` |
| `type` | shell wrapper script (file artifact, not a process) |
| `classification` | **`intentionally_removed_legacy_component`** |
| `removed_at` | 2026-04-24 부근 (`#RCA20260424:` 주석 라인 시점), 영구 확정 시점 v50 적용 시 |
| `removed_by` | 운영자 (사용자 의도, v50/nextgen2 마이그레이션의 일부) |
| `evidence` | (1) `find /home/ubuntu -maxdepth 4 -name redis_cli_link.sh` → 0건 (2) `/home/ubuntu/.env` 헤더에 `# user-local env: Redis credentials moved to SSOT /etc/ares/redis.env.` 명시 (3) 모든 legacy `REDIS_*` export가 `#RCA20260424:` 주석으로 비활성화 |
| `superseded_by` | **SSOT `/etc/ares/redis.env`** (root-owned EnvironmentFile). systemd는 `EnvironmentFile=/etc/ares/redis.env`, shell은 `set -a; source /etc/ares/redis.env; set +a; redis-cli -u $REDIS_URL --tls --insecure ...` 패턴 사용 |
| `restore_policy` | **`manual_explicit_approval_only`** (재도입 시 SSOT와 동시 운용 금지 — credential 분기 위험) |
| `pm2_resurrect` | **N/A** (process가 아닌 파일) |
| `ecosystem_autoregister` | **N/A** |
| `guardian_critical` | **N/A** |
| `alert_missing` | **`disabled`** (이 wrapper의 부재를 alert/장애로 취급 금지) |
| `auto_restore_attempts` | **`forbidden`** (어떤 자동화도 wrapper를 다시 만들거나 `.env`의 주석을 해제하지 않음) |
| `runtime_status_2026_05_07` | SSOT 정상 (`PING=PONG` 검증), `ares:equity:total` 등 모든 키 정상 갱신 — wrapper 부재가 거래 영향 없음 |

**호출 통일 패턴** (모든 운영 스크립트·진단 명령에 적용):

```bash
sudo bash -c 'set -a; source /etc/ares/redis.env; set +a; redis-cli -u "$REDIS_URL" --tls --insecure "$@"' _ <args>
```

ACL별 분기 시:

| 용도 | 사용 변수 |
|---|---|
| 일반 admin 작업 | `$REDIS_URL` |
| 거래 모듈 (least privilege) | `$REDIS_URL_TRADER` |
| guard 모듈 | `$REDIS_URL_GUARD` |
| 읽기 전용 진단 | `$REDIS_URL_READONLY` |
| equity writer 전용 | `$REDIS_URL_EQUITY_WRITER` |

---

## 2. Restore Policy 정의

| Policy | 의미 |
|---|---|
| `manual_explicit_approval_only` | 사용자가 명시적으로 "이걸 다시 살려라"라고 지시한 경우에만 복원. 자동 복구 일체 금지. |
| `forbidden_unless_reconciler_v3_returns` | reconciler-v3가 다시 활성화될 때만 동반 복원. 단독 복구 금지. |
| `forbidden` | 어떠한 경우에도 자동 복구 금지. |

---

## 3. 운영 에이전트 행동 규칙

### 3.1 PM2 process가 사라졌다는 alert를 만들 때

```pseudo
if process_name in DECOMMISSION_REGISTRY:
    if registry[process_name].alert_missing == "disabled":
        suppress_alert()
        return
```

### 3.2 dump.pm2 resurrect 시

```pseudo
for entry in old_dump:
    if entry.name in DECOMMISSION_REGISTRY:
        if registry[entry.name].pm2_resurrect == "forbidden":
            skip(entry)
            log("DECOMMISSIONED: skipped resurrect for " + entry.name)
```

### 3.3 ecosystem.cjs 자동 등록 시

```pseudo
for app in ecosystem_template:
    if app.name in DECOMMISSION_REGISTRY:
        if registry[app.name].ecosystem_autoregister == "forbidden":
            remove(app)
            log("DECOMMISSIONED: removed from ecosystem template")
```

### 3.4 guardian critical list

`/home/ubuntu/ops/deploy/pm2_dump_change_watcher.sh` 또는 guardian 모듈은 본 등록부에 등재된 항목을
critical list에서 자동 제외해야 함. 그렇지 않으면 HC v3.0 장애와 동일 패턴(missing 오판 → trade:halt 강제) 재발 위험.

---

## 4. 본 등록부 추가/수정 절차

| 변경 유형 | 승인 권한 | 검증 |
|---|---|---|
| 새 항목 추가 | 운영자 1인 (의도적 제거 후) | 본 파일에 추가 + commit |
| 기존 항목 정책 변경 | 운영자 1인 + 보고서 | A1_FINAL_REPORT.md 업데이트 |
| 항목 제거 (재도입) | 운영자 명시 지시 + 신규 설계 검토 | 새 ecosystem 등록 + 정상 dump 갱신 |

---

## 5. 참고 — HC v3.0 장애 교훈 (이 등록부가 필요한 이유)

> **2026-05-04 직전 HC v3.0 장애**: trade-actuator-supervisor가 systemd로 이관되었는데도
> PM2 critical 목록에 남아 있었고, guardian이 이를 missing으로 오판 →
> `trade:halt=true` 강제 → order-intent-executor SIGINT 루프 발생.
>
> **교훈**: 운영 contract 변경(이관, 제거, 마이그레이션)이 발생하면 모니터링 측도 즉시 동기화해야 함.
> 본 등록부는 이 동기화를 명시적으로 수행하는 장치임.

---

**최종**: 본 등록부는 운영자의 의도를 코드/머신/agent에게 정확히 전달하기 위한 장치이며,
**자동 복구 시도 자체가 위험 행위**임을 명시합니다. 어떤 자동화도 본 등록부를 우회해서 복구하면 안 됩니다.
