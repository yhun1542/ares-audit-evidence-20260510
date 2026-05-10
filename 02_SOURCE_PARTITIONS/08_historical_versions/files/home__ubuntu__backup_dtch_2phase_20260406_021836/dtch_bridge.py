#!/usr/bin/env python3
"""
DTCH Bridge v4.4 - Redis Intent → Order Execution
Supports two modes:
  1. existing_order_intent: Redis key에 JSON 저장 (기존 방식)
  2. xadd_to_stream: emarkos:v6:order:intent stream에 XADD (EC2 executor 호환)
"""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
import redis

import sys
import importlib.util
_common_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common.py")
_spec = importlib.util.spec_from_file_location("common", _common_path)
_common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_common)
json_hash = _common.json_hash

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", None)

BRIDGE_STATUS_KEY = "ares:dtch:bridge:status"
LAST_HASH_KEY = "ares:dtch:bridge:last_intent_hash"
LAST_ORDER_PAYLOAD_KEY = "ares:dtch:bridge:last_order_payload"
LAST_ORDER_RESULT_KEY = "ares:dtch:bridge:last_order_result"
LAST_UPDATED_KEY = "ares:dtch:bridge:last_updated"
CONSECUTIVE_FAIL_KEY = "ares:dtch:bridge:consecutive_failures"

# 안전장치 상수
MAX_SINGLE_NOTIONAL = 75_000    # 단일 주문 $75K 제한
MAX_DAILY_NOTIONAL = 75_000     # 일일 총 $75K 제한
MAX_CONSECUTIVE_FAILURES = 3    # 3회 연속 실패 → 자동 비활성화
ALLOWED_SYMBOLS = {"SH"}        # SH만 허용

# SH 거래소 코드: NYSE Arca = AMEX (KIS API 기준)
SH_EXCHANGE_CODE = "AMEX"

# ── Daily Notional Tracking ──────────────────────────────
def check_daily_notional(r, order_notional):
    """일일 누적 노셔널 체크. 주문 전에 호출.
    Returns: (allowed: bool, cumulative: float)
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_key = f"ares:dtch:daily_notional:{today}"
    cumulative = float(r.get(daily_key) or "0")
    if cumulative + abs(order_notional) > MAX_DAILY_NOTIONAL:
        return False, cumulative
    return True, cumulative

def record_daily_notional(r, order_notional):
    """주문 성공 후 일일 누적 노셔널 기록. 브로커 ack 후에만 호출."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_key = f"ares:dtch:daily_notional:{today}"
    r.incrbyfloat(daily_key, abs(order_notional))
    r.expire(daily_key, 90000)  # 25시간 TTL



def now():
    return datetime.now(timezone.utc).isoformat()


def get_redis():
    kwargs = {"host": REDIS_HOST, "port": REDIS_PORT, "decode_responses": True}
    password = os.environ.get("REDIS_PASSWORD")
    if not password:
        raise SystemExit("FATAL: REDIS_PASSWORD environment variable is required.")
    kwargs["password"] = password
    return redis.Redis(**kwargs)


class ExistingOrderIntentAdapter:
    """Redis key에 JSON 저장 (기존 방식)"""
    def __init__(self, r):
        self.r = r

    def send(self, payload: dict) -> dict:
        self.r.set("ares:order_intent:dtch:latest",
                    json.dumps(payload, ensure_ascii=False))
        return {"mode": "existing_order_intent", "accepted": True,
                "timestamp_utc": now()}


class XaddStreamAdapter:
    """emarkos:v6:order:intent stream에 XADD (EC2 executor 호환)"""
    STREAM_KEY = "emarkos:v6:order:intent"

    def __init__(self, r):
        self.r = r

    def send(self, payload: dict) -> dict:
        intent_id = f"dtch-{uuid.uuid4().hex[:12]}"
        # executor가 기대하는 필드 구조
        stream_fields = {
            "intent_id": intent_id,
            "origin_intent_id": intent_id,
            "symbol": payload.get("symbol", "SH"),
            "side": payload.get("side", "BUY"),
            "delta_shares": str(payload.get("delta_shares", 0)),
            "intent_type": "HEDGE_DTCH_L3",
            "strategy_version": "dtch_v44",
            "notional_usd": str(payload.get("notional_usd", 0)),
            "source": "dtch_bridge_v44",
            "timestamp_utc": now(),
        }
        msg_id = self.r.xadd(self.STREAM_KEY, stream_fields,
                              maxlen=10000, approximate=True)
        return {"mode": "xadd_to_stream", "accepted": True,
                "stream": self.STREAM_KEY, "msg_id": str(msg_id),
                "intent_id": intent_id, "timestamp_utc": now()}


