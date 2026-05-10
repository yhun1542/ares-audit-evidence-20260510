#!/usr/bin/env node
/**
 * Multi-Source Regime Detector v2.0
 * 
 * 변경사항 (v1.0 → v2.0):
 * - Redis 키 네임스페이스 정규화: regime:* → regime:ms:*
 * - 호환 레이어(dual-write): 기존 regime:current에도 동시 저장
 * - Champion-Challenger 결합 레이어 추가
 * - final_mult 변경 시에만 히스토리 push (로그 폭발 방지)
 * 
 * Redis 키 구조:
 * - regime:ms:current    (신규 단일 진실 - 멀티소스)
 * - regime:ms:sources    (소스별 상세)
 * - regime:ms:history    (멀티소스 레짐 변경 이벤트)
 * - regime:current       (호환용 dual-write)
 * - regime:r15:current   (챔피언 15레짐 입력 - 외부에서 작성)
 * - regime:gate          (게이트 상태 - 히스테리시스)
 * - regime:final:current (최종 결합 결과)
 * - regime:final:history (final_mult 변경 시에만 기록)
 */

import { createClient } from 'redis';
import { GW } from './gw-client.mjs';
import fetch from 'node-fetch';
// === ARES A1-β [PATCH 20260507]: paginated SPY options snapshot helper ===
import { computeOptionsSnapshot } from './polygon_options_snapshot.mjs';

/**
 * Fetch SPY options regime via computeOptionsSnapshot and cache in Redis.
 * Returns: { pcr, ivSkew, atmMidIV, sampleSize, source, ts }
 *
 *   pcr === null     ⇒ no usable put/call signal (both volumes 0, OR
 *                       degenerate callVol=0 with putVol>0 → would be Infinity).
 *                       calculatePolygonScore must add 0 (NOT 0.1) in that case.
 *   ivSkew === null  ⇒ insufficient 25Δ IV pair; score adds 0.
 */
async function fetchSPYOptionsRegime(redis, polygonApiKey) {
    const KEY = 'regime:options:spy';
    const toFiniteOrNull = (v) => {
        if (v === null || v === undefined || v === '') return null;
        const n = Number(v);
        return Number.isFinite(n) ? n : null;
    };
    try {
        const snap = await computeOptionsSnapshot('SPY', polygonApiKey);
        const callVol = Number(snap?.call_volume ?? snap?.callVolume ?? 0) || 0;
        const putVol  = Number(snap?.put_volume  ?? snap?.putVolume  ?? 0) || 0;
        let pcr;
        if (callVol === 0 && putVol === 0) pcr = null;
        else if (callVol === 0) pcr = null;
        else pcr = putVol / callVol;
        let ivSkew = toFiniteOrNull(snap?.ivSkew ?? snap?.iv_skew);
        if (ivSkew === null) {
            const putIv  = toFiniteOrNull(snap?.put_iv_25d  ?? snap?.putIv25d);
            const callIv = toFiniteOrNull(snap?.call_iv_25d ?? snap?.callIv25d);
            if (putIv !== null && callIv !== null) ivSkew = putIv - callIv;
        }
        const atmMidIV   = toFiniteOrNull(snap?.atm_mid_iv ?? snap?.atmMidIV);
        const sampleSize = Number(snap?.sample_size ?? snap?.sampleSize ?? 0) || 0;
        const source     = String(snap?.source ?? 'polygon_options_snapshot');
        const payload = { pcr, ivSkew, atmMidIV, sampleSize, source, ts: Date.now() };
        try { await redis.set(KEY, JSON.stringify(payload), 'EX', 600); } catch (e) {
            console.warn('[Options] Redis cache write failed:', e.message);
        }
        return payload;
    } catch (err) {
        console.error('[Options] fetchSPYOptionsRegime failed:', err.message);
        try {
            const cached = await redis.get(KEY);
            if (cached) {
                const obj = typeof cached === 'string' ? JSON.parse(cached) : cached;
                if (obj && typeof obj === 'object') {
                    return {
                        pcr:        obj.pcr        ?? null,
                        ivSkew:     obj.ivSkew     ?? null,
                        atmMidIV:   obj.atmMidIV   ?? null,
                        sampleSize: obj.sampleSize ?? 0,
                        source:     'last_known_good',
                        ts:         obj.ts         ?? null
                    };
                }
            }
        } catch (_) { /* ignore */ }
        return { pcr: null, ivSkew: null, atmMidIV: null, sampleSize: 0, source: 'unavailable', ts: Date.now() };
    }
}
// === END ARES A1-β PATCH ===

function resolveRequiredRedisUrl() {
  const url = process.env.REDIS_URL || process.env.ARES_REDIS_URL || "";
  if (!url) throw new Error("REDIS_URL or ARES_REDIS_URL is required");
  if (/localhost|127\.0\.0\.1/.test(url)) {
    throw new Error("Local Redis fallback is forbidden in production");
  }
  return url;
}



// ═══════ GPU REGIME CLASSIFIER DIRECT INTEGRATION ═══════
// ═══════ END GPU INTEGRATION ═══════


