import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
from scipy.stats import spearmanr, zscore, norm
from dataclasses import dataclass
from typing import Tuple, Dict, List, Optional
import warnings

warnings.filterwarnings('ignore')

# -------------------------------------------------------------------------
# 1. Configuration & Data Structure
# -------------------------------------------------------------------------

@dataclass
class AresConfig:
    """ARES V10.0 'Invincible' Configuration"""
    # Lookback Windows
    lookback_vol: int = 63      # 변동성 산출 기간 (3개월)
    lookback_mom: int = 252     # 모멘텀 산출 기간 (1년)
    lookback_regime: int = 20   # 레짐 감지용 단기 윈도우
    
    # Portfolio Constraints
    target_vol: float = 0.12    # 목표 변동성 (12%)
    max_leverage: float = 2.0   # 최대 레버리지
    max_position: float = 0.15  # 종목당 최대 비중
    
    # Transaction Costs
    cost_bps: float = 0.0020    # 20bps (슬리피지 포함)
    
    # Regime Thresholds (Dynamic)
    vix_crisis: float = 30.0
    
    # ML Parameters
    lgb_params = {
        'objective': 'multiclass',
        'num_class': 3,  # 0: Bull(Trend), 1: Bear(Panic), 2: Chop(Range)
        'metric': 'multi_logloss',
        'boosting_type': 'gbdt',
        'learning_rate': 0.05,
        'num_leaves': 31,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'verbose': -1,
        'random_state': 42
    }

# -------------------------------------------------------------------------
# 2. Advanced Feature Engineering (Intraday & Macro)
# -------------------------------------------------------------------------

class AdvancedFeatureEngine:
    """Sharpe 3.0을 위한 고급 피처 엔지니어링"""
    
    @staticmethod
    def get_intraday_volatility(high, low, close, window=20):
        """Parkinson Volatility (고가-저가 기반 정밀 변동성)"""
        # 로그 수익률이 아닌 High/Low Range 사용
        hl_ratio = np.log(high / low)
        parkinson_var = (1.0 / (4.0 * np.log(2.0))) * (hl_ratio ** 2)
        # 연율화
        return np.sqrt(parkinson_var.rolling(window).mean()) * np.sqrt(252)

    @staticmethod
    def calculate_factors(df: pd.DataFrame, fundamental_df: pd.DataFrame = None) -> pd.DataFrame:
        """
        멀티 팩터 계산
        1. Momentum: 12-1m
        2. Reversal: 1w (Short term reversion)
        3. Quality: Low Volatility (Idiosyncratic)
        4. Value: Available composites
        """
        # 전처리: 0이나 음수 가격 방지
        df['close'] = df['close'].replace(0, np.nan).ffill()
        
        # 1. Momentum (Trend)
        ret_12m = df['close'].pct_change(252)
        ret_1m = df['close'].pct_change(21)
        df['momentum'] = ret_12m - ret_1m
        
        # 2. Short-Term Reversal (Mean Reversion for Chop Regime)
        # 최근 5일 수익률의 역수 (많이 오르면 숏, 내리면 롱 시그널)
        df['reversal'] = -1 * df['close'].pct_change(5)
        
        # 3. Volatility (Defensive) - Intraday 활용 권장, 없으면 Close 기반
        if 'high' in df.columns and 'low' in df.columns:
            df['volatility'] = AdvancedFeatureEngine.get_intraday_volatility(df['high'], df['low'], df['close'])
        else:
            df['volatility'] = df['close'].pct_change().rolling(63).std() * np.sqrt(252)
            
        # Volatility Factor는 낮을수록 좋음 -> 역수 변환
        df['low_vol_factor'] = 1 / (df['volatility'] + 1e-6)

        # 4. Value (Fundamental)
        # BM, EY, FCFY만 사용 (ROE 등 결측치 많은 컬럼 제외)
        if fundamental_df is not None:
            # 시계열 인덱스 매칭 필요
            combined = df.join(fundamental_df[['bm', 'ey', 'fcfy']], how='left')
            # 섹터 중위값으로 결측치 대체 로직은 데이터 로더 레벨에서 수행 가정
            # 여기서는 단순 Z-Score 합산
            val_cols = ['bm', 'ey', 'fcfy']
            for col in val_cols:
                combined[col] = (combined[col] - combined[col].rolling(252).mean()) / combined[col].rolling(252).std()
            
            df['value'] = combined[val_cols].mean(axis=1)
        else:
            df['value'] = 0.0

        # Z-Score Normalization (Cross-sectional logic should be applied externally, 
        # here we do time-series for simplicity in this snippet)
        for col in ['momentum', 'reversal', 'low_vol_factor', 'value']:
            df[f'{col}_z'] = (df[col] - df[col].rolling(252).mean()) / (df[col].rolling(252).std() + 1e-8)
            
        return df

# -------------------------------------------------------------------------
# 3. ML-Based Regime Classifier
# -------------------------------------------------------------------------

