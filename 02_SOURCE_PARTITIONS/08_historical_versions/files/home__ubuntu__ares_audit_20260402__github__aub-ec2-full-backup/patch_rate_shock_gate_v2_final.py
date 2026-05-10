#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rate_shock_gate 확장 패치 (최종 버전):
1. 엔진 argparse에 zscore 기반 옵션 추가
2. __init__에 새 속성 저장
3. _load_rate_shock_zscore 함수 추가
4. 조건부 rebal_period (쇼크 시 더 빠른 리밸런싱)
5. diagnostics에 rate_shock_v2 통계 추가

ICIR blend 수정은 별도 패치로 진행 (복잡한 인덴트 문제 회피)
"""

import re
from pathlib import Path

ENGINE_FILE = Path("/home/ubuntu/AUB/baseline/ares_phase2b_v661_addon_engine_v3_integration.py")

def patch_engine():
    content = ENGINE_FILE.read_text(encoding="utf-8")
    original = content
    
    # 1. argparse에 새 옵션 추가
    new_args = '''
    # rate shock gate v2 (zscore-based conditional rebal/icir/cap)
    ap.add_argument("--rate_shock_z_th", type=float, default=1.5,
                    help="Z-score threshold for rate shock trigger")
    ap.add_argument("--rate_shock_z_window", type=int, default=252,
                    help="Rolling window for zscore calculation")
    ap.add_argument("--rate_shock_lookback", type=int, default=20,
                    help="Lookback days for spread delta")
    ap.add_argument("--rebal_period_on_shock", type=int, default=10,
                    help="Faster rebal period during rate shock")
    ap.add_argument("--icir_blend_ratio_on_shock", type=float, default=0.30,
                    help="Reduced ICIR blend ratio during rate shock")
    ap.add_argument("--cap_mult_on_shock", type=float, default=0.90,
                    help="Cap multiplier during rate shock")'''
    
    if "--rate_shock_z_th" not in content:
        pattern = r'(ap\.add_argument\("--rate_shock_hedge_max"[^\n]+\n)'
        if re.search(pattern, content):
            content = re.sub(pattern, r'\1' + new_args + '\n', content)
            print("[OK] Added new argparse options for rate_shock_gate v2")
    else:
        print("[SKIP] rate_shock_z_th already exists")
    
    # 2. __init__에 새 속성 저장
    init_attrs = '''
        # rate shock v2 attrs
        self.rate_shock_z_th = float(getattr(args, 'rate_shock_z_th', 1.5) if hasattr(args, 'rate_shock_z_th') else 1.5)
        self.rate_shock_z_window = int(getattr(args, 'rate_shock_z_window', 252) if hasattr(args, 'rate_shock_z_window') else 252)
        self.rate_shock_lookback = int(getattr(args, 'rate_shock_lookback', 20) if hasattr(args, 'rate_shock_lookback') else 20)
        self.rebal_period_on_shock = int(getattr(args, 'rebal_period_on_shock', 10) if hasattr(args, 'rebal_period_on_shock') else 10)
        self.icir_blend_ratio_on_shock = float(getattr(args, 'icir_blend_ratio_on_shock', 0.30) if hasattr(args, 'icir_blend_ratio_on_shock') else 0.30)
        self.cap_mult_on_shock = float(getattr(args, 'cap_mult_on_shock', 0.90) if hasattr(args, 'cap_mult_on_shock') else 0.90)
        self._rate_shock_z = None
        self._rate_shock_rebal_events = 0
        self._rate_shock_days_active = 0'''
    
    if "self.rate_shock_z_th" not in content:
        pattern = r'(self\.rate_shock_hedge_max = float\(rate_shock_hedge_max\))'
        if re.search(pattern, content):
            content = re.sub(pattern, r'\1\n' + init_attrs, content)
            print("[OK] Added rate_shock v2 attributes to __init__")
    else:
        print("[SKIP] rate_shock_z_th attribute already exists")
    
    # 3. _load_rate_shock_zscore 함수 추가
    load_func = '''
    def _load_rate_shock_zscore(self, conn):
        """Load DGS2/DGS10 from fred_series, compute spread zscore."""
        import pandas as pd
        if not getattr(self, 'enable_rate_shock_gate', False):
            self._rate_shock_z = None
            return
        lookback = int(getattr(self, 'rate_shock_lookback', 20))
        zwin = int(getattr(self, 'rate_shock_z_window', 252))
        try:
            df = pd.read_sql_query(
                "SELECT date, series_id, value FROM fred_series WHERE series_id IN ('DGS2','DGS10')",
                conn
            )
            if df.empty:
                self._rate_shock_z = None
                return
            df["date"] = pd.to_datetime(df["date"])
            piv = df.pivot(index="date", columns="series_id", values="value").sort_index().ffill()
            spread = piv["DGS10"] - piv["DGS2"]
            idx = pd.to_datetime(self.dates)
            spread = spread.reindex(idx).ffill()
            s = spread.values.astype(np.float64)
            shock = np.full_like(s, np.nan)
            if len(s) > lookback:
                shock[lookback:] = s[lookback:] - s[:-lookback]
            z = np.full_like(s, np.nan)
            for t in range(zwin, len(s)):
                w = shock[t - zwin : t]
                mu, sd = np.nanmean(w), np.nanstd(w)
                if sd > 1e-12:
                    z[t] = (shock[t] - mu) / sd
            self._rate_shock_z = z
            print(f"[RateShockGate] Loaded zscore series, len={len(z)}, non-nan={np.sum(~np.isnan(z))}")
        except Exception as e:
            print(f"[RateShockGate] Failed to load zscore: {e}")
            self._rate_shock_z = None

'''
    
    if "def _load_rate_shock_zscore" not in content:
        pattern = r'(    def run_single_period\(self, start: int, end: int, features: Dict\))'
        if re.search(pattern, content):
            content = re.sub(pattern, load_func + r'\1', content)
            print("[OK] Added _load_rate_shock_zscore function")
    else:
        print("[SKIP] _load_rate_shock_zscore already exists")
    
    # 4. run_single_period에서 조건부 rebal_period 적용
    rebal_patch = '''            # Conditional faster rebal during rate shock (v2)
            cur_rebal = int(self.rebal_period)
            shock_on_v2 = False
            if getattr(self, 'enable_rate_shock_gate', False) and getattr(self, '_rate_shock_z', None) is not None:
                try:
                    z = self._rate_shock_z[t]
                    if z is not None and np.isfinite(z) and z > float(getattr(self, 'rate_shock_z_th', 1.5)):
                        shock_on_v2 = True
                        cur_rebal = min(cur_rebal, int(getattr(self, 'rebal_period_on_shock', 10)))
                        self._rate_shock_days_active = getattr(self, '_rate_shock_days_active', 0) + 1
                except Exception:
                    pass

            if (t - start) % cur_rebal == 0:'''
    
    if "shock_on_v2" not in content:
        # 기존 패턴: 12칸 공백 + if (t - start) % self.rebal_period == 0:
        pattern = r'(            )(if \(t - start\) % self\.rebal_period == 0:)'
        if re.search(pattern, content):
            content = re.sub(pattern, rebal_patch, content, count=1)
            print("[OK] Patched run_single_period for conditional rebal")
        else:
            print("[WARN] Could not find rebal_period check")
    else:
        print("[SKIP] shock_on_v2 already exists")
    
    # 5. diagnostics에 rate_shock_v2 통계 추가
    diag_patch = '''"rate_shock_v2": {
                    "days_active": int(getattr(self, '_rate_shock_days_active', 0)),
                    "rebal_events": int(getattr(self, '_rate_shock_rebal_events', 0)),
                    "z_th": float(getattr(self, 'rate_shock_z_th', 1.5)),
                },
                '''
    
    if '"rate_shock_v2"' not in content:
        pattern = r'("addon": addon,)'
        if re.search(pattern, content):
            content = re.sub(pattern, diag_patch + r'\1', content)
            print("[OK] Added rate_shock_v2 to diagnostics")
    else:
        print("[SKIP] rate_shock_v2 already in diagnostics")
    
    # 저장
    if content != original:
        backup = ENGINE_FILE.with_suffix('.py.bak_rs_v2_final')
        backup.write_text(original, encoding="utf-8")
        print(f"[OK] Backup saved to {backup}")
        ENGINE_FILE.write_text(content, encoding="utf-8")
        print(f"[OK] Engine patched: {ENGINE_FILE}")
        return True
    else:
        print("[INFO] No changes made")
        return False

if __name__ == "__main__":
    patch_engine()
