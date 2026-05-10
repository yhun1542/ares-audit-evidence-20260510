#!/usr/bin/env python3
"""
ARES v48 4대 AI 컨설팅 - EC2 실행
"""

import os
import json
import time
from datetime import datetime

# API 키
GROK_API_KEY = "xai-aw5kHcocJqB77k0s23o5sTmIlhwrRiTtb2RrlqnT24KtliXfA5A3XsSeeE7bwisjkWZrhpok0yDCc3xh"
CLAUDE_API_KEY = "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"
GEMINI_API_KEY = "AIzaSyAE_aG8-5t8wViYWXCwpVG2I2YUR6C2b4c"
GPT_API_KEY = "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"

# 프롬프트 읽기
with open("/home/ubuntu/ares_v48_4ai_consultation_v3_prompt.md", "r") as f:
    PROMPT = f.read()

SYSTEM_PROMPT = """당신은 세계 최고의 퀀트 트레이딩 전문가입니다. 
20년 이상의 헤지펀드 경험과 학술 연구 경력을 보유하고 있습니다.
제공된 테스트 결과를 철저히 분석하고, 구체적이고 창의적인 해결책을 제시해주세요.
모든 권장사항에는 구체적인 수치, 공식, 코드 예시를 포함해야 합니다.
특히 Crisis 레짐에서 양의 Sharpe를 달성할 수 있는 역발상 전략에 집중해주세요."""

FULL_PROMPT = f"{SYSTEM_PROMPT}\n\n{PROMPT}"


def call_claude():
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        message = client.messages.create(
            model="claude-opus-4-5-20251101",
            max_tokens=16000,
            thinking={"type": "enabled", "budget_tokens": 10000},
            messages=[{"role": "user", "content": FULL_PROMPT}]
        )
        result = {"thinking": "", "response": ""}
        for block in message.content:
            if block.type == "thinking":
                result["thinking"] = block.thinking
            elif block.type == "text":
                result["response"] = block.text
        return result
    except Exception as e:
        return {"error": str(e)}


def call_grok():
    try:
        from openai import OpenAI
        client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")
        response = client.chat.completions.create(
            model="grok-4-1-fast-reasoning-latest",
            messages=[{"role": "user", "content": FULL_PROMPT}],
            max_tokens=16000
        )
        return {"response": response.choices[0].message.content}
    except Exception as e:
        return {"error": str(e)}


def call_gemini():
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model="gemini-3-pro-preview", contents=FULL_PROMPT)
        return {"response": response.text}
    except Exception as e:
        return {"error": str(e)}


def call_gpt():
    try:
        from openai import OpenAI
        client = OpenAI(api_key=GPT_API_KEY)
        response = client.responses.create(model="gpt-5.2-pro", input=FULL_PROMPT)
        if hasattr(response, "output_text"):
            return {"response": response.output_text}
        elif hasattr(response, "output"):
            if isinstance(response.output, list):
                texts = []
                for item in response.output:
                    if hasattr(item, "content"):
                        for c in item.content:
                            if hasattr(c, "text"):
                                texts.append(c.text)
                return {"response": "\n".join(texts)}
            return {"response": str(response.output)}
        return {"response": str(response)}
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=" * 80)
    print("ARES v48 4대 AI 컨설팅 - EC2")
    print(f"시작: {datetime.now()}")
    print("=" * 80)
    
    results_dir = "/home/ubuntu/ares_v48_4ai_ec2_results"
    os.makedirs(results_dir, exist_ok=True)
    results = {}
    
    print("\n[1/4] Claude...")
    start = time.time()
    results["claude"] = call_claude()
    print(f"  완료: {time.time()-start:.1f}s")
    
    print("\n[2/4] Grok...")
    start = time.time()
    results["grok"] = call_grok()
    print(f"  완료: {time.time()-start:.1f}s")
    
    print("\n[3/4] Gemini...")
    start = time.time()
    results["gemini"] = call_gemini()
    print(f"  완료: {time.time()-start:.1f}s")
    
    print("\n[4/4] GPT...")
    start = time.time()
    results["gpt"] = call_gpt()
    print(f"  완료: {time.time()-start:.1f}s")
    
    for name, r in results.items():
        with open(f"{results_dir}/{name}_response.md", "w") as f:
            if "error" in r:
                f.write(f"Error: {r[error]}")
            else:
                if r.get("thinking"):
                    f.write(f"# Thinking\n\n{r[thinking]}\n\n# Response\n\n")
                f.write(r.get("response", "N/A"))
    
    with open(f"{results_dir}/all_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    
    print("\n" + "=" * 80)
    print("=== 요약 ===")
    for name, r in results.items():
        if "error" in r:
            print(f"[{name.upper()}] ❌ {r[error][:100]}...")
        else:
            print(f"[{name.upper()}] ✓ {len(r.get(response,))} 문자")
    print(f"\n결과: {results_dir}")


if __name__ == "__main__":
    main()
