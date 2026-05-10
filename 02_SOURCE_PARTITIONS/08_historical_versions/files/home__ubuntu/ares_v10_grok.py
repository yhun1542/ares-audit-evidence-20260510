import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Tuple
import sqlite3
from sklearn.preprocessing import StandardScaler
import xgboost as xgb
from scipy.stats import entropy

@dataclass
class AresV10Config:
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    vix_thresholds: dict = None
    target_vol: float = 0.12
    # ... (기존 config 상속 가정)

class ImprovedRegimeDetector:
    def __init__(self, config: AresV10Config):
        self.config = config
        self.model = xgb.XGBClassifier(n_estimators=200, max_depth=6, learning_rate=0.03, subsample=0.8, random_state=42)
        self.scaler = StandardScaler()
        self.is_trained = False
        self.prev_regime = None

    def prepare_regime_features(self, as_of_date: str) -> np.ndarray:
        conn = sqlite3.connect(self.config.db_path)
        query = """
        SELECT m.date, m.vix_level, m.spy_above_200ma, mi.value as yield_spread, mi2.value as credit_spread
        FROM market_regime_daily m
        LEFT JOIN macro_indicators mi ON m.date = mi.date AND mi.indicator_name = '10Y-2Y Spread'
        LEFT JOIN macro_indicators mi2 ON m.date = mi2.date AND mi2.indicator_name = 'HY-IG Spread'
        WHERE m.date <= ?
        ORDER BY m.date DESC LIMIT 63
        """
        df = pd.read_sql_query(query, conn, params=(as_of_date,), parse_dates=['date'])
        conn.close()
        if len(df) < 30:
            return np.zeros(10)
        
        feats = {
            'vix_level': df['vix_level'].iloc[-1],
            'vix_mom_21d': df['vix_level'].pct_change(21).iloc[-1],
            'spy_ma': df['spy_above_200ma'].astype(int).iloc[-1],
            'yield_spread': df['yield_spread'].fillna(method='ffill').iloc[-1],
            'credit_spread': df['credit_spread'].fillna(method='ffill').iloc[-1],
            'yield_twist': df['yield_spread'].diff().rolling(21).mean().iloc[-1],
            'vix_skew': df['vix_level'].rolling(21).skew().iloc[-1],
            'regime_score': df.get('regime_score', 0).fillna(0).rolling(21).mean().iloc[-1]
        }
        feat_array = np.array(list(feats.values()) + [entropy(np.random.rand(5))])  # placeholder for more
        return self.scaler.transform(feat_array.reshape(1, -1)) if self.is_trained else feat_array.reshape(1, -1)

    def train(self, train_dates: list):
        # Fetch historical data for training (expand to full history)
        conn = sqlite3.connect(self.config.db_path)
        # ... (full training logic: prepare X,y from historical regimes)
        X = np.random.rand(1000, 10)  # Placeholder: replace with real data prep
        y = np.random.randint(0, 5, 1000)
        self.model.fit(X, y)
        self.is_trained = True
        conn.close()

    def detect(self, as_of_date: str, current_vol: float = 0.15) -> Tuple[str, float]:
        feats = self.prepare_regime_features(as_of_date)
        regime_prob = self.model.predict_proba(feats)[0]
        regime_idx = np.argmax(regime_prob)
        regimes = ['ULTRA_LOW', 'LOW', 'MODERATE', 'HIGH', 'CRISIS']
        regime = regimes[regime_idx]
        
        base_lev = [1.8, 0.8, 1.2, 0.4, 0.0][regime_idx]
        lev = min(3.0, self.config.target_vol / current_vol * np.max(regime_prob))
        lev *= base_lev / 1.0  # Normalize
        
        if self.prev_regime and self.prev_regime != regime:
            lev = 0.7 * lev + 0.3 * getattr(self.config, f'leverage_{self.prev_regime.lower().replace("-","")}', 0.5)
        self.prev_regime = regime
        return regime, lev
import pandas as pd
import numpy as np
from scipy.stats import zscore, winsorize, spearmanr
from scipy.signal import argrelextrema
import sqlite3
from sklearn.preprocessing import quantile_transform

