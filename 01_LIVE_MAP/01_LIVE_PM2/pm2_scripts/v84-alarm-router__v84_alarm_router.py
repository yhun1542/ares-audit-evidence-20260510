#!/usr/bin/env python3
"""
v84_alarm_router.py — Fan-out router for v84 sentinel alarms.

Subscribes to Redis pub/sub channels published by `v84_rcr_alert.py`
and forwards each alert to multiple downstream destinations:
  - Telegram (always-on if TELEGRAM_BOT_TOKEN + TELEGRAM_ALERT_CHAT_ID)
  - Slack    (Slack incoming webhook if SLACK_WEBHOOK_URL)
  - PagerDuty(Events API v2 if PAGERDUTY_ROUTING_KEY)

Channels (subscribe order matters for log clarity):
  - alarm:v84_rcr           ← LIVE (must be enabled with --live or env CHANNEL_MODE=live)
  - alarm:dryrun:v84_rcr    ← default (safe), prints to log + audit but does NOT
                              hit Telegram/PD/Slack unless --route-dryrun is set.

Design choices:
  * Idempotent dedup at router-level using payload hash + 60s TTL Redis SET.
  * Per-destination circuit breaker: 5 consecutive failures → cooldown 300s.
  * Severity mapping:
      WARN  → Telegram (info icon), Slack (#warning), PagerDuty WARNING
      CRIT  → Telegram (red icon), Slack (#danger),  PagerDuty CRITICAL
      INFO  → Telegram (gray),     Slack (#info),    PagerDuty (skip)
  * Fail-soft: any one downstream failure does NOT abort fan-out to others.
  * Audit JSONL at /home/ubuntu/v84_alarm_router.jsonl (append-only,
    flushed line-buffered per Claude's earlier guidance).
  * Graceful shutdown on SIGTERM/SIGINT.

CLI:
  python3 v84_alarm_router.py [--live] [--route-dryrun] [--once]
    --live          subscribe to alarm:v84_rcr (default: alarm:dryrun:v84_rcr)
    --route-dryrun  even on dryrun channel, actually send to destinations
                    (use during e2e drill of downstream connectivity)
    --once          process the next message then exit (for tests)

Env (required for routing):
  REDIS_URL                       (mandatory)
  TELEGRAM_BOT_TOKEN              (optional, enables Telegram)
  TELEGRAM_ALERT_CHAT_ID          (optional, enables Telegram)
  SLACK_WEBHOOK_URL               (optional, enables Slack)
  PAGERDUTY_ROUTING_KEY           (optional, enables PagerDuty Events v2)
  PAGERDUTY_SOURCE                (optional, default ares-prod-bridge)
  ALARM_ROUTER_AUDIT_PATH         (optional, default /home/ubuntu/v84_alarm_router.jsonl)
  ALARM_ROUTER_DEDUP_TTL          (optional, default 60)
"""
from __future__ import annotations
import argparse
import hashlib
import json
import logging
import os
import signal
import socket
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis  # type: ignore

# ----------------------------- constants -----------------------------------
CH_LIVE   = "alarm:v84_rcr"
CH_DRYRUN = "alarm:dryrun:v84_rcr"

DEFAULT_AUDIT = "/home/ubuntu/v84_alarm_router.jsonl"
# dedup TTL: dryrun=60s (test friendly), live=120s (longer to avoid duplicate
# pages on the same incident across consecutive sentinel cycles).
DEFAULT_DEDUP_TTL_DRYRUN = int(os.environ.get("ALARM_ROUTER_DEDUP_TTL_DRYRUN", "60"))
DEFAULT_DEDUP_TTL_LIVE   = int(os.environ.get("ALARM_ROUTER_DEDUP_TTL_LIVE", "120"))
CB_THRESHOLD = 5            # consecutive fails before opening
CB_COOLDOWN  = 300          # seconds in open state

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("v84_alarm_router")

# ----------------------------- audit ---------------------------------------
class Audit:
    def __init__(self, path: str):
        self.path = path
        # line-buffered text mode -> each write flushes (per Claude race-fix)
        self._fh = open(path, "a", buffering=1, encoding="utf-8")
    def write(self, rec: Dict[str, Any]) -> None:
        rec.setdefault("ts", datetime.now(timezone.utc).isoformat()
                                .replace("+00:00", "Z"))
        try:
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            log.error("audit write failed: %s", e)
    def close(self) -> None:
        try: self._fh.close()
        except Exception: pass

# ----------------------------- circuit breaker -----------------------------
class Breaker:
    def __init__(self, name: str):
        self.name = name
        self.fails = 0
        self.opened_at: Optional[float] = None
    def allow(self) -> bool:
        if self.opened_at is None: return True
        if time.time() - self.opened_at >= CB_COOLDOWN:
            log.warning("breaker[%s]: cooldown elapsed, half-opening", self.name)
            self.opened_at = None
            self.fails = 0
            return True
        return False
    def record(self, ok: bool) -> None:
        if ok:
            self.fails = 0
            self.opened_at = None
        else:
            self.fails += 1
            if self.fails >= CB_THRESHOLD and self.opened_at is None:
                self.opened_at = time.time()
                log.error("breaker[%s]: OPENED after %d consecutive fails",
                          self.name, self.fails)

