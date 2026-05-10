# ARES 운영 무결성 종결 계획

## 1. 목표
ARES를 "배포 가능한 코드" 수준에서 "운영 무결성 확보" 수준으로 끌어올린다.

## 2. 완결 조건 (Definition of Done)
다음 8개가 모두 충족되어야 운영계 종결로 판정한다.

1. KIS 미국주식/미국옵션 주문 경로가 분리 계좌로 정상 동작한다.
2. Redis 연결이 Sentinel-aware 구조로 전환되어 단일 127.0.0.1:6379 의존성이 제거된다.
3. Sentinel 강제 failover 실험에서 ARES 핵심 프로세스가 자동 복구되고, emergency lane이 유지된다.
4. 데이터 파이프라인에서 누락 심볼이 0개이거나, 사유가 명시된 quarantine 상태로만 존재한다.
5. `aoa-live` 재발 방지 테스트가 추가되고 7일 이상 동일 에러 재발이 없다.
6. preopen gate가 symbol coverage, broker health, state-writer lock, baseline restore를 모두 검증한다.
7. 배포 게이트(typecheck/resilience/vendor smoke/kis-option-symbol smoke/redis failover drill/symbol coverage audit)가 전부 PASS다.
8. RTO <= 5분, 주요 데이터 파이프라인 freshness <= 1거래일을 만족한다.

## 3. 현재 잔여 리스크
- 심볼 누락 5개 잔존
- Redis Sentinel은 복구됐지만 클라이언트는 완전한 Sentinel-aware 구조가 아님
- 운영 배포 게이트가 코드 품질 중심으로는 닫혔지만, 인프라/데이터/복구까지 완전히 머신-게이트화되어 있지 않음

## 4. 우선순위
### P0 (즉시)
- Sentinel-aware Redis 클라이언트 도입
- Symbol coverage audit를 preopen/release gate에 편입
- emergency lane failover drill 자동화

### P1 (48시간)
- 누락 심볼 5개 복구 또는 quarantine 명문화
- pivot 기준 고정연도(2005) 의존 제거
- `aoa-live` 회귀 테스트 추가

### P2 (1주)
- 알림/감사 로그 완성
- 운영 대시보드에 다음 6개 지표 추가
  - redis failover count
  - state transition count 5m
  - symbol coverage ratio
  - broker reject ratio
  - preopen red count
  - emergency sell success rate

## 5. 배포/운영 기준
배포는 다음 3단계로만 허용한다.
1. Stage gate PASS
2. Canary 1일 PASS
3. Full cutover

## 6. Canary 종료 기준
- broker reject ratio < 0.5%
- symbol coverage missing = 0 또는 quarantine only
- redis reconnect success = 100%
- emergency lane success = 100%
- unexpected HALT = 0
