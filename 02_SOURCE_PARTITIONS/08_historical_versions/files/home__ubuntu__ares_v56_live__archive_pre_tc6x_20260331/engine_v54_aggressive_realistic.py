
#!/usr/bin/env python3
"""
ARES NEXUS V5.4-A — Aggressive Realistic
========================================
V5.3 HOTFIX를 베이스로, 다음 3가지를 추가한 공격형 현실형 엔진.

1) V5.2 멀티 유니버스 후보군
   - 기본 후보: U4_SECTOR_BAL, U5_HIGH_SHARPE, U6_BARBELL, U10_FULL_60, U8_LOW_CORR, U9_SECTOR_ROT
   - U7/U8/U9는 "마지막 시점 고정"이 아니라 매 리밸런싱마다 다시 선별

2) Rolling E3 Selector
   - 전체 샘플 SR 상위 3개가 아니라 최근 ranking_window(기본 63일) 기준 trailing SR 상위 3개 사용
   - warmup 구간은 fallback_universes 사용

3) Daily signal + 2-bucket staggered execution
   - 신호는 매일 갱신
   - 실제 집행은 Book A / Book B 교대로 절반 자본씩 실행
   - 실제 비용(7bps)와 shadow 비용(7bps + spread/slippage + impact proxy) 동시 기록

주의:
- 이 파일은 V5.3를 대체하는 "완전 복제"가 아니라, 공격형 현실형 V5.4-A 구현이다.
- 기존 V5.3 hard cap / per_cap / target_exposure 상한 / A2B2 / NTB / turnover cap은 유지한다.
"""
import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

LIVE_UNI = [
    "AAPL","ABBV","ADBE","AMD","AMZN","ARM","ASML","AVGO","AXP","BA",
    "BAC","BRK.B","CAT","COP","COST","CRWD","CVS","CVX","DE","GD",
    "GE","GOOGL","GS","HD","INTC","JNJ","JPM","K","KO","LLY",
    "LMT","LOW","MA","MCD","META","MO","MRK","MS","MSFT","MU",
    "NFLX","NOC","NVDA","PEP","PFE","PG","PLTR","PM","QCOM","RTX",
    "SMCI","SNOW","SYY","TGT","TSLA","UNH","V","WFC","WMT","XOM",
]
RN={0:"CRASH",1:"BEAR",2:"NORMAL",3:"BULL",4:"STRONG_BULL",5:"INF_SHOCK"}

# V5.3 sleeve mix 유지
SMIX = {
    0:{"TREND":.05,"TF":0,"DLV":.30,"GNN":0,"MRC":.05,"CA":.10,"VC":0,"MC":0,"CASH":.50},
    1:{"TREND":.05,"TF":0,"DLV":.20,"GNN":.05,"MRC":.20,"CA":.10,"VC":.05,"MC":.05,"CASH":.30},
    2:{"TREND":.05,"TF":0,"DLV":0,"GNN":.10,"MRC":.35,"CA":.08,"VC":.10,"MC":.07,"CASH":0},
    3:{"TREND":.10,"TF":0,"DLV":0,"GNN":.10,"MRC":.20,"CA":.05,"VC":.12,"MC":.08,"CASH":0},
    4:{"TREND":.08,"TF":0,"DLV":0,"GNN":.10,"MRC":.17,"CA":.05,"VC":.10,"MC":.05,"CASH":.05},
    5:{"TREND":.05,"TF":0,"DLV":.30,"GNN":0,"MRC":.05,"CA":.10,"VC":0,"MC":.10,"CASH":.30},
}
RTIERS=[(0,4,1.20),(4,8,1.00),(8,12,0.85),(12,16,0.70),(16,20,0.60)]
MAX_GROSS_EXPOSURE = 0.98

UNIVERSES = {
    "U4_SECTOR_BAL": [
        "AAPL","AMD","AMZN","AVGO","GOOGL","META","MSFT","NVDA",
        "JPM","BAC","BRK.B","GS","V","MA",
        "ABBV","JNJ","LLY","MRK","UNH",
        "BA","CAT","DE","GE","GD",
        "COST","KO","MCD","PEP","PG","WMT",
        "XOM","CVX","COP","LMT","NOC","RTX","HD","LOW","NFLX","TSLA",
    ],
    "U5_HIGH_SHARPE": [
        "AAPL","AMZN","AVGO","COST","GOOGL","HD","LLY","MA","MCD","META",
        "MSFT","NFLX","NVDA","UNH","V","WMT","JPM","GS","BRK.B","LMT","NOC","RTX","ABBV","MRK","JNJ",
    ],
    "U6_BARBELL": [
        "AMD","ARM","AVGO","CRWD","META","NVDA","PLTR","SMCI","SNOW","TSLA","MU","NFLX",
        "ABBV","JNJ","KO","MCD","MO","PEP","PG","PM","UNH","WMT","LMT","NOC",
    ],
    "U10_FULL_60": LIVE_UNI.copy(),
}

