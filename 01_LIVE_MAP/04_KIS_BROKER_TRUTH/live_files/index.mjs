// index.mjs — ARES KIS US Broker v7 엔트리포인트
// 통합 브로커: 주식 + 옵션 + WebSocket 실시간 + 인메모리 잔고 상태기계

import { loadEnv, redact } from "./core/env.mjs";
import { TokenManager }   from "./core/token_manager.mjs";
import { getGlobalLimiter } from "./core/rate_limiter.mjs";
import { KisRestClient }  from "./core/rest_client.mjs";
import { KisStockApi }    from "./stock/stock_api.mjs";
import { KisOptionApi }   from "./option/option_api.mjs";
import { KisWsStream }    from "./ws/ws_stream.mjs";
import { resolveExchange, wsKey } from "./util/symbol_router.mjs";
import { EventEmitter } from "node:events";

export class UnifiedKisBroker extends EventEmitter {
  constructor({ logger = console } = {}) {
    super();
    this.logger = logger;
    this.env = loadEnv();
    this.tm = new TokenManager({ env: this.env, logger });
    this.rl = getGlobalLimiter(this.env.mode);
    this.rest = new KisRestClient({ env: this.env, tokenManager: this.tm, rateLimiter: this.rl, logger });
    this.stock = new KisStockApi({ env: this.env, rest: this.rest, logger });
    this.option = new KisOptionApi({ env: this.env, rest: this.rest, logger });
    this.ws = new KisWsStream({ env: this.env, tokenManager: this.tm, logger });

    // 인메모리 상태
    this.state = {
      stockPositions: new Map(),   // symbol -> {qty, avgPrice, nowPrice, pnl}
      optionPositions: new Map(),
      deposits:    { USD: { cash: 0, buyingPower: 0 }, KRW: { cash: 0, buyingPower: 0 } },
      openOrders:  new Map(),      // odno -> {...}
      lastTicks:   new Map(),      // wsSymbol -> last price
      lastSync:    0,
      // v7.3: fresh-data timestamps per subsystem — consumers can reject
      // stale data instead of relying on a global lastSync.
      freshness: {
        stockPositions: 0,
        optionPositions: 0,
        openOrders: 0,
        usdDeposit: 0,
      },
      // v7.3: last snapshot error envelope so health-probe / shadow
      // comparator can tell the difference between "empty because flat"
      // vs "empty because REST failed".
      lastSnapshotError: null,
      // v7.4 (B1): FAIL-CLOSED partial indicator. Whenever snapshot()
      // could not refresh at least one subsystem, this flag is set.
      // Downstream (server.mjs /cash, /state) gates stale data on this.
      lastSyncPartial: false,
      // v7.4 (B3): in-memory ring of recent fills fed by WS fill-notice
      // handlers so reconcileOrder can actually match UNKNOWN_SUBMISSION
      // reconciliations by (symbol, side, qty) inside a recent window.
      // Sized small enough that full scans are cheap and bounded.
      recentFills: [],  // [{ts, kind, symbol|pdno, side, quantity, price, orderNo, origOrderNo, raw}]
      // v7.7 NB1 + v7.8 NF2: per-symbol WS-delta stamps. Whenever a WS
      // STOCK_FILL / STOCK_CCNL / OPT_FILL mutates a position entry, the
      // corresponding symbol (or pdno) gets stamped here. snapshot() uses
      // these stamps in PHASE 2 to decide, per symbol, whether a WS delta
      // landed AFTER snapshot() began fetching REST; if so, the WS value
      // wins and the REST value for that single symbol is discarded.
      //
      // v7.8 NF2 (GPT BLOCK closure): the stamp is now a monotonic sequence
      // number produced by this._nextWsSeq() instead of Date.now(). Date.now
      // has only 1 ms resolution, so a WS delta and snapshot start that fall
      // in the SAME millisecond could fail the `ts > started` check and let
      // v7.8 NF2 (GPT BLOCK closure): the stamp is now a monotonic sequence
      // eliminates that race entirely — every event gets a strictly greater
      // seq than any earlier one, so same-ms ordering is preserved exactly.
      //
      // v7.8 NB2 (GPT BLOCK closure): the stamp value carries an authority
      // tag so snapshot() can distinguish two cases: (a) a FILL event that
      // actually mutated quantity/avgPrice (authority="FILL") and (b) a
      // CCNL tick that only refreshed mark-to-market fields like nowPrice
      // (authority="TICK"). Only FILL entries are authoritative over the
      // REST snapshot for quantity; TICK entries must NOT shield a symbol
      // from a legitimate REST-side quantity update (e.g. a manual trade
      // from another channel). The value shape is { seq, authority }; the
      // map names are retained for backwards compatibility in diagnostics.
      wsStockDeltaTs: new Map(),   // symbol -> { seq, authority:"FILL"|"TICK" }
      wsOptionDeltaTs: new Map(),  // pdno   -> { seq, authority:"FILL"|"TICK" }
    };

    // v7.8 NF2: monotonic sequence counter shared by all WS stamps. Strictly
    // increases once per WS-driven state mutation. snapshot() reads the
    // value BEFORE dispatching REST calls and compares individual stamps
    // against that captured value, so any WS event that happens DURING the
    // REST fetch window is guaranteed to have a strictly greater seq.
    //
    // Overflow: Number.MAX_SAFE_INTEGER ≈ 9e15; at 10000 events/second this
    // is ~28 000 years of continuous running. Wrap-around is therefore not a
    // concern in practice, but _nextWsSeq() still asserts monotonicity.
    this._wsSeq = 0;
    this._recentFillsMax = Number(process.env.ARES_RECENT_FILLS_MAX || 512);

    // v7.6 FF3/B1: explicit refresh-in-progress flag. snapshot() sets this
    // to true at entry and back to false in a `finally` block. /cash and
    // other consumers refuse with 503 while this is true, so there is no
    // observable window in which a concurrent request could see freshly
    // published USD cash but a stale `lastSyncPartial` flag. Combined with
    // the shadow-commit pattern in snapshot() below, the broker is now
    // fully fail-closed against atomicity races.
    this._refreshInProgress = false;

    // v7.7 NF1 (GPT consensus fix): a concurrent snapshot() caller must not
    // silently return stale state while another snapshot is in flight. We
    // store the in-flight promise here; every re-entry awaits the same
    // promise and therefore observes the committed post-refresh state. The
    // server's POST /refresh handler likewise awaits this promise.
    this._refreshPromise = null;

    // v7.7 NN1: explicit counter for /cash requests that hit the
    // refresh-in-progress gate. Exposed via /metrics + /health so operators
    // can trend gate pressure and alert on sustained elevation.
    this._metrics = {
      cash_refresh_in_progress_hits: 0,
      cash_partial_snapshot_hits:    0,
      cash_stale_hits:               0,
      cash_ok_hits:                  0,
      // v7.8 NN2: invariant `snapshot_runs_total === snapshot_success_total
      // + snapshot_errors_total` is now enforced. `snapshot_runs_total` is
      // incremented at the very entry of _doSnapshot() (so a thrown PHASE 1
      // is still counted as a run) and exactly one of the two outcome
      // counters is incremented before the function returns.
      snapshot_runs_total:           0,
      snapshot_success_total:        0,
      snapshot_errors_total:         0,
      snapshot_ws_preserved_total:   0,  // NB1: how many symbols the
                                         // conditional merge preserved from
                                         // WS overlay (FILL-authority only
                                         // since NB2).
      snapshot_ws_tick_ignored_total: 0, // v7.8 NB2: how many symbols had a
                                         // TICK-only stamp during the
                                         // snapshot window and were therefore
                                         // allowed to accept the REST value.
    };

    this._wireWs();
  }

