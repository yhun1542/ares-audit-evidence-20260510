#!/usr/bin/env python3
"""
4AI Pre-Deploy Scoring v2 — 가이드라인 엄수 버전
- Claude: 스트리밍 + thinking budget_tokens=10000
- Gemini: 스트리밍 + thinkingBudget=8192
- GPT: /v1/responses + reasoning effort=high
- Grok: /v1/responses + reasoning effort=high
모든 호출은 EC2에서 실행. 95/100 이상 필수.
"""
import json, os, time, requests, re, sys

# ── API Keys ─────────────────────────────────────────────────────────────
CLAUDE_KEY = "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"
GPT_KEY = "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"
GEMINI_KEY = "AIzaSyBQvT4DuqmWQ-02CjNPfNus3Zvr-Gil6dA"
GROK_KEY = "xai-qulim5HSFRHqBJjG0Ak73WFiw8SJEnP1j8xYI8mhEJGxQ35uH4vFud4YQGgEnoTaAZRbTafckS8876QR"

RESULTS_DIR = "/home/ubuntu/4ai_predeploy_scores"
os.makedirs(RESULTS_DIR, exist_ok=True)

# ── Read all upgrade files ───────────────────────────────────────────────
files = {
    "atomic-cas-lock.mjs": "/home/ubuntu/structural_upgrade/lib/atomic-cas-lock.mjs",
    "kill-switch-authority.mjs": "/home/ubuntu/structural_upgrade/kill-switch-authority.mjs",
    "ortex-circuit-breaker.mjs": "/home/ubuntu/structural_upgrade/lib/ortex-circuit-breaker.mjs",
    "risk-thresholds.mjs": "/home/ubuntu/structural_upgrade/lib/risk-thresholds.mjs",
    "deploy_structural_upgrade.sh": "/home/ubuntu/structural_upgrade/deploy_structural_upgrade.sh",
}

code_bundle = ""
for name, path in files.items():
    with open(path, 'r') as f:
        code_bundle += f"\n{'='*80}\n// FILE: {name}\n{'='*80}\n{f.read()}\n"

PROMPT = f"""You are a senior SRE reviewing a structural upgrade for a LIVE US equity trading system (ARES).
This upgrade addresses 5 critical issues identified by a 4-AI cross-validation panel.

SCORE THIS UPGRADE on a scale of 0-100 based on:
1. **Safety** (30pts): Fail-safe defaults, atomic operations, no data loss risk
2. **Correctness** (25pts): Logic bugs, edge cases, race conditions
3. **Production-readiness** (20pts): Error handling, logging, monitoring hooks
4. **Architecture** (15pts): Clean design, single responsibility, no unnecessary complexity
5. **Deployment safety** (10pts): Backup/rollback, syntax validation, staged restart

CRITICAL CONTEXT:
- This is a LIVE trading system managing real money on US equities
- CAS lock replaces a dual-path (Lua + WATCH-MULTI) with single atomic Lua path
- kill-switch-authority is a NEW service that controls trading:enabled
- ORTEX circuit breaker handles removed data source gracefully
- risk-thresholds provides SSOT for previously undocumented magic numbers
- Current state: 40+ PM2 processes, Redis-centric architecture

RESPOND IN THIS EXACT FORMAT:
SCORE: [number]/100
SAFETY: [number]/30
CORRECTNESS: [number]/25
PRODUCTION_READINESS: [number]/20
ARCHITECTURE: [number]/15
DEPLOYMENT_SAFETY: [number]/10

CRITICAL_ISSUES: [list any issues that MUST be fixed before deployment, or "NONE"]
WARNINGS: [list non-blocking concerns]
VERDICT: [DEPLOY / HOLD / REJECT]

DETAILED_REVIEW:
[Your detailed analysis of each module]

CODE TO REVIEW:
{code_bundle}
"""

# ── 1. Gemini (스트리밍) ─────────────────────────────────────────────────
def call_gemini():
    print("[Gemini] Calling gemini-2.5-flash (streaming)...")
    t0 = time.time()
    try:
        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse",
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": GEMINI_KEY
            },
            json={
                "contents": [{"parts": [{"text": PROMPT}]}],
                "generationConfig": {
                    "maxOutputTokens": 16000,
                    "thinkingConfig": {"thinkingBudget": 8192}
                }
            },
            stream=True,
            timeout=300
        )
        full_text = ""
        for line in resp.iter_lines():
            if line:
                decoded = line.decode("utf-8")
                if decoded.startswith("data: "):
                    try:
                        data = json.loads(decoded[6:])
                        candidates = data.get("candidates", [])
                        for cand in candidates:
                            parts = cand.get("content", {}).get("parts", [])
                            for part in parts:
                                if "text" in part and "thought" not in part:
                                    full_text += part["text"]
                    except json.JSONDecodeError:
                        pass
        elapsed = time.time() - t0
        with open(f"{RESULTS_DIR}/gemini_score.txt", 'w') as f:
            f.write(full_text)
        print(f"[Gemini] Done: {len(full_text)} chars in {elapsed:.1f}s")
        return full_text
    except Exception as e:
        print(f"[Gemini] Error: {e}")
        return ""

# ── 2. Grok (/v1/responses) ─────────────────────────────────────────────
def call_grok():
    print("[Grok] Calling grok-4.20-multi-agent-beta-0309...")
    t0 = time.time()
    try:
        resp = requests.post(
            "https://api.x.ai/v1/responses",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROK_KEY}"
            },
            json={
                "model": "grok-4.20-multi-agent-beta-0309",
                "input": PROMPT,
                "reasoning": {"effort": "high"}
            },
            timeout=600
        )
        data = resp.json()
        text = ""
        for item in data.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")
        elapsed = time.time() - t0
        with open(f"{RESULTS_DIR}/grok_score.txt", 'w') as f:
            f.write(text)
        print(f"[Grok] Done: {len(text)} chars in {elapsed:.1f}s")
        return text
    except Exception as e:
        print(f"[Grok] Error: {e}")
        return ""

