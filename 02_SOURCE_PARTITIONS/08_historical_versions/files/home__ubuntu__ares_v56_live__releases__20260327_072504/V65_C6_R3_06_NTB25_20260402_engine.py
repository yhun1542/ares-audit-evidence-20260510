# V65_UPGRADE variant: C6_TC6x (promoted from C6_gpt_c5plus)
# Release: V65_C6_TC6x_20260331
#!/usr/bin/env python3
"""
ARES NEXUS V5.3 — HOTFIX
===========================
V5.1 Phase 1 버그 수정:

[HOTFIX-1] HARD EXPOSURE CAP = 1.00 (절대 레버리지 불가)
  원인: vol_clamp_high=1.80 + ace_max=0.98 → exposure 최대 176%
  수정: 모든 비중 합산 후 1.0 초과 시 강제 정규화

[HOTFIX-2] per_cap 자동 조정
  원인: 89자산 × 0.045 = 최대 400% 가능
  수정: per_cap = min(설정값, 1.0 / top_k * 1.2)
        top_k=12이면 per_cap = min(0.045, 0.10) = 0.045 (OK)
        top_k=20이면 per_cap = min(0.045, 0.06) = 0.045 (OK)
        근본 방어는 HARD CAP에서 수행

[HOTFIX-3] DB 심볼 매칭 진단
  원인: LIVE_UNI 60종목 중 28개만 매칭 → 필터 무력화
  수정: 매칭률 50% 미만이면 경고 + 매칭된 것만 사용 (28개라도)
        + 매칭 안 된 종목 목록 출력

[HOTFIX-4] target_exposure 절대 상한
  원인: ace_max[BULL]=0.98 × vol_clamp=1.80 = 1.76
  수정: target_exposure = min(계산값, MAX_GROSS_EXPOSURE)
        MAX_GROSS_EXPOSURE = 0.98 (절대 상한)
"""
import numpy as np
import pandas as pd
import sqlite3, time, argparse, json, os, sys
from pathlib import Path
from typing import Dict, List
from collections import deque, Counter

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
SMIX={
    0:{"TREND":.05,"TF":0,"DLV":.20,"GNN":0,"MRC":.05,"CA":.10,"VC":0,"MC":0,"CASH":.60},
    1:{"TREND":.05,"TF":0,"DLV":.20,"GNN":.05,"MRC":.20,"CA":.10,"VC":.05,"MC":.05,"CASH":.30},
    2:{"TREND":.05,"TF":0,"DLV":0,"GNN":.10,"MRC":.35,"CA":.08,"VC":.10,"MC":.07,"CASH":0},
    3:{"TREND":.10,"TF":0,"DLV":0,"GNN":.10,"MRC":.20,"CA":.05,"VC":.12,"MC":.08,"CASH":0},
    4:{"TREND":.15,"TF":.05,"DLV":0,"GNN":.10,"MRC":.15,"CA":.05,"VC":.10,"MC":.05,"CASH":0},
    5:{"TREND":.05,"TF":0,"DLV":.30,"GNN":0,"MRC":.05,"CA":.10,"VC":0,"MC":.10,"CASH":.30},
}
RTIERS=[(0,4,1.20),(4,8,1.00),(8,12,0.85),(12,16,0.70),(16,20,0.60)]

# [HOTFIX-1] HARD EXPOSURE CAP — 절대 초과 불가
MAX_GROSS_EXPOSURE = 0.98  # 하드 노출 캡 0.98 — C6_gpt_c5plus [CONTRACT_FIXED]