SECTOR_MAP = {
    "TECH": ["AAPL","ADBE","AMD","AMZN","ARM","ASML","AVGO","CRWD","GOOGL",
             "INTC","META","MSFT","MU","NFLX","NVDA","PLTR","QCOM","SMCI","SNOW","TSLA"],
    "FINANCE": ["AXP","BAC","BRK.B","GS","JPM","MA","MS","V","WFC"],
    "HEALTH": ["ABBV","CVS","JNJ","LLY","MRK","PFE","UNH"],
    "INDUSTRIAL": ["BA","CAT","DE","GD","GE","LMT","NOC","RTX"],
    "STAPLES": ["COST","K","KO","MCD","MO","PEP","PG","PM","SYY","TGT","WMT"],
    "ENERGY": ["COP","CVX","XOM"],
    "DISC": ["HD","LOW"],
}
REGIME_SECTOR_WEIGHTS = {
    0: {"TECH":0.05,"FINANCE":0.05,"HEALTH":0.20,"INDUSTRIAL":0.10,"STAPLES":0.30,"ENERGY":0.10,"DISC":0.00, "DEFENSE":0.20},
    1: {"TECH":0.10,"FINANCE":0.10,"HEALTH":0.25,"INDUSTRIAL":0.05,"STAPLES":0.25,"ENERGY":0.10,"DISC":0.00, "DEFENSE":0.15},
    2: {"TECH":0.25,"FINANCE":0.15,"HEALTH":0.15,"INDUSTRIAL":0.10,"STAPLES":0.15,"ENERGY":0.08,"DISC":0.05, "DEFENSE":0.07},
    3: {"TECH":0.35,"FINANCE":0.20,"HEALTH":0.10,"INDUSTRIAL":0.10,"STAPLES":0.08,"ENERGY":0.07,"DISC":0.05, "DEFENSE":0.05},
    4: {"TECH":0.45,"FINANCE":0.15,"HEALTH":0.05,"INDUSTRIAL":0.10,"STAPLES":0.05,"ENERGY":0.10,"DISC":0.05, "DEFENSE":0.05},
    5: {"TECH":0.05,"FINANCE":0.05,"HEALTH":0.20,"INDUSTRIAL":0.10,"STAPLES":0.30,"ENERGY":0.10,"DISC":0.00, "DEFENSE":0.20},
}

DEFAULT_PARAMS = {
    "top_k": 20,
    "signal_rebal": 1,                # signal refresh frequency
    "execution_stagger": 2,           # 2-bucket stagger execution
    "target_vol": 0.20,
    "per_cap": 0.045,
    "cost_bps": 7.0,                  # actual execution cost
    "shadow_slippage_bps": 3.0,       # extra shadow spread/slippage
    "shadow_impact_coeff_bps": 6.0,   # extra bps * turnover for impact proxy
    "ace_base": {0:.45,1:.55,2:.90,3:.94,4:.96,5:.40},
    "ace_max": {0:.70,1:.80,2:.95,3:.98,4:.98,5:.65},
    "ace_floor": {0:.25,1:.35,2:.70,3:.80,4:.80,5:.20},
    "vol_clamp_low": 0.60,
    "vol_clamp_high": 1.50,
    "switch_pen": 3.0,
    "inc_bonus": 1.5,
    "ntb_base": 0.012,
    "ntb_min": 0.005,
    "ntb_max": 0.025,
    "to_base": 0.22,
    "to_max": 0.40,
    "a2b2_base": 0.50,
    "a2b2_max": 0.80,
    "a2b2_to": 0.07,
    "crash_vix": 35.0,
    "candidate_universes": ["U4_SECTOR_BAL","U5_HIGH_SHARPE","U6_BARBELL","U10_FULL_60","U8_LOW_CORR","U9_SECTOR_ROT"],
    "fallback_universes": ["U4_SECTOR_BAL","U5_HIGH_SHARPE","U10_FULL_60"],
    "ranking_window": 63,
    "ranking_min_obs": 20,
}

_FULL_CACHE = {}

@dataclass
class StrategyState:
    pw: np.ndarray
    prev_reg: int = 2
    confidence: float = 0.50
    sig_hist: deque = field(default_factory=lambda: deque(maxlen=40))
    ret_hist: deque = field(default_factory=lambda: deque(maxlen=40))
    daily_returns: List[float] = field(default_factory=list)
    last_selected: List[str] = field(default_factory=list)
    last_target: Optional[np.ndarray] = None

@dataclass
class BookState:
    pw: np.ndarray
    prev_reg: int = 2

def _zs(a):
    v=np.isfinite(a)
    if v.sum()<5: return np.zeros_like(a)
    m,s=np.nanmean(a[v]),np.nanstd(a[v])
    return np.where(v,(a-m)/(s+1e-8),0.0) if s>1e-8 else np.zeros_like(a)

def _regime(R,t,vix=None):
    if t<20: return 2
    mkt=np.nanmean(R[max(0,t-20):t],axis=1)
    v20=np.nanstd(mkt) if len(mkt)>2 else .015
    vx=float(vix[t]) if vix is not None and t<len(vix) else 20.0
    if v20>.025 or vx>35: return 0
    if v20>.019 or vx>25: return 1
    if v20>.014: return 2
    if t>=60:
        c=np.nansum(np.nanmean(R[max(0,t-60):t],axis=1))
        if c>.10: return 4
        if c>.03: return 3
    return 2

