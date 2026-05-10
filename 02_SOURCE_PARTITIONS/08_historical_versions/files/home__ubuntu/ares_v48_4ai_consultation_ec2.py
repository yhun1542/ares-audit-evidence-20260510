"""
================================================================================
ARES v48.0 개발을 위한 4대 AI 협의 - EC2 실행용
================================================================================
Claude Opus 4.5, Gemini 3 Pro, GPT-5.2 Pro, Grok 4.1
최대 추론 토큰으로 실행
================================================================================
"""

import os
import requests
import json
from datetime import datetime

# API 키 설정
GROK_API_KEY = "xai-aw5kHcocJqB77k0s23o5sTmIlhwrRiTtb2RrlqnT24KtliXfA5A3XsSeeE7bwisjkWZrhpok0yDCc3xh"
CLAUDE_API_KEY = "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"
GEMINI_API_KEY = "AIzaSyAE_aG8-5t8wViYWXCwpVG2I2YUR6C2b4c"
GPT_API_KEY = "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"

# 현재 최적화 결과
CURRENT_RESULTS = """
## ARES v47.x 엔진 최적화 현황 (2025년 12월 25일)

### 버전별 성능 비교
| 버전 | OOS Sharpe | Bull Sharpe | Neutral Sharpe | Bear Sharpe | MDD |
|------|-----------|-------------|----------------|-------------|-----|
| v47.7 | 1.54 | 5.47 | 2.76 | -1.88 | -16.1% |
| v47.8 | 0.86 | 3.52 | 0.74 | +0.62 | -21.5% |
| v47.10 | 1.31 | 7.69 | 0.52 | 0.00 | -22.2% |
| v47.11 | 1.54 | 7.77 | 0.09 | 0.00 | -24.4% |
| v47.12 (현재 최고) | 1.55 | 9.35 | 1.12 | 0.00 | -18.8% |

### v47.12 현재 전략
1. Multi-Timeframe Factor Ensemble (5일:30%, 20일:50%, 60일:20%)
2. Dynamic Volatility Scaling (RV/IV 비율 기반)
3. Mean Reversion Neutral Boost
4. 엄격한 Bear 레짐 분류 (Bear 일수 최소화)
5. 3단계 손실 제어 (개별 -15%, 포트폴리오 -10%, 3일 연속 손실)

### 핵심 문제점
1. OOS Sharpe가 1.55 수준에서 정체 (목표: 2.0+)
2. Neutral Sharpe가 v47.7 대비 여전히 낮음 (2.76 → 1.12)
3. Bear Sharpe가 0.00 (양수화 필요)

### 데이터
- 2632 거래일 (약 10년)
- 종목 수: 다수
- VIX 데이터 포함
"""

CONSULTATION_PROMPT = """
당신은 세계 최고의 퀀트 투자 전략 전문가입니다. ARES 엔진의 OOS Sharpe를 2.0 이상으로 높이기 위한 혁신적인 전략을 제안해 주세요.

{current_results}

### 핵심 질문
1. OOS Sharpe를 1.55에서 2.0 이상으로 높이기 위한 가장 효과적인 전략은?
2. Neutral Sharpe를 1.12에서 2.0 이상으로 회복시키는 방법은?
3. Bear Sharpe를 0.00에서 양수로 전환하면서 전체 성능을 유지하는 방법은?
4. 새로운 팩터(Quality, Value, Earnings Revision 등) 중 가장 효과적인 것은?
5. 레버리지와 리스크 관리의 최적 균형점은?

### 응답 형식
JSON 형식으로 응답해 주세요:
{{
    "key_insights": ["핵심 인사이트 1", "핵심 인사이트 2", ...],
    "strategy_improvements": [
        {{
            "name": "전략 이름",
            "description": "상세 설명",
            "expected_oos_sharpe_impact": "+0.X",
            "expected_neutral_sharpe_impact": "+0.X",
            "implementation": "구현 방법"
        }}
    ],
    "new_factors": [
        {{
            "name": "팩터 이름",
            "formula": "계산 방법",
            "weight": "권장 가중치",
            "rationale": "이유"
        }}
    ],
    "parameter_recommendations": {{
        "exposure_bull": "권장값과 이유",
        "exposure_neutral": "권장값과 이유",
        "vol_target": "권장값과 이유",
        "top_k": "권장값과 이유",
        "rebalance_freq": "권장값과 이유"
    }},
    "risk_management": {{
        "mdd_target": "목표 MDD",
        "bear_defense": "Bear 방어 전략",
        "leverage_optimization": "레버리지 최적화 방안"
    }},
    "expected_final_metrics": {{
        "oos_sharpe": "예상 OOS Sharpe",
        "neutral_sharpe": "예상 Neutral Sharpe",
        "bear_sharpe": "예상 Bear Sharpe",
        "mdd": "예상 MDD"
    }}
}}
"""


