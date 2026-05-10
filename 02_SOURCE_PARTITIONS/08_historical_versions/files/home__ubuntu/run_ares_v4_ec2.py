#!/usr/bin/env python3
"""
ARES X V4 엔진을 EC2 DB에 연결하여 백테스트 실행
"""
import sys
import os
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ============================================================================
# Configuration (ares_x_v4/config.py 간소화 버전)
# ============================================================================
@dataclass
class CostModelConfig:
    commission_bps: float = 0.5
    slippage_bps: float = 2.0
    spread_bps: float = 3.0
    use_adv: bool = True
    adv_lookback: int = 20
    sigma_lookback: int = 20
    impact_k: float = 0.10

@dataclass
class NeutralityConfig:
    enable_score: bool = True
    enable_weights_post: bool = True
    max_iter: int = 30
    risk_aversion: float = 1.0
    gross_limit: float = 2.0
    turnover_limit: float = 0.30
    w_min: float = -0.05
    w_max: float = 0.05
    turnover_penalty: float = 0.0

@dataclass
class RiskModelConfig:
    covariance_method: str = "ledoit_wolf"
    use_risk_parity: bool = True
    risk_parity_budgets_from_scores: bool = True
    risk_parity_step: float = 0.03
    risk_parity_max_iter: int = 400

@dataclass
class RegimeConfig:
    allow_extra_rebalance_on_shock: bool = True
    fred_lag_days: int = 1
    in_count: int = 3
    out_count: int = 5
    high_vol_threshold_annual: float = 0.22
    low_vol_threshold_annual: float = 0.14

@dataclass
class EnsembleConfig:
    top_k: int = 4
    cap_per_factor: float = 0.45
    ic_window: int = 252
    boost_value_quality_on_inversion: float = 1.25
    boost_lowvol_on_highvol: float = 1.35
    damp_momentum_on_highvol: float = 0.85

@dataclass
class BacktestConfig:
    rebalance_every_days: int = 21  # 월간 리밸런싱
    gross_exposure_target: float = 1.0
    vol_target_annual: float = 0.12
    turnover_limit: float = 0.35
    turnover_penalty: float = 2.0
    periods_per_year: int = 252

@dataclass
class CPUOptConfig:
    enable: bool = True
    train_window: int = 252
    cov_window: int = 60
    cov_every: int = 20