DEFAULT_PARAMS = {
    # ── TC6x Champion Parameters (promoted 2026-03-31) ──
    "top_k":20,"rebal":1,"target_vol":0.13,"per_cap":0.060,"cost_bps":13.0,
    "ace_base":{0:0.54,1:0.64,2:0.93,3:0.97,4:0.97,5:0.49},
    "ace_max":{0:0.65,1:0.75,2:0.95,3:0.98,4:0.98,5:0.60},
    "ace_floor":{0:.20,1:.30,2:.60,3:.70,4:.70,5:.15},
    "vol_clamp_low":0.60,"vol_clamp_high":1.55,
    "switch_pen":3.0,"inc_bonus":1.5,
    "ntb_base": 0.025,"ntb_min":0.003,"ntb_max":0.040,"ntb_sig":1.5,
    "to_base":0.259,"to_max":0.60,
    "a2b2_base":0.55,"a2b2_max":0.85,"a2b2_to":0.08,
    "crash_vix":30.0,
}


# ═══════════════════════════════════════════════════════════════
# CONTRACT METADATA — C6_gpt_c5plus [AUDIT_COMPLIANT]
# ═══════════════════════════════════════════════════════════════
CONTRACT_METADATA = {
    "variant": "C6_TC6x",
    "version": "V65_C6_TC6x",
    "MAX_GROSS_EXPOSURE": 0.98,
    "ace_max_BULL": 0.98,
    "ace_max_STRONG_BULL": 0.98,
    "target_vol": 0.13,
    "vol_clamp_high": 1.55,
    "per_cap": 0.060,
    "universe": "LIVE_UNI_60",
    "db_filter": "universe_filter=True",
    "expected_n_assets": 54,
    "expected_date_start": "2005-01-04",
    "expected_date_end": "2026-03-26",
    "expected_total_days": 5340,
    "contract_fixed_at": "2026-03-31",
    "promoted_from": "V65_C6_gpt_c5plus",
    "promotion_date": "2026-03-31",
    "promotion_candidate_id": "V65_C6_TC6x_20260331",
    "fixes": [
        "MAX_GROSS_EXPOSURE hardcoded to 0.98 (was runtime override)",
        "ace_max[BULL]=0.98 hardcoded (was 1.0)",
        "ace_max[STRONG_BULL]=0.98 hardcoded (was 1.0)",
        "CONTRACT_METADATA block added for audit compliance",
        "n_assets and date_start recorded in metadata",
    ]
}
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
        if c>.15: return 4
        if c>.03: return 3
    return 2

# ═══════════════════════════════════════════════════════════════
# [HOTFIX-3] DATA LOADER WITH DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════

_CACHE = {}

