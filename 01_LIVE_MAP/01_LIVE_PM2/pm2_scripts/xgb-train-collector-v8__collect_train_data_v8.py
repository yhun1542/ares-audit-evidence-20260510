#!/usr/bin/env python3
"""
ARES XGB v8 — Training Data Collector
=====================================
PURPOSE
-------
Continuously snapshot {feat:v2:*, xgb:risk:*, premium:options:*} every CYCLE_SEC
into a SQLite database for downstream v8 model training.

DUAL-LABEL DESIGN
-----------------
1) **Self-distillation label** — `label_v7_pdrop` (REAL): v7 model's calibrated
   p_drop is recorded as a soft target. This enables an immediate v8 train as
   soon as a few hundred snapshots are collected, preserving v7 behaviour while
   exposing the new 5 option features to the gradient boosted ensemble.

2) **Forward-looking realized label** — `label_fwd_dd_60m` (REAL, NULL until
   computed): max-drawdown realized over the next ~60 minutes after the
   snapshot. Computed lazily by `update_forward_labels()` when enough future
   `price:*` history exists. NOT required for the first training run — Track A
   ships immediately, Track B refines later.

OUTPUT TABLE
------------
snapshots(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,        -- unix epoch (s) of capture
    cycle         INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    feat_json     TEXT NOT NULL,           -- raw feat:v2 features (27 keys)
    opt_source    TEXT,                    -- e.g. "polygon" / "default_missing"
    pcr_vol       REAL, pcr_oi REAL,
    iv_skew_25d   REAL, atm_mid_iv REAL,
    zero_dte_share REAL,
    price         REAL,                    -- price:{sym} at capture
    label_v7_pdrop  REAL,                  -- v7 self-distillation target
    v7_flag         TEXT,
    v7_blocked      INTEGER,
    label_fwd_dd_60m REAL,                 -- nullable, filled later
    label_fwd_ret_60m REAL,
    schema_ver    TEXT
)

KEY GUARANTEES
--------------
- ALL writes are within a single transaction per cycle (atomic batch).
- WAL journal mode → safe parallel reads from training jobs.
- Symbol list pulled fresh each cycle from active_symbols.json (47+5).
- Skips a symbol cleanly if feat:v2 is missing or stale (>FEAT_MAX_AGE).
- Heartbeat key `xgb:v8:collector:status` updated every cycle.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import redis

# ────────── CONFIG ──────────
ARES_HOME      = os.getenv("ARES_HOME", "/home/ubuntu/ares_current")
SYM_PATH       = os.getenv("V8_SYMBOLS",
                           f"{ARES_HOME}/config/active_symbols.json")
DB_PATH        = os.getenv("V8_DB_PATH",
                           f"{ARES_HOME}/data/train_v8.db")
REDIS_HOST     = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT     = int(os.getenv("REDIS_PORT", "6379"))
REDIS_USERNAME = os.getenv("REDIS_USERNAME", "default")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "") or None
REDIS_SSL      = os.getenv("REDIS_SSL", "true").lower() == "true"

CYCLE_SEC      = int(os.getenv("V8_COLLECT_CYCLE", "300"))   # 5 min
FEAT_MAX_AGE_S = int(os.getenv("V8_FEAT_MAX_AGE", "1800"))   # 30 min
HEARTBEAT_TTL  = int(os.getenv("V8_COLLECT_HB_TTL", str(CYCLE_SEC * 3)))
FWD_LABEL_LAG  = int(os.getenv("V8_FWD_LABEL_LAG_S", "3900"))  # ~65min
FWD_WINDOW_S   = int(os.getenv("V8_FWD_WINDOW_S", "3600"))     # 60min lookahead

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("v8_collect")

_RUN = True


def _sigterm(*_a):
    global _RUN
    _RUN = False
    log.warning("SIGTERM received — graceful stop.")


signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT, _sigterm)


# ────────── DB ──────────
DDL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    cycle         INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    feat_json     TEXT NOT NULL,
    opt_source    TEXT,
    pcr_vol       REAL,
    pcr_oi        REAL,
    iv_skew_25d   REAL,
    atm_mid_iv    REAL,
    zero_dte_share REAL,
    price         REAL,
    label_v7_pdrop REAL,
    v7_flag        TEXT,
    v7_blocked     INTEGER,
    label_fwd_dd_60m  REAL,
    label_fwd_ret_60m REAL,
    schema_ver    TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_ts     ON snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_snap_sym_ts ON snapshots(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_snap_cycle  ON snapshots(cycle);
CREATE INDEX IF NOT EXISTS idx_snap_fwd_null ON snapshots(label_fwd_dd_60m) WHERE label_fwd_dd_60m IS NULL;

CREATE TABLE IF NOT EXISTS price_log (
    ts     INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    price  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pl_sym_ts ON price_log(symbol, ts);
"""


