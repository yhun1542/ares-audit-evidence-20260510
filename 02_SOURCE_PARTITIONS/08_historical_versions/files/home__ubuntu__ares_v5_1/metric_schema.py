"""
metric_schema.py — 통합 Metric Schema v5.1
============================================
v5 대비 변경:
1. survives_up_to_bps 필드 추가 (breakeven 외삽 금지 대체)
2. audit_pass 필드 추가 (sensitivity sweep 결과 종합)
3. breakeven_tc_bps=None일 때 hard_fail 처리 수정
   - None이면 survives_up_to_bps >= 25 여부로 대체 판정
4. apply_hard_filters_prod() 추가 — production 전용 (25bps 하한)
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
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

    # ── Cost Sensitivity ──
    sharpe_15bps: Optional[float] = None
    sharpe_25bps: Optional[float] = None
    sharpe_30bps: Optional[float] = None
    breakeven_tc_bps: Optional[float] = None       # None = 0 crossing 미관측 (외삽 금지)
    survives_up_to_bps: Optional[float] = None      # v5.1 신규: SR>0인 최대 bps

    # ── Rebal Sensitivity ──
    sharpe_rebal_base: Optional[float] = None
    sharpe_rebal_2x: Optional[float] = None
    rebal_decay_ratio: Optional[float] = None

    # ── Target Vol Elasticity ──
    target_vol_elasticity: Optional[float] = None

    # ── Diagnostics ──
    cost_filter_pass: Optional[bool] = None
    rebal_filter_pass: Optional[bool] = None
    fold19_filter_pass: Optional[bool] = None
    knob_alive: Optional[bool] = None
    audit_pass: Optional[bool] = None               # v5.1 신규: 종합 감사 통과 여부

    # ── Scoring ──
    score: float = -9999.0
    hard_fail: bool = False
    hard_fail_reason: str = ""

    # ── Config (재현용) ──
    full_config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self): return asdict(self)
    def to_json(self): return json.dumps(asdict(self), cls=NpEncoder, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# HARD FILTERS — DIAG 모드 (20bps 허용, 연구용)
# ═══════════════════════════════════════════════════════════════

def apply_hard_filters(r: NormalizedResult, mode: str = "diag") -> NormalizedResult:
    """
    mode="diag": 기존 v5 필터 (연구/진단용)
    mode="prod": production 필터 (25bps 하한 강제)
    """
    def _fail(reason):
        r.hard_fail = True
        r.hard_fail_reason = reason
        r.score = -9999.0
        r.audit_pass = False
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

    # 4. 비용 강건성 — breakeven 또는 survives_up_to_bps 기반 판정
    min_bps = 25 if mode == "prod" else 20
    if r.breakeven_tc_bps is not None:
        # 실측 0-crossing이 있음 → 그 값으로 판정
        if r.breakeven_tc_bps < min_bps:
            r.cost_filter_pass = False
            return _fail(f"breakeven_tc_bps={r.breakeven_tc_bps:.0f} < {min_bps}")
    elif r.survives_up_to_bps is not None:
        # 0-crossing 미관측 → survives_up_to_bps로 대체 판정
        if r.survives_up_to_bps < min_bps:
            r.cost_filter_pass = False
            return _fail(f"survives_up_to_bps={r.survives_up_to_bps:.0f} < {min_bps}")
    # breakeven=None, survives=None → sensitivity sweep 미실행, 통과 (Phase 1에서)
    r.cost_filter_pass = True

    # 5. SR at 25bps
    if r.sharpe_25bps is not None and r.sharpe_25bps <= 0:
        r.cost_filter_pass = False
        return _fail(f"sharpe_25bps={r.sharpe_25bps:.3f} <= 0")

    # 6. SR at 30bps (prod에서만 강제)
    if mode == "prod" and r.sharpe_30bps is not None and r.sharpe_30bps < 0:
        r.cost_filter_pass = False
        return _fail(f"sharpe_30bps={r.sharpe_30bps:.3f} < 0")

    # 7. Rebal decay
    if r.rebal_decay_ratio is not None and r.rebal_decay_ratio < 0.65:
        r.rebal_filter_pass = False
        return _fail(f"rebal_decay={r.rebal_decay_ratio:.2f} < 0.65")
    r.rebal_filter_pass = True

    # 8. Dead knob
    if r.target_vol_elasticity is not None and r.target_vol_elasticity < 0.05:
        r.knob_alive = False
        return _fail(f"target_vol_elasticity={r.target_vol_elasticity:.3f} < 0.05")
    r.knob_alive = True

    # 9. Turnover machine
    max_to = 45 if mode == "prod" else 50
    if r.avg_turnover is not None and r.avg_turnover > max_to:
        return _fail(f"turnover>{max_to}x (turnover machine)")

    # ── Production 추가 필터 ──
    if mode == "prod":
        # fold19_sharpe >= 0 (prod에서는 양수 필수)
        if r.fold19_sharpe is not None and r.fold19_sharpe < 0:
            return _fail(f"fold19_sharpe={r.fold19_sharpe:.3f} < 0 (prod)")

    r.audit_pass = True
    return r


# ═══════════════════════════════════════════════════════════════
# SCORING — 턴오버 페널티 강화 + 비용 강건성 보너스
# ═══════════════════════════════════════════════════════════════

def _zscore_vec(arr):
    """Vectorized z-score for entire array at once (NumPy, ~1000x faster)"""
    a = np.asarray(arr, dtype=np.float64)
    if len(a) < 2: return np.zeros_like(a)
    mu = np.mean(a); sd = np.std(a)
    return (a - mu) / sd if sd > 1e-15 else np.zeros_like(a)


def score_population(results: List[NormalizedResult],
                     mode: str = "diag") -> List[NormalizedResult]:
    for r in results:
        apply_hard_filters(r, mode=mode)

    valid = [r for r in results if not r.hard_fail]
    if len(valid) < 3:
        return results

    # ── Vectorized z-score (NumPy, ~1000x faster than statistics.mean) ──
    fields = ["sharpe", "ann_return", "calmar", "sortino"]
    weights = [0.25, 0.15, 0.20, 0.10]
    neg_fields = ["max_dd_abs", "avg_turnover"]
    neg_weights = [0.10, 0.10]

    # Build value arrays for valid results
    val_map = {}
    for f in fields:
        val_map[f] = np.array([getattr(r, f) if getattr(r, f) is not None else np.nan for r in valid])
    val_map["max_dd_abs"] = np.array([abs(r.max_dd) if r.max_dd is not None else np.nan for r in valid])
    val_map["avg_turnover"] = np.array([r.avg_turnover if r.avg_turnover is not None else np.nan for r in valid])

    # Pre-compute z-scores for each field (vectorized)
    z_map = {}
    for f in list(fields) + neg_fields:
        arr = val_map[f]
        mask = ~np.isnan(arr)
        z = np.zeros(len(arr))
        if mask.sum() >= 2:
            sub = arr[mask]
            mu, sd = np.mean(sub), np.std(sub)
            if sd > 1e-15:
                z[mask] = (sub - mu) / sd
        z_map[f] = z

    # Build index map: valid result -> position in valid list
    valid_set = {id(r): i for i, r in enumerate(valid)}

    for r in results:
        if r.hard_fail: continue
        try:
            idx = valid_set.get(id(r))
            if idx is None: continue
            s = 0.0
            for f, w in zip(fields, weights):
                if not np.isnan(val_map[f][idx]):
                    s += w * z_map[f][idx]
            for f, w in zip(neg_fields, neg_weights):
                if not np.isnan(val_map[f][idx]):
                    s -= w * z_map[f][idx]
            if r.fold19_sharpe is not None and r.fold19_sharpe < 0: s -= 0.05

            # [감사] 비용 강건성 보너스/페널티
            if r.sharpe_25bps is not None:
                if r.sharpe_25bps > 1.0: s += 0.05
                elif r.sharpe_25bps < 0.75: s -= 0.10
            if r.rebal_decay_ratio is not None and r.rebal_decay_ratio < 0.75: s -= 0.10
            if r.target_vol_elasticity is not None and r.target_vol_elasticity < 0.10: s -= 0.05

            # [v5.1] survives_up_to_bps 보너스
            if r.survives_up_to_bps is not None and r.survives_up_to_bps >= 60:
                s += 0.10
            elif r.survives_up_to_bps is not None and r.survives_up_to_bps >= 40:
                s += 0.05

            r.score = round(s, 6)
        except: r.score = -9998.0

    results.sort(key=lambda x: x.score, reverse=True)
    return results
