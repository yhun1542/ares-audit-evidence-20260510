/**
 * redis_typed.mjs — Typed Redis Access Layer
 * ============================================
 * 4AI Consensus: Never let strategy code call raw mget()/get()/hgetall() ad hoc.
 * This module provides type-safe Redis access with automatic key type detection.
 * 
 * Eliminates the ENTIRE CLASS of WRONGTYPE errors permanently.
 */

// Key type registry — SSOT for all Redis key types
const KEY_TYPE_REGISTRY = {
  // HASH keys
  "regime:final:current": "hash",
  "emarkos:v1:positions": "hash",
  "emarkos:v1:cost_basis": "hash",
  "emarkos:v1:ops:policy_state": "hash",
  "emarkos:v1:ops:metrics": "hash",
  "emarkos:v1:sm:track": "hash",
  "emarkos:v1:ops:daily_report": "hash",
  "emarkos:v1:intent_state": "hash",
  "trading:limits": "hash",
  "ofg:session:metrics": "hash",
  // SET keys
  "emarkos:v1:universe:current": "set",
  "policy:buy_market:allow_symbols": "set",
  "policy:blocklist:symbols": "set",
  "policy:universe:symbols": "set",
  // LIST keys
  "regime:ms:history": "list",
  "regime:history": "list",
  "regime:final:history": "list",
  // ZSET keys
  "ares:equity:history": "zset",
  "emarkos:v1:daily_returns": "zset",
  "emarkos:v1:sm:active": "zset",
};

/**
 * readKeyFlex — Type-safe Redis key reader
 * Automatically detects key type and uses correct Redis command.
 * Falls back to runtime TYPE check if key not in registry.
 */
export async function readKeyFlex(redis, key) {
  const registeredType = KEY_TYPE_REGISTRY[key];
  let keyType = registeredType;
  
  if (!keyType) {
    try {
      keyType = await redis.type(key);
    } catch (e) {
      return null;
    }
  }
  
  try {
    switch (keyType) {
      case "string":
        return await redis.get(key);
      case "hash":
        const hashData = await redis.hGetAll ? await redis.hGetAll(key) : await redis.hgetall(key);
        return hashData && Object.keys(hashData).length > 0 ? hashData : null;
      case "set":
        return await redis.sMembers ? await redis.sMembers(key) : await redis.smembers(key);
      case "list":
        return await redis.lRange ? await redis.lRange(key, 0, -1) : await redis.lrange(key, 0, -1);
      case "zset":
        return await redis.zRange ? await redis.zRange(key, 0, -1, { withScores: true }) : await redis.zrange(key, 0, -1, "WITHSCORES");
      case "stream":
        return await redis.xRevRange ? await redis.xRevRange(key, "+", "-", { COUNT: 1 }) : null;
      default:
        return await redis.get(key);
    }
  } catch (e) {
    console.error(`[redis_typed] Error reading ${key} (type=${keyType}): ${e.message}`);
    return null;
  }
}

/**
 * mgetSafeSplit — Drop-in replacement for redis.mget()
 * Splits keys into STRING keys (use mget) and non-STRING keys (use readKeyFlex).
 * Returns array in same order as input keys.
 */
export async function mgetSafeSplit(redis, keys) {
  const results = new Array(keys.length).fill(null);
  const stringKeys = [];
  const stringIndices = [];
  const nonStringEntries = [];
  
  for (let i = 0; i < keys.length; i++) {
    const key = keys[i];
    const registeredType = KEY_TYPE_REGISTRY[key];
    if (registeredType && registeredType !== "string") {
      nonStringEntries.push({ index: i, key, type: registeredType });
    } else {
      stringKeys.push(key);
      stringIndices.push(i);
    }
  }
  
  // Batch mget for string keys
  if (stringKeys.length > 0) {
    try {
      const mgetResults = await redis.mget(stringKeys);
      for (let j = 0; j < mgetResults.length; j++) {
        results[stringIndices[j]] = mgetResults[j];
      }
    } catch (e) {
      console.error(`[redis_typed] mget error: ${e.message}`);
    }
  }
  
  // Individual reads for non-string keys
  for (const entry of nonStringEntries) {
    try {
      const val = await readKeyFlex(redis, entry.key);
      results[entry.index] = typeof val === "object" ? JSON.stringify(val) : val;
    } catch (e) {
      console.error(`[redis_typed] readKeyFlex error for ${entry.key}: ${e.message}`);
    }
  }
  
  return results;
}

/**
 * getRegimeFinal — Dedicated accessor for regime:final:current (HASH)
 * Returns { regime, confidence, ts, source } or null
 */
export async function getRegimeFinal(redis) {
  try {
    const data = await readKeyFlex(redis, "regime:final:current");
    if (!data) return null;
    return {
      regime: data.regime || data.state || null,
      confidence: parseFloat(data.confidence || "0"),
      ts: data.ts || data.updated_at || null,
      source: data.source || "unknown"
    };
  } catch (e) {
    console.error(`[redis_typed] getRegimeFinal error: ${e.message}`);
    return null;
  }
}

export default { readKeyFlex, mgetSafeSplit, getRegimeFinal, KEY_TYPE_REGISTRY };