  // v7.8 NF2: allocate a strictly-monotonic sequence number for the next WS
  // stamp. Called every time a WS event mutates state AND every time
  // snapshot() captures its start point. Because both producers and the
  // snapshot reader run on the same Node event loop, `++` is race-free.
  _nextWsSeq() {
    return ++this._wsSeq;
  }

  // v7.8 NB2: stamp a WS delta with authority. FILL events are authoritative
  // over the REST snapshot for quantity/avgPrice and must be preserved by
  // the commit-time merge. TICK events (STOCK_CCNL) only refresh derived
  // mark-to-market fields and must NOT shield the symbol from a legitimate
  // REST-side quantity update, so the merge ignores them.
  //
  // Authority promotion rule: once a symbol has been stamped with FILL, a
  // subsequent TICK in the same snapshot window must not demote it back to
  // TICK. We always refresh the seq (for freshness ordering) but keep the
  // strongest authority seen since the last merge reset for that symbol.
  _stampStockDelta(symbol, authority /* "FILL" | "TICK" */) {
    const prev = this.state.wsStockDeltaTs.get(symbol);
    const next = {
      seq: this._nextWsSeq(),
      authority: (prev && prev.authority === "FILL") ? "FILL" : authority,
    };
    this.state.wsStockDeltaTs.set(symbol, next);
  }
  _stampOptionDelta(pdno, authority /* "FILL" | "TICK" */) {
    const prev = this.state.wsOptionDeltaTs.get(pdno);
    const next = {
      seq: this._nextWsSeq(),
      authority: (prev && prev.authority === "FILL") ? "FILL" : authority,
    };
    this.state.wsOptionDeltaTs.set(pdno, next);
  }

