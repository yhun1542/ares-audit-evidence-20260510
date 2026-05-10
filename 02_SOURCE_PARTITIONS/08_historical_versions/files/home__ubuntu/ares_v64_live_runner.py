#!/usr/bin/env python3
import os, json, time, signal, hashlib, importlib.util
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import numpy as np


def load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class V64State:
    last_engine_date: Optional[str] = None
    last_target_hash: Optional[str] = None
    last_exec_id: Optional[str] = None
    turnover_today: float = 0.0
    halted: bool = False
    halt_reason: Optional[str] = None
    session_day: Optional[str] = None
    equity_peak: float = 0.0
    stats: Dict[str, Any] = field(default_factory=dict)


class V64ChampionEngine:
    def __init__(self, eng_mod, params: Dict[str, Any]):
        self.eng = eng_mod
        self.params = params

    def load_db(self, db_path: str, min_match: int):
        # engine_v53 uses universe_filter=True by default
        return self.eng.load_db(db_path, universe_filter=True, min_match=min_match)

    def compute_latest(self, data: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
        P,R = data['prices'], data['returns']
        H,L,O,DV = data['high'], data['low'], data['open'], data['dvol']
        vix = data.get('vix')
        N = R.shape[1]
        n = R.shape[0]
        p = {**self.eng.DEFAULT_PARAMS, **params}
        pcap = min(p['per_cap'], self.eng.MAX_GROSS_EXPOSURE / max(p['top_k'],1) * 1.5)
        pw = np.zeros(N)
        prev_reg = 2
        confidence = 0.5
        sig_hist = []
        ret_hist = []
        mkt_var_ewm = 0.0001
        daily_returns = []
        last_target = np.zeros(N)
        last_reg = 2
        for t in range(252, n):
            reg = self.eng._regime(R, t, vix)
            last_reg = reg
            scores = self.eng.compute_sleeves(P,R,H,L,O,DV,t,N,reg)
            mix = dict(self.eng.SMIX.get(reg, self.eng.SMIX[2]))
            # core_only
            if p.get('core_only', False):
                for sn in list(mix.keys()):
                    if sn not in ('TREND','DLV','MRC','CA','CASH'):
                        mix[sn] = 0.0
                non_cash = sum(v for k,v in mix.items() if k != 'CASH')
                if non_cash > 0.01:
                    cash_w = mix.get('CASH', 0.0)
                    scale = (1.0 - cash_w) / non_cash if cash_w < 1.0 else 1.0
                    for k in mix:
                        if k != 'CASH':
                            mix[k] *= scale
            if p.get('topen_030', False):
                mkt_vol = np.nanstd(np.nanmean(R[max(0,t-20):t],axis=1)) if t >= 20 else .02
                if mkt_vol <= p.get('topen_thresh', 0.03) and t >= 63:
                    scores['TF'] = (P[t-21] / (P[t-63] + 1e-12) - 1)
            comb = np.zeros(N); tw = 0.0
            for sn, sw in mix.items():
                if sn == 'CASH' or sw < 0.001 or sn not in scores:
                    continue
                s = scores[sn]
                if s is None or np.all(np.isnan(s)):
                    continue
                v = np.isfinite(s)
                if v.sum() < 5:
                    continue
                m = np.nanmean(s[v]); sd = np.nanstd(s[v])
                if sd < 1e-8:
                    continue
                comb += sw * np.where(v, (s - m) / (sd + 1e-8), 0.0)
                tw += sw
            if tw < 0.01:
                r = float(np.nansum(pw * R[t]))
                daily_returns.append(r)
                continue
            comb /= tw
            # confidence from rolling corr with next day returns
            if t >= 1:
                ret_hist.append(R[t-1].copy())
                sig_hist.append(comb.copy())
            if len(sig_hist) >= 10:
                ics = []
                for i in range(min(15, len(sig_hist)-1)):
                    sg = sig_hist[-(i+2)]
                    rt = ret_hist[-(i+1)]
                    vl = np.isfinite(sg) & np.isfinite(rt)
                    if vl.sum() >= 10:
                        ic = np.corrcoef(sg[vl], rt[vl])[0,1]
                        if np.isfinite(ic):
                            ics.append(ic)
                confidence = np.clip(np.mean(ics)/0.05 if ics else 0.0, 0.0, 1.0) * 0.5 + 0.25
            # alpha transforms used by V64
            if p.get('ewma_vol_target', False):
                mkt_r = float(np.nanmean(R[t]))
                lam = float(p.get('ewma_lam', 0.85))
                mkt_var_ewm = lam * mkt_var_ewm + (1.0 - lam) * (mkt_r * mkt_r)
                rv_ewm = float(np.sqrt(max(mkt_var_ewm, 1e-12) * 252.0))
            else:
                rv_ewm = None
            # exposure target
            ab, am, af = p['ace_base'], p['ace_max'], p['ace_floor']
            b = ab.get(reg, 0.85); mx = am.get(reg, 0.95); fl = af.get(reg, 0.65)
            tgt = b + (mx - b) * confidence
            tv = p['target_vol']
            if rv_ewm and rv_ewm > 0.001:
                tgt = np.clip(tgt * np.clip(tv / rv_ewm, p['vol_clamp_low'], p['vol_clamp_high']), fl, mx)
            else:
                if t >= 30:
                    pr = np.nanmean(R[max(0,t-30):t], axis=1)
                    rv = np.nanstd(pr) * np.sqrt(252) if len(pr)>2 else .15
                    if rv > 0.001:
                        tgt = np.clip(tgt * np.clip(tv / rv, p['vol_clamp_low'], p['vol_clamp_high']), fl, mx)
            tgt = min(tgt, self.eng.MAX_GROSS_EXPOSURE)
            # softmax weighting winner
            net = comb.copy()
            inc = pw > .001
            net[~inc] -= p['switch_pen']/10000.0
            net[inc] += p['inc_bonus']/10000.0
            k = min({0:12,1:14,2:p['top_k'],3:min(20,N),4:min(20,N),5:12}.get(reg, p['top_k']), int(np.isfinite(net).sum()))
            if k < 3:
                r = float(np.nansum(pw * R[t]))
                daily_returns.append(r)
                continue
            top = list(np.argsort(-net)[:k])
            w = np.zeros(N)
            if p.get('softmax_weighting', False):
                eli = np.array(top, dtype=int)
                s = net[eli].copy(); s = s - np.nanmax(s)
                tau = max(float(p.get('softmax_tau', 0.35)), 1e-6)
                raw = np.exp(s / tau); raw = raw / (raw.sum() + 1e-12)
                for j, ai in enumerate(eli):
                    w[ai] = min(tgt * raw[j], pcap)
            else:
                rw = np.ones(len(top))
                for ri in range(len(top)):
                    for lo,hi,wt in self.eng.RTIERS:
                        if lo <= ri < hi:
                            rw[ri] = wt; break
                rw /= rw.sum()
                for ri,ai in enumerate(top):
                    w[ai] = min(tgt * rw[ri], pcap)
            w = np.clip(w, 0, pcap)
            ct = np.sum(w)
            if ct > 1e-8:
                w *= min(tgt / ct, 2.0)
            w = np.minimum(w, pcap)
            eli = np.array(top, dtype=int)
            for _ in range(10):
                gap = tgt - np.sum(w)
                if gap <= 1e-6:
                    break
                room = np.maximum(pcap - w[eli], 0.0)
                av = room > 1e-6
                if not np.any(av):
                    break
                fi = eli[av]; fr = room[av]; rs = np.sum(fr)
                if rs <= 1e-6:
                    break
                w[fi] += np.minimum(fr, gap * fr / rs)
                w = np.minimum(w, pcap)
            # v53 guards
            gross = float(np.sum(w))
            if gross > self.eng.MAX_GROSS_EXPOSURE and gross > 0:
                w *= self.eng.MAX_GROSS_EXPOSURE / gross
            vx = float(vix[t]) if vix is not None and t < len(vix) else 20.0
            if vx >= p['crash_vix']:
                w *= 0.60
            if np.sum(pw) > 1e-8:
                band = p['ntb_base'] * (1.3 if vx > 30 else 1.1 if vx > 20 else 1.0)
                for i in range(N):
                    if abs(w[i] - pw[i]) < band:
                        if not (pw[i] == 0 and w[i] > 0) and not (pw[i] > 0 and w[i] == 0):
                            w[i] = pw[i]
            if reg in {0,1}:
                clamp = p['a2b2_base'] + (p['a2b2_max'] - p['a2b2_base']) * confidence
                clamp = min(clamp, self.eng.MAX_GROSS_EXPOSURE)
                gr = np.sum(w)
                if gr > clamp and gr > 0:
                    w *= clamp / gr
                if np.sum(pw) > 0:
                    bd = sum(max(0, w[i] - pw[i]) for i in range(N))
                    if bd > p['a2b2_to'] and bd > 0:
                        clip = p['a2b2_to'] / bd
                        for i in range(N):
                            d_ = w[i] - pw[i]
                            if d_ > 0:
                                w[i] = pw[i] + d_ * clip
            to_cap = p['to_base'] if reg == prev_reg else p['to_max']
            turn = np.sum(np.abs(w - pw))
            if turn > to_cap:
                w = pw + (w - pw) * (to_cap / turn)
                turn = np.sum(np.abs(w - pw))
            tc = float(turn * p['cost_bps'] / 10000.0)
            pw = np.clip(w, 0, pcap)
            prev_reg = reg
            last_target = 1.0 * pw.copy()
            r = float(np.nansum(pw * R[t])) - tc
            daily_returns.append(r)
        sr = 0.0
        if len(daily_returns) > 30 and np.std(daily_returns) > 1e-12:
            sr = float(np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252))
        return {
            'target': last_target.astype(np.float64),
            'selected': ['V64_PROD'],
            'confidence': float(confidence),
            'regime_code': int(last_reg),
            'regime_name': self.eng.RN.get(int(last_reg), str(last_reg)),
            'actual_cost': 0.0,
            'shadow_cost': 0.0,
            'turnover': 0.0,
            'book_a': last_target.astype(np.float64),
            'book_b': np.zeros_like(last_target),
            'last_engine_date': str(data['dates'][-1].date()) if hasattr(data['dates'][-1], 'date') else str(data['dates'][-1]),
            'sr_proxy': sr,
        }


