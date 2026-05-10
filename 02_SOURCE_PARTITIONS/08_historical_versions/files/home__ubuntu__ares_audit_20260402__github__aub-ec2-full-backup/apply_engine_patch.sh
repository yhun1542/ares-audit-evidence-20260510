#!/bin/bash
# Macro Alpha Addon 엔진 패치 스크립트
# 사용법: bash apply_engine_patch.sh

set -e

ENGINE_FILE="/home/ubuntu/AUB/baseline/ares_phase2b_v661_addon_engine_v3_integration.py"
TEMP_FILE="/tmp/engine_patched.py"

echo "=== Macro Alpha Addon 엔진 패치 시작 ==="

# 1. 파일 복사
cp "$ENGINE_FILE" "$TEMP_FILE"

# 2. IMPORT 추가 (from copy import deepcopy 뒤에)
sed -i '/from copy import deepcopy/a\
\
# === Macro Alpha Addon Import ===\
try:\
    from macro_alpha_addon_patch import (\
        MacroAlphaAddon, \
        MACRO_ALPHA_DEFAULTS,\
    )\
    MACRO_ALPHA_AVAILABLE = True\
except ImportError:\
    MACRO_ALPHA_AVAILABLE = False\
    MACRO_ALPHA_DEFAULTS = dict(enabled=False, available=True, last_reason="", mode="shadow",\
                                apply_type="scale", rebalance_calls=0,\
                                regime_counts=dict(calm_bull=0, volatile_bull=0, tightening=0,\
                                                  bear_market=0, crisis=0, slow_risk=0),\
                                position_scale_sum=0.0, position_scale_min=1.0, position_scale_max=1.0,\
                                position_scale_avg=1.0, hedge_mode_last="", hedge_weight_on_last=0.0,\
                                enter_days_last=0, exit_days_last=0, missing_cols=[])' "$TEMP_FILE"

echo "✓ Import 패치 완료"

# 3. ADDON_DEFAULTS에 macro_alpha 추가 (rate_shock_gate 뒤에)
sed -i '/"rate_shock_gate":/a\
    "macro_alpha": dict(enabled=False, available=True, last_reason="", mode="shadow",\
                       apply_type="scale", rebalance_calls=0,\
                       regime_counts=dict(calm_bull=0, volatile_bull=0, tightening=0,\
                                         bear_market=0, crisis=0, slow_risk=0),\
                       position_scale_sum=0.0, position_scale_min=1.0, position_scale_max=1.0,\
                       position_scale_avg=1.0, hedge_mode_last="", hedge_weight_on_last=0.0,\
                       enter_days_last=0, exit_days_last=0, missing_cols=[]),' "$TEMP_FILE"

echo "✓ ADDON_DEFAULTS 패치 완료"

# 4. CLI 인자 추가 (--output 앞에)
sed -i '/ap.add_argument("--output"/i\
    # === Macro Alpha Addon CLI ===\
    ap.add_argument("--enable_macro_alpha", type=int, default=0,\
                    help="Enable macro alpha addon (0/1)")\
    ap.add_argument("--macro_alpha_mode", type=str, default="shadow",\
                    help="Mode: shadow (no effect) or apply")\
    ap.add_argument("--macro_alpha_apply", type=str, default="scale",\
                    help="Apply type: scale or scale_and_hedge (when mode=apply)")\
    ap.add_argument("--macro_alpha_required_cols", type=str, \
                    default="VIX,MOVE,DGS10,DGS2,T10YIE,SP500",\
                    help="Comma-separated required macro columns")\
' "$TEMP_FILE"

echo "✓ CLI 인자 패치 완료"

# 5. 결과 파일 복사
cp "$TEMP_FILE" "$ENGINE_FILE"

echo ""
echo "=== 패치 완료 ==="
echo "파일: $ENGINE_FILE"

# 6. 패치 확인
echo ""
echo "=== 패치 확인 ==="
grep -n "MACRO_ALPHA_AVAILABLE" "$ENGINE_FILE" | head -3
grep -n "enable_macro_alpha" "$ENGINE_FILE" | head -3
