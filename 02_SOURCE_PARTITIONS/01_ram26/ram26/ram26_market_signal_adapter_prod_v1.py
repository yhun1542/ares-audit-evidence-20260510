#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAM26 Market Signal Adapter Production v1
=========================================

Purpose
-------
Normalize existing live market signal keys that may be raw float strings into
RAM26-compatible JSON signal keys with explicit `value` and `updated_at_ms`.

Default safety policy:
- Existing source keys are NEVER overwritten.
- Normalized keys are written to RAM26-only namespace.
- Raw float sources are treated as fresh only when:
  1) a companion timestamp key is available and fresh, or
  2) raw value changed recently, or
  3) RAM26_MARKET_RAW_MODE=read_time is explicitly enabled.

Recommended production mode:
  RAM26_MARKET_RAW_MODE=change_aware

Temporary transition mode:
  RAM26_MARKET_RAW_MODE=read_time
  Use only after confirming the raw publisher is alive and updating.

Input defaults:
- signal:IWM:mom10
- signal:IWM:vol

Output defaults:
- ram26:market:IWM:mom10
- ram26:market:IWM:vol
- ram26:market:adapter:meta
- ram26:market:adapter:events
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import redis  # type: ignore
except Exception:
    redis = None


LOG = logging.getLogger("ram26_market_signal_adapter_prod_v1")


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        if isinstance(x, bytes):
            x = x.decode("utf-8", errors="ignore")
        if isinstance(x, str):
            x = x.strip().replace(",", "")
            if not x:
                return default
        v = float(x)
        if not math.isfinite(v):
            return default
        return v
    except Exception:
        return default


def parse_json(raw: Any, default: Any = None) -> Any:
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


def parse_timestamp_ms(obj: Any) -> Optional[int]:
    if not isinstance(obj, dict):
        return None

    for k in ("updated_at_ms", "ts_ms", "timestamp_ms", "last_update_ms"):
        v = safe_float(obj.get(k), None)
        if v and v > 10_000_000_000:
            return int(v)

    for k in ("updated_at", "ts", "timestamp", "time", "iso"):
        v = obj.get(k)
        if v is None:
            continue
        fv = safe_float(v, None)
        if fv is not None:
            if fv > 10_000_000_000:
                return int(fv)
            if fv > 1_000_000_000:
                return int(fv * 1000)
        if isinstance(v, str):
            s = v.strip().replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except Exception:
                pass
    return None


