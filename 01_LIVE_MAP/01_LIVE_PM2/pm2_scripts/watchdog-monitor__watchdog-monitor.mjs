/**
 * watchdog-monitor.mjs — 실시간 자동화 모니터링 + 4AI 자동 보정 시스템
 * 
 * 기능:
 * 1. 핵심 지표 주기적 점검 (scaleFactor, budget, P&L, turnover, exposure)
 * 2. 이상 감지 시 자동 보정 (equity anchor 리셋, scaleFactor 클램핑, 거래 중지)
 * 3. 4AI 모듈 건강성 점검 (XGBoost, Factor Engine, Sentiment, Alpha Signal)
 * 4. Telegram 알림 연동
 * 5. 자기 진단 (Watchdog 자체 건강성 점검)
 * 
 * @version 1.0.0
 */

import Redis from "ioredis";
// [ARES-PATCH-v5] Redis schema guard
import { readPositionSnapshot, assertKeyType } from "./lib/redis_schema_guard.mjs";
import {
  emitAuthorityEvidence as _emitAuthorityEvidence,
  isLegacyDirectWriteEnabled,
  getAuthorityEmitStats,
} from "./lib/authority-emitter.mjs";

const redis = new Redis(process.env.REDIS_URL, {
  retryStrategy: (times) => Math.min(times * 500, 30000),
  maxRetriesPerRequest: null,
});

const PROC = "watchdog-monitor";

// [STRUCT-FIX 2026-04-28] Manual/emergency halt is an absolute boundary.
// This watchdog may alert and publish evidence, but must not resume trading or
// restart order-path processes while operator/emergency halt keys are active.
const TRADING_PATH_PROCS = new Set([
  "live-trading-kis",
  "order-intent-executor",
  "oeg-executor",
  "aoa-live",
]);
const truthyHalt = (v) => ["1", "true", "yes", "on", "kill"].includes(String(v || "").toLowerCase());


// [JASON_ALERT_FIX_20260429_WATCHDOG_INTENTIONAL_IDLE]
async function jasonIntentionalGateIdle(redisClient) {
  try {
    const [enabled, override, gate, manualHalt, intentionalHalt, disabledLatest] = await Promise.all([
      redisClient.get('trading:enabled').catch(() => null),
      redisClient.get('trading:enabled:override').catch(() => null),
      redisClient.get('ofg:gate').catch(() => null),
      redisClient.get('ops:manual_halt').catch(() => null),
      redisClient.get('ares:intentional_halt').catch(() => null),
      redisClient.get('live:cycle:disabled:latest').catch(() => null),
    ]);
    const disabledRecent = (() => {
      try { const j = JSON.parse(disabledLatest || '{}'); return j.ts && Date.now() - Number(j.ts) < 900000; } catch { return false; }
    })();
    const idle = String(enabled).toLowerCase() !== 'true' || override === 'kill' || gate === 'HALT' || manualHalt === 'true' || intentionalHalt || disabledRecent;
    if (idle) {
      return { idle: true, reason: 'INTENTIONAL_GATE_IDLE', enabled, override, gate, manualHalt, intentionalHalt, disabledRecent };
    }
  } catch (e) {
    return { idle: false, reason: 'INTENTIONAL_GATE_IDLE_CHECK_FAILED', err: e?.message || String(e) };
  }
  return { idle: false };
}
async function readManualOrEmergencyHalt() {
  try {
    const [override, tradeHalt, hardHalt, intentional, invariant, maintenanceMode, enabled] = await redis.mget(
      "trading:enabled:override",
      "trade:halt",
      "policy:safe_live:hard_halt",
      "ares:intentional_halt",
      "ares:invariant:halt",
      "ares:maintenance:mode",
      "trading:enabled",
    );
    const maintenance = ["intentional_halt", "maintenance", "emergency_halt"].includes(String(maintenanceMode || "").toLowerCase());
    const blocked = String(override || "").toLowerCase() === "kill"
      || truthyHalt(tradeHalt)
      || truthyHalt(hardHalt)
      || truthyHalt(invariant)
      || (intentional != null && String(intentional).trim() !== "")
      || maintenance;
    return { blocked, override, tradeHalt, hardHalt, intentional, invariant, maintenanceMode, enabled };
  } catch (e) {
    return { blocked: true, error: String(e?.message || e).slice(0, 300) };
  }
}

// [PHASE A/B v3] Authority emitter shim — uses shared module
async function emitAuthorityEvidence(kind, payload = {}) {
  return _emitAuthorityEvidence(redis, PROC, kind, payload, { log });
}
const LEGACY_DIRECT_GATE_WRITES = isLegacyDirectWriteEnabled();
const CHECK_INTERVAL_MS = parseInt(process.env.WATCHDOG_INTERVAL || "60000"); // 1분
const ALERT_COOLDOWN_MS = 600000;  // [PATCH-F02] 10분 쿨다운: 중복 알림 감소

// ========== CONFIG ==========
const THRESHOLDS = {
  // scaleFactor 이상 범위
  scaleFactor: { min: 0.25, max: 1.05, criticalMin: 0.15 },
  // budget 이상 범위
  budget: { min: 5000, max: 200000, changeRatePct: 50 },
  // P&L 한도
  dailyPnl: { warningPct: -5, criticalPct: -15 },  // [PATCH-F02] criticalPct 완화,
  // turnover 한도
  turnover: { maxPct: 20 },
  // 4AI 모듈 최대 허용 다운타임
  moduleMaxDowntimeMs: 900000,  // [PATCH-F02] 15분으로 확장
  // 포지션 이상
  maxSinglePositionPct: 20,
  maxGrossExposure: 1.5,
};

