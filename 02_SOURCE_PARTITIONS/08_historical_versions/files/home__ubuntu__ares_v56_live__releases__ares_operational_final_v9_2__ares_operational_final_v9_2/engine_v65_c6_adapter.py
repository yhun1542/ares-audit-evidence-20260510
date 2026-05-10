#!/usr/bin/env python3
"""
engine_v65_c6_adapter_prod.py

목적
- ares_v55_live_autopilot.py가 사용하는 어댑터를 챔피언 엔진 계약과 정렬한다.
- 수정 범위는 딱 필요한 것만 유지한다.
  1) confidence 공식 엔진 정렬
  2) crash guard → VIX-dynamic NTB → E3-floor → A2B2 → symmetric turnover 순서 정렬
  3) dual-book를 고려한 단일 step 호환
  4) 하드캡 0.98 유지

배제한 것
- 텔레그램 호출
- 중복 circuit breaker
- 과도한 운영 텔레메트리
"""
import importlib.util
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional
import numpy as np

_log = logging.getLogger("adapter_prod")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [ADAPTER-PROD] %(levelname)s %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)

_C6_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "E2E3fix_combo_B40S60_floor22_engine.py")
_spec = importlib.util.spec_from_file_location("eng_c6", _C6_PATH)
_eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_eng)

RN = {0: "CRASH", 1: "BEAR", 2: "NORMAL", 3: "BULL", 4: "STRONG_BULL", 5: "INF_SHOCK"}
DEFAULT_PARAMS = _eng.DEFAULT_PARAMS.copy()
SMIX = _eng.SMIX
MAX_GROSS_EXPOSURE = _eng.MAX_GROSS_EXPOSURE
RTIERS = _eng.RTIERS
compute_sleeves = _eng.compute_sleeves
LIVE_UNI = getattr(_eng, 'LIVE_UNI', [])
_FULL_CACHE = {}

_ENABLE_TELEMETRY = str(os.environ.get("ARES_ADAPTER_TELEMETRY", "0")).lower() in {"1", "true", "on"}
_TELEMETRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter_telemetry.jsonl")
_last_valid_vix = 20.0


def _safe_vix(vx):
    global _last_valid_vix
    try:
        v = float(vx)
        if np.isfinite(v) and 0.0 <= v <= 100.0:
            _last_valid_vix = v
            return v
    except Exception:
        pass
    return _last_valid_vix


