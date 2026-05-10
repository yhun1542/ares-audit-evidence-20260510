# ARES RAM26 Alpha Champion Direct Live Cutover Report

## Verdict
**DIRECT_LIVE_CUTOVER_SUCCESS**

## Promotion
- **Promotion ID:** RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z
- **Runtime:** ARES RAM26 v8.8A
- **Previous Champion:** BASELINE_1ca1b110d384 / p9B_106
- **New Live Champion:** RAM26_ALPHA_0001_b215c119ea23
- **Mode:** LIVE
- **Order Routing:** live
- **Paper Trading:** false
- **Shadow Only:** false
- **Production Write:** true

## Config & Backup
- **Live Champion Config:** /home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/live_config/ram26_champion_RAM26_ALPHA_0001_b215c119ea23.json
- **Backup Directory:** /home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z/backup

## Rollback
- **Rollback Script:** /opt/ares/RAM26_alpha_last_rollback.sh
- **Quick Rollback Command:** `sudo bash /opt/ares/RAM26_alpha_last_rollback.sh`

## Expected Alpha Champion Metrics
- **CAGR:** 143.55%
- **MDD:** -9.06%
- **Sharpe Ratio:** 6.91
- **WF Min SR:** 5.99
- **Turnover:** 0.7162
- **Cost:** 9.31 bps

## Notes
Paper/Shadow/Canary additional validation was skipped by direct user instruction.
Backup, risk controls, kill switch expectation, and rollback script were retained.