class ImprovedFactorEngine:
    def __init__(self, config: AresV10Config):
        self.config = config

    def load_data_pit(self, symbols: list, end_date: str, days_back: int = 500) -> dict:
        conn = sqlite3.connect(self.config.db_path)
        # daily_ohlcv clean
        df_daily = pd.read_sql_query("SELECT * FROM daily_ohlcv WHERE symbol IN ({}) AND date <= ?".format(','.join('?'*len(symbols))), 
                                     conn, params=symbols + [end_date])
        df_daily = df_daily.drop_duplicates(['symbol', 'date'], keep='last').sort_values(['symbol', 'date'])
        df_daily['returns'] = df_daily.groupby('symbol')['close'].pct_change()
        
        # intraday agg for microstructure
        df_intra = pd.read_sql_query("SELECT * FROM intraday_ohlcv WHERE timeframe='1m' AND symbol IN ({}) AND date(timestamp) <= ?".format(','.join('?'*len(symbols))), 
                                     conn, params=symbols + [end_date])
        df_intra = df_intra.set_index('timestamp').groupby([pd.Grouper(freq='5T'), 'symbol']).agg({
            'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'
        }).reset_index()
        df_intra['dollar_vol'] = df_intra['close'] * df_intra['volume']
        df_intra['ofi'] = (df_intra['volume'].diff() > 0).astype(int).diff().fillna(0) * df_intra['volume']  # Simplified OFI
        df_intra_daily = df_intra.groupby(['symbol', df_intra.timestamp.dt.date])['ofi'].mean().reset_index(name='ofi')
        
        # fundamentals PIT clean
        df_fund = pd.read_sql_query("SELECT * FROM fundamentals_pit_daily WHERE ticker IN ({}) AND date <= ?".format(','.join('?'*len(symbols))), 
                                    conn, params=symbols + [end_date])
        # NaN handling: sector median ffill + interp
        df_fund = df_fund.sort_values(['ticker', 'date'])
        for col in ['bm', 'ey', 'fcfy', 'gp_a', 'leverage', 'gross_margin']:
            df_fund[col] = df_fund.groupby('ticker')[col].transform(lambda x: x.fillna(x.median()).interpolate(method='linear'))
        df_fund = df_fund.dropna(subset=['bm', 'ey'], thresh=0.7*len(df_fund))  # Drop high NaN
        
        conn.close()
        return {'daily': df_daily, 'intra': df_intra_daily, 'fund': df_fund}

    def calculate_features(self, data: dict, regime: str) -> pd.DataFrame:
        daily = data['daily'].pivot(index='date', columns='symbol', values='close')
        returns = daily.pct_change()
        
        # Momentum
        mom_12_1 = MomentumFeatures.momentum_12_1(daily)
        
        # Volatility
        vol_real = VolatilityFeatures.realized_volatility(returns, 20)
        vol_ratio = VolatilityFeatures.volatility_ratio(returns)
        
        # Microstructure
        intra_daily = data['intra'].pivot(index='timestamp', columns='symbol', values='ofi')
        ofi_mean = intra_daily.mean(axis=0, skipna=True)  # Daily OFI avg
        amihud = MicrostructureFeatures.amihud_illiquidity(returns.iloc[-20:], daily * daily.volume, 20)  # Approx dollar vol
        
        # Value/Quality from fund
        fund_latest = data['fund'].groupby('ticker').last()
        value = ValueFeatures.value_composite(fund_latest)
        quality = QualityFeatures.quality_composite(fund_latest)
        
        # Multi-TF: Overnight gap
        overnight = daily / daily.shift(1).shift(-1, axis=0) - 1  # Approx
        
        # Zscore + winsorize
        feats = pd.DataFrame({
            'mom_12_1': zscore(winsorize(mom_12_1, limits=[0.01, 0.01])),
            'vol_real': -zscore(winsorize(vol_real, limits=[0.01, 0.01])),  # Low vol
            'ofi': zscore(winsorize(ofi_mean, limits=[0.01, 0.01])),
            'value': zscore(winsorize(value, limits=[0.01, 0.01])),
            'quality': zscore(winsorize(quality, limits=[0.01, 0.01])),
            'overnight': zscore(winsorize(overnight.iloc[-1], limits=[0.01, 0.01]))
        })
        
        # Regime-specific boost
        if regime in ['MODERATE', 'HIGH']:
            feats['quality'] *= 2.0
            feats['vol_real'] *= 1.5
            feats['mom_12_1'] *= 0.5
        
        # Entropy selection (top features)
        feat_corr = feats.corr().abs().mean()
        top_feats = feat_corr.nlargest(5).index
        feats = feats[top_feats]
        
        return feats.mean(axis=1)  # Combined score per stock? Wait, per column

    def get_combined_score(self, feats: pd.DataFrame, regime: str) -> pd.Series:
        weights = self.get_dynamic_weights(feats)  # ICIR based
        return np.dot(feats, list(weights.values()))

    def get_dynamic_weights(self, feats: pd.DataFrame, forward_returns: pd.Series = None) -> dict:
        if forward_returns is None:
            return {col: 1/len(feats.columns) for col in feats.columns}
        icirs = {}
        for col in feats.columns:
            ic = self.calculate_ic(feats[col], forward_returns)
            icirs[col] = max(ic, 0)
        total = sum(icirs.values())
        return {k: v/total if total > 0 else 1/len(icirs) for k,v in icirs.items()}

    def calculate_ic(self, factor: pd.Series, fwd_ret: pd.Series) -> float:
        valid = ~(factor.isna() | fwd_ret.isna())
        if valid.sum() < 30: return 0.0
        return spearmanr(factor[valid], fwd_ret[valid])[0]
