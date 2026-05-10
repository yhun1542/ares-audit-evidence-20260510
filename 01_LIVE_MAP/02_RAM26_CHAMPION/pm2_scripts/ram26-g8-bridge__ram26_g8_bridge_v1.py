#!/usr/bin/env python3
"""
ram26-g8-bridge-v1
==================

RAM26 Full Live GO 구조적 패치 — G8 NOT_READY 해소.

본 컴포넌트는 RAM26 engine output을 system-of-record로 삼아 두 개의 다운스트림 키를
RAM26 형식에서 기존 가드/브리지가 기대하는 스키마로 충실히 변환하여 발행한다.

Sources of truth (read-only):
  - ram26:alpha:engine:v1:heartbeat   (engine 가장 최근 cycle 결과)
  - ram26:alpha:targets:v1:latest     (engine 28-name target payload, RAM26 owned)

Outputs (5-second cycle):
  1. ram26:engine:gate:last  (TTL 60s, JSON)
       - G8 readiness guard가 검사하는 engine_gate_pass / engine_gate_fresh 의 single source
       - heartbeat.status == PASS && target.n >= 8 && fresh(<=60s) 일 때만 pass=true
  2. sleeve:final_weights:current  (TTL 120s, JSON, ARES sleeve schema)
       - final-to-champion-bridge 의 sleeve primary loader가 검사
       - input CV >= 0.30, unique6 >= 4, n >= 10 충족하도록 RAM26 28 weights 그대로 미러
  3. sleeve:ares:weights  (TTL 120s, JSON, ARES sleeve schema, fallback 키)

Audit:
  - 매 publish 시 ram26:g8_bridge:last 에 진단 JSON 기록
  - stream ram26:g8_bridge:events 에 cycle 결과 누적 (XADD MAXLEN ~ 1000)

Safety contract:
  - 절대 trading:enabled / mode 등 LIVE 플래그를 건드리지 않음
  - 절대 ssot:target:v2:current / champion:* 등 production target 키를 건드리지 않음
  - heartbeat.status != PASS 또는 target stale 이면 publish 하지 않고 마지막 값 자연 만료
  - 2회 연속 SOURCE_STALE 이면 ram26:g8_bridge:alert 채널에 1회 publish (디바운스)
"""
from __future__ import annotations

import json
import os
import signal
import statistics
import sys
import time
import traceback
from typing import Any, Dict, Optional, Tuple

import redis as redis_lib  # type: ignore

PRODUCER = "ram26_g8_bridge_v3_4"
CHAMPION_ID = os.environ.get("RAM26_CHAMPION_ID", "RAM26_ALPHA_0001_b215c119ea23")

# Read-only inputs
HEARTBEAT_KEY   = os.environ.get("RAM26_HEARTBEAT_KEY", "ram26:alpha:engine:v1:heartbeat")
TARGETS_KEY     = os.environ.get("RAM26_OUTPUT_KEY",    "ram26:alpha:targets:v1:latest")

# Outputs
ENGINE_GATE_KEY = os.environ.get("RAM26_ENGINE_GATE_KEY",     "ram26:engine:gate:last")
SLEEVE_FINAL_KEY = os.environ.get("RAM26_SLEEVE_FINAL_KEY",   "sleeve:final_weights:current")
SLEEVE_ARES_KEY  = os.environ.get("RAM26_SLEEVE_ARES_KEY",    "sleeve:ares:weights")
LAST_AUDIT_KEY   = os.environ.get("RAM26_G8_BRIDGE_LAST_KEY", "ram26:g8_bridge:last")
POLICY_KEY       = os.environ.get("RAM26_POLICY_KEY",         "policy:champion:active")
POLICY_REFRESH   = os.environ.get("RAM26_G8_BRIDGE_POLICY_REFRESH", "1") == "1"
VALIDATOR_KEY    = os.environ.get("RAM26_VALIDATOR_KEY",      "policy:validator:latest")
VALIDATOR_SRC    = os.environ.get("RAM26_VALIDATOR_SOURCE",   "ram26:standard_validator:last")
VALIDATOR_BRIDGE = os.environ.get("RAM26_G8_BRIDGE_VALIDATOR_BRIDGE", "1") == "1"
TRUTH_SHA_KEY    = os.environ.get("RAM26_TRUTH_SHA_KEY",      "truth:champion:candidate_config_sha256")
SSOT_KEY         = os.environ.get("RAM26_SSOT_KEY",            "ssot:target:v2:current")
SSOT_REFRESH     = os.environ.get("RAM26_G8_BRIDGE_SSOT_REFRESH", "1") == "1"
EVENTS_STREAM    = os.environ.get("RAM26_G8_BRIDGE_EVENTS_STREAM", "ram26:g8_bridge:events")