@dataclass
class AresConfig:
    cpu: CPUOptConfig = field(default_factory=CPUOptConfig)
    costs: CostModelConfig = field(default_factory=CostModelConfig)
    neutrality: NeutralityConfig = field(default_factory=NeutralityConfig)
    risk: RiskModelConfig = field(default_factory=RiskModelConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    ensemble: EnsembleConfig = field(default_factory=EnsembleConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

# ============================================================================
# EC2 DB Adapter
# ============================================================================
class EC2DBAdapter:
    """EC2 SQLite DB에서 데이터를 로드하는 어댑터"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        
    def load_prices(self, symbols: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
        """daily_ohlcv에서 가격 데이터 로드"""
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
            df = df.set_index('date')
            df = df.dropna(subset=['close'])
            
            if len(df) > 0:
                prices[symbol] = df
                
        return prices
    
    def load_fundamentals(self, symbols: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """fundamentals_pit_daily에서 펀더멘탈 데이터 로드"""
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
        """macro_regime_daily에서 레짐 데이터 로드"""
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
    
    def load_sleeve_scores(self, start_date: str, end_date: str) -> pd.DataFrame:
        """sleeve_scores_monthly에서 사전 계산된 점수 로드"""
        query = """
            SELECT asof_month as date, ticker, score, score_core, score_fund, 
                   mom_z, vol_z, value_z, coverage_score
            FROM sleeve_scores_monthly
            WHERE asof_month BETWEEN ? AND ?
            ORDER BY asof_month, ticker
        """
        df = pd.read_sql_query(query, self.conn, params=[start_date, end_date])
        df['date'] = pd.to_datetime(df['date'])
        return df
    
    def get_symbols(self) -> List[str]:
        """유니버스 종목 목록"""
        query = "SELECT DISTINCT ticker FROM fundamentals_pit_daily ORDER BY ticker"
        df = pd.read_sql_query(query, self.conn)
        return df['ticker'].tolist()
    
    def close(self):
        self.conn.close()

# ============================================================================
# Risk & Covariance
# ============================================================================
def sample_cov(returns: np.ndarray) -> np.ndarray:
    x = returns - np.nanmean(returns, axis=0, keepdims=True)
    x = np.nan_to_num(x, nan=0.0)
    return (x.T @ x) / max(1, (x.shape[0]-1))

def ledoit_wolf_cov(returns: np.ndarray) -> np.ndarray:
    S = sample_cov(returns)
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

def cov_matrix(returns: np.ndarray, method: str) -> np.ndarray:
    if method.lower() == "ledoit_wolf":
        return ledoit_wolf_cov(returns)
    return sample_cov(returns)

# ============================================================================
# Alpha & Portfolio
# ============================================================================
def winsorize(x: np.ndarray, pct: float = 0.01) -> np.ndarray:
    lo = np.nanpercentile(x, pct * 100)
    hi = np.nanpercentile(x, (1 - pct) * 100)
    return np.clip(x, lo, hi)

def zscore(x: np.ndarray) -> np.ndarray:
    mu = np.nanmean(x)
    sd = np.nanstd(x) + 1e-8
    return (x - mu) / sd

def rank_normalize(x: np.ndarray) -> np.ndarray:
    """랭크 정규화: -1 ~ 1 범위"""
    order = np.argsort(np.nan_to_num(x, nan=-1e9))
    r = np.empty_like(order, dtype=float)
    r[order] = np.arange(len(x))
    r = (r - r.mean()) / (len(x) / 2 + 1e-9)
    return r

def compute_factors(prices_df: pd.DataFrame) -> Dict[str, float]:
    """단일 종목의 팩터 계산"""
    close = prices_df['close'].values
    volume = prices_df['volume'].values
    
    if len(close) < 60:
        return {}
    
    # 모멘텀 (12-1)
    if len(close) >= 252:
        mom_12_1 = (close[-21] / close[-252]) - 1.0
    elif len(close) >= 126:
        mom_12_1 = (close[-21] / close[-126]) - 1.0
    else:
        mom_12_1 = (close[-21] / close[0]) - 1.0 if len(close) > 21 else 0.0
    
    # 단기 모멘텀 (1개월)
    mom_1m = (close[-1] / close[-21]) - 1.0 if len(close) >= 21 else 0.0
    
    # 변동성 (20일)
    rets = np.diff(close) / close[:-1]
    vol_20d = np.std(rets[-20:]) * np.sqrt(252) if len(rets) >= 20 else 0.3
    
    # 평균 회귀
    ma_20 = np.mean(close[-20:])
    mean_rev = (ma_20 - close[-1]) / (np.std(close[-20:]) + 1e-8)
    
    return {
        'momentum': mom_12_1,
        'mom_1m': mom_1m,
        'volatility': vol_20d,
        'mean_reversion': mean_rev,
        'low_vol': 1.0 / (vol_20d + 0.01)
    }

def risk_parity_weights(signal: np.ndarray, cov: np.ndarray, 
                        step: float = 0.03, max_iter: int = 400) -> np.ndarray:
    """리스크 패리티 가중치 계산"""
    n = len(signal)
    w = np.tanh(signal).astype(float)
    if np.allclose(w, 0):
        w = np.ones(n)
    w = w / (np.sum(np.abs(w)) + 1e-12)
    
    b = np.ones(n) / n  # 균등 리스크 버짓
    
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
# Regime Engine
# ============================================================================
@dataclass
class RegimeState:
    date: pd.Timestamp
    vol_regime: str
    macro_regime: str
    risk_on_score: float
    shock: bool

class RegimeEngine:
    def __init__(self, cfg: RegimeConfig):
        self.cfg = cfg
        self._state: Optional[RegimeState] = None
        self._in_counter = 0
        self._out_counter = 0
    
    def update_from_db(self, date: pd.Timestamp, macro_df: pd.DataFrame) -> RegimeState:
        """DB의 macro_regime_daily 데이터를 사용하여 레짐 상태 업데이트"""
        
        if date in macro_df.index:
            row = macro_df.loc[date]
            risk_off_score = row.get('risk_off_score', 0.5)
            regime_label = row.get('regime_label', 'NEUTRAL')
            vol_score = row.get('vol_score', 0.0)
        else:
            # 가장 가까운 이전 날짜 사용
            prev_dates = macro_df.index[macro_df.index <= date]
            if len(prev_dates) > 0:
                row = macro_df.loc[prev_dates[-1]]
                risk_off_score = row.get('risk_off_score', 0.5)
                regime_label = row.get('regime_label', 'NEUTRAL')
                vol_score = row.get('vol_score', 0.0)
            else:
                risk_off_score = 0.5
                regime_label = 'NEUTRAL'
                vol_score = 0.0
        
        # Vol regime 결정
        if vol_score is not None and not np.isnan(vol_score):
            if vol_score >= 1.0:
                vol_regime = "HIGH"
            elif vol_score <= -0.5:
                vol_regime = "LOW"
            else:
                vol_regime = "NORMAL"
        else:
            vol_regime = "NORMAL"
        
        # Macro regime
        macro_regime = regime_label if regime_label else "NEUTRAL"
        
        # Risk-on score (0~1, 높을수록 risk-on)
        risk_on = 1.0 - (risk_off_score if risk_off_score else 0.5)
        
        # Shock detection
        prev_vol = self._state.vol_regime if self._state else "NORMAL"
        shock = (prev_vol != vol_regime) and (vol_regime == "HIGH")
        
        st = RegimeState(
            date=date,
            vol_regime=vol_regime,
            macro_regime=macro_regime,
            risk_on_score=risk_on,
            shock=shock
        )
        self._state = st
        return st

# ============================================================================
# ARES Engine (Optimized)
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

class AresEngineOptimized:
    def __init__(self, cfg: AresConfig):
        self.cfg = cfg
        self.regime = RegimeEngine(cfg.regime)
    
    def run_backtest(self, 
                     prices: Dict[str, pd.DataFrame],
                     fundamentals: pd.DataFrame,
                     macro_regime: pd.DataFrame,
                     sleeve_scores: Optional[pd.DataFrame] = None) -> BacktestResult:
        """
        백테스트 실행
        
        Args:
            prices: Dict[symbol, DataFrame with OHLCV]
            fundamentals: DataFrame with fundamentals_pit_daily
            macro_regime: DataFrame with macro_regime_daily
            sleeve_scores: Optional pre-computed scores
        """
        # 공통 날짜 정렬
        symbols = sorted(list(prices.keys()))
        all_dates = set()
        for sym, df in prices.items():
            all_dates.update(df.index.tolist())
        dates = sorted(list(all_dates))
        
        # 가격 행렬 구성
        T = len(dates)
        N = len(symbols)
        
        close_matrix = np.full((T, N), np.nan)
        volume_matrix = np.full((T, N), np.nan)
        
        date_to_idx = {d: i for i, d in enumerate(dates)}
        sym_to_idx = {s: i for i, s in enumerate(symbols)}
        
        for sym, df in prices.items():
            j = sym_to_idx[sym]
            for dt, row in df.iterrows():
                if dt in date_to_idx:
                    i = date_to_idx[dt]
                    close_matrix[i, j] = row['close']
                    volume_matrix[i, j] = row['volume']
        
        # 수익률 계산
        rets = np.zeros((T, N))
        rets[1:] = close_matrix[1:] / close_matrix[:-1] - 1.0
        rets = np.nan_to_num(rets, nan=0.0)
        
        # 백테스트 루프
        train = self.cfg.cpu.train_window
        equity = 1.0
        w_prev = np.zeros(N)
        rows = []
        
        cov_cache = None
        last_cov_i = -9999
        
        print(f"\n🔄 백테스트 시작: {dates[train].strftime('%Y-%m-%d')} ~ {dates[-1].strftime('%Y-%m-%d')}")
        print(f"   종목 수: {N}, 거래일 수: {T - train}")
        
        for i in range(train, T - 1):
            date = dates[i]
            
            # Regime 상태 업데이트
            st = self.regime.update_from_db(date, macro_regime)
            
            # 공분산 행렬 업데이트
            if (i - last_cov_i) >= self.cfg.cpu.cov_every or cov_cache is None:
                win = rets[max(0, i - self.cfg.cpu.cov_window):i, :]
                cov_cache = cov_matrix(win, self.cfg.risk.covariance_method)
                last_cov_i = i
            
            # 리밸런싱 결정
            do_reb = ((i - train) % self.cfg.backtest.rebalance_every_days == 0)
            
            if do_reb:
                # 팩터 점수 계산
                factor_scores = np.zeros(N)
                
                for j, sym in enumerate(symbols):
                    if np.isnan(close_matrix[i, j]):
                        continue
                    
                    # 가격 기반 팩터
                    sym_prices = prices[sym].loc[:date].tail(260)
                    if len(sym_prices) < 60:
                        continue
                    
                    factors = compute_factors(sym_prices)
                    if not factors:
                        continue
                    
                    # 모멘텀 + 저변동성 조합
                    mom_score = factors.get('momentum', 0.0)
                    lowvol_score = factors.get('low_vol', 0.0)
                    
                    # 펀더멘탈 점수 (있으면)
                    fund_mask = (fundamentals['date'] <= date) & (fundamentals['ticker'] == sym)
                    fund_data = fundamentals[fund_mask].tail(1)
                    
                    if len(fund_data) > 0 and fund_data['value_isna'].iloc[0] == 0:
                        value_z = fund_data['value_z'].iloc[0] or 0.0
                        quality_z = fund_data['quality_z'].iloc[0] or 0.0
                        # 2모델 블렌딩: Core(0.3) + Fund(0.7)
                        core_score = 0.6 * mom_score + 0.4 * lowvol_score
                        fund_score = 0.4 * mom_score + 0.3 * value_z + 0.3 * quality_z
                        factor_scores[j] = 0.3 * core_score + 0.7 * fund_score
                    else:
                        # Core only
                        factor_scores[j] = 0.6 * mom_score + 0.4 * lowvol_score
                
                # 랭크 정규화
                factor_scores = rank_normalize(factor_scores)
                
                # 레짐 기반 익스포저 조정
                if st.macro_regime == "RISK_OFF" or st.vol_regime == "HIGH":
                    exposure = 0.0  # 0% 투자, 100% 현금
                elif st.macro_regime == "NEUTRAL":
                    exposure = 0.7
                else:
                    exposure = 1.0
                
                # 리스크 패리티 가중치
                if self.cfg.risk.use_risk_parity:
                    w = risk_parity_weights(factor_scores, cov_cache,
                                           step=self.cfg.risk.risk_parity_step,
                                           max_iter=self.cfg.risk.risk_parity_max_iter)
                else:
                    # 단순 점수 기반
                    w = np.tanh(factor_scores)
                    w = w / (np.sum(np.abs(w)) + 1e-12)
                
                # Long-only 제약 (음수 가중치 제거)
                w = np.maximum(w, 0)
                w = w / (np.sum(w) + 1e-12)
                
                # 익스포저 적용
                w = w * exposure
                
                # 거래 비용 계산
                dw = w - w_prev
                turnover = float(np.sum(np.abs(dw)))
                cost_bps = self.cfg.costs.commission_bps + self.cfg.costs.slippage_bps + self.cfg.costs.spread_bps
                cost = turnover * (cost_bps / 1e4)
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
            }
            rows.append(row)
            w_prev = w
            
            # 진행 상황 출력
            if i % 252 == 0:
                print(f"   {date.strftime('%Y-%m-%d')}: Equity={equity:.4f}, Regime={st.macro_regime}")
        
        logs = pd.DataFrame(rows).set_index("date")
        eq_s = logs["equity"]
        r_s = logs["return"]
        
        # 메트릭 계산
        metrics = {
            "sharpe": annualize_sharpe(r_s, self.cfg.backtest.periods_per_year),
            "max_drawdown": max_drawdown(eq_s),
            "annual_vol": float(r_s.std(ddof=1) * np.sqrt(self.cfg.backtest.periods_per_year)) if len(r_s) > 1 else float("nan"),
            "cagr": float((eq_s.iloc[-1]) ** (self.cfg.backtest.periods_per_year / max(len(eq_s), 1)) - 1.0) if len(eq_s) > 0 else float("nan"),
            "total_return": float(eq_s.iloc[-1] - 1.0) if len(eq_s) > 0 else 0.0,
        }
        
        return BacktestResult(equity=eq_s, daily_returns=r_s, metrics=metrics, logs=logs)

# ============================================================================
# Main
# ============================================================================
def main():
    print("=" * 80)
    print("🚀 ARES X V4 백테스트 (EC2 DB 연결)")
    print("=" * 80)
    print(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # DB 연결
    db_path = "/home/ubuntu/aresx_pipeline_v18/ares_x_v11_0.db"
    adapter = EC2DBAdapter(db_path)
    
    # 유니버스 로드
    symbols = adapter.get_symbols()
    print(f"✅ 유니버스: {len(symbols)} 종목")
    print(f"   {symbols[:10]}...")
    
    # 데이터 로드
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
    engine = AresEngineOptimized(cfg)
    
    results = engine.run_backtest(prices, fundamentals, macro_regime)
    
    # 결과 출력
    print("\n" + "=" * 80)
    print("✅ ARES X V4 백테스트 결과")
    print("=" * 80)
    print(f"   Sharpe Ratio:  {results.metrics.get('sharpe', 0):.4f}")
    print(f"   CAGR:          {results.metrics.get('cagr', 0)*100:.2f}%")
    print(f"   Total Return:  {results.metrics.get('total_return', 0)*100:.2f}%")
    print(f"   Max Drawdown:  {results.metrics.get('max_drawdown', 0)*100:.2f}%")
    print(f"   Annual Vol:    {results.metrics.get('annual_vol', 0)*100:.2f}%")
    
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
            print(f"   {regime}: {avg_ret:+.2f}% (연환산)")
    
    # 결과 저장
    results.logs.to_csv("/home/ubuntu/ares_v4_backtest_logs.csv")
    print(f"\n💾 결과 저장: /home/ubuntu/ares_v4_backtest_logs.csv")
    
    adapter.close()
    
    return results

if __name__ == "__main__":
    main()
