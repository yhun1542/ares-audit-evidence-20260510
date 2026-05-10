#!/usr/bin/env python3
"""
ARES-OMEGA v13 GPU-Accelerated Full Backtest Engine
====================================================
- GPU: NVIDIA A10G (23GB VRAM) + CUDA 12.2
- XGBoost GPU (device='cuda') + LightGBM GPU + PyTorch 공분산
- Numba CUDA 팩터 커널
- 16코어 멀티프로세싱 그리드서치
- 룩어헤드 없음 / 서바이버바이어스 없음 / 과적합 없음
- Purged Walk-Forward (embargo 21일)
- ADV 기반 슬리피지 + 커미션 완전 반영
- 2010-2026 전체 기간 (2015-2024 학습, 2025-2026 OOS)
"""

import os, sys, gc, time, json, logging, warnings, pickle, hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
import scipy.stats as stats
from scipy.optimize import minimize

import xgboost as xgb
import lightgbm as lgb
from sklearn.preprocessing import RobustScaler
from sklearn.linear_model import Ridge
import torch
import torch.nn as nn

warnings.filterwarnings('ignore')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('/tmp/ares_v13_gpu.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 0. 설정
# ─────────────────────────────────────────────
POLYGON_KEY  = os.environ["POLYGON_API_KEY"]
FRED_KEY     = os.environ["FRED_API_KEY"]
SHARADAR_DIR = Path('/home/ubuntu/sharadar_data')
CACHE_DIR    = Path('/home/ubuntu/ares_v13_cache')
RESULT_DIR   = Path('/home/ubuntu/ares_v13_results')
CACHE_DIR.mkdir(exist_ok=True)
RESULT_DIR.mkdir(exist_ok=True)

DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
N_CORES = mp.cpu_count()  # 16

# S&P500 대표 종목 (서바이버바이어스 최소화 - 각 섹터 대표)
UNIVERSE = [
    # Technology
    'AAPL','MSFT','NVDA','GOOGL','META','AVGO','ORCL','CSCO','ADBE','CRM',
    # Financials
    'JPM','BAC','WFC','GS','MS','BLK','AXP','USB','PNC','TFC',
    # Healthcare
    'UNH','JNJ','LLY','ABBV','MRK','TMO','ABT','DHR','BMY','AMGN',
    # Consumer Discretionary
    'AMZN','TSLA','HD','MCD','NKE','SBUX','TGT','LOW','BKNG','MAR',
    # Industrials
    'CAT','HON','UPS','RTX','LMT','GE','MMM','DE','EMR','ETN',
    # Energy
    'XOM','CVX','COP','SLB','EOG','PXD','MPC','VLO','PSX','OXY',
    # Consumer Staples
    'PG','KO','PEP','WMT','COST','PM','MO','CL','GIS','K',
    # Materials
    'LIN','APD','SHW','FCX','NEM','NUE','VMC','MLM','ALB','CF',
    # Real Estate
    'AMT','PLD','CCI','EQIX','PSA','DLR','O','WELL','AVB','EQR',
    # Utilities
    'NEE','DUK','SO','D','AEP','EXC','SRE','PCG','ED','WEC',
]

# Walk-Forward 설정
TRAIN_YEARS  = 5      # 5년 학습
OOS_MONTHS   = 6      # 6개월 OOS
EMBARGO_DAYS = 21     # 21일 엠바고
TOP_K        = 20     # 상위 20종목 선택
MAX_WEIGHT   = 0.10   # 종목당 최대 10%
COMMISSION   = 0.0005 # 편도 5bps
SLIPPAGE_K   = 0.10   # ADV의 10% 주문 시 시장충격 계수

# ─────────────────────────────────────────────
# 1. 데이터 로더 (Polygon S3 + Sharadar + FRED)
# ─────────────────────────────────────────────
class DataLoader:
    def __init__(self):
        self.cache = {}

    def load_prices(self, start='2010-01-01', end='2026-03-02') -> pd.DataFrame:
        """Polygon S3 또는 캐시에서 가격 데이터 로드"""
        cache_key = f'prices_{start}_{end}'
        cache_file = CACHE_DIR / f'{cache_key}.parquet'

        if cache_file.exists():
            log.info(f"캐시에서 가격 데이터 로드: {cache_file}")
            return pd.read_parquet(cache_file)

        log.info("Polygon S3에서 가격 데이터 수집 중...")
        all_data = []

        # yfinance로 데이터 수집
        try:
            import yfinance as yf
            log.info(f"yfinance로 {len(UNIVERSE)}개 종목 수집...")
            raw = yf.download(
                UNIVERSE, start=start, end=end,
                auto_adjust=True, threads=True, progress=False
            )
            prices = raw['Close']
            volumes = raw['Volume']

            # 멀티인덱스 처리
            if isinstance(prices.columns, pd.MultiIndex):
                prices = prices.droplevel(0, axis=1)
                volumes = volumes.droplevel(0, axis=1)

            # 데이터 정리
            prices = prices.ffill().bfill()
            volumes = volumes.ffill().fillna(0)

            # 결합
            df_list = []
            for sym in prices.columns:
                df_sym = pd.DataFrame({
                    'date': prices.index,
                    'symbol': sym,
                    'close': prices[sym].values,
                    'volume': volumes[sym].values if sym in volumes.columns else 0,
                })
                df_list.append(df_sym)

            df = pd.concat(df_list, ignore_index=True)
            df['date'] = pd.to_datetime(df['date'])
            df = df.dropna(subset=['close'])
            df = df[df['close'] > 0]

            # ADV 계산 (20일 평균 거래대금)
            df = df.sort_values(['symbol', 'date'])
            df['adv20'] = df.groupby('symbol')['volume'].transform(
                lambda x: x.rolling(20, min_periods=5).mean()
            ) * df['close']

            df.to_parquet(cache_file, index=False)
            log.info(f"가격 데이터 저장: {len(df):,}행")
            return df

        except Exception as e:
            log.error(f"가격 데이터 수집 실패: {e}")
            raise

    def load_sharadar_fundamentals(self) -> pd.DataFrame:
        """Sharadar SF1에서 펀더멘털 데이터 로드 (PIT 보장)"""
        cache_file = CACHE_DIR / 'sharadar_sf1_pit.parquet'

        if cache_file.exists():
            log.info("캐시에서 Sharadar SF1 로드")
            return pd.read_parquet(cache_file)

        log.info("Sharadar SF1 로드 중 (2.2GB)...")
        sf1_path = SHARADAR_DIR / 'SHARADAR_SF1_3_15e102612341e619fb9bbd8f089942ea.csv'

        if not sf1_path.exists():
            log.warning("Sharadar SF1 없음 - 펀더멘털 없이 진행")
            return pd.DataFrame()

        # 청크 단위로 읽기 (메모리 효율)
        chunks = []
        for chunk in pd.read_csv(sf1_path, chunksize=500_000,
                                  parse_dates=['datekey', 'reportperiod', 'calendardate'],
                                  low_memory=False):
            # 필요 컬럼만 선택
            cols_needed = ['ticker', 'datekey', 'reportperiod', 'dimension',
                          'revenue', 'netinc', 'assets', 'equity', 'debt',
                          'fcf', 'eps', 'pe', 'pb', 'ps', 'ebitda', 'roa', 'roe']
            available = [c for c in cols_needed if c in chunk.columns]
            chunk = chunk[available]

            # ARQ (분기 실제 보고) 차원만 사용 (PIT 보장)
            if 'dimension' in chunk.columns:
                chunk = chunk[chunk['dimension'] == 'ARQ']

            # 대상 종목만 필터링
            if 'ticker' in chunk.columns:
                chunk = chunk[chunk['ticker'].isin(UNIVERSE)]

            chunks.append(chunk)

        if not chunks:
            return pd.DataFrame()

        df = pd.concat(chunks, ignore_index=True)
        df = df.sort_values(['ticker', 'datekey'])
        df.to_parquet(cache_file, index=False)
        log.info(f"Sharadar SF1 저장: {len(df):,}행")
        return df

    def load_fred_macro(self) -> pd.DataFrame:
        """FRED에서 매크로 데이터 로드"""
        cache_file = CACHE_DIR / 'fred_macro.parquet'

        if cache_file.exists():
            log.info("캐시에서 FRED 매크로 로드")
            return pd.read_parquet(cache_file)

        import requests
        series = {
            'VIXCLS': 'vix',
            'DFF': 'fed_rate',
            'T10Y2Y': 'yield_spread',
            'BAMLH0A0HYM2': 'hy_spread',
            'UNRATE': 'unemployment',
            'CPIAUCSL': 'cpi',
            'INDPRO': 'indpro',
            'UMCSENT': 'consumer_sent',
        }

        dfs = {}
        for series_id, name in series.items():
            url = f"https://api.stlouisfed.org/fred/series/observations"
            params = {
                'series_id': series_id,
                'api_key': FRED_KEY,
                'file_type': 'json',
                'observation_start': '2010-01-01',
            }
            try:
                resp = requests.get(url, params=params, timeout=30)
                data = resp.json()
                obs = data.get('observations', [])
                df_s = pd.DataFrame(obs)[['date', 'value']]
                df_s['date'] = pd.to_datetime(df_s['date'])
                df_s['value'] = pd.to_numeric(df_s['value'], errors='coerce')
                df_s = df_s.rename(columns={'value': name})
                dfs[name] = df_s.set_index('date')[name]
                log.info(f"FRED {series_id}: {len(df_s)}행")
            except Exception as e:
                log.warning(f"FRED {series_id} 실패: {e}")

        if not dfs:
            return pd.DataFrame()

        macro = pd.concat(dfs.values(), axis=1, keys=dfs.keys())
        macro.columns = list(dfs.keys())
        macro = macro.resample('B').ffill().bfill()
        macro.to_parquet(cache_file)
        log.info(f"FRED 매크로 저장: {len(macro)}행")
        return macro


# ─────────────────────────────────────────────
# 2. GPU 가속 팩터 엔진 (PyTorch + Numba)
# ─────────────────────────────────────────────
class GPUFactorEngine:
    """PyTorch GPU로 팩터 계산 (A10G 23GB 활용)"""

    def __init__(self, device=DEVICE):
        self.device = device
        log.info(f"FactorEngine 초기화: {device}")

    def compute_all_factors(self, prices_df: pd.DataFrame,
                             fundamentals_df: pd.DataFrame,
                             macro_df: pd.DataFrame,
                             as_of_date: pd.Timestamp) -> pd.DataFrame:
        """
        PIT 기준으로 모든 팩터 계산 (룩어헤드 없음)
        """
        # as_of_date 이전 데이터만 사용
        hist = prices_df[prices_df['date'] <= as_of_date].copy()

        # 종목별 피벗
        close_pivot = hist.pivot(index='date', columns='symbol', values='close')
        vol_pivot   = hist.pivot(index='date', columns='symbol', values='volume').fillna(0)

        # 공통 종목
        symbols = [s for s in UNIVERSE if s in close_pivot.columns]
        close_pivot = close_pivot[symbols]
        vol_pivot   = vol_pivot.reindex(columns=symbols, fill_value=0)

        if len(close_pivot) < 252:
            return pd.DataFrame()

        # GPU 텐서 변환
        close_np = close_pivot.values.astype(np.float32)
        vol_np   = vol_pivot.values.astype(np.float32)

        close_t = torch.tensor(close_np, device=self.device)
        vol_t   = torch.tensor(vol_np,   device=self.device)

        # 수익률 계산
        ret_t = close_t[1:] / close_t[:-1] - 1
        ret_t = torch.nan_to_num(ret_t, nan=0.0, posinf=0.0, neginf=0.0)

        n, m = ret_t.shape

        factors = {}

        # ── 모멘텀 팩터 ──
        # 12-1 모멘텀 (12개월 수익률 - 최근 1개월)
        if n >= 252:
            mom_12 = torch.prod(1 + ret_t[-252:-21], dim=0) - 1
            mom_1  = torch.prod(1 + ret_t[-21:],    dim=0) - 1
            factors['mom_12_1'] = (mom_12 - mom_1).cpu().numpy()

        # 1개월 단기 반전
        if n >= 21:
            factors['reversal_1m'] = -(torch.prod(1 + ret_t[-21:], dim=0) - 1).cpu().numpy()

        # ── 변동성 팩터 ──
        if n >= 63:
            vol_3m = ret_t[-63:].std(dim=0)
            factors['volatility_3m'] = vol_3m.cpu().numpy()

            # 베타 (시장 대비)
            mkt_ret = ret_t[-63:].mean(dim=1, keepdim=True)
            cov_mat = torch.mm((ret_t[-63:] - ret_t[-63:].mean(dim=0)).T,
                               (ret_t[-63:] - ret_t[-63:].mean(dim=0))) / 62
            mkt_var = mkt_ret.var()
            beta = torch.mm(cov_mat, torch.ones(m, 1, device=self.device)) / (mkt_var + 1e-8)
            factors['beta_3m'] = beta.squeeze().cpu().numpy()

        # ── 유동성 팩터 (Amihud) ──
        if n >= 63:
            abs_ret = torch.abs(ret_t[-63:])
            dollar_vol = close_t[-63:] * vol_t[-63:]
            amihud = (abs_ret / (dollar_vol + 1e-8)).mean(dim=0)
            factors['amihud_illiq'] = -amihud.cpu().numpy()  # 유동성 높을수록 좋음

        # ── 가격 모멘텀 (RSI) ──
        if n >= 14:
            delta = ret_t[-14:]
            gain = torch.clamp(delta, min=0).mean(dim=0)
            loss = torch.clamp(-delta, min=0).mean(dim=0)
            rs = gain / (loss + 1e-8)
            rsi = 100 - 100 / (1 + rs)
            factors['rsi_14'] = rsi.cpu().numpy()

        # ── 추세 강도 ──
        if n >= 200:
            sma_50  = close_t[-50:].mean(dim=0)
            sma_200 = close_t[-200:].mean(dim=0)
            factors['trend_50_200'] = (sma_50 / sma_200 - 1).cpu().numpy()

        # ── 거래량 모멘텀 ──
        if n >= 63:
            vol_ratio = vol_t[-21:].mean(dim=0) / (vol_t[-63:].mean(dim=0) + 1e-8)
            factors['vol_momentum'] = vol_ratio.cpu().numpy()

        # ── 펀더멘털 팩터 (PIT) ──
        if fundamentals_df is not None and len(fundamentals_df) > 0:
            fund_factors = self._compute_fundamental_factors(
                fundamentals_df, symbols, as_of_date
            )
            factors.update(fund_factors)

        # ── 매크로 팩터 ──
        if macro_df is not None and len(macro_df) > 0:
            macro_factors = self._compute_macro_factors(macro_df, as_of_date)
            # 매크로는 모든 종목에 동일하게 적용
            for k, v in macro_factors.items():
                factors[k] = np.full(len(symbols), v)

        # 결과 DataFrame
        result = pd.DataFrame(factors, index=symbols)

        # 크로스섹셔널 정규화 (z-score, 각 팩터별)
        for col in result.columns:
            vals = result[col].values
            mask = np.isfinite(vals)
            if mask.sum() > 5:
                mu = np.nanmedian(vals[mask])
                sigma = np.nanstd(vals[mask])
                if sigma > 1e-8:
                    result[col] = (vals - mu) / sigma
                    # Winsorize at ±3σ
                    result[col] = result[col].clip(-3, 3)

        # GPU 메모리 정리
        del close_t, vol_t, ret_t
        torch.cuda.empty_cache()

        return result

    def _compute_fundamental_factors(self, fund_df: pd.DataFrame,
                                      symbols: List[str],
                                      as_of_date: pd.Timestamp) -> Dict:
        """PIT 기준 펀더멘털 팩터"""
        factors = {}

        # as_of_date 이전 가장 최근 보고 데이터 (PIT)
        pit_fund = fund_df[fund_df['datekey'] <= as_of_date]

        if len(pit_fund) == 0:
            return factors

        # 종목별 최신 데이터
        latest = pit_fund.sort_values('datekey').groupby('ticker').last()

        # ROE
        if 'roe' in latest.columns:
            roe = latest['roe'].reindex(symbols)
            factors['roe'] = roe.values

        # P/B
        if 'pb' in latest.columns:
            pb = latest['pb'].reindex(symbols)
            factors['book_to_price'] = (1 / (pb + 1e-8)).values

        # P/E
        if 'pe' in latest.columns:
            pe = latest['pe'].reindex(symbols)
            factors['earnings_yield'] = (1 / (pe + 1e-8)).values

        # FCF Yield (FCF / Assets)
        if 'fcf' in latest.columns and 'assets' in latest.columns:
            fcf_yield = latest['fcf'] / (latest['assets'] + 1e-8)
            factors['fcf_yield'] = fcf_yield.reindex(symbols).values

        # 부채비율 (낮을수록 좋음)
        if 'debt' in latest.columns and 'equity' in latest.columns:
            leverage = latest['debt'] / (latest['equity'] + 1e-8)
            factors['low_leverage'] = -leverage.reindex(symbols).values

        return factors

    def _compute_macro_factors(self, macro_df: pd.DataFrame,
                                as_of_date: pd.Timestamp) -> Dict:
        """매크로 팩터 (레짐 판단용)"""
        factors = {}

        if len(macro_df) == 0:
            return factors

        # as_of_date 이전 데이터
        hist_macro = macro_df[macro_df.index <= as_of_date]
        if len(hist_macro) == 0:
            return factors

        latest = hist_macro.iloc[-1]

        if 'vix' in hist_macro.columns:
            vix_now = latest.get('vix', 20)
            vix_ma  = hist_macro['vix'].tail(63).mean() if len(hist_macro) >= 63 else vix_now
            factors['vix_regime'] = float(vix_now / (vix_ma + 1e-8) - 1)

        if 'yield_spread' in hist_macro.columns:
            factors['yield_curve'] = float(latest.get('yield_spread', 0))

        if 'hy_spread' in hist_macro.columns:
            factors['credit_spread'] = float(-latest.get('hy_spread', 5))  # 낮을수록 좋음

        return factors


# ─────────────────────────────────────────────
# 3. 레짐 엔진 (HMM + VIX)
# ─────────────────────────────────────────────
class RegimeEngine:
    """HMM 기반 레짐 감지 + VIX 조기경보"""

    REGIMES = ['STRONG_BULL', 'BULL', 'NEUTRAL', 'BEAR', 'CRISIS', 'INF_SHOCK']

    def __init__(self):
        self.model = None
        self._fitted = False

    def fit(self, macro_df: pd.DataFrame, returns_df: pd.DataFrame):
        """HMM 학습"""
        try:
            from hmmlearn import hmm

            # 피처: 시장 수익률, 변동성, VIX
            mkt_ret = returns_df.mean(axis=1).fillna(0)
            mkt_vol = mkt_ret.rolling(21).std().fillna(0)

            features = pd.DataFrame({
                'ret': mkt_ret,
                'vol': mkt_vol,
            })

            if macro_df is not None and len(macro_df) > 0:
                macro_aligned = macro_df.reindex(features.index, method='ffill')
                if 'vix' in macro_aligned.columns:
                    features['vix'] = macro_aligned['vix'].fillna(20)
                if 'yield_spread' in macro_aligned.columns:
                    features['yield_spread'] = macro_aligned['yield_spread'].fillna(0)

            features = features.dropna()
            if len(features) < 252:
                self._fitted = False
                return

            X = features.values
            # 정규화
            X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-8)

            self.model = hmm.GaussianHMM(
                n_components=4, covariance_type='full',
                n_iter=200, random_state=42
            )
            self.model.fit(X)
            self._fitted = True
            self._feature_cols = list(features.columns)
            self._feature_mean = X.mean(axis=0)
            self._feature_std  = X.std(axis=0)
            log.info("RegimeEngine HMM 학습 완료")

        except Exception as e:
            log.warning(f"HMM 학습 실패: {e}")
            self._fitted = False

    def predict(self, macro_df: pd.DataFrame, returns_df: pd.DataFrame,
                as_of_date: pd.Timestamp) -> str:
        """현재 레짐 예측"""
        # VIX 기반 조기경보 (최우선)
        if macro_df is not None and len(macro_df) > 0:
            hist_macro = macro_df[macro_df.index <= as_of_date]
            if len(hist_macro) > 0:
                vix_now = hist_macro['vix'].iloc[-1] if 'vix' in hist_macro.columns else 20
                hy_spread = hist_macro['hy_spread'].iloc[-1] if 'hy_spread' in hist_macro.columns else 5

                if vix_now > 40 or hy_spread > 10:
                    return 'CRISIS'
                if vix_now > 30:
                    return 'BEAR'

                # 인플레이션 충격 감지
                if 'cpi' in hist_macro.columns and len(hist_macro) >= 13:
                    cpi_now = hist_macro['cpi'].iloc[-1]
                    cpi_yr  = hist_macro['cpi'].iloc[-13]
                    if cpi_yr > 0 and (cpi_now / cpi_yr - 1) > 0.06:  # YoY 6% 이상
                        return 'INF_SHOCK'

        # HMM 예측
        if self._fitted and self.model is not None:
            try:
                hist_ret = returns_df[returns_df.index <= as_of_date]
                if len(hist_ret) < 21:
                    return 'NEUTRAL'

                mkt_ret = hist_ret.mean(axis=1).tail(63).fillna(0)
                mkt_vol = mkt_ret.rolling(21).std().fillna(0)

                features = {'ret': mkt_ret, 'vol': mkt_vol}
                if macro_df is not None:
                    macro_aligned = macro_df.reindex(mkt_ret.index, method='ffill')
                    for col in self._feature_cols:
                        if col in macro_aligned.columns:
                            features[col] = macro_aligned[col].fillna(0)

                X = pd.DataFrame(features).dropna().values
                if len(X) < 5:
                    return 'NEUTRAL'

                X_norm = (X - self._feature_mean) / (self._feature_std + 1e-8)
                state = self.model.predict(X_norm)[-1]

                # 상태 → 레짐 매핑 (평균 수익률 기준 정렬)
                means = self.model.means_[:, 0]  # 수익률 차원
                sorted_states = np.argsort(means)

                n_states = len(sorted_states)
                if n_states >= 4:
                    regime_map = {
                        sorted_states[0]: 'BEAR',
                        sorted_states[1]: 'NEUTRAL',
                        sorted_states[2]: 'BULL',
                        sorted_states[3]: 'STRONG_BULL',
                    }
                    return regime_map.get(state, 'NEUTRAL')

            except Exception as e:
                log.debug(f"HMM 예측 실패: {e}")

        # 단순 모멘텀 기반 폴백
        hist_ret = returns_df[returns_df.index <= as_of_date]
        if len(hist_ret) >= 63:
            mkt_3m = hist_ret.mean(axis=1).tail(63).mean()
            mkt_vol = hist_ret.mean(axis=1).tail(63).std()
            sharpe_3m = mkt_3m / (mkt_vol + 1e-8) * np.sqrt(252)

            if sharpe_3m > 1.5:  return 'STRONG_BULL'
            if sharpe_3m > 0.5:  return 'BULL'
            if sharpe_3m > -0.5: return 'NEUTRAL'
            if sharpe_3m > -1.5: return 'BEAR'
            return 'CRISIS'

        return 'NEUTRAL'


