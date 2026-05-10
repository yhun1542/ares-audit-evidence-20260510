#!/usr/bin/env python3
"""
ARES Realized Cost Collector v2.0 (CORRECTED)
==============================================
EC2 실제 Redis 스키마에 맞게 수정됨 (2026-04-01)

실제 stream:fills 스키마:
  - symbol, side, qty, fill_price, intent_price (=0 대부분),
  - intent_id, broker_order_id, fill_type, fill_time,
  - strategy_version, notional_usd, realized_pnl, reconciled, slippage_bps (=0)

emarkos:v1:execution 스키마:
  - json blob: {schema, kind, intent_id, symbol, side, shares, broker_order_id, status, ts, ref_px}

변경점:
  1. stream:fills를 직접 읽음 (ares:events:fills 아님)
  2. fill_price 필드명 사용 (fill_px 아님)
  3. ref_px를 emarkos:v1:execution에서 매칭하여 arrival price로 사용
  4. premium:polygon:{symbol} 키에서 현재 호가를 arrival 대안으로 사용

PM2 배포:
  pm2 start ares_cost_collector.py --name ares-cost-collector --interpreter python3
"""
import os, time, json, logging
from datetime import datetime, timezone

# [PATCH H-6] dotenv 로드 추가
try:
    from dotenv import load_dotenv
    load_dotenv('/home/ubuntu/ares_releases/2026-02-17/.env')
    load_dotenv('/home/ubuntu/.env')
except ImportError:
    pass
import os as _os
# Redis 환경변수 기본값 설정
if 'REDIS_URL' not in os.environ:
    os.environ['REDIS_URL'] = 'redis://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@localhost:6379'
if 'REDIS_PASSWORD' not in os.environ:
    os.environ['REDIS_PASSWORD'] = 'Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U'


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("CostCollector")

try:
    import redis
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "redis"])
    import redis

REDIS_URL = os.getenv("ARES_REDIS_URL", "redis://localhost:6379/0")
EWMA_ALPHA = 0.1
DEFAULT_COST_BPS = 13.0
MAX_COST_BPS = 200.0
MIN_COST_BPS = 1.0
STATS_INTERVAL = 60

# CORRECTED: 실제 Redis 키/스트림 이름
FILLS_STREAM = "stream:fills"
EXEC_STREAM = "emarkos:v1:execution"
QUOTE_PATTERN = "premium:polygon:{symbol}"
EXEC_QUALITY_STREAM = "stream:execution_quality"


def connect_redis():
    for attempt in range(10):
        try:
            r = redis.from_url(REDIS_URL, decode_responses=True)
            r.ping()
            log.info(f"Redis connected: {REDIS_URL}")
            return r
        except Exception as e:
            log.warning(f"Redis connection attempt {attempt+1}/10 failed: {e}")
            time.sleep(3)
    raise ConnectionError("Failed to connect to Redis after 10 attempts")


def get_arrival_price(r, symbol, intent_id):
    """emarkos:v1:execution에서 ref_px를 찾거나, polygon quote에서 mid price를 가져옴"""
    # 방법 1: emarkos 스트림에서 intent_id로 ref_px 매칭
    try:
        results = r.xrevrange(EXEC_STREAM, "+", "-", count=50)
        for entry_id, fields in results:
            raw_json = fields.get("json", "")
            if raw_json:
                data = json.loads(raw_json)
                if data.get("intent_id") == intent_id and data.get("ref_px"):
                    return float(data["ref_px"])
    except Exception as e:
        log.debug(f"emarkos lookup failed for {intent_id}: {e}")

    # 방법 2: polygon quote에서 현재 가격
    try:
        quote_key = QUOTE_PATTERN.format(symbol=symbol)
        raw = r.get(quote_key)
        if raw:
            q = json.loads(raw)
            bid = float(q.get("bid", q.get("bp", 0)))
            ask = float(q.get("ask", q.get("ap", 0)))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
            last = float(q.get("last", q.get("lp", q.get("price", 0))))
            if last > 0:
                return last
    except Exception as e:
        log.debug(f"polygon quote lookup failed for {symbol}: {e}")

    return None


def calculate_slippage(arrival_price, fill_price, side):
    if arrival_price <= 0 or fill_price <= 0:
        return None
    if side.upper() == "BUY":
        slip_pct = (fill_price - arrival_price) / arrival_price
    else:
        slip_pct = (arrival_price - fill_price) / arrival_price
    return slip_pct * 10000  # signed slippage in bps


def update_ewma(r, symbol, new_cost_bps):
    key_sym = f"execution:realized_cost:{symbol}"
    key_agg = "execution:realized_cost:aggregate"

    pipe = r.pipeline()

    current_sym = r.get(key_sym)
    if current_sym:
        updated_sym = float(current_sym) * (1 - EWMA_ALPHA) + new_cost_bps * EWMA_ALPHA
    else:
        updated_sym = new_cost_bps
    updated_sym = max(MIN_COST_BPS, min(abs(updated_sym), MAX_COST_BPS))
    pipe.set(key_sym, f"{updated_sym:.2f}")
    pipe.expire(key_sym, 86400)

    current_agg = r.get(key_agg)
    if current_agg:
        updated_agg = float(current_agg) * (1 - EWMA_ALPHA) + new_cost_bps * EWMA_ALPHA
    else:
        updated_agg = new_cost_bps
    updated_agg = max(MIN_COST_BPS, min(abs(updated_agg), MAX_COST_BPS))
    pipe.set(key_agg, f"{updated_agg:.2f}")

    pipe.execute()
    return updated_sym, updated_agg


