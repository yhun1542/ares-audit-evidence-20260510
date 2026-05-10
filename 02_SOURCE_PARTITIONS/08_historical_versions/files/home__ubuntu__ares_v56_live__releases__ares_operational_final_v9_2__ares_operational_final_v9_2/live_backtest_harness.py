#!/usr/bin/env python3
"""
Live Backtest Harness
=====================
라이브 ares-v56-live 프로세스가 사용하는 engine_v65_c6_adapter.py의
run_single을 직접 호출한다. 이는 ares_v55_live_autopilot.py :: LiveEngine.replay_to_latest()
의 수학적 동치이다 (candidate_universes 모두 동일 계산이므로 top3 blend = 1 run).

단, tc6x_config.json의 라이브 override 파라미터를 DEFAULT_PARAMS에 overlay한다.
"""
import json, sys, os, time, argparse, importlib.util
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
# EC2 에서는 cwd에 adapter/config가 직접 있음, 로컬은 live_chain_v56current 서브디렉
if os.path.exists(os.path.join(HERE, 'engine_v65_c6_adapter.py')):
    ADAPTER = os.path.join(HERE, 'engine_v65_c6_adapter.py')
    CONFIG  = os.path.join(HERE, 'tc6x_config.json')
else:
    ADAPTER = os.path.join(HERE, 'live_chain_v56current', 'engine_v65_c6_adapter.py')
    CONFIG  = os.path.join(HERE, 'live_chain_v56current', 'tc6x_config.json')

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def main(db_path, min_match, out_dir, verbose=False, mode='single'):
    adapter = load_module('adapter', ADAPTER)
    alpha = json.load(open(CONFIG))
    # overlay live alpha params into engine DEFAULT_PARAMS
    params = dict(adapter.DEFAULT_PARAMS)
    for k, v in alpha.items():
        if k in params or k in {'top_k','signal_rebal','execution_stagger','target_vol','per_cap',
                                'cost_bps','ntb_base','ntb_min','ntb_max','to_base','to_max',
                                'a2b2_base','a2b2_max','a2b2_to','crash_vix','switch_pen','inc_bonus',
                                'vol_clamp_low','vol_clamp_high',
                                'ranking_window','ranking_min_obs',
                                'candidate_universes','fallback_universes',
                                'ace_base','ace_max','ace_floor'}:
            params[k] = v

    # dict keys 0..5 may have been loaded as strings — normalize
    for key in ('ace_base','ace_max','ace_floor'):
        if isinstance(params.get(key), dict):
            params[key] = {int(k): float(v) for k, v in params[key].items()}

    print(f"[harness] adapter __version__ = {getattr(adapter,'__version__','?')}")
    print(f"[harness] champion            = {getattr(adapter,'__champion__','?')}")
    print(f"[harness] LIVE_TRUTH_ENGINE   = {getattr(adapter,'LIVE_TRUTH_ENGINE_FILE','?')}")
    print(f"[harness] params overlay:")
    for k in ['top_k','ntb_base','to_base','to_max','a2b2_base','crash_vix','cost_bps','per_cap','target_vol']:
        print(f"    {k:12s} = {params.get(k)}")

    t0 = time.time()
    data = adapter.load_full_db(db_path, min_match=min_match)
    print(f"[harness] data loaded: T={data['returns'].shape[0]} N={data['returns'].shape[1]} in {time.time()-t0:.2f}s")

    t0 = time.time()
    if mode == 'single':
        res = adapter.run_single(data, params=params, verbose=verbose)
    elif mode == 'wf':
        res = adapter.run_wf(data, n_folds=10, params=params, verbose=verbose)
    else:
        raise SystemExit(f'unknown mode {mode}')
    dt = time.time() - t0
    print(f"[harness] run_{mode} done in {dt:.1f}s")
    print(f"[harness] result keys: {list(res.keys()) if isinstance(res, dict) else type(res)}")
    if isinstance(res, dict):
        # Print key metrics
        for k in ['SR','sharpe','CAGR','cagr','MDD','mdd','Calmar','Sortino','sortino','WR','winrate',
                  'TO','turnover','EX','exposure','assets','positions','n_days','hard_cap_hits']:
            if k in res:
                print(f"    {k}: {res[k]}")

    # save
    os.makedirs(out_dir, exist_ok=True)
    # daily csv with ALL daily arrays
    if isinstance(res, dict):
        dates = data.get('dates')
        dr_key = '_daily_returns' if '_daily_returns' in res else ('daily_returns' if 'daily_returns' in res else None)
        if dr_key:
            dr = np.asarray(res[dr_key], dtype=float)
            to = np.asarray(res.get('_daily_turnover', []), dtype=float)
            cost = np.asarray(res.get('_daily_cost', []), dtype=float)
            exp_ = np.asarray(res.get('_daily_exposure', []), dtype=float)
            pos = np.asarray(res.get('_daily_positions', []), dtype=float)
            T = len(dr)
            if dates is not None:
                dates_iso = [pd.Timestamp(d).isoformat() if hasattr(d,'isoformat') or isinstance(d,(str,np.datetime64)) else str(d) for d in dates]
                dates_tail = dates_iso[-T:] if len(dates_iso) >= T else dates_iso + [''] * (T - len(dates_iso))
            else:
                dates_tail = [''] * T
            def pad(a, T):
                if len(a) == T: return a
                if len(a) == 0: return np.zeros(T)
                out = np.zeros(T); out[-len(a):] = a; return out
            df = pd.DataFrame({
                'date': dates_tail,
                'daily_return': dr,
                'daily_turnover': pad(to, T),
                'daily_cost': pad(cost, T),
                'daily_exposure': pad(exp_, T),
                'daily_positions': pad(pos, T),
            })
            df.to_csv(os.path.join(out_dir, 'live_daily_full.csv'), index=False)
            print(f"[harness] daily_full.csv saved: {T} rows")

    # save scalar metrics json (arrays replaced with length info)
    try:
        serial = {}
        for k, v in (res.items() if isinstance(res,dict) else {}):
            if hasattr(v, 'tolist'):
                vv = v.tolist()
                if isinstance(vv, list) and len(vv) > 1000:
                    serial[k] = f'<array len={len(vv)}>'
                else:
                    serial[k] = vv
            elif isinstance(v, list) and len(v) > 1000:
                serial[k] = f'<list len={len(v)}>'
            else:
                serial[k] = v
        json.dump(serial, open(os.path.join(out_dir, f'live_{mode}_result.json'),'w'), default=str, indent=2)
        print(f"[harness] result json saved")
    except Exception as e:
        print(f"[harness] json save error: {e}")

    return res

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--out', default='./live_harness_out')
    ap.add_argument('--min-match', type=int, default=20)
    ap.add_argument('--mode', default='single', choices=['single','wf'])
    ap.add_argument('--verbose', action='store_true')
    a = ap.parse_args()
    main(a.db, a.min_match, a.out, a.verbose, a.mode)
