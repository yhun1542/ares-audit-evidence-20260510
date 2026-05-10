/**
 * Paper Trading 24시간 가동 데몬 시스템
 * 
 * 기능:
 * - 24시간 무중단 운영
 * - 실시간 레짐 감지 및 포트폴리오 리밸런싱
 * - 3-AI 합의 시스템 연동
 * - 헬스체크 및 자동 복구
 * - 상세 로깅 및 알림
 */

import fs from 'fs';
import https from 'https';
import http from 'http';

// ============================================================================
// 설정
// ============================================================================

const CONFIG = {
  // 시장 시간 (미국 동부 시간 기준)
  marketHours: {
    preMarket: { start: '04:00', end: '09:30' },
    regular: { start: '09:30', end: '16:00' },
    afterHours: { start: '16:00', end: '20:00' }
  },
  
  // 폴링 간격
  intervals: {
    marketData: 5000,      // 5초
    regimeCheck: 60000,    // 1분
    portfolioCheck: 300000, // 5분
    healthCheck: 30000,    // 30초
    aiConsensus: 900000    // 15분
  },
  
  // 리스크 한도
  riskLimits: {
    dailyLossLimit: 0.02,
    positionSizeLimit: 0.10,
    sectorExposureLimit: 0.30,
    singleStockLimit: 0.05,
    maxLeverage: 2.0
  },
  
  // 알림 설정
  notifications: {
    enabled: true,
    channels: ['console', 'file', 'webhook']
  }
};

// ============================================================================
// 상태 관리
// ============================================================================

const STATE = {
  isRunning: false,
  startTime: null,
  lastUpdate: null,
  currentRegime: null,
  portfolio: {
    paper: { equity: 100000, positions: [], pnl: 0 },
    shadow: { equity: 100000, positions: [], pnl: 0 }
  },
  metrics: {
    totalSignals: 0,
    executedOrders: 0,
    regimeChanges: 0,
    aiConsensusCount: 0,
    errors: 0
  },
  health: {
    status: 'initializing',
    lastHealthCheck: null,
    uptime: 0,
    memoryUsage: 0
  }
};

// ============================================================================
// 로깅 시스템
// ============================================================================

class Logger {
  constructor(logDir = '/home/ubuntu/aub-trading-system/logs') {
    this.logDir = logDir;
    this.ensureLogDir();
  }

  ensureLogDir() {
    if (!fs.existsSync(this.logDir)) {
      fs.mkdirSync(this.logDir, { recursive: true });
    }
  }

  log(level, message, data = {}) {
    const timestamp = new Date().toISOString();
    const logEntry = {
      timestamp,
      level,
      message,
      data,
      pid: process.pid
    };

    // 콘솔 출력
    const levelColors = {
      INFO: '\x1b[36m',
      WARN: '\x1b[33m',
      ERROR: '\x1b[31m',
      SUCCESS: '\x1b[32m'
    };
    const color = levelColors[level] || '\x1b[0m';
    console.log(`${color}[${timestamp}] [${level}]\x1b[0m ${message}`);

    // 파일 로깅
    const logFile = `${this.logDir}/trading-${new Date().toISOString().split('T')[0]}.log`;
    fs.appendFileSync(logFile, JSON.stringify(logEntry) + '\n');

    return logEntry;
  }

  info(message, data) { return this.log('INFO', message, data); }
  warn(message, data) { return this.log('WARN', message, data); }
  error(message, data) { return this.log('ERROR', message, data); }
  success(message, data) { return this.log('SUCCESS', message, data); }
}

const logger = new Logger();

// ============================================================================
// 시장 데이터 수집기
// ============================================================================

class MarketDataCollector {
  constructor() {
    this.lastData = null;
    this.symbols = ['SPY', 'QQQ', 'IWM', 'VIX', 'TLT', 'GLD'];
  }

  async collectData() {
    try {
      // 시뮬레이션: 실제로는 IBKR API 호출
      const marketData = {
        timestamp: new Date().toISOString(),
        symbols: {}
      };

      for (const symbol of this.symbols) {
        marketData.symbols[symbol] = {
          price: 100 + Math.random() * 10,
          change: (Math.random() - 0.5) * 2,
          volume: Math.floor(Math.random() * 1000000),
          bid: 99.5 + Math.random() * 10,
          ask: 100.5 + Math.random() * 10
        };
      }

      this.lastData = marketData;
      return marketData;
    } catch (error) {
      logger.error('Market data collection failed', { error: error.message });
      STATE.metrics.errors++;
      return null;
    }
  }

