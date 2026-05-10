#!/usr/bin/env python3
"""
ares_io.py — Redis I/O for IonQ Live Rebalancer
v2.0: Patched to use actual EC2 Redis key names (2026-04-12)

KEY MAPPING (bundle original → actual EC2):
  champion:v660:signals  → ssot:target:v2:current (JSON string)
  sleeve:ares:weights    → sleeve:v3:final_weights:current (JSON string)
  ms_regime              → regime:current:str (JSON string with .regime field)
  r15_regime             → regime15:current (JSON string with .regime field)
  final_regime           → regime:unified:current:str (JSON string)
  gpu_regime             → gpu:per_stock_regime:latest (string)
  go_nogo                → ares:go_nogo:verdict (JSON string with .result field)
  trading:enabled        → trading:enabled (unchanged)
  ares:equity:total      → ares:equity:total (unchanged)
  ares:equity:day_start  → ares:equity:day_start (unchanged)
  emarkos:v1:positions   → emarkos:v1:positions (unchanged)
"""

import os, json, logging, redis, ssl

log = logging.getLogger("ares_io")

# ── helpers ──────────────────────────────────────────────────
def getenv_bool(key, default=False):
    v = os.getenv(key, "")
    return v.lower() in ("1", "true", "yes") if v else default

def normalize_weights(w: dict) -> dict:
    s = sum(w.values())
    if s <= 0:
        return w
    return {k: v / s for k, v in w.items()}

def turnover_bps(old: dict, new: dict) -> float:
    keys = set(old) | set(new)
    return sum(abs(old.get(k, 0) - new.get(k, 0)) for k in keys) * 5000