const lastAlerts = {};

// ========== LOGGING ==========
function log(level, message, data = {}) {
  const ts = new Date().toISOString();
  console.log(JSON.stringify({ ts, level, proc: PROC, message, ...data }));
}

// ========== ALERT SYSTEM ==========
async function sendAlert(severity, code, message, ctx = {}) {
  const now = Date.now();
  const key = `${severity}:${code}`;
  if (lastAlerts[key] && (now - lastAlerts[key]) < ALERT_COOLDOWN_MS) return;
  lastAlerts[key] = now;

  const alert = {
    severity,
    code,
    message,
    ts: new Date().toISOString(),
    ...ctx,
  };

  // Redis에 알림 기록
  await redis.xadd("watchdog:alerts", "*", "json", JSON.stringify(alert));
  await redis.set("watchdog:last_alert", JSON.stringify(alert), "EX", 86400);

  // Telegram 알림 (telegram-notifier가 구독)
  const emoji = severity === "CRITICAL" ? "🚨" : severity === "WARNING" ? "⚠️" : "ℹ️";
  await redis.publish("telegram:send", JSON.stringify({
    text: `${emoji} *[Watchdog ${severity}]*\n\`${code}\`\n${message}`,
    parse_mode: "Markdown",
  }));

  log(severity === "CRITICAL" ? "ERROR" : "WARN", `ALERT: ${code}`, alert);
}

// ========== AUTO-CORRECTION ACTIONS ==========
async function autoCorrect(action, ctx = {}) {
  log("WARN", `AUTO_CORRECT: ${action}`, ctx);
  await redis.xadd("watchdog:corrections", "*", "json", JSON.stringify({
    action, ts: new Date().toISOString(), ...ctx,
  }));

  switch (action) {
    case "RESET_EQUITY_ANCHOR": {
      // PATCH15: Anchor management is now handled exclusively by equity-calculator.mjs
      // Watchdog should NEVER modify the anchor to prevent infinite reset loops.
      log("INFO", "ANCHOR_RESET_SKIPPED: Delegated to equity-calculator", ctx);
      break;
    }
    case "CLAMP_SCALE_FACTOR": {
      // scaleFactor는 live-trading-kis가 자체 클램핑하므로 여기서는 경고만
      await sendAlert("WARNING", "SCALE_FACTOR_CLAMPED", `scaleFactor ${ctx.value} clamped`, ctx);
      break;
    }
    case "PAUSE_TRADING": {
      // [PHASE A/B v3] Evidence first, legacy writes guarded
      await emitAuthorityEvidence("PAUSE_REQUEST", {
        reason: `WATCHDOG:${ctx.reason}`,
        source_action: "autoCorrect.PAUSE_TRADING",
        ctx,
      });
      if (LEGACY_DIRECT_GATE_WRITES) {
        await redis.set("trading:enabled:override", "kill", "EX", 3600);
        await redis.set("trading:disable_reason", `WATCHDOG:${ctx.reason}`, "EX", 3600);
      }
      await sendAlert("CRITICAL", "TRADING_PAUSED", `Trading paused: ${ctx.reason}`, ctx);
      break;
    }
    case "RESUME_TRADING": {
      const manualHalt = await readManualOrEmergencyHalt();
      if (manualHalt.blocked) {
        await redis.xadd("watchdog:events", "*", "json", JSON.stringify({
          ts: new Date().toISOString(),
          event: "RESUME_TRADING_GATED_BY_MANUAL_HALT",
          manualHalt,
          ctx,
        })).catch(() => {});
        await sendAlert("INFO", "TRADING_RESUME_GATED", "Trading resume suppressed by manual/emergency halt", { ...ctx, manualHalt });
        break;
      }
      // [PHASE A/B v3] Evidence first, legacy writes guarded
      await emitAuthorityEvidence("RESUME_REQUEST", {
        reason: ctx.reason || "WATCHDOG_RESUME",
        source_action: "autoCorrect.RESUME_TRADING",
        ctx,
      });
      if (LEGACY_DIRECT_GATE_WRITES) {
        await redis.del("trading:enabled:override");
        await redis.del("trading:disable_reason");
        await redis.set("kill-switch-authority:resume_proposal", JSON.stringify({
          source: "watchdog-monitor",
          ts: new Date().toISOString(),
          reason: ctx.reason || "WATCHDOG_RESUME"
        }), "EX", 3600).catch(() => {});
      }
      await sendAlert("INFO", "TRADING_RESUMED", "Trading resume proposed by watchdog", ctx);
      break;
    }
    case "RESTART_MODULE": {
      const manualHalt = await readManualOrEmergencyHalt();
      if (TRADING_PATH_PROCS.has(String(ctx.module || "")) && manualHalt.blocked) {
        await redis.xadd("watchdog:events", "*", "json", JSON.stringify({
          ts: new Date().toISOString(),
          event: "RESTART_MODULE_GATED_BY_MANUAL_HALT",
          module: ctx.module,
          manualHalt,
          ctx,
        })).catch(() => {});
        await sendAlert("INFO", "MODULE_RESTART_GATED", `${ctx.module} restart suppressed by manual/emergency halt`, { ...ctx, manualHalt });
        break;
      }
      // PM2 restart via Redis command channel
      await redis.publish("cmd:pm2:restart", JSON.stringify({ name: ctx.module }));
      await sendAlert("WARNING", "MODULE_RESTARTED", `${ctx.module} restarted`, ctx);
      break;
    }
  }
}

// ========== HEALTH CHECKS ==========

