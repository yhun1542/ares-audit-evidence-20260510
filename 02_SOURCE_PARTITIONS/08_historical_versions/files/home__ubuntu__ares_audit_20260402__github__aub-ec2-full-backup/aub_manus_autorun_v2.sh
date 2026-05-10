#!/usr/bin/env bash
set -euo pipefail

# =========================
# AUB Manus Auto-Run (v2)
# =========================
# Adds: 4AI council → consensus → autogen → autopilot(<=3 exps) loop
#
# Usage:
#   bash aub_manus_autorun_v2.sh
#
# Optional env:
#   AUB_INSTALL_STUB_SERVICES=1      # install intraday_regime/event_risk_overlay stubs + systemd
#   AUB_PRECHECK_ONLY=1              # only baseline check + HANDOFF update (no experiments)
#   AUB_BATCH_SIZE=3                 # max experiments per batch (default 3)
#   AUB_RUN_COUNCIL=1                # run 4AI council cycle (default 1)
#   AUB_COUNCIL_DISABLE="grok,gemini"  # disable subset: claude,grok,gemini,gpt
#   AUB_COUNCIL_EXTRA_CONTEXT="baseline/reports/notes.md"  # extra file to include
#   AUB_AUTOGEN_MAX=3                # max experiments from consensus (default 3)
#   AUB_AUTOGEN_OUT="experiment_system/config/module_registry_autogen.yaml"
#
# Security:
# - Never embed API keys in repo or prompts. Use /etc/aub/4ai.env (exported).
# - Council scripts redact secrets automatically.

ROOT="/home/ubuntu/AUB"
cd "$ROOT"

TS="$(date +%Y%m%d_%H%M%S)"
BATCH_SIZE="${AUB_BATCH_SIZE:-3}"

CHAMPION_JSON="baseline/champion_v5_latest.json"
CHAMPION_REGISTRY="${AUB_CHAMPION_REGISTRY:-experiment_system/config/module_registry_champion_track.yaml}"

REPORT_DIR="baseline/reports/manus_autorun_${TS}"
# ProdStart multi-track overrides
: ${AUB_CHAMPION_REGISTRY:=}
: ${AUB_COUNCIL_DIR:=}
: ${AUB_AUTOGEN_OUT:=}

mkdir -p "$REPORT_DIR"

log(){ echo "[$(date +%H:%M:%S)] $*"; }
die(){ echo "[FATAL] $*" >&2; exit 1; }

# -------------------------
# 0) Baseline sanity check
# -------------------------
log "0) Baseline sanity check: ${CHAMPION_JSON}"
[ -f "$CHAMPION_JSON" ] || die "Missing champion file: $CHAMPION_JSON"

python3 - <<PY > "$REPORT_DIR/baseline_check.json"
import json
from pathlib import Path
p=Path("$CHAMPION_JSON")
d=json.loads(p.read_text())
folds=d.get("folds",[])
exps=[(f.get("diagnostics") or {}).get("avg_exposure",0.0) or 0.0 for f in folds]
deg=[]
for f in folds:
    di=f.get("diagnostics") or {}
    if (di.get("degenerate_sharpe")==True) or (float(di.get("avg_exposure",0.0) or 0.0) < 0.01) or (abs(float(f.get("oos_sharpe",0.0) or 0.0))>100):
        deg.append(int(f.get("fold", -1)))
out={
 "file": str(p),
 "oos_mean": (d.get("summary") or {}).get("oos_sharpe_mean"),
 "oos_min": (d.get("summary") or {}).get("oos_sharpe_min"),
 "worst_mdd": (d.get("summary") or {}).get("worst_mdd") or (d.get("summary") or {}).get("max_drawdown"),
 "exp_min": min(exps) if exps else None,
 "exp_max": max(exps) if exps else None,
 "degenerate_folds": deg,
 "lane_signature_hash": d.get("lane_signature_hash"),
 "run_signature_hash": d.get("run_signature_hash"),
 "total_folds": len(folds),
}
print(json.dumps(out, indent=2, ensure_ascii=False))
PY
log "Baseline check saved: $REPORT_DIR/baseline_check.json"

# -------------------------
# 0.5) Pre-flight hooks (4AI priority + profile)
# -------------------------
log "0.5) Running pre-flight hooks..."
python3 experiment_system/tools/aub_priority_builder_4ai.py --registry experiment_system/config/module_registry_autogen.yaml --topk 12 || true
# Profile once per day (check timestamp)
PROFILE_MARKER="/tmp/aub_profile_$(date +%Y%m%d).done"
if [ ! -f "$PROFILE_MARKER" ]; then
  python3 experiment_system/tools/aub_engine_profile_once.py --registry experiment_system/config/module_registry_autogen.yaml --folds 3 || true
  touch "$PROFILE_MARKER"