# ── Redis connection ─────────────────────────────────────────
def get_redis():
    """Connect to Redis using env vars from /etc/ares/redis.env"""
    url = os.getenv("REDIS_URL", "")
    if url:
        return redis.from_url(url, decode_responses=True, socket_timeout=10)
    
    host = os.getenv("ARES_REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("ARES_REDIS_PORT", "6379"))
    password = os.getenv("ARES_REDIS_PASSWORD") or os.getenv("REDIS_PASSWORD", "")
    use_ssl = os.getenv("ARES_REDIS_SSL", "false").lower() in ("1", "true", "yes")
    db = int(os.getenv("ARES_REDIS_DB", "0"))
    
    kwargs = dict(
        host=host, port=port, db=db,
        decode_responses=True, socket_timeout=10,
    )
    if password:
        kwargs["password"] = password
    if use_ssl:
        kwargs["ssl"] = True
        kwargs["ssl_cert_reqs"] = None
    
    return redis.Redis(**kwargs)

def get_json(client, key):
    """Safe GET + JSON parse"""
    raw = client.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw

# ── load_inputs ──────────────────────────────────────────────
def load_inputs(client=None):
    """
    Load all inputs from Redis using actual EC2 key names.
    Returns dict with: signals, weights, regime, positions, equity, config
    """
    if client is None:
        client = get_redis()
    
    result = {}
    
    # 1. Trading gate checks
    trading_enabled = client.get("trading:enabled")
    result["trading_enabled"] = trading_enabled and trading_enabled.lower() in ("1", "true", "yes")
    
    trade_halt = client.get("trade:halt")
    result["trade_halt"] = trade_halt
    
    go_nogo_raw = get_json(client, "ares:go_nogo:verdict")
    if isinstance(go_nogo_raw, dict):
        result["go_nogo"] = go_nogo_raw.get("result", "NOGO")
        result["go_nogo_reason"] = go_nogo_raw.get("reason", "")
    else:
        result["go_nogo"] = str(go_nogo_raw) if go_nogo_raw else "NOGO"
        result["go_nogo_reason"] = ""
    
    # 2. SSOT target signals (replaces champion:v660:signals)
    ssot_current = get_json(client, "ssot:target:v2:current")
    if isinstance(ssot_current, dict):
        targets = ssot_current.get("targets", {})
        positions = targets.get("positions", [])
        result["signals"] = {p["symbol"]: p.get("w", 0) for p in positions if "symbol" in p}
        result["ssot_gross"] = targets.get("gross", 1.0)
        result["ssot_engine"] = ssot_current.get("engine_version", "")
        result["ssot_ts"] = ssot_current.get("ts", 0)
        result["ssot_asof"] = ssot_current.get("asof", "")
    else:
        result["signals"] = {}
        result["ssot_gross"] = 1.0
        result["ssot_engine"] = ""
        result["ssot_ts"] = 0
        result["ssot_asof"] = ""
    
    # 3. Sleeve weights (replaces sleeve:ares:weights)
    sleeve_raw = get_json(client, "sleeve:v3:final_weights:current")
    if isinstance(sleeve_raw, dict):
        result["weights"] = sleeve_raw.get("weights", {})
        result["sleeve_mix"] = sleeve_raw.get("sleeve_mix", {})
        result["sleeve_version"] = sleeve_raw.get("version", "")
    else:
        result["weights"] = {}
        result["sleeve_mix"] = {}
        result["sleeve_version"] = ""
    
    # 4. Regime (replaces ms_regime, r15_regime, etc.)
    regime_str = get_json(client, "regime:current:str")
    if isinstance(regime_str, dict):
        result["ms_regime"] = regime_str.get("regime", "UNKNOWN")
        result["vol_scale"] = float(regime_str.get("volScale", "1.0"))
        result["max_leverage"] = float(regime_str.get("maxLeverage", "1.0"))
    else:
        result["ms_regime"] = str(regime_str) if regime_str else "UNKNOWN"
        result["vol_scale"] = 1.0
        result["max_leverage"] = 1.0
    
    r15_raw = get_json(client, "regime15:current")
    if isinstance(r15_raw, dict):
        result["r15_regime"] = r15_raw.get("regime", "UNKNOWN")
        result["r15_confidence"] = r15_raw.get("confidence", 0)
    else:
        result["r15_regime"] = str(r15_raw) if r15_raw else "UNKNOWN"
        result["r15_confidence"] = 0
    
    # 5. Positions (unchanged key)
    pos_raw = get_json(client, "emarkos:v1:positions")
    if isinstance(pos_raw, dict):
        result["positions"] = pos_raw.get("positions", {})
        result["total_market_value"] = pos_raw.get("totalMarketValue", 0)
        result["position_count"] = pos_raw.get("count", 0)
    else:
        result["positions"] = {}
        result["total_market_value"] = 0
        result["position_count"] = 0
    
    # 6. Equity (unchanged keys)
    eq_total = client.get("ares:equity:total")
    result["equity_total"] = float(eq_total) if eq_total else 0
    
    eq_start = client.get("ares:equity:day_start")
    result["equity_day_start"] = float(eq_start) if eq_start else 0
    
    # 7. Champion universe
    universe = client.smembers("champion:universe:global")
    result["universe"] = list(universe) if universe else []
    
    # 8. Factor weights
    factor_raw = get_json(client, "factor:active_weights")
    result["factor_weights"] = factor_raw if isinstance(factor_raw, dict) else {}
    
    # 9. SSOT meta
    meta = client.hgetall("ssot:target:v2:meta") if client.type("ssot:target:v2:meta") == "hash" else {}
    result["ssot_meta"] = meta
    
    log.info(f"Loaded: {len(result['signals'])} signals, {len(result['weights'])} weights, "
             f"regime={result['ms_regime']}, positions={result['position_count']}, "
             f"equity=${result['equity_total']:,.2f}")
    
    return result

# ── publish_result ───────────────────────────────────────────
def publish_result(client, result: dict, channel="ionq:rebalance:result"):
    """Publish rebalance result to Redis"""
    if client is None:
        client = get_redis()
    client.set("ionq:rebalance:latest", json.dumps(result))
    client.publish(channel, json.dumps(result))
    log.info(f"Published result: status={result.get('status')}")

# ── parse_windows / should_trade ─────────────────────────────
def parse_windows(raw: str) -> list:
    """Parse trading windows like '09:30-11:00,14:00-16:00'"""
    if not raw:
        return []
    windows = []
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            windows.append((start.strip(), end.strip()))
    return windows

def should_trade(inputs: dict) -> tuple:
    """
    Check if trading is allowed based on inputs.
    Returns (bool, reason_string)
    """
    if inputs.get("trade_halt"):
        return False, f"trade_halt={inputs['trade_halt']}"
    
    if not inputs.get("trading_enabled"):
        return False, "trading_disabled"
    
    go = inputs.get("go_nogo", "NOGO")
    if go != "GO":
        return False, f"go_nogo={go}"
    
    if not inputs.get("signals"):
        return False, "no_signals"
    
    return True, "OK"

# ── load_config ──────────────────────────────────────────────
def load_config():
    """Load configuration from environment variables"""
    return {
        "quantum_weight": float(os.getenv("QUANTUM_WEIGHT", "0.30")),
        "classical_weight": float(os.getenv("CLASSICAL_WEIGHT", "0.70")),
        "max_turnover_bps": float(os.getenv("MAX_TURNOVER_BPS", "500")),
        "max_position_pct": float(os.getenv("MAX_POSITION_PCT", "15")),
        "min_position_pct": float(os.getenv("MIN_POSITION_PCT", "0.5")),
        "cash_target_pct": float(os.getenv("CASH_TARGET_PCT", "5")),
        "cardinality_penalty": float(os.getenv("QUANTUM_CARDINALITY_PENALTY", "1.5")),
        "rebalance_interval": int(os.getenv("REBALANCE_INTERVAL_SEC", "300")),
        "dry_run": getenv_bool("DRY_RUN", True),
        "ionq_backend": os.getenv("IONQ_BACKEND", "simulator"),
        "qaoa_layers": int(os.getenv("QAOA_LAYERS", "2")),
        "qaoa_shots": int(os.getenv("QAOA_SHOTS", "500")),
    }


# ── Backward Compatibility ───────────────────────────────────
class AresRedis:
    """Backward-compatible wrapper for ionq_live_rebalancer.py"""
    def __init__(self):
        self.client = get_redis()
    
    def load_inputs(self):
        return load_inputs(self.client)
    
    def should_trade(self, inputs=None):
        if inputs is None:
            inputs = self.load_inputs()
        return should_trade(inputs)
    
    def publish_result(self, result, channel="ionq:rebalance:result"):
        publish_result(self.client, result, channel)
    
    def get(self, key):
        return self.client.get(key)
    
    def set(self, key, value):
        return self.client.set(key, value)
    
    def ping(self):
        return self.client.ping()
