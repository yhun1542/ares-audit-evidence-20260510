"""
ARES v11.6 - 현실적 프로덕션 버전
===================================
v9.7 FINAL PRODUCTION 기반 + 철저한 검증

핵심 특징:
1. 신호 1일 lag 적용 (t일 신호 → t+1일 체결)
2. 레짐 전날 데이터 사용
3. 거래비용 20bp + 슬리페이지 10bp
4. 월간 리밸런싱 (21일)
5. DD Control (-8% 경고, -12% 정지)
6. Walk-Forward 검증 포함
"""

import numpy as np
import pandas as pd
import sqlite3
from numba import njit, prange
from dataclasses import dataclass, field
from typing import Dict, List
import logging
import warnings
warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

# =============================================================================
# 설정
# =============================================================================
@dataclass
class AresV116Config:
    """ARES v11.6 현실적 프로덕션 설정"""
    
    # Database
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    # VIX 레짐 임계값 (v9.7 기반)
    vix_ultra_low: float = 13.0
    vix_low: float = 16.0
    vix_moderate: float = 19.0
    vix_high: float = 23.0
    vix_crisis: float = 28.0
    
    # 레짐별 레버리지 (v9.7 기반)
    leverage_ultra_low: float = 1.5
    leverage_low: float = 0.8
    leverage_moderate: float = 1.0
    leverage_high: float = 0.3
    leverage_crisis: float = 0.0
    
    # 트렌드 필터
    trend_filter_enabled: bool = True
    ma_period: int = 200
    bear_market_mult: float = 0.7
    
    # 팩터 가중치
    momentum_weight: float = 0.90
    quality_weight: float = 0.10
    
    # DD Control (v9.7 핵심)
    dd_halt_threshold: float = -0.08
    dd_stop_threshold: float = -0.12
    dd_recovery_threshold: float = 0.04
    
    # 포트폴리오
    n_stocks: int = 10
    max_position: float = 0.20
    
    # 리밸런싱 (월간)
    rebalance_days: int = 21
    
    # 비용 (현실적)
    transaction_cost: float = 0.002  # 20bp 편도
    slippage: float = 0.001          # 10bp
    
    # 신호 lag (핵심!)
    signal_lag: int = 1  # 1일 지연

# =============================================================================
# Numba 최적화 함수
# =============================================================================
@njit(parallel=True, cache=True)
def fast_momentum(prices, lookback, skip=21):
    """모멘텀 계산 (21일 스킵)"""
    n_dates, n_assets = prices.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback + skip, n_dates):
        for j in range(n_assets):
            if prices[i - skip, j] > 0 and prices[i - lookback, j] > 0:
                result[i, j] = prices[i - skip, j] / prices[i - lookback, j] - 1
    return result

@njit(parallel=True, cache=True)
def fast_volatility(returns, lookback):
    """변동성 계산"""
    n_dates, n_assets = returns.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback, n_dates):
        for j in range(n_assets):
            window = returns[i-lookback:i, j]
            valid = ~np.isnan(window)
            if valid.sum() >= lookback // 2:
                result[i, j] = np.nanstd(window) * np.sqrt(252)
    return result

@njit(parallel=True, cache=True)
def fast_quality(returns, lookback):
    """Quality 팩터 (수익률 안정성)"""
    n_dates, n_assets = returns.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback, n_dates):
        for j in range(n_assets):
            window = returns[i-lookback:i, j]
            valid = ~np.isnan(window)
            if valid.sum() >= lookback // 2:
                mean_ret = np.nanmean(window)
                std_ret = np.nanstd(window)
                if std_ret > 0:
                    result[i, j] = mean_ret / std_ret  # 수익률 / 변동성
    return result

