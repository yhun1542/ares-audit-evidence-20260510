#!/usr/bin/env python3
"""
NEXUS V5.6 compatibility bridge
===============================

Purpose
-------
Keep GPT V5.5 live autopilot as the single decision-maker while publishing
Claude-style Redis keys / unified regime metadata for compatibility,
observability and downstream tooling.

Design
------
- Source of truth for trading decisions: ares_v55_live_autopilot.py
- Bridge responsibilities only:
  1) Read V5.5 target / decision / metrics / feedback keys
  2) Publish Claude-compatible keys:
       - nexus:target:latest / history
       - nexus:confidence:latest
       - nexus:universe:selected
       - nexus:regime:latest
       - regime:unified:current
       - ares:feedback:v3:* compatibility metrics
  3) If legacy kernel keys exist, compute unified regime using both views.

This bridge NEVER emits orders and NEVER mutates alpha core parameters.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    import redis  # type: ignore
except Exception:
    redis = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ares.v56.bridge")


class V55Keys:
    TARGET_LAST = "ares:v55:target:last"
    TARGET_META = "ares:v55:target:meta"
    METRICS = "ares:v55:metrics:latest"
    DECISION_LAST = "ares:v55:decision:last"
    READINESS = "ares:v55:live:readiness"
    HEARTBEAT = "ares:v55:live:heartbeat"
    STATE = "ares:v55:state:summary"
    ROLLING_ACCURACY = "ares:v55:feedback:rolling_accuracy"
    AUTOTUNE = "ares:v55:feedback:autotune"
    FLAP_COUNT = "ares:v55:feedback:flap_count"


class ClaudeKeys:
    TARGET_LATEST = "nexus:target:latest"
    TARGET_HISTORY = "nexus:target:history"
    CONFIDENCE = "nexus:confidence:latest"
    UNIVERSE_SELECTED = "nexus:universe:selected"
    REGIME_NEXUS = "nexus:regime:latest"
    HEARTBEAT = "nexus:alpha:heartbeat"
    HALT = "nexus:halt"
    REGIME_UNIFIED = "regime:unified:current"
    FEEDBACK_ACCURACY = "ares:feedback:v3:accuracy_rolling"
    FEEDBACK_DAMPEN = "ares:feedback:v3:flap_dampen"
    NEXUS_HIT_RATE = "ares:feedback:v3:nexus_hit_rate"
    UNIVERSE_ALPHA = "ares:feedback:v3:universe_alpha_bps"
    REBAL_DECISION = "ares:rebal:v3:decision"
    KERNEL_HEARTBEAT = "ares:kernel:heartbeat"
    LEGACY_KERNEL_STATE = "ares:kernel:state"
    LEGACY_FINAL_REGIME = "regime:final:current"


SEVERITY = {"BULL": 0, "NORMAL": 1, "CAUTION": 2, "BEAR": 3, "CRISIS": 4}
REV_SEVERITY = {0: "BULL", 1: "NORMAL", 2: "CAUTION", 3: "BEAR", 4: "CRISIS"}
KERNEL_TO_NEXUS = {"CRISIS": 0, "BEAR": 1, "NORMAL": 2, "BULL": 3, "CAUTION": 2}
NEXUS_TO_KERNEL = {0: "CRISIS", 1: "BEAR", 2: "NORMAL", 3: "BULL", 4: "BULL", 5: "CRISIS"}


def _parse_obj(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_unified_regime(v55_regime: str, nexus_regime_id: int,
                           feedback_accuracy: float = 0.5,
                           legacy_kernel_regime: Optional[str] = None) -> str:
    """Blend severity. If legacy kernel exists, use it. Otherwise use V5.5 regime."""
    base_regime = (legacy_kernel_regime or v55_regime or "CAUTION").upper()
    k_sev = SEVERITY.get(base_regime, 2)
    n_regime_str = NEXUS_TO_KERNEL.get(nexus_regime_id, "CAUTION")
    n_sev = SEVERITY.get(n_regime_str, 2)

    if feedback_accuracy > 0.80:
        unified_sev = round(0.4 * k_sev + 0.6 * n_sev)
    elif feedback_accuracy < 0.50:
        unified_sev = round(0.8 * k_sev + 0.2 * n_sev)
    else:
        unified_sev = max(k_sev, n_sev)
    return REV_SEVERITY.get(int(clamp(unified_sev, 0, 4)), "CAUTION")


@dataclass
class BridgeConfig:
    redis_url: str = "redis://localhost:6379/0"
    interval_sec: int = 15
    history_keep: int = 500
    ttl_sec: int = 300
    min_weight_publish: float = 0.001


class RedisBridge:
    def __init__(self, cfg: BridgeConfig):
        if redis is None:
            raise RuntimeError("redis package is required for nexus_bridge_v56_compat.py")
        self.cfg = cfg
        self.r = redis.from_url(cfg.redis_url, decode_responses=True)

    def get_json(self, key: str, default: Any = None) -> Any:
        try:
            v = self.r.get(key)
            if not v:
                return default
            return _parse_obj(v)
        except Exception:
            return default

    def get_hash(self, key: str) -> Dict[str, Any]:
        try:
            h = self.r.hgetall(key)
            return {k: _parse_obj(v) for k, v in h.items()} if h else {}
        except Exception:
            return {}

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            v = self.r.get(key)
            return float(v) if v is not None else default
        except Exception:
            return default

    def publish_history(self, key: str, payload: Dict[str, Any]):
        raw = json.dumps(payload, default=str)
        self.r.lpush(key, raw)
        self.r.ltrim(key, 0, self.cfg.history_keep - 1)

    def _read_legacy_kernel_regime(self) -> Optional[str]:
        h = self.get_hash(ClaudeKeys.LEGACY_FINAL_REGIME)
        if isinstance(h, dict):
            rg = h.get("regime") or h.get("effective_regime") or h.get("state")
            if rg:
                return str(rg).upper()
        h2 = self.get_hash(ClaudeKeys.LEGACY_KERNEL_STATE)
        if isinstance(h2, dict):
            rg = h2.get("regime") or h2.get("effective_regime") or h2.get("state")
            if rg:
                return str(rg).upper()
        return None

    def bridge_once(self) -> Dict[str, Any]:
        target = self.get_json(V55Keys.TARGET_LAST, {}) or {}
        meta = self.get_json(V55Keys.TARGET_META, {}) or {}
        metrics = self.get_json(V55Keys.METRICS, {}) or {}
        decision = self.get_json(V55Keys.DECISION_LAST, {}) or {}
        readiness = self.get_json(V55Keys.READINESS, {}) or {}
        state = self.get_json(V55Keys.STATE, {}) or {}
        feedback_accuracy = self.get_float(V55Keys.ROLLING_ACCURACY, 0.5)
        flap_count = self.get_float(V55Keys.FLAP_COUNT, 0.0)
        legacy_kernel_regime = self._read_legacy_kernel_regime()

        symbols = target.get("symbols", []) or []
        weights = target.get("target", []) or []
        selected = target.get("selected_universes", []) or []
        confidence = float(target.get("confidence", metrics.get("confidence", 0.5) or 0.5))
        v55_regime = str(target.get("regime", metrics.get("regime", "CAUTION")) or "CAUTION").upper()
        decision_name = str(target.get("decision", decision.get("decision", "DEFER")) or "DEFER")
        exposure = float(metrics.get("target_exposure", sum(float(w) for w in weights if isinstance(w, (int, float)))) or 0.0)
        shadow_cost_bps = float(meta.get("shadow_cost_bps", 0.0) or 0.0)
        actual_cost_bps = float(meta.get("actual_cost_bps", 0.0) or 0.0)
        turnover = float(meta.get("turnover", metrics.get("last_turnover", 0.0)) or 0.0)

        # Publish Claude-compatible target payload
        weight_dict: Dict[str, float] = {}
        for sym, w in zip(symbols, weights):
            try:
                wf = float(w)
            except Exception:
                continue
            if wf > self.cfg.min_weight_publish:
                weight_dict[str(sym)] = round(wf, 8)

        nexus_regime_id = KERNEL_TO_NEXUS.get(v55_regime, 2)
        target_payload = {
            "weights": weight_dict,
            "exposure": round(exposure, 6),
            "regime_id": int(nexus_regime_id),
            "confidence": round(confidence, 6),
            "selected_universes": list(selected),
            "n_positions": len(weight_dict),
            "decision": decision_name,
            "actual_cost_bps": actual_cost_bps,
            "shadow_cost_bps": shadow_cost_bps,
            "turnover": turnover,
            "ts": time.time(),
            "source": "ares_v55_live_autopilot",
        }
        self.r.setex(ClaudeKeys.TARGET_LATEST, self.cfg.ttl_sec, json.dumps(target_payload, default=str))
        self.publish_history(ClaudeKeys.TARGET_HISTORY, target_payload)
        self.r.setex(ClaudeKeys.CONFIDENCE, self.cfg.ttl_sec, str(round(confidence, 6)))
        self.r.setex(ClaudeKeys.UNIVERSE_SELECTED, self.cfg.ttl_sec, ",".join(selected))
        self.r.setex(ClaudeKeys.REGIME_NEXUS, self.cfg.ttl_sec, str(nexus_regime_id))
        self.r.setex(ClaudeKeys.HEARTBEAT, self.cfg.ttl_sec, str(time.time()))

        # Feedback compatibility metrics
        hit_rate = clamp(0.5 * feedback_accuracy + 0.5 * clamp(confidence, 0.0, 1.0), 0.0, 1.0)
        dampen = clamp(1.0 + max(0.0, flap_count - 2.0) * 0.10, 1.0, 2.0)
        self.r.setex(ClaudeKeys.FEEDBACK_ACCURACY, self.cfg.ttl_sec, str(round(feedback_accuracy, 6)))
        self.r.setex(ClaudeKeys.NEXUS_HIT_RATE, self.cfg.ttl_sec, str(round(hit_rate, 6)))
        self.r.setex(ClaudeKeys.FEEDBACK_DAMPEN, self.cfg.ttl_sec, str(round(dampen, 6)))
        self.r.setex(ClaudeKeys.UNIVERSE_ALPHA, self.cfg.ttl_sec, str(round(max(0.0, confidence - 0.5) * 10.0, 4)))

        unified = compute_unified_regime(v55_regime, nexus_regime_id, feedback_accuracy, legacy_kernel_regime)
        self.r.hset(ClaudeKeys.REGIME_UNIFIED, mapping={
            "regime": unified,
            "kernel_regime": legacy_kernel_regime or v55_regime,
            "nexus_regime_id": str(nexus_regime_id),
            "nexus_regime_name": NEXUS_TO_KERNEL.get(nexus_regime_id, "CAUTION"),
            "feedback_accuracy": str(round(feedback_accuracy, 6)),
            "nexus_confidence": str(round(confidence, 6)),
            "decision": decision_name,
            "ready": str(bool(readiness.get("ready", False))).lower(),
            "halted": str(bool(state.get("halted", False))).lower(),
            "ts": str(time.time()),
            "source": "v56_compat_bridge",
        })
        self.r.expire(ClaudeKeys.REGIME_UNIFIED, self.cfg.ttl_sec)

        # Decision compatibility
        self.r.hset(ClaudeKeys.REBAL_DECISION, mapping={
            "decision": decision_name,
            "reason": str(decision.get("reason", "")),
            "regime": unified,
            "confidence": str(round(confidence, 6)),
            "turnover": str(round(turnover, 6)),
            "ts": str(time.time()),
            "source": "v56_compat_bridge",
        })
        self.r.expire(ClaudeKeys.REBAL_DECISION, self.cfg.ttl_sec)
        self.r.setex(ClaudeKeys.KERNEL_HEARTBEAT, self.cfg.ttl_sec, str(time.time()))

        snapshot = {
            "positions": len(weight_dict),
            "confidence": confidence,
            "regime": unified,
            "decision": decision_name,
            "turnover": turnover,
            "actual_cost_bps": actual_cost_bps,
            "shadow_cost_bps": shadow_cost_bps,
            "feedback_accuracy": feedback_accuracy,
            "dampen": dampen,
        }
        return snapshot

    def run_forever(self):
        log.info("Starting V5.6 compatibility bridge")
        while True:
            try:
                snap = self.bridge_once()
                log.info("bridge ok regime=%s decision=%s conf=%.3f pos=%d", snap["regime"], snap["decision"], snap["confidence"], snap["positions"])
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log.exception("bridge loop error: %s", e)
            time.sleep(self.cfg.interval_sec)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ARES V5.6 compatibility bridge")
    p.add_argument("--redis-url", default="redis://localhost:6379/0")
    p.add_argument("--interval-sec", type=int, default=15)
    p.add_argument("--once", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = BridgeConfig(redis_url=args.redis_url, interval_sec=args.interval_sec)
    bridge = RedisBridge(cfg)
    if args.once:
        print(json.dumps(bridge.bridge_once(), indent=2, default=str))
    else:
        bridge.run_forever()


if __name__ == "__main__":
    main()
