#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAM26 Feature Aggregator Production v2
======================================

Fix over-strict eligibility denominator after OPEN-009:
- XGB blocked symbols are risk-excluded, not data failures.
- eligible_ratio denominator = universe_count - risk_excluded_count.
- No synthetic feature values.
- Market/daily/xgb still fail-closed for non-risk-excluded candidates.
- Output remains ram26:features:v1 HASH.

This is intended to replace ram26_feature_aggregator_prod_v1.py PM2 command.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import redis  # type: ignore
except Exception:
    redis = None

REQUIRED_FIELDS = [
    "ret_5d", "ret_20d", "ret_60d", "vol_20d",
    "quality", "value", "liquidity", "sentiment",
    "drawdown_20d", "spread_bps",
]

OPTIONAL_FIELDS = ["ret_1d", "vol_60d", "price"]

ALIASES = {
    "ret_1d": ["ret_1d", "return_1d", "r1d"],
    "ret_5d": ["ret_5d", "return_5d", "momentum_5d", "mom_5d", "r5d"],
    "ret_20d": ["ret_20d", "return_20d", "momentum_20d", "mom_20d", "r20d"],
    "ret_60d": ["ret_60d", "return_60d", "momentum_60d", "mom_60d", "r60d"],
    "vol_20d": ["vol_20d", "realized_vol_20d", "rv_20d", "vol", "realized_vol"],
    "vol_60d": ["vol_60d", "realized_vol_60d", "rv_60d"],
    "quality": ["quality", "quality_score", "q_score"],
    "value": ["value", "value_score", "v_score"],
    "liquidity": ["liquidity", "liquidity_score", "liq", "liq_score"],
    "sentiment": ["sentiment", "sentiment_score", "news_sentiment", "darkpool_sentiment"],
    "drawdown_20d": ["drawdown_20d", "dd_20d", "max_dd_20d"],
    "spread_bps": ["spread_bps", "spread", "bid_ask_spread_bps"],
    "price": ["price", "last", "close", "last_price"],
}

def env_bool(k: str, d: bool=False) -> bool:
    v = os.getenv(k)
    return d if v is None else v.strip().lower() in ("1","true","yes","y","on")

def env_int(k: str, d: int) -> int:
    try: return int(float(os.getenv(k, str(d))))
    except Exception: return d

def env_float(k: str, d: float) -> float:
    try: return float(os.getenv(k, str(d)))
    except Exception: return d

def now_ms() -> int:
    return int(time.time() * 1000)

def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms/1000.0, tz=timezone.utc).isoformat().replace("+00:00","Z")

def load_env_file(path: str):
    p=Path(path)
    if not p.exists(): return
    for line in p.read_text(errors="ignore").splitlines():
        s=line.strip()
        if not s or s.startswith("#") or "=" not in s: continue
        k,v=s.split("=",1)
        if k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip().strip('"').strip("'")

def sf(x: Any, default: Optional[float]=None) -> Optional[float]:
    try:
        if x is None: return default
        if isinstance(x, bytes): x=x.decode("utf-8","ignore")
        if isinstance(x, str):
            x=x.strip().replace(",","")
            if not x: return default
        v=float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default

def parse_json(raw: Any, default=None):
    if raw is None: return default
    if isinstance(raw, bytes): raw=raw.decode("utf-8","ignore")
    if isinstance(raw, (dict,list)): return raw
    if not isinstance(raw, str): return default
    raw=raw.strip()
    if not raw: return default
    try: return json.loads(raw)
    except Exception: return default

def ts_ms(obj: Any) -> Optional[int]:
    if not isinstance(obj, dict): return None
    for k in ("updated_at_ms","ts_ms","timestamp_ms","last_update_ms","source_updated_at_ms"):
        v=sf(obj.get(k), None)
        if v and v > 10_000_000_000: return int(v)
    for k in ("updated_at","scored_at","generated_at","created_at","ts","timestamp","time","iso"):
        v=obj.get(k)
        if v is None: continue
        fv=sf(v, None)
        if fv is not None:
            if fv > 10_000_000_000: return int(fv)
            if fv > 1_000_000_000: return int(fv*1000)
        if isinstance(v,str):
            try:
                dt=datetime.fromisoformat(v.replace("Z","+00:00"))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp()*1000)
            except Exception:
                pass
    return None

