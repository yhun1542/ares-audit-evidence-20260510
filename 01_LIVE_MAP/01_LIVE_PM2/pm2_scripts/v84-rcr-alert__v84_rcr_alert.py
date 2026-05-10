#!/usr/bin/env python3
"""
v84_rcr_alert.py — ALPHA_HEALTH_v84MUSK distribution-collapse watcher
=====================================================================
Tier-3 monitor-only sentinel for the v8.4 ALPHA_HEALTH overlay.

Triggers (consensus of senior reviewers GPT-5.4-pro + Claude-Opus-4-7):
  A1. risk_count_ratio_ema > 0.30 for >= 5 consecutive cycles
       -> ALERT_RCR_EMA_HIGH (severity: WARN, escalate after 10 cycles)
  A2. n_blocked / universe > 0.60 for any single cycle
       -> ALERT_BLOCKED_RATIO_HIGH (severity: WARN, datafeed gate review)
  A3. (defense-in-depth) ALPHA_HEALTH log line missing for > 60s
       -> ALERT_ALPHA_HEALTH_STALE (severity: CRITICAL)

Operational guarantees:
  - PUBLISH-ONLY to channel `alarm:dryrun:v84_rcr` (never touches kill-switch keys).
  - Append-only audit log at /home/ubuntu/v84_rcr_alert.jsonl.
  - Reads pm2 log file directly for ALPHA_HEALTH lines (no pm2 subprocess fork).
  - Idempotent: re-firing is debounced for 60s per alert id.
  - Fail-closed: any internal exception -> log only, never raise.

Usage:
  $ pm2 start v84_rcr_alert.py --name v84-rcr-alert --interpreter python3 -- --dryrun
  Add `--live` to publish to live channel after operator approval.
"""
from __future__ import annotations
import os
import re
import sys
import json
import time
import argparse
import collections
from datetime import datetime, timezone
from pathlib import Path

import redis  # provided by ARES python env

# -- configuration --------------------------------------------------------
PM2_LOG = "/home/ubuntu/.pm2/logs/final-to-champion-bridge-out.log"
AUDIT_LOG = "/home/ubuntu/v84_rcr_alert.jsonl"
CHANNEL_DRYRUN = "alarm:dryrun:v84_rcr"
CHANNEL_LIVE   = "alarm:v84_rcr"
TIE_BREAK_DEDUP_SEC = 60
# [v3.1] ARES SSOT redis: TLS ElastiCache, ACL-authenticated. Picked up from
# REDIS_URL env (populated by /etc/ares/redis.env via systemd EnvironmentFile
# or pm2 startup wrapper). Local 127.0.0.1 fallback only kept for sandbox.
REDIS_URL_ENV = os.environ.get("REDIS_URL") or os.environ.get("ARES_REDIS_URL")
RCR_EMA_THR = 0.30
RCR_EMA_CONSEC = 5
RCR_EMA_ESCALATE = 10
N_BLOCKED_RATIO_THR = 0.60
STALE_SEC_THR = 60
POLL_SEC = 5

ALPHA_HEALTH_RE = re.compile(
    r"\[ALPHA_HEALTH_v84MUSK\] universe=(?P<u>\d+) factor_n=\d+ xgb_n=\d+ "
    r"valid_n=\d+ n_blocked=(?P<nb>\d+) n_tradable=(?P<nt>\d+) "
    r"pd_q80=(?P<pdq>[\d.]+) risk_cnt=(?P<rc>\d+) "
    r"risk_count_ratio=(?P<rcr>[\d.]+) rcr_ema=(?P<rema>[\d.]+) "
    r"hyst=(?P<hyst>\w+) alpha_mult=(?P<am>[\d.]+) scale_mult=(?P<sm>[\d.]+)"
)


def utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def audit(rec: dict) -> None:
    try:
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


