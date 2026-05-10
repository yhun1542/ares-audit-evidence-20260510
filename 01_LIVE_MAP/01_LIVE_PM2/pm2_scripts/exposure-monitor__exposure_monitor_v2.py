#!/usr/bin/env python3
"""
ARES Exposure Monitor v2.0 — First Principles Redesign
=======================================================
근본 원인 분석:
1. v1은 `ares:equity:latest` 키를 읽었으나, 이 키는 존재하지 않음
   → 실제 키는 `ares:equity:snapshot` (equity-calculator.mjs가 30초마다 갱신)
2. v1은 Redis 비밀번호 없이 localhost:6379에 접속 시도
   → Redis는 requirepass가 설정되어 있어 "Authentication required" 에러
3. v1은 PM2에 등록되지 않아 수동 실행 후 프로세스 종료 시 모니터링 중단

구조적 해결책:
1. [키 매핑] `ares:equity:snapshot` (JSON)에서 total, cash, positions_mv 직접 읽기
2. [인증] REDIS_URL 환경변수에서 비밀번호 포함 URL 사용, fallback으로 REDIS_PASSWORD
3. [PM2 등록] ecosystem 설정에 포함하여 자동 재시작
4. [노출도 추이] Redis sorted set에 시계열 저장 → 10분/30분/1시간 추이 분석 가능
5. [체크포인트 연동] ares_auto_checkpoint.sh가 읽을 수 있는 키에 최신 상태 저장

설계 원칙 (일론 머스크식):
- "모니터가 왜 필요한가?" → equity-calculator가 이미 30초마다 snapshot을 갱신
- "그러면 모니터는 뭘 해야 하는가?" → snapshot을 읽어서 추이 분석 + 알림만 담당
- "왜 별도 프로세스인가?" → equity-calculator는 계산만, 모니터는 분석/알림만 (SRP)
"""
import redis
import json
import time
import os
import sys
import logging
from datetime import datetime, timezone, timedelta

