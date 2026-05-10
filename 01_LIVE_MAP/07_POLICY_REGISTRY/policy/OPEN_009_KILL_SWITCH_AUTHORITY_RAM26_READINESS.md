# OPEN-009 — kill-switch-authority overrides RAM26 safety freeze

| Field | Value |
|---|---|
| ID | OPEN-009 |
| Class | P0/P1 POLICY-AUTHORITY |
| Title | kill-switch-authority sets trading:enabled=true without RAM26 readiness G8 |
| Status | ACTIVE — G8 GUARD DEPLOYED / POLICY PATCH REQUIRED |
| Created | 2026-05-09 |
| Severity | P0/P1 |
| Trading Impact | No order impact while mode=SAFE + ops halt + manual_order_submission_enabled=false, but policy mismatch is critical |

## Evidence

Redis MONITOR captured kill-switch-authority PID 425115 calling Lua script every ~1s and setting:

```text
SET trading:enabled true
reason=verdict-GO-from-poll
```

The authority reads `trading:enabled:override`, but its G1-G7 checks still attest the OLD E2E3fix engine and do not include RAM26 runtime readiness.

## Required G8 Readiness

1. active_strategy_id == RAM26_ALPHA_0001_b215c119ea23
2. ram26:features:v1:meta.status == PUBLISHED
3. eligible_ratio >= 0.60
4. ram26:engine:gate:last.pass == true
5. ares:bridge:active == ram26_final_to_champion_bridge_v2
6. ssot:target:v2:current.source == ram26_engine_bridge_v2
7. policy:champion:active.issued_by == ram26_final_to_champion_bridge_v2
8. target weights are non-flat

## Guardrails

- Do not stop kill-switch-authority unless emergency.
- Do not set LIVE based on G1-G7 alone.
- Keep G8 guard active until authority code has native G8 check.
