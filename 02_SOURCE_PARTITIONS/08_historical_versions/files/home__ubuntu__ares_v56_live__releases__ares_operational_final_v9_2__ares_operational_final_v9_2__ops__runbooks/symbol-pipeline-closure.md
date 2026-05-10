# Symbol Pipeline Closure Runbook

## 목표
누락 심볼을 0으로 만들거나, 최소한 명시적 quarantine로만 남긴다.

## 근본 원인
- 고정 pivot 연도 의존
- 최근 상장 종목/리브랜딩 종목의 과도한 결측률 판단
- 백필 이후 coverage audit 부재

## 수정 원칙
1. pivot 시작점을 전역 고정연도 대신 `symbol first_available_date` 기준으로 전환
2. `dr >= 0.5` 단일 기준 대신
   - lookback 252 거래일
   - symbol age-adjusted threshold
   - recent liquidity gate
   를 함께 사용
3. 누락 종목은 silent drop 금지. 반드시
   - active
   - quarantined
   - deprecated
   중 하나의 상태를 가진다.

## 실행 순서
1. 누락 심볼 목록 생성
2. Polygon/KIS/master source로 백필
3. age-aware completeness 재계산
4. quarantine 분류
5. preopen gate와 release gate에 반영

## 합격 기준
- missing_symbols = 0
- 또는 quarantine만 존재하며 owner/reason/eta가 전부 기록됨