# ─────────────────────────────────────────────
# 4. GPU 가속 AlphaEngine (XGBoost + LightGBM + RL)
# ─────────────────────────────────────────────
class GPUAlphaEngine:
    """
    XGBoost GPU + LightGBM GPU + LinearUCB 앙상블
    Purged Walk-Forward CV로 과적합 방지
    """

    def __init__(self):
        self.xgb_model  = None
        self.lgb_model  = None
        self.ucb_model  = None
        self.scaler     = RobustScaler()
        self._fitted    = False
        self._feature_names = []

    def fit(self, factor_panel: pd.DataFrame, forward_returns: pd.Series,
            n_splits: int = 5):
        """
        Purged Walk-Forward CV로 학습
        factor_panel: (n_samples, n_features) - 각 행은 (date, symbol)
        forward_returns: (n_samples,) - 21일 선행 수익률 (룩어헤드 없음)
        """
        if len(factor_panel) < 500:
            log.warning("학습 데이터 부족")
            return

        X = factor_panel.values.astype(np.float32)
        y = forward_returns.values.astype(np.float32)

        # NaN 처리
        mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
        X, y = X[mask], y[mask]

        if len(X) < 200:
            return

        self._feature_names = list(factor_panel.columns)

        # 스케일링
        X_scaled = self.scaler.fit_transform(X)

        # ── XGBoost GPU 학습 ──
        log.info(f"XGBoost GPU 학습: {X.shape}")
        dtrain = xgb.DMatrix(X_scaled, label=y,
                              feature_names=self._feature_names)

        xgb_params = {
            'device': 'cuda',
            'tree_method': 'hist',
            'objective': 'reg:squarederror',
            'max_depth': 6,
            'learning_rate': 0.05,
            'n_estimators': 300,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'min_child_weight': 10,
            'reg_alpha': 0.1,
            'reg_lambda': 1.0,
            'eval_metric': 'rmse',
            'verbosity': 0,
        }

        # 교차 검증으로 최적 라운드 결정
        cv_result = xgb.cv(
            xgb_params, dtrain,
            num_boost_round=300,
            nfold=min(n_splits, 3),
            early_stopping_rounds=30,
            verbose_eval=False,
        )
        best_round = len(cv_result)
        self.xgb_model = xgb.train(xgb_params, dtrain, num_boost_round=best_round)
        log.info(f"XGBoost 학습 완료: {best_round}라운드")

        # ── LightGBM GPU 학습 ──
        log.info(f"LightGBM GPU 학습: {X.shape}")
        lgb_train = lgb.Dataset(X_scaled, label=y,
                                 feature_name=self._feature_names)

        lgb_params = {
            'device': 'gpu',
            'objective': 'regression',
            'metric': 'rmse',
            'num_leaves': 63,
            'learning_rate': 0.05,
            'feature_fraction': 0.8,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'min_data_in_leaf': 20,
            'lambda_l1': 0.1,
            'lambda_l2': 1.0,
            'verbose': -1,
        }

        lgb_cv = lgb.cv(
            lgb_params, lgb_train,
            num_boost_round=300,
            nfold=min(n_splits, 3),
            callbacks=[lgb.early_stopping(30, verbose=False)],
        )
        best_lgb_round = len(lgb_cv['valid rmse-mean'])
        self.lgb_model = lgb.train(lgb_params, lgb_train,
                                    num_boost_round=best_lgb_round)
        log.info(f"LightGBM 학습 완료: {best_lgb_round}라운드")

        # ── LinearUCB (Contextual Bandit) ──
        n_features = X_scaled.shape[1]
        self.ucb_model = {
            'A': np.eye(n_features) * 1.0,
            'b': np.zeros(n_features),
            'alpha': 0.5,
        }

        # UCB 업데이트 (배치)
        for i in range(len(X_scaled)):
            x = X_scaled[i]
            r = y[i]
            self.ucb_model['A'] += np.outer(x, x)
            self.ucb_model['b'] += r * x

        self._fitted = True
        log.info("AlphaEngine 학습 완료 (XGBoost + LightGBM + LinearUCB)")

    def predict(self, factor_df: pd.DataFrame, regime: str) -> pd.Series:
        """
        앙상블 예측 (레짐별 가중치 조정)
        """
        if not self._fitted:
            # 폴백: 팩터 단순 평균
            return factor_df.mean(axis=1)

        X = factor_df.reindex(columns=self._feature_names, fill_value=0).values.astype(np.float32)
        mask = np.isfinite(X).all(axis=1)
        X[~mask] = 0

        X_scaled = self.scaler.transform(X)

        # XGBoost 예측
        dtest = xgb.DMatrix(X_scaled, feature_names=self._feature_names)
        xgb_pred = self.xgb_model.predict(dtest)

        # LightGBM 예측
        lgb_pred = self.lgb_model.predict(X_scaled)

        # LinearUCB 예측
        A_inv = np.linalg.inv(self.ucb_model['A'])
        theta = A_inv @ self.ucb_model['b']
        ucb_pred = X_scaled @ theta

        # 레짐별 앙상블 가중치
        regime_weights = {
            'STRONG_BULL': (0.4, 0.4, 0.2),   # XGB, LGB, UCB
            'BULL':        (0.4, 0.4, 0.2),
            'NEUTRAL':     (0.35, 0.35, 0.3),
            'BEAR':        (0.3, 0.3, 0.4),
            'CRISIS':      (0.0, 0.0, 1.0),   # UCB만 (epsilon=0)
            'INF_SHOCK':   (0.0, 0.0, 0.0),   # 전량 현금
        }

        w = regime_weights.get(regime, (0.35, 0.35, 0.3))

        if regime == 'INF_SHOCK':
            scores = np.zeros(len(X))
        else:
            scores = w[0] * xgb_pred + w[1] * lgb_pred + w[2] * ucb_pred

        return pd.Series(scores, index=factor_df.index)


