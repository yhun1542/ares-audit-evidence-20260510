#!/usr/bin/env bash
# v8.4 수평 감사 2단계: 누락 키로 보였던 writer들의 실제 네이밍 컨벤션 확인
RURL="rediss://ares-admin:AresAdmin2026SecureA3kB6jY1xZ8@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379"

echo "=== [A] 각 writer의 실제 Redis 키 네이밍 컨벤션 ==="
for pat in 'ssot*' 'regime15*' 'xgb*' 'technical*' 'features*'; do
  echo "--- pattern: $pat ---"
  timeout 5 redis-cli -u "$RURL" --scan --pattern "$pat" 2>/dev/null | head -10
done

echo ""
echo "=== [B] v8.4 writer 최신 heartbeat ==="
timeout 3 redis-cli -u "$RURL" GET ares:sleeve:writer:heartbeat 2>/dev/null

echo ""
echo "=== [C] sleeve:ares:weights 최신 상태 ==="
timeout 3 redis-cli -u "$RURL" GET sleeve:ares:weights 2>/dev/null > /tmp/_sw.json
python3 - <<'PY'
import json
with open("/tmp/_sw.json") as f:
    j = json.load(f)
print(f"state       : {j['state']}")
print(f"lambda_hint : {round(j['lambda_hint'],3)}")
print(f"vix_z       : {round(j['metadata']['vix_z'],3)}")
print(f"ts          : {j['metadata']['ts']}")
print(f"source      : {j.get('source','')}")
print(f"n_weights   : {len(j['weights'])}")
PY

echo ""
echo "=== [D] Bridge 최근 400줄 통계 ==="
pm2 logs final-to-champion-bridge --nostream --lines 400 --no-color 2>&1 | \
  awk '/LAMBDA_APPLY_SKIP/ {skip++} /LAMBDA_APPLY\]/ {ok++} END {print "LAMBDA_APPLY="ok+0"  LAMBDA_APPLY_SKIP="skip+0}'

echo ""
echo "=== [E] writer 최신 publish 20건 요약 ==="
pm2 logs ares-sleeve-writer --nostream --lines 400 --no-color 2>&1 | grep "PUBLISH" | tail -5

echo ""
echo "=== [F] freshness-watcher 최근 10건 ==="
pm2 logs ares-sleeve-freshness-watcher --nostream --lines 20 --no-color 2>&1 | tail -10

echo ""
echo "=== [G] drift-detector 최근 10건 ==="
pm2 logs ares-pm2-drift-detector --nostream --lines 20 --no-color 2>&1 | tail -10

echo ""
echo "=== [H] 격리된 stub 검증 (실행 시 exit 1인지) ==="
python3 /home/ubuntu/ares_work/ares_sleeve_writer.py 2>&1 | head -3
echo "(exit code: $?)"

echo ""
echo "=== [I] symlink 최종 상태 ==="
ls -la /home/ubuntu/ares_current/external/ares_sleeve_writer.py

echo ""
echo "=== [J] ecosystem 백업 확인 ==="
ls -la /home/ubuntu/ares_current/ecosystem.config.cjs*bak* 2>/dev/null | tail -3

echo ""
echo "=== [K] allowlist 확인 ==="
cat /etc/ares/pm2_allowlist.json 2>/dev/null || echo "allowlist not found"
