/**
 * KIS Token Service v4 - 4AI Robust Edition (AOPS_EVT instrumented)
 * 근본 해결책: 재시작 0회 목표
 * 
 * [AOPS_EVT] 이벤트 삽입 v1:
 *  - circuit_open: Redis circuit breaker가 open 상태로 전환 시
 *  - circuit_half_open: 30초 후 재시도 허용 시
 *  - circuit_closed: Redis 정상 복구 시
 *  - retry_start/retry_success/retry_giveup: 토큰 발급 재시도
 *
 * [AOPS_EVT v2] dep_call 이벤트 (depCall 래퍼 기반):
 *  - dep_call_start/dep_call_ok/dep_call_fail: KIS 토큰 발급 HTTP 호출 증거
 *  - depCall() 래퍼로 표준화: Retry Expectation Guard가 증명 기반으로 전환
 *
 * [AOPS_EVT v2.1] kis:token:health v2 스키마 + 증명 기반 persist:
 *  - schema: "kis.token.health.v2" 강제
 *  - persistHealthV2Strict(): SET + EXPIRE 원자적 증명 (조용한 오염 방지)
 *  - expiryFieldsOk / healthPersistedOk / healthPersistReason 필드 추가
 *  - /api/health가 ONLINE 판정 시 4개 필수조건 충족 요구
 *
 * 4AI 합의 핵심:
 * 1. Redis 'error' 이벤트 리스너 필수 (Unhandled Exception 방지)
 * 2. Circuit Breaker 패턴으로 Redis 의존성 제거
 * 3. 메모리 캐시 Fallback으로 Redis 없이도 동작
 * 4. 지수 백오프 재연결 + 최대 재시도 제한
 */

import { createClient } from "redis";
import fetch from "node-fetch";
import { emitEvent } from "./event_logger.mjs";
import { depCall, newRequestId } from "./dep_call_wrapper.mjs";
import { publishCanonicalKisToken } from "./lib/kis_token_ssot.mjs";

// === AOPS_EVT PROC NAME ===
const PROC = process.env.PM2_PROCESS_NAME || "kis-token-service";

function localhostRedisAllowed() {
  return ['1', 'true', 'yes', 'on'].includes(String(process.env.ARES_ALLOW_LOCALHOST_REDIS || '').toLowerCase());
}

function requireRedisUrl() {
  const url = process.env.ARES_REDIS_URL || process.env.REDIS_URL || process.env.REDIS_WRITER_URL;
  if (!url) {
    throw new Error('REDIS_URL/ARES_REDIS_URL/REDIS_WRITER_URL missing; refusing implicit localhost Redis fallback');
  }
  const low = String(url).toLowerCase();
  if ((low.includes('localhost') || low.includes('127.0.0.1')) && !localhostRedisAllowed()) {
    throw new Error('Redis URL points to localhost; set ARES_ALLOW_LOCALHOST_REDIS=1 only for local development');
  }
  // URL에 특수문자($, #, &, ^, !, <, >) 포함 시 인코딩 처리 (node-redis v5 호환)
  try {
    new URL(url);
    return url; // 이미 유효한 URL
  } catch {
    const match = url.match(/^(rediss?:\/\/[^:]+:)(.+?)(@[^@]+)$/);
    if (match) {
      const encoded = match[1] + encodeURIComponent(match[2]) + match[3];
      return encoded;
    }
    return url;
  }
}

// ============================================
// 설정
// ============================================
const CONFIG = {
  redis: {
    url: requireRedisUrl(),  // explicit Redis URL required; no localhost fallback
    connectTimeout: 5000,
    commandTimeout: 5000,
    maxRetries: 10,
    baseRetryDelay: 100,
    maxRetryDelay: 30000
  },
  kis: {
    appKey: process.env.KIS_APP_KEY,
    appSecret: process.env.KIS_APP_SECRET,
    baseUrl: process.env.KIS_BASE_URL || "https://openapi.koreainvestment.com:9443"
  },
  token: {
    refreshBeforeExpiry: 120 * 60 * 1000,  // 만료 10분 전 갱신
    checkInterval: 60 * 1000,              // 1분마다 체크
    ttl: 24 * 60 * 60                       // Redis TTL 24시간
  }
};

