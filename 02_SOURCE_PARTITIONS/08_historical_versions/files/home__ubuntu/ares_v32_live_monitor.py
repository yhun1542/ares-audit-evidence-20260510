#!/usr/bin/env python3
"""
ARES v3.2 — Live Market Monitoring Script
==========================================
미국 시장 개장 시점에 실행하여 다음을 검증합니다:
1. ares:killswitch:* Redis 키 생성 여부 (pre_cycle_check 동작 확인)
2. ares:killswitch:dashboard 내용 검증
3. force-sell-executor Shadow Mode 동작 로그
4. nextgen2-live 패치 동작 로그 (PATCHES-v3.2 관련)
5. quarantine 상태 (active_set, 이벤트 스트림)
6. PM2 서비스 안정성 (재시작 횟수, 에러 로그)
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
REPORT_PATH = "/home/ubuntu/ares_v32_live_verification_report.json"
LOG_PATH = "/home/ubuntu/ares_v32_live_verification.log"

def log(msg):
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")

def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"

def check_redis():
    """Redis 키 상태 확인"""
    import redis
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return {"error": "REDIS_URL not set"}
    
    r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=5)
    r.ping()
    
    results = {}
    
    # killswitch 키
    ks_keys = r.keys("ares:killswitch:*")
    results["killswitch_keys"] = ks_keys
    results["killswitch_key_count"] = len(ks_keys)
    
    # killswitch dashboard
    ks_dash = r.get("ares:killswitch:dashboard")
    if ks_dash:
        try:
            results["killswitch_dashboard"] = json.loads(ks_dash)
        except:
            results["killswitch_dashboard"] = ks_dash
    else:
        # dashboard가 hash 타입일 수도 있음
        t = r.type("ares:killswitch:dashboard")
        if t == "hash":
            results["killswitch_dashboard"] = r.hgetall("ares:killswitch:dashboard")
        else:
            results["killswitch_dashboard"] = None
    
    # quarantine 상태
    q_keys = r.keys("ares:quarantine:*")
    results["quarantine_keys"] = q_keys
    results["quarantine_key_count"] = len(q_keys)
    
    # quarantine config
    qcfg_type = r.type("ares:quarantine:config")
    if qcfg_type == "hash":
        results["quarantine_config"] = r.hgetall("ares:quarantine:config")
    elif qcfg_type == "string":
        results["quarantine_config"] = r.get("ares:quarantine:config")
    
    # quarantine active_set
    results["quarantine_active_set"] = list(r.smembers("ares:quarantine:active_set")) if r.exists("ares:quarantine:active_set") else []
    
    # force-sell 관련 키
    fs_keys = r.keys("ares:force_sell:*")
    results["force_sell_keys"] = fs_keys
    
    # 최근 quarantine 이벤트 스트림
    stream_key = "ares:live:pos_truth_drift"
    if r.exists(stream_key):
        entries = r.xrevrange(stream_key, count=5)
        results["pos_truth_drift_stream"] = [{"id": e[0], "data": e[1]} for e in entries]
    else:
        results["pos_truth_drift_stream"] = []
    
    return results

def check_pm2():
    """PM2 서비스 상태 확인"""
    results = {}
    
    # nextgen2-live 상태
    out = run_cmd("pm2 jlist 2>/dev/null")
    try:
        pm2_list = json.loads(out)
        for proc in pm2_list:
            name = proc.get("name", "")
            if name in ["nextgen2-live", "force-sell-executor"]:
                results[name] = {
                    "status": proc.get("pm2_env", {}).get("status"),
                    "restarts": proc.get("pm2_env", {}).get("restart_time"),
                    "uptime": proc.get("pm2_env", {}).get("pm_uptime"),
                    "pid": proc.get("pid"),
                    "memory": proc.get("monit", {}).get("memory"),
                    "cpu": proc.get("monit", {}).get("cpu"),
                }
    except:
        results["pm2_parse_error"] = out[:500]
    
    return results

def check_logs():
    """패치 관련 로그 확인"""
    results = {}
    
    # nextgen2-live 로그에서 PATCHES-v3.2 관련
    out = run_cmd("grep 'PATCHES-v3.2\\|QUARANTINE\\|KILLSWITCH\\|KS-00' /home/ubuntu/.pm2/logs/nextgen2-live-out.log 2>/dev/null | tail -30")
    results["nextgen2_patch_logs"] = out.split("\n") if out else []
    
    # nextgen2-live 에러 로그
    out = run_cmd("grep -i 'error\\|exception\\|traceback' /home/ubuntu/.pm2/logs/nextgen2-live-error.log 2>/dev/null | tail -10")
    results["nextgen2_error_logs"] = out.split("\n") if out else []
    
    # force-sell-executor 로그
    out = run_cmd("tail -30 /home/ubuntu/.pm2/logs/force-sell-executor-out.log 2>/dev/null")
    results["force_sell_logs"] = out.split("\n") if out else []
    
    # force-sell-executor 에러 로그
    out = run_cmd("tail -10 /home/ubuntu/.pm2/logs/force-sell-executor-error.log 2>/dev/null")
    results["force_sell_error_logs"] = out.split("\n") if out else []
    
    return results

def main():
    log("=" * 60)
    log("ARES v3.2 Live Market Verification — START")
    log("=" * 60)
    
    report = {
        "timestamp": datetime.now(KST).isoformat(),
        "checks": {}
    }
    
    # 1. Redis 키 확인
    log("Checking Redis keys...")
    try:
        redis_result = check_redis()
        report["checks"]["redis"] = redis_result
        log(f"  killswitch keys: {redis_result.get('killswitch_key_count', 0)}")
        log(f"  killswitch dashboard: {redis_result.get('killswitch_dashboard')}")
        log(f"  quarantine keys: {redis_result.get('quarantine_key_count', 0)}")
        log(f"  quarantine active_set: {redis_result.get('quarantine_active_set', [])}")
        log(f"  force_sell keys: {redis_result.get('force_sell_keys', [])}")
    except Exception as e:
        log(f"  Redis check ERROR: {e}")
        report["checks"]["redis"] = {"error": str(e)}
    
    # 2. PM2 상태 확인
    log("Checking PM2 services...")
    pm2_result = check_pm2()
    report["checks"]["pm2"] = pm2_result
    for name, info in pm2_result.items():
        if isinstance(info, dict):
            log(f"  {name}: status={info.get('status')}, restarts={info.get('restarts')}")
    
    # 3. 로그 확인
    log("Checking patch-related logs...")
    log_result = check_logs()
    report["checks"]["logs"] = log_result
    patch_log_count = len([l for l in log_result.get("nextgen2_patch_logs", []) if l.strip()])
    error_count = len([l for l in log_result.get("nextgen2_error_logs", []) if l.strip()])
    fs_log_count = len([l for l in log_result.get("force_sell_logs", []) if l.strip()])
    log(f"  nextgen2 patch-related log lines: {patch_log_count}")
    log(f"  nextgen2 error log lines: {error_count}")
    log(f"  force-sell-executor log lines: {fs_log_count}")
    
    # 4. 종합 판정
    ks_keys_ok = redis_result.get("killswitch_key_count", 0) > 0 if isinstance(redis_result, dict) else False
    ks_dash_ok = redis_result.get("killswitch_dashboard") is not None if isinstance(redis_result, dict) else False
    pm2_ok = all(
        isinstance(v, dict) and v.get("status") == "online"
        for v in pm2_result.values()
        if isinstance(v, dict)
    )
    no_critical_errors = error_count == 0
    
    verdict = "PASS" if (pm2_ok and no_critical_errors) else "CONDITIONAL"
    if ks_keys_ok and ks_dash_ok:
        verdict = "FULL_PASS"
    
    report["verdict"] = verdict
    report["summary"] = {
        "killswitch_keys_created": ks_keys_ok,
        "killswitch_dashboard_present": ks_dash_ok,
        "pm2_services_online": pm2_ok,
        "no_critical_errors": no_critical_errors,
        "force_sell_executor_active": "force-sell-executor" in pm2_result and isinstance(pm2_result.get("force-sell-executor"), dict) and pm2_result["force-sell-executor"].get("status") == "online",
    }
    
    log("")
    log("=" * 60)
    log(f"VERDICT: {verdict}")
    log(f"  killswitch keys created: {ks_keys_ok}")
    log(f"  killswitch dashboard present: {ks_dash_ok}")
    log(f"  PM2 services online: {pm2_ok}")
    log(f"  No critical errors: {no_critical_errors}")
    log("=" * 60)
    
    # 보고서 저장
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    log(f"Report saved to {REPORT_PATH}")

if __name__ == "__main__":
    main()