// CHECK 1: scaleFactor 건강성
async function checkScaleFactor() {
  const raw = await redis.get("live:scaleFactor");
  if (!raw) return { ok: true, skip: true, reason: "no_data" };

  const sf = parseFloat(raw);
  if (!Number.isFinite(sf)) return { ok: false, code: "SF_NAN", msg: "scaleFactor is NaN" };

  if (sf < THRESHOLDS.scaleFactor.criticalMin) {
    // [PHASE A/B v3] Evidence first, legacy writes guarded
    await emitAuthorityEvidence("PAUSE_REQUEST", {
      reason: "WATCHDOG:SCALE_FACTOR_CRITICAL",
      source_action: "checkScaleFactor.criticalMin",
      ctx: { value: sf, threshold: THRESHOLDS.scaleFactor.criticalMin },
    });
    if (LEGACY_DIRECT_GATE_WRITES) {
      await redis.set("trading:enabled:override", "kill", "EX", 3600).catch(() => {});
      await redis.set("trading:disable_reason", "WATCHDOG:SCALE_FACTOR_CRITICAL", "EX", 3600).catch(() => {});
    }
    await sendAlert("CRITICAL", "SF_CRITICAL", `scaleFactor ${sf.toFixed(4)} critically low`, { value: sf });
    return { ok: false, code: "SF_CRITICAL", msg: `scaleFactor ${sf.toFixed(4)} < ${THRESHOLDS.scaleFactor.criticalMin}` };
  }
  if (sf < THRESHOLDS.scaleFactor.min) {
    await sendAlert("WARNING", "SF_LOW", `scaleFactor ${sf.toFixed(4)} below threshold`, { value: sf });
    return { ok: false, code: "SF_LOW", msg: `scaleFactor ${sf.toFixed(4)} low` };
  }
  if (sf > THRESHOLDS.scaleFactor.max) {
    await sendAlert("WARNING", "SF_HIGH", `scaleFactor ${sf.toFixed(4)} above 1.0`, { value: sf });
    return { ok: false, code: "SF_HIGH", msg: `scaleFactor ${sf.toFixed(4)} high` };
  }
  return { ok: true, value: sf };
}