// ============================================
// 로거
// ============================================
const log = (level, event, data = {}) => {
  const ts = new Date().toISOString();
  console.log(`[${ts}] [${level}] [kis-token-service] ${event}:`, JSON.stringify(data));
};

// ============================================
// Circuit Breaker 상태 (AOPS_EVT instrumented)
// ============================================
const circuitBreaker = {
  isRedisHealthy: false,
  consecutiveFailures: 0,
  lastFailure: null,
  isOpen: false,  // true면 Redis 호출 차단
  _wasOpen: false, // 이전 상태 추적 (이벤트 중복 방지)
  
  recordSuccess() {
    this.consecutiveFailures = 0;
    this.isRedisHealthy = true;
    const wasOpen = this.isOpen;
    this.isOpen = false;
    
    // === AOPS_EVT: circuit_closed — open→closed 전환 시에만 발행 ===
    if (wasOpen) {
      emitEvent({
        proc: PROC,
        event: "circuit_closed",
        sev: "INFO",
        corr: { endpoint: "redis" },
        msg: "circuit breaker closed: Redis recovered",
        ctx: { dependency: "redis" }
      });
    }
  },
  
  recordFailure() {
    this.consecutiveFailures++;
    this.lastFailure = Date.now();
    if (this.consecutiveFailures >= 3) {
      const wasAlreadyOpen = this.isOpen;
      this.isOpen = true;
      this.isRedisHealthy = false;
      log("WARN", "CIRCUIT_BREAKER_OPEN", { failures: this.consecutiveFailures });
      
      // === AOPS_EVT: circuit_open — 새로 open 전환 시에만 발행 ===
      if (!wasAlreadyOpen) {
        emitEvent({
          proc: PROC,
          event: "circuit_open",
          sev: "P1",
          corr: { endpoint: "redis" },
          msg: `circuit breaker opened: ${this.consecutiveFailures} consecutive Redis failures`,
          ctx: { dependency: "redis", consecutive_failures: this.consecutiveFailures }
        });
      }
    }
  },
  
  canAttempt() {
    if (!this.isOpen) return true;
    // 30초 후 half-open 상태로 전환
    if (Date.now() - this.lastFailure > 30000) {
      log("INFO", "CIRCUIT_BREAKER_HALF_OPEN", {});
      // === AOPS_EVT: circuit_half_open — 재시도 허용 전환 ===
      emitEvent({
        proc: PROC,
        event: "circuit_half_open",
        sev: "WARN",
        corr: { endpoint: "redis" },
        msg: "circuit breaker half-open: allowing probe request",
        ctx: { dependency: "redis", cooldown_sec: 30 }
      });
      return true;
    }
    return false;
  }
};

// ============================================
// 메모리 캐시 (Redis Fallback)
// ============================================
const memoryCache = {
  data: new Map(),
  
  set(key, value, ttlSeconds = 3600) {
    const expiry = Date.now() + (ttlSeconds * 1000);
    this.data.set(key, { value, expiry });
    log("DEBUG", "MEMORY_CACHE_SET", { key, ttl: ttlSeconds });
  },
  
  get(key) {
    const item = this.data.get(key);
    if (!item) return null;
    if (Date.now() > item.expiry) {
      this.data.delete(key);
      return null;
    }
    return item.value;
  },
  
  delete(key) {
    this.data.delete(key);
  }
};

// ============================================
// Robust Redis 클라이언트
// ============================================
let redis = null;

