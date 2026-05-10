/**
 * data-gateway-health-publisher v2
 * [STRUCT-FIX-RAM26-2026-05-07]
 * gw:macro:*, gw:market:*, gw:news:* 키들은 deprecated.
 * 실제 데이터 공급은 realtime-data-feed-v3 → realtime:feed:* 키로 이루어짐.
 * 따라서 realtime:feed:universe:ts 와 regime:final:current:str 의 freshness를 체크.
 *
 * redis v5: scanIterator yields batches (arrays), not individual keys
 */
import { createClient } from 'redis';
const REDIS_URL = process.env.ARES_REDIS_URL || process.env.REDIS_URL;
const POLL_MS = 30_000;
const TTL_SEC = 60;
const GW_HEALTH_KEY = 'gw:health:status';
const GW_TS_KEY = 'gw:health:timestamp';
const HB_KEY = 'ares:health:data-gateway-health-publisher:heartbeat';
const HB_TTL = 90;

// 실제 데이터 공급 키들 (gw:* deprecated → realtime:feed:* 사용)
const DATA_KEYS = [
  'realtime:feed:universe:ts',
  'realtime:feed:universe:count',
  'regime:final:current:str',
];
// 최대 허용 staleness (초)
const MAX_STALE_SEC = 14400; // 4시간 - 시장 외 시간 허용 (장 중에는 5분 이내 갱신됨)

function nowIso() { return new Date().toISOString(); }
function log(level, msg, extra = {}) {
  console.log(JSON.stringify({ ts: nowIso(), level, proc: 'data-gateway-health-publisher', msg, ...extra }));
}

async function checkGatewayHealth(redis) {
  let totalKeys = 0;
  let staleKeys = 0;
  const details = {};

  for (const key of DATA_KEYS) {
    const exists = await redis.exists(key);
    if (!exists) {
      staleKeys++;
      details[key] = 'MISSING';
      continue;
    }
    totalKeys++;
    const ttl = await redis.ttl(key);
    if (ttl === -2) {
      staleKeys++;
      details[key] = 'EXPIRED';
    } else {
      details[key] = ttl === -1 ? 'NO_TTL' : `TTL:${ttl}s`;
    }
  }

  // realtime:feed:universe:ts 값 freshness 체크
  try {
    const feedTs = await redis.get('realtime:feed:universe:ts');
    if (feedTs) {
      const ageMs = Date.now() - new Date(feedTs).getTime();
      const ageSec = Math.floor(ageMs / 1000);
      details['realtime:feed:universe:ts:age_sec'] = ageSec;
      if (ageSec > MAX_STALE_SEC) {
        staleKeys++;
        details['realtime:feed:universe:ts'] = `STALE:${ageSec}s`;
      }
    }
  } catch (e) { /* ignore */ }

  let status = 'HEALTHY';
  if (totalKeys === 0) {
    status = 'CRITICAL';
  } else if (staleKeys > 0) {
    status = 'DEGRADED';
  }
  return { status, totalKeys, staleKeys, details };
}

async function main() {
  if (!REDIS_URL) {
    console.error('[FATAL] ARES_REDIS_URL/REDIS_URL not set');
    process.exit(1);
  }
  const redis = createClient({ url: REDIS_URL, socket: { tls: true, rejectUnauthorized: false } });
  await redis.connect();
  log('INFO', 'data-gateway-health-publisher v2 starting', { poll_ms: POLL_MS, data_keys: DATA_KEYS });

  async function cycle() {
    try {
      const { status, totalKeys, staleKeys, details } = await checkGatewayHealth(redis);
      const ts = nowIso();
      await redis.set(GW_HEALTH_KEY, status, { EX: TTL_SEC });
      await redis.set(GW_TS_KEY, ts, { EX: TTL_SEC });
      await redis.set(HB_KEY, JSON.stringify({ ts, status, total_keys: totalKeys, stale_keys: staleKeys }), { EX: HB_TTL });
      log('INFO', 'GW_HEALTH_PUBLISHED', { status, total_keys: totalKeys, stale_keys: staleKeys, details });
    } catch (e) {
      log('ERROR', 'cycle error', { error: e.message });
    }
  }
  await cycle();
  setInterval(cycle, POLL_MS);
}
main().catch(e => { console.error('[FATAL]', e); process.exit(1); });
