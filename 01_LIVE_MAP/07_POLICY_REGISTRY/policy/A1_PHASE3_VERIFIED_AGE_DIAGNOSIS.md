# A1 Phase 3 — `verified_age_or_sla_missing` 경고 코드 레벨 진단 보고서

> **작업 일시**: 2026-05-07 KST
> **요청 옵션**: 4 (Decommission Registry 갱신 + 진단 보고, 코드 패치 미수행)
> **거래 영향**: **없음** (`ofg:status.state = OPEN`, `trading:enabled = true` 유지)
> **심각도**: P3 (메타데이터 hygiene, 거래 차단 위험 0)
> **수정 대상 파일**: `/home/ubuntu/ares_work/order_flow_governor_v2.py` (단 한 줄 수정으로 해결 가능)
> **현 상태**: 진단만 수행, 패치는 사용자 승인 후 별도 진행 권고

---

## 1. 경고 발생 메커니즘 (전체 경로)

### 1.1 경고를 만드는 측 — `structural-integrity-guard`
- 파일: `/home/ubuntu/ares_current/structural-integrity-guard.mjs`
- 라인: **732** (push), 727~731 (조건 분기)

```javascript
// 720~732 라인 발췌
summary.verified_age_sec  = finiteNumber(verified.age_sec ?? verified.equity_age_sec);
summary.verified_sla_sec  = finiteNumber(verified.sla_sec ?? verified.max_age_sec ?? verified.freshness_sla_sec);
// ...
if (summary.verified_sla_sec !== null && summary.verified_age_sec !== null && summary.verified_age_sec > summary.verified_sla_sec) {
    summary.warnings.push('verified_age_exceeds_sla');
} else if (summary.verified_sla_sec !== null && summary.verified_age_sec !== null) {
    results.pass(...);
} else {
    summary.warnings.push('verified_age_or_sla_missing');   // ← 여기
    results.warn('G7-OFG-EQUITY', 'ofg:equity:verified missing age_sec or sla_sec', ...);
}
```

조건: `verified.age_sec` 또는 `verified.sla_sec`가 `null`이면 경고. 현재 `age_sec=null`, `sla_sec=172800`이므로 **age_sec 누락 단독 사유**로 trigger.

### 1.2 verified를 publish 하는 측 — 활성 OFG
- **활성 프로세스**: `pid 3126566` (`/home/ubuntu/ares_work/order_flow_governor_v2.py`)
  - 확인 명령: `ps -ef | grep order_flow_governor` 결과 1건 (이 1개만 실행 중)
- 함수: `_publish_verified_equity()` (716~761 라인)
- 호출 시점: `_get_current_equity_candidate()` 결과를 받아 publish

### 1.3 candidate를 만드는 코드 — 누락 발견
**파일**: `/home/ubuntu/ares_work/order_flow_governor_v2.py`
**누락 라인 번호**: **641** 부근

```python
# 638~644 발췌
# Legacy fast path. It may be absent because the newer truth writer publishes
# ares:equity:authoritative/equity:broker:* instead.
try:
    raw = await self.redis.get("ares:equity:total")
    if raw:
        await add_candidate("ares:equity:total", raw)        # ← ts 인자 없음
except Exception as e:
    self.log("WARN", "EQUITY_SOURCE_READ_FAILED", {"source": "ares:equity:total", "error": str(e)})
```

### 1.4 정상으로 처리되는 다른 source들 (비교)

| Source | 라인 | 호출 형태 | 상태 |
|---|---|---|---|
| `ares:equity:total` | 641 | `add_candidate("ares:equity:total", raw)` | ❌ ts 없음 |
| `ares:equity:authoritative` | 657 | `add_candidate(..., ts=auth.get("ts"), health=..., schema=..., contract_warnings=...)` | ✅ |
| `equity:broker:usd` | 665 | `add_candidate("equity:broker:usd", raw, ts=ts)` (ts는 별도 GET `equity:broker:ts`) | ✅ |
| `equity:broker:components` | 672 | `add_candidate(..., ts=(data or {}).get("ts"), ...)` | ✅ |
| `ofg:equity:verified` | 691 | `add_candidate(..., ts=(data or {}).get("source_ts") or (data or {}).get("ts"), ...)` | ✅ |

---

## 2. 데이터 흐름 (왜 verified.age_sec=null이 되는가)

### 2.1 Redis 실측값 (2026-05-07T07:14Z 기준)

