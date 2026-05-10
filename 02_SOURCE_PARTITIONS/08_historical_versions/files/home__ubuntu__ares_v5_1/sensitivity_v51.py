"""
sensitivity_v51.py — v5.1 감사 기반 민감도 스윕 모듈 (근본 수정)
================================================================
v5 대비 변경점:
1. breakeven_tc_bps 외삽 금지 — 실측 0-crossing만 인정
2. sweep 범위 확장: 15, 20, 25, 30, 35, 40, 50, 60 bps
3. 0 crossing 없으면 breakeven_tc_bps = None, survives_up_to_bps 필드 추가
4. rebal sweep: base / 1.5x / 2x / 3x
5. vol sweep: 0.08 / 0.12 / 0.15 / 0.20 / 0.25
"""
from __future__ import annotations
import copy
from typing import Callable, Any

try:
    from real_market_sim import RealMarketSimulator as MarketSimulator
except ImportError:
    from market_sim import MarketSimulator
from metric_schema import NormalizedResult


# ═══════════════════════════════════════════════════════════════
# 비용 sweep 포인트 — 외삽 금지를 위해 넓은 범위
# ═══════════════════════════════════════════════════════════════
COST_SWEEP_BPS = [15, 20, 25, 30, 35, 40, 50, 60]


def run_sensitivity_sweep(
    base_result: NormalizedResult,
    base_config: Any,
    runner_fn: Callable,
    sim: MarketSimulator,
    family: str,
) -> NormalizedResult:
    """
    base_result에 sensitivity fields를 채워서 반환.
    runner_fn(config, sim) → NormalizedResult 형태를 가정.
    """
    r = base_result

    # ── 1. 비용 민감도 (확장된 sweep) ──
    cost_curve = {}  # bps → sharpe
    for bps in COST_SWEEP_BPS:
        try:
            cfg_copy = _copy_with_cost(base_config, float(bps), family)
            if cfg_copy is None:
                continue
            cfg_copy.config_id = f"{base_config.config_id}_cost{bps}bps"
            result = runner_fn(cfg_copy, sim)
            cost_curve[bps] = result.sharpe
        except Exception:
            cost_curve[bps] = None

    # 표준 필드 채우기
    r.sharpe_15bps = cost_curve.get(15)
    r.sharpe_25bps = cost_curve.get(25)
    r.sharpe_30bps = cost_curve.get(30)

    # ── breakeven_tc_bps: 외삽 금지, 실측 0-crossing만 ──
    r.breakeven_tc_bps = _estimate_breakeven_strict(cost_curve)

    # ── survives_up_to_bps: 가장 높은 bps에서 SR > 0인 지점 ──
    r.survives_up_to_bps = _max_surviving_bps(cost_curve)

    # ── 2. 리밸런싱 민감도 (확장) ──
    r.sharpe_rebal_base = r.sharpe
    rebal_results = {}
    for mult_label, mult in [("1.5x", 1.5), ("2x", 2.0), ("3x", 3.0)]:
        try:
            cfg_r = _copy_with_rebal_mult(base_config, mult, family)
            if cfg_r is None:
                continue
            cfg_r.config_id = f"{base_config.config_id}_rebal{mult_label}"
            result_r = runner_fn(cfg_r, sim)
            rebal_results[mult_label] = result_r.sharpe
        except Exception:
            rebal_results[mult_label] = None

    # rebal_decay_ratio = worst(rebal variants) / base
    r.sharpe_rebal_2x = rebal_results.get("2x")
    valid_rebal = [v for v in rebal_results.values() if v is not None]
    if valid_rebal and r.sharpe is not None and r.sharpe > 0.01:
        r.rebal_decay_ratio = round(min(valid_rebal) / r.sharpe, 4)
    else:
        r.rebal_decay_ratio = None

    # ── 3. target_vol 탄성 (확장) ──
    vol_sharpes = {}
    for vol_label, vol_value in [("08", 0.08), ("12", 0.12), ("15", 0.15),
                                  ("20", 0.20), ("25", 0.25)]:
        try:
            cfg_v = _copy_with_vol(base_config, vol_value, family)
            if cfg_v is None:
                continue
            cfg_v.config_id = f"{base_config.config_id}_vol{vol_label}"
            result_v = runner_fn(cfg_v, sim)
            vol_sharpes[vol_label] = result_v.sharpe
        except Exception:
            vol_sharpes[vol_label] = None

    vals = [v for v in vol_sharpes.values() if v is not None]
    if len(vals) >= 2:
        r.target_vol_elasticity = round(max(vals) - min(vals), 4)
    else:
        r.target_vol_elasticity = None

    # ── Diagnostics (강화) ──
    r.cost_filter_pass = (
        r.sharpe_25bps is not None and r.sharpe_25bps > 0
        and r.breakeven_tc_bps is not None and r.breakeven_tc_bps >= 25
    )
    r.rebal_filter_pass = (
        r.rebal_decay_ratio is None or r.rebal_decay_ratio >= 0.65
    )
    r.knob_alive = (
        r.target_vol_elasticity is None or r.target_vol_elasticity >= 0.05
    )

    # ── 추가 audit 필드 ──
    r.audit_pass = r.cost_filter_pass and r.rebal_filter_pass and r.knob_alive

    return r