@njit(parallel=True, cache=True)
def fast_rank_normalize(data):
    """Cross-sectional rank normalization"""
    n_dates, n_assets = data.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(n_dates):
        row = data[i, :]
        valid_mask = ~np.isnan(row)
        n_valid = valid_mask.sum()
        if n_valid > 0:
            valid_vals = row[valid_mask]
            sorted_idx = np.argsort(valid_vals)
            ranks = np.empty(n_valid, dtype=np.float64)
            for k, idx in enumerate(sorted_idx):
                ranks[idx] = (k + 1) / n_valid
            result[i, valid_mask] = ranks
    return result

@njit(cache=True)
def fast_backtest_realistic(signals, returns, regime_exposure_arr, regime_indices,
                            top_k, rebalance_days, tc_one_way, slippage, signal_lag,
                            dd_halt, dd_stop, dd_recovery):
    """
    현실적 백테스트 (v9.7 기반)
    - 신호 lag 적용
    - 레짐 전날 데이터 사용
    - DD Control
    """
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float64)
    current_weights = np.zeros(n_assets, dtype=np.float64)
    
    # DD Control 상태
    portfolio_value = 1.0
    peak_value = 1.0
    dd_halted = False
    
    for i in range(signal_lag, n_dates):
        # 현재 DD 계산
        current_dd = (portfolio_value / peak_value) - 1.0
        
        # DD Control 로직
        if current_dd <= dd_stop:
            # 완전 정지: 모든 포지션 청산
            if np.sum(np.abs(current_weights)) > 0:
                turnover = np.sum(np.abs(current_weights))
                cost = turnover * (tc_one_way + slippage / 10000)
                portfolio_returns[i] = -cost
                current_weights = np.zeros(n_assets, dtype=np.float64)
            continue
        elif current_dd <= dd_halt:
            dd_halted = True
        elif dd_halted and current_dd >= dd_recovery:
            dd_halted = False
        
        # DD Halt 상태면 레버리지 50% 감소
        dd_mult = 0.5 if dd_halted else 1.0
        
        total_cost = 0.0
        
        # 신호는 signal_lag일 전 데이터 사용 (핵심!)
        signal_idx = i - signal_lag
        
        if signal_idx >= 0 and signal_idx % rebalance_days == 0:
            day_signals = signals[signal_idx, :]
            valid_mask = ~np.isnan(day_signals)
            
            if valid_mask.sum() >= top_k:
                sorted_idx = np.argsort(-day_signals)
                new_weights = np.zeros(n_assets, dtype=np.float64)
                count = 0
                for idx in sorted_idx:
                    if valid_mask[idx] and count < top_k:
                        new_weights[idx] = 1.0 / top_k
                        count += 1
                
                turnover = np.sum(np.abs(new_weights - current_weights))
                # 거래비용 = 턴오버 × (편도비용 + 슬리페이지)
                total_cost = turnover * (tc_one_way + slippage / 10000)
                current_weights = new_weights
        
        # 당일 수익률
        day_return = returns[i, :]
        
        # 레짐도 전날 데이터 사용 (핵심!)
        regime_idx_for_day = regime_indices[max(0, i - 1)]
        base_leverage = regime_exposure_arr[regime_idx_for_day]
        leverage = base_leverage * dd_mult
        
        port_ret = 0.0
        for j in range(n_assets):
            if current_weights[j] > 0 and not np.isnan(day_return[j]):
                port_ret += current_weights[j] * day_return[j]
        
        daily_ret = port_ret * leverage - total_cost
        portfolio_returns[i] = daily_ret
        
        # 포트폴리오 가치 업데이트
        portfolio_value *= (1 + daily_ret)
        if portfolio_value > peak_value:
            peak_value = portfolio_value
    
    return portfolio_returns

