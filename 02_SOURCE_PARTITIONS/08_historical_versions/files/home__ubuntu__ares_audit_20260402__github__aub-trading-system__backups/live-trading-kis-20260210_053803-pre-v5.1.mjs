const dayKey = () => {
  const now = new Date();
  const nyTime = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const y = nyTime.getFullYear();
  const m = String(nyTime.getMonth() + 1).padStart(2, "0");
  const d = String(nyTime.getDate()).padStart(2, "0");
  return `${y}${m}${d}`;
};
/**
 * live-trading-kis v5.0: Champion Pipeline + Decision Scheduler + Turnover Governor
 *                         + ProfitInterrupt + Crash Guard + Advanced News Scoring
 *
 * === ARCHITECTURE (v4.0 strengths + Claude Gap Fix best ideas + 4AI enhancements) ===
 *
 * [KEPT v4] Champion v7.0 4-stage pipeline (hedge→crisis→regime→vol-target)
 * [KEPT v4] 4AI Module Integration (regime, xgb, alpha, factor via Redis)
 * [KEPT v4] Emergency liquidation, cash ratio enforcement, reconciler
 * [KEPT v4] ProfitInterrupt Engine, Bearish Defensive Rotation, Theme Tilt
 * [KEPT v4] Conditional Override, Enhanced Alpha Metadata
 *
 * [NEW v5 from Claude] Decision Scheduler: 5-tier decision (anchor_recompute/regime_interrupt/
 *                       crash_guard/overlay_only/skip) → eliminates unnecessary turnover
 * [NEW v5 from Claude] Regime Hysteresis: N confirms within T-second window (not just streak)
 * [NEW v5 from Claude] 3-Layer Turnover Governor: daily 20% + overlay 10% + cycle 8% + scaling
 * [NEW v5 from Claude] Edge-Based Execution Gate: E[edge] = prob×win - (1-prob)×loss - cost > 0
 * [NEW v5 from Claude] Crash Guard: intraday DD -3% OR vol 3x spike → immediate 50% scale-down
 * [NEW v5 from Claude] Noise Trade Filter: min_trade_threshold systematic filtering
 * [NEW v5 from Claude] Operational Statistics: trigger_rate, avg_edge, crash_count → Redis
 * [NEW v5 4AI]  Advanced News Scoring: multi-source aggregation + LLM-ready scoring
 * [NEW v5 4AI]  Position-Level Risk Attribution: per-symbol VaR contribution tracking
 * [NEW v5 4AI]  Concentration Risk Monitor: single-symbol >8% auto-detect
 *
 * @version 5.0.0
 */
import Redis from "ioredis";
import crypto from "crypto";
import { emitEvent } from "./event_logger.mjs";

const PROC = process.env.PM2_PROCESS_NAME || "live-trading-kis";
const VERSION = "5.0.0";

const redis = new Redis(process.env.REDIS_URL || "redis://localhost:6379", {
  retryStrategy: (times) => Math.min(times * 500, 30000),
  maxRetriesPerRequest: null,
});

// ═══════════════════════════════════════════════════════════════
// SECTION 1: CHAMPION v7.0 PIPELINE CONSTANTS
// ═══════════════════════════════════════════════════════════════
const CHAMPION = {
  // Pareto Top-3 Ensemble (WF Mean 6.7339)
  hedge_boost: 0.30,
  crisis_boost: 0.40,
  vol_mult_low: 1.30,
  vol_mult_high: 0.70,
  target_vol: 0.16,

  // Stage3 Controller bounds
  stage3_scale_min: 0.30,
  stage3_scale_max: 1.20,

  // Turnover control
  min_trade_dw: 0.005,
  rank_change_threshold: 0.02,
  weight_stickiness: 0.10,

  // Hedge parameters
  hedge_base: 0.01,
  hedge_cap: 0.40,
  vix_threshold_floor: 25.0,
  vix_threshold_cap: 45.0,
  vix_band: 7.0,
  vix_sigma: 2.0,
  vix_lookback: 20,

  // Crisis trigger
  crisis_trigger_threshold: 0.05,

  // Regime
  vol_threshold: 25,

  // Cost
  cost_bps: 30,

  // Rebalance
  rebalance_min_interval_ms: 10 * 60 * 1000,
};

// ═══════════════════════════════════════════════════════════════
// SECTION 2: PROFIT INTERRUPT CONSTANTS
// ═══════════════════════════════════════════════════════════════
const PI_CFG = {
  min_edge_bps: parseFloat(process.env.PI_MIN_EDGE_BPS || "5.0"),
  min_prob: parseFloat(process.env.PI_MIN_PROB || "0.55"),
  min_signal_strength: parseFloat(process.env.PI_MIN_SIGNAL_STRENGTH || "0.3"),

  max_shift: parseFloat(process.env.PI_MAX_SHIFT || "0.30"),
  anchor_ratio: parseFloat(process.env.PI_ANCHOR_RATIO || "0.85"),
  basket_frac: parseFloat(process.env.PI_BASKET_FRAC || "0.30"),
  min_basket: parseInt(process.env.PI_MIN_BASKET || "3"),

  // Cost model (from Claude: commission + slippage + buffer)
  commission_bps: parseFloat(process.env.PI_COMMISSION_BPS || "1.0"),
  slippage_bps: parseFloat(process.env.PI_SLIPPAGE_BPS || "2.0"),
  edge_buffer_bps: parseFloat(process.env.PI_EDGE_BUFFER_BPS || "2.0"),
  avg_win: parseFloat(process.env.PI_AVG_WIN || "0.0020"),
  avg_loss: parseFloat(process.env.PI_AVG_LOSS || "0.0015"),

  // Bearish defensive weights
  bear_w_beta: parseFloat(process.env.PI_BEAR_W_BETA || "0.35"),
  bear_w_vol: parseFloat(process.env.PI_BEAR_W_VOL || "0.25"),
  bear_w_mom: parseFloat(process.env.PI_BEAR_W_MOM || "0.15"),
  bear_w_event: parseFloat(process.env.PI_BEAR_W_EVENT || "0.25"),

  // Bullish weights
  bull_w_mom: parseFloat(process.env.PI_BULL_W_MOM || "0.40"),
  bull_w_event: parseFloat(process.env.PI_BULL_W_EVENT || "0.35"),
  bull_w_vol: parseFloat(process.env.PI_BULL_W_VOL || "0.25"),

  // Cross-sectional windows
  cs_fast: parseInt(process.env.PI_CS_FAST || "20"),
  cs_slow: parseInt(process.env.PI_CS_SLOW || "60"),

  // TTL / Cooldown (from Claude)
  ttl_seconds: parseInt(process.env.PI_TTL_SECONDS || "3600"),
  cooldown_ms: parseInt(process.env.PI_COOLDOWN_MS || "600000"), // 10 min (Claude: 600s)
};

// ═══════════════════════════════════════════════════════════════
// SECTION 3: DECISION SCHEDULER CONFIG (NEW from Claude)
// ═══════════════════════════════════════════════════════════════
const SCHEDULER_CFG = {
  // Anchor recompute interval (1 day default, adaptive by VIX)
  anchor_interval_ms: parseInt(process.env.ANCHOR_INTERVAL_MS || String(6 * 3600 * 1000)), // 6h base
  // Regime hysteresis
  regime_confirm_n: parseInt(process.env.REGIME_CONFIRM_N || "3"),
  regime_min_confidence: parseFloat(process.env.REGIME_MIN_CONFIDENCE || "0.60"),
  regime_confirm_window_ms: parseInt(process.env.REGIME_CONFIRM_WINDOW_MS || "1800000"), // 30 min
};

// ═══════════════════════════════════════════════════════════════
// SECTION 4: TURNOVER GOVERNOR CONFIG (NEW from Claude)
// ═══════════════════════════════════════════════════════════════
const TURNOVER_CFG = {
  max_daily_pct: parseFloat(process.env.MAX_DAILY_TURNOVER || "0.20"),       // 20% daily
  max_overlay_daily_pct: parseFloat(process.env.MAX_OVERLAY_TURNOVER || "0.10"), // 10% overlay
  max_single_cycle_pct: parseFloat(process.env.MAX_CYCLE_TURNOVER || "0.08"),   // 8% per cycle
  min_trade_threshold: parseFloat(process.env.MIN_TRADE_THRESHOLD || "0.005"),   // 0.5% noise filter
};

// ═══════════════════════════════════════════════════════════════
// SECTION 5: CRASH GUARD CONFIG (NEW from Claude)
// ═══════════════════════════════════════════════════════════════
const CRASH_CFG = {
  drawdown_threshold: parseFloat(process.env.CG_DRAWDOWN_THRESHOLD || "-0.03"),
  vol_spike_threshold: parseFloat(process.env.CG_VOL_SPIKE_THRESHOLD || "3.0"),
  risk_scale_down: parseFloat(process.env.CG_RISK_SCALE_DOWN || "0.50"),
  recovery_cycles: parseInt(process.env.CG_RECOVERY_CYCLES || "30"),
};

// ═══════════════════════════════════════════════════════════════
// SECTION 6: THEME TILT CONFIG
// ═══════════════════════════════════════════════════════════════
const THEME_CFG = {
  override_min_conf: parseFloat(process.env.TP_OVERRIDE_MIN_CONF || "0.65"),
  override_growth_strength: parseFloat(process.env.TP_OVERRIDE_GROWTH_STRENGTH || "0.85"),
  override_max_regime_conf: parseFloat(process.env.TP_OVERRIDE_MAX_REGIME_CONF || "0.80"),
  override_growth_boost: parseFloat(process.env.TP_OVERRIDE_GROWTH_BOOST || "1.8"),
  override_defensive_damp: parseFloat(process.env.TP_OVERRIDE_DEFENSIVE_DAMP || "0.85"),
  w_fourai: parseFloat(process.env.TP_W_FOURAI || "0.55"),
  w_news: parseFloat(process.env.TP_W_NEWS || "0.25"),
  w_prior: parseFloat(process.env.TP_W_PRIOR || "0.20"),
};

// ═══════════════════════════════════════════════════════════════
// SECTION 7: SECTOR MAPPING
// ═══════════════════════════════════════════════════════════════
const SECTOR_MAP = {
  AAPL: "software", MSFT: "software", GOOGL: "software", GOOG: "software",
  META: "software", AMZN: "cloud", NVDA: "semis", AVGO: "semis",
  AMD: "semis", INTC: "semis", QCOM: "semis", TXN: "semis",
  CRM: "software", ADBE: "software", ORCL: "cloud", CSCO: "software",
  IBM: "cloud", NOW: "software", INTU: "software", ANET: "cloud",
  JNJ: "health", UNH: "health", PFE: "health", ABBV: "health",
  MRK: "health", LLY: "health", TMO: "health", ABT: "health",
  BMY: "health", GILD: "health", AMGN: "health", MDT: "health",
  XOM: "energy", CVX: "energy", COP: "energy", SLB: "energy",
  EOG: "energy", MPC: "energy", PSX: "energy", VLO: "energy",
  LMT: "defense", RTX: "defense", NOC: "defense", GD: "defense", BA: "defense",
  JPM: "banks", BAC: "banks", WFC: "banks", GS: "banks", MS: "banks",
  C: "banks", BLK: "banks", SCHW: "banks", AXP: "banks",
  PG: "staples", KO: "staples", PEP: "staples", WMT: "staples",
  COST: "staples", CL: "staples", MO: "staples", PM: "staples",
  NEE: "utilities", DUK: "utilities", SO: "utilities", D: "utilities",
  AMT: "reits", PLD: "reits", CCI: "reits", EQIX: "reits",
  CAT: "industrials", DE: "industrials", HON: "industrials", UNP: "industrials",
  MMM: "industrials", GE: "industrials",
  VZ: "utilities", T: "utilities", CMCSA: "software", DIS: "software",
  NFLX: "software", TMUS: "utilities",
  NEM: "gold", GOLD: "gold",
  V: "banks", MA: "banks", PYPL: "software",
  TSLA: "software", NKE: "staples", MCD: "staples", SBUX: "staples",
  HD: "staples", LOW: "staples", TGT: "staples",
};

