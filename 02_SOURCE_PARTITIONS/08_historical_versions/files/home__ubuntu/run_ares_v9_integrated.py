#!/usr/bin/env python3
"""
ARES Ultimate v9.0 - FULL ENGINE INTEGRATION
============================================
4대 AI (Claude, GPT, Gemini, Grok) 제안 통합

핵심 통합:
1. NRC v5.1 → 레짐 감지 + 불확실성
2. ICIR Weighter → 동적 팩터 가중치
3. LightGBM Router → 알파 예측
4. HRP Optimizer → 포지션 사이징

목표:
- Sharpe 2.2+ (Invested Sharpe 3.1+)
- 연간 수익률 28%
- MDD -8%
- LOW 레짐 Sharpe 1.1+
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# 1. CONFIGURATION
# =============================================================================

@dataclass
class AresV9Config:
    """v9.0 통합 설정"""
    
    # Database
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    # NRC Settings (레짐 감지)
    nrc_enabled: bool = True
    nrc_vix_weight: float = 0.2  # VIX 앙상블 가중치
    nrc_confidence_threshold: float = 0.6
    
    # ICIR Settings (팩터 가중치)
    icir_enabled: bool = True
    icir_warmup_days: int = 756  # 36개월
    icir_decay_halflife: int = 252  # 12개월
    icir_shrinkage: float = 0.3
    icir_min_threshold: float = 0.02
    
    # Factor Settings
    momentum_base_weight: float = 0.70
    quality_base_weight: float = 0.30
    
    # LightGBM Router
    router_enabled: bool = False  # 학습 필요, 일단 비활성화
    
    # HRP Optimizer
    hrp_enabled: bool = True
    max_leverage_ultra_low: float = 1.5
    max_leverage_low: float = 0.8
    max_leverage_moderate: float = 1.0
    max_leverage_high: float = 0.5
    max_leverage_crisis: float = 0.0
    
    # Portfolio
    n_stocks: int = 10
    max_position: float = 0.25
    target_vol: float = 0.18
    
    # Backtest
    rebalance_days: int = 21
    transaction_cost: float = 0.002  # 0.2%
    
    # Risk Management
    max_drawdown_stop: float = 0.15
    volatility_target: float = 0.15

# =============================================================================
# 2. REGIME DETECTION (NRC + VIX Ensemble)
# =============================================================================

class RegimeDetector:
    """NRC + VIX 앙상블 레짐 감지"""
    
    REGIMES = ['ULTRA_LOW', 'LOW', 'MODERATE', 'HIGH', 'CRISIS', 'EXTREME']
    
    # VIX 임계값 (4대 AI 공통 제안)
    VIX_THRESHOLDS = {
        'ULTRA_LOW': 13,
        'LOW': 16,
        'MODERATE': 18,
        'HIGH': 22,
        'CRISIS': 28,
        'EXTREME': 35
    }
    
    def __init__(self, config: AresV9Config):
        self.config = config
        self.regime_history = []
    
    def detect_vix_regime(self, vix: float) -> Tuple[str, float]:
        """VIX 기반 레짐 감지"""
        if vix < self.VIX_THRESHOLDS['ULTRA_LOW']:
            return 'ULTRA_LOW', 0.9
        elif vix < self.VIX_THRESHOLDS['LOW']:
            return 'LOW', 0.8
        elif vix < self.VIX_THRESHOLDS['MODERATE']:
            return 'MODERATE', 0.7
        elif vix < self.VIX_THRESHOLDS['HIGH']:
            return 'HIGH', 0.6
        elif vix < self.VIX_THRESHOLDS['CRISIS']:
            return 'CRISIS', 0.5
        else:
            return 'EXTREME', 0.4
    
    def detect_nrc_regime(self, features: np.ndarray) -> Tuple[str, float]:
        """NRC 기반 레짐 감지 (간소화 버전)
        
        실제 구현에서는 학습된 Transformer 모델 사용
        여기서는 VIX + 추가 피처로 근사
        """
        # features: [vix, vol_20d, ret_20d, ...]
        vix = features[0]
        vol = features[1] if len(features) > 1 else 0.15
        ret = features[2] if len(features) > 2 else 0
        
        # 추가 피처로 레짐 조정
        base_regime, base_conf = self.detect_vix_regime(vix)
        
        # 변동성이 높으면 한 단계 위험 레짐으로
        if vol > 0.25 and base_regime in ['ULTRA_LOW', 'LOW', 'MODERATE']:
            regime_idx = self.REGIMES.index(base_regime)
            base_regime = self.REGIMES[min(regime_idx + 1, len(self.REGIMES) - 1)]
            base_conf *= 0.9
        
        # 최근 수익률이 음수면 위험 레짐으로
        if ret < -0.05 and base_regime in ['ULTRA_LOW', 'LOW']:
            regime_idx = self.REGIMES.index(base_regime)
            base_regime = self.REGIMES[min(regime_idx + 1, len(self.REGIMES) - 1)]
            base_conf *= 0.85
        
        return base_regime, base_conf
    
    def detect(self, vix: float, features: Optional[np.ndarray] = None) -> Tuple[str, float]:
        """앙상블 레짐 감지"""
        vix_regime, vix_conf = self.detect_vix_regime(vix)
        
        if self.config.nrc_enabled and features is not None:
            nrc_regime, nrc_conf = self.detect_nrc_regime(features)
            
            # 앙상블 (NRC 80%, VIX 20%)
            vix_weight = self.config.nrc_vix_weight
            
            if vix_regime == nrc_regime:
                return vix_regime, max(vix_conf, nrc_conf)
            else:
                # 더 보수적인 레짐 선택
                vix_idx = self.REGIMES.index(vix_regime)
                nrc_idx = self.REGIMES.index(nrc_regime)
                
                if nrc_conf > vix_conf * 1.2:
                    return nrc_regime, nrc_conf * (1 - vix_weight)
                else:
                    return vix_regime, vix_conf * vix_weight + nrc_conf * (1 - vix_weight)
        
        return vix_regime, vix_conf

# =============================================================================
# 3. ICIR WEIGHTER (동적 팩터 가중치)
# =============================================================================

class ICIRWeighter:
    """ICIR 기반 동적 팩터 가중치"""
    
    def __init__(self, config: AresV9Config):
        self.config = config
        self.ic_history = {}  # {factor: [(date, ic), ...]}
    
    def calculate_ic(self, factor_scores: pd.Series, forward_returns: pd.Series) -> float:
        """Information Coefficient 계산"""
        valid = factor_scores.notna() & forward_returns.notna()
        if valid.sum() < 10:
            return 0.0
        return factor_scores[valid].corr(forward_returns[valid], method='spearman')
    
    def update_ic(self, date: datetime, factor_name: str, ic: float):
        """IC 히스토리 업데이트"""
        if factor_name not in self.ic_history:
            self.ic_history[factor_name] = []
        self.ic_history[factor_name].append((date, ic))
        
        # 최대 3년 유지
        cutoff = date - timedelta(days=self.config.icir_warmup_days)
        self.ic_history[factor_name] = [
            (d, ic) for d, ic in self.ic_history[factor_name] if d > cutoff
        ]
    
    def get_icir(self, factor_name: str) -> float:
        """ICIR 계산 (IC / std(IC))"""
        if factor_name not in self.ic_history:
            return 0.0
        
        ics = [ic for _, ic in self.ic_history[factor_name]]
        if len(ics) < 12:  # 최소 12개월 필요
            return 0.0
        
        # Exponential decay
        weights = np.exp(-np.arange(len(ics)) / (self.config.icir_decay_halflife / 21))
        weights = weights[::-1]  # 최근 데이터에 높은 가중치
        weights /= weights.sum()
        
        weighted_ic = np.average(ics, weights=weights)
        ic_std = np.std(ics)
        
        if ic_std < 0.01:
            return 0.0
        
        return weighted_ic / ic_std
    
    def get_weights(self, regime: str) -> Dict[str, float]:
        """레짐별 팩터 가중치 반환"""
        mom_icir = self.get_icir('momentum')
        qual_icir = self.get_icir('quality')
        
        # 기본 가중치
        mom_weight = self.config.momentum_base_weight
        qual_weight = self.config.quality_base_weight
        
        # ICIR 기반 조정
        if mom_icir > self.config.icir_min_threshold:
            mom_weight = min(0.90, mom_weight + 0.1 * mom_icir)
        elif mom_icir < -self.config.icir_min_threshold:
            mom_weight = max(0.50, mom_weight - 0.1 * abs(mom_icir))
        
        if qual_icir > self.config.icir_min_threshold:
            qual_weight = min(0.40, qual_weight + 0.1 * qual_icir)
        elif qual_icir < -self.config.icir_min_threshold:
            qual_weight = max(0.10, qual_weight - 0.1 * abs(qual_icir))
        
        # 레짐별 조정 (LOW 레짐에서 quality 부스트)
        if regime == 'LOW':
            qual_weight *= 1.5
            mom_weight *= 0.8
        elif regime in ['HIGH', 'CRISIS']:
            qual_weight *= 1.3
        
        # 정규화
        total = mom_weight + qual_weight
        mom_weight /= total
        qual_weight /= total
        
        # Shrinkage (극단적 가중치 방지)
        shrink = self.config.icir_shrinkage
        mom_weight = mom_weight * (1 - shrink) + 0.7 * shrink
        qual_weight = qual_weight * (1 - shrink) + 0.3 * shrink
        
        return {'momentum': mom_weight, 'quality': qual_weight}

# =============================================================================
# 4. HRP OPTIMIZER (Hierarchical Risk Parity)
# =============================================================================

class HRPOptimizer:
    """Hierarchical Risk Parity 포트폴리오 최적화"""
    
    def __init__(self, config: AresV9Config):
        self.config = config
    
    def get_cluster_var(self, cov: np.ndarray, cluster_items: List[int]) -> float:
        """클러스터 분산 계산"""
        cov_slice = cov[np.ix_(cluster_items, cluster_items)]
        weights = np.ones(len(cluster_items)) / len(cluster_items)
        return np.dot(weights, np.dot(cov_slice, weights))
    
    def get_quasi_diag(self, link: np.ndarray) -> List[int]:
        """Quasi-diagonal 순서 반환"""
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
    
    def get_rec_bipart(self, cov: np.ndarray, sort_ix: List[int]) -> np.ndarray:
        """Recursive bisection으로 가중치 계산"""
        weights = pd.Series(1.0, index=sort_ix)
        cluster_items = [sort_ix]
        
        while len(cluster_items) > 0:
            cluster_items = [
                i[j:k] for i in cluster_items
                for j, k in ((0, len(i) // 2), (len(i) // 2, len(i)))
                if len(i) > 1
            ]
            
            for i in range(0, len(cluster_items), 2):
                cluster0 = cluster_items[i]
                cluster1 = cluster_items[i + 1]
                
                var0 = self.get_cluster_var(cov, cluster0)
                var1 = self.get_cluster_var(cov, cluster1)
                
                alpha = 1 - var0 / (var0 + var1)
                
                weights[cluster0] *= alpha
                weights[cluster1] *= 1 - alpha
        
        return weights.values
    
    def optimize(self, returns: pd.DataFrame, regime: str) -> np.ndarray:
        """HRP 최적화"""
        if len(returns.columns) < 2:
            return np.array([1.0])
        
        # 공분산 행렬
        cov = returns.cov().values
        
        # 상관관계 행렬
        corr = returns.corr().values
        
        # 거리 행렬
        dist = np.sqrt(0.5 * (1 - corr))
        np.fill_diagonal(dist, 0)
        
        # Hierarchical clustering
        try:
            from scipy.cluster.hierarchy import linkage
            from scipy.spatial.distance import squareform
            
            dist_condensed = squareform(dist)
            link = linkage(dist_condensed, method='single')
            
            # Quasi-diagonal 순서
            sort_ix = self.get_quasi_diag(link)
            
            # Recursive bisection
            weights = self.get_rec_bipart(cov, sort_ix)
            
            # 원래 순서로 복원
            weights_ordered = np.zeros(len(returns.columns))
            for i, ix in enumerate(sort_ix):
                weights_ordered[ix] = weights[i]
            
            weights = weights_ordered
            
        except Exception as e:
            # Fallback: 역변동성 가중
            vols = returns.std().values
            weights = 1 / (vols + 1e-6)
            weights /= weights.sum()
        
        # 레짐별 레버리지 조정
        leverage = {
            'ULTRA_LOW': self.config.max_leverage_ultra_low,
            'LOW': self.config.max_leverage_low,
            'MODERATE': self.config.max_leverage_moderate,
            'HIGH': self.config.max_leverage_high,
            'CRISIS': self.config.max_leverage_crisis,
            'EXTREME': 0.0
        }.get(regime, 0.5)
        
        weights *= leverage
        
        # 최대 포지션 제한
        weights = np.clip(weights, 0, self.config.max_position)
        
        return weights

# =============================================================================
# 5. MAIN ENGINE
# =============================================================================

class AresV9Engine:
    """ARES v9.0 통합 엔진"""
    
    def __init__(self, config: Optional[AresV9Config] = None):
        self.config = config or AresV9Config()
        
        # 엔진 초기화
        self.regime_detector = RegimeDetector(self.config)
        self.icir_weighter = ICIRWeighter(self.config)
        self.hrp_optimizer = HRPOptimizer(self.config)
        
        # 상태
        self.positions = {}
        self.portfolio_value = 1.0
        self.values = [1.0]
        self.regime_stats = {}
        self.total_costs = 0
    
    def load_data(self, start: str, end: str) -> Tuple[pd.DataFrame, pd.Series]:
        """데이터 로드"""
        conn = sqlite3.connect(self.config.db_path)
        
        # 종목 유니버스
        symbols = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'AVGO', 'ADBE',
            'NFLX', 'CRM', 'INTC', 'QCOM', 'TXN', 'AMAT', 'MU', 'LRCX', 'KLAC', 'SNPS',
            'NOW', 'PANW', 'CRWD', 'DDOG', 'ZS', 'FTNT', 'NET', 'SNOW', 'PLTR', 'COIN',
            'V', 'MA', 'JPM', 'BAC', 'GS', 'MS', 'BLK', 'SCHW', 'AXP', 'C',
            'UNH', 'JNJ', 'PFE', 'ABBV', 'MRK', 'LLY', 'TMO', 'ABT', 'DHR', 'BMY'
        ]
        
        symbols_str = ','.join([f"'{s}'" for s in symbols])
        
        # 가격 데이터
        query = f"""
        SELECT date, symbol, close
        FROM daily_ohlcv
        WHERE symbol IN ({symbols_str})
        AND date BETWEEN '{start}' AND '{end}'
        ORDER BY date, symbol
        """
        df = pd.read_sql(query, conn)
        df['date'] = pd.to_datetime(df['date'])
        
        # 중복 제거
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        # 피벗
        prices = df.pivot(index='date', columns='symbol', values='close')
        prices = prices.ffill().dropna(axis=1, how='all')
        
        # VIX 데이터
        vix_query = f"""
        SELECT date, close as vix
        FROM daily_ohlcv
        WHERE symbol = 'VIX'
        AND date BETWEEN '{start}' AND '{end}'
        ORDER BY date
        """
        vix_df = pd.read_sql(vix_query, conn)
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        vix_df = vix_df.drop_duplicates(subset=['date'], keep='last')
        vix = vix_df.set_index('date')['vix']
        
        conn.close()
        
        # 인덱스 정렬
        common_dates = prices.index.intersection(vix.index)
        prices = prices.loc[common_dates]
        vix = vix.loc[common_dates]
        
        return prices, vix
    
    def calculate_factors(self, prices: pd.DataFrame, date: datetime) -> pd.DataFrame:
        """팩터 계산 (T-1 데이터만 사용)"""
        # date 이전 데이터만 사용 (룩어헤드 방지)
        hist = prices.loc[:date - timedelta(days=1)]
        
        if len(hist) < 252:
            return pd.DataFrame()
        
        factors = pd.DataFrame(index=hist.columns)
        
        # 모멘텀 (12-1)
        ret_12m = hist.iloc[-1] / hist.iloc[-252] - 1
        ret_1m = hist.iloc[-1] / hist.iloc[-21] - 1
        factors['momentum'] = ret_12m - ret_1m
        
        # Quality (수익률 안정성)
        returns = hist.pct_change().iloc[-252:]
        factors['quality'] = returns.mean() / (returns.std() + 1e-6)
        
        # 저변동성
        factors['low_vol'] = -returns.iloc[-60:].std()
        
        # Z-score 정규화
        for col in factors.columns:
            mean = factors[col].mean()
            std = factors[col].std()
            if std > 0:
                factors[col] = (factors[col] - mean) / std
        
        return factors
    
    def select_stocks(self, factors: pd.DataFrame, weights: Dict[str, float]) -> List[str]:
        """종목 선택"""
        if factors.empty:
            return []
        
        # 종합 점수
        score = pd.Series(0.0, index=factors.index)
        
        for factor, weight in weights.items():
            if factor in factors.columns:
                score += factors[factor].fillna(0) * weight
        
        # 상위 N개 선택
        top_stocks = score.nlargest(self.config.n_stocks).index.tolist()
        
        return top_stocks
    
    def run_day(self, date: datetime, prices: pd.DataFrame, vix: pd.Series) -> Tuple[Dict, float, Dict]:
        """일일 실행"""
        # 현재 VIX
        current_vix = vix.loc[:date].iloc[-1] if date in vix.index or len(vix.loc[:date]) > 0 else 20
        
        # 추가 피처 (NRC용)
        hist = prices.loc[:date - timedelta(days=1)]
        if len(hist) < 60:
            features = np.array([current_vix, 0.15, 0])
        else:
            returns = hist.pct_change().iloc[-60:]
            vol_20d = returns.iloc[-20:].std().mean()
            ret_20d = (hist.iloc[-1] / hist.iloc[-21] - 1).mean()
            features = np.array([current_vix, vol_20d, ret_20d])
        
        # 레짐 감지
        regime, confidence = self.regime_detector.detect(current_vix, features)
        
        # EXTREME/CRISIS 레짐이면 현금
        if regime in ['EXTREME', 'CRISIS']:
            return {}, 0, {'regime': regime, 'confidence': confidence, 'action': 'CASH'}
        
        # 팩터 계산
        factors = self.calculate_factors(prices, date)
        if factors.empty:
            return {}, 0, {'regime': regime, 'confidence': confidence, 'action': 'NO_DATA'}
        
        # ICIR 가중치
        factor_weights = self.icir_weighter.get_weights(regime)
        
        # 종목 선택
        selected = self.select_stocks(factors, factor_weights)
        if not selected:
            return {}, 0, {'regime': regime, 'confidence': confidence, 'action': 'NO_STOCKS'}
        
        # HRP 최적화
        hist_returns = prices[selected].pct_change().iloc[-60:]
        weights = self.hrp_optimizer.optimize(hist_returns, regime)
        
        # 포지션 생성
        new_positions = {selected[i]: weights[i] for i in range(len(selected)) if weights[i] > 0.01}
        
        # 거래 비용
        turnover = sum(
            abs(new_positions.get(s, 0) - self.positions.get(s, 0))
            for s in set(new_positions) | set(self.positions)
        )
        cost = turnover * self.config.transaction_cost
        
        return new_positions, cost, {
            'regime': regime,
            'confidence': confidence,
            'factor_weights': factor_weights,
            'n_stocks': len(new_positions)
        }
    
    def backtest(self, start: str = '2020-01-01', end: str = '2024-12-31') -> Dict:
        """백테스트 실행"""
        print(f"Loading data from {start} to {end}...")
        prices, vix = self.load_data(start, end)
        print(f"Loaded {len(prices)} days, {len(prices.columns)} symbols")
        
        # Warmup 기간
        warmup = 252
        dates = prices.index[warmup:]
        
        # 초기화
        self.portfolio_value = 1.0
        self.values = [1.0]
        self.positions = {}
        self.total_costs = 0
        self.regime_stats = {}
        
        last_rebal = None
        
        for i, date in enumerate(dates):
            # 리밸런싱 여부
            should_rebal = (
                last_rebal is None or
                (date - last_rebal).days >= self.config.rebalance_days
            )
            
            if should_rebal:
                new_positions, cost, meta = self.run_day(date, prices, vix)
                
                if new_positions:
                    self.positions = new_positions
                    self.total_costs += cost
                    last_rebal = date
                
                regime = meta['regime']
                
                # 레짐 통계
                if regime not in self.regime_stats:
                    self.regime_stats[regime] = {'days': 0, 'returns': []}
                self.regime_stats[regime]['days'] += 1
            else:
                regime = 'HOLD'
            
            # 일일 수익률
            daily_ret = 0
            if date in prices.index and len(self.positions) > 0:
                prev_date = prices.index[prices.index.get_loc(date) - 1]
                for sym, weight in self.positions.items():
                    if sym in prices.columns:
                        if prices.loc[date, sym] > 0 and prices.loc[prev_date, sym] > 0:
                            ret = prices.loc[date, sym] / prices.loc[prev_date, sym] - 1
                            daily_ret += weight * ret
            
            # 비용 차감
            if should_rebal:
                daily_ret -= cost
            
            # 포트폴리오 가치 업데이트
            self.portfolio_value *= (1 + daily_ret)
            self.values.append(self.portfolio_value)
            
            # 레짐별 수익률 기록
            if regime != 'HOLD' and regime in self.regime_stats:
                self.regime_stats[regime]['returns'].append(daily_ret)
            
            # 진행 상황
            if (i + 1) % 100 == 0:
                print(f"Progress: {i+1}/{len(dates)} days, Value: {self.portfolio_value:.4f}")
        
        # 성과 계산
        returns = pd.Series(np.diff(self.values) / self.values[:-1], index=dates[:len(self.values)-1])
        
        # 전체 성과
        total_return = self.portfolio_value - 1
        years = len(dates) / 252
        annual_return = (1 + total_return) ** (1 / years) - 1
        
        # Sharpe
        rf_daily = 0.05 / 252
        excess_returns = returns - rf_daily
        sharpe = np.sqrt(252) * excess_returns.mean() / (excess_returns.std() + 1e-6)
        
        # 투자 기간 Sharpe
        invested_returns = returns[returns.abs() > 1e-6]
        invested_sharpe = np.sqrt(252) * invested_returns.mean() / (invested_returns.std() + 1e-6) if len(invested_returns) > 0 else 0
        
        # MDD
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        mdd = drawdown.min()
        
        # 투자 비율
        invested_days = sum(1 for r in returns if abs(r) > 1e-6)
        investment_ratio = invested_days / len(returns)
        
        # 레짐별 성과
        regime_performance = {}
        for regime, stats in self.regime_stats.items():
            if len(stats['returns']) > 0:
                rets = np.array(stats['returns'])
                regime_performance[regime] = {
                    'days': stats['days'],
                    'annual_return': np.mean(rets) * 252,
                    'sharpe': np.sqrt(252) * np.mean(rets) / (np.std(rets) + 1e-6),
                    'win_rate': np.mean(rets > 0)
                }
        
        results = {
            'sharpe': sharpe,
            'invested_sharpe': invested_sharpe,
            'annual_return': annual_return,
            'total_return': total_return,
            'mdd': mdd,
            'investment_ratio': investment_ratio,
            'total_costs': self.total_costs,
            'regime_performance': regime_performance
        }
        
        return results

# =============================================================================
# 6. MAIN
# =============================================================================

if __name__ == "__main__":
    print("="*60)
    print("ARES Ultimate v9.0 - Full Engine Integration")
    print("="*60)
    
    config = AresV9Config()
    engine = AresV9Engine(config)
    
    results = engine.backtest('2020-01-01', '2024-12-31')
    
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    print(f"Sharpe Ratio: {results['sharpe']:.2f} (Target: 2.2)")
    print(f"Invested Sharpe: {results['invested_sharpe']:.2f} (Target: 3.1)")
    print(f"Annual Return: {results['annual_return']*100:.1f}%")
    print(f"Total Return: {results['total_return']*100:.1f}%")
    print(f"MDD: {results['mdd']*100:.1f}%")
    print(f"Investment Ratio: {results['investment_ratio']*100:.1f}%")
    print(f"Total Costs: {results['total_costs']*100:.2f}%")
    
    print("\n" + "="*60)
    print("REGIME PERFORMANCE")
    print("="*60)
    for regime, perf in sorted(results['regime_performance'].items()):
        print(f"{regime:12} | Days: {perf['days']:4} | Return: {perf['annual_return']*100:6.1f}% | Sharpe: {perf['sharpe']:5.2f} | Win: {perf['win_rate']*100:4.1f}%")
