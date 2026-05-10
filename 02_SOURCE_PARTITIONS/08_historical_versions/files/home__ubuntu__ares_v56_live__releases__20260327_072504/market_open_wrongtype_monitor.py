#!/usr/bin/env python3
"""
ARES v56 Market-Open WRONGTYPE Hardened Monitor
================================================
장중 첫 1시간 동안 WRONGTYPE 재발을 집중 감시하는 강화 모니터링 스크립트.

실행 시점: 장 개시 5분 전 (ET 09:25 = UTC 13:25)
감시 기간: 60분 (장 개시 전 5분 ~ 장 개시 후 55분)
감시 주기: 10초
알림 채널: 텔레그램 + wall + 로그

크론 설정:
  25 13 * * 1-5 cd /home/ubuntu && python3 /home/ubuntu/market_open_wrongtype_monitor.py >> /home/ubuntu/logs/wrongtype_monitor.log 2>&1
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

# ─── Configuration ───────────────────────────────────────────────────────────
DURATION_MIN = int(os.getenv("WRONGTYPE_MONITOR_DURATION_MIN", "60"))
INTERVAL_SEC = int(os.getenv("WRONGTYPE_MONITOR_INTERVAL_SEC", "10"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LOG_DIR = os.path.expanduser("~/logs")
ALERT_LOG = os.path.join(LOG_DIR, "wrongtype_monitor_alerts.log")

# Keys to monitor for type correctness
MONITORED_KEYS = {
    "ares:v55:market:vix": "list",
    "ares:v55:market:spy": "list",
    "ares:v55:market:qqq": "list",
    "ares:v55:market:iwm": "list",
    "ares:v55:market:dia": "list",
    "ares:v55:market:tlt": "list",
    "ares:v55:market:gld": "list",
    "ares:v55:market:vxx": "list",
    "ares:v55:market:hyg": "list",
    "ares:v55:market:breadth": "list",
    "ares:v55:market:sector_rotation": "list",
    "ares:v55:market:flow_imbalance": "list",
    "ares:v55:market:overnight_gap": "list",
    "ares:v55:market:intraday_vol": "list",
}

# Circuit breaker thresholds
MAX_WRONGTYPE_ERRORS = 3          # 3회 WRONGTYPE → EMERGENCY
MAX_HALT_ERRORS = 5               # 5회 HALT → EMERGENCY
MAX_CONSECUTIVE_RESTARTS = 3      # 3회 연속 재시작 → EMERGENCY
QUARANTINE_CHECK_INTERVAL = 6     # 6 ticks (60초)마다 quarantine 키 확인

# ─── Helpers ─────────────────────────────────────────────────────────────────
def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def redis_cmd(*args):
    """Execute redis-cli command and return output."""
    cmd = ["redis-cli", "-a", REDIS_PASSWORD, "--no-auth-warning"] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"

def pm2_jlist():
    """Get PM2 process list as JSON."""
    try:
        result = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=10)
        return json.loads(result.stdout)
    except Exception:
        return []

def get_process_info(name):
    """Get specific PM2 process info."""
    for proc in pm2_jlist():
        if proc.get("name") == name:
            return {
                "status": proc.get("pm2_env", {}).get("status", "unknown"),
                "restarts": proc.get("pm2_env", {}).get("restart_time", 0),
                "uptime": proc.get("pm2_env", {}).get("pm_uptime", 0),
                "memory": proc.get("monit", {}).get("memory", 0),
            }
    return None

def count_recent_errors(log_path, pattern, since_sec=60):
    """Count error pattern occurrences in recent log lines."""
    try:
        result = subprocess.run(
            ["tail", "-200", log_path],
            capture_output=True, text=True, timeout=5
        )
        count = 0
        cutoff = time.time() - since_sec
        for line in result.stdout.splitlines():
            if pattern in line:
                count += 1
        return count
    except Exception:
        return -1

def send_telegram(message):
    """Send alert via Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[{ts()}] Telegram send failed: {e}")
        return False

def alert(level, message):
    """Send alert via all channels."""
    full_msg = f"[{ts()}] [{level}] {message}"
    print(full_msg)
    
    # Log to file
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(ALERT_LOG, "a") as f:
        f.write(full_msg + "\n")
    
    # Wall broadcast
    try:
        subprocess.run(["wall", full_msg], capture_output=True, timeout=3)
    except Exception:
        pass
    
    # Telegram
    emoji = {"INFO": "ℹ️", "WARN": "⚠️", "CRITICAL": "🚨", "EMERGENCY": "🔴"}.get(level, "📌")
    send_telegram(f"{emoji} <b>ARES WRONGTYPE Monitor</b>\n<code>{full_msg}</code>")

