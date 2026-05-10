#!/usr/bin/env python3
"""
engine_v65_c6_adapter.py — V65 C6_gpt_c5plus Engine Adapter
=============================================================
Wraps E2E3fix_combo_B40S60_floor22_engine.py to expose a v54-compatible API
so that ares_v55_live_autopilot.py can use it without code changes.

[STRUCTURAL_FIX 2026-04-09]
The C6 engine only exposes: _regime, compute_sleeves, load_db, run_single, run_wf.
The autopilot expects: step_shadow_strategy, select_top3_universes,
  blend_selected_targets, _enforce_exposure_floor_cap, execute_book.
These are implemented here by extracting the logic from run_single.

Champion: V65_C6_E2E3fix_B40S60_F22_20260403 (SR=6.233, CAGR=108.20%, MDD=-7.29%)
"""
import importlib.util
import os
import numpy as np
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

# ── Load the C6 engine ──
_C6_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "E2E3fix_combo_B40S60_floor22_engine.py")
_spec = importlib.util.spec_from_file_location("eng_c6", _C6_PATH)
_eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_eng)

# ── Re-export everything from C6 that the autopilot needs ──
RN = {0: "CRASH", 1: "BEAR", 2: "NORMAL", 3: "BULL", 4: "STRONG_BULL", 5: "INF_SHOCK"}

# ── C6 uses its own DEFAULT_PARAMS directly ──
DEFAULT_PARAMS = _eng.DEFAULT_PARAMS.copy()
# NOTE: ntb_base override removed (2026-04-09)
# Previously: DEFAULT_PARAMS["ntb_base"] = 0.025  # R3-06_NTB25 override
# Now uses tc6x_config.json value (0.021) via autopilot config loading

# ── Re-export engine internals ──
SMIX = _eng.SMIX
MAX_GROSS_EXPOSURE = _eng.MAX_GROSS_EXPOSURE  # 0.98 hardcoded in C6
RTIERS = _eng.RTIERS
compute_sleeves = _eng.compute_sleeves
LIVE_UNI = getattr(_eng, 'LIVE_UNI', [])

# Cache for autopilot invalidate_cache() compatibility
_FULL_CACHE = {}

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
    ewma_vol: float = 0.0
    ewma_var: float = 0.0
    warmup_done: bool = False

@dataclass
class BookState:
    pw: np.ndarray = field(default_factory=lambda: np.zeros(0))
    cash: float = 1.0
    equity_curve: list = field(default_factory=list)
    trade_log: list = field(default_factory=list)


# ── Core functions ──

def load_full_db(db_path: str, min_match: int = 20):
    """Load database using C6 engine's load_db with _FULL_CACHE management."""
    cache_key = f"full::{db_path}"
    result = _eng.load_db(db_path, min_match=min_match)
    _FULL_CACHE[cache_key] = result
    return result


def _regime(*args, **kwargs):
    """Regime detection — pass-through to C6 engine.
    Engine signature: _regime(R, t, vix=None)
    Autopilot calls: eng._regime(full_data['returns'], t, full_data.get('vix'))
    """
    return _eng._regime(*args, **kwargs)


