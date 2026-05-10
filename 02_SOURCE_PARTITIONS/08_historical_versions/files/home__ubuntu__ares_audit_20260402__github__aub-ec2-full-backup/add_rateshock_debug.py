#!/usr/bin/env python3
"""Add debug logging to RateShockGate hook"""

with open('/home/ubuntu/AUB/baseline/ares_phase2b_v661_addon_engine_v3_integration.py', 'r') as f:
    content = f.read()

old = '''        # RateShockGate hook
        if getattr(self, 'enable_rate_shock_gate', False) and getattr(self, 'rate_shock_gate', None):
            try:
                # d2y features from _compute_regime_features()
                d2y_5 = float(features.get('d2y_5d_bp', np.zeros(1))[min(t, len(features.get('d2y_5d_bp', [0]))-1)]) if 'd2y_5d_bp' in features else 0.0
                d2y_20 = float(features.get('d2y_20d_bp', np.zeros(1))[min(t, len(features.get('d2y_20d_bp', [0]))-1)]) if 'd2y_20d_bp' in features else 0.0
                # NaN 처리
                d2y_5 = 0.0 if np.isnan(d2y_5) else d2y_5
                d2y_20 = 0.0 if np.isnan(d2y_20) else d2y_20
                rs_scale, rs_hedge_w, rs_diag = self.rate_shock_gate.decide(t, d2y_5, d2y_20)
                self._addon_last['rate_shock_gate'] = rs_diag
                if self.rate_shock_mode == 'apply':
                    weights = weights * float(rs_scale)'''

new = '''        # RateShockGate hook
        if getattr(self, 'enable_rate_shock_gate', False) and getattr(self, 'rate_shock_gate', None):
            try:
                # d2y features from _compute_regime_features()
                d2y_5 = float(features.get('d2y_5d_bp', np.zeros(1))[min(t, len(features.get('d2y_5d_bp', [0]))-1)]) if 'd2y_5d_bp' in features else 0.0
                d2y_20 = float(features.get('d2y_20d_bp', np.zeros(1))[min(t, len(features.get('d2y_20d_bp', [0]))-1)]) if 'd2y_20d_bp' in features else 0.0
                # NaN 처리
                d2y_5 = 0.0 if np.isnan(d2y_5) else d2y_5
                d2y_20 = 0.0 if np.isnan(d2y_20) else d2y_20
                # Debug logging
                import os as _os
                if _os.getenv('AUB_DEBUG', '0') == '1' and t % 100 == 0:
                    print(f'[RateShockGate] t={t}, d2y_5={d2y_5:.2f}bp, d2y_20={d2y_20:.2f}bp')
                rs_scale, rs_hedge_w, rs_diag = self.rate_shock_gate.decide(t, d2y_5, d2y_20)
                if _os.getenv('AUB_DEBUG', '0') == '1' and t % 100 == 0:
                    print(f'[RateShockGate] scale={rs_scale:.3f}, trigger={rs_diag.get("trigger", False)}')
                self._addon_last['rate_shock_gate'] = rs_diag
                if self.rate_shock_mode == 'apply':
                    weights = weights * float(rs_scale)'''

if old in content:
    content = content.replace(old, new)
    with open('/home/ubuntu/AUB/baseline/ares_phase2b_v661_addon_engine_v3_integration.py', 'w') as f:
        f.write(content)
    print('Patched')
else:
    print('Pattern not found')
