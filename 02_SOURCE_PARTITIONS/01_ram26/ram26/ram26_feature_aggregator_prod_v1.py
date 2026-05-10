#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAM26 Feature Aggregator Production v1
======================================

Purpose
-------
Collect fragmented live Redis features and publish RAM26-compatible feature
records to `ram26:features:v1`.

This is the production implementation for RFC-009:
- No synthetic feature values for eligible records.
- No 1/N fallback.
- Strict freshness.
- Fail-closed on missing core data.
- Per-symbol eligibility with explicit missing/stale reasons.
- Whole-key publish gate based on minimum eligible ratio.

Output
------
Redis HASH:
  key   = ram26:features:v1
  field = SYMBOL
  value = JSON record

Record schema:
{
  "symbol": "AAPL",
  "updated_at_ms": 1770000000000,
  "eligible": true,
  "ret_5d": 0.012,
  "ret_20d": 0.034,
  "ret_60d": 0.081,
  "vol_20d": 0.018,
  "quality": 0.62,
  "value": 0.45,
  "liquidity": 0.91,
  "sentiment": 0.14,
  "drawdown_20d": -0.03,
  "spread_bps": 4.2,
  "p_drop": 0.27,
  "risk_score": 0.31,
  "market_mom10": 0.02,
  "market_vol": 0.017,
  "missing_fields": [],
  "stale_sources": [],
  "source_ts_ms": {"daily": ..., "xgb": ..., "market": ...}
}

If a field is missing, the record may still be published but `eligible=false`.
Missing numeric fields are set to 0.0 only to preserve parser safety; they are
never treated as valid features while `eligible=false`.

Environment
-----------
REDIS_URL / ARES_REDIS_URL
REDIS_PASSWORD optional

RAM26_FEATURES_KEY=ram26:features:v1
RAM26_FEATURES_META_KEY=ram26:features:v1:meta
RAM26_FEATURES_EVENTS_STREAM=ram26:features:v1:events
RAM26_UNIVERSE_KEYS_CSV=policy:universe:symbols,champion:universe:global
RAM26_AGG_CYCLE_SEC=5
RAM26_AGG_TTL_SEC=60
RAM26_AGG_DELETE_ON_FAIL=true
RAM26_AGG_MIN_ELIGIBLE_RATIO=0.60
RAM26_REQUIRE_MARKET_SIGNAL=true
RAM26_REQUIRE_XGB=true
RAM26_MARKET_MOM_KEY=signal:IWM:mom10
RAM26_MARKET_VOL_KEY=signal:IWM:vol
RAM26_DAILY_KEY_PREFIX=ares:features:daily
RAM26_DAILY_LOOKBACK_DAYS=10
RAM26_DAILY_MAX_AGE_SEC=259200
RAM26_XGB_MAX_AGE_SEC=1800
RAM26_MARKET_MAX_AGE_SEC=1800
RAM26_DARKPOOL_MAX_AGE_SEC=86400
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import redis  # type: ignore
except Exception:
    redis = None


LOGGER = logging.getLogger("ram26_feature_aggregator_prod_v1")

REQUIRED_FIELDS = [
    "ret_5d",
    "ret_20d",
    "ret_60d",
    "vol_20d",
    "quality",
    "value",
    "liquidity",
    "sentiment",
    "drawdown_20d",
    "spread_bps",
]

OPTIONAL_FIELDS = ["ret_1d", "vol_60d", "price"]