def load_full_db(db_path, min_match=20):
    ck = f"full::{db_path}"
    if ck in _FULL_CACHE:
        return _FULL_CACHE[ck]

    conn = sqlite3.connect(db_path, timeout=60)
    df = pd.read_sql_query(
        "SELECT date,symbol,open,high,low,close,volume FROM daily_ohlcv "
        "WHERE symbol IN (SELECT symbol FROM daily_ohlcv GROUP BY symbol HAVING COUNT(*)>1000) "
        "ORDER BY date,symbol", conn
    )
    df['date'] = pd.to_datetime(df['date'], format='mixed')
    df = df.drop_duplicates(subset=['date','symbol'], keep='last')
    df['dvol'] = df['close'] * df['volume']

    pdf = df.pivot(index='date', columns='symbol', values='close').sort_index()
    cov = pdf.notna().sum(axis=1)
    for dt, c in cov.items():
        if c >= 35:
            pdf = pdf.loc[dt:]
            break

    dr = pdf.notna().mean()
    all_available = dr[dr >= 0.5].index.tolist()

    matched = [s for s in LIVE_UNI if s in all_available]
    unmatched = [s for s in LIVE_UNI if s not in all_available]
    print(f"\n[DIAG] DB symbols available: {len(all_available)}")
    print(f"[DIAG] LIVE_UNI requested:   {len(LIVE_UNI)}")
    print(f"[DIAG] Matched:              {len(matched)} ({len(matched)/len(LIVE_UNI)*100:.0f}%)")
    if unmatched:
        print(f"[DIAG] MISSING from DB: {', '.join(sorted(unmatched))}")
    if len(matched) >= min_match:
        keep = matched
        print(f"[DATA] Using {len(keep)} matched LIVE_UNI symbols")
    else:
        keep = all_available
        print(f"[WARN] Only {len(matched)} matched (< {min_match}). Falling back to {len(keep)} available symbols.")

    pdf = pdf[keep].ffill(limit=3)
    rdf = pdf.pct_change()
    idx = rdf.index[1:]

    out = {
        'prices_df': pdf,
        'returns_df': rdf,
        'dates': idx,
        'symbols': keep,
        'symbol_to_idx': {s:i for i,s in enumerate(keep)},
        'prices': pdf.values[1:].astype(np.float64),
        'returns': np.nan_to_num(rdf.values[1:].astype(np.float64), nan=0.0),
    }
    for col, src in [('high','high'),('low','low'),('open','open'),('dvol','dvol')]:
        try:
            pv = df.pivot(index='date', columns='symbol', values=src).sort_index()
            pv = pv[keep].ffill(limit=3)
            out[col] = np.nan_to_num(pv.reindex(idx).values.astype(np.float64), nan=0.0)
            out[f'{col}_df'] = pv
        except Exception:
            out[col] = out['prices'].copy()
            out[f'{col}_df'] = pdf.copy()
    try:
        vdf = pd.read_sql_query("SELECT date,close as vix FROM vix ORDER BY date", conn)
        vdf['date'] = pd.to_datetime(vdf['date'], format='mixed')
        vdf = vdf.drop_duplicates('date', keep='last').set_index('date')
        out['vix'] = vdf['vix'].reindex(idx).ffill().fillna(20).values.astype(np.float64)
    except Exception:
        out['vix'] = np.full(out['returns'].shape[0], 20.0)
    conn.close()
    print(f"[DATA] Final full matrix: {out['returns'].shape[0]}d × {out['returns'].shape[1]}a")
    _FULL_CACHE[ck] = out
    return out

# ═══════════════════════════════════════════════════════════════
# DYNAMIC UNIVERSE SELECTORS
# ═══════════════════════════════════════════════════════════════

def select_low_corr_universe(full_data, t, n_select=25):
    rdf = full_data['returns_df']
    all_sym = full_data['symbols']
    if t < 60:
        return all_sym[:n_select]
    # returns index aligns with idx = rdf.index[1:], so +1 offset into dataframe
    end = min(t+1, len(rdf)-1)
    start = max(1, end-59)
    window = rdf.iloc[start:end+1]
    corr = window.corr().fillna(0.0)
    selected = []
    candidates = list(corr.columns)
    if not candidates:
        return []
    avg_corr = corr.abs().mean().sort_values()
    selected.append(avg_corr.index[0])
    candidates.remove(selected[0])
    while len(selected) < n_select and candidates:
        best_cand = None
        best_avg = 999.0
        for c in candidates:
            avg = float(np.mean([abs(corr.loc[c, s]) for s in selected]))
            if avg < best_avg:
                best_avg = avg
                best_cand = c
        if best_cand is None:
            break
        selected.append(best_cand)
        candidates.remove(best_cand)
    return selected

def select_sector_rotation(full_data, t, regime):
    all_sym = set(full_data['symbols'])
    weights = REGIME_SECTOR_WEIGHTS.get(regime, REGIME_SECTOR_WEIGHTS[2])
    selected = []
    for sector, w in weights.items():
        if sector == "DEFENSE":
            syms = [s for s in SECTOR_MAP.get("INDUSTRIAL", []) if s in all_sym and s in ["GD","LMT","NOC","RTX","BA"]]
        else:
            syms = [s for s in SECTOR_MAP.get(sector, []) if s in all_sym]
        n = max(1, int(w * 40))
        selected.extend(syms[:n])
    out = list(dict.fromkeys(selected))
    return out

