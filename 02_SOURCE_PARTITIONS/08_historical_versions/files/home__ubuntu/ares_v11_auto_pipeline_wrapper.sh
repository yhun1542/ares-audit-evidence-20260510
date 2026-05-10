#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# ARES v11 AUTO PIPELINE WRAPPER (FIXED)
# ============================================================

APP_DIR=${APP_DIR:-/home/ubuntu/ares_v9}
VENV_DIR=${VENV_DIR:-/home/ubuntu/ares_v9/.venv}

# input
EFFECT_DIR=${EFFECT_DIR:-/home/ubuntu/addon_effect_out}

# config / branch
BASE_CONFIG=${BASE_CONFIG:-configs/v10_sharpe_core.yaml}
GRID_SPEC_FILE=${GRID_SPEC_FILE:-configs/supergrid_smoke_sharpe.json} # Bug 3 Fix
BRANCH=${BRANCH:-v11}
TOP_N=${TOP_N:-12}
SHARD_SIZE=${SHARD_SIZE:-100}
BASE_MODE=${BASE_MODE:-v11_sharpe}

# stage naming
PIPELINE_NAME=${PIPELINE_NAME:-v11_auto_stage1}

# local output roots
PIPELINE_ROOT=${PIPELINE_ROOT:-/home/ubuntu/ares_v9/outputs/${PIPELINE_NAME}}
RECO_OUT=${RECO_OUT:-${PIPELINE_ROOT}/01_reco}
BRIDGE_OUT=${BRIDGE_OUT:-${PIPELINE_ROOT}/02_bridge}
MANIFEST_OUT=${MANIFEST_OUT:-${PIPELINE_ROOT}/03_manifest}
DISPATCH_OUT=${DISPATCH_OUT:-${PIPELINE_ROOT}/04_dispatch}
REPORT_DIR=${REPORT_DIR:-/home/ubuntu/ares_v9/outputs/${PIPELINE_NAME}_reports}

# s3
S3_BUCKET=${S3_BUCKET:-my-ares-bucket-bb86abba}
S3_PREFIX=${S3_PREFIX:-ares_v9/${PIPELINE_NAME}}

mkdir -p "${PIPELINE_ROOT}" "${REPORT_DIR}"