def emergency_stop(reason):
    """Emergency stop - halt ares-v56-live."""
    alert("EMERGENCY", f"EMERGENCY STOP: {reason}")
    alert("EMERGENCY", "Executing: pm2 stop ares-v56-live")
    try:
        subprocess.run(["pm2", "stop", "ares-v56-live"], capture_output=True, timeout=10)
    except Exception as e:
        alert("EMERGENCY", f"pm2 stop failed: {e}")
    
    # Set AOA_DISABLE as safety net
    redis_cmd("SET", "ares:v56:aoa_disable", "true")
    alert("EMERGENCY", "AOA_DISABLE set to true as safety net")

# ─── Main Monitor Loop ──────────────────────────────────────────────────────
def main():
    print(f"\n{'='*70}")
    print(f"ARES v56 Market-Open WRONGTYPE Hardened Monitor")
    print(f"Started: {ts()}")
    print(f"Duration: {DURATION_MIN} min, Interval: {INTERVAL_SEC} sec")
    print(f"Monitoring {len(MONITORED_KEYS)} keys")
    print(f"{'='*70}\n")
    
    alert("INFO", f"WRONGTYPE Monitor started - {DURATION_MIN}min @ {INTERVAL_SEC}s interval")
    
    # Detect Telegram config
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN:
        # Try to read from existing watchdog config
        for path in [
            os.path.expanduser("~/ares_watchdog_v4.sh"),
            os.path.expanduser("~/scripts/ares_watchdog_v1.sh"),
        ]:
            try:
                with open(path) as f:
                    content = f.read()
                    m_token = re.search(r'TELEGRAM_BOT_TOKEN[="]([^"]+)', content)
                    m_chat = re.search(r'TELEGRAM_CHAT_ID[="]([^"]+)', content)
                    if m_token and m_chat:
                        TELEGRAM_BOT_TOKEN = m_token.group(1).strip('"= ')
                        TELEGRAM_CHAT_ID = m_chat.group(1).strip('"= ')
                        break
            except Exception:
                continue
    
    # Try PM2 env
    if not TELEGRAM_BOT_TOKEN:
        try:
            result = subprocess.run(["pm2", "env", "223"], capture_output=True, text=True, timeout=5)
            for line in result.stdout.splitlines():
                if "TELEGRAM_BOT_TOKEN" in line:
                    TELEGRAM_BOT_TOKEN = line.split(":", 1)[1].strip()
                elif "TELEGRAM_CHAT_ID" in line:
                    TELEGRAM_CHAT_ID = line.split(":", 1)[1].strip()
        except Exception:
            pass
    
    start = time.time()
    end = start + DURATION_MIN * 60
    tick = 0
    wrongtype_count = 0
    halt_count = 0
    prev_restarts = None
    consecutive_restart_count = 0
    
    error_log = os.path.expanduser("~/.pm2/logs/ares-v56-live-error.log")
    
    while time.time() < end:
        tick += 1
        elapsed_min = (time.time() - start) / 60
        
        # ── 1. Key Type Check ──
        type_violations = []
        for key, expected_type in MONITORED_KEYS.items():
            actual_type = redis_cmd("TYPE", key)
            if actual_type == "none":
                continue  # Key doesn't exist yet, OK
            if actual_type != expected_type:
                type_violations.append(f"{key}: expected={expected_type}, actual={actual_type}")
                wrongtype_count += 1
        
        if type_violations:
            alert("CRITICAL", f"TYPE VIOLATION detected: {'; '.join(type_violations)}")
            if wrongtype_count >= MAX_WRONGTYPE_ERRORS:
                emergency_stop(f"WRONGTYPE count={wrongtype_count} >= {MAX_WRONGTYPE_ERRORS}")
                return
        
        # ── 2. Quarantine Key Check ──
        if tick % QUARANTINE_CHECK_INTERVAL == 0:
            quarantine_keys = redis_cmd("KEYS", "ares:v55:market:*:wrongtype:*")
            if quarantine_keys and quarantine_keys != "":
                q_count = len(quarantine_keys.splitlines())
                alert("WARN", f"Quarantine keys found: {q_count} - {quarantine_keys}")
        
        # ── 3. HALT/WRONGTYPE Error Log Check ──
        wrongtype_in_log = count_recent_errors(error_log, "WRONGTYPE", since_sec=INTERVAL_SEC * 2)
        halt_in_log = count_recent_errors(error_log, "HALT", since_sec=INTERVAL_SEC * 2)
        
        if wrongtype_in_log > 0:
            wrongtype_count += wrongtype_in_log
            alert("CRITICAL", f"WRONGTYPE in error log: {wrongtype_in_log} occurrences")
            if wrongtype_count >= MAX_WRONGTYPE_ERRORS:
                emergency_stop(f"WRONGTYPE total={wrongtype_count} >= {MAX_WRONGTYPE_ERRORS}")
                return
        
        if halt_in_log > 0:
            halt_count += halt_in_log
            alert("CRITICAL", f"HALT in error log: {halt_in_log} occurrences")
            if halt_count >= MAX_HALT_ERRORS:
                emergency_stop(f"HALT total={halt_count} >= {MAX_HALT_ERRORS}")
                return
        
        # ── 4. Process Health Check ──
        ares_info = get_process_info("ares-v56-live")
        exec_info = get_process_info("order-intent-executor")
        
        if ares_info is None or ares_info["status"] != "online":
            alert("CRITICAL", f"ares-v56-live is NOT online: {ares_info}")
        
        if exec_info is None or exec_info["status"] != "online":
            alert("WARN", f"order-intent-executor is NOT online: {exec_info}")
        
        # Restart detection
        if ares_info:
            current_restarts = ares_info["restarts"]
            if prev_restarts is not None and current_restarts > prev_restarts:
                consecutive_restart_count += (current_restarts - prev_restarts)
                alert("WARN", f"ares-v56-live restarted! restarts={current_restarts} (delta={current_restarts - prev_restarts})")
                if consecutive_restart_count >= MAX_CONSECUTIVE_RESTARTS:
                    emergency_stop(f"Consecutive restarts={consecutive_restart_count} >= {MAX_CONSECUTIVE_RESTARTS}")
                    return
            prev_restarts = current_restarts
        
        # ── 5. Stream Health Check ──
        stream_len = redis_cmd("XLEN", "ares:v55:order-intents")
        lag = "?"
        try:
            xinfo = redis_cmd("XINFO", "GROUPS", "ares:v55:order-intents")
            if "lag" in xinfo:
                lag_match = re.search(r"lag\n(\d+)", xinfo)
                if lag_match:
                    lag = lag_match.group(1)
        except Exception:
            pass
        
        # ── 6. VIX/SPY Data Freshness ──
        vix_len = redis_cmd("LLEN", "ares:v55:market:vix")
        spy_len = redis_cmd("LLEN", "ares:v55:market:spy")
        
        # ── Status Output ──
        status_parts = [
            f"T+{elapsed_min:.1f}m",
            f"wt={wrongtype_count}",
            f"halt={halt_count}",
            f"rst={consecutive_restart_count}",
        ]
        if ares_info:
            status_parts.append(f"ares={ares_info['status']}/{ares_info['memory']//1048576}MB")
        status_parts.extend([
            f"vix={vix_len}",
            f"spy={spy_len}",
            f"stream={stream_len}",
            f"lag={lag}",
        ])
        
        print(f"[{ts()}] {' | '.join(status_parts)}")
        
        # ── 7. Periodic Summary (every 5 min) ──
        if tick % (300 // INTERVAL_SEC) == 0:
            summary = (
                f"📊 WRONGTYPE Monitor T+{elapsed_min:.0f}m Summary:\n"
                f"  WRONGTYPE errors: {wrongtype_count}\n"
                f"  HALT errors: {halt_count}\n"
                f"  Restarts: {consecutive_restart_count}\n"
                f"  VIX/SPY: {vix_len}/{spy_len}\n"
                f"  Stream: {stream_len}, Lag: {lag}"
            )
            alert("INFO", summary)
        
        time.sleep(INTERVAL_SEC)
    
    # ── Final Report ──
    final_report = (
        f"✅ WRONGTYPE Monitor completed ({DURATION_MIN}min)\n"
        f"  Total WRONGTYPE errors: {wrongtype_count}\n"
        f"  Total HALT errors: {halt_count}\n"
        f"  Total restarts: {consecutive_restart_count}\n"
        f"  Final VIX/SPY: {vix_len}/{spy_len}\n"
        f"  Verdict: {'PASS ✅' if wrongtype_count == 0 and halt_count == 0 else 'FAIL ❌'}"
    )
    alert("INFO", final_report)
    
    print(f"\n{'='*70}")
    print(f"Monitor completed at {ts()}")
    print(f"{'='*70}")

if __name__ == "__main__":
    main()