def select_universe_symbols(full_data, universe_name, t, regime):
    if universe_name in UNIVERSES:
        return [s for s in UNIVERSES[universe_name] if s in full_data['symbol_to_idx']]
    if universe_name == "U8_LOW_CORR":
        return select_low_corr_universe(full_data, t, 25)
    if universe_name == "U9_SECTOR_ROT":
        return select_sector_rotation(full_data, t, regime)
    raise KeyError(f"Unknown universe: {universe_name}")

# ═══════════════════════════════════════════════════════════════
# FACTOR ENGINE
# ═══════════════════════════════════════════════════════════════

def compute_sleeves(P,R,H,L,O,DV,t,N,regime):
    trend=(P[t-21]/(P[t-252]+1e-12)-1) if t>=252 else np.full(N,np.nan)
    tf=(P[t-21]/(P[t-63]+1e-12)-1) if t>=63 else np.full(N,np.nan)
    sw,lw=min(21,t),min(63,t)
    if t>=sw+5:
        rs=np.nanmean(R[t-sw:t],axis=0)*252; rl=np.nanmean(R[t-lw:t],axis=0)*252
        vol=np.nanstd(R[t-sw:t],axis=0)*np.sqrt(252)+1e-8
        dlv=.6*(rs/vol)+.4*(rl-rs)
    else:
        dlv=np.full(N,np.nan)
    mr5=-np.nansum(R[t-5:t],axis=0) if t>=5 else np.full(N,np.nan)
    if t>=1:
        rng=H[t]-L[t]; rng_s=np.where(rng>1e-8,rng,np.nan)
        clv=np.clip((2*P[t]-H[t]-L[t])/(rng_s+1e-12),-1,1)
    else:
        clv=np.full(N,np.nan)
    mrc=.45*_zs(mr5)+.45*_zs(clv)+.10*np.zeros(N)
    gnn=np.full(N,np.nan)
    if t>=70:
        mkt=np.nanmean(R[max(0,t-60):t],axis=1)
        corrs=np.zeros(N)
        for i in range(N):
            v=np.isfinite(R[max(0,t-60):t,i])
            if v.sum()>20:
                corrs[i]=np.corrcoef(R[max(0,t-60):t,i][v],mkt[v])[0,1]
        ami_r=np.zeros(N)
        for j in range(N):
            s=0.0;c=0
            for k in range(20):
                if DV[t-20+k,j]>1e3:
                    s+=abs(R[t-20+k,j])/DV[t-20+k,j]
                    c+=1
            if c>0:
                ami_r[j]=-s/c
        gnn=.5*_zs(corrs)+.5*_zs(ami_r)
    ca=np.full(N,np.nan)
    if t>=60:
        v=np.nanstd(R[max(0,t-60):t],axis=0)*np.sqrt(252)
        vl=np.isfinite(v)&(v>0)
        if vl.sum()>=5:
            ca=np.where(vl,-_zs(v),np.nan)
    ca=_zs(ca)
    vc=np.full(N,np.nan)
    if t>=20:
        r10=np.nanmean(R[max(0,t-10):t],axis=0)
        vr=np.nanstd(R[max(0,t-5):t],axis=0)/(np.nanstd(R[max(0,t-20):t],axis=0)+1e-8)
        vc=_zs(np.sign(r10)*(vr-1.0))
    mc=np.full(N,np.nan)
    if t>=60:
        mkt=np.nanmean(R[max(0,t-60):t],axis=1)
        if np.nanvar(mkt)>1e-12:
            betas=np.clip(np.array([np.nansum(R[max(0,t-60):t,i]*mkt)/(np.nansum(mkt**2)+1e-12) for i in range(N)]),0.3,2.5)
            m20=np.nansum(np.nanmean(R[max(0,t-20):t],axis=1))
            mc=_zs(betas*np.sign(m20))
    scores={"TREND":trend,"TF":tf,"DLV":dlv,"MRC":mrc,"GNN":gnn,"CA":ca,"VC":vc,"MC":mc}
    if regime==0:
        scores['DLV']*=1.5; scores['GNN']=np.full(N,np.nan); scores['MRC']*=.5
    elif regime==1:
        scores['DLV']*=1.3; scores['GNN']=np.full(N,np.nan); scores['MRC']*=1.2
    elif regime==2:
        scores['MRC']*=1.3; scores['TREND']*=.7
    elif regime==3:
        scores['TF']*=1.2
    elif regime==4:
        scores['TREND']*=1.3; scores['TF']*=1.3; scores['DLV']*=.5
    elif regime==5:
        scores['TREND']*=.1; scores['TF']*=.1; scores['DLV']*=2.0; scores['GNN']=np.full(N,np.nan); scores['MRC']*=.3
    mkt_vol=np.nanstd(np.nanmean(R[max(0,t-20):t],axis=1)) if t>=20 else .02
    if regime<2 or mkt_vol>.030:
        scores['TF']=np.full(N,np.nan)
    return scores

# ═══════════════════════════════════════════════════════════════
# TARGET CONSTRUCTION
# ═══════════════════════════════════════════════════════════════

