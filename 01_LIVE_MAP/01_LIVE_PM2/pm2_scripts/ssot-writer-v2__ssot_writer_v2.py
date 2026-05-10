#!/usr/bin/env python3
# SOURCE: /home/ubuntu/aub-trading-system/ops/ssot_writer_v2.py
# SIZE: 36604 chars

"""
ssot_writer_v2.py — SSOT V2 Writer (v4.0.0-hardened)

핵심 목표
- runtime source(exec_src)와 champion source(engine_src)를 명확히 구분한다.
- fallback을 허용하더라도 '건강한 정상상태'처럼 위장하지 않는다.
- stale source / fallback source가 fresh canary로 세탁되는 구조를 차단한다.
- champion provenance와 실제 generator identity를 함께 기록해 성능 drift를 감시한다.
- 중복 인스턴스 / 짧은 overlap / publish race를 구조적으로 완화한다.

호환성 원칙
- 기존 SSOT_TARGET_V2 스키마를 유지한다.
- 기존 engine router / provenance injection / residual merge 동작은 유지한다.
- downstream 호환을 위해 기존 flat provenance 필드는 계속 쓴다.
- 하지만 asof는 더 이상 '오늘 날짜'로 덮어쓰지 않는다. 진실값을 보존한다.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

import redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

# [ARES-SSOT 2026-04-24] admin tier Redis client helper
import sys as _sys
_sys.path.insert(0, "/home/ubuntu/scripts")
try:
    from redis_ssot_loader import get_redis_client as _ares_get_redis
except ImportError as _e:
    raise RuntimeError(
        "ARES SSOT loader not available. Ensure /home/ubuntu/scripts/redis_ssot_loader.py exists."
    ) from _e

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

import ares_redis_env  # noqa: F401  # REDIS_URL bootstrap from /etc/ares/redis.env

WRITER_VERSION = "4.0.0-hardened"
_NY_TZ = ZoneInfo("America/New_York")

REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL env var is not set. Check /etc/ares/redis.env")

SRC_KEY = "ssot:target:v2:engine_src"
EXEC_SRC_KEY = "ssot:target:v2:exec_src"
LAST_GOOD_KEY = os.getenv("SSOT_LAST_GOOD_KEY", "ssot:target:v2:last_good")
LEGACY_KEY = os.getenv("SSOT_LEGACY_KEY", "ssot:target:v1:current")
CANARY_KEY = os.getenv("SSOT_CANARY_KEY", "ssot:target:v2:canary")
CANARY_TTL = max(int(os.getenv("SSOT_CANARY_TTL", "300")), 60)
ENGINE_VERSION = os.getenv("ENGINE_VERSION", "ares-nextgen-core-prod")
LOOP_INTERVAL = max(int(os.getenv("WRITER_INTERVAL_SEC", "30")), 5)
ENABLE_ENGINE_ROUTER = os.getenv("ENABLE_ENGINE_ROUTER", "true").lower() == "true"
ALLOW_LEGACY_FALLBACK = os.getenv("SSOT_ALLOW_LEGACY_FALLBACK", "true").lower() == "true"
SSOT_SOURCE_MODE = os.getenv("SSOT_SOURCE_MODE", "exec").strip().lower()
SOURCE_STALE_SEC = max(int(os.getenv("SSOT_SOURCE_STALE_SEC", "300")), 30)
EXEC_FALLBACK_MAX_CYCLES = max(int(os.getenv("SSOT_EXEC_FALLBACK_MAX_CYCLES", "2")), 0)
STRICT_RUNTIME_SOURCE = os.getenv("SSOT_STRICT_RUNTIME_SOURCE", "true").lower() == "true"
STRICT_TARGET_ENGINE_MATCH = os.getenv("SSOT_STRICT_TARGET_ENGINE_MATCH", "false").lower() == "true"
WRITER_HEALTH_KEY = os.getenv("SSOT_WRITER_HEALTH_KEY", "ssot:writer:v2:health")
WRITER_STATUS_KEY = os.getenv("SSOT_WRITER_STATUS_KEY", "ssot:writer:v2:last_status")
WRITER_EVENT_STREAM = os.getenv("SSOT_WRITER_EVENT_STREAM", "audit:ssot_v2:writer_events")
WRITER_EVENT_MAXLEN = max(int(os.getenv("SSOT_WRITER_EVENT_MAXLEN", "1000")), 100)
WRITER_LOCK_FILE = os.getenv("SSOT_WRITER_LOCK_FILE", "/tmp/ssot_writer_v2.lock")
CANARY_LOCK_KEY = os.getenv("SSOT_CANARY_LOCK_KEY", "ssot:canary:write_lock")
SINGLETON_WAIT_SEC = max(int(os.getenv("SSOT_WRITER_SINGLETON_WAIT_SEC", "5")), 1)
HEALTH_TTL = max(int(os.getenv("SSOT_WRITER_HEALTH_TTL", str(max(CANARY_TTL * 2, 180)))), 60)
AUDIT_UNCHANGED_EVERY = max(int(os.getenv("SSOT_WRITER_AUDIT_UNCHANGED_EVERY", "20")), 1)

_redis_retry = Retry(ExponentialBackoff(cap=10, base=0.5), retries=5)
r = _ares_get_redis()

_shutdown_requested = False
_singleton_fd = None
_canary_lock_owner: Optional[str] = None
_last_published_digest: Optional[str] = None
_unchanged_publish_count = 0
_fallback_streak = 0
_last_logged_health_fingerprint: Optional[str] = None


@dataclass
class SourceDecision:
    payload: Optional[Dict[str, Any]]
    source_key: Optional[str]
    source_label: str
    source_mode: str
    fallback_active: bool = False
    fallback_reason: str = ""
    source_ts_ms: int = 0
    source_age_sec: Optional[int] = None
    warnings: List[str] = field(default_factory=list)


def log(level: str, msg: str, **kwargs: Any) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    extra = json.dumps(kwargs, ensure_ascii=False) if kwargs else ""
    print(f"[{ts}] [{level}] ssot-writer-v2: {msg} {extra}", flush=True)



def _handle_signal(signum: int, _frame: Any) -> None:
    global _shutdown_requested
    _shutdown_requested = True
    name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    log("INFO", f"Shutdown requested via {name}")


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)



def is_us_market_hours() -> bool:
    now = datetime.now(_NY_TZ)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close



def _now_ms() -> int:
    return int(time.time() * 1000)



def _utc_today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")



def _safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)



def _compute_digest(obj: Any) -> str:
    return hashlib.sha256(_safe_json_dumps(obj).encode()).hexdigest()



def _to_epoch_ms(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            ts = float(v)
        else:
            s = str(v).strip()
            if not s:
                return None
            try:
                ts = float(s)
            except ValueError:
                ts = datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        if ts > 1e12:
            return int(ts)
        return int(ts * 1000)
    except Exception:
        return None



def _source_age_sec(ts_ms: int) -> Optional[int]:
    if ts_ms <= 0:
        return None
    return max(0, int((_now_ms() - ts_ms) / 1000))



def _acquire_singleton_lock() -> Any:
    lock_dir = os.path.dirname(WRITER_LOCK_FILE)
    if lock_dir and not os.path.exists(lock_dir):
        os.makedirs(lock_dir, exist_ok=True)
    fd = open(WRITER_LOCK_FILE, "a+")
    while not _shutdown_requested:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd.seek(0)
            fd.truncate()
            fd.write(str(os.getpid()))
            fd.flush()
            log("INFO", "Writer singleton lock acquired", lock=WRITER_LOCK_FILE)
            return fd
        except OSError:
            log("WARN", "Another writer instance holds the singleton lock; waiting", lock=WRITER_LOCK_FILE)
            time.sleep(SINGLETON_WAIT_SEC)
    return None



def _release_singleton_lock() -> None:
    global _singleton_fd
    if _singleton_fd is None:
        return
    try:
        fcntl.flock(_singleton_fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        _singleton_fd.close()
    except Exception:
        pass
    _singleton_fd = None



def _read_json_key(key: str) -> Optional[Dict[str, Any]]:
    try:
        raw = r.get(key)
    except redis.ResponseError as e:
        if "WRONGTYPE" in str(e):
            log("ERROR", "Redis WRONGTYPE on source key", key=key)
            return None
        raise
    if not raw:
        return None
    if isinstance(raw, str) and raw.startswith("auto_init_"):
        log("WARN", "Poisoned auto_init value detected, treating as empty", key=key, value=raw[:50])
        try:
            r.delete(key)
            log("INFO", "Deleted poisoned source key", key=key)
        except Exception:
            pass
        return None
    try:
        obj = json.loads(raw)
    except Exception as e:
        log("ERROR", "JSON parse error", key=key, err=str(e))
        return None
    return obj if isinstance(obj, dict) else None



def _extract_source_ts_ms(obj: Optional[Dict[str, Any]]) -> int:
    if not isinstance(obj, dict):
        return 0
    direct = _to_epoch_ms(obj.get("ts"))
    if direct:
        return direct
    meta = obj.get("targets", {}).get("meta", {}) if isinstance(obj.get("targets"), dict) else {}
    for key in ("source_ts", "writer_processed_ts", "source_refreshed_ts"):
        parsed = _to_epoch_ms(meta.get(key))
        if parsed:
            return parsed
    return 0



def _read_gpu_regime() -> Dict[str, Any]:
    try:
        meta = r.hgetall("gpu:regime:classifier:meta")
        if not meta:
            return {"status": "not_available"}
        return {
            "status": meta.get("status", "INACTIVE"),
            "mean_auc": float(meta.get("mean_auc", 0) or 0),
            "model_hash": meta.get("model_hash", ""),
            "bound_at": meta.get("bound_at", ""),
            "device": meta.get("device", "cpu"),
        }
    except Exception:
        return {"status": "error"}



def _read_candidate(key: str, label: str) -> SourceDecision:
    obj = _read_json_key(key)
    ts_ms = _extract_source_ts_ms(obj)
    return SourceDecision(
        payload=obj,
        source_key=key,
        source_label=label,
        source_mode=SSOT_SOURCE_MODE,
        source_ts_ms=ts_ms,
        source_age_sec=_source_age_sec(ts_ms),
    )



def read_source() -> SourceDecision:
    market_open = is_us_market_hours()
    warnings: List[str] = []

    if SSOT_SOURCE_MODE == "exec":
        exec_decision = _read_candidate(EXEC_SRC_KEY, "exec_src")
        if exec_decision.payload:
            return exec_decision
        warnings.append("exec_src_empty_or_invalid")

        engine_decision = _read_candidate(SRC_KEY, "engine_src")
        if engine_decision.payload:
            engine_decision.fallback_active = True
            engine_decision.fallback_reason = "exec_src_missing"
            engine_decision.warnings = warnings + ["fallback:engine_src"]
            if market_open:
                log("WARN", "exec_src empty/invalid; falling back to engine_src", source_mode=SSOT_SOURCE_MODE)
            else:
                log("INFO", "exec_src empty/invalid during closed market; using engine_src fallback")
            return engine_decision

        last_good_decision = _read_candidate(LAST_GOOD_KEY, "last_good")
        if last_good_decision.payload:
            last_good_decision.fallback_active = True
            last_good_decision.fallback_reason = "engine_src_missing"
            last_good_decision.warnings = warnings + ["fallback:last_good"]
            log("WARN", "Both exec_src and engine_src unavailable; using last_good as read-only fallback")
            return last_good_decision
    else:
        engine_decision = _read_candidate(SRC_KEY, "engine_src")
        if engine_decision.payload:
            return engine_decision

    if ALLOW_LEGACY_FALLBACK:
        legacy_decision = _read_candidate(LEGACY_KEY, "legacy")
        if legacy_decision.payload:
            legacy_decision.fallback_active = True
            legacy_decision.fallback_reason = "primary_sources_missing"
            legacy_decision.warnings = warnings + ["fallback:legacy"]
            log("WARN", "Primary source missing; using legacy fallback")
            return legacy_decision

    return SourceDecision(
        payload=None,
        source_key=None,
        source_label="none",
        source_mode=SSOT_SOURCE_MODE,
        warnings=warnings + ["all_sources_missing"],
    )



def transform_to_v2(legacy: Dict[str, Any], source_label: str) -> Dict[str, Any]:
    positions_raw = legacy.get("positions", [])
    positions_v2: List[Dict[str, Any]] = []
    if isinstance(positions_raw, list):
        for p in positions_raw:
            if not isinstance(p, dict):
                continue
            sym = p.get("s") or p.get("symbol") or ""
            try:
                w = float(p.get("w") or p.get("weight") or 0)
            except Exception:
                continue
            side = p.get("side", "LONG" if w >= 0 else "SHORT")
            if sym:
                positions_v2.append({
                    "symbol": sym,
                    "w": round(w, 6),
                    "side": side,
                    "reason": p.get("reason", ""),
                })
    elif isinstance(positions_raw, dict):
        for sym, w in positions_raw.items():
            try:
                wf = float(w)
            except Exception:
                continue
            positions_v2.append({
                "symbol": sym,
                "w": round(wf, 6),
                "side": "LONG" if wf >= 0 else "SHORT",
                "reason": "",
            })

    gross = float(legacy.get("gross", sum(abs(p["w"]) for p in positions_v2)))
    meta_raw = legacy.get("meta", {}) if isinstance(legacy.get("meta"), dict) else {}

    regime = "UNKNOWN"
    try:
        regime_raw = r.get("emarkos:v1:regime")
        if regime_raw:
            if regime_raw.startswith("{"):
                rd = json.loads(regime_raw)
                regime = rd.get("ms_state", rd.get("final_regime", regime_raw))
            else:
                regime = regime_raw
    except Exception:
        pass

    vix = 0.0
    try:
        vix_raw = r.get("price:VIX") or r.get("ctx:vix") or "0"
        vix = float(vix_raw)
    except Exception:
        pass

    xgb_risk = 0.0
    try:
        xgb_risk_raw = r.get("xgb:risk:status") or r.get("xgb:risk:portfolio") or "0"
        if str(xgb_risk_raw).startswith("{"):
            xo = json.loads(xgb_risk_raw)
            xgb_risk = float(xo.get("xgb_risk", xo.get("risk", 0.0)))
        else:
            xgb_risk = float(xgb_risk_raw)
    except Exception:
        pass

    router_weights = meta_raw.get("router_weights", {})
    if not router_weights:
        try:
            rw_raw = r.get("router:v12_3:weights")
            if rw_raw:
                router_weights = json.loads(rw_raw)
        except Exception:
            pass

    return {
        "schema_version": "SSOT_TARGET_V2",
        "ts": _now_ms(),
        "asof": legacy.get("asof", _utc_today()),
        "engine_version": ENGINE_VERSION,
        "correlation_id": str(uuid.uuid4()),
        "targets": {
            "gross": round(gross, 6),
            "positions": positions_v2,
            "meta": {
                "regime": regime,
                "vix": vix,
                "xgb_risk": xgb_risk,
                "router_weights": router_weights,
                "risk_scalar": float(meta_raw.get("risk_scalar", 1.0) or 1.0),
                "cost_bps": float(meta_raw.get("cost_bps", 30.0) or 30.0),
                "vol_target": float(meta_raw.get("vol_target", 0.18) or 0.18),
                "universe_id": meta_raw.get("universe_id", "champion_v92"),
                "universe": [p["symbol"] for p in positions_v2],
                "portfolio_vol_est": float(meta_raw.get("portfolio_vol_est", 0.0) or 0.0),
                "master_scalar": float(meta_raw.get("master_scalar", 1.0) or 1.0),
                "regime_scalar": float(meta_raw.get("regime_scalar", 1.0) or 1.0),
                "rebalance_run_id": meta_raw.get("rebalance_run_id"),
                "router_version": meta_raw.get("router_version", ""),
                "dd_state": meta_raw.get("dd_state", {}),
                "warnings": list(meta_raw.get("warnings", [])),
                "xgb_risk_shadow": float(meta_raw.get("xgb_risk_shadow", 0.0) or 0.0),
                "xgb_risk_shadow_source": meta_raw.get("xgb_risk_shadow_source", "shadow_not_available"),
                "source_key": source_label,
                "gpu_regime_classifier": _read_gpu_regime(),
                "source_ts": int(_to_epoch_ms(legacy.get("ts", 0)) or 0),
                "source_refreshed_ts": _now_ms(),
            },
        },
    }



def apply_engine_router(v2_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not ENABLE_ENGINE_ROUTER:
        return v2_payload
    try:
        engines_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engines")
        if engines_dir not in sys.path:
            sys.path.insert(0, os.path.dirname(engines_dir))
        from engines.engine_router import route_and_build  # type: ignore

        cycle_id = v2_payload.get("correlation_id", str(time.time()))
        return route_and_build(r, v2_payload, cycle_id)
    except ImportError as e:
        log("WARN", f"Engine router import failed (running without router): {e}")
        return v2_payload
    except Exception as e:
        log("ERROR", f"Engine router error (safe fallback to base): {e}")
        return v2_payload


_provenance_cache: Dict[str, Any] = {}
_provenance_cache_ts = 0.0
PROVENANCE_CACHE_TTL = 300



def _first_nonempty(keys: List[str]) -> str:
    for key in keys:
        try:
            val = r.get(key)
            if val and val.strip():
                return val.strip()
        except Exception:
            continue
    return ""



def _compute_param_hash(params_str: Any) -> str:
    try:
        if not params_str:
            return ""
        params = json.loads(params_str) if isinstance(params_str, str) else params_str
        canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
    except Exception:
        return hashlib.sha256(str(params_str).encode()).hexdigest()[:16]



def _read_champion_provenance() -> Dict[str, Any]:
    global _provenance_cache, _provenance_cache_ts
    now = time.time()
    if _provenance_cache and (now - _provenance_cache_ts) < PROVENANCE_CACHE_TTL:
        return _provenance_cache
    try:
        strategy_family = _first_nonempty(["truth:live:strategy_family", "truth:champion:strategy_family"])
        champion_strategy_name = _first_nonempty(["truth:champion:strategy_name", "truth:champion:strategy_family"])
        champion_version = _first_nonempty(["truth:champion:official_version", "truth:champion:version"])
        champion_engine_file_raw = _first_nonempty(["truth:champion:engine_file", "truth:engine:file"])
        champion_engine_file = os.path.basename(champion_engine_file_raw) if champion_engine_file_raw else ""
        target_engine_family = _first_nonempty(["truth:live:active_target_engine", "truth:live:target_engine"])
        target_ssot_tag = _first_nonempty(["truth:live:active_ssot_tag", "truth:live:target_engine_family"])
        param_hash_raw = _first_nonempty([
            "truth:champion:param_hash",
            "truth:params:param_hash",
            "truth:params:declared_hash",
            "truth:champion:performance:is",
        ])
        import re as _re
        if param_hash_raw and _re.match(r"^[0-9a-f]{8,64}$", param_hash_raw):
            param_hash = param_hash_raw
        else:
            param_hash = _compute_param_hash(param_hash_raw)
        universe_version = _first_nonempty([
            "truth:live:universe_version",
            "truth:universe:version",
            "truth:baseline:effective_universe_version",
            "truth:live:active_ssot_version",
        ])
        baseline_ref = _first_nonempty(["truth:champion:baseline_ref", "truth:baseline:ref"])
        if not baseline_ref:
            baseline_ref = "champion_baseline_v2.3.1" if champion_version else ""
        provenance = {
            "strategy_family": strategy_family,
            "champion_strategy_name": champion_strategy_name,
            "champion_version": champion_version,
            "champion_engine_file": champion_engine_file,
            "target_engine_family": target_engine_family,
            "target_ssot_tag": target_ssot_tag,
            "param_hash": param_hash,
            "universe_version": universe_version,
            "baseline_ref": baseline_ref,
        }
        if any(provenance.values()):
            _provenance_cache = provenance
            _provenance_cache_ts = now
        return provenance
    except Exception as e:
        log("WARN", f"Failed to read champion provenance: {e}")
        return _provenance_cache



def inject_champion_provenance(v2_payload: Dict[str, Any]) -> Dict[str, Any]:
    provenance = _read_champion_provenance()
    if not provenance:
        return v2_payload
    try:
        meta = v2_payload.setdefault("targets", {}).setdefault("meta", {})
        meta["provenance"] = {
            "version": "v2.3.1",
            "logical": {
                "strategy_family": provenance.get("strategy_family", ""),
                "champion_strategy_name": provenance.get("champion_strategy_name", ""),
                "champion_version": provenance.get("champion_version", ""),
                "champion_engine_file": provenance.get("champion_engine_file", ""),
            },
            "physical": {
                "target_engine_family": provenance.get("target_engine_family", ""),
                "target_ssot_tag": provenance.get("target_ssot_tag", ""),
            },
            "common": {
                "param_hash": provenance.get("param_hash", ""),
                "universe_version": provenance.get("universe_version", ""),
                "baseline_ref": provenance.get("baseline_ref", ""),
            },
        }
        for key, value in provenance.items():
            meta[key] = value
        meta["provenance_injected"] = True
        meta["provenance_patch_version"] = "v2.3.1"
        meta["provenance_ts"] = _now_ms()
        # [P3-PATCH] Phase 9 B identity triplet carry-through
        # Propagate phase9_b1_marker from upstream source payload if present,
        # or inject canonical RAM26_ALPHA_0001_b215c119ea23 identity so exec_src carries the full triplet. [Updated from p9B_106 on 2026-05-07T11:55:34Z]
        try:
            upstream_marker = (
                v2_payload.get("phase9_b1_marker")
                or meta.get("phase9_b1_marker")
                or (v2_payload.get("targets", {}) or {}).get("meta", {}).get("phase9_b1_marker")
            )
            # [SAFETY-PATCH-V2 2026-05-06] Carrythrough only — no hardcoded approval fallback.
            if upstream_marker and isinstance(upstream_marker, dict):
                p9b_marker = dict(upstream_marker)
            else:
                p9b_marker = {
                    "marker_version": "phase9_b1_marker_v2_safety_2026_05_06",
                    "source_stage": "ssot_writer_v2_inject",
                    "validation_state": "REJECTED",
                    "rejection_reason": "upstream_marker_absent_at_writer_v2",
                }
            # Ensure validation_state is present (fail-closed default)
            if "validation_state" not in p9b_marker:
                p9b_marker["validation_state"] = "REJECTED"
                p9b_marker["rejection_reason"] = (
                    p9b_marker.get("rejection_reason") or "writer_v2_marker_missing_validation_state"
                )
            meta["phase9_b1_marker"] = p9b_marker
            meta["phase9_b1_marker_version"] = p9b_marker.get("marker_version", "phase9_b1_marker_v2_safety_2026_05_06")
            # Carrythrough fields (best-effort; do NOT inject hardcoded values)
            for _k in ("candidate_id", "candidate_config_sha256", "universe_sha256"):
                _v = p9b_marker.get(_k)
                if _v:
                    meta.setdefault(_k, _v)
        except Exception as _p3_err:
            log("WARN", f"P3_IDENTITY_MARKER_INJECT_FAILED: {_p3_err}")
    except Exception as e:
        log("ERROR", f"Provenance injection failed (safe passthrough): {e}")
    return v2_payload



def _merge_residuals_into_payload(v2_payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        positions = v2_payload.get("targets", {}).get("positions", [])
        if not isinstance(positions, list):
            return v2_payload
        broker_raw = r.hgetall("kis:broker:positions")
        if not broker_raw:
            return v2_payload

        active_symbols = {str(p.get("symbol", "")).upper() for p in positions if isinstance(p, dict) and p.get("symbol")}
        cleaned_positions: List[Dict[str, Any]] = []
        broker_positive_symbols: set[str] = set()

        for sym_bytes, qty_raw in broker_raw.items():
            sym = sym_bytes.decode() if isinstance(sym_bytes, bytes) else str(sym_bytes)
            try:
                if isinstance(qty_raw, bytes):
                    qty_raw = qty_raw.decode()
                qty_data = json.loads(qty_raw) if isinstance(qty_raw, str) and qty_raw.startswith("{") else {"qty": int(float(qty_raw))}
                qty = int(float(qty_data.get("qty", qty_data.get("shares", qty_data.get("economic_qty", 0)))))
            except Exception:
                try:
                    qty = int(float(qty_raw))
                except Exception:
                    continue
            if qty > 0:
                broker_positive_symbols.add(sym.upper())

        for p in positions:
            if not isinstance(p, dict):
                continue
            sym = str(p.get("symbol", "")).upper()
            if p.get("tag") == "residual_holding" and sym and sym not in broker_positive_symbols:
                continue
            cleaned_positions.append(p)

        existing_symbols = {str(p.get("symbol", "")).upper() for p in cleaned_positions if isinstance(p, dict) and p.get("symbol")}
        merged_count = 0
        for sym in sorted(broker_positive_symbols - existing_symbols):
            if sym in active_symbols:
                continue
            # [PATCH-H v1.0] hold_only residual (Patch H)
            # trade_policy="hold_only" 필드를 기입해 orchestrator/order_builder가
            # 잔존 보유를 명확히 HOLD로 해석하도록 한다. tag/side는 하위 호환 유지.
            cleaned_positions.append({
                "symbol": sym,
                "w": 0.0,
                "side": "HOLD",
                "tag": "residual_holding",
                "trade_policy": "hold_only",
                "reason": "writer_residual_merge",
            })
            merged_count += 1

        if merged_count > 0 or len(cleaned_positions) != len(positions):
            v2_payload["targets"]["positions"] = cleaned_positions
            meta = v2_payload.setdefault("targets", {}).setdefault("meta", {})
            meta["writer_residual_merged"] = merged_count
            meta["writer_residual_merge_ts"] = _now_ms()
        return v2_payload
    except Exception as e:
        log("WARN", f"Residual preserve merge failed (fail-open): {e}")
        return v2_payload



def _acquire_canary_lock(timeout_ms: int = 2000) -> bool:
    global _canary_lock_owner
    try:
        token = f"writer-{uuid.uuid4().hex[:12]}"
        acquired = r.set(CANARY_LOCK_KEY, token, nx=True, px=timeout_ms)
        if acquired:
            _canary_lock_owner = token
            return True
        return False
    except Exception:
        _canary_lock_owner = None
        return True



def _release_canary_lock() -> None:
    global _canary_lock_owner
    release_lua = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """
    try:
        if _canary_lock_owner:
            r.eval(release_lua, 1, CANARY_LOCK_KEY, _canary_lock_owner)
    except Exception:
        pass
    _canary_lock_owner = None



