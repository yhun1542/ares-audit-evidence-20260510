#!/bin/bash
# Full live source code archiver - 8,015 files across 11 locations
# Excludes: node_modules, .git, __pycache__, venv, *.pyc, *.log, *.db, *.sqlite*, *.csv, *.parquet, *.pkl

set -e
OUTDIR="/home/ubuntu/full_live_export"
rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

PART=0

archive_dir() {
    local src="$1"
    local label="$2"
    local max_per_archive="${3:-10}"
    
    # Collect all files
    local tmplist=$(mktemp)
    find -L "$src" -type f \
        ! -path '*/node_modules/*' ! -path '*/.git/*' ! -path '*/__pycache__/*' \
        ! -path '*/venv/*' ! -path '*/.venv/*' \
        ! -name '*.pyc' ! -name '*.pyo' ! -name '*.log' \
        ! -name '*.db' ! -name '*.sqlite*' \
        ! -name '*.csv' ! -name '*.parquet' ! -name '*.pkl' ! -name '*.pickle' \
        2>/dev/null | sort > "$tmplist"
    
    local total=$(wc -l < "$tmplist")
    if [ "$total" -eq 0 ]; then
        rm "$tmplist"
        return
    fi
    
    # Split into chunks of max_per_archive
    local chunk=0
    local num_chunks=$(( (total + max_per_archive - 1) / max_per_archive ))
    
    while IFS= read -r line; do
        chunk_idx=$(( chunk / max_per_archive ))
        chunk_file="/tmp/chunk_${label}_${chunk_idx}.txt"
        echo "$line" >> "$chunk_file"
        chunk=$((chunk + 1))
    done < "$tmplist"
    
    for i in $(seq 0 $((num_chunks - 1))); do
        chunk_file="/tmp/chunk_${label}_${i}.txt"
        if [ -f "$chunk_file" ]; then
            PART=$((PART + 1))
            local partnum=$(printf "%03d" $PART)
            local partlabel="${label}"
            if [ "$num_chunks" -gt 1 ]; then
                partlabel="${label}_part$((i+1))"
            fi
            local archive_name="${partnum}_${partlabel}.tar.gz"
            tar czf "$OUTDIR/$archive_name" -T "$chunk_file" 2>/dev/null
            local fcnt=$(wc -l < "$chunk_file")
            local sz=$(du -h "$OUTDIR/$archive_name" | cut -f1)
            echo "$archive_name  ($fcnt files, $sz)"
            rm "$chunk_file"
        fi
    done
    
    rm "$tmplist"
}

echo "=== Creating full live source archives ==="
echo ""

# 1. aub-trading-system (core trading - symlink resolved)
echo "--- A01: aub-trading-system (core) ---"
archive_dir "/home/ubuntu/aub-trading-system" "A01_aub_trading_system"

# 2. ares-nextgen (nextgen2 orchestrator)
echo "--- A02: ares-nextgen ---"
archive_dir "/home/ubuntu/ares-nextgen" "A02_ares_nextgen"

# 3. ares-v7 (regime detector)
echo "--- A03: ares-v7 ---"
archive_dir "/home/ubuntu/ares-v7" "A03_ares_v7"

# 4. ares_common (GPU modules)
echo "--- A04: ares_common ---"
archive_dir "/home/ubuntu/ares_common" "A04_ares_common"

# 5. ares_v56_live
echo "--- A05: ares_v56_live ---"
archive_dir "/home/ubuntu/ares_v56_live" "A05_ares_v56_live"

# 6. ares_work (OFG, flapping, SDM)
echo "--- A06: ares_work ---"
archive_dir "/home/ubuntu/ares_work" "A06_ares_work"

# 7. autonomous-trading-services
echo "--- A07: autonomous-trading-services ---"
archive_dir "/home/ubuntu/autonomous-trading-services" "A07_autonomous_trading_services"

# 8. alpha_prod
echo "--- A08: alpha_prod ---"
archive_dir "/home/ubuntu/alpha_prod" "A08_alpha_prod"

# 9. ops
echo "--- A09: ops ---"
archive_dir "/home/ubuntu/ops" "A09_ops"

# 10. r11s_v44_dtch_live
echo "--- A10: r11s_v44_dtch_live ---"
archive_dir "/home/ubuntu/r11s_v44_dtch_live" "A10_r11s_dtch_live"

# 11. Home root files (maxdepth 1)
echo "--- A11: home_root ---"
tmplist=$(mktemp)
find /home/ubuntu -maxdepth 1 -type f \
    ! -name '*.pyc' ! -name '.bash_history' ! -name '*.log' \
    ! -name '*.db' ! -name '*.sqlite*' ! -name '*.csv' \
    ! -name '*.parquet' ! -name '*.pkl' \
    2>/dev/null | sort > "$tmplist"
total=$(wc -l < "$tmplist")
num_chunks=$(( (total + 10 - 1) / 10 ))
chunk=0
while IFS= read -r line; do
    chunk_idx=$(( chunk / 10 ))
    echo "$line" >> "/tmp/chunk_home_${chunk_idx}.txt"
    chunk=$((chunk + 1))
done < "$tmplist"
for i in $(seq 0 $((num_chunks - 1))); do
    chunk_file="/tmp/chunk_home_${i}.txt"
    if [ -f "$chunk_file" ]; then
        PART=$((PART + 1))
        partnum=$(printf "%03d" $PART)
        archive_name="${partnum}_A11_home_root_part$((i+1)).tar.gz"
        tar czf "$OUTDIR/$archive_name" -T "$chunk_file" 2>/dev/null
        fcnt=$(wc -l < "$chunk_file")
        sz=$(du -h "$OUTDIR/$archive_name" | cut -f1)
        echo "$archive_name  ($fcnt files, $sz)"
        rm "$chunk_file"
    fi
done
rm "$tmplist"

echo ""
echo "=== SUMMARY ==="
echo "Total archives: $PART"
ls -la "$OUTDIR/" | tail -n +2 | wc -l
echo "Total size:"
du -sh "$OUTDIR/"
echo ""
echo "=== Check for files > 30MB ==="
find "$OUTDIR" -type f -size +30M 2>/dev/null | head -5 || echo "None (all under 30MB)"
echo ""
echo "=== Verify total file count ==="
total_files=0
for f in "$OUTDIR"/*.tar.gz; do
    cnt=$(tar tzf "$f" | wc -l)
    total_files=$((total_files + cnt))
done
echo "Total files in all archives: $total_files"
