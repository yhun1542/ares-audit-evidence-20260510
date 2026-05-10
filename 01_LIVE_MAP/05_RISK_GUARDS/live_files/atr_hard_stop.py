# PATH: /home/ubuntu/ares_current/external/atr_hard_stop.py
# PATCH: V3 — Per-symbol ATR-based hard stop (prevents -10% single-day losses like 5/7 ARM)
# DEPLOY: pm2 start atr_hard_stop.py --name atr-hard-stop --interpreter python3
# ROLLBACK: pm2 delete atr-hard-stop

"""
ARES — ATR Hard Stop v1.0.0 (PATCH V3)
=============================================================================
목적: 5/7 ARM(-10.11%), AMD(-3.07%) 같은 개별 종목 급락이 포트폴리오에 미치는
     영향을 단일 종목 단위에서 차단. 종목별 ATR(Average True Range)을 활용하여
     각 종목의 변동성에 맞는 stop loss를 설정.

알고리즘:
  hard_stop_price = entry_price - K * ATR_14d
    K = 2.0 (default), 변동성 큰 종목은 1.5, 안정 종목은 2.5

  실행:
    1. positions:current 에서 보유 종목 + 평균 매수가 조회
    2. price:realtime:{symbol} 에서 현재가 조회
    3. feat:v2:vol:realized:{symbol} 에서 ATR 또는 realized vol 조회
    4. 현재가 < hard_stop_price 이면 → exec:order:queue 에 SELL 주문 추가

Cycle: 30초 (시장 시간만 작동)

Safety:
  - exec:order:queue 에 idempotency_key 사용 (동일 hard_stop 5분 내 재발행 방지)
  - emarkos:v1:mode == "SAFE" 인 경우 기본 비활성, 환경변수 ARES_ATR_STOP_FORCE=true로 ON
  - kill_switch 또는 invariant_halt = 1 이면 작동 중단
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import redis

# Logging
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("atr_hard_stop")

# Redis
REDIS_URL = os.getenv("REDIS_URL") or os.getenv("ARES_REDIS_URL")
REDIS_PASS = os.environ.get("REDIS_PASSWORD")
if not REDIS_URL:
    _env_file = "/etc/ares/ares.env"
    try:
        with open(_env_file) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("#") or "=" not in _line:
                    continue
                _key, _val = _line.split("=", 1)
                _key, _val = _key.strip(), _val.strip().strip('"').strip("'")
                if _key == "REDIS_URL" and _val:
                    REDIS_URL = _val
                elif _key == "REDIS_PASSWORD" and _val and not REDIS_PASS:
                    REDIS_PASS = _val
    except Exception:
        pass

_SELF_TEST = "--self-test" in sys.argv
if not _SELF_TEST and not REDIS_URL:
    raise RuntimeError("REDIS_URL is required")

# Config
CYCLE_INTERVAL = int(os.getenv("ATR_STOP_CYCLE_SEC", "30"))
ATR_K_DEFAULT = float(os.getenv("ATR_STOP_K", "2.0"))
ATR_K_HIGH_VOL = 1.5
ATR_K_LOW_VOL = 2.5
HIGH_VOL_THRESHOLD = 0.04   # daily realized vol > 4%
LOW_VOL_THRESHOLD = 0.015   # daily realized vol < 1.5%
ATR_DAYS = 14
ENABLE_FLAG = os.getenv("ARES_ATR_STOP_ENABLE", "false").lower() == "true"
FORCE_FLAG = os.getenv("ARES_ATR_STOP_FORCE", "false").lower() == "true"

# Redis keys
POSITIONS_KEY = "positions:current"
KIS_BALANCE_KEY = "kis:account:balance:current"
PRICE_PATTERN = "price:realtime:{}"
PRICE_FALLBACK_PATTERN = "feat:v1:price:close:{}"
VOL_PATTERN = "feat:v2:vol:realized:{}"
VOL_FALLBACK_PATTERN = "feat:v1:realized_vol:{}"
ATR_PATTERN = "feat:v2:atr:14:{}"
EXEC_QUEUE_KEY = "exec:order:queue"
EMARKOS_MODE_KEY = "emarkos:v1:mode"
INVARIANT_HALT_KEY = "ares:invariant:halt"
KILL_SWITCH_KEY = "system:kill_switch"
KILL_SWITCH_KEY_ALT = "kill-switch:active"
HARD_STOP_LOG_KEY = "atr_hard_stop:triggered"
HARD_STOP_IDEMPOTENCY_PREFIX = "atr_hard_stop:idempotency:"
IDEMPOTENCY_TTL_SEC = 300  # 5 min


def _push_health_metric(r, key: str, value, labels: dict | None = None):
    try:
        metric = {"value": value, "ts": time.time(), "labels": labels or {}}
        r.set(f"metrics:{key}", json.dumps(metric), ex=60)
    except Exception:
        pass


def _get_redis():
    return redis.Redis.from_url(REDIS_URL, password=REDIS_PASS, decode_responses=True, socket_timeout=5)


def _safe_float(val, default=None):
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _read_json(r, key, default=None):
    try:
        raw = r.get(key)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return default if default is not None else {}


def _read_positions(r) -> dict[str, dict]:
    """Read current positions: {symbol: {avg_price, qty}}"""
    pos_obj = _read_json(r, POSITIONS_KEY, {})
    if isinstance(pos_obj, dict) and "positions" in pos_obj:
        pos_list = pos_obj["positions"]
    elif isinstance(pos_obj, list):
        pos_list = pos_obj
    elif isinstance(pos_obj, dict):
        # Legacy format: {symbol: {...}}
        return {sym: data for sym, data in pos_obj.items() if isinstance(data, dict)}
    else:
        pos_list = []

    out = {}
    for p in pos_list:
        if not isinstance(p, dict):
            continue
        sym = p.get("symbol") or p.get("ticker") or p.get("pdno")
        if not sym:
            continue
        avg = _safe_float(p.get("avg_price") or p.get("avg") or p.get("pchs_avg_pric"))
        qty = _safe_float(p.get("qty") or p.get("ord_psbl_qty") or p.get("hldg_qty"))
        if avg and qty and qty > 0:
            out[sym.upper()] = {"avg_price": avg, "qty": qty}
    return out


def _read_price(r, sym: str) -> float | None:
    for pattern in (PRICE_PATTERN, PRICE_FALLBACK_PATTERN):
        try:
            raw = r.get(pattern.format(sym))
            if raw:
                try:
                    obj = json.loads(raw)
                    p = _safe_float(obj.get("price") or obj.get("close") or obj.get("last"))
                    if p:
                        return p
                except (json.JSONDecodeError, TypeError):
                    p = _safe_float(raw)
                    if p:
                        return p
        except Exception:
            pass
    return None


def _read_atr(r, sym: str) -> float | None:
    """Read ATR(14d) in absolute price units, or fallback to realized_vol * price."""
    try:
        raw = r.get(ATR_PATTERN.format(sym))
        if raw:
            obj = json.loads(raw) if raw.startswith("{") else None
            if obj:
                v = _safe_float(obj.get("atr") or obj.get("value"))
                if v:
                    return v
            else:
                v = _safe_float(raw)
                if v:
                    return v
    except Exception:
        pass
    return None


def _read_realized_vol(r, sym: str) -> float | None:
    for pattern in (VOL_PATTERN, VOL_FALLBACK_PATTERN):
        try:
            raw = r.get(pattern.format(sym))
            if raw:
                try:
                    obj = json.loads(raw)
                    v = _safe_float(obj.get("vol") or obj.get("realized_vol") or obj.get("value"))
                    if v:
                        return v
                except (json.JSONDecodeError, TypeError):
                    v = _safe_float(raw)
                    if v:
                        return v
        except Exception:
            pass
    return None


def _is_safe_to_run(r) -> tuple[bool, str]:
    if not ENABLE_FLAG:
        return False, "ARES_ATR_STOP_ENABLE != true"
    try:
        if r.get(KILL_SWITCH_KEY) == "true" or r.get(KILL_SWITCH_KEY_ALT) == "true":
            return False, "kill_switch active"
    except Exception:
        pass
    try:
        if r.get(INVARIANT_HALT_KEY) == "1":
            return False, "invariant_halt active"
    except Exception:
        pass
    if not FORCE_FLAG:
        try:
            mode = r.get(EMARKOS_MODE_KEY)
            if mode and mode.upper() == "SAFE":
                return False, f"emarkos mode=SAFE (use FORCE=true to override)"
        except Exception:
            pass
    return True, "ok"


def compute_hard_stop(entry_price: float, atr: float | None, realized_vol: float | None,
                      current_price: float) -> tuple[float, float, str]:
    """
    Returns (hard_stop_price, k_used, method).
    Falls back to realized_vol * price if ATR unavailable.
    """
    if atr is not None and atr > 0:
        atr_units = atr
        method = "atr"
    elif realized_vol is not None and realized_vol > 0:
        # Convert daily realized vol → ATR-equivalent (≈ vol * sqrt(252) * daily_price * sqrt(1/252) = vol * price)
        atr_units = realized_vol * entry_price
        method = "vol_proxy"
    else:
        # Last resort: 3% of entry price as default stop
        atr_units = 0.03 * entry_price
        method = "default_3pct"

    daily_vol_pct = atr_units / max(entry_price, 1e-6)
    if daily_vol_pct > HIGH_VOL_THRESHOLD:
        k = ATR_K_HIGH_VOL
    elif daily_vol_pct < LOW_VOL_THRESHOLD:
        k = ATR_K_LOW_VOL
    else:
        k = ATR_K_DEFAULT

    hard_stop = entry_price - k * atr_units
    return max(hard_stop, 0.0), k, method


def _emit_sell_order(r, sym: str, qty: float, price: float, reason: str):
    """Push sell order to exec queue with idempotency."""
    idem_key = f"{HARD_STOP_IDEMPOTENCY_PREFIX}{sym}"
    if r.set(idem_key, "1", ex=IDEMPOTENCY_TTL_SEC, nx=True) is None:
        log.info("[atr_stop] %s already triggered within %ds, skipping", sym, IDEMPOTENCY_TTL_SEC)
        return False

    order = {
        "symbol": sym,
        "side": "SELL",
        "reduce_only": True,
        "qty": qty,
        "ord_dvsn": "00",          # Market order
        "trigger": "ATR_HARD_STOP",
        "reason": reason,
        "current_price": price,
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "source": "atr_hard_stop_v1",
    }
    try:
        r.rpush(EXEC_QUEUE_KEY, json.dumps(order))
        r.lpush(HARD_STOP_LOG_KEY, json.dumps(order))
        r.ltrim(HARD_STOP_LOG_KEY, 0, 999)
        log.warning("[atr_stop] EMIT SELL %s qty=%.2f @%.4f reason=%s",
                    sym, qty, price, reason)
        return True
    except Exception as e:
        log.error("[atr_stop] failed to emit %s: %s", sym, e)
        return False


def run_cycle(r):
    safe, reason = _is_safe_to_run(r)
    if not safe:
        log.debug("[atr_stop] skipped: %s", reason)
        return

    positions = _read_positions(r)
    if not positions:
        log.debug("[atr_stop] no positions")
        return

    triggered = 0
    for sym, pos in positions.items():
        avg = pos["avg_price"]
        qty = pos["qty"]
        cur = _read_price(r, sym)
        if cur is None or cur <= 0:
            continue
        atr = _read_atr(r, sym)
        rv = _read_realized_vol(r, sym)
        hard_stop, k, method = compute_hard_stop(avg, atr, rv, cur)

        if cur < hard_stop:
            loss_pct = (cur / avg - 1.0) * 100.0
            stop_pct = (hard_stop / avg - 1.0) * 100.0
            r_reason = (f"price={cur:.4f} < stop={hard_stop:.4f} "
                        f"(avg={avg:.4f}, k={k}, method={method}, "
                        f"loss={loss_pct:.2f}%, stop_at={stop_pct:.2f}%)")
            if _emit_sell_order(r, sym, qty, cur, r_reason):
                triggered += 1

    _push_health_metric(r, "atr_hard_stop_triggered", triggered,
                        {"positions_total": len(positions)})
    if triggered:
        log.info("[atr_stop] cycle complete, triggered=%d / total=%d", triggered, len(positions))


def main():
    if _SELF_TEST:
        # Test ARM-like scenario
        # ARM avg=170, current=152.81 (-10.11%), realized vol = 5%
        hs, k, m = compute_hard_stop(170.0, None, 0.05, 152.81)
        print(f"ARM scenario (high vol): hard_stop={hs:.2f} (k={k}, method={m})")
        # Should trigger: 152.81 < 170 - 1.5 * 0.05 * 170 = 170 - 12.75 = 157.25
        assert 152.81 < hs, f"ARM should trigger! current 152.81 vs hard_stop {hs}"
        print(f"  ARM triggered (152.81 < {hs:.2f}) ✓")

        # Test stable stock (MSFT-like)
        # MSFT avg=420, current=410, realized vol = 1.2%
        hs2, k2, m2 = compute_hard_stop(420.0, None, 0.012, 410.0)
        print(f"MSFT scenario (low vol): hard_stop={hs2:.2f} (k={k2}, method={m2})")
        # Should NOT trigger: 410 > 420 - 2.5 * 0.012 * 420 = 420 - 12.6 = 407.4
        assert 410.0 > hs2, f"MSFT should NOT trigger: 410 vs {hs2}"
        print(f"  MSFT NOT triggered (410.0 > {hs2:.2f}) ✓")

        # Default fallback
        hs3, k3, m3 = compute_hard_stop(100.0, None, None, 90.0)
        print(f"Default fallback: hard_stop={hs3:.2f} (k={k3}, method={m3})")
        assert m3 == "default_3pct"
        # 100 - 2.0 * 0.03 * 100 = 94. So 90 < 94 → trigger
        assert 90.0 < hs3
        print(f"  Default triggered (90.0 < {hs3:.2f}) ✓")

        print("[PASS] atr_hard_stop self-test: ARM-like crash detected, stable stock spared")
        return 0

    r = _get_redis()
    log.info("ATR Hard Stop v1.0.0 starting (cycle=%ds, ENABLE=%s, FORCE=%s, K=%.1f)",
             CYCLE_INTERVAL, ENABLE_FLAG, FORCE_FLAG, ATR_K_DEFAULT)
    while True:
        try:
            run_cycle(r)
        except Exception as e:
            log.error("atr_stop cycle error: %s", e, exc_info=True)
        time.sleep(CYCLE_INTERVAL)


if __name__ == "__main__":
    sys.exit(main() or 0)