  getVIX() {
    return this.lastData?.symbols?.VIX?.price || 20;
  }
}

// ============================================================================
// 15단계 레짐 감지 엔진
// ============================================================================

class RegimeDetector {
  constructor() {
    this.regimes = [
      'EXTREME_BULL', 'STRONG_BULL', 'MODERATE_BULL', 'MILD_BULL', 'NEUTRAL_BULL',
      'NEUTRAL', 
      'NEUTRAL_BEAR', 'MILD_BEAR', 'MODERATE_BEAR', 'STRONG_BEAR', 'EXTREME_BEAR',
      'HIGH_VOLATILITY', 'LOW_VOLATILITY', 'TRANSITION', 'CRISIS'
    ];
    this.currentRegime = 'NEUTRAL';
    this.regimeHistory = [];
  }

  detectRegime(marketData) {
    try {
      const vix = marketData?.symbols?.VIX?.price || 20;
      const spyChange = marketData?.symbols?.SPY?.change || 0;
      
      let newRegime = 'NEUTRAL';

      // VIX 기반 변동성 레짐
      if (vix > 35) {
        newRegime = 'CRISIS';
      } else if (vix > 25) {
        newRegime = 'HIGH_VOLATILITY';
      } else if (vix < 12) {
        newRegime = 'LOW_VOLATILITY';
      }
      // SPY 변화 기반 방향성 레짐
      else if (spyChange > 2) {
        newRegime = 'EXTREME_BULL';
      } else if (spyChange > 1.5) {
        newRegime = 'STRONG_BULL';
      } else if (spyChange > 1) {
        newRegime = 'MODERATE_BULL';
      } else if (spyChange > 0.5) {
        newRegime = 'MILD_BULL';
      } else if (spyChange > 0) {
        newRegime = 'NEUTRAL_BULL';
      } else if (spyChange < -2) {
        newRegime = 'EXTREME_BEAR';
      } else if (spyChange < -1.5) {
        newRegime = 'STRONG_BEAR';
      } else if (spyChange < -1) {
        newRegime = 'MODERATE_BEAR';
      } else if (spyChange < -0.5) {
        newRegime = 'MILD_BEAR';
      } else if (spyChange < 0) {
        newRegime = 'NEUTRAL_BEAR';
      }

      // 레짐 변경 감지
      if (newRegime !== this.currentRegime) {
        const oldRegime = this.currentRegime;
        this.currentRegime = newRegime;
        this.regimeHistory.push({
          timestamp: new Date().toISOString(),
          from: oldRegime,
          to: newRegime
        });
        
        STATE.metrics.regimeChanges++;
        logger.warn(`Regime changed: ${oldRegime} → ${newRegime}`, { vix, spyChange });
        
        return { changed: true, from: oldRegime, to: newRegime };
      }

      return { changed: false, current: this.currentRegime };
    } catch (error) {
      logger.error('Regime detection failed', { error: error.message });
      return { changed: false, current: this.currentRegime, error: error.message };
    }
  }
}

// ============================================================================
// 3-AI 합의 시스템
// ============================================================================

class AIConsensusSystem {
  constructor() {
    this.models = ['gpt', 'claude', 'grok'];
    this.lastConsensus = null;
  }