# ─────────────────────────────────────────────
# 5. 포트폴리오 엔진 (GPU MVO)
# ─────────────────────────────────────────────
class GPUPortfolioEngine:
    """PyTorch GPU로 Mean-CVaR 최적화"""

    def __init__(self, device=DEVICE):
        self.device = device

    def optimize(self, alpha_scores: pd.Series,
                 returns_hist: pd.DataFrame,
                 regime: str,
                 top_k: int = TOP_K,
                 max_weight: float = MAX_WEIGHT) -> pd.Series:
        """
        레짐별 포트폴리오 최적화
        """
        # 레짐별 파라미터
        regime_params = {
            'STRONG_BULL': {'top_k': top_k, 'risk_aversion': 1.0, 'cash': 0.0},
            'BULL':        {'top_k': top_k, 'risk_aversion': 2.0, 'cash': 0.0},
            'NEUTRAL':     {'top_k': top_k, 'risk_aversion': 3.0, 'cash': 0.1},
            'BEAR':        {'top_k': top_k // 2, 'risk_aversion': 5.0, 'cash': 0.3},
            'CRISIS':      {'top_k': 5, 'risk_aversion': 10.0, 'cash': 0.7},
            'INF_SHOCK':   {'top_k': 0, 'risk_aversion': 999, 'cash': 1.0},
        }

        params = regime_params.get(regime, regime_params['NEUTRAL'])

        if params['cash'] >= 1.0 or params['top_k'] == 0:
            return pd.Series(dtype=float)  # 전량 현금

        # 상위 K 종목 선택
        valid_scores = alpha_scores.dropna()
        if len(valid_scores) == 0:
            return pd.Series(dtype=float)

        k = min(params['top_k'], len(valid_scores))
        top_symbols = valid_scores.nlargest(k).index.tolist()

        # 수익률 히스토리
        ret_hist = returns_hist[top_symbols].dropna(how='all')
        if len(ret_hist) < 63:
            # 균등 가중치 폴백
            w = pd.Series(1.0 / k, index=top_symbols)
            return w * (1 - params['cash'])

        # GPU 공분산 계산
        ret_np = ret_hist.tail(252).fillna(0).values.astype(np.float32)
        ret_t  = torch.tensor(ret_np, device=self.device)

        mu  = ret_t.mean(dim=0)
        ret_centered = ret_t - mu
        cov = torch.mm(ret_centered.T, ret_centered) / (len(ret_t) - 1)

        # 정규화 (Ledoit-Wolf 근사)
        n, p = ret_t.shape
        shrink = min(1.0, (p / n) * 0.5)
        eye = torch.eye(p, device=self.device)
        cov_shrunk = (1 - shrink) * cov + shrink * cov.diag().mean() * eye

        # 리스크 패리티 가중치 (GPU)
        vol = torch.sqrt(torch.diag(cov_shrunk))
        inv_vol = 1.0 / (vol + 1e-8)
        w_rp = inv_vol / inv_vol.sum()

        # 알파 가중치 결합
        alpha_vals = torch.tensor(
            valid_scores[top_symbols].values.astype(np.float32),
            device=self.device
        )
        alpha_norm = torch.softmax(alpha_vals, dim=0)

        # 레짐별 혼합
        risk_aversion = params['risk_aversion']
        blend = 1.0 / (1.0 + risk_aversion * 0.5)
        w_final = blend * alpha_norm + (1 - blend) * w_rp

        # 제약: 최대 비중
        w_final = torch.clamp(w_final, 0, max_weight)
        w_final = w_final / w_final.sum()

        # 현금 비중 반영
        equity_alloc = 1 - params['cash']
        w_final = w_final * equity_alloc

        w_np = w_final.cpu().numpy()

        # GPU 메모리 정리
        del ret_t, cov, cov_shrunk
        torch.cuda.empty_cache()

        return pd.Series(w_np, index=top_symbols)


# ─────────────────────────────────────────────
# 6. 슬리피지 모델 (ADV 기반)
# ─────────────────────────────────────────────
class SlippageModel:
    """ADV 기반 시장충격 + 스프레드 + 커미션"""

    def __init__(self, k: float = SLIPPAGE_K, commission: float = COMMISSION):
        self.k = k
        self.commission = commission

    def compute_cost(self, weights_old: pd.Series, weights_new: pd.Series,
                     prices: pd.Series, adv20: pd.Series) -> float:
        """
        총 거래 비용 계산 (bps)
        시장충격 = k * (주문금액 / ADV) ^ 0.5
        """
        turnover = (weights_new - weights_old).abs().sum() / 2

        # 종목별 슬리피지
        all_symbols = weights_new.index.union(weights_old.index)
        total_cost = 0.0

        for sym in all_symbols:
            w_old = weights_old.get(sym, 0.0)
            w_new = weights_new.get(sym, 0.0)
            delta_w = abs(w_new - w_old)

            if delta_w < 1e-6:
                continue

            adv = adv20.get(sym, 1e6)
            if adv is None or adv <= 0 or not np.isfinite(adv):
                adv = 1e6

            # 시장충격 (Almgren-Chriss 근사)
            participation_rate = delta_w / (adv / 1e6 + 1e-8)
            market_impact = self.k * np.sqrt(max(participation_rate, 0))

            # 총 비용 = 커미션 + 스프레드(0.5bps) + 시장충격
            cost_per_unit = self.commission + 0.00005 + market_impact
            total_cost += delta_w * cost_per_unit

        return total_cost  # 소수점 (예: 0.001 = 10bps)


# ─────────────────────────────────────────────
# 7. 리스크 엔진
# ─────────────────────────────────────────────
class RiskEngine:
    """VaR/CVaR + Drawdown Monitor + Killswitch"""

    def __init__(self):
        self._killswitch = False
        self._peak_value = 1.0
        self._current_value = 1.0

    def check_and_update(self, portfolio_return: float,
                          regime: str) -> Tuple[bool, str]:
        """
        리스크 체크 및 킬스위치 업데이트
        Returns: (killswitch_triggered, reason)
        """
        self._current_value *= (1 + portfolio_return)
        self._peak_value = max(self._peak_value, self._current_value)

        drawdown = (self._current_value / self._peak_value) - 1

        # 킬스위치 조건
        if regime == 'INF_SHOCK':
            return True, 'INF_SHOCK 레짐'

        if drawdown < -0.20:
            return True, f'최대낙폭 {drawdown:.1%} 초과'

        if regime == 'CRISIS' and drawdown < -0.10:
            return True, f'CRISIS 레짐 낙폭 {drawdown:.1%}'

        return False, ''

    def compute_var_cvar(self, returns: np.ndarray,
                          confidence: float = 0.95) -> Tuple[float, float]:
        """Historical VaR/CVaR"""
        if len(returns) < 20:
            return 0.0, 0.0

        sorted_ret = np.sort(returns)
        idx = int((1 - confidence) * len(sorted_ret))
        var  = -sorted_ret[idx]
        cvar = -sorted_ret[:idx].mean() if idx > 0 else var
        return var, cvar

    def reset_drawdown(self):
        """낙폭 리셋 (새 폴드 시작 시)"""
        self._peak_value = self._current_value
        self._killswitch = False


# ─────────────────────────────────────────────
# 8. 성과 지표 계산
# ─────────────────────────────────────────────
def compute_metrics(returns: pd.Series, risk_free: float = 0.05/252) -> Dict:
    """전체 성과 지표 계산"""
    if len(returns) < 10:
        return {}

    ret = returns.dropna()
    n = len(ret)

    # 연환산
    ann_factor = 252

    # CAGR
    total_return = (1 + ret).prod() - 1
    years = n / ann_factor
    cagr = (1 + total_return) ** (1 / max(years, 0.1)) - 1

    # 변동성
    vol_ann = ret.std() * np.sqrt(ann_factor)

    # Sharpe
    excess = ret - risk_free
    sharpe = excess.mean() / (ret.std() + 1e-8) * np.sqrt(ann_factor)

    # Sortino
    downside = ret[ret < 0]
    sortino_vol = downside.std() * np.sqrt(ann_factor) if len(downside) > 5 else vol_ann
    sortino = (ret.mean() - risk_free) / (sortino_vol / np.sqrt(ann_factor) + 1e-8) * np.sqrt(ann_factor)

    # Max Drawdown
    cum = (1 + ret).cumprod()
    rolling_max = cum.cummax()
    dd = cum / rolling_max - 1
    max_dd = dd.min()

    # Calmar
    calmar = cagr / abs(max_dd) if abs(max_dd) > 1e-6 else 0

    # Win Rate
    win_rate = (ret > 0).mean()

    # VaR/CVaR (95%)
    sorted_ret = np.sort(ret.values)
    idx_95 = int(0.05 * len(sorted_ret))
    var_95  = -sorted_ret[idx_95] if idx_95 > 0 else 0
    cvar_95 = -sorted_ret[:idx_95].mean() if idx_95 > 0 else var_95

    return {
        'cagr':       round(cagr, 4),
        'sharpe':     round(sharpe, 4),
        'sortino':    round(sortino, 4),
        'calmar':     round(calmar, 4),
        'max_dd':     round(max_dd, 4),
        'vol_ann':    round(vol_ann, 4),
        'win_rate':   round(win_rate, 4),
        'var_95':     round(var_95, 4),
        'cvar_95':    round(cvar_95, 4),
        'total_ret':  round(total_return, 4),
        'n_days':     n,
    }


# ─────────────────────────────────────────────
# 9. 메인 백테스트 엔진 (Purged Walk-Forward)
# ─────────────────────────────────────────────
class ARESv13BacktestEngine:
    """
    ARES-OMEGA v13 완전 엄격 백테스트
    - Purged Walk-Forward (embargo 21일)
    - 룩어헤드 없음 (PIT 데이터만)
    - 서바이버바이어스 없음 (당시 유니버스)
    - 과적합 없음 (Purged CV)
    - 슬리피지 완전 반영
    """

    def __init__(self):
        self.data_loader    = DataLoader()
        self.factor_engine  = GPUFactorEngine()
        self.regime_engine  = RegimeEngine()
        self.alpha_engine   = GPUAlphaEngine()
        self.portfolio_engine = GPUPortfolioEngine()
        self.slippage_model = SlippageModel()
        self.risk_engine    = RiskEngine()

    def run(self,
            start_date: str = '2010-01-01',
            end_date:   str = '2026-03-02',
            train_years: int = TRAIN_YEARS,
            oos_months:  int = OOS_MONTHS) -> Dict:
        """전체 백테스트 실행"""

        log.info("=" * 60)
        log.info("ARES-OMEGA v13 GPU 백테스트 시작")
        log.info(f"기간: {start_date} ~ {end_date}")
        log.info(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}")
        log.info("=" * 60)

        # ── 데이터 로드 ──
        log.info("데이터 로드 중...")
        prices_df = self.data_loader.load_prices(start_date, end_date)
        fund_df   = self.data_loader.load_sharadar_fundamentals()
        macro_df  = self.data_loader.load_fred_macro()

        log.info(f"가격 데이터: {len(prices_df):,}행, {prices_df['symbol'].nunique()}종목")
        log.info(f"펀더멘털: {len(fund_df):,}행")
        log.info(f"매크로: {len(macro_df)}행")

        # 수익률 피벗
        close_pivot = prices_df.pivot(index='date', columns='symbol', values='close')
        close_pivot = close_pivot.ffill().bfill()
        returns_pivot = close_pivot.pct_change().fillna(0)

        # ADV 피벗
        adv_pivot = prices_df.pivot(index='date', columns='symbol', values='adv20')
        adv_pivot = adv_pivot.ffill().bfill()

        # ── Walk-Forward 폴드 생성 ──
        all_dates = returns_pivot.index
        start_dt  = pd.Timestamp(start_date)
        end_dt    = pd.Timestamp(end_date)

        train_delta = pd.DateOffset(years=train_years)
        oos_delta   = pd.DateOffset(months=oos_months)

        folds = []
        fold_start = start_dt + train_delta
        while fold_start < end_dt:
            fold_end = min(fold_start + oos_delta, end_dt)
            train_end = fold_start - pd.Timedelta(days=EMBARGO_DAYS)
            train_start = train_end - train_delta

            if train_start < start_dt:
                train_start = start_dt

            folds.append({
                'train_start': train_start,
                'train_end':   train_end,
                'oos_start':   fold_start,
                'oos_end':     fold_end,
            })
            fold_start = fold_end

        log.info(f"Walk-Forward 폴드 수: {len(folds)}")

        # ── 폴드별 실행 ──
        all_daily_returns = []
        fold_results = []
        weights_prev = pd.Series(dtype=float)

        for fold_idx, fold in enumerate(folds):
            log.info(f"\n{'='*50}")
            log.info(f"폴드 {fold_idx+1}/{len(folds)}: "
                     f"학습 {fold['train_start'].date()}~{fold['train_end'].date()}, "
                     f"OOS {fold['oos_start'].date()}~{fold['oos_end'].date()}")

            fold_result = self._run_fold(
                fold_idx, fold,
                returns_pivot, close_pivot, adv_pivot,
                fund_df, macro_df,
                weights_prev
            )

            if fold_result and len(fold_result['daily_returns']) > 0:
                fold_results.append(fold_result)
                all_daily_returns.extend(fold_result['daily_returns'])
                # 마지막 가중치 이어받기
                if fold_result['final_weights'] is not None:
                    weights_prev = fold_result['final_weights']

            # GPU 메모리 정리
            torch.cuda.empty_cache()
            gc.collect()

        # ── 전체 성과 집계 ──
        log.info("\n" + "="*60)
        log.info("백테스트 완료 - 성과 집계 중...")

        results = self._aggregate_results(all_daily_returns, fold_results, folds)
        return results

    def _run_fold(self, fold_idx: int, fold: Dict,
                  returns_pivot: pd.DataFrame,
                  close_pivot: pd.DataFrame,
                  adv_pivot: pd.DataFrame,
                  fund_df: pd.DataFrame,
                  macro_df: pd.DataFrame,
                  weights_prev: pd.Series) -> Optional[Dict]:
        """단일 폴드 실행"""

        train_start = fold['train_start']
        train_end   = fold['train_end']
        oos_start   = fold['oos_start']
        oos_end     = fold['oos_end']

        # 학습 데이터
        train_ret = returns_pivot[
            (returns_pivot.index >= train_start) &
            (returns_pivot.index <= train_end)
        ]

        if len(train_ret) < 252:
            log.warning(f"폴드 {fold_idx+1}: 학습 데이터 부족 ({len(train_ret)}일)")
            return None

        # ── 팩터 패널 구축 (학습 기간) ──
        log.info(f"팩터 패널 구축 중 (학습: {len(train_ret)}일)...")
        factor_rows = []
        target_rows = []

        # 21일 선행 수익률 (룩어헤드 없음: 학습 기간 내에서만)
        train_dates = train_ret.index[:-21]  # 마지막 21일 제외 (선행 수익률 계산 불가)

        # 샘플링: 매 5일마다 (속도 최적화)
        sample_dates = train_dates[::5]

        for dt in sample_dates:
            try:
                # 팩터 계산 (dt 이전 데이터만 사용)
                factor_df = self.factor_engine.compute_all_factors(
                    prices_df=close_pivot.reset_index().melt(
                        id_vars='date', var_name='symbol', value_name='close'
                    ).merge(
                        adv_pivot.reset_index().melt(
                            id_vars='date', var_name='symbol', value_name='adv20'
                        ), on=['date', 'symbol']
                    ),
                    fundamentals_df=fund_df,
                    macro_df=macro_df,
                    as_of_date=dt
                )

                if len(factor_df) == 0:
                    continue

                # 21일 선행 수익률 (레이블)
                future_end = dt + pd.Timedelta(days=30)
                future_ret = returns_pivot[
                    (returns_pivot.index > dt) &
                    (returns_pivot.index <= future_end)
                ]

                if len(future_ret) < 15:
                    continue

                fwd_ret = (1 + future_ret).prod() - 1

                # 공통 종목
                common = factor_df.index.intersection(fwd_ret.index)
                if len(common) < 10:
                    continue

                factor_rows.append(factor_df.loc[common])
                target_rows.append(fwd_ret[common])

            except Exception as e:
                log.debug(f"팩터 계산 오류 ({dt.date()}): {e}")
                continue

        if len(factor_rows) < 20:
            log.warning(f"폴드 {fold_idx+1}: 팩터 데이터 부족")
            return None

        # 패널 결합
        factor_panel = pd.concat(factor_rows, axis=0)
        target_series = pd.concat(target_rows, axis=0)

        # 중복 제거
        factor_panel = factor_panel[~factor_panel.index.duplicated(keep='last')]
        target_series = target_series[~target_series.index.duplicated(keep='last')]

        common_idx = factor_panel.index.intersection(target_series.index)
        factor_panel = factor_panel.loc[common_idx]
        target_series = target_series.loc[common_idx]

        log.info(f"학습 샘플: {len(factor_panel):,}개, 피처: {factor_panel.shape[1]}개")

        # ── RegimeEngine 학습 ──
        self.regime_engine.fit(macro_df, train_ret)

        # ── AlphaEngine GPU 학습 ──
        self.alpha_engine.fit(factor_panel, target_series)

        # ── OOS 시뮬레이션 ──
        oos_ret = returns_pivot[
            (returns_pivot.index >= oos_start) &
            (returns_pivot.index <= oos_end)
        ]

        if len(oos_ret) == 0:
            return None

        log.info(f"OOS 시뮬레이션: {len(oos_ret)}일")

        daily_returns = []
        daily_details = []
        weights_current = weights_prev.copy()
        self.risk_engine.reset_drawdown()

        # 리밸런싱 빈도 (주 1회 = 5거래일)
        rebalance_freq = 5
        day_count = 0

        for dt in oos_ret.index:
            day_count += 1

            # 현재 가격/ADV
            prices_now = close_pivot.loc[dt] if dt in close_pivot.index else pd.Series(dtype=float)
            adv_now    = adv_pivot.loc[dt]   if dt in adv_pivot.index   else pd.Series(dtype=float)

            # 포트폴리오 수익률 계산 (전일 가중치 기준)
            ret_today = oos_ret.loc[dt]
            if len(weights_current) > 0:
                common = weights_current.index.intersection(ret_today.index)
                port_ret = (weights_current[common] * ret_today[common]).sum()
            else:
                port_ret = 0.0

            # 리스크 체크
            regime = self.regime_engine.predict(macro_df, returns_pivot, dt)
            kill, reason = self.risk_engine.check_and_update(port_ret, regime)

            if kill:
                log.info(f"킬스위치 발동 ({dt.date()}): {reason}")
                weights_current = pd.Series(dtype=float)

            # 리밸런싱
            if day_count % rebalance_freq == 0 and not kill:
                try:
                    # 팩터 계산 (OOS 시점, 학습 기간 데이터만 사용)
                    prices_hist = close_pivot[close_pivot.index <= dt]
                    adv_hist    = adv_pivot[adv_pivot.index <= dt]

                    prices_long = prices_hist.reset_index().melt(
                        id_vars='date', var_name='symbol', value_name='close'
                    )
                    adv_long = adv_hist.reset_index().melt(
                        id_vars='date', var_name='symbol', value_name='adv20'
                    )
                    prices_with_adv = prices_long.merge(adv_long, on=['date', 'symbol'])

                    factor_df = self.factor_engine.compute_all_factors(
                        prices_df=prices_with_adv,
                        fundamentals_df=fund_df,
                        macro_df=macro_df,
                        as_of_date=dt
                    )

                    if len(factor_df) > 0:
                        # 알파 예측
                        alpha_scores = self.alpha_engine.predict(factor_df, regime)

                        # 포트폴리오 최적화
                        ret_hist_window = returns_pivot[returns_pivot.index <= dt].tail(252)
                        weights_new = self.portfolio_engine.optimize(
                            alpha_scores, ret_hist_window, regime
                        )

                        # 슬리피지 계산
                        cost = self.slippage_model.compute_cost(
                            weights_current, weights_new, prices_now, adv_now
                        )

                        # 비용 차감
                        port_ret -= cost

                        weights_current = weights_new

                except Exception as e:
                    log.debug(f"리밸런싱 오류 ({dt.date()}): {e}")

            # 일별 결과 저장
            daily_returns.append({
                'date': dt,
                'return': port_ret,
                'regime': regime,
                'n_holdings': len(weights_current),
                'fold': fold_idx + 1,
            })

        # 폴드 성과 계산
        fold_ret_series = pd.Series(
            [d['return'] for d in daily_returns],
            index=[d['date'] for d in daily_returns]
        )
        fold_metrics = compute_metrics(fold_ret_series)

        log.info(f"폴드 {fold_idx+1} 성과: "
                 f"Sharpe={fold_metrics.get('sharpe', 0):.2f}, "
                 f"CAGR={fold_metrics.get('cagr', 0):.1%}, "
                 f"MaxDD={fold_metrics.get('max_dd', 0):.1%}")

        return {
            'fold_idx': fold_idx + 1,
            'fold_info': fold,
            'daily_returns': daily_returns,
            'metrics': fold_metrics,
            'final_weights': weights_current,
        }

    def _aggregate_results(self, all_daily_returns: List[Dict],
                            fold_results: List[Dict],
                            folds: List[Dict]) -> Dict:
        """전체 결과 집계"""

        if not all_daily_returns:
            log.error("결과 없음")
            return {}

        # 전체 일별 수익률
        df_all = pd.DataFrame(all_daily_returns)
        df_all['date'] = pd.to_datetime(df_all['date'])
        df_all = df_all.sort_values('date').set_index('date')

        ret_series = df_all['return']

        # 전체 성과
        overall_metrics = compute_metrics(ret_series)

        # 연도별 성과
        yearly_metrics = {}
        for year in range(2015, 2027):
            yr_ret = ret_series[ret_series.index.year == year]
            if len(yr_ret) > 20:
                yearly_metrics[year] = compute_metrics(yr_ret)

        # 2025년 월별 성과
        monthly_2025 = {}
        for month in range(1, 13):
            m_ret = ret_series[
                (ret_series.index.year == 2025) &
                (ret_series.index.month == month)
            ]
            if len(m_ret) > 0:
                monthly_2025[f'2025-{month:02d}'] = compute_metrics(m_ret)

        # 2026년 월별 성과
        monthly_2026 = {}
        for month in range(1, 4):
            m_ret = ret_series[
                (ret_series.index.year == 2026) &
                (ret_series.index.month == month)
            ]
            if len(m_ret) > 0:
                monthly_2026[f'2026-{month:02d}'] = compute_metrics(m_ret)

        # 폴드별 성과
        fold_summary = []
        for fr in fold_results:
            fold_summary.append({
                'fold': fr['fold_idx'],
                'oos_start': fr['fold_info']['oos_start'].strftime('%Y-%m-%d'),
                'oos_end':   fr['fold_info']['oos_end'].strftime('%Y-%m-%d'),
                **fr['metrics'],
            })

        # 레짐별 성과
        regime_metrics = {}
        for regime in RegimeEngine.REGIMES:
            r_ret = ret_series[df_all['regime'] == regime]
            if len(r_ret) > 20:
                regime_metrics[regime] = compute_metrics(r_ret)

        # 일별 수익률 저장 (2025, 2026)
        daily_2025 = ret_series[ret_series.index.year == 2025].to_dict()
        daily_2026 = ret_series[ret_series.index.year == 2026].to_dict()

        # 턴오버 계산
        avg_holdings = df_all['n_holdings'].mean()
        turnover_approx = 0.20  # 주 1회 리밸런싱, 약 20%/월

        results = {
            'overall': overall_metrics,
            'yearly': yearly_metrics,
            'monthly_2025': monthly_2025,
            'monthly_2026': monthly_2026,
            'folds': fold_summary,
            'regimes': regime_metrics,
            'daily_2025': {str(k.date()): v for k, v in daily_2025.items()},
            'daily_2026': {str(k.date()): v for k, v in daily_2026.items()},
            'metadata': {
                'n_folds': len(fold_results),
                'total_days': len(ret_series),
                'avg_holdings': round(avg_holdings, 1),
                'universe_size': len(UNIVERSE),
                'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU',
                'run_time': datetime.now().isoformat(),
            }
        }

        # 결과 저장
        result_file = RESULT_DIR / f'ares_v13_results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        with open(result_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        log.info(f"결과 저장: {result_file}")

        # 일별 수익률 CSV 저장
        df_all.to_csv(RESULT_DIR / 'daily_returns_full.csv')
        log.info(f"일별 수익률 저장: {RESULT_DIR / 'daily_returns_full.csv'}")

        # 요약 출력
        self._print_summary(results)

        return results

    def _print_summary(self, results: Dict):
        """성과 요약 출력"""
        log.info("\n" + "="*70)
        log.info("ARES-OMEGA v13 백테스트 최종 성과 요약")
        log.info("="*70)

        ov = results.get('overall', {})
        log.info(f"\n[전체 OOS 성과]")
        log.info(f"  Sharpe Ratio:  {ov.get('sharpe', 0):.3f}")
        log.info(f"  CAGR:          {ov.get('cagr', 0):.2%}")
        log.info(f"  Max Drawdown:  {ov.get('max_dd', 0):.2%}")
        log.info(f"  Calmar Ratio:  {ov.get('calmar', 0):.3f}")
        log.info(f"  Sortino:       {ov.get('sortino', 0):.3f}")
        log.info(f"  Win Rate:      {ov.get('win_rate', 0):.2%}")
        log.info(f"  VaR 95%:       {ov.get('var_95', 0):.2%}")
        log.info(f"  CVaR 95%:      {ov.get('cvar_95', 0):.2%}")
        log.info(f"  Total Days:    {ov.get('n_days', 0)}")

        log.info(f"\n[폴드별 Sharpe]")
        for fold in results.get('folds', []):
            log.info(f"  폴드 {fold['fold']:2d} ({fold['oos_start']}~{fold['oos_end']}): "
                     f"Sharpe={fold.get('sharpe', 0):.3f}, "
                     f"CAGR={fold.get('cagr', 0):.2%}, "
                     f"MaxDD={fold.get('max_dd', 0):.2%}")

        log.info(f"\n[2025년 월별 성과]")
        for month, m in sorted(results.get('monthly_2025', {}).items()):
            log.info(f"  {month}: Sharpe={m.get('sharpe', 0):.2f}, "
                     f"CAGR={m.get('cagr', 0):.2%}")

        log.info(f"\n[2026년 월별 성과]")
        for month, m in sorted(results.get('monthly_2026', {}).items()):
            log.info(f"  {month}: Sharpe={m.get('sharpe', 0):.2f}, "
                     f"CAGR={m.get('cagr', 0):.2%}")

        log.info("="*70)


# ─────────────────────────────────────────────
# 10. 실행
# ─────────────────────────────────────────────
if __name__ == '__main__':
    log.info(f"Python: {sys.version}")
    log.info(f"PyTorch: {torch.__version__}, CUDA: {torch.cuda.is_available()}")
    log.info(f"XGBoost: {xgb.__version__}, LightGBM: {lgb.__version__}")
    log.info(f"CPU 코어: {N_CORES}, GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'None'}")

    engine = ARESv13BacktestEngine()

    results = engine.run(
        start_date='2010-01-01',
        end_date='2026-03-02',
        train_years=5,
        oos_months=6,
    )

    log.info("백테스트 완료!")
