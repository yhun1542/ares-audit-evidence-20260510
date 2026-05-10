#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4AI Council 종합 자문 호출 스크립트 (EC2용)
- Claude: 스트리밍 방식 (claude-opus-4-5-20251101)
- GPT: curl 방식 (gpt-5.2-pro-2025-12-11)
- Gemini: genai SDK (gemini-3-pro-preview)
- Grok: OpenAI 호환 SDK (grok-4-1-fast-reasoning-latest)
- 모든 AI: 최대 추론 토큰
"""
import os
import json
import subprocess
import time
from pathlib import Path
from datetime import datetime

# API Keys (정확한 값)
ANTHROPIC_API_KEY = "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"
OPENAI_API_KEY = "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"
GEMINI_API_KEY = "AIzaSyAE_aG8-5t8wViYWXCwpVG2I2YUR6C2b4c"
XAI_API_KEY = "xai-aw5kHcocJqB77k0s23o5sTmIlhwrRiTtb2RrlqnT24KtliXfA5A3XsSeeE7bwisjkWZrhpok0yDCc3xh"

# 모델명 (정확한 값)
CLAUDE_MODEL = "claude-opus-4-5-20251101"
GPT_MODEL = "gpt-5.2-pro-2025-12-11"
GEMINI_MODEL = "gemini-3-pro-preview"
GROK_MODEL = "grok-4-1-fast-reasoning-latest"

# 프롬프트
PROMPT_PATH = Path("/home/ubuntu/AUB/4ai_comprehensive_consultation.md")
PROMPT = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else "시스템 고급화 방안에 대해 자문해주세요."

SYSTEM_PROMPT = """당신은 퀀트 트레이딩 시스템 전문가입니다. 
AUB(Autonomous Unified Backtest) 시스템의 고급화 방안에 대해 자문을 제공합니다.
구체적이고 실행 가능한 제안을 해주세요. 
각 제안에는 우선순위(P0=즉시, P1=단기, P2=중기, P3=장기), 예상 효과, 리스크를 포함해주세요.
한국어로 답변해주세요."""

OUTPUT_DIR = Path("/home/ubuntu/AUB/baseline/reports/4ai_council_A/comprehensive_consultation_v2")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def call_claude_streaming():
    """Claude API 호출 (스트리밍 방식, 최대 토큰)"""
    print("[Claude] Streaming call with max tokens...")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        
        full_response = ""
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=16000,  # 최대 토큰
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": PROMPT}]
        ) as stream:
            for text in stream.text_stream:
                full_response += text
                print(text, end="", flush=True)
        
        print("\n")
        return full_response
    except Exception as e:
        return f"[ERROR] Claude: {e}"


def call_gpt_curl():
    """GPT API 호출 (curl 방식, 최대 토큰)"""
    print("[GPT] Curl call with max tokens...")
    try:
        payload = {
            "model": GPT_MODEL,
            "input": f"{SYSTEM_PROMPT}\n\n{PROMPT}",
            "max_output_tokens": 16000  # 최대 토큰
        }
        
        cmd = [
            "curl", "-s",
            "-H", f"Authorization: Bearer {OPENAI_API_KEY}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(payload),
            "https://api.openai.com/v1/responses"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            resp = json.loads(result.stdout)
            # responses API 응답 파싱
            if "output" in resp:
                for item in resp["output"]:
                    if item.get("type") == "message":
                        for content in item.get("content", []):
                            if content.get("type") == "output_text":
                                return content.get("text", "")
            return json.dumps(resp, indent=2, ensure_ascii=False)
        else:
            return f"[ERROR] GPT curl failed: {result.stderr}"
    except Exception as e:
        return f"[ERROR] GPT: {e}"


def call_gemini():
    """Gemini API 호출 (최대 토큰)"""
    print("[Gemini] SDK call with max tokens...")
    try:
        from google import genai
        
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"{SYSTEM_PROMPT}\n\n{PROMPT}",
            config={
                "max_output_tokens": 8192,  # Gemini 최대
                "temperature": 0.7,
            }
        )
        
        return response.text
    except Exception as e:
        return f"[ERROR] Gemini: {e}"


def call_grok():
    """Grok API 호출 (OpenAI 호환, 최대 토큰)"""
    print("[Grok] OpenAI-compatible call with max tokens...")
    try:
        from openai import OpenAI
        
        client = OpenAI(
            api_key=XAI_API_KEY,
            base_url="https://api.x.ai/v1"
        )
        
        response = client.chat.completions.create(
            model=GROK_MODEL,
            max_tokens=16000,  # 최대 토큰
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": PROMPT}
            ]
        )
        
        return response.choices[0].message.content
    except Exception as e:
        return f"[ERROR] Grok: {e}"


def main():
    print(f"[{datetime.now()}] Starting 4AI Council consultation (max tokens)...")
    print(f"Models: Claude={CLAUDE_MODEL}, GPT={GPT_MODEL}, Gemini={GEMINI_MODEL}, Grok={GROK_MODEL}")
    print("="*80)
    
    results = {}
    
    # 1. Claude (스트리밍)
    print("\n[1/4] Calling Claude (streaming)...")
    results["claude"] = call_claude_streaming()
    (OUTPUT_DIR / "claude_response.md").write_text(results["claude"], encoding="utf-8")
    print(f"  -> Claude: {len(results['claude'])} chars saved")
    time.sleep(2)
    
    # 2. GPT (curl)
    print("\n[2/4] Calling GPT (curl)...")
    results["gpt"] = call_gpt_curl()
    (OUTPUT_DIR / "gpt_response.md").write_text(results["gpt"], encoding="utf-8")
    print(f"  -> GPT: {len(results['gpt'])} chars saved")
    time.sleep(2)
    
    # 3. Gemini
    print("\n[3/4] Calling Gemini...")
    results["gemini"] = call_gemini()
    (OUTPUT_DIR / "gemini_response.md").write_text(results["gemini"], encoding="utf-8")
    print(f"  -> Gemini: {len(results['gemini'])} chars saved")
    time.sleep(2)
    
    # 4. Grok
    print("\n[4/4] Calling Grok...")
    results["grok"] = call_grok()
    (OUTPUT_DIR / "grok_response.md").write_text(results["grok"], encoding="utf-8")
    print(f"  -> Grok: {len(results['grok'])} chars saved")
    
    # 요약 저장
    summary = {
        "timestamp": datetime.now().isoformat(),
        "models": {
            "claude": CLAUDE_MODEL,
            "gpt": GPT_MODEL,
            "gemini": GEMINI_MODEL,
            "grok": GROK_MODEL,
        },
        "response_lengths": {k: len(v) for k, v in results.items()},
        "errors": [k for k, v in results.items() if v.startswith("[ERROR]")],
    }
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    
    print("\n" + "="*80)
    print(f"[{datetime.now()}] All responses saved to {OUTPUT_DIR}")
    print(f"Summary: {summary}")
    
    return results


if __name__ == "__main__":
    main()