fi

# Optional early stop
if [ "${AUB_PRECHECK_ONLY:-0}" = "1" ]; then
  log "AUB_PRECHECK_ONLY=1 set. Stopping after baseline check."
  exit 0
fi

# -------------------------
# 1) Update perf table + HANDOFF (ensure council reads fresh context)
# -------------------------
log "1) Refresh perf_table_latest.md"
python3 experiment_system/tools/aub_detailed_perf_table.py || true
# (DEV) Autoseed v2: choose route based on inactive_reason and seed Calmar/CAGR experiments
python3 experiment_system/tools/aub_cagr_calmar_autoseed_v2.py || true
# Telegram notify (end-of-cycle)
python3 experiment_system/tools/aub_telegram_reporter_v2.py || true


log "2) Refresh HANDOFF.md"
python3 - <<PY
import json, glob
from pathlib import Path
from datetime import datetime
root=Path("/home/ubuntu/AUB")
champ=Path("$CHAMPION_JSON")
handoff=root/"baseline/HANDOFF.md"

def load(p):
    try: return json.loads(Path(p).read_text())
    except: return {}
cj=load(champ)
folds=cj.get("folds") or []
exps=[(f.get("diagnostics") or {}).get("avg_exposure",0.0) or 0.0 for f in folds]
deg=[int(f.get("fold",-1)) for f in folds if ((f.get("diagnostics") or {}).get("degenerate_sharpe")==True) or (float((f.get("diagnostics") or {}).get("avg_exposure",0.0) or 0.0)<0.01) or (abs(float(f.get("oos_sharpe",0.0) or 0.0))>100)]
txt=[]
txt.append(f"# AUB HANDOFF (auto)  {datetime.utcnow().isoformat(timespec='seconds')}Z")
txt.append("")
txt.append("## Current Champion")
txt.append(f"- file: `{champ}`")
txt.append(f"- oos_mean: {(cj.get('summary') or {}).get('oos_sharpe_mean')}  oos_min: {(cj.get('summary') or {}).get('oos_sharpe_min')}")
txt.append(f"- worst_mdd: {(cj.get('summary') or {}).get('worst_mdd') or (cj.get('summary') or {}).get('max_drawdown')}")
txt.append(f"- exposure_min/max: {min(exps) if exps else None} / {max(exps) if exps else None}")
txt.append(f"- degenerate_folds: {deg}")
txt.append(f"- lane_hash: {cj.get('lane_signature_hash')}  run_hash: {cj.get('run_signature_hash')}")
txt.append("")
txt.append("## Next Commands (3 lines)")
txt.append("```bash")
txt.append("python3 experiment_system/tools/aub_autopilot.py --registry experiment_system/config/module_registry_champion_track.yaml --run --select-best")
txt.append("python3 experiment_system/tools/system_autopilot.py --spec experiment_system/config/system_candidates.yaml --run")
txt.append("python3 experiment_system/tools/aub_detailed_perf_table.py")
txt.append("```")
handoff.write_text("\n".join(txt), encoding="utf-8")
print("[OK] wrote", handoff)
PY

# -------------------------
# 2.5) Sync backlog to registry (ensure experiments are picked up)
log "2.5) Syncing backlog to registry"
python3 experiment_system/tools/aub_backlog_registry_sync.py --force || true

