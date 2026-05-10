# OPEN-001 / OPEN-002 진행 계획서 (Read-Only Diagnostic Plan)

> **Version**: 1.0
> **Date**: 2026-05-07 KST
> **Scope**: A1 CLOSED 처리 후 분리된 미결 2건의 **진단 단계 진행 계획**
> **Mode**: **READ-ONLY** (어떤 변경도 수행하지 않음)
> **Companion**: `OPEN_ITEMS_REGISTRY.md` v1.0, `DECOMMISSION_REGISTRY.md` v1.1, `A1_CLOSURE_ACK.md`

---

## 0. 절대 준수 규칙 (전 단계 공통)

본 계획서의 어떤 단계도 다음을 위반할 수 없다. 위반이 필요한 작업은 **사용자 별도 승인 없이는 진행 불가**.

| # | 규칙 | 적용 |
|---|---|---|
| R1 | **수동 gate key 조작 금지** | `ofg:gate:*`, `ares:invariant:*`, `ares:halt:*`, `policy:*`, `trading:*` 키에 대한 모든 SET/DEL/EXPIRE/PUBLISH 금지 |
| R2 | **PM2 프로세스 임의 종료 금지** | `pm2 stop / restart / delete / kill` 금지. `pm2 list / describe / logs --lines N --nostream`만 허용 |
| R3 | **Decommissioned component 자동 복구 금지** | reconciler-v3, sentinel 5종, redis_cli_link.sh 재생성 금지 |
| R4 | **코드 파일 수정 금지** | 본 계획에서는 어떤 `.py / .mjs / .sh / .json / .env` 파일도 수정·삭제·이동 금지 |
| R5 | **Equity-calculator 영역 회피** | 사용자 작업 중인 `equity-calculator` 관련 모든 파일·키 조회 시 read-only 유지, 수정 금지 |
| R6 | **결정은 사용자가** | 모든 단계는 증거 수집 + 옵션 정리에서 멈추고, 실제 변경은 사용자 명시 승인 후 별도 태스크로 분리 |

---

## 1. OPEN-001 진행 계획 (P3-POLICY)

### 1.1 항목 식별

| 필드 | 값 |
|---|---|
| ID | `OPEN-001` |
| Class | **P3-POLICY** |
| Title | `ares:invariant:halt = true` manual lock consistency review |
| Severity | LOW (거래 차단 없음) |
| Owner | 사용자 (정책 결정자) |
| Manus 역할 | 증거 수집 + 옵션 분석 + 결정 지점 정리 |

### 1.2 핵심 의문점

1. `ares:invariant:halt = true`가 **언제·누구에 의해·어떤 의도로** 설정되었는가?
2. 이 키와 `ares:halt:locked`, `policy:trade:halt_reason = USER_MANUAL_HALT_*`의 **일관성**은 유지되고 있는가?
3. invariant가 **alert만 발생시키고 실제 halt는 하지 않는 것**은 의도된 설계인가, 아니면 관측 가능한 contract drift인가?
4. `ofg:gate:alert_level = CRIT`가 **상시 CRIT** 상태로 운영되어도 다른 down-stream consumer가 영향을 받지 않는가?

### 1.3 단계별 실행 계획 (모두 READ-ONLY)

#### Phase 1A — 키 토폴로지 & TTL 매핑 (예상 5분)
**수집 명령** (모두 GET / TTL / TYPE / SCAN, 변경 없음):
```bash
# invariant 계열
redis-cli GET ares:invariant:halt
redis-cli TYPE ares:invariant:halt
redis-cli TTL  ares:invariant:halt
redis-cli OBJECT IDLETIME ares:invariant:halt
redis-cli --scan --pattern 'ares:invariant:*'

# halt lock 계열
redis-cli GET ares:halt:active
redis-cli GET ares:halt:locked
redis-cli GET ares:halt:active:reason
redis-cli GET policy:trade:halt_reason
redis-cli GET policy:unhalt:auto_approve
redis-cli --scan --pattern 'ares:halt:*'
redis-cli --scan --pattern 'policy:*halt*'

# gate 계열 (참조용, 변경 없음)
redis-cli GET ofg:gate:reasons
redis-cli GET ofg:gate:alert_since
redis-cli GET ofg:gate:eval_ts
redis-cli OBJECT IDLETIME ofg:gate:reasons
```

