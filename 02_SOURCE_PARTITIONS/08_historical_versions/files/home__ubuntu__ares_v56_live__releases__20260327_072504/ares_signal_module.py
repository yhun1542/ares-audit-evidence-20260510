"""
ARES Signal Module - Extracted from ares_v55_live_autopilot.py
Phase 1 Refactoring: Module 6/6
Contains: RegimeTracker, AutoTuner, signal processing helpers
"""
import time, logging, math
from typing import Dict, List, Any, Optional
from collections import deque

log = logging.getLogger('ares.signal')

class RegimeTracker:
    """Tracks regime predictions and scores"""
    def __init__(self, history_size=100):
        self.history = deque(maxlen=history_size)
        self.current_regime = 'NORMAL'
        self.confidence = 0.0
    
    def record_prediction(self, regime: str, confidence: float, spy: float, vix: float, selected: List[str], stable_score: float):
        entry = {
            'regime': regime,
            'confidence': confidence,
            'spy': spy,
            'vix': vix,
            'selected_count': len(selected),
            'stable_score': stable_score,
            'timestamp': time.time()
        }
        self.history.append(entry)
        self.current_regime = regime
        self.confidence = confidence
    
    @staticmethod
    def score_prediction(regime: str, ret_1h: float, ret_4h: float, ret_1d: float) -> float:
        if regime == 'BULLISH':
            return (0.3 * max(0, ret_1h) + 0.3 * max(0, ret_4h) + 0.4 * max(0, ret_1d))
        elif regime == 'BEARISH':
            return (0.3 * max(0, -ret_1h) + 0.3 * max(0, -ret_4h) + 0.4 * max(0, -ret_1d))
        else:
            return 0.5 * (1.0 - abs(ret_1d))
    
    def get_regime_name(self, code_or_name: Any) -> str:
        mapping = {0: 'BEARISH', 1: 'NORMAL', 2: 'BULLISH', '0': 'BEARISH', '1': 'NORMAL', '2': 'BULLISH'}
        if code_or_name in mapping:
            return mapping[code_or_name]
        return str(code_or_name).upper() if code_or_name else 'NORMAL'

class AutoTuner:
    """Bounded auto-tuning for alpha parameters"""
    def __init__(self, bounds: Dict[str, tuple]):
        self.bounds = bounds
        self.tune_history = deque(maxlen=50)
    
    def bounded_autotune(self, alpha_params: Dict[str, Any], state: Any, metrics: Dict[str, Any]) -> Dict[str, Any]:
        tuned = dict(alpha_params)
        for key, (lo, hi) in self.bounds.items():
            if key in tuned:
                val = tuned[key]
                if isinstance(val, (int, float)):
                    tuned[key] = max(lo, min(hi, val))
        self.tune_history.append({
            'params': dict(tuned),
            'metrics': dict(metrics),
            'timestamp': time.time()
        })
        return tuned