def _impact_proxy_bps(turnover, params):
    return params["shadow_slippage_bps"] + params["shadow_impact_coeff_bps"] * float(turnover)

def _effective_cap(params):
    return min(params["per_cap"], MAX_GROSS_EXPOSURE / max(params["top_k"], 1) * 1.5)

def _enforce_exposure_floor_cap(w, reg, params):
    w = np.clip(np.array(w, dtype=np.float64), 0.0, None)
    gross = float(np.sum(w))
    floor = params["ace_floor"].get(reg, 0.65)
    cap = min(params["ace_max"].get(reg, 0.95), MAX_GROSS_EXPOSURE)
    if gross > 1e-8 and gross < floor:
        w *= min(floor / gross, cap / gross)
    gross = float(np.sum(w))
    if gross > cap and gross > 0:
        w *= cap / gross
    return w

def build_target_weights(full_data, t, state: StrategyState, params, universe_symbols):
    P,R,H,L,O,DV = full_data['prices'], full_data['returns'], full_data['high'], full_data['low'], full_data['open'], full_data['dvol']
    vix = full_data.get('vix')
    N = R.shape[1]
    reg = _regime(R, t, vix)
    vx = float(vix[t]) if vix is not None and t < len(vix) else 20.0

    # allowed mask
    allowed = np.zeros(N, dtype=bool)
    for s in universe_symbols:
        idx = full_data['symbol_to_idx'].get(s)
        if idx is not None:
            allowed[idx] = True
    if allowed.sum() < 5:
        return state.pw.copy(), {"reg": reg, "vx": vx, "confidence": state.confidence, "selected": []}

    scores = compute_sleeves(P, R, H, L, O, DV, t, N, reg)
    mix = SMIX.get(reg, SMIX[2])
    comb = np.zeros(N); tw = 0.0
    for sn, sw in mix.items():
        if sn == "CASH" or sw < .001 or sn not in scores:
            continue
        s = scores[sn]
        if s is None or np.all(np.isnan(s)):
            continue
        v = np.isfinite(s) & allowed
        if v.sum() < 5:
            continue
        m, sd = np.nanmean(s[v]), np.nanstd(s[v])
        if sd < 1e-8:
            continue
        comb += sw * np.where(v, (s-m)/(sd+1e-8), np.nan)
        tw += sw
    if tw < .01:
        return state.pw.copy(), {"reg": reg, "vx": vx, "confidence": state.confidence, "selected": []}
    comb = comb / tw

    # alpha transform
    rv20 = np.nanstd(np.nanmean(R[max(0,t-20):t], axis=1)) if t >= 20 else .015
    ms = 0
    if vx > 30: ms += 2
    elif vx > 20: ms += 1
    if rv20 > .03: ms += 2
    elif rv20 > .02: ms += 1
    comb = comb * [1, .96, .96, .90, .90, .82, .82][min(ms, 6)]

    # confidence update (same logic as V5.3)
    if t >= 1:
        state.ret_hist.append(R[t-1].copy())
        state.sig_hist.append(np.where(np.isfinite(comb), comb, 0.0).copy())
    if len(state.sig_hist) >= 10:
        ics = []
        for i in range(min(15, len(state.sig_hist)-1)):
            sg = state.sig_hist[-(i+2)]
            rt = state.ret_hist[-(i+1)]
            vl = np.isfinite(sg) & np.isfinite(rt) & allowed
            if vl.sum() >= 10:
                ic = np.corrcoef(sg[vl], rt[vl])[0, 1]
                if np.isfinite(ic):
                    ics.append(ic)
        state.confidence = np.clip(np.mean(ics)/.05 if ics else 0, 0, 1) * 0.5 + 0.25

    # ACE exposure
    ab = params["ace_base"]; am = params["ace_max"]; af = params["ace_floor"]
    b = ab.get(reg, .85); mx = min(am.get(reg, .95), MAX_GROSS_EXPOSURE); fl = af.get(reg, .65)
    tgt = b + (mx - b) * state.confidence
    if t >= 30:
        pr = np.nanmean(R[max(0,t-30):t], axis=1)
        rv = np.nanstd(pr) * np.sqrt(252) if len(pr) > 2 else .15
        if rv > .001:
            tgt = np.clip(tgt * np.clip(params["target_vol"]/rv, params["vol_clamp_low"], params["vol_clamp_high"]), fl, mx)
    tgt = min(tgt, MAX_GROSS_EXPOSURE)

    # incumbent-adjusted alpha
    net = comb.copy()
    inc = state.pw > .001
    net[~inc] = np.where(np.isfinite(net[~inc]), net[~inc] - params["switch_pen"]/10000, np.nan)
    net[inc]  = np.where(np.isfinite(net[inc]),  net[inc] + params["inc_bonus"]/10000, np.nan)

    dtopk = {0:12, 1:14, 2:params["top_k"], 3:min(20,N), 4:min(20,N), 5:12}
    valid_mask = np.isfinite(net) & allowed
    k = min(dtopk.get(reg, params["top_k"]), int(valid_mask.sum()))
    if k < 3:
        return state.pw.copy(), {"reg": reg, "vx": vx, "confidence": state.confidence, "selected": []}

    order = np.argsort(np.where(valid_mask, -net, np.inf))
    top = list(order[:k])

    pcap = _effective_cap(params)
    w = np.zeros(N)
    rw = np.ones(len(top))
    for ri in range(len(top)):
        for lo, hi, wt in RTIERS:
            if lo <= ri < hi:
                rw[ri] = wt
                break
    rw /= max(rw.sum(), 1e-12)
    for ri, ai in enumerate(top):
        w[ai] = min(tgt * rw[ri], pcap)

    # fill to target
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
        fi = eli[av]; fr = room[av]; rs = np.sum(fr)
        if rs <= 1e-6:
            break
        w[fi] += np.minimum(fr, gap * fr / rs)
        w = np.minimum(w, pcap)
    w = np.maximum(w, 0.0)

    gross = float(np.sum(w))
    if gross > MAX_GROSS_EXPOSURE and gross > 0:
        w *= MAX_GROSS_EXPOSURE / gross

    if vx >= params["crash_vix"]:
        w *= 0.60

    if np.sum(state.pw) > 1e-8:
        band = params["ntb_base"] * (1.3 if vx > 30 else 1.1 if vx > 20 else 1.0)
        band = float(np.clip(band, params["ntb_min"], params["ntb_max"]))
        for i in range(N):
            if abs(w[i] - state.pw[i]) < band:
                if not (state.pw[i] == 0 and w[i] > 0) and not (state.pw[i] > 0 and w[i] == 0):
                    w[i] = state.pw[i]

    if reg in {0, 1}:
        clamp = params["a2b2_base"] + (params["a2b2_max"] - params["a2b2_base"]) * state.confidence
        clamp = min(clamp, MAX_GROSS_EXPOSURE)
        gr = np.sum(w)
        if gr > clamp and gr > 0:
            w *= clamp / gr
        if np.sum(state.pw) > 0:
            bd = float(np.sum(np.maximum(w - state.pw, 0)))
            if bd > params["a2b2_to"] and bd > 0:
                clip = params["a2b2_to"] / bd
                for i in range(N):
                    d_ = w[i] - state.pw[i]
                    if d_ > 0:
                        w[i] = state.pw[i] + d_ * clip

    to_cap = params["to_base"] if reg == state.prev_reg else params["to_max"]
    turn = np.sum(np.abs(w - state.pw))
    if turn > to_cap:
        w = state.pw + (w - state.pw) * (to_cap / turn)

    w = np.clip(w, 0, pcap)
    gross = float(np.sum(w))
    if gross > MAX_GROSS_EXPOSURE and gross > 0:
        w *= MAX_GROSS_EXPOSURE / gross

    sel_syms = [full_data['symbols'][i] for i in top]
    return w, {"reg": reg, "vx": vx, "confidence": state.confidence, "selected": sel_syms, "target_exposure": float(np.sum(w))}

