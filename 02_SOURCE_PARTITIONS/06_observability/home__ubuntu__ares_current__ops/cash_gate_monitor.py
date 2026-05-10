#!/usr/bin/env python3
"""Cash Gate Monitor v2.0 — Cash Ledger Split + State Machine reporting.

[P0-FIX] SyntaxError 수정: redis.from_url() 괄호 닫힘 문제 해결
"""
import os, json, redis
import ares_redis_env  # [SSOT v2.0] Force REDIS_URL from /etc/ares/redis.env


def main():
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        print("FATAL: REDIS_URL not set. Refusing to use localhost fallback.")
        return
    r = redis.from_url(redis_url, decode_responses=True)

    # Cash Ledger Split keys
    broker_cash = float(r.get("kis:cash:broker_available_usd") or "0")
    reserved_buy = float(r.get("kis:cash:reserved_buy_usd") or "0")
    reserved_buy_local_raw = r.get("kis:cash:reserved_buy_usd_local")
    reserved_buy_local = round(max(0, int(reserved_buy_local_raw or 0)) / 100, 2)  # stored as cents
    safety_buffer = float(r.get("kis:cash:safety_buffer_usd") or "0")
    usable_cash = float(r.get("kis:cash:usable_usd") or "0")
    # Legacy key (호환용 mirror)
    legacy_cash = float(r.get("kis:live:available_cash") or "0")

    equity_total = float(r.get("ares:equity:total") or "0")
    equity_current = float(r.get("ares:equity:current") or "0")

    # Cash Gate State Machine
    cash_mode = r.get("policy:cash_gate:mode") or "OPEN"
    buy_scale = float(r.get("policy:cash_gate:buy_scale") or "1.0")
    cooldown = r.get("policy:cash_gate:cooldown")
    fail_streak = int(r.get("policy:cash_gate:fail_streak") or "0")

    # Reserved buy details
    reserved_count = int(r.get("kis:cash:reserved_buy_count") or "0")
    reserved_ts = r.get("kis:cash:reserved_buy_ts") or "N/A"

    positions = r.hgetall("kis:broker:positions")
    pos_value = 0.0
    for sym, raw in positions.items():
        try:
            d = json.loads(raw)
            pos_value += d.get("qty", 0) * d.get("current_price", 0)
        except Exception:
            pass

    cash_pct = (usable_cash / equity_total * 100) if equity_total > 0 else 0

    # [P0-FIX] stale equity:current 감지
    equity_drift = abs(equity_total - equity_current)
    equity_stale = equity_drift > 1000 and equity_total > 0

    print("=" * 60)
    print("ARES Cash Gate Monitor v2.1 (Cash Ledger Split + P0 Fix)")
    print("=" * 60)
    print(f"  Equity (total):     ${equity_total:>12,.2f}")
    print(f"  Equity (current):   ${equity_current:>12,.2f}")
    if equity_stale:
        print(f"  ** STALE equity:current detected (drift=${equity_drift:,.2f}) **")
    print(f"  Position Value:     ${pos_value:>12,.2f}")
    print("-" * 60)
    print("  [Cash Ledger Split]")
    print(f"  Broker Cash:        ${broker_cash:>12,.2f}")
    print(f"  Reserved BUY (broker): ${reserved_buy:>12,.2f}  ({reserved_count} orders)")
    print(f"  Reserved BUY (local):  ${reserved_buy_local:>12,.2f}  (executor pending)")
    print(f"  Safety Buffer:         ${safety_buffer:>12,.2f}")
    print(f"  Usable Cash:        ${usable_cash:>12,.2f}  ({cash_pct:.1f}%)")
    print(f"  Legacy Mirror:      ${legacy_cash:>12,.2f}")
    print(f"  Reserved BUY TS:    {reserved_ts}")
    print("-" * 60)
    print("  [Cash Gate State Machine]")
    print(f"  Mode:               {cash_mode}")
    print(f"  Buy Scale:          {buy_scale:.2f}")
    print(f"  Cooldown:           {'ACTIVE' if cooldown else 'OFF'}")
    print(f"  Fail Streak:        {fail_streak}")
    print("-" * 60)

    if cash_mode == "SELL_ONLY":
        print("  CRITICAL: mode=SELL_ONLY — all BUY intents blocked")
    elif cash_mode == "BUY_THROTTLE":
        print(f"  WARNING: mode=BUY_THROTTLE — BUY scaled to {buy_scale:.0%}")
    elif usable_cash < 50:
        print("  CRITICAL: Usable cash below $50 — all BUY intents blocked")
    elif usable_cash < 500:
        print("  WARNING: Usable cash below $500 — limited BUY capacity")
    elif cash_pct < 2:
        print("  WARNING: Cash < 2% of equity — nearly fully invested")
    else:
        print("  OK: Cash position healthy")

    if reserved_buy > broker_cash * 0.5:
        print(f"  WARNING: Reserved BUY ({reserved_buy:.0f}) > 50% of broker cash ({broker_cash:.0f})")
    # Drift detection: usable vs computed
    computed_usable = max(0, broker_cash - reserved_buy - reserved_buy_local - safety_buffer)
    drift = abs(usable_cash - computed_usable)
    if drift > 100 or (broker_cash > 0 and drift / broker_cash > 0.05):
        print(f"  WARNING: LEDGER DRIFT detected: usable={usable_cash:.2f} vs computed={computed_usable:.2f} (drift=${drift:.2f})")
    print("=" * 60)


if __name__ == "__main__":
    main()
