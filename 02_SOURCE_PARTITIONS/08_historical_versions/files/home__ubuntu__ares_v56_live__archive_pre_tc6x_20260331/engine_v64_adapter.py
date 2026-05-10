#!/usr/bin/env python3
"""
engine_v64_adapter.py — V64 PROD Engine Adapter
=================================================
Wraps engine_v53_hotfix.py to expose a v54-compatible API so that
ares_v55_live_autopilot.py can use it without any code changes.

The autopilot calls:
  1. load_full_db(db_path, min_match) → dict
  2. StrategyState, BookState classes
  3. RN dict, _regime(), step_shadow_strategy(), select_top3_universes(),
     blend_selected_targets(), _enforce_exposure_floor_cap(), execute_book()

This adapter:
  - Loads v53_hotfix engine internally
  - Provides all v54-compatible API functions
  - The actual V64 alpha computation happens in the autopilot's
    replay_to_latest() which calls step_shadow_strategy → blend → execute_book
  - We override execute_book to inject V64 EWMA+softmax logic

Champion: V64_PROD (LAM=0.85, TAU=0.35, TV=0.11)
Config Hash: 57120205e67096fb
"""
import importlib.util
import os
import numpy as np
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

# ── Load the real v53_hotfix engine ──
_V53_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine_v53_hotfix.py")
_spec = importlib.util.spec_from_file_location("eng_v53", _V53_PATH)
_eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_eng)

# ── Re-export everything from v53 that the autopilot needs ──
RN = {0: "CRASH", 1: "BEAR", 2: "NORMAL", 3: "BULL", 4: "STRONG_BULL", 5: "INF_SHOCK"}

# ── V64 PROD parameters ──
V64_PROD_PARAMS = {
    "rebal": 2,
    "top_k": 12,
    "target_vol": 0.11,
    "cost_bps": 13.0,
    "core_only": True,
    "topen_030": True,
    "topen_thresh": 0.03,
    "ewma_vol_target": True,
    "ewma_lam": 0.85,
    "softmax_weighting": True,
    "softmax_tau": 0.35,
}

# ── Merge with engine defaults ──
DEFAULT_PARAMS = {**_eng.DEFAULT_PARAMS, **V64_PROD_PARAMS}

# ── Re-export engine internals that the autopilot accesses ──
SMIX = _eng.SMIX
MAX_GROSS_EXPOSURE = _eng.MAX_GROSS_EXPOSURE
RTIERS = _eng.RTIERS
compute_sleeves = _eng.compute_sleeves
LIVE_UNI = getattr(_eng, 'LIVE_UNI', [])

# ── Compatibility classes (match v54 interface) ──
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

@dataclass
class BookState:
    pw: np.ndarray = field(default_factory=lambda: np.zeros(0))
    prev_reg: int = 2

# ── Cache ──
_FULL_CACHE: Dict[str, Any] = {}

def load_full_db(db_path: str, min_match: int = 20) -> dict:
    """Load database in v54-compatible format."""
    cache_key = f"{db_path}:{min_match}"
    if cache_key in _FULL_CACHE:
        return _FULL_CACHE[cache_key]

    data = _eng.load_db(db_path, universe_filter=True, min_match=min_match)

    # v54 expects 'prices_df' for fallback prices
    import pandas as pd
    dates = data['dates']
    symbols = data['symbols']
    prices = data['prices']  # v53 load_db returns prices

    prices_df = pd.DataFrame(prices, index=dates, columns=symbols)

    result = {
        'dates': dates,
        'returns': data['returns'],
        'prices': data['prices'],
        'high': data['high'],
        'low': data['low'],
        'open': data['open'],
        'dvol': data['dvol'],
        'symbols': symbols,
        'n_assets': data['n_assets'],
        'prices_df': prices_df,
        'vix': data.get('vix'),
    }
    _FULL_CACHE[cache_key] = result
    return result


