import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
from numba import njit, float64, int64, boolean, types
from numba.experimental import jitclass
import matplotlib.pyplot as plt
import warnings

# 경고 무시 및 설정
warnings.filterwarnings('ignore')
np.random.seed(42)

# ==========================================
# 1. NUMBA OPTIMIZED CORE FUNCTIONS
# ==========================================

@njit(cache=True)
def calc_rolling_std(values, window):
    n = len(values)
    result = np.full(n, np.nan)
    for i in range(window, n):
        result[i] = np.std(values[i-window:i])
    return result

@njit(cache=True)
def calc_rolling_mean(values, window):
    n = len(values)
    result = np.full(n, np.nan)
    for i in range(window, n):
        result[i] = np.mean(values[i-window:i])
    return result

@njit(cache=True)
def calc_rsi(prices, window=14):
    n = len(prices)
    rsi = np.full(n, np.nan)
    deltas = np.diff(prices)
    seed = deltas[:window+1]
    up = seed[seed >= 0].sum() / window
    down = -seed[seed < 0].sum() / window
    rs = up / down if down != 0 else 0
    rsi[window] = 100 - (100 / (1 + rs))

    for i in range(window + 1, n):
        delta = deltas[i - 1]
        if delta > 0:
            up_val = delta
            down_val = 0.
        else:
            up_val = 0.
            down_val = -delta

        up = (up * (window - 1) + up_val) / window
        down = (down * (window - 1) + down_val) / window
        rs = up / down if down != 0 else 0
        rsi[i] = 100 - (100 / (1 + rs))
    return rsi

@njit(cache=True)
def calc_correlation(a, b, window):
    n = len(a)
    corr = np.full(n, np.nan)
    for i in range(window, n):
        # Simple correlation calculation
        sa = a[i-window:i]
        sb = b[i-window:i]
        ma = np.mean(sa)
        mb = np.mean(sb)
        num = np.sum((sa - ma) * (sb - mb))
        den = np.sqrt(np.sum((sa - ma)**2) * np.sum((sb - mb)**2))
        if den != 0:
            corr[i] = num / den
        else:
            corr[i] = 0.0
    return corr

@njit(cache=True)
def calc_icir_weights(factor_scores, forward_returns, window=60):
    """
    팩터별 IC(Information Coefficient)와 ICIR을 계산하여 동적 가중치 산출
    """
    n_days, n_factors = factor_scores.shape
    weights = np.zeros((n_days, n_factors))
    
    # 초기 가중치는 균등
    initial_w = 1.0 / n_factors
    weights[:window, :] = initial_w
    
    for t in range(window, n_days):
        ic_sum = 0.0
        ics = np.zeros(n_factors)
        
        for f in range(n_factors):
            # Rank Correlation approximation (Pearson on values for speed in Numba)
            # t-1 시점의 팩터 점수와 t 시점의 수익률 간의 상관관계
            f_scores = factor_scores[t-window:t, f]
            # Shifted returns: scores at t-1 predict return at t
            # Here assumes forward_returns are aligned such that ret[i] is return realized at i from i-1
            f_rets = forward_returns[t-window:t] 
            
            # Simple Pearson IC
            ma = np.mean(f_scores)
            mr = np.mean(f_rets)
            num = np.sum((f_scores - ma) * (f_rets - mr))
            den = np.sqrt(np.sum((f_scores - ma)**2) * np.sum((f_rets - mr)**2))
            
            ic = 0.0
            if den > 1e-9:
                ic = num / den
            
            # ICIR adjustment logic can be added here (IC / Std(IC))
            # 여기서는 단순 IC가 양수인 것만 가중치 부여 (Reversal 제외)
            if ic > 0:
                ics[f] = ic
            else:
                ics[f] = 0.0 # 음의 IC는 가중치 0 처리 (모멘텀 관점)
        
        total_ic = np.sum(ics)
        if total_ic > 1e-9:
            weights[t, :] = ics / total_ic
        else:
            # 모든 팩터가 작동하지 않으면 Low Vol 비중을 높이거나 균등
            weights[t, :] = initial_w
            
    return weights

# ==========================================
# 2. HIERARCHICAL RISK PARITY (HRP) ENGINE
# ==========================================

