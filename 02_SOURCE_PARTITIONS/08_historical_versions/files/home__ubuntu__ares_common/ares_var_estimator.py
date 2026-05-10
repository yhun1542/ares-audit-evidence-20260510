#!/usr/bin/env python3
"""
ARES VaR/Gap Risk Estimator
- Historical VaR (95%, 99%) for current portfolio
- Gap risk estimate (overnight gap scenario)
- Correlation-aware portfolio risk
- Publishes risk metrics to Redis
"""
import redis, json, time, logging, os, math

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("var_estimator")

REDIS_PASS = os.environ.get("REDIS_PASSWORD", "Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U")

# Historical daily volatility estimates (annualized) for common US equities
# These are conservative estimates; in production, pull from market data
DEFAULT_VOL = {
    "SPY": 0.15, "QQQ": 0.20, "AAPL": 0.25, "MSFT": 0.22, "NVDA": 0.45,
    "AMZN": 0.28, "GOOGL": 0.25, "META": 0.35, "TSLA": 0.50, "AMD": 0.45,
    "AVGO": 0.30, "COST": 0.18, "LLY": 0.25, "UNH": 0.20, "JPM": 0.22,
    "V": 0.18, "MA": 0.20, "HD": 0.20, "PG": 0.12, "JNJ": 0.14,
    "ABBV": 0.20, "MRK": 0.18, "CVX": 0.22, "CAT": 0.25, "MCD": 0.15,
    "MS": 0.28, "MU": 0.40, "BA": 0.35, "CL": 0.14, "CMCSA": 0.22,
    "VRTX": 0.30, "GILD": 0.22, "REGN": 0.28, "OKTA": 0.45,
}
DEFAULT_DAILY_VOL = 0.25  # Fallback: 25% annualized
AVG_CORRELATION = 0.35  # Average pairwise correlation for US large-cap equities

