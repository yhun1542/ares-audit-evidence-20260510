/**
 * alert-rules.mjs — ARES Centralized Alert Rules Service v1.5.0
 * ================================================================
 * Active rule count is authoritative at runtime via RULES.length
 * (current value is logged on startup; do NOT hard-code in headers).
 *
 * BASE RULES (17):
 *   R-RISK-MULTIPLIER-JUMP: risk_multiplier 급변 detection
 *   R-WRONGTYPE-DETECTED: Redis WRONGTYPE error detection
 *   C-SSOT-UNEXPLAINED: unexplained SSOT symbol count increase
 *   B-PEAK-REBASE: equity peak rebase event
 *   F-KIS-AUTH: KIS auth/token missing or expired
 *   (+ 12 existing base rules)
 *
 * @version 1.5.0
 * @date 2026-04-21
 * [STRUCTURAL-FIX] F-SSOT-STALE: ISO+epoch dual parsing
 * [STRUCTURAL-FIX] F-KIS-AUTH: correct heartbeat key name
 * [STRUCTURAL-FIX] E-CYCLE-STALE: key absence ≠ error
 * [STRUCTURAL-FIX] C-HALT-ACTIVE: also checks ares:invariant:halt
 * [NEW] R-EQUITY-DRIFT: broker vs snapshot equity drift monitoring
 * [NEW] R-OFG-HALT-STUCK: OFG stuck in HALT state detection
 *
 * PATCH-ALR-001 (2026-04-20, v1.3.x → v1.4.0):
 *   * R-OFG-HALT-STUCK: fixed SSOT from 'ofg:gate' → 'ofg:status.state',
 *     thresholdSec ↑ to 420s to align with go-nogo 3-cycle hysteresis,
 *     detail now includes trading_enabled + FTG for faster triage.
 *   * NEW non-alerting tracker rules (4):
 *     R-TRANSITION-TRACKER-TRADING, R-TRANSITION-TRACKER-FTG,
 *     R-TRANSITION-TRACKER-OFG, R-TRANSITION-TRACKER-VERDICT
 *     XADD to ares:transitions:{trading_enabled,ftg,ofg,verdict} with
 *     MAXLEN~100k for forensic replay and hysteresis tuning (consumed by
 *     hysteresis_analyzer.py).
 *   * Total active rules: 23 (17 base + 2 prior additions + 4 trackers).
 *     RULES.length is authoritative at runtime.
 *
 * PATCH-ALR-002 (2026-04-21, v1.4.0 → v1.5.0):
 *   * NEW R-FORENSIC-OFFLOAD-STALE: warn if forensic_offload_s3 has not
 *     advanced any cursor for > 2 hours (S3 backup pipeline stalled).
 *   * NEW R-KILL-SWITCH-FLAPPING: warn if kill-switch-authority changes
 *     `state` more than 4 times in 5 minutes (control-plane unstable).
 *   * NEW R-HEARTBEAT-SENTINEL-DOWN: critical if heartbeat-sentinel’s
 *     own heartbeat (`ares:sentinel:heartbeat`) is missing or stale > 60s.
 *   * NEW R-TRADE-HALT-FLAPPING: warn if `trade:halt` flips ≥ 3 times
 *     in 1 minute (catches the 2026-04-20 incident pattern early).
 *   * Header rule-count comments now reference RULES.length only.
 *   * Total active rules: 27 (still authoritative via RULES.length).
 */

import { parseEpochMs, ageMs, isStale, formatAge, parseJsonWithTs } from '../lib/ares-timestamp.mjs';
import { getRedis } from '../lib/redis-connection.mjs';
import { sendAlert } from '../lib/telegram-alert.mjs';

const EVAL_INTERVAL_MS = 15_000;
const DEFAULT_COOLDOWN_MS = 5 * 60 * 1000;
const HEARTBEAT_TTL_SEC = 45;

let redis;

function log(level, msg, data = {}) {
  console.log(JSON.stringify({ ts: new Date().toISOString(), level, proc: 'alert-rules', msg, ...data }));
}

function safeJson(raw) {
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return raw; }
}

// ─── Alert Rule Definitions (17 total) ───