def step_shadow_strategy(full_data, t, state: StrategyState, params, universe_name):
    reg = _regime(full_data['returns'], t, full_data.get('vix'))
    syms = select_universe_symbols(full_data, universe_name, t, reg)
    state.last_selected = syms
    w, info = build_target_weights(full_data, t, state, params, syms)
    turn = float(np.sum(np.abs(w - state.pw)))
    cost = turn * params["cost_bps"] / 10000.0
    ret = float(np.nansum(w * full_data['returns'][t])) - cost
    state.pw = w
    state.prev_reg = info["reg"]
    state.last_target = w.copy()
    state.daily_returns.append(ret)
    info["turnover"] = turn
    return ret, info

def _trailing_sr(rets):
    r = np.asarray(rets, dtype=np.float64)
    if len(r) < 20:
        return -999.0
    sd = np.std(r)
    if sd < 1e-12:
        return -999.0
    return float(np.mean(r) / sd * np.sqrt(252))

def select_top3_universes(shadow_states: Dict[str, StrategyState], params):
    rank_w = params["ranking_window"]
    min_obs = params["ranking_min_obs"]
    fallback = params["fallback_universes"]
    scored = []
    for name, st in shadow_states.items():
        hist = st.daily_returns[-rank_w:]
        if len(hist) >= min_obs:
            scored.append((name, _trailing_sr(hist)))
    if len(scored) < 3:
        return fallback[:3]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [n for n, _ in scored[:3]]

def blend_selected_targets(shadow_states, selected, n_assets):
    mats = []
    confs = []
    for name in selected:
        st = shadow_states[name]
        if st.last_target is not None:
            mats.append(st.last_target)
            confs.append(st.confidence)
    if not mats:
        return np.zeros(n_assets), 0.5
    target = np.mean(np.vstack(mats), axis=0)
    conf = float(np.mean(confs)) if confs else 0.5
    return target, conf