# =============================================================================
# 메인 엔진
# =============================================================================
class AresV116Engine:
    def __init__(self, config: AresV116Config):
        self.config = config
        self.prices = None
        self.returns = None
        self.vix = None
        self.factors = None
        
    def load_data(self):
        """데이터 로드"""
        logger.info("데이터 로드 중...")
        
        UNIVERSE = [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "UNH", "JNJ",
            "JPM", "V", "PG", "XOM", "HD", "CVX", "MA", "ABBV", "MRK", "LLY",
            "PEP", "KO", "COST", "AVGO", "TMO", "MCD", "WMT", "CSCO", "ACN", "ABT",
            "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "XLF", "XLK", "XLE", "XLV"
        ]
        
        conn = sqlite3.connect(self.config.db_path)
        
        symbols_str = "'" + "','".join(UNIVERSE) + "'"
        query = f"""
        SELECT date, symbol, adj_close as close
        FROM daily_ohlcv
        WHERE symbol IN ({symbols_str}) AND date >= '2016-01-01'
        ORDER BY date, symbol
        """
        df = pd.read_sql_query(query, conn)
        df['date'] = pd.to_datetime(df['date'])
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        self.prices = df.pivot(index='date', columns='symbol', values='close')
        self.prices = self.prices.ffill()  # ffill만 사용 (bfill 금지!)
        
        # VIX 로드
        vix_df = pd.read_sql_query(
            "SELECT date, close as vix FROM vix WHERE date >= '2016-01-01'", conn
        )
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        self.vix = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')['vix']
        
        conn.close()
        
        self.returns = self.prices.pct_change().fillna(0)
        
        valid_cols = self.prices.columns[self.prices.notna().mean() > 0.8]
        self.prices = self.prices[valid_cols]
        self.returns = self.returns[valid_cols]
        
        logger.info(f"로드 완료: {len(self.prices.columns)}개 종목, {len(self.prices)}일")
        
    def compute_factors(self):
        """팩터 계산"""
        logger.info("팩터 계산 중...")
        
        prices_arr = self.prices.values.astype(np.float64)
        returns_arr = self.returns.values.astype(np.float64)
        
        # 모멘텀 (12-1, 21일 스킵)
        mom_12_1 = fast_momentum(prices_arr, 252, skip=21)
        
        # Quality
        quality = fast_quality(returns_arr, 60)
        
        self.factors = {
            'momentum': pd.DataFrame(fast_rank_normalize(mom_12_1), 
                                    index=self.prices.index, columns=self.prices.columns),
            'quality': pd.DataFrame(fast_rank_normalize(quality), 
                                   index=self.prices.index, columns=self.prices.columns)
        }
        
    def detect_regimes(self):
        """레짐 감지 (전날 데이터 사용)"""
        logger.info("레짐 감지 중...")
        
        cfg = self.config
        vix_aligned = self.vix.reindex(self.prices.index).ffill()
        
        # 전날 VIX 사용 (핵심!)
        vix_lagged = vix_aligned.shift(1)
        
        regimes = pd.Series("MODERATE", index=self.prices.index)
        regimes[vix_lagged < cfg.vix_ultra_low] = "ULTRA_LOW"
        regimes[(vix_lagged >= cfg.vix_ultra_low) & (vix_lagged < cfg.vix_low)] = "LOW"
        regimes[(vix_lagged >= cfg.vix_low) & (vix_lagged < cfg.vix_moderate)] = "MODERATE"
        regimes[(vix_lagged >= cfg.vix_moderate) & (vix_lagged < cfg.vix_high)] = "HIGH"
        regimes[vix_lagged >= cfg.vix_crisis] = "CRISIS"
        
        # 트렌드 필터 (전날 MA 사용)
        if cfg.trend_filter_enabled and 'SPY' in self.prices.columns:
            spy_ma = self.prices['SPY'].rolling(cfg.ma_period).mean().shift(1)
            spy_price = self.prices['SPY'].shift(1)
            bear_market = spy_price < spy_ma
            self.bear_market = bear_market
        else:
            self.bear_market = pd.Series(False, index=self.prices.index)
        
        self.regimes = regimes
        
    def backtest(self, start_date, end_date):
        """백테스트 실행"""
        logger.info(f"백테스트: {start_date} ~ {end_date}")
        
        cfg = self.config
        
        # 기간 필터
        mask = (self.prices.index >= start_date) & (self.prices.index <= end_date)
        prices = self.prices.loc[mask]
        returns = self.returns.loc[mask]
        regimes = self.regimes.loc[mask]
        bear_market = self.bear_market.loc[mask]
        
        # 팩터 결합
        combined = (
            self.factors['momentum'].loc[mask] * cfg.momentum_weight +
            self.factors['quality'].loc[mask] * cfg.quality_weight
        )
        
        # 공통 컬럼
        common_cols = combined.columns.intersection(returns.columns)
        signals_arr = combined[common_cols].values.astype(np.float64)
        returns_arr = returns[common_cols].values.astype(np.float64)
        
        # 레짐 설정
        regime_names = ["ULTRA_LOW", "LOW", "MODERATE", "HIGH", "CRISIS"]
        regime_exposure = {
            "ULTRA_LOW": cfg.leverage_ultra_low,
            "LOW": cfg.leverage_low,
            "MODERATE": cfg.leverage_moderate,
            "HIGH": cfg.leverage_high,
            "CRISIS": cfg.leverage_crisis
        }
        
        # 트렌드 필터 적용
        regime_exposure_final = {}
        for name in regime_names:
            regime_exposure_final[name] = regime_exposure[name]
        
        regime_to_idx = {n: i for i, n in enumerate(regime_names)}
        regime_indices = np.array([regime_to_idx.get(regimes.loc[d], 2) for d in prices.index], dtype=np.int32)
        regime_exposure_arr = np.array([regime_exposure_final[n] for n in regime_names], dtype=np.float64)
        
        # 트렌드 필터: 약세장이면 레버리지 감소
        for i, d in enumerate(prices.index):
            if bear_market.loc[d]:
                regime_exposure_arr[regime_indices[i]] *= cfg.bear_market_mult
        
        # 백테스트 실행
        port_rets = fast_backtest_realistic(
            signals_arr, returns_arr, regime_exposure_arr, regime_indices,
            top_k=cfg.n_stocks,
            rebalance_days=cfg.rebalance_days,
            tc_one_way=cfg.transaction_cost,
            slippage=cfg.slippage * 10000,  # bps로 변환
            signal_lag=cfg.signal_lag,
            dd_halt=cfg.dd_halt_threshold,
            dd_stop=cfg.dd_stop_threshold,
            dd_recovery=cfg.dd_recovery_threshold
        )
        
        port_rets_series = pd.Series(port_rets, index=prices.index)
        
        # 성과 계산
        metrics = self.calc_metrics(port_rets_series)
        
        # 연도별 성과
        yearly = {}
        for year in range(int(start_date[:4]), int(end_date[:4]) + 1):
            year_rets = port_rets_series[port_rets_series.index.year == year]
            if len(year_rets) > 0:
                yearly[str(year)] = self.calc_metrics(year_rets)
        
        # 레짐별 성과
        regime_perf = {}
        for regime in regime_names:
            regime_mask = regimes == regime
            regime_rets = port_rets_series[regime_mask]
            if len(regime_rets) > 0:
                regime_perf[regime] = {
                    'days': len(regime_rets),
                    **self.calc_metrics(regime_rets)
                }
        
        return {
            'period': f"{start_date} ~ {end_date}",
            'metrics': metrics,
            'yearly': yearly,
            'regime_performance': regime_perf
        }
    
    def calc_metrics(self, returns):
        """성과 지표 계산"""
        if len(returns) == 0:
            return {'sharpe': 0, 'mdd': 0, 'annual_return': 0, 'total_return': 0}
        
        cum_ret = (1 + returns).cumprod()
        total_ret = cum_ret.iloc[-1] - 1
        n_years = len(returns) / 252
        annual_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0
        vol = returns.std() * np.sqrt(252)
        sharpe = annual_ret / (vol + 1e-10)
        
        max_cum = cum_ret.cummax()
        dd = cum_ret / max_cum - 1
        mdd = dd.min()
        
        return {
            'sharpe': sharpe,
            'mdd': mdd,
            'annual_return': annual_ret,
            'total_return': total_ret,
            'volatility': vol
        }
    
    def walk_forward_validation(self):
        """Walk-Forward 검증"""
        logger.info("\n" + "=" * 60)
        logger.info("Walk-Forward 검증")
        logger.info("=" * 60)
        
        # IS: 2016-2020, OOS: 2021-2024
        is_result = self.backtest('2016-01-01', '2020-12-31')
        oos_result = self.backtest('2021-01-01', '2024-12-31')
        full_result = self.backtest('2016-01-01', '2024-12-31')
        
        logger.info(f"\nIn-Sample (2016-2020):")
        logger.info(f"  Sharpe: {is_result['metrics']['sharpe']:.2f}")
        logger.info(f"  MDD: {is_result['metrics']['mdd']:.2%}")
        logger.info(f"  Annual Return: {is_result['metrics']['annual_return']:.2%}")
        
        logger.info(f"\nOut-of-Sample (2021-2024):")
        logger.info(f"  Sharpe: {oos_result['metrics']['sharpe']:.2f}")
        logger.info(f"  MDD: {oos_result['metrics']['mdd']:.2%}")
        logger.info(f"  Annual Return: {oos_result['metrics']['annual_return']:.2%}")
        
        logger.info(f"\nFull Period (2016-2024):")
        logger.info(f"  Sharpe: {full_result['metrics']['sharpe']:.2f}")
        logger.info(f"  MDD: {full_result['metrics']['mdd']:.2%}")
        logger.info(f"  Annual Return: {full_result['metrics']['annual_return']:.2%}")
        
        # 과적합 분석
        sharpe_decay = (oos_result['metrics']['sharpe'] - is_result['metrics']['sharpe']) / (is_result['metrics']['sharpe'] + 1e-10)
        if sharpe_decay >= 0:
            logger.info(f"\n과적합 분석: OOS >= IS → 과적합 없음 ✅")
        elif sharpe_decay > -0.3:
            logger.info(f"\n과적합 분석: Sharpe 감소 {sharpe_decay:.1%} → 경미한 과적합 ⚠")
        else:
            logger.info(f"\n과적합 분석: Sharpe 감소 {sharpe_decay:.1%} → 심각한 과적합 ❌")
        
        # 연도별 성과
        logger.info("\n연도별 성과:")
        for year, metrics in full_result['yearly'].items():
            logger.info(f"  {year}: Sharpe={metrics['sharpe']:.2f}, Return={metrics['annual_return']:.2%}, MDD={metrics['mdd']:.2%}")
        
        return {
            'is': is_result,
            'oos': oos_result,
            'full': full_result,
            'sharpe_decay': sharpe_decay
        }