def _regime(R, t, vix=None):
    """Regime detection — delegate to v53 engine."""
    return _eng._regime(R, t, vix)


# ── V64 SMIX override: core_only filtering ──
def _get_v64_smix(params):
    """Build SMIX with core_only filtering applied."""
    core_only = params.get("core_only", True)
    smix_local = {}
    for reg_key, mix in SMIX.items():
        m = dict(mix)
        if core_only:
            for sn in list(m.keys()):
                if sn not in ("TREND", "DLV", "MRC", "CA", "CASH"):
                    m[sn] = 0.0
            non_cash = sum(v for k, v in m.items() if k != "CASH")
            if non_cash > 0.01:
                cash_w = m.get("CASH", 0)
                scale = (1.0 - cash_w) / non_cash if cash_w < 1.0 else 1.0
                for k in m:
                    if k != "CASH":
                        m[k] *= scale
        smix_local[reg_key] = m
    return smix_local


# ── V64 State (held at module level for replay) ──
class V64State:
    """Holds all V64-specific state across replay steps."""
    def __init__(self, n_assets):
        self.N = n_assets
        self.pw = np.zeros(n_assets)
        self.prev_reg = 2
        self.confidence = 0.5
        self.sig_hist = deque(maxlen=40)
        self.ret_hist = deque(maxlen=40)
        self.mkt_var_ewm = 0.0001
        self.smix_local = None
        self.initialized = False
        self.last_t = 251  # will start from 252

_v64_state = None


def _ensure_v64_state(n_assets, params):
    global _v64_state
    if _v64_state is None or _v64_state.N != n_assets:
        _v64_state = V64State(n_assets)
    if _v64_state.smix_local is None:
        _v64_state.smix_local = _get_v64_smix(params)
    return _v64_state


