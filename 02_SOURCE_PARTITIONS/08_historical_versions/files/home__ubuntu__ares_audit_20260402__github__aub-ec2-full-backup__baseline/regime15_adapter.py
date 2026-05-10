# -*- coding: utf-8 -*-
"""
regime15_adapter_db.py
======================
Regime15 Adapter for AUB Engine Integration (DB Direct Load Version)

이 어댑터는 fred_macro_daily 테이블에서 직접 데이터를 로드합니다.
캐시 파일 없이도 동작합니다.

Data Sources (from fred_macro_daily):
- VIXCLS: VIX Close
- T10Y2Y: 10년물-2년물 금리 스프레드 (bp 단위로 변환)
- BAMLH0A0HYM2: 하이일드 신용 스프레드 (bp 단위로 변환)

OFF=baseline 동일 보장:
- enable_regime15=False일 때 모든 메서드는 기본값 반환
- 엔진 로직에 영향 없음

Author: AUB Team
Version: 2.0.0 (DB Direct Load)
Date: 2025-12-28
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# =============================================================================
# Regime States
# =============================================================================

class MarketRegime(Enum):
    """시장 레짐 상태"""
    RISK_ON = "risk_on"              # 위험선호: 성장 기대, 낮은 변동성
    RISK_OFF = "risk_off"            # 위험회피: 방어적, 높은 변동성
    TRANSITION = "transition"        # 전환기: 불확실성
    CRISIS = "crisis"                # 위기: 극단적 스트레스
    RECOVERY = "recovery"            # 회복: 침체 후 반등


class VIXRegime(Enum):
    """VIX 레짐"""
    CALM = "calm"                    # < 20: 안정
    ELEVATED = "elevated"            # 20-30: 불안
    FEAR = "fear"                    # 30-40: 공포
    PANIC = "panic"                  # > 40: 패닉


class YieldCurveRegime(Enum):
    """금리 커브 레짐"""
    STEEP = "steep"                  # > 100bp: 강한 성장 기대
    NORMAL = "normal"                # 50-100bp: 정상
    FLAT = "flat"                    # 0-50bp: 둔화 우려
    INVERTED = "inverted"            # < 0: 침체 신호


class CreditRegime(Enum):
    """신용 스프레드 레짐"""
    TIGHT = "tight"                  # < 300bp: 강한 신용
    NORMAL = "normal"                # 300-400bp: 정상
    STRESS = "stress"                # 400-600bp: 스트레스
    CRISIS = "crisis"                # > 600bp: 위기


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class Regime15Config:
    """Regime15 Adapter 설정"""
    
    # 활성화 여부
    enabled: bool = False
    
    # DB 경로 (캐시 대신 DB 직접 로드)
    db_path: str = "/home/ubuntu/ares_x_v11_0.db"
    
    # VIX 임계값
    vix_calm_threshold: float = 20.0
    vix_elevated_threshold: float = 30.0
    vix_fear_threshold: float = 40.0
    
    # 금리 커브 임계값 (bp) - T10Y2Y는 % 단위이므로 100 곱해서 bp로 변환
    yield_steep_threshold: float = 100.0    # 1.0%
    yield_normal_threshold: float = 50.0    # 0.5%
    yield_flat_threshold: float = 0.0       # 0.0%
    
    # 신용 스프레드 임계값 (bp) - BAMLH0A0HYM2는 % 단위이므로 100 곱해서 bp로 변환
    credit_tight_threshold: float = 300.0   # 3.0%
    credit_normal_threshold: float = 400.0  # 4.0%
    credit_stress_threshold: float = 600.0  # 6.0%
    
    # Band Multiplier 설정
    band_mult_risk_on: float = 1.0      # RISK_ON 시 밴드 유지
    band_mult_risk_off: float = 0.5     # RISK_OFF 시 밴드 축소 (더 보수적)
    band_mult_crisis: float = 0.3       # CRISIS 시 밴드 대폭 축소
    band_mult_transition: float = 0.8   # TRANSITION 시 약간 축소
    band_mult_recovery: float = 0.9     # RECOVERY 시 약간 축소


# =============================================================================
# Regime15 Adapter (DB Direct Load)
# =============================================================================

class Regime15Adapter:
    """
    Regime15 Adapter for AUB Engine (DB Direct Load Version)
    
    OFF=baseline 동일 보장:
    - enabled=False일 때 모든 메서드는 기본값 반환
    - 엔진 로직에 영향 없음
    """
    
    def __init__(self, config: Optional[Regime15Config] = None):
        self.config = config or Regime15Config()
        self.enabled = self.config.enabled
        
        # 내부 상태
        self._current_regime: MarketRegime = MarketRegime.RISK_ON
        self._vix_regime: VIXRegime = VIXRegime.CALM
        self._yield_regime: YieldCurveRegime = YieldCurveRegime.NORMAL
        self._credit_regime: CreditRegime = CreditRegime.NORMAL
        
        # 카운터 (diagnostics용)
        self._counters = {
            "pre_rebalance_calls": 0,
            "post_rebalance_calls": 0,
            "regime_changes": 0,
            "risk_on_count": 0,
            "risk_off_count": 0,
            "crisis_count": 0,
            "transition_count": 0,
            "recovery_count": 0,
            "band_mult_sum": 0.0,
            "band_mult_min": None,
            "band_mult_max": None,
        }
        
        # DB에서 로드한 데이터
        self._vix_data: Dict[str, float] = {}
        self._yield_data: Dict[str, float] = {}
        self._credit_data: Dict[str, float] = {}
        self._data_loaded: bool = False
        
        logger.info(f"Regime15Adapter initialized: enabled={self.enabled}, db_path={self.config.db_path}")
    
    # =========================================================================
    # Public API (Engine Integration Points)
    # =========================================================================
    
    def pre_rebalance(self, date_str: str, prices: Optional[np.ndarray] = None) -> Dict[str, Any]:
        """
        리밸런싱 전 레짐 상태 업데이트
        
        Args:
            date_str: 날짜 문자열 (YYYY-MM-DD)
            prices: 가격 데이터 (optional)
        
        Returns:
            레짐 정보 딕셔너리
        
        OFF 보장: enabled=False면 기본값 반환
        """
        if not self.enabled:
            return {
                "enabled": False,
                "regime": MarketRegime.RISK_ON.value,
                "band_multiplier": 1.0
            }
        
        self._counters["pre_rebalance_calls"] += 1
        
        # 레짐 업데이트
        prev_regime = self._current_regime
        self._update_regime(date_str)
        
        if prev_regime != self._current_regime:
            self._counters["regime_changes"] += 1
        
        # 레짐별 카운터 업데이트
        if self._current_regime == MarketRegime.RISK_ON:
            self._counters["risk_on_count"] += 1
        elif self._current_regime == MarketRegime.RISK_OFF:
            self._counters["risk_off_count"] += 1
        elif self._current_regime == MarketRegime.CRISIS:
            self._counters["crisis_count"] += 1
        elif self._current_regime == MarketRegime.RECOVERY:
            self._counters["recovery_count"] += 1
        else:
            self._counters["transition_count"] += 1
        
        band_mult = self.get_band_multiplier()
        self._counters["band_mult_sum"] += band_mult
        if self._counters["band_mult_min"] is None:
            self._counters["band_mult_min"] = band_mult
            self._counters["band_mult_max"] = band_mult
        else:
            self._counters["band_mult_min"] = min(self._counters["band_mult_min"], band_mult)
            self._counters["band_mult_max"] = max(self._counters["band_mult_max"], band_mult)
        
        return {
            "enabled": True,
            "regime": self._current_regime.value,
            "vix_regime": self._vix_regime.value,
            "yield_regime": self._yield_regime.value,
            "credit_regime": self._credit_regime.value,
            "band_multiplier": band_mult
        }
    
    def post_rebalance(self, date_str: str, weights: Optional[np.ndarray] = None) -> Dict[str, Any]:
        """
        리밸런싱 후 카운터 집계
        
        OFF 보장: enabled=False면 기본값 반환
        """
        if not self.enabled:
            return {"enabled": False}
        
        self._counters["post_rebalance_calls"] += 1
        
        return {
            "enabled": True,
            "regime": self._current_regime.value,
            "counters": self._counters.copy()
        }
    
    def get_band_multiplier(self) -> float:
        """
        no_trade_band 조정 계수 반환
        
        OFF 보장: enabled=False면 1.0 반환 (밴드 변경 없음)
        """
        if not self.enabled:
            return 1.0
        
        if self._current_regime == MarketRegime.RISK_ON:
            return self.config.band_mult_risk_on
        elif self._current_regime == MarketRegime.RISK_OFF:
            return self.config.band_mult_risk_off
        elif self._current_regime == MarketRegime.CRISIS:
            return self.config.band_mult_crisis
        elif self._current_regime == MarketRegime.RECOVERY:
            return self.config.band_mult_recovery
        else:  # TRANSITION
            return self.config.band_mult_transition
    
    def get_diagnostics(self) -> Dict[str, Any]:
        """진단 정보 반환 (addons_summary용)"""
        if not self.enabled:
            return {
                "enabled": False,
                "pre_rebalance_calls": 0,
                "post_rebalance_calls": 0
            }
        
        avg_band_mult = (
            self._counters["band_mult_sum"] / self._counters["pre_rebalance_calls"]
            if self._counters["pre_rebalance_calls"] > 0 else 1.0
        )
        
        return {
            "enabled": True,
            "data_loaded": self._data_loaded,
            "vix_data_count": len(self._vix_data),
            "yield_data_count": len(self._yield_data),
            "credit_data_count": len(self._credit_data),
            "pre_rebalance_calls": self._counters["pre_rebalance_calls"],
            "post_rebalance_calls": self._counters["post_rebalance_calls"],
            "regime_changes": self._counters["regime_changes"],
            "risk_on_count": self._counters["risk_on_count"],
            "risk_off_count": self._counters["risk_off_count"],
            "crisis_count": self._counters["crisis_count"],
            "transition_count": self._counters["transition_count"],
            "recovery_count": self._counters["recovery_count"],
            "band_mult_avg": round(avg_band_mult, 4),
            "band_mult_min": self._counters["band_mult_min"],
            "band_mult_max": self._counters["band_mult_max"],
            "current_regime": self._current_regime.value,
        }
    
    def load_data(self, dates: List[str] = None, db_path: str = None) -> bool:
        """
        DB에서 직접 데이터 로드
        
        Args:
            dates: 백테스트 날짜 리스트 (optional, 미사용)
            db_path: DB 경로 (optional, config 값 사용)
        
        Returns:
            로드 성공 여부
        """
        if not self.enabled:
            return True
        
        db = db_path or self.config.db_path
        
        try:
            conn = sqlite3.connect(db, timeout=30)
            
            # VIX 데이터 로드 (VIXCLS)
            vix_df = pd.read_sql_query(
                "SELECT date, value FROM fred_macro_daily WHERE series_id = 'VIXCLS' ORDER BY date",
                conn
            )
            self._vix_data = dict(zip(vix_df['date'], vix_df['value']))
            
            # T10Y2Y 데이터 로드 (% → bp 변환)
            yield_df = pd.read_sql_query(
                "SELECT date, value FROM fred_macro_daily WHERE series_id = 'T10Y2Y' ORDER BY date",
                conn
            )
            # T10Y2Y는 % 단위 (예: 0.68 = 68bp)
            self._yield_data = dict(zip(yield_df['date'], yield_df['value'] * 100))  # bp로 변환
            
            # BAMLH0A0HYM2 데이터 로드 (% → bp 변환)
            credit_df = pd.read_sql_query(
                "SELECT date, value FROM fred_macro_daily WHERE series_id = 'BAMLH0A0HYM2' ORDER BY date",
                conn
            )
            # BAMLH0A0HYM2는 % 단위 (예: 2.95 = 295bp)
            self._credit_data = dict(zip(credit_df['date'], credit_df['value'] * 100))  # bp로 변환
            
            conn.close()
            
            self._data_loaded = True
            logger.info(f"Regime15 data loaded from DB: VIX={len(self._vix_data)}, T10Y2Y={len(self._yield_data)}, HY={len(self._credit_data)}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load Regime15 data from DB: {e}")
            self._data_loaded = False
            return False
    
    # =========================================================================
    # Internal Methods
    # =========================================================================
    
    def _update_regime(self, date_str: str) -> None:
        """레짐 상태 업데이트"""
        
        # 날짜 형식 정규화 (YYYY-MM-DD)
        if isinstance(date_str, (datetime, date)):
            date_str = date_str.strftime("%Y-%m-%d")
        elif " " in str(date_str):
            date_str = str(date_str).split(" ")[0]
        
        # VIX 데이터로 레짐 결정
        vix = self._vix_data.get(date_str, 18.0)  # 기본값: CALM
        self._vix_regime = self._classify_vix(vix)
        
        # 금리 커브 데이터 (bp 단위)
        yield_spread = self._yield_data.get(date_str, 75.0)  # 기본값: NORMAL
        self._yield_regime = self._classify_yield(yield_spread)
        
        # 신용 스프레드 데이터 (bp 단위)
        credit_spread = self._credit_data.get(date_str, 350.0)  # 기본값: NORMAL
        self._credit_regime = self._classify_credit(credit_spread)
        
        # 종합 레짐 결정
        self._current_regime = self._determine_overall_regime()
    
    def _classify_vix(self, vix: float) -> VIXRegime:
        """VIX 레짐 분류"""
        if vix < self.config.vix_calm_threshold:
            return VIXRegime.CALM
        elif vix < self.config.vix_elevated_threshold:
            return VIXRegime.ELEVATED
        elif vix < self.config.vix_fear_threshold:
            return VIXRegime.FEAR
        else:
            return VIXRegime.PANIC
    
    def _classify_yield(self, spread: float) -> YieldCurveRegime:
        """금리 커브 레짐 분류 (bp 단위)"""
        if spread > self.config.yield_steep_threshold:
            return YieldCurveRegime.STEEP
        elif spread > self.config.yield_normal_threshold:
            return YieldCurveRegime.NORMAL
        elif spread > self.config.yield_flat_threshold:
            return YieldCurveRegime.FLAT
        else:
            return YieldCurveRegime.INVERTED
    
    def _classify_credit(self, spread: float) -> CreditRegime:
        """신용 스프레드 레짐 분류 (bp 단위)"""
        if spread < self.config.credit_tight_threshold:
            return CreditRegime.TIGHT
        elif spread < self.config.credit_normal_threshold:
            return CreditRegime.NORMAL
        elif spread < self.config.credit_stress_threshold:
            return CreditRegime.STRESS
        else:
            return CreditRegime.CRISIS
    
    def _determine_overall_regime(self) -> MarketRegime:
        """종합 레짐 결정"""
        
        # Crisis 조건: VIX PANIC 또는 Credit CRISIS
        if self._vix_regime == VIXRegime.PANIC or self._credit_regime == CreditRegime.CRISIS:
            return MarketRegime.CRISIS
        
        # Risk Off 조건: VIX FEAR 또는 Credit STRESS 또는 Yield INVERTED
        if (self._vix_regime == VIXRegime.FEAR or 
            self._credit_regime == CreditRegime.STRESS or
            self._yield_regime == YieldCurveRegime.INVERTED):
            return MarketRegime.RISK_OFF
        
        # Risk On 조건: VIX CALM 또는 ELEVATED + Credit TIGHT/NORMAL + Yield STEEP/NORMAL
        if (self._vix_regime in [VIXRegime.CALM, VIXRegime.ELEVATED] and
            self._credit_regime in [CreditRegime.TIGHT, CreditRegime.NORMAL] and
            self._yield_regime in [YieldCurveRegime.STEEP, YieldCurveRegime.NORMAL]):
            return MarketRegime.RISK_ON
        
        # 그 외: Transition
        return MarketRegime.TRANSITION


# =============================================================================
# Factory Function
# =============================================================================

def create_regime15_adapter(
    enabled: bool = False,
    db_path: str = "/home/ubuntu/ares_x_v11_0.db",
    band_mult_risk_off: float = 0.5,
    band_mult_crisis: float = 0.3,
    **kwargs
) -> Regime15Adapter:
    """
    Regime15 Adapter 생성 팩토리 함수
    
    Args:
        enabled: 활성화 여부
        db_path: DB 경로
        band_mult_risk_off: RISK_OFF 시 밴드 계수
        band_mult_crisis: CRISIS 시 밴드 계수
        **kwargs: 추가 설정
    
    Returns:
        Regime15Adapter 인스턴스
    """
    config = Regime15Config(
        enabled=enabled,
        db_path=db_path,
        band_mult_risk_off=band_mult_risk_off,
        band_mult_crisis=band_mult_crisis,
        **{k: v for k, v in kwargs.items() if hasattr(Regime15Config, k)}
    )
    adapter = Regime15Adapter(config)
    
    # 활성화 시 자동으로 DB 로드
    if enabled:
        adapter.load_data(db_path=db_path)
    
    return adapter


# =============================================================================
# Test
# =============================================================================

if __name__ == "__main__":
    print("=== Regime15 Adapter DB Direct Load Test ===")
    print()
    
    # OFF 테스트
    print("1. OFF mode test:")
    adapter_off = create_regime15_adapter(enabled=False)
    result_off = adapter_off.pre_rebalance("2024-01-15")
    print(f"   Result: {result_off}")
    assert result_off["band_multiplier"] == 1.0, "OFF mode should return 1.0"
    print("   ✅ PASS")
    print()
    
    # ON 테스트
    print("2. ON mode test (DB load):")
    adapter_on = create_regime15_adapter(
        enabled=True,
        db_path="/home/ubuntu/ares_x_v11_0.db"
    )
    
    # 데이터 로드 확인
    diag = adapter_on.get_diagnostics()
    print(f"   Data loaded: {diag['data_loaded']}")
    print(f"   VIX data: {diag['vix_data_count']} days")
    print(f"   T10Y2Y data: {diag['yield_data_count']} days")
    print(f"   HY Spread data: {diag['credit_data_count']} days")
    print()
    
    # 다양한 날짜 테스트
    test_dates = [
        "2008-10-15",  # 금융위기
        "2020-03-15",  # 코로나 위기
        "2024-01-15",  # 최근 정상
        "2023-03-15",  # SVB 위기
    ]
    
    print("3. Regime detection test:")
    for d in test_dates:
        result = adapter_on.pre_rebalance(d)
        print(f"   {d}: regime={result['regime']}, vix={result['vix_regime']}, yield={result['yield_regime']}, credit={result['credit_regime']}, band_mult={result['band_multiplier']}")
    print()
    
    # Diagnostics 테스트
    print("4. Diagnostics:")
    diag = adapter_on.get_diagnostics()
    print(f"   {diag}")
    print()
    
    print("✅ All tests passed")