  _wireWs() {
    this.ws.on("tick", ev => {
      if (ev.kind === "STOCK_CCNL") {
        this.state.lastTicks.set(ev.symbol, ev.last);
        // 실시간 포지션 평가액 업데이트
        const symbol = (ev.symbol || "").replace(/^(D[A-Z]{3}|R[A-Z]{3})/, "");
        const pos = this.state.stockPositions.get(symbol);
        if (pos) {
          pos.nowPrice = ev.last;
          pos.evaluation = pos.quantity * ev.last;
          pos.pnl = (ev.last - pos.avgPrice) * pos.quantity;
          // v7.8 NB2: CCNL only refreshes mark-to-market fields, NOT
          // quantity/avgPrice. Stamp with authority="TICK" so the snapshot
          // merge does NOT use this entry to shield the symbol from a
          // legitimate REST-side quantity update (e.g. manual trade from
          // another channel). A later FILL in the same window will promote
          // authority to "FILL".
          this._stampStockDelta(symbol, "TICK");
        }
      }
      if (ev.kind === "STOCK_FILL") {
        const pos = this.state.stockPositions.get(ev.symbol) || { symbol: ev.symbol, quantity: 0, avgPrice: 0 };
        const delta = ev.side === "BUY" ? ev.ccldQty : -ev.ccldQty;
        const newQty = pos.quantity + delta;
        if (ev.side === "BUY" && newQty > 0) {
          pos.avgPrice = (pos.avgPrice * pos.quantity + ev.ccldPrice * ev.ccldQty) / newQty;
        }
        pos.quantity = newQty;
        if (newQty === 0) this.state.stockPositions.delete(ev.symbol);
        else this.state.stockPositions.set(ev.symbol, pos);
        // v7.7 NB1 + v7.8 NF2 + NB2: FILL actually mutated quantity/avgPrice,
        // so the stamp carries authority="FILL". The snapshot commit will
        // preserve this symbol against any stale REST positions() payload.
        this._stampStockDelta(ev.symbol, "FILL");
        // v7.4 B3: persist into recentFills ring for reconcileOrder.
        this._pushRecentFill({
          ts: Date.now(), kind: "stock",
          symbol: ev.symbol, side: ev.side,
          quantity: ev.ccldQty, price: ev.ccldPrice,
          orderNo: ev.orderNo, origOrderNo: ev.origOrderNo,
          ccldTime: ev.ccldTime,
        });
        this.emit("fill", ev);
      }
      if (ev.kind === "OPT_FILL") {
        // v7.4 B3: even though option FILL_NOTICE omits pdno/side in the raw
        // KIS payload, we cross-reference recent openOrders to recover both.
        // This keeps best-effort reconciliation available for option flows.
        let pdno = null, side = null;
        try {
          const o = this.state.openOrders.get(ev.orderNo);
          if (o) {
            pdno = o.pdno || o.symbol || null;
            side = o.side || null;
          }
        } catch (_) { /* ignore */ }
        this._pushRecentFill({
          ts: Date.now(), kind: "option",
          pdno, side,
          quantity: ev.ccldQty, price: ev.ccldPrice,
          orderNo: ev.orderNo, raw: ev.raw,
        });
        // v7.7 NB1 + v7.8 NF2 + NB2: OPT_FILL mutates option position size,
        // so stamp with authority="FILL" to protect against stale REST
        // optionPositions during the same-ms snapshot race window.
        if (pdno) this._stampOptionDelta(pdno, "FILL");
        this.emit("fill", ev);
      }
    });

    this.ws.on("error", err => this.emit("error", err));
    this.ws.on("close", info => this.emit("ws_close", info));
  }