def execute_book(book: BookState, target, reg, vx, confidence, params):
    pcap = _effective_cap(params)
    w = np.clip(np.array(target, dtype=np.float64), 0.0, pcap)
    w = _enforce_exposure_floor_cap(w, reg, params)

    if vx >= params["crash_vix"]:
        w *= 0.60

    if np.sum(book.pw) > 1e-8:
        band = params["ntb_base"] * (1.3 if vx > 30 else 1.1 if vx > 20 else 1.0)
        band = float(np.clip(band, params["ntb_min"], params["ntb_max"]))
        for i in range(len(w)):
            if abs(w[i] - book.pw[i]) < band:
                if not (book.pw[i] == 0 and w[i] > 0) and not (book.pw[i] > 0 and w[i] == 0):
                    w[i] = book.pw[i]

    if reg in {0, 1}:
        clamp = params["a2b2_base"] + (params["a2b2_max"] - params["a2b2_base"]) * confidence
        clamp = min(clamp, MAX_GROSS_EXPOSURE)
        gr = np.sum(w)
        if gr > clamp and gr > 0:
            w *= clamp / gr
        if np.sum(book.pw) > 0:
            bd = float(np.sum(np.maximum(w - book.pw, 0)))
            if bd > params["a2b2_to"] and bd > 0:
                clip = params["a2b2_to"] / bd
                for i in range(len(w)):
                    d_ = w[i] - book.pw[i]
                    if d_ > 0:
                        w[i] = book.pw[i] + d_ * clip

    to_cap = params["to_base"] if reg == book.prev_reg else params["to_max"]
    turnover = float(np.sum(np.abs(w - book.pw)))
    if turnover > to_cap:
        w = book.pw + (w - book.pw) * (to_cap / turnover)
        turnover = float(np.sum(np.abs(w - book.pw)))

    w = np.clip(w, 0.0, pcap)
    gross = float(np.sum(w))
    if gross > MAX_GROSS_EXPOSURE and gross > 0:
        w *= MAX_GROSS_EXPOSURE / gross
        gross = float(np.sum(w))

    actual_cost = turnover * params["cost_bps"] / 10000.0
    shadow_cost = turnover * (params["cost_bps"] + _impact_proxy_bps(turnover, params)) / 10000.0
    book.pw = w
    book.prev_reg = reg
    return w, turnover, actual_cost, shadow_cost, gross

# ═══════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════

def _calc_metrics(rets, exposure=None, positions=None, actual_cost=None, shadow_cost=None):
    r = np.asarray(rets, dtype=np.float64)
    n = len(r)
    if n < 30:
        return {"SR": -999, "_sr": -999}
    tr = np.prod(1+r) - 1
    ny = n / 252
    cagr = (1+tr) ** (1/max(ny,.01)) - 1
    sd = np.std(r)
    sr = (np.mean(r)/sd) * np.sqrt(252) if sd > 1e-12 else 0.0
    cum = np.cumprod(1+r); pk = np.maximum.accumulate(cum)
    mdd = float(np.min((cum-pk)/pk))
    cal = cagr/abs(mdd) if abs(mdd) > 1e-8 else 0.0
    dn = r[r<0]
    ds = np.std(dn)*np.sqrt(252) if len(dn) > 0 else 1e-8
    sort = np.mean(r) * 252 / ds if ds > 0 else 0.0
    wr = float(np.mean(r > 0))
    out = {
        "SR": round(sr, 4),
        "CAGR": round(cagr*100, 2),
        "MDD": round(mdd*100, 2),
        "Calmar": round(cal, 2),
        "Sortino": round(sort, 2),
        "WinRate": round(wr*100, 1),
        "_sr": sr,
        "_cagr": cagr,
        "_mdd": mdd,
    }
    if exposure is not None:
        out["Exposure"] = round(float(np.mean(exposure))*100, 1)
    if positions is not None:
        out["Positions"] = round(float(np.mean(positions)), 1)
    if actual_cost is not None:
        out["AvgActualCost_bps"] = round(float(np.mean(actual_cost))*10000, 2)
    if shadow_cost is not None:
        out["AvgShadowCost_bps"] = round(float(np.mean(shadow_cost))*10000, 2)
    return out

# ═══════════════════════════════════════════════════════════════
# V5.4 ENGINE
# ═══════════════════════════════════════════════════════════════