activate_venv() {
  if [[ -f "${VENV_DIR}/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "${VENV_DIR}/bin/activate"
  else
    echo "[WARN] venv not found at ${VENV_DIR}, using system python3." # Bug 1 Fix
  fi
}

prepare_recommendation() {
  echo "[1/4] recommend v11 from addon effects"
  python3 "${APP_DIR}/ares_recommend_v11_from_effects.py" \
    --effect_dir "${EFFECT_DIR}" \
    --out "${RECO_OUT}" \
    --base_mode "${BASE_MODE}"

  echo "[2/4] bridge recommender output to matrix-like manifest input"
  python3 "${APP_DIR}/ares_bridge_v11_reco_to_manifest.py" \
    --reco_dir "${RECO_OUT}" \
    --out "${BRIDGE_OUT}" \
    --top_n "${TOP_N}" \
    --shard_size "${SHARD_SIZE}"

  echo "[3/4] build manifest/shards from bridge-compatible matrix"
  python3 "${APP_DIR}/ares_matrix_to_supergrid_manifest.py" \
    --matrix_dir "${BRIDGE_OUT}" \
    --base_config "${APP_DIR}/${BASE_CONFIG}" \
    --out "${MANIFEST_OUT}" \
    --branch "${BRANCH}" \
    --top_n "${TOP_N}" \
    --shard_size "${SHARD_SIZE}"

  echo "[4/4] copy manifest shards into controller dispatch workspace"
  mkdir -p "${DISPATCH_OUT}/shards"
  cp -f "${MANIFEST_OUT}/${BRANCH}/manifest_${BRANCH}.json" "${DISPATCH_OUT}/manifest_${BRANCH}.json"
  cp -f "${MANIFEST_OUT}/${BRANCH}/manifest_${BRANCH}_preview.csv" "${DISPATCH_OUT}/manifest_${BRANCH}_preview.csv" || true
  find "${MANIFEST_OUT}/${BRANCH}/shards/" -name '*.json' -exec cp -t "${DISPATCH_OUT}/shards/" '{}' + # Bug 4 Fix

  echo "[DONE] prepare complete"
  echo "  RECO_OUT      = ${RECO_OUT}"
  echo "  BRIDGE_OUT    = ${BRIDGE_OUT}"
  echo "  MANIFEST_OUT  = ${MANIFEST_OUT}"
  echo "  DISPATCH_OUT  = ${DISPATCH_OUT}"
}

dispatch_only() {
  echo "[DISPATCH] upload shards using existing controller_dispatch.sh"
  APP_DIR="${APP_DIR}" \
  OUT_DIR="${DISPATCH_OUT}" \
  REPORT_DIR="${REPORT_DIR}" \
  BASE_CONFIG="${BASE_CONFIG}" \
  GRID_SPEC="${APP_DIR}/${GRID_SPEC_FILE}" \
  SHARD_SIZE="${SHARD_SIZE}" \
  S3_BUCKET="${S3_BUCKET}" \
  S3_PREFIX="${S3_PREFIX}" \
  bash "${APP_DIR}/infra/ec2/controller_dispatch.sh" init

  echo "[DISPATCH] syncing prepared shards to S3"
  aws s3 sync "${DISPATCH_OUT}/shards/" "s3://${S3_BUCKET#s3://}/${S3_PREFIX}/shards/" \
    --exclude "*" --include "*.json"

  tmp=$(mktemp)
  echo "{}" > "${tmp}"
  aws s3 cp "${tmp}" "s3://${S3_BUCKET}/${S3_PREFIX}/claims/.keep" >/dev/null
  aws s3 cp "${tmp}" "s3://${S3_BUCKET}/${S3_PREFIX}/done/.keep" >/dev/null
  aws s3 cp "${tmp}" "s3://${S3_BUCKET}/${S3_PREFIX}/results/.keep" >/dev/null
  aws s3 cp "${tmp}" "s3://${S3_BUCKET}/${S3_PREFIX}/errors/.keep" >/dev/null
  rm -f "${tmp}"

  echo "[DONE] dispatch complete"
  echo "  S3_PREFIX = s3://${S3_BUCKET}/${S3_PREFIX}"
}

collect_only() {
  echo "[COLLECT] collect results using controller_dispatch.sh"
  APP_DIR="${APP_DIR}" \
  OUT_DIR="${DISPATCH_OUT}" \
  REPORT_DIR="${REPORT_DIR}" \
  BASE_CONFIG="${BASE_CONFIG}" \
  GRID_SPEC="${APP_DIR}/${GRID_SPEC_FILE}" \
  SHARD_SIZE="${SHARD_SIZE}" \
  S3_BUCKET="${S3_BUCKET}" \
  S3_PREFIX="${S3_PREFIX}" \
  bash "${APP_DIR}/infra/ec2/controller_dispatch.sh" collect
}

show_progress() {
  APP_DIR="${APP_DIR}" \
  OUT_DIR="${DISPATCH_OUT}" \
  REPORT_DIR="${REPORT_DIR}" \
  BASE_CONFIG="${BASE_CONFIG}" \
  GRID_SPEC="${APP_DIR}/${GRID_SPEC_FILE}" \
  SHARD_SIZE="${SHARD_SIZE}" \
  S3_BUCKET="${S3_BUCKET}" \
  S3_PREFIX="${S3_PREFIX}" \
  bash "${APP_DIR}/infra/ec2/controller_dispatch.sh" progress
}

reset_remote() {
  APP_DIR="${APP_DIR}" \
  OUT_DIR="${DISPATCH_OUT}" \
  REPORT_DIR="${REPORT_DIR}" \
  BASE_CONFIG="${BASE_CONFIG}" \
  GRID_SPEC="${APP_DIR}/${GRID_SPEC_FILE}" \
  SHARD_SIZE="${SHARD_SIZE}" \
  S3_BUCKET="${S3_BUCKET}" \
  S3_PREFIX="${S3_PREFIX}" \
  bash "${APP_DIR}/infra/ec2/controller_dispatch.sh" reset
}

activate_venv

CMD="${1:-full}"

case "${CMD}" in
  prepare)
    prepare_recommendation
    ;;
  dispatch)
    dispatch_only
    ;;
  collect)
    collect_only
    ;;
  progress)
    show_progress
    ;;
  reset)
    reset_remote
    ;;
  full)
    prepare_recommendation
    dispatch_only
    echo "[INFO] workers will now process shards under s3://${S3_BUCKET}/${S3_PREFIX}/shards/"
    echo "[INFO] later run:"
    echo "  bash $0 progress"
    echo "  bash $0 collect"
    ;;
  *)
    echo "Usage:"
    echo "  $0 prepare   # effect -> reco -> bridge -> manifest"
    echo "  $0 dispatch  # prepared shards -> S3"
    echo "  $0 collect   # S3 results -> merge/rank"
    echo "  $0 progress  # remote progress"
    echo "  $0 reset     # clear remote state"
    echo "  $0 full      # prepare + dispatch"
    exit 1
    ;;
esac