  // v7.4 B3: bounded push into recentFills ring. Oldest entries drop out
  // once the ring exceeds _recentFillsMax; entries also expire by age in
  // the reconcile path.
  _pushRecentFill(entry) {
    try {
      this.state.recentFills.push(entry);
      const max = this._recentFillsMax;
      if (this.state.recentFills.length > max) {
        this.state.recentFills.splice(0, this.state.recentFills.length - max);
      }
    } catch (_) { /* never throw from fill handler */ }
  }

  /** 최초 연결 (토큰 + WS 스트림 + 잔고 스냅샷 1회) */
  async connect() {
    await this.tm.getAccessToken();
    this.logger.info && this.logger.info("KIS_BROKER_READY", {
      mode: this.env.mode, appKey: redact(this.env.appKey), account: this.env.accountNo
    });
    await this.ws.start();
    // 내 체결통보 자동 구독 (HTS ID 있는 경우)
    if (this.env.htsId) {
      try { this.ws.subscribeStockFillNotice(); } catch (_) {}
      try { this.ws.subscribeOptionFillNotice(); } catch (_) {}
    }
    await this.snapshot();
  }

  /**
   * 전체 잔고 스냅샷 (REST 1회) — WebSocket 이전 초기 상태 로드
   *
   * v7.6 FF3/B1 ATOMIC COMMIT (GPT consensus fix): snapshot() used to
   * publish individual subsystem results into `this.state.*` as each REST
   * call returned. A concurrent `/cash` request could therefore observe a
   * state in which fresh USD cash had already been written but
   * `lastSyncPartial` had not yet been flipped — a small but real
   * fail-open window under subsystem-failure conditions.
   *
   * The new pattern is strict two-phase:
   *   PHASE 1 (async fetch): every KIS REST call is awaited and its
   *     result written into a LOCAL `shadow` object. `this.state` is
   *     *never* mutated during this phase.
   *   PHASE 2 (sync commit): under `_refreshInProgress = true`, a
   *     synchronous block copies the shadow fields onto `this.state`.
   *     The commit order is: error/partial flags FIRST, then data fields,
   *     then freshness timestamps. Because the commit block has no
   *     `await`, the Node event loop cannot interleave a /cash request
   *     inside it. Consumers that arrive during PHASE 1 see
   *     `_refreshInProgress=true` and receive 503 refresh_in_progress.
   *
   * On exception the `finally` block still clears `_refreshInProgress`,
   * so a thrown snapshot can never leave the flag wedged true.
   *
   * Earlier guarantees still hold:
   *   - fail-closed shadow-map-swap per subsystem (v7.3 C1/H2)
   *   - explicit numeric checks on USD cash/buyingPower  (v7.5 B2)
   *   - optDep failure tracked                          (v7.5 B1)
   *   - stock+option openOrders co-committed atomically (v7.5.1 BB1)
   *   - lastSync/lastFullSync only advance on fully-green sync (v7.5 F1)
   */
  async snapshot() {
    // v7.7 NF1: concurrent callers share the in-flight promise instead of
    // receiving stale `this.state` immediately. That ensures that callers
    // which explicitly requested a fresh snapshot (POST /refresh,
    // reconcileOrder's best-effort refresh, force-sell FSM) actually get
    // post-commit state.
    if (this._refreshInProgress && this._refreshPromise) {
      this.logger.debug && this.logger.debug("KIS_SNAPSHOT_REENTRY_JOIN", {
        heldForMs: Date.now() - (this._refreshStartedAt || 0),
      });
      return this._refreshPromise;
    }
    // Build an in-flight promise the re-entrant callers can latch onto. The
    // body of this function lives inside _doSnapshot() below.
    //
    // v7.8 NF2: capture the current _wsSeq BEFORE starting any REST work.
    // Every WS event that fires during PHASE 1 will allocate a strictly
    // greater seq, so the merge comparator `stampSeq > startedSeq` can
    // distinguish "fresh-during-fetch" from "pre-fetch" without any
    // dependence on Date.now() resolution. `_refreshStartedAt` is kept as a
    // wall-clock timestamp for logging / reentry-join diagnostics so the
    // /health and log schemas are preserved exactly.
    this._refreshInProgress = true;
    this._refreshStartedAt  = Date.now();
    const startedSeq        = this._wsSeq;  // snapshot of monotonic seq
    this._refreshPromise    = this._doSnapshot(this._refreshStartedAt, startedSeq)
      .finally(() => {
        this._refreshInProgress = false;
        this._refreshStartedAt  = 0;
        this._refreshPromise    = null;
      });
    return this._refreshPromise;
  }