  async getConsensus(context) {
    try {
      const prompt = `
Trading System Status:
- Current Regime: ${context.regime}
- VIX: ${context.vix}
- Portfolio PnL: ${context.pnl}%
- Recent Signals: ${context.signalCount}

Provide trading recommendation in JSON:
{
  "action": "HOLD" | "REDUCE_RISK" | "INCREASE_EXPOSURE" | "REBALANCE",
  "confidence": 0-100,
  "reasoning": "brief explanation"
}
`;

      const results = {};
      
      // GPT
      if (process.env.OPENAI_API_KEY) {
        try {
          results.gpt = await this.callGPT(prompt);
        } catch (e) {
          logger.warn('GPT call failed', { error: e.message });
        }
      }

      // Claude
      if (process.env.ANTHROPIC_API_KEY) {
        try {
          results.claude = await this.callClaude(prompt);
        } catch (e) {
          logger.warn('Claude call failed', { error: e.message });
        }
      }

      // Grok
      if (process.env.XAI_API_KEY) {
        try {
          results.grok = await this.callGrok(prompt);
        } catch (e) {
          logger.warn('Grok call failed', { error: e.message });
        }
      }

      // 합의 계산
      const consensus = this.calculateConsensus(results);
      this.lastConsensus = consensus;
      STATE.metrics.aiConsensusCount++;

      logger.info('AI Consensus obtained', { consensus });
      return consensus;
    } catch (error) {
      logger.error('AI Consensus failed', { error: error.message });
      return null;
    }
  }

  async callGPT(prompt) {
    return new Promise((resolve, reject) => {
      const data = JSON.stringify({
        model: 'gpt-4o',
        messages: [{ role: 'user', content: prompt }],
        max_tokens: 500
      });

      const req = https.request({
        hostname: 'api.openai.com',
        path: '/v1/chat/completions',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + process.env.OPENAI_API_KEY
        }
      }, (res) => {
        let body = '';
        res.on('data', chunk => body += chunk);
        res.on('end', () => {
          try {
            const result = JSON.parse(body);
            resolve(result.choices?.[0]?.message?.content || '');
          } catch (e) {
            reject(e);
          }
        });
      });

      req.on('error', reject);
      req.write(data);
      req.end();
    });
  }

  async callClaude(prompt) {
    return new Promise((resolve, reject) => {
      const data = JSON.stringify({
        model: 'claude-3-5-sonnet-20241022',
        max_tokens: 500,
        messages: [{ role: 'user', content: prompt }]
      });

      const req = https.request({
        hostname: 'api.anthropic.com',
        path: '/v1/messages',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': process.env.ANTHROPIC_API_KEY,
          'anthropic-version': '2023-06-01'
        }
      }, (res) => {
        let body = '';
        res.on('data', chunk => body += chunk);
        res.on('end', () => {
          try {
            const result = JSON.parse(body);
            resolve(result.content?.[0]?.text || '');
          } catch (e) {
            reject(e);
          }
        });
      });

      req.on('error', reject);
      req.write(data);
      req.end();
    });
  }

  async callGrok(prompt) {
    return new Promise((resolve, reject) => {
      const data = JSON.stringify({
        model: 'grok-beta',
        messages: [{ role: 'user', content: prompt }],
        max_tokens: 500
      });

      const req = https.request({
        hostname: 'api.x.ai',
        path: '/v1/chat/completions',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + process.env.XAI_API_KEY
        }
      }, (res) => {
        let body = '';
        res.on('data', chunk => body += chunk);
        res.on('end', () => {
          try {
            const result = JSON.parse(body);
            resolve(result.choices?.[0]?.message?.content || '');
          } catch (e) {
            reject(e);
          }
        });
      });

      req.on('error', reject);
      req.write(data);
      req.end();
    });
  }

  calculateConsensus(results) {
    const actions = [];
    const confidences = [];

    for (const [model, response] of Object.entries(results)) {
      if (!response) continue;
      
      try {
        const jsonMatch = response.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          const parsed = JSON.parse(jsonMatch[0]);
          if (parsed.action) actions.push(parsed.action);
          if (parsed.confidence) confidences.push(parsed.confidence);
        }
      } catch (e) {
        // JSON 파싱 실패
      }
    }

    // 다수결
    const actionCounts = {};
    for (const action of actions) {
      actionCounts[action] = (actionCounts[action] || 0) + 1;
    }
    
    const consensusAction = Object.entries(actionCounts)
      .sort((a, b) => b[1] - a[1])[0]?.[0] || 'HOLD';
    
    const avgConfidence = confidences.length > 0
      ? confidences.reduce((a, b) => a + b, 0) / confidences.length
      : 50;

    return {
      action: consensusAction,
      confidence: avgConfidence,
      modelCount: Object.keys(results).length,
      timestamp: new Date().toISOString()
    };
  }
}

