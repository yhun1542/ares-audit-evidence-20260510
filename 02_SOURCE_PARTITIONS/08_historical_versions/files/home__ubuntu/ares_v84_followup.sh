#!/usr/bin/env bash
# v8.4 후속 정리 스크립트 - 운영 무중단 원칙
set -uo pipefail
LOG="/tmp/ares_v84_followup_$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1

echo "================================================================"
echo " ARES v8.4 FOLLOWUP CLEANUP  $(date -u +%FT%TZ)"
echo "================================================================"

########################################
# [1] drift-detector 재시작 (수정본 반영)
########################################
echo ""
echo "### [1] drift-detector 재시작 (optional REDIS_URL 수정본) ###"
pm2 restart ares-pm2-drift-detector --update-env 2>&1 | tail -3
sleep 4
pm2 logs ares-pm2-drift-detector --nostream --lines 5 --no-color 2>&1 | tail -5

########################################
# [2] 구 v8.0 테스트 하네스 격리 + 심볼릭 링크 재지정
########################################
echo ""
echo "### [2] 구 test harness 격리 + symlink 재지정 ###"
QDIR="/home/ubuntu/ares_work/_quarantine"
mkdir -p "$QDIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

# (2-1) 구 파일 격리 (삭제 아님)
SRC="/home/ubuntu/ares_work/ares_sleeve_writer.py"
if [ -f "$SRC" ]; then
  DST="$QDIR/ares_sleeve_writer.py.v80-test-$STAMP"
  cp -a "$SRC" "$DST"
  echo "  archived: $DST ($(wc -l < "$DST") lines)"
  # 원본을 경고 stub으로 대체 (혹시라도 실행되면 즉시 종료)
  cat > "$SRC" <<'EOF'
#!/usr/bin/env python3
# QUARANTINED 2026-04-24: v8.0 test harness that had no Redis writes.
# Real writer is /home/ubuntu/ares_sleeve_writer_v84.py (v8.4 unified).
# Any attempt to run this stub will exit(1) intentionally to fail-loud.
import sys
sys.stderr.write("[FATAL] ares_work/ares_sleeve_writer.py is quarantined. "
                 "Use /home/ubuntu/ares_sleeve_writer_v84.py\n")
sys.exit(1)
EOF
  chmod +x "$SRC"
  echo "  stub installed at $SRC (exit-1 on run)"
else
  echo "  $SRC not found (already moved?)"
fi

# (2-2) symlink 재지정
LN="/home/ubuntu/ares_current/external/ares_sleeve_writer.py"
if [ -L "$LN" ] || [ -f "$LN" ]; then
  OLD=$(readlink -f "$LN" 2>/dev/null || echo "(non-symlink)")
  echo "  old target: $OLD"
  rm -f "$LN"
  ln -s /home/ubuntu/ares_sleeve_writer_v84.py "$LN"
  echo "  new symlink: $(ls -la "$LN")"
else
  echo "  $LN not present — creating symlink anyway"
  ln -s /home/ubuntu/ares_sleeve_writer_v84.py "$LN" 2>/dev/null || true
fi

########################################
# [3] ecosystem.config.cjs 백업 + ares-sleeve-writer 블록 교체
########################################
echo ""
echo "### [3] ecosystem.config.cjs 패치 ###"
ECO=""
for cand in /home/ubuntu/ares_current/ecosystem.config.cjs /home/ubuntu/ecosystem.config.cjs /opt/ares/ecosystem.config.cjs; do
  [ -f "$cand" ] && ECO="$cand" && break
done
if [ -z "$ECO" ]; then
  ECO=$(find /home/ubuntu /opt -maxdepth 4 -name "ecosystem.config.cjs" 2>/dev/null | head -1)