def _verify_ttl(key: str, expected_ttl: int) -> None:
    try:
        ttl = r.ttl(key)
        if ttl is None or ttl <= 0:
            r.expire(key, expected_ttl)
            log("ERROR", "TTL missing on protected key; repaired", key=key, repaired_ttl=expected_ttl)
    except Exception as e:
        log("ERROR", "TTL verification failed", key=key, err=str(e))



def _audit_writer_event(event_type: str, payload: Dict[str, Any]) -> None:
    try:
        r.xadd(
            WRITER_EVENT_STREAM,
            {"event_type": event_type, "payload": _safe_json_dumps(payload), "ts": str(_now_ms())},
            maxlen=WRITER_EVENT_MAXLEN,
            approximate=True,
        )
    except Exception:
        pass



def write_health(status: str, detail: Dict[str, Any], force_log: bool = False) -> None:
    global _last_logged_health_fingerprint
    payload = {
        "status": status,
        "version": WRITER_VERSION,
        "pid": os.getpid(),
        "ts": _now_ms(),
        **detail,
    }
    try:
        r.set(WRITER_HEALTH_KEY, _safe_json_dumps(payload), ex=HEALTH_TTL)
        r.set(WRITER_STATUS_KEY, _safe_json_dumps(payload), ex=HEALTH_TTL)
    except Exception as e:
        log("WARN", "Failed to write writer health", err=str(e))
    fingerprint = hashlib.sha256(_safe_json_dumps(payload).encode()).hexdigest()[:16]
    if force_log or fingerprint != _last_logged_health_fingerprint:
        log("INFO" if status == "HEALTHY" else "WARN", f"Writer health -> {status}", **detail)
        _last_logged_health_fingerprint = fingerprint