// ============================================================================
// 포트폴리오 관리자
// ============================================================================

class PortfolioManager {
  constructor() {
    this.positions = [];
  }

  async rebalance(regime, consensus) {
    try {
      // 레짐에 따른 목표 배분
      const targetAllocation = this.getTargetAllocation(regime);
      
      // 현재 포트폴리오와 비교
      const rebalanceActions = this.calculateRebalanceActions(targetAllocation);
      
      // Paper Trading 실행
      for (const action of rebalanceActions) {
        await this.executeOrder(action, 'paper');
        await this.recordShadow(action);
      }

      logger.info('Portfolio rebalanced', { 
        regime, 
        consensus: consensus?.action,
        actions: rebalanceActions.length 
      });

      return rebalanceActions;
    } catch (error) {
      logger.error('Rebalance failed', { error: error.message });
      return [];
    }
  }

  getTargetAllocation(regime) {
    const allocations = {
      'EXTREME_BULL': { SPY: 0.4, QQQ: 0.3, IWM: 0.2, cash: 0.1 },
      'STRONG_BULL': { SPY: 0.35, QQQ: 0.25, IWM: 0.15, TLT: 0.1, cash: 0.15 },
      'MODERATE_BULL': { SPY: 0.3, QQQ: 0.2, TLT: 0.15, GLD: 0.1, cash: 0.25 },
      'NEUTRAL': { SPY: 0.25, TLT: 0.25, GLD: 0.15, cash: 0.35 },
      'MODERATE_BEAR': { SPY: 0.15, TLT: 0.3, GLD: 0.2, cash: 0.35 },
      'STRONG_BEAR': { TLT: 0.35, GLD: 0.25, cash: 0.4 },
      'EXTREME_BEAR': { TLT: 0.3, GLD: 0.2, cash: 0.5 },
      'CRISIS': { TLT: 0.2, GLD: 0.1, cash: 0.7 },
      'HIGH_VOLATILITY': { SPY: 0.15, TLT: 0.25, GLD: 0.2, cash: 0.4 },
      'LOW_VOLATILITY': { SPY: 0.35, QQQ: 0.25, IWM: 0.15, cash: 0.25 }
    };

    return allocations[regime] || allocations['NEUTRAL'];
  }

  calculateRebalanceActions(targetAllocation) {
    const actions = [];
    const currentEquity = STATE.portfolio.paper.equity;

    for (const [symbol, targetWeight] of Object.entries(targetAllocation)) {
      if (symbol === 'cash') continue;

      const targetValue = currentEquity * targetWeight;
      const currentPosition = STATE.portfolio.paper.positions.find(p => p.symbol === symbol);
      const currentValue = currentPosition?.value || 0;
      const diff = targetValue - currentValue;

      if (Math.abs(diff) > currentEquity * 0.01) { // 1% 이상 차이
        actions.push({
          symbol,
          action: diff > 0 ? 'BUY' : 'SELL',
          amount: Math.abs(diff),
          targetWeight
        });
      }
    }

    return actions;
  }

  async executeOrder(action, mode) {
    // Paper Trading 주문 실행 시뮬레이션
    const slippage = 0.001 + Math.random() * 0.002; // 0.1-0.3% 슬리피지
    const commission = action.amount * 0.0001; // 0.01% 커미션

    const executedAmount = action.amount * (1 - slippage) - commission;

    STATE.metrics.executedOrders++;
    
    logger.info(`Order executed (${mode})`, {
      symbol: action.symbol,
      action: action.action,
      amount: executedAmount,
      slippage: (slippage * 100).toFixed(2) + '%'
    });

    return { success: true, executedAmount, slippage, commission };
  }

  async recordShadow(action) {
    // Shadow Mode 기록
    STATE.portfolio.shadow.positions.push({
      ...action,
      timestamp: new Date().toISOString(),
      idealExecution: action.amount // 슬리피지 없는 이상적 실행
    });
  }
}

// ============================================================================
// 헬스 모니터
// ============================================================================

class HealthMonitor {
  constructor() {
    this.checks = [];
  }

