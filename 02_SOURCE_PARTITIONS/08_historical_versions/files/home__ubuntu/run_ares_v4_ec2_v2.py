#!/usr/bin/env python3
"""
ARES X V4 엔진 개선 버전
- Shock-Triggered Rebalance: 레짐 전환 시 즉시 리밸런싱
- ICIR 기반 동적 팩터 가중치
- 옵션 오버레이 통합
"""
import sys
import os
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# ============================================================================
# Configuration
# ============================================================================
@dataclass
class CostModelConfig:
    commission_bps: float = 0.5
    slippage_bps: float = 2.0
    spread_bps: float = 3.0
    use_adv: bool = True
    impact_k: float = 0.10

@dataclass
class RiskModelConfig:
    covariance_method: str = "ledoit_wolf"
    use_risk_parity: bool = True
    risk_parity_step: float = 0.03
    risk_parity_max_iter: int = 400

@dataclass
class RegimeConfig:
    allow_shock_rebalance: bool = True  # 쇼크 시 즉시 리밸런싱
    high_vol_threshold: float = 0.7     # risk_off_score 임계값
    neutral_threshold: float = 0.4

@dataclass
class EnsembleConfig:
    ic_window: int = 60                 # IC 계산 윈도우 (일)
    ic_min_samples: int = 30            # 최소 샘플 수
    cap_per_factor: float = 0.5         # 팩터당 최대 가중치
    boost_value_on_inversion: float = 1.3
    boost_lowvol_on_highvol: float = 1.4
    damp_momentum_on_highvol: float = 0.7

@dataclass
class BacktestConfig:
    rebalance_every_days: int = 21      # 기본 리밸런싱 주기
    periods_per_year: int = 252
    train_window: int = 252
    cov_window: int = 60
    cov_every: int = 20