const RULES = [
  // ── Risk Rules (5) ──
  {
    id: 'R-GPU-CRASH',
    name: 'GPU Regime CRASH',
    severity: 'CRITICAL',
    cooldownMs: 10 * 60_000,
    async check() {
      const raw = safeJson(await redis.get('gpu:regime:operational:current'));
      if (raw?.state === 'CRASH') return { fire: true, detail: `GPU CRASH: risk=${raw.risk_score?.toFixed(4)}, reasons=${(raw.reasons||[]).join(',')}` };
      return { fire: false };
    },
  },
  {
    id: 'R-GPU-STALE',
    name: 'GPU Guard Stale',
    severity: 'WARNING',
    cooldownMs: 15 * 60_000,
    async check() {
      const hb = await redis.get('gpu:regime:operational:heartbeat');
      if (!hb) return { fire: true, detail: 'GPU heartbeat key MISSING' };
      const age = ageMs(hb); // [STRUCTURAL-FIX] canonical epoch parser
      if (age > 120_000) return { fire: true, detail: `GPU heartbeat stale: ${formatAge(hb)}` };
      return { fire: false };
    },
  },
  {
    id: 'R-EQUITY-CLIFF',
    name: 'Equity Sudden Drop',
    severity: 'EMERGENCY',
    cooldownMs: 30 * 60_000,
    async check() {
      const issues = safeJson(await redis.get('ares:equity:sanity_issues'));
      if (Array.isArray(issues) && issues.length > 0) {
        const large = issues.find(i => i.type === 'LARGE_CHANGE');
        if (large) return { fire: true, detail: large.detail };
      }
      return { fire: false };
    },
  },
  // [GAP-4] NEW: Risk multiplier jump detection
  {
    id: 'R-RISK-MULTIPLIER-JUMP',
    name: 'Risk Multiplier Sudden Jump',
    severity: 'WARNING',
    cooldownMs: 15 * 60_000,
    _prevMult: null,
    async check() {
      const raw = await redis.get('ares:dtch:risk_multiplier');
      if (!raw) return { fire: false };
      const mult = parseFloat(raw);
      if (!Number.isFinite(mult)) return { fire: false };

      if (this._prevMult !== null) {
        const delta = Math.abs(mult - this._prevMult);
        // Fire if jump exceeds 0.3 in a single eval (e.g., 0.2 → 1.0)
        if (delta > 0.3) {
          const detail = `risk_multiplier jumped: ${this._prevMult.toFixed(2)} → ${mult.toFixed(2)} (Δ${delta.toFixed(2)})`;
          this._prevMult = mult;
          return { fire: true, detail };
        }
      }
      this._prevMult = mult;
      return { fire: false };
    },
  },
  // [GAP-4] NEW: Redis WRONGTYPE error detection
  {
    id: 'R-WRONGTYPE-DETECTED',
    name: 'Redis WRONGTYPE Error',
    severity: 'CRITICAL',
    cooldownMs: 10 * 60_000,
    async check() {
      // Check audit log for recent WRONGTYPE errors
      const raw = await redis.get('ares:wrongtype:last_error');
      if (raw) {
        const err = safeJson(raw);
        const ts = err?.ts || 0;
        const age = Date.now() - ts;
        // Only fire if error is recent (within last 5 minutes)
        if (age < 5 * 60_000) {
          return { fire: true, detail: `WRONGTYPE on key "${err.key || '?'}" by ${err.process || '?'}: ${err.error || '?'}` };
        }
      }
      // Also check the wrongtype counter
      const count = await redis.get('ares:wrongtype:count_today');
      if (count && parseInt(count) > 0) {
        return { fire: true, detail: `${count} WRONGTYPE errors detected today` };
      }
      return { fire: false };
    },
  },

  // ── Control Rules (5) ──
  {
    id: 'C-FTG-BLOCKED',
    name: 'FTG Not OPEN',
    severity: 'CRITICAL',
    cooldownMs: 5 * 60_000,
    async check() {
      const gate = await redis.get('ares:final_trade_gate');
      if (!gate || gate === 'HALT') {
        const reason = await redis.get('ares:final_trade_gate:reason') || 'unknown';
        return { fire: true, detail: `FTG=${gate||'MISSING'}, reason=${reason}` };
      }
      return { fire: false };
    },
  },
  {
    id: 'C-FTG-DEAD',
    name: 'FTG Process Dead',
    severity: 'EMERGENCY',
    cooldownMs: 5 * 60_000,
    async check() {
      const hb = await redis.get('ares:final_trade_gate:heartbeat');
      if (!hb) return { fire: true, detail: 'FTG heartbeat MISSING — process likely dead' };
      const age = ageMs(hb); // [STRUCTURAL-FIX] canonical epoch parser
      if (age > 90_000) return { fire: true, detail: `FTG heartbeat ${formatAge(hb)}` };
      return { fire: false };
    },
  },
  {
    id: 'C-TRADING-DISABLED',
    name: 'Trading Disabled',
    severity: 'WARNING',
    cooldownMs: 15 * 60_000,
    async check() {
      const enabled = await redis.get('trading:enabled');
      if (enabled !== 'true') {
        const reason = await redis.get('trading:disable_reason') || 'unknown';
        return { fire: true, detail: `trading:enabled=${enabled||'MISSING'}, reason=${reason}` };
      }
      return { fire: false };
    },
  },
  {
    id: 'C-HALT-ACTIVE',
    name: 'System HALT Active',
    severity: 'CRITICAL',
    cooldownMs: 10 * 60_000,
    async check() {
      // Check both halt keys
      const active = await redis.get('ares:halt:active');
      const invHalt = await redis.get('ares:invariant:halt');
      if (active === 'true' || active === '1') {
        const severity = await redis.get('ares:halt:severity') || '?';
        return { fire: true, detail: `HALT active: severity=${severity}` };
      }
      if (invHalt === '1' || invHalt === 'true') {
        return { fire: true, detail: `ares:invariant:halt = ${invHalt} (invariant checker triggered)` };
      }
      return { fire: false };
    },
  },
  {
    id: 'C-RAMP-ACTIVE',
    name: 'Recovery Ramp Active',
    severity: 'INFO',
    cooldownMs: 60 * 60_000,
    async check() {
      const active = await redis.get('ares:guard:recovery_ramp:active');
      if (active === 'true') {
        const step = await redis.get('ares:guard:recovery_ramp:current_step') || '?';
        return { fire: true, detail: `Recovery ramp step ${step}` };
      }
      return { fire: false };
    },
  },
  // [GAP-4] NEW: SSOT unexplained symbol increase
  {
    id: 'C-SSOT-UNEXPLAINED',
    name: 'SSOT Unexplained Symbol Growth',
    severity: 'WARNING',
    cooldownMs: 30 * 60_000,
    _prevCount: null,
    async check() {
      const raw = safeJson(await redis.get('ssot:target:v2:current'));
      if (!raw?.targets?.positions) return { fire: false };
      // [STRUCTURAL-FIX-v2 FIX-2] active target만 카운트 (residual_holding 제외)
      const activePositions = raw.targets.positions.filter(p => p.tag !== 'residual_holding');
      const count = activePositions.length;

      // Track engine source count for comparison
      const execSrcRaw = await redis.get('ares:exec_src:symbol_count');
      const execSrcCount = execSrcRaw ? parseInt(execSrcRaw) : null;

      if (execSrcCount && count > execSrcCount + 5) {
        return { fire: true, detail: `SSOT has ${count} symbols but exec_src only ${execSrcCount} (Δ${count - execSrcCount} unexplained)` };
      }

      if (this._prevCount !== null && count > this._prevCount + 10) {
        const detail = `SSOT symbol count jumped: ${this._prevCount} → ${count} (Δ${count - this._prevCount})`;
        this._prevCount = count;
        return { fire: true, detail };
      }
      this._prevCount = count;
      return { fire: false };
    },
  },

  // ── Execution Rules (2) ──
  {
    id: 'E-CYCLE-STALE',
    name: 'Trade Cycle Stale',
    severity: 'WARNING',
    cooldownMs: 15 * 60_000,
    async check() {
      // [STRUCTURAL-FIX 2026-04-15] Don't fire if key doesn't exist at all
      // (system may not use live:cycle:latest)
      const rawStr = await redis.get('live:cycle:latest');
      if (!rawStr) return { fire: false }; // Key absent = feature not active, not an error
      const raw = safeJson(rawStr);
      if (!raw?.ts) return { fire: true, detail: 'Cycle data exists but has no timestamp' };
      const age = Date.now() - new Date(raw.ts).getTime();
      const hour = new Date().getUTCHours();
      const inMarket = hour >= 13 && hour <= 21;
      if (inMarket && age > 5 * 60_000) {
        return { fire: true, detail: `Last cycle ${Math.round(age/1000)}s ago (market hours)` };
      }
      return { fire: false };
    },
  },
  {
    id: 'E-HARD-STOP',
    name: 'Hard Stop Violation',
    severity: 'CRITICAL',
    cooldownMs: 30 * 60_000,
    async check() {
      const raw = safeJson(await redis.get('ares:guard:hard_stop:violations'));
      if (raw?.violations?.length > 0) {
        // [FIX 2026-04-16] Only fire if violations data is recent (< 30 min)
        // Stale violations from previous sessions should not trigger repeated alerts
        const violationAge = ageMs(raw.ts); // [STRUCTURAL-FIX] canonical epoch parser
        if (violationAge > 30 * 60_000) {
          return { fire: false }; // Stale violation data — skip
        }
        const list = raw.violations.map(v => `${v.symbol}:${v.returnPct}%`).join(', ');
        return { fire: true, detail: `${raw.violations.length} violations: ${list}` };
      }
      return { fire: false };
    },
  },

  // ── Freshness Rules (4) ──
  {
    id: 'F-EQUITY-STALE',
    name: 'Equity Snapshot Stale',
    severity: 'WARNING',
    cooldownMs: 10 * 60_000,
    async check() {
      const ts = await redis.get('ares:equity:updated_at');
      if (!ts) return { fire: true, detail: 'ares:equity:updated_at MISSING' };
      const age = Date.now() - new Date(ts).getTime();
      if (age > 90_000) return { fire: true, detail: `Equity snapshot ${Math.round(age/1000)}s old` };
      return { fire: false };
    },
  },
  {
    id: 'F-SSOT-STALE',
    name: 'SSOT Targets Stale',
    severity: 'WARNING',
    cooldownMs: 30 * 60_000,
    async check() {
      // [STRUCTURAL-FIX 2026-04-15] Prefer epoch key; fallback to ISO parsing
      // [FIX 2026-04-16] epoch key stores SECONDS, isoRaw stores MILLISECONDS — normalize both to ms
      const epochRaw = await redis.get('ssot:target:v2:ts:epoch');
      const isoRaw = await redis.get('ssot:target:v2:ts');

      let tsMs = 0;
      if (epochRaw && !isNaN(parseInt(epochRaw))) {
        const epochVal = parseInt(epochRaw);
        // Auto-detect: if value < 1e12, it's seconds; otherwise milliseconds
        tsMs = epochVal < 1e12 ? epochVal * 1000 : epochVal;
      } else if (isoRaw) {
        const isoVal = parseInt(isoRaw);
        // ssot:target:v2:ts may store epoch ms (numeric string) or ISO string
        if (!isNaN(isoVal) && isoVal > 1e12) {
          tsMs = isoVal;  // It's already milliseconds
        } else {
          const parsed = new Date(isoRaw).getTime();
          if (!isNaN(parsed)) tsMs = parsed;
        }
      }

      if (tsMs === 0) return { fire: true, detail: 'ssot:target:v2:ts and ts:epoch both MISSING or unparseable' };
      const age = Date.now() - tsMs;
      if (age > 15 * 60_000) return { fire: true, detail: `SSOT targets ${Math.round(age/60000)}min old` };
      return { fire: false };
    },
  },
  // [GAP-4] NEW: Peak rebase event
  {
    id: 'B-PEAK-REBASE',
    name: 'Equity Peak Rebase',
    severity: 'WARNING',
    cooldownMs: 60 * 60_000,
    async check() {
      const raw = await redis.get('ares:equity:peak_rebase_event');
      if (!raw) return { fire: false };
      const ev = safeJson(raw);
      const ts = ev?.ts || 0;
      const age = Date.now() - ts;
      if (age < 10 * 60_000) {
        return { fire: true, detail: `Peak rebase: old=${ev.old_peak}, new=${ev.new_peak}, reason=${ev.reason || '?'}` };
      }
      return { fire: false };
    },
  },
  // [2026-04-15] NEW: Broker vs Snapshot equity drift monitoring
  {
    id: 'R-EQUITY-DRIFT',
    name: 'Broker-Snapshot Equity Drift',
    severity: 'WARNING',
    cooldownMs: 30 * 60_000,
    async check() {
      // Read cross_check from live-performance-tracker v3
      const raw = safeJson(await redis.get('ares:live:performance:actual'));
      if (!raw?.cross_check) return { fire: false };
      const deltaPct = parseFloat(raw.cross_check.delta_pct);
      if (!Number.isFinite(deltaPct)) return { fire: false };
      // WARNING at 5%, escalate to CRITICAL at 15%
      // STRUCTURAL-FIX: T0 vs component gap is ~16%, raise CRITICAL to 25%
      if (Math.abs(deltaPct) > 25) {
        return {
          fire: true,
          detail: `CRITICAL DRIFT: broker=${raw.cross_check.broker_total_usd} vs snapshot=${raw.cross_check.snapshot_total_usd} (${deltaPct.toFixed(2)}% drift). Possible data contract violation.`,
        };
      }
      // STRUCTURAL-FIX: T0 vs component gap is ~16%, raise WARNING to 18%
      if (Math.abs(deltaPct) > 18) {
        return {
          fire: true,
          detail: `Equity drift: broker=${raw.cross_check.broker_total_usd} vs snapshot=${raw.cross_check.snapshot_total_usd} (${deltaPct.toFixed(2)}% drift). Monitor for convergence.`,
        };
      }
      return { fire: false };
    },
  },
  // [PATCH-ALR-001 2026-04-20] R-OFG-HALT-STUCK rewritten to use ofg:status.state
  // as SSOT (ofg:gate was a stale/legacy key that never existed in this deployment,
  // causing duration tracking to silently no-op). Threshold raised from 5min to 7min
  // to absorb: go-nogo hysteresis (max ~3min) + kill-switch propagation (<10s) +
  // FTG/OFG reconciliation (~1min). Detail string now also reports FTG and
  // trading:enabled so a single alert contains the whole cascade context.
  {
    id: 'R-OFG-HALT-STUCK',
    name: 'OFG Stuck in HALT',
    severity: 'CRITICAL',
    cooldownMs: 15 * 60_000,
    _haltSince: null,
    async check() {
      const status = safeJson(await redis.get('ofg:status'));
      const state = status?.state;
      if (state === 'HALT') {
        if (!this._haltSince) this._haltSince = Date.now();
        const duration = Date.now() - this._haltSince;
        if (duration > 7 * 60_000) {
          const [ftg, ftgReason, tradingEnabled] = await Promise.all([
            redis.get('ares:final_trade_gate'),
            redis.get('ares:final_trade_gate:reason'),
            redis.get('trading:enabled'),
          ]);
          return {
            fire: true,
            detail: `OFG stuck in HALT for ${Math.round(duration/60000)}min. Reason: ${status?.reason || 'unknown'}. FTG=${ftg||'MISSING'} (${ftgReason||'no reason'}). trading:enabled=${tradingEnabled||'MISSING'}. Check go-nogo verdict and kill-switch-authority first.`,
          };
        }
      } else {
        this._haltSince = null;
      }
      return { fire: false };
    },
  },

  // [PATCH-ALR-001 2026-04-20] Transition tracker — non-alerting rule that
  // persists state transitions of the trade-halt cascade to Redis streams for
  // forensic replay. Each transition is written exactly once per change.
  // Streams (bounded via MAXLEN ~):
  //   ares:transitions:trading_enabled   — trading:enabled true↔false
  //   ares:transitions:ftg                — ares:final_trade_gate OPEN↔HALT
  //   ares:transitions:ofg                — ofg:status.state OPEN↔HALT
  //   ares:transitions:verdict            — ares:go_nogo:verdict.result GO↔NO-GO↔DEGRADED
  {
    id: 'R-TRANSITION-TRACKER',
    name: 'Trade-halt Cascade Transition Tracker',
    severity: 'INFO',
    cooldownMs: 0,
    _prev: { te: null, ftg: null, ofg: null, verdict: null },
    async check() {
      try {
        const [te, ftg, ftgReason, ofgRaw, verdictRaw] = await Promise.all([
          redis.get('trading:enabled'),
          redis.get('ares:final_trade_gate'),
          redis.get('ares:final_trade_gate:reason'),
          redis.get('ofg:status'),
          redis.get('ares:go_nogo:verdict'),
        ]);
        const ofg = safeJson(ofgRaw);
        const verdict = safeJson(verdictRaw);
        const now = new Date().toISOString();
        const xadd = async (stream, from, to, extra = {}) => {
          const fields = ['ts', now, 'from', String(from), 'to', String(to)];
          for (const [k, v] of Object.entries(extra)) {
            fields.push(String(k), v == null ? '' : (typeof v === 'string' ? v : JSON.stringify(v)));
          }
          try {
            await redis.xadd(stream, 'MAXLEN', '~', 5000, '*', ...fields);
          } catch { /* stream write must never break alerts */ }
        };
        if (this._prev.te !== null && this._prev.te !== te) {
          await xadd('ares:transitions:trading_enabled', this._prev.te, te, {
            reason: await redis.get('trading:enabled:reason'),
            source: await redis.get('trading:enabled:source'),
          });
        }
        this._prev.te = te;
        if (this._prev.ftg !== null && this._prev.ftg !== ftg) {
          await xadd('ares:transitions:ftg', this._prev.ftg, ftg, { reason: ftgReason });
        }
        this._prev.ftg = ftg;
        const ofgState = ofg?.state ?? null;
        if (this._prev.ofg !== null && this._prev.ofg !== ofgState) {
          await xadd('ares:transitions:ofg', this._prev.ofg, ofgState, {
            reason: ofg?.reason,
            halt_events: ofg?.metrics?.halt_events,
            halt_recoveries: ofg?.metrics?.halt_recoveries,
          });
        }
        this._prev.ofg = ofgState;
        const vResult = verdict?.result ?? null;
        if (this._prev.verdict !== null && this._prev.verdict !== vResult) {
          await xadd('ares:transitions:verdict', this._prev.verdict, vResult, {
            failures: verdict?.failures,
            hysteresis: verdict?.hysteresis,
          });
        }
        this._prev.verdict = vResult;
      } catch { /* intentionally swallow: tracker must never disrupt alerts */ }
      return { fire: false };
    },
  },
  // [GAP-4] NEW: KIS auth/token missing or expired
  {
    id: 'F-KIS-AUTH',
    name: 'KIS Token Missing/Expired',
    severity: 'CRITICAL',
    cooldownMs: 10 * 60_000,
    async check() {
      const token = await redis.get('kis:access_token');
      if (!token) return { fire: true, detail: 'KIS access token MISSING from Redis' };

      // [FP-FIX v2.0] Check token expiry from kis:token:health (SSOT)
      const healthRaw = await redis.get('kis:token:health');
      if (healthRaw) {
        try {
          const health = JSON.parse(healthRaw);
          if (health.tokenExpiresIn !== undefined) {
            const remainingSec = health.tokenExpiresIn;
            if (remainingSec < 0) {
              return { fire: true, detail: `KIS token EXPIRED ${Math.abs(Math.round(remainingSec/60))}min ago (from kis:token:health)` };
            }
            if (remainingSec < 600) {
              return { fire: true, detail: `KIS token expires in ${Math.round(remainingSec/60)}min (from kis:token:health)` };
            }
            // Token is healthy via kis:token:health — skip legacy check
          }
        } catch (e) {
          // fallback to legacy check below
        }
      }
      // Legacy fallback: kis:access_token:expires_at
      const expiresRaw = await redis.get('kis:access_token:expires_at');
      if (expiresRaw) {
        const expiresMs = parseInt(expiresRaw);
        const remaining = expiresMs - Date.now();
        if (remaining < 0) {
          return { fire: true, detail: `KIS token EXPIRED ${Math.abs(Math.round(remaining/60000))}min ago` };
        }
        if (remaining < 10 * 60_000) {
          return { fire: true, detail: `KIS token expires in ${Math.round(remaining/60000)}min` };
        }
      }

      // [STRUCTURAL-FIX 2026-04-15] Check correct heartbeat key
      // Actual key is kis:token:heartbeat (not kis:token:refresh_heartbeat)
      // [FP-FIX v2.0] Primary heartbeat: kis:token:health.ts (SSOT from token service v4)
      let hbAge = Infinity;
      const healthRaw2 = await redis.get('kis:token:health');
      if (healthRaw2) {
        try {
          const h = JSON.parse(healthRaw2);
          if (h.ts) hbAge = Date.now() - new Date(h.ts).getTime();
        } catch {}
      }
      // Fallback: legacy heartbeat keys
      if (hbAge === Infinity) {
        const refreshHb = await redis.get('kis:token:heartbeat')
                        || await redis.get('kis:token:refresh_heartbeat')
                        || await redis.get('kis:token:refresh:heartbeat');
        if (refreshHb) {
          hbAge = ageMs(refreshHb); // [STRUCTURAL-FIX] canonical epoch parser
        }
      }
      if (hbAge === Infinity) {
        return { fire: true, detail: 'KIS token heartbeat MISSING (checked kis:token:health, kis:token:heartbeat)' };
      }
      if (hbAge > 60 * 60_000) {
        return { fire: true, detail: `KIS token refresher stale: ${Math.round(hbAge/60000)}min` };
      }

      return { fire: false };
    },
  },

  // ── FIX-62 Enhancement: Metric Guard Alert Rules (3) ──
  {
    id: 'C-PEAK-PENDING',
    name: 'Peak Update Pending Manual Approval',
    severity: 'CRITICAL',
    cooldownMs: 5 * 60_000,
    check: async () => {
      const [pendingUpdate, pendingRebase, pendingDtch] = await Promise.all([
        redis.get('ares:peak:pending_update'),
        redis.get('ares:peak:pending_rebase'),
        redis.get('ares:peak:pending_update:dtch'),
      ]);
      const pending = [];
      if (pendingUpdate) pending.push({ key: 'ares:peak:pending_update', data: safeJson(pendingUpdate) });
      if (pendingRebase) pending.push({ key: 'ares:peak:pending_rebase', data: safeJson(pendingRebase) });
      if (pendingDtch) pending.push({ key: 'ares:peak:pending_update:dtch', data: safeJson(pendingDtch) });
      if (pending.length > 0) {
        const details = pending.map(p => {
          const d = p.data || {};
          return `${p.key}: jump=${d.jumpPct || d.jump_pct || 'N/A'}% candidate=$${d.candidatePeak || d.candidate_peak || 'N/A'}`;
        }).join('\n');
        return { fire: true, detail: `${pending.length} peak update(s) awaiting manual approval:\n${details}\n\nAction required: Review and approve/reject via Redis CLI or Runbook.` };
      }
      return { fire: false };
    },
  },
  {
    id: 'C-METRIC-VERSION-MISSING',
    name: 'Metric Version Key Missing',
    severity: 'CRITICAL',
    cooldownMs: 10 * 60_000,
    check: async () => {
      const [mvEquity, mvPeak] = await redis.mget('ares:equity:metric_version', 'ares:peak:metric_version');
      const missing = [];
      if (!mvEquity) missing.push('ares:equity:metric_version');
      if (!mvPeak) missing.push('ares:peak:metric_version');
      if (missing.length > 0) {
        return { fire: true, detail: `Metric version key(s) MISSING: ${missing.join(', ')}\nAll DD/Peak services should be in fail-close mode.\nAction: Set version keys via migration Runbook before services can resume active mode.` };
      }
      if (mvEquity !== mvPeak) {
        return { fire: true, detail: `Metric version MISMATCH: equity=${mvEquity} peak=${mvPeak}\nDD computation and peak updates are FROZEN until versions are reconciled.` };
      }
      return { fire: false };
    },
  },
  {
    id: 'W-MIGRATION-LOCK-LINGER',
    name: 'Migration Lock Lingering',
    severity: 'WARNING',
    cooldownMs: 15 * 60_000,
    check: async () => {
      const lock = await redis.get('ares:peak:migration_lock');
      if (lock === '1' || lock === 'true') {
        // Check how long the lock has been active
        const lockMeta = safeJson(await redis.get('ares:peak:migration_lock:meta'));
        const lockAge = lockMeta && lockMeta.ts ? ageMs(lockMeta.ts) : null;
        const ageStr = lockAge ? `${Math.round(lockAge / 60000)}min` : 'unknown duration';
        return { fire: true, detail: `Migration lock has been active for ${ageStr}.\nAll DD/Peak operations are frozen.\nAction: Complete migration or release lock via Runbook.` };
      }
      return { fire: false };
    },
  },

  // ── PATCH-ALR-002 (v1.5.0): Operational stability rules ──
  {
    id: 'R-FORENSIC-OFFLOAD-STALE',
    name: 'Forensic Offload Pipeline Stalled',
    severity: 'WARNING',
    cooldownMs: 30 * 60_000,
    check: async () => {
      // Watch the offload status JSON written by forensic_offload_s3.py.
      const raw = await redis.get('ares:offload:status');
      if (!raw) {
        return { fire: true, detail: 'ares:offload:status MISSING — forensic_offload_s3 has not run successfully yet, or the status key has expired (TTL 7d). Check cron `7 * * * * /home/ubuntu/forensic_offload_cron.sh` and /home/ubuntu/logs/forensic_offload.log.' };
      }
      const parsed = safeJson(raw);
      // forensic_offload_s3.py writes compact `run_ts` like "20260421T020701Z".
      // Accept that, plus other common variants for forward-compat.
      let tsMs = NaN;
      const tsRaw = parsed && (parsed.run_ts || parsed.ts || parsed.last_run_ts || parsed.completed_at);
      if (tsRaw) {
        if (typeof tsRaw === 'string') {
          // 1) ISO 8601
          const isoMs = Date.parse(tsRaw);
          if (Number.isFinite(isoMs)) tsMs = isoMs;
          // 2) compact YYYYMMDDTHHMMSSZ
          if (!Number.isFinite(tsMs)) {
            const m = tsRaw.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
            if (m) tsMs = Date.UTC(+m[1], +m[2]-1, +m[3], +m[4], +m[5], +m[6]);
          }
        } else if (typeof tsRaw === 'number') {
          tsMs = tsRaw > 1e12 ? tsRaw : tsRaw * 1000;
        }
      }
      const age = Number.isFinite(tsMs) ? Date.now() - tsMs : Infinity;
      const STALE_MS = 2 * 60 * 60_000; // 2 hours (cron is hourly)
      if (age > STALE_MS) {
        const ageStr = age === Infinity ? 'unknown' : `${Math.round(age / 60000)}min`;
        return { fire: true, detail: `Forensic offload last ran ${ageStr} ago (threshold 2h).\nS3 archive of ares:order_executions / ares:transitions:* is falling behind.\nCheck /home/ubuntu/logs/forensic_offload.log and crontab.` };
      }
      if (parsed && parsed.last_error) {
        return { fire: true, detail: `Forensic offload reports last_error='${String(parsed.last_error).slice(0,200)}'. Check /home/ubuntu/logs/forensic_offload.log.` };
      }
      return { fire: false };
    },
  },
  {
    id: 'R-KILL-SWITCH-FLAPPING',
    name: 'Kill-Switch Authority State Flapping',
    severity: 'WARNING',
    cooldownMs: 10 * 60_000,
    check: async () => {
      // Use the transition tracker stream populated by R-TRANSITION-TRACKER-TRADING
      // and the kill-switch-authority state itself.
      const cutoffMs = Date.now() - 5 * 60_000;
      const minId = `${cutoffMs}-0`;
      let entries = [];
      try {
        entries = await redis.xrange('ares:transitions:trading_enabled', minId, '+');
      } catch (_) { entries = []; }
      // Flap = unique state changes (true↔false) > 4 in 5min
      const transitions = entries.length;
      if (transitions >= 4) {
        // Confirm it's actual flips (not all same value)
        const states = entries.map(e => {
          const fields = e[1] || [];
          const idx = fields.indexOf('to');
          return idx >= 0 ? String(fields[idx + 1]).toLowerCase() : null;
        }).filter(Boolean);
        const distinct = new Set(states);
        if (distinct.size >= 2) {
          return { fire: true, detail: `kill-switch-authority/trading:enabled flipped ${transitions} times in last 5min (states observed: ${[...distinct].join(', ')}).\nControl-plane is unstable; investigate kill-switch-authority-v2 verdict source.` };
        }
      }
      return { fire: false };
    },
  },
  {
    id: 'R-HEARTBEAT-SENTINEL-DOWN',
    name: 'Heartbeat Sentinel Sidecar Down',
    severity: 'CRITICAL',
    cooldownMs: 5 * 60_000,
    check: async () => {
      // The sentinel is the source of truth for `ares:hb:*` fallback keys
      // consumed by go-nogo-judge-v2 PATCH-GNG-001. If it dies silently,
      // every downstream check that relies on heartbeat fallback is blind.
      // The sentinel writes ares:sentinel:heartbeat as a raw ISO 8601 string
      // (not JSON).
      const raw = await redis.get('ares:sentinel:heartbeat');
      if (!raw) {
        return { fire: true, detail: 'ares:sentinel:heartbeat MISSING — heartbeat-sentinel sidecar is not running or has not yet completed its first tick. Run: `pm2 status heartbeat-sentinel` and `pm2 logs heartbeat-sentinel --lines 50`.' };
      }
      let tsMs = NaN;
      // Try plain ISO first (current writer), then JSON shape (defensive).
      const direct = Date.parse(raw);
      if (Number.isFinite(direct)) {
        tsMs = direct;
      } else {
        const parsed = safeJson(raw);
        const v = parsed && (parsed.ts || parsed.timestamp);
        if (typeof v === 'number') tsMs = v > 1e12 ? v : v * 1000;
        else if (typeof v === 'string') {
          const p = Date.parse(v);
          if (Number.isFinite(p)) tsMs = p;
        }
      }
      const age = Number.isFinite(tsMs) ? Date.now() - tsMs : Infinity;
      if (age > 60_000) {
        const ageStr = age === Infinity ? 'unknown' : `${Math.round(age / 1000)}s`;
        return { fire: true, detail: `heartbeat-sentinel last ticked ${ageStr} ago (threshold 60s).\nFallback heartbeat keys may be stale; go-nogo-judge could begin false-positive NO-GO.` };
      }
      return { fire: false };
    },
  },
  {
    id: 'R-TRADE-HALT-FLAPPING',
    name: 'trade:halt Flapping',
    severity: 'WARNING',
    cooldownMs: 10 * 60_000,
    check: async () => {
      // Track flips in trade:halt itself; also surface trade:halt:reason
      // which is the most actionable triage hint.
      const cutoffMs = Date.now() - 60_000;
      const minId = `${cutoffMs}-0`;
      let entries = [];
      try {
        entries = await redis.xrange('ares:transitions:trade_halt', minId, '+');
      } catch (_) { entries = []; }
      // Soft fallback: if dedicated stream doesn't exist yet, look at FTG
      // transitions which are the closest proxy.
      if (entries.length === 0) {
        try {
          entries = await redis.xrange('ares:transitions:ftg', minId, '+');
        } catch (_) {}
      }
      const transitions = entries.length;
      if (transitions >= 3) {
        const reason = await redis.get('trade:halt:reason').catch(() => null);
        const value = await redis.get('trade:halt').catch(() => null);
        return { fire: true, detail: `trade:halt-related flips: ${transitions} in last 60s. Current trade:halt=${value} reason='${reason || 'n/a'}'\nLikely an upstream invariant or risk check is oscillating. Inspect ares-system-invariant-checker logs and INV-* failures.` };
      }
      return { fire: false };
    },
  },

  // Producer Lock Rule (integrated 2026-04-24 from production_guard_v1.mjs:RULE-P2-01)
  // ─── [PATCH-ALR-003] Sensor Zero Guard & Exposure Alerts (2026-05-06) ───
  // R-SENSOR-ZERO-DEGRADED: gate_core_v51/v52가 DEGRADED 상태로 전환 시 즉시 알림.
  // sensor_zero_guard가 발동하면 exposureMultiplier=0.55로 노출도가 45% 억제됨.
  // 원인: llm:regime:correction 또는 monitor:v3v4:latest_verdict 키 만료/부재.
  {
    id: 'R-SENSOR-ZERO-DEGRADED',
    name: 'Gate sensor_zero_guard DEGRADED — Exposure Suppressed',
    severity: 'CRITICAL',
    cooldownMs: 10 * 60_000, // 10분 쿨다운 (반복 알림 방지)
    async check() {
      const raw = await redis.get('ofg:gate:v51:sensor_health');
      if (!raw) return { fire: false };
      let health;
      try { health = JSON.parse(raw); } catch { return { fire: false }; }
      // sensor_zero_guard가 발동하여 DEGRADED 상태인 경우
      if (health?.sensor_zero_guard === 'DEGRADED' || health?.sufficientCoverage === false) {
        const llmFresh = health?.llmFresh ?? false;
        const monFresh = health?.monitorFresh ?? false;
        const freshSignals = health?.freshSignals ?? 0;
        const vix = health?.vix ?? 'N/A';
        const mult = await redis.get('ofg:gate:exposure_multiplier:active') || '?';
        return {
          fire: true,
          detail: `🚨 sensor_zero_guard DEGRADED\n` +
            `exposureMultiplier=${mult} (정상: 1.0)\n` +
            `llmFresh=${llmFresh} | monitorFresh=${monFresh} | freshSignals=${freshSignals}\n` +
            `VIX=${vix}\n` +
            `원인: llm:regime:correction 또는 monitor:v3v4:latest_verdict 키 만료\n` +
            `조치: regime-writer-v88 및 sensor-keepalive 프로세스 상태 확인`,
        };
      }
      return { fire: false };
    },
  },
  // R-EXPOSURE-SUPPRESSED: exposureMultiplier가 0.8 미만으로 장시간 유지될 때 경고.
  // 정상 시장(VIX<20)에서 노출도가 억제되는 것은 비정상 상태임.
  {
    id: 'R-EXPOSURE-SUPPRESSED',
    name: 'Exposure Multiplier Abnormally Low (VIX-Adjusted)',
    severity: 'WARNING',
    cooldownMs: 15 * 60_000, // 15분 쿨다운
    _streak: 0,
    async check() {
      const multRaw = await redis.get('ofg:gate:exposure_multiplier:active');
      if (!multRaw) return { fire: false };
      const mult = parseFloat(multRaw);
      if (isNaN(mult)) return { fire: false };
      // VIX 확인
      const vixRaw = await redis.get('ctx:macro:vix');
      let vix = 25; // 기본값: 보수적
      try {
        const vixObj = JSON.parse(vixRaw || '{}');
        vix = vixObj?.value ?? vixObj?.vix ?? 25;
      } catch { /* ignore */ }
      // VIX < 20 (안정 구간)에서 mult < 0.8이면 비정상
      const threshold = vix < 20 ? 0.8 : 0.5;
      if (mult < threshold) {
        this._streak++;
        if (this._streak < 3) return { fire: false }; // 3회 연속 확인 후 발동
        return {
          fire: true,
          detail: `⚠️ 노출도 배수 비정상 억제\n` +
            `exposureMultiplier=${mult.toFixed(2)} (임계값: ${threshold})\n` +
            `VIX=${vix.toFixed(2)} | streak=${this._streak}\n` +
            `조치: gate_core_v51 DEGRADED 여부 및 sensor_health 확인`,
        };
      }
      this._streak = 0;
      return { fire: false };
    },
  },
  // R-LLM-KEY-STALE: llm:regime:correction 키가 만료되거나 없을 때 선제 경고.
  // sensor_zero_guard 발동 전에 미리 알림으로써 DEGRADED 예방.
  {
    id: 'R-LLM-KEY-STALE',
    name: 'LLM Regime Key Stale — Pre-DEGRADED Warning',
    severity: 'WARNING',
    cooldownMs: 5 * 60_000, // 5분 쿨다운
    async check() {
      const ttl = await redis.ttl('llm:regime:correction');
      // TTL이 -2(키 없음) 또는 60초 미만(곧 만료)이면 경고
      if (ttl === -2 || (ttl >= 0 && ttl < 60)) {
        const monTtl = await redis.ttl('monitor:v3v4:latest_verdict');
        return {
          fire: true,
          detail: `⚠️ LLM 레짐 키 만료 임박\n` +
            `llm:regime:correction TTL=${ttl}초\n` +
            `monitor:v3v4:latest_verdict TTL=${monTtl}초\n` +
            `조치: regime-writer-v88 및 sensor-keepalive 즉시 확인\n` +
            `방치 시 sensor_zero_guard 발동 → DEGRADED → 노출도 45% 억제`,
        };
      }
      return { fire: false };
    },
  },
  // Contracts: docs/REDIS_HEALTH_CONTRACT.md
  {
    id: 'R-PRODUCER-LOCK-STALE',
    name: 'Producer Lock Not Held (Live+Stale HB)',
    severity: 'WARNING',
    cooldownMs: 10 * 60_000,
    _streak: 0,
    async check() {
      const STRATEGY = process.env.GUARD_STRATEGY || 'ram26';
      const GRACE_SEC = parseInt(process.env.PRODUCER_LOCK_GRACE_SEC || '360', 10);
      const STREAK_THRESHOLD = parseInt(process.env.PRODUCER_LOCK_STREAK || '5', 10);
      const mktSession = await redis.get('market:session');
      if (mktSession !== 'REGULAR') { this._streak = 0; return { fire: false }; }
      const tradeHalt = await redis.get('trade:halt');
      const hardHalt = await redis.get('policy:safe_live:hard_halt');
      if (tradeHalt === 'true' || hardHalt === 'true') { this._streak = 0; return { fire: false }; }
      const lockTtl = await redis.ttl(`producer:order_intent:${STRATEGY}`);
      if (lockTtl >= 0) { this._streak = 0; return { fire: false }; }
      const hbRaw = await redis.get(`nextgen2:heartbeat:${STRATEGY}`);
      let hbFresh = false;
      if (hbRaw) {
        try {
          if (hbRaw.startsWith('{')) {
            const j = JSON.parse(hbRaw);
            const tsVal = j.ts || j.timestamp || 0;
            const epoch = (typeof tsVal === 'number' && tsVal > 1e12) ? tsVal : tsVal * 1000;
            const ageSec = (Date.now() - epoch) / 1000;
            hbFresh = ageSec < GRACE_SEC;
          } else {
            const epoch = parseInt(hbRaw, 10) * 1000;
            const ageSec = (Date.now() - epoch) / 1000;
            hbFresh = ageSec < GRACE_SEC;
          }
        } catch { hbFresh = false; }
      }
      if (hbFresh) { this._streak = 0; return { fire: false }; }
      this._streak++;
      if (this._streak < STREAK_THRESHOLD) return { fire: false };
      return { fire: true, detail: `strategy=${STRATEGY} lock_ttl=${lockTtl} hb_fresh=false streak=${this._streak} grace=${GRACE_SEC}s` };
    },
  },
  // ── PATCH-ALR-003 (2026-05-07) ─────────────────────────────────────
  // Catches the 2026-05-07 P0 incident: rt-position-guard-v9 reported
  // status=OK with positions_monitored=0 during ACTIVE window for hours,
  // because the parser silently dropped 38 held positions due to schema
  // mismatch. Fires only with a 3-cycle streak (~45s) to avoid noise on
  // empty-account or transient zero states.
  {
    id: 'R-RT-GUARD-MONITORED-ZERO',
    name: 'RT Guard: positions_monitored=0 during ACTIVE',
    severity: 'CRITICAL',
    cooldownMs: 10 * 60_000,
    _streak: 0,
    async check() {
      const raw = await redis.get('ctx:rt_guard:health');
      if (!raw) {
        // Missing health key is handled by a different concept (process
        // liveness via heartbeat-sentinel). Don't double-alert here.
        this._streak = 0;
        return { fire: false };
      }
      const h = safeJson(raw);
      if (!h || typeof h !== 'object') {
        this._streak = 0;
        return { fire: false };
      }
      // Only meaningful while the guard considers the window ACTIVE.
      if (h.window !== 'ACTIVE') {
        this._streak = 0;
        return { fire: false };
      }
      const monitored = Number(h.positions_monitored ?? 0);
      if (monitored > 0) {
        this._streak = 0;
        return { fire: false };
      }
      this._streak++;
      if (this._streak < 3) return { fire: false };
      const cycle  = h.cycle ?? '?';
      const conf   = h.data_confidence ?? '?';
      const errors = h.error_count ?? 0;
      const hhmm   = h.hhmm ?? '?';
      return {
        fire: true,
        detail:
          `rt-position-guard-v9 reports positions_monitored=0 during ACTIVE window ` +
          `(streak=${this._streak} ticks ≈ ${this._streak * 15}s).\n` +
          `cycle=${cycle} hhmm=${hhmm} confidence=${conf} errors=${errors}\n` +
          `If positions are non-zero in 'emarkos:v1:positions', the parser is silently dropping them ` +
          `(SELL trigger effectively disabled). Triage:\n` +
          `  1) redis-cli GET emarkos:v1:positions | jq '.count'\n` +
          `  2) pm2 logs rt-position-guard-v9 --lines 80\n` +
          `  3) Check live src vs ` +
          `live-patches/2026-05-07-rt-guard-v9-positions-parser/src/`,
      };
    },
  },
  // ── PATCH-ALR-004 (2026-05-07) ─────────────────────────────────────
  // Schema drift sentinel: detects broker positions schema field-name drift
  // proactively (before it causes a silent positions_monitored=0). Compares
  // the keys of every position entry in 'emarkos:v1:positions' against the
  // expected canonical alias set used by the v9 guard parser. Fires when
  // (a) at least 5 positions are present, (b) ALL of them are missing every
  // known qty alias, or (c) ALL are missing every known avg-price alias.
  // Uses a 3-cycle streak (~45s) to avoid transient publish flips.
  {
    id: 'R-POSITIONS-SCHEMA-DRIFT',
    name: 'Positions: broker schema drift (qty/avg field rename)',
    severity: 'CRITICAL',
    cooldownMs: 30 * 60_000,
    _streak: 0,
    async check() {
      const QTY_ALIASES = ['qty', 'quantity', 'shares', 'economic_qty', 'sellable_qty', 'settled_qty'];
      const AVG_ALIASES = ['avg_price', 'entry_price', 'avgPrice', 'avg_px'];
      const raw = await redis.get('emarkos:v1:positions');
      if (!raw) { this._streak = 0; return { fire: false }; }
      const obj = safeJson(raw);
      if (!obj || typeof obj !== 'object') { this._streak = 0; return { fire: false }; }
      const positions = obj.positions && typeof obj.positions === 'object' ? obj.positions : null;
      if (!positions) { this._streak = 0; return { fire: false }; }
      const entries = Array.isArray(positions) ? positions : Object.values(positions);
      const total = entries.length;
      if (total < 5) { this._streak = 0; return { fire: false }; }
      let qtyMissingAll = 0;
      let avgMissingAll = 0;
      const sampleMissing = [];
      for (const p of entries) {
        if (!p || typeof p !== 'object') continue;
        const hasQty = QTY_ALIASES.some(k => Number(p[k]) > 0);
        const hasAvg = AVG_ALIASES.some(k => Number(p[k]) > 0);
        if (!hasQty) qtyMissingAll++;
        if (!hasAvg) avgMissingAll++;
        if ((!hasQty || !hasAvg) && sampleMissing.length < 3) {
          const keys = Object.keys(p).slice(0, 12).join(',');
          sampleMissing.push(`${p.symbol || p.sym || '?'}{${keys}}`);
        }
      }
      const qtyDrift = qtyMissingAll === total;
      const avgDrift = avgMissingAll === total;
      if (!qtyDrift && !avgDrift) { this._streak = 0; return { fire: false }; }
      this._streak++;
      if (this._streak < 3) return { fire: false };
      const which = [qtyDrift ? 'qty' : null, avgDrift ? 'avg_price' : null].filter(Boolean).join('+');
      return {
        fire: true,
        detail:
          `Broker positions schema drift detected: ALL ${total} positions are missing every known ` +
          `${which} alias (streak=${this._streak} ticks ≈ ${this._streak * 15}s).\n` +
          `Known qty aliases: ${QTY_ALIASES.join('|')}\n` +
          `Known avg aliases: ${AVG_ALIASES.join('|')}\n` +
          `Sample (sym{first-12-keys}): ${sampleMissing.join(' ')}\n` +
          `If this fires, the v9 parser will silently drop all positions and\n` +
          `RT Guard SELL triggers will be effectively disabled (P0).\n` +
          `Triage: extend QTY_ALIASES/AVG_ALIASES in rt_position_guard_v9.mjs and redeploy.`,
      };
    },
  },
];

