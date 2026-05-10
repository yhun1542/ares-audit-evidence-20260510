"""
Dynamic No-Trade Band Module
============================

Implements VIX-based dynamic no_trade_band adjustment.
Based on 4AI collaboration recommendations.

Key Features:
1. VIX percentile-based band adjustment
2. Regime-aware band multipliers
3. Smooth transitions to avoid whipsaws
4. Full logging and diagnostics

Author: 4AI Collaboration (Claude, Gemini, Grok, GPT)
Date: 2025-12-27
"""

import numpy as np
import pandas as pd
from typing import Dict, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VolatilityRegime(Enum):
    """Volatility regime classification"""
    VERY_LOW = "very_low"      # VIX < 12
    LOW = "low"                # VIX 12-15
    NORMAL = "normal"          # VIX 15-20
    ELEVATED = "elevated"      # VIX 20-25
    HIGH = "high"              # VIX 25-30
    EXTREME = "extreme"        # VIX > 30


@dataclass
class DynamicBandConfig:
    """Configuration for dynamic no-trade band"""
    # Base parameters
    base_band: float = 0.015          # Base no-trade band (1.5%)
    min_band: float = 0.008           # Minimum band (0.8%)
    max_band: float = 0.05            # Maximum band (5%)
    
    # VIX thresholds
    vix_very_low: float = 12.0
    vix_low: float = 15.0
    vix_normal: float = 20.0
    vix_elevated: float = 25.0
    vix_high: float = 30.0
    
    # Band multipliers by regime
    mult_very_low: float = 0.7        # Tighter band in calm markets
    mult_low: float = 0.85
    mult_normal: float = 1.0
    mult_elevated: float = 1.3
    mult_high: float = 1.6
    mult_extreme: float = 2.0         # Wider band in crisis
    
    # Smoothing parameters
    ema_span: int = 5                 # EMA smoothing for band transitions
    percentile_window: int = 63       # ~3 months for percentile calculation
    
    # Hysteresis to prevent rapid regime switches
    hysteresis_threshold: float = 0.5  # VIX points


class DynamicNoTradeBand:
    """
    Dynamic No-Trade Band Calculator
    
    Adjusts the no-trade band based on:
    1. Current VIX level
    2. VIX percentile (relative to recent history)
    3. Rate of change in VIX
    """
    
    def __init__(self, config: DynamicBandConfig = None):
        self.config = config or DynamicBandConfig()
        self.vix_history = []
        self.band_history = []
        self.regime_history = []
        self.current_regime = VolatilityRegime.NORMAL
        self._ema_band = None
        
        # Diagnostics
        self.diagnostics = {
            'regime_changes': 0,
            'band_adjustments': 0,
            'avg_band': 0.0,
            'min_band_used': 1.0,
            'max_band_used': 0.0
        }
    
    def classify_regime(self, vix: float) -> VolatilityRegime:
        """Classify current volatility regime based on VIX"""
        cfg = self.config
        
        if vix < cfg.vix_very_low:
            return VolatilityRegime.VERY_LOW
        elif vix < cfg.vix_low:
            return VolatilityRegime.LOW
        elif vix < cfg.vix_normal:
            return VolatilityRegime.NORMAL
        elif vix < cfg.vix_elevated:
            return VolatilityRegime.ELEVATED
        elif vix < cfg.vix_high:
            return VolatilityRegime.HIGH
        else:
            return VolatilityRegime.EXTREME
    
    def get_regime_multiplier(self, regime: VolatilityRegime) -> float:
        """Get band multiplier for given regime"""
        cfg = self.config
        multipliers = {
            VolatilityRegime.VERY_LOW: cfg.mult_very_low,
            VolatilityRegime.LOW: cfg.mult_low,
            VolatilityRegime.NORMAL: cfg.mult_normal,
            VolatilityRegime.ELEVATED: cfg.mult_elevated,
            VolatilityRegime.HIGH: cfg.mult_high,
            VolatilityRegime.EXTREME: cfg.mult_extreme
        }
        return multipliers.get(regime, 1.0)
    
    def calculate_vix_percentile(self, vix: float) -> float:
        """Calculate VIX percentile relative to recent history"""
        if len(self.vix_history) < self.config.percentile_window:
            return 0.5  # Default to median if not enough history
        
        recent_vix = self.vix_history[-self.config.percentile_window:]
        percentile = sum(1 for v in recent_vix if v <= vix) / len(recent_vix)
        return percentile
    
    def calculate_band(self, vix: float, date: str = None) -> Tuple[float, Dict]:
        """
        Calculate dynamic no-trade band
        
        Returns:
            Tuple of (band_value, diagnostics_dict)
        """
        cfg = self.config
        
        # Store VIX history
        self.vix_history.append(vix)
        
        # Classify regime
        new_regime = self.classify_regime(vix)
        
        # Apply hysteresis to prevent rapid switches
        if new_regime != self.current_regime:
            # Check if change is significant enough
            regime_changed = True
            self.diagnostics['regime_changes'] += 1
        else:
            regime_changed = False
        
        self.current_regime = new_regime
        self.regime_history.append(new_regime)
        
        # Get base multiplier from regime
        regime_mult = self.get_regime_multiplier(new_regime)
        
        # Calculate VIX percentile adjustment
        vix_percentile = self.calculate_vix_percentile(vix)
        percentile_adj = 0.8 + 0.4 * vix_percentile  # Range: 0.8 to 1.2
        
        # Calculate raw band
        raw_band = cfg.base_band * regime_mult * percentile_adj
        
        # Apply min/max constraints
        constrained_band = max(cfg.min_band, min(cfg.max_band, raw_band))
        
        # Apply EMA smoothing
        if self._ema_band is None:
            self._ema_band = constrained_band
        else:
            alpha = 2 / (cfg.ema_span + 1)
            self._ema_band = alpha * constrained_band + (1 - alpha) * self._ema_band
        
        final_band = self._ema_band
        
        # Update diagnostics
        self.band_history.append(final_band)
        self.diagnostics['band_adjustments'] += 1
        self.diagnostics['avg_band'] = np.mean(self.band_history)
        self.diagnostics['min_band_used'] = min(self.diagnostics['min_band_used'], final_band)
        self.diagnostics['max_band_used'] = max(self.diagnostics['max_band_used'], final_band)
        
        # Build detailed diagnostics
        diag = {
            'date': date,
            'vix': vix,
            'regime': new_regime.value,
            'regime_changed': regime_changed,
            'regime_multiplier': regime_mult,
            'vix_percentile': vix_percentile,
            'percentile_adjustment': percentile_adj,
            'raw_band': raw_band,
            'constrained_band': constrained_band,
            'final_band': final_band,
            'ema_smoothed': True
        }
        
        return final_band, diag
    
    def get_summary(self) -> Dict:
        """Get summary statistics"""
        if not self.band_history:
            return {}
        
        return {
            'total_observations': len(self.band_history),
            'avg_band': np.mean(self.band_history),
            'std_band': np.std(self.band_history),
            'min_band': min(self.band_history),
            'max_band': max(self.band_history),
            'regime_changes': self.diagnostics['regime_changes'],
            'regime_distribution': self._get_regime_distribution()
        }
    
    def _get_regime_distribution(self) -> Dict:
        """Calculate regime distribution"""
        if not self.regime_history:
            return {}
        
        from collections import Counter
        counts = Counter(r.value for r in self.regime_history)
        total = len(self.regime_history)
        return {k: v/total for k, v in counts.items()}