def _emit(event: str, payload: dict):
    if not _ENABLE_TELEMETRY:
        return
    try:
        row = {"event": event, **payload}
        with open(_TELEMETRY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception:
        pass


@dataclass
class StrategyState:
    pw: np.ndarray = field(default_factory=lambda: np.zeros(0))
    prev_reg: int = 2
    confidence: float = 0.5
    sig_hist: list = field(default_factory=list)
    ret_hist: list = field(default_factory=list)
    daily_returns: list = field(default_factory=list)
    last_selected: list = field(default_factory=list)
    last_target: Optional[np.ndarray] = None
    ewma_vol: float = 0.0
    ewma_var: float = 0.0
    warmup_done: bool = False


@dataclass
class BookState:
    pw: np.ndarray = field(default_factory=lambda: np.zeros(0))
    cash: float = 1.0
    equity_curve: list = field(default_factory=list)
    trade_log: list = field(default_factory=list)
    prev_reg: int = 2


def load_full_db(db_path: str, min_match: int = 20):
    cache_key = f"full::{db_path}"
    result = _eng.load_db(db_path, min_match=min_match)
    _FULL_CACHE[cache_key] = result
    return result


def _regime(*args, **kwargs):
    return _eng._regime(*args, **kwargs)


def step_shadow_strategy(full_data, t, shadow_state, params=None, uname=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    R = full_data["returns"]
    P = full_data["prices"]
    H = full_data.get("high", P)
    L = full_data.get("low", P)
    O = full_data.get("open", P)
    DV = full_data.get("volume", np.ones_like(P))
    vix = full_data.get("vix")
    N = R.shape[1] if R.ndim == 2 else len(R[0])

    if len(shadow_state.pw) != N:
        shadow_state.pw = np.zeros(N)

    reg = _eng._regime(R, t, vix)
    vx = _safe_vix(vix[t] if vix is not None and t < len(vix) else None)

    scores = _eng.compute_sleeves(P, R, H, L, O, DV, t, N, reg)
    mix = SMIX.get(reg, SMIX[2])
    comb = np.zeros(N)
    tw = 0.0
    for sn, sw in mix.items():
        if sn == "CASH" or sw < 0.001 or sn not in scores:
            continue
        s = scores[sn]
        if s is None or np.all(np.isnan(s)):
            continue
        v = np.isfinite(s)
        if v.sum() < 5:
            continue
        m, sd = np.nanmean(s[v]), np.nanstd(s[v])
        if sd < 1e-8:
            continue
        comb += sw * np.where(v, (s - m) / (sd + 1e-8), 0.0)
        tw += sw

    if tw < 0.01:
        shadow_state.last_target = shadow_state.pw.copy()
        return

    comb /= tw
    rv20 = np.nanstd(np.nanmean(R[max(0, t - 20):t], axis=1)) if t >= 20 else 0.015
    ms = 0
    if vx > 30:
        ms += 2
    elif vx > 20:
        ms += 1
    if rv20 > 0.03:
        ms += 2
    elif rv20 > 0.02:
        ms += 1
    comb *= [1, 0.96, 0.96, 0.90, 0.90, 0.82, 0.82][min(ms, 6)]

    if t >= 1:
        shadow_state.ret_hist.append(R[t - 1].copy() if R.ndim == 2 else np.array(R[t - 1]))
        shadow_state.sig_hist.append(comb.copy())
    if len(shadow_state.sig_hist) >= 10:
        ics = []
        sh = shadow_state.sig_hist
        rh = shadow_state.ret_hist
        for i in range(min(15, len(sh) - 1)):
            sg = sh[-(i + 2)]
            rt = rh[-(i + 1)]
            vl = np.isfinite(sg) & np.isfinite(rt)
            if vl.sum() >= 10:
                ic = np.corrcoef(sg[vl], rt[vl])[0, 1]
                if np.isfinite(ic):
                    ics.append(ic)
        if ics:
            shadow_state.confidence = np.clip(np.mean(ics) / 0.05, 0, 1) * 0.5 + 0.25

    shadow_state.last_target = comb.copy()
    shadow_state.prev_reg = reg


def select_top3_universes(shadow_states, params=None):
    scored = []
    for uname, state in shadow_states.items():
        if state.last_target is not None:
            scored.append((uname, state.confidence))
    scored.sort(key=lambda x: -x[1])
    top3 = scored[:3]
    if not top3:
        return []
    total_conf = sum(c for _, c in top3)
    if total_conf < 1e-8:
        return [(n, 1.0 / len(top3)) for n, _ in top3]
    return [(n, c / total_conf) for n, c in top3]


def blend_selected_targets(shadow_states, selected, n_assets):
    composite = np.zeros(n_assets)
    total_weight = 0.0
    total_conf = 0.0
    for uname, weight in selected:
        if uname in shadow_states and shadow_states[uname].last_target is not None:
            tgt = shadow_states[uname].last_target
            if len(tgt) == n_assets:
                composite += weight * tgt
                total_weight += weight
                total_conf += weight * shadow_states[uname].confidence
    if total_weight > 1e-8:
        composite /= total_weight
        avg_conf = total_conf / total_weight
    else:
        avg_conf = 0.5
    return composite, avg_conf


def _enforce_exposure_floor_cap(composite, reg, params=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    N = len(composite)
    pcap = float(np.clip(
        min(p.get("per_cap", 0.06), MAX_GROSS_EXPOSURE / max(p.get("top_k", 20), 1) * 1.2),
        0.02, 0.10,
    ))
    dtopk = {0: 12, 1: 14, 2: p.get("top_k", 20), 3: min(20, N), 4: min(20, N), 5: 12}
    topk = dtopk.get(reg, p.get("top_k", 20))
    net = composite.copy()
    k = min(topk, int(np.isfinite(net).sum()))
    if k < 3:
        return np.zeros(N)
    top = list(np.argsort(-net)[:k])
    tgt_exp = 0.0
    for lo, hi, wt in RTIERS:
        if lo <= k:
            tgt_exp = wt
    tgt_exp = min(tgt_exp, MAX_GROSS_EXPOSURE)
    w = np.zeros(N)
    rw = np.ones(len(top))
    for ri in range(len(top)):
        for lo, hi, wt in RTIERS:
            if lo <= ri < hi:
                rw[ri] = wt
                break
    rw_sum = rw.sum()
    if rw_sum > 0:
        rw /= rw_sum
    for ri, ai in enumerate(top):
        w[ai] = min(tgt_exp * rw[ri], pcap)
    w = np.clip(w, 0, pcap)
    ct = np.sum(w)
    if ct > 1e-8:
        w *= min(tgt_exp / ct, 2.0)
    w = np.minimum(w, pcap)
    eli = np.array(top, dtype=int)
    for _ in range(10):
        gap = tgt_exp - np.sum(w)
        if gap <= 1e-6:
            break
        room = np.maximum(pcap - w[eli], 0.0)
        av = room > 1e-6
        if not np.any(av):
            break
        fi = eli[av]
        fr = room[av]
        rs = np.sum(fr)
        if rs <= 1e-6:
            break
        w[fi] += np.minimum(fr, gap * fr / rs)
        w = np.minimum(w, pcap)
    gross = float(np.sum(w))
    if gross > MAX_GROSS_EXPOSURE:
        w *= MAX_GROSS_EXPOSURE / gross
    return w


def execute_book(book, target, reg, vx, avg_conf, params=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    pw = book.pw.copy()
    w = target.copy()
    N = len(w)
    if len(pw) != N:
        pw = np.zeros(N)
        book.pw = pw
    vx = _safe_vix(vx)
    pcap = float(np.clip(
        min(p.get("per_cap", 0.06), MAX_GROSS_EXPOSURE / max(p.get("top_k", 20), 1) * 1.2),
        0.02, 0.10,
    ))

    crash_triggered = False
    if vx >= p.get("crash_vix", 30.0):
        w *= 0.60
        crash_triggered = True

    ntb = p.get("ntb_base", 0.025)
    band = ntb * (1.3 if vx > 30 else 1.1 if vx > 20 else 1.0)
    ntb_suppressed = 0
    if np.sum(np.abs(pw)) > 1e-8:
        for i in range(N):
            if abs(w[i] - pw[i]) < band:
                if not (pw[i] < 0.001 and w[i] > 0.001) and not (pw[i] > 0.001 and w[i] < 0.001):
                    w[i] = pw[i]
                    ntb_suppressed += 1

    cur_pos = int(np.sum(w > 0.001))
    e3_added = 0
    if cur_pos < 22:
        cands = np.argsort(-target)
        cur_gross = float(np.sum(w))
        for ci in cands:
            if e3_added >= (22 - cur_pos):
                break
            if w[ci] <= 0.001 and np.isfinite(target[ci]) and target[ci] > 0:
                w[ci] = max(0.005, cur_gross / 22 * 0.5)
                e3_added += 1

    a2b2_clamped = False
    if reg in {0, 1}:
        clamp = p.get("a2b2_base", 0.55) + (p.get("a2b2_max", 0.85) - p.get("a2b2_base", 0.55)) * avg_conf
        clamp = min(clamp, MAX_GROSS_EXPOSURE)
        gr = np.sum(w)
        if gr > clamp and gr > 0:
            w *= clamp / gr
            a2b2_clamped = True
        if np.sum(np.abs(pw)) > 0:
            bd = sum(max(0, w[i] - pw[i]) for i in range(N))
            if bd > p.get("a2b2_to", 0.08) and bd > 0:
                clip = p.get("a2b2_to", 0.08) / bd
                for i in range(N):
                    d_ = w[i] - pw[i]
                    if d_ > 0:
                        w[i] = pw[i] + d_ * clip

    dw = w - pw
    total_to = float(np.sum(np.abs(dw)))
    to_cap = p.get("to_base", 0.259)
    regime_changed = (reg != getattr(book, 'prev_reg', 2))
    if regime_changed:
        to_cap = p.get("to_max", 0.60)
    to_clipped = False
    if total_to > to_cap and total_to > 1e-12:
        dw *= to_cap / total_to
        w = pw + dw
        to_clipped = True

    w = np.clip(w, 0, pcap)
    final_gross = float(np.sum(w))
    hard_cap_hit = False
    if final_gross > MAX_GROSS_EXPOSURE:
        w *= MAX_GROSS_EXPOSURE / final_gross
        hard_cap_hit = True

    turnover = float(np.sum(np.abs(w - pw)))
    cost = turnover * p.get("cost_bps", 13) / 10000
    shadow_cost = cost * 0.5
    n_trades = int(np.sum(np.abs(w - pw) > 0.001))
    book.pw = w
    book.prev_reg = reg
    book.trade_log.append({"turnover": turnover, "cost": cost, "n_trades": n_trades})
    _emit("execute_book", {
        "reg": reg,
        "vx": round(vx, 2),
        "avg_conf": round(float(avg_conf), 4),
        "turnover": round(turnover, 6),
        "n_trades": n_trades,
        "crash_guard": crash_triggered,
        "ntb_suppressed": ntb_suppressed,
        "e3_added": e3_added,
        "a2b2_clamped": a2b2_clamped,
        "to_clipped": to_clipped,
        "hard_cap_hit": hard_cap_hit,
    })
    return w, turnover, cost, shadow_cost, n_trades


def run_single(data, params=None, verbose=False):
    p = {**DEFAULT_PARAMS, **(params or {})}
    return _eng.run_single(data, p, verbose=verbose)


def run_wf(data, n_folds=10, params=None, verbose=False):
    p = {**DEFAULT_PARAMS, **(params or {})}
    return _eng.run_wf(data, n_folds=n_folds, params=p, verbose=verbose)


def get_health_report():
    return {
        "adapter_version": __version__,
        "telemetry_enabled": _ENABLE_TELEMETRY,
        "last_valid_vix": _last_valid_vix,
    }


__version__ = "V65_C6_E2E3fix_B40S60_F22_20260413_PROD_INTEGRATED_v2"
__champion__ = "V65_C6_E2E3fix_B40S60_F22_20260403"
__adapter_for__ = "ares_v55_live_autopilot.py"
LIVE_TRUTH_STRATEGY = "E2E3fix_combo_B40S60_floor22"
LIVE_TRUTH_ENGINE_FILE = "E2E3fix_combo_B40S60_floor22_engine.py"