class Sentinel:
    def __init__(self, channel: str):
        # [v3.1] Connect to ARES SSOT Redis (ElastiCache, rediss://, ACL).
        # Falls back to 127.0.0.1 only when REDIS_URL is unset (sandbox/test).
        if REDIS_URL_ENV:
            self.r = redis.from_url(REDIS_URL_ENV, decode_responses=True,
                                    socket_timeout=5, socket_connect_timeout=5,
                                    health_check_interval=30)
            self.redis_target = REDIS_URL_ENV.split("@")[-1]  # host:port[/db]
        else:
            self.r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True,
                                 socket_timeout=5, socket_connect_timeout=5)
            self.redis_target = "127.0.0.1:6379"
        # Verify connectivity at startup; bail audit-only if Redis is unreachable
        try:
            self.r.ping()
            self.redis_ok = True
        except Exception as exc:
            self.redis_ok = False
            audit({"ts": utcnow_iso(), "kind": "redis_connect_fail",
                   "target": self.redis_target, "err": str(exc)})
        self.channel = channel
        self.last_fired_ts: dict[str, float] = {}
        self.consec_high = 0
        self.last_log_ts = time.time()
        # tail position: start near tail, never re-process the whole file
        self.fpos = 0
        self.buf = ""  # [v3.1] retain trailing partial line across reads
        self.fino = None
        try:
            st = os.stat(PM2_LOG)
            self.fino = st.st_ino
            self.fpos = max(0, st.st_size - 8192)
        except FileNotFoundError:
            pass

    def _publish(self, alert_id: str, severity: str, payload: dict) -> bool:
        now = time.time()
        last = self.last_fired_ts.get(alert_id, 0.0)
        if now - last < TIE_BREAK_DEDUP_SEC:
            return False
        body = {
            "alert_id": alert_id,
            "severity": severity,
            "ts": utcnow_iso(),
            "host": "i-0b641c9af9456cfa8",
            "monitor": "v84_rcr_alert.py",
            "payload": payload,
        }
        try:
            self.r.publish(self.channel, json.dumps(body))
        except Exception as exc:
            audit({"ts": utcnow_iso(), "kind": "publish_fail",
                   "alert_id": alert_id, "err": str(exc)})
            return False
        self.last_fired_ts[alert_id] = now
        audit({"ts": utcnow_iso(), "kind": "fired", **body})
        return True

    def _read_new_lines(self):
        try:
            st = os.stat(PM2_LOG)
        except FileNotFoundError:
            return []
        if self.fino is None or self.fino != st.st_ino or self.fpos > st.st_size:
            # log rotated or truncated -> rewind to tail and drop partial buffer
            self.fino = st.st_ino
            self.fpos = 0
            self.buf = ""
        new = []
        try:
            with open(PM2_LOG, "rb") as f:
                f.seek(self.fpos)
                chunk = f.read()
                self.fpos = f.tell()
            if chunk:
                # [v3.1] tail-reader race fix: a chunk boundary may split the
                # final ALPHA_HEALTH line. Concatenate with the previous remainder
                # and keep the trailing partial line for next iteration so we
                # never feed an incomplete record to the regex (which would
                # otherwise silently drop it and could trigger a false STALE
                # alarm). The trailing piece is kept exactly as-is and only
                # released once a newline arrives.
                text = self.buf + chunk.decode("utf-8", errors="replace")
                lines = text.split("\n")
                self.buf = lines.pop()  # tail (may be empty if chunk ended in \n)
                new = [ln for ln in lines if ln]
        except Exception as exc:
            audit({"ts": utcnow_iso(), "kind": "read_fail", "err": str(exc)})
        return new

    def step(self) -> None:
        for ln in self._read_new_lines():
            m = ALPHA_HEALTH_RE.search(ln)
            if not m:
                continue
            self.last_log_ts = time.time()
            u = int(m["u"]); nb = int(m["nb"]); rema = float(m["rema"])
            hyst = m["hyst"]
            # A1: rcr_ema persistent high
            if rema > RCR_EMA_THR:
                self.consec_high += 1
            else:
                self.consec_high = 0
            if self.consec_high == RCR_EMA_CONSEC:
                self._publish("ALERT_RCR_EMA_HIGH", "WARN",
                              {"rcr_ema": rema, "consec": self.consec_high,
                               "hyst": hyst, "thr": RCR_EMA_THR})
            elif self.consec_high == RCR_EMA_ESCALATE:
                self._publish("ALERT_RCR_EMA_HIGH_ESCALATED", "CRITICAL",
                              {"rcr_ema": rema, "consec": self.consec_high,
                               "hyst": hyst, "thr": RCR_EMA_THR})
            # A2: blocked share too high
            if u > 0 and (nb / u) > N_BLOCKED_RATIO_THR:
                self._publish("ALERT_BLOCKED_RATIO_HIGH", "WARN",
                              {"n_blocked": nb, "universe": u,
                               "ratio": round(nb / u, 4),
                               "thr": N_BLOCKED_RATIO_THR})
        # A3: log staleness
        if time.time() - self.last_log_ts > STALE_SEC_THR:
            self._publish("ALERT_ALPHA_HEALTH_STALE", "CRITICAL",
                          {"silent_for_sec":
                              round(time.time() - self.last_log_ts, 1)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="publish to live channel (default: dryrun)")
    args = ap.parse_args()
    chan = CHANNEL_LIVE if args.live else CHANNEL_DRYRUN
    Path(AUDIT_LOG).touch(exist_ok=True)
    sent = Sentinel(chan)
    audit({"ts": utcnow_iso(), "kind": "start", "channel": chan,
           "redis_target": sent.redis_target,
           "redis_ok": sent.redis_ok,
           "thresholds": {"rcr_ema": RCR_EMA_THR, "consec": RCR_EMA_CONSEC,
                          "blocked_ratio": N_BLOCKED_RATIO_THR,
                          "stale_sec": STALE_SEC_THR}})
    while True:
        try:
            sent.step()
        except Exception as exc:
            audit({"ts": utcnow_iso(), "kind": "step_fail", "err": str(exc)})
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
