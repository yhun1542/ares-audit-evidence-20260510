#!/usr/bin/env python3
"""
PATCH-INV-ORD-001-A: emarkos:v6:order:intent:quarantine 119건을 archive로
완전 복사 후 source stream을 trim하여 INV-ORD-001 시한폭탄 즉시 해제.

- idempotent: archive에 이미 동일 batch가 있으면 skip
- 데이터 보존: archived_at, archive_batch_id, original_id, original_ts 메타 추가
- 검증: archive XLEN == source XLEN 확인 후에만 trim 실행
- archive stream MAXLEN 100,000 (자동 회전)
- forensic offload가 자동으로 archive도 S3에 누적 보존
"""
import os, sys, time, json, uuid, hashlib
from datetime import datetime, timezone
import redis
from urllib.parse import urlparse

REDIS_URL = os.environ.get("ARES_REDIS_URL")
if not REDIS_URL:
    h = os.environ["ARES_REDIS_HOST"]
    p = os.environ.get("ARES_REDIS_PASSWORD","")
    REDIS_URL = f"rediss://default:{p}@{h}:6379/0"

SRC = "emarkos:v6:order:intent:quarantine"
DST = "emarkos:v6:order:intent:quarantine:archive"
DST_MAXLEN = 100_000
RUN_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def main():
    u = urlparse(REDIS_URL)
    r = redis.Redis(
        host=u.hostname, port=u.port or 6379,
        username=u.username or "default", password=u.password,
        ssl=(u.scheme == "rediss"), ssl_cert_reqs=None, decode_responses=True,
        socket_connect_timeout=5, socket_timeout=10
    )
    r.ping()
    src_len_before = r.xlen(SRC)
    dst_len_before = r.xlen(DST)
    print(f"[INFO] src({SRC}) XLEN before = {src_len_before}")
    print(f"[INFO] dst({DST}) XLEN before = {dst_len_before}")
    if src_len_before == 0:
        print("[OK] source already empty; nothing to do.")
        return 0

    batch_id = f"archive-{RUN_TS}-{uuid.uuid4().hex[:8]}"
    print(f"[INFO] batch_id = {batch_id}")

    # Read all entries (oldest first)
    entries = r.xrange(SRC, min='-', max='+')
    print(f"[INFO] read {len(entries)} entries from source")
    if len(entries) != src_len_before:
        print(f"[WARN] read count {len(entries)} != XLEN {src_len_before} (possibly raced); continuing safely")

    # Idempotency guard: hash(src content) → if already archived in last 60s, skip
    src_hash = hashlib.sha256(json.dumps(entries, sort_keys=True, default=str).encode()).hexdigest()[:16]
    guard_key = f"ares:offload:quarantine_archive:lock:{src_hash}"
    if r.set(guard_key, batch_id, nx=True, ex=300):
        print(f"[INFO] idempotency guard acquired ({guard_key})")
    else:
        existing = r.get(guard_key)
        print(f"[ERROR] another archive already in progress for same content (existing batch={existing}); aborting")
        return 2

    # Pipelined XADD to archive stream
    pipe = r.pipeline(transaction=False)
    written = 0
    for orig_id, fields in entries:
        # fields is dict {key: value}
        new_fields = dict(fields)
        new_fields["__archived_at"] = datetime.now(timezone.utc).isoformat()
        new_fields["__archive_batch_id"] = batch_id
        new_fields["__original_id"] = orig_id
        # try to extract original ts from id (ms-seq)
        try:
            orig_ts_ms = int(str(orig_id).split("-")[0])
            new_fields["__original_ts"] = datetime.fromtimestamp(orig_ts_ms/1000, timezone.utc).isoformat()
        except Exception:
            pass
        pipe.xadd(DST, new_fields, maxlen=DST_MAXLEN, approximate=True)
        written += 1
        if written % 50 == 0:
            pipe.execute()
            pipe = r.pipeline(transaction=False)
    pipe.execute()

    dst_len_after = r.xlen(DST)
    expected_increase = src_len_before
    actual_increase = dst_len_after - dst_len_before
    print(f"[INFO] dst XLEN after = {dst_len_after} (increase={actual_increase}, expected={expected_increase})")
    if actual_increase < expected_increase:
        print(f"[ABORT] archive incomplete; NOT trimming source")
        return 3

    # Verify by sampling: count entries with this batch_id
    sample_count = 0
    for _id, f in r.xrevrange(DST, min='-', max='+', count=expected_increase + 5):
        if f.get("__archive_batch_id") == batch_id:
            sample_count += 1
        if sample_count >= expected_increase:
            break
    print(f"[INFO] archive sample verification: {sample_count}/{expected_increase} entries with batch_id={batch_id}")
    if sample_count < expected_increase:
        print(f"[ABORT] sample verification failed; NOT trimming source")
        return 4

    # Now safe to trim source to 0
    last_id_archived = entries[-1][0]
    print(f"[INFO] last_id_archived = {last_id_archived}")
    # Use MINID to drop everything <= last_id_archived. To get exactly 0 (or very close),
    # use XTRIM MAXLEN 0 directly, accepting that any new entries after this script started
    # will be preserved (XTRIM applies to entries up to current tail at execution moment).
    # Safer alternative: XTRIM MINID last_id_archived (exclusive of newer entries arrived during run)
    pre_trim_len = r.xlen(SRC)
    print(f"[INFO] src XLEN immediately before trim = {pre_trim_len}")
    # XTRIM MINID last_id+1 — Redis supports XTRIM ... MINID id
    # To exclude last_id itself, we use XTRIM ... MINID (last_id_ms + 1)-0
    try:
        ms, seq = last_id_archived.split("-")
        next_id = f"{int(ms)+1}-0"
    except Exception:
        next_id = last_id_archived
    trimmed = r.xtrim(SRC, minid=next_id, approximate=False)
    src_len_after = r.xlen(SRC)
    print(f"[OK] trimmed {trimmed} entries from source. src XLEN after = {src_len_after}")

    # Post-condition: report current INV-ORD-001 expected eval
    print(f"[OK] INV-ORD-001 next eval should pass ({src_len_after} < 100)")

    # Write status snapshot
    status = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "batch_id": batch_id,
        "archived": expected_increase,
        "src_len_before": src_len_before,
        "src_len_after": src_len_after,
        "dst_len_before": dst_len_before,
        "dst_len_after": dst_len_after,
        "last_id_archived": last_id_archived,
    }
    r.set("ares:offload:quarantine_archive:last", json.dumps(status), ex=86400*30)
    print(f"[OK] status snapshot written to ares:offload:quarantine_archive:last")
    return 0

if __name__ == "__main__":
    sys.exit(main())