def _decorate_source_meta(v2_payload: Dict[str, Any], decision: SourceDecision) -> None:
    meta = v2_payload.setdefault("targets", {}).setdefault("meta", {})
    warnings = list(meta.get("warnings", []))
    for warning in decision.warnings:
        if warning not in warnings:
            warnings.append(warning)
    meta["warnings"] = warnings
    meta["source_key"] = decision.source_key or ""
    meta["source_selected"] = decision.source_label
    meta["source_mode"] = decision.source_mode
    meta["source_fallback_active"] = decision.fallback_active
    meta["source_fallback_reason"] = decision.fallback_reason
    meta["source_ts"] = decision.source_ts_ms
    meta["source_age_sec"] = decision.source_age_sec
    meta["source_refreshed_ts"] = _now_ms()
    meta["writer_version"] = WRITER_VERSION
    meta["writer_processed_ts"] = _now_ms()
    meta["writer_processed_date"] = _utc_today()
    meta["source_original_asof"] = v2_payload.get("asof")
    meta["gpu_regime_classifier"] = _read_gpu_regime()



def _evaluate_parity(v2_payload: Dict[str, Any], decision: SourceDecision) -> Dict[str, Any]:
    global _fallback_streak
    market_open = is_us_market_hours()
    meta = v2_payload.setdefault("targets", {}).setdefault("meta", {})

    if decision.fallback_active:
        _fallback_streak += 1
    else:
        _fallback_streak = 0

    reasons: List[str] = []
    state = "HEALTHY"

    if decision.fallback_active:
        reasons.append(f"fallback_active:{decision.fallback_reason}")
        if market_open and STRICT_RUNTIME_SOURCE and _fallback_streak > EXEC_FALLBACK_MAX_CYCLES:
            state = "FAILED"
        elif market_open:
            state = "DEGRADED"
        elif state == "HEALTHY":
            state = "DEGRADED"

    if decision.source_age_sec is not None and decision.source_age_sec > SOURCE_STALE_SEC:
        reasons.append(f"source_stale:{decision.source_age_sec}s")
        if market_open:
            state = "FAILED"
        else:
            state = max(state, "DEGRADED", key=lambda s: {"HEALTHY": 0, "DEGRADED": 1, "FAILED": 2}[s])

    actual_engine = str(v2_payload.get("engine_version", "") or "")
    expected_engine = str(
        meta.get("expected_actual_engine_version", "")
        or meta.get("target_engine_version_expected", "")
        or ""
    )
    if expected_engine and actual_engine and expected_engine != actual_engine:
        reasons.append(f"target_engine_mismatch:{actual_engine}!={expected_engine}")
        if market_open and STRICT_TARGET_ENGINE_MATCH:
            state = "FAILED"
        elif state == "HEALTHY":
            state = "DEGRADED"

    parity_ok = state == "HEALTHY"
    meta["parity_status"] = state
    meta["parity_reasons"] = reasons
    meta["exec_fallback_streak"] = _fallback_streak
    meta["actual_engine_version"] = actual_engine
    meta["expected_actual_engine_version"] = expected_engine

    return {
        "state": state,
        "reasons": reasons,
        "market_open": market_open,
        "fallback_streak": _fallback_streak,
        "parity_ok": parity_ok,
    }



