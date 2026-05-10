"""
ARES Risk Module - Extracted from ares_v55_live_autopilot.py
Phase 1 Refactoring: Module 5/6
Contains: BrokerConsumerGuard, halt/kill-switch helpers
"""
import json, time, logging
from typing import Optional, Dict, Tuple

log = logging.getLogger('ares.risk')

class BrokerConsumerGuard:
    """Broker consumer rate limiting and guard"""
    def __init__(self, redis_client, max_calls_per_min=30, cooldown_sec=60):
        self.r = redis_client
        self.max_calls = max_calls_per_min
        self.cooldown = cooldown_sec
        self.call_log = []
    
    def can_call(self) -> bool:
        now = time.time()
        self.call_log = [t for t in self.call_log if now - t < 60]
        return len(self.call_log) < self.max_calls
    
    def record_call(self):
        self.call_log.append(time.time())
    
    def get_rate(self) -> float:
        now = time.time()
        recent = [t for t in self.call_log if now - t < 60]
        return len(recent)

class HaltManager:
    """Manages halt state and kill-switch checks"""
    def __init__(self, redis_client, keys):
        self.r = redis_client
        self.keys = keys
        self.halted = False
        self.halt_reason = ''
    
    def check_kill_switch(self) -> Tuple[bool, str]:
        try:
            val = self.r.get(self.keys.get('kill_switch', 'ares:kill_switch'))
            if val and str(val).lower() in ('1', 'true', 'on'):
                return True, 'Kill switch activated'
        except Exception as e:
            log.error(f'Kill switch check error: {e}')
        return False, ''
    
    def halt_with_incident(self, reason: str, context: Optional[Dict] = None):
        self.halted = True
        self.halt_reason = reason
        incident = {
            'reason': reason,
            'context': context or {},
            'timestamp': time.time(),
            'iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }
        try:
            self.r.set('ares:halt:incident', json.dumps(incident), ex=86400)
            self.r.set('ares:halt:active', '1', ex=86400)
            log.warning(f'HALT: {reason}')
        except Exception as e:
            log.error(f'Halt incident save error: {e}')
    
    def maybe_toggle_halt(self) -> bool:
        try:
            val = self.r.get('ares:halt:active')
            if val and str(val) == '1':
                self.halted = True
                return True
        except Exception:
            pass
        return False
    
    def clear_halt(self):
        self.halted = False
        self.halt_reason = ''
        try:
            self.r.delete('ares:halt:active')
        except Exception:
            pass