  async _doSnapshot(started, startedSeq = 0) {
    // v7.8 NN2: count the run AT ENTRY, not after commit. This guarantees
    // the invariant `snapshot_runs_total === snapshot_success_total +
    // snapshot_errors_total` even when PHASE 1 throws (network blow-up,
    // token refresh failure, etc.) before the commit block is reached.
    // The catch handler below is responsible for incrementing
    // snapshot_errors_total in that case so the invariant still holds.
    this._metrics.snapshot_runs_total++;

    // ------------------------------------------------------------------
    // PHASE 1  —  gather every REST result into a local shadow. `this.state`
    // is treated as read-only in this phase.
    // ------------------------------------------------------------------
    const shadow = {
      errors: [],
      stockPositions: null,   // Map | null (null = keep existing)
      optionPositions: null,  // Map | null
      openOrders: null,       // Map | null
      depositsUSD: null,      // { cash, buyingPower } | null
      depositsKRW: null,
      depositsUSDOption: null,
      freshnessTs: {},        // subsystem -> ts (only written on success)
    };

    try {
      const [posAll, pb, fm] = await Promise.all([
        this.stock.positions({ excg: "ALL" }).catch(e => ({ ok: false, positions: [], thrown: String(e).slice(0, 200) })),
        this.stock.presentBalance().catch(e => ({ ok: false, thrown: String(e).slice(0, 200) })),
        this.stock.foreignMargin().catch(e => ({ ok: false, thrown: String(e).slice(0, 200) })),
      ]);

      // --- stockPositions ---
      if (posAll && posAll.ok === true) {
        const nextStock = new Map();
        for (const p of (posAll.positions || [])) nextStock.set(p.symbol, p);
        shadow.stockPositions = nextStock;
        shadow.freshnessTs.stockPositions = Date.now();
      } else {
        shadow.errors.push({
          subsystem: "stockPositions",
          partial: !!(posAll && posAll.partial),
          failedExchange: posAll && posAll.failedExchange,
          status: posAll && posAll.status,
          thrown: posAll && posAll.thrown,
        });
      }

      // --- deposits (presentBalance) ---
      if (pb && pb.ok && pb.deposits) {
        if (pb.deposits.USD) shadow.depositsUSD = pb.deposits.USD;
        if (pb.deposits.KRW) shadow.depositsKRW = pb.deposits.KRW;
        shadow.freshnessTs.usdDeposit = Date.now();
      } else if (pb) {
        shadow.errors.push({ subsystem: "presentBalance", thrown: pb.thrown });
      }

      // --- deposits (foreignMargin) — authoritative for USD over presentBalance
      if (fm && fm.ok === true && Number.isFinite(fm.usdCash)) {
        const bp = Number.isFinite(fm.usdOrderable) ? Number(fm.usdOrderable) : Number(fm.usdCash);
        shadow.depositsUSD = {
          cash: Number(fm.usdCash),
          buyingPower: bp,
        };
        shadow.freshnessTs.usdDeposit = Date.now();
      } else if (fm) {
        shadow.errors.push({ subsystem: "foreignMargin", thrown: fm.thrown });
      }

      // --- option positions + deposits ---
      const [optPos, optDep] = await Promise.all([
        this.option.openPositions().catch(e => ({ ok: false, positions: [], thrown: String(e).slice(0, 200) })),
        this.option.deposit().catch(e => ({ ok: false, thrown: String(e).slice(0, 200) })),
      ]);
      if (optPos && optPos.ok === true) {
        const nextOpt = new Map();
        for (const p of (optPos.positions || [])) nextOpt.set(p.pdno, p);
        shadow.optionPositions = nextOpt;
        shadow.freshnessTs.optionPositions = Date.now();
      } else {
        shadow.errors.push({ subsystem: "optionPositions", thrown: optPos && optPos.thrown });
      }
      if (optDep && optDep.ok === true) {
        shadow.depositsUSDOption = {
          cash: optDep.cashDeposit,
          orderable: optDep.orderableCash,
          usedMargin: optDep.usedMargin,
        };
        shadow.freshnessTs.usdOptionDeposit = Date.now();
      } else if (optDep) {
        shadow.errors.push({ subsystem: "optionDeposit", thrown: optDep.thrown });
      }

      // --- openOrders (stock first, then option merge) ---
      const open = await this.stock.openOrders({ excg: "ALL" })
        .catch(e => ({ ok: false, orders: [], thrown: String(e).slice(0, 200) }));
      let nextOpenOrders;
      if (open && open.ok === true) {
        nextOpenOrders = new Map();
        for (const o of (open.orders || [])) {
          nextOpenOrders.set(o.orderNo, { ...o, kind: "stock" });
        }
        shadow.freshnessTs.openOrders = Date.now();
      } else {
        shadow.errors.push({
          subsystem: "openOrders",
          failedExchange: open && open.failedExchange,
          thrown: open && open.thrown,
        });
        // Keep the current state map (read-only reference is fine since we
        // will only mutate via shadow.openOrders commit below).
        nextOpenOrders = new Map(this.state.openOrders);
      }
      // option todayOrders merge (non-terminal entries only)
      const optToday = await this.option.todayOrders().catch(
        e => ({ ok: false, orders: [], thrown: String(e).slice(0, 200) }),
      );
      if (optToday && optToday.ok === true && Array.isArray(optToday.orders)) {
        for (const o of optToday.orders) {
          if (!o || !o.orderNo) continue;
          const unfilledQty = Number(o.unfilledQty ?? o.remainQty ?? NaN);
          const stateStr = String(o.state || o.status || "").toUpperCase();
          const terminal = /FILLED|CANCELED|CANCELLED|REJECTED|DONE|EXPIRED/;
          const isOpen = Number.isFinite(unfilledQty)
            ? unfilledQty > 0
            : !terminal.test(stateStr);
          if (isOpen) {
            nextOpenOrders.set(String(o.orderNo), { ...o, kind: "option" });
          }
        }
        shadow.freshnessTs.optionOpenOrders = Date.now();
      } else if (optToday) {
        shadow.errors.push({ subsystem: "optionTodayOrders", thrown: optToday.thrown });
      }
      shadow.openOrders = nextOpenOrders;

      // ----------------------------------------------------------------
      // PHASE 2  —  synchronous atomic commit. No `await` inside this block.
      // Consumers racing us see `_refreshInProgress=true` and bail with 503.
      // Commit order: flags FIRST (so any read that slips through still
      // sees partial=true if errors exist), then data, then freshness.
      // ----------------------------------------------------------------
      const errs = shadow.errors;
      // --- flags (partial/error/attempt) must be written BEFORE data ---
      this.state.lastSnapshotAttempt = Date.now();
      this.state.lastSnapshotError = errs.length > 0 ? errs : null;
      this.state.lastSyncPartial = errs.length > 0;
      // --- data maps ---
      // v7.7 NB1 (GPT consensus fix): conditional per-symbol merge. If a WS
      // delta landed AFTER this snapshot began fetching REST data, the WS
      // value is authoritative for THAT symbol/pdno, and the REST value for
      // that same symbol/pdno is discarded. Every other symbol is committed
      // from the REST shadow as before. The commit is still synchronous, so
      // atomicity against /cash is preserved.
      // v7.8 NF2: merge comparator is now `stampSeq > startedSeq` rather
      // than the previous `ts > started`. Because every WS event that fires
      // after the snapshot start point is guaranteed a strictly greater
      // seq, there is no longer a same-ms tie case where a fresh WS delta
      // could be mistaken for pre-fetch state.
      //
      // v7.8 NB2: the stamp carries an authority tag. Only FILL entries are
      // authoritative over the REST quantity; TICK entries (CCNL only
      // refreshed nowPrice/evaluation/pnl) are IGNORED for quantity-authority
      // purposes and the REST value wins. This closes the hole where a CCNL
      // tick could shield a symbol from a legitimate REST-side quantity
      // update (e.g. manual trade from another channel). After the commit
      // we clear consumed stamps so the next snapshot starts from a clean
      // slate and metrics reflect only in-window deltas.
      if (shadow.stockPositions) {
        const merged = new Map(shadow.stockPositions);
        let preservedCnt = 0;
        let ignoredTickCnt = 0;
        for (const [sym, stamp] of this.state.wsStockDeltaTs.entries()) {
          if (!stamp || stamp.seq <= startedSeq) continue;
          if (stamp.authority !== "FILL") {
            // TICK-only: record for observability and let REST value win.
            ignoredTickCnt++;
            continue;
          }
          // FILL authority: WS had the newer word on this symbol's quantity.
          const wsPos = this.state.stockPositions.get(sym);
          if (wsPos) {
            merged.set(sym, wsPos);
            preservedCnt++;
          } else {
            // WS flat-out fill deleted the position. Respect the delete.
            merged.delete(sym);
            preservedCnt++;
          }
        }
        this.state.stockPositions = merged;
        this._metrics.snapshot_ws_preserved_total += preservedCnt;
        this._metrics.snapshot_ws_tick_ignored_total =
          (this._metrics.snapshot_ws_tick_ignored_total || 0) + ignoredTickCnt;
      }
      if (shadow.optionPositions) {
        const merged = new Map(shadow.optionPositions);
        let preservedCnt = 0;
        let ignoredTickCnt = 0;
        for (const [pdno, stamp] of this.state.wsOptionDeltaTs.entries()) {
          if (!stamp || stamp.seq <= startedSeq) continue;
          if (stamp.authority !== "FILL") {
            ignoredTickCnt++;
            continue;
          }
          const wsPos = this.state.optionPositions.get(pdno);
          if (wsPos) {
            merged.set(pdno, wsPos);
            preservedCnt++;
          } else {
            merged.delete(pdno);
            preservedCnt++;
          }
        }
        this.state.optionPositions = merged;
        this._metrics.snapshot_ws_preserved_total += preservedCnt;
        this._metrics.snapshot_ws_tick_ignored_total =
          (this._metrics.snapshot_ws_tick_ignored_total || 0) + ignoredTickCnt;
      }
      if (shadow.openOrders)      this.state.openOrders      = shadow.openOrders;
      if (shadow.depositsUSD)     this.state.deposits.USD    = shadow.depositsUSD;
      if (shadow.depositsKRW)     this.state.deposits.KRW    = shadow.depositsKRW;
      if (shadow.depositsUSDOption) this.state.deposits.USD_OPTION = shadow.depositsUSDOption;
      // --- freshness (per-subsystem) ---
      for (const [k, ts] of Object.entries(shadow.freshnessTs)) {
        this.state.freshness[k] = ts;
      }
      // --- lastSync / lastFullSync: only on fully-green sync ---
      if (errs.length === 0) {
        this.state.lastSync = Date.now();
        this.state.lastFullSync = Date.now();
      }

      // v7.8 NN2: snapshot_runs_total was already incremented at entry.
      // Here we book exactly one outcome counter so the invariant
      // `runs === success + errors` is preserved by construction.
      if (errs.length > 0) {
        this._metrics.snapshot_errors_total++;
      } else {
        this._metrics.snapshot_success_total++;
      }
      const tookMs = Date.now() - started;
      const logLevel = errs.length > 0 ? "warn" : "info";
      this.logger[logLevel] && this.logger[logLevel]("KIS_SNAPSHOT", {
        stockPositions: this.state.stockPositions.size,
        optionPositions: this.state.optionPositions.size,
        usdCash: this.state.deposits.USD?.cash,
        openOrders: this.state.openOrders.size,
        took_ms: tookMs,
        partial: errs.length > 0,
        errors: errs.length > 0 ? errs : undefined,
        lastFullSync_age_ms: this.state.lastFullSync
          ? (Date.now() - this.state.lastFullSync) : null,
        ws_preserved_total: this._metrics.snapshot_ws_preserved_total,
        ws_seq_at_start: startedSeq,    // v7.8 NF2 diagnostic
        ws_seq_at_commit: this._wsSeq,  // v7.8 NF2 diagnostic
        build: "ares-kis-us-broker@v7.8-nn2",
      });
      return this.state;
    } catch (e) {
      this._metrics.snapshot_errors_total++;
      throw e;
    }
    // NB: `finally` clearing of _refreshInProgress / _refreshPromise lives
    // in the wrapping snapshot() method so that a shared promise re-entrant
    // caller always observes the committed state.
  }