**산출**: `OPEN-001_phase1a_topology.json`

#### Phase 1B — Writer / Reader 코드 식별 (예상 10분)
**수집 명령**:
```bash
# invariant key를 publish하는 코드 검색 (READ-ONLY grep)
grep -rln 'ares:invariant:halt' /home/ubuntu/ares_current/ /home/ubuntu/ares_work/ /home/ubuntu/aub-trading-system/ 2>/dev/null

# 코드의 어느 부분이 set/get을 수행하는지 관찰
grep -rn 'invariant.*halt\|halt.*invariant' /home/ubuntu/ares_current/guards/ 2>/dev/null
grep -rn 'invariant' /home/ubuntu/ares_current/structural-integrity-guard.mjs 2>/dev/null
```

**산출**: `OPEN-001_phase1b_writers.md` (publisher / consumer 표)

#### Phase 1C — 설정 이력 추적 (예상 5분)
**수집 명령**:
```bash
# 5/6 사용자 수동 halt 시점 (USER_MANUAL_HALT_at_2026-05-06T13:50:18Z)
# 그 시각 전후 PM2 logs 검색 (read-only, --nostream)
pm2 logs --lines 100 --nostream 2>&1 | grep -iE 'invariant|manual_halt|USER_MANUAL'
journalctl --since '2026-05-06 13:00' --until '2026-05-06 14:30' 2>&1 | grep -iE 'invariant|halt' | head -50
```

**산출**: `OPEN-001_phase1c_history.md` (timeline 정리)

#### Phase 1D — 결정 지점 정리 (Manus 작성, 사용자 결정)

| 옵션 | 의미 | 작업 (사용자 승인 시) |
|---|---|---|
| **A. 그대로 유지** | 의도된 manual lock + alert noise는 받아들임 | 변경 없음, 본 항목 `ACCEPTED-AS-IS`로 종결 |
| **B. invariant만 unset** | gate alert는 해소되지만 manual halt 의도는 유지 | `redis-cli SET ares:invariant:halt false` (사용자 직접 실행 권장) |
| **C. manual halt 정책 재설계** | `ares:halt:locked` + `invariant`가 일관된 contract를 가지도록 publisher 재설계 | 별도 PR-X 태스크로 분리 |
| **D. 정책 회의 후 결정** | 보류, 추가 정보 수집 | 1주 후 재검토 |

> **Manus는 옵션 B/C/D 중 어느 것도 자동 진행하지 않는다**. Phase 1A~1C 증거 보고 후 사용자 명시 선택을 대기한다.

### 1.4 OPEN-001 진행 진입 조건
- 사용자가 본 계획서를 승인 (전체 또는 OPEN-001만)
- 시장 세션 상태 무관 (READ-ONLY이므로)

---

## 2. OPEN-002 진행 계획 (P2-CONTRACT)

### 2.1 항목 식별

| 필드 | 값 |
|---|---|
| ID | `OPEN-002` |
| Class | **P2-CONTRACT** |
| Title | active gate process root inventory 재검증 |
| Severity | MEDIUM (운영 contract 모호성) |
| Owner | 사용자 (운영 contract 결정자) |
| Manus 역할 | 프로세스 inventory + 책임 분담 매핑 + 중복 writer 식별 |

### 2.2 핵심 의문점

1. `/home/ubuntu/aub-trading-system/ops/ares-final-trade-gate.mjs`(PID 3124233)와 `/home/ubuntu/ares_current/guards/final-trade-gate.mjs`(PID 3124252) **두 프로세스가 동일 키에 동시에 write하는가?**
2. 두 프로세스의 PM2 등록 형태(ecosystem.config / pm2 dump / direct start)는 어떤가?
3. 어떤 것이 **의도된 v50 active**이고 어떤 것이 **legacy ride-along**인가?
4. KSE v2 판단이 가정한 `ares_current` 단일 root 가정과 충돌하는가?

### 2.3 단계별 실행 계획 (모두 READ-ONLY)

