#!/bin/bash
# quarterly_archive.sh
# Purpose: Quarterly maintenance of /home/ubuntu/log_archive/.
#   - Default: dry-run + report sizes; exit 0
#   - When ARCHIVE_S3_BUCKET is set AND head-bucket succeeds:
#         upload all *.gz files older than 90 days to s3://$BUCKET/$PREFIX/
#         and remove local copies on success.
#   - When ARCHIVE_S3_BUCKET is NOT set:
#         move (not delete) files older than 365 days to log_archive/_cold/
#         so nothing is destroyed without operator action.
#
# Cron suggestion (1st of Jan/Apr/Jul/Oct, 02:30 UTC):
#   30 2 1 1,4,7,10 *  /home/ubuntu/quarterly_archive.sh >> /home/ubuntu/precheck_reports/quarterly_archive.log 2>&1

set -u
ARCHIVE_DIR="${ARCHIVE_DIR:-/home/ubuntu/log_archive}"
S3_BUCKET="${ARCHIVE_S3_BUCKET:-}"
S3_PREFIX="${ARCHIVE_S3_PREFIX:-ares/log_archive}"
COLD_DIR="$ARCHIVE_DIR/_cold"
S3_AGE_DAYS=90
COLD_AGE_DAYS=365

NOW_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LOGFMT() { printf "[%s] %s\n" "$NOW_ISO" "$*"; }

if [[ ! -d "$ARCHIVE_DIR" ]]; then
    LOGFMT "archive dir missing: $ARCHIVE_DIR"
    exit 0
fi

TOTAL_SIZE=$(du -sb "$ARCHIVE_DIR" 2>/dev/null | awk '{print $1}')
TOTAL_FILES=$(find "$ARCHIVE_DIR" -type f | wc -l)
LOGFMT "scan: total_files=$TOTAL_FILES total_bytes=$TOTAL_SIZE"

UPLOADED=0
SKIPPED=0
COLDED=0

if [[ -n "$S3_BUCKET" ]]; then
    # confirm bucket reachable
    if aws s3api head-bucket --bucket "$S3_BUCKET" 2>/dev/null; then
        LOGFMT "S3 bucket $S3_BUCKET ok; uploading files older than ${S3_AGE_DAYS}d"
        while IFS= read -r -d '' f; do
            rel="${f#$ARCHIVE_DIR/}"
            target="s3://${S3_BUCKET}/${S3_PREFIX}/${rel}"
            if aws s3 cp "$f" "$target" --only-show-errors 2>>/tmp/qarchive.err; then
                rm -f "$f"
                UPLOADED=$((UPLOADED+1))
            else
                SKIPPED=$((SKIPPED+1))
            fi
        done < <(find "$ARCHIVE_DIR" -type f -name '*.gz' -mtime +$S3_AGE_DAYS -print0)
        LOGFMT "S3 result: uploaded=$UPLOADED skipped=$SKIPPED"
    else
        LOGFMT "S3 bucket $S3_BUCKET not reachable; falling back to cold tier"
        S3_BUCKET=""
    fi
fi

if [[ -z "$S3_BUCKET" ]]; then
    mkdir -p "$COLD_DIR"
    while IFS= read -r -d '' f; do
        # never destroy data; just move to cold tier so operators can review
        mv "$f" "$COLD_DIR/" 2>/dev/null && COLDED=$((COLDED+1))
    done < <(find "$ARCHIVE_DIR" -maxdepth 2 -type f -mtime +$COLD_AGE_DAYS \
                  -not -path "$COLD_DIR/*" -print0)
    LOGFMT "cold-tier result: moved=$COLDED"
fi

LOGFMT "done"
exit 0
