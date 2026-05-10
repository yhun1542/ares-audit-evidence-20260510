#!/usr/bin/env bash
RURL="rediss://ares-admin:AresAdmin2026SecureA3kB6jY1xZ8@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379"

echo "=== [EVIDENCE-1] sleeve:ares:weights payload ==="
timeout 5 redis-cli -u "$RURL" GET sleeve:ares:weights 2>/dev/null | python3 -c "
import json,sys
j=json.load(sys.stdin)
print('version:    ', j['version'])
print('state:      ', j['state'])
print('lambda_hint:', round(j['lambda_hint'],3))
print('n_weights:  ', len(j['weights']))
print('metadata:   ', j['metadata'])
print('source:     ', j.get('source',''))
gross=sum(abs(v) for v in j['weights'].values())
netlong=sum(v for v in j['weights'].values() if v>0)
print(f'gross={gross:.3f} net_long={netlong:.3f}')
"

echo ""
echo "=== [EVIDENCE-2] heartbeat ==="
timeout 5 redis-cli -u "$RURL" GET ares:sleeve:writer:heartbeat 2>/dev/null

echo ""
echo "=== [EVIDENCE-3] Bridge LAMBDA_APPLY 최근 5건 ==="
pm2 logs final-to-champion-bridge --nostream --lines 60 --no-color 2>&1 | grep "LAMBDA_APPLY" | tail -5

echo ""
echo "=== [EVIDENCE-3b] Bridge LAMBDA_APPLY_SKIP 유무 (복구 이후) ==="
pm2 logs final-to-champion-bridge --nostream --lines 200 --no-color 2>&1 | grep "LAMBDA_APPLY_SKIP" | tail -3
echo "(위가 비어있어야 정상)"

echo ""
echo "=== [EVIDENCE-4] pm2 상태 ==="
pm2 list --no-color 2>&1 | grep -E "ares-sleeve-writer|ares-sleeve-freshness-watcher|ares-pm2-drift-detector"

echo ""
echo "=== [EVIDENCE-5] drift-detector 로그 ==="
pm2 logs ares-pm2-drift-detector --nostream --lines 10 --no-color 2>&1 | tail -8

echo ""
echo "=== [EVIDENCE-6] freshness-watcher 로그 ==="
pm2 logs ares-sleeve-freshness-watcher --nostream --lines 10 --no-color 2>&1 | tail -8

echo ""
echo "=== [EVIDENCE-7] writer publish 카운트 (누적) ==="
grep -c PUBLISH /home/ubuntu/.pm2/logs/ares-sleeve-writer-combined.log 2>/dev/null || echo "log file not found (check out.log)"

echo ""
echo "=== [EVIDENCE-8] pm2 describe (script path drift 없는지) ==="
pm2 jlist 2>/dev/null | python3 -c "
import json,sys
for x in json.load(sys.stdin):
    if x['name'] in ('ares-sleeve-writer','ares-sleeve-freshness-watcher','ares-pm2-drift-detector'):
        print(x['name'], '->', x.get('pm2_env',{}).get('pm_exec_path',''), 'status=', x.get('pm2_env',{}).get('status'))
"
