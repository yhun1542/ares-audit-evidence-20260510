"""
AVT (Adaptive Volatility Target) 엔진 통합 패치
기존 엔진에 AVT 로직을 추가하는 패치 스크립트
"""

import re
import shutil
from datetime import datetime

ENGINE_FILE = "baseline/ares_phase2b_v661_addon_engine_v3_integration.py"

def backup_engine():
    """엔진 파일 백업"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{ENGINE_FILE.replace(.py, )}_backup_{timestamp}.py"
    shutil.copy(ENGINE_FILE, backup_file)
    print(f"백업 생성: {backup_file}")
    return backup_file

def add_avt_import():
    """AVT 모듈 import 추가"""
    with open(ENGINE_FILE, "r") as f:
        content = f.read()
    
    # 이미 추가되어 있으면 스킵
    if "from avt_module import" in content:
        print("AVT import 이미 존재")
        return content
    
    # import 섹션 찾기 (from ... import 다음에 추가)
    import_line = "from avt_module import AdaptiveVolatilityTarget, AVTConfig, create_avt_from_args"
    
    # rate_shock_gate import 다음에 추가
    if "from rate_shock_gate import" in content:
        content = content.replace(
            "from rate_shock_gate import RateShockGate, RateShockConfig",
            "from rate_shock_gate import RateShockGate, RateShockConfig\n" + import_line
        )
    else:
        # 파일 시작 부분에 추가
        content = import_line + "\n" + content
    
    print("AVT import 추가됨")
    return content

def add_avt_argparse(content):
    """AVT argparse 인자 추가"""
    if "--enable_avt" in content:
        print("AVT argparse 이미 존재")
        return content
    
    # argparse 섹션 찾기 (risk_target 다음에 추가)
    avt_args = 