# ----------------------------- destinations --------------------------------
def _http_post(url: str, payload: Dict[str, Any], timeout: float = 6.0,
               headers: Optional[Dict[str, str]] = None) -> int:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items(): req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        log.warning("http_post(%s) failed: %s", url[:60], e)
        return 0

def _telegram_message(alert: Dict[str, Any]) -> str:
    """Build a Telegram message body in PLAIN TEXT (no parse_mode).

    We deliberately avoid Markdown/MarkdownV2/HTML to bypass entity-parser
    failures on values containing reserved chars (e.g. '.', '_', '`').
    Readability is preserved with emoji, severity tag and newline layout.
    """
    sev   = alert.get("severity", "WARN")
    emoji = {"INFO":"ℹ️", "WARN":"⚠️", "CRIT":"🔴"}.get(sev, "⚠️")
    aid   = alert.get("alert_id", "ALERT")
    src   = alert.get("source") or alert.get("host") or ""
    pl    = alert.get("payload", {})
    # Render payload in deterministic key order, max 8 items.
    keys = list(pl.keys())[:8]
    pl_compact = ", ".join(f"{k}={pl[k]}" for k in keys)
    head = f"{emoji} [{sev}] {aid}"
    if src: head += f"  src={src}"
    return head + ("\n" + pl_compact if pl_compact else "")

def send_telegram(token: str, chat_id: str, alert: Dict[str, Any]) -> bool:
    """Plain-text Telegram send (no parse_mode) for maximum robustness.

    Payload values from the wild (floats with '.', alert ids with '_')
    repeatedly broke MarkdownV1 entity parsing. We dropped parse_mode
    altogether; emoji + tags carry the visual hierarchy.
    """
    sev = alert.get("severity", "WARN")
    text = _telegram_message(alert)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    code = _http_post(url, {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": (sev == "INFO"),
    })
    return 200 <= code < 300

def send_slack(webhook: str, alert: Dict[str, Any]) -> bool:
    sev = alert.get("severity", "WARN")
    color = {"INFO":"#9aa6b2","WARN":"#f0ad4e","CRIT":"#d9534f"}.get(sev, "#f0ad4e")
    aid = alert.get("alert_id", "ALERT")
    pl = alert.get("payload", {})
    fields = [{"title": k, "value": str(v), "short": True}
              for k, v in list(pl.items())[:10]]
    code = _http_post(webhook, {
        "attachments": [{
            "fallback": f"{aid} [{sev}]",
            "color": color,
            "title": f"{aid} [{sev}]",
            "text": alert.get("source", ""),
            "fields": fields,
            "ts": int(time.time()),
        }],
    })
    return 200 <= code < 300

def send_pagerduty(routing_key: str, source: str, alert: Dict[str, Any]) -> bool:
    sev = alert.get("severity", "WARN")
    if sev == "INFO":
        return True  # don't page on INFO
    pd_sev = {"WARN":"warning","CRIT":"critical"}.get(sev, "warning")
    aid = alert.get("alert_id", "ALERT")
    dedup = alert.get("dedup_key") or aid
    payload = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": dedup,
        "payload": {
            "summary": f"{aid} [{sev}]: " + ", ".join(
                f"{k}={v}" for k,v in list(alert.get("payload",{}).items())[:6]),
            "severity": pd_sev,
            "source": source,
            "component": alert.get("component", "v84-rcr-alert"),
            "group": "ARES",
            "class": aid,
            "custom_details": alert.get("payload", {}),
        },
    }
    code = _http_post("https://events.pagerduty.com/v2/enqueue", payload)
    return 200 <= code < 300