fi
if [ -n "$ECO" ] && [ -f "$ECO" ]; then
  echo "  target: $ECO"
  BAK="${ECO}.bak-$STAMP"
  cp -a "$ECO" "$BAK"
  echo "  backup: $BAK"
  # 기존 script 경로 확인 후 in-place 치환
  if grep -q "ares_work/ares_sleeve_writer.py" "$ECO"; then
    sed -i.tmp 's|/home/ubuntu/ares_work/ares_sleeve_writer\.py|/home/ubuntu/ares_sleeve_writer_v84.py|g' "$ECO"
    rm -f "$ECO.tmp"
    echo "  patched: ares_work/ares_sleeve_writer.py -> ares_sleeve_writer_v84.py"
    grep -n "ares_sleeve_writer" "$ECO" | head -10
  else
    echo "  (no ares_work/ares_sleeve_writer.py reference found — maybe already clean)"
  fi
else
  echo "  ecosystem.config.cjs not found — skipping"
fi

########################################
# [4] 다른 writer 수평 감사
########################################
echo ""
echo "### [4] 수평 감사: 다른 writer의 script path vs Redis 계약 키 ###"
RURL="rediss://ares-admin:AresAdmin2026SecureA3kB6jY1xZ8@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379"

echo "  (4-1) pm2 jlist → writer/bridge 스크립트 경로"
pm2 jlist 2>/dev/null | python3 -c "
import json,sys,os
arr=json.load(sys.stdin)
rows=[]
for x in arr:
    n=x.get('name','')
    if any(k in n for k in ['writer','bridge','guard','detector','watcher','watchdog','monitor','sleeve','regime','feature','allocator']):
        env=x.get('pm2_env',{})
        path=env.get('pm_exec_path','')
        rt=env.get('restart_time',0)
        st=env.get('status','')
        uptime_ms=0
        try:
            uptime_ms = int(env.get('pm_uptime',0))
        except: pass
        rows.append((n, st, rt, path, os.path.basename(path)))
rows.sort()
print(f\"{'name':45s} {'stat':10s} {'rst':>4s} {'basename':40s}\")
for n,st,rt,p,bn in rows:
    marker = '' if ('ares_work/_quarantine' not in p) else ' [QUARANTINED!]'
    print(f'{n:45s} {st:10s} {rt:>4d} {bn:40s}{marker}')
"

echo ""
echo "  (4-2) 주요 계약 키 신선도 (EXISTS + TTL + 마지막 ts 있으면)"
for k in \
  sleeve:ares:weights \
  ctx:regime:state \
  ssot:targets:current \
  regime15:state \
  champion:v660:signals \
  xgb:riskflag:latest \
  features:technical:latest \
  sleeve:v3:final_weights:current \
  ; do
  EX=$(timeout 3 redis-cli -u "$RURL" EXISTS "$k" 2>/dev/null | tail -1)
  TT=$(timeout 3 redis-cli -u "$RURL" TTL    "$k" 2>/dev/null | tail -1)
  TY=$(timeout 3 redis-cli -u "$RURL" TYPE   "$k" 2>/dev/null | tail -1)
  printf "    %-42s EXISTS=%s TTL=%-6s TYPE=%s\n" "$k" "$EX" "$TT" "$TY"
done

echo ""
echo "  (4-3) 추가 심볼릭링크 / ares_work 내 파일 vs 실제 pm2 사용 교차 검증"
ls -la /home/ubuntu/ares_current/external/*.py 2>/dev/null | head -20 | awk '{print "    "$NF" -> "$NF}' || true
for f in /home/ubuntu/ares_current/external/*.py; do
  [ -L "$f" ] || continue
  echo "    $f -> $(readlink -f "$f")"
done

echo ""
echo "  (4-4) allowlist 확장 제안 (drift-detector 커버리지)"
pm2 jlist 2>/dev/null | python3 -c "
import json,sys
arr=json.load(sys.stdin)
crit=['ares-sleeve-writer','final-to-champion-bridge','regime15_bridge',
      'regime-writer-v88','regime-consensus-engine','xgb-riskflag-writer-v2',
      'technical-indicator-writer-v2','ssot-writer-v2','sleeve-allocator']
print('    name'.ljust(40), 'pm_exec_path')
for x in arr:
    if x['name'] in crit:
        p=x.get('pm2_env',{}).get('pm_exec_path','')
        print('    '+x['name'].ljust(36), p)
"

echo ""
echo "================================================================"
echo " DONE. Full log: $LOG"
echo "================================================================"
