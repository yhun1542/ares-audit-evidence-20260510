# [운영 공지] ARES 운영 진실 단일화 및 챔피언 고정 완료

금일 기준 ARES 운영 기준선은 아래와 같이 확정되었습니다.

현재 라이브 운영 챔피언은 **E2E3fix_B40S60_F22** 입니다.
R9까지의 bounded verification 결과, confirm_3는 실시간 equity curve 반영 후 **CAGR 88.59%**로 확인되어 운영 챔피언 후보군에서 제외되었고, panic_override는 탐지 자산으로는 유효하지만 승격 게이트를 통과하지 못해 research-only 자산으로 분류되었습니다.

이에 따라 시스템 전반의 단일 운영 진실은 다음과 같이 고정합니다.

| 항목 | 값 | 상태 |
|------|-----|------|
| Live Champion | E2E3fix_B40S60_F22 | LIVE |
| Retired Engine Candidate | confirm_3 | 은퇴 |
| Research-only Asset | panic_override | 보관 |
| Rollback Baseline | E2_04 | 대기 |

이번 정리 작업을 통해 SSOT, Redis, PM2 manifest, GitHub 정렬 상태를 재검증했고, 최종 consistency check는 **9/9 PASS**로 확인되었습니다. 즉, 앞으로 누가 어떤 경로로 확인하더라도 현재 운영 전략이 F22라는 사실은 동일하게 읽혀야 합니다.

---

## PM2 핵심 라이브 프로세스

PM2 기준 핵심 라이브 프로세스는 정상 수집되었습니다.
order-intent-executor, live-trading-kis, algo-maker-pegger, go-nogo-judge, equity-calculator, realtime-data-feed, exposure-monitor은 현재 online 상태로 확인되었습니다. 다만 kill-switch-authority, orchestrator는 PM2 manifest에서 canonical name에 직접 매핑되지 않는 문서 예외(alias/name-mapping exception)로 관리합니다. 이는 서비스 장애가 아니라 명명/수집 규칙 차이로 인한 예외이며, 현재 운영 truth에는 영향을 주지 않습니다.

---

## GitHub 정렬

GitHub 정렬도 다음 원칙으로 고정합니다.
`ARES-KIS-US-AUTOPILOT` 저장소는 운영 truth 반영 브랜치 정렬이 완료된 **authoritative operational repository** 입니다. 반면 `aub-trading-system`은 archived read-only 상태이므로 push 실패는 정상이며, **legacy mirror**로만 취급합니다. 따라서 GitHub truth의 권위 저장소는 `ARES-KIS-US-AUTOPILOT` 하나로 봅니다.

---

## 운영 원칙

마지막으로, 다음 사항을 운영 원칙으로 명확히 합니다.

1. `confirm_3`를 더 이상 라이브 후보처럼 다루지 않습니다.
2. `panic_override`를 라이브 패치처럼 해석하거나 적용하지 않습니다.
3. 운영 판단, 재현 검증, 문서 참조는 SSOT와 current PM2 live manifest 기준으로만 수행합니다.
4. 향후 전략 변경은 별도 검증 라운드와 명시적 승격 절차 없이는 반영하지 않습니다.

---

## 결론

ARES의 현재 운영 진실은 **F22 고정**입니다.
남은 PM2 alias 2건과 archived GitHub 1건은 비차단 문서 예외로 관리하며, 운영 전략 변경 없이 현재 체계를 유지합니다.
