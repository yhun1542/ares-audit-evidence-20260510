# Promotion Log

## 2026-04-03 — Promote E2E3fix_B40S60_F22
- live_engine_id: `V65_C6_E2E3fix_B40S60_F22_20260403`
- rollback: `E2_04`
- previous: `R3-06_NTB25`
- baseline: SR=6.233, CAGR=108.2%, MDD=-7.29%, Exposure=73.9%, Positions=22.1
- rationale: 집중 완화 + MDD 개선 + CAGR 소폭 개선 + floor 안정성 검증 완료 균형형 업그레이드
- validation: Mega Validation 10T PASS, 4AI 3/4 APPROVED (Grok10, Gemini10, GPT9, Claude6)

## 2026-04-02 — Promote R3-06_NTB25
- live_engine_id: `V65_C6_R3_06_NTB25_20260402`
- rollback: `V65_C6_TC6x_20260331`
- baseline: SR=4.05, CAGR=50.43%, MDD=-8.18%

## 2026-04-03 Crash Fix V2 Research Round

**판정**: F22 유지 (승격 게이트 통과 후보 없음)
**실행**: 8개 실험 (UC1~UC5 + 조합) × Market/Engine 이중 crash 정의
**핵심 발견**:
- Market Crash(DD>5%)와 Engine Crash(regime=0)의 Jaccard 유사도 = 0.044 (거의 겹치지 않음)
- Market Crash 48일 중 엔진이 CRASH로 인식한 날은 14일(29%), 나머지 56%는 BEAR로 분류
- UC2(Market soft cut)만 Market Crash SR을 -6.042→-5.036으로 개선했으나 flash false-cut 발생으로 게이트 실패
- 외부 wrapper 방식으로는 한계 → 엔진 내부 _regime 함수 수정 필요
**다음 연구 방향**: 엔진 내부 조건부 C5 + Flash/Slow 다변량 분류기 + Market Crash 선행지표 기반 정의