  async performHealthCheck() {
    const health = {
      timestamp: new Date().toISOString(),
      status: 'healthy',
      checks: {}
    };

    // 메모리 체크
    const memUsage = process.memoryUsage();
    const memoryPercent = memUsage.heapUsed / memUsage.heapTotal;
    health.checks.memory = {
      status: memoryPercent < 0.85 ? 'ok' : 'warning',
      value: (memoryPercent * 100).toFixed(1) + '%'
    };

    // 업타임 체크
    const uptime = STATE.startTime ? Date.now() - STATE.startTime : 0;
    health.checks.uptime = {
      status: 'ok',
      value: Math.floor(uptime / 1000 / 60) + ' minutes'
    };

    // 에러율 체크
    const errorRate = STATE.metrics.errors / (STATE.metrics.totalSignals || 1);
    health.checks.errorRate = {
      status: errorRate < 0.05 ? 'ok' : 'warning',
      value: (errorRate * 100).toFixed(2) + '%'
    };

    // 전체 상태 결정
    const hasWarning = Object.values(health.checks).some(c => c.status === 'warning');
    const hasError = Object.values(health.checks).some(c => c.status === 'error');
    
    if (hasError) health.status = 'unhealthy';
    else if (hasWarning) health.status = 'degraded';

    STATE.health = {
      status: health.status,
      lastHealthCheck: health.timestamp,
      uptime,
      memoryUsage: memoryPercent
    };

    return health;
  }
}

// ============================================================================
// HTTP API 서버
// ============================================================================

class APIServer {
  constructor(port = 8080) {
    this.port = port;
    this.server = null;
  }

  start() {
    this.server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Access-Control-Allow-Origin', '*');

      if (req.url === '/health') {
        res.end(JSON.stringify(STATE.health));
      } else if (req.url === '/status') {
        res.end(JSON.stringify({
          isRunning: STATE.isRunning,
          startTime: STATE.startTime,
          lastUpdate: STATE.lastUpdate,
          currentRegime: STATE.currentRegime,
          metrics: STATE.metrics
        }));
      } else if (req.url === '/portfolio') {
        res.end(JSON.stringify(STATE.portfolio));
      } else if (req.url === '/metrics') {
        res.end(JSON.stringify(STATE.metrics));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ error: 'Not found' }));
      }
    });

    this.server.listen(this.port, () => {
      logger.info(`API server started on port ${this.port}`);
    });
  }

  stop() {
    if (this.server) {
      this.server.close();
      logger.info('API server stopped');
    }
  }
}

// ============================================================================
// 메인 트레이딩 데몬
// ============================================================================

class TradingDaemon {
  constructor() {
    this.marketData = new MarketDataCollector();
    this.regimeDetector = new RegimeDetector();
    this.aiConsensus = new AIConsensusSystem();
    this.portfolioManager = new PortfolioManager();
    this.healthMonitor = new HealthMonitor();
    this.apiServer = new APIServer(8080);
    
    this.intervals = {};
  }

  async start() {
    logger.success('='.repeat(60));
    logger.success('Paper Trading Daemon Starting...');
    logger.success('='.repeat(60));

    STATE.isRunning = true;
    STATE.startTime = Date.now();

    // API 서버 시작
    this.apiServer.start();

    // 메인 루프 시작
    this.startMarketDataLoop();
    this.startRegimeCheckLoop();
    this.startPortfolioCheckLoop();
    this.startHealthCheckLoop();
    this.startAIConsensusLoop();

    logger.success('All loops started. Daemon is running 24/7.');
    logger.info('API endpoints available:', {
      health: 'http://localhost:8080/health',
      status: 'http://localhost:8080/status',
      portfolio: 'http://localhost:8080/portfolio',
      metrics: 'http://localhost:8080/metrics'
    });

    // 그레이스풀 셧다운 핸들러
    process.on('SIGINT', () => this.shutdown('SIGINT'));
    process.on('SIGTERM', () => this.shutdown('SIGTERM'));
  }

  startMarketDataLoop() {
    this.intervals.marketData = setInterval(async () => {
      const data = await this.marketData.collectData();
      if (data) {
        STATE.lastUpdate = new Date().toISOString();
        STATE.metrics.totalSignals++;
      }
    }, CONFIG.intervals.marketData);
  }

