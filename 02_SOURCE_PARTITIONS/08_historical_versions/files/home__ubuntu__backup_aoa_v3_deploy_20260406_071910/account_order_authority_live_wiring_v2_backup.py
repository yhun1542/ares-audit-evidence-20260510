#!/usr/bin/env python3
"""
AOA Live Wiring Service v2
──────────────────────────
현재 ARES 운영 Redis 키/PM2 이름에 맞춘 live wiring 참조 구현.

용도
- Stage 0: observe-only (AOA_EMIT=false)
- Stage 1: nextgen cutover
- Stage 2: ares-v56 shadow compare
- Stage 3: DTCH 편입 전 준비

주의
- 이 파일은 production-oriented reference다.
- 현재 live key를 그대로 읽도록 맞췄지만, cutover 전에는 반드시 observe-only로 검증해야 한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import redis

from account_order_authority_service import (
    AccountOrderAuthority,
    AuthorityConfig,
    BrokerPosition,
    DesiredPosition,
    FamilyBook,
    OpenOrder,
    PriceQuote,
    SnapshotBundle,
)

LOG = logging.getLogger("aoa.live")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TRUTHY = {"1", "true", "yes", "y", "on"}


LUA_ATOMIC_EMIT = """
local stream = KEYS[1]
local idem_key = KEYS[2]
local idem_val = ARGV[1]
local idem_ttl = tonumber(ARGV[2])
local maxlen = tonumber(ARGV[3])

local existing = redis.call('GET', idem_key)
if existing and string.sub(existing, 1, 4) == 'SENT' then
  local last_colon = 0
  for i = 1, #existing do
    if string.sub(existing, i, i) == ':' then last_colon = i end
  end
  local sid = (last_colon > 0) and string.sub(existing, last_colon + 1) or string.sub(existing, 6)
  return sid
end

