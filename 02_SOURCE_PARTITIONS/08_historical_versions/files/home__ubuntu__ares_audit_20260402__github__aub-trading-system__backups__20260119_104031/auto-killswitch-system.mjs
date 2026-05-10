#!/usr/bin/env node
/**
 * 자동 킬스위치 및 청산 시스템
 * Level 6 Critical 요구사항 구현
 */

import https from 'https';
import fs from 'fs';

// Configuration
const CONFIG = {
  // Risk limits
  dailyLossLimit: 0.02, // 2% daily loss limit
  maxDrawdown: 0.05, // 5% max drawdown
  positionLossLimit: 0.03, // 3% per position loss limit
  volatilityThreshold: 0.5, // VIX > 50 triggers caution
  
  // Killswitch triggers
  triggers: {
    dailyLossExceeded: false,
    maxDrawdownExceeded: false,
    systemError: false,
    manualOverride: false,
    marketHalt: false,
    brokerDisconnect: false,
    aiConsensusReject: false
  },
  
  // Liquidation settings
  liquidation: {
    urgentLiquidationThreshold: 0.04, // 4% loss = urgent liquidation
    gracefulLiquidationThreshold: 0.025, // 2.5% loss = graceful liquidation
    liquidationOrderType: 'MARKET', // or 'LIMIT' with slippage tolerance
    maxSlippageTolerance: 0.005, // 0.5% max slippage for limit orders
    liquidationBatchSize: 5, // Number of positions to liquidate per batch
    batchIntervalMs: 1000 // 1 second between batches
  },
  
  // Notification settings
  notifications: {
    smsEnabled: true,
    emailEnabled: true,
    webhookEnabled: true,
    escalationLevels: ['WARNING', 'ALERT', 'CRITICAL', 'EMERGENCY']
  }
};

// State
const STATE = {
  isKillswitchActive: false,
  activeTriggers: [],
  liquidationInProgress: false,
  liquidatedPositions: [],
  totalLiquidatedValue: 0,
  lastHealthCheck: null,
  systemStatus: 'NORMAL' // NORMAL, CAUTION, WARNING, CRITICAL, EMERGENCY
};

// Portfolio state (simulated)
let PORTFOLIO = {
  startOfDayEquity: 1000000,
  currentEquity: 1000000,
  peakEquity: 1000000,
  positions: [
    { symbol: 'AAPL', quantity: 100, avgPrice: 150, currentPrice: 148, unrealizedPnL: -200 },
    { symbol: 'GOOGL', quantity: 50, avgPrice: 140, currentPrice: 142, unrealizedPnL: 100 },
    { symbol: 'MSFT', quantity: 75, avgPrice: 380, currentPrice: 375, unrealizedPnL: -375 },
    { symbol: 'NVDA', quantity: 30, avgPrice: 500, currentPrice: 520, unrealizedPnL: 600 },
    { symbol: 'TSLA', quantity: 40, avgPrice: 250, currentPrice: 245, unrealizedPnL: -200 }
  ],
  cash: 500000,
  dailyPnL: 0,
  realizedPnL: 0
};

// Logging
const LOG_FILE = '/home/ubuntu/aub-trading-system/logs/killswitch.log';

function log(level, message, data = {}) {
  const timestamp = new Date().toISOString();
  const logEntry = {
    timestamp,
    level,
    message,
    data,
    systemStatus: STATE.systemStatus,
    killswitchActive: STATE.isKillswitchActive
  };
  
  console.log(`[${timestamp}] [${level}] ${message}`, data);
  
  try {
    fs.appendFileSync(LOG_FILE, JSON.stringify(logEntry) + '\n');
  } catch (e) {
    // Ignore file write errors
  }
}

// Risk calculations
function calculateDailyLoss() {
  const dailyPnL = PORTFOLIO.currentEquity - PORTFOLIO.startOfDayEquity;
  const dailyLossPercent = dailyPnL / PORTFOLIO.startOfDayEquity;
  return dailyLossPercent;
}

