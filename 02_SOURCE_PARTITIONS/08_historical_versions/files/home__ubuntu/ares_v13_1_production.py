#!/usr/bin/env python3
"""
ARES v1.3.1 — UNIFIED CORE OVERLAY REDESIGN (PRODUCTION)
=========================================================
Takes v1.2.1 trained models and integrates them into a Unified Core
with DecisionContext/DecisionOutput JSON contract.

Modules updated:
  - RLBehaviorClonePolicy (replaces RLPolicy)
  - ChronosToxicity (deterministic tox_score + thresholds)
  - CostModel (asymmetric TWAP cost)
  - IRBController (PnL triggers)

Runs REPLAY mode on 2026 live data to verify parity.
"""
from __future__ import annotations
import datetime, hashlib, json, os, sys, time
import numpy as np
import pandas as pd

# ─── SSOT CONFIG ─────────────────────────────────────────────
UTC_TAG = datetime.datetime.utcnow().strftime("%Y%m%d")
SNAPSHOT_ID = f"ARES_V13_1_CORE_OVERLAY_REDESIGN_{UTC_TAG}"
BASE_DIR = f"/home/ubuntu/ssot/v1.3.1/{SNAPSHOT_ID}"
V12_SNAPSHOT = f"ARES_V12_1_OVERLAY_REDESIGN_{UTC_TAG}"
V12_BASE = f"/home/ubuntu/ssot/v1.2.1/{V12_SNAPSHOT}"

SEED = 42
np.random.seed(SEED)

# ─── HELPERS ─────────────────────────────────────────────────
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def write_json(path: str, obj: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"  [SSOT] {os.path.relpath(path, BASE_DIR)}")

# ═══════════════════════════════════════════════════════════════
#  MODULE 1: DecisionContext / DecisionOutput Schema
# ═══════════════════════════════════════════════════════════════
def build_decision_context_schema():
    return {
        "schema_id": "ARES_V13_1_DECISION_CONTEXT_001",
        "version": "1.3.1",
        "required_fields": {
            "market": [
                "date", "qqq_ret_1d", "qqq_vol_20d_ann", "qqq_mom_60d",
                "vix_z_20d", "risk_on_proxy"
            ],
            "portfolio": [
                "portfolio_vol_20d", "portfolio_ret_1d",
                "pnl_5d_sum_lag1", "dd_20d_lag1"
            ],
            "regime": [
                "regime_state_lag1", "regime_score_lag1", "regime_conf_lag1"
            ],
            "toxicity": [
                "tox_score_lag1", "qqq_absret_z_60d_lag1",
                "downside_cluster_5d_lag1"
            ],
            "cost": ["ac_cost_bps_est"],
            "model": ["xgb_risk", "vol_target_scale"]
        }
    }

def build_decision_output_schema():
    return {
        "schema_id": "ARES_V13_1_DECISION_OUTPUT_001",
        "version": "1.3.1",
        "output_fields": {
            "scaling": ["rl_scale", "vol_target_scale", "overlay_scale"],
            "hedge": ["w_hedge", "hedge_return"],
            "cost": ["cost_bps", "cost_daily"],
            "irb": ["irb_cap", "irb_state"],
            "toxicity": ["tox_score", "tox_state"],
            "meta": ["regime_state", "base_leverage", "final_leverage"]
        }
    }

