/**
 * position-sync-service v3.1.0
 * 
 * [STRUCTURAL_FIX_S1] 포지션 SSOT 아키텍처:
 * 
 * 운영 룰:
 *   kis:broker:positions = 외부 진실 (hash, kis-balance-sync 30초 갱신)
 *   emarkos:v1:positions = 내부 기대 상태 (JSON, kis-balance-sync 동일 API)
 *   alarm 기준 = broker vs internal diff != 0
 *   kis:live:positions = deprecated, 2단계 폐기 (tombstone → read hit 감지 → 삭제)
 * 
 * 2단계 폐기 절차:
 *   Phase 1: write 금지 + tombstone 마커 설정 + read hit 카운터
 *   Phase 2: 24~48h read hit 0 확인 후 DEL
 */
import Redis from 'ioredis';

import { scanKeysArray } from "file:///home/ubuntu/ares_current/lib/ares-runtime/redis-scan-utils.mjs";
const REDIS_URL = (process.env.REDIS_URL || (() => { throw new Error('REDIS_URL not set (SSOT /etc/ares/redis.env required)'); })());
if (!process.env.REDIS_URL) console.warn('[WARN] position-sync-service: REDIS_URL not set, using localhost fallback');
const redis = new Redis(REDIS_URL, {
    retryStrategy: (times) => Math.min(times * 500, 30000),
    maxRetriesPerRequest: null,
    enableReadyCheck: true,
    tls: REDIS_URL.startsWith('rediss://') ? { rejectUnauthorized: false } : undefined
});

redis.on('error', (e) => console.error(`[${ts()}] [REDIS_ERROR]`, e.message));
redis.on('connect', () => console.log(`[${ts()}] [REDIS_CONNECTED]`));

const CONFIG = {
    SYNC_INTERVAL: 60000,
    MISMATCH_THRESHOLD_SHARES: 0,
    MAX_CONSECUTIVE_FAILURES: 5,
    HEARTBEAT_INTERVAL: 30000,
    TELEGRAM_COOLDOWN: 300000,
    // [STRUCTURAL_FIX_S1] SSOT 키 정의
    BROKER_KEY: "kis:broker:positions",
    INTERNAL_KEY: "emarkos:v1:positions",
    DEPRECATED_KEY: "kis:live:positions",
    DEPRECATED_TOMBSTONE_KEY: "kis:live:positions:tombstone",
    DEPRECATED_READ_HIT_KEY: "kis:live:positions:read_hits",
    DEPRECATION_SAFE_HOURS: 48,
};

let consecutiveFailures = 0;
let lastSyncSuccess = Date.now();
let syncRunning = false;
let lastTelegramAlert = 0;

function ts() { return new Date().toISOString(); }

function structLog(level, event, data = {}) {
    console.log(JSON.stringify({
        ts: ts(), level, component: "position-sync-service", version: "v3.1.0", event, ...data
    }));
}

// ============================================================
// Heartbeat
// ============================================================
async function updateHeartbeat() {
    try {
        await redis.set("positions:sync:heartbeat", ts());
        await redis.set("positions:sync:pid", process.pid.toString());
    } catch(e) {
        structLog("ERROR", "heartbeat_fail", { error: e.message });
    }
}