def step_shadow_strategy(full_data, t, shadow_state, params=None, uname=None):
    """Single-step shadow strategy for one universe.

    [STRUCTURAL_FIX] This function extracts the per-step logic from
    run_single() to compute a target weight vector for one universe.

    Args:
        full_data: dict with 'prices', 'returns', 'high', 'low', 'open', 'volume', 'vix'
        t: current time index
        shadow_state: StrategyState for this universe
        params: strategy parameters dict
        uname: universe name (unused, for compatibility)
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    R = full_data["returns"]
    P = full_data["prices"]
    H = full_data.get("high", P)
    L = full_data.get("low", P)
    O = full_data.get("open", P)
    DV = full_data.get("volume", np.ones_like(P))
    vix = full_data.get("vix")
    N = R.shape[1] if R.ndim == 2 else len(R[0])

    # Initialize pw if needed
    if len(shadow_state.pw) != N:
        shadow_state.pw = np.zeros(N)

    reg = _eng._regime(R, t, vix)
    vx = float(vix[t]) if vix is not None and t < len(vix) else 20.0

    # Compute sleeves (signal scores)
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

    # Alpha transform
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

    # Confidence
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
            shadow_state.confidence = np.clip(np.mean(ics) * 10 + 0.5, 0.1, 1.0)

    shadow_state.last_target = comb.copy()
    shadow_state.prev_reg = reg


def select_top3_universes(shadow_states, params=None):
    """Select top 3 universes by confidence.
    Returns list of (universe_name, weight) tuples.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
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
    """Blend targets from selected universes.
    Returns (composite_target, avg_confidence).
    """
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
    """Enforce exposure floor and cap on target weights.
    Mirrors the logic from run_single.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    N = len(composite)
    pcap = p.get("per_cap", 0.08)
    # Effective per-cap adjustment for small N
    if N > 0 and 1.0 / N > pcap:
        pcap = min(1.0 / N * 1.5, 0.15)

    # Top-K filter by regime
    dtopk = {0: 12, 1: 14, 2: p.get("top_k", 20), 3: min(20, N), 4: min(20, N), 5: 12}
    topk = dtopk.get(reg, p.get("top_k", 20))

    w = composite.copy()
    # Zero out below top-K
    if N > topk:
        threshold = np.partition(w, -topk)[-topk] if topk > 0 else np.inf
        w[w < threshold] = 0.0

    # Apply per-cap
    w = np.clip(w, 0, pcap)

    # NTB (No-Trade-Band)
    ntb = p.get("ntb_base", 0.021)

    # Normalize to target exposure via RTIERS
    tiers = RTIERS
    target_exp = 0.0
    for lo, hi, wt in tiers:
        if lo <= topk:
            target_exp = wt
    if np.sum(w) > 1e-8:
        w *= target_exp / np.sum(w)

    # [E3-FIX] Always-on positions floor
    cur_pos = int(np.sum(w > 0.001))
    if cur_pos < 22:
        cands = np.argsort(-composite)
        added = 0
        cur_gross = float(np.sum(w))
        for ci in cands:
            if added >= (22 - cur_pos):
                break
            if w[ci] <= 0.001 and np.isfinite(composite[ci]) and composite[ci] > 0:
                w[ci] = max(0.005, cur_gross / 22 * 0.5)
                added += 1

    # Final cap
    final_gross = float(np.sum(w))
    if final_gross > MAX_GROSS_EXPOSURE:
        w *= MAX_GROSS_EXPOSURE / final_gross

    return w


def execute_book(book, target, reg, vx, avg_conf, params=None):
    """Execute book: apply target weights with turnover controls.
    Returns (new_pw, turnover, actual_cost, shadow_cost, n_trades).
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    pw = book.pw.copy()
    w = target.copy()
    N = len(w)

    if len(pw) != N:
        pw = np.zeros(N)
        book.pw = pw

    # NTB (No-Trade-Band)
    ntb = p.get("ntb_base", 0.021)
    for i in range(N):
        if abs(w[i] - pw[i]) < ntb:
            if not (pw[i] == 0 and w[i] > 0) and not (pw[i] > 0 and w[i] == 0):
                w[i] = pw[i]

    # A2B2 (regime-dependent clamp)
    if reg in {0, 1}:
        clamp = p.get("a2b2_base", 0.3) + (p.get("a2b2_max", 0.7) - p.get("a2b2_base", 0.3)) * avg_conf
        clamp = min(clamp, MAX_GROSS_EXPOSURE)
        gr = np.sum(w)
        if gr > clamp and gr > 0:
            w *= clamp / gr
        if np.sum(pw) > 0:
            bd = sum(max(0, w[i] - pw[i]) for i in range(N))
            if bd > p.get("a2b2_to", 0.15) and bd > 0:
                clip = p.get("a2b2_to", 0.15) / bd
                for i in range(N):
                    d_ = w[i] - pw[i]
                    if d_ > 0:
                        w[i] = pw[i] + d_ * clip

    # [E2-ROBUST] Asymmetric Turnover Cap
    prev_reg = getattr(book, '_prev_reg', 2)
    dw = w - pw
    buy_dw = np.where(dw > 0, dw, 0.0)
    sell_dw = np.where(dw < 0, dw, 0.0)
    buy_to = float(np.sum(buy_dw))
    sell_to = float(np.sum(np.abs(sell_dw)))
    buy_cap = 0.4 if reg == prev_reg else min(0.4 * 1.5, p.get("to_max", 0.8))
    sell_cap = 0.6 if reg == prev_reg else p.get("to_max", 0.8)
    if buy_to > buy_cap and buy_to > 1e-12:
        buy_dw *= buy_cap / buy_to
    if sell_to > sell_cap and sell_to > 1e-12:
        sell_dw *= sell_cap / sell_to
    w = pw + buy_dw + sell_dw

    pcap = p.get("per_cap", 0.08)
    w = np.clip(w, 0, pcap)

    # [HOTFIX-1] Final gross cap
    final_gross = float(np.sum(w))
    if final_gross > MAX_GROSS_EXPOSURE:
        w *= MAX_GROSS_EXPOSURE / final_gross

    turnover = float(np.sum(np.abs(w - pw)))
    cost = turnover * p.get("cost_bps", 5) / 10000
    shadow_cost = cost * 0.5  # shadow cost estimate

    n_trades = int(np.sum(np.abs(w - pw) > 0.001))

    book.pw = w
    book._prev_reg = reg
    book.trade_log.append({"turnover": turnover, "cost": cost, "n_trades": n_trades})

    return w, turnover, cost, shadow_cost, n_trades


# ── Backtest wrapper ──
def run_single(data, params=None, verbose=False):
    """Full backtest — delegate to C6 engine."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    return _eng.run_single(data, p, verbose=verbose)

def run_wf(data, n_folds=10, params=None, verbose=False):
    """Walk-forward — delegate to C6 engine."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    return _eng.run_wf(data, n_folds=n_folds, params=p, verbose=verbose)


# ── Version info ──
__version__ = "V65_C6_E2E3fix_B40S60_F22_20260403"
__champion__ = "V65_C6_E2E3fix_B40S60_F22_20260403"
__adapter_for__ = "ares_v55_live_autopilot.py"
LIVE_TRUTH_STRATEGY = "E2E3fix_combo_B40S60_floor22"
LIVE_TRUTH_ENGINE_FILE = "E2E3fix_combo_B40S60_floor22_engine.py"
