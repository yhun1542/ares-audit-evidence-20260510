#!/usr/bin/env python3
"""
Claude 종합 질문 (5개 주제) - 스트리밍
EC2에서 실행
"""

import anthropic

ANTHROPIC_API_KEY = "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"

QUESTION = """퀀트 트레이딩 레짐 시스템 개선을 위해 5가지 주제에 대해 구체적으로 답변해주세요.

**현재 문제점:**
- missed_recovery: 91.3% (회복 기회 90% 이상 놓침)
- on_ratio: 4.1% (거의 항상 OFF 상태)
- off_ratio: 49.2% (시간의 절반이 OFF)
- max_off_streak: 192분 (3시간 이상 연속 OFF)

---

## 질문 1: 동적 임계값 설계
시장 변동성 레짐(저/중/고)에 따라 파라미터가 자동 조정되는 시스템을 설계해주세요.
- 레짐 분류 기준 (VIX 수준, 백분위 등)
- 각 레짐별 shock/recovery 임계값
- 레짐 전환 시 스무딩 방법

## 질문 2: 비대칭 쿨다운 & 포지션 스케일링
- OFF→ON 복귀 시 쿨다운과 ON→OFF 진입 시 쿨다운을 다르게 설정하는 방안
- ON/OFF 이진 대신 100%→70%→40%→10%→0% 단계적 포지션 조정 방안
- 각 단계별 조건과 전환 로직

## 질문 3: 목적함수 재설계
현재 Top 5 설정이 모두 동일한 objective(29.14)를 가짐 → 해상도 부족
- missed_recovery 페널티 가중치
- on_ratio 보너스 가중치
- whipsaw vs opportunity cost 트레이드오프 반영 방법
- 구체적인 목적함수 수식 제안

## 질문 4: 강제 복귀 규칙 (Override Rules)
장시간 OFF 상태 지속 시 자동 복귀하는 안전장치
- 시간 기반 강제 복귀 조건 (예: 120분 초과 시)
- 시장 지표 기반 강제 복귀 조건 (예: VIX < 15)
- 부분 복귀 vs 전체 복귀 전략

## 질문 5: 멀티 타임프레임 신호 통합
1분/5분/15분/1시간 데이터를 조합한 신호 생성
- 각 타임프레임별 가중치
- 신호 충돌 시 우선순위
- 노이즈 필터링 방법

---

반드시 아래 JSON 형식으로 답변해주세요:
```json
{
  "dynamic_thresholds": {
    "regime_classification": {"method": "", "low_vol": "", "high_vol": ""},
    "low_vol": {"shock_vix_z": 0, "rec_vix_z": 0, "cooldown": 0},
    "normal": {"shock_vix_z": 0, "rec_vix_z": 0, "cooldown": 0},
    "high_vol": {"shock_vix_z": 0, "rec_vix_z": 0, "cooldown": 0},
    "smoothing": ""
  },
  "asymmetric_cooldown": {
    "off_to_on_cooldown": 0,
    "on_to_off_cooldown": 0,
    "reasoning": ""
  },
  "position_scaling": {
    "levels": [{"position": 100, "condition": ""}, {"position": 0, "condition": ""}],
    "transition_speed": ""
  },
  "objective_function": {
    "formula": "",
    "missed_recovery_penalty": 0,
    "on_ratio_bonus": 0,
    "whipsaw_penalty": 0
  },
  "override_rules": {
    "time_based": {"max_off_minutes": 0, "action": ""},
    "indicator_based": {"vix_threshold": 0, "spread_percentile": 0},
    "partial_vs_full": ""
  },
  "multi_timeframe": {
    "weights": {"1min": 0, "5min": 0, "15min": 0, "1hour": 0},
    "conflict_priority": "",
    "noise_filter": ""
  },
  "expected_results": {
    "missed_recovery": "",
    "on_ratio": "",
    "key_tradeoffs": ""
  }
}
```"""

print("=" * 60)
print("Claude 종합 질문 (5개 주제) - 스트리밍")
print("=" * 60)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

with client.messages.stream(
    model="claude-opus-4-5-20251101",
    max_tokens=16384,
    messages=[
        {"role": "user", "content": QUESTION}
    ]
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)

print("\n")
print("=" * 60)
print("Claude 완료")
print("=" * 60)
