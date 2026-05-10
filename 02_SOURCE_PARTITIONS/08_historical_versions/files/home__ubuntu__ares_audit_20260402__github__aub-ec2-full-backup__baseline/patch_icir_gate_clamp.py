#!/usr/bin/env python3
"""
ICIR 게이트/클램프 패치 스크립트
- MR weight cap (0.15)
- R15 state가 CRISIS/TRANSITION이면 ICIR 업데이트 동결
- diagnostics에 icir_gated_count 기록
"""

import re

def patch_icir_gate_clamp(input_file: str, output_file: str):
    with open(input_file, 'r') as f:
        content = f.read()
    
    # 1. __init__에 ICIR 게이트 파라미터 추가
    init_params_pattern = r'(icir_min_periods: int = 20,)'
    init_params_replacement = r'''\1
                 icir_mr_cap: float = 0.15,
                 icir_gate_on_crisis: bool = True,'''
    content = re.sub(init_params_pattern, init_params_replacement, content)
    
    # 2. self 속성 초기화 추가
    self_init_pattern = r'(self\.icir_tracker = ICIRSleeveTracker.*)'
    self_init_replacement = r'''\1
        self.icir_mr_cap = float(icir_mr_cap)
        self.icir_gate_on_crisis = bool(icir_gate_on_crisis)
        self._icir_gated_count = 0'''
    content = re.sub(self_init_pattern, self_init_replacement, content)
    
    # 3. ICIR 가중치 적용 부분에 게이트/클램프 로직 추가
    # 기존: if self.enable_icir_v4:
    #           icir_w = self.icir_tracker.weights(base_weights)
    # 변경: 게이트 체크 + MR cap 적용
    
    icir_apply_pattern = r'''(if self\.enable_icir_v4:
            icir_w = self\.icir_tracker\.weights\(base_weights\))'''
    
    icir_apply_replacement = r'''if self.enable_icir_v4:
            # ICIR 게이트: R15 CRISIS/TRANSITION 시 동결
            icir_gated = False
            if self.icir_gate_on_crisis and hasattr(self, '_last_regime15_state'):
                r15_state = self._last_regime15_state
                if r15_state in ('crisis', 'transition'):
                    icir_gated = True
                    self._icir_gated_count += 1
                    self._addon_last["icir_gated"] = True
                    self._addon_last["icir_gate_reason"] = f"R15={r15_state}"
            
            if icir_gated:
                # 게이트 발동: base_weights 사용
                icir_w = base_weights.copy()
            else:
                icir_w = self.icir_tracker.weights(base_weights)
                
                # MR weight cap 적용
                if icir_w.get("MR", 0) > self.icir_mr_cap:
                    excess = icir_w["MR"] - self.icir_mr_cap
                    icir_w["MR"] = self.icir_mr_cap
                    # excess를 다른 슬리브에 균등 분배
                    other_sleeves = [k for k in icir_w.keys() if k != "MR"]
                    for k in other_sleeves:
                        icir_w[k] += excess / len(other_sleeves)
                    self._addon_last["icir_mr_capped"] = True
                    self._addon_last["icir_mr_excess"] = float(excess)'''
    
    content = re.sub(icir_apply_pattern, icir_apply_replacement, content, flags=re.DOTALL)
    
    # 4. Regime15 상태 저장 (pre_rebalance에서)
    # regime15 적용 후 상태 저장
    regime15_pattern = r'(if self\.enable_regime15.*?self\._addon_last\["regime15_band_mult"\] = float\(band_mult\))'
    regime15_replacement = r'''\1
                self._last_regime15_state = r15_result.get("regime", "unknown")'''
    content = re.sub(regime15_pattern, regime15_replacement, content, flags=re.DOTALL)
    
    # 5. diagnostics에 icir_gated_count 추가
    diag_pattern = r'("icir_v4": \{[^}]*"enabled": bool\(self\.enable_icir_v4\), "rebalance_calls": 0, "updates": 0,)'
    diag_replacement = r'''\1
                "gated_count": 0, "mr_capped_count": 0,'''
    content = re.sub(diag_pattern, diag_replacement, content)
    
    # 6. argparse에 새 파라미터 추가
    argparse_pattern = r'(ap\.add_argument\("--icir_min_periods", type=int, default=20\))'
    argparse_replacement = r'''\1
    ap.add_argument("--icir_mr_cap", type=float, default=0.15)
    ap.add_argument("--icir_gate_on_crisis", type=int, default=1)'''
    content = re.sub(argparse_pattern, argparse_replacement, content)
    
    # 7. 엔진 생성 시 새 파라미터 전달
    engine_create_pattern = r'(icir_min_periods=args\.icir_min_periods,)'
    engine_create_replacement = r'''\1
        icir_mr_cap=args.icir_mr_cap,
        icir_gate_on_crisis=bool(args.icir_gate_on_crisis),'''
    content = re.sub(engine_create_pattern, engine_create_replacement, content)
    
    with open(output_file, 'w') as f:
        f.write(content)
    
    print(f"[OK] Patched: {output_file}")
    return True

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python patch_icir_gate_clamp.py <input_file> <output_file>")
        sys.exit(1)
    
    patch_icir_gate_clamp(sys.argv[1], sys.argv[2])
