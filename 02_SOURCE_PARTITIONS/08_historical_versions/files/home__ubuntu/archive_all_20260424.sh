#!/usr/bin/env bash
# ============================================================
# ARES 20260424 전체 아카이브 번들 생성
# ============================================================
# - 장애 복구 및 운영 개선 작업에서 생성된 백업/패치/로그를 일괄 수집
# - 재빌드 전 시점의 전체 상태를 단일 tgz 아카이브로 보존
# - 회귀 시 본 아카이브에서 파일 복원 가능
# ============================================================
set -u

TS="20260424T173000Z"
ARCHIVE_BASE="/home/ubuntu/ares_ops/archive/bundle_${TS}"
mkdir -p "${ARCHIVE_BASE}"
LOG="${ARCHIVE_BASE}/archive.log"
exec > >(tee -a "$LOG") 2>&1

echo "============================================================"
echo "[${TS}] ARES Archive Bundle Creation"
echo "============================================================"

# ---- 1. 백업 파일 수집 (이번 작업에서 생성된 것만) ----
echo ""
echo "[1/6] Collecting backup files (.bak.redisurl_*, .bak.ratepatch_*, .bak.thresh_*, .bak.sleevedaemon_*, .bak.entrypoint_*, .bak.rediscleanup_*, .bak.aclfix_*) ..."
BAKDIR="${ARCHIVE_BASE}/backups"
mkdir -p "${BAKDIR}"

find /home/ubuntu/ares_current /home/ubuntu/ares_work /home/ubuntu/aub-trading-system /home/ubuntu/scripts /home/ubuntu/ares_ops /home/ubuntu/ARES-KIS-US-AUTOPILOT \
  -type f \( \
    -name "*.bak.redisurl_20260424" -o \
    -name "*.bak.ratepatch_20260424*" -o \
    -name "*.bak.thresh_20260424*" -o \
    -name "*.bak.cashdrift-20260424*" -o \
    -name "*.bak.sleevedaemon_*" -o \
    -name "*.bak.entrypoint_*" -o \
    -name "*.bak.rediscleanup_*" -o \
    -name "*.bak.aclfix_20260424*" \
  \) 2>/dev/null > "${BAKDIR}/filelist.txt"

echo "  Collected file count: $(wc -l < ${BAKDIR}/filelist.txt)"

# ---- 2. 수정된 소스의 현재판 스냅샷 ----
echo ""
echo "[2/6] Snapshot current source files (patched versions) ..."
SRCDIR="${ARCHIVE_BASE}/current_sources"
mkdir -p "${SRCDIR}"
for f in \
  /home/ubuntu/ares_current/ecosystem.master.cjs \
  /home/ubuntu/ares_current/ecosystem.critical.config.cjs \
  /home/ubuntu/ares_current/ecosystem.core30.cjs \
  /home/ubuntu/ares_current/ops/ares_safe_live_guard_v2.mjs \
  /home/ubuntu/ares_current/external/rt_position_guard_v9.mjs \
  /home/ubuntu/ares_current/lib/ares-redis-preload.cjs \
  /home/ubuntu/aub-trading-system/realtime-data-feed-v3.mjs \
  /home/ubuntu/ares_work/ares_sleeve_writer.py \
  /home/ubuntu/ares_work/ares_sleeve_writer_entrypoint.py \
  /home/ubuntu/ares_ops/manus_patches/halt_bridge.py \
  /home/ubuntu/scripts/update_market_clock.py \
  /home/ubuntu/pm2_rebuild.sh \
  /home/ubuntu/redis_url_mass_replace.sh \
  /home/ubuntu/ares_incident_report_20260424.md \
  /etc/ares/services.registry.v2.yaml \
  /etc/ares/redis.env \
  ; do
  if [ -f "$f" ]; then
    # 경로 상대화하여 복사
    rel=$(echo "$f" | sed 's|^/||')
    mkdir -p "${SRCDIR}/$(dirname ${rel})"
    cp -p "$f" "${SRCDIR}/${rel}" 2>/dev/null && echo "  ok: $f"
  fi
done

# ---- 3. PM2 상태 스냅샷 ----
echo ""
echo "[3/6] PM2 state snapshot ..."
pm2 jlist > "${ARCHIVE_BASE}/pm2_jlist.json" 2>/dev/null
pm2 ls > "${ARCHIVE_BASE}/pm2_ls.txt" 2>/dev/null
cp -p ~/.pm2/dump.pm2 "${ARCHIVE_BASE}/dump.pm2" 2>/dev/null
echo "  done"

# ---- 4. Redis 게이트 상태 ----
echo ""
echo "[4/6] Redis gate state snapshot ..."
RURL='rediss://ares-admin:AresAdmin2026SecureA3kB6jY1xZ8@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379'
RCLI="redis-cli -u $RURL --no-auth-warning"
{
  echo "=== Gates ==="
  for k in trade:halt policy:safe_live:hard_halt trading:enabled market:session policy:trade:halt_reason; do
    echo "$k = $($RCLI GET "$k")"
  done
  echo ""
  echo "=== Go/No-Go ==="
  $RCLI GET ares:go_nogo:verdict
  echo ""
  echo "=== Brokers Truth ==="
  $RCLI GET truth:broker:health
  echo ""
  $RCLI GET truth:broker:components
} > "${ARCHIVE_BASE}/redis_state.txt" 2>&1

# ---- 5. 최근 로그 수집 (key services, last 500 lines) ----
echo ""
echo "[5/6] Collecting recent logs (last 500 lines) ..."
LOGDIR="${ARCHIVE_BASE}/logs"
mkdir -p "${LOGDIR}"
for svc in ares-safe-live-guard ares-sleeve-writer halt-bridge realtime-data-feed-v3 final-trade-gate kis-token-service ares-broker-truth-writer go-nogo-judge drift-detector; do
  pm2 logs "$svc" --lines 500 --nostream > "${LOGDIR}/${svc}.log" 2>&1
done
echo "  done"

# ---- 6. tgz 번들 생성 ----
echo ""
echo "[6/6] Creating tgz bundle ..."
cd /home/ubuntu/ares_ops/archive/
tar -czf "bundle_${TS}.tgz" "bundle_${TS}/" 2>&1 | tail -3
BUNDLE_SIZE=$(du -h "bundle_${TS}.tgz" | cut -f1)
echo "  bundle: /home/ubuntu/ares_ops/archive/bundle_${TS}.tgz  (size: ${BUNDLE_SIZE})"

echo ""
echo "============================================================"
echo "[DONE] Archive bundle created at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================================"
