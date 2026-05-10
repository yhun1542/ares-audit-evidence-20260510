#!/usr/bin/env python3
"""
Firebase Firestore + Storage 자동 백업 스크립트
프로젝트: braindump-jasoneye
실행: 매일 새벽 2시 (KST) via cron
보관: 최근 30일치 유지
"""

import os
import sys
import json
import gzip
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ── 설정 ──────────────────────────────────────────────────────────────────────
SERVICE_ACCOUNT_PATH = "/home/ubuntu/firebase_service_account.json"
BACKUP_DIR = "/home/ubuntu/firebase_backup"
KEEP_DAYS = 30
LOG_FILE = "/home/ubuntu/firebase_backup/backup.log"
PROJECT_ID = "braindump-jasoneye"
STORAGE_BUCKET = "braindump-jasoneye.firebasestorage.app"

# ── 로깅 설정 ─────────────────────────────────────────────────────────────────
os.makedirs(BACKUP_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def init_firebase():
    """Firebase Admin SDK 초기화"""
    import firebase_admin
    from firebase_admin import credentials, firestore, storage

    if not firebase_admin._apps:
        cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
        firebase_admin.initialize_app(cred, {"storageBucket": STORAGE_BUCKET})
        log.info("Firebase 초기화 완료")

    db = firestore.client()
    bucket = storage.bucket()
    return db, bucket


def backup_firestore(db, date_str):
    """Firestore 전체 컬렉션 백업"""
    log.info("Firestore 백업 시작...")
    backup_data = {}
    total_docs = 0

    # 최상위 컬렉션 목록 가져오기
    collections = db.collections()
    for col in collections:
        col_name = col.id
        log.info(f"  컬렉션 백업 중: {col_name}")
        backup_data[col_name] = {}

        # 컬렉션 내 모든 문서
        docs = col.stream()
        for doc in docs:
            doc_data = doc.to_dict()
            # Firestore Timestamp → ISO 문자열 변환
            doc_data = convert_timestamps(doc_data)
            backup_data[col_name][doc.id] = doc_data
            total_docs += 1

            # 서브컬렉션 처리 (users/{uid}/archive 등)
            sub_cols = doc.reference.collections()
            for sub_col in sub_cols:
                sub_key = f"{col_name}/{doc.id}/{sub_col.id}"
                backup_data[sub_key] = {}
                sub_docs = sub_col.stream()
                for sub_doc in sub_docs:
                    sub_data = sub_doc.to_dict()
                    sub_data = convert_timestamps(sub_data)
                    backup_data[sub_key][sub_doc.id] = sub_data
                    total_docs += 1

    # JSON 파일로 저장 (gzip 압축)
    out_path = os.path.join(BACKUP_DIR, f"firestore_{date_str}.json.gz")
    json_bytes = json.dumps(backup_data, ensure_ascii=False, indent=2).encode("utf-8")
    with gzip.open(out_path, "wb") as f:
        f.write(json_bytes)

    size_kb = os.path.getsize(out_path) / 1024
    log.info(f"Firestore 백업 완료: {total_docs}개 문서, {size_kb:.1f} KB → {out_path}")
    return total_docs, size_kb


def backup_storage_list(bucket, date_str):
    """Storage 파일 목록 백업 (메타데이터만, 파일 자체는 Firebase에 보관)"""
    log.info("Storage 파일 목록 백업 시작...")
    blobs = list(bucket.list_blobs())
    file_list = []

    for blob in blobs:
        file_list.append({
            "name": blob.name,
            "size": blob.size,
            "content_type": blob.content_type,
            "updated": blob.updated.isoformat() if blob.updated else None,
            "public_url": blob.public_url,
            "md5_hash": blob.md5_hash,
        })

    out_path = os.path.join(BACKUP_DIR, f"storage_list_{date_str}.json.gz")
    json_bytes = json.dumps(file_list, ensure_ascii=False, indent=2).encode("utf-8")
    with gzip.open(out_path, "wb") as f:
        f.write(json_bytes)

    size_kb = os.path.getsize(out_path) / 1024
    log.info(f"Storage 목록 백업 완료: {len(blobs)}개 파일, {size_kb:.1f} KB → {out_path}")
    return len(blobs), size_kb


def convert_timestamps(data):
    """Firestore Timestamp 객체를 ISO 문자열로 변환"""
    from google.cloud.firestore_v1._helpers import DatetimeWithNanoseconds
    from google.protobuf.timestamp_pb2 import Timestamp

    if isinstance(data, dict):
        return {k: convert_timestamps(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [convert_timestamps(v) for v in data]
    elif hasattr(data, "isoformat"):
        return data.isoformat()
    elif hasattr(data, "seconds") and hasattr(data, "nanos"):
        # Firestore Timestamp
        try:
            return datetime.utcfromtimestamp(data.seconds + data.nanos / 1e9).isoformat()
        except Exception:
            return str(data)
    else:
        return data


def cleanup_old_backups():
    """30일 이상 된 백업 파일 삭제"""
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    removed = 0
    for f in Path(BACKUP_DIR).glob("*.json.gz"):
        if f.stat().st_mtime < cutoff.timestamp():
            f.unlink()
            log.info(f"  오래된 백업 삭제: {f.name}")
            removed += 1
    if removed:
        log.info(f"오래된 백업 {removed}개 삭제 완료")
    else:
        log.info("삭제할 오래된 백업 없음")


def main():
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info(f"{'='*60}")
    log.info(f"Firebase 자동 백업 시작: {date_str}")
    log.info(f"{'='*60}")

    try:
        db, bucket = init_firebase()

        # Firestore 백업
        fs_docs, fs_kb = backup_firestore(db, date_str)

        # Storage 목록 백업
        st_files, st_kb = backup_storage_list(bucket, date_str)

        # 오래된 백업 정리
        cleanup_old_backups()

        # 백업 요약
        summary = {
            "date": date_str,
            "firestore_docs": fs_docs,
            "firestore_size_kb": round(fs_kb, 1),
            "storage_files": st_files,
            "storage_list_kb": round(st_kb, 1),
            "status": "success",
        }
        summary_path = os.path.join(BACKUP_DIR, "last_backup_summary.json")
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        log.info(f"{'='*60}")
        log.info(f"백업 완료! Firestore {fs_docs}개 문서, Storage {st_files}개 파일")
        log.info(f"{'='*60}")

    except Exception as e:
        log.error(f"백업 실패: {e}", exc_info=True)
        summary = {"date": date_str, "status": "failed", "error": str(e)}
        summary_path = os.path.join(BACKUP_DIR, "last_backup_summary.json")
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)
        sys.exit(1)


if __name__ == "__main__":
    main()