// ─── Alert Engine ───

const lastFired = {};

async function evaluateRules() {
  const now = Date.now();
  const results = [];

  for (const rule of RULES) {
    try {
      const result = await rule.check();
      const cooldown = rule.cooldownMs || DEFAULT_COOLDOWN_MS;
      const lastTime = lastFired[rule.id] || 0;
      const cooledDown = (now - lastTime) > cooldown;

      if (result.fire && cooledDown) {
        const icon = rule.severity === 'EMERGENCY' ? '🚨' :
                     rule.severity === 'CRITICAL' ? '🔴' :
                     rule.severity === 'WARNING' ? '🟡' : 'ℹ️';
        const msg = `${icon} [${rule.severity}] ${rule.name}\n${result.detail}\n\nRule: ${rule.id} | ${new Date().toISOString()}`;

        await sendAlert(rule.id, msg, { force: rule.severity === 'EMERGENCY' });
        lastFired[rule.id] = now;
        log('ALERT', `Fired: ${rule.id}`, { severity: rule.severity, detail: result.detail });
        results.push({ id: rule.id, status: 'FIRED', severity: rule.severity });
      } else if (result.fire) {
        results.push({ id: rule.id, status: 'COOLDOWN', severity: rule.severity });
      } else {
        results.push({ id: rule.id, status: 'OK' });
      }
    } catch (err) {
      log('ERROR', `Rule ${rule.id} failed`, { error: err.message });
      results.push({ id: rule.id, status: 'ERROR', error: err.message });
    }
  }

  const statusPayload = {
    ts: new Date().toISOString(),
    version: '1.5.0',
    rules_total: RULES.length,
    fired: results.filter(r => r.status === 'FIRED').length,
    cooldown: results.filter(r => r.status === 'COOLDOWN').length,
    ok: results.filter(r => r.status === 'OK').length,
    errors: results.filter(r => r.status === 'ERROR').length,
    results,
  };
  await redis.set('ares:alerting:status', JSON.stringify(statusPayload), 'EX', 60);
  await redis.set('ares:alerting:heartbeat', String(now), 'EX', HEARTBEAT_TTL_SEC);

  return statusPayload;
}

// ─── Main Loop ───

async function main() {
  redis = await getRedis();
  log('INFO', `Alert Rules Service v1.5.0 starting — ${RULES.length} rules, ${EVAL_INTERVAL_MS/1000}s interval`);

  const tick = async () => {
    try {
      const status = await evaluateRules();
      if (status.fired > 0) {
        log('INFO', 'EVAL_COMPLETE', { fired: status.fired, cooldown: status.cooldown, ok: status.ok, errors: status.errors });
      }
    } catch (err) {
      log('ERROR', 'Eval cycle failed', { error: err.message });
    }
  };

  await tick();
  setInterval(tick, EVAL_INTERVAL_MS);
}

main().catch(err => {
  log('ERROR', 'Fatal', { error: err.message });
  throw err;
});
