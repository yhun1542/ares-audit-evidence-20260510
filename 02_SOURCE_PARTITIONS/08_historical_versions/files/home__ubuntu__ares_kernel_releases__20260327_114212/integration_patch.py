"""
ARES v2.0 Integration Patch for orchestrator.py

Applies surgical patches to the production orchestrator.py to integrate
with the new ARES Kernel in SHADOW MODE.

Shadow Mode = new components LOG decisions but DON'T ENFORCE.
Existing behavior is 100% preserved.

Patches:
1. ARES Kernel import block (after existing imports)
2. Shadow Kernel State in KPI (STAGE 15)
3. Shadow Kernel Heartbeat at cycle end (before return)
4. Shadow Rebalance Job Tracking (after LIVE emit)

All patches are idempotent (safe to run multiple times).

Usage: python3 integration_patch.py [path_to_orchestrator.py]
"""

import sys
import os
import shutil
import re
from datetime import datetime

def find_orchestrator():
    """Find the production orchestrator.py"""
    candidates = [
        "/home/ubuntu/orchestrator.py",
        "/home/ubuntu/ARES-KIS-US-AUTOPILOT/services/nextgen2/orchestrator.py",
        "/home/ubuntu/codebase/ares-nextgen/nextgen2/orchestrator.py",
    ]
    if len(sys.argv) > 1:
        candidates.insert(0, sys.argv[1])
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def apply_patches(filepath):
    """Apply all integration patches."""
    
    # Backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{filepath}.ares_v2_backup_{ts}"
    shutil.copy2(filepath, backup)
    print(f"[BACKUP] {backup}")
    
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    original_content = ''.join(lines)
    patches = 0
    
    # ═══════════════════════════════════════════════════════════
    # PATCH 1: ARES Kernel Import Block
    # Insert after "from nextgen2.bus.redis_stream import RedisStreamBus"
    # ═══════════════════════════════════════════════════════════
    IMPORT_MARKER = "# [ARES-v2.0] Kernel Integration"
    if IMPORT_MARKER not in original_content:
        import_lines = [
            '\n',
            '# [ARES-v2.0] Kernel Integration (Shadow Mode)\n',
            '# All kernel calls are wrapped in try/except and NEVER block trading.\n',
            '# Feature flag: ares:kernel:flags -> kernel_enforce = 0 (shadow)\n',
            'try:\n',
            '    import sys as _ares_sys\n',
            '    _ares_sys.path.insert(0, "/home/ubuntu/ares_kernel")\n',
            '    from kernel_client import KernelClient as _AresKernelClient\n',
            '    _ARES_KERNEL = True\n',
            'except ImportError:\n',
            '    _ARES_KERNEL = False\n',
            '\n',
        ]
        
        # Find the anchor line
        anchor = "from nextgen2.bus.redis_stream import RedisStreamBus"
        inserted = False
        for i, line in enumerate(lines):
            if anchor in line:
                # Insert after this line
                lines = lines[:i+1] + import_lines + lines[i+1:]
                inserted = True
                patches += 1
                print("[PATCH 1] ARES Kernel import block added")
                break
        if not inserted:
            print("[PATCH 1] SKIP: anchor not found")
    else:
        print("[PATCH 1] Already applied")
    
    # Rebuild content for subsequent patches
    content = ''.join(lines)
    lines = content.split('\n')
    # Keep newlines for line-based operations
    
    # ═══════════════════════════════════════════════════════════
    # PATCH 2: Shadow Kernel State in KPI (STAGE 15)
    # ═══════════════════════════════════════════════════════════
    KPI_MARKER = '"ares_kernel_state"'
    if KPI_MARKER not in content:
        kpi_anchor = '"rebal_gate_blocked_notional"'
        if kpi_anchor in content:
            # Find the line containing this anchor
            for i, line in enumerate(lines):
                if kpi_anchor in line:
                    # Add after this line
                    indent = "            "
                    new_lines = [
                        f'{indent}# [ARES-v2.0] Shadow kernel state in KPI',
                        f'{indent}"ares_kernel_state": "N/A",',
                        f'{indent}"ares_kernel_available": _ARES_KERNEL if "_ARES_KERNEL" in dir() else False,',
                    ]
                    lines = lines[:i+1] + new_lines + lines[i+1:]
                    patches += 1
                    print("[PATCH 2] Shadow kernel state added to KPI")
                    break
        else:
            print("[PATCH 2] SKIP: KPI anchor not found")
    else:
        print("[PATCH 2] Already applied")
    
    # Rebuild
    content = '\n'.join(lines)
    lines = content.split('\n')
    
    # ═══════════════════════════════════════════════════════════
    # PATCH 3: Shadow Kernel Heartbeat before return statement
    # Insert BEFORE "return {" at end of _cycle
    # ═══════════════════════════════════════════════════════════
    HB_MARKER = "# [ARES-v2.0] Shadow kernel heartbeat"
    if HB_MARKER not in content:
        # Find the return { after CYCLE_COMPLETE
        # Look for the pattern: return {\n            "ok": errors == 0
        for i, line in enumerate(lines):
            if '"ok": errors == 0' in line and i > 0 and 'return {' in lines[i-1]:
                # Insert before "return {"
                return_line_idx = i - 1
                indent = "        "
                hb_lines = [
                    f'{indent}# [ARES-v2.0] Shadow kernel heartbeat + metrics export',
                    f'{indent}if _ARES_KERNEL:',
                    f'{indent}    try:',
                    f'{indent}        _ares_r = self._redis',
                    f'{indent}        _ares_r.hset("ares:shadow:cycle_metrics", mapping={{',
                    f'{indent}            "last_cycle_ts": str(time.time()),',
                    f'{indent}            "cycle_id": cycle_id,',
                    f'{indent}            "emitted": str(emitted),',
                    f'{indent}            "mode": mode,',
                    f'{indent}            "regime": regime,',
                    f'{indent}            "gate_state": _rebal_gate_state,',
                    f'{indent}            "version": VERSION,',
                    f'{indent}        }})',
                    f'{indent}        _ares_r.set("ares:kernel:heartbeat", str(int(time.time() * 1000)), ex=120)',
                    f'{indent}    except Exception:',
                    f'{indent}        pass  # Shadow mode: never block trading',
                    '',
                ]
                lines = lines[:return_line_idx] + hb_lines + lines[return_line_idx:]
                patches += 1
                print("[PATCH 3] Shadow kernel heartbeat added")
                break
        else:
            print("[PATCH 3] SKIP: return anchor not found")
    else:
        print("[PATCH 3] Already applied")
    
    # Rebuild
    content = '\n'.join(lines)
    lines = content.split('\n')
    
    # ═══════════════════════════════════════════════════════════
    # PATCH 4: Shadow Rebalance Job Tracking (after LIVE emit)
    # Insert after "r.expire(emitted_key, 86400)"
    # ═══════════════════════════════════════════════════════════
    REBAL_MARKER = "# [ARES-v2.0] Shadow rebalance tracking"
    if REBAL_MARKER not in content:
        emit_anchor = 'r.expire(emitted_key, 86400)'
        if emit_anchor in content:
            for i, line in enumerate(lines):
                if emit_anchor in line:
                    indent = "                    "
                    track_lines = [
                        f'{indent}# [ARES-v2.0] Shadow rebalance tracking',
                        f'{indent}if _ARES_KERNEL:',
                        f'{indent}    try:',
                        f'{indent}        r.xadd("ares:shadow:emit_log", {{',
                        f'{indent}            "symbol": intent.symbol,',
                        f'{indent}            "side": intent.side,',
                        f'{indent}            "delta": str(intent.delta_shares),',
                        f'{indent}            "notional": str(intent.notional_usd),',
                        f'{indent}            "stream_id": str(stream_id),',
                        f'{indent}            "cycle_id": cycle_id,',
                        f'{indent}            "gate_state": _rebal_gate_state,',
                        f'{indent}            "ts": str(time.time()),',
                        f'{indent}        }}, maxlen=10000, approximate=True)',
                        f'{indent}    except Exception:',
                        f'{indent}        pass  # Shadow: never block',
                    ]
                    lines = lines[:i+1] + track_lines + lines[i+1:]
                    patches += 1
                    print("[PATCH 4] Shadow rebalance tracking added")
                    break
        else:
            print("[PATCH 4] SKIP: emit anchor not found")
    else:
        print("[PATCH 4] Already applied")
    
    # Write final result
    final_content = '\n'.join(lines)
    if final_content != original_content:
        with open(filepath, 'w') as f:
            f.write(final_content)
        print(f"\n[SUCCESS] {patches} patches applied to {filepath}")
        print(f"[MODE] SHADOW — all hooks are log-only, no enforcement")
        print(f"[NEXT] Restart nextgen2-live to activate")
    else:
        print(f"\n[INFO] No changes needed — all patches already applied")
    
    return patches


if __name__ == "__main__":
    filepath = find_orchestrator()
    if not filepath:
        print("[ERROR] orchestrator.py not found")
        sys.exit(1)
    
    print(f"ARES v2.0 Integration Patch")
    print(f"Target: {filepath}")
    print(f"Mode: SHADOW (log-only, zero enforcement)")
    print("=" * 60)
    
    apply_patches(filepath)