# ─── Configuration ───────────────────────────────────────────────
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
# REDIS_URL: 환경변수 우선, fallback으로 /etc/ares/redis.env 파일에서 읽기
def _load_redis_url():
    import os as _os
    url = _os.environ.get("REDIS_URL", "")
    if url and url.startswith("redis"):
        return url
    env_path = "/etc/ares/redis.env"
    if _os.path.exists(env_path):
        with open(env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("REDIS_URL="):
                    return _line.split("=", 1)[1]
    return ""
REDIS_URL = _load_redis_url()

# Patch B: FEASIBILITY_CAP / minimum cash buffer
MIN_CASH_BUFFER_USD = 5000.0
MIN_CASH_BUFFER_PCT = 0.02

# ─── FIX-5: Market Hours Guard (v6 patch) ───────────────────

def compute_feasibility_cap(equity_total, cash):
    """현실 자본 제약 하에 도달 가능한 최대 노출도(%).
    feasibility_cap = (equity - min_cash_buffer) / equity * 100
    min_cash_buffer = max(MIN_CASH_BUFFER_USD, equity * MIN_CASH_BUFFER_PCT)
    """
    try:
        e = float(equity_total or 0)
        c = float(cash or 0)
        if e <= 0:
            return 100.0, 0.0
        buf = max(MIN_CASH_BUFFER_USD, e * MIN_CASH_BUFFER_PCT)
        cap = max(0.0, (e - buf) / e * 100.0)
        cash_pct = (c / e * 100.0) if e > 0 else 0.0
        return round(cap, 2), round(cash_pct, 2)
    except Exception:
        return 100.0, 0.0

def is_us_market_hours():
    """Check if US stock market is currently open (9:30-16:00 ET, Mon-Fri)."""
    from datetime import datetime as _dt, timezone, timedelta
    try:
        now_utc = _dt.now(timezone.utc)
        et_offset = timedelta(hours=-4)  # EDT
        now_et = now_utc + et_offset
        if now_et.weekday() >= 5:
            return False
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= now_et <= market_close
    except Exception:
        return True  # Fail-open
# ─── END FIX-5 ──────────────────────────────────────────────

INTERVAL = 60  # seconds
LOG_FILE = "/home/ubuntu/logs/exposure_monitor.log"

# Redis Keys (읽기)
KEY_EQUITY_SNAPSHOT = "ares:equity:snapshot"       # equity-calculator가 30초마다 갱신
KEY_EQUITY_TOTAL = "ares:equity:total"             # 단순 숫자
KEY_TARGET_EXPOSURE = "champion:target:us_sleeve_gross_exposure"  # P3 SSOT (was: champion:target:gross_exposure deprecated 0.4979)
KEY_SLEEVE_WEIGHTS = "sleeve:v3:final_weights:current"
KEY_OFG_GATE = "ofg:gate"
KEY_OFG_STATUS = "ofg:status"
KEY_EQUITY_POSITIONS = "ares:equity:positions"     # {totalMV, count, ts}

# Redis Keys (쓰기)
KEY_MONITOR_HISTORY = "monitor:exposure:history"   # Stream
KEY_MONITOR_LATEST = "monitor:exposure:latest"     # JSON (체크포인트용)

# === P5_ADDITIVE_LABEL_SPLIT_MARKER ===
# Patch P5: emit execution-basis-aware US sleeve & account decomposition
# Additive only — does not mutate existing record fields.
# Approval: APPROVE_ARES_EXPOSURE_BASIS_US_SLEEVE_C_PATCH:P5
KEY_BROKER_TRUTH_POSITIONS_V2 = "truth:broker:positions:v2"
KEY_BROKER_TRUTH_CASH_V2      = "truth:broker:cash:v2"
KEY_EQUITY_AUTHORITATIVE      = "ares:equity:authoritative"
KEY_EXPOSURE_EXEC_BASIS       = "ares:exposure:execution:basis"
KEY_EXPOSURE_US_SLEEVE_GROSS  = "ares:exposure:us_sleeve:gross"
KEY_EXPOSURE_US_SLEEVE_LATEST = "ares:exposure:us_sleeve:latest"
KEY_CHAMPION_EXEC_GROSS_TGT   = "champion:target:execution_gross_exposure"
P5_EXEC_BASIS = "US_SLEEVE"
P5_EXEC_TARGET_DEFAULT = 0.685
P5_EXEC_SOFT_CAP_DEFAULT = 0.75


def _p5_safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def _p5_compute_us_sleeve_fields(r, equity):
    """Read broker truth and authoritative equity to derive US sleeve / account split.
    All keys read are existing keys; nothing is mutated for ORDER_SIZING.
    Emits additional Redis SETs (additive) for execution basis."""
    out = {
        "execution_gross_exposure_basis": P5_EXEC_BASIS,
        "execution_gross_exposure_target": P5_EXEC_TARGET_DEFAULT,
        "execution_gross_exposure_soft_cap": P5_EXEC_SOFT_CAP_DEFAULT,
        "deprecated_for_order_sizing": True,
        "positions_mv_usd_current_meaning": "account_non_cash_mv_usd",
        "p5_marker": "P5_ADDITIVE_LABEL_SPLIT_MARKER",
    }
    try:
        # Source 1: truth:broker:positions:v2 — fresh US sleeve (positions_mv_usd_from_rows)
        raw_btv2 = r.get(KEY_BROKER_TRUTH_POSITIONS_V2)
        us_eq_mv = 0.0
        if raw_btv2:
            d = json.loads(raw_btv2)
            us_eq_mv = _p5_safe_float(d.get("positions_mv_usd_from_rows"), 0.0)
            if us_eq_mv == 0.0:
                # Fallback: sum positions[].market_value
                positions = d.get("positions") or []
                us_eq_mv = sum(_p5_safe_float(p.get("market_value"), 0.0) for p in positions)
        out["us_equity_mv_usd"] = round(us_eq_mv, 2)

        # Source 2: truth:broker:cash:v2 — usd_cash
        raw_btc2 = r.get(KEY_BROKER_TRUTH_CASH_V2)
        usd_cash = 0.0
        if raw_btc2:
            c = json.loads(raw_btc2)
            usd_cash = _p5_safe_float(c.get("cash_total_usd"), 0.0)
        out["usd_cash"] = round(usd_cash, 2)

        # Derived US sleeve
        us_nav = us_eq_mv + usd_cash
        out["us_sleeve_nav_usd"] = round(us_nav, 2)
        if us_nav > 0:
            out["us_sleeve_gross_exposure"] = round(us_eq_mv / us_nav, 4)
        else:
            out["us_sleeve_gross_exposure"] = 0.0
        out["execution_gross_exposure"] = out["us_sleeve_gross_exposure"]

        # Source 3: ares:equity:authoritative — account_total / non_cash
        raw_auth = r.get(KEY_EQUITY_AUTHORITATIVE)
        if raw_auth:
            a = json.loads(raw_auth)
            total = _p5_safe_float(a.get("total"), 0.0)
            cash_total = _p5_safe_float(a.get("cash_total_usd") or a.get("cash") or 0.0)
            account_non_cash = _p5_safe_float(a.get("positions_mv_usd"), 0.0)
            out["account_total_usd"] = round(total, 2)
            out["account_non_cash_mv_usd"] = round(account_non_cash, 2)
            if total > 0:
                out["account_non_cash_risk_pct"] = round(account_non_cash / total, 4)
                out["total_account_us_equity_pct"] = round(us_eq_mv / total, 4)
                kr_assets = max(total - cash_total - us_eq_mv, 0.0)
                out["kr_assets_mv_usd"] = round(kr_assets, 2)
            else:
                out["account_non_cash_risk_pct"] = 0.0
                out["total_account_us_equity_pct"] = 0.0
                out["kr_assets_mv_usd"] = 0.0

        # Champion execution target (additive key)
        try:
            ct = r.get(KEY_CHAMPION_EXEC_GROSS_TGT)
            out["champion_execution_gross_target"] = _p5_safe_float(ct, P5_EXEC_TARGET_DEFAULT) if ct else P5_EXEC_TARGET_DEFAULT
        except Exception:
            out["champion_execution_gross_target"] = P5_EXEC_TARGET_DEFAULT

        # Emit additive Redis keys (DOES NOT TOUCH existing exposure_monitor keys)
        try:
            r.set(KEY_EXPOSURE_EXEC_BASIS, P5_EXEC_BASIS)
            r.set(KEY_EXPOSURE_US_SLEEVE_GROSS, str(out.get("us_sleeve_gross_exposure", 0.0)), ex=300)
            r.set(KEY_EXPOSURE_US_SLEEVE_LATEST, json.dumps(out), ex=600)
        except Exception:
            pass
    except Exception as _e:
        out["p5_error"] = str(_e)[:200]
    return out
# === END P5_ADDITIVE_LABEL_SPLIT_MARKER ===

KEY_MONITOR_TREND = "monitor:exposure:trend"       # Sorted Set (시계열)
MAX_STREAM_LEN = 10000
MAX_TREND_LEN = 1440  # 24시간 × 60분

# ─── Logging Setup ───────────────────────────────────────────────
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("exposure_monitor_v2")
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(console)

# ─── Redis Connection ────────────────────────────────────────────
def connect_redis():
    """Redis 연결 (비밀번호 포함)"""
    try:
        r = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        r.ping()
        log.info("Redis connected successfully (authenticated)")
        return r
    except Exception as e:
        log.error(f"Redis connection failed: {e}")
        raise

# ─── Data Collection Functions ───────────────────────────────────
def get_equity_data(r):
    """equity-calculator의 canonical snapshot에서 데이터 읽기"""
    try:
        raw = r.get(KEY_EQUITY_SNAPSHOT)
        if raw:
            d = json.loads(raw)
            total = float(d.get("total", 0))
            cash = float(d.get("cash", 0))
            positions_mv = float(d.get("positions_mv", 0))
            exposure_pct = (positions_mv / total * 100) if total > 0 else 0
            return {
                "ok": d.get("ok", False),
                "total": total,
                "cash": cash,
                "positions_mv": positions_mv,
                "exposure_pct": round(exposure_pct, 2),
                "source": d.get("source", "unknown"),
                "ts": d.get("ts", ""),
                "cash_status": d.get("cash_status", "unknown")
            }
    except Exception as e:
        log.warning(f"Equity snapshot parse error: {e}")
    
    # Fallback: 개별 키에서 조합
    try:
        total = float(r.get(KEY_EQUITY_TOTAL) or 0)
        pos_raw = r.get(KEY_EQUITY_POSITIONS)
        if pos_raw and total > 0:
            pos = json.loads(pos_raw)
            positions_mv = float(pos.get("totalMV", 0))
            exposure_pct = (positions_mv / total * 100) if total > 0 else 0
            return {
                "ok": True,
                "total": total,
                "cash": total - positions_mv,
                "positions_mv": positions_mv,
                "exposure_pct": round(exposure_pct, 2),
                "source": "fallback:individual_keys",
                "ts": pos.get("ts", ""),
                "cash_status": "fallback"
            }
    except Exception as e:
        log.warning(f"Equity fallback parse error: {e}")
    
    return None

def get_target_exposure(r):
    """목표 노출도 (%) 가져오기"""
    try:
        raw = r.get(KEY_TARGET_EXPOSURE)
        if raw:
            return round(float(raw) * 100, 2)
    except Exception:
        pass
    return None

def get_sleeve_data(r):
    """슬리브 배분 데이터"""
    try:
        raw = r.get(KEY_SLEEVE_WEIGHTS)
        if raw:
            d = json.loads(raw)
            mix = d.get("sleeve_mix", {})
            weights = d.get("weights", {})
            details = d.get("sleeve_details", {})
            return {
                "sleeve_mix": mix,
                "n_symbols": len(weights),
                "total_weight": round(sum(weights.values()), 4),
                "flow_event_symbols": len(details.get("flow_event", {})),
                "defensive_symbols": len(details.get("defensive", {})),
                "core_symbols": len(details.get("core", {}))
            }
    except Exception as e:
        log.warning(f"Sleeve parse error: {e}")
    return None

def get_ofg_state(r):
    """OFG 상태"""
    try:
        gate = r.get(KEY_OFG_GATE) or "UNKNOWN"
        status_raw = r.get(KEY_OFG_STATUS)
        status = json.loads(status_raw) if status_raw else {}
        return {
            "gate": gate,
            "orders_this_session": status.get("orders_this_session", 0),
            "vix_regime": status.get("vix_regime", "UNKNOWN"),
            "current_vix": status.get("current_vix", 0),
            "session_dd": status.get("current_session_dd", 0),
            "rate_session_limit": status.get("effective_thresholds", {}).get("rate_session", 0)
        }
    except Exception as e:
        log.warning(f"OFG state error: {e}")
    return {"gate": "ERROR"}

# ─── Trend Analysis ─────────────────────────────────────────────
def analyze_trend(r, current_exposure):
    """10분/30분/1시간 노출도 추이 분석"""
    now = time.time()
    trends = {}
    
    for label, window_sec in [("10min", 600), ("30min", 1800), ("60min", 3600)]:
        try:
            cutoff = now - window_sec
            # Sorted set에서 시간 범위 내 데이터 가져오기
            entries = r.zrangebyscore(KEY_MONITOR_TREND, cutoff, now, withscores=True)
            if entries and len(entries) >= 2:
                first_val = float(entries[0][0])
                last_val = float(entries[-1][0])
                delta = last_val - first_val
                rate_per_min = delta / (window_sec / 60) if window_sec > 0 else 0
                trends[label] = {
                    "start": round(first_val, 2),
                    "end": round(last_val, 2),
                    "delta": round(delta, 2),
                    "rate_per_min": round(rate_per_min, 4),
                    "samples": len(entries)
                }
            else:
                trends[label] = {"start": None, "end": None, "delta": 0, "rate_per_min": 0, "samples": 0}
        except Exception as e:
            log.warning(f"Trend analysis error ({label}): {e}")
            trends[label] = {"error": str(e)}
    
    return trends

def estimate_target_arrival(current_exposure, target_exposure, trends):
    """목표 도달 예상 시간 계산"""
    if not target_exposure or current_exposure >= target_exposure:
        return {"status": "REACHED" if current_exposure >= target_exposure else "NO_TARGET"}
    
    gap = target_exposure - current_exposure
    
    # 가장 최근 추이(10분)의 상승 속도 사용
    trend_10m = trends.get("10min", {})
    rate = trend_10m.get("rate_per_min", 0)
    
    if rate <= 0:
        return {
            "status": "STALLED",
            "gap_pct": round(gap, 2),
            "rate_per_min": rate,
            "message": "노출도 상승 중단 또는 하락 중"
        }
    
    minutes_to_target = gap / rate
    hours = minutes_to_target / 60
    
    return {
        "status": "RISING",
        "gap_pct": round(gap, 2),
        "rate_per_min": round(rate, 4),
        "est_minutes": round(minutes_to_target, 1),
        "est_hours": round(hours, 2),
        "message": f"현재 속도({rate:.4f}%/분)로 약 {hours:.1f}시간 후 목표 도달 예상"
    }

# ─── Main Loop ───────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("ARES Exposure Monitor v2.0 — First Principles Redesign")
    log.info(f"Interval: {INTERVAL}s | Log: {LOG_FILE}")
    log.info(f"Equity source: {KEY_EQUITY_SNAPSHOT}")
    log.info(f"Target source: {KEY_TARGET_EXPOSURE}")
    log.info("=" * 60)
    
    try:
        r = connect_redis()
    except Exception as e:
        log.error(f"Failed to connect to Redis: {e}")
        raise SystemExit("[P0-C] Fatal error")
    
    cycle = 0
    baseline_exposure = None
    
    while True:
        try:
            cycle += 1
            now_ts = time.time()
            now_iso = datetime.now(timezone.utc).isoformat()
            
            # 데이터 수집
            equity = get_equity_data(r)
            target_exp = get_target_exposure(r)
            sleeve = get_sleeve_data(r)
            ofg = get_ofg_state(r)
            
            if equity and equity.get("ok"):
                exp = equity["exposure_pct"]
                # Patch B feasibility compute
                feas_cap, cash_pct = compute_feasibility_cap(equity['total'], equity['cash'])
                cap_gap = round(feas_cap - exp, 2)
                feas_status = ('AT_CAP' if cap_gap <= 1.0 else 'BELOW_CAP')
                
                # 베이스라인 설정
                if baseline_exposure is None:
                    baseline_exposure = exp
                
                delta_from_baseline = round(exp - baseline_exposure, 2)
                target_gap = round(target_exp - exp, 2) if target_exp else None
                
                # 추이 데이터 저장 (sorted set: score=timestamp, member=exposure)
                try:
                    r.zadd(KEY_MONITOR_TREND, {str(exp): now_ts})
                    # 오래된 데이터 정리 (24시간 이전)
                    cutoff = now_ts - 86400
                    r.zremrangebyscore(KEY_MONITOR_TREND, 0, cutoff)
                except Exception:
                    pass
                
                # 추이 분석
                trends = analyze_trend(r, exp)
                arrival = estimate_target_arrival(exp, target_exp, trends)
                
                # 종합 레코드
                record = {
                    "ts": now_iso,
                    "cycle": cycle,
                    "exposure_pct": exp,
                    "baseline": baseline_exposure,
                    "delta_from_baseline": delta_from_baseline,
                    "target_exposure": target_exp,
                    "target_gap": target_gap,
                    "total_equity": equity["total"],
                    "cash": equity["cash"],
                    "positions_mv": equity["positions_mv"],
                    "positions_source": equity["source"],
                    "ofg_gate": ofg.get("gate", "?"),
                    "ofg_orders_session": ofg.get("orders_this_session", 0),
                    "ofg_vix_regime": ofg.get("vix_regime", "?"),
                    "ofg_vix": ofg.get("current_vix", 0),
                    "ofg_session_dd": ofg.get("session_dd", 0),
                    "sleeve_weight": sleeve.get("total_weight") if sleeve else None,
                    "sleeve_n_symbols": sleeve.get("n_symbols") if sleeve else None,
                    "flow_event_symbols": sleeve.get("flow_event_symbols") if sleeve else None,
                    "trend_10min": trends.get("10min", {}),
                    "trend_30min": trends.get("30min", {}),
                    "trend_60min": trends.get("60min", {}),
                    "target_arrival": arrival,
                    # === P5_ADDITIVE_LABEL_SPLIT_MARKER (do not remove) ===
                    # Patch P5 — execution_gross_exposure_basis = US_SLEEVE
                    # Approval: APPROVE_ARES_EXPOSURE_BASIS_US_SLEEVE_C_PATCH:P5
                    # Additive only: existing fields unchanged
                    **_p5_compute_us_sleeve_fields(r, equity),
                }
                
                # Redis에 최신 상태 저장 (체크포인트용)
                try:
                    r.set(KEY_MONITOR_LATEST, json.dumps(record), ex=120)  # TTL=120s (2x interval)
                    # FIX-62: Auto-sync ares:exposure:current from calculated exposure
                    r.set("ares:exposure:current", str(round(exp, 2)), ex=300)
                except Exception:
                    pass
                
                # Stream에 히스토리 저장
                try:
                    stream_record = {
                        "ts": now_iso,
                        "exposure_pct": str(exp),
                        "target_gap": str(target_gap) if target_gap else "",
                        "ofg_gate": ofg.get("gate", "?"),
                        "equity_total": str(equity["total"]),
                        "positions_mv": str(equity["positions_mv"]),
                        "feasibility_cap": str(feas_cap),
                        "cap_gap": str(cap_gap),
                        "feas_status": feas_status
                    }
                    r.xadd(KEY_MONITOR_HISTORY, stream_record, maxlen=MAX_STREAM_LEN)
                except Exception:
                    pass
                
                # 로그 출력
                trend_10m = trends.get("10min", {})
                trend_30m = trends.get("30min", {})
                trend_60m = trends.get("60min", {})
                
                log.info(
                    f"EXPOSURE_TICK #{cycle}: "
                    f"exp={exp:.1f}% (delta={delta_from_baseline:+.1f}%) "
                    f"target={target_exp}% gap={target_gap}%p "
                    f"| equity=${equity['total']:,.0f} pos_mv=${equity['positions_mv']:,.0f} "
                    f"| OFG={ofg.get('gate','?')} orders={ofg.get('orders_this_session',0)} "
                    f"| 10m={trend_10m.get('delta',0):+.2f}% 30m={trend_30m.get('delta',0):+.2f}% 60m={trend_60m.get('delta',0):+.2f}%" + f" | cap={feas_cap:.1f}% cap_gap={cap_gap:+.2f}%p {feas_status}"
                )
                
                # 알림 조건
                if exp < 30:
                    log.warning(f"CRITICAL: Exposure at {exp}% — critically low!")
                elif exp < 50:
                    log.warning(f"WARNING: Exposure at {exp}% — below 50%")
                
                if ofg.get("gate") == "THROTTLE":
                    log.warning(f"OFG THROTTLE: orders={ofg.get('orders_this_session',0)}/{ofg.get('rate_session_limit',0)}")
                elif ofg.get("gate") == "HALT":
                    log.warning(f"OFG HALT: session_dd={ofg.get('session_dd',0):.4f}")
                
                # [ARES_PATCH_E5_20260508] Strengthen STALLED detection: also trigger when 60min delta == 0 with significant gap
                _e5_60m_delta = trend_60m.get("delta", 0) or 0
                _e5_is_60m_stalled = (abs(_e5_60m_delta) < 0.05 and target_gap is not None and abs(target_gap) > 1.0)
                if arrival.get("status") == "STALLED" or _e5_is_60m_stalled:
                    if is_us_market_hours():
                        log.warning(f"TARGET STALLED: gap={arrival.get('gap_pct', target_gap)}%p, rate={arrival.get('rate_per_min',0)}%/min, 60m_delta={_e5_60m_delta:+.3f}%p")
                        # Emit KPI alert + Redis flag (consumed by monitoring/dashboard)
                        try:
                            r.set("monitor:exposure:stalled", json.dumps({
                                "ts": now_iso,
                                "gap_pct": float(target_gap or 0),
                                "trend_60m_delta": float(_e5_60m_delta),
                                "trend_10m_rate": float(arrival.get("rate_per_min", 0) or 0),
                                "current_exposure": float(exp),
                                "target_exposure": float(target_exp or 0),
                            }), ex=300)
                            r.xadd("kpi:v1:alerts", {
                                "type": "EXPOSURE_STALLED_60M",
                                "gap_pct": str(target_gap),
                                "trend_60m_delta": str(_e5_60m_delta),
                                "current_exposure": str(exp),
                                "target_exposure": str(target_exp or 0),
                                "ts": now_iso,
                            }, maxlen=10000, approximate=True)
                        except Exception as _e5_err:
                            log.warning(f"[ARES_PATCH_E5] alert publish failed: {_e5_err}")
                    else:
                        log.info(f"TARGET STALLED (market closed, normal): gap={arrival.get('gap_pct',0)}%p")
                elif arrival.get("status") == "RISING":
                    log.info(f"TARGET ETA: {arrival.get('message', '')}")
                    
            else:
                log.warning(f"EXPOSURE_TICK #{cycle}: No equity data available (snapshot key: {KEY_EQUITY_SNAPSHOT})")
                
        except redis.ConnectionError as e:
            log.error(f"Redis connection lost: {e}. Reconnecting...")
            try:
                r = connect_redis()
            except Exception:
                log.error("Reconnection failed. Retrying in 60s...")
        except Exception as e:
            log.error(f"Monitor error: {e}")
        
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