def publish_canary(v2_payload: Dict[str, Any]) -> Tuple[int, str]:
    global _last_published_digest, _unchanged_publish_count

    digest = _compute_digest(v2_payload)
    payload_str = _safe_json_dumps(v2_payload)
    lock_acquired = _acquire_canary_lock(timeout_ms=2000)
    if not lock_acquired:
        log("WARN", "Could not acquire canary lock; proceeding fail-open")

    try:
        pipe = r.pipeline()
        pipe.set(CANARY_KEY, payload_str, ex=CANARY_TTL)
        changed = digest != _last_published_digest
        if changed:
            _unchanged_publish_count = 0
        else:
            _unchanged_publish_count += 1

        if changed or (_unchanged_publish_count % AUDIT_UNCHANGED_EVERY == 0):
            meta = v2_payload.get("targets", {}).get("meta", {})
            pipe.xadd(
                "audit:ssot_v2:writes",
                {
                    "key": CANARY_KEY,
                    "schema_version": "SSOT_TARGET_V2",
                    "ts": str(v2_payload.get("ts", 0)),
                    "n_positions": str(len(v2_payload.get("targets", {}).get("positions", []))),
                    "gross": str(v2_payload.get("targets", {}).get("gross", 0.0)),
                    "correlation_id": str(v2_payload.get("correlation_id", "")),
                    "engine_version": str(v2_payload.get("engine_version", "")),
                    "source_ts": str(meta.get("source_ts", 0)),
                    "source_selected": str(meta.get("source_selected", "")),
                    "source_fallback_active": str(meta.get("source_fallback_active", False)),
                    "parity_status": str(meta.get("parity_status", "")),
                    "payload_sha": digest[:16],
                    "provenance_injected": str(meta.get("provenance_injected", False)),
                    "provenance_patch_version": str(meta.get("provenance_patch_version", "")),
                },
                maxlen=1000,
            )
        pipe.execute()
        _verify_ttl(CANARY_KEY, CANARY_TTL)
        _last_published_digest = digest
        return len(v2_payload.get("targets", {}).get("positions", [])), digest
    finally:
        _release_canary_lock()