// ===== GPU RISK-OFF ONLY HARDENING v2 (FIXED: hGetAll for hash meta) =====
async function readGpuRiskOff(redis) {
  try {
    const status = await redis.get('gpu:regime:classifier:status');
    if (status !== 'ACTIVE') return null;
    
    // C-2 FIX: meta는 hash 타입이므로 hGetAll 사용
    const meta = await redis.hGetAll('gpu:regime:classifier:meta');
    if (!meta || Object.keys(meta).length === 0) return null;
    const oos_auc = Number(meta.oos_auc || meta.mean_auc || 0);
    if (oos_auc < 0.65) return null;
    
    const dominantRaw = await redis.get('gpu:regime:classifier:dominant_classes');
    if (!dominantRaw) return null;
    let classes;
    try { classes = JSON.parse(dominantRaw); } catch(e) { return null; }
    if (!Array.isArray(classes) || classes.length === 0) return null;
    
    const riskOffPct = classes
      .filter(c => ['CRASH','BEAR'].includes(String(c.class||'').toUpperCase()))
      .reduce((s,c) => s + Number(c.pct||0), 0);
    const topClass = String(classes[0].class || 'NEUTRAL').toUpperCase();
    const topPct = Number(classes[0].pct || 0);
    
    // 2-bar persistence
    const prev1Raw = await redis.get('gpu:regime:classifier:last1');
    const prev2Raw = await redis.get('gpu:regime:classifier:last2');
    const prev1 = prev1Raw ? JSON.parse(prev1Raw) : null;
    const prev2 = prev2Raw ? JSON.parse(prev2Raw) : null;
    const currentState = { topClass, topPct, riskOffPct, ts: Date.now() };
    await redis.set('gpu:regime:classifier:last2', JSON.stringify(prev1 || currentState), 'EX', 86400);
    await redis.set('gpu:regime:classifier:last1', JSON.stringify(currentState), 'EX', 86400);
    
    const persistentRisk = (prev1 && prev2 &&
      Number(prev1.riskOffPct||0) >= 0.30 &&
      Number(prev2.riskOffPct||0) >= 0.30 &&
      riskOffPct >= 0.30);
    
    let gpuGateOverride = null;
    let gpuRegime = 'NEUTRAL';
    if (persistentRisk) {
      if (riskOffPct >= 0.60) { gpuGateOverride = 'CRISIS'; gpuRegime = 'CRISIS'; }
      else { gpuGateOverride = 'CAUTION'; gpuRegime = 'CAUTION'; }
    }
    
    return {
      regime: gpuRegime,
      gate_override: gpuGateOverride,
      risk_off_pct: riskOffPct,
      top_class: topClass,
      top_pct: topPct,
      oos_auc: oos_auc,
      source: 'gpu_alpha_head_v2_hardened_riskoff_only'
    };
  } catch (err) {
    return null;
  }
}
// ===== END GPU RISK-OFF ONLY HARDENING v2 =====