  startRegimeCheckLoop() {
    this.intervals.regimeCheck = setInterval(async () => {
      const result = this.regimeDetector.detectRegime(this.marketData.lastData);
      STATE.currentRegime = result.current || result.to;
      
      if (result.changed) {
        // 레짐 변경 시 리밸런싱 트리거
        const consensus = await this.aiConsensus.getConsensus({
          regime: result.to,
          vix: this.marketData.getVIX(),
          pnl: (STATE.portfolio.paper.pnl / STATE.portfolio.paper.equity * 100).toFixed(2),
          signalCount: STATE.metrics.totalSignals
        });
        
        await this.portfolioManager.rebalance(result.to, consensus);
      }
    }, CONFIG.intervals.regimeCheck);
  }

  startPortfolioCheckLoop() {
    this.intervals.portfolioCheck = setInterval(async () => {
      // 정기 포트폴리오 체크 및 리밸런싱
      const consensus = this.aiConsensus.lastConsensus;
      
      if (consensus?.action === 'REBALANCE') {
        await this.portfolioManager.rebalance(STATE.currentRegime, consensus);
      }
    }, CONFIG.intervals.portfolioCheck);
  }

  startHealthCheckLoop() {
    this.intervals.healthCheck = setInterval(async () => {
      const health = await this.healthMonitor.performHealthCheck();
      
      if (health.status !== 'healthy') {
        logger.warn('Health check warning', health);
      }
    }, CONFIG.intervals.healthCheck);
  }

  startAIConsensusLoop() {
    this.intervals.aiConsensus = setInterval(async () => {
      await this.aiConsensus.getConsensus({
        regime: STATE.currentRegime,
        vix: this.marketData.getVIX(),
        pnl: (STATE.portfolio.paper.pnl / STATE.portfolio.paper.equity * 100).toFixed(2),
        signalCount: STATE.metrics.totalSignals
      });
    }, CONFIG.intervals.aiConsensus);
  }

  async shutdown(signal) {
    logger.warn(`Shutdown signal received: ${signal}`);
    
    STATE.isRunning = false;

    // 모든 인터벌 정리
    for (const [name, interval] of Object.entries(this.intervals)) {
      clearInterval(interval);
      logger.info(`Stopped ${name} loop`);
    }

    // API 서버 종료
    this.apiServer.stop();

    // 최종 상태 저장
    const finalState = {
      shutdownTime: new Date().toISOString(),
      uptime: Date.now() - STATE.startTime,
      metrics: STATE.metrics,
      portfolio: STATE.portfolio
    };
    
    fs.writeFileSync(
      '/home/ubuntu/aub-trading-system/logs/final-state.json',
      JSON.stringify(finalState, null, 2)
    );

    logger.success('Daemon shutdown complete');
    process.exit(0);
  }
}

// ============================================================================
// PM2 설정 생성
// ============================================================================

function generatePM2Config() {
  const config = {
    apps: [{
      name: 'paper-trading-daemon',
      script: 'paper-trading-daemon.mjs',
      cwd: '/home/ubuntu/aub-trading-system',
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
      env: {
        NODE_ENV: 'production'
      },
      error_file: '/home/ubuntu/aub-trading-system/logs/pm2-error.log',
      out_file: '/home/ubuntu/aub-trading-system/logs/pm2-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
      restart_delay: 5000,
      max_restarts: 10,
      min_uptime: '10s'
    }]
  };

  fs.writeFileSync(
    '/home/ubuntu/aub-trading-system/ecosystem.config.cjs',
    'module.exports = ' + JSON.stringify(config, null, 2)
  );

  logger.info('PM2 config generated: ecosystem.config.cjs');
}

// ============================================================================
// 메인 실행
// ============================================================================

async function main() {
  console.log('='.repeat(70));
  console.log('🚀 Paper Trading 24시간 가동 시스템');
  console.log('='.repeat(70));

  // PM2 설정 생성
  generatePM2Config();

  // 데몬 시작
  const daemon = new TradingDaemon();
  await daemon.start();
}

main().catch(error => {
  logger.error('Fatal error', { error: error.message, stack: error.stack });
  process.exit(1);
});