function calculateDrawdown() {
  const drawdown = (PORTFOLIO.peakEquity - PORTFOLIO.currentEquity) / PORTFOLIO.peakEquity;
  return drawdown;
}

function calculatePositionLosses() {
  return PORTFOLIO.positions.map(pos => ({
    symbol: pos.symbol,
    lossPercent: (pos.currentPrice - pos.avgPrice) / pos.avgPrice,
    unrealizedPnL: pos.unrealizedPnL
  }));
}

// Killswitch logic
function checkKillswitchTriggers() {
  const triggers = [];
  
  // Check daily loss limit
  const dailyLoss = calculateDailyLoss();
  if (dailyLoss < -CONFIG.dailyLossLimit) {
    triggers.push({
      type: 'DAILY_LOSS_EXCEEDED',
      value: dailyLoss,
      threshold: -CONFIG.dailyLossLimit,
      severity: 'CRITICAL'
    });
  }
  
  // Check max drawdown
  const drawdown = calculateDrawdown();
  if (drawdown > CONFIG.maxDrawdown) {
    triggers.push({
      type: 'MAX_DRAWDOWN_EXCEEDED',
      value: drawdown,
      threshold: CONFIG.maxDrawdown,
      severity: 'CRITICAL'
    });
  }
  
  // Check position losses
  const positionLosses = calculatePositionLosses();
  for (const pos of positionLosses) {
    if (pos.lossPercent < -CONFIG.positionLossLimit) {
      triggers.push({
        type: 'POSITION_LOSS_EXCEEDED',
        symbol: pos.symbol,
        value: pos.lossPercent,
        threshold: -CONFIG.positionLossLimit,
        severity: 'WARNING'
      });
    }
  }
  
  return triggers;
}

function activateKillswitch(triggers) {
  if (STATE.isKillswitchActive) {
    log('WARNING', 'Killswitch already active');
    return;
  }
  
  STATE.isKillswitchActive = true;
  STATE.activeTriggers = triggers;
  STATE.systemStatus = 'EMERGENCY';
  
  log('CRITICAL', 'KILLSWITCH ACTIVATED', { triggers });
  
  // Send notifications
  sendNotification('EMERGENCY', 'KILLSWITCH ACTIVATED', triggers);
  
  // Start liquidation
  startLiquidation(triggers);
}

function deactivateKillswitch(reason) {
  if (!STATE.isKillswitchActive) {
    log('INFO', 'Killswitch already inactive');
    return;
  }
  
  STATE.isKillswitchActive = false;
  STATE.activeTriggers = [];
  STATE.systemStatus = 'NORMAL';
  
  log('INFO', 'Killswitch deactivated', { reason });
  
  sendNotification('INFO', 'Killswitch deactivated', { reason });
}

// Liquidation logic
async function startLiquidation(triggers) {
  if (STATE.liquidationInProgress) {
    log('WARNING', 'Liquidation already in progress');
    return;
  }
  
  STATE.liquidationInProgress = true;
  log('CRITICAL', 'Starting position liquidation');
  
  // Determine liquidation type based on severity
  const hasCriticalTrigger = triggers.some(t => t.severity === 'CRITICAL');
  const liquidationType = hasCriticalTrigger ? 'URGENT' : 'GRACEFUL';
  
  // Sort positions by loss (worst first for urgent, best first for graceful)
  const sortedPositions = [...PORTFOLIO.positions].sort((a, b) => {
    if (liquidationType === 'URGENT') {
      return a.unrealizedPnL - b.unrealizedPnL; // Worst first
    } else {
      return b.unrealizedPnL - a.unrealizedPnL; // Best first
    }
  });
  
  // Liquidate in batches
  for (let i = 0; i < sortedPositions.length; i += CONFIG.liquidation.liquidationBatchSize) {
    const batch = sortedPositions.slice(i, i + CONFIG.liquidation.liquidationBatchSize);
    
    for (const position of batch) {
      await liquidatePosition(position, liquidationType);
    }
    
    // Wait between batches (except for urgent liquidation)
    if (liquidationType !== 'URGENT' && i + CONFIG.liquidation.liquidationBatchSize < sortedPositions.length) {
      await new Promise(resolve => setTimeout(resolve, CONFIG.liquidation.batchIntervalMs));
    }
  }
  
  STATE.liquidationInProgress = false;
  log('INFO', 'Liquidation complete', {
    totalLiquidated: STATE.totalLiquidatedValue,
    positionsLiquidated: STATE.liquidatedPositions.length
  });
}