def load_env_file(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def redis_client():
    if redis is None:
        raise RuntimeError("python redis package required. pip install redis")
    for p in ("/etc/ares/redis.env", "/etc/ares/ares.env", os.path.expanduser("~/.ares_redis_env")):
        load_env_file(p)

    url = os.getenv("REDIS_URL") or os.getenv("ARES_REDIS_URL")
    password = os.getenv("REDIS_PASSWORD")
    if not url:
        raise RuntimeError("REDIS_URL/ARES_REDIS_URL required")

    client = redis.Redis.from_url(
        url,
        password=password,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )
    client.ping()
    return client


class MarketSignalAdapterConfig:
    def __init__(self) -> None:
        self.mom_src = os.getenv("RAM26_MARKET_MOM_SRC_KEY", "signal:IWM:mom10")
        self.vol_src = os.getenv("RAM26_MARKET_VOL_SRC_KEY", "signal:IWM:vol")
        self.mom_dst = os.getenv("RAM26_MARKET_MOM_DST_KEY", "ram26:market:IWM:mom10")
        self.vol_dst = os.getenv("RAM26_MARKET_VOL_DST_KEY", "ram26:market:IWM:vol")
        self.meta_key = os.getenv("RAM26_MARKET_ADAPTER_META_KEY", "ram26:market:adapter:meta")
        self.state_key = os.getenv("RAM26_MARKET_ADAPTER_STATE_KEY", "ram26:market:adapter:state")
        self.events_stream = os.getenv("RAM26_MARKET_ADAPTER_EVENTS_STREAM", "ram26:market:adapter:events")

        self.max_age_sec = env_int("RAM26_MARKET_ADAPTER_MAX_AGE_SEC", 1800)
        self.output_ttl_sec = env_int("RAM26_MARKET_ADAPTER_OUTPUT_TTL_SEC", 120)
        self.cycle_sec = env_float("RAM26_MARKET_ADAPTER_CYCLE_SEC", 5.0)
        self.event_maxlen = env_int("RAM26_MARKET_ADAPTER_EVENT_MAXLEN", 1000)

        # change_aware: raw float freshness is based on last value change.
        # read_time: raw float receives current timestamp every cycle. Use only as temporary bridge.
        # timestamp_required: raw float without companion timestamp is not published.
        self.raw_mode = os.getenv("RAM26_MARKET_RAW_MODE", "change_aware").strip().lower()
        self.delete_on_fail = env_bool("RAM26_MARKET_DELETE_ON_FAIL", True)

        self.companion_ts_suffixes = [
            x.strip()
            for x in os.getenv(
                "RAM26_MARKET_COMPANION_TS_SUFFIXES",
                ":updated_at_ms,:ts_ms,:updated_at,:ts",
            ).split(",")
            if x.strip()
        ]


class MarketSignalAdapter:
    def __init__(self, client: Any, cfg: MarketSignalAdapterConfig):
        self.r = client
        self.cfg = cfg

    def get_type(self, key: str) -> str:
        try:
            return str(self.r.type(key))
        except Exception:
            return "none"

    def get_raw(self, key: str) -> str:
        try:
            v = self.r.get(key)
            return "" if v is None else str(v)
        except Exception:
            return ""

    def get_companion_ts_ms(self, key: str) -> Optional[int]:
        for suffix in self.cfg.companion_ts_suffixes:
            raw = self.get_raw(f"{key}{suffix}")
            if not raw:
                continue
            d = parse_json(raw, None)
            if isinstance(d, dict):
                ts = parse_timestamp_ms(d)
                if ts:
                    return ts
                v = safe_float(d.get("value"), None)
                if v and v > 10_000_000_000:
                    return int(v)
                if v and v > 1_000_000_000:
                    return int(v * 1000)
            v = safe_float(raw, None)
            if v and v > 10_000_000_000:
                return int(v)
            if v and v > 1_000_000_000:
                return int(v * 1000)
        return None

    def read_state(self, state_id: str) -> Dict[str, Any]:
        try:
            raw = self.r.hget(self.cfg.state_key, state_id)
            d = parse_json(raw, {})
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def write_state(self, state_id: str, data: Dict[str, Any]) -> None:
        try:
            self.r.hset(self.cfg.state_key, state_id, json.dumps(data, separators=(",", ":"), sort_keys=True))
        except Exception:
            pass

    def normalize_one(self, state_id: str, src_key: str, dst_key: str, name: str) -> Tuple[bool, Dict[str, Any]]:
        tnow = now_ms()
        raw = self.get_raw(src_key)
        ktype = self.get_type(src_key)

        if not raw:
            return False, {
                "name": name,
                "src_key": src_key,
                "dst_key": dst_key,
                "status": "NO_PUBLISH",
                "reason": "source_missing",
                "source_type": ktype,
                "ts_ms": tnow,
            }

        parsed = parse_json(raw, None)
        source_format = "unknown"
        value: Optional[float] = None
        src_ts_ms: Optional[int] = None

        if isinstance(parsed, dict):
            source_format = "json"
            for k in ("value", "v", "score", "signal", "mom10", "vol"):
                if k in parsed:
                    value = safe_float(parsed.get(k), None)
                    break
            src_ts_ms = parse_timestamp_ms(parsed)
        else:
            source_format = "raw_float"
            value = safe_float(raw, None)
            src_ts_ms = self.get_companion_ts_ms(src_key)

        if value is None:
            return False, {
                "name": name,
                "src_key": src_key,
                "dst_key": dst_key,
                "status": "NO_PUBLISH",
                "reason": "value_parse_failed",
                "source_type": ktype,
                "source_format": source_format,
                "raw_preview": raw[:80],
                "ts_ms": tnow,
            }

        state = self.read_state(state_id)
        last_value = safe_float(state.get("last_value"), None)
        last_change_ms = int(safe_float(state.get("last_change_ms"), 0) or 0)

        changed = last_value is None or abs(value - last_value) > 1e-12
        if changed:
            last_change_ms = tnow

        state.update({
            "last_value": value,
            "last_seen_ms": tnow,
            "last_change_ms": last_change_ms,
            "source_format": source_format,
            "src_key": src_key,
            "dst_key": dst_key,
        })
        self.write_state(state_id, state)

        if src_ts_ms is not None:
            updated_at_ms = src_ts_ms
            timestamp_policy = "source_timestamp"
        elif source_format == "raw_float" and self.cfg.raw_mode == "read_time":
            updated_at_ms = tnow
            timestamp_policy = "read_time"
        elif source_format == "raw_float" and self.cfg.raw_mode == "change_aware":
            updated_at_ms = last_change_ms
            timestamp_policy = "raw_value_last_change"
        else:
            return False, {
                "name": name,
                "src_key": src_key,
                "dst_key": dst_key,
                "status": "NO_PUBLISH",
                "reason": "timestamp_missing",
                "source_format": source_format,
                "raw_mode": self.cfg.raw_mode,
                "ts_ms": tnow,
            }

        age_sec = (tnow - int(updated_at_ms)) / 1000.0
        fresh = age_sec <= self.cfg.max_age_sec and age_sec >= -60.0

        payload = {
            "value": value,
            "updated_at_ms": int(updated_at_ms),
            "updated_at_iso": iso_ms(int(updated_at_ms)),
            "fresh": fresh,
            "age_sec": round(age_sec, 3),
            "source_key": src_key,
            "source_type": ktype,
            "source_format": source_format,
            "timestamp_policy": timestamp_policy,
            "raw_mode": self.cfg.raw_mode,
            "adapter_ts_ms": tnow,
            "adapter_iso": iso_ms(tnow),
            "producer": "ram26_market_signal_adapter_prod_v1",
        }

        if not fresh:
            return False, {
                "name": name,
                "src_key": src_key,
                "dst_key": dst_key,
                "status": "NO_PUBLISH",
                "reason": "source_stale",
                "age_sec": round(age_sec, 3),
                "max_age_sec": self.cfg.max_age_sec,
                "payload": payload,
                "ts_ms": tnow,
            }

        self.r.set(dst_key, json.dumps(payload, separators=(",", ":"), sort_keys=True), ex=self.cfg.output_ttl_sec)
        return True, {
            "name": name,
            "src_key": src_key,
            "dst_key": dst_key,
            "status": "PUBLISHED",
            "payload": payload,
            "ts_ms": tnow,
        }

    def run_cycle(self) -> Dict[str, Any]:
        mom_ok, mom_result = self.normalize_one("mom10", self.cfg.mom_src, self.cfg.mom_dst, "market_mom10")
        vol_ok, vol_result = self.normalize_one("vol", self.cfg.vol_src, self.cfg.vol_dst, "market_vol")

        status = "PUBLISHED" if mom_ok and vol_ok else "NO_PUBLISH"
        reason = "ok" if status == "PUBLISHED" else "one_or_more_market_signals_missing_or_stale"
        tnow = now_ms()

        meta = {
            "status": status,
            "reason": reason,
            "ts_ms": tnow,
            "ts_iso": iso_ms(tnow),
            "raw_mode": self.cfg.raw_mode,
            "max_age_sec": self.cfg.max_age_sec,
            "output_ttl_sec": self.cfg.output_ttl_sec,
            "mom10": mom_result,
            "vol": vol_result,
            "publish_performed": status == "PUBLISHED",
        }

        self.r.set(self.cfg.meta_key, json.dumps(meta, separators=(",", ":"), sort_keys=True), ex=max(self.cfg.output_ttl_sec, 60))
        self.r.xadd(self.cfg.events_stream, {
            "status": status,
            "reason": reason,
            "payload": json.dumps(meta, separators=(",", ":")),
        }, maxlen=self.cfg.event_maxlen, approximate=True)

        if status != "PUBLISHED" and self.cfg.delete_on_fail:
            try:
                self.r.delete(self.cfg.mom_dst)
                self.r.delete(self.cfg.vol_dst)
            except Exception:
                pass

        return meta


def self_test() -> int:
    class Fake:
        def __init__(self):
            self.kv: Dict[str, str] = {}
            self.h: Dict[str, Dict[str, str]] = {}
            self.events = []
        def type(self, k): return "string" if k in self.kv else "none"
        def get(self, k): return self.kv.get(k)
        def set(self, k, v, ex=None): self.kv[k] = v
        def delete(self, k): self.kv.pop(k, None)
        def hget(self, k, f): return self.h.get(k, {}).get(f)
        def hset(self, k, f, v): self.h.setdefault(k, {})[f] = v
        def xadd(self, k, fields, maxlen=None, approximate=True): self.events.append((k, fields))

    fake = Fake()
    cfg = MarketSignalAdapterConfig()
    cfg.raw_mode = "change_aware"
    cfg.max_age_sec = 1800
    ad = MarketSignalAdapter(fake, cfg)

    fake.kv[cfg.mom_src] = "0.054"
    fake.kv[cfg.vol_src] = "0.018"
    meta = ad.run_cycle()
    assert meta["status"] == "PUBLISHED", meta
    assert parse_json(fake.kv[cfg.mom_dst], {})["value"] == 0.054

    # simulate stale unchanged value
    state = parse_json(fake.h[cfg.state_key]["mom10"], {})
    state["last_change_ms"] = now_ms() - 2_000_000
    fake.h[cfg.state_key]["mom10"] = json.dumps(state)
    meta2 = ad.run_cycle()
    assert meta2["status"] == "NO_PUBLISH", meta2

    print(json.dumps({"SELFTEST": "PASS", "meta1": meta, "meta2": meta2}, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--print-config", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=os.getenv("RAM26_MARKET_ADAPTER_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.self_test:
        return self_test()

    cfg = MarketSignalAdapterConfig()
    if args.print_config:
        print(json.dumps(cfg.__dict__, indent=2, ensure_ascii=False))
        return 0

    client = redis_client()
    adapter = MarketSignalAdapter(client, cfg)
    LOG.info("RAM26 Market Signal Adapter starting raw_mode=%s mom=%s->%s vol=%s->%s",
             cfg.raw_mode, cfg.mom_src, cfg.mom_dst, cfg.vol_src, cfg.vol_dst)

    while True:
        try:
            meta = adapter.run_cycle()
            LOG.info("[RAM26-MKT] status=%s reason=%s raw_mode=%s",
                     meta.get("status"), meta.get("reason"), meta.get("raw_mode"))
        except Exception as e:
            LOG.exception("cycle failed: %s", e)
        if args.once:
            break
        time.sleep(cfg.cycle_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
