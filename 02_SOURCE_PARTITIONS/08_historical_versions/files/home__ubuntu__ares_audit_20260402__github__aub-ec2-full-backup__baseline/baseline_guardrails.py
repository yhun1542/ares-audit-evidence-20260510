#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
baseline_guardrails.py
================================================================================
ARES 베이스라인 고정 + 재발 방지 "가드레일" 실행기

목표
- 데이터/코드/파라미터 드리프트를 '즉시' 탐지해서 중단
- Look-ahead bias(시점 누출) 재발 차단: signal_lag=1, bfill 금지, verify_no_lookahead 실행
- OOS 검증 결과(특히 OOS/IS 비율)를 자동으로 리포트 + 통과/실패 판정
- 실험 결과를 "항상" 동일 포맷(JSON + Markdown)으로 저장해 혼란 제거

사용 예시(EC2)
  # 0) (권장) 프로젝트 폴더
  mkdir -p ~/ares_guardrails && cd ~/ares_guardrails

  # 1) 베이스라인 '지문' 초기화(한 번만)
  python3 baseline_guardrails.py init \
    --engine ./ARES_CLEAN_BASELINE_v1.py \
    --db /home/ubuntu/ares_x_unified_database/ares_universal_v2.db \
    --params ./mega_pareto_v2_best.json \
    --outdir ./_baseline

  # 2) 이후 모든 실행은 run
  python3 baseline_guardrails.py run \
    --engine ./ARES_CLEAN_BASELINE_v1.py \
    --db /home/ubuntu/ares_x_unified_database/ares_universal_v2.db \
    --params ./mega_pareto_v2_best.json \
    --manifest ./_baseline/baseline_manifest.json \
    --outdir ./runs \
    --strict

Exit code
  0: PASS
  1: WARNING (strict 모드에선 실패로 처리)
  2: FAIL

주의
- 본 가드레일은 "engine 모듈"을 import 해서 실행합니다.
- 엔진 파일 내부에 절대경로(/home/claude 등)가 있으면 자동으로 outdir 기반으로 패치된 복사본을 만들어 import합니다.
================================================================================
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import platform
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# 작은 유틸
# -----------------------------
def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# -----------------------------
# 스캔 결과 구조체
# -----------------------------
@dataclasses.dataclass
class StaticScanResult:
    errors: List[str]
    warnings: List[str]
    infos: List[str]


@dataclasses.dataclass
class Fingerprint:
    created_at: str
    engine_path: str
    engine_sha256: str
    params_path: str
    params_sha256: str
    db_path: str
    data_fingerprint: Dict[str, Any]
    python: Dict[str, Any]


# -----------------------------
# 엔진 패치(절대경로/권한 문제 방지)
# -----------------------------
def patch_engine_source(engine_path: Path, outdir: Path) -> Tuple[Path, bool, List[str]]:
    """
    - /home/claude 같은 절대경로가 있으면 outdir 아래로 치환한 "패치본"을 생성
    - 원본은 건드리지 않음
    """
    src = read_text(engine_path)
    notes: List[str] = []
    changed = False

    # 흔한 문제: 로그/결과 폴더가 존재하지 않는 사용자 홈(예: /home/claude)
    # 필요 시 여기에 치환 규칙 추가
    replacements = {
        "/home/claude/ares_clean_baseline.log": str(outdir / "ares_clean_baseline.log"),
        "/home/claude/ares_clean_results": str(outdir / "ares_clean_results"),
        "/home/claude/": str(outdir) + "/",  # 광역 치환(최후의 수단)
    }

    for old, new in replacements.items():
        if old in src:
            src = src.replace(old, new)
            changed = True
            notes.append(f"PATCH: '{old}' -> '{new}'")

    if not changed:
        return engine_path, False, notes

    patched_path = outdir / f"patched_{engine_path.name}"
    write_text(patched_path, src)
    return patched_path, True, notes