# ═══════════════════════════════════════════════════════════════
# breakeven 계산 — 외삽 금지 (v5.1 핵심 수정)
# ═══════════════════════════════════════════════════════════════

def _estimate_breakeven_strict(cost_curve: dict) -> float | None:
    """
    실측 데이터에서 Sharpe가 양수→음수로 전환하는 구간을 찾아 선형 보간.
    0 crossing이 관측되지 않으면 None 반환 (외삽 금지).
    """
    # bps 오름차순 정렬, None 제거
    points = sorted(
        [(bps, sr) for bps, sr in cost_curve.items() if sr is not None],
        key=lambda x: x[0]
    )

    if len(points) < 2:
        return None

    # 인접 두 점 사이에서 0 crossing 찾기
    for i in range(len(points) - 1):
        bps1, sr1 = points[i]
        bps2, sr2 = points[i + 1]

        # SR이 양수→음수 (또는 0)로 전환하는 구간
        if sr1 > 0 and sr2 <= 0:
            if abs(sr1 - sr2) > 1e-8:
                breakeven = bps1 + (bps2 - bps1) * (sr1 / (sr1 - sr2))
                return round(breakeven, 1)
            else:
                return round((bps1 + bps2) / 2, 1)

    # 0 crossing이 관측되지 않음 → None (외삽 금지!)
    return None


def _max_surviving_bps(cost_curve: dict) -> float | None:
    """가장 높은 bps에서 SR > 0인 지점을 반환."""
    surviving = [
        bps for bps, sr in cost_curve.items()
        if sr is not None and sr > 0
    ]
    return max(surviving) if surviving else None


# ═══════════════════════════════════════════════════════════════
# Config 복사 헬퍼
# ═══════════════════════════════════════════════════════════════

def _copy_with_cost(cfg, bps, family):
    c = copy.deepcopy(cfg)
    if family == "family_a":
        c.cost_bps = bps
    elif family == "family_b":
        c.base_spread_bps = bps * 0.4
        c.impact_coeff = bps * 0.2
    elif family == "family_c":
        c.spread_bps = bps * 0.5
        c.impact_bps_per_pct = bps * 0.1
    else:
        return None
    return c


def _copy_with_rebal_mult(cfg, mult, family):
    """리밸런싱 주기를 mult배로 늘리기."""
    c = copy.deepcopy(cfg)
    if family == "family_a":
        # Family A: turnover를 1/mult로 줄여서 근사
        c.annual_turnover = c.annual_turnover / mult
    elif family == "family_b":
        c.rebal_weeks = max(2, int(c.rebal_weeks * mult))
    elif family == "family_c":
        c.rebal_lambda = max(0.05, c.rebal_lambda / mult)
    else:
        return None
    return c


def _copy_with_vol(cfg, vol, family):
    c = copy.deepcopy(cfg)
    if family == "family_c":
        c.target_vol = vol
    elif family == "family_a":
        vol_ratio = vol / 0.15
        c.leverage_mult = c.leverage_mult * vol_ratio
    elif family == "family_b":
        return None
    else:
        return None
    return c
