#!/usr/bin/env python3
"""
ARES Ortex Data Collector v3.1 — 크레딧 소진 시 다음 날까지 실행 유보
=================================================
v3.0 → v3.1 변경 사항 (2026-03-30):
  1. [NEW] 크레딧 소진 시 Redis 플래그 설정 (ortex:credits:exhausted:{date})
  2. [NEW] 다음 실행 시 플래그 확인 → 소진된 경우 즉시 종료 (불필요 재시작 방지)
  3. [NEW] 플래그 TTL은 다음 날 UTC 00:00 자동 만료
  4. [NEW] 중간 크레딧 소진 시에도 플래그 설정

v2 → v3 변경 사항:
  1. premium:ortex:{symbol} 저장을 hash 타입(hset)으로 통일
     → realtime_streaming_server, advanced-signal-integrator 등 소비자 호환
  2. 기존 string 타입 키를 자동 감지하여 hash로 마이그레이션
  3. 크레딧 소진 시 ares:ortex:latest는 기존 캐시 유지 (TTL 없음)
  4. SDK 연결 실패 시 3회 재시도 + 지수 백오프
  5. 심볼별 독립 실패 처리 — 1개 실패해도 나머지 계속 수집
  6. 수집 결과 healthcheck 키 기록 → watchdog 연동

Redis 키 구조 (v3 확정):
  ares:ortex:latest          → string (JSON) — alpha combiner 소비
  premium:ortex:{symbol}     → hash {data: JSON, updated_at: ISO} — streaming server 소비
  premium:ortex:last_updated → string (ISO timestamp)
  ortex:collector:status     → string (JSON) — 모니터링
  ortex:credits:daily:{date} → string (float) — 크레딧 추적

스케줄: PM2 cron — 평일 2시간 간격 (0 */2 * * 1-5)
크레딧: ~4.4 credits/symbol × 47 symbols = ~207 credits/cycle
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import redis

# [PATCH H-6] dotenv 로드 추가
try:
    from dotenv import load_dotenv
    load_dotenv('/home/ubuntu/ares_releases/2026-02-17/.env')
    load_dotenv('/home/ubuntu/.env')
except ImportError:
    pass
import os as _os
# Redis 환경변수 기본값 설정
if 'REDIS_URL' not in os.environ:
    os.environ['REDIS_URL'] = 'redis://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@localhost:6379'
if 'REDIS_PASSWORD' not in os.environ:
    os.environ['REDIS_PASSWORD'] = 'Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U'


# ─── 로깅 ────────────────────────────────────────────────────
LOG_DIR = "/home/ubuntu/logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{LOG_DIR}/ortex_collector_v3.log", mode="a"),
    ],
)
log = logging.getLogger("ortex_v3")

# ─── 환경변수 ────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@127.0.0.1:6379/0")
ORTEX_API_KEY = os.getenv("ORTEX_API_KEY", "LmjaBx5P.viIxynYwvQThwbgVc6oPg6uS1ESOX3Ia")
DAILY_CREDIT_LIMIT = int(os.getenv("ORTEX_DAILY_CREDIT_LIMIT", "5000"))
INTER_SYMBOL_DELAY = float(os.getenv("ORTEX_INTER_SYMBOL_DELAY", "0.5"))
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # 초 (지수 백오프 기본값)

# ─── 심볼 → 거래소 매핑 ──────────────────────────────────────
SYMBOL_EXCHANGE_MAP: Dict[str, str] = {
    # Nasdaq
    "AAPL": "Nasdaq", "ADBE": "Nasdaq", "AMD": "Nasdaq", "AMGN": "Nasdaq",
    "AMZN": "Nasdaq", "AVGO": "Nasdaq", "COST": "Nasdaq", "CRM": "Nasdaq",
    "CSCO": "Nasdaq", "GOOG": "Nasdaq", "GOOGL": "Nasdaq", "HON": "Nasdaq",
    "INTC": "Nasdaq", "INTU": "Nasdaq", "META": "Nasdaq", "MCD": "Nasdaq",
    "MSFT": "Nasdaq", "NFLX": "Nasdaq", "NVDA": "Nasdaq", "PEP": "Nasdaq",
    "TSLA": "Nasdaq", "QQQ": "Nasdaq", "GILD": "Nasdaq", "PYPL": "Nasdaq",
    "SBUX": "Nasdaq", "QCOM": "Nasdaq", "TXN": "Nasdaq", "AMAT": "Nasdaq",
    "LRCX": "Nasdaq", "KLAC": "Nasdaq", "MRVL": "Nasdaq", "PANW": "Nasdaq",
    "SNPS": "Nasdaq", "CDNS": "Nasdaq", "CRWD": "Nasdaq", "DDOG": "Nasdaq",
    # NYSE
    "ABBV": "NYSE", "ABT": "NYSE", "ACN": "NYSE", "AXP": "NYSE",
    "BA": "NYSE", "BAC": "NYSE", "BRK.B": "NYSE", "CAT": "NYSE",
    "COP": "NYSE", "CVX": "NYSE", "DHR": "NYSE", "DIS": "NYSE",
    "GE": "NYSE", "GS": "NYSE", "HD": "NYSE", "IBM": "NYSE",
    "JNJ": "NYSE", "JPM": "NYSE", "KO": "NYSE", "LIN": "NYSE",
    "LLY": "NYSE", "MA": "NYSE", "MRK": "NYSE", "NEE": "NYSE",
    "PFE": "NYSE", "PG": "NYSE", "UNH": "NYSE", "V": "NYSE",
    "WMT": "NYSE", "XOM": "NYSE", "T": "NYSE", "VZ": "NYSE",
    # ARCA (ETFs)
    "SPY": "ARCA", "IWM": "ARCA", "DIA": "ARCA",
}

DEFAULT_UNIVERSE = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AMD", "AMGN", "AMZN", "AVGO", "AXP",
    "BA", "BAC", "BRK.B", "CAT", "COP", "COST", "CRM", "CSCO", "CVX", "DHR",
    "DIS", "GE", "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "INTU",
    "JNJ", "JPM", "KO", "LIN", "LLY", "MA", "MCD", "META", "MRK", "MSFT",
    "NEE", "NFLX", "NVDA", "PEP", "PFE", "PG", "TSLA",
]


# ═══════════════════════════════════════════════════════════════
# Redis 유틸리티
# ═══════════════════════════════════════════════════════════════

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_redis() -> redis.Redis:
    """Redis 연결 (3회 재시도)"""
    for attempt in range(3):
        try:
            r = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)
            r.ping()
            return r
        except Exception as e:
            log.warning(f"Redis 연결 실패 ({attempt+1}/3): {e}")
            time.sleep(2)
    raise ConnectionError("Redis 연결 불가 — 3회 실패")


def migrate_key_type(r: redis.Redis, key: str):
    """
    premium:ortex:{symbol} 키가 string 타입이면 hash로 마이그레이션.
    이전 v2가 string으로 저장한 키를 자동 변환.
    """
    try:
        key_type = r.type(key)
        if key_type == "string":
            # 기존 string 데이터 읽기
            raw = r.get(key)
            if raw:
                try:
                    old_data = json.loads(raw)
                    # string 키 삭제 후 hash로 재생성
                    r.delete(key)
                    r.hset(key, mapping={
                        "data": json.dumps(old_data.get("data", old_data)),
                        "updated_at": old_data.get("updated_at", now_iso()),
                    })
                    log.info(f"  마이그레이션 완료: {key} (string → hash)")
                except json.JSONDecodeError:
                    r.delete(key)
                    log.warning(f"  {key}: 잘못된 JSON — 삭제")
            else:
                r.delete(key)
        elif key_type == "none":
            pass  # 키 없음 — 새로 생성됨
        elif key_type == "hash":
            pass  # 이미 올바른 타입
        else:
            r.delete(key)
            log.warning(f"  {key}: 예상치 못한 타입 '{key_type}' — 삭제")
    except Exception as e:
        log.warning(f"  마이그레이션 실패 {key}: {e}")


def check_daily_credits(r: redis.Redis) -> Tuple[float, bool]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"ortex:credits:daily:{today}"
    used = float(r.get(key) or 0)
    return used, used < DAILY_CREDIT_LIMIT


def is_credit_exhausted_today(r: redis.Redis) -> bool:
    """
    [v3.1] 크레딧 소진 플래그 확인.
    이전 실행에서 크레딧이 소진되어 플래그가 설정된 경우,
    다음 날(UTC)까지 실행을 유보합니다.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    flag_key = f"ortex:credits:exhausted:{today}"
    return r.exists(flag_key) == 1


