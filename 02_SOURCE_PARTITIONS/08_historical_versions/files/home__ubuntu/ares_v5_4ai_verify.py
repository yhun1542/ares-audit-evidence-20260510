#!/usr/bin/env python3
"""
ARES V5.0 4AI 병렬 검증 스크립트
GPT-4.1, Gemini-2.5-Flash, Claude, Grok에게 V5 핵심 로직을 검증 요청
"""

import asyncio
import json
import os
import time
from openai import AsyncOpenAI

# EC2 환경변수에서 API 키 로드
OPENAI_KEY = os.environ.get('OPENAI_API_KEY', '')
ANTHROPIC_KEY = os.environ.get('ANTHROPIC_KEY', '') or os.environ.get('ANTHROPIC_API_KEY', '')
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
XAI_KEY = os.environ.get('XAI_API_KEY', '')

# V5 핵심 로직 요약 (검증 요청용)
V5_LOGIC_SUMMARY = """
ARES V5.0 Aegis Production - 핵심 로직 요약:

1. Risk Lattice (4단계):
   - OPEN: riskScore < 0.42, exposureMultiplier = 1.0
   - OPEN_REDUCED: 0.42 <= riskScore < 0.55, exposureMultiplier = 0.6
   - HOLD_ONLY: 0.55 <= riskScore < 0.72, 신규 매수 차단
   - LIQUIDATE_ONLY: riskScore >= 0.72, 청산만 허용

2. Kinetic Preemption:
   - LLM correction_factor 속도(velocity)가 -0.15/tick 이상이면 즉시 HOLD_ONLY
   - warm-start 후 2틱 이상 경과해야 활성화

3. Trap Detector:
   - 뉴스 감성 양수(+) + 시장 레짐 음수(-) 비대칭 감지 시 차단
   - 조건: llm_factor > 0.3 AND direction_score < -0.15

4. Fluid Exposure:
   - exposureMultiplier = max(0.2, 1.0 - riskScore)
   - OPEN 상태에서도 위험도에 따라 포지션 크기 동적 조정

5. Debounce:
   - 완화(restrictive→relaxing) 전환: 2틱 연속 확인 후 적용
   - 강화(relaxing→restrictive) 전환: 즉시 적용

6. Signal Freshness Guard:
   - 신호 나이 > 5분이면 신뢰도 0으로 처리
   - 신선 신호 < 2개이면 HOLD_PENDING_STABILITY

7. Order Thermostat V5.0:
   - 심볼별 쿨다운: 동일 심볼 재매수 300초 차단
   - 글로벌 쿨다운: 60초 내 5회 이상 주문 차단
   - 일일 손실 한도: -2% 초과 시 신규 매수 차단
   - 일일 회전율 한도: 포트폴리오 대비 500% 초과 시 차단

현재 실시간 상태:
- V5 Active Gate: OPEN (riskScore=0.0, exposureMultiplier=1.0)
- LLM Correction: MAINTAIN (factor=0.0, conf=0.57)
- Signal Integrity: APPROVE (fresh=11/11, trust=1.0)
- GEX 신호: SPY=0.112, QQQ=0.107, AAPL=0.179, MSFT=0.206, NVDA=0.191, TSLA=0.379
- OFI 신호: SPY=0.273, QQQ=-0.576, MSFT=0.513, TSLA=0.297
"""

VERIFY_PROMPT = f"""당신은 퀀트 트레이딩 시스템 전문가입니다.

아래는 ARES V5.0 Aegis Production 실시간 트레이딩 게이트의 핵심 로직과 현재 실시간 상태입니다.

{V5_LOGIC_SUMMARY}

다음 3가지를 검증해주세요:

1. **로직 정합성**: Risk Lattice, Kinetic Preemption, Trap Detector, Fluid Exposure, Debounce 로직에 논리적 오류나 엣지 케이스 취약점이 있는가?

2. **현재 상태 평가**: 현재 실시간 신호(GEX, OFI, LLM)를 기반으로 V5 게이트의 OPEN 판정이 올바른가? 놓친 위험 신호가 있는가?

3. **개선 제안**: V5.0 대비 레짐 인식 정확도와 과잉거래 방지를 더 향상시킬 수 있는 구체적인 개선 방안 3가지를 제시하라.

간결하고 기술적으로 답변하라. 각 항목은 3-5문장으로 제한.
"""

async def query_gpt(client: AsyncOpenAI, model: str, name: str) -> dict:
    start = time.time()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": VERIFY_PROMPT}],
            max_tokens=1000,
            temperature=0.3,
        )
        elapsed = time.time() - start
        return {
            "ai": name,
            "model": model,
            "status": "success",
            "elapsed_sec": round(elapsed, 1),
            "response": resp.choices[0].message.content,
        }
    except Exception as e:
        return {"ai": name, "model": model, "status": "error", "error": str(e)}

async def query_claude(key: str) -> dict:
    if not key:
        return {"ai": "Claude", "status": "error", "error": "ANTHROPIC_KEY not set"}
    start = time.time()
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=key)
        resp = await client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": VERIFY_PROMPT}],
        )
        elapsed = time.time() - start
        return {
            "ai": "Claude",
            "model": "claude-opus-4-5",
            "status": "success",
            "elapsed_sec": round(elapsed, 1),
            "response": resp.content[0].text,
        }
    except Exception as e:
        return {"ai": "Claude", "status": "error", "error": str(e)}

async def run_4ai_verify():
    print("=== ARES V5.0 4AI 병렬 검증 시작 ===\n")
    
    tasks = []
    
    # GPT-4.1-mini (로컬 OpenAI 호환 API)
    local_client = AsyncOpenAI()  # base_url과 key는 환경변수에서 자동 로드
    tasks.append(query_gpt(local_client, "gpt-4.1-mini", "GPT-4.1-mini"))
    
    # Gemini-2.5-Flash (OpenAI 호환 API)
    tasks.append(query_gpt(local_client, "gemini-2.5-flash", "Gemini-2.5-Flash"))
    
    # GPT-4.1 (직접 OpenAI API - 키가 있으면)
    if OPENAI_KEY and 'sk-' in OPENAI_KEY:
        direct_client = AsyncOpenAI(
            api_key=OPENAI_KEY,
            base_url='https://api.openai.com/v1'
        )
        tasks.append(query_gpt(direct_client, "gpt-4.1", "GPT-4.1-Direct"))
    
    # Claude
    tasks.append(query_claude(ANTHROPIC_KEY))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    output = {}
    for r in results:
        if isinstance(r, Exception):
            print(f"Exception: {r}")
            continue
        ai_name = r.get('ai', 'Unknown')
        output[ai_name] = r
        
        print(f"\n{'='*60}")
        print(f"[{ai_name}] ({r.get('model', 'N/A')}) - {r.get('status')} ({r.get('elapsed_sec', '?')}초)")
        print('='*60)
        if r.get('status') == 'success':
            print(r['response'])
        else:
            print(f"오류: {r.get('error')}")
    
    # 결과 저장
    with open('/tmp/ares_v5_4ai_verify_result.json', 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n\n=== 4AI 검증 완료 ===")
    success_count = sum(1 for r in output.values() if r.get('status') == 'success')
    print(f"성공: {success_count}/{len(output)}개 AI 응답")
    
    return output

if __name__ == '__main__':
    asyncio.run(run_4ai_verify())
