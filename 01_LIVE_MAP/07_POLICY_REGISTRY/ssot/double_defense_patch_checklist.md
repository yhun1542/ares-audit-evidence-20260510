# Double Defense Patch Checklist — live-trading-kis.mjs
Generated: 2026-04-01T14:56:49Z

## 현재 이중 방어 구조 분석

### 1. crashGuard (line 687~780)
- **기능**: VIX 급등/drawdown 감지 시 포지션 50% 축소
- **트리거**: drawdown < -3%, vol_spike > 3.0, adaptive VIX 기반
- **결과**: decision="crash_guard" → 전체 포지션에 risk_scale_down(0.50) 적용
- **이중 방어 여부**: orchestrator.py의 CRISIS regime과 독립적으로 동작
- **판단**: 의도적 이중 방어 — orchestrator는 champion_scale, live-trading-kis는 cycle-level crash guard
- **조치**: 현재 유지. UEC 통합 후 orchestrator 측에서 충분히 축소하면 crashGuard 임계치 완화 검토

### 2. RISK_OFF_BLOCK (line 1567~1578)
- **기능**: regime confidence >= 0.65일 때 overlay 매수 차단
- **트리거**: ProfitInterrupt override 미승인 + 높은 regime confidence
- **결과**: overlay 매수 차단 (매도/청산은 허용)
- **이중 방어 여부**: orchestrator의 champion_scale 축소와 별개로 동작
- **판단**: 보수적 안전장치 — 유지 권장
- **조치**: 현재 유지

### 3. riskOffLiquidation bypass (line 1918)
- **기능**: risk-off 청산 시 governor scale 무시 (전량 매도)
- **판단**: 정상 동작 — 긴급 청산 시 governor가 방해하면 안 됨
- **조치**: 현재 유지

### 4. Turnover Budget (line 591~670)
- **기능**: 일일/사이클별 turnover 한도 관리
- **이중 방어 여부**: orchestrator와 독립적 — 실행 레벨 안전장치
- **판단**: 정상 — 실행 레벨에서 과도한 거래 방지
- **조치**: 현재 유지

## 결론
| 방어 메커니즘 | 위치 | 조치 |
|-------------|------|------|
| crashGuard | live-trading-kis.mjs | **유지** (UEC 안정화 후 임계치 재검토) |
| RISK_OFF_BLOCK | live-trading-kis.mjs | **유지** |
| riskOffLiquidation | live-trading-kis.mjs | **유지** |
| Turnover Budget | live-trading-kis.mjs | **유지** (max_daily 80% 모니터링) |

**자동 대규모 치환 금지** — 문서 지시에 따라 scan/todo/plan 중심으로만 접근