| Key | TYPE | 값 (요약) |
|---|---|---|
| `ares:equity:total` | string | `223283.01` (TTL=111s) |
| `ares:equity:total:ts` | string | `2026-05-07T07:14:31.537Z` ✅ **존재함** |
| `ares:equity:authoritative` | string (JSON) | `{"ts":"...","schema":"ares.authoritative_equity.v42","writer":"ares-broker-truth-writer-v2","health":"OK", ...}` |
| `ares:equity:authoritative.health` | string | `OK` |
| `ofg:equity:verified` | string (JSON) | `{"source":"ares:equity:total","source_ts":null,"age_sec":null,"sla_sec":172800,"health":null,"schema":null, ...}` |

**핵심**: `ares:equity:total:ts`가 정상으로 publish되고 있음에도, OFG가 이를 함께 GET 하지 않아 candidate에 누락됨.

### 2.2 candidate 빌드 추적

`add_candidate()` 함수 시그니처 (608행):
```python
async def add_candidate(source, value, ts=None, raw_age=None, degraded=False,
                        health=None, schema=None, contract_warnings=None):
    ...
    age_sec = raw_age if raw_age is not None else self._equity_age_sec(ts)
    sla_sec = int(policy.get("sla_sec", OFG_EQUITY_MAX_AGE_SEC))   # 172800 default
    age_ok = age_sec is None or age_sec <= sla_sec                 # ← age_sec=None일 때 True!
    ...
    "fresh": bool(age_ok) and not candidate_degraded,              # ← True가 됨
    ...
```

`ts=None`일 때:
- `_equity_age_sec(None)` → 즉시 `None` 반환 (함수 584행)
- `age_sec = None`
- `age_ok = True` (age가 None이면 무조건 OK 처리하는 옵션)
- `candidate_degraded = False` (no warnings, no degraded flag)
- **`fresh = True` 로 마킹됨** ← 이게 함정

### 2.3 fresh 후보 우선순위 (730행 근처)

```python
fresh = [c for c in candidates if c["fresh"]]
if fresh:
    chosen = fresh[0]   # ← list 첫 번째 항목 선택
    ...
    return chosen
```

`add_candidate` 호출 순서: ① `ares:equity:total` → ② `ares:equity:authoritative` → ③ `equity:broker:usd` → ④ `equity:broker:components` → ⑤ `ofg:equity:verified`. 

→ **첫 번째인 `ares:equity:total`이 fresh로 잘못 분류되어 항상 chosen 됨**.
→ `ts=None`이므로 publish payload의 `source_ts/age_sec/health/schema`가 모두 `null`.

### 2.4 publish payload 구조 (722행)

```python
payload = {
    "ts": datetime.now(timezone.utc).isoformat(),   # ← OFG가 publish한 시점 (정상)
    "purpose": purpose,
    "total": candidate.get("value"),                # 223283.01 (정상)
    "source": candidate.get("source"),              # "ares:equity:total" (정상)
    "source_ts": candidate.get("source_ts"),        # null ← 문제
    "age_sec": candidate.get("age_sec"),            # null ← 문제 (guard가 검사하는 필드)
    "sla_sec": candidate.get("sla_sec") or policy.get("sla_sec"),  # 172800 (정상)
    "health": candidate.get("health"),              # null ← 문제
    "schema": candidate.get("schema"),              # null ← 문제
    ...
}
```

→ guard가 `verified_age_or_sla_missing` warn 발생 (실제 데이터는 fresh이지만 메타가 비어 있어서).

---

## 3. 영향 분석 (시스템 안정성 평가)

### 3.1 현재 거래 차단 가능성

| 메커니즘 | 발화 조건 | 현재 상태 |
|---|---|---|
| `verified_age_exceeds_sla` halt | `age_sec > sla_sec` | **False** (age_sec=null이라 비교 자체 안 됨) |
| `applyG7EquityPolicy` HALT_ASSERTED | `policy.action === 'HALT_ASSERTED'` | `policy.action = "CLEAR"` (현재) |
| `ofg:status.state` flip to HALT | OFG 자체 판단 (별개 경로) | `state = OPEN` (영향 없음) |

→ **거래 차단 메커니즘 작동 안 함**. 경고는 dashboard/log 레벨에 머무름.

### 3.2 신뢰성 영향

진짜 위험: **`ares:equity:total` publisher가 멈춰도 OFG가 이를 stale로 인식하지 못함** (age_sec=null이면 항상 fresh).

다행히 다음 안전장치가 작동:
1. `ares:equity:total` 자체에 **TTL 120s**가 걸려 있어 publisher 정지 시 `redis.get()`이 `None` 반환 → candidate 미생성
2. `ares:equity:authoritative` (writer-v2)가 동일 데이터를 ts와 함께 publish하므로 fallback 보장
3. `lifetime_peak/dd` 등 다른 경로로도 OFG가 stale을 감지