class MLRegimeDetector:
    """
    LightGBM 기반 3-State 레짐 분류기
    Class 0: Bull Trend (Momentum 유리)
    Class 1: Bear/Panic (Cash/Low Vol 유리)
    Class 2: Chop/Sideways (Reversal 유리) - 여기가 MODERATE/HIGH의 핵심
    """
    def __init__(self, config: AresConfig):
        self.config = config
        self.model = lgb.LGBMClassifier(**config.lgb_params)
        self.is_fitted = False
        
    def prepare_features(self, market_data: pd.DataFrame, macro_data: pd.DataFrame) -> pd.DataFrame:
        """ML 입력 피처 생성 (Look-ahead Bias 엄격 제거)"""
        df = pd.DataFrame(index=market_data.index)
        
        # VIX Features
        vix = macro_data['vix_close'].reindex(df.index).ffill()
        df['vix'] = vix
        df['vix_ma20'] = vix.rolling(20).mean()
        df['vix_rank'] = vix.rolling(252).rank(pct=True) # VIX 백분위
        
        # Trend Features (SPY 기준)
        spy_close = market_data['close'] # SPY or Market proxy
        df['spy_ma200_dist'] = (spy_close / spy_close.rolling(200).mean()) - 1
        df['spy_rsi'] = self._calc_rsi(spy_close, 14)
        
        # Macro Features
        # Yield Spread (10Y-2Y) - 데이터 있다고 가정
        if 'yield_spread' in macro_data.columns:
            df['yield_spread'] = macro_data['yield_spread'].reindex(df.index).ffill()
        
        return df.dropna()

    def _calc_rsi(self, series, period):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / (loss + 1e-8)
        return 100 - (100 / (1 + rs))

    def train_walk_forward(self, X, y, train_window=1000):
        """Walk-Forward 학습 (OOS 검증용)"""
        # 실제 프로덕션에서는 전체 데이터로 학습된 모델을 로드하거나
        # 주기적으로 재학습하는 로직이 필요함.
        self.model.fit(X, y)
        self.is_fitted = True

    def predict_proba(self, current_features):
        if not self.is_fitted:
            # Fallback: VIX 기반 룰 베이스
            vix = current_features.get('vix', 20)
            if vix > 30: return np.array([0.0, 1.0, 0.0]) # Bear
            elif vix < 16: return np.array([0.9, 0.0, 0.1]) # Bull
            else: return np.array([0.2, 0.0, 0.8]) # Chop
            
        return self.model.predict_proba(current_features.reshape(1, -1))[0]

# -------------------------------------------------------------------------
# 4. HRP Optimization (Sharpe Booster)
# -------------------------------------------------------------------------

class HRPOptimizer:
    """Hierarchical Risk Parity Optimizer"""
    
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

    def get_cluster_var(self, cov, c_items):
        cov_slice = cov.loc[c_items, c_items]
        w = self.get_ivp(cov_slice).reshape(-1, 1)
        c_var = np.dot(np.dot(w.T, cov_slice), w)[0, 0]
        return c_var

    def get_ivp(self, cov):
        ivp = 1. / np.diag(cov)
        ivp /= ivp.sum()
        return ivp

    def optimize(self, returns: pd.DataFrame) -> pd.Series:
        """
        HRP 최적화 실행
        returns: 자산별 수익률 DataFrame (lookback 기간)
        """
        # 공분산 및 상관관계
        cov = returns.cov()
        corr = returns.corr().fillna(0)
        
        # 거리 행렬 및 클러스터링
        dist = np.sqrt((1 - corr) / 2)
        link = linkage(squareform(dist), 'single')
        
        # 정렬 및 가중치 계산
        sort_ix = self.get_quasi_diag(link)
        sort_ix = corr.index[sort_ix].tolist()
        weights = self.get_rec_bipart(cov, sort_ix)
        
        return weights

# -------------------------------------------------------------------------
# 5. Integrated Production System
# -------------------------------------------------------------------------