#### Phase 2A — 프로세스 / PM2 inventory 수집 (예상 5분)
```bash
# PM2 list 전체 (read-only)
pm2 jlist  > /tmp/open002_pm2_jlist.json
pm2 prettylist > /tmp/open002_pm2_prettylist.txt
pm2 dump  > /tmp/open002_pm2_dump_$(date -u +%Y%m%dT%H%M%SZ).json   # ★ 사용자 승인 필요 (dump 파일 생성)

# 각 gate 프로세스 describe (read-only)
for ID in $(pm2 jlist | jq -r '.[] | select(.name|test("gate|guard|trade")) | .pm_id'); do
  pm2 describe $ID > /tmp/open002_pm2_describe_${ID}.txt
done

# 직접 실행 (PM2 외) gate 프로세스 inventory
ps -eo pid,ppid,lstart,cmd | grep -iE 'final-trade-gate|gate_core|structural-integrity' | grep -v grep

# 각 프로세스 cwd / open files (read-only)
for PID in 3124233 3124252 3124310 900316; do
  echo "=== PID $PID ==="
  ls -la /proc/$PID/cwd 2>/dev/null
  cat /proc/$PID/cmdline 2>/dev/null | tr '\0' ' '
done
```

**산출**: `OPEN-002_phase2a_inventory.json` + `_describe.txt`

> **Note (R2 예외 처리)**: `pm2 dump`는 dump.pm2 파일을 **새로 작성**한다. 이는 R2 위반 가능성이 있으므로 **사용자 명시 승인 후에만 실행**. 대안으로 기존 `~/.pm2/dump.pm2` 읽기만 수행 가능.

#### Phase 2B — Writer 책임 매핑 (예상 15분)
```bash
# 두 final-trade-gate 코드의 writer 키 비교 (read-only grep)
F1=/home/ubuntu/aub-trading-system/ops/ares-final-trade-gate.mjs
F2=/home/ubuntu/ares_current/guards/final-trade-gate.mjs

echo "=== F1 writes ==="
grep -nE 'redis\.(set|publish|hset|expire)' $F1
echo "=== F2 writes ==="
grep -nE 'redis\.(set|publish|hset|expire)' $F2

# diff (구조 비교)
diff <(grep -nE 'redis\.(set|publish|hset|expire)' $F1 | awk '{print $NF}') \
     <(grep -nE 'redis\.(set|publish|hset|expire)' $F2 | awk '{print $NF}')

# 둘 다 trading:enabled 등 핵심 키를 쓰는지
grep -nE 'trading:enabled|trade:halt|ofg:gate:' $F1 $F2
```

**산출**: `OPEN-002_phase2b_writer_matrix.md` (key × file 매트릭스)

#### Phase 2C — 동시 write 충돌 가능성 평가 (예상 10분)
**관찰 방법** (read-only):
```bash
# Redis CLIENT LIST에서 각 프로세스가 활성 연결인지 확인
redis-cli CLIENT LIST | grep -E "addr=.*:[0-9]+" | head -30

# 60초간 키 변동 추적 (samping, 변경 없음)
for i in {1..6}; do
  echo "--- t=${i}0s ---"
  redis-cli GET ofg:gate:current
  redis-cli GET ofg:gate:eval_ts
  redis-cli OBJECT IDLETIME ofg:gate:current
  sleep 10
done > /tmp/open002_phase2c_sampling.log
```

**산출**: `OPEN-002_phase2c_concurrency.md`

#### Phase 2D — 결정 지점 정리

| 옵션 | 의미 | 작업 |
|---|---|---|
| **A. aub-trading-system 측을 active로 확정** | ares_current/guards 측을 deprecate | DECOMMISSION_REGISTRY 추가 등록 + PM2 정식 stop (사용자 승인 후) |
| **B. ares_current/guards 측을 active로 확정** | aub-trading-system/ops 측을 deprecate | 동일 |
| **C. 둘 다 active 유지** | 역할 분담이 다름 (예: shadow vs primary) | 책임 분담 contract 문서화 + 양쪽 유지 |
| **D. 추가 조사 필요** | 정보 부족 | Phase 2B 결과로 재평가 |

> **Manus는 옵션 A/B/C 중 어느 것도 자동 진행하지 않는다**. Phase 2A~2C 증거 보고 후 사용자 명시 선택을 대기한다.

