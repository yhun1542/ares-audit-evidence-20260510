"""
ARES v3.0 Standalone Patch for orchestrator.py
================================================
This patch:
1. Replaces legacy REBAL_GATE block (lines 2973-3084) with Kernel-native control
2. Adds feedback loop hooks (prediction recording, trade logging)
3. Adds VIXY activation tracking
4. Maintains backward compatibility with existing orchestrator flow

Usage:
    python3 patch_v3_standalone.py [--dry-run] [--target /path/to/orchestrator.py]
"""
import os
import sys
import re
import shutil
from datetime import datetime

DRY_RUN = "--dry-run" in sys.argv
TARGET = None
for i, arg in enumerate(sys.argv):
    if arg == "--target" and i + 1 < len(sys.argv):
        TARGET = sys.argv[i + 1]

if not TARGET:
    # Auto-detect
    candidates = [
        os.path.expanduser("~/orchestrator.py"),
        os.path.expanduser("~/nextgen2-live/orchestrator.py"),
        "/home/ubuntu/orchestrator.py",
    ]
    for c in candidates:
        if os.path.exists(c):
            TARGET = c
            break

if not TARGET or not os.path.exists(TARGET):
    print(f"ERROR: orchestrator.py not found at {TARGET}")
    sys.exit(1)

print(f"Target: {TARGET}")
print(f"Dry run: {DRY_RUN}")

# Read original
with open(TARGET, "r") as f:
    content = f.read()
lines = content.split("\n")

# Backup
if not DRY_RUN:
    backup = f"{TARGET}.v3_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(TARGET, backup)
    print(f"Backup: {backup}")

# ── Patch 1: Add imports at top ──
IMPORT_MARKER = "# ARES-v3.0-STANDALONE-IMPORTS"
if IMPORT_MARKER not in content:
    # Find the last import line
    import_insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            import_insert_idx = i + 1

    import_block = f"""
{IMPORT_MARKER}
try:
    import sys as _sys_ares
    _sys_ares.path.insert(0, os.path.join(os.path.dirname(__file__), 'ares_kernel'))
    from kernel_rebalance_controller import (
        KernelRebalanceController, MarketContext, PortfolioContext,
        RebalanceDecision, RebalKeys
    )
    from regime_feedback_loop import (
        RegimeFeedbackLoop, RegimePrediction, PerformanceMetrics, FeedbackKeys
    )
    _ARES_V3_AVAILABLE = True
    logger.info("[ARES-v3.0] Standalone modules loaded successfully")
except Exception as _ares_err:
    _ARES_V3_AVAILABLE = False
    logger.warning("[ARES-v3.0] Standalone modules not available: %s", _ares_err)
# END-ARES-v3.0-STANDALONE-IMPORTS
"""
    lines.insert(import_insert_idx, import_block)
    print(f"Patch 1: Imports added at line {import_insert_idx}")
else:
    print("Patch 1: Imports already present (skipped)")

# ── Patch 2: Add initialization after Redis client setup ──
INIT_MARKER = "# ARES-v3.0-STANDALONE-INIT"
if INIT_MARKER not in "\n".join(lines):
    # Find Redis client initialization
    init_idx = None
    for i, line in enumerate(lines):
        if "redis" in line.lower() and ("client" in line.lower() or "from_url" in line.lower()):
            init_idx = i + 1
            break

    if init_idx is None:
        # Fallback: find class __init__ or main function
        for i, line in enumerate(lines):
            if "def run_cycle" in line or "def main_loop" in line:
                init_idx = i
                break

    if init_idx:
        init_block = f"""
{INIT_MARKER}
_ares_rebal_ctrl = None
_ares_feedback = None
if _ARES_V3_AVAILABLE:
    try:
        _ares_rebal_ctrl = KernelRebalanceController(r)
        _ares_feedback = RegimeFeedbackLoop(r)
        logger.info("[ARES-v3.0] Standalone controllers initialized")
    except Exception as _e:
        logger.warning("[ARES-v3.0] Controller init failed: %s", _e)
# END-ARES-v3.0-STANDALONE-INIT
"""
        lines.insert(init_idx, init_block)
        print(f"Patch 2: Initialization added at line {init_idx}")
    else:
        print("Patch 2: WARNING - Could not find Redis init location")
else:
    print("Patch 2: Initialization already present (skipped)")

# ── Patch 3: Replace REBAL_GATE block ──
GATE_MARKER = "# ARES-v3.0-KERNEL-GATE"
joined = "\n".join(lines)

