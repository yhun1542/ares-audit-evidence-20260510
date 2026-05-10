"""
Polygon S3에서 SPY (S&P 500 ETF) 데이터를 로드하는 모듈

사용법:
    from polygon_sp500_loader import load_spy_from_polygon_s3, load_spy_from_db
    
    # Polygon S3에서 직접 로드
    spy_df = load_spy_from_polygon_s3(start_date='2016-01-01', end_date='2024-12-31')
    
    # DB에서 로드 (캐싱된 데이터)
    spy_df = load_spy_from_db(db_path='/path/to/db.db')
"""

import os
import sqlite3
from datetime import datetime
from typing import Optional, Tuple

import numpy as np
import pandas as pd

# Polygon S3 설정
POLYGON_S3_CONFIG = {
    'access_key': 'f0bc904a-9d5c-476b-af56-2cb4a2455a3e',
    'secret_key': os.environ["POLYGON_API_KEY"],
    'endpoint': 'https://files.polygon.io',
    'bucket': 'flatfiles',
}


def load_spy_from_polygon_s3(
    start_date: str = '2016-01-01',
    end_date: str = '2024-12-31',
    cache_to_db: Optional[str] = None,
) -> pd.DataFrame:
    """
    Polygon S3에서 SPY 일봉 데이터를 로드합니다.
    
    Args:
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)
        cache_to_db: 캐싱할 DB 경로 (선택)
        
    Returns:
        pd.DataFrame: date, open, high, low, close, volume, vwap 컬럼
    """
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise ImportError("boto3가 필요합니다: pip install boto3")
    
    # S3 클라이언트 설정
    s3_client = boto3.client(
        's3',
        aws_access_key_id=POLYGON_S3_CONFIG['access_key'],
        aws_secret_access_key=POLYGON_S3_CONFIG['secret_key'],
        endpoint_url=POLYGON_S3_CONFIG['endpoint'],
        config=Config(signature_version='s3v4'),
    )
    
    # Polygon flatfiles 구조: us_stocks_sip/day_aggs_v1/YYYY/MM/YYYY-MM-DD.csv.gz
    # 또는 개별 종목: us_stocks_sip/day_aggs_v1/SPY.csv.gz
    
    all_data = []
    
    # 연도별로 데이터 로드 시도
    start_year = int(start_date[:4])
    end_year = int(end_date[:4])
    
    for year in range(start_year, end_year + 1):
        try:
            # 방법 1: 연도별 파일
            key = f"us_stocks_sip/day_aggs_v1/{year}/SPY.csv.gz"
            response = s3_client.get_object(Bucket=POLYGON_S3_CONFIG['bucket'], Key=key)
            df = pd.read_csv(response['Body'], compression='gzip')
            all_data.append(df)
            print(f"[INFO] Loaded SPY data for {year}")
        except Exception as e:
            print(f"[WARNING] Failed to load {year}: {e}")
            continue
    
    if not all_data:
        # 방법 2: 전체 파일
        try:
            key = "us_stocks_sip/day_aggs_v1/SPY.csv.gz"
            response = s3_client.get_object(Bucket=POLYGON_S3_CONFIG['bucket'], Key=key)
            df = pd.read_csv(response['Body'], compression='gzip')
            all_data.append(df)
            print(f"[INFO] Loaded SPY data from single file")
        except Exception as e:
            print(f"[ERROR] Failed to load SPY data: {e}")
            raise
    
    # 데이터 병합
    spy_df = pd.concat(all_data, ignore_index=True)
    
    # 컬럼 정규화
    spy_df.columns = [c.lower() for c in spy_df.columns]
    
    # 날짜 필터링
    if 'date' in spy_df.columns:
        spy_df['date'] = pd.to_datetime(spy_df['date'])
    elif 'timestamp' in spy_df.columns:
        spy_df['date'] = pd.to_datetime(spy_df['timestamp'], unit='ms')
    
    spy_df = spy_df[(spy_df['date'] >= start_date) & (spy_df['date'] <= end_date)]
    spy_df = spy_df.sort_values('date').reset_index(drop=True)
    
    # 필요한 컬럼만 선택
    cols = ['date', 'open', 'high', 'low', 'close', 'volume']
    if 'vwap' in spy_df.columns:
        cols.append('vwap')
    spy_df = spy_df[cols]
    
    # DB에 캐싱
    if cache_to_db:
        _cache_spy_to_db(spy_df, cache_to_db)
    
    return spy_df