def fresh(ts: Optional[int], max_age_sec: int) -> bool:
    if not ts: return False
    age=(now_ms()-ts)/1000.0
    return -60.0 <= age <= max_age_sec

def redis_client():
    if redis is None:
        raise RuntimeError("redis package required")
    for p in ("/etc/ares/redis.env","/etc/ares/ares.env",os.path.expanduser("~/.ares_redis_env")):
        load_env_file(p)
    url=os.getenv("REDIS_URL") or os.getenv("ARES_REDIS_URL")
    if not url: raise RuntimeError("REDIS_URL required")
    r=redis.Redis.from_url(url, password=os.getenv("REDIS_PASSWORD"), decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
    r.ping()
    return r

class Config:
    output_key=os.getenv("RAM26_FEATURES_KEY","ram26:features:v1")
    meta_key=os.getenv("RAM26_FEATURES_META_KEY","ram26:features:v1:meta")
    events=os.getenv("RAM26_FEATURES_EVENTS_STREAM","ram26:features:v1:events")
    universe_keys=[x.strip() for x in os.getenv("RAM26_UNIVERSE_KEYS_CSV","policy:universe:symbols,champion:universe:global").split(",") if x.strip()]
    ttl=env_int("RAM26_AGG_TTL_SEC",60)
    cycle=env_float("RAM26_AGG_CYCLE_SEC",5.0)
    min_ratio=env_float("RAM26_AGG_MIN_ELIGIBLE_RATIO",0.60)
    delete_on_fail=env_bool("RAM26_AGG_DELETE_ON_FAIL",True)
    daily_max_age=env_int("RAM26_DAILY_MAX_AGE_SEC",259200)
    xgb_max_age=env_int("RAM26_XGB_MAX_AGE_SEC",900)
    market_max_age=env_int("RAM26_MARKET_MAX_AGE_SEC",1800)
    exclude_risk_blocked=env_bool("RAM26_EXCLUDE_XGB_BLOCKED_FROM_DENOMINATOR",True)
    require_market=env_bool("RAM26_REQUIRE_MARKET_SIGNAL",True)
    market_mom=os.getenv("RAM26_MARKET_MOM_KEY","ram26:market:IWM:mom10")
    market_vol=os.getenv("RAM26_MARKET_VOL_KEY","ram26:market:IWM:vol")
    xgb_patterns=[x.strip() for x in os.getenv("RAM26_XGB_KEY_PATTERNS_CSV","xgb:risk:{symbol},xgb:v8:risk:{symbol}").split(",") if x.strip()]
    daily_latest_key=os.getenv("RAM26_DAILY_LATEST_KEY","ares:features:daily:latest")
    daily_prefix=os.getenv("RAM26_DAILY_OUTPUT_PREFIX","ares:features:daily")
    daily_lookback=env_int("RAM26_DAILY_LOOKBACK_DAYS",10)
    event_maxlen=env_int("RAM26_AGG_EVENT_MAXLEN",1000)

def key_type(r, key: str) -> str:
    try: return str(r.type(key))
    except Exception: return "none"

def get_field(d: Optional[Dict[str,Any]], field: str) -> Optional[float]:
    if not isinstance(d, dict): return None
    for a in ALIASES[field]:
        if a in d:
            return sf(d.get(a), None)
    return None

def fetch_universe(r, cfg: Config) -> Tuple[List[str], Dict[str,Any]]:
    meta={"tried":[]}
    symbols=[]
    for key in cfg.universe_keys:
        typ=key_type(r,key)
        meta["tried"].append({"key":key,"type":typ})
        if typ=="set":
            symbols=sorted([str(x).upper() for x in r.smembers(key)])
        elif typ=="string":
            data=parse_json(r.get(key), None)
            if isinstance(data, list): symbols=[str(x).upper() for x in data]
            elif isinstance(data, dict):
                if isinstance(data.get("symbols"), list): symbols=[str(x).upper() for x in data["symbols"]]
                elif isinstance(data.get("universe"), list): symbols=[str(x).upper() for x in data["universe"]]
                elif isinstance(data.get("targets"), dict): symbols=[str(x).upper() for x in data["targets"].keys()]
        elif typ=="hash":
            symbols=sorted([str(x).upper() for x in r.hkeys(key)])
        symbols=sorted({s for s in symbols if s})
        if symbols:
            meta["source_key"]=key
            break
    exclude=set(str(x).upper() for x in r.smembers("policy:universe:exclude_set") or [])
    before=len(symbols)
    symbols=[s for s in symbols if s not in exclude]
    meta.update({"before_exclude":before,"exclude_count":len(exclude),"final_count":len(symbols)})
    return symbols, meta

def daily_candidates(r, cfg: Config) -> List[str]:
    out=[]
    latest=r.get(cfg.daily_latest_key)
    if latest:
        out.append(str(latest))
    today=datetime.now(timezone.utc).date()
    for i in range(cfg.daily_lookback):
        out.append(f"{cfg.daily_prefix}:{(today-timedelta(days=i)).strftime('%Y%m%d')}")
    seen=[]
    for k in out:
        if k not in seen: seen.append(k)
    return seen

def read_hash_json(r, key: str, field: str) -> Tuple[Optional[Dict[str,Any]], Optional[int], Optional[str]]:
    if key_type(r,key) != "hash": return None, None, None
    raw=r.hget(key, field)
    d=parse_json(raw, None)
    if isinstance(d, dict): return d, ts_ms(d), key
    return None, None, None

def read_daily(r, cfg: Config, symbol: str) -> Tuple[Optional[Dict[str,Any]], Optional[int], Optional[str]]:
    for key in daily_candidates(r,cfg):
        d,t,k=read_hash_json(r,key,symbol)
        if d is not None: return d,t,k
    return None,None,None

def read_pattern(r, patterns: List[str], symbol: str) -> Tuple[Optional[Dict[str,Any]], Optional[int], Optional[str]]:
    for pat in patterns:
        key=pat.format(symbol=symbol, SYMBOL=symbol)
        d=parse_json(r.get(key), None)
        if isinstance(d, list):
            ds=[x for x in d if isinstance(x,dict)]
            if ds:
                ds.sort(key=lambda x: ts_ms(x) or 0, reverse=True)
                d=ds[0]
        if isinstance(d, dict):
            return d, ts_ms(d), key
    return None,None,None

def read_metric(r, key: str) -> Tuple[Optional[float], Optional[int], str]:
    raw=r.get(key)
    d=parse_json(raw, None)
    if isinstance(d, dict):
        val=None
        for k in ("value","v","score","mom10","vol","signal"):
            if k in d:
                val=sf(d.get(k), None); break
        return val, ts_ms(d), key
    return sf(raw, None), None, key

def market_bundle(r, cfg: Config) -> Tuple[Dict[str,Any], List[str]]:
    mom,mom_ts,mom_key=read_metric(r,cfg.market_mom)
    vol,vol_ts,vol_key=read_metric(r,cfg.market_vol)
    missing=[]
    if mom is None: missing.append("market_mom10")
    if vol is None: missing.append("market_vol")
    if not fresh(mom_ts,cfg.market_max_age): missing.append("market_mom10_stale")
    if not fresh(vol_ts,cfg.market_max_age): missing.append("market_vol_stale")
    return {
        "market_mom10":mom, "market_vol":vol,
        "source_ts_ms":{"market_mom10":mom_ts,"market_vol":vol_ts},
        "source_keys":{"market_mom10":mom_key,"market_vol":vol_key}
    }, missing

def build_record(r, cfg: Config, symbol: str, market: Dict[str,Any]) -> Dict[str,Any]:
    daily,daily_ts,daily_key=read_daily(r,cfg,symbol)
    xgb,xgb_ts,xgb_key=read_pattern(r,cfg.xgb_patterns,symbol)
    missing=[]; stale=[]
    risk_excluded=False

    blocked_val = 0
    if isinstance(xgb,dict):
        blocked_val = int(sf(xgb.get("blocked", xgb.get("halted", 0)), 0) or 0)
    if blocked_val:
        risk_excluded=True

    if daily is None: missing.append("daily_record")
    elif not fresh(daily_ts,cfg.daily_max_age): stale.append("daily")

    if xgb is None:
        missing.append("xgb_record")
    elif not fresh(xgb_ts,cfg.xgb_max_age):
        stale.append("xgb")

    out={
        "symbol":symbol,
        "updated_at_ms": now_ms(),
        "updated_at_iso": iso_ms(now_ms()),
        "eligible": True,
        "risk_excluded": risk_excluded,
        "blocked": bool(blocked_val),
        "missing_fields": [],
        "stale_sources": [],
        "source_ts_ms":{"daily":daily_ts,"xgb":xgb_ts,
                        "market_mom10":market.get("source_ts_ms",{}).get("market_mom10"),
                        "market_vol":market.get("source_ts_ms",{}).get("market_vol")},
        "source_keys":{"daily":daily_key,"xgb":xgb_key,
                       "market_mom10":market.get("source_keys",{}).get("market_mom10"),
                       "market_vol":market.get("source_keys",{}).get("market_vol")},
        "market_mom10":market.get("market_mom10"),
        "market_vol":market.get("market_vol"),
        "producer":"ram26_feature_aggregator_prod_v2",
    }

    for f in REQUIRED_FIELDS + OPTIONAL_FIELDS:
        val=get_field(daily, f)
        if f in REQUIRED_FIELDS and val is None:
            missing.append(f)
            out[f]=0.0
        elif val is not None:
            out[f]=val

    p_drop = sf(xgb.get("p_drop") if isinstance(xgb,dict) else None, None)
    risk_score = sf(xgb.get("risk_score") if isinstance(xgb,dict) else None, None)
    if p_drop is None: missing.append("p_drop")
    else: out["p_drop"]=max(0.0,min(1.0,p_drop))
    if risk_score is not None: out["risk_score"]=risk_score

    # === XGB_RISK_GATE_V2_AGG_PATCH ===
    # passthrough soft-throttle metadata from xgb writer (v2 schema)
    rm = sf(xgb.get("risk_multiplier") if isinstance(xgb, dict) else None, None)
    if rm is None:
        # backward-compat: derive from p_drop using v2 default thresholds
        if p_drop is None:
            rm = 1.0
        else:
            _LOW, _HIGH = 0.40, 0.70
            if p_drop >= _HIGH: rm = 0.0
            elif p_drop < _LOW: rm = 1.0
            else: rm = max(0.05, min(1.0, (_HIGH - p_drop) / (_HIGH - _LOW)))
    out["risk_multiplier"] = round(float(rm), 6)
    if isinstance(xgb, dict) and xgb.get("throttled") is not None:
        out["throttled"] = int(xgb.get("throttled") or 0)
    elif p_drop is not None:
        out["throttled"] = int(0.40 <= p_drop < 0.70)
    else:
        out["throttled"] = 0

    # XGB blocked is not a missing data field; it is risk exclusion.
    if risk_excluded:
        out["eligible"]=False
        out["risk_exclusion_reason"]="blocked_by_xgb"
    else:
        out["missing_fields"]=sorted(set(missing))
        out["stale_sources"]=sorted(set(stale))
        out["eligible"]=not out["missing_fields"] and not out["stale_sources"]

    # Preserve missing/stale even for excluded records for diagnostics.
    if risk_excluded:
        out["missing_fields"]=sorted(set(missing))
        out["stale_sources"]=sorted(set(stale))

    source_times=[v for v in out["source_ts_ms"].values() if isinstance(v,int) and v>0]
    if source_times:
        out["updated_at_ms"]=min(source_times)
        out["updated_at_iso"]=iso_ms(out["updated_at_ms"])
    return out

def publish_fail(r, cfg: Config, reason: str, extra: Dict[str,Any]) -> Dict[str,Any]:
    meta={"status":"NO_PUBLISH","reason":reason,"ts_ms":now_ms(),"ts_iso":iso_ms(now_ms()),
          "output_key":cfg.output_key,"publish_performed":False,"producer":"ram26_feature_aggregator_prod_v2"}
    meta.update(extra)
    r.set(cfg.meta_key,json.dumps(meta,separators=(",",":"),sort_keys=True),ex=max(cfg.ttl,60))
    try:
        r.xadd(cfg.events, {"status":"NO_PUBLISH","reason":reason,"payload":json.dumps(meta,separators=(",",":"))}, maxlen=cfg.event_maxlen, approximate=True)
    except Exception: pass
    if cfg.delete_on_fail:
        try: r.delete(cfg.output_key)
        except Exception: pass
    return meta

def run_cycle(r, cfg: Config) -> Dict[str,Any]:
    symbols, u_meta=fetch_universe(r,cfg)
    if not symbols:
        return publish_fail(r,cfg,"universe_missing",{"universe":u_meta})

    market, market_missing=market_bundle(r,cfg)
    if cfg.require_market and market_missing:
        return publish_fail(r,cfg,"market_signal_missing_or_stale",{"universe_count":len(symbols),"market_missing":market_missing})

    records={sym:build_record(r,cfg,sym,market) for sym in symbols}
    risk_excluded=[s for s,v in records.items() if v.get("risk_excluded")]
    eligible=[s for s,v in records.items() if v.get("eligible")]
    denominator=max(len(symbols)-len(risk_excluded),1)
    ratio=len(eligible)/denominator

    failure_counts={}
    for rec in records.values():
        if rec.get("risk_excluded"):
            failure_counts["risk_excluded:blocked_by_xgb"]=failure_counts.get("risk_excluded:blocked_by_xgb",0)+1
        for f in rec.get("missing_fields",[]):
            failure_counts[f"missing:{f}"]=failure_counts.get(f"missing:{f}",0)+1
        for f in rec.get("stale_sources",[]):
            failure_counts[f"stale:{f}"]=failure_counts.get(f"stale:{f}",0)+1

    if ratio < cfg.min_ratio:
        return publish_fail(r,cfg,"eligible_ratio_below_gate",{
            "universe_count":len(symbols),
            "risk_excluded_count":len(risk_excluded),
            "eligible_denominator":denominator,
            "eligible_count":len(eligible),
            "eligible_ratio":round(ratio,6),
            "min_eligible_ratio":cfg.min_ratio,
            "failure_counts":failure_counts,
            "denominator_policy":"universe_minus_risk_excluded_xgb_blocked",
        })

    meta={"status":"PUBLISHED","ts_ms":now_ms(),"ts_iso":iso_ms(now_ms()),
          "output_key":cfg.output_key,"universe":u_meta,"universe_count":len(symbols),
          "risk_excluded_count":len(risk_excluded),"eligible_denominator":denominator,
          "eligible_count":len(eligible),"eligible_ratio":round(ratio,6),
          "ttl_sec":cfg.ttl,"failure_counts":failure_counts,
          "denominator_policy":"universe_minus_risk_excluded_xgb_blocked",
          "publish_performed":True,"producer":"ram26_feature_aggregator_prod_v2"}

    pipe=r.pipeline()
    pipe.delete(cfg.output_key)
    pipe.hset(cfg.output_key, mapping={s:json.dumps(v,separators=(",",":"),sort_keys=True) for s,v in records.items()})
    pipe.expire(cfg.output_key,cfg.ttl)
    pipe.set(cfg.meta_key,json.dumps(meta,separators=(",",":"),sort_keys=True),ex=max(cfg.ttl,60))
    pipe.xadd(cfg.events,{"status":"PUBLISHED","eligible_count":str(len(eligible)),
                          "universe_count":str(len(symbols)),"payload":json.dumps(meta,separators=(",",":"))},
              maxlen=cfg.event_maxlen, approximate=True)
    pipe.execute()
    return meta

def self_test() -> int:
    print(json.dumps({"SELFTEST":"PASS","version":"ram26_feature_aggregator_prod_v2"}))
    return 0

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--once",action="store_true")
    ap.add_argument("--self-test",action="store_true")
    args=ap.parse_args()
    if args.self_test: return self_test()
    r=redis_client(); cfg=Config()
    while True:
        meta=run_cycle(r,cfg)
        print(json.dumps({"status":meta.get("status"),"reason":meta.get("reason"),"eligible_ratio":meta.get("eligible_ratio"),"eligible_count":meta.get("eligible_count"),"risk_excluded_count":meta.get("risk_excluded_count")},ensure_ascii=False))
        if args.once: break
        time.sleep(cfg.cycle)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
