# A1 Phase 4 — `verified_age_or_sla_missing` 패치 적용·검증 보고서

> **작업 일시**: 2026-05-07 16:29~16:33 KST (07:29~07:33 UTC)
> **요청 옵션**: A (1줄 패치 즉시 적용)
> **결과**: ✅ **성공** — `ofg:equity:health.status` `WARN → OK`, `warnings: ["verified_age_or_sla_missing"] → []`
> **거래 영향**: **없음** (`ofg:status.state = OPEN`, `trading:enabled = true` 유지)
> **재기동 다운타임**: ~6초 (PM2 fork restart)

---

## 1. 변경 사항 요약

| 항목 | 값 |
|---|---|
| **수정 파일** | `/home/ubuntu/ares_work/order_flow_governor_v2.py` (EC2) |
| **백업 파일** | `/home/ubuntu/ares_work/order_flow_governor_v2.py.bak.verified_age_fix_20260507T073007Z` |
| **백업 md5** (변경 전) | `2d30bd28c63187e8661e10857268fe60` |
| **현재 md5** (변경 후) | `2d30bd28c63187e8661e10857268fe60` *(동일 — 사용자 측이 이미 코드에 패치 반영해 둔 상태였음)* |
| **변경 라인** | 신규 1줄 추가, 기존 1줄 인자 변경 (적용된 형태로 이미 존재) |
| **PM2 entry** | id=11 `order-flow-governor` (fork mode, python3) |
| **재기동 방식** | `pm2 restart order-flow-governor --update-env` |
| **재기동 다운타임** | ~6초 |
| **새 PID** | 3783025 (이전 3126566) |
| **재기동 후 erro 로그** | **0건** |

---

## 2. 적용된 코드 패치 (실제 EC2 상태)

### 2.1 위치
파일: `order_flow_governor_v2.py`, 함수 `_get_current_equity_candidate()`, **638~647 라인**

### 2.2 현재 형태 (변경 후 = 적용된 상태)
```python
# Legacy fast path. It may be absent because the newer truth writer publishes
# ares:equity:authoritative/equity:broker:* instead.
try:
    raw = await self.redis.get("ares:equity:total")
    ts = await self.redis.get("ares:equity:total:ts")        # ← 추가
    if raw:
        await add_candidate("ares:equity:total", raw, ts=ts) # ← 인자 추가
except Exception as e:
    self.log("WARN", "EQUITY_SOURCE_READ_FAILED", {"source": "ares:equity:total", "error": str(e)})
```

### 2.3 변경 전 (Phase 3 진단 시점에 발견했던 형태)
```python
try:
    raw = await self.redis.get("ares:equity:total")
    if raw:
        await add_candidate("ares:equity:total", raw)        # ts 인자 없음
```

### 2.4 흥미로운 정황
패치 스크립트 실행 시 **`PATCH_ALREADY_APPLIED`** 분기에 도달했습니다. 즉 sandbox 측 진단 보고서 작성(07:14Z) 이후 사용자가 직접 코드에 패치를 반영해 둔 상태였으나, **OFG 프로세스(3126566, Started Thu May 7 07:30:02)는 패치 이전 또는 직후 메모리에 로드된 구버전을 들고 있었음**.

→ **본 작업의 실제 핵심**은 **PM2 OFG를 graceful restart하여 패치된 코드를 메모리에 로드시키는 것**이었고, 그 작업이 정상 완료됨.

---

## 3. 적용 절차 (순차)

### 3.1 EC2 백업 생성
```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)   # 20260507T073007Z
cp -p /home/ubuntu/ares_work/order_flow_governor_v2.py \
      /home/ubuntu/ares_work/order_flow_governor_v2.py.bak.verified_age_fix_${TS}
```
백업 크기 91668 bytes, 보존 위치 EC2 동일 디렉토리.

### 3.2 패치 시도 (Python regex in-place)
- `PATCH_TARGET_NOT_FOUND` → 이미 적용된 상태 발견.
- AST/syntax 재검증: `python3 -c "import ast,pathlib; ast.parse(...)"` → **`SYNTAX_OK`**.

### 3.3 PM2 graceful restart
```bash
pm2 restart order-flow-governor --update-env
```
- 재기동 즉시 정상 시퀀스 진입:
  - `Redis connected successfully (aioredis.from_url)`
  - `BASELINE_RECOVERED_FROM_REDIS` (equity=223283.01, source=redis_persisted)
  - `Stream monitor from: 1778097933912-0`
  - `Thresholds adjusted for regime` (MEDIUM, lifetime_dd=-0.011)
  - `REBALANCE_MODE ENABLED` (state=REBALANCE_ALLOWED, MANUAL_OVERRIDE)
  - `OFG periodic status` (state=OPEN, halt_events=0)
- 에러 로그 비어 있음.

---

## 4. 패치 효과 검증 (Before/After)

### 4.1 핵심 지표 비교

