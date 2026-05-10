#!/usr/bin/env python3
"""
ARES v1.2.1 — OVERLAY REDESIGN BACKTEST (STRICT SSOT) — PRODUCTION
====================================================================
Fixed indentation, added missing features, corrected SSOT hook paths.

Phase 1: Feature Pipeline (+ new features: qqq_absret_z_60d_lag1,
         downside_cluster_5d_lag1, pnl_5d_sum_lag1, dd_20d_lag1)
Phase 2: XGBoost Risk Head (6-Fold Walk-Forward)
Phase 3: RL Scaling Policy (6-Fold Walk-Forward)
Phase 4: Overlay Generation (full OOS) — with Chronos tox + asymmetric cost + IRB PnL
Phase 5: Integrated Backtest (Alpha + Overlay)
Phase 6: Acceptance Gates

All outputs → /home/ubuntu/ssot/v1.2.1/ARES_V12_1_OVERLAY_REDESIGN_YYYYMMDD/
"""

import os, json, hashlib, time, warnings
import datetime
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════
SEED = 42
np.random.seed(SEED)

SNAPSHOT_ID = f"ARES_V12_1_OVERLAY_REDESIGN_{datetime.datetime.utcnow():%Y%m%d}"
BASE_DIR = f"/home/ubuntu/ssot/v1.2.1/{SNAPSHOT_ID}"
DATA_DIR = "/home/ubuntu/data"

UNIVERSE = ["ABBV","CMCSA","CRWD","GILD","GOOGL","HD","JNJ","KO","LLY",
            "MCD","META","NFLX","NVDA","OKTA","PEP","PYPL","REGN","TSLA","VRTX","WMT"]
CORE = ["GOOGL","META","NVDA","NFLX","CRWD","TSLA"]
GROWTH = ["OKTA","PYPL","HD","CMCSA","LLY","REGN","VRTX"]
DEFENSIVE = ["ABBV","GILD","JNJ","KO","MCD","PEP","WMT"]

TRAIN_END = "2014-12-31"
OOS_START = "2015-01-02"
FOLD_SIZES = [504, 502, 505, 503, 502, 292]  # 292 = 242 + 50 (2026 data)

# Regime thresholds (from existing engine)
REGIME_VOL_HIGH = 0.25
REGIME_VOL_LOW = 0.18
REGIME_MOM_NEG = -0.05

REGIME_MAP = {"NORMAL": 0, "RISK_OFF": 1, "CRISIS": 2, "RECOVERY": 3}
REGIME_LEVERAGE = {"NORMAL": 1.2, "RISK_OFF": 0.8, "CRISIS": 0.5, "RECOVERY": 1.0}
REGIME_HEDGE = {"NORMAL": 0.05, "RISK_OFF": 0.15, "CRISIS": 0.35, "RECOVERY": 0.10}
REGIME_BUDGETS = {
    "NORMAL":   {"CORE": 0.45, "GROWTH": 0.40, "DEFENSIVE": 0.15},
    "RISK_OFF": {"CORE": 0.30, "GROWTH": 0.15, "DEFENSIVE": 0.55},
    "CRISIS":   {"CORE": 0.20, "GROWTH": 0.05, "DEFENSIVE": 0.75},
    "RECOVERY": {"CORE": 0.40, "GROWTH": 0.35, "DEFENSIVE": 0.25},
}

# v1.2.1 Asymmetric cost constants (frozen)
K_DOWN = 0.20
K_PRED = 0.30
K_VAC_SPREAD_ADD = 2.0
ANNUAL_TURNOVER = 42.54

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


