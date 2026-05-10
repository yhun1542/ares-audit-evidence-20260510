export const KEY_CONTRACTS = {
  "regime:final:current": "hash",
  "ares:ops:*": "string",
  "ares:equity:total": "string",
  "ares:equity:snapshot": "string_json",
  "emarkos:v1:open_orders": "string_json",
  "policy:execution_router": "string_json",
  "trading:enabled": "string",
  "trade:halt": "string",
  "ssot:target:v2:current": "flex",
  "kis:broker:positions": "flex",
  "emarkos:v1:universe:current": "flex",
};

function wildcardMatch(pattern, value) {
  const re = new RegExp("^" + pattern.replace(/\*/g, ".*") + "$");
  return re.test(value);
}

function contractFor(key) {
  for (const [pat, kind] of Object.entries(KEY_CONTRACTS)) {
    if (wildcardMatch(pat, key)) return kind;
  }
  return "flex";
}

function parseMaybeJson(raw) {
  if (raw == null) return null;
  if (typeof raw !== "string") return raw;
  const s = raw.trim();
  if (!s) return raw;
  try {
    return JSON.parse(s);
  } catch {
    return raw;
  }
}

export async function readKeyFlex(redis, key) {
  const expected = contractFor(key);
  const actual = String(await redis.type(key));

  if (actual === "none") return { value: null, actual, expected };
  if (actual === "string") {
    const raw = await redis.get(key);
    const value = (expected === "string_json" || expected === "flex")
      ? parseMaybeJson(raw)
      : raw;
    return { value, actual, expected };
  }
  if (actual === "hash") {
    return { value: await redis.hgetall(key), actual, expected };
  }
  if (actual === "set") {
    return { value: (await redis.smembers(key)).sort(), actual, expected };
  }
  if (actual === "list") {
    return { value: await redis.lrange(key, 0, -1), actual, expected };
  }
  if (actual === "zset") {
    return { value: await redis.zrange(key, 0, -1, "WITHSCORES"), actual, expected };
  }
  return { value: null, actual, expected };
}

export async function mgetSafeSplit(redis, keys) {
  const pipe = redis.pipeline();
  for (const k of keys) pipe.type(k);
  const typed = await pipe.exec();

  const typeMap = {};
  const stringKeys = [];
  keys.forEach((k, idx) => {
    const actual = String(typed[idx]?.[1] ?? "none");
    typeMap[k] = actual;
    if (actual === "string") stringKeys.push(k);
  });

  const values = {};
  const violations = [];

  if (stringKeys.length) {
    const raws = await redis.mget(...stringKeys);
    stringKeys.forEach((k, i) => {
      const expected = contractFor(k);
      values[k] =
        expected === "string_json" || expected === "flex"
          ? parseMaybeJson(raws[i])
          : raws[i];
    });
  }

  for (const k of keys) {
    if (!(k in values)) {
      const one = await readKeyFlex(redis, k);
      values[k] = one.value;
    }
    const expected = contractFor(k);
    const actual = typeMap[k];
    if (expected === "hash" && actual !== "hash" && actual !== "none") {
      violations.push({ key: k, expected, actual });
    }
    if (expected === "string" && actual !== "string" && actual !== "none") {
      violations.push({ key: k, expected, actual });
    }
  }

  return { values, types: typeMap, violations };
}
