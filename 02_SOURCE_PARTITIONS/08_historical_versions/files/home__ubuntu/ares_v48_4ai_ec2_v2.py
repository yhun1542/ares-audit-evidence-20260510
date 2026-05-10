#!/usr/bin/env python3
import os, json, time
from datetime import datetime

GROK_API_KEY = "xai-aw5kHcocJqB77k0s23o5sTmIlhwrRiTtb2RrlqnT24KtliXfA5A3XsSeeE7bwisjkWZrhpok0yDCc3xh"
CLAUDE_API_KEY = "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"
GEMINI_API_KEY = "AIzaSyAE_aG8-5t8wViYWXCwpVG2I2YUR6C2b4c"
GPT_API_KEY = "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"

with open("/home/ubuntu/ares_v48_4ai_consultation_v3_prompt.md", "r") as f:
    PROMPT = f.read()

SYSTEM = "당신은 세계 최고의 퀀트 트레이딩 전문가입니다. 20년 이상의 헤지펀드 경험과 학술 연구 경력을 보유하고 있습니다. 제공된 테스트 결과를 철저히 분석하고, 구체적이고 창의적인 해결책을 제시해주세요. 모든 권장사항에는 구체적인 수치, 공식, 코드 예시를 포함해야 합니다. 특히 Crisis 레짐에서 양의 Sharpe를 달성할 수 있는 역발상 전략에 집중해주세요."
FULL = f"{SYSTEM}\n\n{PROMPT}"

def call_claude():
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        msg = client.messages.create(model="claude-opus-4-5-20251101", max_tokens=16000, thinking={"type":"enabled","budget_tokens":10000}, messages=[{"role":"user","content":FULL}])
        r = {"thinking":"", "response":""}
        for b in msg.content:
            if b.type=="thinking": r["thinking"]=b.thinking
            elif b.type=="text": r["response"]=b.text
        return r
    except Exception as e: return {"error":str(e)}

def call_grok():
    try:
        from openai import OpenAI
        c = OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")
        r = c.chat.completions.create(model="grok-4-1-fast-reasoning-latest", messages=[{"role":"user","content":FULL}], max_tokens=16000)
        return {"response": r.choices[0].message.content}
    except Exception as e: return {"error":str(e)}

def call_gemini():
    try:
        from google import genai
        c = genai.Client(api_key=GEMINI_API_KEY)
        r = c.models.generate_content(model="gemini-3-pro-preview", contents=FULL)
        return {"response": r.text}
    except Exception as e: return {"error":str(e)}

def call_gpt():
    try:
        from openai import OpenAI
        c = OpenAI(api_key=GPT_API_KEY)
        r = c.responses.create(model="gpt-5.2-pro", input=FULL)
        if hasattr(r,"output_text"): return {"response":r.output_text}
        elif hasattr(r,"output"):
            if isinstance(r.output,list):
                t=[]
                for i in r.output:
                    if hasattr(i,"content"):
                        for x in i.content:
                            if hasattr(x,"text"): t.append(x.text)
                return {"response":"\n".join(t)}
            return {"response":str(r.output)}
        return {"response":str(r)}
    except Exception as e: return {"error":str(e)}

def main():
    print("="*80)
    print(f"ARES v48 4AI - {datetime.now()}")
    print("="*80)
    d = "/home/ubuntu/ares_v48_4ai_ec2_results_v2"
    os.makedirs(d, exist_ok=True)
    res = {}
    
    for name, fn in [("claude",call_claude),("grok",call_grok),("gemini",call_gemini),("gpt",call_gpt)]:
        print(f"\n[{name}]...")
        s = time.time()
        res[name] = fn()
        print(f"  {time.time()-s:.1f}s")
    
    for n, r in res.items():
        with open(f"{d}/{n}_response.md", "w") as f:
            if "error" in r: f.write(f"Error: {r[error]}")
            else:
                if r.get("thinking"): f.write(f"# Thinking\n\n{r[thinking]}\n\n# Response\n\n")
                f.write(r.get("response","N/A"))
    
    with open(f"{d}/all_results.json", "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False, default=str)
    
    print("\n=== 요약 ===")
    for n, r in res.items():
        if "error" in r: print(f"[{n}] ❌ {r[error][:100]}")
        else: print(f"[{n}] ✓ {len(r.get(response,))} 문자")
    print(f"\n결과: {d}")

if __name__ == "__main__": main()