def _sleep_interruptible(seconds: int) -> None:
    for _ in range(max(seconds, 0)):
        if _shutdown_requested:
            break
        time.sleep(1)



def main_loop() -> None:
    global _singleton_fd
    _singleton_fd = _acquire_singleton_lock()
    if _singleton_fd is None:
        return

    log(
        "INFO",
        f"Starting SSOT V2 Writer v{WRITER_VERSION}",
        interval=LOOP_INTERVAL,
        canary_key=CANARY_KEY,
        src_key=SRC_KEY,
        exec_src_key=EXEC_SRC_KEY,
        source_mode=SSOT_SOURCE_MODE,
        strict_runtime_source=STRICT_RUNTIME_SOURCE,
        strict_target_engine_match=STRICT_TARGET_ENGINE_MATCH,
        legacy_fallback=ALLOW_LEGACY_FALLBACK,
    )

    while not _shutdown_requested:
        try:
            decision = read_source()
            health_detail = {
                "source_mode": SSOT_SOURCE_MODE,
                "source_selected": decision.source_label,
                "source_key": decision.source_key,
                "source_fallback_active": decision.fallback_active,
                "source_fallback_reason": decision.fallback_reason,
                "source_age_sec": decision.source_age_sec,
                "warnings": decision.warnings,
            }

            if not decision.payload:
                write_health("FAILED", {**health_detail, "reason": "all_sources_missing"}, force_log=True)
                _audit_writer_event("writer_source_missing", health_detail)
                _sleep_interruptible(LOOP_INTERVAL)
                continue

            if decision.payload.get("schema_version") == "SSOT_TARGET_V2" and isinstance(decision.payload.get("targets"), dict):
                v2 = copy.deepcopy(decision.payload)
            else:
                v2 = transform_to_v2(decision.payload, decision.source_label)

            _decorate_source_meta(v2, decision)
            v2 = apply_engine_router(v2)
            v2 = inject_champion_provenance(v2)
            _decorate_source_meta(v2, decision)
            parity = _evaluate_parity(v2, decision)

            v2["ts"] = _now_ms()
            v2["correlation_id"] = str(uuid.uuid4())
            if not v2.get("asof"):
                v2["asof"] = _utc_today()

            v2 = _merge_residuals_into_payload(v2)

            if parity["state"] == "FAILED" and parity["market_open"]:
                detail = {
                    **health_detail,
                    "parity_status": parity["state"],
                    "parity_reasons": parity["reasons"],
                    "fallback_streak": parity["fallback_streak"],
                }
                write_health("FAILED", detail, force_log=True)
                _audit_writer_event("writer_publish_blocked", detail)
                log("ERROR", "Canary publish blocked to avoid masking stale/fallback source", **detail)
                _sleep_interruptible(LOOP_INTERVAL)
                continue

            n_positions, digest = publish_canary(v2)
            meta = v2.get("targets", {}).get("meta", {})
            write_health(
                "HEALTHY" if parity["state"] == "HEALTHY" else "DEGRADED",
                {
                    **health_detail,
                    "parity_status": parity["state"],
                    "parity_reasons": parity["reasons"],
                    "fallback_streak": parity["fallback_streak"],
                    "payload_sha": digest[:16],
                    "n_positions": n_positions,
                    "engine_version": v2.get("engine_version", "unknown"),
                },
            )
            log(
                "INFO",
                f"Canary published: {n_positions} positions, gross={v2['targets']['gross']:.4f}, engine={v2.get('engine_version', 'unknown')}, source_ts={meta.get('source_ts', 0)}, parity={parity['state']}",
                source=decision.source_key,
                payload_sha=digest[:16],
            )
        except Exception as e:
            detail = {"error": str(e), "source_mode": SSOT_SOURCE_MODE}
            write_health("FAILED", detail, force_log=True)
            _audit_writer_event("writer_cycle_error", detail)
            log("ERROR", f"Writer error: {e}")
        _sleep_interruptible(LOOP_INTERVAL)

    write_health("STOPPED", {"reason": "shutdown"}, force_log=True)
    _release_singleton_lock()


if __name__ == "__main__":
    main_loop()