// ============================================================
// [STRUCTURAL_FIX_S1] 2단계 폐기: kis:live:positions
// Phase 1: tombstone 설정 + read hit 카운터 초기화
// Phase 2: 48h 후 read hit 0이면 삭제
// ============================================================
async function manageDeprecatedKey() {
    try {
        const tombstone = await redis.get(CONFIG.DEPRECATED_TOMBSTONE_KEY);
        
        if (!tombstone) {
            // Phase 1 시작: tombstone 설정
            const exists = await redis.exists(CONFIG.DEPRECATED_KEY);
            if (exists) {
                await redis.set(CONFIG.DEPRECATED_TOMBSTONE_KEY, JSON.stringify({
                    deprecated_at: ts(),
                    reason: "STRUCTURAL_FIX_S1: stale data, replaced by kis:broker:positions",
                    replacement: CONFIG.BROKER_KEY,
                    phase: "tombstone_active",
                    safe_delete_after_hours: CONFIG.DEPRECATION_SAFE_HOURS
                }));
                await redis.set(CONFIG.DEPRECATED_READ_HIT_KEY, "0");
                structLog("INFO", "deprecated_key_tombstone_set", {
                    key: CONFIG.DEPRECATED_KEY,
                    phase: "Phase 1: tombstone active, monitoring read hits"
                });
            }
            return;
        }
        
        // Phase 2 체크: 48h 경과 + read hit 0이면 삭제
        const meta = JSON.parse(tombstone);
        const deprecatedAt = new Date(meta.deprecated_at);
        const hoursElapsed = (Date.now() - deprecatedAt.getTime()) / (1000 * 60 * 60);
        const readHits = parseInt(await redis.get(CONFIG.DEPRECATED_READ_HIT_KEY) || "0");
        
        if (hoursElapsed >= CONFIG.DEPRECATION_SAFE_HOURS && readHits === 0) {
            // Phase 2 완료: 안전하게 삭제
            await redis.del(CONFIG.DEPRECATED_KEY);
            await redis.set(CONFIG.DEPRECATED_TOMBSTONE_KEY, JSON.stringify({
                ...meta,
                phase: "deleted",
                deleted_at: ts(),
                hours_monitored: hoursElapsed.toFixed(1),
                read_hits_at_deletion: readHits
            }));
            structLog("INFO", "deprecated_key_deleted", {
                key: CONFIG.DEPRECATED_KEY,
                hours_monitored: hoursElapsed.toFixed(1),
                read_hits: readHits
            });
        } else {
            structLog("INFO", "deprecated_key_monitoring", {
                key: CONFIG.DEPRECATED_KEY,
                hours_elapsed: hoursElapsed.toFixed(1),
                required_hours: CONFIG.DEPRECATION_SAFE_HOURS,
                read_hits: readHits,
                phase: hoursElapsed < CONFIG.DEPRECATION_SAFE_HOURS ? "waiting_for_safe_period" : "read_hits_nonzero"
            });
            // read hit 카운터 리셋 (다음 48h 윈도우)
            if (readHits > 0 && hoursElapsed >= CONFIG.DEPRECATION_SAFE_HOURS) {
                await redis.set(CONFIG.DEPRECATED_READ_HIT_KEY, "0");
                await redis.set(CONFIG.DEPRECATED_TOMBSTONE_KEY, JSON.stringify({
                    ...meta,
                    deprecated_at: ts(),
                    phase: "tombstone_active_retry",
                    previous_read_hits: readHits,
                    retry_reason: "read hits detected, restarting safe period"
                }));
                structLog("WARN", "deprecated_key_retry", {
                    key: CONFIG.DEPRECATED_KEY,
                    previous_read_hits: readHits,
                    reason: "Restarting 48h safe period due to active readers"
                });
            }
        }
    } catch (e) {
        structLog("WARN", "deprecated_key_management_fail", { error: e.message });
    }
}

// ============================================================
// [STRUCTURAL_FIX_S1] Broker 포지션 로더 — kis:broker:positions (hash)
// ============================================================
async function getBrokerPositions() {
    const raw = await redis.hgetall(CONFIG.BROKER_KEY);
    if (!raw || Object.keys(raw).length === 0) {
        structLog("WARN", "broker_positions_empty", { key: CONFIG.BROKER_KEY });
        return {};
    }
    
    try {
        const result = {};
        for (const [sym, jsonStr] of Object.entries(raw)) {
            try {
                const parsed = JSON.parse(jsonStr);
                result[sym] = parseInt(parsed.qty || parsed.shares || parsed.hldg_qty || 0);
            } catch {
                result[sym] = parseInt(jsonStr) || 0;
            }
        }
        
        structLog("INFO", "broker_positions_loaded", { 
            key: CONFIG.BROKER_KEY, count: Object.keys(result).length 
        });
        return result;
    } catch (e) {
        structLog("ERROR", "broker_positions_parse_fail", { key: CONFIG.BROKER_KEY, error: e.message });
        return {};
    }
}

// ============================================================
// [STRUCTURAL_FIX_V2] 내부 포지션 로더 — emarkos:v1:positions (STRING/JSON)
// ============================================================
// 근본 수정: 시스템 전체에서 이 키는 STRING(JSON) 타입으로 사용됨.
// kis-balance-sync.mjs가 redis.set()으로 쓰고, orchestrator.py/equity-calculator/
// watchdog-monitor/go-nogo-judge 등 모든 서비스가 redis.get()으로 읽음.
// position-sync-service만 HGETALL을 시도하던 것이 근본 원인이었음.
// ============================================================
async function getInternalPositions() {
    let raw = null;
    try {
        raw = await redis.get(CONFIG.INTERNAL_KEY);
    } catch(err) {
        structLog("ERROR", "internal_positions_get_fail", { key: CONFIG.INTERNAL_KEY, error: err.message });
        return {};
    }
    if (!raw) {
        structLog("WARN", "internal_positions_empty", { key: CONFIG.INTERNAL_KEY });
        return {};
    }

    try {
        const snapshot = JSON.parse(raw);
        // kis-balance-sync 형식: { asOf, count, positions: { SYM: { shares, avgPrice, marketValue, ... } } }
        const posObj = snapshot.positions || snapshot;
        if (!posObj || typeof posObj !== "object") {
            structLog("WARN", "internal_positions_invalid_format", { key: CONFIG.INTERNAL_KEY, type: typeof posObj });
            return {};
        }

        const result = {};
        for (const [sym, val] of Object.entries(posObj)) {
            if (sym === '__meta__' || sym === 'asOf' || sym === 'count' || sym === 'totalMarketValue') continue;
            if (typeof val === "object" && val !== null) {
                result[sym] = parseInt(val.shares || val.qty || val.quantity || 0);
            } else if (typeof val === "string") {
                try {
                    const parsed = JSON.parse(val);
                    result[sym] = parseInt(parsed.shares || parsed.qty || parsed.quantity || 0);
                } catch(e) {
                    if (!isNaN(parseInt(val))) result[sym] = parseInt(val);
                }
            } else if (typeof val === "number") {
                result[sym] = parseInt(val);
            }
        }
        
        structLog("INFO", "internal_positions_loaded", { 
            key: CONFIG.INTERNAL_KEY, count: Object.keys(result).length 
        });
        return result;
    } catch (e) {
        structLog("ERROR", "internal_positions_parse_fail", { key: CONFIG.INTERNAL_KEY, error: e.message });
        return {};
    }
}