def emit_exec_quality(r, rec):
    """stream:execution_quality에 텔레메트리 기록"""
    try:
        fields = {k: str(v) for k, v in rec.items() if v is not None}
        r.xadd(EXEC_QUALITY_STREAM, fields, maxlen=10000)
    except Exception as e:
        log.error(f"Failed to emit exec quality: {e}")


def process_fills_stream(r):
    """CORRECTED: stream:fills에서 체결 이벤트를 읽어 처리"""
    last_id = "$"
    stats = {"processed": 0, "skipped": 0, "errors": 0, "last_update": None}
    last_stats_time = time.time()

    log.info(f"Listening on {FILLS_STREAM}...")

    while True:
        try:
            events = r.xread({FILLS_STREAM: last_id}, count=10, block=5000)
            if not events:
                continue

            for stream_name, messages in events:
                for msg_id, data in messages:
                    last_id = msg_id
                    try:
                        # CORRECTED: 실제 stream:fills 필드명
                        symbol = data.get("symbol", "")
                        side = data.get("side", "")
                        fill_price = float(data.get("fill_price", 0))
                        fill_type = data.get("fill_type", "")
                        intent_id = data.get("intent_id", "")
                        notional = float(data.get("notional_usd", 0))

                        # FILLED 상태만 처리 (FILL_PENDING 제외)
                        if fill_type != "FILLED":
                            stats["skipped"] += 1
                            continue

                        if not all([symbol, side]) or fill_price <= 0:
                            stats["skipped"] += 1
                            continue

                        # arrival price 조회
                        arrival_price = get_arrival_price(r, symbol, intent_id)
                        if arrival_price is None or arrival_price <= 0:
                            log.debug(f"No arrival price for {symbol} {intent_id}, using fill_price as baseline")
                            stats["skipped"] += 1
                            continue

                        slip_bps = calculate_slippage(arrival_price, fill_price, side)
                        if slip_bps is not None:
                            sym_cost, agg_cost = update_ewma(r, symbol, abs(slip_bps))
                            stats["processed"] += 1
                            stats["last_update"] = datetime.now(timezone.utc).isoformat()

                            # execution_quality 텔레메트리 발행
                            emit_exec_quality(r, {
                                "intent_id": intent_id,
                                "symbol": symbol,
                                "side": side,
                                "route_mode": "direct_market",
                                "arrival_mid": arrival_price,
                                "fill_px": fill_price,
                                "slippage_bps": round(slip_bps, 2),
                                "order_notional_usd": notional,
                                "fill_ts_ms": int(time.time() * 1000),
                                "live_engine": "V65_C6_TC6x_20260331",
                                "maker_requotes": 0,
                                "escape_used": 0,
                            })

                            log.info(
                                f"[{symbol} {side}] arrival={arrival_price:.2f} "
                                f"fill={fill_price:.2f} slip={slip_bps:+.1f}bps | "
                                f"EWMA sym={sym_cost:.1f} agg={agg_cost:.1f}bps"
                            )

                    except Exception as e:
                        stats["errors"] += 1
                        log.error(f"Error processing {msg_id}: {e}")

            if time.time() - last_stats_time > STATS_INTERVAL:
                r.set("execution:cost_collector:stats", json.dumps(stats))
                last_stats_time = time.time()
                log.info(f"Stats: processed={stats['processed']} skipped={stats['skipped']} errors={stats['errors']}")

        except redis.ConnectionError:
            log.error("Redis connection lost. Reconnecting in 5s...")
            time.sleep(5)
            r = connect_redis()
        except Exception as e:
            log.error(f"Unexpected error: {e}")
            time.sleep(1)


def main():
    log.info("=" * 60)
    log.info("ARES Realized Cost Collector v2.0 (CORRECTED)")
    log.info(f"  Fills stream: {FILLS_STREAM}")
    log.info(f"  Exec stream:  {EXEC_STREAM}")
    log.info(f"  Quote pattern: {QUOTE_PATTERN}")
    log.info(f"  Quality stream: {EXEC_QUALITY_STREAM}")
    log.info("=" * 60)

    r = connect_redis()

    # 초기화
    if not r.exists("execution:realized_cost:aggregate"):
        r.set("execution:realized_cost:aggregate", f"{DEFAULT_COST_BPS:.2f}")
        log.info(f"Initialized aggregate cost to {DEFAULT_COST_BPS} bps")
    else:
        current = r.get("execution:realized_cost:aggregate")
        log.info(f"Current aggregate cost: {current} bps")

    # CORRECTED: 직접 stream:fills 사용
    process_fills_stream(r)


if __name__ == "__main__":
    main()