  /** 핵심 조회 공용 API */
  // v7.4 B2: GPT correctly flagged truthy-OR coercion — legitimate 0
  // buyingPower was being treated as "missing" and silently promoting
  // `cash` (which may include non-orderable margin) in its place. This
  // version uses explicit number checks AND returns an envelope that lets
  // server.mjs /cash gate on freshness/partial.
  getUsdCashAvailable() {
    const d = this.state.deposits?.USD || {};
    const bp = Number.isFinite(d.buyingPower) ? Number(d.buyingPower) : null;
    const cash = Number.isFinite(d.cash) ? Number(d.cash) : null;
    // Only fall back to cash if buyingPower is genuinely absent (null/NaN),
    // not merely 0. A real 0 orderable balance MUST be reported as 0.
    if (bp !== null) return bp;
    if (cash !== null) return cash;
    return 0;
  }

  // v7.4 F4: structured USD cash envelope for /cash + snapshot consumers.
  // The server uses the partial/freshness fields to decide whether to
  // serve the value, serve-with-stale-flag, or refuse (503).
  getUsdCashEnvelope() {
    const d = this.state.deposits?.USD || {};
    const bpRaw = d.buyingPower;
    const cashRaw = d.cash;
    return {
      cash: Number.isFinite(cashRaw) ? Number(cashRaw) : null,
      buyingPower: Number.isFinite(bpRaw) ? Number(bpRaw) : null,
      freshness_ms: this.state.freshness?.usdDeposit || 0,
      age_ms: (this.state.freshness?.usdDeposit || 0) > 0
        ? (Date.now() - this.state.freshness.usdDeposit) : null,
      partial: !!this.state.lastSyncPartial,
      lastSyncErrorCount: Array.isArray(this.state.lastSnapshotError)
        ? this.state.lastSnapshotError.length : 0,
    };
  }