class VaREstimator:
    def __init__(self):
        self.r = redis.Redis(host=os.environ.get("ARES_REDIS_HOST", "127.0.0.1"), port=int(os.environ.get("ARES_REDIS_PORT", "6379")), password=REDIS_PASS, decode_responses=True, connection_class=redis.SSLConnection if os.environ.get("ARES_REDIS_SSL", "false").lower() in ("true", "1") else redis.Connection)
        
    def get_positions_with_values(self):
        """Get positions with estimated market values"""
        positions = {}
        try:
            pos_data = self.r.hgetall("kis:broker:positions")
            for sym, val in pos_data.items():
                try:
                    p = json.loads(val) if isinstance(val, str) else val
                    qty = int(p.get("qty", p.get("hldg_qty", 0)))
                    # Try to get current price
                    price = float(p.get("avg_price", p.get("pchs_avg_pric", 0)))
                    if qty > 0 and price > 0:
                        positions[sym] = {
                            "qty": qty,
                            "price": price,
                            "notional": qty * price
                        }
                except:
                    pass
        except:
            pass
        return positions
    
    def get_equity(self):
        """Get current equity"""
        for key in ["kis:equity:total", "ares:equity:current", "ares:pnl_circuit_breaker:status"]:
            val = self.r.get(key)
            if val:
                try:
                    if key.endswith(":status"):
                        d = json.loads(val)
                        eq = float(d.get("equity", 0))
                    else:
                        eq = float(val)
                    if eq > 0:
                        return eq
                except:
                    pass
        return 0
    
    def estimate_daily_vol(self, symbol):
        """Get estimated daily volatility for a symbol"""
        annual_vol = DEFAULT_VOL.get(symbol, DEFAULT_DAILY_VOL)
        return annual_vol / math.sqrt(252)  # Convert to daily
    
    def calculate_portfolio_var(self, positions, equity, confidence=0.95):
        """
        Calculate portfolio VaR using parametric method with correlation adjustment
        
        For N positions with weights w_i and daily vols sigma_i:
        Portfolio variance = sum(w_i^2 * sigma_i^2) + 2 * rho * sum(w_i * w_j * sigma_i * sigma_j)
        
        Using average correlation simplification
        """
        if not positions or equity <= 0:
            return {"var_95_pct": 0, "var_99_pct": 0, "var_95_usd": 0, "var_99_usd": 0, "gap_risk_pct": 0, "gap_risk_usd": 0, "portfolio_daily_vol_pct": 0, "total_notional": 0, "effective_positions": 0, "hhi": 0, "worst_single_name": "N/A", "worst_single_loss_usd": 0, "avg_correlation_used": 0.35, "position_count": 0}
        
        total_notional = sum(p["notional"] for p in positions.values())
        if total_notional <= 0:
            return {"var_95_pct": 0, "var_99_pct": 0, "var_95_usd": 0, "var_99_usd": 0, "gap_risk_pct": 0, "gap_risk_usd": 0, "portfolio_daily_vol_pct": 0, "total_notional": 0, "effective_positions": 0, "hhi": 0, "worst_single_name": "N/A", "worst_single_loss_usd": 0, "avg_correlation_used": 0.35, "position_count": 0}
        
        # Calculate weighted portfolio variance
        weights = {}
        daily_vols = {}
        for sym, p in positions.items():
            weights[sym] = p["notional"] / total_notional
            daily_vols[sym] = self.estimate_daily_vol(sym)
        
        # Idiosyncratic component: sum(w_i^2 * sigma_i^2)
        idio_var = sum(w**2 * daily_vols[sym]**2 for sym, w in weights.items())
        
        # Systematic component: rho * sum(w_i * sigma_i) * sum(w_j * sigma_j) - rho * sum(w_i^2 * sigma_i^2)
        weighted_vol_sum = sum(w * daily_vols[sym] for sym, w in weights.items())
        sys_var = AVG_CORRELATION * (weighted_vol_sum**2 - sum(w**2 * daily_vols[sym]**2 for sym, w in weights.items()))
        
        portfolio_var = idio_var + sys_var
        portfolio_vol = math.sqrt(max(portfolio_var, 0))
        
        # Z-scores for confidence levels
        z_95 = 1.645
        z_99 = 2.326
        
        var_95_pct = portfolio_vol * z_95
        var_99_pct = portfolio_vol * z_99
        
        var_95_usd = var_95_pct * total_notional
        var_99_usd = var_99_pct * total_notional
        
        # Gap risk: 3x daily vol (overnight gap scenario)
        gap_risk_pct = portfolio_vol * 3.0
        gap_risk_usd = gap_risk_pct * total_notional
        
        # Concentration risk: Herfindahl index
        hhi = sum(w**2 for w in weights.values())
        effective_positions = 1.0 / hhi if hhi > 0 else len(positions)
        
        # Worst-case: largest position * 3 daily vols
        max_pos = max(positions.items(), key=lambda x: x[1]["notional"])
        worst_single = max_pos[1]["notional"] * self.estimate_daily_vol(max_pos[0]) * 3
        
        return {
            "var_95_pct": round(var_95_pct * 100, 2),
            "var_99_pct": round(var_99_pct * 100, 2),
            "var_95_usd": round(var_95_usd, 2),
            "var_99_usd": round(var_99_usd, 2),
            "gap_risk_pct": round(gap_risk_pct * 100, 2),
            "gap_risk_usd": round(gap_risk_usd, 2),
            "portfolio_daily_vol_pct": round(portfolio_vol * 100, 2),
            "total_notional": round(total_notional, 2),
            "effective_positions": round(effective_positions, 1),
            "hhi": round(hhi, 4),
            "worst_single_name": max_pos[0],
            "worst_single_loss_usd": round(worst_single, 2),
            "avg_correlation_used": AVG_CORRELATION,
            "position_count": len(positions)
        }
    
    def check_risk_limits(self, var_result, equity):
        """Check if VaR exceeds risk limits"""
        alerts = []
        
        # VaR 99% should not exceed 5% of equity
        if equity > 0 and var_result["var_99_usd"] > equity * 0.05:
            alerts.append({
                "level": "CRITICAL",
                "msg": f"VaR99 ${var_result['var_99_usd']:.0f} exceeds 5% of equity ${equity:.0f}"
            })
        
        # Gap risk should not exceed 8% of equity (matches DD hard stop)
        if equity > 0 and var_result["gap_risk_usd"] > equity * 0.08:
            alerts.append({
                "level": "WARNING",
                "msg": f"Gap risk ${var_result['gap_risk_usd']:.0f} exceeds 8% DD hard stop ${equity*0.08:.0f}"
            })
        
        # Effective positions should be > 5 for diversification
        if var_result["effective_positions"] < 5:
            alerts.append({
                "level": "WARNING",
                "msg": f"Low diversification: effective positions = {var_result['effective_positions']}"
            })
        
        # HHI should be < 0.1 (well-diversified)
        if var_result["hhi"] > 0.1:
            alerts.append({
                "level": "WARNING",
                "msg": f"High concentration: HHI = {var_result['hhi']}"
            })
            
        return alerts
    
    def check_cycle(self):
        positions = self.get_positions_with_values()
        equity = self.get_equity()
        
        var_result = self.calculate_portfolio_var(positions, equity)
        alerts = self.check_risk_limits(var_result, equity)
        
        status = {
            "ts": time.time(),
            "equity": equity,
            **var_result,
            "alerts": alerts,
            "alert_count": len(alerts),
            "status": "CRITICAL" if any(a["level"] == "CRITICAL" for a in alerts) else
                      "WARNING" if alerts else "GREEN"
        }
        
        self.r.setex("ares:var:status", 120, json.dumps(status))
        self.r.setex("ares:var:var95", 120, str(var_result["var_95_usd"]))
        self.r.setex("ares:var:var99", 120, str(var_result["var_99_usd"]))
        self.r.setex("ares:var:gap_risk", 120, str(var_result["gap_risk_usd"]))
        
        # Publish to CloudWatch-compatible metric
        self.r.setex("ares:metric:var_99_pct", 120, str(var_result["var_99_pct"]))
        self.r.setex("ares:metric:gap_risk_pct", 120, str(var_result["gap_risk_pct"]))
        
        return status
    
    def run(self):
        logger.info("VaR Estimator started")
        cycle = 0
        while True:
            try:
                status = self.check_cycle()
                cycle += 1
                if cycle % 6 == 0:  # Log every minute
                    logger.info(
                        f"VaR95={status['var_95_pct']:.2f}% (${status['var_95_usd']:.0f}) | "
                        f"VaR99={status['var_99_pct']:.2f}% (${status['var_99_usd']:.0f}) | "
                        f"GapRisk={status['gap_risk_pct']:.2f}% (${status['gap_risk_usd']:.0f}) | "
                        f"EffPos={status['effective_positions']:.1f} | "
                        f"Status={status['status']}"
                    )
                if status["alerts"]:
                    for a in status["alerts"]:
                        logger.warning(f"ALERT [{a['level']}]: {a['msg']}")
                time.sleep(10)
            except Exception as e:
                logger.error(f"Error: {e}")
                time.sleep(30)

if __name__ == "__main__":
    VaREstimator().run()