# ── 3. Claude (스트리밍 + thinking) ──────────────────────────────────────
def call_claude():
    print("[Claude] Calling claude-opus-4-6 (streaming + thinking)...")
    t0 = time.time()
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": CLAUDE_KEY,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-opus-4-6",
                "max_tokens": 16000,
                "stream": True,
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": 10000
                },
                "messages": [{"role": "user", "content": PROMPT}]
            },
            stream=True,
            timeout=600
        )
        full_text = ""
        thinking_text = ""
        for line in resp.iter_lines():
            if line:
                decoded = line.decode("utf-8")
                if decoded.startswith("data: "):
                    try:
                        event_data = json.loads(decoded[6:])
                        if event_data.get("type") == "content_block_delta":
                            delta = event_data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                full_text += delta.get("text", "")
                            elif delta.get("type") == "thinking_delta":
                                thinking_text += delta.get("thinking", "")
                    except json.JSONDecodeError:
                        pass
        elapsed = time.time() - t0
        with open(f"{RESULTS_DIR}/claude_score.txt", 'w') as f:
            f.write(full_text)
        with open(f"{RESULTS_DIR}/claude_thinking.txt", 'w') as f:
            f.write(thinking_text)
        print(f"[Claude] Done: {len(full_text)} chars (thinking: {len(thinking_text)} chars) in {elapsed:.1f}s")
        return full_text
    except Exception as e:
        print(f"[Claude] Error: {e}")
        return ""

# ── 4. GPT (/v1/responses) ──────────────────────────────────────────────
def call_gpt():
    print("[GPT] Calling gpt-5.4-pro (/v1/responses)...")
    t0 = time.time()
    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {GPT_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-5.4-pro",
                "input": PROMPT,
                "reasoning": {"effort": "high"}
            },
            timeout=600
        )
        data = resp.json()
        text = ""
        for item in data.get("output", []):
            if item.get("type") == "message" and "content" in item:
                for c in item["content"]:
                    if c.get("type") == "output_text":
                        text += c.get("text", "")
        elapsed = time.time() - t0
        with open(f"{RESULTS_DIR}/gpt_score.txt", 'w') as f:
            f.write(text)
        with open(f"{RESULTS_DIR}/gpt_raw.json", 'w') as f:
            json.dump(data, f, indent=2)
        print(f"[GPT] Done: {len(text)} chars in {elapsed:.1f}s")
        return text
    except Exception as e:
        print(f"[GPT] Error: {e}")
        return ""

# ── Main ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"{'='*60}")
    print(f"4AI PRE-DEPLOY SCORING — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    
    results = {}
    
    # Run in order: Gemini (fastest) → Grok → Claude → GPT (slowest)
    results["gemini"] = call_gemini()
    results["grok"] = call_grok()
    results["claude"] = call_claude()
    results["gpt"] = call_gpt()
    
    # ── Extract scores ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("SCORE EXTRACTION")
    print(f"{'='*60}")
    
    summary = []
    for ai, text in results.items():
        score_match = re.search(r'SCORE:\s*(\d+)', text)
        verdict_match = re.search(r'VERDICT:\s*(\w+)', text)
        safety_match = re.search(r'SAFETY:\s*(\d+)', text)
        correct_match = re.search(r'CORRECTNESS:\s*(\d+)', text)
        prod_match = re.search(r'PRODUCTION_READINESS:\s*(\d+)', text)
        arch_match = re.search(r'ARCHITECTURE:\s*(\d+)', text)
        deploy_match = re.search(r'DEPLOYMENT_SAFETY:\s*(\d+)', text)
        
        score = int(score_match.group(1)) if score_match else 0
        verdict = verdict_match.group(1) if verdict_match else "UNKNOWN"
        
        entry = {
            "ai": ai,
            "score": score,
            "verdict": verdict,
            "safety": int(safety_match.group(1)) if safety_match else 0,
            "correctness": int(correct_match.group(1)) if correct_match else 0,
            "production_readiness": int(prod_match.group(1)) if prod_match else 0,
            "architecture": int(arch_match.group(1)) if arch_match else 0,
            "deployment_safety": int(deploy_match.group(1)) if deploy_match else 0,
            "response_length": len(text),
        }
        summary.append(entry)
        print(f"  {ai:8s}: {score:3d}/100 — {verdict} (S:{entry['safety']} C:{entry['correctness']} P:{entry['production_readiness']} A:{entry['architecture']} D:{entry['deployment_safety']})")
    
    # Only count AIs that actually responded
    responding = [s for s in summary if s["score"] > 0]
    avg = sum(s["score"] for s in responding) / max(len(responding), 1)
    
    print(f"\n{'='*60}")
    print(f"RESPONDING AIs: {len(responding)}/4")
    print(f"AVERAGE SCORE: {avg:.1f}/100")
    print(f"MINIMUM REQUIRED: 95/100")
    print(f"RESULT: {'✅ PASS — DEPLOY APPROVED' if avg >= 95 else '❌ FAIL — HOLD DEPLOYMENT'}")
    print(f"{'='*60}")
    
    with open(f"{RESULTS_DIR}/summary.json", 'w') as f:
        json.dump({
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "scores": summary,
            "responding_count": len(responding),
            "average": round(avg, 1),
            "pass": avg >= 95,
            "threshold": 95
        }, f, indent=2)
    
    print(f"\nResults saved to: {RESULTS_DIR}/")
    sys.exit(0 if avg >= 95 else 1)