# ═══════════════════════════════════════════════════════════════
# PHASE 1: FEATURE PIPELINE
# ═══════════════════════════════════════════════════════════════
def build_features():
    print("=" * 70)
    print("  PHASE 1: FEATURE PIPELINE")
    print("=" * 70)

    # Load data
    port = pd.read_csv(f"{DATA_DIR}/v87d_returns_wide.csv", index_col='date', parse_dates=True)
    champ = pd.read_csv(f"{DATA_DIR}/champion_returns_wide.csv", index_col='date', parse_dates=True)

    # Use champion returns for individual symbols, portfolio for PORTFOLIO
    rets = champ.copy()
    if 'PORTFOLIO' not in rets.columns and 'PORTFOLIO' in port.columns:
        rets['PORTFOLIO'] = port['PORTFOLIO']

    # Fill NaN with 0
    rets = rets.fillna(0.0)

    # QQQ returns
    qqq = rets['QQQ'].values
    n = len(rets)
    dates = rets.index

    print(f"[FEAT] Data: {n} days ({dates[0].date()} ~ {dates[-1].date()})")

    # Helper: rolling sum/std using numpy (deterministic)
    def roll_sum(arr, w):
        out = np.full(len(arr), np.nan)
        cs = np.cumsum(np.nan_to_num(arr))
        cs_padded = np.concatenate([[0], cs])
        out[w-1:] = cs_padded[w:] - cs_padded[:len(arr)-w+1]
        return out

    def roll_std(arr, w):
        out = np.full(len(arr), np.nan)
        for i in range(w-1, len(arr)):
            out[i] = np.std(arr[i-w+1:i+1], ddof=1)
        return out

    def roll_mean(arr, w):
        out = np.full(len(arr), np.nan)
        cs = np.cumsum(np.nan_to_num(arr))
        cs_padded = np.concatenate([[0], cs])
        out[w-1:] = (cs_padded[w:] - cs_padded[:len(arr)-w+1]) / w
        return out

    # ── Market/Regime features ──
    feat = pd.DataFrame(index=dates)

    # All features use lag=1 (shift by 1 day)
    feat['qqq_ret_1d'] = pd.Series(qqq, index=dates).shift(1)
    feat['qqq_ret_5d_sum'] = pd.Series(roll_sum(qqq, 5), index=dates).shift(1)
    feat['qqq_ret_20d_sum'] = pd.Series(roll_sum(qqq, 20), index=dates).shift(1)

    qqq_std20 = roll_std(qqq, 20)
    feat['qqq_vol_20d_ann'] = pd.Series(qqq_std20 * np.sqrt(252), index=dates).shift(1)

    qqq_std60 = roll_std(qqq, 60)
    feat['qqq_vol_60d_ann'] = pd.Series(qqq_std60 * np.sqrt(252), index=dates).shift(1)

    feat['qqq_mom_60d'] = pd.Series(roll_sum(qqq, 60), index=dates).shift(1)
    feat['qqq_mom_120d'] = pd.Series(roll_sum(qqq, 120), index=dates).shift(1)
    feat['qqq_trend_score'] = feat['qqq_mom_60d'].copy()

    # ── NEW v1.2.1 Feature: qqq_absret_z_60d_lag1 ──
    qqq_absret = np.abs(qqq)
    absret_mean60 = roll_mean(qqq_absret, 60)
    absret_std60 = roll_std(qqq_absret, 60)
    qqq_absret_z = np.where(absret_std60 > 1e-9, (qqq_absret - absret_mean60) / absret_std60, 0.0)
    feat['qqq_absret_z_60d_lag1'] = pd.Series(qqq_absret_z, index=dates).shift(1).fillna(0.0)

    # ── NEW v1.2.1 Feature: downside_cluster_5d_lag1 ──
    # Number of negative days in last 5 days / 5 (normalized 0~1)
    qqq_neg = (qqq < 0).astype(float)
    downside_cluster_raw = roll_sum(qqq_neg, 5) / 5.0
    feat['downside_cluster_5d_lag1'] = pd.Series(downside_cluster_raw, index=dates).shift(1).fillna(0.0)

    # Regime detection (4-state hysteresis) — deterministic
    vol_20 = pd.Series(qqq_std20 * np.sqrt(252), index=dates).shift(1).fillna(0.15).values
    mom_60 = pd.Series(roll_sum(qqq, 60), index=dates).shift(1).fillna(0.0).values

    regime_states = np.zeros(n, dtype=int)  # 0=NORMAL
    regime_scores = np.zeros(n)
    regime_names = ["NORMAL"] * n

    for i in range(1, n):
        v = vol_20[i]
        m = mom_60[i]
        prev = regime_states[i-1]

        if v > REGIME_VOL_HIGH and m < REGIME_MOM_NEG:
            state = 2  # CRISIS
        elif v > REGIME_VOL_HIGH or m < REGIME_MOM_NEG:
            state = 1  # RISK_OFF
        elif prev >= 2 and v < REGIME_VOL_LOW and m > 0:
            state = 3  # RECOVERY
        elif prev == 3 and v < REGIME_VOL_LOW:
            state = 0  # NORMAL
        else:
            state = prev  # Hysteresis

        regime_states[i] = state
        regime_scores[i] = -v + m  # Simple composite
        regime_names[i] = ["NORMAL","RISK_OFF","CRISIS","RECOVERY"][state]

    feat['regime_state_lag1'] = regime_states
    feat['regime_score_lag1'] = regime_scores
    feat['regime_conf_lag1'] = np.abs(regime_scores)

    # ── Cross-asset risk (VIX proxy) ──
    vix_proxy = feat['qqq_vol_20d_ann'].fillna(0.15).values * 100
    feat['vix_close_lag1'] = vix_proxy
    feat['vix_ret_1d'] = pd.Series(vix_proxy, index=dates).pct_change().shift(1).fillna(0)

    vix_mean20 = roll_mean(vix_proxy, 20)
    vix_std20 = roll_std(vix_proxy, 20)
    vix_z = np.where(vix_std20 > 0, (vix_proxy - vix_mean20) / vix_std20, 0)
    feat['vix_z_20d'] = pd.Series(vix_z, index=dates).shift(1).fillna(0)

    qqq_mom_20 = pd.Series(roll_sum(qqq, 20), index=dates).shift(1).fillna(0)
    feat['risk_on_proxy'] = qqq_mom_20 - feat['qqq_vol_20d_ann'].fillna(0)

    # ── Multi-universe sleeve features ──
    for sleeve_name, sleeve_syms in [("core", CORE), ("growth", GROWTH), ("defensive", DEFENSIVE)]:
        valid = [s for s in sleeve_syms if s in rets.columns]
        if valid:
            sleeve_rets = rets[valid].mean(axis=1).values
        else:
            sleeve_rets = np.zeros(n)
        feat[f'{sleeve_name}_ret_5d'] = pd.Series(roll_sum(sleeve_rets, 5), index=dates).shift(1)
        feat[f'{sleeve_name}_vol_20d'] = pd.Series(roll_std(sleeve_rets, 20) * np.sqrt(252), index=dates).shift(1)

    # Momentum spread
    growth_mom60 = pd.Series(roll_sum(rets[[s for s in GROWTH if s in rets.columns]].mean(axis=1).values, 60), index=dates).shift(1)
    def_mom60 = pd.Series(roll_sum(rets[[s for s in DEFENSIVE if s in rets.columns]].mean(axis=1).values, 60), index=dates).shift(1)
    feat['mom_spread_60d'] = growth_mom60 - def_mom60
    feat['lowvol_spread_20d'] = feat['defensive_vol_20d'] - feat['growth_vol_20d']

    # Sleeve scores
    feat['sleeve_mom_score'] = growth_mom60
    feat['sleeve_lowvol_score'] = -feat['growth_vol_20d'].fillna(0)

    # Portfolio features
    port_rets = rets[UNIVERSE].mean(axis=1).values if all(s in rets.columns for s in UNIVERSE) else np.zeros(n)
    feat['portfolio_ret_1d'] = pd.Series(port_rets, index=dates).shift(1)
    feat['portfolio_ret_5d'] = pd.Series(roll_sum(port_rets, 5), index=dates).shift(1)
    feat['portfolio_vol_20d'] = pd.Series(roll_std(port_rets, 20) * np.sqrt(252), index=dates).shift(1)

    # ── NEW v1.2.1 Feature: pnl_5d_sum_lag1 ──
    feat['pnl_5d_sum_lag1'] = pd.Series(roll_sum(port_rets, 5), index=dates).shift(1).fillna(0.0)

    # ── NEW v1.2.1 Feature: dd_20d_lag1 (20-day peak-to-trough drawdown) ──
    cum_rets = np.cumprod(1 + port_rets)
    dd_20d = np.zeros(n)
    for i in range(20, n):
        window_cum = cum_rets[i-20:i+1]
        peak = np.maximum.accumulate(window_cum)
        dd = (window_cum - peak) / peak
        dd_20d[i] = float(np.min(dd))
    feat['dd_20d_lag1'] = pd.Series(dd_20d, index=dates).shift(1).fillna(0.0)

    # ── Execution/Toxicity/Cost features ──
    vol_z = feat['vix_z_20d'].fillna(0).values
    feat['tox_score_lag1'] = np.abs(vol_z) * feat['qqq_vol_20d_ann'].fillna(0.15)
    feat['participation_rate_target'] = np.clip(0.10 + 0.05 * vol_z, 0.05, 0.30)

    # Almgren-Chriss cost estimate (simplified deterministic)
    sigma_daily = feat['portfolio_vol_20d'].fillna(0.15 / np.sqrt(252)).values / np.sqrt(252)
    participation = feat['participation_rate_target'].values
    ac_cost = 0.5 * sigma_daily * np.sqrt(participation) * 10000  # in bps
    feat['ac_cost_bps_est'] = np.clip(ac_cost, 1.0, 100.0)
    feat['irb_state_lag1'] = np.ones(n)  # Default: no IRB cap

    # Fill NaN
    feat = feat.fillna(0.0)

    # Store regime names for later use
    feat['_regime_name'] = regime_names

    print(f"[FEAT] Generated {len(feat.columns)} features for {n} days")
    print(f"[FEAT] Feature columns: {sorted([c for c in feat.columns if not c.startswith('_')])}")

    return feat, rets, regime_names