# ═══════════════════════════════════════════════════════════════
#  MODULE 2: RLBehaviorClonePolicy
# ═══════════════════════════════════════════════════════════════
class RLBehaviorClonePolicy:
    """Loads v1.2.1 RL BC model and produces rl_scale predictions."""

    def __init__(self, model_dir: str, fold: int, feat_cols: list):
        self.feat_cols = feat_cols
        self.fold = fold
        self.model_path = os.path.join(model_dir, f"rl_fold{fold}.pt")
        self.model = None
        self._load_model()

    def _load_model(self):
        try:
            import torch
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

            class BCNet(torch.nn.Module):
                def __init__(self, n_feat):
                    super().__init__()
                    self.net = torch.nn.Sequential(
                        torch.nn.Linear(n_feat, 64),
                        torch.nn.ReLU(),
                        torch.nn.Linear(64, 32),
                        torch.nn.ReLU(),
                        torch.nn.Linear(32, 1),
                        torch.nn.Sigmoid()
                    )
                def forward(self, x):
                    return self.net(x) * 1.1 + 0.2

            state = torch.load(self.model_path, map_location=self.device, weights_only=False)
            # Handle v1.2.1 saved format: may have 'model_state_dict' key
            if isinstance(state, dict) and "model_state_dict" in state:
                state_dict = state["model_state_dict"]
                n_feat = state.get("state_dim", len(self.feat_cols))
                self.state_mean = state.get("state_mean", np.zeros(n_feat))
                self.state_std = state.get("state_std", np.ones(n_feat))
            else:
                state_dict = state
                n_feat = len(self.feat_cols)
                self.state_mean = np.zeros(n_feat)
                self.state_std = np.ones(n_feat)

            # Detect architecture from state_dict keys
            first_key = list(state_dict.keys())[0]
            if first_key.startswith("net."):
                self.model = BCNet(n_feat).to(self.device)
            else:
                # Build model matching saved architecture
                layers = []
                prev_key = None
                layer_sizes = []
                for k in state_dict.keys():
                    if k.endswith(".weight"):
                        layer_sizes.append(state_dict[k].shape)
                if layer_sizes:
                    in_dim = layer_sizes[0][1]
                    for i, (out_d, in_d) in enumerate(layer_sizes):
                        layers.append(torch.nn.Linear(in_d, out_d))
                        if i < len(layer_sizes) - 1:
                            layers.append(torch.nn.ReLU())
                    layers.append(torch.nn.Sigmoid())
                    self.model = torch.nn.Sequential(*layers).to(self.device)
                else:
                    self.model = BCNet(n_feat).to(self.device)

            self.model.load_state_dict(state_dict)
            self.model.eval()
            print(f"  [RL-BC] Loaded fold {self.fold} model (n_feat={n_feat}) from {self.model_path}")
        except Exception as e:
            print(f"  [RL-BC] WARNING: Could not load model: {e}")
            self.model = None

    def predict(self, features: np.ndarray) -> np.ndarray:
        if self.model is None:
            return np.ones(len(features))
        import torch
        # Normalize using saved stats
        if hasattr(self, 'state_mean') and hasattr(self, 'state_std'):
            mean = np.array(self.state_mean, dtype=np.float64)
            std = np.array(self.state_std, dtype=np.float64)
            std = np.where(std < 1e-9, 1.0, std)
            # Ensure dimensions match
            if len(mean) == features.shape[1]:
                features = (features - mean) / std
        with torch.no_grad():
            x = torch.tensor(features, dtype=torch.float32).to(self.device)
            raw = self.model(x).cpu().numpy().flatten()
            # Scale to [0.2, 1.3] range
            out = raw * 1.1 + 0.2
        # Smoothness clamp: |Δscale| <= 0.30
        for i in range(1, len(out)):
            delta = out[i] - out[i-1]
            if abs(delta) > 0.30:
                out[i] = out[i-1] + np.sign(delta) * 0.30
        return out