def run_v54(full_data, params=None, start=252, end=None, verbose=True, label="V5.4-A"):
    p = {**DEFAULT_PARAMS, **(params or {})}
    R = full_data['returns']
    vix = full_data.get('vix')
    N = R.shape[1]
    if end is None:
        end = R.shape[0]
    T = end - start
    if T < 60:
        return {"SR": -999, "_sr": -999}

    candidate_universes = [u for u in p["candidate_universes"]]
    shadow_states = {u: StrategyState(pw=np.zeros(N)) for u in candidate_universes}
    book_a = BookState(pw=np.zeros(N))
    book_b = BookState(pw=np.zeros(N))

    dr = np.zeros(T)
    ex_l = np.zeros(T)
    po_l = np.zeros(T)
    ac_l = np.zeros(T)
    sc_l = np.zeros(T)
    to_l = np.zeros(T)
    selected_hist = []

    if verbose:
        print(f"\n{'='*78}")
        print(f"{label} — Aggressive Realistic")
        print(f"top_k={p['top_k']} signal_rebal={p['signal_rebal']} stagger={p['execution_stagger']} target_vol={p['target_vol']:.2f}")
        print(f"cost={p['cost_bps']}bps + shadow({p['shadow_slippage_bps']}bps + impact)  max_gross={MAX_GROSS_EXPOSURE:.2f}")
        print(f"candidate_universes={candidate_universes}")
        print(f"{'='*78}")

    for d in range(T):
        t = start + d
        reg = _regime(full_data['returns'], t, vix)
        vx = float(vix[t]) if vix is not None and t < len(vix) else 20.0

        # 1) update shadow strategies daily
        if d % p["signal_rebal"] == 0:
            for uname in candidate_universes:
                step_shadow_strategy(full_data, t, shadow_states[uname], p, uname)

        # 2) rolling top-3 selector
        selected = select_top3_universes(shadow_states, p)
        selected_hist.append(tuple(selected))
        composite_target, avg_conf = blend_selected_targets(shadow_states, selected, N)
        composite_target = _enforce_exposure_floor_cap(composite_target, reg, p)

        # 3) 2-bucket stagger execution
        actual_cost = 0.0
        shadow_cost = 0.0
        turnover = 0.0
        if d % p["execution_stagger"] == 0:
            _, turnover, actual_cost, shadow_cost, _ = execute_book(book_a, composite_target, reg, vx, avg_conf, p)
        else:
            _, turnover, actual_cost, shadow_cost, _ = execute_book(book_b, composite_target, reg, vx, avg_conf, p)

        combined = 0.5 * (book_a.pw + book_b.pw)
        ret = float(np.nansum(combined * full_data['returns'][t])) - actual_cost

        dr[d] = ret
        ex_l[d] = float(np.sum(combined))
        po_l[d] = int(np.sum(combined > .001))
        ac_l[d] = actual_cost
        sc_l[d] = shadow_cost
        to_l[d] = turnover

        if verbose and d > 0 and d % 252 == 0:
            nav = np.prod(1 + dr[:d+1]) - 1
            print(f"  Year {d//252:>2d}: Cum={nav:>7.1%} Exp={ex_l[d]:>5.0%} Pos={po_l[d]:>4.0f} Reg={RN.get(reg,'?'):>11s} Sel={selected}")

    result = _calc_metrics(dr, exposure=ex_l, positions=po_l, actual_cost=ac_l, shadow_cost=sc_l)
    result["Turnover"] = round(float(np.mean(to_l))*100, 2)
    result["SelectedTop3Counts"] = {"/".join(k): int(v) for k, v in pd.Series(selected_hist).value_counts().items()}
    result["_returns"] = dr
    result["_exposure"] = ex_l
    result["_actual_cost"] = ac_l
    result["_shadow_cost"] = sc_l
    return result

def run_v54_wf(full_data, params=None, n_folds=10, verbose=True):
    n = full_data['returns'].shape[0]
    wu = 252
    fs = (n - wu) // n_folds
    folds = []
    for fi in range(n_folds):
        s = wu + fi * fs
        e = s + fs if fi < n_folds - 1 else n
        r = run_v54(full_data, params, s, e, verbose=False, label=f"WF{fi}")
        folds.append(r)
        if verbose:
            print(f"  Fold{fi}: SR={r['SR']} CAGR={r['CAGR']}% MDD={r['MDD']}% Exp={r['Exposure']}% Turn={r['Turnover']}%")
    full = run_v54(full_data, params, wu, n, verbose=verbose, label="FULL")
    srs = [f['_sr'] for f in folds]
    exps = [f['Exposure'] for f in folds]
    turns = [f['Turnover'] for f in folds]
    if verbose:
        print(f"\nWF: SR mean={np.mean(srs):.3f} min={np.min(srs):.3f} max={np.max(srs):.3f}")
        print(f"WF: Exp mean={np.mean(exps):.1f}% Turn mean={np.mean(turns):.1f}%")
        print(f"Full: SR={full['SR']} Exp={full['Exposure']}% Turn={full['Turnover']}%")
    return {"folds": folds, "full": full,
            "wf_mean_sr": round(float(np.mean(srs)), 4),
            "wf_min_sr": round(float(np.min(srs)), 4)}

def main():
    ap = argparse.ArgumentParser(description="ARES NEXUS V5.4-A Aggressive Realistic")
    ap.add_argument("--mode", default="run", choices=["run","wf"])
    ap.add_argument("--db", default="ares_x_v11_0.db")
    ap.add_argument("--folds", type=int, default=10)
    ap.add_argument("--params-json", default=None)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    params = {}
    if args.params_json and Path(args.params_json).exists():
        params = json.loads(Path(args.params_json).read_text())

    full_data = load_full_db(args.db)

    if args.mode == "run":
        result = run_v54(full_data, params=params, verbose=True)
    else:
        result = run_v54_wf(full_data, params=params, n_folds=args.folds, verbose=True)

    if args.output:
        def sanitize(obj):
            if isinstance(obj, (np.integer,)): return int(obj)
            if isinstance(obj, (np.floating,)): return float(obj)
            if isinstance(obj, np.ndarray): return obj.tolist()
            if isinstance(obj, dict): return {k:sanitize(v) for k,v in obj.items()}
            if isinstance(obj, list): return [sanitize(v) for v in obj]
            return obj
        Path(args.output).write_text(json.dumps(sanitize(result), indent=2, default=str))
        print(f"[SAVE] {args.output}")

if __name__ == "__main__":
    main()