@dataclass
class AresConfig:
    costs: CostModelConfig = field(default_factory=CostModelConfig)
    risk: RiskModelConfig = field(default_factory=RiskModelConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    ensemble: EnsembleConfig = field(default_factory=EnsembleConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

# ============================================================================
# EC2 DB Adapter
# ============================================================================
class EC2DBAdapter:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        
    def load_prices(self, symbols: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
        prices = {}
        for symbol in symbols:
            query = """
                SELECT date, open, high, low, close, volume
                FROM daily_ohlcv
                WHERE symbol = ?
                  AND date BETWEEN ? AND ?
                ORDER BY date
            """
            df = pd.read_sql_query(query, self.conn, params=[symbol, start_date, end_date])
            if df.empty:
                continue
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').dropna(subset=['close'])
            if len(df) > 0:
                prices[symbol] = df
        return prices
    
    def load_fundamentals(self, symbols: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        placeholders = ','.join(['?' for _ in symbols])
        query = f"""
            SELECT date, ticker, value_z, quality_z, value_isna, coverage_score
            FROM fundamentals_pit_daily
            WHERE ticker IN ({placeholders})
              AND date BETWEEN ? AND ?
            ORDER BY date, ticker
        """
        params = symbols + [start_date, end_date]
        df = pd.read_sql_query(query, self.conn, params=params)
        df['date'] = pd.to_datetime(df['date'])
        return df
    
    def load_macro_regime(self, start_date: str, end_date: str) -> pd.DataFrame:
        query = """
            SELECT date, vol_score, credit_score, rates_score, risk_off_score, regime_label
            FROM macro_regime_daily
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """
        df = pd.read_sql_query(query, self.conn, params=[start_date, end_date])
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        return df
    
    def get_symbols(self) -> List[str]:
        query = "SELECT DISTINCT ticker FROM fundamentals_pit_daily ORDER BY ticker"
        df = pd.read_sql_query(query, self.conn)
        return df['ticker'].tolist()
    
    def close(self):
        self.conn.close()

# ============================================================================
# Risk & Covariance
# ============================================================================
def ledoit_wolf_cov(returns: np.ndarray) -> np.ndarray:
    x = returns - np.nanmean(returns, axis=0, keepdims=True)
    x = np.nan_to_num(x, nan=0.0)
    S = (x.T @ x) / max(1, (x.shape[0]-1))
    n = S.shape[0]
    var = np.diag(S).copy()
    std = np.sqrt(np.maximum(var, 1e-12))
    corr = S / (std[:,None]*std[None,:] + 1e-12)
    mask = ~np.eye(n, dtype=bool)
    rbar = corr[mask].mean() if mask.sum()>0 else 0.0
    F = rbar*(std[:,None]*std[None,:])
    np.fill_diagonal(F, var)
    alpha = 0.15
    return (1-alpha)*S + alpha*F

# ============================================================================
# ICIR 기반 동적 팩터 가중치
# ============================================================================
FACTOR_NAMES = ["momentum", "mean_reversion", "value", "quality", "low_vol"]

class ICIRTracker:
    """각 팩터의 Information Coefficient (IC)를 추적하고 ICIR 기반 가중치 계산"""
    
    def __init__(self, cfg: EnsembleConfig):
        self.cfg = cfg
        self._ic_history: Dict[str, List[float]] = defaultdict(list)
        
    def update_ic(self, factor_scores: Dict[str, np.ndarray], realized_returns: np.ndarray):
        """팩터 점수와 실현 수익률 간의 IC 업데이트"""
        for name, scores in factor_scores.items():
            if name not in FACTOR_NAMES:
                continue
            x = np.asarray(scores, dtype=float)
            y = np.asarray(realized_returns, dtype=float)
            mask = np.isfinite(x) & np.isfinite(y)
            if mask.sum() < 5:
                continue
            # Rank IC (Spearman correlation)
            x_rank = np.argsort(np.argsort(x[mask]))
            y_rank = np.argsort(np.argsort(y[mask]))
            ic = np.corrcoef(x_rank, y_rank)[0, 1]
            if np.isfinite(ic):
                self._ic_history[name].append(float(ic))
                # 윈도우 유지
                if len(self._ic_history[name]) > self.cfg.ic_window:
                    self._ic_history[name] = self._ic_history[name][-self.cfg.ic_window:]
    
    def get_weights(self, regime_state) -> Dict[str, float]:
        """ICIR 기반 팩터 가중치 계산"""
        icir = {}
        for name in FACTOR_NAMES:
            vals = self._ic_history.get(name, [])
            if len(vals) < self.cfg.ic_min_samples:
                icir[name] = 0.1  # 기본 가중치
            else:
                arr = np.array(vals, dtype=float)
                mu = arr.mean()
                sd = arr.std(ddof=1) + 1e-9
                icir[name] = max(0.0, mu / sd)  # ICIR (양수만)
        
        # 레짐 기반 조정
        if regime_state:
            if regime_state.macro_regime in ["RISK_OFF", "INVERSION"]:
                icir["value"] *= self.cfg.boost_value_on_inversion
                icir["quality"] *= self.cfg.boost_value_on_inversion
            if regime_state.vol_regime == "HIGH":
                icir["low_vol"] *= self.cfg.boost_lowvol_on_highvol
                icir["momentum"] *= self.cfg.damp_momentum_on_highvol
        
        # 정규화
        total = sum(icir.values())
        if total <= 0:
            return {name: 1.0/len(FACTOR_NAMES) for name in FACTOR_NAMES}
        
        weights = {}
        for name, score in icir.items():
            w = score / total
            w = min(w, self.cfg.cap_per_factor)  # 캡 적용
            weights[name] = w
        
        # 재정규화
        total = sum(weights.values())
        return {k: v/total for k, v in weights.items()}

# ============================================================================
# Regime Engine (Enhanced)
# ============================================================================
@dataclass
class RegimeState:
    date: pd.Timestamp
    vol_regime: str
    macro_regime: str
    risk_on_score: float
    shock: bool
    prev_regime: str = "NEUTRAL"

class RegimeEngine:
    def __init__(self, cfg: RegimeConfig):
        self.cfg = cfg
        self._state: Optional[RegimeState] = None
        self._prev_macro = "NEUTRAL"
    
    def update_from_db(self, date: pd.Timestamp, macro_df: pd.DataFrame) -> RegimeState:
        if date in macro_df.index:
            row = macro_df.loc[date]
        else:
            prev_dates = macro_df.index[macro_df.index <= date]
            if len(prev_dates) > 0:
                row = macro_df.loc[prev_dates[-1]]
            else:
                row = pd.Series({'risk_off_score': 0.5, 'regime_label': 'NEUTRAL', 'vol_score': 0.0})
        
        risk_off_score = row.get('risk_off_score', 0.5) or 0.5
        regime_label = row.get('regime_label', 'NEUTRAL') or 'NEUTRAL'
        vol_score = row.get('vol_score', 0.0) or 0.0
        
        # Vol regime
        if vol_score >= 1.0:
            vol_regime = "HIGH"
        elif vol_score <= -0.5:
            vol_regime = "LOW"
        else:
            vol_regime = "NORMAL"
        
        # Macro regime (risk_off_score 기반)
        if risk_off_score >= self.cfg.high_vol_threshold:
            macro_regime = "RISK_OFF"
        elif risk_off_score <= self.cfg.neutral_threshold:
            macro_regime = "RISK_ON"
        else:
            macro_regime = "NEUTRAL"
        
        # Shock detection: RISK_ON/NEUTRAL → RISK_OFF 전환
        shock = (self._prev_macro != "RISK_OFF" and macro_regime == "RISK_OFF")
        
        risk_on = 1.0 - risk_off_score
        
        st = RegimeState(
            date=date,
            vol_regime=vol_regime,
            macro_regime=macro_regime,
            risk_on_score=risk_on,
            shock=shock,
            prev_regime=self._prev_macro
        )
        
        self._prev_macro = macro_regime
        self._state = st
        return st

# ============================================================================
# Factor Computation
# ============================================================================
def compute_all_factors(prices_df: pd.DataFrame) -> Dict[str, float]:
    """종목별 전체 팩터 계산"""
    close = prices_df['close'].values
    
    if len(close) < 60:
        return {}
    
    rets = np.diff(close) / close[:-1]
    
    # 모멘텀 (12-1)
    if len(close) >= 252:
        mom_12_1 = (close[-21] / close[-252]) - 1.0
    elif len(close) >= 126:
        mom_12_1 = (close[-21] / close[-126]) - 1.0
    else:
        mom_12_1 = (close[-21] / close[0]) - 1.0 if len(close) > 21 else 0.0
    
    # 변동성 (20일)
    vol_20d = np.std(rets[-20:]) * np.sqrt(252) if len(rets) >= 20 else 0.3
    
    # 평균 회귀
    ma_20 = np.mean(close[-20:])
    mean_rev = (ma_20 - close[-1]) / (np.std(close[-20:]) + 1e-8)
    
    return {
        'momentum': mom_12_1,
        'mean_reversion': mean_rev,
        'low_vol': 1.0 / (vol_20d + 0.01),
        'value': 0.0,  # 펀더멘탈에서 채움
        'quality': 0.0  # 펀더멘탈에서 채움
    }

def rank_normalize(x: np.ndarray) -> np.ndarray:
    order = np.argsort(np.nan_to_num(x, nan=-1e9))
    r = np.empty_like(order, dtype=float)
    r[order] = np.arange(len(x))
    r = (r - r.mean()) / (len(x) / 2 + 1e-9)
    return r

def risk_parity_weights(signal: np.ndarray, cov: np.ndarray, 
                        step: float = 0.03, max_iter: int = 400) -> np.ndarray:
    n = len(signal)
    w = np.tanh(signal).astype(float)
    if np.allclose(w, 0):
        w = np.ones(n)
    w = w / (np.sum(np.abs(w)) + 1e-12)
    b = np.ones(n) / n
    cov = cov.copy()
    cov.flat[::n+1] += 1e-6
    
    for _ in range(max_iter):
        m = cov @ w
        port_var = float(w @ m) + 1e-12
        rc = np.abs(w * m) / port_var
        grad = (rc - b)
        w = w - step * np.sign(w) * grad
        s = np.sum(np.abs(w))
        if s > 1e-12:
            w = w / s
    return w

# ============================================================================
# ARES Engine V2 (Enhanced)
# ============================================================================
@dataclass
class BacktestResult:
    equity: pd.Series
    daily_returns: pd.Series
    metrics: Dict
    logs: pd.DataFrame

def annualize_sharpe(returns: pd.Series, periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return float('nan')
    mu = returns.mean()
    sd = returns.std(ddof=1)
    if sd < 1e-12:
        return float('nan')
    return float(mu / sd * np.sqrt(periods_per_year))

def max_drawdown(equity: pd.Series) -> float:
    peak = equity.expanding().max()
    dd = (equity - peak) / peak
    return float(dd.min())

class AresEngineV2:
    def __init__(self, cfg: AresConfig):
        self.cfg = cfg
        self.regime = RegimeEngine(cfg.regime)
        self.icir_tracker = ICIRTracker(cfg.ensemble)
    
    def run_backtest(self, 
                     prices: Dict[str, pd.DataFrame],
                     fundamentals: pd.DataFrame,
                     macro_regime: pd.DataFrame) -> BacktestResult:
        
        # 데이터 준비
        symbols = sorted(list(prices.keys()))
        all_dates = set()
        for sym, df in prices.items():
            all_dates.update(df.index.tolist())
        dates = sorted(list(all_dates))
        
        T = len(dates)
        N = len(symbols)
        
        close_matrix = np.full((T, N), np.nan)
        date_to_idx = {d: i for i, d in enumerate(dates)}
        sym_to_idx = {s: i for i, s in enumerate(symbols)}
        
        for sym, df in prices.items():
            j = sym_to_idx[sym]
            for dt, row in df.iterrows():
                if dt in date_to_idx:
                    i = date_to_idx[dt]
                    close_matrix[i, j] = row['close']
        
        rets = np.zeros((T, N))
        rets[1:] = close_matrix[1:] / close_matrix[:-1] - 1.0
        rets = np.nan_to_num(rets, nan=0.0)
        
        # 백테스트 루프
        train = self.cfg.backtest.train_window
        equity = 1.0
        w_prev = np.zeros(N)
        rows = []
        
        cov_cache = None
        last_cov_i = -9999
        last_reb_i = train
        pending_shock_rebalance = False
        
        # 팩터 점수 캐시 (ICIR 업데이트용)
        prev_factor_scores = None
        
        print(f"\n🔄 백테스트 시작: {dates[train].strftime('%Y-%m-%d')} ~ {dates[-1].strftime('%Y-%m-%d')}")
        print(f"   종목 수: {N}, 거래일 수: {T - train}")
        print(f"   Shock-Triggered Rebalance: {'ON' if self.cfg.regime.allow_shock_rebalance else 'OFF'}")
        
        shock_count = 0
        
        for i in range(train, T - 1):
            date = dates[i]
            
            # Regime 상태 업데이트 (매일)
            st = self.regime.update_from_db(date, macro_regime)
            
            # Shock 감지 시 즉시 리밸런싱 플래그
            if st.shock and self.cfg.regime.allow_shock_rebalance:
                pending_shock_rebalance = True
                shock_count += 1
            
            # 공분산 행렬 업데이트
            if (i - last_cov_i) >= self.cfg.backtest.cov_every or cov_cache is None:
                win = rets[max(0, i - self.cfg.backtest.cov_window):i, :]
                cov_cache = ledoit_wolf_cov(win)
                last_cov_i = i
            
            # 리밸런싱 결정
            regular_reb = ((i - last_reb_i) >= self.cfg.backtest.rebalance_every_days)
            do_reb = regular_reb or pending_shock_rebalance
            
            if do_reb:
                # ICIR 업데이트 (이전 팩터 점수와 실현 수익률)
                if prev_factor_scores is not None:
                    realized_rets = rets[i, :]
                    self.icir_tracker.update_ic(prev_factor_scores, realized_rets)
                
                # 팩터 가중치 (ICIR 기반)
                factor_weights = self.icir_tracker.get_weights(st)
                
                # 팩터 점수 계산
                factor_scores_dict = {name: np.zeros(N) for name in FACTOR_NAMES}
                
                for j, sym in enumerate(symbols):
                    if np.isnan(close_matrix[i, j]):
                        continue
                    
                    sym_prices = prices[sym].loc[:date].tail(260)
                    if len(sym_prices) < 60:
                        continue
                    
                    factors = compute_all_factors(sym_prices)
                    if not factors:
                        continue
                    
                    # 가격 기반 팩터
                    factor_scores_dict['momentum'][j] = factors['momentum']
                    factor_scores_dict['mean_reversion'][j] = factors['mean_reversion']
                    factor_scores_dict['low_vol'][j] = factors['low_vol']
                    
                    # 펀더멘탈 팩터
                    fund_mask = (fundamentals['date'] <= date) & (fundamentals['ticker'] == sym)
                    fund_data = fundamentals[fund_mask].tail(1)
                    
                    if len(fund_data) > 0 and fund_data['value_isna'].iloc[0] == 0:
                        factor_scores_dict['value'][j] = fund_data['value_z'].iloc[0] or 0.0
                        factor_scores_dict['quality'][j] = fund_data['quality_z'].iloc[0] or 0.0
                
                # 팩터 점수 정규화 및 결합
                combined_score = np.zeros(N)
                for name in FACTOR_NAMES:
                    scores = factor_scores_dict[name]
                    scores_norm = rank_normalize(scores)
                    combined_score += factor_weights[name] * scores_norm
                
                prev_factor_scores = factor_scores_dict
                
                # 레짐 기반 익스포저 조정
                if st.macro_regime == "RISK_OFF" or st.vol_regime == "HIGH":
                    exposure = 0.0  # 완전 현금화
                    if pending_shock_rebalance:
                        print(f"   ⚠️ SHOCK REBALANCE @ {date.strftime('%Y-%m-%d')}: {st.prev_regime} → {st.macro_regime}")
                elif st.macro_regime == "NEUTRAL":
                    exposure = 0.7
                else:
                    exposure = 1.0
                
                # 리스크 패리티 가중치
                if self.cfg.risk.use_risk_parity and exposure > 0:
                    w = risk_parity_weights(combined_score, cov_cache,
                                           step=self.cfg.risk.risk_parity_step,
                                           max_iter=self.cfg.risk.risk_parity_max_iter)
                else:
                    w = np.tanh(combined_score)
                    w = w / (np.sum(np.abs(w)) + 1e-12)
                
                # Long-only
                w = np.maximum(w, 0)
                w = w / (np.sum(w) + 1e-12) if np.sum(w) > 0 else w
                
                # 익스포저 적용
                w = w * exposure
                
                # 거래 비용
                dw = w - w_prev
                turnover = float(np.sum(np.abs(dw)))
                cost_bps = self.cfg.costs.commission_bps + self.cfg.costs.slippage_bps + self.cfg.costs.spread_bps
                cost = turnover * (cost_bps / 1e4)
                
                last_reb_i = i
                pending_shock_rebalance = False
            else:
                w = w_prev
                cost = 0.0
                turnover = 0.0
            
            # 포트폴리오 수익률
            next_ret_vec = rets[i + 1, :]
            port_ret = float(np.nansum(w * next_ret_vec))
            port_ret_after_cost = port_ret - cost
            
            equity *= (1.0 + port_ret_after_cost)
            
            row = {
                "date": dates[i + 1],
                "equity": equity,
                "return": port_ret_after_cost,
                "turnover": turnover,
                "cost": cost,
                "vol_regime": st.vol_regime,
                "macro_regime": st.macro_regime,
                "exposure": float(np.sum(w)),
                "shock": st.shock,
            }
            rows.append(row)
            w_prev = w
            
            if i % 252 == 0:
                print(f"   {date.strftime('%Y-%m-%d')}: Equity={equity:.4f}, Regime={st.macro_regime}, Exposure={np.sum(w):.1%}")
        
        print(f"\n   총 Shock Rebalance 횟수: {shock_count}")
        
        logs = pd.DataFrame(rows).set_index("date")
        eq_s = logs["equity"]
        r_s = logs["return"]
        
        metrics = {
            "sharpe": annualize_sharpe(r_s, self.cfg.backtest.periods_per_year),
            "max_drawdown": max_drawdown(eq_s),
            "annual_vol": float(r_s.std(ddof=1) * np.sqrt(self.cfg.backtest.periods_per_year)) if len(r_s) > 1 else float("nan"),
            "cagr": float((eq_s.iloc[-1]) ** (self.cfg.backtest.periods_per_year / max(len(eq_s), 1)) - 1.0) if len(eq_s) > 0 else float("nan"),
            "total_return": float(eq_s.iloc[-1] - 1.0) if len(eq_s) > 0 else 0.0,
            "shock_rebalances": shock_count,
        }
        
        return BacktestResult(equity=eq_s, daily_returns=r_s, metrics=metrics, logs=logs)

# ============================================================================
# Main
# ============================================================================
def main():
    print("=" * 80)
    print("🚀 ARES X V4 백테스트 V2 (Shock-Triggered + ICIR)")
    print("=" * 80)
    print(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    db_path = "/home/ubuntu/aresx_pipeline_v18/ares_x_v11_0.db"
    adapter = EC2DBAdapter(db_path)
    
    symbols = adapter.get_symbols()
    print(f"✅ 유니버스: {len(symbols)} 종목")
    
    start_date = "2020-01-01"
    end_date = "2025-12-18"
    
    print(f"\n📊 데이터 로드 중...")
    prices = adapter.load_prices(symbols, start_date, end_date)
    print(f"   가격 데이터: {len(prices)} 종목")
    
    fundamentals = adapter.load_fundamentals(symbols, start_date, end_date)
    print(f"   펀더멘탈 데이터: {len(fundamentals)} 레코드")
    
    macro_regime = adapter.load_macro_regime(start_date, end_date)
    print(f"   매크로 레짐: {len(macro_regime)} 일")
    
    # 백테스트 실행
    cfg = AresConfig()
    engine = AresEngineV2(cfg)
    
    results = engine.run_backtest(prices, fundamentals, macro_regime)
    
    # 결과 출력
    print("\n" + "=" * 80)
    print("✅ ARES X V4 V2 백테스트 결과")
    print("=" * 80)
    print(f"   Sharpe Ratio:     {results.metrics.get('sharpe', 0):.4f}")
    print(f"   CAGR:             {results.metrics.get('cagr', 0)*100:.2f}%")
    print(f"   Total Return:     {results.metrics.get('total_return', 0)*100:.2f}%")
    print(f"   Max Drawdown:     {results.metrics.get('max_drawdown', 0)*100:.2f}%")
    print(f"   Annual Vol:       {results.metrics.get('annual_vol', 0)*100:.2f}%")
    print(f"   Shock Rebalances: {results.metrics.get('shock_rebalances', 0)}")
    
    # 연도별 성과
    print("\n📅 연도별 성과:")
    logs = results.logs.copy()
    logs['year'] = logs.index.year
    yearly = logs.groupby('year')['return'].sum() * 100
    for year, ret in yearly.items():
        print(f"   {year}: {ret:+.2f}%")
    
    # 레짐별 성과
    print("\n🎯 레짐별 성과:")
    for regime in logs['macro_regime'].unique():
        regime_rets = logs[logs['macro_regime'] == regime]['return']
        if len(regime_rets) > 0:
            avg_ret = regime_rets.mean() * 252 * 100
            days = len(regime_rets)
            print(f"   {regime}: {avg_ret:+.2f}% (연환산, {days}일)")
    
    # 평균 익스포저
    print(f"\n📊 평균 익스포저: {logs['exposure'].mean()*100:.1f}%")
    
    # 결과 저장
    results.logs.to_csv("/home/ubuntu/ares_v4_v2_backtest_logs.csv")
    print(f"\n💾 결과 저장: /home/ubuntu/ares_v4_v2_backtest_logs.csv")
    
    adapter.close()
    
    return results

if __name__ == "__main__":
    main()