def set_credit_exhausted_flag(r: redis.Redis):
    """
    [v3.1] 크레딧 소진 플래그를 Redis에 설정합니다.
    TTL은 다음 날 UTC 00:00까지로 설정하여 자동 만료됩니다.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    flag_key = f"ortex:credits:exhausted:{today}"
    # 다음 날 UTC 00:00까지 TTL 계산
    tomorrow = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    ttl_seconds = int((tomorrow - datetime.now(timezone.utc)).total_seconds())
    ttl_seconds = max(ttl_seconds, 60)  # 최소 60초
    r.set(flag_key, json.dumps({
        "set_at": now_iso(),
        "credits_used": float(r.get(f"ortex:credits:daily:{today}") or 0),
        "limit": DAILY_CREDIT_LIMIT,
        "expires_at": tomorrow.isoformat(),
    }), ex=ttl_seconds)
    log.info(f"크레딧 소진 플래그 설정: {flag_key} (TTL: {ttl_seconds}초, 만료: {tomorrow.isoformat()})")


def record_credits(r: redis.Redis, credits_used: float):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"ortex:credits:daily:{today}"
    pipe = r.pipeline()
    pipe.incrbyfloat(key, credits_used)
    pipe.expire(key, 86400 * 2)
    pipe.execute()


# ═══════════════════════════════════════════════════════════════
# Ortex SDK 래퍼 (재시도 + 지수 백오프)
# ═══════════════════════════════════════════════════════════════

def _sdk_call_with_retry(func, *args, retries=MAX_RETRIES, **kwargs):
    """SDK 호출을 재시도 + 지수 백오프로 실행"""
    last_err = None
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            # 429 Rate Limit → 더 긴 대기
            if "429" in str(e) or "rate" in err_str:
                wait = RETRY_BACKOFF * (2 ** attempt) * 2
                log.warning(f"    Rate limit — {wait:.0f}초 대기 후 재시도 ({attempt+1}/{retries})")
                time.sleep(wait)
            # 5xx 서버 에러 → 재시도
            elif "500" in str(e) or "502" in str(e) or "503" in str(e):
                wait = RETRY_BACKOFF * (2 ** attempt)
                log.warning(f"    서버 에러 — {wait:.0f}초 대기 후 재시도 ({attempt+1}/{retries})")
                time.sleep(wait)
            # 403 크레딧 부족 → 재시도 무의미
            elif "403" in str(e):
                log.warning(f"    403 Forbidden — 크레딧 부족 또는 권한 없음")
                raise
            # 기타 → 짧은 대기 후 재시도
            else:
                wait = RETRY_BACKOFF * (2 ** attempt)
                log.warning(f"    에러: {e} — {wait:.0f}초 대기 후 재시도 ({attempt+1}/{retries})")
                time.sleep(wait)
    raise last_err


def get_exchange(symbol: str) -> str:
    return SYMBOL_EXCHANGE_MAP.get(symbol, "NYSE")


def get_exchange_fallbacks(symbol: str) -> List[str]:
    primary = get_exchange(symbol)
    fallbacks = ["NYSE", "Nasdaq", "AMEX", "ARCA"]
    result = [primary]
    for fb in fallbacks:
        if fb != primary:
            result.append(fb)
    return result


# ═══════════════════════════════════════════════════════════════
# 심볼별 데이터 수집
# ═══════════════════════════════════════════════════════════════

def fetch_symbol_data(sdk, symbol: str) -> Dict[str, Any]:
    """
    단일 심볼의 Ortex 데이터 수집 (4개 엔드포인트).
    반환 형식은 기존 소비자(realtime_streaming_server, advanced-signal-integrator)와 호환.
    """
    exchanges = get_exchange_fallbacks(symbol)
    total_credits = 0.0
    data = {
        "symbol": symbol,
        "short_interest": 0.0,
        "short_interest_pct": 0.0,
        "short_interest_shares": 0,
        "short_interest_usd": 0.0,
        "utilization": 0.0,
        "availability_shares": 0,
        "borrow_cost": 0.0,
        "cost_to_borrow": 0.0,
        "days_to_cover": 0.0,
        "credits_used": 0.0,
        "source": "ortex_sdk_v3",
        "fetched_at": now_iso(),
        "status": "ok",
    }

    # exchange 후보를 순서대로 시도
    exchange_used = None
    for exch in exchanges:
        try:
            si = _sdk_call_with_retry(sdk.get_short_interest, exch, symbol, page_size=1)
            total_credits += si.credits_used or 0
            df_si = si.df
            if df_si is not None and len(df_si) > 0:
                row = df_si.iloc[0]
                pct = float(row.get("shortInterestPcFreeFloat", 0) or 0)
                data["short_interest"] = pct / 100.0
                data["short_interest_pct"] = pct
                data["short_interest_shares"] = int(row.get("shortInterestShares", 0) or 0)
                data["short_interest_usd"] = float(row.get("shortInterestUsd", 0) or 0)
                exchange_used = exch
                break
        except Exception as e:
            if "not a valid" in str(e).lower() or "404" in str(e):
                continue
            log.warning(f"    {symbol}@{exch} SI 실패: {e}")
            continue

    if exchange_used is None:
        data["status"] = "exchange_not_found"
        data["credits_used"] = total_credits
        return data

    # 나머지 엔드포인트
    try:
        sa = _sdk_call_with_retry(sdk.get_short_availability, exchange_used, symbol, page_size=1)
        total_credits += sa.credits_used or 0
        df_sa = sa.df
        if df_sa is not None and len(df_sa) > 0:
            avail_pct = float(df_sa.iloc[0].get("shortAvailabilityPct", 0) or 0)
            data["utilization"] = max(0.0, min(1.0, 1.0 - (avail_pct / 10000.0))) if avail_pct > 0 else 0.0
            data["availability_shares"] = int(df_sa.iloc[0].get("shortAvailabilityShares", 0) or 0)
    except Exception as e:
        log.warning(f"    {symbol} availability: {e}")

    try:
        ctb = _sdk_call_with_retry(sdk.get_cost_to_borrow, exchange_used, symbol, page_size=1)
        total_credits += ctb.credits_used or 0
        df_ctb = ctb.df
        if df_ctb is not None and len(df_ctb) > 0:
            ctb_val = float(df_ctb.iloc[0].get("costToBorrowAll", 0) or 0)
            data["borrow_cost"] = ctb_val
            data["cost_to_borrow"] = ctb_val
    except Exception as e:
        log.warning(f"    {symbol} CTB: {e}")

    try:
        dtc = _sdk_call_with_retry(sdk.get_days_to_cover, exchange_used, symbol, page_size=1)
        total_credits += dtc.credits_used or 0
        df_dtc = dtc.df
        if df_dtc is not None and len(df_dtc) > 0:
            data["days_to_cover"] = float(df_dtc.iloc[0].get("daysToCover", 0) or 0)
    except Exception as e:
        log.warning(f"    {symbol} DTC: {e}")

    data["credits_used"] = total_credits
    data["exchange"] = exchange_used
    return data


# ═══════════════════════════════════════════════════════════════
# Redis 저장 (hash 타입 통일)
# ═══════════════════════════════════════════════════════════════

def save_to_redis(r: redis.Redis, all_data: Dict[str, Dict], total_credits: float,
                  success_count: int, fail_count: int, duration: float):
    """
    수집 결과를 Redis에 저장.
    
    핵심: premium:ortex:{symbol}은 반드시 hash 타입으로 저장.
    소비자 호환: r.hget(f"premium:ortex:{symbol}", "data") → JSON string
    """
    ts = now_iso()

    # 1) ares:ortex:latest — 전체 스냅샷 (string, alpha combiner 소비)
    snapshot = {
        "ts": ts,
        "symbols": {},
        "meta": {
            "collector_version": "v3.0",
            "total_credits_used": total_credits,
            "success_count": success_count,
            "fail_count": fail_count,
            "universe_size": len(DEFAULT_UNIVERSE),
        },
    }
    for sym, d in all_data.items():
        snapshot["symbols"][sym] = {
            "short_interest": d.get("short_interest", 0.0),
            "utilization": d.get("utilization", 0.0),
            "borrow_cost": d.get("borrow_cost", 0.0),
            "days_to_cover": d.get("days_to_cover", 0.0),
            "short_interest_shares": d.get("short_interest_shares", 0),
            "availability_shares": d.get("availability_shares", 0),
        }
    r.set("ares:ortex:latest", json.dumps(snapshot))

    # 2) premium:ortex:{symbol} — hash 타입 (소비자 호환)
    pipe = r.pipeline()
    for sym, d in all_data.items():
        key = f"premium:ortex:{sym}"
        # 키 타입 확인 → string이면 삭제 후 hash로 재생성
        migrate_key_type(r, key)
        # hash로 저장 — 소비자는 r.hget(key, "data")로 읽음
        pipe.hset(key, mapping={
            "data": json.dumps(d),
            "updated_at": ts,
        })
    pipe.execute()

    # 3) premium:ortex:last_updated
    # 이것도 타입 확인 (string이어야 함)
    r.set("premium:ortex:last_updated", ts)

    # 4) 수집기 상태
    r.set("ortex:collector:status", json.dumps({
        "ts": ts,
        "status": "completed",
        "success_count": success_count,
        "fail_count": fail_count,
        "total_credits_used": total_credits,
        "duration_sec": round(duration, 1),
        "version": "v3.0",
    }))

    # 5) 크레딧 기록
    record_credits(r, total_credits)

    log.info(f"Redis 저장 완료: ares:ortex:latest + {len(all_data)} premium:ortex 키 (hash)")


# ═══════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════

def main():
    start_time = time.time()
    log.info("=" * 60)
    log.info("ARES Ortex Collector v3.0 시작 (재발 방지 구조)")
    log.info("=" * 60)

    # 1) Redis 연결
    try:
        r = get_redis()
        log.info("Redis 연결 성공")
    except Exception as e:
        log.error(f"Redis 연결 실패: {e}")
        sys.exit(1)

    # 2) 기존 string 타입 키 → hash 마이그레이션 (1회성, 이후 무해)
    log.info("기존 키 타입 마이그레이션 확인...")
    for sym in DEFAULT_UNIVERSE:
        migrate_key_type(r, f"premium:ortex:{sym}")

    # 3) Ortex SDK 초기화
    try:
        import ortex as ortex_sdk
        ortex_sdk.set_api_key(ORTEX_API_KEY)
        log.info(f"Ortex SDK 초기화 완료 (키: {ORTEX_API_KEY[:8]}...)")
    except ImportError:
        log.error("ortex SDK 미설치 — pip install ortex")
        sys.exit(1)

    # 4) [v3.1] 크레딧 소진 플래그 확인 — 이전 실행에서 소진된 경우 즉시 종료
    if is_credit_exhausted_today(r):
        log.warning("크레딧 소진 플래그 감지 — 다음 날(UTC 00:00)까지 실행 유보")
        r.set("ortex:collector:status", json.dumps({
            "ts": now_iso(), "status": "credit_exhausted_skip",
            "reason": "Credit exhaustion flag set from previous run",
            "version": "v3.1",
        }))
        log.info("\ud06c\ub808\ub527 \uc18c\uc9c4\uc73c\ub85c \uc778\ud55c \uc2a4\ud0b5 — PM2 cron\uc774 \ub2e4\uc74c \uc2a4\ucf00\uc904\uc5d0\uc11c \uc7ac\uc2dc\ub3c4")
        return

    # 4-b) 크레딧 한도 확인
    credits_used_today, within_limit = check_daily_credits(r)
    log.info(f"오늘 사용 크레딧: {credits_used_today:.1f} / {DAILY_CREDIT_LIMIT}")
    if not within_limit:
        log.warning("일일 크레딧 한도 초과 — 수집 스킵 (기존 캐시 유지)")
        set_credit_exhausted_flag(r)  # [v3.1] 플래그 설정
        r.set("ortex:collector:status", json.dumps({
            "ts": now_iso(), "status": "credit_limit_exceeded",
            "credits_used_today": credits_used_today, "version": "v3.1",
        }))
        return

    # 5) 수집 실행
    universe = DEFAULT_UNIVERSE.copy()
    log.info(f"수집 대상: {len(universe)} 심볼")

    all_data: Dict[str, Dict] = {}
    total_credits = 0.0
    success_count = 0
    fail_count = 0

    for i, symbol in enumerate(universe):
        # 크레딧 중간 체크
        if total_credits + credits_used_today > DAILY_CREDIT_LIMIT:
            log.warning(f"크레딧 한도 근접 — {symbol}부터 스킵")
            set_credit_exhausted_flag(r)  # [v3.1] 중간 소진 시에도 플래그 설정
            break

        try:
            log.info(f"  [{i+1}/{len(universe)}] {symbol}")
            sym_data = fetch_symbol_data(ortex_sdk, symbol)
            all_data[symbol] = sym_data
            total_credits += sym_data.get("credits_used", 0)

            if sym_data["status"] == "ok":
                success_count += 1
                log.info(
                    f"    OK — SI={sym_data['short_interest']:.4f} "
                    f"UTIL={sym_data['utilization']:.4f} "
                    f"CTB={sym_data['borrow_cost']:.2f}% "
                    f"DTC={sym_data['days_to_cover']:.2f} "
                    f"(credits: {sym_data['credits_used']:.1f})"
                )
            else:
                fail_count += 1
                log.warning(f"    {symbol}: {sym_data['status']}")

        except Exception as e:
            fail_count += 1
            log.error(f"    {symbol} 수집 실패: {e}")
            all_data[symbol] = {
                "symbol": symbol,
                "short_interest": 0.0, "utilization": 0.0,
                "borrow_cost": 0.0, "days_to_cover": 0.0,
                "status": f"error: {str(e)[:100]}",
                "fetched_at": now_iso(),
            }

        time.sleep(INTER_SYMBOL_DELAY)

    # 6) Redis 저장
    elapsed = time.time() - start_time
    save_to_redis(r, all_data, total_credits, success_count, fail_count, elapsed)

    log.info("=" * 60)
    log.info(f"수집 완료: {success_count} 성공, {fail_count} 실패")
    log.info(f"크레딧: {total_credits:.1f} (오늘 누적: {credits_used_today + total_credits:.1f})")
    log.info(f"소요: {elapsed:.1f}초")
    log.info("=" * 60)

    for sym in ["NVDA", "TSLA", "AAPL", "META", "AMZN"]:
        if sym in all_data and all_data[sym].get("status") == "ok":
            d = all_data[sym]
            log.info(f"  {sym}: SI={d['short_interest']:.4f} UTIL={d['utilization']:.4f} CTB={d['borrow_cost']:.2f}% DTC={d['days_to_cover']:.2f}")


if __name__ == "__main__":
    main()
