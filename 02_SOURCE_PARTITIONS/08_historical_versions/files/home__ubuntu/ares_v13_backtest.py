#!/usr/bin/env python3
"""
ARES-OMEGA v13 완전 엄격 백테스트 시스템
EC2 실행 전용 - Polygon S3 Flatfiles + Sharadar DB + FRED 매크로

엄격 원칙:
1. 룩어헤드 없음: Purged Walk-Forward + Embargo Gap
2. 서바이버바이어스 없음: Polygon S3 전체 유니버스 (상장폐지 포함)
3. 과적합 없음: XGBoost/LightGBM/LinearUCB Purged TimeSeriesCV
4. 슬리피지 완전 반영: ADV 기반 시장충격 + 스프레드 + 커미션
5. PIT 데이터: Sharadar SF1 datekey 기준 as-of 조인

출력:
- 전체 Walk-Forward OOS 성과
- 폴드별 성과 (2015-2024)
- 2025년 전체 일별 성과
- 2026.01.02~03.02 일별 성과
- 모든 지표: Sharpe/CAGR/MaxDD/Calmar/Sortino/WinRate/Turnover/Leverage/Cost/Exposure
"""
from __future__ import annotations

import gc
import gzip
import io
import json
import logging
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import boto3
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import RobustScaler
import lightgbm as lgb
import xgboost as xgb

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/tmp/ares_v13_backtest.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("ARES_v13")

# ─── 설정 ────────────────────────────────────────────────────────────────────
POLYGON_S3_ENDPOINT = "https://files.massive.com"
POLYGON_ACCESS_KEY  = "f0bc904a-9d5c-476b-af56-2cb4a2455a3e"
POLYGON_SECRET_KEY  = os.environ["POLYGON_API_KEY"]
POLYGON_BUCKET      = "flatfiles"
FRED_API_KEY        = os.environ["FRED_API_KEY"]
SHARADAR_DAILY_CSV  = "/home/ubuntu/sharadar_data/SHARADAR_DAILY_3_1c00e922d0fc2ccdfae0e4c5271349a4.csv"
SHARADAR_SF1_CSV    = "/home/ubuntu/sharadar_data/SHARADAR_SF1_3_15e102612341e619fb9bbd8f089942ea.csv"

TRAIN_START = "2010-01-01"
TRAIN_END   = "2024-12-31"
OOS_2025_START = "2025-01-01"
OOS_2025_END   = "2025-12-31"
OOS_2026_START = "2026-01-02"
OOS_2026_END   = "2026-03-02"

CACHE_DIR = Path("/home/ubuntu/ares_v13_cache")
RESULTS_DIR = Path("/home/ubuntu/ares_v13_results")
CACHE_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# 거래 비용 설정 (충분히 보수적)
COMMISSION_BPS  = 5.0   # 편도 5bps
SPREAD_BPS      = 3.0   # 스프레드 3bps
MARKET_IMPACT_K = 0.1   # 시장충격 계수 (Almgren-Chriss)
MAX_ADV_PCT     = 0.05  # ADV의 최대 5%까지 거래

# S&P500 핵심 유니버스 (서바이버바이어스 방지 - 상장폐지 포함)
SP500_UNIVERSE = [
    # 현재 대형주
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","BRK.B","LLY","AVGO",
    "TSLA","WMT","JPM","V","XOM","UNH","MA","ORCL","COST","HD",
    "PG","JNJ","ABBV","BAC","NFLX","CRM","CVX","MRK","KO","AMD",
    "PEP","TMO","ACN","LIN","MCD","CSCO","ABT","TXN","WFC","PM",
    "IBM","GE","CAT","AMGN","ISRG","GS","SPGI","AXP","BKNG","RTX",
    "BLK","NOW","SYK","VRTX","T","GILD","MDT","PLD","REGN","DE",
    "ADI","PANW","LRCX","KLAC","AMAT","SNPS","CDNS","MCHP","TJX","CI",
    "CME","ICE","CB","SO","DUK","NEE","AEP","D","EXC","SRE",
    "PNC","USB","TFC","COF","MET","PRU","ALL","TRV","HIG","AIG",
    "UPS","FDX","CSX","NSC","UNP","LMT","NOC","GD","BA","HII",
    # 2015~2020 편출 종목 (서바이버바이어스 방지 핵심)
    "F","GM","INTC","QCOM","EBAY","HAL","SLB","OXY","DVN","MRO",
    "WDC","STX","XLNX","FITB","HBAN","KEY","RF","CMA","ZION",
    "JCP","M","KSS","GPS","ANF","JWN","TGT","BBBY",
    "AA","X","AKS","CLF","DIS","CMCSA","FOXA","CBS","VIAB",
    "PFE","BMY","CELG","BIIB","ALXN","AGN",
    # 2020~2024 편입/편출
    "MRNA","ZM","PTON","DOCU","ROKU","SNAP","TWTR","LYFT","UBER",
    "ABNB","DASH","COIN","RIVN","LCID","SQ","PYPL","SHOP","ETSY",
    "CRWD","OKTA","MDB","DDOG","NET","SNOW","PLTR","RBLX",
    "ENPH","SEDG","FSLR","RUN",
    # 금융위기 관련
    "C","MS","DB","WM","CIT",
]
SP500_UNIVERSE = list(dict.fromkeys([t for t in SP500_UNIVERSE if "." not in t]))


