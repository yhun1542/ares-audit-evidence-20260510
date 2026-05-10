// core/rate_limiter.mjs — Token bucket (전역 단일 인스턴스)
// KIS 실전: 20 req/sec, 모의: 2 req/sec
//  - 고속 주문 파동이 와도 TPS 초과를 물리적으로 막음
//  - 각 호출은 acquire()로 1 permit 소비

export class RateLimiter {
  constructor({ rps = 20, burst = 20 } = {}) {
    this.rps = rps;
    this.capacity = burst;
    this.tokens = burst;
    this.lastRefillMs = Date.now();
    this.queue = [];
  }

  _refill() {
    const now = Date.now();
    const elapsed = (now - this.lastRefillMs) / 1000;
    this.tokens = Math.min(this.capacity, this.tokens + elapsed * this.rps);
    this.lastRefillMs = now;
  }

  async acquire(n = 1) {
    return new Promise(resolve => {
      const tryTake = () => {
        this._refill();
        if (this.tokens >= n) {
          this.tokens -= n;
          resolve();
        } else {
          // 다음 permit이 생성되는 시점을 계산
          const need = (n - this.tokens) / this.rps * 1000;
          setTimeout(tryTake, Math.max(5, need));
        }
      };
      tryTake();
    });
  }

  stats() {
    this._refill();
    return { tokens: this.tokens.toFixed(2), rps: this.rps, capacity: this.capacity };
  }
}

// 전역 싱글톤 (동일 프로세스 내 모든 요청이 공유)
let _global = null;
export function getGlobalLimiter(mode = "real") {
  if (!_global) {
    _global = new RateLimiter({
      rps:   mode === "paper" ? 2  : 18,  // KIS 한도보다 10% 여유
      burst: mode === "paper" ? 2  : 18,
    });
  }
  return _global;
}
