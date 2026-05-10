# Patch Plan

1. order-intent-executor.mjs - direct broker path를 router publish path로 교체
2. live-trading-kis.mjs - direct execution은 fallback only, telemetry 발행
3. algo-maker-pegger.mjs - policy-driven primary candidate (v2.1 완료)
4. polygon-ws-nbbo.mjs - freshness timestamp 보장
5. docs/runbook - shadow -> canary -> live cutover 정리