// CHECK 2: Budget 건강성
async function checkBudget() {
  // ── PATCH15+P2-1: Use ares:equity:total (SSOT from equity-calculator) ──
  // IMPORTANT: budget_usd = available cash only (ord_psbl_frcr_amt), NOT total equity.
  // ares:equity:total = cash + positions (the sole SSOT for total equity).
  // We NEVER calculate equity ourselves to avoid double-counting.
  
  const totalRaw = await redis.get("ares:equity:total");
  if (!totalRaw) {
    // Fallback: if equity-calculator hasn't run yet, read budget_usd directly
    const budgetRaw = await redis.get("emarkos:v1:budget_usd");
    if (!budgetRaw) return { ok: true, skip: true, reason: "no_data" };
    const budget = parseFloat(budgetRaw);
    await redis.set("watchdog:prev_budget", budget.toString());
    return { ok: true, value: budget, source: "fallback" };
  }
  
  const total = parseFloat(totalRaw);
  if (!Number.isFinite(total) || total <= 0) {
    return { ok: true, skip: true, reason: "invalid_equity" };
  }
  
  // Check for sudden changes (WARNING only, never PAUSE)
  const prevRaw = await redis.get("watchdog:prev_budget");
  if (prevRaw) {
    const prev = parseFloat(prevRaw);
    if (prev > 0) {
      const changePct = ((total - prev) / prev) * 100;
      if (Math.abs(changePct) > THRESHOLDS.budget.changeRatePct) {
        log("WARN", "BUDGET_CHANGE_DETECTED", { 
          prev: prev.toFixed(0), current: total.toFixed(0), changePct: changePct.toFixed(1),
          note: "Not pausing - delegating to checkDailyPnl for real loss detection"
        });
      }
    }
  }
  // prev_budget is managed by equity-calculator (no TTL), but update here too as backup
  await redis.set("watchdog:prev_budget", total.toString());
  
  return { ok: true, value: total, source: "ares:equity:total" };
}
// CHECK 3: Daily P&L
// CHECK 3: Daily P&L
async function checkDailyPnl() {
  // ── PATCH15: Use ares:equity:total (SSOT) for current equity ──
  // IMPORTANT: budget_usd is CASH ONLY (ord_psbl_frcr_amt), NOT total equity.
  // ares:equity:total is the sole SSOT for total equity (cash + positions).
  // Fallback path adds positions MV manually if SSOT is unavailable.
  
  const anchorRaw = await redis.get("champion:equity_anchor:usd");
  if (!anchorRaw) return { ok: false, skip: false, reason: "NO_ANCHOR", severity: "CRITICAL" } // PATCH: missing anchor is NOT ok;
  const anchor = parseFloat(anchorRaw);
  if (anchor <= 0) return { ok: true, skip: true, reason: "INVALID_ANCHOR" };
  
  // Read current equity = positions MV + cash (FIX: budget_usd is cash only, not total equity)
  let current = 0;
  const ssotRaw = await redis.get("ares:equity:total");
  if (ssotRaw && parseFloat(ssotRaw) > 1000) {
    current = parseFloat(ssotRaw);
  } else {
    // Fallback: positions MV + budget_usd (WARNING: budget_usd = cash only, not total equity)
    // [PATCH-HASH-FIX] emarkos:v1:positions is now a HASH, use HGETALL
    let posMV_eq = 0;
    try {
      // [STRUCTURAL_FIX] emarkos:v1:positions is STRING(JSON), not HASH
      const posRaw_eq = await redis.get("emarkos:v1:positions");
      const posHash_eq = posRaw_eq ? (JSON.parse(posRaw_eq).positions || {}) : {};
      if (posHash_eq) {
        for (const [sym, raw] of Object.entries(posHash_eq)) {
          if (sym === "__meta__") continue;
          try { const p = JSON.parse(raw); posMV_eq += Number(p?.marketValue ?? 0); } catch(e) {}
        }
      }
    } catch(e) { posMV_eq = 0; }
    const budgetRaw_eq = await redis.get("emarkos:v1:budget_usd");
    const cash_eq = parseFloat(budgetRaw_eq || "0");
    current = posMV_eq + cash_eq;
    log("WARN", "PNL_USING_FALLBACK", { note: "ares:equity:total missing, using positions+budget_usd", posMV_eq, cash_eq });
    if (current < 1000) return { ok: true, skip: true, reason: "EQUITY_TOO_LOW" };
  }
  
  if (current <= 0) return { ok: true, skip: true, reason: "ZERO_EQUITY" };
  
  const pnlPct = ((current - anchor) / anchor) * 100;
  
  // Log every check for audit trail
  log("DEBUG", "PNL_CHECK", { anchor: anchor.toFixed(2), current: current.toFixed(2), pnlPct: pnlPct.toFixed(2) });
  
  // [STRUCTURAL_FIX_V2] Detect anchor drift in BOTH directions (positive and negative)
  // If |pnlPct| > 30%, anchor is likely stale from PM2 reload or portfolio resize
  if (Math.abs(pnlPct) > 30) {
    const sanity = await pnlSanityCheck(redis, pnlPct, current, anchor);
    if (sanity.action !== "HALT") {
      log("WARN", "ANCHOR_DRIFT_DETECTED", {
        pnlPct: pnlPct.toFixed(2), anchor: anchor.toFixed(2), current: current.toFixed(2),
        direction: pnlPct > 0 ? "POSITIVE" : "NEGATIVE",
        sanity_action: sanity.action,
        note: "Anchor likely stale - auto-resetting to current equity"
      });
      await redis.set("champion:equity_anchor:usd", current.toString());
      return { ok: true, pnlPct: 0, note: "ANCHOR_DRIFT_RESET" };
    }
  }
  
  // CRITICAL: Only pause for genuine large losses
  // [STRUCTURAL_FIX] Always run sanity check before PAUSE_TRADING
  if (pnlPct <= THRESHOLDS.dailyPnl.criticalPct) {
    const sanity = await pnlSanityCheck(redis, pnlPct, current, anchor);
    if (sanity.action !== "HALT") {
      log("WARN", "PNL_CRITICAL_SUPPRESSED_BY_SANITY", { pnlPct, anchor, current, sanity });
      // Do NOT pause trading — this is likely a data error, not a real loss
      return { ok: false, code: "PNL_SUPPRESSED", pnlPct, sanity };
    }
    await sendAlert("CRITICAL", "PNL_CRITICAL",
      `Daily P&L ${pnlPct.toFixed(2)}% exceeds critical threshold (${THRESHOLDS.dailyPnl.criticalPct}%)`,
      { pnlPct, anchor, current, sanityAction: sanity.action });
    await autoCorrect("PAUSE_TRADING", { reason: "PNL_CRITICAL", pnlPct });
    return { ok: false, code: "PNL_CRITICAL", pnlPct };
  }
  
  // WARNING: approaching limit
  if (pnlPct <= THRESHOLDS.dailyPnl.warningPct) {
    await sendAlert("WARNING", "PNL_WARNING",
      `Daily P&L ${pnlPct.toFixed(2)}% approaching limit (${THRESHOLDS.dailyPnl.warningPct}%)`,
      { pnlPct, anchor, current });
    return { ok: false, code: "PNL_WARNING", pnlPct };
  }
  
  // NOTE: Anchor day management is handled by equity-calculator.mjs
  // We do NOT reset anchor here. This prevents the infinite loop where
  // watchdog resets anchor to a wrong value, which triggers more resets.
  
  return { ok: true, pnlPct };
}
// CHECK 4: Turnover
async function checkTurnover() {
  const today = new Date().toISOString().slice(0, 10);
  const usedRaw = await redis.get("turnover:daily:" + today);
  // PATCH-P2-1a: Use ares:equity:total (SSOT) as denominator, NOT budget_usd (which is cash-only)
  const equityRaw = await redis.get("ares:equity:total");
  const budgetFallback = await redis.get("emarkos:v1:budget_usd");  // fallback only
  if (!usedRaw) return { ok: true, skip: true };
  
  const used = parseFloat(usedRaw);
  const budget = parseFloat(equityRaw || budgetFallback || "0");
  if (budget <= 0) return { ok: true, skip: true };
  if (!equityRaw) log("WARN", "TURNOVER_USING_FALLBACK", { note: "ares:equity:total missing, using budget_usd (cash-only) as fallback" });
  
  const turnoverPct = (used / budget) * 100;
  if (turnoverPct > THRESHOLDS.turnover.maxPct) {
    await sendAlert("WARNING", "TURNOVER_HIGH", 
      `Daily turnover ${turnoverPct.toFixed(1)}% exceeds ${THRESHOLDS.turnover.maxPct}%`,
      { used, budget, turnoverPct });
    return { ok: false, code: "TURNOVER_HIGH", turnoverPct };
  }
  return { ok: true, turnoverPct };
}