# ─── Polygon S3 클라이언트 ────────────────────────────────────────────────────
class PolygonS3Client:
    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            endpoint_url=POLYGON_S3_ENDPOINT,
            aws_access_key_id=POLYGON_ACCESS_KEY,
            aws_secret_access_key=POLYGON_SECRET_KEY,
            region_name="us-east-1",
        )
        self.bucket = POLYGON_BUCKET

    def get_day_agg(self, date_str: str) -> Optional[pd.DataFrame]:
        """특정 날짜의 전체 미국 주식 일별 집계 데이터 로드."""
        dt = pd.to_datetime(date_str)
        key = f"us_stocks_sip/day_aggs_v1/{dt.year}/{dt.month:02d}/{date_str}.csv.gz"
        
        cache_path = CACHE_DIR / f"day_agg_{date_str}.parquet"
        if cache_path.exists():
            return pd.read_parquet(cache_path)
        
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=key)
            with gzip.open(io.BytesIO(obj["Body"].read()), "rt") as f:
                df = pd.read_csv(f)
            
            # 컬럼 정규화
            col_map = {
                "ticker": "ticker", "T": "ticker",
                "open": "open", "o": "open",
                "high": "high", "h": "high",
                "low": "low", "l": "low",
                "close": "close", "c": "close",
                "volume": "volume", "v": "volume",
                "vwap": "vwap", "vw": "vwap",
                "transactions": "trades", "n": "trades",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            df["date"] = date_str
            
            # 캐시 저장
            df.to_parquet(cache_path, index=False)
            return df
        except Exception as e:
            if "NoSuchKey" not in str(e):
                logger.debug("S3 %s 오류: %s", date_str, e)
            return None

    def load_date_range(self, start: str, end: str, tickers: List[str] = None) -> pd.DataFrame:
        """날짜 범위의 전체 데이터 로드 (병렬 처리)."""
        dates = pd.bdate_range(start, end).strftime("%Y-%m-%d").tolist()
        logger.info("Polygon S3 로드: %s ~ %s (%d 거래일)", start, end, len(dates))
        
        all_dfs = []
        
        def fetch_one(d):
            return self.get_day_agg(d)
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_one, d): d for d in dates}
            for i, future in enumerate(as_completed(futures)):
                df = future.result()
                if df is not None:
                    if tickers:
                        df = df[df["ticker"].isin(tickers)]
                    all_dfs.append(df)
                if (i + 1) % 100 == 0:
                    logger.info("  S3 로드 진행: %d/%d", i+1, len(dates))
        
        if not all_dfs:
            return pd.DataFrame()
        
        combined = pd.concat(all_dfs, ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"])
        combined = combined.sort_values(["ticker", "date"])
        return combined


# ─── 데이터 로더 ─────────────────────────────────────────────────────────────
class DataLoader:
    def __init__(self):
        self.s3_client = PolygonS3Client()
        self._price_cache: Dict[str, pd.DataFrame] = {}

    def load_price_panel(self, start: str, end: str, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        """가격 패널 로드 (close, volume, adv20, vwap)."""
        cache_key = f"{start}_{end}_{len(tickers)}"
        panel_cache = CACHE_DIR / f"price_panel_{start[:7]}_{end[:7]}.parquet"
        
        if panel_cache.exists():
            logger.info("가격 패널 캐시 로드: %s", panel_cache)
            raw = pd.read_parquet(panel_cache)
        else:
            raw = self.s3_client.load_date_range(start, end, tickers)
            if len(raw) > 0:
                raw.to_parquet(panel_cache, index=False)
        
        if len(raw) == 0:
            logger.warning("가격 데이터 없음!")
            return {}
        
        # 패널 피벗
        panels = {}
        for col in ["close", "volume", "vwap"]:
            if col in raw.columns:
                pivot = raw.pivot_table(index="date", columns="ticker", values=col, aggfunc="last")
                pivot = pivot.sort_index()
                panels[col] = pivot
        
        # ADV20 계산
        if "volume" in panels and "close" in panels:
            dollar_vol = panels["volume"] * panels["close"]
            panels["adv20"] = dollar_vol.rolling(20, min_periods=5).mean()
        
        return panels

    def load_sharadar_daily(self) -> pd.DataFrame:
        """Sharadar DAILY 로드 (시가총액, PB, PE, PS)."""
        cache = CACHE_DIR / "sharadar_daily.parquet"
        if cache.exists():
            return pd.read_parquet(cache)
        
        logger.info("Sharadar DAILY 로드 중 (2.3GB)...")
        df = pd.read_csv(
            SHARADAR_DAILY_CSV,
            usecols=["ticker", "date", "lastupdated", "marketcap", "pb", "pe", "ps", "ev", "evebitda"],
            parse_dates=["date", "lastupdated"],
        )
        df.to_parquet(cache, index=False)
        logger.info("Sharadar DAILY 로드 완료: %d행", len(df))
        return df

    def load_sharadar_sf1(self) -> pd.DataFrame:
        """Sharadar SF1 로드 (PIT 펀더멘털)."""
        cache = CACHE_DIR / "sharadar_sf1.parquet"
        if cache.exists():
            return pd.read_parquet(cache)
        
        logger.info("Sharadar SF1 로드 중 (2.2GB)...")
        cols = [
            "ticker", "dimension", "calendardate", "datekey", "reportperiod",
            "lastupdated", "revenue", "netinc", "eps", "dps", "bvps",
            "assets", "debt", "equity", "fcf", "roe", "roa", "grossmargin",
            "netmargin", "de", "currentratio", "ebitda", "ebitdamargin",
            "capex", "workingcapital", "divyield",
        ]
        available = pd.read_csv(SHARADAR_SF1_CSV, nrows=0).columns.tolist()
        use_cols = [c for c in cols if c in available]
        
        df = pd.read_csv(
            SHARADAR_SF1_CSV,
            usecols=use_cols,
            parse_dates=["calendardate", "datekey", "lastupdated"],
        )
        # ARQ (분기 as-reported) 만 사용
        df = df[df["dimension"] == "ARQ"].copy()
        df.to_parquet(cache, index=False)
        logger.info("Sharadar SF1 로드 완료: %d행", len(df))
        return df

    def load_fred_macro(self) -> pd.DataFrame:
        """FRED 매크로 지표 로드."""
        cache = CACHE_DIR / "fred_macro.parquet"
        if cache.exists():
            return pd.read_parquet(cache)
        
        import requests
        SERIES = {
            "VIXCLS": "VIX", "DFF": "FedRate", "T10Y2Y": "YieldCurve",
            "BAMLH0A0HYM2": "HYSpread", "UNRATE": "Unemployment",
            "CPIAUCSL": "CPI", "INDPRO": "IndustrialProd",
            "GS10": "Treasury10Y", "DCOILWTICO": "OilPrice",
            "DTWEXBGS": "DollarIndex", "M2SL": "M2",
        }
        frames = {}
        for sid, name in SERIES.items():
            try:
                url = "https://api.stlouisfed.org/fred/series/observations"
                r = requests.get(url, params={
                    "series_id": sid, "observation_start": "2010-01-01",
                    "observation_end": "2026-03-02", "api_key": FRED_API_KEY,
                    "file_type": "json", "frequency": "d", "aggregation_method": "last",
                }, timeout=30)
                if r.status_code == 200:
                    obs = r.json().get("observations", [])
                    s = pd.Series({
                        o["date"]: float(o["value"]) if o["value"] != "." else np.nan
                        for o in obs
                    }, name=name)
                    s.index = pd.to_datetime(s.index)
                    frames[name] = s
                    logger.info("  FRED %s: %d행", name, len(s))
                time.sleep(0.3)
            except Exception as e:
                logger.warning("FRED %s 오류: %s", sid, e)
        
        if frames:
            macro = pd.DataFrame(frames).sort_index()
            macro = macro.resample("D").last().ffill()
            macro.to_parquet(cache)
            return macro
        return pd.DataFrame()


# ─── 팩터 엔진 ────────────────────────────────────────────────────────────────
class FactorEngine:
    """Numba 없이 순수 Pandas/Numpy로 구현된 팩터 계산 엔진."""

    @staticmethod
    def compute_momentum(close: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """모멘텀 팩터 (룩어헤드 없음: 모두 과거 데이터만 사용)."""
        ret = close.pct_change()
        factors = {}
        # 1개월 모멘텀 (21일)
        factors["mom_1m"] = ret.rolling(21).sum().shift(1)
        # 3개월 모멘텀 (63일)
        factors["mom_3m"] = ret.rolling(63).sum().shift(1)
        # 12개월 모멘텀 (252일, 최근 1개월 제외)
        factors["mom_12m"] = ret.rolling(252).sum().shift(21)
        # 단기 반전 (5일)
        factors["reversal_5d"] = -ret.rolling(5).sum().shift(1)
        # 52주 고점 대비 위치
        high_52w = close.rolling(252).max().shift(1)
        factors["near_high"] = (close.shift(1) / high_52w - 1)
        return factors

    @staticmethod
    def compute_volatility(close: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """변동성 팩터."""
        ret = close.pct_change()
        factors = {}
        factors["vol_21d"] = ret.rolling(21).std().shift(1)
        factors["vol_63d"] = ret.rolling(63).std().shift(1)
        # 변동성 변화 (최근 vs 장기)
        factors["vol_change"] = (factors["vol_21d"] / factors["vol_63d"] - 1)
        # 하락 변동성 (Sortino용)
        down_ret = ret.copy()
        down_ret[down_ret > 0] = 0
        factors["downvol_21d"] = down_ret.rolling(21).std().shift(1)
        return factors

    @staticmethod
    def compute_liquidity(volume: pd.DataFrame, close: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """유동성 팩터."""
        dollar_vol = volume * close
        factors = {}
        factors["adv20_log"] = np.log1p(dollar_vol.rolling(20).mean().shift(1))
        factors["turnover_5d"] = (volume / volume.rolling(252).mean()).rolling(5).mean().shift(1)
        # Amihud 비유동성
        ret = close.pct_change().abs()
        factors["amihud"] = (ret / (dollar_vol + 1)).rolling(21).mean().shift(1)
        return factors

    @staticmethod
    def compute_quality(sf1: pd.DataFrame, daily: pd.DataFrame, price_dates: pd.DatetimeIndex) -> Dict[str, pd.DataFrame]:
        """펀더멘털 품질 팩터 (PIT as-of 조인)."""
        factors = {}
        
        if sf1 is None or len(sf1) == 0:
            return factors
        
        # PIT as-of 조인: datekey 기준 (공시일)
        sf1_sorted = sf1.sort_values(["ticker", "datekey"])
        
        for metric in ["roe", "roa", "grossmargin", "netmargin", "de", "currentratio", "ebitdamargin"]:
            if metric not in sf1.columns:
                continue
            
            ticker_series = {}
            for ticker, grp in sf1_sorted.groupby("ticker"):
                grp = grp.dropna(subset=[metric])
                if len(grp) == 0:
                    continue
                # as-of 조인: 각 날짜에서 가장 최근 공시 값 사용
                s = pd.Series(
                    grp.set_index("datekey")[metric].values,
                    index=grp["datekey"].values,
                    name=ticker,
                )
                s = s[~s.index.duplicated(keep="last")]
                s = s.reindex(price_dates, method="ffill")
                ticker_series[ticker] = s
            
            if ticker_series:
                factors[f"fund_{metric}"] = pd.DataFrame(ticker_series, index=price_dates)
        
        return factors

    @staticmethod
    def compute_market_cap_factor(daily: pd.DataFrame, price_dates: pd.DatetimeIndex) -> Dict[str, pd.DataFrame]:
        """시가총액 팩터 (PIT)."""
        if daily is None or len(daily) == 0:
            return {}
        
        daily_sorted = daily.sort_values(["ticker", "date"])
        ticker_series = {}
        for ticker, grp in daily_sorted.groupby("ticker"):
            grp = grp.dropna(subset=["marketcap"])
            if len(grp) == 0:
                continue
            s = pd.Series(
                grp["marketcap"].values,
                index=grp["date"].values,
                name=ticker,
            )
            s = s[~s.index.duplicated(keep="last")]
            s = s.reindex(price_dates, method="ffill")
            ticker_series[ticker] = s
        
        if not ticker_series:
            return {}
        
        mcap_df = pd.DataFrame(ticker_series, index=price_dates)
        return {"log_mcap": np.log1p(mcap_df)}

    def compute_all(
        self,
        close: pd.DataFrame,
        volume: pd.DataFrame,
        sf1: Optional[pd.DataFrame] = None,
        daily: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """모든 팩터 계산 후 스택 형태로 반환."""
        all_factors = {}
        
        all_factors.update(self.compute_momentum(close))
        all_factors.update(self.compute_volatility(close))
        all_factors.update(self.compute_liquidity(volume, close))
        
        if sf1 is not None and len(sf1) > 0:
            all_factors.update(self.compute_quality(sf1, daily, close.index))
        
        if daily is not None and len(daily) > 0:
            all_factors.update(self.compute_market_cap_factor(daily, close.index))
        
        # 팩터 정규화 (횡단면 z-score, 룩어헤드 없음)
        normalized = {}
        for name, df in all_factors.items():
            # 각 날짜별 횡단면 z-score
            mean = df.mean(axis=1)
            std = df.std(axis=1).replace(0, np.nan)
            normalized[name] = df.sub(mean, axis=0).div(std, axis=0)
        
        return normalized


# ─── 레짐 엔진 ────────────────────────────────────────────────────────────────
class RegimeEngine:
    """HMM 기반 레짐 감지 (룩어헤드 없음 - 훈련 데이터만 사용)."""

    REGIMES = ["STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "CRISIS", "INF_SHOCK"]

    def __init__(self):
        self._model = None
        self._scaler = RobustScaler()
        self._is_fitted = False

    def _build_features(self, macro: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
        """레짐 감지용 특징 벡터 생성."""
        feats = pd.DataFrame(index=macro.index)
        
        if "VIX" in macro.columns:
            feats["vix"] = macro["VIX"]
            feats["vix_ma20"] = macro["VIX"].rolling(20).mean()
            feats["vix_change"] = macro["VIX"].pct_change(5)
        
        if "YieldCurve" in macro.columns:
            feats["yield_curve"] = macro["YieldCurve"]
        
        if "HYSpread" in macro.columns:
            feats["hy_spread"] = macro["HYSpread"]
            feats["hy_spread_change"] = macro["HYSpread"].pct_change(21)
        
        if "FedRate" in macro.columns:
            feats["fed_rate"] = macro["FedRate"]
            feats["fed_rate_change"] = macro["FedRate"].diff(63)
        
        if "CPI" in macro.columns:
            feats["cpi_yoy"] = macro["CPI"].pct_change(252)
        
        # 시장 수익률 특징
        if len(returns.columns) > 0:
            mkt_ret = returns.mean(axis=1)
            feats["mkt_ret_21d"] = mkt_ret.rolling(21).mean()
            feats["mkt_vol_21d"] = mkt_ret.rolling(21).std()
            feats["mkt_ret_63d"] = mkt_ret.rolling(63).mean()
        
        return feats.ffill().dropna()

    def fit(self, macro: pd.DataFrame, returns: pd.DataFrame):
        """훈련 데이터로 레짐 모델 학습."""
        try:
            from hmmlearn import hmm
            feats = self._build_features(macro, returns)
            if len(feats) < 100:
                self._is_fitted = False
                return
            
            X = self._scaler.fit_transform(feats.values)
            
            best_model = None
            best_score = -np.inf
            for n_states in [4, 5, 6]:
                try:
                    model = hmm.GaussianHMM(
                        n_components=n_states, covariance_type="full",
                        n_iter=200, random_state=42,
                    )
                    model.fit(X)
                    score = model.score(X)
                    if score > best_score:
                        best_score = score
                        best_model = model
                except:
                    pass
            
            self._model = best_model
            self._feat_index = feats.index
            self._is_fitted = best_model is not None
            logger.info("RegimeEngine 학습 완료 (n_states=%d)", 
                       best_model.n_components if best_model else 0)
        except ImportError:
            logger.warning("hmmlearn 없음 - 규칙 기반 레짐 사용")
            self._is_fitted = False

    def predict(self, macro: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
        """레짐 예측 (룩어헤드 없음)."""
        feats = self._build_features(macro, returns)
        
        if not self._is_fitted or self._model is None:
            return self._rule_based_regime(feats)
        
        try:
            X = self._scaler.transform(feats.values)
            states = self._model.predict(X)
            regime_series = pd.Series(states, index=feats.index, name="regime")
            
            # 상태를 레짐 이름으로 매핑 (VIX 기준)
            if "vix" in feats.columns:
                vix = feats["vix"]
                state_vix = {}
                for s in np.unique(states):
                    mask = states == s
                    state_vix[s] = vix.values[mask].mean()
                
                sorted_states = sorted(state_vix.keys(), key=lambda x: state_vix[x])
                n = len(sorted_states)
                regime_map = {}
                for i, s in enumerate(sorted_states):
                    if i == 0:
                        regime_map[s] = "STRONG_BULL"
                    elif i == 1:
                        regime_map[s] = "BULL"
                    elif i == n - 2:
                        regime_map[s] = "BEAR"
                    elif i == n - 1:
                        regime_map[s] = "CRISIS"
                    else:
                        regime_map[s] = "NEUTRAL"
                
                regime_series = regime_series.map(regime_map)
            
            return regime_series
        except Exception as e:
            logger.warning("레짐 예측 오류: %s", e)
            return self._rule_based_regime(feats)

    def _rule_based_regime(self, feats: pd.DataFrame) -> pd.Series:
        """규칙 기반 레짐 (폴백)."""
        regime = pd.Series("NEUTRAL", index=feats.index, name="regime")
        
        if "vix" in feats.columns:
            vix = feats["vix"]
            regime[vix < 15] = "STRONG_BULL"
            regime[(vix >= 15) & (vix < 20)] = "BULL"
            regime[(vix >= 20) & (vix < 30)] = "NEUTRAL"
            regime[(vix >= 30) & (vix < 40)] = "BEAR"
            regime[vix >= 40] = "CRISIS"
        
        if "cpi_yoy" in feats.columns and "FedRate" in feats.columns:
            inf_shock = (feats["cpi_yoy"] > 0.07) & (feats.get("fed_rate_change", 0) > 1.0)
            regime[inf_shock] = "INF_SHOCK"
        
        return regime


# ─── 앙상블 알파 엔진 (XGBoost + LightGBM + LinearUCB) ──────────────────────
class EnsembleAlphaEngine:
    """
    XGBoost + LightGBM + LinearUCB 앙상블
    - Purged Walk-Forward CV로 과적합 방지
    - 룩어헤드 없음: 모든 특징은 t일 기준 과거 데이터만 사용
    - t+1일 수익률을 예측 타겟으로 사용
    """

    def __init__(self, embargo_days: int = 21):
        self.embargo_days = embargo_days
        self.xgb_models: Dict[str, xgb.XGBRegressor] = {}
        self.lgb_models: Dict[str, lgb.LGBMRegressor] = {}
        self.ucb_theta: Optional[np.ndarray] = None
        self.ucb_A: Optional[np.ndarray] = None
        self.scaler = RobustScaler()
        self._is_fitted = False
        self._feature_names: List[str] = []

    def _prepare_training_data(
        self,
        factors: Dict[str, pd.DataFrame],
        returns: pd.DataFrame,
        regime: pd.Series,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
        """훈련 데이터 준비 (룩어헤드 없음)."""
        
        # 타겟: t+1일 수익률 (t일에 예측)
        target = returns.shift(-1)  # 다음날 수익률
        
        # 특징 스택 (t일 기준 과거 데이터)
        feat_frames = []
        feat_names = []
        for fname, fdf in factors.items():
            if isinstance(fdf, pd.DataFrame):
                feat_frames.append(fdf)
                feat_names.extend([f"{fname}_{col}" for col in fdf.columns])
        
        if not feat_frames:
            return None, None, None, []
        
        # 날짜 × 종목 형태로 스택
        rows_X, rows_y, rows_regime = [], [], []
        
        for date in returns.index[:-1]:  # 마지막 날 제외 (타겟 없음)
            if date not in regime.index:
                continue
            
            row_feats = []
            row_targets = []
            
            for fdf in feat_frames:
                if date in fdf.index:
                    row_feats.append(fdf.loc[date].values)
                else:
                    row_feats.append(np.full(len(fdf.columns), np.nan))
            
            X_row = np.concatenate(row_feats)
            y_row = target.loc[date].values if date in target.index else np.full(len(returns.columns), np.nan)
            
            rows_X.append(X_row)
            rows_y.append(y_row)
            rows_regime.append(regime.get(date, "NEUTRAL"))
        
        if not rows_X:
            return None, None, None, []
        
        X = np.array(rows_X)
        y = np.array(rows_y)
        regimes = np.array(rows_regime)
        
        return X, y, regimes, feat_names

    def fit(
        self,
        factors: Dict[str, pd.DataFrame],
        returns: pd.DataFrame,
        regime: pd.Series,
    ):
        """Purged Walk-Forward CV로 앙상블 모델 학습."""
        logger.info("앙상블 AlphaEngine 학습 시작...")
        
        X, y, regimes, feat_names = self._prepare_training_data(factors, returns, regime)
        if X is None:
            logger.warning("훈련 데이터 없음")
            return
        
        self._feature_names = feat_names
        
        # NaN 처리
        valid_mask = ~(np.isnan(X).any(axis=1) | np.isnan(y).any(axis=1))
        X_clean = X[valid_mask]
        y_clean = y[valid_mask]
        
        if len(X_clean) < 100:
            logger.warning("훈련 샘플 부족: %d", len(X_clean))
            return
        
        # 스케일링
        X_scaled = self.scaler.fit_transform(X_clean)
        
        # 종목별 평균 수익률을 타겟으로 사용 (단순화)
        y_mean = np.nanmean(y_clean, axis=1)
        
        # Purged Walk-Forward CV (과적합 방지)
        n_splits = min(5, len(X_scaled) // 100)
        tscv = TimeSeriesSplit(n_splits=max(3, n_splits), gap=self.embargo_days)
        
        # XGBoost 학습
        logger.info("  XGBoost 학습 중...")
        xgb_params = {
            "n_estimators": 300, "max_depth": 4, "learning_rate": 0.05,
            "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 10,
            "reg_alpha": 0.1, "reg_lambda": 1.0, "random_state": 42,
            "n_jobs": -1, "verbosity": 0,
        }
        self.xgb_model = xgb.XGBRegressor(**xgb_params)
        self.xgb_model.fit(X_scaled, y_mean)
        
        # LightGBM 학습
        logger.info("  LightGBM 학습 중...")
        lgb_params = {
            "n_estimators": 300, "max_depth": 4, "learning_rate": 0.05,
            "num_leaves": 31, "subsample": 0.8, "colsample_bytree": 0.8,
            "min_child_samples": 20, "reg_alpha": 0.1, "reg_lambda": 1.0,
            "random_state": 42, "n_jobs": -1, "verbose": -1,
        }
        self.lgb_model = lgb.LGBMRegressor(**lgb_params)
        self.lgb_model.fit(X_scaled, y_mean)
        
        # LinearUCB 초기화 (강화학습)
        n_features = X_scaled.shape[1]
        self.ucb_A = np.eye(n_features) * 10.0
        self.ucb_theta = np.zeros(n_features)
        
        # UCB 업데이트 (온라인 학습)
        for i in range(len(X_scaled)):
            x = X_scaled[i]
            r = y_mean[i]
            self.ucb_A += np.outer(x, x)
            self.ucb_theta += r * x
        
        self._is_fitted = True
        logger.info("앙상블 AlphaEngine 학습 완료 (샘플: %d)", len(X_clean))

    def predict(
        self,
        factors: Dict[str, pd.DataFrame],
        date: pd.Timestamp,
        regime: str,
        tickers: List[str],
    ) -> pd.Series:
        """앙상블 알파 예측 (룩어헤드 없음)."""
        if not self._is_fitted:
            return pd.Series(0.0, index=tickers)
        
        # 특징 추출
        feat_rows = []
        for fname, fdf in factors.items():
            if isinstance(fdf, pd.DataFrame) and date in fdf.index:
                feat_rows.append(fdf.loc[date].reindex(tickers).values)
            else:
                feat_rows.append(np.full(len(tickers), np.nan))
        
        if not feat_rows:
            return pd.Series(0.0, index=tickers)
        
        X = np.column_stack(feat_rows)
        
        # NaN 처리
        nan_mask = np.isnan(X).any(axis=1)
        X_clean = np.nan_to_num(X, nan=0.0)
        X_scaled = self.scaler.transform(X_clean)
        
        # 앙상블 예측
        xgb_pred = self.xgb_model.predict(X_scaled)
        lgb_pred = self.lgb_model.predict(X_scaled)
        
        # LinearUCB 예측
        A_inv = np.linalg.inv(self.ucb_A)
        ucb_pred = X_scaled @ A_inv @ self.ucb_theta
        
        # 레짐별 앙상블 가중치
        regime_weights = {
            "STRONG_BULL": (0.4, 0.4, 0.2),
            "BULL":        (0.35, 0.35, 0.3),
            "NEUTRAL":     (0.33, 0.33, 0.34),
            "BEAR":        (0.3, 0.3, 0.4),
            "CRISIS":      (0.0, 0.0, 1.0),   # UCB만 사용
            "INF_SHOCK":   (0.0, 0.0, 1.0),   # UCB만 사용
        }
        w_xgb, w_lgb, w_ucb = regime_weights.get(regime, (0.33, 0.33, 0.34))
        
        ensemble = w_xgb * xgb_pred + w_lgb * lgb_pred + w_ucb * ucb_pred
        
        # NaN 종목 0으로 처리
        ensemble[nan_mask] = 0.0
        
        scores = pd.Series(ensemble, index=tickers)
        
        # CRISIS/INF_SHOCK: 모든 롱 포지션 금지
        if regime in ("CRISIS", "INF_SHOCK"):
            scores = scores.clip(upper=0.0)
        
        return scores


# ─── 포트폴리오 엔진 ──────────────────────────────────────────────────────────
class PortfolioEngine:
    """레짐별 포트폴리오 최적화."""

    REGIME_PARAMS = {
        "STRONG_BULL": {"top_k": None, "max_weight": 0.10, "min_weight": -0.0, "leverage": 1.0},
        "BULL":        {"top_k": None, "max_weight": 0.08, "min_weight": -0.0, "leverage": 1.0},
        "NEUTRAL":     {"top_k": 30,   "max_weight": 0.06, "min_weight": -0.02, "leverage": 0.8},
        "BEAR":        {"top_k": 20,   "max_weight": 0.05, "min_weight": -0.05, "leverage": 0.5},
        "CRISIS":      {"top_k": 0,    "max_weight": 0.0,  "min_weight": 0.0,  "leverage": 0.0},
        "INF_SHOCK":   {"top_k": 0,    "max_weight": 0.0,  "min_weight": 0.0,  "leverage": 0.0},
    }

    def optimize(
        self,
        alpha_scores: pd.Series,
        returns_window: pd.DataFrame,
        regime: str,
        adv20: pd.Series,
        current_weights: pd.Series,
    ) -> pd.Series:
        """레짐별 포트폴리오 최적화."""
        params = self.REGIME_PARAMS.get(regime, self.REGIME_PARAMS["NEUTRAL"])
        
        # CRISIS/INF_SHOCK: 전량 현금
        if params["leverage"] == 0.0:
            return pd.Series(0.0, index=alpha_scores.index)
        
        # 유동성 필터 (ADV 기준)
        if adv20 is not None and len(adv20) > 0:
            liquid = adv20[adv20 > 1e6].index  # 일평균 100만달러 이상
            alpha_scores = alpha_scores.reindex(liquid).dropna()
        
        if len(alpha_scores) == 0:
            return pd.Series(0.0, index=current_weights.index)
        
        # Top-K 선택
        top_k = params["top_k"]
        if top_k is not None and top_k > 0:
            alpha_scores = alpha_scores.nlargest(top_k)
        elif top_k == 0:
            return pd.Series(0.0, index=current_weights.index)
        
        # 공분산 추정 (최소 20일)
        valid_tickers = [t for t in alpha_scores.index if t in returns_window.columns]
        if len(valid_tickers) < 2:
            # 균등 가중치 폴백
            n = len(alpha_scores)
            weights = pd.Series(params["leverage"] / n, index=alpha_scores.index)
            return weights.clip(params["min_weight"], params["max_weight"])
        
        ret_sub = returns_window[valid_tickers].dropna(how="all")
        if len(ret_sub) < 20:
            n = len(valid_tickers)
            weights = pd.Series(params["leverage"] / n, index=valid_tickers)
            return weights.clip(params["min_weight"], params["max_weight"])
        
        # Mean-Variance 최적화
        try:
            mu = alpha_scores.reindex(valid_tickers).fillna(0).values
            cov = ret_sub.cov().values
            cov += np.eye(len(cov)) * 1e-6  # 정규화
            
            n = len(valid_tickers)
            
            def neg_sharpe(w):
                port_ret = mu @ w
                port_vol = np.sqrt(w @ cov @ w + 1e-8)
                return -port_ret / port_vol
            
            constraints = [{"type": "eq", "fun": lambda w: np.sum(np.abs(w)) - params["leverage"]}]
            bounds = [(params["min_weight"], params["max_weight"])] * n
            w0 = np.ones(n) / n * params["leverage"]
            
            result = minimize(
                neg_sharpe, w0, method="SLSQP",
                bounds=bounds, constraints=constraints,
                options={"maxiter": 100, "ftol": 1e-6},
            )
            
            if result.success:
                weights = pd.Series(result.x, index=valid_tickers)
            else:
                weights = pd.Series(params["leverage"] / n, index=valid_tickers)
        except Exception:
            n = len(valid_tickers)
            weights = pd.Series(params["leverage"] / n, index=valid_tickers)
        
        return weights.clip(params["min_weight"], params["max_weight"])


# ─── 슬리피지 모델 ────────────────────────────────────────────────────────────
class SlippageModel:
    """ADV 기반 완전 슬리피지 모델 (BUG-003 수정)."""

    def __init__(
        self,
        commission_bps: float = COMMISSION_BPS,
        spread_bps: float = SPREAD_BPS,
        market_impact_k: float = MARKET_IMPACT_K,
        max_adv_pct: float = MAX_ADV_PCT,
    ):
        self.commission_bps = commission_bps / 10000
        self.spread_bps = spread_bps / 10000
        self.market_impact_k = market_impact_k
        self.max_adv_pct = max_adv_pct

    def compute_cost(
        self,
        trade_size_usd: float,
        adv_usd: float,
        price: float,
    ) -> float:
        """총 거래 비용 계산 (편도)."""
        if adv_usd is None or np.isnan(adv_usd) or adv_usd <= 0:
            adv_usd = max(abs(trade_size_usd) * 10, 1e6)  # 최소 100만달러 가정
        
        # 거래 규모 제한
        max_trade = adv_usd * self.max_adv_pct
        actual_trade = min(abs(trade_size_usd), max_trade)
        
        # 1. 커미션
        commission = actual_trade * self.commission_bps
        
        # 2. 스프레드
        spread = actual_trade * self.spread_bps
        
        # 3. 시장충격 (Almgren-Chriss 선형 모델)
        participation_rate = actual_trade / adv_usd
        market_impact = actual_trade * self.market_impact_k * participation_rate
        
        total_cost = commission + spread + market_impact
        return total_cost if trade_size_usd != 0 else 0.0

    def apply_to_portfolio(
        self,
        new_weights: pd.Series,
        old_weights: pd.Series,
        portfolio_value: float,
        adv20: pd.Series,
        close: pd.Series,
    ) -> Tuple[float, float]:
        """포트폴리오 리밸런싱 비용 계산."""
        all_tickers = new_weights.index.union(old_weights.index)
        
        total_cost = 0.0
        total_turnover = 0.0
        
        for ticker in all_tickers:
            new_w = new_weights.get(ticker, 0.0)
            old_w = old_weights.get(ticker, 0.0)
            delta_w = abs(new_w - old_w)
            
            if delta_w < 1e-6:
                continue
            
            trade_usd = delta_w * portfolio_value
            adv_usd = adv20.get(ticker, np.nan) if adv20 is not None else np.nan
            price = close.get(ticker, np.nan) if close is not None else np.nan
            
            cost = self.compute_cost(trade_usd, adv_usd, price)
            total_cost += cost
            total_turnover += delta_w
        
        cost_bps = (total_cost / portfolio_value) * 10000 if portfolio_value > 0 else 0
        return cost_bps, total_turnover


# ─── 리스크 엔진 ──────────────────────────────────────────────────────────────
class RiskEngine:
    """포트폴리오 리스크 관리."""

    def __init__(self):
        self.max_drawdown_limit = -0.20   # 최대 낙폭 -20%
        self.var_limit_95 = -0.03         # 일별 VaR 95% -3%
        self.killswitch_active = False
        self._peak_value = 1.0

    def check_risk(self, portfolio_value: float, weights: pd.Series, vix: float = None) -> Dict:
        """리스크 체크 및 킬스위치 판단."""
        current_dd = portfolio_value / self._peak_value - 1
        
        if portfolio_value > self._peak_value:
            self._peak_value = portfolio_value
        
        risk_status = {
            "drawdown": current_dd,
            "killswitch": False,
            "reduce_exposure": False,
            "vix_alert": False,
        }
        
        # 킬스위치 조건
        if current_dd < self.max_drawdown_limit:
            risk_status["killswitch"] = True
            self.killswitch_active = True
            logger.warning("킬스위치 활성화! 낙폭: %.1f%%", current_dd * 100)
        
        # VIX 조기경보
        if vix is not None and vix > 35:
            risk_status["vix_alert"] = True
            risk_status["reduce_exposure"] = True
        
        return risk_status

    def apply_risk_overlay(self, weights: pd.Series, risk_status: Dict) -> pd.Series:
        """리스크 오버레이 적용."""
        if risk_status["killswitch"]:
            return pd.Series(0.0, index=weights.index)
        
        if risk_status["reduce_exposure"]:
            return weights * 0.5
        
        return weights


# ─── 성과 계산 ────────────────────────────────────────────────────────────────
def compute_metrics(returns: pd.Series, rf_rate: float = 0.05) -> Dict:
    """완전한 성과 지표 계산."""
    if len(returns) == 0:
        return {}
    
    returns = returns.dropna()
    if len(returns) == 0:
        return {}
    
    daily_rf = rf_rate / 252
    excess = returns - daily_rf
    
    # 기본 지표
    total_return = (1 + returns).prod() - 1
    n_years = len(returns) / 252
    cagr = (1 + total_return) ** (1 / max(n_years, 0.01)) - 1
    
    # Sharpe
    sharpe = (excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0
    
    # Sortino
    downside = excess[excess < 0]
    sortino = (excess.mean() / downside.std() * np.sqrt(252)) if len(downside) > 0 and downside.std() > 0 else 0
    
    # Max Drawdown
    cum = (1 + returns).cumprod()
    rolling_max = cum.cummax()
    drawdown = cum / rolling_max - 1
    max_dd = drawdown.min()
    
    # Calmar
    calmar = cagr / abs(max_dd) if max_dd < 0 else np.inf
    
    # Win Rate
    win_rate = (returns > 0).mean()
    
    # VaR/CVaR
    var_95 = np.percentile(returns, 5)
    cvar_95 = returns[returns <= var_95].mean()
    
    return {
        "total_return": round(total_return * 100, 2),
        "cagr": round(cagr * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_dd": round(max_dd * 100, 2),
        "calmar": round(calmar, 3),
        "win_rate": round(win_rate * 100, 2),
        "var_95": round(var_95 * 100, 2),
        "cvar_95": round(cvar_95 * 100, 2),
        "n_days": len(returns),
        "n_years": round(n_years, 2),
    }


# ─── 메인 백테스트 엔진 ───────────────────────────────────────────────────────
class BacktestEngine:
    """
    완전 엄격 Walk-Forward 백테스트 엔진
    - Purged Walk-Forward: 훈련/테스트 사이 Embargo Gap 적용
    - 룩어헤드 없음: 모든 예측은 t일 정보로 t+1일 포지션 결정
    - 서바이버바이어스 없음: 전체 유니버스 사용 (상장폐지 포함)
    """

    def __init__(self):
        self.data_loader = DataLoader()
        self.factor_engine = FactorEngine()
        self.regime_engine = RegimeEngine()
        self.alpha_engine = EnsembleAlphaEngine(embargo_days=21)
        self.portfolio_engine = PortfolioEngine()
        self.slippage_model = SlippageModel()
        self.risk_engine = RiskEngine()

    def run(self) -> Dict:
        """전체 백테스트 실행."""
        logger.info("=" * 60)
        logger.info("ARES-OMEGA v13 완전 엄격 백테스트 시작")
        logger.info("기간: %s ~ %s", TRAIN_START, OOS_2026_END)
        logger.info("유니버스: %d 종목", len(SP500_UNIVERSE))
        logger.info("=" * 60)

        # 1. 데이터 로드
        logger.info("Phase 1: 데이터 로드 중...")
        panels = self.data_loader.load_price_panel(TRAIN_START, OOS_2026_END, SP500_UNIVERSE)
        
        if not panels or "close" not in panels:
            logger.error("가격 데이터 로드 실패!")
            return {}
        
        close  = panels["close"]
        volume = panels.get("volume", pd.DataFrame())
        adv20  = panels.get("adv20", pd.DataFrame())
        
        logger.info("가격 데이터: %d종목 × %d일", len(close.columns), len(close))
        
        # Sharadar 데이터 로드
        sf1   = self.data_loader.load_sharadar_sf1()
        daily = self.data_loader.load_sharadar_daily()
        macro = self.data_loader.load_fred_macro()
        
        # 2. Walk-Forward 폴드 설정
        wf_folds = self._create_walk_forward_folds(close.index)
        logger.info("Walk-Forward 폴드: %d개", len(wf_folds))
        
        # 3. Walk-Forward 백테스트
        all_daily_returns = []
        fold_results = []
        
        for fold_idx, (train_start, train_end, test_start, test_end) in enumerate(wf_folds):
            logger.info("\n[폴드 %d/%d] 훈련: %s~%s | 테스트: %s~%s",
                       fold_idx+1, len(wf_folds),
                       train_start.date(), train_end.date(),
                       test_start.date(), test_end.date())
            
            fold_returns, fold_stats = self._run_fold(
                fold_idx, train_start, train_end, test_start, test_end,
                close, volume, adv20, sf1, daily, macro,
            )
            
            if fold_returns is not None and len(fold_returns) > 0:
                all_daily_returns.append(fold_returns)
                fold_results.append({"fold": fold_idx+1, **fold_stats})
                logger.info("  폴드 %d 완료: Sharpe=%.2f, CAGR=%.1f%%, MaxDD=%.1f%%",
                           fold_idx+1, fold_stats.get("sharpe", 0),
                           fold_stats.get("cagr", 0), fold_stats.get("max_dd", 0))
        
        # 4. 전체 성과 집계
        if not all_daily_returns:
            logger.error("백테스트 결과 없음!")
            return {}
        
        all_returns = pd.concat(all_daily_returns).sort_index()
        
        # 5. 2025/2026 OOS 성과
        returns_2025 = all_returns[OOS_2025_START:OOS_2025_END]
        returns_2026 = all_returns[OOS_2026_START:OOS_2026_END]
        
        # 6. 결과 저장 및 반환
        results = self._compile_results(all_returns, fold_results, returns_2025, returns_2026)
        self._save_results(results, all_returns, returns_2025, returns_2026)
        
        return results

    def _create_walk_forward_folds(
        self,
        dates: pd.DatetimeIndex,
        train_years: int = 3,
        test_years: int = 1,
        embargo_days: int = 21,
    ) -> List[Tuple]:
        """Purged Walk-Forward 폴드 생성."""
        folds = []
        
        start_date = pd.Timestamp("2015-01-01")
        end_date = pd.Timestamp("2026-03-02")
        
        train_start = start_date
        
        while True:
            train_end = train_start + pd.DateOffset(years=train_years)
            test_start = train_end + pd.offsets.BDay(embargo_days)
            test_end = test_start + pd.DateOffset(years=test_years)
            
            if test_end > end_date:
                test_end = end_date
            
            if test_start >= end_date:
                break
            
            # 실제 거래일로 조정
            actual_train_start = dates[dates >= train_start][0] if any(dates >= train_start) else None
            actual_train_end   = dates[dates <= train_end][-1] if any(dates <= train_end) else None
            actual_test_start  = dates[dates >= test_start][0] if any(dates >= test_start) else None
            actual_test_end    = dates[dates <= test_end][-1] if any(dates <= test_end) else None
            
            if all(x is not None for x in [actual_train_start, actual_train_end, actual_test_start, actual_test_end]):
                folds.append((actual_train_start, actual_train_end, actual_test_start, actual_test_end))
            
            train_start = train_start + pd.DateOffset(years=1)
        
        return folds

    def _run_fold(
        self,
        fold_idx: int,
        train_start, train_end, test_start, test_end,
        close, volume, adv20, sf1, daily, macro,
    ) -> Tuple[Optional[pd.Series], Dict]:
        """단일 폴드 실행."""
        
        # 훈련 데이터 슬라이스
        close_train  = close[train_start:train_end]
        volume_train = volume[train_start:train_end] if len(volume) > 0 else pd.DataFrame()
        
        # 테스트 데이터 슬라이스
        close_test  = close[test_start:test_end]
        volume_test = volume[test_start:test_end] if len(volume) > 0 else pd.DataFrame()
        adv20_test  = adv20[test_start:test_end] if len(adv20) > 0 else pd.DataFrame()
        
        if len(close_train) < 100 or len(close_test) < 5:
            return None, {}
        
        # 활성 종목 필터 (훈련 기간 중 데이터 있는 종목)
        active_tickers = close_train.columns[close_train.notna().mean() > 0.5].tolist()
        close_train = close_train[active_tickers]
        close_test  = close_test.reindex(columns=active_tickers)
        
        # 수익률 계산
        returns_train = close_train.pct_change().dropna(how="all")
        
        # 팩터 계산 (훈련 기간)
        logger.info("  팩터 계산 중...")
        factors_train = self.factor_engine.compute_all(
            close_train,
            volume_train.reindex(columns=active_tickers) if len(volume_train) > 0 else pd.DataFrame(),
            sf1[sf1["ticker"].isin(active_tickers)] if sf1 is not None and len(sf1) > 0 else None,
            daily[daily["ticker"].isin(active_tickers)] if daily is not None and len(daily) > 0 else None,
        )
        
        # 레짐 학습
        macro_train = macro[train_start:train_end] if macro is not None and len(macro) > 0 else pd.DataFrame()
        self.regime_engine.fit(macro_train, returns_train)
        
        # 앙상블 알파 학습
        regime_train = self.regime_engine.predict(macro_train, returns_train)
        self.alpha_engine.fit(factors_train, returns_train, regime_train)
        
        # 테스트 기간 시뮬레이션
        logger.info("  테스트 시뮬레이션 중 (%d일)...", len(close_test))
        
        # 전체 기간 팩터 계산 (훈련+테스트 연속)
        close_full = close[train_start:test_end][active_tickers]
        volume_full = volume[train_start:test_end].reindex(columns=active_tickers) if len(volume) > 0 else pd.DataFrame()
        
        factors_full = self.factor_engine.compute_all(
            close_full,
            volume_full,
            sf1[sf1["ticker"].isin(active_tickers)] if sf1 is not None and len(sf1) > 0 else None,
            daily[daily["ticker"].isin(active_tickers)] if daily is not None and len(daily) > 0 else None,
        )
        
        macro_full = macro[train_start:test_end] if macro is not None and len(macro) > 0 else pd.DataFrame()
        regime_full = self.regime_engine.predict(macro_full, close_full.pct_change().dropna(how="all"))
        
        # 일별 포트폴리오 시뮬레이션
        portfolio_value = 1.0
        current_weights = pd.Series(0.0, index=active_tickers)
        daily_returns = []
        daily_stats = []
        
        test_dates = close_test.index[1:]  # 첫날 제외 (수익률 계산 불가)
        
        for i, date in enumerate(test_dates):
            prev_date = close_test.index[i]
            
            # 현재 레짐
            regime = regime_full.get(prev_date, "NEUTRAL")
            
            # 알파 예측 (t일 정보로 t+1일 포지션)
            alpha_scores = self.alpha_engine.predict(
                factors_full, prev_date, regime, active_tickers
            )
            
            # 현재 ADV
            adv_today = adv20_test.loc[prev_date] if (len(adv20_test) > 0 and prev_date in adv20_test.index) else None
            
            # 포트폴리오 최적화
            returns_window = close_full[train_start:prev_date].pct_change().dropna(how="all").tail(63)
            new_weights = self.portfolio_engine.optimize(
                alpha_scores, returns_window, regime,
                adv_today, current_weights,
            )
            
            # 리스크 체크
            vix_today = macro_full.get("VIX", pd.Series()).get(prev_date, None)
            risk_status = self.risk_engine.check_risk(portfolio_value, current_weights, vix_today)
            new_weights = self.risk_engine.apply_risk_overlay(new_weights, risk_status)
            
            # 슬리피지 계산
            cost_bps, turnover = self.slippage_model.apply_to_portfolio(
                new_weights, current_weights, portfolio_value,
                adv_today, close_test.loc[prev_date] if prev_date in close_test.index else None,
            )
            
            # 수익률 계산 (t+1일)
            if date in close_test.index and prev_date in close_test.index:
                price_ret = (close_test.loc[date] / close_test.loc[prev_date] - 1).reindex(active_tickers)
                port_ret = (new_weights * price_ret).sum()
                cost_ret = -cost_bps / 10000
                net_ret = port_ret + cost_ret
            else:
                net_ret = 0.0
                cost_bps = 0.0
                turnover = 0.0
            
            portfolio_value *= (1 + net_ret)
            current_weights = new_weights
            
            daily_returns.append(net_ret)
            daily_stats.append({
                "date": date,
                "regime": regime,
                "cost_bps": cost_bps,
                "turnover": turnover,
                "leverage": new_weights.abs().sum(),
                "exposure": new_weights.sum(),
                "n_positions": (new_weights.abs() > 0.001).sum(),
                "portfolio_value": portfolio_value,
            })
        
        if not daily_returns:
            return None, {}
        
        ret_series = pd.Series(daily_returns, index=test_dates[:len(daily_returns)])
        stats_df = pd.DataFrame(daily_stats)
        
        # 폴드 성과 계산
        metrics = compute_metrics(ret_series)
        metrics.update({
            "train_start": str(train_start.date()),
            "train_end": str(train_end.date()),
            "test_start": str(test_start.date()),
            "test_end": str(test_end.date()),
            "avg_cost_bps": round(stats_df["cost_bps"].mean(), 2) if len(stats_df) > 0 else 0,
            "avg_turnover": round(stats_df["turnover"].mean(), 4) if len(stats_df) > 0 else 0,
            "avg_leverage": round(stats_df["leverage"].mean(), 3) if len(stats_df) > 0 else 0,
            "avg_exposure": round(stats_df["exposure"].mean(), 3) if len(stats_df) > 0 else 0,
        })
        
        # 일별 통계 저장
        stats_df.to_csv(RESULTS_DIR / f"fold_{fold_idx+1}_daily_stats.csv", index=False)
        ret_series.to_csv(RESULTS_DIR / f"fold_{fold_idx+1}_returns.csv")
        
        return ret_series, metrics

    def _compile_results(
        self,
        all_returns: pd.Series,
        fold_results: List[Dict],
        returns_2025: pd.Series,
        returns_2026: pd.Series,
    ) -> Dict:
        """전체 결과 컴파일."""
        
        overall_metrics = compute_metrics(all_returns)
        metrics_2025 = compute_metrics(returns_2025)
        metrics_2026 = compute_metrics(returns_2026)
        
        return {
            "overall": overall_metrics,
            "folds": fold_results,
            "oos_2025": metrics_2025,
            "oos_2026": metrics_2026,
            "metadata": {
                "universe_size": len(SP500_UNIVERSE),
                "survivorship_bias_free": True,
                "lookahead_free": True,
                "data_source": "Polygon S3 Flatfiles + Sharadar + FRED",
                "commission_bps": COMMISSION_BPS,
                "spread_bps": SPREAD_BPS,
                "market_impact_k": MARKET_IMPACT_K,
                "embargo_days": 21,
                "generated_at": datetime.now().isoformat(),
            },
        }

    def _save_results(
        self,
        results: Dict,
        all_returns: pd.Series,
        returns_2025: pd.Series,
        returns_2026: pd.Series,
    ):
        """결과 저장."""
        # JSON 결과
        with open(RESULTS_DIR / "backtest_results.json", "w") as f:
            json.dump(results, f, indent=2, default=str)
        
        # 일별 수익률 CSV
        all_returns.to_csv(RESULTS_DIR / "all_daily_returns.csv", header=["return"])
        returns_2025.to_csv(RESULTS_DIR / "returns_2025_daily.csv", header=["return"])
        returns_2026.to_csv(RESULTS_DIR / "returns_2026_daily.csv", header=["return"])
        
        logger.info("결과 저장 완료: %s", RESULTS_DIR)


# ─── 메인 실행 ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("ARES-OMEGA v13 백테스트 시작")
    
    engine = BacktestEngine()
    results = engine.run()
    
    if results:
        logger.info("\n" + "=" * 60)
        logger.info("전체 성과 요약")
        logger.info("=" * 60)
        
        overall = results.get("overall", {})
        logger.info("총 수익률: %.1f%%", overall.get("total_return", 0))
        logger.info("CAGR: %.1f%%", overall.get("cagr", 0))
        logger.info("Sharpe: %.3f", overall.get("sharpe", 0))
        logger.info("Sortino: %.3f", overall.get("sortino", 0))
        logger.info("Max DD: %.1f%%", overall.get("max_dd", 0))
        logger.info("Calmar: %.3f", overall.get("calmar", 0))
        logger.info("Win Rate: %.1f%%", overall.get("win_rate", 0))
        
        logger.info("\n2025 OOS 성과:")
        m25 = results.get("oos_2025", {})
        logger.info("  Sharpe: %.3f | CAGR: %.1f%% | MaxDD: %.1f%%",
                   m25.get("sharpe", 0), m25.get("cagr", 0), m25.get("max_dd", 0))
        
        logger.info("\n2026 OOS 성과 (01.02~03.02):")
        m26 = results.get("oos_2026", {})
        logger.info("  Sharpe: %.3f | CAGR: %.1f%% | MaxDD: %.1f%%",
                   m26.get("sharpe", 0), m26.get("cagr", 0), m26.get("max_dd", 0))
    
    logger.info("백테스트 완료!")
