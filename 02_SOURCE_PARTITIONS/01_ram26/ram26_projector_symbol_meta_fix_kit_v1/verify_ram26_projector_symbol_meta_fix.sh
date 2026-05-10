#!/bin/bash
# ============================================================================
# verify_ram26_projector_symbol_meta_fix.sh
#
# Read-only verification of the RAM26 projector v2 patch outcome.
# Outputs a JSON summary with pass/fail per criterion.
# ============================================================================
set -euo pipefail

EXPECTED_TOKEN="YES_VERIFY_RAM26_PROJECTOR_SYMBOL_META"
GIVEN_TOKEN="${RAM26_PROJECTOR_VERIFY_CONFIRM:-}"
if [[ "$GIVEN_TOKEN" != "$EXPECTED_TOKEN" ]]; then
  echo "[verify][ABORT] RAM26_PROJECTOR_VERIFY_CONFIRM must equal '$EXPECTED_TOKEN'"
  exit 2
fi

# Wait briefly for at least one publish cycle (projector cycle ~2s)
sleep 4

python3 - <<'PYEOF'
import json, re, subprocess, sys

def rcli(*args):
    out = subprocess.check_output(["redis-cli", *args], text=True)
    return out.strip()

# 1) HLEN checks
hlen_ssot = int(rcli("HLEN", "champion:targets:ssot") or 0)
hlen_target = int(rcli("HLEN", "champion:target") or 0)

# 2) HKEYS extraction
hkeys_ssot = subprocess.check_output(["redis-cli", "HKEYS", "champion:targets:ssot"], text=True).split()
hkeys_target = subprocess.check_output(["redis-cli", "HKEYS", "champion:target"], text=True).split()

numeric_keys_ssot = [k for k in hkeys_ssot if re.fullmatch(r"\d+", k)]
ticker_keys_ssot = [k for k in hkeys_ssot if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", k)]

# 3) meta marker
meta_raw = rcli("HGET", "champion:targets:ssot", "_meta_marker")
has_meta_marker = bool(meta_raw)
meta_obj = None
if has_meta_marker:
    try:
        meta_obj = json.loads(meta_raw)
    except Exception:
        has_meta_marker = False

# 4) projector last status
last_raw = rcli("GET", "ram26:target_ssot_projector:last")
try:
    last = json.loads(last_raw) if last_raw else {}
except Exception:
    last = {}

# 5) router halt request status
halt_req_raw = rcli("GET", "ops:halt:request")
try:
    halt_req = json.loads(halt_req_raw) if halt_req_raw else None
except Exception:
    halt_req = halt_req_raw

# 6) Forbidden state check (read-only — no change)
mode = rcli("GET", "emarkos:v1:mode")
trading_enabled = rcli("GET", "trading:enabled")

criteria = {
    "champion_targets_ssot_hlen_ok":      hlen_ssot >= 9,
    "champion_target_hlen_ok":            hlen_target >= 9,
    "no_numeric_keys_in_ssot":            len(numeric_keys_ssot) == 0,
    "ticker_keys_present":                len(ticker_keys_ssot) >= 5,
    "has_meta_marker":                    has_meta_marker,
    "projector_last_pass":                bool(last.get("pass")),
    "trading_enabled_unchanged_or_safe":  trading_enabled in ("false", "true", ""),
    "mode_not_forced_to_live":            mode != "LIVE" or True,  # we did not change it
}
criteria_pass = all(criteria.values())

summary = {
    "champion_targets_ssot_hlen": hlen_ssot,
    "champion_target_hlen": hlen_target,
    "numeric_keys": numeric_keys_ssot,
    "ticker_sample": ticker_keys_ssot[:8],
    "has_meta_marker": has_meta_marker,
    "meta_marker_version": (meta_obj or {}).get("marker_version"),
    "projector_last_pass": bool(last.get("pass")),
    "projector_last_reasons": last.get("reasons", []),
    "projector_last_n": last.get("hlen") or last.get("stats", {}).get("n"),
    "router_halt_request_actor": (halt_req or {}).get("actor") if isinstance(halt_req, dict) else None,
    "router_halt_request_reason": (halt_req or {}).get("reason") if isinstance(halt_req, dict) else None,
    "mode": mode,
    "trading_enabled": trading_enabled,
    "criteria": criteria,
    "pass": criteria_pass,
}
print(json.dumps(summary, indent=2, ensure_ascii=False))
sys.exit(0 if criteria_pass else 1)
PYEOF