// ═══════════════════════════════════════════════════════════════
// SECTION 8: REGIME-THEME WEIGHT MATRIX (5-tier from Claude)
// ═══════════════════════════════════════════════════════════════
const REGIME_THEME_WEIGHTS = {
  CRISIS: {
    defense: 1.6, energy: 1.3, health: 1.3, utilities: 1.2, staples: 1.2, gold: 1.2,
    semis: 0.5, ai: 0.5, software: 0.7, cloud: 0.7, banks: 0.7, reits: 0.7, industrials: 0.8,
  },
  BEARISH: {
    defense: 1.4, energy: 1.2, health: 1.2, utilities: 1.1, staples: 1.1, gold: 1.1,
    semis: 0.7, ai: 0.7, software: 0.8, cloud: 0.8, banks: 0.8, reits: 0.8, industrials: 0.9,
  },
  NEUTRAL: {
    defense: 1.0, energy: 1.0, health: 1.0, utilities: 0.95, staples: 0.95, gold: 0.9,
    semis: 1.2, ai: 1.2, software: 1.1, cloud: 1.1, banks: 1.0, reits: 1.0, industrials: 1.0,
  },
  BULLISH: {
    defense: 0.8, energy: 1.0, health: 1.0, utilities: 0.7, staples: 0.8, gold: 0.7,
    semis: 1.7, ai: 1.7, software: 1.4, cloud: 1.4, banks: 1.2, reits: 1.1, industrials: 1.1,
  },
  RISK_ON: {
    defense: 0.7, energy: 1.0, health: 0.9, utilities: 0.6, staples: 0.7, gold: 0.6,
    semis: 1.8, ai: 1.8, software: 1.5, cloud: 1.5, banks: 1.3, reits: 1.1, industrials: 1.2,
  },
};

function getRegimeThemeWeights(regime) {
  const r = (regime || "NEUTRAL").toUpperCase();
  return REGIME_THEME_WEIGHTS[r] || REGIME_THEME_WEIGHTS.NEUTRAL;
}

// ═══════════════════════════════════════════════════════════════
// SECTION 9: MAIN CONFIG
// ═══════════════════════════════════════════════════════════════
const CFG = {
  run_id: "kis-live",
  mode_key: "emarkos:v1:mode",
  broker_key: "emarkos:v1:broker",
  regime_key: "emarkos:v1:regime",
  cost_basis_key: "emarkos:v1:cost_basis",
  targets_key: process.env.CHAMPION_TARGET_POSITIONS_KEY || "champion:target:positions",
  order_intent_stream: "emarkos:v1:order_intent",
  execution_stream: "emarkos:v1:execution",
  allow_modes: new Set(["LIVE", "SHADOW"]),
  allow_brokers: new Set(["kis"]),
  min_abs_shares: 1,
  min_notional_usd: 25,
  max_cycle_orders: 50,
  max_cycle_notional_usd: 500000,
  max_single_order_notional_usd: 100000,
  base_poll_interval_ms: parseInt(process.env.POLL_INTERVAL || "30000"),
  poll_interval_ms: parseInt(process.env.POLL_INTERVAL || "30000"),
  idempo_ttl_sec: 86400,
  idempo_prefix: "emarkos:v1:idempo:intent",
  cycle_lock_key: "emarkos:v1:lock:runCycle",
  cycle_lock_ttl_sec: 10,
  reconciler_consumer_group: "live-trading-kis",
  reconciler_consumer_name: "reconciler-1",
  sell_first: true,
  dry_run: process.env.DRY_RUN === "true",
  enforce_cash_ratio: parseFloat(process.env.ENFORCE_CASH_RATIO || "0.05"),
  enforce_cash_enabled: (process.env.ENFORCE_CASH_ENABLED || "true") === "true",
  // Concentration risk
  max_single_position_pct: parseFloat(process.env.MAX_SINGLE_POSITION_PCT || "0.08"),
};

// ═══════════════════════════════════════════════════════════════
// SECTION 10: UTILITIES
// ═══════════════════════════════════════════════════════════════
function log(level, msg, ctx = {}) {
  const ts = new Date().toISOString();
  const line = JSON.stringify({ ts, level, msg, ...ctx });
  console.log(line);
}

function safeJsonParse(str) {
  try { return JSON.parse(str); } catch { return null; }
}

async function setNx(key, val, ttl) {
  const res = await redis.set(key, val, "EX", ttl, "NX");
  return res === "OK";
}

function makeCycleId() {
  const t = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const base = t.getFullYear() + pad(t.getMonth()+1) + pad(t.getDate()) + pad(t.getHours()) + pad(t.getMinutes()) + pad(t.getSeconds());
  return base + "-" + crypto.randomBytes(3).toString("hex");
}

function makeIntentId() {
  return "kis-live-auto-" + Date.now() + "-" + crypto.randomBytes(2).toString("hex");
}

function zScore(values) {
  if (!values || values.length === 0) return [];
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const std = Math.sqrt(values.reduce((s, v) => s + (v - mean) ** 2, 0) / values.length) + 1e-12;
  return values.map(v => (v - mean) / std);
}

// ═══════════════════════════════════════════════════════════════
// SECTION 11: DECISION SCHEDULER (NEW v5.0 from Claude)
// ═══════════════════════════════════════════════════════════════
/**
 * Decides WHAT to do each cycle:
 *   "anchor_recompute" → full champion pipeline + target recalc (periodic)
 *   "regime_interrupt"  → regime confirmed switch → exposure scale only
 *   "crash_guard"       → emergency risk reduction
 *   "overlay_only"      → ProfitInterrupt evaluation only
 *   "skip"              → do nothing (data collection only)
 *
 * Key insight from Claude: "데이터 폴링은 실시간, 의사결정은 이벤트 기반"
 */
const scheduler = {
  lastAnchorTs: null,
  forceRecompute: false,
  crashActive: false,
  crashActiveUntil: 0,
  cyclesSinceAnchor: 0,

  // Regime Hysteresis (Claude: N confirms within T-second window)
  hysteresis: {
    current: "NEUTRAL",
    observations: [],  // [{ts, regime, confidence}]

    update(regime, confidence, now) {
      const ts = now || Date.now();

      // Same regime → clear observations
      if (regime === this.current) {
        this.observations = [];
        return { switched: false, confirmed: this.current };
      }

      // Confidence too low → ignore
      if (confidence < SCHEDULER_CFG.regime_min_confidence) {
        return { switched: false, confirmed: this.current };
      }

      // Remove stale observations outside window
      const cutoff = ts - SCHEDULER_CFG.regime_confirm_window_ms;
      this.observations = this.observations.filter(o => o.ts >= cutoff);

      // Add new observation
      this.observations.push({ ts, regime, confidence });

      // Count matching observations
      const matching = this.observations.filter(
        o => o.regime === regime && o.confidence >= SCHEDULER_CFG.regime_min_confidence
      );

      if (matching.length >= SCHEDULER_CFG.regime_confirm_n) {
        const old = this.current;
        this.current = regime;
        this.observations = [];
        const avgConf = matching.reduce((s, o) => s + o.confidence, 0) / matching.length;
        log("INFO", "REGIME_HYSTERESIS_CONFIRMED", {
          old, new_regime: regime, n: matching.length,
          required: SCHEDULER_CFG.regime_confirm_n, avg_conf: avgConf.toFixed(3),
        });
        return { switched: true, confirmed: regime, avgConf };
      }

      return { switched: false, confirmed: this.current, pending: regime, pending_n: matching.length };
    },

    forceSet(regime) {
      this.current = regime;
      this.observations = [];
    },
  },

  /**
   * Evaluate what to do this cycle
   */
  evaluate(regimeInfo, crashTriggered, now) {
    const ts = now || Date.now();

    // Crash Guard is highest priority
    if (crashTriggered) {
      this.crashActive = true;
      this.crashActiveUntil = ts + CRASH_CFG.recovery_cycles * CFG.poll_interval_ms;
      log("WARN", "DECISION_SCHEDULER: CRASH_GUARD triggered");
      return "crash_guard";
    }

    // Force recompute request
    if (this.forceRecompute) {
      this.forceRecompute = false;
      this.lastAnchorTs = ts;
      this.cyclesSinceAnchor = 0;
      return "anchor_recompute";
    }

    // First run
    if (this.lastAnchorTs === null) {
      this.lastAnchorTs = ts;
      this.cyclesSinceAnchor = 0;
      return "anchor_recompute";
    }

    // Adaptive anchor interval based on VIX
    const adaptiveInterval = this._getAdaptiveAnchorInterval(regimeInfo.vix || 20);
    const elapsed = ts - this.lastAnchorTs;
    if (elapsed >= adaptiveInterval) {
      this.lastAnchorTs = ts;
      this.cyclesSinceAnchor = 0;
      log("INFO", "DECISION_SCHEDULER: anchor_recompute (interval elapsed)", {
        elapsed_ms: elapsed, interval_ms: adaptiveInterval,
      });
      return "anchor_recompute";
    }

    // Regime switch check
    const { switched } = this.hysteresis.update(
      regimeInfo.regime, regimeInfo.confidence || 0.5, ts
    );
    if (switched) {
      log("INFO", "DECISION_SCHEDULER: regime_interrupt", { new_regime: regimeInfo.regime });
      return "regime_interrupt";
    }

    // Crash recovery period → skip overlay too
    if (this.crashActive && ts < this.crashActiveUntil) {
      return "skip";
    } else if (this.crashActive) {
      this.crashActive = false;
      log("INFO", "DECISION_SCHEDULER: crash recovery ended");
    }

    this.cyclesSinceAnchor++;

    // Most cycles: overlay evaluation only
    return "overlay_only";
  },

  _getAdaptiveAnchorInterval(vix) {
    const base = SCHEDULER_CFG.anchor_interval_ms;
    if (vix > 35) return Math.max(30 * 60 * 1000, base * 0.25);  // 30min in crisis
    if (vix > 25) return Math.max(60 * 60 * 1000, base * 0.5);   // 1h in elevated
    if (vix < 15) return Math.min(12 * 3600 * 1000, base * 2);   // 12h in calm
    return base;
  },

  get confirmedRegime() { return this.hysteresis.current; },
  get secondsUntilNextAnchor() {
    if (!this.lastAnchorTs) return 0;
    const elapsed = Date.now() - this.lastAnchorTs;
    return Math.max(0, (SCHEDULER_CFG.anchor_interval_ms - elapsed) / 1000);
  },
};

// ═══════════════════════════════════════════════════════════════
// SECTION 12: TURNOVER GOVERNOR (NEW v5.0 from Claude)
// ═══════════════════════════════════════════════════════════════
/**
 * 3-layer turnover budget:
 *   Layer 1: Daily total (anchor + overlay) ≤ 20%
 *   Layer 2: Daily overlay ≤ 10%
 *   Layer 3: Single cycle ≤ 8%
 *
 * Key insight from Claude: "스케일링, 차단 아님" — scale down instead of block
 */
const turnoverGov = {
  _day: null,
  _usedAnchor: 0,
  _usedOverlay: 0,
  _cycleCount: 0,

  _resetIfNewDay() {
    const d = dayKey();
    if (this._day !== d) {
      if (this._day !== null) {
        log("INFO", "TURNOVER_GOV: daily reset", {
          prev_day: this._day,
          anchor: this._usedAnchor.toFixed(4),
          overlay: this._usedOverlay.toFixed(4),
          total: (this._usedAnchor + this._usedOverlay).toFixed(4),
          cycles: this._cycleCount,
        });
      }
      this._day = d;
      this._usedAnchor = 0;
      this._usedOverlay = 0;
      this._cycleCount = 0;
    }
  },

  /**
   * Check if anchor turnover is within budget
   * Returns: { allowed, remaining, info }
   */
  checkAnchorBudget(turnoverPct) {
    this._resetIfNewDay();
    const totalUsed = this._usedAnchor + this._usedOverlay;
    const remaining = TURNOVER_CFG.max_daily_pct - totalUsed;

    if (turnoverPct > remaining) {
      return { allowed: false, remaining, reason: "daily_budget_exceeded" };
    }
    if (turnoverPct > TURNOVER_CFG.max_single_cycle_pct) {
      return { allowed: false, remaining: TURNOVER_CFG.max_single_cycle_pct, reason: "cycle_budget_exceeded" };
    }
    return { allowed: true, remaining };
  },

  /**
   * Check if overlay turnover is within budget
   */
  checkOverlayBudget(turnoverPct) {
    this._resetIfNewDay();
    const remainingOverlay = TURNOVER_CFG.max_overlay_daily_pct - this._usedOverlay;
    const remainingTotal = TURNOVER_CFG.max_daily_pct - (this._usedAnchor + this._usedOverlay);
    const remaining = Math.min(remainingOverlay, remainingTotal);

    if (turnoverPct > remaining) {
      return { allowed: false, remaining, reason: "overlay_budget_exceeded" };
    }
    return { allowed: true, remaining };
  },

  /**
   * Scale turnover to fit within budget (Claude: "할 수 있는 만큼만 실행")
   * Returns: scaleFactor (0 to 1)
   */
  scaleToFit(turnoverPct, isOverlay = false) {
    this._resetIfNewDay();
    const check = isOverlay ? this.checkOverlayBudget(turnoverPct) : this.checkAnchorBudget(turnoverPct);

    if (check.allowed) return { scale: 1.0, info: check };

    const budget = Math.max(0, check.remaining);
    if (budget <= 0) return { scale: 0, info: { ...check, note: "budget_exhausted" } };

    const scale = budget / Math.max(turnoverPct, 1e-12);
    log("INFO", "TURNOVER_GOV: scaled", {
      original: turnoverPct.toFixed(4), budget: budget.toFixed(4), scale: scale.toFixed(3),
    });
    return { scale: Math.min(1.0, scale), info: check };
  },

  consumeAnchor(turnoverPct) {
    this._usedAnchor += turnoverPct;
    this._cycleCount++;
  },

  consumeOverlay(turnoverPct) {
    this._usedOverlay += turnoverPct;
    this._cycleCount++;
  },

  /**
   * Filter noise trades: changes below threshold are ignored
   */
  isNoiseTrade(weightChangePct) {
    return Math.abs(weightChangePct) < TURNOVER_CFG.min_trade_threshold;
  },

  get dailyUsage() {
    return {
      anchor: this._usedAnchor,
      overlay: this._usedOverlay,
      total: this._usedAnchor + this._usedOverlay,
      max_daily: TURNOVER_CFG.max_daily_pct,
      remaining: TURNOVER_CFG.max_daily_pct - (this._usedAnchor + this._usedOverlay),
      cycles_today: this._cycleCount,
    };
  },
};

