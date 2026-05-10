# ARES v46 - Ultimate Quant Strategy

**Version:** v46 (Baseline)  
**Date:** 2024-12-25  
**Status:** Production Ready

## Performance Summary

| Metric | Value |
|--------|-------|
| **Sharpe Ratio** | 1.688 |
| **Max Drawdown** | -14.6% |
| **Max Recovery** | 350 days |
| **Backtest Period** | 2002-2025 (23 years) |

## Key Features

### 1. SLOW_RISK Detection (저강도 하락장 감지)
- **SLOW60**: 60일 누적 수익률 ≤ -4%
- **SLOW120**: 120일 누적 수익률 ≤ -8%
- 저강도 하락장에서 노출을 0.35x로 제한

### 2. Crisis Detection (A+C Logic)
- **VIX Level**: VIX ≥ 30 → Crisis 전환
- **VIX Spike**: VIX 일간 변화율 ≥ +20% → 조기 경보
- Crisis 시 노출 0.2x로 축소

### 3. Bull Guard
- SLOW_RISK 조건 충족 시 Bull 레짐에서도 노출 0.8x로 제한
- 2008년 같은 "저강도 → 급변" 시나리오 방어

### 4. EXIT Conditions
- 20일 수익률 ≥ +2% 시 SLOW_RISK 해제
- 1일 확인 기간

## Directory Structure

```
ares_v46_251225/
├── README.md                    # 이 문서
├── config/
│   └── v46_config.yaml          # 설정 파일
├── src/
│   ├── ares_v46_engine.py       # 메인 백테스트 엔진
│   └── utils.py                 # 유틸리티 함수
├── docs/
│   ├── CHANGELOG.md             # 변경 이력
│   ├── VALIDATION_REPORT.md     # 검증 보고서
│   └── PARAMETER_EVOLUTION.md   # 파라미터 진화 과정
├── results/
│   └── v46_backtest_results.csv # 백테스트 결과
└── tests/
    └── test_v46_engine.py       # 테스트 코드
```

## Quick Start

### 1. 환경 설정

```bash
# Python 3.11+ 필요
pip install numpy pandas numba sqlite3 pyyaml
```

### 2. 백테스트 실행

```python
from src.ares_v46_engine import AresV46Engine, AresV46Config

# 설정 로드
config = AresV46Config()
engine = AresV46Engine(config)

# 데이터 로드
engine.load_data('/path/to/ares_universal_v2.db')

# 팩터 계산
engine.compute_factors()
engine.compute_cumulative_returns()

# 백테스트 실행
equity, daily_returns = engine.run_backtest()

# 성과 지표
metrics = engine.calculate_metrics(equity, daily_returns)
print(f"Sharpe: {metrics['sharpe']:.3f}")
print(f"MDD: {metrics['mdd']*100:.1f}%")
```

## Regime Logic

### State Machine

```
                    ┌─────────────┐
                    │   CRISIS    │ (0.2x)
                    │  VIX≥30 or  │
                    │ VIX↑≥20%   │
                    └──────┬──────┘
                           │
    ┌──────────────────────┼──────────────────────┐
    │                      │                      │
    ▼                      ▼                      ▼
┌───────┐            ┌───────────┐          ┌───────┐
│ BULL  │            │  NEUTRAL  │          │ BEAR  │
│ (1.0x)│◄──────────►│  (0.7x)   │◄────────►│(0.5x) │
└───┬───┘            └─────┬─────┘          └───┬───┘
    │                      │                    │
    │    ┌─────────────────┼────────────────┐   │
    │    │                 │                │   │
    │    ▼                 ▼                ▼   │
    │  ┌─────────────────────────────────────┐  │
    └─►│           SLOW_RISK (0.35x)         │◄─┘
       │  ret60≤-4% OR ret120≤-8%            │
       │  EXIT: ret20≥+2% (1일 확인)          │
       └─────────────────────────────────────┘
```

## Validation History

| Phase | Description | Result |
|-------|-------------|--------|
| Phase 1 | SLOW_RISK 기본 로직 | MDD -23.4% → -18.8% |
| Phase 2 | 그리드 탐색 + Bull 가드레일 | MDD → -14.6%, Sharpe 1.688 |
| Phase 2.5A | EXIT 튜닝 | MaxRecovery 350일 |
| Phase 2.5B | 2단계 램프 | 효과 없음, 2.5A 유지 |

## Stress Test Results

| Event | Period | Strategy MDD | SPY MDD |
|-------|--------|--------------|---------|
| 2008 Financial Crisis | 2008-06 ~ 2009-04 | -9.3% | -56.8% |
| 2015 China Shock | 2015-08 ~ 2015-11 | -6.3% | -12.4% |
| 2020 COVID Crash | 2020-02 ~ 2020-04 | -7.7% | -33.9% |
| 2022 Rate Hike | 2021-12 ~ 2023-01 | -14.6% | -25.4% |

## Future Improvements (v47+)

1. **DD-duration 기반 cap 완화**: 오래 물리면 조금씩 풀기
2. **외부 지표 추가**: 크레딧 스프레드, 브레드스
3. **MaxRecovery 95% 분위 기준**: 최악 1개 대신 분포 기반

## License

Proprietary - All rights reserved.

## Contact

For questions or support, please contact the ARES development team.