local fields = {}
for i = 4, #ARGV, 2 do
  fields[#fields+1] = ARGV[i]
  fields[#fields+1] = ARGV[i+1]
end

local stream_id = redis.call('XADD', stream, 'MAXLEN', '~', maxlen, '*', unpack(fields))
redis.call('SET', idem_key, idem_val .. ':' .. stream_id, 'EX', idem_ttl)
return stream_id
"""


@dataclass
class LiveKeys:
    account_id: str
    active_family_key: str
    last_plan_key: str
    last_plan_hash_key: str
    status_key: str
    heartbeat_key: str
    shadow_diff_key: str
    blocked_symbols_key: str

    order_intent_stream: str

    nextgen_ssot_current_key: str
    ares_shadow_book_key: str

    kis_positions_hash_key: str
    legacy_positions_key: str
    open_orders_key: str
    open_orders_ts_key: str

    equity_total_key: str
    equity_total_ts_key: str

    trading_enabled_key: str
    trade_halt_key: str


class RedisAtomicEmitter:
    def __init__(self, r: redis.Redis, stream: str) -> None:
        self.r = r
        self.stream = stream
        self.script = r.register_script(LUA_ATOMIC_EMIT)

    def emit(self, intent) -> str:
        fields = intent.to_stream_fields()
        args: List[str] = [
            "SENT:aoa",
            "86400",
            "5000",
        ]
        for k, v in fields.items():
            args.extend([str(k), str(v)])
        stream_id = self.script(
            keys=[self.stream, f"aoa:idem:{intent.intent_id}"],
            args=args,
        )
        if isinstance(stream_id, bytes):
            stream_id = stream_id.decode()
        return str(stream_id)


class AoaLiveWiring:
    def __init__(self) -> None:
        self.r = self._connect()
        self.keys = self._load_keys()
        self.cfg = self._load_cfg()
        self.core = AccountOrderAuthority(self.cfg)
        self.emitter = RedisAtomicEmitter(self.r, self.keys.order_intent_stream)
        self.emit_enabled = str(os.getenv("AOA_EMIT", "false")).lower() in TRUTHY
        self.loop_sec = int(os.getenv("AOA_LOOP_SEC", "5"))
        self.active_family_default = os.getenv("AOA_ACTIVE_FAMILY_DEFAULT", "nextgen2")
        self.observe_only = not self.emit_enabled

    @staticmethod
    def _connect() -> redis.Redis:
        url = os.getenv("REDIS_URL")
        pwd = os.getenv("REDIS_PASSWORD")
        if url:
            return redis.from_url(url, password=pwd, decode_responses=True)
        return redis.Redis(
            host=os.getenv("REDIS_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=pwd,
            decode_responses=True,
        )

    def _load_keys(self) -> LiveKeys:
        account_id = os.getenv("AOA_ACCOUNT_ID", "us_equity_main")
        return LiveKeys(
            account_id=account_id,
            active_family_key=os.getenv("AOA_ACTIVE_FAMILY_KEY", f"ares:aoa:active_family:{account_id}"),
            last_plan_key=os.getenv("AOA_LAST_PLAN_KEY", f"ares:aoa:last_plan:{account_id}"),
            last_plan_hash_key=os.getenv("AOA_LAST_PLAN_HASH_KEY", f"ares:aoa:last_plan_hash:{account_id}"),
            status_key=os.getenv("AOA_STATUS_KEY", "ares:aoa:status"),
            heartbeat_key=os.getenv("AOA_HEARTBEAT_KEY", "ares:aoa:heartbeat"),
            shadow_diff_key=os.getenv("AOA_SHADOW_DIFF_KEY", f"ares:aoa:shadow_diff:{account_id}"),
            blocked_symbols_key=os.getenv("AOA_BLOCKED_SYMBOLS_KEY", f"ares:aoa:blocked_symbols:{account_id}"),
            order_intent_stream=os.getenv("ORDER_INTENT_STREAM", "emarkos:v6:order:intent"),
            nextgen_ssot_current_key=os.getenv("NEXTGEN_SSOT_CURRENT_KEY", "ssot:target:v2:current"),
            ares_shadow_book_key=os.getenv("ARES_SHADOW_BOOK_KEY", f"ares:aoa:desired_book:{account_id}:ares-v56"),
            kis_positions_hash_key=os.getenv("KIS_POSITIONS_HASH_KEY", "kis:broker:positions"),
            legacy_positions_key=os.getenv("LEGACY_POSITIONS_KEY", "emarkos:v1:positions"),
            open_orders_key=os.getenv("OPEN_ORDERS_KEY", "emarkos:v1:open_orders"),
            open_orders_ts_key=os.getenv("OPEN_ORDERS_TS_KEY", "emarkos:v1:open_orders:ts"),
            equity_total_key=os.getenv("EQUITY_TOTAL_KEY", "ares:equity:total"),
            equity_total_ts_key=os.getenv("EQUITY_TOTAL_TS_KEY", "ares:equity:total:ts"),
            trading_enabled_key=os.getenv("TRADING_ENABLED_KEY", "trading:enabled"),
            trade_halt_key=os.getenv("TRADE_HALT_KEY", "trade:halt"),
        )

    def _load_cfg(self) -> AuthorityConfig:
        allowlist_raw = os.getenv("AOA_SYMBOL_ALLOWLIST", "")
        allowlist = {x.strip().upper() for x in allowlist_raw.split(",") if x.strip()} or None
        return AuthorityConfig(
            authority_id=os.getenv("AOA_AUTHORITY_ID", "account-order-authority"),
            active_family=self.active_family_default if hasattr(self, "active_family_default") else "nextgen2",
            allow_shadow_emit=False,
            min_lot=int(os.getenv("AOA_MIN_LOT", "1")),
            price_stale_ms=int(os.getenv("AOA_PRICE_STALE_MS", "10000")),
            broker_stale_ms=int(os.getenv("AOA_BROKER_STALE_MS", "30000")),
            open_orders_stale_ms=int(os.getenv("AOA_OPEN_ORDERS_STALE_MS", "30000")),
            max_single_notional_usd=float(os.getenv("AOA_MAX_SINGLE_NOTIONAL_USD", "75000")),
            max_daily_notional_usd=float(os.getenv("AOA_MAX_DAILY_NOTIONAL_USD", "250000")),
            symbol_allowlist=allowlist,
        )

    def run_forever(self) -> None:
        LOG.info("AOA live wiring start | emit=%s loop=%ss stream=%s",
                 self.emit_enabled, self.loop_sec, self.keys.order_intent_stream)
        while True:
            started = time.time()
            try:
                self.run_cycle()
            except Exception as e:
                LOG.exception("AOA cycle failed: %s", e)
                self.r.set(self.keys.status_key, json.dumps({
                    "status": "ERROR",
                    "error": str(e),
                    "ts": int(time.time() * 1000),
                }, ensure_ascii=False))
                self.r.set("ares:aoa:last_error", str(e))
            elapsed = time.time() - started
            sleep_s = max(0.1, self.loop_sec - elapsed)
            time.sleep(sleep_s)

    def run_cycle(self) -> None:
        active_family = self.r.get(self.keys.active_family_key) or self.active_family_default
        self.core.cfg.active_family = str(active_family)

        bundle = self._build_snapshot_bundle()
        plan = self.core.build_plan(bundle)
        last_hash = self.r.get(self.keys.last_plan_hash_key)

        emitted = False
        stream_ids: List[str] = []
        if self.emit_enabled:
            emitted, stream_ids = self.core.emit_if_new(plan, last_hash, self.emitter)

        self._write_plan(plan, emitted, stream_ids)
        self._heartbeat(plan, emitted, stream_ids)

    def _build_snapshot_bundle(self) -> SnapshotBundle:
        now_ms = int(time.time() * 1000)
        books = list(self._read_family_books(now_ms))
        broker_positions, broker_snapshot_ms = self._read_broker_positions(now_ms)
        open_orders, open_orders_snapshot_ms = self._read_open_orders(now_ms)
        symbols = self._collect_symbols(books, broker_positions, open_orders)
        prices = self._read_prices(symbols)
        trading_enabled = self._read_trading_enabled()
        daily_used = float(self.r.get("ares:dtch:daily_notional:" + time.strftime("%Y-%m-%d")) or "0") \
            if False else 0.0
        return SnapshotBundle(
            books=books,
            broker_positions=broker_positions,
            open_orders=open_orders,
            prices=prices,
            trading_enabled=trading_enabled,
            broker_snapshot_ms=broker_snapshot_ms,
            open_orders_snapshot_ms=open_orders_snapshot_ms,
            daily_notional_used_usd=daily_used,
        )

    def _read_family_books(self, now_ms: int) -> Iterable[FamilyBook]:
        nextgen_raw = self.r.get(self.keys.nextgen_ssot_current_key)
        if nextgen_raw:
            book = self._parse_ssot_current_to_book(nextgen_raw, now_ms)
            if book:
                yield book

        ares_raw = self.r.get(self.keys.ares_shadow_book_key)
        if ares_raw:
            book = self._parse_desired_book(ares_raw)
            if book:
                yield book

    def _parse_ssot_current_to_book(self, raw: str, now_ms: int) -> Optional[FamilyBook]:
        try:
            payload = json.loads(raw)
            targets = payload.get("targets", {})
            pos = targets.get("positions", [])
            equity_total = float(self.r.get(self.keys.equity_total_key) or self.r.get("emarkos:v1:budget_usd") or "0")
            if equity_total <= 0:
                return None
            quotes = {p.get("symbol", "").upper(): self._get_price(p.get("symbol", "")) for p in pos}
            positions: List[DesiredPosition] = []
            for p in pos:
                sym = str(p.get("symbol", "")).upper()
                w = self._to_float(p.get("w"), 0.0)
                px = quotes.get(sym)
                if not sym or px is None or px <= 0:
                    continue
                target_notional = w * equity_total
                target_qty = int(round(target_notional / px))
                positions.append(DesiredPosition(symbol=sym, target_qty=target_qty, target_weight=w))
            if not positions:
                return None
            correlation_id = str(payload.get("correlation_id", "ssot-current"))
            source_ts = int(payload.get("ts") or now_ms)
            return FamilyBook(
                account_id=self.keys.account_id,
                family="nextgen2",
                mode="PRIMARY",
                asof_ms=source_ts,
                correlation_id=correlation_id,
                positions=positions,
                meta={
                    "source": self.keys.nextgen_ssot_current_key,
                    "source_ts": source_ts,
                    "schema": payload.get("schema", ""),
                },
            )
        except Exception as e:
            LOG.warning("Failed to parse ssot current: %s", e)
            return None

    def _parse_desired_book(self, raw: str) -> Optional[FamilyBook]:
        try:
            obj = json.loads(raw)
            positions = [
                DesiredPosition(
                    symbol=str(p["symbol"]).upper(),
                    target_qty=int(p["target_qty"]),
                    target_weight=self._to_float(p.get("target_weight"), None) if p.get("target_weight") is not None else None,
                    limit_price=self._to_float(p.get("limit_price"), None) if p.get("limit_price") is not None else None,
                )
                for p in obj.get("positions", [])
                if p.get("symbol") is not None and p.get("target_qty") is not None
            ]
            if not positions:
                return None
            return FamilyBook(
                account_id=str(obj.get("account_id", self.keys.account_id)),
                family=str(obj.get("family", "ares-v56")),
                mode=str(obj.get("mode", "SHADOW")).upper(),  # type: ignore[arg-type]
                asof_ms=int(obj.get("asof_ms", int(time.time() * 1000))),
                correlation_id=str(obj.get("correlation_id", str(uuid.uuid4()))),
                positions=positions,
                meta=obj.get("meta", {}) if isinstance(obj.get("meta"), dict) else {},
            )
        except Exception as e:
            LOG.warning("Failed to parse desired book: %s", e)
            return None

    def _read_broker_positions(self, now_ms: int) -> Tuple[List[BrokerPosition], int]:
        out: List[BrokerPosition] = []
        snapshot_ms = now_ms
        try:
            data = self.r.hgetall(self.keys.kis_positions_hash_key)
            if data:
                ts_raw = self.r.get(f"{self.keys.kis_positions_hash_key}:ts") or self.r.get("kis:broker:positions:ts")
                snapshot_ms = self._ts_to_ms(ts_raw, now_ms)
                for sym, raw in data.items():
                    obj = self._json_or_empty(raw)
                    qty = self._extract_qty(obj)
                    mv = self._to_float(
                        obj.get("market_value_usd") or obj.get("market_value") or obj.get("mv"),
                        0.0,
                    )
                    out.append(BrokerPosition(symbol=sym.upper(), qty=qty, market_value_usd=mv))
                return out, snapshot_ms
        except Exception as e:
            LOG.warning("Broker hash read failed: %s", e)

        try:
            raw = self.r.get(self.keys.legacy_positions_key)
            if raw:
                obj = json.loads(raw)
                snapshot_ms = self._ts_to_ms(obj.get("ts") or self.r.get("emarkos:v1:positions:ts"), now_ms)
                positions = obj.get("positions", {})
                if isinstance(positions, dict):
                    for sym, p in positions.items():
                        p = p if isinstance(p, dict) else {"qty": p}
                        out.append(BrokerPosition(
                            symbol=str(sym).upper(),
                            qty=self._extract_qty(p),
                            market_value_usd=self._to_float(p.get("market_value_usd") or p.get("market_value"), 0.0),
                        ))
        except Exception as e:
            LOG.warning("Legacy positions read failed: %s", e)

        return out, snapshot_ms

    def _read_open_orders(self, now_ms: int) -> Tuple[List[OpenOrder], int]:
        out: List[OpenOrder] = []
        raw = self.r.get(self.keys.open_orders_key)
        ts_raw = self.r.get(self.keys.open_orders_ts_key)
        snapshot_ms = self._ts_to_ms(ts_raw, now_ms)
        if not raw:
            return out, snapshot_ms
        try:
            obj = json.loads(raw)
            orders = obj.get("orders", obj if isinstance(obj, list) else [])
            if isinstance(orders, list):
                for o in orders:
                    if not isinstance(o, dict):
                        continue
                    sym = str(o.get("symbol") or o.get("pdno") or "").upper()
                    if not sym:
                        continue
                    side_raw = str(o.get("side") or o.get("sll_buy_dvsn_cd") or "BUY").upper()
                    side = "SELL" if side_raw in {"SELL", "S", "02"} else "BUY"
                    qty_remaining = self._extract_qty(
                        {
                            "qty_remaining": o.get("qty_remaining"),
                            "remaining": o.get("remaining_qty"),
                            "qty": o.get("qty"),
                            "unfilled": o.get("ord_psbl_qty"),
                        }
                    )
                    order_id = str(o.get("client_order_id") or o.get("odno") or uuid.uuid4())
                    submitted_ms = self._ts_to_ms(o.get("submitted_ms") or o.get("timestamp_utc"), now_ms)
                    out.append(OpenOrder(
                        symbol=sym,
                        side=side,  # type: ignore[arg-type]
                        qty_remaining=max(0, qty_remaining),
                        client_order_id=order_id,
                        submitted_ms=submitted_ms,
                    ))
        except Exception as e:
            LOG.warning("Open orders parse failed: %s", e)
        return out, snapshot_ms

    def _read_prices(self, symbols: Sequence[str]) -> List[PriceQuote]:
        out: List[PriceQuote] = []
        now_ms = int(time.time() * 1000)
        for sym in sorted(set(x.upper() for x in symbols if x)):
            px = self._get_price(sym)
            if px is None:
                continue
            out.append(PriceQuote(symbol=sym, price=px, asof_ms=now_ms))
        return out

    def _get_price(self, symbol: str) -> Optional[float]:
        symbol = symbol.upper()
        if not symbol:
            return None
        raw = self.r.get(f"price:{symbol}")
        if raw is None:
            return None
        px = self._to_float(raw, None)
        return px if px and px > 0 else None

    def _read_trading_enabled(self) -> bool:
        halt_raw = self.r.get(self.keys.trade_halt_key)
        if halt_raw is not None and str(halt_raw).lower() in TRUTHY:
            return False
        raw = self.r.get(self.keys.trading_enabled_key)
        return raw is not None and str(raw).strip().lower() in TRUTHY

    def _write_plan(self, plan, emitted: bool, stream_ids: Sequence[str]) -> None:
        payload = {
            "status": plan.status,
            "reason": plan.reason,
            "plan_id": plan.plan_id,
            "shadow_drift_symbols": plan.shadow_drift_symbols,
            "blocked_symbols": plan.blocked_symbols,
            "emit_enabled": self.emit_enabled,
            "emitted": emitted,
            "stream_ids": list(stream_ids),
            "n_intents": len(plan.intents),
            "intents": [
                {
                    "symbol": i.symbol,
                    "side": i.side,
                    "qty": i.qty,
                    "notional_usd": round(i.notional_usd, 6),
                    "family": i.family,
                }
                for i in plan.intents
            ],
            "ts": int(time.time() * 1000),
        }
        self.r.set(self.keys.last_plan_key, json.dumps(payload, ensure_ascii=False))
        if plan.plan_id:
            self.r.set(self.keys.last_plan_hash_key, plan.plan_id)
        if plan.shadow_drift_symbols:
            self.r.set(self.keys.shadow_diff_key, json.dumps(plan.shadow_drift_symbols, ensure_ascii=False))
        if plan.blocked_symbols:
            self.r.set(self.keys.blocked_symbols_key, json.dumps(plan.blocked_symbols, ensure_ascii=False))
        self.r.set(self.keys.status_key, json.dumps({
            "status": "EMITTED" if emitted else plan.status,
            "reason": plan.reason,
            "n_intents": len(plan.intents),
            "emit_enabled": self.emit_enabled,
            "observe_only": self.observe_only,
            "ts": int(time.time() * 1000),
        }, ensure_ascii=False))

    def _heartbeat(self, plan, emitted: bool, stream_ids: Sequence[str]) -> None:
        self.r.set(self.keys.heartbeat_key, json.dumps({
            "service": "account-order-authority-v1",
            "status": plan.status,
            "reason": plan.reason,
            "emit_enabled": self.emit_enabled,
            "emitted": emitted,
            "stream_ids": list(stream_ids),
            "ts": int(time.time() * 1000),
        }, ensure_ascii=False), ex=120)

    @staticmethod
    def _collect_symbols(
        books: Sequence[FamilyBook],
        broker_positions: Sequence[BrokerPosition],
        open_orders: Sequence[OpenOrder],
    ) -> List[str]:
        out: List[str] = []
        for b in books:
            out.extend([p.symbol for p in b.positions])
        out.extend([p.symbol for p in broker_positions])
        out.extend([o.symbol for o in open_orders])
        return out

    @staticmethod
    def _extract_qty(obj: Dict[str, Any]) -> int:
        for key in ("qty", "quantity", "shares", "position_qty", "qty_remaining", "remaining", "unfilled"):
            if key in obj and obj.get(key) is not None:
                try:
                    return int(float(obj[key]))
                except Exception:
                    pass
        return 0

    @staticmethod
    def _json_or_empty(raw: str) -> Dict[str, Any]:
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _to_float(v: Any, default: Optional[float]) -> Optional[float]:
        try:
            return float(v)
        except Exception:
            return default

    @staticmethod
    def _ts_to_ms(v: Any, default_ms: int) -> int:
        if v is None:
            return default_ms
        try:
            if isinstance(v, (int, float)):
                iv = int(v)
                return iv if iv > 10_000_000_000 else iv * 1000
            s = str(v).strip()
            if s.isdigit():
                iv = int(s)
                return iv if iv > 10_000_000_000 else iv * 1000
            # ISO 8601 fallback
            from datetime import datetime
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return default_ms


if __name__ == "__main__":
    svc = AoaLiveWiring()
    svc.run_forever()
