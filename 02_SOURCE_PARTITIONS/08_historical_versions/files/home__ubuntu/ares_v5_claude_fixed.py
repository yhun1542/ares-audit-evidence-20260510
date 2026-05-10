#!/usr/bin/env python3
"""
ARES Ultimate v5.0 - Claude 버전 (수정됨)
버그 수정: compute_features에서 DataFrame 인덱스 문제 해결
"""

# Claude 코드 로드 및 수정
import sys
sys.path.insert(0, '/home/ubuntu')

# 원본 코드 읽기
with open('/home/ubuntu/ares_v5_claude.py', 'r') as f:
    code = f.read()

# 버그 수정: validate_no_lookahead 함수의 prices DataFrame 수정
old_code = '''    dates = pd.date_range("2020-01-01", "2020-01-31", freq='B')
    prices = pd.DataFrame({
        'A': [100, np.nan, np.nan, 103, 104, 105] + [np.nan] * (len(dates) - 6),
        'B': [50, 51, np.nan, 53, np.nan, 55] + [np.nan] * (len(dates) - 6)
    }, index=dates[:len(dates)])'''

new_code = '''    dates = pd.date_range("2020-01-01", "2020-01-31", freq='B')
    n = len(dates)
    prices = pd.DataFrame({
        'A': np.concatenate([[100, np.nan, np.nan, 103, 104, 105], np.full(n - 6, np.nan)]),
        'B': np.concatenate([[50, 51, np.nan, 53, np.nan, 55], np.full(n - 6, np.nan)])
    }, index=dates)'''

code = code.replace(old_code, new_code)

# 수정된 코드 실행
exec(code)
