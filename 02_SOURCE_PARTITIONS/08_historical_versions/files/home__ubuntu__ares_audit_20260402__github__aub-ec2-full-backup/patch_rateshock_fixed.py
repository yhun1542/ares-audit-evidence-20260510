#!/usr/bin/env python3
"""
patch_rateshock_fixed.py
========================
RateShockGate + AlphaOrchestrator canary 패치 (들여쓰기 수정)
"""

import re
from pathlib import Path

ENGINE_PATH = Path("/home/ubuntu/AUB/baseline/ares_phase2b_v661_addon_engine_v3_integration.py")

def main():
    # 백업
    bak = ENGINE_PATH.with_suffix(ENGINE_PATH.suffix + ".bak_rateshock_fixed")
    if not bak.exists():
        bak.write_text(ENGINE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    
    s = ENGINE_PATH.read_text(encoding="utf-8")
    
    # 1. Import 추가
    if "from rate_shock_gate import" not in s:
        # 파일 시작 부분에 import 추가
        import_line = "from rate_shock_gate import RateShockGate, RateShockConfig\n"
        # 첫 번째 import 문 앞에 추가
        if "import numpy as np" in s:
            s = s.replace("import numpy as np", import_line + "import numpy as np")
        else:
            s = import_line + s
    
    # 2. CLI 플래그 추가 (--enable_regime15 뒤에)
    if "--enable_rate_shock_gate" not in s:
        cli_flags = '''
    # RateShockGate
    ap.add_argument("--enable_rate_shock_gate", type=int, default=0)
    ap.add_argument("--rate_shock_mode", type=str, default="shadow", choices=["shadow","apply"])
    ap.add_argument("--rate_shock_thr_2y_20d_bp", type=float, default=150.0)
    ap.add_argument("--rate_shock_thr_2y_5d_bp", type=float, default=80.0)
    ap.add_argument("--rate_shock_min_scale", type=float, default=0.70)
    ap.add_argument("--rate_shock_hold_days", type=int, default=5)
    ap.add_argument("--rate_shock_enable_hedge", type=int, default=0)
    ap.add_argument("--rate_shock_hedge_weight", type=float, default=0.10)
    # AlphaOrchestrator canary
    ap.add_argument("--alpha_orch_canary_fraction", type=float, default=0.20)
    ap.add_argument("--alpha_orch_risk_off_only", type=int, default=1)
'''
        # --enable_regime15 뒤에 추가
        pattern = r'(ap\.add_argument\("--enable_regime15"[^\n]*\))'
        s = re.sub(pattern, r'\1' + cli_flags, s)
    
    # 3. __init__ 시그니처에 파라미터 추가
    if "enable_rate_shock_gate: bool = False" not in s:
        init_params = '''enable_rate_shock_gate: bool = False,
                 rate_shock_mode: str = "shadow",
                 rate_shock_thr_2y_20d_bp: float = 150.0,
                 rate_shock_thr_2y_5d_bp: float = 80.0,
                 rate_shock_min_scale: float = 0.70,
                 rate_shock_hold_days: int = 5,
                 rate_shock_enable_hedge: bool = False,
                 rate_shock_hedge_weight: float = 0.10,
                 alpha_orch_canary_fraction: float = 0.20,
                 alpha_orch_risk_off_only: bool = True,
                 '''
        s = s.replace(
            "enable_regime15: bool = False,",
            "enable_regime15: bool = False,\n                 " + init_params
        )
    
    # 4. __init__ 본문에 인스턴스 변수 추가
    if "self.rate_shock_gate = " not in s:
        init_body = '''
        # RateShockGate 초기화
        self.enable_rate_shock_gate = bool(enable_rate_shock_gate)
        self.rate_shock_mode = str(rate_shock_mode or "shadow")
        self.alpha_orch_canary_fraction = float(alpha_orch_canary_fraction)
        self.alpha_orch_risk_off_only = bool(alpha_orch_risk_off_only)
        try:
            rscfg = RateShockConfig(
                enabled=bool(enable_rate_shock_gate),
                mode=str(rate_shock_mode or "shadow"),
                thr_2y_20d_bp=float(rate_shock_thr_2y_20d_bp),
                thr_2y_5d_bp=float(rate_shock_thr_2y_5d_bp),
                min_scale=float(rate_shock_min_scale),
                enable_hedge=bool(rate_shock_enable_hedge),
                hedge_weight_if_shock=float(rate_shock_hedge_weight),
                hold_days=int(rate_shock_hold_days),
            )
            self.rate_shock_gate = RateShockGate(rscfg)
        except Exception as e:
            self.rate_shock_gate = None
            print(f"[WARN] RateShockGate init failed: {e}")
'''
        # self.enable_regime15 = bool(enable_regime15) 뒤에 추가
        pattern = r'(self\.enable_regime15\s*=\s*bool\(enable_regime15\))'
        s = re.sub(pattern, r'\1' + init_body, s)
    
    # 5. 엔진 인스턴스 생성 시 파라미터 전달
    if "enable_rate_shock_gate=bool(args.enable_rate_shock_gate)" not in s:
        ctor_params = '''enable_rate_shock_gate=bool(args.enable_rate_shock_gate),
            rate_shock_mode=args.rate_shock_mode,
            rate_shock_thr_2y_20d_bp=args.rate_shock_thr_2y_20d_bp,
            rate_shock_thr_2y_5d_bp=args.rate_shock_thr_2y_5d_bp,
            rate_shock_min_scale=args.rate_shock_min_scale,
            rate_shock_hold_days=args.rate_shock_hold_days,
            rate_shock_enable_hedge=bool(args.rate_shock_enable_hedge),
            rate_shock_hedge_weight=args.rate_shock_hedge_weight,
            alpha_orch_canary_fraction=args.alpha_orch_canary_fraction,
            alpha_orch_risk_off_only=bool(args.alpha_orch_risk_off_only),
            '''
        # enable_regime15=bool(args.enable_regime15) 뒤에 추가
        pattern = r'(enable_regime15=bool\(args\.enable_regime15\),)'
        s = re.sub(pattern, r'\1\n            ' + ctor_params, s)
    
    # 6. _build_portfolio 반환 직전에 RateShockGate 훅 추가
    if "# RateShockGate hook" not in s:
        hook_code = '''
        # RateShockGate hook
        if getattr(self, 'enable_rate_shock_gate', False) and getattr(self, 'rate_shock_gate', None):
            try:
                d2y_5 = 0.0  # TODO: features에서 가져오기
                d2y_20 = 0.0  # TODO: features에서 가져오기
                rs_scale, rs_hedge_w, rs_diag = self.rate_shock_gate.decide(t, d2y_5, d2y_20)
                self._addon_last['rate_shock_gate'] = rs_diag
                if self.rate_shock_mode == 'apply':
                    weights = weights * float(rs_scale)
            except Exception as _e:
                self._addon_last['rate_shock_gate'] = {'error': str(_e)[:120]}
        '''
        # return weights, cash_w, severity_state, cb_state 앞에 추가
        s = s.replace(
            "        return weights, cash_w, severity_state, cb_state",
            hook_code + "\n        return weights, cash_w, severity_state, cb_state"
        )
    
    ENGINE_PATH.write_text(s, encoding="utf-8")
    print("[OK] Patch applied successfully")
    
    # 구문 검증
    import subprocess
    result = subprocess.run(
        ["python3", "-c", f"import sys; sys.path.insert(0, '{ENGINE_PATH.parent}'); import ares_phase2b_v661_addon_engine_v3_integration; print('Syntax OK')"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("[OK] Syntax verification passed")
    else:
        print(f"[ERROR] Syntax verification failed: {result.stderr}")
        # 복원
        ENGINE_PATH.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
        print("[RESTORED] Reverted to backup")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
