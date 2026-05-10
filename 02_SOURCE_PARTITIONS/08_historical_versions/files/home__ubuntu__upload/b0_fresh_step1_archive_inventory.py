#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
from typing import Dict, Any, List

EXPECTED_ADAPTER_SHA = {
    'B0_BASELINE': 'f6d33b1c3d65dc102ebdaaec910c8f2ab8dcd03fd5b7f8c86e5ce7fac337300c',
    'B0_PLUS_T1_B1_IDENTITY_MARKER': '2412426c83acf0c2a9f025f7dc51d1db64e54d0074dbf0da6586a246e18a6748',
    'B0_PLUS_T2_STAGE_D_SCHEMA_CONTRACT': '2c42b3447ac3dfc198bb14971ad4d8d4a83600840a6508e724238ddef436fe37',
    'B0_PLUS_T3_CLOSED_EVENT_LEDGER': 'ebdf68d41533bd24e76f5b008fecbf1f67948d54dd0b094b0b096059520e60db',
    'B0_PLUS_T4_EXECUTION_ROUTER': '36e1f5fc600088c023f80b05f8b8d49948961d302eda57d596053da3c8a928eb',
    'B0_PLUS_T5_ORDER_INTENT_EXECUTOR': 'c6f2a2c6c709e2218931f7aa2ce382ee81e1d19a208bbeb5d0d33fdad180de23',
    'B0_PLUS_T6_COST_BACKTEST_ENGINE': 'b72e71b8d10803b98a30c406290975d702f42942f717640163112a390cdd4fdd',
    'B0_PLUS_T7_PORTFOLIO_OPTIMIZER': 'bd81db017f877b6cf8c628f65e141648810a298ed80b66a4f0c3391a75448a1e',
    'B0_PLUS_T8_SELF_DIAGNOSTICS': 'b7fc071c5655e1b392eff0ea61a589edc3257a050802f01855593b7be90bed08',
}
EXTRA_FILES = [
    'diffs/phase15_2_actual_backtest_runner.loop_breaking.patch',
    'harness/full_engine_runner_manifest.json',
    'reports/ab_run_matrix.json',
    'reports/replay_harness_readiness.json',
]


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def safe_extract_tar(tar: tarfile.TarFile, path: pathlib.Path) -> None:
    base = path.resolve()
    for member in tar.getmembers():
        target = (base / member.name).resolve()
        if not str(target).startswith(str(base) + os.sep) and target != base:
            raise RuntimeError(f'unsafe tar member path: {member.name}')
    tar.extractall(path)


def extract_archive(archive: pathlib.Path, extracted_dir: pathlib.Path) -> Dict[str, Any]:
    extracted_dir.mkdir(parents=True, exist_ok=True)
    log: Dict[str, Any] = {'archive': str(archive), 'archive_size': archive.stat().st_size, 'archive_sha256': sha256(archive)}
    try:
        with tarfile.open(archive, 'r:*') as tar:
            names = tar.getnames()
            safe_extract_tar(tar, extracted_dir)
            log['method'] = 'tarfile_r_star'
            log['member_count'] = len(names)
            log['first_members'] = names[:20]
            return log
    except tarfile.TarError as e:
        log['tar_error'] = repr(e)
    raw_out = extracted_dir / archive.name.replace('.gz', '')
    try:
        with gzip.open(archive, 'rb') as src, raw_out.open('wb') as dst:
            shutil.copyfileobj(src, dst)
        log['method'] = 'gzip_raw'
        log['raw_output'] = str(raw_out)
        log['raw_output_size'] = raw_out.stat().st_size
        log['raw_output_sha256'] = sha256(raw_out)
        return log
    except Exception as e:
        log['gzip_error'] = repr(e)
        raise RuntimeError(f'archive extraction failed: {log}')


def main() -> int:
    archive = pathlib.Path(sys.argv[1]).resolve()
    work_dir = pathlib.Path(sys.argv[2]).resolve()
    reports = work_dir / 'reports'
    logs = work_dir / 'logs'
    extracted = work_dir / 'extracted'
    reports.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    status: Dict[str, Any] = {
        'schema': 'phase9_b1_b0_only_step1_archive_inventory_v1',
        'created_utc': dt.datetime.utcnow().isoformat() + 'Z',
        'step': 'STEP_1_ARCHIVE_EXTRACT_AND_GROUND_TRUTH_VERIFY',
        'status': 'FAILED',
        'work_dir': str(work_dir),
        'checks': {},
        'adapters': [],
        'extra_files': [],
        'notes': [],
    }
    if not archive.exists():
        status['checks']['archive_exists'] = {'status': 'FAIL', 'path': str(archive)}
        write_json(reports / 'archive_actual_sha_inventory.json', status)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 2
    status['checks']['archive_exists'] = {'status': 'PASS', 'path': str(archive), 'size': archive.stat().st_size, 'sha256': sha256(archive)}
    extraction_log = extract_archive(archive, extracted)
    status['extraction'] = extraction_log
    candidates = list(extracted.rglob('phase9_b1_full_engine_harness_repair_pack'))
    if not candidates:
        status['checks']['repair_pack_dir_exists'] = {'status': 'FAIL', 'searched_under': str(extracted)}
        write_json(reports / 'archive_actual_sha_inventory.json', status)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 2
    pack = candidates[0]
    status['pack_root'] = str(pack)
    status['checks']['repair_pack_dir_exists'] = {'status': 'PASS', 'path': str(pack)}
    adapter_dir = pack / 'variant_adapters'
    all_match = True
    for variant_id, expected in EXPECTED_ADAPTER_SHA.items():
        p = adapter_dir / f'{variant_id}.adapter.json'
        item = {'variant_id': variant_id, 'path': str(p), 'expected_sha256': expected, 'exists': p.exists()}
        if p.exists():
            actual = sha256(p)
            item['actual_sha256'] = actual
            item['status'] = 'PASS' if actual == expected else 'FAIL'
            if actual != expected:
                all_match = False
        else:
            item['actual_sha256'] = None
            item['status'] = 'FAIL'
            all_match = False
        status['adapters'].append(item)
    status['checks']['all_9_adapter_actual_sha_match_ground_truth'] = {'status': 'PASS' if all_match else 'FAIL'}
    extras_ok = True
    for rel in EXTRA_FILES:
        p = pack / rel
        item = {'relative_path': rel, 'path': str(p), 'exists': p.exists()}
        if p.exists():
            item['size'] = p.stat().st_size
            item['sha256'] = sha256(p)
            item['status'] = 'PASS'
        else:
            item['status'] = 'FAIL'
            extras_ok = False
        status['extra_files'].append(item)
    status['checks']['extra_files_inventoried'] = {'status': 'PASS' if extras_ok else 'FAIL'}
    status['status'] = 'PASS' if all_match and extras_ok else 'FAILED'
    write_json(reports / 'archive_actual_sha_inventory.json', status)
    (work_dir / 'CURRENT_WORK_DIR.txt').write_text(str(work_dir) + '\n', encoding='utf-8')
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status['status'] == 'PASS' else 2

if __name__ == '__main__':
    raise SystemExit(main())