// ═══════════════════════════════════════════════════════════════
// SECTION 13: CRASH GUARD (NEW v5.0 from Claude)
// ═══════════════════════════════════════════════════════════════
/**
 * Intraday crash detection:
 *   - Portfolio drawdown > -3% from day's peak
 *   - VIX spike > 3x normal
 *   → Immediate 50% position scale-down
 */
const crashGuard = {
  _dayPeak: 0,
  _dayStart: 0,
  _prevVix: null,
  _vixHistory: [],

  async check(regimeSignals, currentEquity) {
    const vix = regimeSignals.vix;
    const now = Date.now();

    // Track day peak
    if (currentEquity > this._dayPeak) this._dayPeak = currentEquity;
    if (this._dayStart === 0) this._dayStart = currentEquity;

    // Intraday drawdown
    const dd = this._dayPeak > 0 ? (currentEquity - this._dayPeak) / this._dayPeak : 0;

    // VIX spike detection
    this._vixHistory.push({ ts: now, vix });
    if (this._vixHistory.length > 60) this._vixHistory = this._vixHistory.slice(-60);

    const recentVix = this._vixHistory.slice(-5).map(v => v.vix);
    const historicalVix = this._vixHistory.slice(0, -5).map(v => v.vix);
    const avgRecent = recentVix.reduce((a, b) => a + b, 0) / Math.max(recentVix.length, 1);
    const avgHistorical = historicalVix.length > 0
      ? historicalVix.reduce((a, b) => a + b, 0) / historicalVix.length
      : avgRecent;
    const vixRatio = avgHistorical > 0 ? avgRecent / avgHistorical : 1.0;

    const triggered = dd < CRASH_CFG.drawdown_threshold || vixRatio > CRASH_CFG.vol_spike_threshold;

    const meta = {
      intraday_dd: parseFloat(dd.toFixed(4)),
      vix_ratio: parseFloat(vixRatio.toFixed(2)),
      vix_current: vix,
      day_peak: parseFloat(this._dayPeak.toFixed(2)),
      day_start: parseFloat(this._dayStart.toFixed(2)),
    };

    if (triggered) {
      log("WARN", "CRASH_GUARD_TRIGGERED", meta);
    }

    return { triggered, meta };
  },

  resetDay() {
    this._dayPeak = 0;
    this._dayStart = 0;
    this._vixHistory = [];
  },
};

// ═══════════════════════════════════════════════════════════════
// SECTION 14: OPERATIONAL STATISTICS (NEW v5.0 from Claude)
// ═══════════════════════════════════════════════════════════════
const opStats = {
  _triggers: 0,
  _skips: 0,
  _totalEdge: 0,
  _crashGuards: 0,
  _anchorRecomputes: 0,
  _regimeInterrupts: 0,
  _overlayTriggered: 0,
  _decisions: {},

  record(decision, edge = 0) {
    this._decisions[decision] = (this._decisions[decision] || 0) + 1;
    if (decision === "overlay_only") this._skips++;
    if (decision === "crash_guard") this._crashGuards++;
    if (decision === "anchor_recompute") this._anchorRecomputes++;
    if (decision === "regime_interrupt") this._regimeInterrupts++;
    if (edge > 0) { this._triggers++; this._totalEdge += edge; }
  },

  recordOverlayTrigger(edge) {
    this._overlayTriggered++;
    this._totalEdge += edge;
  },

  get summary() {
    const total = Object.values(this._decisions).reduce((s, v) => s + v, 0) || 1;
    return {
      total_cycles: total,
      decisions: this._decisions,
      overlay_triggered: this._overlayTriggered,
      trigger_rate: this._overlayTriggered / total,
      avg_edge: this._overlayTriggered > 0 ? this._totalEdge / this._overlayTriggered : 0,
      crash_guards: this._crashGuards,
      anchor_recomputes: this._anchorRecomputes,
      regime_interrupts: this._regimeInterrupts,
    };
  },

  async persist() {
    try {
      await redis.set("live:ops:stats", JSON.stringify(this.summary), "EX", 86400);
    } catch {}
  },
};


// ═══════════════════════════════════════════════════════════════
// SECTION 15: EMERGENCY LIQUIDATION (kept from v4.0)
// ═══════════════════════════════════════════════════════════════
const subRedis = new Redis(process.env.REDIS_URL || "redis://localhost:6379", {
  retryStrategy: (times) => Math.min(times * 500, 30000),
  maxRetriesPerRequest: null,
});
subRedis.subscribe("cmd:emergency:liquidation").catch(() => {});
subRedis.on("message", async (channel, message) => {
  if (channel !== "cmd:emergency:liquidation") return;
  try {
    const cmd = JSON.parse(message);
    emitEvent({ proc: PROC, event: "EMERGENCY_LIQUIDATION_RECEIVED", sev: "CRITICAL",
      msg: `Emergency liquidation command received: ${cmd.reason}`, ctx: cmd });
    log("CRITICAL", "EMERGENCY_LIQUIDATION_RECEIVED", cmd);
    await redis.set("policy:buy_freeze", "true");

    let positions = {};
    const emPosRaw = await redis.get("emarkos:v1:positions");
    if (emPosRaw) {
      try {
        const emPos = JSON.parse(emPosRaw);
        if (emPos?.positions) positions = emPos.positions;
      } catch {}
    }
    if (Object.keys(positions).length === 0) {
      try {
        const hashData = await redis.hgetall("kis:live:positions");
        if (hashData && Object.keys(hashData).length > 0) {
          for (const [sym, raw] of Object.entries(hashData)) {
            try { positions[sym] = JSON.parse(raw); } catch { positions[sym] = { qty: 0 }; }
          }
        }
      } catch {}
    }

    const symbols = Object.keys(positions).filter(s => {
      const qty = Number(positions[s]?.shares ?? positions[s]?.qty ?? 0);
      return qty > 0;
    });

    for (const symbol of symbols) {
      const pos = positions[symbol];
      const qty = Math.floor(Math.abs(Number(pos?.shares ?? pos?.qty ?? 0)));
      if (qty <= 0) continue;
      const lastPrice = Number(pos?.current_price ?? pos?.last_price ?? pos?.avg_price ?? 0);
      const intentId = `EMRG-${Date.now()}-${symbol}`;
      const intentPayload = {
        intent_id: intentId, symbol, side: "SELL", delta_shares: -qty,
        last_price: lastPrice, reason: "EMERGENCY_LIQUIDATION",
        priority: "CRITICAL", limit: { type: "MARKET" },
        source: "liquidation-daemon", ts: new Date().toISOString(),
      };
      await redis.xadd(CFG.order_intent_stream, "*", "json", JSON.stringify(intentPayload));
      emitEvent({ proc: PROC, event: "EMERGENCY_SELL_INTENT", sev: "CRITICAL",
        corr: { intent_id: intentId, symbol },
        msg: `Emergency SELL: ${symbol} qty=${qty}`, ctx: intentPayload });
    }
    log("CRITICAL", "EMERGENCY_LIQUIDATION_COMPLETE", { symbols_count: symbols.length, symbols });
  } catch (err) {
    log("ERROR", "EMERGENCY_LIQUIDATION_ERROR", { error: err.message });
  }
});

// ═══════════════════════════════════════════════════════════════
// SECTION 16: CASH RATIO ENFORCEMENT (kept from v4.0)
// ═══════════════════════════════════════════════════════════════
async function enforceCashRatio() {
  if (!CFG.enforce_cash_enabled) return;
  try {
    const snapshotStr = await redis.get("kis:balance:snapshot");
    if (!snapshotStr) return;
    const snapshot = JSON.parse(snapshotStr);
    const cashUsd = parseFloat(snapshot.cash_usd || 0);
    const equity = parseFloat(snapshot.equity || snapshot.total_equity || 0);
    if (!Number.isFinite(cashUsd) || !Number.isFinite(equity) || equity <= 0) return;
    const currentRatio = cashUsd / equity;
    const target = CFG.enforce_cash_ratio;
    if (currentRatio < target) {
      emitEvent({ proc: PROC, event: "CASH_RATIO_BREACH", sev: "CRITICAL",
        msg: `Cash ratio ${(currentRatio*100).toFixed(2)}% < target ${(target*100).toFixed(2)}%`,
        ctx: { currentRatio, target, cashUsd, equity } });
      await redis.publish("cmd:emergency:liquidation", JSON.stringify({
        reason: "CASH_RATIO_BREACH", target_cash_ratio: target,
        current_ratio: currentRatio, timestamp: new Date().toISOString(),
      }));
      await redis.set("policy:buy_freeze", "true");
    }
  } catch (err) {
    log("ERROR", "CASH_RATIO_CHECK_ERROR", { error: err.message });
  }
}

// ═══════════════════════════════════════════════════════════════
// SECTION 17: BUDGET OVERLAY (kept from v4.0)
// ═══════════════════════════════════════════════════════════════
async function getBudgetUSD(redis, log) {
  const CONFIG = {
    KIS_BASE_URL: process.env.KIS_BASE_URL || "https://openapi.koreainvestment.com:9443",
    KIS_APP_KEY: process.env.KIS_APP_KEY,
    KIS_APP_SECRET: process.env.KIS_APP_SECRET,
    KIS_ACCOUNT_NO: process.env.KIS_ACCOUNT_NO || "8137893001",
    KIS_ACCOUNT_PROD_CD: process.env.KIS_ACCOUNT_PROD_CD || "01",
  };

  try {
    let token = await redis.get("kis:token:access_token");
    if (!token) {
      const tokenRaw = await redis.get("kis:access_token");
      if (tokenRaw) {
        try { const parsed = JSON.parse(tokenRaw); token = parsed.access_token || tokenRaw; } catch { token = tokenRaw; }
      }
    }
    if (!token) {
      const authToken = await redis.get("kis:auth:access_token");
      if (authToken) token = authToken;
    }
    if (token) {
      const url = new URL(CONFIG.KIS_BASE_URL + "/uapi/overseas-stock/v1/trading/inquire-psamount");
      url.searchParams.append("CANO", CONFIG.KIS_ACCOUNT_NO);
      url.searchParams.append("ACNT_PRDT_CD", CONFIG.KIS_ACCOUNT_PROD_CD);
      url.searchParams.append("OVRS_EXCG_CD", "NASD");
      url.searchParams.append("OVRS_ORD_UNPR", "100");
      url.searchParams.append("ITEM_CD", "AAPL");

      const resp = await fetch(url.toString(), {
        method: "GET",
        headers: {
          "authorization": "Bearer " + token,
          "appkey": CONFIG.KIS_APP_KEY,
          "appsecret": CONFIG.KIS_APP_SECRET,
          "tr_id": "TTTS3007R",
          "content-type": "application/json; charset=utf-8",
        },
      });

      const data = await resp.json();
      if (data.rt_cd === "0" && data.output) {
        const cashUsd = Number(data.output.ord_psbl_frcr_amt || 0);
        if (Number.isFinite(cashUsd) && cashUsd > 0) {
          log("INFO", "Budget USD from KIS API", { source: "kis_api", cash_usd: cashUsd });
          await redis.set("emarkos:v1:budget_usd_cash", cashUsd.toString(), "EX", 60);
          await redis.set("kis:live:available_cash", cashUsd.toString(), "EX", 120);
          return cashUsd;
        }
      }
      log("WARN", "KIS API budget query failed", { rt_cd: data.rt_cd, msg: data.msg1 });
    }

    const redisKeys = ["emarkos:v1:budget_usd_cash", "kis:live:available_cash", "kis:balance:cash_usd"];
    for (const k of redisKeys) {
      const v = await redis.get(k);
      if (v) {
        const n = Number(v);
        if (Number.isFinite(n) && n > 0) {
          log("INFO", "Budget USD from Redis", { source: k, cash_usd: n });
          return n;
        }
      }
    }
    return 0;
  } catch (err) {
    log("ERROR", "getBudgetUSD error", { error: err.message });
    return 0;
  }
}