class V64LiveRunner:
    def __init__(self, cfg_path: str):
        self.cfg_raw = json.loads(Path(cfg_path).read_text())
        self.ops = load_module(self.cfg_raw['ops_module_path'], 'v55ops')
        self.cfg = self.ops.LiveAutopilotConfig.from_json(self.cfg_raw['ops_config_path'])
        self.cfg.execution.db_path = self.cfg_raw.get('db_path', self.cfg.execution.db_path)
        self.cfg.execution.state_path = self.cfg_raw.get('state_path', self.cfg.execution.state_path)
        self.cfg.audit_dir = self.cfg_raw.get('audit_dir', self.cfg.audit_dir)
        Path(self.cfg.audit_dir).mkdir(parents=True, exist_ok=True)
        self.store = self.ops.RedisStore(self.cfg.execution.redis_url)
        self.reader = self.ops.MarketAccountReader(self.store, self.cfg)
        self.governor = self.ops.AggressiveKernelGovernor(self.store, self.cfg)
        self.feedback = self.ops.LiveFeedbackLoop(self.store, self.cfg)
        self.bus = self.ops.RedisExecutionBus(self.store, self.cfg)
        self.eng_mod = load_module(self.cfg_raw['engine_v53_path'], 'engv53')
        self.engine = V64ChampionEngine(self.eng_mod, self.cfg_raw['prod_params'])
        self.state_path = Path(self.cfg_raw['state_path'])
        self.cache_path = Path(self.cfg_raw['cache_path'])
        self.state = self._load_state()
        self.full_data = None
        self.last_db_refresh = 0.0
        self.last_data_date = None
        self.cached_snapshot = None
        self.running = True
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

    def _stop(self, *_):
        self.running = False

    def _load_state(self) -> V64State:
        if self.state_path.exists():
            try:
                return V64State(**json.loads(self.state_path.read_text()))
            except Exception:
                pass
        return V64State()

    def _save_state(self):
        self.state_path.write_text(json.dumps(asdict(self.state), indent=2, default=str))
        self.store.set_json(self.ops.LiveKeys.STATE_JSON, {**asdict(self.state), 'ts': time.time()})
        self.store.set_json(f"{self.cfg_raw['v64_namespace_prefix']}:state:summary", {**asdict(self.state), 'ts': time.time()})

    def heartbeat(self):
        now = str(time.time())
        self.store.r.setex(self.ops.LiveKeys.HEARTBEAT, self.cfg.execution.heartbeat_ttl_sec, now)
        self.store.r.setex(f"{self.cfg_raw['v64_namespace_prefix']}:live:heartbeat", self.cfg.execution.heartbeat_ttl_sec, now)

    def set_readiness(self, ready: bool, reason: str):
        payload = {'ready': ready, 'reason': reason, 'ts': time.time()}
        self.store.set_json(self.ops.LiveKeys.READINESS, payload, ex=self.cfg.execution.heartbeat_ttl_sec)
        self.store.set_json(f"{self.cfg_raw['v64_namespace_prefix']}:live:readiness", payload, ex=self.cfg.execution.heartbeat_ttl_sec)

    def load_or_refresh_data(self, force: bool = False):
        if self.full_data is None or force or (time.time() - self.last_db_refresh >= self.cfg_raw.get('db_refresh_sec', 300)):
            self.full_data = self.engine.load_db(self.cfg.execution.db_path, self.cfg.execution.min_match)
            self.last_db_refresh = time.time()
            self.last_data_date = str(self.full_data['dates'][-1].date()) if hasattr(self.full_data['dates'][-1], 'date') else str(self.full_data['dates'][-1])
        return self.full_data

    def _build_snapshot(self, force: bool = False):
        full_data = self.load_or_refresh_data(force=force)
        current_date = str(full_data['dates'][-1].date()) if hasattr(full_data['dates'][-1], 'date') else str(full_data['dates'][-1])
        if not force and self.cached_snapshot and self.state.last_engine_date == current_date:
            return full_data, self.cached_snapshot
        params = dict(self.cfg_raw['prod_params'])
        snap = self.engine.compute_latest(full_data, params)
        self.cached_snapshot = snap
        self.state.last_engine_date = snap['last_engine_date']
        return full_data, snap

    def current_market_snapshot(self):
        vix = self.reader.get_vix(); spy = self.reader.get_spy()
        self.feedback.record_market_point(spy=spy, vix=vix)
        flap = self.feedback.get_flap_count()
        data_lag_hours = 0.0
        return {'vix': vix, 'spy': spy, 'quotes_stale': False, 'flap_count': flap, 'data_lag_hours': data_lag_hours}

    def _portfolio_metrics(self, target, actual, equity, open_orders):
        drift = float(np.max(np.abs(target - actual))) if len(target) else 0.0
        gross = float(np.sum(np.clip(np.abs(actual), 0.0, 10.0)))
        session_day = time.strftime('%Y-%m-%d', time.gmtime())
        if self.state.session_day != session_day:
            self.state.session_day = session_day
            self.state.turnover_today = 0.0
        peak = max(self.state.equity_peak, equity)
        self.state.equity_peak = peak
        dd = (peak - equity) / peak if peak > 0 else 0.0
        return {'equity': equity, 'gross_exposure': gross, 'leverage': gross, 'max_weight_drift': drift, 'open_orders': open_orders, 'drawdown_pct': dd, 'daily_pnl_pct': 0.0, 'turnover_today': self.state.turnover_today}

    def build_execution_plan(self, symbols, target, actual, prices, equity, decision):
        # copied from v55 build_execution_plan
        tgt = np.array(target, dtype=np.float64)
        if decision.decision == self.ops.Decision.FLATTEN:
            tgt[:] = 0.0
        elif decision.decision == self.ops.Decision.DE_RISK_ONLY:
            gross = np.sum(tgt); cap = min(decision.params.get('exposure_target', 0.35), self.cfg.execution.max_published_exposure)
            if gross > cap and gross > 0: tgt *= cap / gross
        elif decision.decision == self.ops.Decision.CONSERVATIVE_ONLY:
            gross = np.sum(tgt); cap = min(decision.params.get('exposure_target', 0.75), self.cfg.execution.max_published_exposure)
            if gross > cap and gross > 0: tgt *= cap / gross
        if self.cfg.kernel.long_only:
            tgt = np.clip(tgt, 0.0, None)
        tgt = np.clip(tgt, 0.0, self.cfg.execution.max_published_exposure)
        gross = float(np.sum(tgt))
        if gross > self.cfg.execution.max_published_exposure and gross > 0:
            tgt *= self.cfg.execution.max_published_exposure / gross
        delta = tgt - actual
        turnover = float(np.sum(np.abs(delta)))
        if turnover > self.cfg.execution.max_live_turnover_pct:
            tgt = actual + (delta * (self.cfg.execution.max_live_turnover_pct / turnover))
            delta = tgt - actual
            turnover = float(np.sum(np.abs(delta)))
        orders = []
        max_single_notional = equity * self.cfg.execution.max_single_order_pct_equity
        for idx, (sym, dw) in enumerate(sorted(zip(symbols, delta), key=lambda kv: abs(kv[1]), reverse=True)):
            if abs(dw) < self.cfg.execution.min_order_weight_delta:
                continue
            px = prices.get(sym)
            if px is None or px <= 0:
                continue
            d_notional = float(dw * equity)
            if abs(d_notional) < self.cfg.execution.min_order_notional:
                continue
            d_notional = self.ops.clamp(d_notional, -max_single_notional, max_single_notional)
            orders.append({
                'symbol': sym,
                'side': 'BUY' if d_notional > 0 else 'SELL',
                'delta_weight': round(float(dw), 8),
                'target_weight': round(float(tgt[symbols.index(sym)]), 8),
                'delta_notional': round(float(d_notional), 2),
                'price': round(float(px), 6),
            })
            if len(orders) >= self.cfg.execution.max_order_count:
                break
        payload = {'ts': time.time(), 'decision': decision.decision, 'regime': decision.regime, 'urgency': decision.urgency, 'reason': decision.reason, 'orders': orders, 'turnover': turnover, 'gross_target': float(np.sum(tgt))}
        return payload, tgt, turnover

    def publish_dual(self, decision, target_payload, target_hash, selected, confidence, metrics, autotune):
        # publish v55 keys for compatibility
        record = {'ts': time.time(), 'decision': decision.decision, 'regime': decision.regime, 'urgency': decision.urgency, 'reason': decision.reason, 'target_hash': target_hash, 'turnover': target_payload.get('turnover', 0.0)}
        self.store.set_json(self.ops.LiveKeys.DECISION_LAST, record)
        self.store.publish_history_point(self.ops.LiveKeys.DECISION_HISTORY, record, self.cfg.price_history_ttl_sec, self.cfg.history_keep)
        self.store.set_json(self.ops.LiveKeys.TARGET_LAST, {'ts': time.time(), 'symbols': target_payload['symbols'], 'target': target_payload['target'], 'selected_universes': selected, 'confidence': confidence, 'regime': decision.regime, 'decision': decision.decision})
        self.store.r.set(self.ops.LiveKeys.TARGET_HASH, target_hash)
        self.store.set_json(self.ops.LiveKeys.TARGET_META, {'actual_cost_bps': metrics['avg_actual_cost_bps'], 'shadow_cost_bps': metrics['avg_shadow_cost_bps'], 'turnover': target_payload['turnover'], 'autotune': autotune, 'ts': time.time()})
        self.store.set_json(self.ops.LiveKeys.METRICS, metrics)
        self.store.publish_history_point(self.ops.LiveKeys.METRIC_HISTORY, metrics, self.cfg.price_history_ttl_sec, self.cfg.history_keep)
        # duplicate v64 namespace
        prefix = self.cfg_raw['v64_namespace_prefix']
        self.store.set_json(f"{prefix}:decision:last", record)
        self.store.set_json(f"{prefix}:target:last", {'ts': time.time(), 'symbols': target_payload['symbols'], 'target': target_payload['target'], 'selected_universes': selected, 'confidence': confidence, 'regime': decision.regime, 'decision': decision.decision})
        self.store.set_json(f"{prefix}:target:meta", {'actual_cost_bps': metrics['avg_actual_cost_bps'], 'shadow_cost_bps': metrics['avg_shadow_cost_bps'], 'turnover': target_payload['turnover'], 'ts': time.time(), 'config_hash': self.cfg_raw['prod_params']})
        self.store.set_json(f"{prefix}:metrics:latest", metrics)

    def run_once(self):
        self.heartbeat()
        self.set_readiness(False, 'initializing')
        force_rebuild = bool(self.store.r.get(self.ops.LiveKeys.FORCE_REBUILD))
        if force_rebuild:
            self.store.r.delete(self.ops.LiveKeys.FORCE_REBUILD)
            self.cached_snapshot = None
        full_data, snapshot = self._build_snapshot(force=force_rebuild)
        symbols = list(full_data['symbols'])
        market = self.current_market_snapshot()
        self.feedback.score_due_predictions()
        if self.state.halted:
            self.set_readiness(False, f'HALTED: {self.state.halt_reason}')
            self._save_state(); return
        target = snapshot['target']
        regime_name = str(snapshot['regime_name']).upper()
        confidence = float(snapshot['confidence'])
        selected = list(snapshot.get('selected', ['V64_PROD']))
        fallback_prices = {sym: float(full_data['prices'][ -1 ][i]) for i, sym in enumerate(symbols)}
        actual_w, pos_meta = self.reader.positions_to_weights(symbols, fallback_prices)
        equity = float(pos_meta.get('equity', self.reader.get_equity()))
        portfolio = self._portfolio_metrics(target, actual_w, equity, self.reader.get_open_orders())
        meta = self.store.get_json(self.ops.LiveKeys.AUTOTUNE, {}) or {}
        decision = self.governor.evaluate(regime_name, confidence, market, portfolio, target, actual_w, meta)
        target_payload, live_target, turnover = self.build_execution_plan(symbols, target, actual_w, fallback_prices, equity, decision)
        prices_used = {sym: fallback_prices.get(sym) for sym in symbols}
        target_hash = sha256_json({'symbols': symbols, 'target': [round(float(x), 8) for x in live_target.tolist()], 'decision': decision.decision})
        exec_id = sha256_json({'date': snapshot['last_engine_date'], 'decision': decision.decision, 'hash': target_hash})[:24]
        target_payload.update({'exec_id': exec_id, 'engine_date': snapshot['last_engine_date'], 'selected_universes': selected, 'confidence': confidence, 'prices_used': prices_used, 'meta_override': meta, 'alpha_params_subset': {k: self.cfg_raw['prod_params'].get(k) for k in ['top_k','rebal','target_vol','per_cap','cost_bps','ntb_base','to_base','to_max','a2b2_base','a2b2_to','crash_vix','ewma_lam','softmax_tau']}, 'symbols': symbols, 'target': [round(float(x),8) for x in live_target.tolist()]})
        metrics = {'equity': equity, 'gross_exposure': float(np.sum(np.abs(actual_w))), 'target_exposure': float(np.sum(live_target)), 'turnover_today': self.state.turnover_today, 'last_turnover': turnover, 'avg_actual_cost_bps': turnover * self.cfg.actual_cost_bps, 'avg_shadow_cost_bps': turnover * (self.cfg.actual_cost_bps + self.cfg.shadow_slippage_bps + self.cfg.shadow_impact_coeff_bps * turnover), 'decision': decision.decision, 'regime': regime_name, 'confidence': confidence, 'v64_prod': True}
        autotune = {}  # disabled for V64 direct cutover unless explicit
        self.publish_dual(decision, target_payload, target_hash, selected, confidence, metrics, autotune)
        if decision.decision in {self.ops.Decision.EXECUTE, self.ops.Decision.CONSERVATIVE_ONLY, self.ops.Decision.DE_RISK_ONLY, self.ops.Decision.FLATTEN}:
            ack = self.bus.submit(target_payload)
            self.state.last_exec_id = exec_id
            self.state.last_target_hash = target_hash
            self.state.turnover_today = self.ops.clamp(self.state.turnover_today + turnover, 0.0, 10.0)
            if decision.decision in {self.ops.Decision.FLATTEN, self.ops.Decision.HALT}:
                self.state.halted = True; self.state.halt_reason = decision.reason
            self._write_audit('execution', {'intent': target_payload, 'ack': ack, 'metrics': metrics})
        elif decision.decision == self.ops.Decision.HALT:
            self.state.halted = True; self.state.halt_reason = decision.reason
        self.set_readiness(True, f"{decision.decision}: {decision.reason}")
        self._save_state()

    def _write_audit(self, name, obj):
        p = Path(self.cfg.audit_dir) / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{name}.json"
        p.write_text(json.dumps(obj, indent=2, default=str))

    def run_forever(self):
        cycle = int(self.cfg_raw.get('cycle_sec', self.cfg.execution.cycle_sec))
        while self.running:
            try:
                self.run_once()
            except Exception as e:
                self.set_readiness(False, f'error: {e}')
                self._write_audit('error', {'ts': time.time(), 'error': str(e)})
            time.sleep(cycle)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--once', action='store_true')
    args = ap.parse_args()
    runner = V64LiveRunner(args.config)
    if args.once:
        runner.run_once()
    else:
        runner.run_forever()