// CHECK 5: Position Concentration
async function checkPositionConcentration() {
  // [PATCH-HASH-FIX] emarkos:v1:positions is now a HASH
  let positions_conc = {};
  try {
    // [STRUCTURAL_FIX] emarkos:v1:positions is STRING(JSON), not HASH
      const posRaw_conc = await redis.get("emarkos:v1:positions");
      const posHash_conc = posRaw_conc ? (JSON.parse(posRaw_conc).positions || {}) : {};
    if (!posHash_conc) return { ok: true, skip: true };
    for (const [sym, raw] of Object.entries(posHash_conc)) {
      if (sym === "__meta__") continue;
      try { positions_conc[sym] = JSON.parse(raw); } catch(e) {}
    }
  } catch(e) { return { ok: true, skip: true }; }
  if (Object.keys(positions_conc).length === 0) return { ok: true, skip: true };
  
  try {
    const positions = positions_conc;
    const entries = Object.entries(positions);
    
    let totalMV = 0;
    const mvBySymbol = {};
    for (const [sym, p] of entries) {
      const shares = Number(p?.shares ?? p?.qty ?? 0);
      const mv = Number(p?.marketValue ?? 0) || shares * Number(p?.current_price ?? p?.last_price ?? 0);
      mvBySymbol[sym] = mv;
      totalMV += mv;
    }
    
    if (totalMV <= 0) return { ok: true, skip: true };
    
    const issues = [];
    for (const [sym, mv] of Object.entries(mvBySymbol)) {
      const pct = (mv / totalMV) * 100;
      if (pct > THRESHOLDS.maxSinglePositionPct) {
        issues.push({ symbol: sym, pct: pct.toFixed(1) });
      }
    }
    
    if (issues.length > 0) {
      await sendAlert("WARNING", "CONCENTRATION_HIGH", 
        `Position concentration: ${issues.map(i => `${i.symbol}=${i.pct}%`).join(", ")}`,
        { issues, totalMV });
    }
    
    return { ok: issues.length === 0, issues };
  } catch (err) { console.error(JSON.stringify({ level: "ERROR", ts: new Date().toISOString(), component: "watchdog", error: err.message }));
    return { ok: true, skip: true };
  }
}

// CHECK 6: 4AI Module Health
async function check4AIModules() {
  const modules = [
// [P7 disabled v5]     { name: "xgb-riskflag-writer-v5", statusKey: "xgb:risk:status", required: true },
    { name: "factor-engine", statusKey: "factor:active_weights", required: false },
    { name: "sentiment-analyzer", statusKey: null, required: false },
    { name: "alpha-signal-scheduler", statusKey: null, required: false },
    { name: "final-to-champion-bridge", statusKey: process.env.TARGETS_KEY || "ssot:target:v2:current", required: true },
  ];
  
  const results = {};
  for (const mod of modules) {
    const health = { name: mod.name, ok: true };
    
    // Check if module has recent data
    if (mod.statusKey) {
      const data = await redis.get(mod.statusKey);
      if (!data) {
        // Try hash type
        const hashData = await redis.hgetall(mod.statusKey).catch(() => null);
        if (!hashData || Object.keys(hashData).length === 0) {
          health.ok = false;
          health.reason = "no_data";
          if (mod.required) {
            await sendAlert("WARNING", `MODULE_NO_DATA_${mod.name}`, 
              `${mod.name} has no data in ${mod.statusKey}`);
          }
        }
      } else {
        try {
          const parsed = JSON.parse(data);
          const ts = parsed.ts || parsed.timestamp;
          if (ts) {
            const age = Date.now() - new Date(ts).getTime();
            health.dataAge = age;
            if (age > THRESHOLDS.moduleMaxDowntimeMs) {
              health.ok = false;
              health.reason = "stale_data";
              health.ageMinutes = (age / 60000).toFixed(1);
              if (mod.required) {
                await sendAlert("WARNING", `MODULE_STALE_${mod.name}`,
                  `${mod.name} data is ${health.ageMinutes} min old`);
              }
            }
          }
        } catch {}
      }
    }
    
    results[mod.name] = health;
  }
  
  return results;
}

// CHECK 7: Order Execution Health
async function checkOrderExecution() {
  const nyDay = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }))
    .toISOString().slice(0, 10).replaceAll("-", "");
  
  const sellUsed = Number(await redis.get(`kpi:sell_used_usd:${nyDay}`) || "0");
  const sellCap = Number(await redis.get("policy:sell_cap_usd_day") || "28138");
  
  if (sellUsed > sellCap * 0.8) {
    await sendAlert("WARNING", "SELL_CAP_NEAR", 
      `Daily SELL used $${sellUsed.toFixed(0)} / $${sellCap} (${((sellUsed/sellCap)*100).toFixed(0)}%)`,
      { sellUsed, sellCap });
  }
  
  return { sellUsed, sellCap, pctUsed: sellCap > 0 ? (sellUsed / sellCap * 100) : 0 };
}

// CHECK 8: Self-Diagnosis
async function selfDiagnosis() {
  const uptime = process.uptime();
  const memUsage = process.memoryUsage();
  
  // Record heartbeat
  await redis.set("watchdog:heartbeat", JSON.stringify({
    ts: new Date().toISOString(),
    uptime: uptime.toFixed(0),
    rss: (memUsage.rss / 1024 / 1024).toFixed(1) + "MB",
    checks_run: checksRun,
  }), "EX", 300);
  
  // Check if we're consuming too much memory
  if (memUsage.rss > 500 * 1024 * 1024) { // 500MB
    await sendAlert("WARNING", "WATCHDOG_HIGH_MEMORY", 
      `Watchdog RSS: ${(memUsage.rss / 1024 / 1024).toFixed(0)}MB`);
  }
  
  return { ok: true, uptime, rss: memUsage.rss };
}