def load_db(db_path, universe_filter=True, min_match=20):
    """
    DB 로드 + 심볼 매칭 진단.
    
    [HOTFIX-3] 매칭률이 낮아도 매칭된 것만 사용.
    28개라도 20개 이상이면 진행.
    """
    ck = f"{db_path}_{universe_filter}"
    if ck in _CACHE: return _CACHE[ck]

    conn = sqlite3.connect(db_path, timeout=60)
    df = pd.read_sql_query(
        "SELECT date,symbol,open,high,low,close,volume FROM daily_ohlcv "
        "WHERE symbol IN (SELECT symbol FROM daily_ohlcv GROUP BY symbol HAVING COUNT(*)>1000) "
        "ORDER BY date,symbol", conn)
    df['date'] = pd.to_datetime(df['date'], format='mixed')
    df = df.drop_duplicates(subset=['date','symbol'], keep='last')
    df['dvol'] = df['close'] * df['volume']

    pdf = df.pivot(index='date', columns='symbol', values='close').sort_index()
    cov = pdf.notna().sum(axis=1)
    for dt, c in cov.items():
        if c >= 35: pdf = pdf.loc[dt:]; break

    dr = pdf.notna().mean()
    all_available = dr[dr >= 0.5].index.tolist()

    # ── [HOTFIX-3] 심볼 매칭 진단 ──
    if universe_filter:
        matched = [s for s in LIVE_UNI if s in all_available]
        unmatched = [s for s in LIVE_UNI if s not in all_available]

        print(f"\n[DIAG] DB symbols available: {len(all_available)}")
        print(f"[DIAG] LIVE_UNI requested:   {len(LIVE_UNI)}")
        print(f"[DIAG] Matched:              {len(matched)} ({len(matched)/len(LIVE_UNI)*100:.0f}%)")

        if unmatched:
            print(f"[DIAG] MISSING from DB: {', '.join(sorted(unmatched))}")

        if len(matched) >= min_match:
            keep = matched
            print(f"[DATA] Using {len(keep)} matched symbols")
        else:
            print(f"[WARN] Only {len(matched)} matched (< {min_match} minimum)")
            print(f"[WARN] Falling back to all {len(all_available)} symbols")
            print(f"[WARN] ⚠️ Results will differ from SSOT 48-asset baseline")
            keep = all_available
    else:
        keep = all_available

    pdf = pdf[keep].ffill(limit=3)
    rdf = pdf.pct_change()
    idx = rdf.index[1:]

    d = {
        'prices': pdf.values[1:].astype(np.float64),
        'returns': np.nan_to_num(rdf.values[1:].astype(np.float64), nan=0.0),
        'symbols': keep, 'dates': idx, 'n_assets': len(keep),
    }
    for col, src in [('high','high'),('low','low'),('open','open'),('dvol','dvol')]:
        try:
            pv = df.pivot(index='date', columns='symbol', values=src).sort_index()
            pv = pv[keep].ffill(limit=3)
            d[col] = np.nan_to_num(pv.reindex(idx).values.astype(np.float64), nan=0.0)
        except:
            d[col] = d['prices'].copy()
    try:
        vdf = pd.read_sql_query("SELECT date,close as vix FROM vix ORDER BY date", conn)
        vdf['date'] = pd.to_datetime(vdf['date'], format='mixed')
        vdf = vdf.drop_duplicates('date', keep='last').set_index('date')
        d['vix'] = vdf['vix'].reindex(idx).ffill().fillna(20).values.astype(np.float64)
    except:
        d['vix'] = np.full(d['returns'].shape[0], 20.0)
    conn.close()

    n_d, n_a = d['returns'].shape
    print(f"[DATA] Final: {n_d}d × {n_a}a ({d['dates'][0].date()}~{d['dates'][-1].date()})")
    _CACHE[ck] = d
    return d


# ═══════════════════════════════════════════════════════════════
# FACTORS (동일)
# ═══════════════════════════════════════════════════════════════

def compute_sleeves(P,R,H,L,O,DV,t,N,regime):
    trend=(P[t-21]/(P[t-252]+1e-12)-1) if t>=252 else np.full(N,np.nan)
    tf=(P[t-21]/(P[t-63]+1e-12)-1) if t>=63 else np.full(N,np.nan)
    sw,lw=min(21,t),min(63,t)
    if t>=sw+5:
        rs=np.nanmean(R[t-sw:t],axis=0)*252; rl=np.nanmean(R[t-lw:t],axis=0)*252
        vol=np.nanstd(R[t-sw:t],axis=0)*np.sqrt(252)+1e-8
        dlv=.6*(rs/vol)+.4*(rl-rs)
    else: dlv=np.full(N,np.nan)
    mr5=-np.nansum(R[t-5:t],axis=0) if t>=5 else np.full(N,np.nan)
    if t>=1:
        rng=H[t]-L[t]; rng_s=np.where(rng>1e-8,rng,np.nan)
        clv=np.clip((2*P[t]-H[t]-L[t])/(rng_s+1e-12),-1,1)
    else: clv=np.full(N,np.nan)
    mrc=.45*_zs(mr5)+.45*_zs(clv)+.10*np.zeros(N)
    gnn=np.full(N,np.nan)
    if t>=70:
        mkt=np.nanmean(R[max(0,t-60):t],axis=1)
        corrs=np.zeros(N)
        for i in range(N):
            v=np.isfinite(R[max(0,t-60):t,i])
            if v.sum()>20: corrs[i]=np.corrcoef(R[max(0,t-60):t,i][v],mkt[v])[0,1]
        ami_r=np.zeros(N)
        for j in range(N):
            s=0;c=0
            for k in range(20):
                if DV[t-20+k,j]>1e3: s+=abs(R[t-20+k,j])/DV[t-20+k,j];c+=1
            if c>0: ami_r[j]=-s/c
        gnn=.5*_zs(corrs)+.5*_zs(ami_r)
    ca=np.full(N,np.nan)
    if t>=60:
        v=np.nanstd(R[max(0,t-60):t],axis=0)*np.sqrt(252)
        vl=np.isfinite(v)&(v>0)
        if vl.sum()>=5: ca=np.where(vl,-_zs(v),np.nan)
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
    if regime==0: scores['DLV']*=1.5; scores['GNN']=np.full(N,np.nan); scores['MRC']*=.5
    elif regime==1: scores['DLV']*=1.3; scores['GNN']=np.full(N,np.nan); scores['MRC']*=1.2
    elif regime==2: scores['MRC']*=1.3; scores['TREND']*=.7
    elif regime==3: scores['TF']*=1.2
    elif regime==4: scores['TREND']*=1.3; scores['TF']*=1.3; scores['DLV']*=.5
    elif regime==5: scores['TREND']*=.1; scores['TF']*=.1; scores['DLV']*=2.0; scores['GNN']=np.full(N,np.nan); scores['MRC']*=.3
    mkt_vol=np.nanstd(np.nanmean(R[max(0,t-20):t],axis=1)) if t>=20 else .02
    if regime<2 or mkt_vol>.030: scores['TF']=np.full(N,np.nan)
    return scores