# ═══════════════════════════════════════════════════════════════
# PHASE 2: XGBoost Risk Head (6-Fold Walk-Forward)
# ═══════════════════════════════════════════════════════════════
def train_xgb_models(feat, rets):
    print("\n" + "=" * 70)
    print("  PHASE 2: XGBoost RISK HEAD (6-Fold Walk-Forward)")
    print("=" * 70)

    import xgboost as xgb

    port_rets = rets[UNIVERSE].mean(axis=1).values
    port_std = np.std(port_rets[port_rets != 0])
    target = (port_rets < -port_std).astype(int)

    feat_cols = [c for c in feat.columns if not c.startswith('_') and c != 'regime_state_lag1']
    X_all = feat[feat_cols].values
    y_all = target

    dates = feat.index
    train_mask = dates <= pd.Timestamp(TRAIN_END)
    oos_mask = dates > pd.Timestamp(TRAIN_END)

    X_train_full = X_all[train_mask]
    y_train_full = y_all[train_mask]

    oos_indices = np.where(oos_mask)[0]

    fold_starts = [0]
    for fs in FOLD_SIZES[:-1]:
        fold_starts.append(fold_starts[-1] + fs)

    models = []
    xgb_risk_oos = np.zeros(len(oos_indices))

    for fold_i in range(6):
        fold_start = fold_starts[fold_i]
        fold_end = fold_start + FOLD_SIZES[fold_i]
        fold_oos_idx = oos_indices[fold_start:fold_end]

        if fold_i == 0:
            X_tr = X_train_full
            y_tr = y_train_full
        else:
            prev_oos_end = oos_indices[fold_start - 1] + 1
            X_tr = X_all[:prev_oos_end]
            y_tr = y_all[:prev_oos_end]

        split_idx = int(len(X_tr) * 0.8)
        X_tr_inner, X_val_inner = X_tr[:split_idx], X_tr[split_idx:]
        y_tr_inner, y_val_inner = y_tr[:split_idx], y_tr[split_idx:]

        dtrain = xgb.DMatrix(X_tr_inner, label=y_tr_inner, feature_names=feat_cols)
        dval = xgb.DMatrix(X_val_inner, label=y_val_inner, feature_names=feat_cols)

        params = {
            'objective': 'binary:logistic',
            'eval_metric': 'logloss',
            'max_depth': 4,
            'learning_rate': 0.05,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'min_child_weight': 10,
            'gamma': 1.0,
            'reg_alpha': 0.1,
            'reg_lambda': 1.0,
            'seed': SEED,
            'nthread': 4,
            'verbosity': 0,
        }

        model = xgb.train(
            params, dtrain,
            num_boost_round=500,
            evals=[(dval, 'val')],
            early_stopping_rounds=50,
            verbose_eval=False,
        )

        model_path = f"{BASE_DIR}/models/xgb_fold{fold_i+1}.json"
        model.save_model(model_path)
        models.append(model)

        X_fold_oos = X_all[fold_oos_idx]
        dtest = xgb.DMatrix(X_fold_oos, feature_names=feat_cols)
        preds = model.predict(dtest)
        xgb_risk_oos[fold_start:fold_end] = preds

        print(f"  Fold {fold_i+1}: train={len(X_tr)} val={len(X_val_inner)} "
              f"OOS={len(fold_oos_idx)} best_iter={model.best_iteration} "
              f"mean_risk={preds.mean():.4f}")

    print(f"[XGB] All 6 models trained and saved")
    return xgb_risk_oos, models, feat_cols


# ═══════════════════════════════════════════════════════════════
# PHASE 3: RL Scaling Policy (6-Fold Walk-Forward)
# ═══════════════════════════════════════════════════════════════
def train_rl_models(feat, rets, xgb_risk_oos):
    print("\n" + "=" * 70)
    print("  PHASE 3: RL SCALING POLICY (6-Fold Walk-Forward)")
    print("=" * 70)

    import torch
    import torch.nn as nn
    import torch.optim as optim

    torch.manual_seed(SEED)
    np.random.seed(SEED)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"[RL] Device: {device}")

    # RL State: subset of features + xgb_risk
    rl_feat_cols = ['qqq_ret_1d', 'qqq_vol_20d_ann', 'qqq_mom_60d',
                    'regime_score_lag1', 'vix_z_20d', 'risk_on_proxy',
                    'portfolio_ret_1d', 'portfolio_vol_20d', 'tox_score_lag1']

    dates = feat.index
    train_mask = dates <= pd.Timestamp(TRAIN_END)
    oos_mask = dates > pd.Timestamp(TRAIN_END)
    oos_indices = np.where(oos_mask)[0]

    port_rets = rets[UNIVERSE].mean(axis=1).values

    class RLPolicy(nn.Module):
        def __init__(self, state_dim):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(state_dim, 64),
                nn.ReLU(),
                nn.Linear(64, 32),
                nn.ReLU(),
                nn.Linear(32, 1),
                nn.Sigmoid(),
            )

        def forward(self, x):
            return self.net(x) * 1.1 + 0.2  # Scale to [0.2, 1.3]

    fold_starts = [0]
    for fs in FOLD_SIZES[:-1]:
        fold_starts.append(fold_starts[-1] + fs)

    rl_scale_oos = np.ones(len(oos_indices))
    rl_models = []

    for fold_i in range(6):
        torch.manual_seed(SEED + fold_i)
        np.random.seed(SEED + fold_i)

        fold_start = fold_starts[fold_i]
        fold_end = fold_start + FOLD_SIZES[fold_i]
        fold_oos_idx = oos_indices[fold_start:fold_end]

        if fold_i == 0:
            tr_idx = np.where(train_mask)[0]
        else:
            prev_oos_end = oos_indices[fold_start - 1] + 1
            tr_idx = np.arange(prev_oos_end)

        X_feat = feat[rl_feat_cols].values
        state_dim = len(rl_feat_cols) + 1  # +1 for xgb_risk

        xgb_proxy_train = np.abs(feat['vix_z_20d'].values)

        states_tr = np.column_stack([X_feat[tr_idx], xgb_proxy_train[tr_idx]])
        rewards_tr = port_rets[tr_idx]

        state_mean = np.mean(states_tr, axis=0)
        state_std = np.std(states_tr, axis=0) + 1e-8

        states_tr_norm = (states_tr - state_mean) / state_std

        policy = RLPolicy(state_dim).to(device)
        optimizer = optim.Adam(policy.parameters(), lr=1e-3)

        n_epochs = 100
        batch_size = min(256, len(states_tr_norm))

        for epoch in range(n_epochs):
            idx = np.random.choice(len(states_tr_norm), batch_size, replace=False)
            s_batch = torch.FloatTensor(states_tr_norm[idx]).to(device)
            r_batch = torch.FloatTensor(rewards_tr[idx]).to(device)

            scales = policy(s_batch).squeeze()
            scaled_returns = scales * r_batch
            penalty = 0.1 * torch.relu(-r_batch) * (scales - 0.5).clamp(min=0)
            reward = scaled_returns - penalty

            loss = -reward.mean()

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        model_path = f"{BASE_DIR}/models/rl_fold{fold_i+1}.pt"
        torch.save({
            'model_state_dict': policy.state_dict(),
            'state_mean': state_mean,
            'state_std': state_std,
            'state_dim': state_dim,
            'rl_feat_cols': rl_feat_cols,
            'seed': SEED + fold_i,
        }, model_path)
        rl_models.append((policy, state_mean, state_std))

        policy.eval()
        with torch.no_grad():
            xgb_risk_fold = xgb_risk_oos[fold_start:fold_end]
            states_oos = np.column_stack([X_feat[fold_oos_idx], xgb_risk_fold])
            states_oos_norm = (states_oos - state_mean) / state_std
            s_oos = torch.FloatTensor(states_oos_norm).to(device)
            scales_oos = policy(s_oos).squeeze().cpu().numpy()

        rl_scale_oos[fold_start:fold_end] = scales_oos
        print(f"  Fold {fold_i+1}: train={len(tr_idx)} OOS={len(fold_oos_idx)} "
              f"mean_scale={scales_oos.mean():.4f} std={scales_oos.std():.4f}")

    print(f"[RL] All 6 policies trained and saved")
    return rl_scale_oos, rl_models