def open_db(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.executescript(DDL)
    return conn


def open_redis() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT,
        username=REDIS_USERNAME, password=REDIS_PASSWORD,
        ssl=REDIS_SSL, decode_responses=True,
        socket_timeout=5, socket_connect_timeout=5,
    )


def load_symbols() -> list[str]:
    p = Path(SYM_PATH)
    if not p.exists():
        log.error("symbols file missing: %s", SYM_PATH)
        return []
    obj = json.loads(p.read_text())
    syms = obj.get("symbols") or []
    etfs = obj.get("etfs") or obj.get("etf_universe") or []
    return list(dict.fromkeys(list(syms) + list(etfs)))  # dedup, preserve order


# ────────── COLLECT ──────────
def _safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:           # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def collect_one(r: redis.Redis, sym: str, now_ts: int) -> dict | None:
    raw = r.get(f"feat:v2:{sym}")
    if not raw:
        return None
    try:
        feat_obj = json.loads(raw)
    except Exception as e:
        log.debug("feat:v2:%s parse failed: %s", sym, e)
        return None

    # freshness check
    computed = feat_obj.get("computed_at")
    if computed:
        # very loose age check — accept if within FEAT_MAX_AGE_S of now
        try:
            from datetime import datetime
            t = datetime.fromisoformat(computed.replace("Z", "+00:00"))
            age = now_ts - int(t.timestamp())
            if age > FEAT_MAX_AGE_S:
                return None
        except Exception:
            pass

    feats = feat_obj.get("features") or {}
    if not feats:
        return None

    # v7 label
    risk_raw = r.get(f"xgb:risk:{sym}")
    p_drop, flag, blocked = None, None, 0
    if risk_raw:
        try:
            ro = json.loads(risk_raw)
            p_drop = _safe_float(ro.get("p_drop"))
            flag = ro.get("flag")
            blocked = 1 if int(ro.get("blocked") or 0) else 0
        except Exception:
            pass

    price = _safe_float(r.get(f"price:{sym}"))

    return {
        "ts":           now_ts,
        "symbol":       sym,
        "feat_json":    json.dumps(feats, separators=(",", ":")),
        "opt_source":   feat_obj.get("opt_source"),
        "pcr_vol":      _safe_float(feats.get("opt_pcr_volume")),
        "pcr_oi":       _safe_float(feats.get("opt_pcr_oi")),
        "iv_skew_25d":  _safe_float(feats.get("opt_iv_skew_25d")),
        "atm_mid_iv":   _safe_float(feats.get("opt_atm_mid_iv")),
        "zero_dte_share": _safe_float(feats.get("opt_zero_dte_share")),
        "price":        price,
        "label_v7_pdrop": p_drop,
        "v7_flag":      flag,
        "v7_blocked":   blocked,
        "schema_ver":   feat_obj.get("schema_version", "v2"),
    }