# ═══════════════════════════════════════════════════════════════
#  MODULE 3: ChronosToxicity (Deterministic)
# ═══════════════════════════════════════════════════════════════
class ChronosToxicity:
    """Deterministic toxicity scoring with train-only calibration."""

    def __init__(self, tox_config: dict):
        self.a = tox_config["coeffs"]["a"]
        self.b = tox_config["coeffs"]["b"]
        self.c = tox_config["coeffs"]["c"]
        self.predator_th = tox_config["thresholds"]["predator_tox_norm_ge"]
        self.vacuum_th = tox_config["thresholds"]["vacuum_tox_norm_ge"]

    def compute(self, vix_z: np.ndarray, absret_z: np.ndarray,
                down_cluster: np.ndarray) -> tuple:
        tox_raw = self.a * vix_z + self.b * absret_z + self.c * down_cluster
        # Rank normalization
        n = len(tox_raw)
        order = np.argsort(tox_raw, kind="mergesort")
        ranks = np.empty(n, dtype=np.float64)
        ranks[order] = np.arange(1, n + 1, dtype=np.float64)
        tox_norm = ranks / n

        states = np.where(tox_norm >= self.predator_th, 2,
                 np.where(tox_norm >= self.vacuum_th, 1, 0))
        return tox_norm, states  # 0=NORMAL, 1=VACUUM, 2=PREDATOR

# ═══════════════════════════════════════════════════════════════
#  MODULE 4: CostModel (Asymmetric TWAP)
# ═══════════════════════════════════════════════════════════════
class CostModel:
    K_DOWN = 0.20
    K_PRED = 0.30
    K_VAC_SPREAD_ADD = 2.0

    @staticmethod
    def compute(base_cost_bps: np.ndarray, qqq_ret: np.ndarray,
                tox_states: np.ndarray) -> np.ndarray:
        cost = base_cost_bps.copy()
        # Asymmetric: down days cost more
        down_mask = qqq_ret < 0
        cost[down_mask] *= (1 + CostModel.K_DOWN)
        # Predator: additional multiplier
        pred_mask = tox_states == 2
        cost[pred_mask] *= (1 + CostModel.K_PRED)
        # Vacuum: spread add
        vac_mask = tox_states == 1
        cost[vac_mask] += CostModel.K_VAC_SPREAD_ADD
        return cost

# ═══════════════════════════════════════════════════════════════
#  MODULE 5: IRBController (PnL Triggers)
# ═══════════════════════════════════════════════════════════════
class IRBController:
    @staticmethod
    def compute(xgb_risk: np.ndarray, pnl_5d: np.ndarray,
                dd_20d: np.ndarray) -> np.ndarray:
        n = len(xgb_risk)
        irb_cap = np.ones(n)

        # XGB risk-based cap
        irb_cap = np.where(xgb_risk > 0.15, 0.8,
                  np.where(xgb_risk > 0.10, 0.9, 1.0))

        # PnL triggers
        for i in range(n):
            cap = irb_cap[i]
            if pnl_5d[i] < -0.02:
                cap = min(cap, 0.85)
            if dd_20d[i] < -0.05:
                cap = min(cap, 0.75)
            # Recovery: +0.02/day from previous (if applicable)
            if i > 0 and irb_cap[i-1] < 1.0:
                recovered = irb_cap[i-1] + 0.02
                cap = min(cap, min(recovered, 1.0))
            irb_cap[i] = cap

        return irb_cap