# 3) 4AI council → consensus → autogen → autopilot (<=3 experiments)
# -------------------------
RUN_COUNCIL="${AUB_RUN_COUNCIL:-1}"
if [ "$RUN_COUNCIL" = "1" ]; then
  log "3) 4AI council cycle enabled"

  # Load env keys/models
  if [ -f "/etc/aub/4ai.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source /etc/aub/4ai.env
    set +a
    log "Loaded /etc/aub/4ai.env"
  else
    log "WARN: /etc/aub/4ai.env not found. Council will run only if env vars are already set."
  fi

  # Tool resolution
  COUNCIL="experiment_system/tools/aub_4ai_council.py"
  [ -f "$COUNCIL" ] || COUNCIL="experiment_system/tools/aub_4ai_council_v3.py"
  CONSENSUS="experiment_system/tools/aub_4ai_consensus.py"
  AUTOGEN="experiment_system/tools/aub_consensus_autogen_v2.py"
  [ -f "$AUTOGEN" ] || AUTOGEN="experiment_system/tools/aub_consensus_autogen_v2.py"

  [ -f "$COUNCIL" ] || die "Missing council tool: $COUNCIL"
  [ -f "$CONSENSUS" ] || die "Missing consensus tool: $CONSENSUS"
  [ -f "$AUTOGEN" ] || die "Missing autogen tool: $AUTOGEN"

  COUNCIL_OUT="baseline/reports/4ai_council"
  DISABLE="${AUB_COUNCIL_DISABLE:-}"
  # Auto-inject last delta-guard report into next council run (if user did not override)
  AUTO_CTX="baseline/reports/delta_guard_latest.md"
  if [ -n "${AUB_COUNCIL_EXTRA_CONTEXT:-}" ]; then
    EXTRA_CTX="${AUB_COUNCIL_EXTRA_CONTEXT}"
  elif [ -f "$AUTO_CTX" ]; then
    EXTRA_CTX="$AUTO_CTX"
  else
    EXTRA_CTX=""
  fi
  MAX_EXPS="${AUB_AUTOGEN_MAX:-3}"
  AUTOGEN_OUT="${AUB_AUTOGEN_OUT:-experiment_system/config/module_registry_autogen.yaml}"

  log "3.1) Running council: $COUNCIL"
  if [ -n "$DISABLE" ]; then
    export AUB_COUNCIL_EXTRA_CONTEXT="$EXTRA_CTX"
    python3 "$COUNCIL" --out-dir "$COUNCIL_OUT" --disable "$DISABLE" ${EXTRA_CTX:+--extra-file "$EXTRA_CTX"} | tee "$REPORT_DIR/council.log"
  else
    export AUB_COUNCIL_EXTRA_CONTEXT="$EXTRA_CTX"
    python3 "$COUNCIL" --out-dir "$COUNCIL_OUT" ${EXTRA_CTX:+--extra-file "$EXTRA_CTX"} | tee "$REPORT_DIR/council.log"
  fi

  log "3.2) Building consensus"
  python3 "$CONSENSUS" --out-dir "$COUNCIL_OUT" | tee "$REPORT_DIR/consensus.log"
  log "3.2.1) Run CAGR/Calmar autoseed (priority experiments)"
  python3 experiment_system/tools/aub_cagr_calmar_autoseed.py || true

  # === Complete Autonomous Loop: proposal -> sync -> pareto ===
  log "3.2.2) 4AI Proposal Builder: generate new experiment proposals"
  python3 experiment_system/tools/aub_4ai_proposal_builder.py --registry experiment_system/config/module_registry_autogen.yaml --n 10 || true
  log "3.2.3) Backlog -> Registry sync"
  python3 experiment_system/tools/aub_backlog_registry_sync.py || true
  log "3.2.4) Pareto selector: update allowlist"
  python3 experiment_system/tools/aub_pareto_selector.py --registry experiment_system/config/module_registry_autogen.yaml --topk 12 || true
  log "3.3) Autogen registry (<=${MAX_EXPS} exps)"
  python3 "$AUTOGEN" --base "$CHAMPION_REGISTRY" --out "$AUTOGEN_OUT" --max "$MAX_EXPS" | tee "$REPORT_DIR/autogen.log"

  # Ensure active_experiments == promotion.group_experiments for autopilot (with fallback)
  export AUTOGEN_OUT="$AUTOGEN_OUT"
  export AUB_AUTOGEN_MAX="$MAX_EXPS"
  python3 - <<'PY'
import os, yaml
from pathlib import Path

p = Path(os.environ.get("AUTOGEN_OUT", "experiment_system/config/module_registry_autogen.yaml"))
d = yaml.safe_load(p.read_text(encoding="utf-8"))

promo = d.get("promotion") or {}
group = promo.get("group_experiments") or []
active = d.get("active_experiments") or []
exps = d.get("experiments") or []
max_n = int(os.environ.get("AUB_AUTOGEN_MAX", "2"))

# fallback priority: group -> active -> experiments list
ids = group or active or [e.get("id") for e in exps if isinstance(e, dict) and e.get("id")]
ids = [x for x in ids if x]  # drop None/empty
ids = ids[:max_n]

# if still empty, create a safe no-op experiment (keeps pipeline alive)
if not ids:
    if exps and exps[0].get("id"):
        ids = [exps[0]["id"]]
    else:
        d.setdefault("experiments", [])
        d["experiments"].append({"id":"exp_noop", "note":"fallback noop", "overrides":{}})
        ids = ["exp_noop"]

d.setdefault("promotion", {})
d["promotion"]["group_experiments"] = ids
d["active_experiments"] = ids

p.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("[OK] autogen ids:", ids)
PY

  
# Guard: Check autogen registry is not empty
log "3.3.1) Checking autogen registry experiments"
AUTOGEN_EXP_COUNT=$(python3 -c "import yaml; r=yaml.safe_load(open('$AUTOGEN_OUT')); print(len(r.get('experiments',[])))" 2>/dev/null || echo "0")
if [ "$AUTOGEN_EXP_COUNT" = "0" ]; then
    log "[WARN] AUTOGEN REGISTRY EMPTY - forcing sync"
    python3 experiment_system/tools/aub_backlog_registry_sync.py --force
    AUTOGEN_EXP_COUNT=$(python3 -c "import yaml; r=yaml.safe_load(open('$AUTOGEN_OUT')); print(len(r.get('experiments',[])))" 2>/dev/null || echo "0")
    if [ "$AUTOGEN_EXP_COUNT" = "0" ]; then
        log "[ERROR] SYNC BROKEN - no experiments in autogen registry after sync"
        python3 -c "
import requests, os
token=os.environ.get('TELEGRAM_BOT_TOKEN','')
chat=os.environ.get('TELEGRAM_CHAT_ID','')
if token and chat:
    requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json={'chat_id':chat,'text':'[AUB] ⚠️ SYNC BROKEN: autogen registry empty after sync. Check backlog.'}, timeout=10)