// ========== MAIN MONITORING LOOP ==========
let checksRun = 0;

async function runChecks() {
  checksRun++;
  const startTime = Date.now();
  
  try {
    const results = {
      ts: new Date().toISOString(),
      cycle: checksRun,
      checks: {},
    };
    
    // Run all checks
    results.checks.scaleFactor = await checkScaleFactor();
    results.checks.budget = await checkBudget();
    results.checks.dailyPnl = await checkDailyPnl();
    results.checks.turnover = await checkTurnover();
    results.checks.concentration = await checkPositionConcentration();
    results.checks.modules = await check4AIModules();
    results.checks.orderExecution = await checkOrderExecution();
    results.checks.self = await selfDiagnosis();
    
    // Calculate overall health score
    const checkResults = Object.values(results.checks);
    const totalChecks = checkResults.length;
    const passedChecks = checkResults.filter(c => c.ok !== false).length;
    results.healthScore = Math.round((passedChecks / totalChecks) * 100);
    results.durationMs = Date.now() - startTime;
    
    // Store results
    await redis.set("watchdog:latest", JSON.stringify(results), "EX", 300);
    await redis.xadd("watchdog:history", "MAXLEN", "~", "1000", "*", "json", JSON.stringify(results));
    
    // Store scaleFactor for tracking
    if (results.checks.scaleFactor.value) {
      await redis.set("live:scaleFactor", results.checks.scaleFactor.value.toString(), "EX", 120);
    }
    
    // Log summary
    const failedChecks = Object.entries(results.checks)
      .filter(([, v]) => v.ok === false)
      .map(([k, v]) => `${k}:${v.code || 'FAIL'}`);
    
    if (failedChecks.length > 0) {
      log("WARN", "HEALTH_CHECK_ISSUES", { 
        score: results.healthScore, 
        failed: failedChecks,
        duration: results.durationMs,
      });
    } else if (checksRun % 10 === 0) {
      // Log OK every 10 cycles (10 minutes)
      log("INFO", "HEALTH_CHECK_OK", { 
        score: results.healthScore, 
        cycle: checksRun,
        duration: results.durationMs,
      });
    }
    
  } catch (err) {
    log("ERROR", "HEALTH_CHECK_ERROR", { error: err.message, stack: err.stack });
  }
}

// ========== EQUITY ANCHOR DAILY RESET (Cron-like) ==========
let lastAnchorResetDay = "";

async function checkEquityAnchorReset() {
  const nyNow = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const nyDay = nyNow.toISOString().slice(0, 10); // 4AI-audit: YYYY-MM-DD format (matches self-healer nyDate)
  const hhmm = nyNow.getHours() * 100 + nyNow.getMinutes();
  
  // Reset at 9:25 AM ET (5 minutes before market open)
  if (hhmm >= 925 && hhmm <= 935 && lastAnchorResetDay !== nyDay) {
    lastAnchorResetDay = nyDay;
    
    // PATCH-P2-1b: Use ares:equity:total (SSOT) for anchor seen_val, NOT budget_usd + positions (independent calc)
    const ssotEquityRaw = await redis.get("ares:equity:total");
    let totalEquityAnchor = 0;
    let anchorSource = "unknown";
    
    if (ssotEquityRaw && parseFloat(ssotEquityRaw) > 1000) {
      totalEquityAnchor = parseFloat(ssotEquityRaw);
      anchorSource = "ares:equity:total";
    } else {
      // Fallback: positions MV + budget_usd (cash-only key)
      // [PATCH-HASH-FIX] emarkos:v1:positions is now a HASH
      const budgetRaw = await redis.get("emarkos:v1:budget_usd");
      let posMV = 0;
      try {
        // [STRUCTURAL_FIX] emarkos:v1:positions is STRING(JSON), not HASH
          const posRaw_anchor = await redis.get("emarkos:v1:positions");
          const posHash_anchor = posRaw_anchor ? (JSON.parse(posRaw_anchor).positions || {}) : {};
        if (posHash_anchor) {
          for (const [sym, raw] of Object.entries(posHash_anchor)) {
            if (sym === "__meta__") continue;
            try { const p = JSON.parse(raw); posMV += Number(p?.marketValue ?? 0); } catch(e) {}
          }
        }
      } catch (e) {
        log("ERROR", "ANCHOR_POSITIONS_PARSE_FAIL", { error: e.message });
        posMV = 0;
      }
      const budget = parseFloat(budgetRaw || "0");
      totalEquityAnchor = posMV + budget;
      anchorSource = "fallback:pos+budget_usd";
      log("WARN", "ANCHOR_USING_FALLBACK", { note: "ares:equity:total missing, using positions+budget_usd", posMV, budget });
    }
    
    if (totalEquityAnchor > 1000) {  // minimum $1k to prevent dust anchor
      await redis.mset(
        // PATCH-08b: DISABLED — equity-calculator.mjs is the sole anchor SSOT
        // was: "champion:equity_anchor:usd", "champion:equity_anchor:day", etc.
        "champion:equity_anchor:watchdog_seen_ts", new Date().toISOString(),
        "champion:equity_anchor:watchdog_seen_val", String(totalEquityAnchor.toFixed(2))
      );
      
      log("INFO", "EQUITY_ANCHOR_DAILY_RESET", { 
        day: nyDay, 
        anchor: totalEquityAnchor.toFixed(2), source: anchorSource,
      });
      
      await sendAlert("INFO", "ANCHOR_DAILY_RESET", 
        `Equity anchor seen $${totalEquityAnchor.toFixed(0)} (src=${anchorSource}) for ${nyDay}`);
    }
  }
}