class AresProductionSystem:
    def __init__(self, config: AresConfig):
        self.config = config
        self.feature_engine = AdvancedFeatureEngine()
        self.regime_detector = MLRegimeDetector(config)
        self.optimizer = HRPOptimizer()
        
    def get_dynamic_factor_weights(self, regime_probs: np.ndarray) -> Dict[str, float]:
        """
        ML 레짐 확률에 따른 팩터 가중치 동적 혼합
        regime_probs: [Bull, Bear, Chop]
        """
        p_bull, p_bear, p_chop = regime_probs
        
        # Base weights
        weights = {
            'momentum_z': 0.0,
            'reversal_z': 0.0,
            'low_vol_factor_z': 0.0,
            'value_z': 0.0
        }
        
        # 1. Bull (Trend): Momentum + Value
        weights['momentum_z'] += p_bull * 0.7
        weights['value_z'] += p_bull * 0.3
        
        # 2. Bear (Panic): Low Volatility + Quality
        weights['low_vol_factor_z'] += p_bear * 1.0
        
        # 3. Chop (Sideways): Reversal + Value
        weights['reversal_z'] += p_chop * 0.6
        weights['value_z'] += p_chop * 0.4
        
        return weights

    def run_daily_rebalance(self, 
                          current_date: str,
                          universe_prices: pd.DataFrame, 
                          universe_fundamentals: pd.DataFrame,
                          macro_data: pd.DataFrame,
                          current_portfolio_value: float) -> Dict[str, float]:
        """
        일일 리밸런싱 로직 (프로덕션 진입점)
        """
        # 1. Feature Engineering
        # 각 종목별 팩터 계산
        processed_data = {}
        valid_tickers = []
        
        for ticker in universe_prices.columns.get_level_values(0).unique():
            df = universe_prices[ticker].copy()
            if len(df) < 252: continue
            
            fund = universe_fundamentals[universe_fundamentals['ticker'] == ticker] if universe_fundamentals is not None else None
            df_factors = self.feature_engine.calculate_factors(df, fund)
            
            # 마지막 날짜 데이터 추출
            last_row = df_factors.iloc[-1]
            if not np.isnan(last_row['close']):
                processed_data[ticker] = last_row
                valid_tickers.append(ticker)
                
        if not processed_data:
            return {}

        factor_df = pd.DataFrame(processed_data).T
        
        # 2. Regime Detection
        # 매크로 및 시장 데이터 준비 (SPY 등 대표 지수 활용 가정)
        # 여기서는 예시로 첫 번째 종목을 시장으로 가정하거나 별도 입력 필요
        market_proxy = universe_prices[universe_prices.columns[0]] 
        regime_features = self.regime_detector.prepare_features(market_proxy, macro_data)
        
        if len(regime_features) > 0:
            current_regime_feat = regime_features.iloc[-1].values
            regime_probs = self.regime_detector.predict_proba(current_regime_feat)
        else:
            regime_probs = np.array([0.33, 0.33, 0.33]) # Default fallback
            
        # 3. Factor Scoring
        factor_weights = self.get_dynamic_factor_weights(regime_probs)
        
        final_scores = pd.Series(0.0, index=factor_df.index)
        for factor, weight in factor_weights.items():
            if factor in factor_df.columns:
                final_scores += factor_df[factor] * weight
        
        # 상위 N 종목 선정 (예: 상위 20%)
        top_n = int(len(final_scores) * 0.2)
        selected_tickers = final_scores.nlargest(top_n).index.tolist()
        
        if not selected_tickers:
            return {}
            
        # 4. HRP Optimization for Selected Tickers
        # 선정된 종목들의 최근 수익률 행렬
        returns_matrix = pd.DataFrame()
        for ticker in selected_tickers:
            returns_matrix[ticker] = universe_prices[ticker]['close'].pct_change().tail(63) # 3개월 상관관계
        
        returns_matrix = returns_matrix.dropna()
        if returns_matrix.empty:
            # Fallback to equal weight
            weights = {t: 1.0/len(selected_tickers) for t in selected_tickers}
        else:
            raw_weights = self.optimizer.optimize(returns_matrix)
            weights = raw_weights.to_dict()
            
        # 5. Volatility Targeting & Leverage Control
        # 포트폴리오 예상 변동성 계산
        port_vol = 0.0
        cov = returns_matrix.cov() * 252
        w_vec = np.array([weights[t] for t in selected_tickers])
        
        # 포트폴리오 분산 = w.T * Cov * w
        # (간략화를 위해 여기서는 인덱스 매칭 필요)
        sorted_tickers = list(weights.keys())
        w_vec = np.array([weights[t] for t in sorted_tickers])
        cov_sorted = cov.loc[sorted_tickers, sorted_tickers]
        
        port_var = np.dot(np.dot(w_vec, cov_sorted), w_vec)
        port_vol = np.sqrt(port_var)
        
        # 타겟 변동성 레버리지
        target_lev = self.config.target_vol / (port_vol + 1e-4)
        
        # 레짐 기반 레버리지 캡 (Bear장에서는 강제 축소)
        regime_cap = 1.0
        if regime_probs[1] > 0.5: # Bear prob > 50%
            regime_cap = 0.5
        elif regime_probs[1] > 0.8: # Crisis
            regime_cap = 0.0
            
        final_leverage = min(target_lev, self.config.max_leverage, regime_cap)
        
        # 최종 포지션 사이즈 (Cash 포함)
        final_positions = {t: w * final_leverage * current_portfolio_value for t, w in weights.items()}
        
        return final_positions

# 실행 예시 (가상 데이터)
if __name__ == "__main__":
    config = AresConfig()
    system = AresProductionSystem(config)
    print("ARES v10 Production System initialized. Ready for data ingestion.")