async function liquidatePosition(position, liquidationType) {
  log('INFO', `Liquidating position: ${position.symbol}`, { position, liquidationType });
  
  // Simulate order execution
  const executionPrice = liquidationType === 'URGENT' 
    ? position.currentPrice * (1 - CONFIG.liquidation.maxSlippageTolerance) // Worst case slippage
    : position.currentPrice * (1 - CONFIG.liquidation.maxSlippageTolerance / 2); // Half slippage
  
  const liquidationValue = position.quantity * executionPrice;
  
  // Record liquidation
  STATE.liquidatedPositions.push({
    symbol: position.symbol,
    quantity: position.quantity,
    executionPrice,
    liquidationValue,
    timestamp: new Date().toISOString(),
    liquidationType
  });
  
  STATE.totalLiquidatedValue += liquidationValue;
  
  // Update portfolio (remove position)
  PORTFOLIO.positions = PORTFOLIO.positions.filter(p => p.symbol !== position.symbol);
  PORTFOLIO.cash += liquidationValue;
  PORTFOLIO.realizedPnL += position.unrealizedPnL;
  
  log('INFO', `Position liquidated: ${position.symbol}`, {
    executionPrice,
    liquidationValue,
    remainingPositions: PORTFOLIO.positions.length
  });
  
  return { success: true, executionPrice, liquidationValue };
}

// Notification system
function sendNotification(level, message, data) {
  const notification = {
    timestamp: new Date().toISOString(),
    level,
    message,
    data,
    systemStatus: STATE.systemStatus
  };
  
  log('INFO', 'Sending notification', notification);
  
  // In production, this would call AWS SNS/SES
  // For now, just log
  console.log('\n📢 NOTIFICATION:', JSON.stringify(notification, null, 2));
}

// Health check
function performHealthCheck() {
  const healthStatus = {
    timestamp: new Date().toISOString(),
    systemStatus: STATE.systemStatus,
    killswitchActive: STATE.isKillswitchActive,
    liquidationInProgress: STATE.liquidationInProgress,
    portfolio: {
      equity: PORTFOLIO.currentEquity,
      dailyPnL: calculateDailyLoss() * PORTFOLIO.startOfDayEquity,
      drawdown: calculateDrawdown(),
      positionCount: PORTFOLIO.positions.length,
      cash: PORTFOLIO.cash
    },
    riskMetrics: {
      dailyLossPercent: calculateDailyLoss(),
      drawdownPercent: calculateDrawdown(),
      positionLosses: calculatePositionLosses()
    }
  };
  
  STATE.lastHealthCheck = healthStatus;
  return healthStatus;
}

