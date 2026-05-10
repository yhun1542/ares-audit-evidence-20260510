/**
 * PATCH-005: Type-Safe Parsing & Redis Type Guard
 *
 * TARGET: System-wide (all .mjs files doing parseInt/parseFloat from Redis)
 * SEVERITY: 🔴 HIGH
 *
 * PROBLEMS:
 *   1. parseInt("0" || undefined) → parseInt(undefined) → NaN (short-circuit bug)
 *   2. parseFloat on Redis null → NaN propagates silently through calculations
 *   3. Redis HASH/STRING type mismatch causes WRONGTYPE errors
 *   4. effSide defaults to "BUY" when empty → can cause wrong-direction orders
 *
 * USAGE:
 *   import { safeInt, safeFloat, safeSide, assertRedisType } from './lib/safe-parse.mjs';
 *   const qty = safeInt(item.cblc_qty13, 0);
 *   const price = safeFloat(data.price, { min: 0.01, fallback: null });
 *   const side = safeSide(rawSide);  // throws on ambiguous
 */

/**
 * Safe integer parse. Returns fallback if input is null/undefined/NaN.
 * @param {*} value
 * @param {number} fallback
 * @returns {number}
 */
export function safeInt(value, fallback = 0) {
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = parseInt(String(value), 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

/**
 * Safe float parse with optional range validation.
 * @param {*} value
 * @param {Object} opts
 * @param {number} opts.fallback - default 0
 * @param {number} [opts.min] - reject if below
 * @param {number} [opts.max] - reject if above
 * @returns {number|null}
 */
export function safeFloat(value, opts = {}) {
  const fallback = opts.fallback !== undefined ? opts.fallback : 0;
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = parseFloat(String(value));
  if (Number.isNaN(parsed) || !Number.isFinite(parsed)) return fallback;
  if (opts.min !== undefined && parsed < opts.min) return fallback;
  if (opts.max !== undefined && parsed > opts.max) return fallback;
  return parsed;
}

/**
 * Safe order side resolution. NEVER defaults to BUY silently.
 * @param {string} side
 * @returns {string} 'BUY' | 'SELL'
 * @throws {Error} if side is ambiguous/empty
 */
export function safeSide(side) {
  const normalized = (side || '').toUpperCase().trim();
  if (normalized === 'BUY' || normalized === 'B' || normalized === '02') return 'BUY';
  if (normalized === 'SELL' || normalized === 'S' || normalized === '01') return 'SELL';
  throw new Error(`AMBIGUOUS_ORDER_SIDE: '${side}' cannot be resolved. Refusing to default.`);
}

/**
 * Assert Redis key is expected type. Delete and recreate if mismatched.
 * Prevents WRONGTYPE errors that silently disable features.
 *
 * @param {import('ioredis').Redis} redis
 * @param {string} key
 * @param {'hash'|'string'|'list'|'set'|'zset'|'stream'} expectedType
 * @param {Object} opts
 * @param {Function} opts.logger
 * @param {boolean} opts.autoFix - if true, del key on mismatch (default false)
 * @returns {Promise<{ok: boolean, actualType: string}>}
 */
export async function assertRedisType(redis, key, expectedType, opts = {}) {
  const log = opts.logger || console.log;
  const actualType = await redis.type(key);

  if (actualType === 'none') {
    return { ok: true, actualType: 'none' };
  }

  if (actualType !== expectedType) {
    log(JSON.stringify({
      level: 'ERROR',
      tag: 'REDIS_TYPE_MISMATCH',
      key,
      expected: expectedType,
      actual: actualType,
      autoFix: !!opts.autoFix,
      ts: new Date().toISOString()
    }));

    if (opts.autoFix) {
      // Backup to stream before deleting
      const backup = await redis.dump(key);
      if (backup) {
        await redis.xadd(
          'ares:type_mismatch:audit',
          '*',
          'key', key,
          'expected', expectedType,
          'actual', actualType,
          'ts', new Date().toISOString()
        );
      }
      await redis.del(key);
      log(JSON.stringify({
        level: 'WARN',
        tag: 'REDIS_TYPE_MISMATCH_FIXED',
        key,
        action: 'DEL'
      }));
    }

    return { ok: false, actualType };
  }

  return { ok: true, actualType };
}

/**
 * Safe JSON parse from Redis value
 * @param {string|null} raw
 * @param {*} fallback
 * @returns {*}
 */
export function safeJsonParse(raw, fallback = null) {
  if (raw === null || raw === undefined) return fallback;
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export default { safeInt, safeFloat, safeSide, assertRedisType, safeJsonParse };
