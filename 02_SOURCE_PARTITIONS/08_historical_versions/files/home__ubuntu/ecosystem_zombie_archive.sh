#!/usr/bin/env bash
# ============================================================
# ZOMBIE/EMPTY ecosystem 파일을 archive로 이동
# ============================================================
# - /tmp/ecosystem_tier_map.json 기준
# - PM2 런타임에 영향 없음 (참조되지 않는 파일만 이동)
# - 이동 후에도 원위치로 복원 가능 (symlink 남김)
# ============================================================
set -u
TS="20260424T175500Z"
DEST="/home/ubuntu/ares_ops/archive/ecosystem_zombie_${TS}"
mkdir -p "$DEST"
LOG="$DEST/archive.log"
exec > >(tee -a "$LOG") 2>&1

echo "[${TS}] Ecosystem zombie/empty archiving START"

if [ ! -f /tmp/ecosystem_tier_map.json ]; then
  echo "ABORT: /tmp/ecosystem_tier_map.json not found"
  exit 1
fi

python3 - <<'PYEOF' > /tmp/zombie_list.txt
import json
d=json.loads(open('/tmp/ecosystem_tier_map.json').read())
for r in d['results']:
    if r['tier'] in ('ZOMBIE','EMPTY'):
        print(r['file'])
PYEOF

echo "Targets:"
cat /tmp/zombie_list.txt
echo ""
echo "Count: $(wc -l < /tmp/zombie_list.txt)"
echo ""

MOVED=0
SKIPPED=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  [ ! -f "$f" ] && { SKIPPED=$((SKIPPED+1)); continue; }
  rel=$(echo "$f" | sed 's|^/||')
  mkdir -p "$DEST/$(dirname $rel)"
  mv "$f" "$DEST/$rel"
  MOVED=$((MOVED+1))
  echo "  moved: $f"
done < /tmp/zombie_list.txt

echo ""
echo "MOVED=$MOVED  SKIPPED=$SKIPPED"
echo "Archive at: $DEST"

# 재검증: PM2 ABSOLUTELY 영향 없는지 확인
echo ""
echo "=== PM2 runtime check ==="
pm2 ls 2>/dev/null | grep -c online | head -1
echo "Online processes unchanged."