FIELD_ALIASES: Dict[str, List[str]] = {
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


def env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return default


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


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


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            value = value.replace(",", "").strip()
            if value == "":
                return default
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except Exception:
        return default


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    f = safe_float(value, None)
    if f is None:
        return default
    return int(f)


def parse_json(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not isinstance(raw, str):
        return default
    raw = raw.strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def parse_timestamp_ms(obj: Any, default: Optional[int] = None) -> Optional[int]:
    if not isinstance(obj, dict):
        return default

    for key in [
        "updated_at_ms",
        "ts_ms",
        "timestamp_ms",
        "last_update_ms",
        "source_updated_at_ms",
    ]:
        v = safe_int(obj.get(key), None)
        if v is not None and v > 10_000_000_000:
            return v

    for key in [
        "updated_at",
        "scored_at",
        "generated_at",
        "created_at",
        "ts",
        "timestamp",
        "time",
        "last_update",
        "created_at",
        "iso",
    ]:
        v = obj.get(key)
        if v is None:
            continue
        # epoch seconds or ms
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

    return default


class RedisRuntime:
    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_env(cls) -> "RedisRuntime":
        if redis is None:
            raise RuntimeError("python redis package is required. Run: pip install redis")

        for path in ("/etc/ares/redis.env", "/etc/ares/ares.env", os.path.expanduser("~/.ares_redis_env")):
            load_env_file(path)

        url = os.getenv("REDIS_URL") or os.getenv("ARES_REDIS_URL")
        password = os.getenv("REDIS_PASSWORD")
        if not url:
            raise RuntimeError("REDIS_URL or ARES_REDIS_URL is required")

        client = redis.Redis.from_url(
            url,
            password=password,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        client.ping()
        return cls(client)

    def type(self, key: str) -> str:
        try:
            return str(self.client.type(key))
        except Exception:
            return "none"

    def get(self, key: str) -> str:
        try:
            v = self.client.get(key)
            return "" if v is None else str(v)
        except Exception:
            return ""

    def hget(self, key: str, field: str) -> str:
        try:
            v = self.client.hget(key, field)
            return "" if v is None else str(v)
        except Exception:
            return ""

    def hgetall(self, key: str) -> Dict[str, str]:
        try:
            out = self.client.hgetall(key)
            return dict(out or {})
        except Exception:
            return {}

    def smembers(self, key: str) -> List[str]:
        try:
            return sorted(str(x) for x in (self.client.smembers(key) or []))
        except Exception:
            return []

    def pipeline(self):
        return self.client.pipeline()

    def set(self, key: str, value: str, ex: Optional[int] = None):
        return self.client.set(key, value, ex=ex)

    def delete(self, key: str):
        return self.client.delete(key)

    def xadd(self, key: str, fields: Dict[str, Any], maxlen: Optional[int] = None):
        if maxlen:
            return self.client.xadd(key, fields, maxlen=maxlen, approximate=True)
        return self.client.xadd(key, fields)


class FeatureAggregatorConfig:
    def __init__(self) -> None:
        self.output_key = os.getenv("RAM26_FEATURES_KEY", "ram26:features:v1")
        self.meta_key = os.getenv("RAM26_FEATURES_META_KEY", "ram26:features:v1:meta")
        self.events_stream = os.getenv("RAM26_FEATURES_EVENTS_STREAM", "ram26:features:v1:events")
        self.universe_keys = [
            x.strip()
            for x in os.getenv(
                "RAM26_UNIVERSE_KEYS_CSV",
                "policy:universe:symbols,champion:universe:global",
            ).split(",")
            if x.strip()
        ]
        self.output_ttl_sec = env_int("RAM26_AGG_TTL_SEC", 60)
        self.cycle_sec = env_float("RAM26_AGG_CYCLE_SEC", 5.0)
        self.delete_on_fail = env_bool("RAM26_AGG_DELETE_ON_FAIL", True)
        self.min_eligible_ratio = env_float("RAM26_AGG_MIN_ELIGIBLE_RATIO", 0.60)

        self.require_market_signal = env_bool("RAM26_REQUIRE_MARKET_SIGNAL", True)
        self.require_xgb = env_bool("RAM26_REQUIRE_XGB", True)

        self.market_mom_key = os.getenv("RAM26_MARKET_MOM_KEY", "signal:IWM:mom10")
        self.market_vol_key = os.getenv("RAM26_MARKET_VOL_KEY", "signal:IWM:vol")

        self.daily_key_prefix = os.getenv("RAM26_DAILY_KEY_PREFIX", "ares:features:daily")
        self.daily_keys_csv = os.getenv("RAM26_DAILY_FEATURE_KEYS_CSV", "")
        self.daily_lookback_days = env_int("RAM26_DAILY_LOOKBACK_DAYS", 10)

        self.xgb_key_patterns = [
            x.strip()
            for x in os.getenv("RAM26_XGB_KEY_PATTERNS_CSV", "xgb:v8:risk:{symbol},xgb:risk:{symbol}").split(",")
            if x.strip()
        ]
        self.darkpool_key_patterns = [
            x.strip()
            for x in os.getenv("RAM26_DARKPOOL_KEY_PATTERNS_CSV", "ares:feature:darkpool_history:{symbol}").split(",")
            if x.strip()
        ]

        self.daily_max_age_sec = env_int("RAM26_DAILY_MAX_AGE_SEC", 259200)
        self.xgb_max_age_sec = env_int("RAM26_XGB_MAX_AGE_SEC", 1800)
        self.market_max_age_sec = env_int("RAM26_MARKET_MAX_AGE_SEC", 1800)
        self.darkpool_max_age_sec = env_int("RAM26_DARKPOOL_MAX_AGE_SEC", 86400)

        self.event_maxlen = env_int("RAM26_AGG_EVENT_MAXLEN", 1000)
        self.fail_if_no_daily = env_bool("RAM26_FAIL_IF_NO_DAILY", True)


class RAM26FeatureAggregator:
    def __init__(self, runtime: RedisRuntime, cfg: FeatureAggregatorConfig):
        self.r = runtime
        self.cfg = cfg

    def fetch_universe(self) -> Tuple[List[str], Dict[str, Any]]:
        meta: Dict[str, Any] = {"tried": []}
        symbols: List[str] = []

        for key in self.cfg.universe_keys:
            ktype = self.r.type(key)
            meta["tried"].append({"key": key, "type": ktype})
            if ktype == "set":
                symbols = self.r.smembers(key)
            elif ktype == "string":
                raw = self.r.get(key)
                data = parse_json(raw, None)
                if isinstance(data, list):
                    symbols = [str(x) for x in data]
                elif isinstance(data, dict):
                    if isinstance(data.get("symbols"), list):
                        symbols = [str(x) for x in data["symbols"]]
                    elif isinstance(data.get("universe"), list):
                        symbols = [str(x) for x in data["universe"]]
                    elif isinstance(data.get("targets"), dict):
                        symbols = [str(x) for x in data["targets"].keys()]
            elif ktype == "hash":
                h = self.r.hgetall(key)
                if h:
                    symbols = list(h.keys())

            symbols = sorted({s.strip().upper() for s in symbols if str(s).strip()})
            if symbols:
                meta["source_key"] = key
                break

        exclude = set(s.upper() for s in self.r.smembers("policy:universe:exclude_set"))
        before = len(symbols)
        symbols = [s for s in symbols if s not in exclude]
        meta["before_exclude"] = before
        meta["exclude_count"] = len(exclude)
        meta["final_count"] = len(symbols)
        return symbols, meta

    def date_key_candidates(self) -> List[str]:
        if self.cfg.daily_keys_csv:
            return [x.strip() for x in self.cfg.daily_keys_csv.split(",") if x.strip()]
        today = datetime.now(timezone.utc).date()
        return [
            f"{self.cfg.daily_key_prefix}:{(today - timedelta(days=i)).strftime('%Y%m%d')}"
            for i in range(self.cfg.daily_lookback_days)
        ]

    def read_symbol_record_from_key(self, key: str, symbol: str) -> Tuple[Optional[Dict[str, Any]], Optional[int], str]:
        ktype = self.r.type(key)
        if ktype == "hash":
            raw = self.r.hget(key, symbol)
            data = parse_json(raw, None)
            if isinstance(data, dict):
                return data, parse_timestamp_ms(data, None), key
            # Some hashes may store each symbol as raw scalar; not enough for daily contract.
            return None, None, key

        if ktype == "string":
            data = parse_json(self.r.get(key), None)
            if isinstance(data, dict):
                rec = None
                if isinstance(data.get(symbol), dict):
                    rec = data[symbol]
                elif isinstance(data.get("symbols"), dict) and isinstance(data["symbols"].get(symbol), dict):
                    rec = data["symbols"][symbol]
                elif isinstance(data.get("data"), dict) and isinstance(data["data"].get(symbol), dict):
                    rec = data["data"][symbol]
                elif isinstance(data.get("features"), dict) and isinstance(data["features"].get(symbol), dict):
                    rec = data["features"][symbol]
                if isinstance(rec, dict):
                    return rec, parse_timestamp_ms(rec, parse_timestamp_ms(data, None)), key
        return None, None, key

    def read_daily(self, symbol: str) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[str]]:
        for key in self.date_key_candidates():
            rec, ts_ms, source_key = self.read_symbol_record_from_key(key, symbol)
            if rec is not None:
                return rec, ts_ms, source_key
        return None, None, None

    def read_pattern_json(self, patterns: List[str], symbol: str) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[str]]:
        for pat in patterns:
            key = pat.format(symbol=symbol, SYMBOL=symbol)
            data = parse_json(self.r.get(key), None)
            if isinstance(data, list):
                # Use latest timestamped dict if possible.
                dicts = [x for x in data if isinstance(x, dict)]
                if dicts:
                    dicts.sort(key=lambda x: parse_timestamp_ms(x, 0) or 0, reverse=True)
                    data = dicts[0]
            if isinstance(data, dict):
                return data, parse_timestamp_ms(data, None), key
        return None, None, None

    def read_metric(self, key: str) -> Tuple[Optional[float], Optional[int], str]:
        raw = self.r.get(key)
        data = parse_json(raw, None)
        if isinstance(data, dict):
            for k in ("value", "v", "score", "mom10", "vol", "signal"):
                if k in data:
                    return safe_float(data.get(k), None), parse_timestamp_ms(data, None), key
            return None, parse_timestamp_ms(data, None), key
        return safe_float(raw, None), None, key

    @staticmethod
    def get_field(record: Optional[Dict[str, Any]], field: str) -> Optional[float]:
        if not isinstance(record, dict):
            return None
        for alias in FIELD_ALIASES[field]:
            if alias in record:
                return safe_float(record.get(alias), None)
        return None

    def source_fresh(self, ts_ms: Optional[int], max_age_sec: int) -> bool:
        if ts_ms is None:
            return False
        age = (now_ms() - ts_ms) / 1000.0
        return age <= max_age_sec and age >= -60.0

    def market_bundle(self) -> Tuple[Dict[str, Any], List[str]]:
        missing: List[str] = []
        mom, mom_ts, mom_key = self.read_metric(self.cfg.market_mom_key)
        vol, vol_ts, vol_key = self.read_metric(self.cfg.market_vol_key)

        bundle = {
            "market_mom10": mom,
            "market_vol": vol,
            "source_ts_ms": {
                "market_mom10": mom_ts,
                "market_vol": vol_ts,
            },
            "source_keys": {
                "market_mom10": mom_key,
                "market_vol": vol_key,
            },
            "fresh": {
                "market_mom10": self.source_fresh(mom_ts, self.cfg.market_max_age_sec),
                "market_vol": self.source_fresh(vol_ts, self.cfg.market_max_age_sec),
            },
        }

        if mom is None:
            missing.append("market_mom10")
        if vol is None:
            missing.append("market_vol")
        if not bundle["fresh"]["market_mom10"]:
            missing.append("market_mom10_stale")
        if not bundle["fresh"]["market_vol"]:
            missing.append("market_vol_stale")
        return bundle, missing

    def build_record(self, symbol: str, market: Dict[str, Any]) -> Dict[str, Any]:
        tnow = now_ms()
        missing: List[str] = []
        stale: List[str] = []
        source_ts: Dict[str, Optional[int]] = {}
        source_keys: Dict[str, str] = {}

        daily, daily_ts, daily_key = self.read_daily(symbol)
        source_ts["daily"] = daily_ts
        if daily_key:
            source_keys["daily"] = daily_key

        xgb, xgb_ts, xgb_key = self.read_pattern_json(self.cfg.xgb_key_patterns, symbol)
        source_ts["xgb"] = xgb_ts
        if xgb_key:
            source_keys["xgb"] = xgb_key

        dark, dark_ts, dark_key = self.read_pattern_json(self.cfg.darkpool_key_patterns, symbol)
        source_ts["darkpool"] = dark_ts
        if dark_key:
            source_keys["darkpool"] = dark_key

        if self.cfg.fail_if_no_daily and daily is None:
            missing.append("daily_record")

        if daily is not None and not self.source_fresh(daily_ts, self.cfg.daily_max_age_sec):
            stale.append("daily")

        if self.cfg.require_xgb:
            if xgb is None:
                missing.append("xgb_record")
            elif not self.source_fresh(xgb_ts, self.cfg.xgb_max_age_sec):
                stale.append("xgb")

        if dark is not None and dark_ts is not None and not self.source_fresh(dark_ts, self.cfg.darkpool_max_age_sec):
            stale.append("darkpool")

        out: Dict[str, Any] = {
            "symbol": symbol,
            "eligible": True,
            "updated_at_ms": tnow,
            "updated_at_iso": utc_iso_from_ms(tnow),
            "missing_fields": [],
            "stale_sources": [],
            "source_ts_ms": source_ts,
            "source_keys": source_keys,
        }

        for f in REQUIRED_FIELDS + OPTIONAL_FIELDS:
            val = self.get_field(daily, f)

            # Allow darkpool to enrich sentiment/liquidity if daily lacks it.
            if val is None and f in ("sentiment", "liquidity") and isinstance(dark, dict):
                val = self.get_field(dark, f)

            if f in REQUIRED_FIELDS and val is None:
                missing.append(f)
                out[f] = 0.0
            elif val is not None:
                out[f] = val

        # XGB / risk fields.
        p_drop = safe_float(xgb.get("p_drop") if isinstance(xgb, dict) else None, None)
        risk_score = safe_float(xgb.get("risk_score") if isinstance(xgb, dict) else None, None)
        blocked = int(safe_float(xgb.get("blocked") if isinstance(xgb, dict) else 0, 0) or 0)

        if self.cfg.require_xgb and p_drop is None:
            missing.append("p_drop")

        if p_drop is not None:
            out["p_drop"] = max(0.0, min(1.0, p_drop))
        if risk_score is not None:
            out["risk_score"] = risk_score

        out["blocked"] = bool(blocked)
        if blocked:
            missing.append("blocked_by_xgb")

        # Market fields copied into each record for engine simplicity.
        out["market_mom10"] = market.get("market_mom10")
        out["market_vol"] = market.get("market_vol")
        out["source_ts_ms"]["market_mom10"] = market.get("source_ts_ms", {}).get("market_mom10")
        out["source_ts_ms"]["market_vol"] = market.get("source_ts_ms", {}).get("market_vol")

        if self.cfg.require_market_signal:
            if market.get("market_mom10") is None:
                missing.append("market_mom10")
            if market.get("market_vol") is None:
                missing.append("market_vol")
            if not market.get("fresh", {}).get("market_mom10"):
                stale.append("market_mom10")
            if not market.get("fresh", {}).get("market_vol"):
                stale.append("market_vol")

        source_times = [v for v in out["source_ts_ms"].values() if isinstance(v, int) and v > 0]
        if source_times:
            out["updated_at_ms"] = min(source_times)
            out["updated_at_iso"] = utc_iso_from_ms(out["updated_at_ms"])

        out["missing_fields"] = sorted(set(missing))
        out["stale_sources"] = sorted(set(stale))
        out["eligible"] = not out["missing_fields"] and not out["stale_sources"]
        return out

    def publish_fail_meta(self, status: str, reason: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        meta = {
            "status": status,
            "reason": reason,
            "ts_ms": now_ms(),
            "ts_iso": utc_iso_from_ms(now_ms()),
            "output_key": self.cfg.output_key,
            "publish_performed": False,
        }
        if extra:
            meta.update(extra)

        self.r.set(self.cfg.meta_key, json.dumps(meta, separators=(",", ":")), ex=max(self.cfg.output_ttl_sec, 60))
        try:
            self.r.xadd(self.cfg.events_stream, {"status": status, "reason": reason, "payload": json.dumps(meta)}, maxlen=self.cfg.event_maxlen)
        except Exception:
            pass

        if self.cfg.delete_on_fail:
            try:
                self.r.delete(self.cfg.output_key)
            except Exception:
                pass
        return meta

    def run_cycle(self) -> Dict[str, Any]:
        symbols, universe_meta = self.fetch_universe()
        if not symbols:
            return self.publish_fail_meta("NO_PUBLISH", "universe_missing", {"universe": universe_meta})

        market, market_missing = self.market_bundle()
        if self.cfg.require_market_signal and market_missing:
            return self.publish_fail_meta("NO_PUBLISH", "market_signal_missing_or_stale", {
                "market_missing": market_missing,
                "universe_count": len(symbols),
            })

        records: Dict[str, Dict[str, Any]] = {}
        for sym in symbols:
            records[sym] = self.build_record(sym, market)

        eligible = [s for s, rec in records.items() if rec.get("eligible")]
        eligible_ratio = len(eligible) / max(len(symbols), 1)

        failure_counts: Dict[str, int] = {}
        for rec in records.values():
            for f in rec.get("missing_fields", []):
                failure_counts[f"missing:{f}"] = failure_counts.get(f"missing:{f}", 0) + 1
            for f in rec.get("stale_sources", []):
                failure_counts[f"stale:{f}"] = failure_counts.get(f"stale:{f}", 0) + 1

        if eligible_ratio < self.cfg.min_eligible_ratio:
            return self.publish_fail_meta("NO_PUBLISH", "eligible_ratio_below_gate", {
                "universe_count": len(symbols),
                "eligible_count": len(eligible),
                "eligible_ratio": round(eligible_ratio, 6),
                "min_eligible_ratio": self.cfg.min_eligible_ratio,
                "failure_counts": failure_counts,
            })

        meta = {
            "status": "PUBLISHED",
            "ts_ms": now_ms(),
            "ts_iso": utc_iso_from_ms(now_ms()),
            "output_key": self.cfg.output_key,
            "universe": universe_meta,
            "universe_count": len(symbols),
            "eligible_count": len(eligible),
            "eligible_ratio": round(eligible_ratio, 6),
            "ttl_sec": self.cfg.output_ttl_sec,
            "failure_counts": failure_counts,
            "publish_performed": True,
        }

        pipe = self.r.pipeline()
        pipe.delete(self.cfg.output_key)
        # redis-py hset mapping is supported; use individual hset via mapping for clarity.
        pipe.hset(self.cfg.output_key, mapping={
            sym: json.dumps(rec, separators=(",", ":"), sort_keys=True)
            for sym, rec in records.items()
        })
        pipe.expire(self.cfg.output_key, self.cfg.output_ttl_sec)
        pipe.set(self.cfg.meta_key, json.dumps(meta, separators=(",", ":"), sort_keys=True), ex=max(self.cfg.output_ttl_sec, 60))
        pipe.xadd(self.cfg.events_stream, {
            "status": "PUBLISHED",
            "eligible_count": str(len(eligible)),
            "universe_count": str(len(symbols)),
            "payload": json.dumps(meta, separators=(",", ":")),
        }, maxlen=self.cfg.event_maxlen, approximate=True)
        pipe.execute()
        return meta


class FakeRedisRuntime(RedisRuntime):
    def __init__(self):
        self.store: Dict[str, Any] = {}
        self.hashes: Dict[str, Dict[str, str]] = {}
        self.sets: Dict[str, set] = {}
        self.events: List[Tuple[str, Dict[str, Any]]] = []

    def type(self, key: str) -> str:
        if key in self.sets:
            return "set"
        if key in self.hashes:
            return "hash"
        if key in self.store:
            return "string"
        return "none"

    def get(self, key: str) -> str:
        return self.store.get(key, "")

    def hget(self, key: str, field: str) -> str:
        return self.hashes.get(key, {}).get(field, "")

    def hgetall(self, key: str) -> Dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def smembers(self, key: str) -> List[str]:
        return sorted(self.sets.get(key, set()))

    def pipeline(self):
        return FakePipeline(self)

    def set(self, key: str, value: str, ex: Optional[int] = None):
        self.store[key] = value

    def delete(self, key: str):
        self.store.pop(key, None)
        self.hashes.pop(key, None)

    def xadd(self, key: str, fields: Dict[str, Any], maxlen: Optional[int] = None):
        self.events.append((key, fields))


class FakePipeline:
    def __init__(self, r: FakeRedisRuntime):
        self.r = r
        self.ops: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []

    def delete(self, *args, **kwargs):
        self.ops.append(("delete", args, kwargs))
        return self

    def hset(self, *args, **kwargs):
        self.ops.append(("hset", args, kwargs))
        return self

    def expire(self, *args, **kwargs):
        self.ops.append(("expire", args, kwargs))
        return self

    def set(self, *args, **kwargs):
        self.ops.append(("set", args, kwargs))
        return self

    def xadd(self, *args, **kwargs):
        self.ops.append(("xadd", args, kwargs))
        return self

    def execute(self):
        for name, args, kwargs in self.ops:
            if name == "delete":
                self.r.delete(args[0])
            elif name == "hset":
                key = args[0]
                mapping = kwargs.get("mapping")
                if mapping is None and len(args) >= 2:
                    mapping = args[1]
                self.r.hashes[key] = dict(mapping or {})
            elif name == "set":
                self.r.set(args[0], args[1], ex=kwargs.get("ex"))
            elif name == "xadd":
                self.r.xadd(args[0], args[1])
        self.ops.clear()


def self_test() -> int:
    r = FakeRedisRuntime()
    ts = now_ms()
    symbols = {f"STK{i:02d}" for i in range(12)}
    r.sets["policy:universe:symbols"] = symbols
    r.sets["policy:universe:exclude_set"] = set()

    daily_key = f"ares:features:daily:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    r.hashes[daily_key] = {}
    for i, sym in enumerate(sorted(symbols)):
        rec = {
            "symbol": sym,
            "updated_at_ms": ts,
            "ret_5d": 0.01 + i * 0.001,
            "ret_20d": 0.02 + i * 0.001,
            "ret_60d": 0.05 + i * 0.001,
            "vol_20d": 0.015 + i * 0.001,
            "quality": 0.5 + i * 0.01,
            "value": 0.4 + i * 0.01,
            "liquidity": 0.9,
            "sentiment": 0.05,
            "drawdown_20d": -0.05,
            "spread_bps": 5.0,
            "price": 100 + i,
        }
        r.hashes[daily_key][sym] = json.dumps(rec)
        r.store[f"xgb:v8:risk:{sym}"] = json.dumps({"p_drop": 0.2 + i * 0.02, "risk_score": 0.1 + i * 0.01, "updated_at_ms": ts})

    r.store["signal:IWM:mom10"] = json.dumps({"value": 0.012, "updated_at_ms": ts})
    r.store["signal:IWM:vol"] = json.dumps({"value": 0.018, "updated_at_ms": ts})

    cfg = FeatureAggregatorConfig()
    cfg.min_eligible_ratio = 0.80
    cfg.daily_lookback_days = 2
    agg = RAM26FeatureAggregator(r, cfg)
    meta = agg.run_cycle()

    assert meta["status"] == "PUBLISHED", meta
    assert meta["eligible_count"] == 12, meta
    assert r.type(cfg.output_key) == "hash"
    sample = parse_json(next(iter(r.hashes[cfg.output_key].values())), {})
    assert sample["eligible"] is True
    assert sample["p_drop"] is not None
    print(json.dumps({"SELFTEST": "PASS", "meta": meta, "sample": sample}, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--print-config", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=os.getenv("RAM26_AGG_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.self_test:
        return self_test()

    cfg = FeatureAggregatorConfig()
    if args.print_config:
        print(json.dumps(cfg.__dict__, indent=2, ensure_ascii=False))
        return 0

    runtime = RedisRuntime.from_env()
    agg = RAM26FeatureAggregator(runtime, cfg)

    LOGGER.info("RAM26 Feature Aggregator prod v1 starting output=%s ttl=%s cycle=%s",
                cfg.output_key, cfg.output_ttl_sec, cfg.cycle_sec)

    while True:
        try:
            meta = agg.run_cycle()
            LOGGER.info("[RAM26-AGG] status=%s universe=%s eligible=%s ratio=%s reason=%s",
                        meta.get("status"), meta.get("universe_count"),
                        meta.get("eligible_count"), meta.get("eligible_ratio"),
                        meta.get("reason", ""))
        except Exception as e:
            LOGGER.exception("cycle failed: %s", e)
        if args.once:
            break
        time.sleep(cfg.cycle_sec)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# RAM26_XGB_SCORED_AT_PATCH_V1
