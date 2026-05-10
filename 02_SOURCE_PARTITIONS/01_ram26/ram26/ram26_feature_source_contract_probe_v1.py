#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAM26 Feature Source Contract Probe v1
Read-only checker for:
- universe keys
- market signal keys
- xgb risk keys
- daily feature keys
- darkpool keys
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import redis  # type: ignore
except Exception:
    redis = None


def load_env_file(path: str):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


def client():
    if redis is None:
        raise RuntimeError("redis package required")
    for p in ("/etc/ares/redis.env", "/etc/ares/ares.env", os.path.expanduser("~/.ares_redis_env")):
        load_env_file(p)
    url = os.getenv("REDIS_URL") or os.getenv("ARES_REDIS_URL")
    password = os.getenv("REDIS_PASSWORD")
    if not url:
        raise RuntimeError("REDIS_URL required")
    r = redis.Redis.from_url(url, password=password, decode_responses=True, socket_timeout=5)
    r.ping()
    return r


def parse_json(raw: Any, default=None):
    if raw is None:
        return default
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return default
    raw = raw.strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def sf(x, default=None):
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip().replace(",", "")
            if not x:
                return default
        return float(x)
    except Exception:
        return default


def ts_ms(obj) -> Optional[int]:
    if not isinstance(obj, dict):
        return None
    for k in ("updated_at_ms", "ts_ms", "timestamp_ms"):
        v = sf(obj.get(k), None)
        if v and v > 10_000_000_000:
            return int(v)
    for k in ("updated_at", "ts", "timestamp", "iso"):
        v = obj.get(k)
        if v is None:
            continue
        fv = sf(v, None)
        if fv:
            if fv > 10_000_000_000:
                return int(fv)
            if fv > 1_000_000_000:
                return int(fv * 1000)
        if isinstance(v, str):
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except Exception:
                pass
    return None


def key_summary(r, key: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"key": key}
    try:
        typ = r.type(key)
    except Exception as e:
        return {"key": key, "error": str(e)}
    out["type"] = typ

    if typ == "none":
        return out
    if typ == "string":
        raw = r.get(key)
        out["ttl"] = r.ttl(key)
        out["raw_preview"] = str(raw)[:200]
        data = parse_json(raw, None)
        if isinstance(data, dict):
            out["format"] = "json_object"
            out["keys"] = sorted(list(data.keys()))[:50]
            out["updated_at_ms"] = ts_ms(data)
        elif isinstance(data, list):
            out["format"] = "json_array"
            out["len"] = len(data)
        else:
            out["format"] = "raw_scalar"
            out["float_value"] = sf(raw, None)
    elif typ == "hash":
        out["hlen"] = r.hlen(key)
        sample = {}
        for k in r.hkeys(key)[:5]:
            raw = r.hget(key, k)
            data = parse_json(raw, None)
            if isinstance(data, dict):
                sample[k] = {"format": "json_object", "keys": sorted(list(data.keys()))[:30], "updated_at_ms": ts_ms(data)}
            else:
                sample[k] = {"format": "raw", "preview": str(raw)[:100]}
        out["sample"] = sample
    elif typ == "set":
        out["scard"] = r.scard(key)
        out["sample"] = sorted(list(r.smembers(key)))[:20]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--sample-n", type=int, default=15)
    args = ap.parse_args()

    r = client()
    today = datetime.now(timezone.utc).date()
    daily_keys = [f"ares:features:daily:{(today - timedelta(days=i)).strftime('%Y%m%d')}" for i in range(10)]

    universe = []
    universe_sources = []
    for key in ("policy:universe:symbols", "champion:universe:global"):
        s = key_summary(r, key)
        universe_sources.append(s)
        if s.get("type") == "set":
            universe = sorted(list(r.smembers(key)))
            if universe:
                break

    symbols = universe[:args.sample_n]
    market_keys = [
        "signal:IWM:mom10",
        "signal:IWM:vol",
        "ram26:market:IWM:mom10",
        "ram26:market:IWM:vol",
    ]

    xgb = {}
    daily = {}
    dark = {}
    for sym in symbols:
        xgb[sym] = [
            key_summary(r, f"xgb:v8:risk:{sym}"),
            key_summary(r, f"xgb:risk:{sym}"),
        ]
        dark[sym] = [key_summary(r, f"ares:feature:darkpool_history:{sym}")]

    for dk in daily_keys:
        daily[dk] = key_summary(r, dk)

    report = {
        "generated_at_ms": int(time.time() * 1000),
        "universe_count": len(universe),
        "universe_sample": symbols,
        "universe_sources": universe_sources,
        "market": [key_summary(r, k) for k in market_keys],
        "daily_keys": daily,
        "xgb_sample": xgb,
        "darkpool_sample": dark,
        "recommendations": [],
    }

    # simple recommendations
    for m in report["market"]:
        if m.get("key", "").startswith("signal:IWM") and m.get("format") == "raw_scalar" and not m.get("updated_at_ms"):
            report["recommendations"].append("signal:IWM raw float lacks timestamp; use RAM26 market adapter JSON wrapper.")
    if not universe:
        report["recommendations"].append("Universe missing; restore policy:universe:symbols or champion:universe:global.")
    if all(v.get("type") == "none" for v in daily.values()):
        report["recommendations"].append("No ares:features:daily:{YYYYMMDD} key found in last 10 days; daily feature publisher required.")

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
