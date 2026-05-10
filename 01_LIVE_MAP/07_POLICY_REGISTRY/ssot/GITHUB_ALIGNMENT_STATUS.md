# GitHub Alignment Status

- Intended truth branch: `production-f22-live-truth-20260403`
- Live Champion: `E2E3fix_B40S60_F22`
- Retired Candidate: `confirm_3`
- Research-only Asset: `panic_override`
- Updated: `2026-04-03T15:34:50.864241Z`

> Status note: `confirm_3` is `retired_engine_candidate` and must not be treated as a live candidate.
> Status note: `panic_override` is `research_only` and must not be treated as a live patch.

---

## GitHub Truth Alignment Status

GitHub truth alignment 결과는 아래와 같이 해석한다.

### ARES-KIS-US-AUTOPILOT

- branch: `production-f22-live-truth-20260403`
- status: **push success**
- 해석: 현재 운영 truth를 반영하는 주 저장소(main operational repository)

### aub-trading-system

- branch: `production-f22-live-truth-20260403`
- status: **push failed (403)**
- 원인: GitHub archived repository / read-only
- 해석: **legacy mirror**
- 운영 원칙: 본 저장소는 archived 상태이므로 truth push 대상이 아니라 참고용 historical mirror로만 취급
- 따라서 GitHub alignment의 authoritative source는 `ARES-KIS-US-AUTOPILOT` 저장소로 한정한다.

### 결론

GitHub truth alignment는 **주 저장소 기준 완료**로 판정한다.
`aub-trading-system`의 push 실패는 운영 실패가 아니라 archived read-only 예외다.
