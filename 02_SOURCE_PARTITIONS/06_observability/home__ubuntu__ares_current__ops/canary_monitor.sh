# ARES Canary Monitor - 24시간 핵심 지표 수집
LOGFILE="/home/ubuntu/logs/canary_20260428_021002.log"
echo "=== ARES Canary Monitor Tue Apr 28 06:10:02 UTC 2026 ==" | tee -a "$LOGFILE"

# 1. PM2 restart count
echo '--- PM2 restart counts ---' | tee -a "$LOGFILE"
pm2 list 2>/dev/null | grep -E 'online|stopped' | awk '{print $2, $12}' | sort -k2 -rn | head -10 | tee -a "$LOGFILE"

# 2. ERROR/FATAL/CRITICAL 로그 (최근 1시간)
echo '--- ERROR/FATAL/CRITICAL (last 1h) ---' | tee -a "$LOGFILE"
find /home/ubuntu/.pm2/logs -name '*.log' -newer /tmp/canary_last_check 2>/dev/null | xargs grep -l 'ERROR\|FATAL\|CRITICAL\|Exception\|Traceback' 2>/dev/null | head -10 | tee -a "$LOGFILE"
touch /tmp/canary_last_check

# 3. Redis 연결 상태
echo '--- Redis status ---' | tee -a "$LOGFILE"
source /etc/ares/redis.env 2>/dev/null
redis-cli --tls -u "$REDIS_URL" PING 2>/dev/null | tee -a "$LOGFILE"

# 4. KIS API 오류 확인
echo '--- KIS API errors (last 1h) ---' | tee -a "$LOGFILE"
find /home/ubuntu/.pm2/logs -name '*kis*' -newer /tmp/canary_last_check 2>/dev/null | xargs grep -c 'KIS.*error\|token.*fail\|401\|403' 2>/dev/null | grep -v ':0' | head -5 | tee -a "$LOGFILE"

# 5. guard reject 로그
echo '--- Guard rejects (last 1h) ---' | tee -a "$LOGFILE"
find /home/ubuntu/.pm2/logs -newer /tmp/canary_last_check 2>/dev/null | xargs grep -c 'REJECT\|BLOCKED\|GUARD\|fail.closed' 2>/dev/null | grep -v ':0' | head -5 | tee -a "$LOGFILE"

echo "=== Canary check complete Tue Apr 28 06:10:02 UTC 2026 ==" | tee -a "$LOGFILE"