### 2.4 OPEN-002 진행 진입 조건
- 사용자가 본 계획서를 승인 (전체 또는 OPEN-002만)
- 거래 세션 외(off-hours/premarket/postmarket) 진행 권장 (정확한 sampling을 위해)

---

## 3. 진행 순서 권장

| 순서 | 항목 | 사유 |
|---|---|---|
| 1 | **OPEN-002 Phase 2A (inventory)** | 가장 안전(완전 read-only) + 5분 소요 |
| 2 | **OPEN-001 Phase 1A (topology)** | 가장 안전 + 5분 소요 |
| 3 | OPEN-002 Phase 2B (writer 매핑) | grep 만 사용, 변경 없음 |
| 4 | OPEN-001 Phase 1B (writer 식별) | grep 만 사용, 변경 없음 |
| 5 | OPEN-001 Phase 1C (history) | journalctl + pm2 logs read-only |
| 6 | OPEN-002 Phase 2C (concurrency) | 60초 sampling, 변경 없음 |
| **검토** | 사용자 결정 회의 | Manus 보고 후 사용자가 옵션 선택 |
| 7+ | 사용자 선택 옵션에 따른 별도 태스크 분리 | 본 계획서 범위 밖 |

총 예상 진단 소요 시간: **약 50분** (Manus 작업 시간 기준, 사용자 결정 시간 별도)

---

## 4. 산출물 (Phase 1A~2C 완료 시)

| 파일 | 내용 |
|---|---|
| `OPEN-001_phase1a_topology.json` | invariant/halt/policy 키 토폴로지 + TTL |
| `OPEN-001_phase1b_writers.md` | invariant publisher/consumer 매핑 |
| `OPEN-001_phase1c_history.md` | 5/6 manual halt 시점 timeline |
| `OPEN-002_phase2a_inventory.json` | PM2 + ps gate 프로세스 inventory |
| `OPEN-002_phase2b_writer_matrix.md` | 두 final-trade-gate 코드 writer 비교 |
| `OPEN-002_phase2c_concurrency.md` | 60초 sampling 동시 write 충돌 평가 |
| `OPEN_ITEMS_FINAL_REPORT.md` | 통합 보고서 + 사용자 결정 지점 명시 |

위 파일 7종을 sandbox + EC2 양쪽에 동기화 후 사용자에게 옵션 선택 요청.

---

## 5. 위험 및 보호 장치

| 위험 | 보호 장치 |
|---|---|
| 진단 명령이 실수로 SET을 실행할 가능성 | **모든 명령은 GET/TYPE/TTL/SCAN/grep/pm2 list/describe/logs --nostream만 사용** |
| `pm2 dump`가 파일 생성 | **사용자 명시 승인 후에만 실행**. 기본은 기존 `~/.pm2/dump.pm2` 읽기만 |
| journalctl 권한 부족 | sudo 사용, read-only |
| 진단 중 trading 시간 진입 | Phase 2C는 PREMARKET/OFFHOURS에서만 실행 |
| equity-calculator 영역 침범 | grep 시 `equity-calculator` 디렉토리 제외 또는 read-only로만 처리 |

---

## 6. 승인 요청 사항 (사용자 결정)

본 계획서 진행을 위해 다음 중 하나를 선택해 주십시오:

1. **전체 진행 승인** — Phase 1A~2C 모두 진행, 결과 보고서로 통합 제출
2. **OPEN-002만 우선 진행** — Phase 2A~2C만 진행 (gate inventory가 더 시급)
3. **OPEN-001만 우선 진행** — Phase 1A~1C만 진행 (정책 검토 우선)
4. **개별 Phase 단위 진행** — 각 Phase 완료 후 사용자 검토 후 다음 Phase 승인
5. **계획 보류** — 추후 별도 지시 시 진행

> 어떤 선택을 하시더라도 § 0의 R1~R6 절대 준수 규칙은 유지됩니다.

---

## 7. History

| Date | Change | By |
|---|---|---|
| 2026-05-07 | 계획서 v1.0 작성 (A1 CLOSED 직후 사용자 분리 지시 응답) | Manus |