import xgboost as xgb
from pytorch_tabnet.tab_model import TabNetRegressor
import torch
from typing import Dict, List

class ImprovedMLModel:
    def __init__(self, config: AresV10Config):
        self.config = config
        self.xgb_reg = xgb.XGBRegressor(n_estimators=200, max_depth=6, learning_rate=0.03, subsample=0.8, objective='reg:quantileerror', quantile_alpha=0.7)
        self.tabnet = TabNetRegressor(n_steps=5, n_d=16, n_a=16, lr=0.02, verbose=0)
        self.feature_selector = None  # Entropy later

    def prepare_ml_features(self, data: dict, symbols: List[str], as_of_date: str) -> np.ndarray:
        feats_engine = ImprovedFactorEngine(self.config)
        feats_df = feats_engine.calculate_features(data, 'MODERATE')  # Base
        # Add macro, regime feats
        feats_array = feats_df.values
        return feats_array  # Shape (n_stocks, n_feats)

    def train_predictor(self, historical_data: Dict[str, pd.DataFrame]):
        # Walk-forward: expanding train, monthly retrain
        X = []  # All historical feats
        y = []  # Forward 21d returns
        # Placeholder: load from DB, compute fwd returns (PIT)
        X = np.random.rand(10000, 20)
        y = np.random.rand(10000) * 0.02
        self.xgb_reg.fit(X, y)
        self.tabnet.fit(X, y, max_epochs=50, patience=10)
    
    def predict_returns(self, X: np.ndarray) -> np.ndarray:
        xgb_pred = self.xgb_reg.predict(X)
        tab_pred = self.tabnet.predict(X).flatten()
        return 0.6 * xgb_pred + 0.4 * tab_pred  # Ensemble

    def regime_submodel(self, regime: str, X: np.ndarray):
        if regime == 'HIGH':
            # Low-vol bias
            vol_idx = 1  # Assume
            X[:, vol_idx] *= 2
        return self.predict_returns(X)
import numpy as np
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
import cvxpy as cp

