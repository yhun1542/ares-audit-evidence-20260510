#!/usr/bin/env bash
set -uo pipefail

# ══════════════════════════════════════════════════════════════
# ARES 수집 데이터 분할 압축 스크립트
# 규칙: 파트당 30MB 이하, 10개 미만 파일
# ══════════════════════════════════════════════════════════════

DEST="$HOME/ares_delivery"
rm -rf "$DEST"
mkdir -p "$DEST"

log(){ echo "[$(date -u +%H:%M:%S)] $1"; }

log "══ ARES 데이터 분할 압축 시작 ══"

# ──────────────────────────────────────────────
# PART A: ares_audit (현재 시스템 전수조사)
# ──────────────────────────────────────────────

# A1: audit_source (소스코드) — 60MB → 압축 시 ~15MB 예상
log "A1: audit_source (소스코드)"
cd ~/ares_audit
tar czf "$DEST/A1_audit_source.tar.gz" source/
ls -lh "$DEST/A1_audit_source.tar.gz"

# A2: audit_git (Git 이력) — 28MB → 압축 시 ~8MB 예상
log "A2: audit_git (Git 이력)"
tar czf "$DEST/A2_audit_git.tar.gz" git/
ls -lh "$DEST/A2_audit_git.tar.gz"

# A3: audit_state (PM2, Redis, 로그, OS, 설정, 아키텍처, 매뉴얼보강) — ~9MB
log "A3: audit_state (PM2/Redis/로그/OS/설정)"
tar czf "$DEST/A3_audit_state.tar.gz" pm2/ redis/ logs/ os/ config/ architecture/ manual_supplement/ MANIFEST.txt
ls -lh "$DEST/A3_audit_state.tar.gz"

# ──────────────────────────────────────────────
# PART B: ares_upgrade_scan (업그레이드 후보)
# ──────────────────────────────────────────────

cd ~/ares_upgrade_scan

# B1: upgrade_meta (아카이브, 비교, 후보 요약) — ~500KB
log "B1: upgrade_meta (아카이브/비교/후보요약)"
tar czf "$DEST/B1_upgrade_meta.tar.gz" archives/ live_comparison/ upgrade_candidates/ github/repo_list.jsonl
ls -lh "$DEST/B1_upgrade_meta.tar.gz"

# B2~Bn: GitHub repos를 크기별로 그룹핑
log "GitHub repos 크기별 그룹핑..."

cd ~/ares_upgrade_scan/github/repos

# 대형 리포 (>50MB 원본) → 개별 압축
PART_NUM=2
for repo_dir in */; do
    repo_name="${repo_dir%/}"
    repo_size=$(du -sm "$repo_dir" 2>/dev/null | awk '{print $1}')
    
    if [[ $repo_size -gt 50 ]]; then
        log "B${PART_NUM}: github_${repo_name} (${repo_size}MB)"
        tar czf "$DEST/B${PART_NUM}_github_${repo_name}.tar.gz" "$repo_dir"
        actual_size=$(ls -lh "$DEST/B${PART_NUM}_github_${repo_name}.tar.gz" | awk '{print $5}')
        log "  → $actual_size"
        
        # 30MB 초과 시 split
        actual_bytes=$(stat -c%s "$DEST/B${PART_NUM}_github_${repo_name}.tar.gz")
        if [[ $actual_bytes -gt 31457280 ]]; then
            log "  → 30MB 초과! split 실행"
            split -b 29M "$DEST/B${PART_NUM}_github_${repo_name}.tar.gz" "$DEST/B${PART_NUM}_github_${repo_name}_part"
            rm "$DEST/B${PART_NUM}_github_${repo_name}.tar.gz"
            # Rename parts
            idx=1
            for part in "$DEST/B${PART_NUM}_github_${repo_name}_part"*; do
                mv "$part" "$DEST/B${PART_NUM}_github_${repo_name}_split${idx}.tar.gz.part"
                idx=$((idx+1))
            done
        fi
        PART_NUM=$((PART_NUM+1))
    fi
done

# 중형 리포 (10~50MB) → 2~3개씩 묶기
log "중형 리포 그룹핑 (10~50MB)..."
MEDIUM_GROUP=""
MEDIUM_SIZE=0
MEDIUM_COUNT=0
GROUP_NUM=$PART_NUM

for repo_dir in */; do
    repo_name="${repo_dir%/}"
    repo_size=$(du -sm "$repo_dir" 2>/dev/null | awk '{print $1}')
    
    if [[ $repo_size -ge 10 && $repo_size -le 50 ]]; then
        # 그룹에 추가하면 30MB 초과하거나 9개 초과하면 새 그룹
        new_total=$((MEDIUM_SIZE + repo_size))
        new_count=$((MEDIUM_COUNT + 1))
        
        if [[ $new_total -gt 80 || $new_count -ge 9 ]]; then
            # 현재 그룹 압축
            if [[ -n "$MEDIUM_GROUP" ]]; then
                log "B${GROUP_NUM}: github_medium_group (${MEDIUM_COUNT} repos, ~${MEDIUM_SIZE}MB)"
                tar czf "$DEST/B${GROUP_NUM}_github_medium_group_${GROUP_NUM}.tar.gz" $MEDIUM_GROUP
                ls -lh "$DEST/B${GROUP_NUM}_github_medium_group_${GROUP_NUM}.tar.gz"
                GROUP_NUM=$((GROUP_NUM+1))
            fi
            MEDIUM_GROUP="$repo_dir"
            MEDIUM_SIZE=$repo_size
            MEDIUM_COUNT=1
        else
            MEDIUM_GROUP="$MEDIUM_GROUP $repo_dir"
            MEDIUM_SIZE=$new_total
            MEDIUM_COUNT=$new_count
        fi
    fi
