// tests/test_v7_6_atomic_snapshot.mjs
// v7.6 FF3/B1 regression: under concurrent /cash requests, a snapshot() that
// ultimately fails (e.g. option subsystem down) MUST NOT produce a 200
// response from /cash during any observable window.
//
// Strategy: we import the broker class directly (no network boot), stub out
// every KIS REST dependency with controllable delays, then call snapshot()
// from within a test harness that simulates a server /cash handler running
// concurrently on every event-loop tick. If the harness sees any window in
// which the simulated /cash handler would have returned 200, the test fails.
//
// Run with:
//   node /home/ubuntu/ares_kis_upgrade/ec2_broker_v7/tests/test_v7_6_atomic_snapshot.mjs
//
// Exit 0 on pass, 1 on fail.

import { UnifiedKisBroker } from "../index.mjs";

// ---------------------------------------------------------------------------
// Minimal stub-injection helper. We cannot pass stocks/options into the ctor
// so we monkey-patch after construction. This mirrors what the server.mjs
// real runtime does and keeps the test black-box.
// ---------------------------------------------------------------------------
function newBroker() {
  const b = new UnifiedKisBroker({ logger: { info() {}, warn() {}, error() {} } });
  // Stub token/rest/ws so constructor did not crash in env check — we discard
  // whatever they wired up.
  b.tm  = { getAccessToken: async () => "stub" };
  b.rest = {};
  b.ws = { start: async () => {}, stop: async () => {}, on() {}, subscribeStockFillNotice() {}, subscribeOptionFillNotice() {} };
  return b;
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

// Simulated /cash handler that exactly mirrors the logic in server.mjs.
// If this returns { code: 200 }, we have a bug.
function simulatedCashHandler(broker, { maxAge = 90_000 } = {}) {
  // v7.6 FF3/B1: refresh-in-progress gate (must be first)
  if (broker._refreshInProgress === true) {
    return { code: 503, reason: "refresh_in_progress" };
  }
  const env = broker.getUsdCashEnvelope();
  const cashPartial = env.partial || env.lastSyncErrorCount > 0;
  const cashStale = env.freshness_ms === 0 ||
    (typeof env.age_ms === "number" && env.age_ms > maxAge);
  if (cashPartial || cashStale) {
    return { code: 503, reason: cashPartial ? "partial_snapshot" : "stale_snapshot" };
  }
  return { code: 200, cash: env.cash, bp: env.buyingPower };
}

let totalTests = 0;
let failedTests = 0;
function assert(cond, msg) {
  totalTests++;
  if (!cond) {
    failedTests++;
    console.error(`FAIL: ${msg}`);
  } else {
    console.log(`PASS: ${msg}`);
  }
}

// ---------------------------------------------------------------------------
// Test 1: option subsystem failure during snapshot() must never produce a
// /cash:200 observable window.
// ---------------------------------------------------------------------------
async function testOptionFailNoFailOpenWindow() {
  const b = newBroker();

  // Seed a previously-successful snapshot so deposits look fresh before the
  // failing snapshot() starts. This is the exact scenario GPT described.
  b.state.deposits.USD = { cash: 10_000, buyingPower: 9_000 };
  b.state.freshness.usdDeposit = Date.now();
  b.state.lastSync = Date.now();
  b.state.lastSyncPartial = false;
  b.state.lastSnapshotError = null;

  // Now inject stubs: stock OK, foreignMargin OK (new cash value!), option FAIL.
  // Timing matters: we want option to resolve LAST so the commit only happens
  // after the failing subsystem returns.
  b.stock = {
    positions: async () => { await delay(5);  return { ok: true, positions: [] }; },
    presentBalance: async () => { await delay(5); return { ok: true, deposits: { USD: { cash: 11_000, buyingPower: 10_000 } } }; },
    foreignMargin: async () => { await delay(10); return { ok: true, usdCash: 12_000, usdOrderable: 11_500 }; },
    openOrders: async () => { await delay(30); return { ok: true, orders: [] }; },
  };
  b.option = {
    openPositions: async () => { await delay(20); return { ok: true, positions: [] }; },
    // Simulate option deposit failure AFTER stock/foreignMargin already
    // returned \u2014 the previous (pre-v7.6) implementation would have already
    // written deposits.USD from foreignMargin by this point. v7.6 must not.
    deposit: async () => { await delay(40); return { ok: false, thrown: "E_OPT_DEPOSIT_TIMEOUT" }; },
    todayOrders: async () => { await delay(15); return { ok: true, orders: [] }; },
  };

  // Harness: fire /cash handlers on every macrotask while snapshot() runs.
  // Collect every response. After snapshot resolves, assert no 200 was
  // observed and that deposits.USD reflects the pre-snapshot value OR the
  // new value but with partial flag set.
  // IMPORTANT ordering: start snapshot() FIRST so its synchronous prologue
  // sets `_refreshInProgress = true` before the probe loop gets a chance to
  // read state. In a real HTTP server this is how things play out too —
  // snapshot() is triggered internally and /cash arrives via the event
  // loop after snapshot()'s sync prologue has already latched the flag.
  const observations = [];
  let stopProbing = false;
  const snapP = b.snapshot();   // sync prologue runs IMMEDIATELY here.
  const probe = async () => {
    while (!stopProbing) {
      observations.push(simulatedCashHandler(b));
      await delay(0); // yield to event loop
    }
  };
  const probeP = probe();

  await snapP;

  stopProbing = true;
  await probeP;

  const code200s = observations.filter(o => o.code === 200);
  const code503RIPs = observations.filter(o => o.code === 503 && o.reason === "refresh_in_progress");
  const code503Partials = observations.filter(o => o.code === 503 && o.reason === "partial_snapshot");

  console.log(`  probes: total=${observations.length} 200=${code200s.length} 503(RIP)=${code503RIPs.length} 503(partial)=${code503Partials.length}`);

  assert(code200s.length === 0, "no /cash:200 observable during a failing snapshot (GPT FF3/B1)");
  assert(b.state.lastSyncPartial === true, "lastSyncPartial=true after option subsystem failure");
  assert(b._refreshInProgress === false, "_refreshInProgress cleared after snapshot resolves");
  assert(Array.isArray(b.state.lastSnapshotError) && b.state.lastSnapshotError.some(e => e.subsystem === "optionDeposit"),
    "optionDeposit failure recorded in lastSnapshotError");
  assert(code503RIPs.length > 0, "at least some probes hit the refresh_in_progress gate");

  // Post-condition: deposits.USD must not have been committed from foreignMargin
  // while lastSyncPartial was still false. The current v7.6 semantics allow
  // deposits.USD to be committed atomically together with lastSyncPartial=true
  // (that's fine \u2014 /cash will 503 anyway). What we verify is that there was
  // no window in which deposits.USD was new AND lastSyncPartial was false AND
  // _refreshInProgress was false.
  // This is implicit in the code200s==0 assertion above; we re-check explicitly.
  assert(
    !(b.state.deposits.USD.cash === 12_000 && b.state.lastSyncPartial === false && b._refreshInProgress === false),
    "new foreignMargin cash never visible with partial=false (atomic commit)"
  );
}

// ---------------------------------------------------------------------------
// Test 2: successful snapshot path \u2014 /cash should still return 200 afterwards
// and there should be NO stuck refreshInProgress flag.
// ---------------------------------------------------------------------------
async function testHappyPath() {
  const b = newBroker();
  b.stock = {
    positions: async () => ({ ok: true, positions: [] }),
    presentBalance: async () => ({ ok: true, deposits: { USD: { cash: 11_000, buyingPower: 10_000 } } }),
    foreignMargin: async () => ({ ok: true, usdCash: 12_000, usdOrderable: 11_500 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: true, cashDeposit: 100, orderableCash: 90, usedMargin: 10 }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };
  await b.snapshot();

  assert(b.state.lastSyncPartial === false, "lastSyncPartial=false on fully-green snapshot");
  assert(b._refreshInProgress === false, "_refreshInProgress cleared after happy path");
  assert(b.state.lastFullSync > 0, "lastFullSync advanced on fully-green snapshot");

  const r = simulatedCashHandler(b);
  assert(r.code === 200 && r.cash === 12_000, "/cash returns 200 with expected USD cash after happy path");
}

// ---------------------------------------------------------------------------
// Test 3: snapshot() re-entry is rejected and doesn't wedge the flag.
// ---------------------------------------------------------------------------
async function testReentryGuard() {
  const b = newBroker();
  b.stock = {
    positions: async () => { await delay(30); return { ok: true, positions: [] }; },
    presentBalance: async () => ({ ok: true, deposits: {} }),
    foreignMargin: async () => ({ ok: true, usdCash: 1_000, usdOrderable: 900 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };
  const p1 = b.snapshot();
  // Immediately fire a second snapshot() \u2014 v7.6 guard should skip it.
  await delay(2);
  const p2 = b.snapshot();
  await Promise.all([p1, p2]);
  assert(b._refreshInProgress === false, "flag cleared after two concurrent snapshot() calls");
  assert(b.state.deposits.USD.cash === 1_000, "first snapshot committed its data");
}

// ---------------------------------------------------------------------------
// Test 4: snapshot() throwing must still clear the refreshInProgress flag.
// ---------------------------------------------------------------------------
async function testFinallyClearsFlagOnThrow() {
  const b = newBroker();
  b.stock = {
    positions: async () => { throw new Error("stub_positions_throw"); },
    presentBalance: async () => ({ ok: true, deposits: {} }),
    foreignMargin: async () => ({ ok: true, usdCash: 1_000, usdOrderable: 900 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };
  // The inner `.catch(e => ({...}))` wraps each REST call so snapshot() itself
  // will not throw; instead we induce a throw by nuking `broker.state`
  // temporarily. This exercises the finally clause.
  const realSnapshot = b.snapshot.bind(b);
  b.snapshot = async function wrapped() {
    b._refreshInProgress = false; // reset just in case
    try {
      // Inject a fault into PHASE 2 via a Proxy that throws on assignment.
      // We wrap state.deposits so any write throws.
      const origDeposits = b.state.deposits;
      b.state.deposits = new Proxy(origDeposits, {
        set() { throw new Error("test_induced_commit_failure"); }
      });
      try {
        await realSnapshot();
      } finally {
        b.state.deposits = origDeposits;
      }
    } catch (e) {
      // expected
    }
  };
  await b.snapshot();
  assert(b._refreshInProgress === false, "refreshInProgress cleared even when commit throws");
}

// ---------------------------------------------------------------------------
// v7.7 NB1: WS STOCK_FILL that lands DURING snapshot() must survive PHASE 2
// commit. The REST positions fetch returned a stale quantity for the same
// symbol; the conditional merge must prefer the newer WS value.
// ---------------------------------------------------------------------------
async function testWsFillPreservedDuringSnapshot() {
  const b = newBroker();
  // Pre-seed state: empty positions; REST will return AAPL qty=10, but WS
  // will fire AAPL qty-bump to 25 during the snapshot fetch window.
  b.stock = {
    positions: async () => {
      await delay(40);
      return { ok: true, positions: [{ symbol: "AAPL", quantity: 10, avgPrice: 180 }] };
    },
    presentBalance: async () => ({ ok: true, deposits: {} }),
    foreignMargin: async () => ({ ok: true, usdCash: 1_000, usdOrderable: 900 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };

  const snapP = b.snapshot();
  // Simulate a WS STOCK_FILL arriving mid-snapshot: bump AAPL to qty=25.
  await delay(10);
  b.state.stockPositions.set("AAPL", { symbol: "AAPL", quantity: 25, avgPrice: 180, nowPrice: 195 });
  // v7.8 NB2: simulate the real handler — a STOCK_FILL would call
  // _stampStockDelta(sym, "FILL"), giving the stamp FILL authority.
  b._stampStockDelta("AAPL", "FILL");
  await snapP;

  const pos = b.state.stockPositions.get("AAPL");
  assert(pos && pos.quantity === 25, "NB1: WS-updated quantity (25) preserved after snapshot commit (REST had 10)");
  assert(b._metrics.snapshot_ws_preserved_total >= 1, "NB1: snapshot_ws_preserved_total counter incremented");
}

// ---------------------------------------------------------------------------
// v7.7 NB1 (delete variant): WS STOCK_FILL that closes out a position to
// quantity=0 mid-snapshot must cause that symbol to be DELETED from state
// after commit, even if the REST fetch still returned a non-zero position.
// ---------------------------------------------------------------------------
async function testWsFlatFillPreservedDuringSnapshot() {
  const b = newBroker();
  b.stock = {
    positions: async () => {
      await delay(40);
      return { ok: true, positions: [{ symbol: "TSLA", quantity: 5, avgPrice: 240 }] };
    },
    presentBalance: async () => ({ ok: true, deposits: {} }),
    foreignMargin: async () => ({ ok: true, usdCash: 1_000, usdOrderable: 900 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };

  // Pre-seed the position so WS can "close" it.
  b.state.stockPositions.set("TSLA", { symbol: "TSLA", quantity: 5, avgPrice: 240 });

  const snapP = b.snapshot();
  await delay(10);
  // Simulate full-close WS fill: delete + stamp delta timestamp.
  b.state.stockPositions.delete("TSLA");
  // v7.8 NB2: flat-out close is a FILL event — authority="FILL".
  b._stampStockDelta("TSLA", "FILL");
  await snapP;

  assert(!b.state.stockPositions.has("TSLA"), "NB1: WS flat-out fill (delete) preserved against stale REST positions");
}

// ---------------------------------------------------------------------------
// v7.7 NF1: concurrent callers share the same in-flight promise and observe
// the committed post-refresh state (not an immediate stale reference).
// ---------------------------------------------------------------------------
async function testSharedRefreshPromise() {
  const b = newBroker();
  b.stock = {
    positions: async () => { await delay(30); return { ok: true, positions: [] }; },
    presentBalance: async () => ({ ok: true, deposits: {} }),
    foreignMargin: async () => ({ ok: true, usdCash: 3_333, usdOrderable: 3_000 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };

  // Use microtask yield (Promise.resolve) instead of setTimeout so the
  // underlying snapshot's delay(30) can't resolve between p1 and p3. In a
  // real server with many concurrent /cash-triggered refreshes, this is
  // how the event loop actually queues them.
  //
  // NOTE on reference equality: `async snapshot()` always wraps its return
  // value in a fresh Promise at every call site, so `p1 === p2` would be
  // false even when both latched onto the same in-flight work. The
  // correctness property we actually care about is that the work ran ONCE
  // and all callers resolve to the same committed state, which the next
  // three assertions verify. We use the internal _metrics counter to
  // prove the single-run property.
  const runsBefore = b._metrics.snapshot_runs_total;
  const p1 = b.snapshot();
  await Promise.resolve();
  const p2 = b.snapshot();
  await Promise.resolve();
  const p3 = b.snapshot();

  const [r1, r2, r3] = await Promise.all([p1, p2, p3]);
  const runsAfter = b._metrics.snapshot_runs_total;
  assert(runsAfter - runsBefore === 1, "NF1: three concurrent snapshot() calls triggered exactly ONE underlying run (shared in-flight promise)");
  assert(r1 === r2 && r2 === r3, "NF1: all concurrent callers receive the same resolved state reference");
  assert(b.state.deposits.USD.cash === 3_333, "NF1: state fully committed after shared promise resolves");
  assert(b._refreshInProgress === false && b._refreshPromise === null, "NF1: refresh flags cleared post-commit");
}

async function testSharedRefreshPromise_legacy_stub() {
  // legacy test name kept so the main() dispatch below still matches.

  // (above block already asserts r1/r2/r3 equality and flag clearing)
  return;
}

// ---------------------------------------------------------------------------
// v7.8 NF2 — T28: when a WS STOCK_FILL lands in the SAME millisecond as the
// snapshot-start, the fresh WS value MUST still be preserved. The v7.7
// `ts > started` comparator fails this case whenever Date.now() is identical
// for the two events. The v7.8 monotonic _wsSeq comparator must pass it.
// ---------------------------------------------------------------------------
async function testNF2_SameMsPreserve() {
  const b = newBroker();
  // Force Date.now() to return a constant value so the v7.7 comparator would
  // be guaranteed to see equal timestamps. If the code has been properly
  // migrated to seq comparison, this has no effect on correctness — the
  // merge should still preserve the WS delta.
  const FIXED = 1_700_000_000_000;
  const origDateNow = Date.now;
  Date.now = () => FIXED;
  try {
    b.stock = {
      positions: async () => {
        // Return a stale REST quantity BEFORE we publish the WS delta so
        // phase-1 shadow = qty:10, but by commit time WS has qty:77.
        await delay(30);
        return { ok: true, positions: [{ symbol: "NVDA", quantity: 10, avgPrice: 800 }] };
      },
      presentBalance: async () => ({ ok: true, deposits: {} }),
      foreignMargin: async () => ({ ok: true, usdCash: 5_000, usdOrderable: 4_800 }),
      openOrders: async () => ({ ok: true, orders: [] }),
    };
    b.option = {
      openPositions: async () => ({ ok: true, positions: [] }),
      deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
      todayOrders: async () => ({ ok: true, orders: [] }),
    };

    const seqBefore = b._wsSeq;
    const snapP = b.snapshot();
    // While snapshot is fetching REST (30ms delay), fire a WS fill with
    // Date.now() PINNED to the same value as the snapshot's captured
    // _refreshStartedAt. Under v7.7 this tied the comparator and lost.
    await delay(10);
    b.state.stockPositions.set("NVDA", { symbol: "NVDA", quantity: 77, avgPrice: 800 });
    // v7.8 NB2: stamp the same way the real STOCK_FILL handler does.
    b._stampStockDelta("NVDA", "FILL");
    await snapP;

    const pos = b.state.stockPositions.get("NVDA");
    assert(pos && pos.quantity === 77, "NF2 (T28): WS delta that fired in the same ms as snapshot-start preserved (qty=77, not 10)");
    assert(b._wsSeq > seqBefore, "NF2 (T28): _wsSeq strictly advanced across the test");
    assert(b._metrics.snapshot_ws_preserved_total >= 1, "NF2 (T28): ws_preserved_total incremented");
  } finally {
    Date.now = origDateNow;
  }
}

// ---------------------------------------------------------------------------
// v7.8 NF2 — T28-2: when TWO WS deltas fire for the same symbol in the same
// millisecond, the final committed state must reflect the LAST one (strict
// monotonic ordering via _wsSeq even at sub-ms resolution).
// ---------------------------------------------------------------------------
async function testNF2_DualSameMsOrder() {
  const b = newBroker();
  const FIXED = 1_700_000_000_000;
  const origDateNow = Date.now;
  Date.now = () => FIXED;
  try {
    b.stock = {
      positions: async () => {
        await delay(30);
        return { ok: true, positions: [{ symbol: "MSFT", quantity: 1, avgPrice: 410 }] };
      },
      presentBalance: async () => ({ ok: true, deposits: {} }),
      foreignMargin: async () => ({ ok: true, usdCash: 2_000, usdOrderable: 1_800 }),
      openOrders: async () => ({ ok: true, orders: [] }),
    };
    b.option = {
      openPositions: async () => ({ ok: true, positions: [] }),
      deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
      todayOrders: async () => ({ ok: true, orders: [] }),
    };

    const snapP = b.snapshot();
    await delay(10);
    // Two "same ms" WS deltas in order. The last one should win because it
    // is allocated a higher seq. Under a ts-only scheme these would both
    // collapse to a tied ts and the comparator would be undefined.
    b.state.stockPositions.set("MSFT", { symbol: "MSFT", quantity: 50, avgPrice: 410 });
    b._stampStockDelta("MSFT", "FILL");
    const seqA = b.state.wsStockDeltaTs.get("MSFT").seq;
    b.state.stockPositions.set("MSFT", { symbol: "MSFT", quantity: 99, avgPrice: 410 });
    b._stampStockDelta("MSFT", "FILL");
    const seqB = b.state.wsStockDeltaTs.get("MSFT").seq;
    await snapP;

    assert(seqB > seqA, "NF2 (T28-2): second WS stamp received a strictly greater seq");
    const pos = b.state.stockPositions.get("MSFT");
    assert(pos && pos.quantity === 99, "NF2 (T28-2): later same-ms WS update wins (qty=99, not 50 or 1)");
  } finally {
    Date.now = origDateNow;
  }
}

// ---------------------------------------------------------------------------
// v7.7 NN1: _metrics counters increment correctly across happy / RIP / partial.
// ---------------------------------------------------------------------------
async function testMetricsCounters() {
  const b = newBroker();
  b.stock = {
    positions: async () => { await delay(20); return { ok: true, positions: [] }; },
    presentBalance: async () => ({ ok: true, deposits: {} }),
    foreignMargin: async () => ({ ok: true, usdCash: 1_234, usdOrderable: 1_200 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };

  assert(b._metrics.snapshot_runs_total === 0, "NN1: snapshot_runs_total starts at 0");
  await b.snapshot();
  assert(b._metrics.snapshot_runs_total === 1, "NN1: snapshot_runs_total=1 after one run");
  assert(b._metrics.snapshot_errors_total === 0, "NN1: snapshot_errors_total=0 on happy path");

  // Now induce an error and verify the error counter ticks up.
  b.option.deposit = async () => ({ ok: false, thrown: "synthetic" });
  await b.snapshot();
  assert(b._metrics.snapshot_runs_total === 2, "NN1: snapshot_runs_total=2 after second run");
  assert(b._metrics.snapshot_errors_total === 1, "NN1: snapshot_errors_total=1 after partial snapshot");
}

// ---------------------------------------------------------------------------
// v7.8 NB2 — T29: a CCNL tick (authority="TICK") alone must NOT shield a
// symbol from a legitimate REST-side quantity update. Scenario: external
// system (e.g. another trading channel) sold down AAPL from 100 to 50; the
// broker's WS session sees only a CCNL tick (price refresh) during the
// snapshot window, no FILL. After commit, the broker state MUST reflect the
// REST-side 50, not the old 100 that TICK alone would have preserved under
// NF2-without-NB2.
// ---------------------------------------------------------------------------
async function testNB2_TickDoesNotShieldRestQuantityUpdate() {
  const b = newBroker();
  // REST returns the NEW (smaller) quantity…
  b.stock = {
    positions: async () => {
      await delay(40);
      return { ok: true, positions: [{ symbol: "AAPL", quantity: 50, avgPrice: 180 }] };
    },
    presentBalance: async () => ({ ok: true, deposits: {} }),
    foreignMargin: async () => ({ ok: true, usdCash: 1_000, usdOrderable: 900 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };
  // …but the broker WS state still has the OLD (larger) quantity from before
  // the external sell. This mirrors what happens when an external channel
  // executes out-of-band.
  b.state.stockPositions.set("AAPL", { symbol: "AAPL", quantity: 100, avgPrice: 180, nowPrice: 195 });

  const snapP = b.snapshot();
  await delay(10);
  // Mid-snapshot: only a CCNL tick arrives (price refresh). No FILL.
  b._stampStockDelta("AAPL", "TICK");
  await snapP;

  const pos = b.state.stockPositions.get("AAPL");
  assert(pos && pos.quantity === 50,
    "NB2 (T29): TICK-only stamp did NOT shield AAPL from REST-side quantity update (state=50, not 100)");
  assert(b._metrics.snapshot_ws_tick_ignored_total >= 1,
    "NB2 (T29): snapshot_ws_tick_ignored_total incremented for the TICK-only symbol");
}

// ---------------------------------------------------------------------------
// v7.8 NB2 — T29-2: a FILL followed by a CCNL for the SAME symbol must keep
// FILL authority (promotion rule: once FILL, always FILL until next snapshot
// window). The WS state (which has the FILL-mutated quantity) must win over
// the stale REST state.
// ---------------------------------------------------------------------------
async function testNB2_FillAuthorityNotDemotedByLaterTick() {
  const b = newBroker();
  // REST returns the OLD quantity (stale, because FILL hadn't settled yet).
  b.stock = {
    positions: async () => {
      await delay(40);
      return { ok: true, positions: [{ symbol: "NVDA", quantity: 10, avgPrice: 800 }] };
    },
    presentBalance: async () => ({ ok: true, deposits: {} }),
    foreignMargin: async () => ({ ok: true, usdCash: 1_000, usdOrderable: 900 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };

  const snapP = b.snapshot();
  await delay(10);
  // First a FILL lifts the WS quantity to 77…
  b.state.stockPositions.set("NVDA", { symbol: "NVDA", quantity: 77, avgPrice: 800 });
  b._stampStockDelta("NVDA", "FILL");
  // …then a CCNL tick arrives for the same symbol. This MUST NOT demote
  // authority back to TICK; the promotion rule holds FILL.
  b._stampStockDelta("NVDA", "TICK");
  await snapP;

  const pos = b.state.stockPositions.get("NVDA");
  assert(pos && pos.quantity === 77,
    "NB2 (T29-2): FILL authority held against subsequent TICK; WS qty=77 preserved, not REST 10");
  assert(b._metrics.snapshot_ws_preserved_total >= 1,
    "NB2 (T29-2): snapshot_ws_preserved_total incremented (FILL authority honoured)");
}

// ---------------------------------------------------------------------------
// v7.8 NN2 — T30: HAPPY PATH invariant. After one fully-green snapshot,
// snapshot_runs_total must increase by exactly 1, snapshot_success_total
// must increase by exactly 1, and snapshot_errors_total must NOT change.
// The invariant `runs === success + errors` must hold.
// ---------------------------------------------------------------------------
async function testNN2_HappyPathInvariant() {
  const b = newBroker();
  b.stock = {
    positions: async () => { await delay(20); return { ok: true, positions: [] }; },
    presentBalance: async () => ({ ok: true, deposits: {} }),
    foreignMargin: async () => ({ ok: true, usdCash: 9_876, usdOrderable: 9_500 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: true, cashDeposit: 0, orderableCash: 0, usedMargin: 0 }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };

  const r0 = b._metrics.snapshot_runs_total;
  const s0 = b._metrics.snapshot_success_total;
  const e0 = b._metrics.snapshot_errors_total;
  assert(r0 === 0 && s0 === 0 && e0 === 0,
    "NN2 (T30): all snapshot counters start at 0");
  assert(r0 === s0 + e0,
    "NN2 (T30): invariant runs = success + errors holds at start");

  await b.snapshot();

  const r1 = b._metrics.snapshot_runs_total;
  const s1 = b._metrics.snapshot_success_total;
  const e1 = b._metrics.snapshot_errors_total;
  assert(r1 - r0 === 1, "NN2 (T30): snapshot_runs_total increased by exactly 1 on happy path");
  assert(s1 - s0 === 1, "NN2 (T30): snapshot_success_total increased by exactly 1 on happy path");
  assert(e1 - e0 === 0, "NN2 (T30): snapshot_errors_total unchanged on happy path");
  assert(r1 === s1 + e1, "NN2 (T30): invariant runs = success + errors holds after happy path");
}

// ---------------------------------------------------------------------------
// v7.8 NN2 — T30-2: ERROR PATH invariant. After a snapshot with at least one
// failed subsystem, snapshot_runs_total must increase by exactly 1 (the run
// is counted at ENTRY of _doSnapshot), snapshot_errors_total must increase
// by exactly 1, snapshot_success_total must NOT change, and the invariant
// `runs === success + errors` must continue to hold.
//
// This also covers the harder hard-throw case: if PHASE 1 throws BEFORE the
// commit block, runs is still incremented at entry, and the catch handler
// books the error counter, so the invariant survives.
// ---------------------------------------------------------------------------
async function testNN2_ErrorPathInvariant() {
  const b = newBroker();
  // Stock side is fine, but option deposit fails — produces a "partial"
  // commit (errs.length > 0) which goes through the success path branch and
  // increments snapshot_errors_total.
  b.stock = {
    positions: async () => { await delay(20); return { ok: true, positions: [] }; },
    presentBalance: async () => ({ ok: true, deposits: {} }),
    foreignMargin: async () => ({ ok: true, usdCash: 1_000, usdOrderable: 900 }),
    openOrders: async () => ({ ok: true, orders: [] }),
  };
  b.option = {
    openPositions: async () => ({ ok: true, positions: [] }),
    deposit: async () => ({ ok: false, thrown: "synthetic-deposit-fail" }),
    todayOrders: async () => ({ ok: true, orders: [] }),
  };

  const r0 = b._metrics.snapshot_runs_total;
  const s0 = b._metrics.snapshot_success_total;
  const e0 = b._metrics.snapshot_errors_total;

  await b.snapshot();

  const r1 = b._metrics.snapshot_runs_total;
  const s1 = b._metrics.snapshot_success_total;
  const e1 = b._metrics.snapshot_errors_total;
  assert(r1 - r0 === 1, "NN2 (T30-2): snapshot_runs_total increased by exactly 1 on partial-error path");
  assert(e1 - e0 === 1, "NN2 (T30-2): snapshot_errors_total increased by exactly 1 on partial-error path");
  assert(s1 - s0 === 0, "NN2 (T30-2): snapshot_success_total unchanged on partial-error path");
  assert(r1 === s1 + e1, "NN2 (T30-2): invariant runs = success + errors holds after partial-error path");

  // Hard-throw variant: force the OUTER try/catch in _doSnapshot to fire by
  // injecting a Proxy on `b.state.deposits` whose `set` trap throws. The
  // commit path writes to `this.state.deposits.USD = shadow.depositsUSD;`
  // which triggers the trap inside the try block, jumps to catch, increments
  // snapshot_errors_total, and rethrows. snapshot()'s wrapper resolves the
  // promise as rejected. We mirror the proven pattern from
  // testFinallyClearsFlagOnThrow.
  const r2_pre = b._metrics.snapshot_runs_total;
  const s2_pre = b._metrics.snapshot_success_total;
  const e2_pre = b._metrics.snapshot_errors_total;
  const origDeposits = b.state.deposits;
  b.state.deposits = new Proxy(origDeposits, {
    set() { throw new Error("NN2-T30-2-induced-commit-failure"); },
  });
  let threw = false;
  try {
    await b.snapshot();
  } catch (_e) {
    threw = true;
  }
  // Restore so subsequent tests are not affected.
  b.state.deposits = origDeposits;
  assert(threw, "NN2 (T30-2): hard-throw path actually threw out of snapshot()");
  const r2 = b._metrics.snapshot_runs_total;
  const s2 = b._metrics.snapshot_success_total;
  const e2 = b._metrics.snapshot_errors_total;
  assert(r2 - r2_pre === 1, "NN2 (T30-2): runs incremented at entry even on hard-throw");
  assert(e2 - e2_pre === 1, "NN2 (T30-2): errors incremented from catch handler on hard-throw");
  assert(s2 - s2_pre === 0, "NN2 (T30-2): success unchanged on hard-throw");
  assert(r2 === s2 + e2, "NN2 (T30-2): invariant runs = success + errors holds after hard-throw");
}

(async () => {
  console.log("=== v7.6/7.7/7.8 atomic snapshot + WS-merge + invariant regression tests ===");
  await testOptionFailNoFailOpenWindow();
  await testHappyPath();
  await testReentryGuard();
  await testFinallyClearsFlagOnThrow();
  await testWsFillPreservedDuringSnapshot();
  await testWsFlatFillPreservedDuringSnapshot();
  await testSharedRefreshPromise();
  await testNF2_SameMsPreserve();      // v7.8 NF2 — T28
  await testNF2_DualSameMsOrder();     // v7.8 NF2 — T28-2
  await testNB2_TickDoesNotShieldRestQuantityUpdate();  // v7.8 NB2 — T29
  await testNB2_FillAuthorityNotDemotedByLaterTick();   // v7.8 NB2 — T29-2
  await testNN2_HappyPathInvariant();  // v7.8 NN2 — T30
  await testNN2_ErrorPathInvariant();  // v7.8 NN2 — T30-2
  await testMetricsCounters();
  console.log(`=== ${totalTests - failedTests}/${totalTests} tests passed ===`);
  process.exit(failedTests === 0 ? 0 : 1);
})().catch(e => {
  console.error("test harness crashed:", e);
  process.exit(2);
});
