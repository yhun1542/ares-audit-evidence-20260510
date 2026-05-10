# ares_market_data_module.py - MarketAccountReader
class MarketAccountReader:
    def __init__(self, store: RedisStore, cfg: LiveAutopilotConfig):
        self.store = store
        self.cfg = cfg

    def _read_first_float(self, keys: Iterable[str]) -> Optional[float]:
        for key in keys:
            try:
                v = self.store.r.get(key)
                if v is None:
                    continue
                if isinstance(v, bytes):
                    v = v.decode()
                return float(v)
            except Exception:
                continue
        return None

    def get_vix(self) -> float:
        return self._read_first_float(self.cfg.execution.vix_keys) or 20.0

    def get_spy(self) -> float:
        return self._read_first_float(self.cfg.execution.spy_keys) or 0.0

    def get_equity(self) -> float:
        for key in self.cfg.execution.equity_keys:
            raw = self.store.r.get(key)
            if not raw:
                continue
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                data = json.loads(raw)
                if isinstance(data, dict):
                    return safe_float(data.get("total", data.get("equity", 0.0)), 0.0)
                return safe_float(data, 0.0)
            except Exception:
                try:
                    return float(raw)
                except Exception:
                    continue
        return 0.0

    def get_open_orders(self) -> int:
        raw = self.store.r.get(self.cfg.execution.open_orders_key)
        try:
            if raw is None:
                return 0
            if isinstance(raw, bytes):
                raw = raw.decode()
            return int(float(raw))
        except Exception:
            return 0

    def get_quote(self, sym: str) -> Tuple[Optional[float], Optional[float]]:
        for tmpl in self.cfg.execution.price_keys:
            key = tmpl.format(sym=sym)
            raw = self.store.r.get(key)
            if raw is None:
                continue
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                data = json.loads(raw)
                if isinstance(data, dict):
                    px = safe_float(data.get("last", data.get("price")))
                    ts = data.get("ts") or data.get("timestamp")
                    dt = parse_date_str(ts)
                    age = (utcnow() - dt).total_seconds() if dt else None
                    if px > 0:
                        return px, age
                else:
                    px = float(data)
                    return px, None
            except Exception:
                try:
                    px = float(raw)
                    return px, None
                except Exception:
                    continue
        return None, None

    def get_positions(self) -> Dict[str, Dict[str, float]]:
        raw = self.store.r.get(self.cfg.execution.positions_key)
        if not raw:
            return {}
        try:
            if isinstance(raw, bytes):
                raw = raw.decode()
            data = json.loads(raw)
        except Exception:
            return {}

        # Accept dict[str, payload] or {positions:{...}} or list[dict]
        if isinstance(data, dict) and "positions" in data:
            data = data["positions"]
        out: Dict[str, Dict[str, float]] = {}
        if isinstance(data, list):
            for row in data:
                if not isinstance(row, dict):
                    continue
                sym = row.get("symbol") or row.get("ticker")
                if not sym:
                    continue
                out[str(sym)] = {
                    "qty": safe_float(row.get("qty", row.get("quantity", row.get("shares", 0.0)))),
                    "market_value": safe_float(row.get("market_value", row.get("marketValue", row.get("value", 0.0)))),
                    "weight": safe_float(row.get("weight", 0.0)),
                    "side": 1.0 if str(row.get("side", "LONG")).upper() != "SHORT" else -1.0,
                }
        elif isinstance(data, dict):
            for sym, row in data.items():
                if not isinstance(row, dict):
                    continue
                out[str(sym)] = {
                    "qty": safe_float(row.get("qty", row.get("quantity", row.get("shares", 0.0)))),
                    "market_value": safe_float(row.get("market_value", row.get("marketValue", row.get("value", 0.0)))),
                    "weight": safe_float(row.get("weight", 0.0)),
                    "side": 1.0 if str(row.get("side", "LONG")).upper() != "SHORT" else -1.0,
                }
        return out

    def positions_to_weights(self, symbols: List[str], fallback_prices: Dict[str, float]) -> Tuple[np.ndarray, Dict[str, Any]]:
        positions = self.get_positions()
        equity = max(self.get_equity(), 1.0)
        weights = np.zeros(len(symbols), dtype=np.float64)
        stale_symbols = []
        for i, sym in enumerate(symbols):
            row = positions.get(sym)
            if not row:
                continue
            if abs(row.get("weight", 0.0)) > 1e-9:
                weights[i] = row["weight"] * row.get("side", 1.0)
                continue
            mv = row.get("market_value", 0.0)
            qty = row.get("qty", 0.0)
            px = None
            age = None
            if mv == 0.0 and qty != 0.0:
                px, age = self.get_quote(sym)
                if px is None:
                    px = fallback_prices.get(sym)
                if age is not None and age > self.cfg.kernel.max_quote_stale_sec:
                    stale_symbols.append(sym)
                mv = qty * (px or 0.0)
            weights[i] = mv / equity if equity > 0 else 0.0
        return weights, {"stale_symbols": stale_symbols, "positions": positions, "equity": equity}


# ---------------------------------------------------------------------------
# Execution bus
# ---------------------------------------------------------------------------