def write_batch(conn: sqlite3.Connection, rows: list[dict], cycle: int) -> int:
    if not rows:
        return 0
    sql = """
    INSERT INTO snapshots
    (ts, cycle, symbol, feat_json, opt_source,
     pcr_vol, pcr_oi, iv_skew_25d, atm_mid_iv, zero_dte_share,
     price, label_v7_pdrop, v7_flag, v7_blocked, schema_ver)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    payload = [
        (
            r["ts"], cycle, r["symbol"], r["feat_json"], r["opt_source"],
            r["pcr_vol"], r["pcr_oi"], r["iv_skew_25d"], r["atm_mid_iv"], r["zero_dte_share"],
            r["price"], r["label_v7_pdrop"], r["v7_flag"], r["v7_blocked"], r["schema_ver"],
        )
        for r in rows
    ]
    plog = [(r["ts"], r["symbol"], r["price"]) for r in rows if r["price"] is not None]
    cur = conn.cursor()
    cur.execute("BEGIN;")
    try:
        cur.executemany(sql, payload)
        if plog:
            cur.executemany("INSERT INTO price_log(ts, symbol, price) VALUES (?,?,?)", plog)
        cur.execute("COMMIT;")
    except Exception:
        cur.execute("ROLLBACK;")
        raise
    return len(rows)


# ────────── FORWARD LABEL ──────────
def update_forward_labels(conn: sqlite3.Connection, now_ts: int) -> tuple[int, int]:
    """For snapshots older than FWD_LABEL_LAG, compute realized fwd metrics
    using `price_log` rows in (ts, ts+FWD_WINDOW_S].
    """
    cutoff = now_ts - FWD_LABEL_LAG
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT id, ts, symbol, price FROM snapshots
        WHERE label_fwd_dd_60m IS NULL AND ts <= ? AND price IS NOT NULL
        ORDER BY ts ASC LIMIT 5000
    """, (cutoff,)).fetchall()
    n_total, n_filled = len(rows), 0
    for sid, ts0, sym, p0 in rows:
        future = cur.execute("""
            SELECT price FROM price_log
            WHERE symbol=? AND ts>? AND ts<=?
            ORDER BY ts ASC
        """, (sym, ts0, ts0 + FWD_WINDOW_S)).fetchall()
        if not future or p0 is None or p0 == 0:
            continue
        prices = [p[0] for p in future if p[0] is not None and p[0] > 0]
        if not prices:
            continue
        ret_min = (min(prices) - p0) / p0   # most negative drift
        ret_end = (prices[-1] - p0) / p0
        cur.execute(
            "UPDATE snapshots SET label_fwd_dd_60m=?, label_fwd_ret_60m=? WHERE id=?",
            (ret_min, ret_end, sid),
        )
        n_filled += 1
    if n_filled:
        conn.commit()
    return n_total, n_filled


# ────────── MAIN ──────────
def main() -> int:
    log.info("v8 collector starting — cycle=%ss db=%s", CYCLE_SEC, DB_PATH)
    conn = open_db(DB_PATH)
    r = open_redis()
    cycle = 0
    while _RUN:
        t0 = time.time()
        cycle += 1
        try:
            symbols = load_symbols()
            now_ts = int(time.time())
            rows = []
            for s in symbols:
                row = collect_one(r, s, now_ts)
                if row is not None:
                    rows.append(row)
            n = write_batch(conn, rows, cycle)
            n_total, n_filled = update_forward_labels(conn, now_ts)
            elapsed = time.time() - t0
            stat = {
                "cycle": cycle,
                "ts": now_ts,
                "n_universe": len(symbols),
                "n_captured": n,
                "fwd_pending": n_total,
                "fwd_filled": n_filled,
                "elapsed_sec": round(elapsed, 3),
                "db": DB_PATH,
                "status": "OK" if n > 0 else "EMPTY",
            }
            try:
                r.set("xgb:v8:collector:status",
                      json.dumps(stat), ex=HEARTBEAT_TTL)
                r.set("xgb:v8:collector:heartbeat", str(now_ts),
                      ex=HEARTBEAT_TTL)
            except Exception as e:
                log.warning("heartbeat publish failed: %s", e)
            log.info("cycle=%d captured=%d/%d fwd_filled=%d/%d elapsed=%.2fs",
                     cycle, n, len(symbols), n_filled, n_total, elapsed)
        except Exception as e:
            log.exception("cycle %d failed: %s", cycle, e)

        # sleep with early-exit responsiveness
        sleep_left = CYCLE_SEC - (time.time() - t0)
        while _RUN and sleep_left > 0:
            time.sleep(min(2.0, sleep_left))
            sleep_left -= 2.0
    conn.close()
    log.info("v8 collector exiting cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
