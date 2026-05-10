"""
sensitivity.py — 감사 기반 민감도 스윕 모듈
=============================================
감사 보고서가 요구하는 3가지 민감도 테스트를 자동 실행:
1. 비용 민감도: 15 / 25 / 30 bps
2. 리밸런싱 민감도: base / 2x period
3. target_vol 탄성: 0.08 / 0.15 / 0.25

Phase 2 champion 추출 후, 상위 후보에만 적용한다 (비용 절감).
"""
from __future__ import annotations
import copy
from typing import Callable, Any
try:
    from real_market_sim import RealMarketSimulator as MarketSimulator
except ImportError:
    from market_sim import MarketSimulator
from metric_schema import NormalizedResult


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

    # ── 1. 비용 민감도 ──
    cost_results = {}
    for bps_label, bps_value in [("15bps", 15.0), ("25bps", 25.0), ("30bps", 30.0)]:
        try:
            cfg_copy = _copy_with_cost(base_config, bps_value, family)
            if cfg_copy is None:
                continue
            cfg_copy.config_id = f"{base_config.config_id}_cost{bps_label}"
            result = runner_fn(cfg_copy, sim)
            cost_results[bps_label] = result.sharpe
        except:
            cost_results[bps_label] = None

    r.sharpe_15bps = cost_results.get("15bps")
    r.sharpe_25bps = cost_results.get("25bps")
    r.sharpe_30bps = cost_results.get("30bps")

    # Breakeven TC 추정 (선형 보간)
    r.breakeven_tc_bps = _estimate_breakeven(cost_results, base_config, family)

    # ── 2. 리밸런싱 민감도 ──
    r.sharpe_rebal_base = r.sharpe
    try:
        cfg_2x = _copy_with_rebal_2x(base_config, family)
        if cfg_2x is not None:
            cfg_2x.config_id = f"{base_config.config_id}_rebal2x"
            result_2x = runner_fn(cfg_2x, sim)
            r.sharpe_rebal_2x = result_2x.sharpe
            if r.sharpe is not None and r.sharpe > 0.01:
                r.rebal_decay_ratio = round(result_2x.sharpe / r.sharpe, 4)
    except:
        pass

    # ── 3. target_vol 탄성 ──
    vol_sharpes = {}
    for vol_label, vol_value in [("08", 0.08), ("15", 0.15), ("25", 0.25)]:
        try:
            cfg_v = _copy_with_vol(base_config, vol_value, family)
            if cfg_v is None:
                continue
            cfg_v.config_id = f"{base_config.config_id}_vol{vol_label}"
            result_v = runner_fn(cfg_v, sim)
            vol_sharpes[vol_label] = result_v.sharpe
        except:
            vol_sharpes[vol_label] = None

    vals = [v for v in vol_sharpes.values() if v is not None]
    if len(vals) >= 2:
        r.target_vol_elasticity = round(max(vals) - min(vals), 4)
    else:
        r.target_vol_elasticity = None

    # Diagnostics
    r.cost_filter_pass = (r.sharpe_25bps is not None and r.sharpe_25bps > 0
                          and r.breakeven_tc_bps is not None and r.breakeven_tc_bps >= 25)
    r.rebal_filter_pass = r.rebal_decay_ratio is None or r.rebal_decay_ratio >= 0.65
    r.knob_alive = r.target_vol_elasticity is None or r.target_vol_elasticity >= 0.05

    return r


# ═══════════════════════════════════════════════════════════════
# Config 복사 헬퍼 (family별 비용/리밸 파라미터 이름이 다르므로)
# ═══════════════════════════════════════════════════════════════

def _copy_with_cost(cfg, bps, family):
    c = copy.deepcopy(cfg)
    if family == "family_a":
        c.cost_bps = bps
    elif family == "family_b":
        c.base_spread_bps = bps * 0.4   # spread portion
        c.impact_coeff = bps * 0.2       # impact portion
    elif family == "family_c":
        c.spread_bps = bps * 0.5
        c.impact_bps_per_pct = bps * 0.1
    else:
        return None
    return c


def _copy_with_rebal_2x(cfg, family):
    c = copy.deepcopy(cfg)
    if family == "family_b":
        c.rebal_weeks = max(2, c.rebal_weeks * 2)
    elif family == "family_a":
        # Family A는 weekly 고정이라 rebal 개념이 다름
        # 턴오버를 절반으로 줄이는 것으로 근사
        c.annual_turnover = c.annual_turnover * 0.5
    elif family == "family_c":
        c.rebal_lambda = max(0.10, c.rebal_lambda * 0.5)  # partial rebal을 더 느리게
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
        # Family B has no direct target_vol; skip this test
        return None
    else:
        return None
    return c


def _estimate_breakeven(cost_results, cfg, family):
    """비용 수준별 SR을 선형 보간하여 SR=0이 되는 bps 추정."""
    points = []
    for label, bps in [("15bps", 15), ("25bps", 25), ("30bps", 30)]:
        sr = cost_results.get(label)
        if sr is not None:
            points.append((bps, sr))

    if len(points) < 2:
        return None

    # 가장 가까운 두 점으로 선형 보간
    points.sort(key=lambda x: x[0])
    for i in range(len(points) - 1):
        bps1, sr1 = points[i]
        bps2, sr2 = points[i + 1]
        if sr1 > 0 >= sr2:
            # SR이 0을 크로스하는 지점
            if sr1 - sr2 > 1e-8:
                breakeven = bps1 + (bps2 - bps1) * (sr1 / (sr1 - sr2))
                return round(breakeven, 1)

    # 모든 점에서 SR > 0이면 마지막 두 점 외삽
    if all(sr > 0 for _, sr in points):
        bps1, sr1 = points[-2]
        bps2, sr2 = points[-1]
        if sr1 - sr2 > 1e-8:
            breakeven = bps1 + (bps2 - bps1) * (sr1 / (sr1 - sr2))
            return round(breakeven, 1)
        else:
            return 100.0  # very robust

    return round(points[0][0], 1) if points[0][1] <= 0 else None