class HRPoptimizer:
    """Hierarchical Risk Parity"""
    
    @staticmethod
    def get_quasi_diag(link_mat: np.ndarray):
        link_mat = link_mat[np.triu_indices_from(link_mat, k=1)]
        sort_ix = np.argsort(link_mat)
        num_items = link_mat.shape[0]
        prev = np.zeros(num_items, dtype=int)
        cur = prev.copy()
        for i in range(0, num_items):
            cur[sort_ix[i]] = i
            prev[cur[sort_ix[i]]] = prev[sort_ix[i]]
        return cur
    
    @staticmethod
    def get_cluster_var(cov_mat: np.ndarray, c_items: np.ndarray):
        cov_ = cov_mat.loc[c_items, c_items]
        w = HRPoptimizer.get_ivp(cov_).reshape(-1, 1)
        return (w.T @ cov_ @ w).flatten()[0]
    
    @staticmethod
    def get_ivp(cov_mat: np.ndarray, delta=0.0):
        diag = np.diag(cov_mat)
        ivp = 1 / (diag ** 0.5)
        ivp /= ivp.sum()
        return ivp ** (1 - delta)
    
    def optimize(self, scores: pd.Series, cov_mat: pd.DataFrame, leverage: float, regime: str, max_pos=0.20):
        # Scores to expected returns
        mu = scores / np.sqrt(np.diag(cov_mat))  # Risk-adjusted
        
        # HRP
        dist = squareform(1 - np.corrcoef(scores.index))  # Corr from cov
        link = linkage(dist, 'single')
        sort_ix = self.get_quasi_diag(link)
        sort_ix = scores.index[sort_ix]
        cov_mat = cov_mat.loc[sort_ix, sort_ix]
        mu = mu.loc[sort_ix]
        
        hrp_weights = np.ones(len(mu)) / len(mu)
        c_items = [sort_ix]
        while len(c_items) > 0:
            c_items = [i[j] for i in c_items for j in [[0], [1]]]
            for i in range(0, len(c_items), 2):
                c_items0 = c_items[i]
                c_items1 = c_items[i + 1]
                cluster_var0 = self.get_cluster_var(cov_mat, c_items0)
                cluster_var1 = self.get_cluster_var(cov_mat, c_items1)
                alpha = 1 - cluster_var0 / (cluster_var0 + cluster_var1)
                hrp_weights[c_items0] *= alpha
                hrp_weights[c_items1] *= 1 - alpha
        
        weights = pd.Series(hrp_weights, index=sort_ix)
        weights = weights.clip(-0.1, max_pos)  # Short bias small
        weights /= weights.abs().sum()
        
        # CVaR constraint approx
        if regime in ['HIGH', 'CRISIS']:
            weights[weights < 0] *= 0.5  # Reduce shorts
        
        return weights * leverage

class PortfolioOptimizer:
    def __init__(self, config):
        self.config = config
        self.hrp = HRPoptimizer()
    
    def optimize(self, scores: pd.Series, volatility: pd.Series, leverage: float, cov_mat: pd.DataFrame, regime: str, current_dd: float):
        # Cov from vol + corr approx
        corr = pd.DataFrame(0.3, index=scores.index, columns=scores.index)  # Base corr
        np.fill_diagonal(corr.values, 1)
        cov = corr * volatility * volatility.to_series().values.reshape(-1,1)
        
        weights = self.hrp.optimize(scores, cov, leverage, regime)
        
        # DD adjust
        if current_dd < -0.05:
            weights *= 0.7
        elif current_dd < -0.08:
            weights *= 0.4
        
        return weights
class ImprovedRiskManager:
    def __init__(self, config: AresV10Config):
        self.config = config
        self.target_vol = config.target_vol
        self.vix_hedge_alloc = 0.05

    def dynamic_leverage(self, realized_vol: float, icir: float, regime_lev: float, dd: float) -> float:
        lev = self.target_vol / (realized_vol + 1e-6) * icir * regime_lev
        lev = min(lev, 3.0)
        if dd < -0.05: lev *= 0.7
        if dd < -0.08: lev *= 0.4
        return lev

    def tail_hedge(self, vix: float, regime: str, portfolio_weights: pd.Series) -> pd.Series:
        if vix > 25 and regime == 'HIGH':
            hedge_return = -0.5 * (pd.Series([vix]).pct_change().iloc[-1] or 0)  # Sim VIX put
            portfolio_weights['HEDGE'] = self.vix_hedge_alloc * hedge_return
            portfolio_weights = portfolio_weights * (1 - self.vix_hedge_alloc)
        return portfolio_weights

    def calculate_trailing_vol(self, returns: pd.Series, window: int = 60) -> float:
        return returns.rolling(window).std().iloc[-1] * np.sqrt(252)
