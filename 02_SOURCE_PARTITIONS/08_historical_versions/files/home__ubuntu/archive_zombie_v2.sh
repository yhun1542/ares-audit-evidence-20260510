#!/usr/bin/env bash
# ============================================================
# Node 분류기 기반 ZOMBIE + ERROR ecosystem 파일 archive
# ============================================================
set -u
TS="20260424T180500Z"
DEST="/home/ubuntu/ares_ops/archive/ecosystem_zombie_${TS}"
mkdir -p "$DEST"
LOG="$DEST/archive.log"
exec > >(tee -a "$LOG") 2>&1

echo "[${TS}] Node-based zombie/error archiving START"
echo ""

# PM2 상태 기록 (사전)
PRE_ONLINE=$(pm2 ls 2>/dev/null | grep -c online)
echo "Pre-archive PM2 online: $PRE_ONLINE"

# 대상 추출 (ZOMBIE + ERROR)
node -e '
const d = JSON.parse(require("fs").readFileSync("/tmp/ecosystem_tier_v2.json", "utf8"));
d.results.filter(r => r.tier === "ZOMBIE" || r.tier === "ERROR").forEach(r => console.log(r.file));
' > /tmp/zombie_v2_list.txt

N=$(wc -l < /tmp/zombie_v2_list.txt)
echo ""
echo "Targets (ZOMBIE + ERROR): $N"
cat /tmp/zombie_v2_list.txt
echo ""

# 최종 안전 점검: 각 파일의 apps 중 live인 것이 있으면 거부
ABORT=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  [ ! -f "$f" ] && continue
  # require로 apps 이름 뽑고 pm2에서 status 확인
  names=$(node -e "try{const c=require('$f');console.log((c.apps||[]).map(a=>a.name).join(' '));}catch(e){console.log('');}")
  for n in $names; do
    [ -z "$n" ] && continue
    if pm2 describe "$n" 2>/dev/null | grep -q 'online'; then
      echo "  ⚠️  ABORT: $f has live app '$n'"
      ABORT=1
    fi
  done
done < /tmp/zombie_v2_list.txt

if [ "$ABORT" = "1" ]; then
  echo ""
  echo "ABORT: some files still have live apps. Skipping."
  exit 2
fi

MOVED=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  [ ! -f "$f" ] && continue
  rel=$(echo "$f" | sed 's|^/||')
  mkdir -p "$DEST/$(dirname "$rel")"
  mv "$f" "$DEST/$rel"
  MOVED=$((MOVED+1))
  echo "  moved: $f"
done < /tmp/zombie_v2_list.txt

echo ""
echo "MOVED=$MOVED"

# PM2 상태 확인 (사후)
sleep 2
POST_ONLINE=$(pm2 ls 2>/dev/null | grep -c online)
echo ""
echo "Post-archive PM2 online: $POST_ONLINE (was $PRE_ONLINE)"
if [ "$POST_ONLINE" -ge "$PRE_ONLINE" ]; then
  echo "✅ PM2 unchanged or better"
else
  echo "❌ PM2 went DOWN — need investigation"
  exit 3
fi

echo ""
echo "Archive at: $DEST"