def choose_adapter(r, mode: str):
    if mode == "existing_order_intent":
        return ExistingOrderIntentAdapter(r)
    if mode == "xadd_to_stream":
        return XaddStreamAdapter(r)
    raise ValueError(f"unsupported bridge mode: {mode}")


def build_order_payload(intent: dict, current_hedge_notional: float,
                        max_delta_notional: float, sh_price: float = 0) -> dict:
    desired = float(intent.get("target_notional", 0.0))
    symbol = str(intent.get("symbol", "SH")).upper()

    # 안전장치 1: SH만 허용
    if symbol not in ALLOWED_SYMBOLS:
        return {"skip": True, "reason": f"symbol_not_allowed_{symbol}",
                "desired": desired, "current": current_hedge_notional}

    delta = desired - current_hedge_notional
    side = "BUY" if delta > 0 else "SELL"
    abs_delta = abs(delta)

    if abs_delta < 1.0:
        return {"skip": True, "reason": "delta_too_small",
                "desired": desired, "current": current_hedge_notional}

    # 안전장치 2: 단일 주문 금액 제한
    if abs_delta > MAX_SINGLE_NOTIONAL:
        abs_delta = MAX_SINGLE_NOTIONAL  # cap to max

    # 안전장치 3: max_delta_notional 초과 체크
    if abs_delta > max_delta_notional:
        return {"skip": True, "reason": "delta_exceeds_cap",
                "desired": desired, "current": current_hedge_notional}

    # delta_shares 계산 (SH 가격 기반)
    delta_shares = 0
    if sh_price and sh_price > 0:
        delta_shares = int(abs_delta / sh_price)
        if delta_shares == 0:
            return {"skip": True, "reason": "delta_shares_zero",
                    "desired": desired, "current": current_hedge_notional}
    else:
        # 가격 정보 없으면 notional만 전달
        delta_shares = int(abs_delta / 15.0)  # SH 대략 $15 기준 추정

    # side에 따른 signed delta_shares
    signed_delta = delta_shares if side == "BUY" else -delta_shares

    return {
        "intent_type": "HEDGE_DTCH_L3",
        "symbol": symbol,
        "exchange": SH_EXCHANGE_CODE,
        "side": side,
        "delta_shares": signed_delta,
        "notional_usd": round(abs_delta, 2),
        "target_notional": round(desired, 2),
        "current_notional": round(current_hedge_notional, 2),
        "timestamp_utc": now(),
        "source_intent_hash": json_hash(intent),
    }