function createRobustRedisClient() {
  const client = createClient({
    url: CONFIG.redis.url,
    socket: {
      connectTimeout: CONFIG.redis.connectTimeout,
      reconnectStrategy: (retries) => {
        if (retries > CONFIG.redis.maxRetries) {
          log("ERROR", "REDIS_MAX_RETRIES_REACHED", { retries });
          circuitBreaker.recordFailure();
          // 재연결 포기하지 않고 최대 딜레이로 계속 시도
          return CONFIG.redis.maxRetryDelay;
        }
        const delay = Math.min(
          CONFIG.redis.baseRetryDelay * Math.pow(2, retries),
          CONFIG.redis.maxRetryDelay
        );
        log("INFO", "REDIS_RECONNECT_SCHEDULED", { retries, delay });
        return delay;
      }
    }
  });
  
  // [CRITICAL] 모든 에러 이벤트 핸들링 - 프로세스 크래시 방지
  client.on("error", (err) => {
    log("ERROR", "REDIS_ERROR_HANDLED", { error: err.message });
    circuitBreaker.recordFailure();
    // 절대 throw하지 않음 - 프로세스 유지
  });
  
  client.on("connect", () => {
    log("INFO", "REDIS_CONNECTED", {});
  });
  
  client.on("ready", () => {
    log("INFO", "REDIS_READY", {});
    circuitBreaker.recordSuccess();
  });
  
  client.on("reconnecting", () => {
    log("WARN", "REDIS_RECONNECTING", {});
  });
  
  client.on("end", () => {
    log("WARN", "REDIS_CONNECTION_ENDED", {});
    circuitBreaker.isRedisHealthy = false;
  });
  
  return client;
}

// ============================================
// Safe Redis Operations (Circuit Breaker 적용)
// ============================================
async function safeRedisGet(key) {
  // Circuit Breaker 체크
  if (!circuitBreaker.canAttempt()) {
    log("DEBUG", "REDIS_SKIPPED_CIRCUIT_OPEN", { key });
    return memoryCache.get(key);
  }
  
  try {
    if (!redis || !redis.isOpen) {
      return memoryCache.get(key);
    }
    
    const value = await Promise.race([
      redis.get(key),
      new Promise((_, reject) => 
        setTimeout(() => reject(new Error("Redis timeout")), CONFIG.redis.commandTimeout)
      )
    ]);
    
    circuitBreaker.recordSuccess();
    
    // 메모리 캐시에도 저장 (백업)
    if (value) {
      memoryCache.set(key, value);
    }
    
    return value;
  } catch (err) {
    log("WARN", "REDIS_GET_FAILED", { key, error: err.message });
    circuitBreaker.recordFailure();
    return memoryCache.get(key);
  }
}

async function safeRedisSet(key, value, ttl = CONFIG.token.ttl) {
  // 항상 메모리 캐시에 먼저 저장
  memoryCache.set(key, value, ttl);
  
  if (!circuitBreaker.canAttempt()) {
    log("DEBUG", "REDIS_SET_SKIPPED_CIRCUIT_OPEN", { key });
    return true;
  }
  
  try {
    if (!redis || !redis.isOpen) {
      return true;  // 메모리 캐시에 저장됨
    }
    
    await Promise.race([
      redis.setEx(key, ttl, value),
      new Promise((_, reject) => 
        setTimeout(() => reject(new Error("Redis timeout")), CONFIG.redis.commandTimeout)
      )
    ]);
    
    circuitBreaker.recordSuccess();
    return true;
  } catch (err) {
    log("WARN", "REDIS_SET_FAILED", { key, error: err.message });
    circuitBreaker.recordFailure();
    return true;  // 메모리 캐시에는 저장됨
  }
}