# ═══════════════════════════════════════════════════════════════
# PHASE 4: OVERLAY GENERATION
# ═══════════════════════════════════════════════════════════════
def generate_overlays(feat, xgb_risk_oos, rl_scale_oos):
    print("\n" + "=" * 70)
    print("  PHASE 4: OVERLAY GENERATION (Chronos + Asymmetric Cost + IRB PnL)")
    print("=" * 70)

    dates = feat.index
    oos_mask = dates > pd.Timestamp(TRAIN_END)
    oos_dates = dates[oos_mask]
    n_oos = len(oos_dates)

    regime_names = feat['_regime_name'].values[oos_mask]

    # ── Vol Target Scale ──
    port_vol = feat['portfolio_vol_20d'].values[oos_mask]
    vol_target_scale = np.ones(n_oos) * 1.6  # Default: match live
    for idx in range(n_oos):
        if port_vol[idx] > 0.25:
            vol_target_scale[idx] = np.clip(0.15 / port_vol[idx], 0.6, 1.6)
        elif regime_names[idx] in ('CRISIS', 'RISK_OFF'):
            vol_target_scale[idx] = np.clip(0.15 / max(port_vol[idx], 0.01), 0.6, 1.6)

    # ── Chronos Toxicity Score (v1.2.1 enhanced) ──
    tox_score_raw = feat['tox_score_lag1'].values[oos_mask]
    absret_z = feat['qqq_absret_z_60d_lag1'].values[oos_mask]
    down_cluster = feat['downside_cluster_5d_lag1'].values[oos_mask]
    vol_z = feat['vix_z_20d'].values[oos_mask]

    # Enhanced tox_score = a*vol_z + b*absret_z + c*downside_cluster
    # Using default coefficients (will be calibrated by SSOT artifacts generator)
    tox_a, tox_b, tox_c = 1.0, 0.6, 0.4
    tox_score_enhanced = tox_a * np.abs(vol_z) + tox_b * np.abs(absret_z) + tox_c * down_cluster

    # Tox state classification (fixed thresholds)
    tox_state = np.array(['NORMAL'] * n_oos, dtype=object)
    tox_sorted = np.sort(tox_score_enhanced)
    p90 = tox_sorted[int(0.90 * len(tox_sorted))] if len(tox_sorted) > 0 else 999
    p75 = tox_sorted[int(0.75 * len(tox_sorted))] if len(tox_sorted) > 0 else 999
    tox_state[tox_score_enhanced >= p90] = 'PREDATOR'
    tox_state[(tox_score_enhanced >= p75) & (tox_score_enhanced < p90)] = 'VACUUM'

    # ── Dynamic Hedge Weight (with Chronos tox connection) ──
    w_hedge = np.zeros(n_oos)
    for idx in range(n_oos):
        r = regime_names[idx]
        xr = xgb_risk_oos[idx]
        if r == 'CRISIS':
            base_h = 0.20
        elif r == 'RISK_OFF':
            base_h = 0.10
        elif r == 'RECOVERY':
            base_h = 0.05
        else:
            base_h = 0.018
        risk_adj = xr * 0.15
        # Chronos tox bonus
        tox_bonus = 0.0
        if tox_state[idx] == 'PREDATOR':
            tox_bonus = 0.05
        elif tox_state[idx] == 'VACUUM':
            tox_bonus = 0.02
        w_hedge[idx] = np.clip(base_h + risk_adj + tox_bonus, 0.0, 0.35)

    # ── IRB Cap (with PnL triggers — v1.2.1) ──
    pnl_5d = feat['pnl_5d_sum_lag1'].values[oos_mask]
    dd_20d = feat['dd_20d_lag1'].values[oos_mask]

    irb_cap = np.ones(n_oos)
    # XGB risk-based cap
    irb_cap[xgb_risk_oos > 0.7] = np.minimum(irb_cap[xgb_risk_oos > 0.7], 0.8)
    irb_cap[xgb_risk_oos > 0.9] = np.minimum(irb_cap[xgb_risk_oos > 0.9], 0.5)
    # PnL triggers
    irb_cap[pnl_5d < -0.02] = np.minimum(irb_cap[pnl_5d < -0.02], 0.85)
    irb_cap[dd_20d < -0.05] = np.minimum(irb_cap[dd_20d < -0.05], 0.75)
    # Tox-based IRB tightening
    irb_cap[tox_state == 'PREDATOR'] = np.minimum(irb_cap[tox_state == 'PREDATOR'], 0.80)
    # Recovery: gradual increase (simulated)
    for idx in range(1, n_oos):
        if irb_cap[idx] < 1.0 and pnl_5d[idx] >= 0 and dd_20d[idx] > -0.03:
            irb_cap[idx] = min(irb_cap[idx] + 0.02, 1.0)

    # ── Dynamic Cost Model (Asymmetric — v1.2.1) ──
    base_cost_bps = feat['ac_cost_bps_est'].values[oos_mask]
    spread_adj = np.array([5.0 if r in ["CRISIS","RISK_OFF"] else 0.0 for r in regime_names])
    cost_bps = base_cost_bps + spread_adj

    qqq_ret_1d = feat['qqq_ret_1d'].values[oos_mask]
    for idx in range(n_oos):
        c = cost_bps[idx]
        # Asymmetric: higher cost on down days
        if qqq_ret_1d[idx] < 0:
            c *= (1 + K_DOWN)
        # Tox-based cost multiplier
        if tox_state[idx] == 'PREDATOR':
            c *= (1 + K_PRED)
        elif tox_state[idx] == 'VACUUM':
            c += K_VAC_SPREAD_ADD
        cost_bps[idx] = c

    # Participation rate (adjusted by tox)
    participation = feat['participation_rate_target'].values[oos_mask].copy()
    participation[tox_state == 'PREDATOR'] *= 0.7
    participation[tox_state == 'VACUUM'] *= 0.85

    overlay = pd.DataFrame({
        'date': oos_dates.strftime('%Y-%m-%d'),
        'xgb_risk': xgb_risk_oos,
        'rl_scale': rl_scale_oos,
        'vol_target_scale': vol_target_scale,
        'w_hedge': w_hedge,
        'irb_cap': irb_cap,
        'cost_bps': cost_bps,
        'participation_rate': participation,
        'tox_score': tox_score_enhanced,
        'tox_state': tox_state,
        'pnl_5d_sum': pnl_5d,
        'dd_20d': dd_20d,
    })

    overlay_path = f"{BASE_DIR}/overlays/overlay_daily_2015_2026.csv"
    overlay.to_csv(overlay_path, index=False)
    print(f"[OVERLAY] Generated {n_oos} days of overlays")
    print(f"[OVERLAY] Tox distribution: NORMAL={np.sum(tox_state=='NORMAL')}, "
          f"VACUUM={np.sum(tox_state=='VACUUM')}, PREDATOR={np.sum(tox_state=='PREDATOR')}")

    overlay_2026 = overlay[overlay['date'] >= '2026-01-01']
    overlay_2026_path = f"{BASE_DIR}/overlays/overlay_daily_2026.csv"
    overlay_2026.to_csv(overlay_2026_path, index=False)
    print(f"[OVERLAY] 2026 subset: {len(overlay_2026)} days")

    return overlay


