#!/usr/bin/env python3
"""Fix early return condition to allow RateShockGate to run"""

with open('/home/ubuntu/AUB/baseline/ares_phase2b_v661_addon_engine_v3_integration.py', 'r') as f:
    content = f.read()

# Fix 1: Line 509-510
old1 = '''        if (not self.enable_icir_v4) and (not self.enable_mvo):
            return weights, cash_w, severity_state, cb_state'''

new1 = '''        if (not self.enable_icir_v4) and (not self.enable_mvo) and (not getattr(self, 'enable_rate_shock_gate', False)):
            return weights, cash_w, severity_state, cb_state'''

if old1 in content:
    content = content.replace(old1, new1)
    print('Fixed early return 1')
else:
    print('Pattern 1 not found')

# Fix 2: Line 513-514 (invest_total <= 1e-12)
# This should still return early, but we need to apply RateShockGate before returning
# Actually, if invest_total is 0, there's nothing to scale, so this is fine

# Fix 3: Line 519-520 (sleeve_scores is None)
old3 = '''        if sleeve_scores is None:
            return weights, cash_w, severity_state, cb_state'''

new3 = '''        if sleeve_scores is None and (not getattr(self, 'enable_rate_shock_gate', False)):
            return weights, cash_w, severity_state, cb_state'''

if old3 in content:
    content = content.replace(old3, new3)
    print('Fixed early return 3')
else:
    print('Pattern 3 not found')

with open('/home/ubuntu/AUB/baseline/ares_phase2b_v661_addon_engine_v3_integration.py', 'w') as f:
    f.write(content)

print('Done')