// Main monitoring loop
async function monitoringLoop() {
  log('INFO', 'Starting killswitch monitoring loop');
  
  while (true) {
    try {
      // Perform health check
      const health = performHealthCheck();
      
      // Check for killswitch triggers
      const triggers = checkKillswitchTriggers();
      
      if (triggers.length > 0) {
        const criticalTriggers = triggers.filter(t => t.severity === 'CRITICAL');
        
        if (criticalTriggers.length > 0) {
          log('CRITICAL', 'Critical triggers detected', { triggers: criticalTriggers });
          activateKillswitch(criticalTriggers);
        } else {
          log('WARNING', 'Warning triggers detected', { triggers });
          STATE.systemStatus = 'WARNING';
          sendNotification('WARNING', 'Risk thresholds approaching', triggers);
        }
      } else if (!STATE.isKillswitchActive) {
        STATE.systemStatus = 'NORMAL';
      }
      
      // Log status every 10 seconds
      log('DEBUG', 'Monitoring status', {
        systemStatus: STATE.systemStatus,
        equity: PORTFOLIO.currentEquity,
        dailyLoss: calculateDailyLoss(),
        drawdown: calculateDrawdown()
      });
      
    } catch (error) {
      log('ERROR', 'Monitoring loop error', { error: error.message });
      STATE.systemStatus = 'CRITICAL';
      sendNotification('CRITICAL', 'System error in monitoring loop', { error: error.message });
    }
    
    // Wait 1 second before next check
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}

// API endpoints
import http from 'http';

const server = http.createServer((req, res) => {
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Access-Control-Allow-Origin', '*');
  
  if (req.method === 'GET') {
    if (req.url === '/status') {
      res.end(JSON.stringify({
        status: 'ok',
        systemStatus: STATE.systemStatus,
        killswitchActive: STATE.isKillswitchActive,
        liquidationInProgress: STATE.liquidationInProgress,
        activeTriggers: STATE.activeTriggers,
        lastHealthCheck: STATE.lastHealthCheck
      }));
    } else if (req.url === '/health') {
      const health = performHealthCheck();
      res.end(JSON.stringify(health));
    } else if (req.url === '/portfolio') {
      res.end(JSON.stringify(PORTFOLIO));
    } else if (req.url === '/liquidations') {
      res.end(JSON.stringify({
        liquidatedPositions: STATE.liquidatedPositions,
        totalLiquidatedValue: STATE.totalLiquidatedValue
      }));
    } else {
      res.statusCode = 404;
      res.end(JSON.stringify({ error: 'Not found' }));
    }
  } else if (req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const data = JSON.parse(body || '{}');
        
        if (req.url === '/killswitch/activate') {
          activateKillswitch([{ type: 'MANUAL_OVERRIDE', severity: 'CRITICAL', reason: data.reason || 'Manual activation' }]);
          res.end(JSON.stringify({ success: true, message: 'Killswitch activated' }));
        } else if (req.url === '/killswitch/deactivate') {
          deactivateKillswitch(data.reason || 'Manual deactivation');
          res.end(JSON.stringify({ success: true, message: 'Killswitch deactivated' }));
        } else if (req.url === '/simulate/loss') {
          // Simulate a loss for testing
          const lossPercent = data.lossPercent || 0.03;
          PORTFOLIO.currentEquity = PORTFOLIO.startOfDayEquity * (1 - lossPercent);
          res.end(JSON.stringify({ success: true, newEquity: PORTFOLIO.currentEquity }));
        } else {
          res.statusCode = 404;
          res.end(JSON.stringify({ error: 'Not found' }));
        }
      } catch (error) {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: error.message }));
      }
    });
  } else {
    res.statusCode = 405;
    res.end(JSON.stringify({ error: 'Method not allowed' }));
  }
});

const PORT = 8084;

// Main
async function main() {
  console.log('='.repeat(60));
  console.log('자동 킬스위치 및 청산 시스템');
  console.log('Level 6 Critical 요구사항 구현');
  console.log('='.repeat(60));
  
  // Ensure log directory exists
  try {
    fs.mkdirSync('/home/ubuntu/aub-trading-system/logs', { recursive: true });
  } catch (e) {}
  
  // Start API server
  server.listen(PORT, () => {
    console.log(`\n✅ Killswitch API server running on port ${PORT}`);
    console.log(`   - GET /status - System status`);
    console.log(`   - GET /health - Health check`);
    console.log(`   - GET /portfolio - Portfolio state`);
    console.log(`   - GET /liquidations - Liquidation history`);
    console.log(`   - POST /killswitch/activate - Activate killswitch`);
    console.log(`   - POST /killswitch/deactivate - Deactivate killswitch`);
    console.log(`   - POST /simulate/loss - Simulate loss for testing`);
  });
  
  // Start monitoring loop
  monitoringLoop().catch(console.error);
}

main().catch(console.error);