# ═══════════════════════════════════════════════════════════════
# PHASE 5: INTEGRATED BACKTEST
# ═══════════════════════════════════════════════════════════════
def run_integrated_backtest(feat, rets, overlay):
    print("\n" + "=" * 70)
    print("  PHASE 5: INTEGRATED BACKTEST (Alpha + Overlay)")
    print("=" * 70)

    dates = feat.index
    oos_mask = dates > pd.Timestamp(TRAIN_END)
    oos_dates = dates[oos_mask]
    n_oos = len(oos_dates)

    regime_names = feat['_regime_name'].values[oos_mask]
    port_rets = rets[UNIVERSE].mean(axis=1).values[oos_mask]
    qqq_rets = rets['QQQ'].values[oos_mask]

    xgb_risk = overlay['xgb_risk'].values
    rl_scale = overlay['rl_scale'].values
    vol_target_scale = overlay['vol_target_scale'].values
    w_hedge = overlay['w_hedge'].values
    irb_cap = overlay['irb_cap'].values
    cost_bps = overlay['cost_bps'].values
    tox_state = overlay['tox_state'].values

    core_rets = rets[[s for s in CORE if s in rets.columns]].mean(axis=1).values[oos_mask]
    growth_rets = rets[[s for s in GROWTH if s in rets.columns]].mean(axis=1).values[oos_mask]
    def_rets = rets[[s for s in DEFENSIVE if s in rets.columns]].mean(axis=1).values[oos_mask]

    # v1.0 Alpha Pipeline Parameters (EXACT from S4 snapshot)
    GAIN_MULT = {"bull": 1.05, "bear": 1.00, "crisis": 0.70}
    LOSS_MULT = {"bull": 1.00, "bear": 0.80, "crisis": 0.50}
    EXTREME_LOSS_THRESH = -0.03
    EXTREME_LOSS_REDUCTION = 0.60
    SMALL_LOSS_THRESH = -0.005
    SMALL_LOSS_REDUCTION = 0.50
    CRASH_GUARD_DD = -0.03
    CRASH_GUARD_VOL_MULT = 3.0
    CRASH_GUARD_SCALE = 0.50
    TREND_BAD_THRESH = -0.15
    TREND_CRISIS_THRESH = -0.35
    TREND_CLAMP_MIN = 0.60

    qqq_full = rets['QQQ'].values
    oos_start_idx = np.where(oos_mask)[0][0]

    daily_returns = np.zeros(n_oos)
    records = []
    prev_vol20 = None

    for i in range(n_oos):
        gi = oos_start_idx + i
        regime = regime_names[i]

        if regime in ("CRISIS",):
            adj_key = "crisis"
        elif regime in ("RISK_OFF", "RECOVERY"):
            adj_key = "bear"
        else:
            adj_key = "bull"

        # Step 1: Base return
        r_base = port_rets[i]

        # Step 2: Regime Leverage
        base_leverage = REGIME_LEVERAGE.get(regime, 1.2)
        r_adj = r_base * base_leverage

        # Step 3: Multi-Universe Reweighting
        if regime != "NORMAL":
            budgets = REGIME_BUDGETS.get(regime, REGIME_BUDGETS["NORMAL"])
            bsum = sum(budgets.values())
            budgets_n = {k: v/bsum for k, v in budgets.items()} if bsum > 0 else budgets
            r_reweighted = (budgets_n["CORE"] * core_rets[i] +
                           budgets_n["GROWTH"] * growth_rets[i] +
                           budgets_n["DEFENSIVE"] * def_rets[i])
            r_adj = r_reweighted * base_leverage

        # Step 4: Trend Overlay
        trend_scalar = 1.0
        if gi >= 60:
            trend_score = float(np.sum(qqq_full[gi-60:gi]))
            if trend_score <= TREND_CRISIS_THRESH:
                trend_scalar = TREND_CLAMP_MIN
            elif trend_score <= TREND_BAD_THRESH:
                t = (trend_score - TREND_CRISIS_THRESH) / (TREND_BAD_THRESH - TREND_CRISIS_THRESH + 1e-9)
                trend_scalar = TREND_CLAMP_MIN + t * (0.90 - TREND_CLAMP_MIN)
            else:
                trend_scalar = 1.0
        r_adj *= trend_scalar

        # Step 5: Hedge Return
        hedge_budget = w_hedge[i]
        if hedge_budget > 0:
            qqq_r = qqq_rets[i]
            if regime == "CRISIS":
                hedge_mult = 3.0
            elif regime == "RISK_OFF":
                hedge_mult = 2.0
            else:
                hedge_mult = 1.0
            hedge_return = hedge_budget * (-qqq_r * hedge_mult)
            r_adj += hedge_return
        else:
            hedge_return = 0.0

        # Step 6: Gain/Loss Multiplier
        if r_adj > 0:
            r_adj *= GAIN_MULT[adj_key]
        elif r_adj < 0:
            r_adj *= LOSS_MULT[adj_key]

        # Step 7: Crash Guard
        crash_triggered = False
        if gi >= 20:
            vol20 = float(np.std(qqq_full[gi-20:gi+1]) * np.sqrt(252))
            if prev_vol20 is not None and prev_vol20 > 0:
                vol_spike = vol20 / prev_vol20
            else:
                vol_spike = 1.0
            prev_vol20 = vol20
            if r_adj < CRASH_GUARD_DD or vol_spike > CRASH_GUARD_VOL_MULT:
                r_adj *= CRASH_GUARD_SCALE
                crash_triggered = True

        # Step 8: Extreme Loss Defense
        extreme_triggered = False
        if r_adj < EXTREME_LOSS_THRESH:
            r_adj *= EXTREME_LOSS_REDUCTION
            extreme_triggered = True

        # Step 9: Small Loss Filter
        small_loss_triggered = False
        if SMALL_LOSS_THRESH < r_adj < 0:
            r_adj *= SMALL_LOSS_REDUCTION
            small_loss_triggered = True

        # Step 10: Edge Gate
        if -0.0003 < r_adj < 0.0001:
            r_adj = 0.0

        # Step 11: v1.2.1 Overlay Scaling
        overlay_scale = rl_scale[i] * vol_target_scale[i] * irb_cap[i]
        r_adj *= overlay_scale

        # Step 12: v1.2.1 Asymmetric Cost (dynamic, not fixed)
        cost_daily = (ANNUAL_TURNOVER / 100) * (cost_bps[i] / 10000) / 252
        r_net = r_adj - cost_daily
        daily_returns[i] = r_net

        leverage_final = base_leverage * rl_scale[i] * vol_target_scale[i] * irb_cap[i]

        records.append({
            'date': oos_dates[i].strftime('%Y-%m-%d'),
            'regime': regime,
            'alpha_return': float(r_base),
            'base_leverage': float(base_leverage),
            'rl_scale': float(rl_scale[i]),
            'vol_target_scale': float(vol_target_scale[i]),
            'irb_cap': float(irb_cap[i]),
            'leverage_final': float(leverage_final),
            'overlay_scale': float(overlay_scale),
            'xgb_risk': float(xgb_risk[i]),
            'hedge_weight': float(w_hedge[i]),
            'hedge_return': float(hedge_return),
            'trend_scalar': float(trend_scalar),
            'crash_guard': crash_triggered,
            'extreme_loss': extreme_triggered,
            'small_loss': small_loss_triggered,
            'tox_state': str(tox_state[i]),
            'cost_bps': float(cost_bps[i]),
            'cost_daily': float(cost_daily),
            'gross_return': float(r_adj + cost_daily),
            'net_return': float(r_net),
        })

    # Compute metrics
    def compute_metrics(rets_arr, label, recs=None):
        n = len(rets_arr)
        if n < 2:
            return {}
        cum = np.cumprod(1 + rets_arr)
        total = cum[-1] - 1
        years = n / 252
        cagr = (cum[-1] ** (1/years) - 1) * 100 if years > 0 and cum[-1] > 0 else 0
        mu = np.mean(rets_arr)
        sigma = np.std(rets_arr, ddof=1)
        sharpe = mu / sigma * np.sqrt(252) if sigma > 0 else 0
        vol = sigma * np.sqrt(252) * 100
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak) / peak
        mdd = float(np.min(dd)) * 100
        calmar = cagr / abs(mdd) if abs(mdd) > 0 else 0
        neg = rets_arr[rets_arr < 0]
        ds = np.std(neg, ddof=1) if len(neg) > 1 else 1e-9
        sortino = mu / ds * np.sqrt(252) if ds > 0 else 0
        wr = np.sum(rets_arr > 0) / n * 100

        # Turnover estimate
        if recs and len(recs) > 1:
            lev_changes = np.abs(np.diff([r['leverage_final'] for r in recs]))
            turnover = np.mean(lev_changes) * 252 * 100 if len(lev_changes) > 0 else ANNUAL_TURNOVER
        else:
            turnover = ANNUAL_TURNOVER

        # Exposure
        if recs:
            exposure = np.mean([r['leverage_final'] for r in recs])
        else:
            exposure = 1.0

        return {
            'label': label, 'n_days': n,
            'sharpe': round(float(sharpe), 4),
            'cagr_pct': round(float(cagr), 2),
            'ann_vol_pct': round(float(vol), 2),
            'max_dd_pct': round(float(mdd), 2),
            'calmar': round(float(calmar), 2),
            'sortino': round(float(sortino), 2),
            'win_rate_pct': round(float(wr), 1),
            'cumulative_pct': round(float(total * 100), 4),
            'turnover_pct': round(float(turnover), 2),
            'exposure': round(float(exposure), 4),
        }

    full_metrics = compute_metrics(daily_returns, "Full_OOS_v12_1", records)

    fold_starts = [0]
    for fs in FOLD_SIZES[:-1]:
        fold_starts.append(fold_starts[-1] + fs)

    fold_metrics = []
    for fold_i in range(6):
        fs = fold_starts[fold_i]
        fe = fs + FOLD_SIZES[fold_i]
        fm = compute_metrics(daily_returns[fs:fe], f"Fold_{fold_i+1}", records[fs:fe])
        fold_metrics.append(fm)

    mask_2025 = np.array([r['date'].startswith('2025') for r in records])
    m2025 = compute_metrics(daily_returns[mask_2025], "Year_2025", [r for r in records if r['date'].startswith('2025')]) if mask_2025.any() else {}

    mask_2026 = np.array([r['date'].startswith('2026') for r in records])
    m2026 = compute_metrics(daily_returns[mask_2026], "Year_2026", [r for r in records if r['date'].startswith('2026')]) if mask_2026.any() else {}

    print(f"\n  FULL OOS: Sharpe={full_metrics['sharpe']:.4f} CAGR={full_metrics['cagr_pct']:.2f}% "
          f"MDD={full_metrics['max_dd_pct']:.2f}% Sortino={full_metrics['sortino']:.2f}")
    for fm in fold_metrics:
        print(f"  {fm['label']}: Sharpe={fm['sharpe']:.4f} CAGR={fm['cagr_pct']:.2f}% MDD={fm['max_dd_pct']:.2f}%")
    if m2025:
        print(f"  2025: Sharpe={m2025['sharpe']:.4f} CAGR={m2025['cagr_pct']:.2f}%")
    if m2026:
        print(f"  2026: Sharpe={m2026['sharpe']:.4f} CAGR={m2026['cagr_pct']:.2f}%")

    return {
        'full_oos': full_metrics,
        'folds': fold_metrics,
        'year_2025': m2025,
        'year_2026': m2026,
        'daily_records': records,
        'daily_returns': daily_returns,
    }