// ============================================
// Strict health persistence (SET + EXPIRE 증명)
// - "조용한 오염" 방지: EXPIRE 실패를 탐지하고, expiryFieldsOk=false로 기록
// - node-redis v5: multi().exec() returns flat array ["OK", 1]
// ============================================
async function persistHealthV2Strict(key, jsonValue, ttlSeconds) {
  // 메모리 캐시는 항상 우선 저장(운영 가용성)
  memoryCache.set(key, jsonValue, ttlSeconds);

  // Circuit OPEN이면 Redis 증명 불가
  if (!circuitBreaker.canAttempt()) {
    log("DEBUG", "HEALTH_PERSIST_SKIPPED_CIRCUIT_OPEN", { key });
    return { persisted_ok: false, reason: "circuit_open" };
  }

  try {
    if (!redis || !redis.isOpen) {
      return { persisted_ok: false, reason: "redis_not_ready" };
    }

    // MULTI: SET + EXPIRE가 둘 다 성공해야 persisted_ok=true
    const execRes = await Promise.race([
      redis.multi().set(key, jsonValue).expire(key, ttlSeconds).exec(),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("Redis timeout")), CONFIG.redis.commandTimeout)
      )
    ]);

    // node-redis v5: exec() returns flat array ["OK", 1]
    const okShape = Array.isArray(execRes) && execRes.length >= 2;
    if (!okShape) {
      circuitBreaker.recordFailure();
      return { persisted_ok: false, reason: "bad_exec_reply", detail: execRes };
    }

    const [setReply, expReply] = execRes;
    const setOk = (setReply === "OK");
    const expOk = (expReply === 1 || expReply === true);

    if (setOk && expOk) {
      circuitBreaker.recordSuccess();
      return { persisted_ok: true, reason: null };
    }

    // SET 또는 EXPIRE 중 하나라도 실패 → 조용한 오염 위험
    circuitBreaker.recordFailure();
    return {
      persisted_ok: false,
      reason: "set_or_expire_failed",
      detail: { setReply, expReply }
    };
  } catch (err) {
    log("WARN", "HEALTH_PERSIST_FAILED", { key, error: err.message });
    circuitBreaker.recordFailure();
    return { persisted_ok: false, reason: "exception", error: err.message };
  }
}

// ============================================
// KIS Token 관리 (AOPS_EVT instrumented)
// ============================================
let currentToken = null;
let tokenExpiry = null;