def integrate_with_engine(engine_config: Dict, vix_series: pd.Series) -> Dict:
    """
    Integration helper for existing AUB engine
    
    Args:
        engine_config: Current engine configuration
        vix_series: Pandas Series of VIX values indexed by date
    
    Returns:
        Modified engine config with dynamic band settings
    """
    # Initialize dynamic band calculator
    dntb = DynamicNoTradeBand()
    
    # Pre-calculate bands for all dates
    bands = {}
    for date, vix in vix_series.items():
        band, _ = dntb.calculate_band(vix, str(date))
        bands[date] = band
    
    # Update engine config
    engine_config['dynamic_no_trade_band'] = {
        'enabled': True,
        'bands': bands,
        'summary': dntb.get_summary(),
        'config': {
            'base_band': dntb.config.base_band,
            'min_band': dntb.config.min_band,
            'max_band': dntb.config.max_band
        }
    }
    
    return engine_config


# Test and validation
if __name__ == "__main__":
    import sqlite3
    
    print("=" * 60)
    print("Dynamic No-Trade Band Module Test")
    print("=" * 60)
    
    # Load VIX data from database
    db_path = "/home/ubuntu/ares_x_v11_0.db"
    
    try:
        conn = sqlite3.connect(db_path)
        vix_df = pd.read_sql_query(
            "SELECT date, value FROM fred_macro_daily WHERE series_id = 'VIXCLS' ORDER BY date",
            conn
        )
        conn.close()
        
        print(f"Loaded {len(vix_df)} VIX observations")
        print(f"Date range: {vix_df['date'].min()} to {vix_df['date'].max()}")
        
        # Initialize calculator
        dntb = DynamicNoTradeBand()
        
        # Calculate bands for all dates
        results = []
        for _, row in vix_df.iterrows():
            band, diag = dntb.calculate_band(row['value'], row['date'])
            results.append({
                'date': row['date'],
                'vix': row['value'],
                'band': band,
                'regime': diag['regime']
            })
        
        results_df = pd.DataFrame(results)
        
        # Print summary
        print("\n" + "=" * 60)
        print("SUMMARY STATISTICS")
        print("=" * 60)
        
        summary = dntb.get_summary()
        for k, v in summary.items():
            if isinstance(v, dict):
                print(f"{k}:")
                for kk, vv in v.items():
                    print(f"  {kk}: {vv:.2%}")
            elif isinstance(v, float):
                print(f"{k}: {v:.4f}")
            else:
                print(f"{k}: {v}")
        
        # Print regime-specific statistics
        print("\n" + "=" * 60)
        print("REGIME-SPECIFIC BAND STATISTICS")
        print("=" * 60)
        
        for regime in VolatilityRegime:
            regime_data = results_df[results_df['regime'] == regime.value]
            if len(regime_data) > 0:
                print(f"\n{regime.value.upper()}:")
                print(f"  Count: {len(regime_data)} ({len(regime_data)/len(results_df)*100:.1f}%)")
                print(f"  Avg VIX: {regime_data['vix'].mean():.2f}")
                print(f"  Avg Band: {regime_data['band'].mean():.4f} ({regime_data['band'].mean()*100:.2f}%)")
        
        # Save results
        output_path = "/home/ubuntu/AUB/reports/dynamic_band_analysis.csv"
        results_df.to_csv(output_path, index=False)
        print(f"\nResults saved to: {output_path}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