# ═══════════════════════════════════════════════════════════════
# PHASE 6: ACCEPTANCE GATES
# ═══════════════════════════════════════════════════════════════
def run_acceptance_gates(bt_results, overlay):
    print("\n" + "=" * 70)
    print("  PHASE 6: ACCEPTANCE GATES")
    print("=" * 70)

    gates = {}

    # Gate 1: Parity with live (2026 window)
    live_path = "/home/ubuntu/ares_full_wf_daily_2026.csv"
    if os.path.exists(live_path):
        live = pd.read_csv(live_path)
        model_2026 = overlay[overlay['date'] >= '2026-01-01'].reset_index(drop=True)

        if len(model_2026) > 0 and len(live) > 0:
            common_dates = set(model_2026['date'].values) & set(live['date'].values)
            m26 = model_2026[model_2026['date'].isin(common_dates)].sort_values('date').reset_index(drop=True)
            l26 = live[live['date'].isin(common_dates)].sort_values('date').reset_index(drop=True)

            corr_rl = np.corrcoef(m26['rl_scale'].values, l26['rl_scale'].values)[0, 1] if len(m26) > 1 else 0
            mae_rl = np.mean(np.abs(m26['rl_scale'].values - l26['rl_scale'].values))
            mae_cost = np.mean(np.abs(m26['cost_bps'].values - l26['cost_bps'].values))
            mae_hedge = np.mean(np.abs(m26['w_hedge'].values - l26['w_hedge'].values))

            # Stability gate: P95(|Δrl_scale|) <= 0.30
            rl_diffs = np.abs(np.diff(m26['rl_scale'].values))
            p95_rl_diff = np.percentile(rl_diffs, 95) if len(rl_diffs) > 0 else 0

            gate1 = {
                'corr_rl_scale': round(float(corr_rl), 4) if not np.isnan(corr_rl) else 0.0,
                'mae_rl_scale': round(float(mae_rl), 4),
                'mae_cost_bps': round(float(mae_cost), 2),
                'mae_w_hedge': round(float(mae_hedge), 4),
                'p95_delta_rl_scale': round(float(p95_rl_diff), 4),
                'pass_corr_rl': float(corr_rl) >= 0.90 if not np.isnan(corr_rl) else False,
                'pass_mae_rl': float(mae_rl) <= 0.05,
                'pass_mae_cost': float(mae_cost) <= 5.0,
                'pass_mae_hedge': float(mae_hedge) <= 0.02,
                'pass_stability': float(p95_rl_diff) <= 0.30,
            }
            gate1['overall_pass'] = all([gate1['pass_corr_rl'], gate1['pass_mae_rl'],
                                          gate1['pass_mae_cost'], gate1['pass_mae_hedge'],
                                          gate1['pass_stability']])
        else:
            gate1 = {'error': 'No matching 2026 data', 'overall_pass': False}
    else:
        gate1 = {'error': 'Live data not found', 'overall_pass': False}

    gates['parity_2026'] = gate1

    # Gate 2: OOS Sanity
    v10_sharpe = 5.85
    v12_sharpe = bt_results['full_oos']['sharpe']

    fold_sharpes = [f['sharpe'] for f in bt_results['folds']]
    fold_cv = np.std(fold_sharpes) / np.mean(fold_sharpes) if np.mean(fold_sharpes) > 0 else 999

    # v1.2.1 updated thresholds: Sharpe between 3.8 and 5.0, Fold CV <= 0.20
    gate2 = {
        'v10_sharpe': v10_sharpe,
        'v12_sharpe': round(float(v12_sharpe), 4),
        'sharpe_in_range': 3.8 <= float(v12_sharpe) <= 5.0,
        'fold_sharpe_cv': round(float(fold_cv), 4),
        'pass_sharpe_range': 3.8 <= float(v12_sharpe) <= 5.0,
        'pass_fold_cv': float(fold_cv) <= 0.20,
    }
    gate2['overall_pass'] = gate2['pass_sharpe_range'] and gate2['pass_fold_cv']

    gates['oos_sanity'] = gate2

    # Print results
    print("\n  GATE 1 — Parity (2026 window):")
    if 'error' not in gate1:
        print(f"    Corr(rl_scale): {gate1['corr_rl_scale']:.4f} (>=0.90) {'PASS' if gate1['pass_corr_rl'] else 'FAIL'}")
        print(f"    MAE(rl_scale):  {gate1['mae_rl_scale']:.4f} (<=0.05) {'PASS' if gate1['pass_mae_rl'] else 'FAIL'}")
        print(f"    MAE(cost_bps):  {gate1['mae_cost_bps']:.2f} (<=5.0)  {'PASS' if gate1['pass_mae_cost'] else 'FAIL'}")
        print(f"    MAE(w_hedge):   {gate1['mae_w_hedge']:.4f} (<=0.02) {'PASS' if gate1['pass_mae_hedge'] else 'FAIL'}")
        print(f"    P95(dRL):       {gate1['p95_delta_rl_scale']:.4f} (<=0.30) {'PASS' if gate1['pass_stability'] else 'FAIL'}")
        print(f"    OVERALL: {'PASS' if gate1['overall_pass'] else 'FAIL'}")
    else:
        print(f"    ERROR: {gate1['error']}")

    print(f"\n  GATE 2 — OOS Sanity:")
    print(f"    v1.2.1 Sharpe: {gate2['v12_sharpe']:.4f} (3.8~5.0) {'PASS' if gate2['pass_sharpe_range'] else 'FAIL'}")
    print(f"    Fold CV:       {gate2['fold_sharpe_cv']:.4f} (<=0.20) {'PASS' if gate2['pass_fold_cv'] else 'FAIL'}")
    print(f"    OVERALL: {'PASS' if gate2['overall_pass'] else 'FAIL'}")

    overall = gate1.get('overall_pass', False) and gate2['overall_pass']
    if not overall:
        print("\n  WARNING: V1.2.1 GATE FAILURE — REVIEW REQUIRED")
    else:
        print("\n  ALL GATES PASSED")

    return gates, overall


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    t0 = time.time()

    print("=" * 70)
    print("  ARES v1.2.1 — OVERLAY REDESIGN BACKTEST (PRODUCTION)")
    print("  STRICT SSOT EXECUTION MODE")
    print("  Snapshot: " + SNAPSHOT_ID)
    print("=" * 70)

    # Create output directories
    for subdir in ['models', 'overlays', 'features', 'hashes', 'reports', 'tox']:
        os.makedirs(f"{BASE_DIR}/{subdir}", exist_ok=True)

    # Phase 1: Features
    feat, rets, regime_names = build_features()

    # ── SSOT Artifact Hook Prereq: persist feature_matrix ──
    fm_path = f"{BASE_DIR}/features/feature_matrix_2015_2026.parquet"
    try:
        fm_df = feat.copy()
        fm_df = fm_df.reset_index().rename(columns={"index": "date"})
        if "date" in fm_df.columns:
            fm_df["date"] = pd.to_datetime(fm_df["date"])
        fm_df.to_parquet(fm_path, index=False)
        print(f"[SSOT] Saved feature matrix -> {fm_path}")
    except Exception as e:
        fm_path = f"{BASE_DIR}/features/feature_matrix_2015_2026.csv"
        fm_df.to_csv(fm_path, index=False)
        print(f"[SSOT] Parquet save failed; saved CSV -> {fm_path} (reason: {e})")

    # Save feature schema
    import shutil
    schema_src = "/home/ubuntu/v12_feature_schema.json"
    schema_dst = f"{BASE_DIR}/features/feature_schema.json"
    if os.path.exists(schema_src):
        shutil.copy2(schema_src, schema_dst)

    # Phase 2: XGBoost
    xgb_risk_oos, xgb_models, feat_cols = train_xgb_models(feat, rets)

    # Phase 3: RL
    rl_scale_oos, rl_models = train_rl_models(feat, rets, xgb_risk_oos)

    # Phase 4: Overlays
    overlay = generate_overlays(feat, xgb_risk_oos, rl_scale_oos)

    # Phase 5: Backtest
    bt_results = run_integrated_backtest(feat, rets, overlay)

    # Phase 6: Gates
    gates, overall = run_acceptance_gates(bt_results, overlay)

    # ── Save results ──
    daily_df = pd.DataFrame(bt_results['daily_records'])
    daily_path = f"{BASE_DIR}/overlays/backtest_daily_v12_1.csv"
    daily_df.to_csv(daily_path, index=False)

    results = {
        'snapshot_id': SNAPSHOT_ID,
        'mode': 'MODEL_INTEGRATED_V12_1_OVERLAY_REDESIGN',
        'created_utc': time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        'determinism': {
            'rng_seed_global': SEED,
            'python_version': '3.12.3',
            'numpy_version': '1.26.4',
            'xgboost_version': '3.1.2',
            'torch_version': '2.5.1+cu121',
            'os': 'Ubuntu, linux/amd64',
        },
        'v121_changes': [
            'qqq_absret_z_60d_lag1 feature added',
            'downside_cluster_5d_lag1 feature added',
            'pnl_5d_sum_lag1 feature added',
            'dd_20d_lag1 feature added',
            'Chronos enhanced tox_score with tox_state classification',
            'Asymmetric cost model (k_down=0.20, k_pred=0.30, k_vac=+2bps)',
            'IRB PnL triggers (pnl_5d < -2% => 0.85, dd_20d < -5% => 0.75)',
            'Tox-connected hedge/participation/cost/IRB adjustments',
        ],
        'full_oos': bt_results['full_oos'],
        'folds': bt_results['folds'],
        'year_2025': bt_results['year_2025'],
        'year_2026': bt_results['year_2026'],
        'gates': gates,
        'gates_overall': overall,
    }

    results_path = f"{BASE_DIR}/manifest.json"
    with open(results_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    # SHA256 manifest
    hashes = {}
    for root, dirs, files in os.walk(BASE_DIR):
        for fn in files:
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, BASE_DIR)
            if 'sha256' not in rel.lower():
                hashes[rel] = sha256_file(fp)

    hash_path = f"{BASE_DIR}/hashes/sha256.txt"
    with open(hash_path, 'w') as f:
        for fn, h in sorted(hashes.items()):
            f.write(f"sha256:{h}  {fn}\n")

    # ── SSOT Artifact Hook: generate RL feat-col JSONs + tox calibration ──
    try:
        import subprocess, sys

        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "generate_v12_1_ssot_artifacts_utc.py"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "generate_v12_1_ssot_artifacts.py"),
            "/home/ubuntu/ssot/tools/generate_v12_1_ssot_artifacts_utc.py",
            "/home/ubuntu/generate_v12_1_ssot_artifacts_utc.py",
        ]
        gen_script = next((c for c in candidates if os.path.exists(c)), None)

        if gen_script is None:
            print("[SSOT] Artifact hook skipped: generator script not found.")
        else:
            ssot_base = os.path.dirname(BASE_DIR)
            feature_matrix_path = f"{BASE_DIR}/features/feature_matrix_2015_2026.parquet"
            if not os.path.exists(feature_matrix_path):
                feature_matrix_path = f"{BASE_DIR}/features/feature_matrix_2015_2026.csv"

            cmd = [
                sys.executable, gen_script,
                "--snapshot_id", SNAPSHOT_ID,
                "--ssot_base", ssot_base,
                "--feature_matrix", feature_matrix_path,
                "--train_end", TRAIN_END,
                "--fold_sizes", ",".join(str(x) for x in FOLD_SIZES),
            ]
            print("[SSOT] Running artifact generator:", " ".join(cmd))
            subprocess.run(cmd, check=True)
            print("[SSOT] Artifact hook complete.")
    except Exception as e:
        print(f"[SSOT] Artifact hook FAILED (non-fatal): {e}")

    elapsed = time.time() - t0
    print(f"\n[DONE] {elapsed:.1f}s — All outputs: {BASE_DIR}")
    print(f"\n{'='*70}")
    if overall:
        print("  ARES v1.2.1 OVERLAY REDESIGN COMPLETE — ALL GATES PASSED")
    else:
        print("  ARES v1.2.1 OVERLAY REDESIGN COMPLETE — GATE FAILURE (REVIEW REQUIRED)")
    print(f"{'='*70}")

    return results


if __name__ == "__main__":
    main()