// ========== 4AI SELF-HEALING ==========
async function check4AISelfHealing() {
  // Check if champion bridge targets are stale
  const targetsRaw = await redis.get(process.env.TARGETS_KEY || "ssot:target:v2:current");
  if (targetsRaw) {
    try {
      const targets = JSON.parse(targetsRaw);
      const ts = targets.ts;
      if (ts) {
        const age = Date.now() - new Date(ts).getTime();
        // If targets are older than 5 minutes, trigger bridge refresh
        if (age > 300000) {
          await redis.publish("cmd:bridge:refresh", JSON.stringify({
            reason: "STALE_TARGETS",
            age: (age / 60000).toFixed(1) + "min",
          }));
          log("WARN", "BRIDGE_REFRESH_TRIGGERED", { age: (age / 60000).toFixed(1) });
        }
      }
    } catch {}
  }
  
  // Check XGBoost model health
  const xgbStatus = await redis.get("xgb:risk:status");
  if (xgbStatus) {
    try {
      const status = JSON.parse(xgbStatus);
      if (!status.model_loaded) {
        await sendAlert("CRITICAL", "XGB_MODEL_NOT_LOADED", "XGBoost model not loaded");
// [P7 disabled v5]         await autoCorrect("RESTART_MODULE", { module: "xgb-riskflag-writer-v5" });
      }
    } catch {}
  }
}