def step_shadow_strategy(full_data, t, state, params, universe_name):
    """
    V64 step: compute alpha signal at time t.
    This replaces v54's multi-universe shadow strategy with V64's
    single-pass EWMA+softmax logic from run_single_patched.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    R = full_data['returns']
    P = full_data['prices']
    H = full_data['high']
    L = full_data['low']
    O = full_data['open']
    DV = full_data['dvol']
    vix = full_data.get('vix')
    N = R.shape[1]

    v64 = _ensure_v64_state(N, p)

    reg = _regime(R, t, vix)
    vx = float(vix[t]) if vix is not None and t < len(vix) else 20.0

    topen_030 = p.get("topen_030", True)
    topen_thresh = p.get("topen_thresh", 0.03)
    ewma_vol_target = p.get("ewma_vol_target", True)
    ewma_lam = p.get("ewma_lam", 0.85)
    softmax_weighting = p.get("softmax_weighting", True)
    softmax_tau = p.get("softmax_tau", 0.35)

    pcap = min(p["per_cap"], MAX_GROSS_EXPOSURE / max(p["top_k"], 1) * 1.5)
    dtopk = {0: 12, 1: 14, 2: p["top_k"], 3: min(20, N), 4: min(20, N), 5: 12}

    # NOTE: The autopilot's replay_to_latest already gates calls via signal_rebal.
    # Every call here should compute new weights (no internal rebal check).
    if True:  # Always compute on call
        # Compute sleeves
        scores = compute_sleeves(P, R, H, L, O, DV, t, N, reg)

        if topen_030:
            mkt_vol = np.nanstd(np.nanmean(R[max(0, t-20):t], axis=1)) if t >= 20 else 0.02
            if mkt_vol <= topen_thresh and t >= 63:
                scores['TF'] = (P[t-21] / (P[t-63] + 1e-12) - 1)

        mix = v64.smix_local.get(reg, v64.smix_local.get(2, SMIX[2]))
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
            m_val, sd = np.nanmean(s[v]), np.nanstd(s[v])
            if sd < 1e-8:
                continue
            comb += sw * np.where(v, (s - m_val) / (sd + 1e-8), 0.0)
            tw += sw

        if tw < 0.01:
            # No valid signals, keep current weights
            state.last_target = v64.pw.copy()
            return

        comb /= tw

        # Market stress dampening
        rv20 = np.nanstd(np.nanmean(R[max(0, t-20):t], axis=1)) if t >= 20 else 0.015
        ms = 0
        if vx > 30: ms += 2
        elif vx > 20: ms += 1
        if rv20 > 0.03: ms += 2
        elif rv20 > 0.02: ms += 1
        comb *= [1, 0.96, 0.96, 0.90, 0.90, 0.82, 0.82][min(ms, 6)]

        # IC tracking
        if t >= 1:
            v64.ret_hist.append(R[t-1].copy())
            v64.sig_hist.append(comb.copy())
        if len(v64.sig_hist) >= 10:
            ics = []
            for i in range(min(15, len(v64.sig_hist) - 1)):
                sg = v64.sig_hist[-(i+2)]
                rt = v64.ret_hist[-(i+1)]
                vl = np.isfinite(sg) & np.isfinite(rt)
                if vl.sum() >= 10:
                    ic = np.corrcoef(sg[vl], rt[vl])[0, 1]
                    if np.isfinite(ic):
                        ics.append(ic)
            v64.confidence = np.clip(np.mean(ics) / 0.05 if ics else 0, 0, 1) * 0.5 + 0.25

        # Exposure target
        ab = p["ace_base"]
        am = p["ace_max"]
        af = p["ace_floor"]
        b = ab.get(reg, 0.85)
        mx = am.get(reg, 0.95)
        fl = af.get(reg, 0.65)
        tgt = b + (mx - b) * v64.confidence
        tv = p["target_vol"]

        if ewma_vol_target:
            mkt_r = float(np.nanmean(R[t]))
            v64.mkt_var_ewm = ewma_lam * v64.mkt_var_ewm + (1 - ewma_lam) * mkt_r**2
            rv_ewm = float(np.sqrt(max(v64.mkt_var_ewm, 1e-12) * 252.0))
            if rv_ewm > 0.001:
                tgt = np.clip(tgt * np.clip(tv / rv_ewm, p["vol_clamp_low"], p["vol_clamp_high"]), fl, mx)
        else:
            if t >= 30:
                pr = np.nanmean(R[max(0, t-30):t], axis=1)
                rv = np.nanstd(pr) * np.sqrt(252) if len(pr) > 2 else 0.15
                if rv > 0.001:
                    tgt = np.clip(tgt * np.clip(tv / rv, p["vol_clamp_low"], p["vol_clamp_high"]), fl, mx)

        tgt = min(tgt, MAX_GROSS_EXPOSURE)

        # Net score with incumbent bonus
        net = comb.copy()
        inc = v64.pw > 0.001
        net[~inc] -= p["switch_pen"] / 10000
        net[inc] += p["inc_bonus"] / 10000

        # Top-K selection
        k = min(dtopk.get(reg, 16), int(np.isfinite(net).sum()))
        if k < 3:
            state.last_target = v64.pw.copy()
            return

        top = list(np.argsort(-net)[:k])

        # Softmax weighting (V64 key feature)
        if softmax_weighting:
            eli = np.array(top, dtype=int)
            s = net[eli].copy()
            s = s - np.nanmax(s)
            raw = np.exp(s / max(softmax_tau, 1e-6))
            raw = raw / (raw.sum() + 1e-12)
            w = np.zeros(N)
            for j, ai in enumerate(eli):
                w[ai] = min(tgt * raw[j], pcap)
        else:
            w = np.zeros(N)
            rw = np.ones(len(top))
            for ri in range(len(top)):
                for lo, hi, wt in RTIERS:
                    if lo <= ri < hi:
                        rw[ri] = wt
                        break
            rw /= rw.sum()
            for ri, ai in enumerate(top):
                w[ai] = min(tgt * rw[ri], pcap)

        # Clip and redistribute
        w = np.clip(w, 0, pcap)
        ct = np.sum(w)
        if ct > 1e-8:
            w *= min(tgt / ct, 2.0)
        w = np.minimum(w, pcap)
        eli = np.array(top, dtype=int)
        for _ in range(10):
            gap = tgt - np.sum(w)
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

        w = np.maximum(w, 0.0)
        gross = float(np.sum(w))
        if gross > MAX_GROSS_EXPOSURE:
            w *= MAX_GROSS_EXPOSURE / gross

        # Crash VIX override
        if vx >= p["crash_vix"]:
            w *= 0.60

        # No-trade band
        if np.sum(v64.pw) > 1e-8:
            band = p["ntb_base"] * (1.3 if vx > 30 else 1.1 if vx > 20 else 1.0)
            for i in range(N):
                if abs(w[i] - v64.pw[i]) < band:
                    if not (v64.pw[i] == 0 and w[i] > 0) and not (v64.pw[i] > 0 and w[i] == 0):
                        w[i] = v64.pw[i]

        # Bear/crash clamp
        if reg in {0, 1}:
            clamp = p["a2b2_base"] + (p["a2b2_max"] - p["a2b2_base"]) * v64.confidence
            clamp = min(clamp, MAX_GROSS_EXPOSURE)
            gr = np.sum(w)
            if gr > clamp and gr > 0:
                w *= clamp / gr
            if np.sum(v64.pw) > 0:
                bd = sum(max(0, w[i] - v64.pw[i]) for i in range(N))
                if bd > p["a2b2_to"] and bd > 0:
                    clip = p["a2b2_to"] / bd
                    for i in range(N):
                        d_ = w[i] - v64.pw[i]
                        if d_ > 0:
                            w[i] = v64.pw[i] + d_ * clip

        # Turnover cap
        to_cap = p["to_base"] if reg == v64.prev_reg else p["to_max"]
        t_ = np.sum(np.abs(w - v64.pw))
        if t_ > to_cap:
            w = v64.pw + (w - v64.pw) * (to_cap / t_)
        w = np.clip(w, 0, pcap)

        # Final gross cap
        final_gross = float(np.sum(w))
        if final_gross > 1.0:
            w *= 1.0 / final_gross

        v64.pw = w.copy()
        v64.prev_reg = reg

    # Store result in state for the autopilot to read (always, even if no rebal)
    state.pw = v64.pw.copy()
    state.prev_reg = v64.prev_reg
    state.confidence = v64.confidence
    state.last_target = v64.pw.copy()


def select_top3_universes(shadow_states, params):
    """V64 uses single universe."""
    return ["V64_PROD"]


def blend_selected_targets(shadow_states, selected, n_assets):
    """V64: return the target from the single V64_PROD universe."""
    for name, st in shadow_states.items():
        if hasattr(st, 'last_target') and st.last_target is not None:
            return st.last_target.copy(), float(getattr(st, 'confidence', 0.8))
    return np.zeros(n_assets), 0.5


def _enforce_exposure_floor_cap(w, reg, params):
    """Pass through — exposure management is done in step_shadow_strategy."""
    return w


def execute_book(book, target, reg, vx, confidence, params):
    """Execute: update book weights to target, compute turnover and cost."""
    old_pw = book.pw.copy() if len(book.pw) > 0 else np.zeros_like(target)
    turnover = float(np.sum(np.abs(target - old_pw)))
    cost_bps = params.get("cost_bps", 13.0)
    actual_cost = turnover * cost_bps / 10000
    shadow_cost = turnover * (cost_bps + params.get("shadow_slippage_bps", 5.0) +
                              params.get("shadow_impact_coeff_bps", 8.0) * turnover) / 10000
    book.pw = target.copy()
    book.prev_reg = reg
    return target, turnover, actual_cost, shadow_cost, 0.0