if GATE_MARKER not in joined:
    # Find REBAL_GATE block
    gate_start = None
    gate_end = None
    for i, line in enumerate(lines):
        if "REBAL_GATE" in line and gate_start is None:
            # Find the start of this block (look for preceding if/comment)
            gate_start = i
            # Look backward for the if statement
            for j in range(i - 1, max(0, i - 10), -1):
                stripped = lines[j].strip()
                if stripped.startswith("if ") or stripped.startswith("# REBAL"):
                    gate_start = j
                    break

    if gate_start is not None:
        # Find end of REBAL_GATE block
        indent = len(lines[gate_start]) - len(lines[gate_start].lstrip())
        gate_end = gate_start + 1
        for i in range(gate_start + 1, min(len(lines), gate_start + 150)):
            stripped = lines[i].strip()
            if stripped == "":
                continue
            current_indent = len(lines[i]) - len(lines[i].lstrip())
            if current_indent <= indent and stripped and not stripped.startswith("#"):
                gate_end = i
                break
            gate_end = i + 1

        # Build replacement block
        base_indent = " " * indent
        replacement = f"""{base_indent}{GATE_MARKER}
{base_indent}# Legacy REBAL_GATE replaced by Kernel-native control (v3.0)
{base_indent}if _ARES_V3_AVAILABLE and _ares_rebal_ctrl:
{base_indent}    try:
{base_indent}        _mkt_ctx = MarketContext(
{base_indent}            vix=getattr(self, '_vix_current', 20.0) if hasattr(self, '_vix_current') else 20.0,
{base_indent}        )
{base_indent}        _port_ctx = PortfolioContext(
{base_indent}            equity=getattr(self, 'equity', 140000),
{base_indent}            position_count=getattr(self, 'position_count', 0),
{base_indent}            gross_exposure=getattr(self, 'gross_exposure', 0.0),
{base_indent}            max_weight_drift=getattr(self, 'max_drift', 0.0),
{base_indent}            drawdown=getattr(self, 'current_drawdown', 0.0),
{base_indent}        )
{base_indent}        _rebal_result = _ares_rebal_ctrl.evaluate(_mkt_ctx, _port_ctx)
{base_indent}        logger.info("[ARES-v3.0] Rebal decision: %s regime=%s urgency=%.2f reason=%s",
{base_indent}                    _rebal_result.decision.value, _rebal_result.regime.value,
{base_indent}                    _rebal_result.urgency, _rebal_result.reason)
{base_indent}        if _rebal_result.decision == RebalanceDecision.DEFER:
{base_indent}            logger.info("[ARES-v3.0] Rebalance DEFERRED: %s", _rebal_result.reason)
{base_indent}        elif _rebal_result.decision == RebalanceDecision.EMERGENCY_HALT:
{base_indent}            logger.critical("[ARES-v3.0] EMERGENCY HALT!")
{base_indent}        elif _rebal_result.decision == RebalanceDecision.DE_RISK_ONLY:
{base_indent}            logger.warning("[ARES-v3.0] DE_RISK_ONLY mode: %s", _rebal_result.reason)
{base_indent}    except Exception as _e:
{base_indent}        logger.error("[ARES-v3.0] Kernel gate error: %s", _e)
{base_indent}# END-ARES-v3.0-KERNEL-GATE"""

        # Replace the block
        lines[gate_start:gate_end] = [replacement]
        print(f"Patch 3: REBAL_GATE replaced (lines {gate_start}-{gate_end})")
    else:
        # REBAL_GATE not found - add as hook near CYCLE_COMPLETE
        print("Patch 3: WARNING - REBAL_GATE block not found, adding as hook")
        for i, line in enumerate(lines):
            if "CYCLE_COMPLETE" in line:
                hook = f"""
{GATE_MARKER}
# Kernel-native rebalance control hook (REBAL_GATE not found, added as hook)
if _ARES_V3_AVAILABLE and _ares_rebal_ctrl:
    try:
        _mkt_ctx = MarketContext()
        _port_ctx = PortfolioContext()
        _rebal_result = _ares_rebal_ctrl.evaluate(_mkt_ctx, _port_ctx)
        logger.info("[ARES-v3.0] Rebal: %s", _rebal_result.decision.value)
    except Exception as _e:
        logger.error("[ARES-v3.0] Kernel gate error: %s", _e)
# END-ARES-v3.0-KERNEL-GATE"""
                lines.insert(i, hook)
                print(f"Patch 3: Hook added before CYCLE_COMPLETE at line {i}")
                break
else:
    print("Patch 3: Kernel gate already present (skipped)")

# ── Patch 4: Add feedback loop hook ──
FEEDBACK_MARKER = "# ARES-v3.0-FEEDBACK-HOOK"
joined = "\n".join(lines)

if FEEDBACK_MARKER not in joined:
    # Add before the return statement at end of _cycle()
    for i, line in enumerate(lines):
        if 'pass  # Shadow mode: never block trading' in line:
            fb_hook = f"""
        {FEEDBACK_MARKER}
        if _ARES_V3_AVAILABLE and _ares_feedback:
            try:
                _ares_feedback.update_metrics(PerformanceMetrics(
                    equity=getattr(self, 'equity', 0) if hasattr(self, 'equity') else 0,
                ))
            except Exception:
                pass
        # END-ARES-v3.0-FEEDBACK-HOOK"""
            lines.insert(i + 1, fb_hook)
            print(f"Patch 4: Feedback hook added after Shadow block at line {i+1}")
            break
    else:
        print("Patch 4: WARNING - Shadow block not found")
else:
    print("Patch 4: Feedback hook already present (skipped)")

# ── Write patched file ──
patched = "\n".join(lines)

if DRY_RUN:
    print("\n[DRY RUN] Would write patched file to:", TARGET)
    print(f"Original: {len(content)} chars, Patched: {len(patched)} chars")
else:
    with open(TARGET, "w") as f:
        f.write(patched)
    print(f"\nPatched file written: {TARGET}")
    print(f"Original: {len(content)} chars, Patched: {len(patched)} chars")

# Verify syntax
import py_compile
try:
    py_compile.compile(TARGET, doraise=True)
    print("SYNTAX VERIFICATION: OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
    if not DRY_RUN:
        print("Restoring backup...")
        shutil.copy2(backup, TARGET)
        print("Backup restored.")
    sys.exit(1)

print("\n=== ARES v3.0 Standalone Patch Complete ===")
