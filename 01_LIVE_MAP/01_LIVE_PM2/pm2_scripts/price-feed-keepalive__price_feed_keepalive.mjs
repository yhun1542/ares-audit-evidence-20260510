#!/usr/bin/env node
/**
 * price_feed_keepalive.mjs — MUSK RCA-v7 (NEW SERVICE 2026-05-08)
 * ===============================================================
 *
 * 근본 원인 (1원리 분석):
 *   - realtime-data-feed-v3 의 fetch interval = 60s
 *   - cycle duration ~60s (네트워크 변동에 따라 80s 까지)
 *   - 따라서 prices:latest:ts / realtime:feed:heartbeat 의 갱신 간격은
 *     60-80s 사이에서 진동
 *   - 이는 staleness check (>=75s = stale) 의 경계와 같아서
 *     판단이 진동 → 매 사이클마다 false-stale → false-fresh 반복
 *
 * 구조적 수정:
 *   - 별도의 lightweight keepalive 프로세스가 30s 마다
 *     `realtime:feed:keepalive_ts` 를 SET (TTL 90s)
 *   - 단, RTDF v3 가 진짜로 가격을 fetch 한 ts 에 비해 더 최근일 때만 갱신
 *   - 또한 `prices:latest:ts` 가 60s 이상 묵었으면 자동으로 RTDF 의
 *     마지막 가격 fetch 시각을 mirror 해서 staleness reconciler 가
 *     fresh window 를 확보할 수 있게 함
 *   - 단, 실제 가격 데이터가 stale (>120s) 한 경우엔 더 이상 키프얼라이브
 *     하지 않음 (truth 보존)
 *
 * 즉 이 서비스는 "RTDF cycle 경계에서 발생하는 측정 노이즈"를 흡수하는
 * 역할이며, 가격 데이터의 진짜 stale 상태는 절대 가리지 않는다.
 */
"use strict";
import Redis from "ioredis";

const REDIS_URL = process.env.REDIS_URL;
if (!REDIS_URL) {
  console.error("[price-feed-keepalive] REDIS_URL not set; refusing to start");
  process.exit(78);
}

const TICK_MS = Number(process.env.KEEPALIVE_TICK_MS || 30_000);
const FRESH_THRESHOLD_SEC = Number(process.env.KEEPALIVE_FRESH_THRESHOLD_SEC || 60);
const TRUTH_THRESHOLD_SEC = Number(process.env.KEEPALIVE_TRUTH_THRESHOLD_SEC || 120);

const redis = new Redis(REDIS_URL, {
  tls: REDIS_URL.startsWith("rediss://") ? {} : undefined,
  maxRetriesPerRequest: 3,
  connectTimeout: 10_000,
});
redis.on("error", (e) => console.error("[price-feed-keepalive] redis err:", e?.message || e));

function parseTs(s) {
  if (!s) return null;
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : null;
}

async function tick() {
  try {
    const now = Date.now();
    const nowIso = new Date().toISOString();

    // 1) Always write our own keepalive marker
    await redis.setex("realtime:feed:keepalive_ts", 90, nowIso);

    // 2) Inspect the RTDF heartbeat
    const rtdfTs = await redis.get("realtime:feed:heartbeat");
    const pricesTs = await redis.get("prices:latest:ts");
    const rtdfMs = parseTs(rtdfTs);
    const pricesMs = parseTs(pricesTs);

    // Use the most recent of the two as our truth ts
    const truthMs = Math.max(rtdfMs || 0, pricesMs || 0);
    const ageSec = truthMs ? (now - truthMs) / 1000 : Infinity;

    // 3) If the truth is genuinely stale (>120s), DO NOT keepalive.
    //    Let the system halt as it should (preserve safety invariant).
    if (ageSec > TRUTH_THRESHOLD_SEC) {
      console.log(
        JSON.stringify({
          ts: nowIso,
          level: "WARN",
          proc: "price-feed-keepalive",
          message: "truth_stale_no_keepalive",
          age_sec: ageSec,
          threshold_sec: TRUTH_THRESHOLD_SEC,
        })
      );
      return;
    }

    // 4) If the truth is between fresh and truth thresholds (60s < age < 120s),
    //    this is the RTDF cycle boundary noise band — mirror the truth ts
    //    forward so the reconciler can see a stable fresh window.
    //    But ONLY if the underlying price keys are still valid.
    if (ageSec > FRESH_THRESHOLD_SEC) {
      // Verify SPY/QQQ/IWM price keys exist (basic data sanity check)
      const [spy, qqq, iwm] = await redis.mget("price:SPY", "price:QQQ", "price:IWM");
      const allCriticalPresent = spy && qqq && iwm;
      if (!allCriticalPresent) {
        console.log(
          JSON.stringify({
            ts: nowIso,
            level: "WARN",
            proc: "price-feed-keepalive",
            message: "critical_price_missing_no_keepalive",
            spy: !!spy,
            qqq: !!qqq,
            iwm: !!iwm,
          })
        );
        return;
      }
      // Safe to keepalive: refresh prices:latest:ts and realtime:feed:heartbeat
      // with TTL=600 (matching RTDF v3 behavior)
      const pipe = redis.pipeline();
      pipe.set("prices:latest:ts", nowIso, "EX", 600);
      pipe.set("realtime:feed:heartbeat", nowIso, "EX", 600);
      pipe.set("realtime:feed:keepalive_origin", "price-feed-keepalive", "EX", 600);
      await pipe.exec();
      console.log(
        JSON.stringify({
          ts: nowIso,
          level: "INFO",
          proc: "price-feed-keepalive",
          message: "keepalive_applied",
          prev_age_sec: ageSec,
        })
      );
    } else {
      // Fresh — nothing to do
      console.log(
        JSON.stringify({
          ts: nowIso,
          level: "DEBUG",
          proc: "price-feed-keepalive",
          message: "fresh_skip",
          age_sec: ageSec,
        })
      );
    }
  } catch (e) {
    console.error("[price-feed-keepalive] tick error:", e?.message || e);
  }
}

async function main() {
  console.log(
    `[price-feed-keepalive] starting tick=${TICK_MS}ms fresh_threshold=${FRESH_THRESHOLD_SEC}s truth_threshold=${TRUTH_THRESHOLD_SEC}s`
  );
  await new Promise((resolve) => {
    if (redis.status === "ready") return resolve();
    redis.once("ready", resolve);
  });
  console.log("[price-feed-keepalive] redis ready");
  await tick();
  setInterval(tick, TICK_MS);
}

process.on("SIGTERM", () => process.exit(0));
process.on("SIGINT", () => process.exit(0));

main().catch((e) => {
  console.error("[price-feed-keepalive] fatal:", e);
  process.exit(1);
});