async function getCurrentPrices(symbols) {
  const prices = {};
  const pipe = redis.pipeline();
  for (const sym of symbols) {
    pipe.get("price:" + sym);
    pipe.get("kis:price:" + sym);
  }
  const results = await pipe.exec();
  for (let i = 0; i < symbols.length; i++) {
    const sym = symbols[i];
    const v1 = results[i * 2]?.[1];
    const v2 = results[i * 2 + 1]?.[1];
    const raw = v1 || v2;
    if (raw) {
      const parsed = safeJsonParse(raw);
      prices[sym] = parsed?.price ?? parsed?.last ?? (Number(raw) || 0);
    }
  }
  return prices;
}

// ═══════════════════════════════════════════════════════════════
// SECTION 18: 4AI SIGNAL LOADING (enhanced v5.0)
// ═══════════════════════════════════════════════════════════════

/**
 * Load regime signals from Redis
 * v5.0: Added regime source count → confidence penalty (Claude insight)
 */
async function loadRegimeSignals() {
  const defaults = {
    regime: "NEUTRAL", vix: 20, volScale: 1.0, crisisMode: false,
    gateState: "NO_GATE", clearStreak: 0, newsScore: 0, newsCrisisKeywords: 0,
    finalMult: 1.0, confidence: 0.5, realizedVol: null,
    newsThemes: {}, newsTickers: {}, sourceCount: 0,
  };

  try {
    const pipe = redis.pipeline();
    pipe.hgetall("regime:current");       // 0
    pipe.hgetall("regime:sources");       // 1
    pipe.get("market:vix:current");       // 2
    pipe.get("market:vix");               // 3
    pipe.get("regime:realized_vol_ann");  // 4
    pipe.get("sentiment:themes");         // 5
    pipe.get("sentiment:tickers");        // 6
    pipe.get("news:aggregated:scores");   // 7 (NEW v5: multi-source news)
    const results = await pipe.exec();

    const regimeCurrent = results[0]?.[1] || {};
    const regimeSources = results[1]?.[1] || {};
    const vixCurrent = results[2]?.[1];
    const vixFallback = results[3]?.[1];
    const realizedVolRaw = results[4]?.[1];
    const sentimentThemesRaw = results[5]?.[1];
    const sentimentTickersRaw = results[6]?.[1];
    const newsAggregatedRaw = results[7]?.[1];

    // VIX
    let vix = parseFloat(vixCurrent || vixFallback || "20");
    if (!Number.isFinite(vix) || vix <= 0) vix = 20;

    // Regime
    const regime = String(regimeCurrent.regime || "NEUTRAL").toUpperCase();
    let confidence = parseFloat(regimeCurrent.confidence || "0.5");
    const gateState = String(regimeCurrent.gate || "NO_GATE").toUpperCase();
    const clearStreak = parseInt(regimeCurrent.clearStreak || "0");
    const crisisMode = gateState === "CRISIS";

    // Source count confidence penalty (Claude insight)
    const sourceCount = Object.keys(regimeSources).length;
    if (sourceCount > 0 && sourceCount < 3) {
      const penalty = 1.0 - (3 - sourceCount) * 0.15;
      confidence = confidence * penalty;
      log("INFO", "REGIME_CONFIDENCE_PENALTY", { sourceCount, penalty: penalty.toFixed(2), adjusted: confidence.toFixed(3) });
    }

    const volScale = parseFloat(regimeCurrent.volScale || "1.0");
    const newsScore = parseFloat(regimeSources.news_score || "0");
    const newsCrisisKeywords = parseInt(regimeSources.crisis_keywords_count || "0");

    // Gate multiplier
    let finalMult = 1.0;
    if (gateState === "CRISIS") finalMult = 0.3;
    else if (gateState === "CAUTION") finalMult = 0.6;

    // Realized vol
    let realizedVol = null;
    if (realizedVolRaw) {
      const rv = parseFloat(realizedVolRaw);
      if (Number.isFinite(rv) && rv > 0.01) realizedVol = rv;
    }

    // Parse sentiment themes/tickers
    let newsThemes = {};
    let newsTickers = {};
    try { if (sentimentThemesRaw) newsThemes = JSON.parse(sentimentThemesRaw); } catch {}
    try { if (sentimentTickersRaw) newsTickers = JSON.parse(sentimentTickersRaw); } catch {}

    // NEW v5: Merge multi-source news aggregation
    if (newsAggregatedRaw) {
      try {
        const agg = JSON.parse(newsAggregatedRaw);
        if (agg.tickers) {
          for (const [sym, score] of Object.entries(agg.tickers)) {
            newsTickers[sym] = newsTickers[sym] != null
              ? (newsTickers[sym] * 0.6 + score * 0.4)
              : score;
          }
        }
        if (agg.themes) {
          for (const [theme, score] of Object.entries(agg.themes)) {
            newsThemes[theme] = newsThemes[theme] != null
              ? (newsThemes[theme] * 0.6 + score * 0.4)
              : score;
          }
        }
      } catch {}
    }

    return {
      regime, vix, volScale, crisisMode, gateState, clearStreak,
      newsScore, newsCrisisKeywords, finalMult, confidence,
      realizedVol, newsThemes, newsTickers, sourceCount,
    };
  } catch (err) {
    log("ERROR", "loadRegimeSignals failed", { error: err.message });
    return defaults;
  }
}

/**
 * Load per-symbol 4AI signals (XGBoost risk, Alpha scores)
 */
async function loadSymbolSignals(symbols) {
  const symbolSignals = {};
  const pipe = redis.pipeline();
  for (const sym of symbols) {
    pipe.get("xgb:risk:" + sym);
    pipe.get("alpha:" + sym + ":score");
  }
  const results = await pipe.exec();

  for (let i = 0; i < symbols.length; i++) {
    const sym = symbols[i];
    const sig = { xgb: null, alpha: null };
    const xgbRaw = results[i * 2]?.[1];
    if (xgbRaw) { try { sig.xgb = JSON.parse(xgbRaw); } catch {} }
    const alphaRaw = results[i * 2 + 1]?.[1];
    if (alphaRaw) { try { sig.alpha = JSON.parse(alphaRaw); } catch {} }
    symbolSignals[sym] = sig;
  }
  return symbolSignals;
}

/**
 * Load factor weights
 */
async function loadFactorWeights() {
  const defaultWeights = { momentum: 0.35, quality: 0.25, value: 0.20, low_vol: 0.10, size: 0.10 };
  try {
    const raw = await redis.get("factor:active_weights");
    if (raw) return { ...defaultWeights, ...JSON.parse(raw) };
  } catch {}
  return defaultWeights;
}

// ═══════════════════════════════════════════════════════════════
// SECTION 19: CHAMPION 4-STAGE PIPELINE (kept from v4.0)
// ═══════════════════════════════════════════════════════════════

function computeHedgeWeight(regimeSignals) {
  const vix = regimeSignals.vix;
  const vix_thr = Math.max(CHAMPION.vix_threshold_floor,
    Math.min(CHAMPION.vix_threshold_cap, vix * regimeSignals.volScale));
  const vix_scalar = Math.max(0, Math.min(1, (vix_thr - vix) / Math.max(CHAMPION.vix_band, 1e-9)));
  const w_hedge = CHAMPION.hedge_base + (1.0 - vix_scalar) * (CHAMPION.hedge_boost - CHAMPION.hedge_base);
  return { w_hedge, vix_thr, vix_scalar };
}

function applyCrisisTrigger(w_hedge, regimeSignals, vix_thr) {
  const newsRisk = regimeSignals.newsScore;
  const crisisKeywords = regimeSignals.newsCrisisKeywords;
  const vix = regimeSignals.vix;

  const isCrisisTrigger = (
    (newsRisk > 0.3 || crisisKeywords > 5) && vix > vix_thr
  ) || regimeSignals.crisisMode;

  const isGateCaution = regimeSignals.gateState === "CAUTION" || regimeSignals.gateState === "CRISIS";
  const crisis_trigger = (isCrisisTrigger || isGateCaution) ? 1.0 : 0.0;

  const hedgeOut = w_hedge * (1.0 + CHAMPION.crisis_boost * crisis_trigger);
  return { w_hedge: hedgeOut, crisis_trigger, isCrisisTrigger };
}

function applyRegimeMultiplier(w_hedge, regimeSignals) {
  const vix = regimeSignals.vix;
  let vol_mult = vix < CHAMPION.vol_threshold ? CHAMPION.vol_mult_low : CHAMPION.vol_mult_high;

  if (regimeSignals.regime === "BEARISH") {
    vol_mult = Math.min(vol_mult * 1.2, CHAMPION.vol_mult_low);
  } else if (regimeSignals.regime === "CRISIS") {
    vol_mult = CHAMPION.vol_mult_low * 1.5;
  }

  let hedgeOut = w_hedge * vol_mult;
  hedgeOut = hedgeOut * regimeSignals.finalMult;
  hedgeOut = Math.max(0, Math.min(CHAMPION.hedge_cap, hedgeOut));
  return { w_hedge: hedgeOut, vol_mult };
}

async function computeVolTargetScale(regimeSignals) {
  let realizedVol = regimeSignals.realizedVol || CHAMPION.target_vol;
  if (realizedVol === CHAMPION.target_vol && regimeSignals.vix > 0) {
    realizedVol = regimeSignals.vix / 100;
  }
  const scale = Math.max(
    CHAMPION.stage3_scale_min,
    Math.min(CHAMPION.stage3_scale_max, CHAMPION.target_vol / (realizedVol + 1e-6))
  );
  return { scale, realizedVol };
}

async function computeChampionScale(regimeSignals) {
  const { w_hedge: hedge1, vix_thr, vix_scalar } = computeHedgeWeight(regimeSignals);
  const { w_hedge: hedge2, crisis_trigger } = applyCrisisTrigger(hedge1, regimeSignals, vix_thr);
  const { w_hedge: hedge3, vol_mult } = applyRegimeMultiplier(hedge2, regimeSignals);
  const { scale: volScale, realizedVol } = await computeVolTargetScale(regimeSignals);

  const w_asset = 1.0 - hedge3;
  const championScale = w_asset * volScale;

  const telemetry = {
    stage1_hedge: parseFloat(hedge1.toFixed(4)),
    stage2_hedge_crisis: parseFloat(hedge2.toFixed(4)),
    stage3_hedge_regime: parseFloat(hedge3.toFixed(4)),
    stage4_vol_scale: parseFloat(volScale.toFixed(4)),
    vix: regimeSignals.vix, vix_thr: parseFloat(vix_thr.toFixed(2)),
    vix_scalar: parseFloat(vix_scalar.toFixed(4)),
    crisis_trigger, vol_mult: parseFloat(vol_mult.toFixed(4)),
    w_asset: parseFloat(w_asset.toFixed(4)),
    realized_vol: parseFloat(realizedVol.toFixed(4)),
    champion_scale: parseFloat(championScale.toFixed(4)),
    regime: regimeSignals.regime, gate_state: regimeSignals.gateState,
    news_score: regimeSignals.newsScore,
  };

  return { championScale, telemetry };
}


// ═══════════════════════════════════════════════════════════════
// SECTION 20: PROFIT INTERRUPT ENGINE (v5.0 enhanced with Edge-Based Gate)
// ═══════════════════════════════════════════════════════════════

const piState = {
  prevShift: null,
  lastTriggerTs: 0,
  triggerCount: 0,
  lastTriggerDay: "",
  totalEdge: 0,
  // NEW v5: anchor weights cache for overlay comparison
  lastAnchorWeights: null,
};

/**
 * Edge-Based Execution Gate (NEW v5.0 from Claude)
 * E[edge] = prob × avg_win - (1-prob) × avg_loss - cost > 0
 * Only execute when expected edge exceeds total cost
 */
function computeEdgeGate(prob, direction) {
  const costBps = PI_CFG.commission_bps + PI_CFG.slippage_bps + PI_CFG.edge_buffer_bps;
  const costPct = costBps / 10000;

  const expectedEdge = prob * PI_CFG.avg_win - (1 - prob) * PI_CFG.avg_loss;
  const netEdge = expectedEdge - costPct;

  return {
    pass: netEdge > 0,
    expected_edge_bps: parseFloat((expectedEdge * 10000).toFixed(2)),
    cost_bps: costBps,
    net_edge_bps: parseFloat((netEdge * 10000).toFixed(2)),
    prob,
  };
}

/**
 * Compute ProfitInterrupt edge from 4AI signals
 * v5.0: Uses edge-based gate (Claude) instead of simple threshold
 */