→ **안전망은 충분**. 단, "OFG의 명목상 freshness check가 무력화된 corner case"는 향후 리팩터에서 정리 권고.

### 3.3 관련 backup 파일 흔적
- `/home/ubuntu/ares_current/structural-integrity-guard.mjs.bak_20260507T074043` — 오늘(05-07) 16:40 KST에 백업 생성됨. 누군가가 바로 직전에 guard 측을 수정한 정황.
- → 사용자께서 진행 중인 `equity-calculator` 작업과 연관 가능. **본 보고서는 어떤 파일도 수정하지 않음**.

---

## 4. 권고 (참고만, 적용은 사용자 결정)

### 4.1 최소 변경안 (1줄 추가)
**파일**: `/home/ubuntu/ares_work/order_flow_governor_v2.py`, **641행**

```python
# Before
try:
    raw = await self.redis.get("ares:equity:total")
    if raw:
        await add_candidate("ares:equity:total", raw)
except Exception as e:
    ...

# After (1줄 추가)
try:
    raw = await self.redis.get("ares:equity:total")
    ts = await self.redis.get("ares:equity:total:ts")        # ← 추가
    if raw:
        await add_candidate("ares:equity:total", raw, ts=ts) # ← 인자 추가
except Exception as e:
    ...
```

### 4.2 적용 시 기대 효과
- `ofg:equity:verified.source_ts` = `ares:equity:total:ts` 값
- `ofg:equity:verified.age_sec` = (현재시각 - source_ts) 초
- guard의 `verified_age_or_sla_missing` 경고 자동 해소
- `ofg:gate:alert_level`이 `CRIT` → `WARN` 또는 `OK`로 회복 (다른 warnings 없으면 OK)
- **거래 로직에는 영향 없음** (이미 정상 동작 중인 OPEN 상태가 그대로 유지)

### 4.3 적용 시 회귀 위험 평가

| 위험 | 평가 |
|---|---|
| age_sec 채워진 후 sla 초과로 갑자기 `verified_age_exceeds_sla` 발생 | 낮음 — `ares:equity:total:ts` 갱신 주기 ~수 초, sla_sec=172800s (48h, OFFHOURS) |
| `ts` GET 실패 시 candidate 자체 누락 | 없음 — `ts=None`이면 기존 동작 그대로 (현재와 동일) |
| `equity:broker:ts` 패턴 변경 영향 | 없음 — 별개 변수 |

→ 안전한 1줄 패치. 다만 **사용자가 `equity-calculator` 수정 중이라면 충돌 우려** 있어 본 보고서는 패치를 적용하지 않음.

### 4.4 대안 (혹은 동시 적용 권장 사항)
- 장기적으로 `ares:equity:total` legacy fast path를 **삭제** 또는 **deprioritize**:
  - 코드 주석에도 "It may be absent because the newer truth writer publishes ares:equity:authoritative/equity:broker:* instead."로 명시
  - `ares:equity:authoritative`(ts/schema/health 모두 정상)를 1순위로 우선화하면 자연스럽게 메타데이터 누락 문제도 사라짐
  - 단, 우선순위 변경은 시스템 거래 결정에 영향 가능 → 별도 변경 윈도우에서 신중히

---

## 5. 작업 산출물 요약

| 항목 | 위치 | 상태 |
|---|---|---|
| Decommission Registry v1.1 (§1.7 redis_cli_link.sh 추가) | Sandbox + EC2 `/home/ubuntu/ops/policy/` + EC2 `/home/ubuntu/ops_archive_a1_20260507/` | md5 `2b98d68572d859d00b5a2056b927f35f` 일치 |
| 본 진단 보고서 | Sandbox `/home/ubuntu/ops_archive_a1_20260507/A1_PHASE3_VERIFIED_AGE_DIAGNOSIS.md` | (EC2 동기화 예정) |
| 시스템 거래 상태 | OPEN / LIVE / armed=true | 변경 없음 |

---

## 6. 다음 단계 (사용자 결정 사항)

**옵션 A**: 본 보고서 검토 후 4.1 1줄 패치를 즉시 적용 (별도 지시 시 진행)
**옵션 B**: `equity-calculator` 사용자 작업 완료 대기 후 패치 적용
**옵션 C**: 패치 적용 보류, 경고 무시 (거래 영향 없으므로 운영상 가능)

본 보고서는 **진단 단계까지만** 수행. 사용자 결정을 기다립니다.