// ============================================================
// 텔레그램 알림
// ============================================================
async function sendTelegramAlert(title, lines) {
    if (Date.now() - lastTelegramAlert < CONFIG.TELEGRAM_COOLDOWN) return;
    
    try {
        const botToken = await redis.get("telegram:bot_token");
        const chatId = await redis.get("telegram:chat_id");
        if (!botToken || !chatId) return;
        
        const text = `${title}\n\n${lines.join('\n')}\n\n시각: ${ts()}`;
        const url = `https://api.telegram.org/bot${botToken}/sendMessage`;
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_id: chatId, text, parse_mode: 'HTML' })
        });
        
        if (resp.ok) {
            lastTelegramAlert = Date.now();
            structLog("INFO", "telegram_alert_sent", { title });
        }
    } catch (e) {
        structLog("WARN", "telegram_alert_fail", { error: e.message });
    }
}

// ============================================================
// [STRUCTURAL_FIX_S1] 포지션 동기화 — broker hash vs internal JSON
// ============================================================

// [FORCE_SYNC] Check and cleanup forced resync keys
async function checkAndCleanForcedPositionKeys() {
  try {
    const keys = await scanKeysArray(redis, "ares:ops:force_sync:positions:*");
    if (!keys || keys.length === 0) return { forced: false, keys: [] };
    structLog("INFO", "force_sync_position_requested", { keys });
    return { forced: true, keys };
  } catch (_) {
    return { forced: false, keys: [] };
  }
}

async function cleanupForcedPositionKeys(keys) {
  if (!keys || keys.length === 0) return;
  try {
    const pipe = redis.pipeline();
    for (const k of keys) {
      const symbol = String(k).split(":").pop();
      pipe.del(k);
      pipe.set(`ares:ops:force_sync:ack:positions:${symbol}`, new Date().toISOString(), "EX", 600);
    }
    await pipe.exec();
    structLog("INFO", "force_sync_position_cleanup_done", { keys });
  } catch (e) {
    structLog("WARN", "force_sync_position_cleanup_error", { error: String(e) });
  }
}