" 2>/dev/null || true
        log "Skipping autopilot run due to empty registry"
        # Don't exit, continue to next steps
    fi
fi
log "Autogen registry has $AUTOGEN_EXP_COUNT experiments"

  log "3.4) Run autopilot on autogen registry"
  python3 experiment_system/tools/aub_autopilot.py --registry "$AUTOGEN_OUT" --run --select-best | tee "$REPORT_DIR/autogen_autopilot.log"

  # P0-2: Run delta_guard on worst FAIL candidate and inject to next council
  log "3.4.1) Generate delta_guard report for next council context"
  python3 - <<'DELTA_PY'
import json, glob, os
from pathlib import Path

# Find worst FAIL candidate (lowest lock_min)
artifacts = sorted(glob.glob("/home/ubuntu/AUB/artifacts/autopilot_*/*.json"), key=lambda x: Path(x).stat().st_mtime, reverse=True)[:20]
worst_path, worst_lock_min = None, 999
for p in artifacts:
    try:
        d = json.loads(Path(p).read_text())
        lm = d.get("summary", {}).get("oos_sharpe_min", 999)
        if lm < worst_lock_min:
            worst_lock_min, worst_path = lm, p
    except: pass

if worst_path:
    print(f"[INFO] Worst candidate: {worst_path} (lock_min={worst_lock_min:.4f})")
    os.system(f"python3 experiment_system/tools/aub_delta_guard.py --base baseline/champion_v5_latest.json --cand {worst_path} > baseline/reports/no_delta_diagnosis.md 2>/dev/null || true")
    # Set extra context for next council
    Path("/tmp/aub_council_extra_context.txt").write_text("baseline/reports/no_delta_diagnosis.md")
else:
    print("[INFO] No FAIL candidates found for delta_guard")
DELTA_PY
  # Export extra context for next cycle
  if [ -f /tmp/aub_council_extra_context.txt ]; then
    export AUB_COUNCIL_EXTRA_CONTEXT=$(cat /tmp/aub_council_extra_context.txt)
    log "3.4.2) Next council extra context: $AUB_COUNCIL_EXTRA_CONTEXT"
  fi
  log "3.5) Refresh perf_table_latest.md after autogen run"
  python3 experiment_system/tools/aub_detailed_perf_table.py || true
else
  log "3) Council cycle skipped (AUB_RUN_COUNCIL=0)"
fi

log "DONE. Outputs in: $REPORT_DIR"

# === Inactive Dominant Router Hook ===
log "4) Running inactive dominant router"
python3 experiment_system/tools/aub_inactive_dominant_router.py --track A 2>&1 | tee -a "$REPORT_DIR/inactive_router.log" || true
log "4.1) Inactive router completed"

# === Precision Observer (post-run collector) ===
log "5) Running precision observer"
python3 experiment_system/tools/aub_precision_observer.py 2>&1 | tee -a "$REPORT_DIR/precision_observer.log" || true
log "5.1) Precision observer completed"

# === Escape Mode Notifier ===
log "6) Running escape mode notifier"
python3 experiment_system/tools/aub_escape_mode_notifier.py 2>&1 | tee -a "$REPORT_DIR/escape_notifier.log" || true
log "6.1) Escape mode notifier completed"
