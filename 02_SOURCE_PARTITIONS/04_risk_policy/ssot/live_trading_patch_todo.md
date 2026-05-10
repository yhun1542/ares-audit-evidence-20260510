# live-trading-kis.mjs Patch TODO
Generated: 2026-04-01T14:56:49Z

## 즉시 조치 불필요 (현재 안정 운영 중)
live-trading-kis.mjs는 운영 중요도가 매우 높으므로 자동 대규모 치환을 금지합니다.
아래는 향후 개선 검토 항목입니다.

## TODO 항목

### [P3] crashGuard 임계치 UEC 연동 검토
- **현재**: crashGuard가 독립적으로 drawdown -3%, vol_spike 3.0 기준 사용
- **개선**: UEC가 안정화되면 crashGuard의 risk_scale_down을 UEC의 smooth_exposure와 연동
- **시기**: UEC 3거래일 shadow 모니터링 완료 후
- **위험도**: LOW (현재 구조도 안전)

### [P4] TURNOVER_CFG 주석 정리
- **현재**: 주석에 "was 20%/10%/8%/0.5%" 기록되어 있으나 실값과 다름
- **개선**: 주석을 현재 값으로 업데이트하거나, 변경 이력 문서화
- **시기**: 다음 정기 코드 리뷰
- **위험도**: NONE (주석만 변경)

### [P5] evidence producer 전환 준비
- **현재**: crashGuard, RISK_OFF_BLOCK 등이 직접 축소/차단
- **개선**: Phase 3 후반에서 evidence producer 패턴으로 전환 검토
  - crashGuard → evidence 생성 → orchestrator가 최종 scale 결정
  - RISK_OFF_BLOCK → evidence 생성 → 통합 판단
- **시기**: Phase 4 이후
- **위험도**: HIGH (대규모 구조 변경 — 충분한 shadow 검증 필수)

### [P5] riskOffLiquidation 로깅 강화
- **현재**: governor bypass 시 로그 없음
- **개선**: bypass 발생 시 Redis stream에 기록하여 post-close 리포트에 포함
- **시기**: 다음 패치
- **위험도**: LOW

## 금지 사항
- live-trading-kis.mjs 자동 대규모 리팩터링 금지
- 장중 코드 변경 금지
- manifest 없이 신규 entrypoint live 투입 금지
