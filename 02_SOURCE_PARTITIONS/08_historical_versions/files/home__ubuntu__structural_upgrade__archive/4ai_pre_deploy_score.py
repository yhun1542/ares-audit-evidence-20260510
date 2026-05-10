#!/usr/bin/env python3
"""
4AI Pre-Deploy Scoring — 95/100 minimum required before deployment.
Sends all 5 upgrade modules to 4 AI models for code review and scoring.
"""
import json, os, time, subprocess, sys

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
- This is a LIVE trading system managing real money
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
[Your detailed analysis]

CODE TO REVIEW:
{code_bundle}
"""

def call_claude():
    print("[Claude] Calling claude-opus-4-6...")
    cmd = [
        "curl", "-s", "--max-time", "300",
        "-H", f"x-api-key: {CLAUDE_KEY}",
        "-H", "content-type: application/json",
        "-H", "anthropic-version: 2023-06-01",
        "-d", json.dumps({
            "model": "claude-opus-4-6",
            "max_tokens": 16000,
            "messages": [{"role": "user", "content": PROMPT}]
        }),
        "https://api.anthropic.com/v1/messages"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=360)
    with open(f"{RESULTS_DIR}/claude_score.json", 'w') as f:
        f.write(result.stdout)
    try:
        resp = json.loads(result.stdout)
        text = resp.get("content", [{}])[0].get("text", "")
        with open(f"{RESULTS_DIR}/claude_score.txt", 'w') as f:
            f.write(text)
        print(f"[Claude] Done: {len(text)} chars")
        return text
    except Exception as e:
        print(f"[Claude] Error: {e}")
        return ""

def call_gemini():
    print("[Gemini] Calling gemini-2.5-flash...")
    cmd = [
        "curl", "-s", "--max-time", "120",
        "-H", "Content-Type: application/json",
        "-H", f"X-goog-api-key: {GEMINI_KEY}",
        "-X", "POST",
        "-d", json.dumps({
            "contents": [{"parts": [{"text": PROMPT}]}],
            "generationConfig": {"maxOutputTokens": 16000, "thinkingConfig": {"thinkingBudget": 24576}}
        }),
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    with open(f"{RESULTS_DIR}/gemini_score.json", 'w') as f:
        f.write(result.stdout)
    try:
        resp = json.loads(result.stdout)
        parts = resp.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = ""
        for p in parts:
            if "text" in p:
                text += p["text"]
        with open(f"{RESULTS_DIR}/gemini_score.txt", 'w') as f:
            f.write(text)
        print(f"[Gemini] Done: {len(text)} chars")
        return text
    except Exception as e:
        print(f"[Gemini] Error: {e}")
        return ""

def call_grok():
    print("[Grok] Calling grok-4.20-multi-agent-beta-0309...")
    cmd = [
        "curl", "-s", "--max-time", "300",
        "-H", f"Authorization: Bearer {GROK_KEY}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({
            "model": "grok-4.20-multi-agent-beta-0309",
            "input": PROMPT,
            "reasoning": {"effort": "high"}
        }),
        "https://api.x.ai/v1/responses"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=360)
    with open(f"{RESULTS_DIR}/grok_score.json", 'w') as f:
        f.write(result.stdout)
    try:
        resp = json.loads(result.stdout)
        text = ""
        for item in resp.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")
        with open(f"{RESULTS_DIR}/grok_score.txt", 'w') as f:
            f.write(text)
        print(f"[Grok] Done: {len(text)} chars")
        return text
    except Exception as e:
        print(f"[Grok] Error: {e}")
        return ""

def call_gpt():
    print("[GPT] Calling gpt-5.4-pro...")
    cmd = [
        "curl", "-s", "--max-time", "600",
        "-H", f"Authorization: Bearer {GPT_KEY}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({
            "model": "gpt-5.4-pro",
            "input": PROMPT,
            "reasoning": {"effort": "high"}
        }),
        "https://api.openai.com/v1/responses"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=660)
    with open(f"{RESULTS_DIR}/gpt_score.json", 'w') as f:
        f.write(result.stdout)
    try:
        resp = json.loads(result.stdout)
        text = ""
        for item in resp.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")
        with open(f"{RESULTS_DIR}/gpt_score.txt", 'w') as f:
            f.write(text)
        print(f"[GPT] Done: {len(text)} chars")
        return text
    except Exception as e:
        print(f"[GPT] Error: {e}")
        return ""

# ── Run all 4 AIs ────────────────────────────────────────────────────────
if __name__ == "__main__":
    results = {}
    
    # Run Gemini first (fastest)
    results["gemini"] = call_gemini()
    
    # Then Grok
    results["grok"] = call_grok()
    
    # Then Claude
    results["claude"] = call_claude()
    
    # Then GPT (slowest)
    results["gpt"] = call_gpt()
    
    # ── Extract scores ───────────────────────────────────────────────────
    import re
    summary = []
    for ai, text in results.items():
        score_match = re.search(r'SCORE:\s*(\d+)', text)
        verdict_match = re.search(r'VERDICT:\s*(\w+)', text)
        score = int(score_match.group(1)) if score_match else 0
        verdict = verdict_match.group(1) if verdict_match else "UNKNOWN"
        summary.append({"ai": ai, "score": score, "verdict": verdict})
        print(f"  {ai}: {score}/100 — {verdict}")
    
    avg = sum(s["score"] for s in summary) / max(len(summary), 1)
    print(f"\n{'='*60}")
    print(f"AVERAGE SCORE: {avg:.1f}/100")
    print(f"MINIMUM REQUIRED: 95/100")
    print(f"RESULT: {'✅ PASS — DEPLOY APPROVED' if avg >= 95 else '❌ FAIL — HOLD DEPLOYMENT'}")
    
    with open(f"{RESULTS_DIR}/summary.json", 'w') as f:
        json.dump({"scores": summary, "average": avg, "pass": avg >= 95}, f, indent=2)
    
    print(f"\nResults saved to: {RESULTS_DIR}/")