def import_module_from_path(py_path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, str(py_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"모듈 로딩 실패: {py_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod


# -----------------------------
# 정적 검사 (룩어헤드/드리프트 위험 신호)
# -----------------------------
def static_scan_python(py_path: Path) -> StaticScanResult:
    txt = read_text(py_path)

    errors: List[str] = []
    warnings: List[str] = []
    infos: List[str] = []

    # 1) bfill 금지 (체크리스트 핵심) — ffill은 허용
    if re.search(r"\.bfill\s*\(", txt) or re.search(r"fillna\s*\(\s*method\s*=\s*['\"]bfill['\"]\s*\)", txt):
        errors.append("bfill() 또는 fillna(method='bfill') 발견 → 룩어헤드 위험(금지).")

    # 2) 미래 시프트 금지
    if re.search(r"\.shift\s*\(\s*-\s*\d+", txt):
        errors.append("shift(-k) 발견 → 미래 데이터 누출 가능(금지).")

    # 3) signal_lag=0 강력 경고/에러
    if re.search(r"\bsignal_lag\s*=\s*0\b", txt) or re.search(r"\bSIGNAL_LAG\s*=\s*0\b", txt):
        errors.append("signal_lag=0 설정 발견 → 당일 신호/당일 체결 형태(현실 불가 + 룩어헤드).")

    # 4) signal_lag=1 존재 확인(없으면 경고)
    if not (re.search(r"\bsignal_lag\s*=\s*1\b", txt) or re.search(r"\bSIGNAL_LAG\s*=\s*1\b", txt)):
        warnings.append("signal_lag=1 설정을 코드에서 명시적으로 찾지 못함(엔진 내부 적용 여부 확인 필요).")

    # 5) vix[t] 직접 참조(백테스트 루프에서면 위험) — 경고만
    if re.search(r"\bvix\s*\[\s*t\s*\]", txt):
        warnings.append("vix[t] 직접 참조 패턴 감지 → 백테스트 루프에서면 룩어헤드 위험(전일 vix[t-1] 권장).")

    # 6) returns[t]를 팩터 계산에 쓰는 흔적(완전 판별은 어려워 경고)
    if re.search(r"\breturns\s*\[\s*t\s*[,\]]", txt):
        warnings.append("returns[t] 직접 참조 패턴 감지 → 팩터/레짐 계산이면 룩어헤드 위험(실현수익률 계산이면 OK).")

    # 7) 과도한 집중 TOP_K=5 같은 경우(과최적화 위험)
    m = re.search(r"\bTOP_K\s*=\s*(\d+)", txt)
    if m:
        try:
            k = int(m.group(1))
            if k <= 5:
                warnings.append(f"TOP_K={k} 설정 감지 → 집중도 과도(과적합/테일리스크 가능).")
        except Exception:
            pass

    infos.append(f"파일 크기: {py_path.stat().st_size} bytes")
    return StaticScanResult(errors=errors, warnings=warnings, infos=infos)


# -----------------------------
# 데이터 지문(엔진 기준) 수집
# -----------------------------
def try_engine_data_fingerprint(engine_mod: Any, db_path: str) -> Dict[str, Any]:
    """
    엔진이 load_and_preprocess_data(db_path)를 제공하면 그 결과를 기반으로
    '엔진이 실제로 쓰는 데이터' 지문을 만든다.
    """
    fp: Dict[str, Any] = {"available": False}
    if not hasattr(engine_mod, "load_and_preprocess_data"):
        fp["reason"] = "engine_mod.load_and_preprocess_data 없음"
        return fp

    try:
        returns_np, prices_np, vix_np, dates, symbols = engine_mod.load_and_preprocess_data(db_path)
        n_dates = int(getattr(returns_np, "shape", [0, 0])[0])
        n_assets = int(getattr(returns_np, "shape", [0, 0])[1])

        sym_hash = sha256_bytes((",".join(symbols)).encode("utf-8")) if symbols else None
        # dates는 pandas Timestamp일 수 있으니 문자열로 안전 변환
        date_strs = [str(d) for d in dates] if dates else []
        date_hash = sha256_bytes((",".join(date_strs[:50] + date_strs[-50:])).encode("utf-8")) if date_strs else None

        # returns_np는 nan_to_num 처리되어 있을 수 있으므로 "0 비율"을 참고치로 기록
        zero_ratio = None
        try:
            import numpy as np  # 엔진 의존
            zero_ratio = float(np.mean(returns_np == 0.0))
        except Exception:
            pass

        fp = {
            "available": True,
            "n_dates": n_dates,
            "n_assets": n_assets,
            "date_range": f"{date_strs[0]} ~ {date_strs[-1]}" if date_strs else None,
            "symbols_hash": sym_hash,
            "dates_hash": date_hash,
            "zero_ratio_returns_np": zero_ratio,
            "symbols_preview": symbols[:10],
        }
        return fp
    except Exception as e:
        fp["available"] = False
        fp["reason"] = f"load_and_preprocess_data 실행 실패: {e}"
        fp["traceback"] = traceback.format_exc(limit=5)
        return fp


# -----------------------------
# 룩어헤드 검증(엔진 제공 테스트 우선)
# -----------------------------
def run_lookahead_tests(engine_mod: Any) -> Tuple[bool, List[str]]:
    """
    엔진이 verify_no_lookahead()를 제공하면 그걸 1순위로 실행.
    없으면 가능한 범위에서 최소 테스트를 수행.
    """
    notes: List[str] = []
    if hasattr(engine_mod, "verify_no_lookahead"):
        try:
            ok = bool(engine_mod.verify_no_lookahead())
            notes.append(f"verify_no_lookahead() -> {ok}")
            return ok, notes
        except Exception as e:
            notes.append(f"verify_no_lookahead() 실행 실패: {e}")
            notes.append(traceback.format_exc(limit=5))
            return False, notes

    # fallback: 엔진의 clean 함수가 있으면 마지막날 변경 불변성 간단 점검
    required = ["compute_momentum_clean", "compute_volatility_clean", "compute_cumret_clean"]
    if all(hasattr(engine_mod, n) for n in required):
        try:
            import numpy as np
            np.random.seed(42)
            n_dates = 400
            n_assets = 8
            prices = np.exp(np.cumsum(np.random.randn(n_dates, n_assets) * 0.02, axis=0))
            returns = np.diff(prices, axis=0) / prices[:-1]

            mom_o = engine_mod.compute_momentum_clean(prices, 20)
            vol_o = engine_mod.compute_volatility_clean(returns, 20)
            cum_o = engine_mod.compute_cumret_clean(returns, 20)

            prices2 = prices.copy()
            prices2[-1, :] *= 2
            returns2 = np.diff(prices2, axis=0) / prices2[:-1]

            mom_m = engine_mod.compute_momentum_clean(prices2, 20)
            vol_m = engine_mod.compute_volatility_clean(returns2, 20)
            cum_m = engine_mod.compute_cumret_clean(returns2, 20)

            mom_diff = float(np.max(np.abs(mom_o[:-1] - mom_m[:-1])))
            vol_diff = float(np.max(np.abs(vol_o[:-1] - vol_m[:-1])))
            cum_diff = float(np.max(np.abs(cum_o[:-1] - cum_m[:-1])))

            notes.append(f"fallback last-day invariance diff: mom={mom_diff:.3e}, vol={vol_diff:.3e}, cum={cum_diff:.3e}")
            ok = (mom_diff < 1e-10 and vol_diff < 1e-10 and cum_diff < 1e-10)
            return ok, notes
        except Exception as e:
            notes.append(f"fallback 테스트 실패: {e}")
            notes.append(traceback.format_exc(limit=5))
            return False, notes

    notes.append("룩어헤드 자동 테스트를 실행할 수 있는 훅이 없음(verify_no_lookahead 또는 clean 함수 미존재).")
    return False, notes


# -----------------------------
# 성과 체크(체크리스트 기반)
# -----------------------------
def evaluate_results(results: Any, oos_is_ratio_min: float = 0.70) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    엔진 결과 dict에서 핵심 지표를 읽어 PASS/FAIL 판정.
    - 체크리스트: OOS/IS Sharpe 비율 > 0.7 권장
    """
    notes: List[str] = []
    extracted: Dict[str, Any] = {}

    if not isinstance(results, dict):
        return False, ["엔진 실행 결과가 dict가 아님(리포트 생성 불가)."], extracted

    metrics = results.get("metrics", {})
    wf = results.get("walk_forward", {})
    extracted["metrics"] = metrics
    extracted["walk_forward"] = wf
    extracted["regime_analysis"] = results.get("regime_analysis", {})

    # 기본 값
    sharpe = metrics.get("sharpe", None)
    mdd = metrics.get("mdd", None)
    is_sh = wf.get("mean_is_sharpe", None)
    oos_sh = wf.get("mean_oos_sharpe", None)

    if sharpe is not None:
        notes.append(f"Total Sharpe: {sharpe:.4f}")
    if mdd is not None:
        notes.append(f"MDD: {mdd:.2%}" if isinstance(mdd, float) else f"MDD: {mdd}")

    if is_sh is None or oos_sh is None:
        notes.append("Walk-forward(IS/OOS) 결과가 없음 → OOS/IS 비율 체크 불가.")
        return False, notes, extracted

    ratio = float(oos_sh) / (float(is_sh) + 1e-12)
    extracted["oos_is_ratio"] = ratio
    notes.append(f"WF Mean IS Sharpe: {is_sh:.4f}")
    notes.append(f"WF Mean OOS Sharpe: {oos_sh:.4f}")
    notes.append(f"OOS/IS Ratio: {ratio:.2f}")

    # PASS 기준
    ok = True
    if ratio < oos_is_ratio_min:
        ok = False
        notes.append(f"FAIL: OOS/IS 비율 {ratio:.2f} < {oos_is_ratio_min:.2f}")

    # 경고 신호(체크리스트)
    if is_sh is not None and float(is_sh) > 2.0:
        notes.append("WARN: IS Sharpe > 2.0 → 선택편향/과최적화 가능성 점검 권장.")

    # 레짐 한쪽만 과도하게 튀는 경우 경고
    regime = extracted.get("regime_analysis", {})
    if isinstance(regime, dict) and regime:
        sharpes = []
        for k, v in regime.items():
            if isinstance(v, dict) and "sharpe" in v:
                sharpes.append((k, float(v["sharpe"])))
        if sharpes:
            max_reg = max(sharpes, key=lambda x: x[1])
            min_reg = min(sharpes, key=lambda x: x[1])
            if max_reg[1] - min_reg[1] >= 3.0:
                notes.append(f"WARN: 레짐별 Sharpe 편차 큼 (max {max_reg}, min {min_reg}) → 특정 레짐 과대적합 가능.")

    return ok, notes, extracted


# -----------------------------
# 리포트 생성
# -----------------------------
def write_markdown_report(path: Path, payload: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append(f"# ARES Baseline Guardrails Report")
    lines.append("")
    lines.append(f"- generated_at: {payload.get('generated_at')}")
    lines.append(f"- status: **{payload.get('status')}**")
    lines.append("")
    lines.append("## Inputs")
    lines.append(f"- engine: `{payload.get('engine_path')}`")
    lines.append(f"- engine_sha256: `{payload.get('engine_sha256')}`")
    lines.append(f"- params: `{payload.get('params_path')}`")
    lines.append(f"- params_sha256: `{payload.get('params_sha256')}`")
    lines.append(f"- db: `{payload.get('db_path')}`")
    lines.append("")
    lines.append("## Guardrails")
    g = payload.get("guardrails", {})
    for k in ["static_scan", "fingerprint_match", "lookahead_tests", "performance_gate"]:
        v = g.get(k, {})
        lines.append(f"### {k}")
        lines.append(f"- pass: **{v.get('pass')}**")
        for msg in v.get("notes", []):
            lines.append(f"  - {msg}")
        lines.append("")

    lines.append("## Data Fingerprint (engine-based)")
    dfp = payload.get("data_fingerprint", {})
    for k, v in dfp.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    # 성과 요약
    lines.append("## Performance Snapshot")
    perf = payload.get("performance", {})
    metrics = perf.get("metrics", {})
    wf = perf.get("walk_forward", {})
    lines.append(f"- Total Sharpe: {metrics.get('sharpe')}")
    lines.append(f"- MDD: {metrics.get('mdd')}")
    lines.append(f"- WF Mean IS Sharpe: {wf.get('mean_is_sharpe')}")
    lines.append(f"- WF Mean OOS Sharpe: {wf.get('mean_oos_sharpe')}")
    lines.append(f"- OOS/IS Ratio: {perf.get('oos_is_ratio')}")
    lines.append("")
    lines.append("### Regime Sharpe")
    regime = perf.get("regime_analysis", {})
    if isinstance(regime, dict):
        for k, v in regime.items():
            if isinstance(v, dict):
                lines.append(f"- {k}: sharpe={v.get('sharpe')}, days={v.get('days')}, pct={v.get('pct')}")
    lines.append("")

    write_text(path, "\n".join(lines))


# -----------------------------
# manifest 생성/검증
# -----------------------------
def make_manifest(engine_path: Path, db_path: str, params_path: Path, outdir: Path) -> Fingerprint:
    patched_engine_path, patched, patch_notes = patch_engine_source(engine_path, outdir / "_patched")
    if patched:
        print(f"[INFO] 엔진 패치본 생성: {patched_engine_path}")
        for n in patch_notes:
            print(f"  - {n}")

    engine_sha = sha256_file(patched_engine_path)
    params_sha = sha256_file(params_path) if params_path.exists() else sha256_bytes(b"")
    engine_mod = import_module_from_path(patched_engine_path, f"ares_engine_{engine_sha[:8]}")
    data_fp = try_engine_data_fingerprint(engine_mod, db_path)

    py = {
        "version": sys.version.replace("\n", " "),
        "platform": platform.platform(),
    }

    return Fingerprint(
        created_at=_now(),
        engine_path=str(engine_path),
        engine_sha256=engine_sha,
        params_path=str(params_path),
        params_sha256=params_sha,
        db_path=db_path,
        data_fingerprint=data_fp,
        python=py,
    )


def compare_manifest(manifest: Dict[str, Any], current: Fingerprint) -> Tuple[bool, List[str]]:
    notes: List[str] = []
    ok = True

    # 1) 엔진 해시 고정
    if manifest.get("engine_sha256") != current.engine_sha256:
        ok = False
        notes.append("FAIL: engine_sha256 불일치(코드 변경 감지).")

    # 2) 파라미터 해시 고정
    if manifest.get("params_sha256") != current.params_sha256:
        ok = False
        notes.append("FAIL: params_sha256 불일치(파라미터 변경 감지).")

    # 3) 데이터 지문(엔진이 실제 사용하는 데이터 기준)
    mdfp = manifest.get("data_fingerprint", {})
    cdfp = current.data_fingerprint

    # 비교 포인트(핵심만)
    keys = ["n_dates", "n_assets", "date_range", "symbols_hash", "dates_hash"]
    for k in keys:
        mv = mdfp.get(k)
        cv = cdfp.get(k)
        if mv is None or cv is None:
            continue
        if mv != cv:
            ok = False
            notes.append(f"FAIL: data_fingerprint.{k} 불일치 → 드리프트(데이터 기간/종목/정렬 변화) 의심.")

    if ok:
        notes.append("PASS: manifest와 현재 지문이 일치(재현성 OK).")

    return ok, notes


# -----------------------------
# main
# -----------------------------
def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sub.add_parser("init", help="베이스라인 manifest 생성(한 번만)")
    p_init.add_argument("--engine", required=True, help="엔진 파이썬 파일 경로")
    p_init.add_argument("--db", required=True, help="sqlite db 경로")
    p_init.add_argument("--params", required=True, help="파라미터 json 경로(mega_pareto 등)")
    p_init.add_argument("--outdir", required=True, help="manifest 저장 폴더")

    # run
    p_run = sub.add_parser("run", help="가드레일 적용 + 엔진 실행 + 리포트 생성")
    p_run.add_argument("--engine", required=True, help="엔진 파이썬 파일 경로")
    p_run.add_argument("--db", required=True, help="sqlite db 경로")
    p_run.add_argument("--params", required=True, help="파라미터 json 경로(mega_pareto 등)")
    p_run.add_argument("--manifest", required=True, help="baseline_manifest.json 경로")
    p_run.add_argument("--outdir", required=True, help="실행 결과 저장 폴더")
    p_run.add_argument("--strict", action="store_true", help="경고도 실패 처리")
    p_run.add_argument("--oos_is_ratio_min", type=float, default=0.70, help="OOS/IS 비율 최소 기준(기본 0.70)")

    # scan
    p_scan = sub.add_parser("scan", help="정적 검사만 수행")
    p_scan.add_argument("--engine", required=True, help="엔진 파이썬 파일 경로")

    args = p.parse_args()
    cmd = args.cmd

    if cmd == "scan":
        engine_path = Path(args.engine).resolve()
        scan = static_scan_python(engine_path)
        print(f"[SCAN] {engine_path}")
        for msg in scan.errors:
            print(f"  [ERROR] {msg}")
        for msg in scan.warnings:
            print(f"  [WARN ] {msg}")
        for msg in scan.infos:
            print(f"  [INFO ] {msg}")
        if scan.errors:
            return 2
        if scan.warnings:
            return 1
        return 0

    if cmd == "init":
        outdir = Path(args.outdir).resolve()
        outdir.mkdir(parents=True, exist_ok=True)

        engine_path = Path(args.engine).resolve()
        params_path = Path(args.params).resolve()

        fp = make_manifest(engine_path, args.db, params_path, outdir)
        manifest_path = outdir / "baseline_manifest.json"
        dump_json(manifest_path, dataclasses.asdict(fp))
        print(f"[OK] baseline manifest saved: {manifest_path}")
        return 0

    if cmd == "run":
        out_root = Path(args.outdir).resolve()
        out_root.mkdir(parents=True, exist_ok=True)
        run_dir = out_root / _dt.datetime.now().strftime("run_%Y%m%d_%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)

        engine_path = Path(args.engine).resolve()
        params_path = Path(args.params).resolve()
        manifest_path = Path(args.manifest).resolve()
        baseline_manifest = load_json(manifest_path)

        # 1) 정적 검사
        static = static_scan_python(engine_path)

        # 2) manifest 기반 지문 생성/비교
        fp = make_manifest(engine_path, args.db, params_path, run_dir)
        fp_ok, fp_notes = compare_manifest(baseline_manifest, fp)

        # 3) 엔진 import (패치본 사용)
        patched_engine_path, patched, patch_notes = patch_engine_source(engine_path, run_dir / "_patched")
        engine_sha = sha256_file(patched_engine_path)
        engine_mod = import_module_from_path(patched_engine_path, f"ares_engine_{engine_sha[:8]}")

        # 4) 룩어헤드 테스트
        look_ok, look_notes = run_lookahead_tests(engine_mod)

        # 5) 엔진 실행
        engine_results = None
        engine_err = None
        try:
            # 파라미터 파일을 엔진이 직접 읽지는 않지만, "고정된 실험" 증빙용으로 함께 저장
            dump_json(run_dir / "params_copy.json", load_json(params_path) if params_path.exists() else {})
            if hasattr(engine_mod, "run_baseline_test"):
                engine_results = engine_mod.run_baseline_test(args.db)
            elif hasattr(engine_mod, "main"):
                engine_results = engine_mod.main(args.db)  # type: ignore
            else:
                # 최후: subprocess로 실행(결과 dict는 못 읽고 성공 여부만)
                import subprocess
                r = subprocess.run([sys.executable, str(patched_engine_path)], capture_output=True, text=True)
                dump_json(run_dir / "subprocess_stdio.json", {"stdout": r.stdout[-20000:], "stderr": r.stderr[-20000:], "returncode": r.returncode})
                engine_results = {"_subprocess_only": True, "returncode": r.returncode}
        except Exception as e:
            engine_err = str(e)
            dump_json(run_dir / "engine_exception.json", {"error": str(e), "traceback": traceback.format_exc()})

        # 6) 성과 게이트
        perf_ok = False
        perf_notes: List[str] = []
        perf_extracted: Dict[str, Any] = {}
        if engine_err is None:
            perf_ok, perf_notes, perf_extracted = evaluate_results(engine_results, oos_is_ratio_min=float(args.oos_is_ratio_min))
        else:
            perf_ok = False
            perf_notes = [f"엔진 실행 실패: {engine_err}"]

        # 7) 종합 판정
        status = "PASS"
        exit_code = 0

        # static scan errors는 즉시 FAIL
        if static.errors:
            status = "FAIL"
            exit_code = 2

        # 지문 불일치/룩어헤드 실패/성과게이트 실패도 FAIL
        if not fp_ok or not look_ok or not perf_ok:
            status = "FAIL"
            exit_code = 2

        # strict 모드에서 warning도 실패
        if args.strict and static.warnings and exit_code == 0:
            status = "WARNING"
            exit_code = 1

        # 8) 결과 저장
        payload = {
            "generated_at": _now(),
            "status": status,
            "engine_path": str(engine_path),
            "engine_sha256": fp.engine_sha256,
            "params_path": str(params_path),
            "params_sha256": fp.params_sha256,
            "db_path": args.db,
            "patch": {"applied": patched, "notes": patch_notes},
            "guardrails": {
                "static_scan": {"pass": len(static.errors) == 0, "notes": static.errors + static.warnings + static.infos},
                "fingerprint_match": {"pass": fp_ok, "notes": fp_notes},
                "lookahead_tests": {"pass": look_ok, "notes": look_notes},
                "performance_gate": {"pass": perf_ok, "notes": perf_notes},
            },
            "data_fingerprint": fp.data_fingerprint,
            "performance": perf_extracted,
        }

        dump_json(run_dir / "guardrails_payload.json", payload)
        write_markdown_report(run_dir / "report.md", payload)

        print(f"[{status}] report: {run_dir / 'report.md'}")
        if exit_code != 0:
            print(f"[EXIT {exit_code}] 세부 내용: {run_dir / 'guardrails_payload.json'}")
        return exit_code

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
