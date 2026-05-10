#!/usr/bin/env python3
"""
Crisis-Only 헤지 패치
- CLI 인자: --crisis_only_hedge (int, 0=OFF, 1=ON)
- 로직: regime15가 risk_off 또는 crisis일 때만 헤지 적용
"""

ENGINE_PATH = "/home/ubuntu/AUB/baseline/ares_phase2b_v661_addon_engine_v3_integration.py"

def patch_engine():
    with open(ENGINE_PATH, 'r') as f:
        content = f.read()
    
    # 1. CLI 인자 추가 (--enable_hedge 다음에)
    if '--crisis_only_hedge' not in content:
        cli_pattern = 'ap.add_argument("--enable_hedge", type=int, default=0)'
        cli_replacement = '''ap.add_argument("--enable_hedge", type=int, default=0)
    ap.add_argument("--crisis_only_hedge", type=int, default=0,
                    help="Enable hedge only in risk_off/crisis regimes (0=OFF, 1=ON)")'''
        content = content.replace(cli_pattern, cli_replacement)
        print("[OK] CLI 인자 추가: --crisis_only_hedge")
    else:
        print("[SKIP] CLI 인자 이미 존재")
    
    # 2. __init__ 파라미터에 crisis_only_hedge 추가
    if 'crisis_only_hedge: bool = False' not in content:
        init_param_pattern = 'enable_hedge: bool = False,'
        init_param_replacement = '''enable_hedge: bool = False,
                 crisis_only_hedge: bool = False,'''
        content = content.replace(init_param_pattern, init_param_replacement)
        print("[OK] __init__ 파라미터에 crisis_only_hedge 추가")
    else:
        print("[SKIP] __init__ 파라미터 이미 존재")
    
    # 3. self.crisis_only_hedge 할당 추가 (self.enable_hedge 다음에)
    if 'self.crisis_only_hedge' not in content:
        # enable_hedge 할당 찾기
        import re
        pattern = r'(self\.enable_hedge = bool\(enable_hedge\))'
        replacement = r'''\1
        self.crisis_only_hedge = bool(crisis_only_hedge)
        self._current_r15_regime = "risk_on"  # 현재 regime15 상태 추적'''
        content = re.sub(pattern, replacement, content)
        print("[OK] self.crisis_only_hedge 할당 추가")
    else:
        print("[SKIP] self.crisis_only_hedge 이미 존재")
    
    # 4. regime15 결과에서 현재 레짐 저장
    if '_current_r15_regime' not in content or 'self._current_r15_regime = r15_regime' not in content:
        # r15_regime 설정 후 저장
        old_r15 = 'r15_regime = r15_result.get("regime", "risk_on")'
        new_r15 = '''r15_regime = r15_result.get("regime", "risk_on")
                            self._current_r15_regime = r15_regime  # Crisis-Only 헤지용'''
        if old_r15 in content and 'self._current_r15_regime = r15_regime' not in content:
            content = content.replace(old_r15, new_r15)
            print("[OK] regime15 결과 저장 추가")
        else:
            print("[SKIP] regime15 결과 저장 이미 존재 또는 패턴 없음")
    
    # 5. 헤지 적용 로직 수정 (crisis_only_hedge 조건 추가)
    old_hedge = 'hedge_ret = -float(hedge_notional) * float(self.mkt_ret[t]) if self.enable_hedge else 0.0'
    new_hedge = '''# Crisis-Only 헤지: risk_off/crisis일 때만 헤지 적용
            should_hedge = self.enable_hedge
            if self.crisis_only_hedge and self.enable_hedge:
                r15_regime = getattr(self, '_current_r15_regime', 'risk_on')
                should_hedge = r15_regime in ('risk_off', 'crisis')
            hedge_ret = -float(hedge_notional) * float(self.mkt_ret[t]) if should_hedge else 0.0'''
    
    if 'Crisis-Only 헤지' not in content:
        content = content.replace(old_hedge, new_hedge)
        print("[OK] Crisis-Only 헤지 로직 추가")
    else:
        print("[SKIP] Crisis-Only 헤지 로직 이미 존재")
    
    # 6. 엔진 생성 시 crisis_only_hedge 전달
    if 'crisis_only_hedge=bool(args.crisis_only_hedge)' not in content:
        engine_pattern = 'enable_hedge=bool(args.enable_hedge),'
        engine_replacement = '''enable_hedge=bool(args.enable_hedge),
        crisis_only_hedge=bool(getattr(args, 'crisis_only_hedge', False)),'''
        content = content.replace(engine_pattern, engine_replacement)
        print("[OK] 엔진 생성 시 crisis_only_hedge 전달")
    else:
        print("[SKIP] 엔진 생성 이미 변경됨")
    
    # 저장
    with open(ENGINE_PATH, 'w') as f:
        f.write(content)
    
    print("\n[DONE] 패치 완료!")
    return True

if __name__ == "__main__":
    patch_engine()