async function fetchWithTimeout(url, options = {}, timeoutMs = 10000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

// ============================================
// Legacy dual-write switch
// - default: ON (backward compatible)
// - disable: LEGACY_WRITE=0 / false / off / no
// ============================================
const LEGACY_WRITE = !/^(0|false|off|no)$/i.test(String(process.env.LEGACY_WRITE ?? '0'));

// ============================================
// Configuration
// ============================================
const CONFIG = {
    redis: {
        url: resolveRequiredRedisUrl()
    },
    api: {
        polygon: process.env.POLYGON_API_KEY,
        fred: process.env.FRED_API_KEY,
        alphaVantage: process.env.ALPHA_VANTAGE_KEY,
        tavily: process.env.TAVILY_API_KEY,
        newsApi: process.env.NEWS_API_KEY,
        secApi: process.env.SEC_API_KEY
    },
    weights: {
        polygon: 0.35,
        fred: 0.20,
        alphaVantage: 0.20,
        news: 0.25,
        sec: 0.00
    },
    updateInterval: 300000,
    regimeThresholds: {
        crisis: 0.7,
        caution: 0.5,
        normal: 0.3
    }
};

// ============================================
// Champion-Challenger Combiner Configuration
// ============================================
const COMBINE_CFG = {
    staleMs: 180_000,          // 멀티소스 3분 stale이면 개입 안 함
    enterCautionCount: 3,      // 3분 연속 CAUTION+ → CAUTION_GATE
    enterCrisisCount: 2,       // 2분 연속 CRISIS → CRISIS_GATE
    clearCount: 10,            // 10분 연속 NORMAL- → 게이트 해제
    gateMult: {
        NO_GATE: 1.0,
        CAUTION_GATE: 0.8,
        CRISIS_GATE: 0.5
    }
};

// ============================================
// Helper Functions
// ============================================
async function loadHash(redis, key) {
    const obj = await redis.hGetAll(key);
    return (obj && Object.keys(obj).length > 0) ? obj : null;
}

function toNum(x, fallback = null) {
    const n = Number(x);
    return Number.isFinite(n) ? n : fallback;
}

// ============================================
// Data Fetchers
// ============================================

async function fetchPolygonData(redis) {
    // [ARES A1-β PATCH 20260507] redis client passed in for VIX LKG / Options regime caching.
    const data = {
        vix: null,
        putCallRatio: null,
        ivSkew: null,
        vixTermStructure: null,
        score: 0
    };
    
    try {
        // ── Gateway: VIX level (Yahoo ^VIX → FRED VIXCLS → Polygon-VIXY-adjusted → Cache) ──
        // [FIX-A' v3 2026-04-22] Strictly validate GW response:
        //   - Accept only if proxy is a fresh primary (YAHOO_VIX, FRED_VIXCLS, POLYGON_VIXY_ADJ)
        //   - Reject proxy ∈ {DEFAULT, CACHE, UNKNOWN} or stale===true → force direct fallback.
        //   - On total failure, read last-known-good from Redis and emit CRITICAL flag.
        const gwVix = await GW.vix();
        const SANE = (x) => Number.isFinite(Number(x)) && Number(x) >= 5 && Number(x) <= 150;
        const VALID_PRIMARIES = new Set(['YAHOO_VIX', 'FRED_VIXCLS', 'POLYGON_VIXY_ADJ', 'AV_VIX']);
        const proxy = gwVix?.proxy;
        const useGw = gwVix && SANE(gwVix.level) && VALID_PRIMARIES.has(String(proxy)) && gwVix.stale !== true;

        if (useGw) {
            data.vix = Number(gwVix.level);
            data.vix_source = proxy;
            data.vixy_raw = gwVix.raw?.vixy || null;
            console.log(`[VIX] Gateway (${proxy}): ${data.vix}`);
        } else {
            if (gwVix) console.warn(`[VIX] Gateway returned non-primary proxy=${proxy} stale=${gwVix.stale} — forcing direct fallback`);
            // Emergency direct fallback chain: Yahoo ^VIX → FRED → last-known-good
            let got = false;
            try {
                const yR = await fetchWithTimeout('https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d');
                if (yR.ok) {
                    const yD = await yR.json();
                    const lvl = Number(yD?.chart?.result?.[0]?.meta?.regularMarketPrice);
                    if (SANE(lvl)) { data.vix = lvl; data.vix_source = 'YAHOO_DIRECT'; got = true;
                        console.log(`[VIX] Direct Yahoo fallback: ${data.vix}`); }
                }
            } catch (e) { console.warn('[VIX] Yahoo direct failed:', e.message); }

            if (!got) {
                try {
                    const fR = await fetchWithTimeout(
                        `https://api.stlouisfed.org/fred/series/observations?series_id=VIXCLS&api_key=${CONFIG.api.fred}&file_type=json&sort_order=desc&limit=1`
                    );
                    if (fR.ok) {
                        const fD = await fR.json();
                        const lvl = parseFloat(fD?.observations?.[0]?.value);
                        if (SANE(lvl)) { data.vix = lvl; data.vix_source = 'FRED_DIRECT'; got = true;
                            console.log(`[VIX] Direct FRED fallback: ${data.vix}`); }
                    }
                } catch (e) { console.warn('[VIX] FRED direct failed:', e.message); }
            }

            if (!got) {
                // Last-known-good: read Redis cached value (if non-stale).
                try {
                    const cachedRaw = await redis.get('gw:vix:level');
                    if (cachedRaw) {
                        const cached = typeof cachedRaw === 'string' ? JSON.parse(cachedRaw) : cachedRaw;
                        const lvl = Number(cached?.level);
                        if (SANE(lvl) && VALID_PRIMARIES.has(String(cached?.proxy))) {
                            data.vix = lvl; data.vix_source = 'LAST_KNOWN_GOOD';
                            console.warn(`[VIX] Using last-known-good ${cached.proxy}=${lvl}`);
                            got = true;
                        }
                    }
                } catch (e) { console.warn('[VIX] LKG read failed:', e.message); }
            }

            if (!got) {
                // Explicit FAIL: do NOT invent a VIX. Mark in Redis for operators.
                console.error('[VIX] CRITICAL: all VIX sources unavailable — skipping VIX contribution this cycle.');
                try { await redis.set('ares:alert:vix_unavailable', String(Date.now()), 'EX', 600); } catch(_) {}
                data.vix_source = 'UNAVAILABLE';
                // leave data.vix as null (score contribution from VIX is 0 in calculatePolygonScore)
            }
        }
        
        // ── Options regime (PCR + 25Δ IV skew) via computeOptionsSnapshot ── [ARES A1-β PATCH 20260507]
        // Replaces the legacy single-page /v3/snapshot/options/SPY block.
        // pcr/ivSkew may be null → calculatePolygonScore awards 0 (not 0.1).
        try {
            const opt = await fetchSPYOptionsRegime(redis, CONFIG.api.polygon);
            data.putCallRatio = opt.pcr;
            data.ivSkew       = opt.ivSkew;
            console.log(`[Options] pcr=${opt.pcr} ivSkew=${opt.ivSkew} atmMidIV=${opt.atmMidIV} n=${opt.sampleSize} src=${opt.source}`);
        } catch (e) {
            console.warn('[Options] Regime fetch error:', e.message);
            data.putCallRatio = null;
            data.ivSkew = null;
        }
        
        // ── Gateway: VIX term structure ──
        const gwTerm = await GW.vixTermStructure();
        if (gwTerm && gwTerm.ratio) {
            data.vixTermStructure = gwTerm.ratio;
            console.log(`[VIX] Term structure from gateway: ${gwTerm.ratio}`);
        } else {
            // Direct fallback
            try {
                const vix3mResponse = await fetchWithTimeout(
                    `https://api.polygon.io/v2/aggs/ticker/VXZ/prev?apiKey=${CONFIG.api.polygon}`
                );
                if (vix3mResponse.ok) {
                    const vix3mData = await vix3mResponse.json();
                    if (vix3mData.results && vix3mData.results[0] && data.vix) {
                        const vxz_raw = vix3mData.results[0].c;
                        const vix3m = vxz_raw * 0.854;
                        data.vixTermStructure = data.vix / vix3m;
                    }
                }
            } catch(e) { console.warn('[VIX3M] Direct fallback failed:', e.message); }
        }
        
        data.score = calculatePolygonScore(data);
    } catch (error) {
        console.error('[Polygon] Error:', error.message);
    }
    
    return data;
}

function calculatePolygonScore(data) {
    let score = 0;
    
    if (data.vix !== null && isFinite(data.vix)) {
        // [R-003] 연속 VIX 점수: VIX 15 기준, 최대 0.4점
        const vixScore = Math.min(0.4, Math.max(0, (data.vix - 15) / 75));
        score += vixScore;
    }
    
    // [ARES A1-β PATCH 20260507] PCR/IV-skew score: explicit null/Infinity/NaN → +0
    if (data.putCallRatio !== null && data.putCallRatio !== undefined && Number.isFinite(data.putCallRatio)) {
        if (data.putCallRatio >= 1.5)      score += 0.3;
        else if (data.putCallRatio >= 1.2) score += 0.2;
        else if (data.putCallRatio >= 1.0) score += 0.1;
    }
    if (data.ivSkew != null && Number.isFinite(data.ivSkew)) {
        if (data.ivSkew >= 0.10)      score += 0.2;
        else if (data.ivSkew >= 0.05) score += 0.1;
    }
    
    if (data.vixTermStructure !== null && data.vixTermStructure > 1) {
        score += 0.1;
    }
    
    return Math.min(score, 1);
}

async function fetchFredData() {
    const data = { yieldCurve: null, creditSpread: null, fedFundsRate: null, score: 0 };
    try {
        // ── Gateway: Macro series (FRED → Cache) ──
        const [gwT10Y2Y, gwBAML, gwFED] = await Promise.all([
            GW.macro('T10Y2Y'),
            GW.macro('BAMLC0A0CM'),
            GW.macro('FEDFUNDS')
        ]);

        if (gwT10Y2Y) { data.yieldCurve = gwT10Y2Y.value; console.log(`[FRED] T10Y2Y: ${data.yieldCurve}`); }
        if (gwBAML) { data.creditSpread = gwBAML.value; console.log(`[FRED] Credit Spread: ${data.creditSpread}`); }
        if (gwFED) { data.fedFundsRate = gwFED.value; console.log(`[FRED] Fed Funds: ${data.fedFundsRate}`); }

        data.score = calculateFredScore(data);
    } catch (error) {
        console.error('[FRED] Error:', error.message);
    }
    return data;
}

function calculateFredScore(data) {
    // [FP-FIX-v3.1] Root cause: was referencing data.yieldSpread (undefined)
    // but fetchFredData stores as data.yieldCurve. Also added fedFundsRate scoring.
    let score = 0;
    
    // T10Y2Y yield curve (stored as data.yieldCurve)
    if (data.yieldCurve !== null && data.yieldCurve !== undefined) {
        if (data.yieldCurve < -0.5) score += 0.5;       // Deep inversion = high risk
        else if (data.yieldCurve < 0) score += 0.4;      // Inverted
        else if (data.yieldCurve < 0.5) score += 0.2;    // Flat
        console.log(`[FRED] yieldCurve score component: ${data.yieldCurve} → +${score}`);
    }
    
    // BAML credit spread
    if (data.creditSpread !== null && data.creditSpread !== undefined) {
        const csScore = data.creditSpread >= 3.0 ? 0.5 :
                        data.creditSpread >= 2.0 ? 0.3 :
                        data.creditSpread >= 1.5 ? 0.2 : 0;
        score += csScore;
        console.log(`[FRED] creditSpread score component: ${data.creditSpread} → +${csScore}`);
    }
    
    // Fed Funds Rate (new: higher rate = tighter policy = more risk)
    if (data.fedFundsRate !== null && data.fedFundsRate !== undefined) {
        const ffScore = data.fedFundsRate >= 5.0 ? 0.3 :
                        data.fedFundsRate >= 4.0 ? 0.2 :
                        data.fedFundsRate >= 3.0 ? 0.1 : 0;
        score += ffScore;
        console.log(`[FRED] fedFundsRate score component: ${data.fedFundsRate} → +${ffScore}`);
    }
    
    const finalScore = Math.min(score, 1);
    console.log(`[FRED] Final score: ${finalScore}`);
    return finalScore;
}

async function fetchAlphaVantageData() {
    const data = { rsi: null, macd: null, macdSignal: null, bbPosition: null, score: 0 };
    try {
        // ── Gateway: Technical indicators (AV → Cache) ──
        const [gwRSI, gwMACD] = await Promise.all([
            GW.technical('SPY', 'RSI'),
            GW.technical('SPY', 'MACD')
        ]);

        if (gwRSI) { data.rsi = gwRSI.value; console.log(`[AV] RSI: ${data.rsi}`); }
        if (gwMACD) {
            data.macd = gwMACD.value;
            // MACD signal from raw data if available
            console.log(`[AV] MACD: ${data.macd}`);
        }

        data.score = calculateAlphaScore(data);
    } catch (error) {
        console.error('[AlphaVantage] Error:', error.message);
    }
    return data;
}

function calculateAlphaScore(data) {
    let score = 0;
    
    if (data.rsi !== null) {
        if (data.rsi <= 30) score += 0.3;
        else if (data.rsi <= 40) score += 0.2;
        else if (data.rsi >= 70) score += 0.1;
    }
    
    if (data.macd !== null && data.macdSignal !== null) {
        if (data.macd < data.macdSignal && data.macd < 0) score += 0.4;
        else if (data.macd < data.macdSignal) score += 0.2;
    }
    
    return Math.min(score, 1);
}

async function fetchNewsData() {
    const data = { sentiment: 0, crisisKeywords: 0, score: 0 };

    const crisisKeywords = [
        'crash', 'crisis', 'recession', 'collapse', 'panic',
        'bear market', 'sell-off', 'plunge', 'tumble', 'fear'
    ];

    try {
        // ── Gateway: News sentiment (Tavily → NewsAPI → GNews → Cache) ──
        const gwNews = await GW.news();
        if (gwNews && gwNews.results) {
            let keywordCount = 0;
            gwNews.results.forEach(result => {
                const text = ((result.title || '') + ' ' + (result.content || result.description || '')).toLowerCase();
                crisisKeywords.forEach(keyword => {
                    if (text.includes(keyword)) keywordCount++;
                });
            });
            data.crisisKeywords = keywordCount;
            console.log(`[News] Gateway (${gwNews.source || 'cached'}): ${keywordCount} crisis keywords`);
        }

        data.score = calculateNewsScore(data);
    } catch (error) {
        console.error('[News] Error:', error.message);
    }
    return data;
}

function calculateNewsScore(data) {
    if (data.crisisKeywords >= 15) return 0.8;
    if (data.crisisKeywords >= 10) return 0.6;
    if (data.crisisKeywords >= 5) return 0.4;
    if (data.crisisKeywords >= 2) return 0.2;
    return 0;
}

async function fetchSecData() {
    const data = {
        institutionalSelling: false,
        score: 0
    };
    
    try {
        data.score = 0.0;  // [R-002] 고정값 제거: 실제 데이터 없으므로 0
    } catch (error) {
        console.error('[SEC] Error:', error.message);
    }
    
    return data;
}

// ============================================
// Regime Determination
// ============================================

function determineRegime(weightedScore) {
    if (weightedScore >= CONFIG.regimeThresholds.crisis) {
        return 'CRISIS';
    } else if (weightedScore >= CONFIG.regimeThresholds.caution) {
        return 'CAUTION';
    } else if (weightedScore >= CONFIG.regimeThresholds.normal) {
        return 'NORMAL';
    } else {
        return 'BULLISH';
    }
}

function getStrategyParameters(regime) {
    switch (regime) {
        case 'CRISIS':
            return {
                volScale: 0.5,
                hedgeRatio: 0.4,
                maxLeverage: 0.5,
                crisisMode: true
            };
        case 'CAUTION':
            return {
                volScale: 0.7,
                hedgeRatio: 0.2,
                maxLeverage: 1.0,
                crisisMode: false
            };
        case 'NORMAL':
            return {
                volScale: 1.0,
                hedgeRatio: 0.01,
                maxLeverage: 2.0,
                crisisMode: false
            };
        case 'BULLISH':
        default:
            return {
                volScale: 1.2 + (0.3 * (1 - 0)),
                hedgeRatio: 0,
                maxLeverage: 2.5,
                crisisMode: false
            };
    }
}

// ============================================
// Champion-Challenger Combiner
// ============================================

async function runCombiner(redis, msRegime, msScore) {
    // 1) Load R15 (Champion)
    const r15 = await loadHash(redis, 'regime:r15:current');
    const r15Mult = r15 ? toNum(r15.band_multiplier ?? r15.mult, 1) : 1; // [PATCH-R15-FIELDNAME] 양방향 폴백
    const r15Regime = r15 ? (r15.regime || r15.state || 'UNKNOWN') : 'UNKNOWN'; // [PATCH-R15-FIELDNAME] 양방향 폴백
    
    // 2) Load Gate State
    let gate = await loadHash(redis, 'regime:gate');
    if (!gate) {
        gate = { state: 'NO_GATE', cautionStreak: '0', crisisStreak: '0', clearStreak: '0' };
    }
    
    let gateState = gate.state || 'NO_GATE';
    let cautionStreak = toNum(gate.cautionStreak, 0);
    let crisisStreak = toNum(gate.crisisStreak, 0);
    let clearStreak = toNum(gate.clearStreak, 0);
    
    // 3) Update Streaks
    const isCrisis = msRegime === 'CRISIS';
    const isCaution = msRegime === 'CAUTION' || msRegime === 'CRISIS';
    const isNormal = msRegime === 'NORMAL' || msRegime === 'BULLISH';
    
    if (isCrisis) {
        crisisStreak++;
        cautionStreak++;
        clearStreak = 0;
    } else if (isCaution) {
        cautionStreak++;
        crisisStreak = 0;
        clearStreak = 0;
    } else {
        clearStreak++;
        cautionStreak = 0;
        crisisStreak = 0;
    }
    
    // 4) State Transition
    const prevGateState = gateState;
    
    if (gateState === 'NO_GATE') {
        if (crisisStreak >= COMBINE_CFG.enterCrisisCount) {
            gateState = 'CRISIS_GATE';
        } else if (cautionStreak >= COMBINE_CFG.enterCautionCount) {
            gateState = 'CAUTION_GATE';
        }
    } else if (gateState === 'CAUTION_GATE') {
        if (crisisStreak >= COMBINE_CFG.enterCrisisCount) {
            gateState = 'CRISIS_GATE';
        } else if (clearStreak >= COMBINE_CFG.clearCount) {
            gateState = 'NO_GATE';
        }
    } else if (gateState === 'CRISIS_GATE') {
        if (clearStreak >= COMBINE_CFG.clearCount) {
            gateState = 'NO_GATE';
        }
    }
    
    // 5) Calculate Final Multiplier
    const gateMult = COMBINE_CFG.gateMult[gateState] || 1;
    const finalMult = Math.min(r15Mult, gateMult);
    
    // 6) Save Gate State
    await redis.hSet('regime:gate', {
        state: gateState,
        cautionStreak: String(cautionStreak),
        crisisStreak: String(crisisStreak),
        clearStreak: String(clearStreak),
        timestamp: new Date().toISOString()
    });
    
    // 7) Load Previous Final State
    const prevFinal = await loadHash(redis, 'regime:final:current');
    const prevFinalMult = prevFinal ? toNum(prevFinal.final_mult, null) : null;
    const prevFinalGateState = prevFinal ? prevFinal.gate_state : null;
    
    // 8) Save Final State
    const finalState = {
        r15_regime: r15Regime,
        r15_mult: String(r15Mult),
        ms_regime: msRegime,
        ms_score: String(msScore),
        gate_state: gateState,
        gate_mult: String(gateMult),
        final_mult: String(finalMult),
        timestamp: new Date().toISOString()
    };
    
    // GPU Regime Direct Integration
    const gpuResult = await readGpuRiskOff(redis);
    if (gpuResult) {
        finalState.gpu_regime = gpuResult.regime;
        finalState.gpu_confidence = String(gpuResult.top_pct);
        finalState.gpu_risk_off_pct = String(gpuResult.risk_off_pct);
        finalState.gpu_oos_auc = String(gpuResult.oos_auc);
        finalState.gpu_source = gpuResult.source;
        
        // GPU gate override: GPU가 CAUTION/CRISIS를 감지하면 gate_state를 override
        if (gpuResult.gate_override) {
            const currentGateSeverity = {'NORMAL': 0, 'CAUTION': 1, 'CRISIS': 2}[finalState.gate_state] || 0;
            const gpuGateSeverity = {'CAUTION': 1, 'CRISIS': 2}[gpuResult.gate_override] || 0;
            
            // GPU가 더 보수적(위험 감지)이면 override
            if (gpuGateSeverity > currentGateSeverity) {
                finalState.gate_state = gpuResult.gate_override;
                finalState.gate_mult = String({'CAUTION': 0.5, 'CRISIS': 0.0}[gpuResult.gate_override] || 1);
                finalState.gpu_gate_override = 'true';
            }
        }
    }
    
    await redis.hSet('regime:final:current', finalState);
    
    // 9) Push to History (only if final_mult OR gate_state changed)
    const multChanged = prevFinalMult !== finalMult;
    const gateChanged = prevFinalGateState !== gateState;
    
    if (multChanged || gateChanged) {
        const historyEntry = {
            ...finalState,
            change_reason: multChanged && gateChanged ? 'mult_and_gate' : (multChanged ? 'mult_change' : 'gate_change')
        };
        await redis.lPush('regime:final:history', JSON.stringify(historyEntry));
        await redis.lTrim('regime:final:history', 0, 999);
        
        console.log(`📜 History push: final_mult=${finalMult}, gate_state=${gateState}, reason=${historyEntry.change_reason}`);
    }
    
    return { r15Regime, r15Mult, gateState, gateMult, finalMult };
}

// ============================================
// Main Loop
// ============================================

async function main() {
    console.log('🚀 Multi-Source Regime Detector v2.0 Starting...');
    console.log(`🧷 Legacy dual-write: ${LEGACY_WRITE ? 'ON' : 'OFF'} (LEGACY_WRITE=${process.env.LEGACY_WRITE ?? 'not set'})`);
    console.log('');
    
    const redis = createClient({ url: CONFIG.redis.url , socket: { reconnectStrategy: (retries) => Math.min(retries * 500, 30000) } });
    
    redis.on('error', (err) => console.error('Redis Error:', err));
    
    await redis.connect();
    console.log('✅ Connected to Redis');
    
    let previousRegime = null;
    
    async function update() {
        console.log('\n' + '='.repeat(50));
        console.log(`📊 Fetching data at ${new Date().toISOString()}`);
        console.log('='.repeat(50));
        
        const [polygonData, fredData, alphaData, newsData, secData] = await Promise.all([
            fetchPolygonData(redis),
            fetchFredData(),
            fetchAlphaVantageData(),
            fetchNewsData(),
            fetchSecData()
        ]);
        
        const weightedScore = 
            (polygonData.score * CONFIG.weights.polygon) +
            (fredData.score * CONFIG.weights.fred) +
            (alphaData.score * CONFIG.weights.alphaVantage) +
            (newsData.score * CONFIG.weights.news) +
            (secData.score * CONFIG.weights.sec);
        
        let regime = determineRegime(weightedScore);
        const override = await applyPortfolioOverride(redis, regime);
        if (override.overridden) regime = override.regime;
        const params = getStrategyParameters(regime);
        
        console.log('\n📈 Source Scores:');
        console.log(`  Polygon (VIX/Options): ${(polygonData.score * 100).toFixed(1)}%`);
        console.log(`    - VIX: ${polygonData.vix?.toFixed(2) || 'N/A'}`);
        console.log(`    - Put/Call Ratio: ${polygonData.putCallRatio?.toFixed(2) || 'N/A'}`);
        console.log(`  FRED (Macro): ${(fredData.score * 100).toFixed(1)}%`);
        console.log(`    - Yield Spread: ${fredData.yieldSpread?.toFixed(2) || 'N/A'}%`);
        console.log(`    - Credit Spread: ${fredData.creditSpread?.toFixed(2) || 'N/A'}%`);
        console.log(`  Alpha Vantage (Technical): ${(alphaData.score * 100).toFixed(1)}%`);
        console.log(`    - RSI: ${alphaData.rsi?.toFixed(1) || 'N/A'}`);
        console.log(`  News (Sentiment): ${(newsData.score * 100).toFixed(1)}%`);
        console.log(`    - Crisis Keywords: ${newsData.crisisKeywords}`);
        console.log(`  SEC (Institutional): ${(secData.score * 100).toFixed(1)}%`);
        
        console.log(`\n📈 Weighted Risk Score: ${(weightedScore * 100).toFixed(1)}%`);
        console.log(`\n🎯 Current Regime: ${regime}`);
        console.log('📊 Strategy Parameters:');
        console.log(`  - Vol Scale: ${params.volScale.toFixed(2)}x`);
        console.log(`  - Hedge Ratio: ${(params.hedgeRatio * 100).toFixed(1)}%`);
        console.log(`  - Max Leverage: ${params.maxLeverage}x`);
        console.log(`  - Crisis Mode: ${params.crisisMode ? 'ON' : 'OFF'}`);
        
        // Save to Redis (신규 키: regime:ms:*)
        const regimeData = {
            regime,
            score: weightedScore.toFixed(4),
            volScale: params.volScale.toFixed(2),
            hedgeRatio: params.hedgeRatio.toFixed(4),
            maxLeverage: String(params.maxLeverage),
            crisisMode: params.crisisMode ? '1' : '0',
            timestamp: new Date().toISOString()
        };
        
        // 신규 키 (단일 진실)
        await redis.hSet('regime:ms:current', regimeData);
        
        // 호환 레이어 (dual-write)
        if (LEGACY_WRITE) {
            await redis.hSet('regime:current', regimeData);
        }
        
        // Save source details (신규 키: regime:ms:sources)
        const sourcesData = {
            polygon_score: polygonData.score.toFixed(4),
            polygon_vix: String(polygonData.vix ? polygonData.vix.toFixed(2) : ''),
            polygon_vix_source: String(polygonData.vix_source || 'UNKNOWN'),
            polygon_vixy: String(polygonData.vixy_raw || ''),
            polygon_pcr: String(polygonData.putCallRatio || ''),
            fred_score: fredData.score.toFixed(4),
            fred_yield: String(fredData.yieldCurve || ''),
            fred_credit: String(fredData.creditSpread || ''),
            alpha_score: alphaData.score.toFixed(4),
            alpha_rsi: String(alphaData.rsi || ''),
            news_score: newsData.score.toFixed(4),
            news_keywords: String(newsData.crisisKeywords),
            sec_score: secData.score.toFixed(4),
            timestamp: new Date().toISOString()
        };
        
        // 신규 키 (단일 진실)
        await redis.hSet('regime:ms:sources', sourcesData);
        
        // 호환 레이어 (dual-write)
        if (LEGACY_WRITE) {
            await redis.hSet('regime:sources', sourcesData);
        }
        
        // Record regime change (신규 키: regime:ms:history)
        if (regime !== previousRegime) {
            const historyEntry = JSON.stringify({
                from: previousRegime,
                to: regime,
                score: weightedScore,
                timestamp: new Date().toISOString()
            });
            
            // 신규 키 (단일 진실)
            await redis.lPush('regime:ms:history', historyEntry);
            await redis.lTrim('regime:ms:history', 0, 99);
            
            // 호환 레이어 (dual-write)
            if (LEGACY_WRITE) {
                await redis.lPush('regime:history', historyEntry);
                await redis.lTrim('regime:history', 0, 99);
            }
            
            console.log(`\n⚠️ REGIME CHANGE: ${previousRegime || 'INIT'} → ${regime}`);
            previousRegime = regime;
        }
        
        // Run Combiner
        const combineResult = await runCombiner(redis, regime, weightedScore);
        
        console.log('\n🔗 Combiner Result:');
        console.log(`  R15: ${combineResult.r15Regime} (mult=${combineResult.r15Mult})`);
        console.log(`  MS:  ${regime} (score=${(weightedScore * 100).toFixed(1)}%)`);
        console.log(`  Gate: ${combineResult.gateState} (gate_mult=${combineResult.gateMult})`);
        console.log(`  FINAL mult: ${combineResult.finalMult}`);
        
        console.log('\n✅ Saved to Redis');
    }
    
    await update();
    
    
    // [HEALTH-STD 2026-05-06] standardized health key for unified watchdog
    const _publishHealth = async () => {
      try {
        await redis.set(
          'ctx:multi-source-regime-detector:health',
          JSON.stringify({ ts: Date.now(), status: 'OK', service: 'multi-source-regime-detector' }),
          { EX: 180 }
        );
      } catch (_) { /* never crash main loop on health publish */ }
    };
    _publishHealth();
    setInterval(_publishHealth, 30000);

    const intervalId = setInterval(update, CONFIG.updateInterval);
    
    process.on('SIGINT', async () => {
        console.log('\n🛑 Shutting down...');
        clearInterval(intervalId);
        await redis.quit();
        /* process.exit(0) removed for PM2 */ return;
    });
    
    process.on('SIGTERM', async () => {
        console.log('\n🛑 Shutting down...');
        clearInterval(intervalId);
        await redis.quit();
        /* process.exit(0) removed for PM2 */ return;
    });
}

main().catch(console.error);

// ============================================================
// Portfolio Reality Check (패치 추가)
// ============================================================
const PORTFOLIO_OVERRIDES = {
  FORCE_DEFENSIVE_PNL: -2.0,
  FORCE_CRISIS_PNL: -5.0,
  HEAVY_LOSS_STOCK_THRESHOLD: -5.0,
  HEAVY_LOSS_STOCK_COUNT: 3,
};

async function applyPortfolioOverride(redis, regime) {
  try {
    // FIX-WRONGTYPE: emarkos:v1:positions is a hash, not string
    const posType = await redis.type("emarkos:v1:positions");
    let positionsStr = null;
    if (posType === "hash") {
      const allPos = await redis.hGetAll("emarkos:v1:positions");
      if (allPos) {
        const positions = {};
        for (const [k, v] of Object.entries(allPos)) {
          if (k === "__meta__") continue;
          try { positions[k] = JSON.parse(v); } catch {}
        }
        positionsStr = JSON.stringify({ positions });
      }
    } else {
      positionsStr = await redis.get("emarkos:v1:positions");
    }
    if (!positionsStr) return { regime, overridden: false };

    const positionsData = JSON.parse(positionsStr);
    const positions = positionsData.positions || {};
    
    let totalPnL = 0;
    let heavyLossCount = 0;
    const heavyLossStocks = [];

    for (const [symbol, pos] of Object.entries(positions)) {
      const pnlPct = pos.pnlPct || 0;
      totalPnL += pnlPct;
      if (pnlPct <= PORTFOLIO_OVERRIDES.HEAVY_LOSS_STOCK_THRESHOLD) {
        heavyLossCount++;
        heavyLossStocks.push({ symbol, pnlPct });
      }
    }

    const avgPnL = Object.keys(positions).length > 0 ? totalPnL / Object.keys(positions).length : 0;

    let newRegime = regime;
    let overrideReason = null;

    if (avgPnL <= PORTFOLIO_OVERRIDES.FORCE_CRISIS_PNL) {
      newRegime = "CRISIS";
      overrideReason = `Portfolio avg PnL ${avgPnL.toFixed(2)}% → FORCE CRISIS`;
    } else if (avgPnL <= PORTFOLIO_OVERRIDES.FORCE_DEFENSIVE_PNL) {
      if (regime === "BULLISH" || regime === "NORMAL") {
        newRegime = "CAUTION";
        overrideReason = `Portfolio avg PnL ${avgPnL.toFixed(2)}% → FORCE CAUTION`;
      }
    }

    if (heavyLossCount >= PORTFOLIO_OVERRIDES.HEAVY_LOSS_STOCK_COUNT) {
      if (newRegime === "BULLISH" || newRegime === "NORMAL") {
        newRegime = "CAUTION";
        overrideReason = `${heavyLossCount} stocks below ${PORTFOLIO_OVERRIDES.HEAVY_LOSS_STOCK_THRESHOLD}%`;
      }
    }

    if (overrideReason) {
      console.log(`\n⚠️ [PORTFOLIO_OVERRIDE] ${regime} → ${newRegime}: ${overrideReason}`);
    }

    return { regime: newRegime, overridden: !!overrideReason, reason: overrideReason };
  } catch (e) {
    console.error("[PORTFOLIO_OVERRIDE] Error:", e.message);
    return { regime, overridden: false };
  }
}