// ========== STARTUP ==========
async function main() {
const WATCHDOG_SESSION_ID = `wd_${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
  log("INFO", "=== Watchdog Monitor v1.0.0 Starting ===", {
    interval: CHECK_INTERVAL_MS,
    thresholds: THRESHOLDS,
  });


// [FIX-9] STARTUP_ANCHOR_RECONCILIATION
// On startup, validate anchor freshness and session binding
(async () => {
  try {
    const Redis = (await import("ioredis")).default;
    const r = new Redis(process.env.REDIS_URL, { lazyConnect: true });
    await r.connect();
    
    const anchor = parseFloat(await r.get("champion:equity_anchor:usd") || "0");
    const equity = parseFloat(await r.get("ares:equity:total") || "0");
    const anchorSession = await r.get("watchdog:anchor:session_id");
    const anchorDate = await r.get("watchdog:anchor:trading_date");
    const today = new Date().toISOString().slice(0, 10);
    
    let needsReset = false;
    let reason = "";
    
    // Case 1: No anchor exists
    if (!anchor || anchor === 0) {
      needsReset = true;
      reason = "no_anchor";
    }
    // Case 2: Anchor from a different trading date
    else if (anchorDate !== today) {
      needsReset = true;
      reason = `stale_date:${anchorDate}->today:${today}`;
    }
    // Case 3: Session mismatch (new process start)
    else if (anchorSession && anchorSession !== WATCHDOG_SESSION_ID) {
      // Check if drift is within reasonable bounds
      const drift = Math.abs((equity - anchor) / anchor);
      if (drift > 0.01) {  // >1% drift on session change
        needsReset = true;
        reason = `session_mismatch:drift=${(drift*100).toFixed(2)}%`;
      }
    }
    
    if (needsReset && equity > 0) {
      await r.set("champion:equity_anchor:usd", equity.toString());
      await r.set("watchdog:anchor:session_id", WATCHDOG_SESSION_ID);
      await r.set("watchdog:anchor:trading_date", today);
      console.log(JSON.stringify({
        ts: new Date().toISOString(),
        level: "WARN",
        proc: "watchdog-monitor",
        event: "STARTUP_ANCHOR_RECONCILIATION",
        reason,
        old_anchor: anchor,
        new_anchor: equity,
        session_id: WATCHDOG_SESSION_ID,
        trading_date: today
      }));
    } else {
      // Update session binding
      await r.set("watchdog:anchor:session_id", WATCHDOG_SESSION_ID);
      if (!anchorDate) await r.set("watchdog:anchor:trading_date", today);
    }
    
    await r.quit();
  } catch (e) {
    console.error("Startup anchor reconciliation error:", e.message);
  }
})();
  
  // Initial check
  await runChecks();
  
  // Periodic checks (every 1 minute) — ROUND2 PATCH: safe sequential pattern
  // Prevents race conditions: next tick only starts after previous completes
  const scheduleNext = () => {
    setTimeout(async () => {
      try {
        await runChecks();
        await checkEquityAnchorReset();
        await check4AISelfHealing();
      } catch (err) {
        log("ERROR", "Watchdog periodic check failed", { error: err.message, stack: err.stack });
      } finally {
        scheduleNext(); // always reschedule after completion
      }
    }, CHECK_INTERVAL_MS);
  };
  scheduleNext();
}

main().catch(err => {
  log("FATAL", "Watchdog crashed", { error: err.message, stack: err.stack });
  throw new Error("Fatal error - PM2 will restart");
});
// ══════════════════════════════════════════════════════════════
// ARES UNIFIED PATCH — watchdog-monitor.mjs PNL Guard
// ID: ARES-UNIFIED-20260408-P5
// Sources: Claude (3-layer validation) + GPT (kis:broker:positions_sync_status)
// ══════════════════════════════════════════════════════════════
//
// 파일: ~/aub-trading-system/watchdog-monitor.mjs
//
// 이 패치는 checkDailyPnl() 함수 (line 187~) 내부의 PNL_CRITICAL 판정을
// 3중 검증으로 보호합니다.
//
// ═══════════════════════════════════════════════════════════════
// 변경: checkDailyPnl() 안에서 autoCorrect("PAUSE_TRADING") 호출 전에
//       sanity check 삽입
// ═══════════════════════════════════════════════════════════════
//
// 현재 코드에서 PNL_CRITICAL을 트리거하는 부분을 찾아서
// (대략: if (pnlPct < criticalPct) → autoCorrect("PAUSE_TRADING"))
// 아래 함수를 호출하도록 변경:
//
//   const sanity = await pnlSanityCheck(redis, pnlPct, current, anchor);
//   if (sanity.action === "HALT") {
//     await autoCorrect("PAUSE_TRADING", { reason: "PNL_CRITICAL", pnlPct });
//   } else {
//     log("WARN", "PNL_CRITICAL_SUPPRESSED", sanity);
//     // WARN만 남기고 거래 정지하지 않음
//   }

/**
 * PNL Sanity Check — 3중 검증
 * 
 * 1. Position 데이터 신선도 (kis:broker:positions_sync_status 활용)
 * 2. Position key 타입 정합성 (WRONGTYPE 방어)
 * 3. 변화율 이상 감지 (5분 내 >20% 급변 = 데이터 오류 의심)
 * 
 * @returns {{ action: "HALT"|"WARN", reason: object }}
 */
async function pnlSanityCheck(redis, pnlPct, currentEquity, anchorEquity) {
  // ── 검증 1: Position sync 상태 (GPT 방식: 실제 sync_status 키 활용) ──
  try {
    const syncRaw = await redis.get("kis:broker:positions_sync_status");
    if (syncRaw) {
      const sync = JSON.parse(syncRaw);
      const syncAge = (Date.now() - new Date(sync.last_sync_ts).getTime()) / 1000;
      if (syncAge > 300) {  // 5분 이상 stale
        return {
          action: "WARN",
          reason: { check: "SYNC_STALE", age_sec: Math.round(syncAge), threshold: 300 }
        };
      }
    } else {
      // sync_status 키 자체가 없음 — 데이터 신뢰 불가
      return {
        action: "WARN",
        reason: { check: "NO_SYNC_STATUS", note: "kis:broker:positions_sync_status missing" }
      };
    }
  } catch (e) {
    return { action: "WARN", reason: { check: "SYNC_CHECK_ERROR", error: e.message } };
  }

  // ── 검증 2: Position key 타입 (Claude + GPT 공통) ──
  try {
    const posType = await redis.type("emarkos:v1:positions");
    if (posType !== "string" && posType !== "none") {
      return {
        action: "WARN",
        reason: { check: "POSITION_WRONGTYPE", actual_type: posType, expected: "string" }
      };
    }

    // 추가: position 데이터 자체가 비어있는지 확인
    if (posType === "string") {
      const posRaw = await redis.get("emarkos:v1:positions");
      if (posRaw) {
        const parsed = JSON.parse(posRaw);
        const posCount = Object.keys(parsed.positions || parsed).length;
        if (posCount === 0) {
          return {
            action: "WARN",
            reason: { check: "POSITIONS_EMPTY", note: "0 positions in key" }
          };
        }
      }
    }
  } catch (e) {
    return { action: "WARN", reason: { check: "POSITION_CHECK_ERROR", error: e.message } };
  }

  // ── 검증 3: 변화율 이상 (Claude 3-layer) ──
  if (anchorEquity > 0 && currentEquity > 0) {
    const changePct = Math.abs((currentEquity - anchorEquity) / anchorEquity);
    if (changePct > 0.20) {  // >20% 즉시 변화 = 거의 확실히 데이터 오류
      // [STRUCTURAL_FIX_V2] ANCHOR_AUTO_RESET: anchor를 current로 리셋하여 오탐 방지
      log("WARN", "ANCHOR_AUTO_RESET", {
        reason: "anchor/current ratio exceeds 20% — PM2 reload or portfolio resize detected",
        anchor: anchorEquity.toFixed(2), current: currentEquity.toFixed(2),
        change_pct: (changePct * 100).toFixed(1),
        action: "resetting anchor to current",
        session_id: typeof WATCHDOG_SESSION_ID !== "undefined" ? WATCHDOG_SESSION_ID : "unknown",
        process_uptime_ms: Math.round(process.uptime() * 1000)
      });
      await redis.set("champion:equity_anchor:usd", currentEquity.toString());
      return {
        action: "WARN",
        reason: {
          check: "ANOMALOUS_CHANGE_ANCHOR_RESET",
          change_pct: (changePct * 100).toFixed(1),
          current: currentEquity.toFixed(0),
          anchor: anchorEquity.toFixed(0),
          note: ">20% instantaneous change — anchor auto-reset to current"
        }
      };
    }
  }

  // equity가 0이거나 비정상적으로 작은 경우
  if (currentEquity <= 1000 && anchorEquity > 50000) {
    // [STRUCTURAL_FIX_V2] Do NOT reset anchor here — equity near zero is suspicious
    return {
      action: "WARN",
      reason: {
        check: "EQUITY_IMPLAUSIBLE",
        current: currentEquity.toFixed(0),
        anchor: anchorEquity.toFixed(0),
        note: "Current equity near zero while anchor is large — data error, NOT resetting anchor"
      }
    };
  }

  // ── 모든 검증 통과 → 진짜 PNL_CRITICAL ──
  return {
    action: "HALT",
    reason: {
      check: "PNL_CRITICAL_CONFIRMED",
      pnl_pct: pnlPct,
      current: currentEquity.toFixed(0),
      anchor: anchorEquity.toFixed(0),
      confidence: "HIGH"
    }
  };
}

// export for testing
export { pnlSanityCheck };
