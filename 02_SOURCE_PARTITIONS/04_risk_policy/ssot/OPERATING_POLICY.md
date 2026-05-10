# Operating Policy

- Live Champion remains `E2E3fix_B40S60_F22`.
- `confirm_3` is retired as engine candidate and must not be treated as live candidate.
- `panic_override` is research-only and must not be activated in live.
- PM2 core service manifest under `PM2_LIVE_MANIFEST.json` is authoritative for runtime paths.
- Archive-first cleanup only; do not delete active live files.

> Status note: `confirm_3` is `retired_engine_candidate` and must not be treated as a live candidate.
> Status note: `panic_override` is `research_only` and must not be treated as a live patch.

---

## Documentation Exceptions (Non-Blocking)

아래 항목은 운영 예외가 아니라 문서상 명시된 비차단 예외다.

1. `kill-switch-authority`
   - PM2 manifest에서 canonical name 직접 매핑 불가
   - alias / process-name mismatch 예외로 관리

2. `orchestrator`
   - PM2 manifest에서 canonical name 직접 매핑 불가
   - alias / process-name mismatch 예외로 관리

3. `aub-trading-system`
   - archived GitHub repository
   - read-only legacy mirror
   - truth push 대상 제외

이 3개 예외는 현재 운영 진실을 바꾸지 않는다.
현재 authoritative live truth는 아래와 같다.

- Live Champion: `E2E3fix_B40S60_F22`
- Retired Candidate: `confirm_3`
- Research-only Asset: `panic_override`