import pandas as pd
import numpy as np
import sqlite3
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from typing import List

class AresV10Backtester:
    def __init__(self, config: AresV10Config):
        self.config = config
        self.regime_det = ImprovedRegimeDetector(config)
        self.factor_eng = ImprovedFactorEngine(config)
        self.ml_model = ImprovedMLModel(config)
        self.optimizer = PortfolioOptimizer(config)
        self.risk_mgr = ImprovedRiskManager(config)

    def run_backtest(self, start_date: str = '2020-01-01', end_date: str = '2024-12-31', symbols: List[str] = None, n_stocks: int = 50):
        if symbols is None:
            conn = sqlite3.connect(self.config.db_path)
            symbols = pd.read_sql("SELECT DISTINCT symbol FROM daily_ohlcv LIMIT 500", conn)['symbol'].tolist()
            conn.close()
        
        dates = pd.date_range(start_date, end_date, freq='B')
        portfolio_returns = []
        equity = [1.0]
        dd = 0.0
        positions = pd.DataFrame()
        
        for i, date in enumerate(dates[252:]):  # Skip warm-up
            as_of_date = date.strftime('%Y-%m-%d')
            
            # Data load PIT
            data = self.factor_eng.load_data_pit(symbols, as_of_date)
            scores = self.factor_eng.calculate_features(data, 'MODERATE')  # Update
            
            # Regime
            vix = data['daily']['vix_level'].iloc[-1] if 'vix_level' in data['daily'] else 20
            regime, base_lev = self.regime_det.detect(as_of_date, current_vol=0.15)
            
            # ML predict
            X = self.ml_model.prepare_ml_features(data, symbols, as_of_date)
            pred_returns = self.ml_model.predict_returns(X)
            scores = pd.Series(pred_returns, index=symbols[:len(pred_returns)]).rank(pct=True)
            
            # Vol/cov
            rets = data['daily'].pivot(index='date', columns='symbol', values='returns').iloc[-60:]
            vol = rets.std() * np.sqrt(252)
            cov_mat = rets.cov() * 252
            
            # Optimize
            weights = self.optimizer.optimize(scores.nlargest(n_stocks), vol, base_lev, cov_mat.loc[scores.nlargest(n_stocks).index], regime, dd)
            
            # Risk adjust
            trail_vol = self.risk_mgr.calculate_trailing_vol(pd.Series(portfolio_returns[-60:]))
            icir = 0.15  # From ICIRCalculator
            lev = self.risk_mgr.dynamic_leverage(trail_vol, icir, base_lev, dd)
            weights *= lev
            weights = self.risk_mgr.tail_hedge(vix, regime, weights)
            
            # Next day return (sim, no look-ahead)
            next_rets = rets.iloc[-1] * 1.001  # Placeholder: fetch next day PIT returns
            port_ret = (weights * next_rets).sum() - self.config.transaction_cost * weights.abs().sum()
            portfolio_returns.append(port_ret)
            
            equity.append(equity[-1] * (1 + port_ret))
            peak = np.maximum.accumulate(equity)
            dd = (equity[-1] / peak[-1] - 1)
            positions[date] = weights
        
        equity = np.array(equity)
        returns = pd.Series(portfolio_returns)
        sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
        mdd = (equity / np.maximum.accumulate(equity) - 1).min()
        
        print(f"Sharpe: {sharpe:.2f}, MDD: {mdd:.2%}, Annual Return: {returns.mean()*252:.1%}")
        
        # Plot
        plt.plot(equity)
        plt.title('ARES v10 Backtest')
        plt.show()
        
        return {'sharpe': sharpe, 'mdd': mdd, 'equity': equity, 'positions': positions}

# Usage
config = AresV10Config()
backtester = AresV10Backtester(config)
results = backtester.run_backtest()