# ----------------------------- core ----------------------------------------
def make_dedup_key(alert: Dict[str, Any]) -> str:
    raw = json.dumps({
        "id": alert.get("alert_id"),
        "p": alert.get("payload", {}),
        "s": alert.get("severity"),
    }, sort_keys=True)
    return "alarm:dedup:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="subscribe to live channel " + CH_LIVE)
    ap.add_argument("--route-dryrun", action="store_true",
                    help="actually send to downstreams even on dryrun channel")
    ap.add_argument("--once", action="store_true",
                    help="process one message then exit")
    args = ap.parse_args(argv)

    audit = Audit(os.environ.get("ALARM_ROUTER_AUDIT_PATH", DEFAULT_AUDIT))
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        log.error("REDIS_URL not set"); return 2
    # health/control client: short timeout for SET/PING/etc.
    r = redis.from_url(redis_url, socket_timeout=5,
                       socket_connect_timeout=5, decode_responses=True,
                       socket_keepalive=True,
                       health_check_interval=30)
    # pubsub client: NO socket_timeout (idle long-poll required).
    r_sub = redis.from_url(redis_url,
                       socket_timeout=None,
                       socket_connect_timeout=5,
                       decode_responses=True,
                       socket_keepalive=True,
                       health_check_interval=30)
    try:
        r.ping(); r_sub.ping()
    except Exception as e:
        audit.write({"kind":"start","ok":False,"error":f"redis_ping_failed:{e}"})
        log.error("redis ping failed: %s", e); return 3

    channel = CH_LIVE if args.live else CH_DRYRUN
    route_to_remote = args.live or args.route_dryrun

    # destination availability
    # B-3 design: severity-based Telegram chat routing.
    #   * TELEGRAM_ALERT_CHAT_ID  → CRIT-only target (pagerduty replacement)
    #   * TELEGRAM_CHAT_ID        → INFO/WARN target
    # If only one is set, fall back to it for all severities.
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_alert = os.environ.get("TELEGRAM_ALERT_CHAT_ID")  # CRIT preferred
    tg_chat_main  = os.environ.get("TELEGRAM_CHAT_ID")        # INFO/WARN preferred
    tg_chat_any   = tg_chat_alert or tg_chat_main             # any-fallback
    sk_url   = os.environ.get("SLACK_WEBHOOK_URL")
    pd_key   = os.environ.get("PAGERDUTY_ROUTING_KEY")
    pd_src   = os.environ.get("PAGERDUTY_SOURCE", "ares-prod-bridge")

    enabled = {
        "telegram":  bool(tg_token and tg_chat_any),
        "slack":     bool(sk_url),
        "pagerduty": bool(pd_key),
    }
    breakers = {k: Breaker(k) for k in enabled}

    def _resolve_tg_chat(severity: str) -> Optional[str]:
        if severity == "CRIT":
            return tg_chat_alert or tg_chat_any
        # WARN / INFO
        return tg_chat_main or tg_chat_any

    dedup_ttl = DEFAULT_DEDUP_TTL_LIVE if args.live else DEFAULT_DEDUP_TTL_DRYRUN
    audit.write({
        "kind":"start","channel":channel,"route_to_remote":route_to_remote,
        "enabled":enabled,"dedup_ttl":dedup_ttl,
        "tg_routing":{
            "crit_chat_set":  bool(tg_chat_alert),
            "main_chat_set":  bool(tg_chat_main),
            "shared_chat":    bool(tg_chat_alert and tg_chat_main and tg_chat_alert==tg_chat_main),
        },
        "hostname":socket.gethostname(),
    })
    log.info("router start channel=%s remote=%s enabled=%s ttl=%ds",
             channel, route_to_remote, enabled, dedup_ttl)

    pubsub = r_sub.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(channel)

    # graceful shutdown
    stop = {"v": False}
    def _shutdown(signum, frame):
        stop["v"] = True
        log.info("signal %s received, shutting down", signum)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    n_seen = n_routed = n_dup = 0
    try:
        for msg in pubsub.listen():
            if stop["v"]: break
            if msg.get("type") != "message": continue
            n_seen += 1
            data = msg.get("data") or "{}"
            try:
                alert = json.loads(data)
            except Exception:
                audit.write({"kind":"parse_fail","raw":str(data)[:300]})
                if args.once: break
                continue

            ddk = make_dedup_key(alert)
            try:
                ok_dedup = r.set(ddk, "1", nx=True,
                                 ex=dedup_ttl)
            except Exception as e:
                ok_dedup = True  # fail-soft: route anyway
                log.warning("dedup set failed: %s", e)
            if not ok_dedup:
                n_dup += 1
                audit.write({"kind":"dedup_skip","alert_id":alert.get("alert_id"),
                             "dedup_key":ddk})
                if args.once: break
                continue

            sev_in = alert.get("severity", "WARN")
            results: Dict[str, Any] = {}
            tg_chat_used: Optional[str] = None
            if route_to_remote:
                if enabled["telegram"] and breakers["telegram"].allow():
                    tg_chat_used = _resolve_tg_chat(sev_in)
                    if tg_chat_used:
                        ok = send_telegram(tg_token, tg_chat_used, alert)  # type: ignore[arg-type]
                        breakers["telegram"].record(ok); results["telegram"]=ok
                if enabled["slack"] and breakers["slack"].allow():
                    ok = send_slack(sk_url, alert)  # type: ignore[arg-type]
                    breakers["slack"].record(ok); results["slack"]=ok
                if enabled["pagerduty"] and breakers["pagerduty"].allow():
                    ok = send_pagerduty(pd_key, pd_src, alert)  # type: ignore[arg-type]
                    breakers["pagerduty"].record(ok); results["pagerduty"]=ok
            n_routed += 1

            audit.write({
                "kind":"routed" if route_to_remote else "dryrun_observe",
                "alert_id":alert.get("alert_id"),
                "severity":sev_in,
                "channel":channel,
                "tg_chat_used":("crit" if tg_chat_used and tg_chat_used==tg_chat_alert
                                else ("main" if tg_chat_used and tg_chat_used==tg_chat_main
                                      else ("fallback" if tg_chat_used else None))),
                "results":results,
                "dedup_key":ddk,
            })
            if args.once: break
    finally:
        audit.write({"kind":"stop","seen":n_seen,"routed":n_routed,"dup":n_dup})
        audit.close()
        try: pubsub.close()
        except Exception: pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