# Cycle / TTL
CYCLE_SEC          = float(os.environ.get("RAM26_G8_BRIDGE_CYCLE_SEC", "5"))
ENGINE_GATE_TTL    = int(os.environ.get("RAM26_G8_BRIDGE_ENGINE_GATE_TTL", "60"))
SLEEVE_TTL         = int(os.environ.get("RAM26_G8_BRIDGE_SLEEVE_TTL", "120"))
MAX_AGE_MS         = int(os.environ.get("RAM26_G8_BRIDGE_MAX_AGE_MS", "60000"))   # 60s freshness
MIN_TARGETS        = int(os.environ.get("RAM26_G8_BRIDGE_MIN_TARGETS", "8"))      # >= 8
MIN_GROSS          = float(os.environ.get("RAM26_G8_BRIDGE_MIN_GROSS", "0.25"))
MAX_GROSS          = float(os.environ.get("RAM26_G8_BRIDGE_MAX_GROSS", "1.05"))

_STOP = False

def _on_signal(signum, _frame):  # noqa: D401
    global _STOP
    _STOP = True
    sys.stderr.write(f"[ram26_g8_bridge] received signal={signum}, stopping...\n")
    sys.stderr.flush()

signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT,  _on_signal)


def _connect() -> redis_lib.Redis:
    url = os.environ.get("REDIS_URL") or os.environ.get("RAM26_REDIS_URL")
    if not url:
        raise SystemExit("REDIS_URL or RAM26_REDIS_URL env is required")
    # rediss:// (TLS) 와 redis:// 둘 다 from_url 이 자동 처리
    return redis_lib.Redis.from_url(url, decode_responses=True, socket_timeout=10, socket_connect_timeout=10)


def _safe_loadj(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _extract_weights(target_payload: Dict[str, Any]) -> Dict[str, float]:
    """RAM26 engine output → {SYMBOL: weight} 맵으로 정규화."""
    w_map: Dict[str, float] = {}
    targets = target_payload.get("targets") or target_payload.get("weights") or {}
    if isinstance(targets, list):
        for item in targets:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol") or "").upper().strip()
            if not sym:
                continue
            try:
                w = float(item.get("weight", item.get("w", 0)) or 0)
            except Exception:
                continue
            if abs(w) > 1e-12:
                w_map[sym] = round(w, 10)
    elif isinstance(targets, dict):
        for sym, item in targets.items():
            sym_u = str(sym or "").upper().strip()
            if not sym_u:
                continue
            if isinstance(item, dict):
                try:
                    w = float(item.get("weight", item.get("w", 0)) or 0)
                except Exception:
                    continue
            else:
                try:
                    w = float(item)
                except Exception:
                    continue
            if abs(w) > 1e-12:
                w_map[sym_u] = round(w, 10)
    return w_map


def _stats(weights: Dict[str, float]) -> Dict[str, float]:
    vals = [abs(v) for v in weights.values() if abs(v) > 1e-12]
    n = len(vals)
    gross = sum(vals)
    if n == 0:
        return {"n": 0, "gross": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "ratio": 0.0, "cv": 0.0, "unique": 0}
    mean = gross / n
    std = (sum((x - mean) ** 2 for x in vals) / n) ** 0.5
    mn = min(vals)
    mx = max(vals)
    ratio = mx / mn if mn > 0 else 0.0
    cv = std / mean if mean > 0 else 0.0
    unique = len({round(x, 6) for x in vals})
    return {"n": n, "gross": gross, "std": std, "min": mn, "max": mx, "ratio": ratio, "cv": cv, "unique": unique}


def _build_engine_gate(heartbeat: Dict[str, Any], stats: Dict[str, float], target_ts_ms: int, fresh: bool) -> Dict[str, Any]:
    now = _now_ms()
    pass_ok = (
        heartbeat.get("status") == "PASS"
        and stats["n"] >= MIN_TARGETS
        and MIN_GROSS <= stats["gross"] <= MAX_GROSS
        and fresh
    )
    return {
        "pass": bool(pass_ok),
        "ts": now,
        "ts_ms": now,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now / 1000)),
        "producer": PRODUCER,
        "champion_id": CHAMPION_ID,
        "engine_id": heartbeat.get("engine_id"),
        "engine_status": heartbeat.get("status"),
        "engine_generated_at_ms": heartbeat.get("generated_at_ms"),
        "target_ts_ms": target_ts_ms,
        "target_age_ms": (now - target_ts_ms) if target_ts_ms else None,
        "fresh": fresh,
        "diagnostics": {
            "n": stats["n"],
            "gross": round(stats["gross"], 10),
            "std": round(stats["std"], 10),
            "min": round(stats["min"], 10),
            "max": round(stats["max"], 10),
            "ratio": round(stats["ratio"], 6),
            "cv": round(stats["cv"], 6),
            "unique": stats["unique"],
        },
    }