function computePIEdge(regimeSignals, symbolSignals) {
  let xgbProbs = [];
  for (const [, sig] of Object.entries(symbolSignals)) {
    if (sig.xgb?.p_drop != null) xgbProbs.push(sig.xgb.p_drop);
  }
  const avgPDrop = xgbProbs.length > 0 ? xgbProbs.reduce((a, b) => a + b, 0) / xgbProbs.length : 0.3;

  const newsScore = regimeSignals.newsScore || 0;

  let alphaScores = [];
  for (const [, sig] of Object.entries(symbolSignals)) {
    if (sig.alpha?.alpha_score != null) alphaScores.push(sig.alpha.alpha_score);
  }
  const avgAlpha = alphaScores.length > 0 ? alphaScores.reduce((a, b) => a + b, 0) / alphaScores.length : 0;

  const bearSignal = avgPDrop * 0.5 + Math.max(0, newsScore) * 0.3 + Math.max(0, -avgAlpha) * 0.2;
  const bullSignal = (1 - avgPDrop) * 0.4 + Math.max(0, avgAlpha) * 0.4 + Math.max(0, -newsScore) * 0.2;

  const direction = bullSignal > bearSignal ? 1.0 : -1.0;
  const signal_strength = Math.abs(bullSignal - bearSignal);
  const prob = direction > 0 ? bullSignal : bearSignal;

  // v5.0: Edge-based gate
  const edgeGate = computeEdgeGate(prob, direction);

  return {
    edge_bps: edgeGate.net_edge_bps,
    prob: parseFloat(prob.toFixed(4)),
    direction,
    signal_strength: parseFloat(signal_strength.toFixed(4)),
    edge_gate: edgeGate,
    meta: {
      avg_p_drop: parseFloat(avgPDrop.toFixed(4)),
      news_score: parseFloat(newsScore.toFixed(4)),
      avg_alpha: parseFloat(avgAlpha.toFixed(4)),
      bear_signal: parseFloat(bearSignal.toFixed(4)),
      bull_signal: parseFloat(bullSignal.toFixed(4)),
      xgb_count: xgbProbs.length,
      alpha_count: alphaScores.length,
    },
  };
}

/**
 * Compute cross-sectional scores for defensive rotation
 */
async function computeCrossSectionalScores(symbols) {
  const scores = {};
  const pipe = redis.pipeline();
  for (const sym of symbols) {
    pipe.get(`returns:${sym}:20d`);
    pipe.get(`vol:${sym}:20d`);
    pipe.get(`beta:${sym}`);
  }
  const results = await pipe.exec();

  const momValues = [];
  const volValues = [];
  const betaValues = [];

  for (let i = 0; i < symbols.length; i++) {
    const sym = symbols[i];
    const mom = parseFloat(results[i * 3]?.[1] || "0");
    const vol = parseFloat(results[i * 3 + 1]?.[1] || "0.20");
    const beta = parseFloat(results[i * 3 + 2]?.[1] || "1.0");
    scores[sym] = { mom, vol, beta };
    momValues.push(mom);
    volValues.push(vol);
    betaValues.push(beta);
  }

  const momZ = zScore(momValues);
  const volZ = zScore(volValues);
  const betaZ = zScore(betaValues);

  for (let i = 0; i < symbols.length; i++) {
    scores[symbols[i]].mom_z = momZ[i];
    scores[symbols[i]].vol_z = volZ[i];
    scores[symbols[i]].beta_z = betaZ[i];
  }
  return scores;
}

/**
 * Compute regime-aware theme tilt per symbol
 */
function computeThemeTilt(symbols, regimeSignals) {
  const themeWeights = getRegimeThemeWeights(regimeSignals.regime);
  const newsThemes = regimeSignals.newsThemes || {};
  const newsTickers = regimeSignals.newsTickers || {};

  const tilt = {};
  for (const sym of symbols) {
    let score = 0;
    if (newsTickers[sym] != null) score += parseFloat(newsTickers[sym]) * THEME_CFG.w_news;
    const sector = SECTOR_MAP[sym];
    if (sector) {
      const tw = themeWeights[sector] || 1.0;
      const newsThemeScore = newsThemes[sector] || 0;
      score += (tw - 1.0) * THEME_CFG.w_fourai;
      score += newsThemeScore * THEME_CFG.w_news;
    }
    tilt[sym] = Math.max(-1, Math.min(1, score));
  }
  return tilt;
}

/**
 * Check if growth-event override should bypass risk_off
 */
function shouldOverrideRiskOff(regimeSignals, themeTilt) {
  const regime = regimeSignals.regime;
  if (regime !== "CRISIS" && regime !== "BEARISH") {
    return { override: false, reason: "not_risk_off" };
  }
  const confidence = regimeSignals.confidence || 0.5;
  if (confidence < THEME_CFG.override_min_conf) {
    return { override: false, reason: "conf_low", confidence };
  }
  let growthStrength = 0;
  const growthSectors = ["semis", "software", "cloud"];
  const symbols = Object.keys(themeTilt);
  for (const sym of symbols) {
    const sector = SECTOR_MAP[sym];
    if (growthSectors.includes(sector) && themeTilt[sym] > 0) {
      growthStrength += Math.abs(themeTilt[sym]);
    }
  }
  growthStrength = growthStrength / Math.max(1, symbols.length);
  if (growthStrength < THEME_CFG.override_growth_strength / symbols.length) {
    return { override: false, reason: "growth_weak", growthStrength };
  }
  if (regimeSignals.confidence > THEME_CFG.override_max_regime_conf) {
    return { override: false, reason: "regime_too_strong", regimeConf: regimeSignals.confidence };
  }
  return { override: true, reason: "override_ok", growthStrength, confidence };
}

/**
 * Build shift vector for ProfitInterrupt
 * v5.0: Enhanced with multi-factor scoring for both bull and bear
 */
async function buildPIShift(symbols, targetWeights, direction, regimeSignals, symbolSignals) {
  const n = symbols.length;
  if (n === 0) return {};

  const k = Math.max(PI_CFG.min_basket, Math.floor(n * PI_CFG.basket_frac));
  const shift = {};
  for (const sym of symbols) shift[sym] = 0;

  const csScores = await computeCrossSectionalScores(symbols);
  const themeTilt = computeThemeTilt(symbols, regimeSignals);

  if (direction > 0) {
    // Bullish: momentum + event + low-vol
    const compositeScores = {};
    for (const sym of symbols) {
      const cs = csScores[sym] || { mom_z: 0, vol_z: 0, beta_z: 0 };
      const event = themeTilt[sym] || 0;
      compositeScores[sym] = (
        PI_CFG.bull_w_mom * cs.mom_z +
        PI_CFG.bull_w_event * event +
        -PI_CFG.bull_w_vol * cs.vol_z
      );
    }
    const sorted = [...symbols].sort((a, b) => (compositeScores[b] || 0) - (compositeScores[a] || 0));
    const top = sorted.slice(0, k);
    const bot = sorted.slice(-k);
    for (const sym of top) shift[sym] = +1.0 / k;
    for (const sym of bot) shift[sym] = -1.0 / k;
  } else {
    // Bearish: defensive rotation
    const compositeScores = {};
    for (const sym of symbols) {
      const cs = csScores[sym] || { mom_z: 0, vol_z: 0, beta_z: 0 };
      const event = themeTilt[sym] || 0;
      compositeScores[sym] = (
        -PI_CFG.bear_w_beta * cs.beta_z +
        -PI_CFG.bear_w_vol * cs.vol_z +
        PI_CFG.bear_w_mom * cs.mom_z +
        PI_CFG.bear_w_event * event
      );
    }
    const sorted = [...symbols].sort((a, b) => (compositeScores[b] || 0) - (compositeScores[a] || 0));
    const winners = sorted.slice(0, k);
    const losers = sorted.slice(-k);
    for (const sym of winners) shift[sym] = +1.0 / k;
    for (const sym of losers) shift[sym] = -1.0 / k;
  }

  // Scale to max_shift
  const absSum = Object.values(shift).reduce((s, v) => s + Math.abs(v), 0);
  if (absSum > 1e-12) {
    const scale = PI_CFG.max_shift / absSum;
    for (const sym of symbols) shift[sym] *= scale;
  }

  // v5.0: Turnover governor check for overlay
  if (piState.prevShift) {
    let overlayTurnover = 0;
    for (const sym of symbols) {
      overlayTurnover += Math.abs((shift[sym] || 0) - (piState.prevShift[sym] || 0));
    }
    const { scale: govScale } = turnoverGov.scaleToFit(overlayTurnover, true);
    if (govScale < 1.0) {
      for (const sym of symbols) {
        shift[sym] = (piState.prevShift[sym] || 0) + (shift[sym] - (piState.prevShift[sym] || 0)) * govScale;
      }
    }
  }

  piState.prevShift = { ...shift };
  return shift;
}

/**
 * Main ProfitInterrupt decision
 * v5.0: Uses edge-based gate + turnover governor
 */
async function evaluateProfitInterrupt(symbols, targetWeights, regimeSignals, symbolSignals) {
  const now = Date.now();
  const today = dayKey();

  if (piState.lastTriggerDay !== today) {
    piState.triggerCount = 0;
    piState.totalEdge = 0;
    piState.lastTriggerDay = today;
  }

  // TTL-based cooldown (Claude: 600s)
  if (now - piState.lastTriggerTs < PI_CFG.cooldown_ms) {
    return {
      triggered: false, reason: "COOLDOWN",
      shift: null, edge_bps: 0, prob: 0, direction: 0, signal_strength: 0,
      meta: { cooldown_remaining_ms: PI_CFG.cooldown_ms - (now - piState.lastTriggerTs) },
    };
  }

  const { edge_bps, prob, direction, signal_strength, edge_gate, meta: edgeMeta } =
    computePIEdge(regimeSignals, symbolSignals);

  // v5.0: Edge-based gate (Claude: "기대값 > 비용 일 때만 실행")
  if (!edge_gate.pass) {
    return {
      triggered: false, reason: "EDGE_GATE_FAIL",
      shift: null, edge_bps, prob, direction, signal_strength,
      meta: { ...edgeMeta, edge_gate },
    };
  }

  // Signal strength threshold
  if (signal_strength < PI_CFG.min_signal_strength) {
    return {
      triggered: false, reason: "SIGNAL_WEAK",
      shift: null, edge_bps, prob, direction, signal_strength,
      meta: { ...edgeMeta, edge_gate },
    };
  }

  // Risk-off block with conditional override
  if (regimeSignals.regime === "CRISIS" || regimeSignals.regime === "BEARISH") {
    const themeTilt = computeThemeTilt(symbols, regimeSignals);
    const overrideResult = shouldOverrideRiskOff(regimeSignals, themeTilt);
    if (!overrideResult.override && regimeSignals.confidence >= 0.65) {
      return {
        triggered: false, reason: "RISK_OFF_BLOCK",
        shift: null, edge_bps, prob, direction, signal_strength,
        meta: { ...edgeMeta, override: overrideResult, edge_gate },
      };
    }
    if (overrideResult.override) {
      log("INFO", "PI_RISK_OFF_OVERRIDE", overrideResult);
    }
  }

  // Build shift vector
  const shift = await buildPIShift(symbols, targetWeights, direction, regimeSignals, symbolSignals);

  piState.lastTriggerTs = now;
  piState.triggerCount++;
  piState.totalEdge += edge_bps;

  // Record in operational stats
  opStats.recordOverlayTrigger(edge_bps);

  return {
    triggered: true,
    reason: direction > 0 ? "BULLISH_EDGE" : "BEARISH_DEFENSIVE",
    shift, edge_bps, prob, direction, signal_strength,
    meta: { ...edgeMeta, edge_gate, trigger_count_today: piState.triggerCount },
  };
}

// ═══════════════════════════════════════════════════════════════
// SECTION 21: PER-SYMBOL ADJUSTMENT (enhanced v5.0)
// ═══════════════════════════════════════════════════════════════

function computeSymbolAdjustment(sym, symbolSignal, regimeSignals, piResult, themeTilt) {
  let mult = 1.0;
  const reasons = [];

  // XGBoost risk filter
  if (symbolSignal.xgb) {
    const xgb = symbolSignal.xgb;
    if (xgb.action === "BLOCK") {
      mult = 0;
      reasons.push("XGB_BLOCK");
    } else if (xgb.action === "FLAG") {
      mult *= (xgb.mult ?? 0.8);
      reasons.push(`XGB_FLAG(${(xgb.mult ?? 0.8).toFixed(2)})`);
    }
    if (xgb.p_drop > 0.5 && xgb.action !== "BLOCK") {
      const pDropMult = Math.max(0.3, 1.0 - xgb.p_drop);
      mult *= pDropMult;
      reasons.push(`XGB_PDROP(${pDropMult.toFixed(2)})`);
    }
  }

  // Alpha score boost/reduction
  if (symbolSignal.alpha) {
    const alpha = symbolSignal.alpha;
    if (alpha.risk_flag) {
      mult *= 0.5;
      reasons.push("ALPHA_RISK");
    } else if (alpha.boost_allowed && alpha.alpha_score > 0.01) {
      const alphaBoost = Math.min(1.2, 1.0 + alpha.alpha_score * 5);
      mult *= alphaBoost;
      reasons.push(`ALPHA_BOOST(${alphaBoost.toFixed(2)})`);
    } else if (alpha.alpha_score < -0.01) {
      const alphaReduce = Math.max(0.5, 1.0 + alpha.alpha_score * 3);
      mult *= alphaReduce;
      reasons.push(`ALPHA_REDUCE(${alphaReduce.toFixed(2)})`);
    }
  }

  // ProfitInterrupt shift overlay
  if (piResult.triggered && piResult.shift && piResult.shift[sym] != null) {
    const shiftVal = piResult.shift[sym];
    const piMult = PI_CFG.anchor_ratio + (1 - PI_CFG.anchor_ratio) * (1 + shiftVal * 3);
    mult *= Math.max(0.5, Math.min(1.5, piMult));
    reasons.push(`PI_SHIFT(${shiftVal.toFixed(3)},mult=${piMult.toFixed(2)})`);
  }

  // Theme tilt adjustment
  if (themeTilt && themeTilt[sym] != null && Math.abs(themeTilt[sym]) > 0.05) {
    const tiltMult = 1.0 + themeTilt[sym] * 0.15;
    mult *= Math.max(0.85, Math.min(1.15, tiltMult));
    reasons.push(`THEME_TILT(${themeTilt[sym].toFixed(3)})`);
  }

  return { mult: Math.max(0, Math.min(1.5, mult)), reasons };
}