# ═══════════════════════════════════════════════════════════════
# ENGINE WITH HOTFIXES
# ═══════════════════════════════════════════════════════════════

def run_single(data, params=None, start=252, end=None, verbose=False):
    p = {**DEFAULT_PARAMS, **(params or {})}
    P,R = data['prices'], data['returns']
    H,L,O,DV = data['high'], data['low'], data['open'], data['dvol']
    vix = data.get('vix'); N = R.shape[1]
    if end is None: end = R.shape[0]
    T = end - start
    if T < 60: return {"_sr": -999, "SR": -999}

    # [HOTFIX-2] per_cap 자동 상한
    effective_cap = min(p["per_cap"], MAX_GROSS_EXPOSURE / max(p["top_k"], 1) * 1.2)
    if effective_cap != p["per_cap"]:
        if verbose: print(f"[HOTFIX-2] per_cap adjusted: {p['per_cap']}→{effective_cap:.4f} (N={N})")
    pcap = effective_cap

    dtopk = {0:12, 1:14, 2:p["top_k"], 3:min(20,N), 4:min(20,N), 5:12}

    dr = np.zeros(T); to_l = np.zeros(T); co_l = np.zeros(T)
    ex_l = np.zeros(T); po_l = np.zeros(T)
    pw = np.zeros(N); prev_reg = 2; confidence = 0.5
    sig_hist = deque(maxlen=40); ret_hist = deque(maxlen=40)
    n_hard_caps = 0  # HOTFIX-1 발동 횟수 추적

    for d in range(T):
        t = start + d
        reg = _regime(R, t, vix)
        vx = float(vix[t]) if vix is not None and t < len(vix) else 20.0
        tc = 0.0

        if d % p["rebal"] == 0:
            scores = compute_sleeves(P, R, H, L, O, DV, t, N, reg)
            mix = SMIX.get(reg, SMIX[2])
            comb = np.zeros(N); tw = 0.0
            for sn, sw in mix.items():
                if sn == "CASH" or sw < .001 or sn not in scores: continue
                s = scores[sn]
                if s is None or np.all(np.isnan(s)): continue
                v = np.isfinite(s)
                if v.sum() < 5: continue
                m, sd = np.nanmean(s[v]), np.nanstd(s[v])
                if sd < 1e-8: continue
                comb += sw * np.where(v, (s-m)/(sd+1e-8), 0.0); tw += sw
            if tw < .01:
                dr[d] = float(np.nansum(pw * R[t]))
                ex_l[d] = np.sum(pw); po_l[d] = np.sum(pw > .001)
                continue
            comb /= tw

            # Alpha transform
            rv20 = np.nanstd(np.nanmean(R[max(0,t-20):t], axis=1)) if t >= 20 else .015
            ms = 0
            if vx > 30: ms += 2
            elif vx > 20: ms += 1
            if rv20 > .03: ms += 2
            elif rv20 > .02: ms += 1
            comb *= [1, .96, .96, .90, .90, .82, .82][min(ms, 6)]

            # Confidence (FIX-1: R[t-1])
            if t >= 1:
                ret_hist.append(R[t-1].copy())
                sig_hist.append(comb.copy())
            if len(sig_hist) >= 10:
                ics = []
                for i in range(min(15, len(sig_hist)-1)):
                    sg = sig_hist[-(i+2)]; rt = ret_hist[-(i+1)]
                    vl = np.isfinite(sg) & np.isfinite(rt)
                    if vl.sum() >= 10:
                        ic = np.corrcoef(sg[vl], rt[vl])[0, 1]
                        if np.isfinite(ic): ics.append(ic)
                confidence = np.clip(np.mean(ics)/.05 if ics else 0, 0, 1) * 0.5 + 0.25

            # ACE Exposure
            ab = p["ace_base"]; am = p["ace_max"]; af = p["ace_floor"]
            b = ab.get(reg, .85); mx = am.get(reg, .95); fl = af.get(reg, .65)
            tgt = b + (mx - b) * confidence

            # Vol targeting
            if t >= 30:
                pr = np.nanmean(R[max(0,t-30):t], axis=1)
                rv = np.nanstd(pr) * np.sqrt(252) if len(pr) > 2 else .15
                if rv > .001:
                    tgt = np.clip(tgt * np.clip(p["target_vol"]/rv, p["vol_clamp_low"], p["vol_clamp_high"]), fl, mx)

            # [HOTFIX-4] target_exposure 절대 상한
            tgt = min(tgt, MAX_GROSS_EXPOSURE)

            # Net alpha
            net = comb.copy(); inc = pw > .001
            net[~inc] -= p["switch_pen"] / 10000
            net[inc] += p["inc_bonus"] / 10000

            k = min(dtopk.get(reg, 16), int(np.isfinite(net).sum()))
            if k < 3:
                dr[d] = float(np.nansum(pw * R[t]))
                ex_l[d] = np.sum(pw); po_l[d] = np.sum(pw > .001)
                continue

            top = list(np.argsort(-net)[:k])

            # Rank-tiered weights
            w = np.zeros(N)
            rw = np.ones(len(top))
            for ri in range(len(top)):
                for lo, hi, wt in RTIERS:
                    if lo <= ri < hi: rw[ri] = wt; break
            rw /= rw.sum()
            for ri, ai in enumerate(top):
                w[ai] = min(tgt * rw[ri], pcap)

            # Fill to target
            w = np.clip(w, 0, pcap)
            ct = np.sum(w)
            if ct > 1e-8:
                w *= min(tgt / ct, 2.0)
            w = np.minimum(w, pcap)
            eli = np.array(top, dtype=int)
            for _ in range(10):
                gap = tgt - np.sum(w)
                if gap <= 1e-6: break
                room = np.maximum(pcap - w[eli], 0.0)
                av = room > 1e-6
                if not np.any(av): break
                fi = eli[av]; fr = room[av]; rs = np.sum(fr)
                if rs <= 1e-6: break
                w[fi] += np.minimum(fr, gap * fr / rs)
                w = np.minimum(w, pcap)
            w = np.maximum(w, 0.0)

            # ══════════════════════════════════════════════
            # [HOTFIX-1] HARD EXPOSURE CAP — 절대 방어선
            # ══════════════════════════════════════════════
            gross = float(np.sum(w))
            if gross > MAX_GROSS_EXPOSURE:
                w *= MAX_GROSS_EXPOSURE / gross
                n_hard_caps += 1
            # ══════════════════════════════════════════════

            # Crash guard
            if vx >= p["crash_vix"]:
                w *= 0.60

            # NTB
            if np.sum(pw) > 1e-8:
                band = p["ntb_base"] * (1.3 if vx > 30 else 1.1 if vx > 20 else 1.0)
                for i in range(N):
                    if abs(w[i] - pw[i]) < band:
                        if not (pw[i] == 0 and w[i] > 0) and not (pw[i] > 0 and w[i] == 0):
                            w[i] = pw[i]

            # A2B2
            if reg in {0, 1}:
                clamp = p["a2b2_base"] + (p["a2b2_max"] - p["a2b2_base"]) * confidence
                clamp = min(clamp, MAX_GROSS_EXPOSURE)  # HOTFIX: clamp도 상한 적용
                gr = np.sum(w)
                if gr > clamp and gr > 0:
                    w *= clamp / gr
                if np.sum(pw) > 0:
                    bd = sum(max(0, w[i] - pw[i]) for i in range(N))
                    if bd > p["a2b2_to"] and bd > 0:
                        clip = p["a2b2_to"] / bd
                        for i in range(N):
                            d_ = w[i] - pw[i]
                            if d_ > 0: w[i] = pw[i] + d_ * clip

            # Turnover cap
            to_cap = p["to_base"] if reg == prev_reg else p["to_max"]
            t_ = np.sum(np.abs(w - pw))
            if t_ > to_cap:
                w = pw + (w - pw) * (to_cap / t_)

            w = np.clip(w, 0, pcap)

            # [HOTFIX-1] 최종 확인 — 이 시점에서 절대 1.0 초과 불가
            final_gross = float(np.sum(w))
            if final_gross > MAX_GROSS_EXPOSURE:
                w *= MAX_GROSS_EXPOSURE / final_gross
                n_hard_caps += 1

            to = float(np.sum(np.abs(w - pw)))
            tc = to * p["cost_bps"] / 10000
            pw = w; prev_reg = reg; to_l[d] = to

        co_l[d] = tc
        dr[d] = float(np.nansum(pw * R[t])) - tc
        ex_l[d] = float(np.sum(pw))
        po_l[d] = int(np.sum(pw > .001))

        if verbose and d > 0 and d % 504 == 0:
            nav = np.prod(1 + dr[:d+1])
            print(f"  Yr{d//252:>2d}: Cum={nav-1:>8.1%} Exp={ex_l[d]:>5.0%} "
                  f"Pos={po_l[d]:.0f} {RN.get(reg,'?')}")

    # Metrics
    r = dr; n = len(r)
    if n < 30: return {"_sr": -999, "SR": -999}
    tr = np.prod(1+r) - 1; ny = n / 252
    cagr = (1+tr) ** (1/max(ny, .01)) - 1
    sr = (np.mean(r) / np.std(r)) * np.sqrt(252) if np.std(r) > 0 else 0
    cum = np.cumprod(1+r); pk = np.maximum.accumulate(cum)
    mdd = float(np.min((cum - pk) / pk))
    cal = cagr / abs(mdd) if abs(mdd) > 1e-8 else 0
    dn = r[r < 0]; ds = np.std(dn) * np.sqrt(252) if len(dn) > 0 else 1e-8
    sort = np.mean(r) * 252 / ds if ds > 0 else 0
    wr = float(np.mean(r > 0))
    avg_exp = float(np.mean(ex_l))

    result = {
        "SR": round(sr, 4), "CAGR": round(cagr*100, 2), "MDD": round(mdd*100, 2),
        "Calmar": round(cal, 2), "Sortino": round(sort, 2), "WinRate": round(wr*100, 1),
        "Exposure": round(avg_exp*100, 1), "Positions": round(float(np.mean(po_l)), 1),
        "Assets": N, "HardCapHits": n_hard_caps,
        "_sr": sr, "_cagr": cagr, "_mdd": mdd,
        "_daily_returns": dr.tolist(),
        "_daily_turnover": to_l.tolist(),
        "_daily_cost": co_l.tolist(),
        "_daily_exposure": ex_l.tolist(),
        "_daily_positions": po_l.tolist(),
        "_n_assets": data['n_assets'],
        "_date_start": str(data['dates'][0]) if len(data['dates']) > 0 else None,
        "_date_end": str(data['dates'][-1]) if len(data['dates']) > 0 else None,
        "_contract_metadata": CONTRACT_METADATA,
    }

    if verbose:
        print(f"\n{'='*60}")
        print(f"SR={result['SR']} CAGR={result['CAGR']}% MDD={result['MDD']}% "
              f"Cal={result['Calmar']} Exp={result['Exposure']}%")
        print(f"Assets={N} Positions={result['Positions']} HardCapHits={n_hard_caps}")
        if n_hard_caps > 0:
            print(f"[HOTFIX-1] Hard exposure cap triggered {n_hard_caps} times")
        if avg_exp > 1.0:
            print(f"[ERROR] EXPOSURE STILL >100%! avg={avg_exp:.2%} — SOMETHING IS WRONG")
        elif avg_exp > 0.95:
            print(f"[WARN] Exposure very high ({avg_exp:.0%}) — near maximum")
        print(f"{'='*60}")

    return result


