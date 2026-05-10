"""
metric_schema.py — 통합 Metric Schema v3.0
============================================
감사 보고서(V232_R3 REJECT) + GPT 피드백 + Claude v2.1 통합.

15 base fields + 16 sensitivity fields + 4 diagnostics = 35 total.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from statistics import mean, pstdev
import json, math, numpy as np


class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        return super().default(obj)


@dataclass
class NormalizedResult:
    manifest_id: str = ""
    family: str = ""
    config_summary: str = ""

    # ── 15 Base Fields (공통) ──
    ann_return: Optional[float] = None
    ann_vol: Optional[float] = None
    sharpe: Optional[float] = None
    max_dd: Optional[float] = None
    calmar: Optional[float] = None
    sortino: Optional[float] = None
    win_rate: Optional[float] = None
    avg_turnover: Optional[float] = None
    avg_exposure: Optional[float] = None
    avg_cost_bps_equity: Optional[float] = None
    fold19_sharpe: Optional[float] = None
    fold19_mdd: Optional[float] = None
    avg_fill_rate: Optional[float] = None
    reject_rate: Optional[float] = None
    avg_slippage_bps: Optional[float] = None

    # ── Cost Sensitivity (감사 결함 #1) ──
    sharpe_15bps: Optional[float] = None
    sharpe_25bps: Optional[float] = None
    sharpe_30bps: Optional[float] = None
    breakeven_tc_bps: Optional[float] = None

    # ── Rebal Sensitivity (감사 결함 #2) ──
    sharpe_rebal_base: Optional[float] = None
    sharpe_rebal_2x: Optional[float] = None
    rebal_decay_ratio: Optional[float] = None

    # ── Target Vol Elasticity (감사 결함: dead knob) ──
    target_vol_elasticity: Optional[float] = None

    # ── Diagnostics ──
    cost_filter_pass: Optional[bool] = None
    rebal_filter_pass: Optional[bool] = None
    fold19_filter_pass: Optional[bool] = None
    knob_alive: Optional[bool] = None

    # ── Scoring ──
    score: float = -9999.0
    hard_fail: bool = False
    hard_fail_reason: str = ""

    # ── Config (재현용) ──
    full_config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self): return asdict(self)
    def to_json(self): return json.dumps(asdict(self), cls=NpEncoder, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# HARD FILTERS — 감사 보고서 + GPT 피드백 통합
# ═══════════════════════════════════════════════════════════════

def apply_hard_filters(r: NormalizedResult) -> NormalizedResult:
    """
    즉시 탈락 조건 (7개 규칙):

    [기존]
    1. fold19_sharpe < -0.2
    2. max_dd < -12%
    3. avg_exposure > 70% AND avg_turnover > 60x

    [감사 #1: 비용 강건성]
    4. breakeven_tc_bps < 25
    5. sharpe_25bps <= 0
    6. sharpe_30bps < 0 (있으면)

    [감사 #2: 리밸런싱 과적합]
    7. rebal_decay_ratio < 0.65

    [감사 #3: dead knob]
    8. target_vol_elasticity < 0.05

    [턴오버 머신]
    9. avg_turnover > 45x
    """
    def _fail(reason):
        r.hard_fail = True
        r.hard_fail_reason = reason
        r.score = -9999.0
        return r

    # 1. Fold 19
    if r.fold19_sharpe is not None and r.fold19_sharpe < -0.2:
        return _fail("fold19_sharpe < -0.2")
    r.fold19_filter_pass = True

    # 2. Max DD
    if r.max_dd is not None and r.max_dd < -12.0:
        return _fail("max_dd < -12%")

    # 3. Exposure + Turnover combo
    if (r.avg_exposure is not None and r.avg_turnover is not None
            and r.avg_exposure > 0.70 and r.avg_turnover > 60):
        return _fail("exposure>70% & turnover>60x")

    # 4. Breakeven TC (감사 #1)
    if r.breakeven_tc_bps is not None and r.breakeven_tc_bps < 25:
        r.cost_filter_pass = False
        return _fail(f"breakeven_tc_bps={r.breakeven_tc_bps:.0f} < 25")
    r.cost_filter_pass = True

    # 5. SR at 25bps (감사 #1)
    if r.sharpe_25bps is not None and r.sharpe_25bps <= 0:
        r.cost_filter_pass = False
        return _fail(f"sharpe_25bps={r.sharpe_25bps:.3f} <= 0")

    # 6. SR at 30bps (감사 #1)
    if r.sharpe_30bps is not None and r.sharpe_30bps < 0:
        r.cost_filter_pass = False
        return _fail(f"sharpe_30bps={r.sharpe_30bps:.3f} < 0")

    # 7. Rebal decay (감사 #2)
    if r.rebal_decay_ratio is not None and r.rebal_decay_ratio < 0.65:
        r.rebal_filter_pass = False
        return _fail(f"rebal_decay={r.rebal_decay_ratio:.2f} < 0.65")
    r.rebal_filter_pass = True

    # 8. Dead knob (감사: target_vol)
    if r.target_vol_elasticity is not None and r.target_vol_elasticity < 0.05:
        r.knob_alive = False
        return _fail(f"target_vol_elasticity={r.target_vol_elasticity:.3f} < 0.05")
    r.knob_alive = True

    # 9. Turnover machine
    if r.avg_turnover is not None and r.avg_turnover > 45:
        return _fail("turnover>45x (turnover machine)")

    return r


# ═══════════════════════════════════════════════════════════════
# SCORING — 턴오버 페널티 강화 + 비용 강건성 보너스
# ═══════════════════════════════════════════════════════════════

def _zscore(v, arr):
    if len(arr) < 2: return 0.0
    mu = mean(arr); sd = pstdev(arr)
    return (v - mu) / sd if sd > 0 else 0.0


def score_population(results: List[NormalizedResult]) -> List[NormalizedResult]:
    for r in results:
        apply_hard_filters(r)

    valid = [r for r in results if not r.hard_fail]
    if len(valid) < 3:
        return results

    def sl(f): return [getattr(r, f) for r in valid if getattr(r, f) is not None]

    s_arr = sl("sharpe"); c_arr = sl("ann_return"); cal_arr = sl("calmar")
    sor_arr = sl("sortino"); mdd_arr = [abs(r.max_dd) for r in valid if r.max_dd is not None]
    to_arr = sl("avg_turnover")

    for r in results:
        if r.hard_fail: continue
        try:
            s = 0.0
            if r.sharpe is not None and s_arr:   s += 0.25 * _zscore(r.sharpe, s_arr)
            if r.ann_return is not None and c_arr: s += 0.15 * _zscore(r.ann_return, c_arr)
            if r.calmar is not None and cal_arr:   s += 0.20 * _zscore(r.calmar, cal_arr)
            if r.sortino is not None and sor_arr:  s += 0.10 * _zscore(r.sortino, sor_arr)
            if r.max_dd is not None and mdd_arr:   s -= 0.10 * _zscore(abs(r.max_dd), mdd_arr)
            if r.avg_turnover is not None and to_arr: s -= 0.10 * _zscore(r.avg_turnover, to_arr)
            if r.fold19_sharpe is not None and r.fold19_sharpe < 0: s -= 0.05

            # [감사] 비용 강건성 보너스/페널티
            if r.sharpe_25bps is not None:
                if r.sharpe_25bps > 1.0: s += 0.05
                elif r.sharpe_25bps < 0.75: s -= 0.10
            if r.rebal_decay_ratio is not None and r.rebal_decay_ratio < 0.75: s -= 0.10
            if r.target_vol_elasticity is not None and r.target_vol_elasticity < 0.10: s -= 0.05

            r.score = round(s, 6)
        except: r.score = -9998.0

    results.sort(key=lambda x: x.score, reverse=True)
    return results