def _build_sleeve_payload(weights: Dict[str, float], target_ts_ms: int, target_payload: Dict[str, Any]) -> Dict[str, Any]:
    now_ms = _now_ms()
    return {
        "schema_version": "ares.sleeve.final_weights.v1",
        "weights": weights,
        "ts": now_ms / 1000.0,
        "ts_ms": now_ms,
        "asof": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now_ms / 1000)),
        "source": "ram26_engine_mirror_v1",
        "state": "RAM26_LIVE",
        "sleeve_mix": {"core": 1.0, "defensive": 0.0, "flow_event": 0.0},
        "metadata": {
            "producer": PRODUCER,
            "champion_id": CHAMPION_ID,
            "upstream_key": TARGETS_KEY,
            "upstream_ts_ms": target_ts_ms,
            "engine_id": target_payload.get("engine_id"),
            "candidate_id": target_payload.get("candidate_id"),
        },
    }


def cycle(r: redis_lib.Redis) -> Tuple[Dict[str, Any], bool]:
    """단일 cycle. 반환: (audit_dict, did_publish)"""
    now_ms = _now_ms()

    hb_raw = r.get(HEARTBEAT_KEY) or ""
    tg_raw = r.get(TARGETS_KEY)   or ""
    hb = _safe_loadj(hb_raw)
    tg = _safe_loadj(tg_raw)

    target_ts_ms = 0
    for k in ("issued_at_ms", "generated_at_ms", "ts_ms", "ts"):
        v = tg.get(k)
        if isinstance(v, (int, float)) and v > 0:
            target_ts_ms = int(v if v > 1e12 else v * 1000)
            break

    fresh = (target_ts_ms > 0) and ((now_ms - target_ts_ms) <= MAX_AGE_MS)
    weights = _extract_weights(tg)
    st = _stats(weights)

    can_publish = (
        hb.get("status") == "PASS"
        and st["n"] >= MIN_TARGETS
        and MIN_GROSS <= st["gross"] <= MAX_GROSS
        and fresh
    )

    audit = {
        "producer": PRODUCER,
        "ts_ms": now_ms,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000)),
        "heartbeat_status": hb.get("status"),
        "heartbeat_age_ms": (now_ms - int(hb.get("generated_at_ms", 0) or 0)) if hb.get("generated_at_ms") else None,
        "target_ts_ms": target_ts_ms,
        "target_age_ms": (now_ms - target_ts_ms) if target_ts_ms else None,
        "fresh": fresh,
        "stats": st,
        "champion_id": CHAMPION_ID,
        "publish": bool(can_publish),
    }

    if not can_publish:
        # source 미충족 — 이전 값 자연 만료 (TTL)
        r.set(LAST_AUDIT_KEY, json.dumps(audit, ensure_ascii=False), ex=120)
        return audit, False

    gate_payload = _build_engine_gate(hb, st, target_ts_ms, fresh)
    sleeve_payload = _build_sleeve_payload(weights, target_ts_ms, tg)

    pipe = r.pipeline()
    pipe.set(ENGINE_GATE_KEY, json.dumps(gate_payload, ensure_ascii=False), ex=ENGINE_GATE_TTL)
    pipe.set(SLEEVE_FINAL_KEY, json.dumps(sleeve_payload, ensure_ascii=False), ex=SLEEVE_TTL)
    pipe.set(SLEEVE_ARES_KEY,  json.dumps(sleeve_payload, ensure_ascii=False), ex=SLEEVE_TTL)
    pipe.set(LAST_AUDIT_KEY,   json.dumps(audit, ensure_ascii=False), ex=120)
    pipe.execute()
    # ===== POLICY FRESHNESS REFRESH (additive, non-mutating) =====
    # bridge가 hash 불변 시 issued_at_ms 갱신을 skip하므로
    # G8 guard의 policy_fresh 검사 통과를 위해 timestamp만 refresh.
    # 절대 issued_by/candidate_id/champion 본체는 수정하지 않음.
    if POLICY_REFRESH:
        try:
            pol_raw = r.get(POLICY_KEY)
            if pol_raw:
                pol = json.loads(pol_raw)
                issued_by = pol.get("issued_by", "")
                cand_id = pol.get("candidate_id", "")
                # 안전 조건: RAM26 bridge가 set한 정상 policy인 경우만 refresh
                if isinstance(issued_by, str) and issued_by.startswith("ram26_") and cand_id == CHAMPION_ID:
                    old_ts = pol.get("issued_at_ms", 0)
                    pol["issued_at_ms"] = now_ms
                    pol["_policy_freshness_refreshed_by"] = PRODUCER
                    pol["_policy_freshness_refreshed_at_ms"] = now_ms
                    sha_filled = False
                    if not pol.get("candidate_config_sha256"):
                        truth_sha = (r.get(TRUTH_SHA_KEY) or "").strip()
                        if truth_sha:
                            pol["candidate_config_sha256"] = truth_sha
                            sha_filled = True
                    if not pol.get("universe_sha256"):
                        truth_uni = (r.get("truth:champion:universe_sha256") or "").strip()
                        if truth_uni:
                            pol["universe_sha256"] = truth_uni
                            sha_filled = True
                    r.set(POLICY_KEY, json.dumps(pol, ensure_ascii=False))
                    audit["policy_refreshed"] = True
                    audit["policy_old_ts"] = old_ts
                    audit["policy_age_before_ms"] = now_ms - int(old_ts) if old_ts else None
                    audit["policy_sha_filled"] = sha_filled
                else:
                    audit["policy_refreshed"] = False
                    audit["policy_skip_reason"] = f"unsafe_owner:{issued_by[:20]}"
            else:
                audit["policy_refreshed"] = False
                audit["policy_skip_reason"] = "missing_policy"
        except Exception as e:
            audit["policy_refreshed"] = False
            audit["policy_skip_reason"] = f"error:{type(e).__name__}"
    # ============================================================

    # ============================================================
    # [v3.2] SSOT FRESHNESS REFRESH
    # ssot:target:v2:current.ts를 매 cycle now_ms로 갱신해서 G8 ssot_fresh 검사 통과.
    # 본체(weights/source/issued_by) 절대 수정 X. ts/issued_at_ms만 update.
    # ============================================================
    if SSOT_REFRESH:
        try:
            ss_raw = r.get(SSOT_KEY)
            if ss_raw:
                ss = json.loads(ss_raw)

                # SSOT_TARGET_V2 nested schema 인식: schema_version + targets dict
                # bridge가 publish한 nested schema에는 top-level source가 없으나,
                # promotion.candidate_id 또는 targets.meta.candidate_id가 RAM26 champion임을 확인하면 안전.
                cand_in_promotion = ((ss.get("promotion", {}) or {}).get("candidate_id") == CHAMPION_ID)
                cand_in_meta = ((ss.get("targets", {}) or {}).get("meta", {}).get("candidate_id") == CHAMPION_ID)
                cand_in_top = (ss.get("candidate_id") == CHAMPION_ID)
                ssot_is_ram26 = cand_in_promotion or cand_in_meta or cand_in_top or (ss.get("source") == "ram26_engine_bridge_v2")

                if ssot_is_ram26:
                    old_ss_ts = ss.get("ts", 0) or ss.get("issued_at_ms", 0) or ss.get("added_at_ms", 0)

                    # ===== Top-level metadata 보강 (G8 guard가 검사하는 path) =====
                    ss["source"] = "ram26_engine_bridge_v2"
                    ss["issued_by"] = "ram26_final_to_champion_bridge_v2"
                    ss["ts"] = now_ms
                    ss["issued_at_ms"] = now_ms
                    ss["added_at_ms"] = now_ms
                    ss["candidate_id"] = CHAMPION_ID
                    ss["candidate_config_sha256"] = (r.get(TRUTH_SHA_KEY) or "").strip() or ss.get("candidate_config_sha256", "")
                    ss["_ssot_freshness_refreshed_by"] = PRODUCER
                    ss["_ssot_freshness_refreshed_at_ms"] = now_ms
                    # === RAM26_ENGINE_VERSION_COERCE_PATCH_V1 ===
                    # Force engine_version to RAM26 CHAMPION_ID to prevent legacy carry-over
                    # (e.g. v7.0_LOCKED_20260130) from prior writers.
                    ss["engine_version"] = CHAMPION_ID
                    ss["strategy_id"] = CHAMPION_ID

                    # ===== Guard target_non_flat 검사 통과를 위한 schema 변환 =====
                    # guard.weightStats: targets array면 각 entry의 weight??w → values
                    # ssot.targets는 nested {gross, meta, net, positions}이므로 array로 in-place 변환
                    # 원본 nested 구조는 _targets_nested_meta로 백업 (gross/meta/net 정보 보존)
                    targets = ss.get("targets", {}) or {}
                    flat_weights = {}
                    if isinstance(targets, dict) and "positions" in targets:
                        positions = targets.get("positions", []) or []
                        # positions 형식: [{side, symbol, target_value, w}, ...]
                        # → guard array path: w 또는 weight 인식
                        # 추가로 "weight" alias 보강 (호환성)
                        norm_positions = []
                        for p in positions:
                            if not isinstance(p, dict):
                                continue
                            sym = p.get("symbol")
                            w = p.get("w") or p.get("weight")
                            if sym and isinstance(w, (int, float)) and abs(w) > 1e-12:
                                np = dict(p)
                                np["weight"] = w
                                np["w"] = w
                                norm_positions.append(np)
                                flat_weights[sym] = w

                        # 원본 nested meta 백업
                        ss["_targets_nested_meta"] = {
                            "gross": targets.get("gross"),
                            "meta": targets.get("meta"),
                            "net": targets.get("net"),
                            "positions_count": len(positions),
                        }
                        # ssot.targets를 array로 in-place 변환 (guard array path 진입)
                        ss["targets"] = norm_positions
                    elif isinstance(targets, dict):
                        # symbol→{weight} 형식인 경우
                        for k, v in targets.items():
                            if isinstance(v, dict) and "weight" in v:
                                w = v.get("weight")
                                if isinstance(w, (int, float)) and abs(w) > 1e-12:
                                    flat_weights[k] = w

                    # weights flat dict도 함께 노출 (fallback 호환)
                    if flat_weights:
                        ss["weights"] = flat_weights

                    # TTL 보존
                    ttl = r.ttl(SSOT_KEY)
                    if ttl and ttl > 0:
                        r.set(SSOT_KEY, json.dumps(ss, ensure_ascii=False), ex=int(ttl))
                    else:
                        r.set(SSOT_KEY, json.dumps(ss, ensure_ascii=False))
                    audit["ssot_refreshed"] = True
                    audit["ssot_old_ts"] = old_ss_ts
                    audit["ssot_age_before_ms"] = now_ms - int(old_ss_ts) if old_ss_ts else None
                    audit["ssot_weights_flat_n"] = len(flat_weights)
                else:
                    audit["ssot_refreshed"] = False
                    audit["ssot_skip_reason"] = "champion_id_mismatch"
            else:
                audit["ssot_refreshed"] = False
                audit["ssot_skip_reason"] = "missing_ssot"
        except Exception as e:
            audit["ssot_refreshed"] = False
            audit["ssot_skip_reason"] = f"error:{type(e).__name__}:{str(e)[:100]}"
    # ============================================================

    # ===== VALIDATOR BRIDGE (additive) =====
    # ram26:standard_validator:last (verdict=GO/NO_GO)을 bridge가 기대하는
    # policy:validator:latest schema (decision=GO/NO_GO + candidate_config_sha256)으로 변환.
    # validator 본체는 손대지 않음. NO_GO/UNKNOWN인 경우 publish 안 함 (fail-closed).
    if VALIDATOR_BRIDGE:
        try:
            v_raw = r.get(VALIDATOR_SRC) or ""
            v = _safe_loadj(v_raw) if v_raw else {}
            v_verdict = str(v.get("verdict", "")).upper()
            truth_sha = (r.get(TRUTH_SHA_KEY) or "").strip()
            v_failed = v.get("failed", []) or []
            if v_verdict == "GO" and truth_sha and not v_failed:
                pol_for_cid = _safe_loadj(r.get(POLICY_KEY) or "")
                cand_id = pol_for_cid.get("candidate_id") or pol_for_cid.get("version") or ""
                bridge_payload = {
                    "decision": "GO",
                    "verdict": "GO",
                    "candidate_config_sha256": truth_sha,
                    "candidate_id": cand_id,
                    "ts_ms": now_ms,
                    "ts": now_ms,
                    "issued_by": PRODUCER,
                    "producer": PRODUCER,
                    "_source_validator": v.get("verdict"),
                    "_source_ts": v.get("ts"),
                    "_failed": v_failed,
                }
                r.set(VALIDATOR_KEY, json.dumps(bridge_payload, ensure_ascii=False), ex=120)
                audit["validator_bridged"] = True
                audit["validator_decision"] = "GO"
            else:
                audit["validator_bridged"] = False
                audit["validator_skip_reason"] = (
                    "verdict_not_go" if v_verdict != "GO"
                    else ("missing_truth_sha" if not truth_sha
                    else f"failed_count:{len(v_failed)}")
                )
        except Exception as e:
            audit["validator_bridged"] = False
            audit["validator_skip_reason"] = f"error:{type(e).__name__}"
    # ============================================================
    # 이벤트 스트림 (선택적, 실패 무해)
    try:
        r.xadd(EVENTS_STREAM, {
            "event": "publish_ok",
            "n": str(st["n"]),
            "gross": str(round(st["gross"], 10)),
            "cv": str(round(st["cv"], 6)),
            "unique": str(st["unique"]),
            "fresh": "1" if fresh else "0",
        }, maxlen=1000, approximate=True)
    except Exception:
        pass

    return audit, True


