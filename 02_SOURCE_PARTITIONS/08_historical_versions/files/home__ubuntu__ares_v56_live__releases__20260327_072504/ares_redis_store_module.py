from typing import Any, Optional
# ares_redis_store_module.py - RedisStore
class RedisStore:
    def __init__(self, url: str):
        if redis is None:
            raise RuntimeError("redis-py is required at runtime for live orchestration")
        self.r = redis.from_url(url, decode_responses=False)
        self.r.ping()

    def get_json(self, key: str, default=None):
        v = self.r.get(key)
        if not v:
            return default
        try:
            if isinstance(v, bytes):
                v = v.decode()
            return json.loads(v)
        except Exception:
            return default

    def set_json(self, key: str, obj: Any, ex: Optional[int] = None):
        raw = json.dumps(obj, separators=(",", ":"), default=str)
        if ex:
            self.r.setex(key, ex, raw)
        else:
            self.r.set(key, raw)

    def hgetall_text(self, key: str) -> Dict[str, str]:
        raw = self.r.hgetall(key)
        out = {}
        for k, v in raw.items():
            kk = k.decode() if isinstance(k, bytes) else str(k)
            vv = v.decode() if isinstance(v, bytes) else str(v)
            out[kk] = vv
        return out

    _LUA_QUARANTINE = """
    local key = KEYS[1]
    local data = ARGV[1]
    local quarantine_prefix = ARGV[2]
    local current_type = redis.call('TYPE', key)
    if type(current_type) == 'table' then current_type = current_type['ok'] end
    if current_type ~= 'none' and current_type ~= 'list' then
        local t = redis.call('TIME')
        local seq_key = quarantine_prefix .. ':seq'
        local seq = redis.call('INCR', seq_key)
        redis.call('EXPIRE', seq_key, 604800)
        local quarantine_key = quarantine_prefix .. ':' .. t[1] .. '-' .. t[2] .. '-' .. seq
        redis.call('RENAME', key, quarantine_key)
        redis.call('EXPIRE', quarantine_key, 604800)
    end
    return redis.call('LPUSH', key, data)
    """
    _lua_quarantine_sha = None
    MAX_WRONGTYPE_RETRIES = 3

    def publish_history_point(self, key: str, payload: Dict[str, Any], ttl_sec: int, max_items: int = 2000):
        raw = json.dumps(payload, separators=(",", ":"), default=str)
        try:
            self.r.lpush(key, raw)
        except Exception as e:
            if "WRONGTYPE" in str(e):
                counter_key = f"ares:wrongtype:counter:{key}"
                count = self.r.incr(counter_key)
                self.r.expire(counter_key, 86400)
                if count > self.MAX_WRONGTYPE_RETRIES:
                    log.critical("WRONGTYPE on %s exceeded %dx — HALT justified, quarantine key check required", key, self.MAX_WRONGTYPE_RETRIES)
                    raise
                log.warning("WRONGTYPE on %s (count=%d) — Lua atomic quarantine + retry", key, count)
                if self._lua_quarantine_sha is None:
                    self._lua_quarantine_sha = self.r.script_load(self._LUA_QUARANTINE)
                try:
                    self.r.evalsha(self._lua_quarantine_sha, 1, key, raw, f"{key}:wrongtype")
                except Exception:
                    self.r.eval(self._LUA_QUARANTINE, 1, key, raw, f"{key}:wrongtype")
                log.info("Recovered %s via Lua atomic handler", key)
                self.r.ltrim(key, 0, max_items - 1)
                self.r.expire(key, ttl_sec)
                return
            else:
                raise
        self.r.ltrim(key, 0, max_items - 1)
        self.r.expire(key, ttl_sec)


# ---------------------------------------------------------------------------
# Market / account adapters
# ---------------------------------------------------------------------------