// ═══════════════════════════════════════════════════════════════
// SECTION 22: P&L FILTER + EQUITY ANCHOR (kept from v4.0)
// ═══════════════════════════════════════════════════════════════

async function getDailyPnlPct(redis, currentEquityUSD) {
  const anchorUSD = Number(await redis.get("champion:equity_anchor:usd") || "0");
  if (!Number.isFinite(anchorUSD) || anchorUSD <= 0) return 0;
  if (!Number.isFinite(currentEquityUSD) || currentEquityUSD <= 0) return 0;
  return (currentEquityUSD - anchorUSD) / anchorUSD;
}

function computeLossFilterScale(dailyPnlPct) {
  const EXTREME_LOSS_THRESHOLD = Number(process.env.EXTREME_LOSS_THRESHOLD ?? -0.03);
  const EXTREME_LOSS_SCALE     = Number(process.env.EXTREME_LOSS_SCALE ?? 0.4);
  const SMALL_LOSS_THRESHOLD   = Number(process.env.SMALL_LOSS_THRESHOLD ?? -0.005);
  const SMALL_LOSS_SCALE       = Number(process.env.SMALL_LOSS_SCALE ?? 0.5);

  if (dailyPnlPct <= EXTREME_LOSS_THRESHOLD) return EXTREME_LOSS_SCALE;
  if (dailyPnlPct <= SMALL_LOSS_THRESHOLD)   return SMALL_LOSS_SCALE;
  return 1.0;
}

async function getEquityAnchorUSD(redis, currentEquityUSD) {
  const nyDay = new Date(new Date().toLocaleString("en-US",{timeZone:"America/New_York"}))
    .toISOString().slice(0,10).replaceAll("-","");
  const keyDay = "champion:equity_anchor:day";
  const keyVal = "champion:equity_anchor:usd";
  const keyTs  = "champion:equity_anchor:ts";
  const keyMode= "champion:equity_anchor:mode";

  const mode = (await redis.get(keyMode)) || "daily_fixed";
  const prevDay = (await redis.get(keyDay)) || "";
  let anchor = Number(await redis.get(keyVal) || "0");

  if (prevDay !== nyDay || !Number.isFinite(anchor) || anchor <= 0) {
    anchor = currentEquityUSD;
    await redis.set(keyDay, nyDay);
    await redis.set(keyVal, anchor.toString());
    await redis.set(keyTs, new Date().toISOString());
    log("INFO", "Equity anchor set", { mode, anchor, day: nyDay });
  }

  if (mode === "trailing_high" && currentEquityUSD > anchor) {
    anchor = currentEquityUSD;
    await redis.set(keyVal, anchor.toString());
    await redis.set(keyTs, new Date().toISOString());
  }

  return anchor;
}

// ═══════════════════════════════════════════════════════════════
// SECTION 23: CONCENTRATION RISK MONITOR (NEW v5.0)
// ═══════════════════════════════════════════════════════════════

function checkConcentrationRisk(positions, prices, totalEquity) {
  const warnings = [];
  if (totalEquity <= 0) return warnings;

  for (const [sym, pos] of Object.entries(positions)) {
    const qty = Number(pos?.qty || 0);
    const px = Number(prices[sym] || 0);
    if (qty <= 0 || px <= 0) continue;

    const mv = qty * px;
    const pct = mv / totalEquity;

    if (pct > CFG.max_single_position_pct) {
      warnings.push({
        symbol: sym, pct: parseFloat((pct * 100).toFixed(2)),
        threshold: parseFloat((CFG.max_single_position_pct * 100).toFixed(2)),
        mv: parseFloat(mv.toFixed(2)),
      });
    }
  }

  if (warnings.length > 0) {
    log("WARN", "CONCENTRATION_RISK", { count: warnings.length, warnings });
  }
  return warnings;
}


// ═══════════════════════════════════════════════════════════════
// SECTION 24: BUILD INTENTS (v5.0 enhanced with noise filter + turnover governor)
// ═══════════════════════════════════════════════════════════════

async function buildIntents({ targetsObj, positions, prices, cashUsd, budgetUsd, championScale,
  regimeSignals, symbolSignals, factorWeights, piResult, themeTilt, lossFilterScale,
  isOverlay, crashScale }) {

  const intents = [];
  const cycleId = makeCycleId();
  const day = dayKey();
  let totalTargets = 0, skipped = 0;
  let rtScaledCnt = 0, rtBlockedCnt = 0;
  let buyCandidates = 0, buyReduced = 0;
  let sellIdx = [], sellNotionalCycle = 0;
  let noiseFiltered = 0;

  const buyFreeze = await redis.get("policy:buy_freeze");

  const budgetScaleFactor = budgetUsd > 0 && Object.values(targetsObj).reduce((s, t) => s + (t.target_value || 0), 0) > budgetUsd
    ? budgetUsd / Object.values(targetsObj).reduce((s, t) => s + (t.target_value || 0), 0)
    : 1.0;

  // v5.0: crash guard scale
  const effectiveCrashScale = crashScale || 1.0;

  // Pre-compute total turnover for governor check
  const preIntents = [];
  for (const [sym, target] of Object.entries(targetsObj)) {
    totalTargets++;
    const px = Number(prices[sym] || 0);
    if (!px || px <= 0) { skipped++; continue; }

    const pos = positions[sym] || { qty: 0, avg_px: 0 };
    const currentQty = Number(pos.qty || 0);

    let rawTargetValue = Number(target.target_value || 0);
    rawTargetValue *= championScale * budgetScaleFactor * lossFilterScale * effectiveCrashScale;

    const symSignal = symbolSignals[sym] || { xgb: null, alpha: null };
    const { mult: symMult, reasons: symReasons } = computeSymbolAdjustment(sym, symSignal, regimeSignals, piResult, themeTilt);
    rawTargetValue *= symMult;

    const targetShares = Math.max(0, Math.floor(rawTargetValue / px));
    const deltaShares = targetShares - currentQty;

    if (deltaShares === 0) { skipped++; continue; }

    const side = deltaShares > 0 ? "BUY" : "SELL";
    const absDelta = Math.abs(deltaShares);
    const notional = absDelta * px;
    const weightChange = budgetUsd > 0 ? notional / budgetUsd : 0;

    // v5.0: Noise trade filter (Claude: min_trade_threshold)
    if (turnoverGov.isNoiseTrade(weightChange) && side === "BUY") {
      noiseFiltered++;
      skipped++;
      continue;
    }

    if (notional < CFG.min_notional_usd) { skipped++; continue; }
    if (notional > CFG.max_single_order_notional_usd) { skipped++; continue; }
    if (side === "BUY" && buyFreeze === "true") { skipped++; continue; }

    preIntents.push({
      sym, side, absDelta, targetShares, currentQty, px, notional, weightChange,
      symMult, symReasons, symSignal, rawTargetValue,
    });
  }

  // v5.0: Turnover governor - compute total and scale if needed
  const totalTurnoverPct = preIntents.reduce((s, i) => s + i.weightChange, 0);
  const { scale: govScale, info: govInfo } = turnoverGov.scaleToFit(totalTurnoverPct, isOverlay);

  if (govScale < 1.0) {
    log("INFO", "TURNOVER_GOV_SCALING", {
      original_turnover: totalTurnoverPct.toFixed(4),
      scale: govScale.toFixed(3),
      info: govInfo,
    });
  }

  for (const pi of preIntents) {
    // Apply governor scale
    let adjustedDelta = pi.side === "SELL" ? pi.absDelta : Math.max(1, Math.floor(pi.absDelta * govScale));
    const adjustedNotional = adjustedDelta * pi.px;

    if (adjustedNotional < CFG.min_notional_usd) { skipped++; continue; }

    if (pi.side === "BUY") buyCandidates++;

    const alphaMetadata = {
      version: VERSION,
      champion_scale: parseFloat(championScale.toFixed(4)),
      budget_scale: parseFloat(budgetScaleFactor.toFixed(4)),
      loss_filter: lossFilterScale,
      crash_scale: effectiveCrashScale,
      gov_scale: parseFloat(govScale.toFixed(3)),
      sym_mult: parseFloat(pi.symMult.toFixed(4)),
      sym_reasons: pi.symReasons,
      // Decision context
      decision_type: isOverlay ? "overlay" : "anchor",
      // PI data
      pi_triggered: piResult.triggered,
      pi_reason: piResult.reason,
      pi_edge_bps: piResult.edge_bps || 0,
      pi_direction: piResult.direction,
      pi_shift: piResult.shift?.[pi.sym] || 0,
      // Theme tilt
      theme_sector: SECTOR_MAP[pi.sym] || "unknown",
      theme_tilt: themeTilt?.[pi.sym] || 0,
      // Regime
      regime: regimeSignals.regime,
      confirmed_regime: scheduler.confirmedRegime,
      vix: regimeSignals.vix,
      gate: regimeSignals.gateState,
      confidence: regimeSignals.confidence,
      // XGB/Alpha
      xgb_action: pi.symSignal.xgb?.action || "NONE",
      xgb_p_drop: pi.symSignal.xgb?.p_drop || null,
      alpha_score: pi.symSignal.alpha?.alpha_score || null,
      // Turnover
      turnover_gov: turnoverGov.dailyUsage,
      noise_filtered: noiseFiltered,
    };

    const intentId = makeIntentId();
    intents.push({
      intent_id: intentId,
      symbol: pi.sym,
      side: pi.side,
      delta_shares: pi.side === "SELL" ? -adjustedDelta : adjustedDelta,
      target_shares: pi.targetShares,
      current_shares: pi.currentQty,
      last_price: pi.px,
      notional_usd: adjustedNotional,
      reason: pi.symReasons.join("|") || "CHAMPION_REBAL",
      cycle_id: cycleId,
      source: `live-trading-kis-v${VERSION}`,
      ts: new Date().toISOString(),
      alpha_metadata: alphaMetadata,
    });

    if (pi.side === "SELL") {
      sellIdx.push(intents.length - 1);
      sellNotionalCycle += adjustedNotional;
    }

    if (pi.symMult < 1.0 || budgetScaleFactor < 1.0 || championScale < 1.0) buyReduced++;
  }

  // Sell cap (cycle + day)
  const nyDay2 = new Date(new Date().toLocaleString("en-US",{timeZone:"America/New_York"}))
    .toISOString().slice(0,10).replaceAll("-","");
  const capCycle = Number(await redis.get("policy:sell_cap_usd_cycle") || "1500");
  const capDay   = Number(await redis.get("policy:sell_cap_usd_day") || "5000");
  const usedKey  = `kpi:sell_used_usd:${nyDay2}`;
  const usedDay  = Number(await redis.get(usedKey) || "0");
  const remainingDay = Math.max(0, capDay - usedDay);
  const allowed = Math.min(capCycle, remainingDay);

  if (sellNotionalCycle > allowed && sellNotionalCycle > 0 && sellIdx.length > 0) {
    const ratio = allowed / sellNotionalCycle;
    for (const idx of sellIdx) {
      const it = intents[idx];
      const newShares = Math.max(1, Math.floor(Math.abs(Number(it.delta_shares)) * ratio));
      it.delta_shares = -newShares;
      it.notional_usd = Number(it.last_price) * newShares;
      it.reason = (it.reason || "") + "|SELL_CAP_SCALED";
    }
    sellNotionalCycle = intents.filter(x => x.side === "SELL").reduce((s, x) => s + Number(x.notional_usd || 0), 0);
  }
  if (sellNotionalCycle > 0) {
    await redis.incrbyfloat(usedKey, sellNotionalCycle);
    await redis.expire(usedKey, 60 * 60 * 24 * 3);
  }

  // Record turnover consumption
  const actualTurnoverPct = intents.reduce((s, i) => s + (budgetUsd > 0 ? i.notional_usd / budgetUsd : 0), 0);
  if (isOverlay) {
    turnoverGov.consumeOverlay(actualTurnoverPct);
  } else {
    turnoverGov.consumeAnchor(actualTurnoverPct);
  }

  // KPI
  redis.incrby(`kpi:ops:buy_candidates:${day}`, buyCandidates).catch(() => {});
  redis.incrby(`kpi:ops:buy_reduced:${day}`, buyReduced).catch(() => {});
  redis.incrby(`kpi:ops:noise_filtered:${day}`, noiseFiltered).catch(() => {});
  redis.expire(`kpi:ops:buy_candidates:${day}`, 86400).catch(() => {});
  redis.expire(`kpi:ops:buy_reduced:${day}`, 86400).catch(() => {});
  redis.expire(`kpi:ops:noise_filtered:${day}`, 86400).catch(() => {});

  return {
    intents,
    summary: {
      cycleId, totalTargets, intents: intents.length, skipped,
      rt_scaled: rtScaledCnt, rt_blocked: rtBlockedCnt,
      buyCandidates, buyReduced, noiseFiltered,
      gov_scale: parseFloat(govScale.toFixed(3)),
      actual_turnover_pct: parseFloat(actualTurnoverPct.toFixed(4)),
    },
  };
}