def consult_grok():
    """Grok 4.1 컨설팅 - 최대 추론 토큰"""
    print("\n" + "="*60)
    print("=== Grok 4.1 (grok-4-1-fast-reasoning-latest) 컨설팅 시작 ===")
    print("="*60)
    try:
        response = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROK_API_KEY}"
            },
            json={
                "model": "grok-4-1-fast-reasoning-latest",
                "messages": [
                    {"role": "system", "content": "You are an expert quantitative investment strategist. Always respond in JSON format."},
                    {"role": "user", "content": CONSULTATION_PROMPT.format(current_results=CURRENT_RESULTS)}
                ],
                "max_tokens": 32000,
                "temperature": 0.7
            },
            timeout=300
        )
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content']
            print("✅ Grok 4.1 컨설팅 완료")
            return result
        else:
            error = f"Error {response.status_code}: {response.text[:500]}"
            print(f"❌ Grok 오류: {error}")
            return error
    except Exception as e:
        print(f"❌ Grok 오류: {str(e)}")
        return str(e)


def consult_claude():
    """Claude Opus 4.5 컨설팅 - 최대 추론 토큰"""
    print("\n" + "="*60)
    print("=== Claude Opus 4.5 (claude-opus-4-5-20251101) 컨설팅 시작 ===")
    print("="*60)
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-opus-4-5-20251101",
                "max_tokens": 16000,
                "messages": [
                    {"role": "user", "content": CONSULTATION_PROMPT.format(current_results=CURRENT_RESULTS)}
                ]
            },
            timeout=300
        )
        if response.status_code == 200:
            result = response.json()['content'][0]['text']
            print("✅ Claude Opus 4.5 컨설팅 완료")
            return result
        else:
            error = f"Error {response.status_code}: {response.text[:500]}"
            print(f"❌ Claude 오류: {error}")
            return error
    except Exception as e:
        print(f"❌ Claude 오류: {str(e)}")
        return str(e)


def consult_gemini():
    """Gemini 3 Pro 컨설팅 - 최대 추론 토큰"""
    print("\n" + "="*60)
    print("=== Gemini 3 Pro (gemini-3-pro-preview) 컨설팅 시작 ===")
    print("="*60)
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-preview:generateContent?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": CONSULTATION_PROMPT.format(current_results=CURRENT_RESULTS)}]}],
                "generationConfig": {
                    "maxOutputTokens": 32000,
                    "temperature": 0.7
                }
            },
            timeout=300
        )
        if response.status_code == 200:
            result = response.json()
            text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', 'No response')
            print("✅ Gemini 3 Pro 컨설팅 완료")
            return text
        else:
            error = f"Error {response.status_code}: {response.text[:500]}"
            print(f"❌ Gemini 오류: {error}")
            return error
    except Exception as e:
        print(f"❌ Gemini 오류: {str(e)}")
        return str(e)


def consult_gpt():
    """GPT-5.2 Pro 컨설팅 - responses 엔드포인트, 최대 추론 토큰"""
    print("\n" + "="*60)
    print("=== GPT-5.2 Pro (gpt-5.2-pro) 컨설팅 시작 ===")
    print("="*60)
    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GPT_API_KEY}"
            },
            json={
                "model": "gpt-5.2-pro",
                "input": CONSULTATION_PROMPT.format(current_results=CURRENT_RESULTS)
            },
            timeout=300
        )
        if response.status_code == 200:
            result = response.json()
            # responses 엔드포인트의 응답 형식에 맞게 파싱
            if 'output_text' in result:
                text = result['output_text']
            elif 'output' in result:
                text = result['output'][0]['content'][0]['text'] if isinstance(result['output'], list) else str(result['output'])
            else:
                text = str(result)
            print("✅ GPT-5.2 Pro 컨설팅 완료")
            return text
        else:
            error = f"Error {response.status_code}: {response.text[:500]}"
            print(f"❌ GPT 오류: {error}")
            return error
    except Exception as e:
        print(f"❌ GPT 오류: {str(e)}")
        return str(e)


def main():
    print("="*60)
    print("ARES v48.0 개발을 위한 4대 AI 협의 시작")
    print(f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    results = {}
    
    # 1. Grok 컨설팅
    results['grok_4_1'] = consult_grok()
    
    # 2. Claude 컨설팅
    results['claude_opus_4_5'] = consult_claude()
    
    # 3. Gemini 컨설팅
    results['gemini_3_pro'] = consult_gemini()
    
    # 4. GPT 컨설팅
    results['gpt_5_2_pro'] = consult_gpt()
    
    # 결과 저장
    os.makedirs('/home/ubuntu/ares_v48_results', exist_ok=True)
    output_file = f'/home/ubuntu/ares_v48_results/v48_4ai_consultation_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print("\n" + "="*60)
    print("4대 AI 협의 완료!")
    print(f"완료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"결과 저장: {output_file}")
    print("="*60)
    
    # 각 AI 결과 미리보기
    for ai_name, result in results.items():
        print(f"\n{'='*60}")
        print(f"=== {ai_name} 결과 미리보기 ===")
        print("="*60)
        preview = result[:1000] + "..." if len(result) > 1000 else result
        print(preview)
    
    return results


if __name__ == "__main__":
    main()
