"""
ARES Event-Based Market Guard
- Redis pub/sub 기반 실시간 이벤트 감지 (30초 폴링이 아닌 즉시 반응)
- 가격 급변, 주문 폭주, 데이터 stale 감지
- 감지 즉시 L2(ofg-halt) 또는 L1(kill-switch) 트리거
"""
import json, os, sys, time, logging, redis

logging.basicConfig(level=logging.INFO, format='%(asctime)s [EVENT-GUARD] %(message)s')
log = logging.getLogger('event-guard')

REDIS_URL = os.environ.get('REDIS_URL', 'redis://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@localhost:6379/0')
PRICE_CHANGE_THRESHOLD = 0.05  # 5% 급변 감지
ORDER_BURST_THRESHOLD = 10     # 10초 내 10건 이상 주문 폭주
DATA_STALE_SEC = 120           # 2분 이상 데이터 미갱신

r = redis.from_url(REDIS_URL, decode_responses=True)
pubsub = r.pubsub()

# 이벤트 채널 구독
channels = [
    'ares:market:price_update',
    'emarkos:v6:order:intent',
    'ares:reconciliation:status',
    'ares:health:heartbeat'
]

order_timestamps = []
last_price_cache = {}
last_data_ts = time.time()

def check_price_spike(data):
    """가격 급변 감지"""
    try:
        sym = data.get('symbol', '')
        price = float(data.get('price', 0))
        if sym in last_price_cache and last_price_cache[sym] > 0:
            change = abs(price - last_price_cache[sym]) / last_price_cache[sym]
            if change > PRICE_CHANGE_THRESHOLD:
                log.warning(f'PRICE SPIKE DETECTED: {sym} {change:.1%} ({last_price_cache[sym]}->{price})')
                r.set('ares:event_guard:alert:price_spike', json.dumps({
                    'symbol': sym, 'change': round(change, 4),
                    'old_price': last_price_cache[sym], 'new_price': price,
                    'timestamp': time.time()
                }), ex=300)
                return True
        last_price_cache[sym] = price
    except Exception as e:
        log.error(f'Price check error: {e}')
    return False

def check_order_burst():
    """주문 폭주 감지"""
    global order_timestamps
    now = time.time()
    order_timestamps.append(now)
    order_timestamps = [t for t in order_timestamps if now - t < 10]
    if len(order_timestamps) > ORDER_BURST_THRESHOLD:
        log.warning(f'ORDER BURST DETECTED: {len(order_timestamps)} orders in 10s')
        r.set('ares:event_guard:alert:order_burst', json.dumps({
            'count': len(order_timestamps), 'window_sec': 10,
            'timestamp': now
        }), ex=300)
        return True
    return False

def check_data_staleness():
    """데이터 stale 감지"""
    global last_data_ts
    now = time.time()
    if now - last_data_ts > DATA_STALE_SEC:
        log.warning(f'DATA STALE DETECTED: {now - last_data_ts:.0f}s since last update')
        r.set('ares:event_guard:alert:data_stale', json.dumps({
            'stale_seconds': round(now - last_data_ts),
            'timestamp': now
        }), ex=300)
        return True
    return False

def run():
    log.info('Event-Based Market Guard started')
    log.info(f'Monitoring channels: {channels}')
    log.info(f'Thresholds: price_spike={PRICE_CHANGE_THRESHOLD:.0%}, order_burst={ORDER_BURST_THRESHOLD}/10s, data_stale={DATA_STALE_SEC}s')
    
    for ch in channels:
        try:
            pubsub.subscribe(ch)
        except Exception:
            pass
    
    # 폴백: pub/sub 메시지가 없어도 주기적으로 데이터 stale 체크
    cycle = 0
    while True:
        try:
            msg = pubsub.get_message(timeout=5)
            if msg and msg['type'] == 'message':
                last_data_ts = time.time()
                try:
                    data = json.loads(msg['data']) if isinstance(msg['data'], str) else {}
                except:
                    data = {}
                
                ch = msg.get('channel', '')
                if 'price_update' in ch:
                    check_price_spike(data)
                elif 'order:intent' in ch:
                    check_order_burst()
            
            cycle += 1
            if cycle % 12 == 0:  # 매 60초마다 stale 체크
                check_data_staleness()
                r.set('ares:event_guard:heartbeat', json.dumps({
                    'status': 'alive', 'cycle': cycle, 'timestamp': time.time()
                }), ex=120)
                
        except redis.ConnectionError:
            log.error('Redis connection lost, reconnecting...')
            time.sleep(5)
        except Exception as e:
            log.error(f'Guard error: {e}')
            time.sleep(1)

if __name__ == '__main__':
    run()