async function syncPositions() {
    if (syncRunning) {
        structLog("WARN", "sync_skip_already_running");
        return;
    }
    syncRunning = true;
    const forceSync = await checkAndCleanForcedPositionKeys();
    
    try {
        await updateHeartbeat();
        await manageDeprecatedKey();
        structLog("INFO", "sync_start");
        
        const brokerPos = await getBrokerPositions();
        const internalPos = await getInternalPositions();
        
        const brokerCount = Object.keys(brokerPos).length;
        const internalCount = Object.keys(internalPos).length;
        
        if (brokerCount === 0 && internalCount === 0) {
            structLog("WARN", "sync_skip_both_empty");
            consecutiveFailures = 0;
            lastSyncSuccess = Date.now();
            setTimeout(syncPositions, CONFIG.SYNC_INTERVAL);
            return;
        }
        
        const allSymbols = new Set([...Object.keys(brokerPos), ...Object.keys(internalPos)]);
        const mismatches = [];
        const matched = [];
        
        for (const symbol of allSymbols) {
            const bQty = brokerPos[symbol] || 0;
            const iQty = internalPos[symbol] || 0;
            const diff = bQty - iQty;
            
            if (Math.abs(diff) > CONFIG.MISMATCH_THRESHOLD_SHARES) {
                mismatches.push({
                    symbol,
                    internal_qty: iQty,
                    broker_qty: bQty,
                    diff,
                    type: iQty === 0 ? "broker_only" : bQty === 0 ? "internal_only" : "qty_mismatch"
                });
            } else {
                matched.push(symbol);
            }
        }
        
        const syncResult = {
            ts: ts(),
            version: "v3.1.0",
            broker_key: CONFIG.BROKER_KEY,
            internal_key: CONFIG.INTERNAL_KEY,
            broker_count: brokerCount,
            internal_count: internalCount,
            total_symbols: allSymbols.size,
            matched: matched.length,
            mismatches: mismatches.length,
            mismatch_details: mismatches
        };
        
        if (mismatches.length > 0) {
            structLog("WARN", "position_mismatch_detected", {
                mismatches: mismatches.length,
                details: mismatches,
                note: "Both keys sourced from same KIS API via kis-balance-sync. Mismatch = conversion bug or race condition."
            });
            
            await redis.set('positions:mismatches', JSON.stringify(mismatches));
            await redis.lpush('system:alerts', JSON.stringify({
                timestamp: ts(),
                level: 'WARN',
                source: 'position-sync-v3.1',
                message: `포지션 불일치: ${mismatches.length}건 (${CONFIG.BROKER_KEY} vs ${CONFIG.INTERNAL_KEY})`,
                data: mismatches
            }));
            
            const alertLines = mismatches.map(m => 
                `  ${m.symbol}: broker=${m.broker_qty} internal=${m.internal_qty} (${m.diff > 0 ? '+' : ''}${m.diff})`
            );
            await sendTelegramAlert(`⚠️ POSITION MISMATCH (${mismatches.length}건)`, alertLines);
        } else {
            structLog("INFO", "sync_complete_no_mismatch", {
                broker_count: brokerCount,
                internal_count: internalCount,
                matched: matched.length
            });
            await redis.del('positions:mismatches');
        }
        
        await redis.lpush("audit:position_sync_check", JSON.stringify(syncResult));
        await redis.ltrim("audit:position_sync_check", 0, 999);
        
        const now = ts();
        await redis.set("positions:sync:real_ts", now).catch(() => {});
        await redis.hset('positions:sync:status', {
            last_sync: now,
            mismatches: mismatches.length.toString(),
            broker_positions: brokerCount.toString(),
            internal_positions: internalCount.toString(),
            total_symbols: allSymbols.size.toString(),
            matched: matched.length.toString(),
            version: "v3.1.0",
            broker_key: CONFIG.BROKER_KEY,
            internal_key: CONFIG.INTERNAL_KEY,
            status: mismatches.length > 0 ? 'MISMATCH' : 'OK'
        });
        
        await updateHeartbeat();
        consecutiveFailures = 0;
        lastSyncSuccess = Date.now();
        
        // [FORCE_SYNC] Cleanup forced resync keys after successful sync
        if (forceSync.forced) {
          await cleanupForcedPositionKeys(forceSync.keys);
        }
        
    } catch (e) {
        consecutiveFailures++;
        structLog("ERROR", "sync_error", {
            error: e.message,
            consecutive_failures: consecutiveFailures,
            max_failures: CONFIG.MAX_CONSECUTIVE_FAILURES
        });
        
        await redis.hset('positions:sync:status', {
            last_sync: ts(), status: 'ERROR', error: e.message,
            consecutive_failures: consecutiveFailures.toString()
        }).catch(() => {});
        
        if (consecutiveFailures >= CONFIG.MAX_CONSECUTIVE_FAILURES) {
            structLog("FATAL", "max_failures_reached — waiting 60s for PM2 restart", { consecutive_failures: consecutiveFailures });
            await new Promise(r => setTimeout(r, 60000));
            throw new Error("MAX_CONSECUTIVE_FAILURES reached");
        }
    } finally {
        syncRunning = false;
    }
    
    setTimeout(syncPositions, CONFIG.SYNC_INTERVAL);
}

// Watchdog
setInterval(() => {
    const elapsed = Date.now() - lastSyncSuccess;
    if (elapsed > 5 * 60 * 1000) {
        structLog("FATAL", "watchdog_timeout — forcing resync", { elapsed_sec: Math.round(elapsed / 1000) });
        consecutiveFailures = 0; // reset to allow retry
        syncPositions().catch(e => structLog("ERROR", "forced_resync_failed", { error: e?.message }));
    }
}, 60000);

// 시작
structLog("INFO", "service_start", {
    version: "v3.1.0",
    pid: process.pid,
    sync_interval_ms: CONFIG.SYNC_INTERVAL,
    broker_key: CONFIG.BROKER_KEY,
    internal_key: CONFIG.INTERNAL_KEY,
    deprecated_key: CONFIG.DEPRECATED_KEY,
    deprecation_strategy: "2-phase: tombstone → 48h read-hit monitoring → safe delete"
});

updateHeartbeat();
syncPositions();