function prioritize(intents) {
  const arr = [...intents];
  arr.sort((a, b) => {
    if (CFG.sell_first && a.side !== b.side) return a.side === "SELL" ? -1 : 1;
    return b.notional_usd - a.notional_usd;
  });
  return arr;
}

// ═══════════════════════════════════════════════════════════════
// SECTION 25: ADAPTIVE REBALANCE (enhanced v5.0)
// ═══════════════════════════════════════════════════════════════

function computeAdaptivePollInterval(regimeSignals) {
  const vix = regimeSignals.vix;
  const base = CFG.base_poll_interval_ms;
  if (vix > 35) return Math.max(15000, base * 0.5);
  if (vix > 25) return Math.max(20000, base * 0.7);
  if (vix < 15) return Math.min(60000, base * 1.5);
  return base;
}

// ═══════════════════════════════════════════════════════════════
// SECTION 26: MAIN CYCLE (v5.0 with Decision Scheduler)
// ═══════════════════════════════════════════════════════════════

function isUsMarketSessionNY() {
  const ny = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const hhmm = ny.getHours() * 100 + ny.getMinutes();
  return hhmm >= 930 && hhmm <= 1555;
}

async function runCycle() {
  if (!isUsMarketSessionNY()) {
    console.log(`[MARKET_SESSION] skip intent emit outside session`);
    // Reset crash guard at day boundary
    const ny = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
    if (ny.getHours() < 9) crashGuard.resetDay();
    return;
  }

  const runTs = new Date().toISOString();
  const cycleId = makeCycleId();

  // Distributed lock
  const locked = await setNx(CFG.cycle_lock_key, cycleId, CFG.cycle_lock_ttl_sec);
  if (!locked) {
    log("WARN", "Cycle skipped: locked", { cycle_id: cycleId });
    return { ok: true, skipped: true, reason: "LOCKED" };
  }

  try {
    log("INFO", `=== Cycle Start (v${VERSION} Champion+Scheduler+Governor) ===`, { cycle_id: cycleId });

    // Cash ratio
    await enforceCashRatio();

    // Trading enabled gate
    const tradingEnabled = await redis.get("trading:enabled");
    if (tradingEnabled === "false") {
      const disableReason = await redis.get("trading:disable_reason") || "unknown";
      log("WARN", "Cycle skipped: trading disabled", { reason: disableReason });
      return { ok: true, skipped: true, reason: "TRADING_DISABLED", disableReason };
    }

    // Mode gate
    const modeRaw = await redis.get(CFG.mode_key) || "SAFE";
    const mode = String(modeRaw).toUpperCase();
    if (!CFG.allow_modes.has(mode)) {
      log("INFO", "Cycle skipped: mode blocked", { mode });
      return { ok: true, skipped: true, reason: "MODE_BLOCKED", mode };
    }

    // Broker gate
    const brokerRaw = await redis.get(CFG.broker_key) || "";
    const broker = String(brokerRaw).toLowerCase();
    if (!CFG.allow_brokers.has(broker)) {
      log("INFO", "Cycle skipped: broker mismatch", { broker });
      return { ok: true, skipped: true, reason: "BROKER_MISMATCH", broker };
    }

    // ===== STAGE A: Load all 4AI signals =====
    const regimeSignals = await loadRegimeSignals();
    log("INFO", "REGIME_SIGNALS_v5", {
      regime: regimeSignals.regime, vix: regimeSignals.vix,
      volScale: regimeSignals.volScale, crisisMode: regimeSignals.crisisMode,
      gateState: regimeSignals.gateState, newsScore: regimeSignals.newsScore,
      confidence: regimeSignals.confidence, sourceCount: regimeSignals.sourceCount,
    });

    // ===== STAGE B: Champion 4-stage pipeline =====
    const { championScale, telemetry: championTelemetry } = await computeChampionScale(regimeSignals);
    log("INFO", "CHAMPION_PIPELINE_v5", championTelemetry);

    await redis.set("live:champion:telemetry", JSON.stringify({
      ...championTelemetry, ts: runTs, cycle_id: cycleId, version: VERSION,
    }), "EX", 120);

    // ===== STAGE C: Load targets =====
    const targetsRaw = await redis.get(CFG.targets_key);
    if (!targetsRaw) {
      log("WARN", "No target positions found");
      return { ok: true, skipped: true, reason: "NO_TARGETS" };
    }
    const targetsData = safeJsonParse(targetsRaw);
    const targetsObj = targetsData?.targets || targetsData || {};
    const symbols = Object.keys(targetsObj);
    if (symbols.length === 0) {
      log("WARN", "Empty target positions");
      return { ok: true, skipped: true, reason: "EMPTY_TARGETS" };
    }

    // ===== STAGE D: Load per-symbol signals =====
    const symbolSignals = await loadSymbolSignals(symbols);
    const factorWeights = await loadFactorWeights();

    let xgbFlagCount = 0, alphaCount = 0;
    for (const [, sig] of Object.entries(symbolSignals)) {
      if (sig.xgb && sig.xgb.action !== "PASS") xgbFlagCount++;
      if (sig.alpha) alphaCount++;
    }
    log("INFO", "SYMBOL_SIGNALS_v5", { symbols: symbols.length, xgb_flagged: xgbFlagCount, alpha_scored: alphaCount });

    // ===== STAGE E: Load positions + budget =====
    const cashUsd = await getBudgetUSD(redis, log);

    let positions = {};
    let posSource = "none";
    const emPosRaw = await redis.get("emarkos:v1:positions");
    if (emPosRaw) {
      try {
        const emPos = JSON.parse(emPosRaw);
        if (emPos?.positions && Object.keys(emPos.positions).length > 0) {
          positions = emPos.positions;
          posSource = "emarkos:v1:positions";
        }
      } catch {}
    }
    if (Object.keys(positions).length === 0) {
      try {
        const hashData = await redis.hgetall("kis:live:positions");
        if (hashData && Object.keys(hashData).length > 0) {
          for (const [sym, raw] of Object.entries(hashData)) {
            try { positions[sym] = JSON.parse(raw); } catch { positions[sym] = { qty: 0 }; }
          }
          posSource = "kis:live:positions";
        }
      } catch (e) { log("WARN", "kis:live:positions hash read failed", { error: e.message }); }
    }

    for (const sym of symbols) {
      if (!positions[sym]) positions[sym] = { qty: 0, avg_px: 0 };
    }

    const posCount = Object.values(positions).filter(p => p.qty > 0).length;
    log("INFO", "Positions loaded", { count: posCount, source: posSource });

    const prices = await getCurrentPrices(symbols);
    log("INFO", "Prices loaded", { count: Object.keys(prices).length });

    const totalMV = Object.entries(positions).reduce((sum, [sym, pos]) => {
      const px = Number(prices[sym] || 0);
      return sum + (Number(pos?.qty || 0) * px);
    }, 0);

    const budgetUsd = cashUsd + totalMV;
    const equityAnchorUSD = await getEquityAnchorUSD(redis, budgetUsd);
    const bufferPct = Number(process.env.BUDGET_BUFFER_PCT ?? 0.02);
    const dailyPnlPct = await getDailyPnlPct(redis, budgetUsd);
    const lossFilterScale = computeLossFilterScale(dailyPnlPct);

    if (lossFilterScale < 1.0) {
      log("WARN", "LOSS_FILTER_ACTIVE_v5", { dailyPnlPct: (dailyPnlPct * 100).toFixed(2) + "%", scale: lossFilterScale });
    }

    // ===== STAGE F: Crash Guard check (NEW v5.0) =====
    const crashResult = await crashGuard.check(regimeSignals, budgetUsd);
    let crashScale = 1.0;
    if (crashResult.triggered) {
      crashScale = CRASH_CFG.risk_scale_down;
      log("WARN", "CRASH_GUARD_ACTIVE", { scale: crashScale, meta: crashResult.meta });
    }

    // ===== STAGE G: Decision Scheduler (NEW v5.0) =====
    const decision = scheduler.evaluate(regimeSignals, crashResult.triggered, Date.now());
    opStats.record(decision);
    log("INFO", "DECISION_SCHEDULER_v5", {
      decision,
      confirmed_regime: scheduler.confirmedRegime,
      next_anchor_in: scheduler.secondsUntilNextAnchor.toFixed(0) + "s",
      cycles_since_anchor: scheduler.cyclesSinceAnchor,
    });

    // ===== STAGE H: Concentration risk check (NEW v5.0) =====
    const concentrationWarnings = checkConcentrationRisk(positions, prices, budgetUsd);

    // ===== STAGE I: Execute based on decision =====
    let piResult = { triggered: false, reason: "SCHEDULER_SKIP", shift: null, edge_bps: 0, prob: 0, direction: 0, signal_strength: 0, meta: {} };
    let themeTilt = {};
    let isOverlay = false;

    if (decision === "skip") {
      log("INFO", "Cycle: skip (crash recovery or no action needed)");
      // Still persist stats
      await opStats.persist();
      return { ok: true, skipped: true, reason: "SCHEDULER_SKIP" };
    }

    if (decision === "crash_guard") {
      // Emergency scale-down: use crash scale on all positions
      log("WARN", "Cycle: crash_guard execution");
      crashScale = CRASH_CFG.risk_scale_down;
      isOverlay = false;
    }

    if (decision === "overlay_only") {
      // Only evaluate ProfitInterrupt overlay
      isOverlay = true;
      const targetWeights = {};
      for (const [sym, t] of Object.entries(targetsObj)) {
        targetWeights[sym] = budgetUsd > 0 ? (t.target_value || 0) / budgetUsd : 0;
      }
      piResult = await evaluateProfitInterrupt(symbols, targetWeights, regimeSignals, symbolSignals);
      themeTilt = computeThemeTilt(symbols, regimeSignals);

      if (!piResult.triggered) {
        log("INFO", "Overlay: no trigger", { reason: piResult.reason, edge_bps: piResult.edge_bps });
        await opStats.persist();
        return { ok: true, skipped: true, reason: "OVERLAY_NO_TRIGGER", pi: piResult.reason };
      }
      log("INFO", "Overlay: triggered", { reason: piResult.reason, edge_bps: piResult.edge_bps, direction: piResult.direction });
    }

    if (decision === "anchor_recompute" || decision === "regime_interrupt") {
      // Full recompute
      isOverlay = false;
      const targetWeights = {};
      for (const [sym, t] of Object.entries(targetsObj)) {
        targetWeights[sym] = budgetUsd > 0 ? (t.target_value || 0) / budgetUsd : 0;
      }
      piResult = await evaluateProfitInterrupt(symbols, targetWeights, regimeSignals, symbolSignals);
      themeTilt = computeThemeTilt(symbols, regimeSignals);
    }

    // ===== STAGE J: Build intents =====
    const { intents, summary } = await buildIntents({
      targetsObj, positions, prices, cashUsd, budgetUsd,
      championScale, regimeSignals, symbolSignals, factorWeights,
      piResult, themeTilt, lossFilterScale, isOverlay, crashScale,
    });

    log("INFO", "Intents built", summary);

    // ===== STAGE K: Emit intents =====
    const sorted = prioritize(intents);
    const results = [];
    let cycleNotional = 0;

    for (const intent of sorted) {
      if (results.filter(r => r.ok).length >= CFG.max_cycle_orders) break;
      if (cycleNotional + intent.notional_usd > CFG.max_cycle_notional_usd) break;

      // Idempotency
      const idempoKey = `${CFG.idempo_prefix}:${intent.symbol}:${intent.side}:${dayKey()}`;
      const idempoSet = await setNx(idempoKey, intent.intent_id, CFG.idempo_ttl_sec);
      if (!idempoSet) {
        log("INFO", "Idempotency skip", { symbol: intent.symbol, side: intent.side });
        results.push({ ok: false, reason: "IDEMPOTENCY" });
        continue;
      }

      if (CFG.dry_run) {
        log("INFO", "DRY_RUN intent", {
          symbol: intent.symbol, side: intent.side,
          delta: intent.delta_shares, notional: intent.notional_usd.toFixed(0),
          decision,
        });
        results.push({ ok: true, dry_run: true });
        cycleNotional += intent.notional_usd;
        continue;
      }

      try {
        await redis.xadd(CFG.order_intent_stream, "*", "json", JSON.stringify(intent));
        emitEvent({
          proc: PROC, event: "ORDER_INTENT_EMITTED", sev: "INFO",
          corr: { intent_id: intent.intent_id, symbol: intent.symbol, cycle_id: cycleId },
          msg: `${intent.side} ${intent.symbol} Δ${intent.delta_shares} $${intent.notional_usd.toFixed(0)}`,
          ctx: intent,
        });
        results.push({ ok: true });
        cycleNotional += intent.notional_usd;
      } catch (err) {
        log("ERROR", "Intent emit failed", { symbol: intent.symbol, error: err.message });
        results.push({ ok: false, error: err.message });
      }
    }

    // Store budget
    await redis.set("emarkos:v1:budget_usd", budgetUsd.toFixed(2), "EX", 120);

    // ===== STAGE L: Adaptive poll interval =====
    const newPollInterval = computeAdaptivePollInterval(regimeSignals);
    if (newPollInterval !== CFG.poll_interval_ms) {
      CFG.poll_interval_ms = newPollInterval;
      log("INFO", "ADAPTIVE_POLL_INTERVAL", { new_interval_ms: newPollInterval, vix: regimeSignals.vix });
    }

    // ===== STAGE M: Comprehensive cycle KPI =====
    const activeTilts = Object.entries(themeTilt).filter(([, v]) => Math.abs(v) > 0.05);
    const cycleKPI = {
      ts: new Date().toISOString(),
      cycle_id: cycleId,
      version: VERSION,
      // Decision
      decision,
      confirmed_regime: scheduler.confirmedRegime,
      cycles_since_anchor: scheduler.cyclesSinceAnchor,
      // Regime & Champion
      regime: regimeSignals.regime,
      vix: regimeSignals.vix,
      confidence: regimeSignals.confidence,
      source_count: regimeSignals.sourceCount,
      champion_scale: parseFloat(championScale.toFixed(4)),
      crash_scale: crashScale,
      combined_scale: parseFloat((championScale * lossFilterScale * crashScale).toFixed(4)),
      // Champion pipeline stages
      stage1_hedge: championTelemetry.stage1_hedge,
      stage2_crisis: championTelemetry.stage2_hedge_crisis,
      stage3_regime: championTelemetry.stage3_hedge_regime,
      stage4_vol: championTelemetry.stage4_vol_scale,
      w_asset: championTelemetry.w_asset,
      crisis_trigger: championTelemetry.crisis_trigger,
      gate_state: regimeSignals.gateState,
      // ProfitInterrupt
      pi_triggered: piResult.triggered,
      pi_reason: piResult.reason,
      pi_edge_bps: piResult.edge_bps || 0,
      pi_direction: piResult.direction,
      pi_signal_strength: piResult.signal_strength,
      pi_trigger_count: piState.triggerCount,
      // Theme tilt
      theme_active_tilts: activeTilts.length,
      // Budget
      cashUsd: parseFloat(cashUsd.toFixed(2)),
      totalMV: parseFloat(totalMV.toFixed(2)),
      budgetUsd: parseFloat(budgetUsd.toFixed(2)),
      // Execution
      emitted: results.filter(r => r.ok).length,
      failed: results.filter(r => !r.ok).length,
      cycleNotional: parseFloat(cycleNotional.toFixed(2)),
      posCount,
      // Risk
      dailyPnlPct: parseFloat((dailyPnlPct * 100).toFixed(2)),
      lossFilterScale,
      concentration_warnings: concentrationWarnings.length,
      // Crash guard
      crash_triggered: crashResult.triggered,
      crash_meta: crashResult.meta,
      // 4AI
      xgb_flagged: xgbFlagCount,
      alpha_scored: alphaCount,
      rt_scaled: summary.rt_scaled,
      rt_blocked: summary.rt_blocked,
      noise_filtered: summary.noiseFiltered,
      // Turnover Governor
      turnover_gov: turnoverGov.dailyUsage,
      gov_scale: summary.gov_scale,
      actual_turnover_pct: summary.actual_turnover_pct,
      // Operational stats
      ops_stats: opStats.summary,
      // Adaptive
      poll_interval_ms: CFG.poll_interval_ms,
      // Skipped
      skipped: summary.skipped,
    };
    await redis.set("live:cycle:latest", JSON.stringify(cycleKPI), "EX", 120);
    await redis.xadd("live:cycle:history", "MAXLEN", "~", "500", "*", "json", JSON.stringify(cycleKPI));

    // Persist operational stats
    await opStats.persist();

    log("INFO", `=== Cycle Complete (v${VERSION}) ===`, {
      decision, emitted: results.filter(r => r.ok).length,
      cycleNotional: cycleNotional.toFixed(0),
      champion_scale: championScale.toFixed(4),
      crash_scale: crashScale,
      gov_scale: summary.gov_scale,
    });

    return { ok: true, cycle_id: cycleId, decision, summary, results };

  } finally {
    await redis.del(CFG.cycle_lock_key).catch(() => {});
  }
}