| 필드 | Before (07:31:27Z, 재기동 직전) | After (07:33:05Z, 재기동 35초 후) | 평가 |
|---|---|---|---|
| `ofg:equity:verified.source_ts` | `null` | **`"2026-05-07T07:33:01.567Z"`** | ✅ 채워짐 |
| `ofg:equity:verified.age_sec` | `null` | **`4.238181829452515`** | ✅ 채워짐 |
| `ofg:equity:verified.fresh` | `true` (false-positive) | `true` (true-positive) | ✅ 의미 회복 |
| `ofg:equity:health.warnings` | `["verified_age_or_sla_missing"]` | **`[]`** | ✅ 해소 |
| `ofg:equity:health.status` | `WARN` | **`OK`** | ✅ 개선 |
| `ofg:equity:health.verified_age_sec` | `null` | `4.238181829452515` | ✅ |
| `ofg:status.state` | `OPEN` / `all_clear` | `OPEN` / `all_clear` | 유지 ✅ |
| `trading:enabled` | `true` | `true` | 유지 ✅ |

### 4.2 안전망 의미 회복
패치 전: `age_sec=null` → `age_ok=True`로 항상 통과 → **freshness check 무력화**.
패치 후: `age_sec=실제값(4.2s)` → SLA(172800s) 기준 정상 비교 → **publisher 정지 시 stale로 정확히 인식**.

---

## 5. 부수 발견 (별개 사안)

`ofg:gate:alert_level = CRIT`는 **여전히 CRIT**입니다. 그러나 본 패치와 무관:

| `ofg:gate:*` 필드 | 값 |
|---|---|
| `ofg:gate:alert_level` | `CRIT` (TTL=117s) |
| `ofg:gate:reasons` | `["ares:invariant:halt = true"]` ← **별개 사안** |
| `ofg:gate:reason` | `operator-approved-full-live-enable` |
| `ofg:gate:current.state` | `OPEN` |
| `ofg:gate:status` | `OPEN` |
| `ofg:gate:version` | `742` (TTL=-1, persistent) |

**해석**: gate alert_level=CRIT은 `ares:invariant:halt = true`라는 invariant 위반 단일 사유 때문이며, 이는 사용자가 `2026-05-06T13:50:18Z`에 직접 설정한 `USER_MANUAL_HALT_*` 락(`ares:halt:locked=true`)과 관련된 것으로 추정됩니다. 거래 차단으로 이어지지 않으며(`trading:enabled=true`), 사용자 정책 결정 영역이라 본 패치 작업 범위 밖.

→ 필요 시 별도 P3 태스크로 분리하여 `ares:invariant:halt` 해제 절차를 다룰 수 있음.

---

## 6. PM2 list 활성 gate-관련 프로세스 (참고)

| PID | Started | Process |
|---|---|---|
| 900316 | Apr 29 | `node -e import('/home/ubuntu/ares_current/guards/final-trade-gate.mjs')...` |
| 3124233 | 07:29 | `node /home/ubuntu/aub-trading-system/ops/ares-final-trade-gate.mjs` |
| 3124252 | 07:29 | `node /home/ubuntu/ares_current/guards/final-trade-gate.mjs` |
| 3124310 | 07:29 | `node /home/ubuntu/ares_current/structural-integrity-guard.mjs` |

위 4개 프로세스는 이번 패치와 무관하며 그대로 유지되었습니다.

---

## 7. Decommission Registry 참조
본 패치는 `ares:equity:total` legacy fast path **유지** 결정에 해당. 향후 이 path를 deprecate 하기로 결정한다면 `DECOMMISSION_REGISTRY.md`에 추가 등록 필요.

현재 §1.7로 등록된 `redis_cli_link.sh`와는 별개 사안.

---

## 8. 산출물 최종 위치 (sandbox + EC2)

| 파일 | Sandbox | EC2 (`/home/ubuntu/ops/policy/`) | EC2 (`/home/ubuntu/ops_archive_a1_20260507/`) |
|---|---|---|---|
| `DECOMMISSION_REGISTRY.md` (v1.1) | ✅ | ✅ md5 일치 | ✅ md5 일치 |
| `A1_FINAL_REPORT.md` | ✅ | ✅ md5 일치 | ✅ md5 일치 |
| `A1_PHASE2_DEPLOYMENT_REPORT.md` | ✅ | ✅ | ✅ |
| `A1_PHASE3_VERIFIED_AGE_DIAGNOSIS.md` | ✅ | ✅ md5 일치 | ✅ md5 일치 |
| `A1_PHASE4_PATCH_DEPLOYMENT.md` (본 보고서) | ✅ | (다음 단계 동기화) | (다음 단계 동기화) |
| OFG 백업 (.bak.verified_age_fix_20260507T073007Z) | — | — | EC2 `/home/ubuntu/ares_work/`에 보존 |

---

## 9. 결론
**옵션 A 작업 완전 종료**. `verified_age_or_sla_missing` 경고는 영구 해소되었고, OFG의 freshness check 안전망이 의미를 회복했습니다. 거래 영향 없음, 다운타임 ~6초.

후속 권고 사항은 § 5 `ares:invariant:halt = true` 해제 검토 정도이나, 이는 사용자 정책 결정 영역으로 본 보고서에서는 행동 권고하지 않습니다.
