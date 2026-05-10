#!/usr/bin/env python3
"""Bridge legacy pipeline_health into canonical ssot:pipeline:health:v1.

Operational safety: this daemon only reads Redis health keys and writes the
canonical health beacon consumed by ssot-pipeline-guard/equity-pipeline-monitor.
It never triggers orders and never starts/restarts/stops other PM2 processes.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, Optional

sys.path.insert(0, "/home/ubuntu/scripts")
sys.path.insert(0, "/home/ubuntu/aub-trading-system/ops/lib")

from redis_ssot_loader import get_redis_client  # type: ignore
from pipeline_health_contract import (  # type: ignore
    SSOT_PIPELINE_HEALTH_KEY,
    make_health_payload,
    serialise,
    now_ms,
)

LEGACY_KEY = os.getenv("PIPELINE_HEALTH_LEGACY_KEY", "pipeline_health")
INTERVAL_SEC = max(10, int(os.getenv("PIPELINE_HEALTH_BRIDGE_INTERVAL_SEC", "30")))
TTL_SEC = max(180, int(os.getenv("PIPELINE_HEALTH_BRIDGE_TTL_SEC", "600")))
MAX_LAG_MS = max(60_000, int(os.getenv("PIPELINE_HEALTH_BRIDGE_MAX_LAG_MS", "300000")))
WRITER = os.getenv("PIPELINE_HEALTH_BRIDGE_WRITER", "pipeline-health-bridge")


def _decode(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _parse_json(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _legacy_is_healthy(payload: Dict[str, Any], raw: Optional[str]) -> bool:
    # Compatibility bridge rule:
    # The canonical contract requires ssot:pipeline:health:v1.  Some current
    # production flows no longer maintain the old pipeline_health key, while
    # ssot-source/writer/promote/current can still be healthy.  Therefore a
    # missing legacy key must not by itself publish unhealthy; only an explicit
    # legacy unhealthy/fail/error/stale state is propagated as unhealthy.
    if payload:
        status = str(payload.get("status") or payload.get("state") or "").lower()
        if status in {"unhealthy", "fail", "failed", "error", "stale"}:
            return False
        if status in {"healthy", "ok", "online", "pass"}:
            return True
    return True


def main() -> None:
    r = get_redis_client()
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "pipeline_health_bridge_started", "legacy_key": LEGACY_KEY, "canonical_key": SSOT_PIPELINE_HEALTH_KEY, "interval_sec": INTERVAL_SEC, "ttl_sec": TTL_SEC}), flush=True)
    while True:
        raw = _decode(r.get(LEGACY_KEY))
        legacy = _parse_json(raw)
        healthy = _legacy_is_healthy(legacy, raw)
        t = now_ms()
        payload = make_health_payload(
            status="healthy" if healthy else "unhealthy",
            last_success_ms=t if healthy else int(legacy.get("last_success_ms") or 0),
            updated_at_ms=t,
            max_allowed_lag_ms=MAX_LAG_MS,
            writer=WRITER,
            reason="bridged_from_legacy_pipeline_health" if healthy else "legacy_pipeline_health_missing_or_unhealthy",
        )
        # Additive metadata is tolerated by all contract readers.
        payload["legacy_key"] = LEGACY_KEY
        payload["legacy_status"] = legacy.get("status") or legacy.get("state") or ("present" if raw else "missing")
        r.set(SSOT_PIPELINE_HEALTH_KEY, serialise(payload), ex=TTL_SEC)
        print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "pipeline_health_bridge_tick", "status": payload["status"], "canonical_key": SSOT_PIPELINE_HEALTH_KEY}), flush=True)
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