// ═══════════════════════════════════════════════════════════════
// SECTION 27: RECONCILER (kept from v4.0)
// ═══════════════════════════════════════════════════════════════

async function initReconcilerConsumerGroup() {
  try {
    await redis.xgroup("CREATE", CFG.execution_stream, CFG.reconciler_consumer_group, "0", "MKSTREAM");
    log("INFO", "Consumer group created", { group: CFG.reconciler_consumer_group });
  } catch (e) {
    if (!e.message.includes("BUSYGROUP")) {
      log("WARN", "Consumer group creation failed", { error: e.message });
    }
  }
}

async function processExecution(execData) {
  const { kind, intent_id, symbol, side, filled_est, target_qty, reason } = execData;

  if (kind === "FILLED" || kind === "PARTIAL") {
    const filledQty = Number(filled_est) || 0;
    if (filledQty <= 0 || !symbol) return;

    const currentRaw = await redis.hget(CFG.cost_basis_key, symbol);
    const current = currentRaw ? safeJsonParse(currentRaw) : { qty: 0, avg_px: 0 };

    const currentQty = Number(current.qty) || 0;
    const currentAvgPx = Number(current.avg_px) || 0;

    let newQty, newAvgPx;
    if (side === "BUY") {
      newQty = currentQty + filledQty;
      newAvgPx = currentAvgPx;
    } else {
      newQty = Math.max(0, currentQty - filledQty);
      newAvgPx = newQty > 0 ? currentAvgPx : 0;
    }

    const newCostBasis = { qty: newQty, avg_px: newAvgPx };
    await redis.hset(CFG.cost_basis_key, symbol, JSON.stringify(newCostBasis));
    log("INFO", "Cost basis updated", { symbol, kind, filled_est: filledQty, old: current, new: newCostBasis });

    const targetsStr = await redis.get(CFG.targets_key);
    if (targetsStr) {
      const targets = safeJsonParse(targetsStr);
      const tgtInner = targets?.targets || targets;
      if (tgtInner && tgtInner[symbol]) {
        tgtInner[symbol].current_shares = newQty;
        tgtInner[symbol].status = kind === "FILLED" ? "DONE" : "PARTIAL";
        await redis.set(CFG.targets_key, JSON.stringify(targets));
      }
    }
  }

  if (kind === "REJECTED") {
    log("WARN", "Execution rejected", { intent_id, symbol, reason });
    const targetsStr = await redis.get(CFG.targets_key);
    if (targetsStr) {
      const targets = safeJsonParse(targetsStr);
      const tgtInner2 = targets?.targets || targets;
      if (tgtInner2 && tgtInner2[symbol]) {
        tgtInner2[symbol].status = "FAILED";
        tgtInner2[symbol].last_error = reason;
        await redis.set(CFG.targets_key, JSON.stringify(targets));
      }
    }
  }
}

async function runReconciler() {
  try {
    const result = await redis.xreadgroup(
      "GROUP", CFG.reconciler_consumer_group, CFG.reconciler_consumer_name,
      "COUNT", 10, "BLOCK", 1000,
      "STREAMS", CFG.execution_stream, ">"
    );

    if (!result || result.length === 0) return;

    for (const [stream, messages] of result) {
      for (const [id, fields] of messages) {
        try {
          const jsonIdx = fields.indexOf("json");
          if (jsonIdx >= 0 && fields[jsonIdx + 1]) {
            const execData = safeJsonParse(fields[jsonIdx + 1]);
            if (execData) await processExecution(execData);
          }
          await redis.xack(CFG.execution_stream, CFG.reconciler_consumer_group, id);
        } catch (e) {
          log("ERROR", "Reconciler processing error", { id, error: e.message });
        }
      }
    }
  } catch (e) {
    if (!e.message.includes("NOGROUP")) {
      log("ERROR", "Reconciler error", { error: e.message });
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// SECTION 28: MAIN
// ═══════════════════════════════════════════════════════════════

async function main() {
  log("INFO", `=== KIS Live Trading Daemon v${VERSION} (Champion+Scheduler+Governor+CrashGuard) Starting ===`);
  log("INFO", "Champion v7.0 Parameters", {
    hedge_boost: CHAMPION.hedge_boost, crisis_boost: CHAMPION.crisis_boost,
    vol_mult_low: CHAMPION.vol_mult_low, vol_mult_high: CHAMPION.vol_mult_high,
    target_vol: CHAMPION.target_vol,
    stage3_scale_min: CHAMPION.stage3_scale_min, stage3_scale_max: CHAMPION.stage3_scale_max,
  });
  log("INFO", "Decision Scheduler Parameters", {
    anchor_interval_ms: SCHEDULER_CFG.anchor_interval_ms,
    regime_confirm_n: SCHEDULER_CFG.regime_confirm_n,
    regime_confirm_window_ms: SCHEDULER_CFG.regime_confirm_window_ms,
    regime_min_confidence: SCHEDULER_CFG.regime_min_confidence,
  });
  log("INFO", "Turnover Governor Parameters", {
    max_daily_pct: TURNOVER_CFG.max_daily_pct,
    max_overlay_daily_pct: TURNOVER_CFG.max_overlay_daily_pct,
    max_single_cycle_pct: TURNOVER_CFG.max_single_cycle_pct,
    min_trade_threshold: TURNOVER_CFG.min_trade_threshold,
  });
  log("INFO", "Crash Guard Parameters", {
    drawdown_threshold: CRASH_CFG.drawdown_threshold,
    vol_spike_threshold: CRASH_CFG.vol_spike_threshold,
    risk_scale_down: CRASH_CFG.risk_scale_down,
    recovery_cycles: CRASH_CFG.recovery_cycles,
  });
  log("INFO", "ProfitInterrupt Parameters", {
    min_edge_bps: PI_CFG.min_edge_bps, min_prob: PI_CFG.min_prob,
    max_shift: PI_CFG.max_shift, anchor_ratio: PI_CFG.anchor_ratio,
    commission_bps: PI_CFG.commission_bps, slippage_bps: PI_CFG.slippage_bps,
    edge_buffer_bps: PI_CFG.edge_buffer_bps,
    cooldown_ms: PI_CFG.cooldown_ms, ttl_seconds: PI_CFG.ttl_seconds,
  });
  log("INFO", "Theme Tilt Parameters", {
    override_min_conf: THEME_CFG.override_min_conf,
    w_fourai: THEME_CFG.w_fourai, w_news: THEME_CFG.w_news,
  });
  log("INFO", "Configuration", {
    base_poll_interval_ms: CFG.base_poll_interval_ms,
    min_notional_usd: CFG.min_notional_usd,
    max_cycle_orders: CFG.max_cycle_orders,
    max_single_position_pct: CFG.max_single_position_pct,
    dry_run: CFG.dry_run,
  });

  await initReconcilerConsumerGroup();

  // Initial cycle
  await runCycle();

  // Adaptive periodic cycle
  const adaptiveLoop = async () => {
    try {
      await runCycle();
    } catch (err) {
      log("ERROR", "Cycle error", { error: err.message, stack: err.stack });
    }
    setTimeout(adaptiveLoop, CFG.poll_interval_ms);
  };
  setTimeout(adaptiveLoop, CFG.poll_interval_ms);

  // Reconciler loop
  setInterval(runReconciler, 2000);

  // Periodic stats persistence
  setInterval(() => opStats.persist(), 60000);
}

main().catch(error => {
  log("FATAL", "Daemon crashed", { error: error.message, stack: error.stack });
  process.exit(1);
});