# ═══════════════════════════════════════════════════════════════
#  MODULE 6: AresDecisionCore (Unified API)
# ═══════════════════════════════════════════════════════════════
class AresDecisionCore:
    """Unified decision API for both live and backtest."""

    def __init__(self, rl_policy, chronos, cost_model, irb_ctrl):
        self.rl = rl_policy
        self.chronos = chronos
        self.cost = cost_model
        self.irb = irb_ctrl

    def decide(self, context: dict) -> dict:
        """
        Takes DecisionContext dict, returns DecisionOutput dict.
        All arrays must be same length (N days).
        """
        n = len(context["qqq_ret_1d"])

        # 1. Toxicity
        tox_norm, tox_states = self.chronos.compute(
            context["vix_z_20d"],
            context["qqq_absret_z_60d_lag1"],
            context["downside_cluster_5d_lag1"]
        )

        # 2. RL Scale
        feat_matrix = np.column_stack([
            context.get(c, np.zeros(n)) for c in self.rl.feat_cols
        ])
        rl_scale = self.rl.predict(feat_matrix)

        # 3. Vol Target Scale (fixed 1.6x as per live)
        vol_target_scale = np.full(n, 1.6)

        # 4. IRB Cap
        irb_cap = self.irb.compute(
            context["xgb_risk"],
            context["pnl_5d_sum_lag1"],
            context["dd_20d_lag1"]
        )

        # 5. Cost
        cost_bps = self.cost.compute(
            context["ac_cost_bps_est"],
            context["qqq_ret_1d"],
            tox_states
        )

        # 6. Hedge
        w_hedge = np.where(tox_states == 2, 0.20,
                  np.where(tox_states == 1, 0.10, 0.05))
        # Tox bonus
        tox_bonus = np.where(tox_states == 2, 0.10,
                    np.where(tox_states == 1, 0.05, 0.0))
        w_hedge = np.minimum(w_hedge + tox_bonus, 0.35)

        # 7. Overlay scale
        overlay_scale = rl_scale * vol_target_scale * irb_cap

        # 8. State labels
        state_labels = np.where(tox_states == 2, "PREDATOR",
                       np.where(tox_states == 1, "VACUUM", "NORMAL"))

        return {
            "rl_scale": rl_scale,
            "vol_target_scale": vol_target_scale,
            "overlay_scale": overlay_scale,
            "w_hedge": w_hedge,
            "cost_bps": cost_bps,
            "irb_cap": irb_cap,
            "tox_score": tox_norm,
            "tox_state": state_labels,
        }

# ═══════════════════════════════════════════════════════════════
#  REPLAY MODE: Run on 2026 live data
# ═══════════════════════════════════════════════════════════════
def run_replay_2026(core: AresDecisionCore, live_df: pd.DataFrame) -> dict:
    """Run Unified Core on 2026 live data and compare."""
    n = len(live_df)
    print(f"\n[REPLAY] Running on {n} days of 2026 live data")

    # Build context from live data
    context = {}
    for col in live_df.columns:
        if col == "date":
            continue
        try:
            context[col] = live_df[col].to_numpy(dtype=np.float64)
        except (ValueError, TypeError):
            # Skip non-numeric columns (e.g., string regime states)
            pass

    # Fill missing context fields with zeros
    for needed in core.rl.feat_cols:
        if needed not in context:
            context[needed] = np.zeros(n)

    # Run unified core
    output = core.decide(context)

    # Compare with live
    results = {"n_days": n}

    for field in ["rl_scale", "w_hedge", "cost_bps"]:
        if field in live_df.columns:
            live_vals = live_df[field].to_numpy(dtype=np.float64)
            model_vals = output[field]
            if isinstance(model_vals[0], str):
                continue
            mae = float(np.mean(np.abs(model_vals - live_vals)))
            corr = float(np.corrcoef(model_vals, live_vals)[0, 1]) if np.std(live_vals) > 1e-9 else 0.0
            results[f"{field}_mae"] = mae
            results[f"{field}_corr"] = corr
            results[f"{field}_model_mean"] = float(np.mean(model_vals))
            results[f"{field}_live_mean"] = float(np.mean(live_vals))

    # RL stability
    if len(output["rl_scale"]) > 1:
        delta_rl = np.abs(np.diff(output["rl_scale"]))
        results["rl_delta_p95"] = float(np.percentile(delta_rl, 95))

    return results, output

# ═══════════════════════════════════════════════════════════════
#  PARITY GATES
# ═══════════════════════════════════════════════════════════════
def check_parity_gates(replay_results: dict) -> dict:
    gates = {}

    # G1: RL parity
    rl_mae = replay_results.get("rl_scale_mae", 999)
    rl_corr = replay_results.get("rl_scale_corr", 0)
    rl_p95 = replay_results.get("rl_delta_p95", 999)

    gates["rl_corr"] = {"value": rl_corr, "threshold": 0.90, "pass": rl_corr >= 0.90}
    gates["rl_mae"] = {"value": rl_mae, "threshold": 0.05, "pass": rl_mae <= 0.05}
    gates["rl_p95"] = {"value": rl_p95, "threshold": 0.30, "pass": rl_p95 <= 0.30}

    # G2: Hedge parity
    hedge_mae = replay_results.get("w_hedge_mae", 999)
    gates["hedge_mae"] = {"value": hedge_mae, "threshold": 0.02, "pass": hedge_mae <= 0.02}

    # G3: Cost parity
    cost_mae = replay_results.get("cost_bps_mae", 999)
    gates["cost_mae"] = {"value": cost_mae, "threshold": 5.0, "pass": cost_mae <= 5.0}

    gates["overall"] = all(g["pass"] for g in gates.values() if isinstance(g, dict) and "pass" in g)

    return gates