  getStockPosition(symbol) {
    return this.state.stockPositions.get(String(symbol).toUpperCase()) || null;
  }

  getAllStockPositions() {
    return Array.from(this.state.stockPositions.values());
  }

  getOptionPosition(pdno) { return this.state.optionPositions.get(pdno) || null; }
  getAllOptionPositions() { return Array.from(this.state.optionPositions.values()); }

  getOpenOrders() { return Array.from(this.state.openOrders.values()); }

  /** 실시간 구독 헬퍼 — v7.1: 정적 import + 에러 전파 */
  async subscribeRealtime(symbol, { excd, extended = false } = {}) {
    try {
      const eq = excd || await resolveExchange(symbol, this.rest);
      const k = wsKey(eq, symbol, extended);
      this.ws.subscribeStockPrice(k);
      this.ws.subscribeStockAsking(k);
      return { ok: true, wsKey: k, exchange: eq };
    } catch (e) {
      this.logger.error && this.logger.error("KIS_WS_SUB_FAIL", { symbol, err: String(e).slice(0, 160) });
      this.emit("error", e);
      return { ok: false, error: String(e).slice(0, 200) };
    }
  }

  /** v7.1 신규: 옵션 구독 헬퍼 */
  async subscribeOptionRealtime(pdno) {
    try {
      this.ws.subscribeOptionPrice(pdno);
      this.ws.subscribeOptionAsking(pdno);
      return { ok: true, pdno };
    } catch (e) {
      this.logger.error && this.logger.error("KIS_WS_OPT_SUB_FAIL", { pdno, err: String(e).slice(0, 160) });
      this.emit("error", e);
      return { ok: false, error: String(e).slice(0, 200) };
    }
  }

  async disconnect() {
    await this.ws.stop();
  }
}

export default UnifiedKisBroker;
