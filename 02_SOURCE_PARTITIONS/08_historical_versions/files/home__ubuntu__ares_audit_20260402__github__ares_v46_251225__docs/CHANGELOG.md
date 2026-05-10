# ARES v46 Changelog

## [v46] - 2024-12-25 (Baseline)

### Performance
- **Sharpe Ratio**: 1.688 (+8.8% vs v45 baseline)
- **Max Drawdown**: -14.6% (+8.8%p vs v45 baseline)
- **Max Recovery**: 350 days (-144 days vs v45 baseline)

### Added
- SLOW_RISK state for low-intensity bear market detection
- Bull Guard (CAP 0.8x) to prevent overexposure during SLOW conditions
- EXIT conditions with 1-day confirmation period
- A+C Crisis detection logic (VIX level + VIX spike)

### Changed
- Regime state machine now includes SLOW_RISK as 5th state
- Bull regime exposure capped at 0.8x when SLOW conditions met
- Crisis detection uses lagged VIX + spike detection

### Fixed
- 2008 MDD regression prevented by Bull Guard
- Hysteresis logic properly maintained during SLOW_RISK state

### Validated
- K-Fold CV with 131-day Purging/Embargo
- Walk-Forward validation (17 windows)
- Stress tests: 2008, 2015, 2020, 2022

---

## [v45] - 2024-12-24 (Previous Baseline)

### Performance
- Sharpe Ratio: 1.552
- Max Drawdown: -23.4%
- Max Recovery: 494 days

### Features
- A+C Crisis detection (VIX level + spike)
- Basic regime classification (Bull/Neutral/Bear/Crisis)
- Hysteresis for regime transitions

---

## Version History Summary

| Version | Date | Sharpe | MDD | Key Change |
|---------|------|--------|-----|------------|
| v46 | 2024-12-25 | 1.688 | -14.6% | SLOW_RISK + Bull Guard |
| v45 | 2024-12-24 | 1.552 | -23.4% | A+C Crisis detection |
| v44 | 2024-12-23 | 1.519 | -23.4% | Extended Pareto search |
| v43 | 2024-12-22 | 1.766 | -10.3% | Soft Frequency Gating |
| v41 | 2024-12-20 | 1.519 | -12.3% | Initial baseline |