# ═══════════════════════════════════════════════════════════════
#  SSOT LOGGING
# ═══════════════════════════════════════════════════════════════
def save_decision_log(output: dict, dates: np.ndarray, path: str):
    """Save daily decision log as CSV."""
    log_df = pd.DataFrame({
        "date": dates,
        "rl_scale": output["rl_scale"],
        "vol_target_scale": output["vol_target_scale"],
        "overlay_scale": output["overlay_scale"],
        "w_hedge": output["w_hedge"],
        "cost_bps": output["cost_bps"],
        "irb_cap": output["irb_cap"],
        "tox_score": output["tox_score"],
        "tox_state": output["tox_state"],
    })
    os.makedirs(os.path.dirname(path), exist_ok=True)
    log_df.to_csv(path, index=False)
    print(f"  [SSOT] Decision log: {os.path.relpath(path, BASE_DIR)}")

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    t0 = time.time()
    print("=" * 70)
    print(f"  ARES v1.3.1 — UNIFIED CORE OVERLAY REDESIGN (PRODUCTION)")
    print(f"  Snapshot: {SNAPSHOT_ID}")
    print(f"  v1.2.1 models from: {V12_BASE}")
    print("=" * 70)

    # Create output dirs
    for d in ["schemas", "logs", "reports", "hashes"]:
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)

    # ── PHASE 1: Save schemas ──
    print("\n" + "=" * 70)
    print("  PHASE 1: DECISION SCHEMAS")
    print("=" * 70)
    ctx_schema = build_decision_context_schema()
    out_schema = build_decision_output_schema()
    write_json(os.path.join(BASE_DIR, "schemas", "decision_context_v131.json"), ctx_schema)
    write_json(os.path.join(BASE_DIR, "schemas", "decision_output_v131.json"), out_schema)

    # ── PHASE 2: Load v1.2.1 models ──
    print("\n" + "=" * 70)
    print("  PHASE 2: LOAD v1.2.1 MODELS")
    print("=" * 70)

    # Load tox config
    tox_global_path = os.path.join(V12_BASE, "tox", "tox_calibration_global.json")
    if os.path.exists(tox_global_path):
        with open(tox_global_path) as f:
            tox_config = json.load(f)
        print(f"  [TOX] Loaded global config: a={tox_config['coeffs']['a']}, b={tox_config['coeffs']['b']}, c={tox_config['coeffs']['c']}")
    else:
        print(f"  [TOX] WARNING: No tox config found, using defaults")
        tox_config = {"coeffs": {"a": 0.6, "b": 0.97, "c": 0.43},
                      "thresholds": {"predator_tox_norm_ge": 0.90, "vacuum_tox_norm_ge": 0.75}}

    # Load RL feat cols
    tier1_path = os.path.join(V12_BASE, "features", "rl_feat_cols_tier1.json")
    if os.path.exists(tier1_path):
        with open(tier1_path) as f:
            rl_feat_config = json.load(f)
        rl_feat_cols = rl_feat_config["rl_feat_cols"]
        print(f"  [RL] Feature cols: {rl_feat_cols}")
    else:
        rl_feat_cols = ["xgb_risk", "vol_target_scale", "qqq_vol_20d_ann",
                        "qqq_mom_60d", "vix_z_20d", "risk_on_proxy",
                        "portfolio_vol_20d", "pnl_5d_sum_lag1", "dd_20d_lag1",
                        "tox_score_lag1"]
        print(f"  [RL] Using default feat cols")

    # Initialize modules
    rl_policy = RLBehaviorClonePolicy(
        model_dir=os.path.join(V12_BASE, "models"),
        fold=6, feat_cols=rl_feat_cols
    )
    chronos = ChronosToxicity(tox_config)
    cost_model = CostModel()
    irb_ctrl = IRBController()

    core = AresDecisionCore(rl_policy, chronos, cost_model, irb_ctrl)
    print("  [CORE] AresDecisionCore initialized with all modules")

    # ── PHASE 3: Load 2026 live data ──
    print("\n" + "=" * 70)
    print("  PHASE 3: LOAD 2026 LIVE DATA")
    print("=" * 70)

    live_path = "/home/ubuntu/data/backtest_daily_2026.csv"
    if not os.path.exists(live_path):
        live_path = "/home/ubuntu/ares_full_wf_daily_2026.csv"
    if not os.path.exists(live_path):
        print("  [ERROR] No 2026 live data found!")
        sys.exit(1)

    live_df = pd.read_csv(live_path)
    live_df["date"] = pd.to_datetime(live_df["date"])
    print(f"  [DATA] Live: {len(live_df)} days: {live_df['date'].min().date()} ~ {live_df['date'].max().date()}")
    print(f"  [DATA] Live columns: {list(live_df.columns)}")

    # Load v1.2.1 feature matrix for full context
    feat_path = os.path.join(V12_BASE, "features", "feature_matrix_2015_2026.parquet")
    if not os.path.exists(feat_path):
        feat_path = os.path.join(V12_BASE, "features", "feature_matrix_2015_2026.csv")
    if os.path.exists(feat_path):
        if feat_path.endswith(".parquet"):
            feat_df = pd.read_parquet(feat_path)
        else:
            feat_df = pd.read_csv(feat_path)
        feat_df["date"] = pd.to_datetime(feat_df["date"])
        # Filter to 2026 dates
        feat_2026 = feat_df[feat_df["date"] >= "2026-01-01"].copy().reset_index(drop=True)
        print(f"  [DATA] Feature matrix 2026: {len(feat_2026)} days")
        # Merge features into live_df
        live_df = live_df.merge(feat_2026, on="date", how="left", suffixes=("", "_feat"))
        # Fill any remaining NaN with 0
        live_df = live_df.fillna(0)
        print(f"  [DATA] Merged columns: {len(live_df.columns)}")
    else:
        print(f"  [WARN] No feature matrix found at {feat_path}")

    # Also load overlay data from v1.2.1
    overlay_path = os.path.join(V12_BASE, "overlays", "overlay_daily_2026.csv")
    if os.path.exists(overlay_path):
        ov_df = pd.read_csv(overlay_path)
        ov_df["date"] = pd.to_datetime(ov_df["date"])
        for col in ["xgb_risk", "vol_target_scale", "tox_score_lag1"]:
            if col in ov_df.columns and col not in live_df.columns:
                live_df = live_df.merge(ov_df[["date", col]], on="date", how="left")
        print(f"  [DATA] Overlay data merged")

    # ── PHASE 4: Run REPLAY ──
    print("\n" + "=" * 70)
    print("  PHASE 4: REPLAY MODE (2026)")
    print("=" * 70)

    replay_results, output = run_replay_2026(core, live_df)

    # Print replay results
    for k, v in replay_results.items():
        if isinstance(v, float):
            print(f"    {k}: {v:.4f}")
        else:
            print(f"    {k}: {v}")

    # Save decision log
    save_decision_log(output, live_df["date"].values, os.path.join(BASE_DIR, "logs", "decision_log_2026.csv"))

    # ── PHASE 5: PARITY GATES ──
    print("\n" + "=" * 70)
    print("  PHASE 5: PARITY GATES")
    print("=" * 70)

    gates = check_parity_gates(replay_results)
    for name, info in gates.items():
        if isinstance(info, dict) and "value" in info:
            status = "PASS" if info["pass"] else "FAIL"
            print(f"    {name}: {info['value']:.4f} (threshold: {info['threshold']}) {status}")
        elif name == "overall":
            print(f"    OVERALL: {'PASS' if info else 'FAIL'}")

    # ── PHASE 6: Save manifest + hashes ──
    print("\n" + "=" * 70)
    print("  PHASE 6: MANIFEST & HASHES")
    print("=" * 70)

    manifest = {
        "snapshot_id": SNAPSHOT_ID,
        "created_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": "1.3.1",
        "v12_source": V12_SNAPSHOT,
        "modules": {
            "rl_policy": "RLBehaviorClonePolicy (BC from v1.2.1)",
            "chronos": f"ChronosToxicity (a={tox_config['coeffs']['a']}, b={tox_config['coeffs']['b']}, c={tox_config['coeffs']['c']})",
            "cost_model": f"AsymmetricTWAP (k_down={CostModel.K_DOWN}, k_pred={CostModel.K_PRED})",
            "irb": "IRBController (PnL triggers: 5d<-2% -> 0.85, dd20d<-5% -> 0.75)"
        },
        "gates": gates,
        "replay_results": replay_results,
        "seed": SEED,
        "deterministic": True
    }
    write_json(os.path.join(BASE_DIR, "manifest.json"), manifest)

    # Module registry
    registry = {
        "schema_id": "ARES_V13_1_MODULE_REGISTRY_001",
        "modules": [
            {"name": "AlphaEngine", "version": "v87d", "source": "champion_returns_wide.csv"},
            {"name": "RegimeDetector", "version": "v9.0", "source": "QQQ vol+mom hysteresis"},
            {"name": "XGBRiskHead", "version": "v1.2.1", "source": f"{V12_BASE}/models/xgb_fold*.json"},
            {"name": "RLBehaviorClonePolicy", "version": "v1.3.1", "source": f"{V12_BASE}/models/rl_fold*.pt"},
            {"name": "ChronosToxicity", "version": "v1.3.1", "source": f"{V12_BASE}/tox/tox_calibration_global.json"},
            {"name": "CostModel", "version": "v1.3.1-asymmetric", "params": {"k_down": 0.20, "k_pred": 0.30, "k_vac_add": 2.0}},
            {"name": "IRBController", "version": "v1.3.1-pnl", "triggers": {"pnl_5d": -0.02, "dd_20d": -0.05}},
            {"name": "HedgeOverlay", "version": "v1.3.1-tox", "tox_bonus": {"PREDATOR": 0.10, "VACUUM": 0.05}}
        ]
    }
    write_json(os.path.join(BASE_DIR, "schemas", "module_registry.json"), registry)

    # SHA256
    all_files = []
    for root, dirs, files in os.walk(BASE_DIR):
        for fn in files:
            fp = os.path.join(root, fn)
            if "sha256" not in fn.lower():
                all_files.append(fp)

    sha_lines = []
    for fp in sorted(all_files):
        h = sha256_file(fp)
        rel = os.path.relpath(fp, BASE_DIR)
        sha_lines.append(f"sha256:{h}  {rel}")

    sha_path = os.path.join(BASE_DIR, "hashes", "sha256.txt")
    with open(sha_path, "w") as f:
        f.write("\n".join(sha_lines) + "\n")
    print(f"  [SSOT] SHA256 manifest: {len(sha_lines)} files")

    elapsed = time.time() - t0
    print(f"\n[DONE] {elapsed:.1f}s — All outputs: {BASE_DIR}")
    print("=" * 70)
    if gates.get("overall"):
        print("  ARES v1.3.1 CORE OVERLAY REDESIGN COMPLETE — ALL GATES PASS")
    else:
        print("  ARES v1.3.1 CORE OVERLAY REDESIGN COMPLETE — GATE FAILURE (REVIEW REQUIRED)")
    print("=" * 70)

if __name__ == "__main__":
    main()
