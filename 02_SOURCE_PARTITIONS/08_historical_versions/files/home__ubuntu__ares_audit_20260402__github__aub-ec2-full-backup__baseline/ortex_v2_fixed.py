#!/usr/bin/env python3
"""
ORTEX API Connector v2.0 - 수정된 엔드포인트 형식
가이드 v2.0 기반 올바른 엔드포인트 적용

핵심 변경사항:
- Base URL: https://api.ortex.com/api/v1
- 엔드포인트 형식: /stock/{exchange}/{ticker}/{endpoint}
- exchange: nasdaq, nyse 등
- 언더스코어 사용: short_interest (하이픈 아님)
"""

import asyncio
import aiohttp
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class OrtexClientV2:
    """ORTEX API 클라이언트 v2.0 - 올바른 엔드포인트 형식"""
    
    BASE_URL = "https://api.ortex.com/api/v1"
    
    # 주요 종목의 거래소 매핑
    EXCHANGE_MAP = {
        # NASDAQ 종목
        'AAPL': 'nasdaq', 'MSFT': 'nasdaq', 'GOOGL': 'nasdaq', 'GOOG': 'nasdaq',
        'AMZN': 'nasdaq', 'META': 'nasdaq', 'NVDA': 'nasdaq', 'TSLA': 'nasdaq',
        'AMD': 'nasdaq', 'INTC': 'nasdaq', 'NFLX': 'nasdaq', 'PYPL': 'nasdaq',
        'ADBE': 'nasdaq', 'CSCO': 'nasdaq', 'CMCSA': 'nasdaq', 'PEP': 'nasdaq',
        'COST': 'nasdaq', 'AVGO': 'nasdaq', 'TXN': 'nasdaq', 'QCOM': 'nasdaq',
        # NYSE 종목
        'JPM': 'nyse', 'V': 'nyse', 'JNJ': 'nyse', 'WMT': 'nyse',
        'PG': 'nyse', 'MA': 'nyse', 'UNH': 'nyse', 'HD': 'nyse',
        'DIS': 'nyse', 'BAC': 'nyse', 'XOM': 'nyse', 'CVX': 'nyse',
        'KO': 'nyse', 'MRK': 'nyse', 'PFE': 'nyse', 'ABT': 'nyse',
        'CRM': 'nyse', 'ORCL': 'nyse', 'ACN': 'nyse', 'NKE': 'nyse',
        # ETF
        'SPY': 'nyse', 'QQQ': 'nasdaq', 'IWM': 'nyse', 'DIA': 'nyse',
    }
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Ortex-Api-Key": api_key,
            "accept": "application/json"
        }
        self._session: Optional[aiohttp.ClientSession] = None
    
    def _get_exchange(self, ticker: str) -> str:
        """종목의 거래소 코드 반환"""
        return self.EXCHANGE_MAP.get(ticker.upper(), 'nasdaq')
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """API 요청 실행"""
        session = await self._get_session()
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', '')
                    if 'json' in content_type:
                        return await resp.json()
                    else:
                        logger.warning(f"Non-JSON response from {endpoint}: {content_type}")
                        return None
                else:
                    logger.error(f"ORTEX API error: {resp.status} for {endpoint}")
                    return None
        except Exception as e:
            logger.error(f"ORTEX request failed: {e}")
            return None
    
    # ==================== Short Interest APIs ====================
    
    async def get_short_interest(self, ticker: str, from_date: Optional[str] = None) -> Optional[List[Dict]]:
        """공매도 데이터 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/short_interest"
        params = {}
        if from_date:
            params['from_date'] = from_date
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    async def get_days_to_cover(self, ticker: str, from_date: Optional[str] = None) -> Optional[List[Dict]]:
        """공매도 상환 기간 (DTC) 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/dtc"
        params = {}
        if from_date:
            params['from_date'] = from_date
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    async def get_ctb_all(self, ticker: str, from_date: Optional[str] = None) -> Optional[List[Dict]]:
        """대차 수수료 (전체) 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/ctb/all"
        params = {}
        if from_date:
            params['from_date'] = from_date
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    async def get_ctb_new(self, ticker: str, from_date: Optional[str] = None) -> Optional[List[Dict]]:
        """대차 수수료 (신규) 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/ctb/new"
        params = {}
        if from_date:
            params['from_date'] = from_date
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    async def get_availability(self, ticker: str, from_date: Optional[str] = None) -> Optional[List[Dict]]:
        """대차 가능 주식 수량 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/availability"
        params = {}
        if from_date:
            params['from_date'] = from_date
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    # ==================== Stock Scores ====================
    
    async def get_stock_scores(self, ticker: str) -> Optional[List[Dict]]:
        """주식 점수 (성장, 모멘텀, 퀄리티, 가치) 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/stock_scores"
        
        data = await self._request(endpoint)
        return data.get('rows', []) if data else None
    
    # ==================== Options ====================
    
    async def get_option_expiries(self, ticker: str) -> Optional[List[Dict]]:
        """옵션 만기일 목록 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/options/expiries"
        
        data = await self._request(endpoint)
        return data.get('rows', []) if data else None
    
    async def get_option_chain(self, ticker: str, expiry: Optional[str] = None) -> Optional[List[Dict]]:
        """옵션 체인 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/options/chain"
        params = {}
        if expiry:
            params['expiry'] = expiry
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    # ==================== Fundamentals ====================
    
    async def get_fundamentals_balance(self, ticker: str, period: Optional[str] = None) -> Optional[List[Dict]]:
        """재무상태표 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/fundamentals/balance"
        params = {}
        if period:
            params['period'] = period
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    async def get_fundamentals_income(self, ticker: str, period: Optional[str] = None) -> Optional[List[Dict]]:
        """손익계산서 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/fundamentals/income"
        params = {}
        if period:
            params['period'] = period
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    async def get_fundamentals_ratios(self, ticker: str, period: Optional[str] = None) -> Optional[List[Dict]]:
        """재무 비율 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/fundamentals/ratios"
        params = {}
        if period:
            params['period'] = period
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    # ==================== Shares ====================
    
    async def get_free_float(self, ticker: str, from_date: Optional[str] = None) -> Optional[List[Dict]]:
        """유통 주식 수 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/free_float"
        params = {}
        if from_date:
            params['from_date'] = from_date
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    async def get_shares_outstanding(self, ticker: str, from_date: Optional[str] = None) -> Optional[List[Dict]]:
        """발행 주식 수 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/shares_outstanding"
        params = {}
        if from_date:
            params['from_date'] = from_date
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    # ==================== Events ====================
    
    async def get_earnings(self, from_date: str, to_date: str) -> Optional[List[Dict]]:
        """어닝스 이벤트 조회 (1개월 이내 범위 필수)"""
        endpoint = "/earnings"
        params = {'from_date': from_date, 'to_date': to_date}
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    async def get_stock_splits(self, ticker: str) -> Optional[List[Dict]]:
        """주식 분할 내역 조회"""
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/stock_splits"
        
        data = await self._request(endpoint)
        return data.get('rows', []) if data else None
    
    async def get_macro_events(self, country: str = "US") -> Optional[List[Dict]]:
        """거시 경제 이벤트 캘린더 조회"""
        endpoint = "/macro_events"
        params = {'country': country}
        
        data = await self._request(endpoint, params)
        return data.get('rows', []) if data else None
    
    # ==================== Price ====================
    
    async def get_closing_prices(self, ticker: str, from_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """일별 종가 조회 (OHLCV 데이터)
        
        Note: closing_prices API는 다른 API들과 달리 'data' 키에 데이터가 있음
        반환값에는 company, currency, data(OHLCV 리스트) 포함
        """
        exchange = self._get_exchange(ticker)
        endpoint = f"/stock/{exchange}/{ticker}/closing_prices"
        params = {}
        if from_date:
            params['from_date'] = from_date
        
        data = await self._request(endpoint, params)
        if data:
            return {
                'company': data.get('company'),
                'currency': data.get('currency'),
                'prices': data.get('data', []),  # OHLCV 데이터는 'data' 키에 있음
                'credits_used': data.get('creditsUsed')
            }
        return None
    
    async def get_closing_prices_list(self, ticker: str, from_date: Optional[str] = None) -> Optional[List[Dict]]:
        """일별 종가 조회 - OHLCV 리스트만 반환"""
        result = await self.get_closing_prices(ticker, from_date)
        return result.get('prices', []) if result else None
    
    # ==================== Comprehensive Data ====================
    
    async def get_comprehensive_short_data(self, ticker: str, from_date: Optional[str] = None) -> Dict[str, Any]:
        """종합 공매도 데이터 조회"""
        if from_date is None:
            from_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        
        # 병렬로 모든 공매도 관련 데이터 조회
        results = await asyncio.gather(
            self.get_short_interest(ticker, from_date),
            self.get_days_to_cover(ticker, from_date),
            self.get_ctb_all(ticker, from_date),
            self.get_availability(ticker, from_date),
            self.get_stock_scores(ticker),
            return_exceptions=True
        )
        
        return {
            'short_interest': results[0] if not isinstance(results[0], Exception) else None,
            'days_to_cover': results[1] if not isinstance(results[1], Exception) else None,
            'ctb_all': results[2] if not isinstance(results[2], Exception) else None,
            'availability': results[3] if not isinstance(results[3], Exception) else None,
            'stock_scores': results[4] if not isinstance(results[4], Exception) else None,
        }


# 테스트 코드
if __name__ == "__main__":
    import os
    
    async def test():
        api_key = os.environ.get('ORTEX_API_KEY', 'BKcZex6n.ekh0D3F2LICK8TOtgii4OjyRKy6DCn8b')
        client = OrtexClientV2(api_key)
        
        try:
            print("=== ORTEX API v2.0 테스트 ===")
            
            # Short Interest
            si = await client.get_short_interest('AAPL', '2024-12-01')
            print(f"Short Interest: {len(si) if si else 0} rows")
            
            # Days to Cover
            dtc = await client.get_days_to_cover('AAPL', '2024-12-01')
            print(f"Days to Cover: {len(dtc) if dtc else 0} rows")
            
            # CTB
            ctb = await client.get_ctb_all('AAPL', '2024-12-01')
            print(f"CTB All: {len(ctb) if ctb else 0} rows")
            
            # Stock Scores
            scores = await client.get_stock_scores('AAPL')
            print(f"Stock Scores: {len(scores) if scores else 0} rows")
            
            # Option Expiries
            expiries = await client.get_option_expiries('AAPL')
            print(f"Option Expiries: {len(expiries) if expiries else 0} rows")
            
            # Comprehensive
            comprehensive = await client.get_comprehensive_short_data('AAPL')
            print(f"\nComprehensive Data:")
            for key, value in comprehensive.items():
                print(f"  {key}: {len(value) if value else 0} rows")
            
        finally:
            await client.close()
    
    asyncio.run(test())