class HRPOptimizer:
    """
    HRP 포트폴리오 최적화 엔진
    Numba 호환성 문제로 Pure Python + Numpy 구현 (리밸런싱 시점에만 호출)
    """
    def __init__(self):
        pass
        
    def get_quasi_diag(self, link):
        link = link.astype(int)
        sort_ix = pd.Series([link[-1, 0], link[-1, 1]])
        num_items = link[-1, 3]
        while sort_ix.max() >= num_items:
            sort_ix.index = range(0, sort_ix.shape[0] * 2, 2)
            df0 = sort_ix[sort_ix >= num_items]
            i = df0.index
            j = df0.values - num_items
            sort_ix[i] = link[j, 0]
            df0 = pd.Series(link[j, 1], index=i + 1)
            sort_ix = pd.concat([sort_ix, df0])
            sort_ix = sort_ix.sort_index()
            sort_ix.index = range(sort_ix.shape[0])
        return sort_ix.tolist()

    def get_cluster_var(self, cov, c_items):
        cov_slice = cov.iloc[c_items, c_items]
        w = 1 / np.diag(cov_slice)  # Inverse Variance
        w /= w.sum()
        w = w.reshape(-1, 1)
        cluster_var = np.dot(np.dot(w.T, cov_slice), w)[0, 0]
        return cluster_var

    def get_rec_bipart(self, cov, sort_ix):
        w = pd.Series(1, index=sort_ix)
        c_items = [sort_ix]
        while len(c_items) > 0:
            c_items = [i[j:k] for i in c_items for j, k in ((0, len(i) // 2), (len(i) // 2, len(i))) if len(i) > 1]
            for i in range(0, len(c_items), 2):
                c_items0 = c_items[i]
                c_items1 = c_items[i + 1]
                c_var0 = self.get_cluster_var(cov, c_items0)
                c_var1 = self.get_cluster_var(cov, c_items1)
                alpha = 1 - c_var0 / (c_var0 + c_var1)
                w[c_items0] *= alpha
                w[c_items1] *= 1 - alpha
        return w

    def optimize(self, returns_df):
        """
        returns_df: 자산별 수익률 데이터프레임 (Look-back window)
        """
        # 공분산 및 상관행렬
        cov = returns_df.cov()
        corr = returns_df.corr()
        
        # 거리 행렬
        dist = np.sqrt((1 - corr) / 2)
        dist_mat = squareform(dist.values, checks=False)
        
        # 클러스터링 (Single Linkage)
        link = linkage(dist_mat, 'single')
        
        # 정렬
        sort_ix = self.get_quasi_diag(link)
        sort_ix = corr.index[sort_ix].tolist()
        
        # 재귀적 이분법을 통한 가중치 할당
        hrp_weights = self.get_rec_bipart(cov, sort_ix)
        
        return hrp_weights.reindex(returns_df.columns).fillna(0.0)

# ==========================================
# 3. ARES V11 SYSTEM CLASS
# ==========================================

class ARES_v11_System:
    def __init__(self, initial_capital=100_000, 
                 tc_bps=0.0050,  # 50bps
                 vol_target=0.15,
                 mdd_limit=0.15):
        self.initial_capital = initial_capital
        self.tc = tc_bps
        self.vol_target = vol_target
        self.mdd_limit = mdd_limit
        self.hrp = HRPOptimizer()
        
        # 하이퍼파라미터
        self.lookback_mom = [21*12, 21*6, 21*3] # 12M, 6M, 3M
        self.lookback_vol = 21 * 3
        self.rebalance_period = 21 # 월간
        self.regime_ma_window = 200
        
    def generate_features(self, prices):
        """
        벡터화된 팩터 계산
        """
        log_prices = np.log(prices)
        returns = prices.pct_change().fillna(0)
        
        features = {}
        
        # 1. Momentum Factors
        for lookback in self.lookback_mom:
            # 1개월 gap을 둔 모멘텀
            mom = prices.shift(21) / prices.shift(lookback) - 1
            features[f'mom_{lookback//21}m'] = mom
            
        # 2. Volatility Factor (Inverse Vol)
        vol = returns.rolling(self.lookback_vol).std()
        features['inv_vol'] = 1.0 / (vol + 1e-6)
        
        # 3. Reversal (Short term)
        features['reversal'] = -returns.rolling(5).mean()
        
        return features, returns

    def detect_regime(self, market_data):
        """
        개선된 다차원 레짐 감지
        - VIX Level + Trend
        - VIX Slope (변동성의 변화율)
        - Rates/Inflation Stress (TLT 상관성)
        """
        vix = market_data['VIX']
        spy = market_data['SPY']
        tlt = market_data['TLT']
        
        vix_ma = vix.rolling(200).mean()
        spy_ma = spy.rolling(200).mean()
        
        # 1. Market Trend (Bull/Bear)
        trend_score = np.where(spy > spy_ma, 1, -1)
        
        # 2. Volatility Regime
        vol_regime = np.where(vix > vix_ma * 1.2, -1, 1) # VIX 급등 시 부정적
        
        # 3. VIX Slope (Panic Check)
        vix_slope = vix.diff(5)
        panic_mode = np.where(vix_slope > 2.0, -2, 0) # VIX 급상승 패닉
        
        # 4. Correlation Break (Stock-Bond Correlation) -> 2022 방어
        # SPY와 TLT의 60일 상관관계가 양수이고(함께 하락), 둘 다 MA 아래면 "CRISIS"
        corr_sb = spy.pct_change().rolling(60).corr(tlt.pct_change())
        rates_stress = np.where((corr_sb > 0.3) & (spy < spy_ma) & (tlt < tlt.rolling(200).mean()), -2, 0)
        
        # 종합 점수 (-5 ~ +2)
        total_score = trend_score + vol_regime + panic_mode + rates_stress
        
        regimes = []
        for s in total_score:
            if s >= 2: regimes.append("BULL")
            elif s >= 0: regimes.append("NORMAL")
            elif s >= -2: regimes.append("CAUTION")
            elif s >= -4: regimes.append("TIGHTENING") # 긴축/하락
            else: regimes.append("CRISIS") # 공황
            
        return np.array(regimes), total_score

    def get_regime_params(self, regime_name):
        """
        레짐별 레버리지 및 전략 파라미터
        """
        params = {
            "BULL": {"leverage": 2.2, "top_k": 5, "min_cash": 0.0},
            "NORMAL": {"leverage": 1.5, "top_k": 6, "min_cash": 0.0},
            "CAUTION": {"leverage": 0.8, "top_k": 8, "min_cash": 0.2}, # 현금 20%
            "TIGHTENING": {"leverage": 0.5, "top_k": 10, "min_cash": 0.5}, # 현금 50%
            "CRISIS": {"leverage": 0.0, "top_k": 0, "min_cash": 1.0}, # 전량 현금
        }
        return params.get(regime_name, params["NORMAL"])

    def run_backtest(self, price_data, market_data):
        print(">>> Starting ARES v11 Backtest...")
        
        features, returns = self.generate_features(price_data)
        regimes, regime_scores = self.detect_regime(market_data)
        
        dates = returns.index
        n_days = len(dates)
        assets = returns.columns
        n_assets = len(assets)
        
        # 포트폴리오 상태 초기화
        portfolio_value = np.zeros(n_days)
        portfolio_value[0] = self.initial_capital
        weights = pd.DataFrame(0.0, index=dates, columns=assets)
        cash_weight = np.zeros(n_days)
        leverages = np.zeros(n_days)
        
        current_holdings = np.zeros(n_assets)
        current_cash = self.initial_capital
        
        # 팩터 점수 합성용 가중치 (초기값)
        factor_names = [k for k in features.keys()]
        # 팩터 데이터 3차원 배열화 [Time, Asset, Factor]
        # 메모리 효율을 위해 루프 내에서 처리하거나 미리 구성
        
        # Backtest Loop
        print(">>> Processing Loop...")
        
        # ICIR 계산을 위한 히스토리 저장소
        factor_history = {f: [] for f in factor_names}
        
        for t in range(252, n_days): # 1년치 데이터 확보 후 시작
            date = dates[t]
            prev_date = dates[t-1]
            
            # 1. Regime Check
            current_regime = regimes[t-1] # 어제 종가 기준 레짐
            regime_params = self.get_regime_params(current_regime)
            target_lev = regime_params['leverage']
            min_cash = regime_params['min_cash']
            top_k = regime_params['top_k']
            
            # 2. Risk Management: MDD Control
            # 현재 MDD 계산 (여기서는 간소화하여 t-1까지의 peak 대비)
            current_dd = 1.0 - portfolio_value[t-1] / np.maximum.accumulate(portfolio_value[:t]).max()
            if current_dd > self.mdd_limit * 0.8: # DD 한계의 80% 도달 시
                target_lev *= 0.5 # 레버리지 절반 축소
            if current_dd > self.mdd_limit:
                target_lev = 0.0 # 강제 청산
                
            # 3. Dynamic Factor Weighting (ICIR)
            # 여기서는 단순화를 위해 매 리밸런싱 때만 계산하지 않고, 
            # 레짐에 따라 팩터 선호도를 동적으로 조정
            # Bull/Normal -> Momentum 가중치 Up
            # Caution/Tightening -> Low Vol, Reversal 가중치 Up
            
            w_mom = 1.0
            w_vol = 1.0
            w_rev = 1.0
            
            if current_regime in ["BULL", "NORMAL"]:
                w_mom = 2.0
                w_vol = 0.5
                w_rev = 0.0
            elif current_regime in ["CAUTION"]:
                w_mom = 0.5
                w_vol = 1.5
                w_rev = 0.5
            else: # TIGHTENING, CRISIS
                w_mom = 0.0
                w_vol = 2.0
                w_rev = 1.0
                
            # 4. Signal Generation & Rebalancing
            # 리밸런싱 주기가 되었거나, 현금 100% (CRISIS) 상황에서 복귀할 때
            is_rebalance_day = (t % self.rebalance_period == 0)
            
            if is_rebalance_day and top_k > 0:
                # Calculate Composite Score
                scores = pd.Series(0.0, index=assets)
                valid_cnt = 0
                
                # 자산별 점수 합산
                m12 = features['mom_12m'].iloc[t-1]
                m6 = features['mom_6m'].iloc[t-1]
                m3 = features['mom_3m'].iloc[t-1]
                iv = features['inv_vol'].iloc[t-1]
                rev = features['reversal'].iloc[t-1]
                
                # Z-Score Normalization (Cross-sectional)
                def zscore(s): return (s - s.mean()) / (s.std() + 1e-6)
                
                combined = (
                    w_mom * (zscore(m12) + zscore(m6) + zscore(m3)) +
                    w_vol * zscore(iv) +
                    w_rev * zscore(rev)
                )
                
                # Select Top K
                top_assets = combined.nlargest(top_k).index
                
                # 5. HRP Optimization
                # Top K 자산들에 대해서만 과거 60일 수익률로 HRP 수행
                hist_ret = returns.loc[dates[t-60]:dates[t-1], top_assets]
                if len(hist_ret) > 30:
                    hrp_w = self.hrp.optimize(hist_ret)
                else:
                    hrp_w = pd.Series(1.0/top_k, index=top_assets)
                
                # 변동성 타겟팅 (Volatility Scaled Weights)
                # 포트폴리오 예측 변동성 계산
                port_vol = np.sqrt(hrp_w.T @ hist_ret.cov() @ hrp_w) * np.sqrt(252)
                vol_scalar = self.vol_target / (port_vol + 1e-4)
                vol_scalar = min(vol_scalar, 1.5) # 최대 스케일링 제한
                
                # 최종 타겟 비중 (레버리지 적용 전 순수 비중)
                target_weights = hrp_w * (1.0 - min_cash) # 현금 비중 제외한 나머지 할당
                
                # 레버리지 적용 (BULL Market 등)
                final_leverage = target_lev * vol_scalar
                
                # 거래 실행 로직 (간소화: 종가 거래)
                # Turnover Cost 반영
                new_holdings_val = target_weights * final_leverage * portfolio_value[t-1]
                
            elif top_k == 0: # CRISIS -> All Cash
                target_weights = pd.Series(0.0, index=assets)
                final_leverage = 0.0
                
            else:
                # 리밸런싱 아님 -> 웨이트 드리프트 허용 (Buy & Hold 효과)
                # 비중은 가격 변동에 따라 자동 조정됨
                target_weights = weights.iloc[t-1] # 명목상 유지 (실제론 보유수량 유지)
                final_leverage = leverages[t-1]
            
            # 6. PnL Calculation
            # 실제 수익률 계산 (t-1 보유량 * t 수익률)
            # 여기서는 단순화를 위해 비중 기반 계산 후 비용 차감
            
            if is_rebalance_day:
                # 리밸런싱 비용 계산
                prev_w = weights.iloc[t-1] if t > 0 else pd.Series(0, index=assets)
                # 현재 목표 비중 (레버리지 포함)
                curr_target_w_lev = target_weights.reindex(assets).fillna(0) * final_leverage
                
                turnover = np.abs(curr_target_w_lev - prev_w).sum()
                cost = turnover * self.tc
                
                # 수익률 적용 (자산 수익률 * 레버리지된 비중)
                # 주의: 리밸런싱은 시가(Open) 혹은 종가(Close) 기준이나 여기선 전일 결정->당일 종가 반영 가정
                day_ret = (returns.iloc[t] * curr_target_w_lev).sum()
                
                # 현금 수익률 (Risk Free Rate 가정, 연 2% flat)
                cash_pos = 1.0 - curr_target_w_lev.sum()
                day_ret += cash_pos * (0.02/252)
                
                net_ret = day_ret - cost
                
                weights.iloc[t] = curr_target_w_lev
                leverages[t] = final_leverage
                
            else:
                # 드리프트된 비중으로 수익 발생
                prev_w = weights.iloc[t-1]
                day_ret = (returns.iloc[t] * prev_w).sum()
                
                cash_pos = 1.0 - prev_w.sum()
                day_ret += cash_pos * (0.02/252)
                
                net_ret = day_ret
                
                # 비중 업데이트 (드리프트 반영)
                new_w = prev_w * (1 + returns.iloc[t])
                weights.iloc[t] = new_w # 정규화 하지 않음 (레버리지 효과 반영)
                leverages[t] = leverages[t-1] # 참조용

            portfolio_value[t] = portfolio_value[t-1] * (1 + net_ret)
            
        return pd.Series(portfolio_value, index=dates), weights, regimes

# ==========================================
# 4. SYNTHETIC DATA GENERATOR (Production Simulation)
# ==========================================

def generate_synthetic_data(start_date='2016-01-01', end_date='2024-12-31'):
    """
    현실적인 백테스트를 위해 상관관계 구조가 반영된 합성 데이터 생성
    SPY, TLT, 섹터 ETF 등 생성
    """
    dates = pd.date_range(start_date, end_date, freq='B')
    n = len(dates)
    
    # 팩터: Market, Rates, Oil, Volatility
    np.random.seed(42)
    market_factor = np.random.normal(0.0004, 0.01, n) # SPY Trend
    rate_factor = np.random.normal(0, 0.008, n)       # TLT inverse
    oil_factor = np.random.normal(0, 0.015, n)        # Inflation
    
    # 2022년 시나리오 주입 (Market Down, Rates Up/TLT Down, Oil Up)
    mask_2022 = (dates.year == 2022)
    market_factor[mask_2022] -= 0.001
    rate_factor[mask_2022] += 0.001  # 금리 상승
    oil_factor[mask_2022] += 0.002   # 인플레
    
    # 자산별 민감도 (Beta)
    assets = {
        'SPY':  {'base': 1.0, 'mkt': 1.0, 'rate': 0.2, 'oil': -0.1},
        'QQQ':  {'base': 1.0, 'mkt': 1.2, 'rate': 0.4, 'oil': -0.2}, # 금리에 민감
        'IWM':  {'base': 1.0, 'mkt': 1.1, 'rate': 0.1, 'oil': 0.1},
        'TLT':  {'base': 1.0, 'mkt': -0.3, 'rate': -1.5, 'oil': -0.3}, # 금리 상승시 폭락
        'IEF':  {'base': 1.0, 'mkt': -0.1, 'rate': -0.8, 'oil': -0.1},
        'GLD':  {'base': 1.0, 'mkt': 0.0, 'rate': -0.2, 'oil': 0.4},
        'DBC':  {'base': 1.0, 'mkt': 0.3, 'rate': 0.1, 'oil': 0.9}, # 원자재
        'XLK':  {'base': 1.0, 'mkt': 1.1, 'rate': 0.3, 'oil': -0.1},
        'XLE':  {'base': 1.0, 'mkt': 0.6, 'rate': 0.0, 'oil': 0.8},
        'XLF':  {'base': 1.0, 'mkt': 0.9, 'rate': -0.4, 'oil': 0.0},
        'XLV':  {'base': 1.0, 'mkt': 0.7, 'rate': 0.0, 'oil': 0.0},
        'XLP':  {'base': 1.0, 'mkt': 0.5, 'rate': 0.0, 'oil': -0.1}, # Low Vol Proxy
        'XLU':  {'base': 1.0, 'mkt': 0.4, 'rate': -0.2, 'oil': 0.1}, # Low Vol Proxy
    }
    
    price_data = pd.DataFrame(index=dates)
    
    for name, betas in assets.items():
        # 수익률 생성
        ret = (betas['mkt'] * market_factor + 
               betas['rate'] * rate_factor + 
               betas['oil'] * oil_factor + 
               np.random.normal(0, 0.005, n)) # Idiosyncratic risk
        
        # 가격 변환
        price = 100 * np.cumprod(1 + ret)
        price_data[name] = price
        
    # VIX 생성 (Market 하락시 급등)
    vix_base = 15
    vix_noise = np.random.normal(0, 1, n)
    vix_shock = np.where(market_factor < -0.01, 5, 0) # 시장 폭락시 쇼크
    vix_series = vix_base - (market_factor * 1000) + np.cumsum(vix_noise * 0.1) + vix_shock
    vix_series = np.maximum(vix_series, 9)
    
    market_data = pd.DataFrame(index=dates)
    market_data['SPY'] = price_data['SPY']
    market_data['TLT'] = price_data['TLT']
    market_data['VIX'] = vix_series
    
    return price_data, market_data

# ==========================================
# 5. EXECUTION & ANALYSIS
# ==========================================

def calculate_metrics(portfolio_series):
    returns = portfolio_series.pct_change().dropna()
    cagr = (portfolio_series.iloc[-1] / portfolio_series.iloc[0]) ** (252 / len(portfolio_series)) - 1
    vol = returns.std() * np.sqrt(252)
    sharpe = (cagr - 0.02) / vol # Risk free 2%
    
    # MDD
    cum_ret = (1 + returns).cumprod()
    peak = cum_ret.cummax()
    dd = (cum_ret - peak) / peak
    mdd = dd.min()
    
    # Sortino
    downside_vol = returns[returns < 0].std() * np.sqrt(252)
    sortino = (cagr - 0.02) / downside_vol if downside_vol > 0 else 0
    
    return {
        "CAGR": cagr,
        "Vol": vol,
        "Sharpe": sharpe,
        "Sortino": sortino,
        "MDD": mdd
    }

def main():
    # 1. Data Generation
    print("Generating Synthetic Data (2016-2024)...")
    prices, market_info = generate_synthetic_data()
    
    # 2. Initialize System
    ares = ARES_v11_System(initial_capital=100000, tc_bps=0.0050, vol_target=0.15)
    
    # 3. Run Backtest
    port_value, weights, regimes = ares.run_backtest(prices, market_info)
    
    # 4. Analyze Results
    metrics = calculate_metrics(port_value)
    
    print("\n" + "="*30)
    print("   ARES v11 Performance Report   ")
    print("="*30)
    print(f"Final Balance: ${port_value.iloc[-1]:,.2f}")
    print(f"CAGR         : {metrics['CAGR']*100:.2f}%")
    print(f"Volatility   : {metrics['Vol']*100:.2f}%")
    print(f"Sharpe Ratio : {metrics['Sharpe']:.2f} (Target: 3.0+)")
    print(f"MDD          : {metrics['MDD']*100:.2f}% (Target: < -15%)")
    print(f"Sortino      : {metrics['Sortino']:.2f}")
    
    # Year over Year Analysis
    print("\n[Yearly Performance]")
    yearly_ret = port_value.resample('Y').last().pct_change()
    for year, ret in yearly_ret.items():
        print(f"{year.year}: {ret*100:.2f}%")
        
    # Plotting
    # plt.figure(figsize=(12, 6))
    # plt.plot(port_value, label='ARES v11')
    # plt.plot(prices['SPY'] / prices['SPY'].iloc[0] * 100000, label='SPY Benchmark', alpha=0.5)
    # plt.title('ARES v11 vs SPY')
    # plt.legend()
    # plt.show()

if __name__ == "__main__":
    main()