def run_wf(data, params=None, n_folds=10, verbose=True):
    n = data['returns'].shape[0]; wu = 252; fs = (n - wu) // n_folds
    folds = []
    for fi in range(n_folds):
        s = wu + fi * fs; e = s + fs if fi < n_folds - 1 else n
        r = run_single(data, params, s, e, verbose=False)
        folds.append(r)
        if verbose:
            print(f"  Fold{fi}: SR={r['SR']} CAGR={r['CAGR']}% MDD={r['MDD']}% Exp={r['Exposure']}%")
    full = run_single(data, params, wu, n, verbose=verbose)
    srs = [f['_sr'] for f in folds]
    exps = [f['Exposure'] for f in folds]
    if verbose:
        print(f"\nWF: SR mean={np.mean(srs):.3f} med={np.median(srs):.3f} "
              f"min={np.min(srs):.3f} max={np.max(srs):.3f}")
        print(f"WF: Exp mean={np.mean(exps):.0f}% max={np.max(exps):.0f}%")
        if np.max(exps) > 100:
            print(f"[ERROR] LEVERAGE DETECTED IN FOLD! max_exp={np.max(exps):.1f}%")
        print(f"Full: SR={full['SR']} Exp={full['Exposure']}%")
    return {"folds": folds, "full": full,
            "wf_mean_sr": round(np.mean(srs), 4), "wf_min_sr": round(np.min(srs), 4)}


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description="ARES NEXUS V5.3 HOTFIX")
    ap.add_argument('--mode', default='run', choices=['run', 'wf', 'diag'])
    ap.add_argument('--db', default='ares_x_v11_0.db')
    ap.add_argument('--folds', type=int, default=10)
    ap.add_argument('--no-filter', action='store_true', help="Skip universe filter (use all DB symbols)")
    args = ap.parse_args()

    if args.mode == 'diag':
        # DB 진단만 실행
        print("=== DB DIAGNOSTICS ===")
        data = load_db(args.db, universe_filter=not args.no_filter)
        print(f"\nSymbols in DB: {data['symbols'][:20]}...")
        print(f"Total: {data['n_assets']}")
        print(f"Max possible exposure: {data['n_assets'] * DEFAULT_PARAMS['per_cap'] * 100:.0f}%")
        print(f"MAX_GROSS_EXPOSURE cap: {MAX_GROSS_EXPOSURE*100:.0f}%")

    elif args.mode == 'run':
        data = load_db(args.db, universe_filter=not args.no_filter)
        run_single(data, verbose=True)

    elif args.mode == 'wf':
        data = load_db(args.db, universe_filter=not args.no_filter)
        run_wf(data, n_folds=args.folds)