async function fetchNewToken() {
  // === AOPS_EVT: retry_start — 토큰 발급 시도 ===
  const corrToken = { endpoint: "/oauth2/tokenP" };
  emitEvent({
    proc: PROC,
    event: "retry_start",
    sev: "WARN",
    corr: corrToken,
    msg: "fetching new KIS access token",
    ctx: { attempt: 1 }
  });

  try {
    log("INFO", "FETCHING_NEW_TOKEN", {});
    
    // === AOPS_EVT v2: depCall 래퍼 — KIS 토큰 발급 HTTP 호출 (증명 기반) ===
    const depCorr = { ...corrToken, request_id: newRequestId() };
    const data = await depCall({
      proc: PROC,
      corr: depCorr,
      dep: { name: "KIS", type: "http", method: "POST", path: "/oauth2/tokenP" }
    }, async () => {
      const response = await fetch(`${CONFIG.kis.baseUrl}/oauth2/tokenP`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          grant_type: "client_credentials",
          appkey: CONFIG.kis.appKey,
          appsecret: CONFIG.kis.appSecret
        }),
        timeout: 10000
      });
      if (!response.ok) {
        throw new Error(`KIS API error: ${response.status}`);
      }
      return await response.json();
    });
    
    if (!data.access_token) {
      throw new Error("No access_token in response");
    }
    
    currentToken = data.access_token;
    // KIS 토큰은 보통 24시간 유효
    tokenExpiry = Date.now() + (data.expires_in || 86400) * 1000;
    
    // Redis와 메모리 캐시에 저장
    const tokenData = JSON.stringify({
      token: currentToken,
      expiry: tokenExpiry,
      fetchedAt: Date.now()
    });
    
    await safeRedisSet("kis:token:current", tokenData);
    await safeRedisSet("kis:token:canonical", tokenData);
    await safeRedisSet("kis:token:access_token", currentToken);
    await safeRedisSet("kis:token:access", currentToken, 86400);  // alias for healthcheck compatibility
    await publishCanonicalKisToken(redis, {
      access_token: currentToken,
      expires_at_ms: tokenExpiry,
      source: "kis-token-service",
      ttl_sec: CONFIG.token.ttl,
    }).catch((err) => log("WARN", "CANONICAL_TOKEN_PUBLISH_FAILED", { error: err.message }));
    
    // [v4.1] 레거시 호환: go-nogo-judge가 참조하는 키도 갱신 (재발 방지)
    await safeRedisSet("kis:auth:token_expires_at", String(tokenExpiry), CONFIG.token.ttl);
    await safeRedisSet("kis:auth:access_token", currentToken, CONFIG.token.ttl);
    await safeRedisSet("kis:access_token", currentToken, CONFIG.token.ttl);
    await safeRedisSet("kis:access_token_expires", String(tokenExpiry), CONFIG.token.ttl);
    log("INFO", "LEGACY_KEYS_UPDATED", { keys: ["kis:auth:token_expires_at", "kis:auth:access_token", "kis:access_token", "kis:access_token_expires"] });
    
    log("INFO", "TOKEN_FETCHED_SUCCESS", { 
      expiresIn: data.expires_in,
      expiryTime: new Date(tokenExpiry).toISOString()
    });
    
    // === AOPS_EVT: retry_success — 토큰 발급 성공 ===
    emitEvent({
      proc: PROC,
      event: "retry_success",
      sev: "INFO",
      corr: corrToken,
      msg: "KIS token fetched successfully",
      ctx: { attempt: 1, expires_in: data.expires_in }
    });
    
    return currentToken;
  } catch (err) {
    log("ERROR", "TOKEN_FETCH_FAILED", { error: err.message });
    // === AOPS_EVT: retry_giveup — 토큰 발급 실패 ===
    emitEvent({
      proc: PROC,
      event: "retry_giveup",
      sev: "P1",
      corr: corrToken,
      msg: `KIS token fetch failed: ${err.message}`,
      ctx: { attempt: 1, error: err.message }
    });
    throw err;
  }
}

async function loadTokenFromCache() {
  try {
    const cached = await safeRedisGet("kis:token:current");
    if (cached) {
      const data = JSON.parse(cached);
      if (data.expiry > Date.now()) {
        currentToken = data.token;
        tokenExpiry = data.expiry;
        log("INFO", "TOKEN_LOADED_FROM_CACHE", { 
          expiryTime: new Date(tokenExpiry).toISOString()
        });
        return true;
      }
    }
  } catch (err) {
    log("WARN", "CACHE_LOAD_FAILED", { error: err.message });
  }
  return false;
}

function isTokenExpiringSoon() {
  if (!tokenExpiry) return true;
  return (tokenExpiry - Date.now()) < CONFIG.token.refreshBeforeExpiry;
}

async function ensureValidToken() {
  try {
    // 토큰이 없거나 곧 만료되면 갱신
    if (!currentToken || isTokenExpiringSoon()) {
      // 먼저 캐시에서 로드 시도
      const loaded = await loadTokenFromCache();
      
      // 캐시에도 없거나 만료 임박이면 새로 발급
      if (!loaded || isTokenExpiringSoon()) {
        await fetchNewToken();
      }
    }
    
    return currentToken;
  } catch (err) {
    log("ERROR", "ENSURE_TOKEN_FAILED", { error: err.message });
    // 기존 토큰이 있으면 반환 (만료되었더라도)
    return currentToken;
  }
}

