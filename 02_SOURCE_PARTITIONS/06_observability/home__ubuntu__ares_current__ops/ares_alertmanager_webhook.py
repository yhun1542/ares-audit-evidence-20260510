#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ARES Alertmanager → Telegram Webhook v1.0.0

Standalone HTTP server that receives Alertmanager webhook POSTs and
forwards formatted alerts to a Telegram chat.

Features:
  - Severity-based emoji (🔴 critical / 🟡 warning / 🟢 resolved)
  - Cooldown per alert name (default 300s) to prevent spam
  - Enriched context: rollout phase, GA paused, OIE status from Redis
  - Graceful degradation: Redis read failures don't block alerts
  - Health endpoint: GET /health

Usage:
  TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy python3 ares_alertmanager_webhook.py

Systemd unit: ares-alertmanager-webhook.service (port 9095)
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, Optional
import ares_redis_env  # [SSOT v2.0] Force REDIS_URL from /etc/ares/redis.env

# ── Configuration ────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LISTEN_PORT = int(os.environ.get("ALERT_WEBHOOK_PORT", "9095"))
COOLDOWN_SEC = int(os.environ.get("ALERT_COOLDOWN_SEC", "300"))
REDIS_URL = os.environ.get("REDIS_URL")  # [FP-FIX] localhost fallback removed

# In-memory cooldown tracker: {alert_name: last_sent_epoch}
_cooldown: Dict[str, float] = {}


def _redis_client():
    """Lazy Redis client (best-effort)."""
    try:
        import redis as _redis
        return _redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)
    except Exception:
        return None


def _get_ops_context() -> str:
    """Read operational context from Redis for enriched alerts."""
    try:
        r = _redis_client()
        if not r:
            return ""
        parts = []
        # Rollout phase
        phase = r.hget("ssot:engine:rollout", "phase") or "HOLD"
        parts.append(f"rollout={phase}")
        # GA paused
        ga = r.get("ga:v1:paused")
        if ga and str(ga).lower() in ("1", "true"):
            parts.append("GA=PAUSED")
        else:
            parts.append("GA=active")
        # OIE consumer group lag (best-effort)
        try:
            info = r.xinfo_groups("emarkos:v6:order:intent")
            for g in info:
                if g.get("name") == "oie_group":
                    lag = g.get("lag", g.get("pending", "?"))
                    parts.append(f"OIE_lag={lag}")
                    break
        except Exception:
            pass
        return " | ".join(parts)
    except Exception:
        return ""


def _send_telegram(text: str) -> bool:
    """Send message via Telegram Bot API."""
    if not BOT_TOKEN or not CHAT_ID:
        print(f"WARN telegram_skip: no BOT_TOKEN or CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"WARN telegram_send_failed: {e}")
        return False


def _format_alert(alert: Dict[str, Any], ops_ctx: str) -> str:
    """Format a single Alertmanager alert into Telegram HTML."""
    status = str(alert.get("status", "firing")).lower()
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    severity = str(labels.get("severity", "warning")).lower()

    if status == "resolved":
        emoji = "\U0001F7E2"  # green
    elif severity == "critical":
        emoji = "\U0001F534"  # red
    else:
        emoji = "\U0001F7E1"  # yellow

    name = labels.get("alertname", "Unknown")
    summary = annotations.get("summary", "")
    desc = annotations.get("description", "")

    lines = [
        f"{emoji} <b>[{status.upper()}] {name}</b>",
        f"Severity: {severity}",
    ]
    if summary:
        lines.append(f"Summary: {summary}")
    if desc:
        lines.append(f"Detail: {desc[:300]}")
    if ops_ctx:
        lines.append(f"Context: {ops_ctx}")
    return "\n".join(lines)


def _should_send(alert_name: str) -> bool:
    """Check cooldown for alert name."""
    now = time.time()
    last = _cooldown.get(alert_name, 0)
    if now - last < COOLDOWN_SEC:
        return False
    _cooldown[alert_name] = now
    return True


class WebhookHandler(BaseHTTPRequestHandler):
    """Handle Alertmanager webhook POST and health GET."""

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "cooldown_entries": len(_cooldown)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"Bad request: {e}".encode())
            return

        alerts = data.get("alerts", [])
        ops_ctx = _get_ops_context()
        sent = 0

        for alert in alerts:
            name = alert.get("labels", {}).get("alertname", "Unknown")
            status = str(alert.get("status", "firing")).lower()
            # Always send resolved; apply cooldown only to firing
            if status != "resolved" and not _should_send(name):
                print(f"COOLDOWN skip: {name}")
                continue
            text = _format_alert(alert, ops_ctx)
            if _send_telegram(text):
                sent += 1

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"received": len(alerts), "sent": sent}).encode())

    def log_message(self, format, *args):
        print(f"[webhook] {args[0]}" if args else "")


def main():
    print(f"ARES Alertmanager Webhook v1.0.0 starting on :{LISTEN_PORT}")
    print(f"  TELEGRAM_BOT_TOKEN: {'SET' if BOT_TOKEN else 'NOT SET'}")
    print(f"  TELEGRAM_CHAT_ID: {'SET' if CHAT_ID else 'NOT SET'}")
    print(f"  COOLDOWN_SEC: {COOLDOWN_SEC}")
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), WebhookHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
