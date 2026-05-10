#!/usr/bin/env python3
"""
send_selfheal_telegram.py에 ESCAPE MODE ON/OFF 렌더링 추가 패치
"""
from pathlib import Path

TELEGRAM_PATH = "/home/ubuntu/AUB/experiment_system/tools/send_selfheal_telegram.py"

# 읽기
content = Path(TELEGRAM_PATH).read_text()

# 1. ESCAPE MODE 렌더링 함수 추가 (format_review_reject 함수 뒤에)
escape_render_func = '''
def format_escape_mode_on(payload: Dict[str, Any]) -> str:
    """ESCAPE_MODE_ON 알림 메시지 포맷"""
    mx_ewma = payload.get("mx_ewma", 0)
    alpha = payload.get("alpha", 0.3)
    ewma = payload.get("ewma", {})
    explore = payload.get("explore_ratio", 0.3)
    note = payload.get("note", "")
    
    # EWMA 축별 값
    ewma_lines = []
    for k, v in ewma.items():
        emoji = "🔴" if v >= 0.3 else "🟡" if v >= 0.15 else "🟢"
        ewma_lines.append(f"  {emoji} {k}: {v:.2f}")
    ewma_str = "\\n".join(ewma_lines) if ewma_lines else "  (no data)"
    
    lines = [
        "🚨 **ESCAPE MODE ON**",
        "",
        f"📊 Max EWMA: {mx_ewma:.4f} (α={alpha:.2f})",
        f"🔍 Explore Ratio: {explore:.0%}",
        "",
        "📈 EWMA by Axis:",
        ewma_str,
        "",
        f"📝 {note}",
    ]
    return "\\n".join(lines)


def format_escape_mode_off(payload: Dict[str, Any]) -> str:
    """ESCAPE_MODE_OFF 알림 메시지 포맷"""
    mx_ewma = payload.get("mx_ewma", 0)
    alpha = payload.get("alpha", 0.3)
    ewma = payload.get("ewma", {})
    explore = payload.get("explore_ratio", 0.3)
    duration = payload.get("duration", "N/A")
    note = payload.get("note", "")
    
    # EWMA 축별 값
    ewma_lines = []
    for k, v in ewma.items():
        emoji = "🟢" if v < 0.15 else "🟡" if v < 0.3 else "🔴"
        ewma_lines.append(f"  {emoji} {k}: {v:.2f}")
    ewma_str = "\\n".join(ewma_lines) if ewma_lines else "  (no data)"
    
    lines = [
        "✅ **ESCAPE MODE OFF**",
        "",
        f"⏱️ Duration: {duration}",
        f"📊 Max EWMA: {mx_ewma:.4f} (α={alpha:.2f})",
        f"🔍 Explore Ratio: {explore:.0%} (back to normal)",
        "",
        "📈 EWMA by Axis:",
        ewma_str,
        "",
        f"📝 {note}",
    ]
    return "\\n".join(lines)

'''

# format_review_reject 함수 끝 찾기
old_format_message = "def format_message(payload: Dict[str, Any]) -> str:"
new_format_message = escape_render_func + "\ndef format_message(payload: Dict[str, Any]) -> str:"

content = content.replace(old_format_message, new_format_message)

# 2. format_message에 ESCAPE_MODE 이벤트 처리 추가
old_event_handling = '''    if event == "REVIEW_APPROVE":
        return format_review_approve(payload)
    elif event == "REVIEW_REJECT":
        return format_review_reject(payload)
    elif event in ["HEALED", "STUCK"]:
        return format_selfheal_status(payload)'''

new_event_handling = '''    if event == "REVIEW_APPROVE":
        return format_review_approve(payload)
    elif event == "REVIEW_REJECT":
        return format_review_reject(payload)
    elif event in ["HEALED", "STUCK"]:
        return format_selfheal_status(payload)
    elif event == "ESCAPE_MODE_ON":
        return format_escape_mode_on(payload)
    elif event == "ESCAPE_MODE_OFF":
        return format_escape_mode_off(payload)'''

content = content.replace(old_event_handling, new_event_handling)

# 저장
Path(TELEGRAM_PATH).write_text(content)
print(f"[OK] Patched: {TELEGRAM_PATH}")

# 검증
patched = Path(TELEGRAM_PATH).read_text()
checks = [
    ("format_escape_mode_on function", "def format_escape_mode_on" in patched),
    ("format_escape_mode_off function", "def format_escape_mode_off" in patched),
    ("ESCAPE_MODE_ON handling", '"ESCAPE_MODE_ON"' in patched),
    ("ESCAPE_MODE_OFF handling", '"ESCAPE_MODE_OFF"' in patched),
    ("Duration display", "Duration:" in patched),
]

print("\n=== Patch Verification ===")
all_ok = True
for name, ok in checks:
    status = "✅" if ok else "❌"
    print(f"  {status} {name}")
    if not ok:
        all_ok = False

if all_ok:
    print("\n[OK] All patches verified successfully!")
else:
    print("\n[WARN] Some patches may have failed")