// ============================================
// Health Check & Status (v2 schema + 증명 기반 persist)
// ============================================
async function updateHealthStatus() {
  const nowMs = Date.now();
  const expiresIn = tokenExpiry ? Math.floor((tokenExpiry - nowMs) / 1000) : null;
  const expiryIso = tokenExpiry ? new Date(tokenExpiry).toISOString() : null;

  // v2 스키마 강제: tokenExpiry + tokenExpiresIn 동시 존재(토큰이 있을 때)
  const hasToken = !!currentToken;
  const tokenFieldsOk = (!hasToken) || (!!expiryIso && Number.isFinite(expiresIn));

  const base = {
    schema: "kis.token.health.v2",
    ts: new Date().toISOString(),
    service: "kis-token-service",
    version: "v4-robust",
    hasToken,
    tokenExpiry: expiryIso,
    tokenExpiresIn: expiresIn,
    redisHealthy: circuitBreaker.isRedisHealthy,
    circuitBreakerOpen: circuitBreaker.isOpen,
    consecutiveFailures: circuitBreaker.consecutiveFailures
  };

  // 온라인 증명력: (1) 토큰 필드 일관성 (2) Redis SET+EXPIRE 증명
  let finalObj = {
    ...base,
    expiryFieldsOk: tokenFieldsOk,
    healthPersistedOk: false,
    healthPersistReason: "not_attempted"
  };

  // 1차 시도: persistHealthV2Strict로 SET+EXPIRE 원자적 증명
  const w = await persistHealthV2Strict("kis:token:health", JSON.stringify(finalObj), 120);
  finalObj.healthPersistedOk = !!w.persisted_ok;
  finalObj.healthPersistReason = w.reason || null;

  if (!w.persisted_ok) {
    // SET/EXPIRE 증명 실패 → expiryFieldsOk를 false로 강제
    finalObj.expiryFieldsOk = false;
  }

  // 2차 쓰기(필수): 1차에서 저장된 값은 healthPersistedOk=false 상태이므로,
  // 성공/실패 결과를 반영한 최종 finalObj를 다시 기록
  await persistHealthV2Strict("kis:token:health", JSON.stringify(finalObj), 120);

  return finalObj;
}

// ============================================
// Main Loop
// ============================================
async function main() {
  log("INFO", "SERVICE_STARTING", { version: "v4-robust" });
  
  // === AOPS_EVT: process_start ===
  emitEvent({
    proc: PROC,
    event: "process_start",
    sev: "INFO",
    corr: {},
    msg: "kis-token-service starting (v4-robust, AOPS_EVT instrumented)",
    ctx: { version: "v4-robust" }
  });
  
  // Redis 연결 시도 (실패해도 계속 진행)
  try {
    redis = createRobustRedisClient();
    await redis.connect();
  } catch (err) {
    log("WARN", "REDIS_INITIAL_CONNECT_FAILED", { error: err.message });
    // Redis 없이도 동작 가능
  }
  
  // 초기 토큰 확보
  try {
    await ensureValidToken();
  } catch (err) {
    log("ERROR", "INITIAL_TOKEN_FAILED", { error: err.message });
  }
  
  // 메인 루프 - 절대 죽지 않음
  while (true) {
    try {
      await ensureValidToken();
      await updateHealthStatus();
    } catch (err) {
      // 모든 에러를 잡아서 로깅만 함
      log("ERROR", "MAIN_LOOP_ERROR", { error: err.message });
    }
    
    // 다음 체크까지 대기
    await new Promise(resolve => setTimeout(resolve, CONFIG.token.checkInterval));
  }
}

// ============================================
// Global Error Handlers (프로세스 크래시 방지)
// ============================================
process.on("uncaughtException", (err) => {
  log("FATAL", "UNCAUGHT_EXCEPTION", { error: err.message, stack: err.stack });
  // 프로세스 종료하지 않음
});

process.on("unhandledRejection", (reason, promise) => {
  log("FATAL", "UNHANDLED_REJECTION", { reason: String(reason) });
  // 프로세스 종료하지 않음
});

// 시작
main().catch(err => {
  log("FATAL", "MAIN_CRASHED", { error: err.message });
});