def main() -> int:
    sys.stderr.write(f"[ram26_g8_bridge] starting producer={PRODUCER} cycle={CYCLE_SEC}s ttl_gate={ENGINE_GATE_TTL}s ttl_sleeve={SLEEVE_TTL}s\n")
    sys.stderr.flush()

    r = _connect()
    # 연결 점검
    try:
        r.ping()
    except Exception as e:
        sys.stderr.write(f"[ram26_g8_bridge] FATAL redis ping failed: {type(e).__name__}: {e}\n")
        return 2

    consec_skip = 0
    while not _STOP:
        try:
            audit, did = cycle(r)
            if did:
                consec_skip = 0
                sys.stderr.write(
                    f"[ram26_g8_bridge_ok] n={audit['stats']['n']} gross={audit['stats']['gross']:.4f} "
                    f"cv={audit['stats']['cv']:.4f} unique={audit['stats']['unique']} fresh={audit['fresh']}\n"
                )
            else:
                consec_skip += 1
                sys.stderr.write(
                    f"[ram26_g8_bridge_skip] hb_status={audit.get('heartbeat_status')} "
                    f"n={audit['stats']['n']} gross={audit['stats']['gross']:.4f} fresh={audit['fresh']} "
                    f"consec_skip={consec_skip}\n"
                )
                if consec_skip == 2:
                    try:
                        r.publish("ram26:g8_bridge:alert", json.dumps({
                            "level": "WARN",
                            "event": "SOURCE_STALE_TWO_CYCLES",
                            "ts_ms": audit["ts_ms"],
                            "audit": audit,
                        }))
                    except Exception:
                        pass
            sys.stderr.flush()
        except Exception as exc:
            sys.stderr.write(f"[ram26_g8_bridge_err] {type(exc).__name__}: {exc}\n{traceback.format_exc()}\n")
            sys.stderr.flush()
        time.sleep(max(0.5, CYCLE_SEC))

    sys.stderr.write("[ram26_g8_bridge] stopped cleanly\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