# =============================================================================
# 메인
# =============================================================================
def main():
    logger.info("=" * 70)
    logger.info("ARES v11.6 - 현실적 프로덕션 버전")
    logger.info("=" * 70)
    
    config = AresV116Config()
    engine = AresV116Engine(config)
    
    engine.load_data()
    engine.compute_factors()
    engine.detect_regimes()
    
    # Walk-Forward 검증
    results = engine.walk_forward_validation()
    
    logger.info("\n" + "=" * 70)
    logger.info("최종 결과")
    logger.info("=" * 70)
    logger.info(f"Full Period Sharpe: {results['full']['metrics']['sharpe']:.2f}")
    logger.info(f"OOS Sharpe: {results['oos']['metrics']['sharpe']:.2f}")
    logger.info(f"Sharpe Decay: {results['sharpe_decay']:.1%}")
    
    # 설정 출력
    logger.info("\n설정:")
    logger.info(f"  신호 lag: {config.signal_lag}일")
    logger.info(f"  거래비용: {config.transaction_cost*10000:.0f}bp")
    logger.info(f"  슬리페이지: {config.slippage*10000:.0f}bp")
    logger.info(f"  리밸런싱: {config.rebalance_days}일")
    logger.info(f"  DD Halt: {config.dd_halt_threshold:.0%}")
    logger.info(f"  DD Stop: {config.dd_stop_threshold:.0%}")

if __name__ == "__main__":
    main()
