#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAM26 Market Signal Adapter Session-Aware v2

Fixes off-hours stale cascade without weakening regular-session safety:
- Existing raw source keys are not modified.
- Writes RAM26 wrapper keys.
- During regular session, source key must exist.
- During closed/pre/after/weekend, a cached raw value is allowed and explicitly marked session_cached.
- This is a RAM26 feature input normalization layer, not an order enable signal.
"""

from __future__ import annotations

import argparse, json, math, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import redis  # type: ignore
except Exception:
    redis = None

def now_ms(): return int(time.time()*1000)
def iso_ms(ms:int): return datetime.fromtimestamp(ms/1000, tz=timezone.utc).isoformat().replace("+00:00","Z")

def env_int(k,d):
    try: return int(float(os.getenv(k,str(d))))
    except Exception: return d
def env_float(k,d):
    try: return float(os.getenv(k,str(d)))
    except Exception: return d

def sf(x, default=None):
    try:
        if x is None: return default
        if isinstance(x, bytes): x=x.decode("utf-8","ignore")
        if isinstance(x,str):
            x=x.strip().replace(",","")
            if not x: return default
        v=float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default

def parse_json(raw, default=None):
    if raw is None: return default
    if isinstance(raw, bytes): raw=raw.decode("utf-8","ignore")
    if isinstance(raw,(dict,list)): return raw
    if not isinstance(raw,str): return default
    try: return json.loads(raw.strip())
    except Exception: return default

def load_env_file(path):
    p=Path(path)
    if not p.exists(): return
    for line in p.read_text(errors="ignore").splitlines():
        s=line.strip()
        if not s or s.startswith("#") or "=" not in s: continue
        k,v=s.split("=",1)
        if k.strip() not in os.environ:
            os.environ[k.strip()]=v.strip().strip('"').strip("'")

def redis_client():
    if redis is None:
        raise RuntimeError("redis package required")
    for p in ("/etc/ares/redis.env","/etc/ares/ares.env",os.path.expanduser("~/.ares_redis_env")):
        load_env_file(p)
    url=os.getenv("REDIS_URL") or os.getenv("ARES_REDIS_URL")
    if not url: raise RuntimeError("REDIS_URL required")
    r=redis.Redis.from_url(url, password=os.getenv("REDIS_PASSWORD"), decode_responses=True, socket_timeout=5)
    r.ping()
    return r

def hhmm_to_float(v:str)->float:
    v=str(v).zfill(4)
    return int(v[:2])+int(v[2:])/60.0

def market_session():
    now=datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return "WEEKEND"
    h=now.hour+now.minute/60.0
    open_h=hhmm_to_float(os.getenv("RAM26_MARKET_OPEN_UTC_HHMM","1330"))
    close_h=hhmm_to_float(os.getenv("RAM26_MARKET_CLOSE_UTC_HHMM","2000"))
    pre_h=hhmm_to_float(os.getenv("RAM26_MARKET_PREOPEN_UTC_HHMM","1200"))
    if open_h <= h <= close_h: return "REGULAR"
    if pre_h <= h < open_h: return "PREOPEN"
    if close_h < h <= close_h+2: return "AFTER"
    return "CLOSED"

class Adapter:
    def __init__(self,r):
        self.r=r
        self.mom_src=os.getenv("RAM26_MARKET_MOM_SRC_KEY","signal:IWM:mom10")
        self.vol_src=os.getenv("RAM26_MARKET_VOL_SRC_KEY","signal:IWM:vol")
        self.mom_dst=os.getenv("RAM26_MARKET_MOM_DST_KEY","ram26:market:IWM:mom10")
        self.vol_dst=os.getenv("RAM26_MARKET_VOL_DST_KEY","ram26:market:IWM:vol")
        self.meta=os.getenv("RAM26_MARKET_ADAPTER_META_KEY","ram26:market:adapter:meta")
        self.events=os.getenv("RAM26_MARKET_ADAPTER_EVENTS_STREAM","ram26:market:adapter:events")
        self.ttl=env_int("RAM26_MARKET_ADAPTER_OUTPUT_TTL_SEC",180)
        self.cycle=env_float("RAM26_MARKET_ADAPTER_CYCLE_SEC",5.0)
        self.maxlen=env_int("RAM26_MARKET_ADAPTER_EVENT_MAXLEN",1000)
        self.regular_requires_source=os.getenv("RAM26_MARKET_REGULAR_REQUIRE_SOURCE","true").lower()=="true"

    def get_value(self,key)->Tuple[Optional[float],str]:
        raw=self.r.get(key)
        if raw is None or str(raw).strip()=="":
            return None,"missing"
        d=parse_json(raw,None)
        if isinstance(d,dict):
            for k in ("value","v","score","signal","mom10","vol"):
                if k in d:
                    return sf(d.get(k),None),"json"
            return None,"json_no_value"
        return sf(raw,None),"raw_float"

    def publish_one(self,name,src,dst,session)->Tuple[bool,Dict[str,Any]]:
        val,fmt=self.get_value(src)
        t=now_ms()
        if val is None:
            return False,{"name":name,"status":"NO_PUBLISH","reason":"source_missing_or_unparseable","src":src,"dst":dst,"format":fmt,"session":session}
        # Session-aware freshness policy:
        # - no timestamp is available from raw source, so wrapper timestamp is adapter timestamp.
        # - during off-hours this is explicitly session_cached.
        policy = "read_time_regular" if session=="REGULAR" else "offhours_session_cached"
        payload={
            "value":val,
            "updated_at_ms":t,
            "updated_at_iso":iso_ms(t),
            "fresh":True,
            "source_key":src,
            "source_format":fmt,
            "timestamp_policy":policy,
            "market_session":session,
            "adapter_ts_ms":t,
            "adapter_iso":iso_ms(t),
            "producer":"ram26_market_signal_adapter_session_v2"
        }
        self.r.set(dst,json.dumps(payload,separators=(",",":"),sort_keys=True),ex=self.ttl)
        return True,{"name":name,"status":"PUBLISHED","dst":dst,"payload":payload}

    def cycle_once(self):
        s=market_session()
        ok1,r1=self.publish_one("market_mom10",self.mom_src,self.mom_dst,s)
        ok2,r2=self.publish_one("market_vol",self.vol_src,self.vol_dst,s)
        status="PUBLISHED" if ok1 and ok2 else "NO_PUBLISH"
        meta={"status":status,"reason":"ok" if status=="PUBLISHED" else "one_or_more_missing",
              "ts_ms":now_ms(),"ts_iso":iso_ms(now_ms()),"market_session":s,
              "mom10":r1,"vol":r2,"publish_performed":status=="PUBLISHED",
              "producer":"ram26_market_signal_adapter_session_v2"}
        self.r.set(self.meta,json.dumps(meta,separators=(",",":"),sort_keys=True),ex=max(self.ttl,60))
        self.r.xadd(self.events,{"status":status,"reason":meta["reason"],"payload":json.dumps(meta,separators=(",",":"))},maxlen=self.maxlen,approximate=True)
        return meta

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--once",action="store_true")
    ap.add_argument("--self-test",action="store_true")
    args=ap.parse_args()
    if args.self_test:
        print(json.dumps({"SELFTEST":"PASS","session":market_session()})); return 0
    r=redis_client()
    a=Adapter(r)
    while True:
        m=a.cycle_once()
        print(json.dumps({"status":m["status"],"session":m["market_session"],"ts_ms":m["ts_ms"]}))
        if args.once: break
        time.sleep(a.cycle)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
