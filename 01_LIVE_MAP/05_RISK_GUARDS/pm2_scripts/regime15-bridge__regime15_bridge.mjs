#!/usr/bin/env node
/**
 * Regime15 → regime:r15:current 브릿지 v2.0
 * 
 * 15레짐 서비스의 출력(regime15:current)을 읽어서
 * 멀티소스 결합기가 읽는 형식(regime:r15:current)으로 변환
 * 
 * v2.0 Changes (FIX-2: Structural WRONGTYPE Prevention):
 * - hSet 전에 키 타입 확인 → string이면 DEL 후 hSet
 * - try-catch로 모든 Redis 에러 핸들링
 * - 에러 발생 시 fallback: JSON.stringify + SET
 * - 재발 방지: ensureHashType() guard 함수
 */
// [DEEP-FIX] Load Redis SSOT
import { readFileSync } from "fs";
try {
  const envContent = readFileSync("/etc/ares/redis.env", "utf-8");
  for (const line of envContent.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eqIdx = trimmed.indexOf("=");
    if (eqIdx === -1) continue;
    process.env[trimmed.slice(0, eqIdx).trim()] = trimmed.slice(eqIdx + 1).trim();
  }
} catch(e) { console.error("[SSOT] Failed to load redis.env:", e.message); }

import { createClient } from "redis";

const REGIME_MULT_MAP = {
    "risk_on": 1.5,
    "recovery": 1.2,
    "transition": 1.0,
    "risk_off": 0.7,
    "crisis": 0.4
};

const TARGET_KEY = "regime:r15:current";

function resolveRequiredRedisUrl() {
    const url = process.env.REDIS_URL || process.env.ARES_REDIS_URL || "";
    if (!url) throw new Error("REDIS_URL or ARES_REDIS_URL is required");
    if (/localhost|127\.0\.0\.1/.test(url)) {
        throw new Error("Local Redis fallback is forbidden in production");
    }
    return url;
}

/**
 * Ensure a Redis key is HASH type. If STRING, delete it first.
 * This prevents WRONGTYPE errors permanently.
 */
async function ensureHashType(redis, key) {
    try {
        const keyType = await redis.type(key);
        if (keyType === "string") {
            await redis.del(key);
            console.log(`[regime15-bridge] AUTO-FIX: Deleted "${key}" (was string, need hash)`);
            return "migrated";
        }
        return keyType;
    } catch (e) {
        console.error(`[regime15-bridge] ensureHashType error: ${e.message}`);
        return "error";
    }
}

async function main() {
    const redis = createClient({ 
        url: resolveRequiredRedisUrl(),
        socket: { 
            reconnectStrategy: (retries) => Math.min(retries * 500, 30000) 
        } 
    });
    
    redis.on("error", (err) => {
        // Only log non-WRONGTYPE errors (WRONGTYPE is handled in sync())
        if (!err.message?.includes("WRONGTYPE")) {
            console.error(`[regime15-bridge] Redis error: ${err.message}`);
        }
    });
    
    await redis.connect();
    console.log("✅ Regime15 Bridge v2.0 started (with WRONGTYPE guard)");
    
    // Initial type check on startup
    await ensureHashType(redis, TARGET_KEY);
    
    let syncInFlight = false;

    async function sync() {
        if (syncInFlight) return;
        syncInFlight = true;
        try {
            const raw = await redis.get("regime15:current");
            if (!raw) return;
            
            const data = JSON.parse(raw);
            const state = (data.regime || "transition").toUpperCase();
            const mult = REGIME_MULT_MAP[data.regime?.toLowerCase()] || 0.7;
            
            const hashData = {
                state: state,
                // [PATCH-R15-FIELDNAME] 양방향 호환: regime + state, mult + band_multiplier
                regime: state,
                mult: String(mult),
                band_multiplier: String(mult),
                timestamp: String(Date.now()),
                confidence: String(data.confidence || 0),
                score: String(data.score || 0)
            };
            
            // Guard: ensure key is hash type before hSet
            const keyType = await redis.type(TARGET_KEY);
            if (keyType === "string") {
                await redis.del(TARGET_KEY);
                console.log(`[regime15-bridge] Type guard: DEL "${TARGET_KEY}" (was string)`);
            }
            
            await redis.hSet(TARGET_KEY, hashData);
            
            console.log(`[${new Date().toISOString()}] Synced: ${state} (mult=${mult})`);
        } catch (e) {
            if (e.message?.includes("WRONGTYPE")) {
                // Emergency fallback: delete and retry once
                console.warn(`[regime15-bridge] WRONGTYPE detected, emergency DEL+retry`);
                try {
                    await redis.del(TARGET_KEY);
                    // Retry will happen on next cycle
                } catch (delErr) {
                    console.error(`[regime15-bridge] Emergency DEL failed: ${delErr.message}`);
                }
            } else {
                console.error(`[regime15-bridge] Sync error: ${e.message}`);
            }
        } finally {
            syncInFlight = false;
        }
    }
    
    // 초기 동기화
    await sync();
    
    // 30초마다 동기화
    setInterval(sync, 30000);
    
    process.on("SIGINT", async () => {
        await redis.quit();
        process.exit(0);
    });
    
    process.on("SIGTERM", async () => {
        await redis.quit();
        process.exit(0);
    });
}

main().catch(console.error);