def run_once(config: dict) -> dict:
    r = get_redis()

    # Fail-closed: manual disable
    active = r.get("ares:dtch:active")
    if active is not None and str(active).lower() == "false":
        res = {"status": "FAIL_CLOSED", "reason": "manual_disable",
               "timestamp_utc": now()}
        r.set(BRIDGE_STATUS_KEY, json.dumps(res, ensure_ascii=False))
        r.set(LAST_UPDATED_KEY, now())
        return res

    # Fail-closed: missing source intent
    raw = r.get(config["source_key"])
    if not raw:
        res = {"status": "FAIL_CLOSED", "reason": "missing_source_intent",
               "timestamp_utc": now()}
        r.set(BRIDGE_STATUS_KEY, json.dumps(res, ensure_ascii=False))
        r.set(LAST_UPDATED_KEY, now())
        return res

    # Fail-closed: malformed JSON
    try:
        intent = json.loads(raw)
    except Exception:
        res = {"status": "FAIL_CLOSED", "reason": "malformed_source_intent",
               "timestamp_utc": now()}
        r.set(BRIDGE_STATUS_KEY, json.dumps(res, ensure_ascii=False))
        r.set(LAST_UPDATED_KEY, now())
        return res

    # Duplicate suppression
    h = json_hash(intent)
    if r.get(LAST_HASH_KEY) == h:
        res = {"status": "DUPLICATE_SUPPRESSED", "timestamp_utc": now(),
               "intent_hash": h}
        r.set(BRIDGE_STATUS_KEY, json.dumps(res, ensure_ascii=False))
        r.set(LAST_UPDATED_KEY, now())
        return res

    # Daily notional limit check (pre-send)
    estimated_notional = abs(float(r.get(config["current_hedge_notional_key"]) or "0"))
    daily_allowed, daily_cumulative = check_daily_notional(r, estimated_notional)
    if not daily_allowed:
        res = {"status": "DAILY_LIMIT_EXCEEDED",
               "cumulative": daily_cumulative,
               "max_daily": MAX_DAILY_NOTIONAL,
               "timestamp_utc": now()}
        r.set(BRIDGE_STATUS_KEY, json.dumps(res, ensure_ascii=False))
        r.set(LAST_UPDATED_KEY, now())
        return res
    # Build order payload
    current_notional = float(r.get(config["current_hedge_notional_key"]) or "0")
    sh_price = float(r.get("md:mid:SH") or r.get("md:last:SH") or "0")
    payload = build_order_payload(
        intent, current_notional,
        float(config["max_delta_notional_usd"]),
        sh_price
    )

    if payload.get("skip"):
        res = {"status": "SKIPPED", "payload": payload, "timestamp_utc": now()}
        r.set(BRIDGE_STATUS_KEY, json.dumps(res, ensure_ascii=False))
        r.set(LAST_UPDATED_KEY, now())
        r.set(LAST_HASH_KEY, h)
        return res

    # Send via adapter
    adapter = choose_adapter(r, config["bridge_mode"])
    try:
        result = adapter.send(payload)
    except Exception as e:
        # 연속 실패 카운터 증가
        fails = int(r.get(CONSECUTIVE_FAIL_KEY) or "0") + 1
        r.set(CONSECUTIVE_FAIL_KEY, str(fails))
        if fails >= MAX_CONSECUTIVE_FAILURES:
            r.set("ares:dtch:active", "false")
            res = {"status": "FAIL_CLOSED",
                   "reason": f"consecutive_failures_{fails}_auto_disable",
                   "error": str(e), "timestamp_utc": now()}
        else:
            res = {"status": "FAIL_CLOSED",
                   "reason": f"adapter_error_attempt_{fails}",
                   "error": str(e), "timestamp_utc": now()}
        r.set(BRIDGE_STATUS_KEY, json.dumps(res, ensure_ascii=False))
        r.set(LAST_UPDATED_KEY, now())
        return res

    # 성공 시 연속 실패 카운터 리셋
    r.set(CONSECUTIVE_FAIL_KEY, "0")
    r.set(LAST_HASH_KEY, h)
    r.set(LAST_ORDER_PAYLOAD_KEY, json.dumps(payload, ensure_ascii=False))
    r.set(LAST_ORDER_RESULT_KEY, json.dumps(result, ensure_ascii=False))

    status = "SENT" if result.get("accepted") else "FAIL_CLOSED"
    r.set(BRIDGE_STATUS_KEY,
          json.dumps({"status": status, "timestamp_utc": now()},
                     ensure_ascii=False))
    r.set(LAST_UPDATED_KEY, now())
    return {"payload": payload, "result": result}


if __name__ == "__main__":
    default_cfg = {
        "source_key": "ares:hazard:dtch:intent:latest",
        "current_hedge_notional_key": "ares:dtch:live_hedge_notional",
        "max_delta_notional_usd": MAX_SINGLE_NOTIONAL,
        "bridge_mode": os.environ.get("DTCH_BRIDGE_MODE",
                                       "existing_order_intent"),
    }
    print(json.dumps(run_once(default_cfg), ensure_ascii=False, indent=2))
