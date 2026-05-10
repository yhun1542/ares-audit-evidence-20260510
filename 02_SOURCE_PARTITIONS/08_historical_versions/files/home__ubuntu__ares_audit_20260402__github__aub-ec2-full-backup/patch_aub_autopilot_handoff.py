#!/usr/bin/env python3
"""
aub_autopilot.py에 write_handoff 후킹 추가
- 실행 완료 후 baseline/HANDOFF.md 갱신
"""

import re

# Read the file
with open('experiment_system/tools/aub_autopilot.py', 'r') as f:
    content = f.read()

# Check if already patched
if 'write_handoff_strategy' in content:
    print("Already patched!")
    exit(0)

# Add the write_handoff_strategy function after imports
handoff_func = '''
# ----------------------------
# HANDOFF updater (strategy)
# ----------------------------

def write_handoff_strategy(rows, champion_info, tag):
    """Write baseline/HANDOFF.md after strategy autopilot run"""
    from pathlib import Path
    import json
    
    out = Path("baseline/HANDOFF.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    
    lines = []
    lines.append(f"# HANDOFF (auto) - {tag}")
    lines.append("")
    
    # Current Champion
    lines.append("## Current Champion")
    champ_path = Path("baseline/champion_v5_latest.json")
    if not champ_path.exists():
        xs = list(Path("baseline").glob("champion_v5_*.json"))
        if xs:
            xs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            champ_path = xs[0]
    
    if champ_path.exists():
        try:
            d = json.loads(champ_path.read_text())
            lines.append(f"- file: `{champ_path}`")
            # Extract metrics from folds
            tuning_sharpes = [f.get("oos_sharpe") for f in d.get("folds", []) if f.get("fold", 0) <= 16 and f.get("oos_sharpe") is not None]
            lockbox_sharpes = [f.get("oos_sharpe") for f in d.get("folds", []) if f.get("fold", 0) >= 17 and f.get("oos_sharpe") is not None]
            if tuning_sharpes:
                lines.append(f"- tuning_mean/min: {sum(tuning_sharpes)/len(tuning_sharpes)} / {min(tuning_sharpes)}")
            if lockbox_sharpes:
                lines.append(f"- lockbox_mean/min: {sum(lockbox_sharpes)/len(lockbox_sharpes)} / {min(lockbox_sharpes)}")
        except Exception as e:
            lines.append(f"- (error reading champion: {e})")
    else:
        lines.append("- (no champion found)")
    lines.append("")
    
    # Last PASS Candidate
    lines.append("## Last PASS Candidate")
    passes = [(eid, ev, dest) for (eid, ev, dest) in rows if ev.verdict == "PASS"]
    if passes:
        passes.sort(key=lambda x: (x[1].lock_mean, x[1].lock_min), reverse=True)
        best = passes[0]
        lines.append(f"- experiment: `{best[0]}`")
        lines.append(f"- file: `{best[2]}`")
        lines.append(f"- lock_mean/min: {best[1].lock_mean:.4f} / {best[1].lock_min:.4f}")
        if best[1].fold14_mdd:
            lines.append(f"- fold14_mdd: {best[1].fold14_mdd:.2%}")
    else:
        lines.append("- (no PASS candidates)")
    lines.append("")
    
    # Champion Promotion Info
    if champion_info:
        lines.append("## Champion Promotion")
        lines.append(f"- winner: `{champion_info.get('winner', 'N/A')}`")
        lines.append(f"- champion_path: `{champion_info.get('champion_path', 'N/A')}`")
        lines.append(f"- improvement: +{champion_info.get('improvement', 0):.4f}")
        lines.append("")
    
    # INCONCLUSIVE
    lines.append("## INCONCLUSIVE Now")
    inconcs = [eid for (eid, ev, _) in rows if ev.verdict == "INCONCLUSIVE"]
    if inconcs:
        for i in inconcs:
            lines.append(f"- {i}")
    else:
        lines.append("- (none)")
    lines.append("")
    
    # Next Commands
    lines.append("## Next Commands (3 lines)")
    lines.append("```bash")
    lines.append("python3 experiment_system/tools/aub_autopilot.py --registry experiment_system/config/module_registry.yaml --run --select-best")
    lines.append("python3 experiment_system/tools/system_autopilot.py --spec experiment_system/config/system_candidates.yaml --run")
    lines.append("python3 experiment_system/tools/aub_next_fix.py --registry experiment_system/config/module_registry.yaml --output baseline/reports/next_fix.md")
    lines.append("```")
    
    out.write_text("\\n".join(lines), encoding="utf-8")
    print(f"[OK] wrote: baseline/HANDOFF.md")

'''

# Find the position after imports (after the last import statement)
import_match = re.search(r'^(import .+|from .+ import .+)\n(?!import|from)', content, re.MULTILINE)
if import_match:
    insert_pos = import_match.end()
    content = content[:insert_pos] + handoff_func + content[insert_pos:]

# Add the call to write_handoff_strategy at the end of main()
# Find the pattern before "if __name__"
main_end_pattern = r'(if champion_info:\s*\n\s*print\(f"\\n=== V5\.1 CHAMPION PROMOTION ==="\)\s*\n\s*print\(f"Winner: \{champion_info\[\'winner\'\]\}"\)\s*\n\s*print\(f"Champion: \{champion_info\[\'champion_path\'\]\}"\)\s*\n\s*print\(f"Improvement: \+\{champion_info\[\'improvement\'\]:.4f\}"\))'

handoff_call = '''\\1
    
    # Write HANDOFF.md (one truth)
    if args.run and (not args.dry_run):
        write_handoff_strategy(rows, champion_info, tag)'''

content = re.sub(main_end_pattern, handoff_call, content)

# Write back
with open('experiment_system/tools/aub_autopilot.py', 'w') as f:
    f.write(content)

print("Patch applied successfully!")