def _cache_spy_to_db(spy_df: pd.DataFrame, db_path: str) -> None:
    """SPY 데이터를 DB에 캐싱합니다."""
    conn = sqlite3.connect(db_path)
    
    # 테이블 생성
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spy_daily (
            date TEXT PRIMARY KEY,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            vwap REAL
        )
    """)
    
    # 데이터 삽입
    for _, row in spy_df.iterrows():
        conn.execute("""
            INSERT OR REPLACE INTO spy_daily (date, open, high, low, close, volume, vwap)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10],
            row['open'],
            row['high'],
            row['low'],
            row['close'],
            row['volume'],
            row.get('vwap', None),
        ))
    
    conn.commit()
    conn.close()
    print(f"[INFO] Cached {len(spy_df)} SPY records to {db_path}")


def load_spy_from_db(db_path: str) -> Optional[pd.DataFrame]:
    """
    DB에서 캐싱된 SPY 데이터를 로드합니다.
    
    Args:
        db_path: DB 파일 경로
        
    Returns:
        pd.DataFrame 또는 None (데이터 없음)
    """
    try:
        conn = sqlite3.connect(db_path)
        
        # spy_daily 테이블 존재 여부 확인
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='spy_daily'"
        )
        if not cursor.fetchone():
            conn.close()
            return None
        
        # 데이터 로드
        spy_df = pd.read_sql_query(
            "SELECT * FROM spy_daily ORDER BY date",
            conn
        )
        conn.close()
        
        if spy_df.empty:
            return None
        
        spy_df['date'] = pd.to_datetime(spy_df['date'])
        return spy_df
        
    except Exception as e:
        print(f"[WARNING] Failed to load SPY from DB: {e}")
        return None


def compute_spy_returns(spy_df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """
    SPY 가격 데이터에서 일간 수익률을 계산합니다.
    
    Args:
        spy_df: SPY 가격 데이터 (date, close 컬럼 필요)
        
    Returns:
        (dates, returns): 날짜 배열과 일간 수익률 배열
    """
    spy_df = spy_df.sort_values('date').reset_index(drop=True)
    
    dates = spy_df['date'].values
    prices = spy_df['close'].values
    
    # 일간 수익률 계산
    returns = np.zeros(len(prices))
    returns[1:] = np.diff(prices) / prices[:-1]
    
    return dates, returns


def get_spy_data_for_engine(
    db_path: str,
    engine_dates: pd.DatetimeIndex,
    fallback_to_polygon: bool = True,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    엔진에서 사용할 SPY 데이터를 로드합니다.
    
    1. 먼저 DB에서 캐싱된 데이터 로드 시도
    2. 없으면 Polygon S3에서 로드 후 캐싱
    3. 엔진의 날짜 인덱스에 맞춰 정렬
    
    Args:
        db_path: DB 파일 경로
        engine_dates: 엔진의 거래일 인덱스
        fallback_to_polygon: DB에 없을 때 Polygon에서 로드할지 여부
        
    Returns:
        (spy_prices, spy_returns): 엔진 날짜에 맞춘 가격/수익률 배열
    """
    # 1. DB에서 로드 시도
    spy_df = load_spy_from_db(db_path)
    
    # 2. DB에 없으면 Polygon에서 로드
    if spy_df is None and fallback_to_polygon:
        print("[INFO] SPY data not in DB, loading from Polygon S3...")
        start_date = engine_dates.min().strftime('%Y-%m-%d')
        end_date = engine_dates.max().strftime('%Y-%m-%d')
        spy_df = load_spy_from_polygon_s3(
            start_date=start_date,
            end_date=end_date,
            cache_to_db=db_path,
        )
    
    if spy_df is None:
        raise ValueError("SPY 데이터를 로드할 수 없습니다")
    
    # 3. 엔진 날짜에 맞춰 정렬
    spy_df = spy_df.set_index('date')
    spy_df = spy_df.reindex(engine_dates)
    
    # 결측치 처리 (forward fill)
    spy_df = spy_df.ffill().bfill()
    
    spy_prices = spy_df['close'].values
    spy_returns = np.zeros(len(spy_prices))
    spy_returns[1:] = np.diff(spy_prices) / spy_prices[:-1]
    
    return spy_prices, spy_returns


if __name__ == '__main__':
    # 테스트
    print("Testing Polygon S3 SPY loader...")
    
    # DB에서 로드 테스트
    db_path = '/home/ubuntu/etf_data_s3.db'
    spy_df = load_spy_from_db(db_path)
    
    if spy_df is not None:
        print(f"Loaded {len(spy_df)} SPY records from DB")
        print(spy_df.head())
    else:
        print("No SPY data in DB, will load from Polygon S3...")
        spy_df = load_spy_from_polygon_s3(
            start_date='2016-01-01',
            end_date='2024-12-31',
            cache_to_db=db_path,
        )
        print(f"Loaded {len(spy_df)} SPY records from Polygon S3")
        print(spy_df.head())