done

# 남은 중형 그룹 압축
if [[ -n "$MEDIUM_GROUP" ]]; then
    log "B${GROUP_NUM}: github_medium_group (${MEDIUM_COUNT} repos, ~${MEDIUM_SIZE}MB)"
    tar czf "$DEST/B${GROUP_NUM}_github_medium_group_${GROUP_NUM}.tar.gz" $MEDIUM_GROUP
    ls -lh "$DEST/B${GROUP_NUM}_github_medium_group_${GROUP_NUM}.tar.gz"
    GROUP_NUM=$((GROUP_NUM+1))
fi

# 소형 리포 (<10MB) → 최대한 묶기 (30MB 압축 기준, 9개씩)
log "소형 리포 그룹핑 (<10MB)..."
SMALL_GROUP=""
SMALL_SIZE=0
SMALL_COUNT=0

for repo_dir in */; do
    repo_name="${repo_dir%/}"
    repo_size=$(du -sm "$repo_dir" 2>/dev/null | awk '{print $1}')
    
    if [[ $repo_size -lt 10 ]]; then
        new_total=$((SMALL_SIZE + repo_size))
        new_count=$((SMALL_COUNT + 1))
        
        if [[ $new_total -gt 80 || $new_count -ge 9 ]]; then
            if [[ -n "$SMALL_GROUP" ]]; then
                log "B${GROUP_NUM}: github_small_group (${SMALL_COUNT} repos, ~${SMALL_SIZE}MB)"
                tar czf "$DEST/B${GROUP_NUM}_github_small_group_${GROUP_NUM}.tar.gz" $SMALL_GROUP
                ls -lh "$DEST/B${GROUP_NUM}_github_small_group_${GROUP_NUM}.tar.gz"
                GROUP_NUM=$((GROUP_NUM+1))
            fi
            SMALL_GROUP="$repo_dir"
            SMALL_SIZE=$repo_size
            SMALL_COUNT=1
        else
            SMALL_GROUP="$SMALL_GROUP $repo_dir"
            SMALL_SIZE=$new_total
            SMALL_COUNT=$new_count
        fi
    fi
done

# 남은 소형 그룹 압축
if [[ -n "$SMALL_GROUP" ]]; then
    log "B${GROUP_NUM}: github_small_group (${SMALL_COUNT} repos, ~${SMALL_SIZE}MB)"
    tar czf "$DEST/B${GROUP_NUM}_github_small_group_${GROUP_NUM}.tar.gz" $SMALL_GROUP
    ls -lh "$DEST/B${GROUP_NUM}_github_small_group_${GROUP_NUM}.tar.gz"
    GROUP_NUM=$((GROUP_NUM+1))
fi

# ──────────────────────────────────────────────
# 최종 검증: 30MB 초과 파일이 있으면 split
# ──────────────────────────────────────────────
log "최종 검증: 30MB 초과 파일 체크..."
for f in "$DEST"/*.tar.gz; do
    [[ -f "$f" ]] || continue
    fsize=$(stat -c%s "$f")
    if [[ $fsize -gt 31457280 ]]; then
        log "  SPLIT: $(basename "$f") ($(ls -lh "$f" | awk '{print $5}'))"
        base=$(basename "$f" .tar.gz)
        split -b 29M "$f" "$DEST/${base}_split"
        rm "$f"
        idx=1
        for part in "$DEST/${base}_split"*; do
            mv "$part" "$DEST/${base}_p${idx}.tar.gz.part"
            idx=$((idx+1))
        done
    fi
done

# ──────────────────────────────────────────────
# MANIFEST 생성
# ──────────────────────────────────────────────
log "MANIFEST 생성..."
{
    echo "═══════════════════════════════════════════════════════"
    echo "  ARES 전체 시스템 수집 데이터 — 분할 패키지 목록"
    echo "  생성일: $(date -u)"
    echo "═══════════════════════════════════════════════════════"
    echo ""
    echo "파트 | 파일명 | 크기 | 내용"
    echo "-----|--------|------|------"
    for f in "$DEST"/A*.tar.gz "$DEST"/B*.tar.gz "$DEST"/B*.tar.gz.part; do
        [[ -f "$f" ]] || continue
        bn=$(basename "$f")
        sz=$(ls -lh "$f" | awk '{print $5}')
        echo "$bn | $sz"
    done
    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "  사용법:"
    echo "  1. 개별 파트: 각 .tar.gz 파일을 tar xzf로 해제"
    echo "  2. split 파일: cat *_p1.tar.gz.part *_p2.tar.gz.part > merged.tar.gz"
    echo "  3. 마스터: ARES_MASTER.tar.gz 하나로 전체 해제 가능"
    echo "═══════════════════════════════════════════════════════"
} > "$DEST/MANIFEST.txt"

# ──────────────────────────────────────────────
# 결과 요약
# ──────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════"
echo "  분할 압축 완료!"
echo ""
ls -lhS "$DEST"/ | grep -v "^total"
echo ""
echo "  총 파일 수: $(ls "$DEST" | wc -l)"
echo "  총 크기: $(du -sh "$DEST" | awk '{print $1}')"
echo "══════════════════════════════════════════════════════════"
