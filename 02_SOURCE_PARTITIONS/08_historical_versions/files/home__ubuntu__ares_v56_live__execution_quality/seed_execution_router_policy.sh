#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# CORRECTED: seed_execution_router_policy.sh
# EC2 실제 Redis 키 패턴에 맞게 수정됨 (2026-04-01)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

MODE="${1:-shadow}"
REDIS_CLI="${REDIS_CLI:-redis-cli}"
KEY="policy:execution_router"
CANARY_SYMBOLS="${CANARY_SYMBOLS:-AAPL,MSFT,NVDA,META}"

json_for_mode() {
  python3 - "$MODE" "$CANARY_SYMBOLS" <<'PY'
import json, sys
mode = sys.argv[1]
canary_symbols = [x.strip() for x in sys.argv[2].split(',') if x.strip()]
base = {
    "version": "execution_router_policy_v1",
    "policy_key": "policy:execution_router",
    "shadow_enabled": True,
    "canary_enabled": False,
    "canary_symbols": canary_symbols,
    "quote_fresh_ms": 1500,
    "max_spread_bps_buy": 15,
    "max_spread_bps_sell": 20,
    "min_notional_usd": 150,
    "max_requotes_buy": 3,
    "max_requotes_sell": 2,
    "escape_after_ms_buy": 45000,
    "escape_after_ms_sell": 30000,
    "allow_direct_market_fallback": True,
    "fallback_only_when_urgent": True,
    "require_polygon_quote": True,
    "block_if_quote_missing": False,
    "streams": {
        "peg_stream": "stream:peg_requests",
        "peg_shadow_stream": "stream:peg_requests_shadow",
        "exec_stream": "emarkos:v1:execution",
        "exec_quality_stream": "stream:execution_quality",
        "fills_stream": "stream:fills",
        "executions_stream": "stream:executions"
    },
    "quotes": {
        "pattern": "premium:polygon:{symbol}",
        "max_quote_age_ms": 1500
    },
    "telemetry": {
        "record_shadow": True,
        "record_direct_fallback": True,
        "record_requote_events": True,
        "record_escape_events": True,
        "record_arrival_quote": True,
        "record_fill_summary": True
    },
    "pm2_process_paths": {
        "order_intent_executor": "/home/ubuntu/ares_releases/2026-02-17/order-intent-executor.mjs",
        "live_trading_kis": "/home/ubuntu/ares_releases/2026-02-17/live-trading-kis.mjs",
        "algo_maker_pegger": "/home/ubuntu/ares_v56_live/releases/20260327_072504/algo-maker-pegger.mjs",
        "polygon_ws_nbbo": "/home/ubuntu/ares_releases/2026-02-17/ops/polygon-ws-nbbo.mjs",
        "data_gateway": "/home/ubuntu/data-gateway.mjs"
    }
}
if mode == "shadow":
    base["mode"] = "shadow_only"
elif mode == "canary":
    base["mode"] = "maker_primary"
    base["canary_enabled"] = True
elif mode == "live":
    base["mode"] = "maker_primary"
    base["canary_enabled"] = False
elif mode == "panic_direct":
    base["mode"] = "direct_primary"
    base["shadow_enabled"] = False
    base["allow_direct_market_fallback"] = True
else:
    raise SystemExit(f"unsupported mode: {mode}")
print(json.dumps(base, ensure_ascii=False))
PY
}

PAYLOAD="$(json_for_mode)"
$REDIS_CLI SET "$KEY" "$PAYLOAD" >/dev/null

echo "[OK] seeded $KEY with mode=$MODE"
echo "$PAYLOAD" | python3 -m json.tool
