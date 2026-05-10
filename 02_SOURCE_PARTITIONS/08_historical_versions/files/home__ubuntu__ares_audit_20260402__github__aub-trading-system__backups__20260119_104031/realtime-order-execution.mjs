#!/usr/bin/env node
/**
 * 실시간 주문 실행 및 체결 확인 시스템
 * Level 6 Critical 요구사항 구현
 */

import https from 'https';
import http from 'http';
import fs from 'fs';

// Configuration
const CONFIG = {
  kis: {
    appKey: process.env.KIS_APP_KEY,
    appSecret: process.env.KIS_APP_SECRET,
    accountNo: process.env.KIS_ACCOUNT_NO,
    baseUrl: 'https://openapivts.koreainvestment.com:29443' // 모의투자
  },
  execution: {
    maxRetries: 3,
    retryDelayMs: 1000,
    confirmationTimeoutMs: 30000,
    slippageTolerance: 0.005 // 0.5%
  },
  orderTypes: ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT', 'TWAP', 'VWAP']
};

// State
const STATE = {
  pendingOrders: new Map(),
  executedOrders: [],
  failedOrders: [],
  orderIdCounter: 1,
  accessToken: null,
  tokenExpiry: null
};

// Logging
const LOG_FILE = '/home/ubuntu/aub-trading-system/logs/order-execution.log';

function log(level, message, data = {}) {
  const timestamp = new Date().toISOString();
  const logEntry = { timestamp, level, message, data };
  console.log(`[${timestamp}] [${level}] ${message}`, JSON.stringify(data).substring(0, 200));
  try {
    fs.appendFileSync(LOG_FILE, JSON.stringify(logEntry) + '\n');
  } catch (e) {}
}

// KIS API Authentication
async function getAccessToken() {
  if (STATE.accessToken && STATE.tokenExpiry && new Date() < STATE.tokenExpiry) {
    return STATE.accessToken;
  }
  
  log('INFO', 'Requesting new KIS access token');
  
  return new Promise((resolve, reject) => {
    const data = JSON.stringify({
      grant_type: 'client_credentials',
      appkey: CONFIG.kis.appKey,
      appsecret: CONFIG.kis.appSecret
    });
    
    const url = new URL('/oauth2/tokenP', CONFIG.kis.baseUrl);
    const options = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(data)
      }
    };
    
    const req = https.request(options, (res) => {
      let body = '';
      res.on('data', chunk => body += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(body);
          if (result.access_token) {
            STATE.accessToken = result.access_token;
            STATE.tokenExpiry = new Date(Date.now() + (result.expires_in - 60) * 1000);
            log('INFO', 'KIS access token obtained');
            resolve(STATE.accessToken);
          } else {
            reject(new Error('Failed to get access token'));
          }
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

// Order class
class Order {
  constructor(params) {
    this.orderId = `ORD-${Date.now()}-${STATE.orderIdCounter++}`;
    this.symbol = params.symbol;
    this.side = params.side; // BUY or SELL
    this.quantity = params.quantity;
    this.orderType = params.orderType || 'MARKET';
    this.limitPrice = params.limitPrice;
    this.stopPrice = params.stopPrice;
    this.status = 'PENDING';
    this.createdAt = new Date().toISOString();
    this.executedAt = null;
    this.executedPrice = null;
    this.executedQuantity = 0;
    this.remainingQuantity = params.quantity;
    this.fills = [];
    this.retryCount = 0;
    this.brokerOrderId = null;
    this.errorMessage = null;
  }
}

// Order execution
async function submitOrder(order) {
  log('INFO', `Submitting order: ${order.orderId}`, {
    symbol: order.symbol,
    side: order.side,
    quantity: order.quantity,
    orderType: order.orderType
  });
  
  STATE.pendingOrders.set(order.orderId, order);
  
  try {
    const token = await getAccessToken();
    
    // KIS API 해외주식 주문
    const result = await executeKISOrder(order, token);
    
    if (result.success) {
      order.status = 'SUBMITTED';
      order.brokerOrderId = result.brokerOrderId;
      log('INFO', `Order submitted: ${order.orderId}`, { brokerOrderId: result.brokerOrderId });
      
      // Start confirmation monitoring
      monitorOrderConfirmation(order);
      
      return { success: true, orderId: order.orderId, brokerOrderId: result.brokerOrderId };
    } else {
      throw new Error(result.error || 'Order submission failed');
    }
  } catch (error) {
    log('ERROR', `Order submission failed: ${order.orderId}`, { error: error.message });
    
    // Retry logic
    if (order.retryCount < CONFIG.execution.maxRetries) {
      order.retryCount++;
      log('INFO', `Retrying order: ${order.orderId} (attempt ${order.retryCount})`);
      await new Promise(resolve => setTimeout(resolve, CONFIG.execution.retryDelayMs));
      return submitOrder(order);
    }
    
    order.status = 'FAILED';
    order.errorMessage = error.message;
    STATE.pendingOrders.delete(order.orderId);
    STATE.failedOrders.push(order);
    
    return { success: false, orderId: order.orderId, error: error.message };
  }
}

async function executeKISOrder(order, token) {
  // Simulated KIS order execution
  // In production, this would call the actual KIS API
  
  return new Promise((resolve) => {
    setTimeout(() => {
      // Simulate 95% success rate
      if (Math.random() < 0.95) {
        resolve({
          success: true,
          brokerOrderId: `KIS-${Date.now()}`
        });
      } else {
        resolve({
          success: false,
          error: 'Simulated order rejection'
        });
      }
    }, 100);
  });
}

// Order confirmation monitoring
async function monitorOrderConfirmation(order) {
  const startTime = Date.now();
  
  while (order.status === 'SUBMITTED' && Date.now() - startTime < CONFIG.execution.confirmationTimeoutMs) {
    try {
      const confirmation = await checkOrderStatus(order);
      
      if (confirmation.status === 'FILLED') {
        order.status = 'FILLED';
        order.executedAt = new Date().toISOString();
        order.executedPrice = confirmation.avgPrice;
        order.executedQuantity = confirmation.filledQuantity;
        order.remainingQuantity = 0;
        order.fills = confirmation.fills;
        
        STATE.pendingOrders.delete(order.orderId);
        STATE.executedOrders.push(order);
        
        log('INFO', `Order filled: ${order.orderId}`, {
          executedPrice: order.executedPrice,
          executedQuantity: order.executedQuantity
        });
        
        // Send notification
        sendOrderNotification(order);
        return;
      } else if (confirmation.status === 'PARTIAL') {
        order.status = 'PARTIAL';
        order.executedQuantity = confirmation.filledQuantity;
        order.remainingQuantity = order.quantity - confirmation.filledQuantity;
        order.fills = confirmation.fills;
        
        log('INFO', `Order partially filled: ${order.orderId}`, {
          filledQuantity: confirmation.filledQuantity,
          remainingQuantity: order.remainingQuantity
        });
      } else if (confirmation.status === 'REJECTED') {
        order.status = 'REJECTED';
        order.errorMessage = confirmation.reason;
        
        STATE.pendingOrders.delete(order.orderId);
        STATE.failedOrders.push(order);
        
        log('ERROR', `Order rejected: ${order.orderId}`, { reason: confirmation.reason });
        return;
      }
      
      await new Promise(resolve => setTimeout(resolve, 500));
    } catch (error) {
      log('ERROR', `Error checking order status: ${order.orderId}`, { error: error.message });
    }
  }
  
  // Timeout handling
  if (order.status === 'SUBMITTED') {
    log('WARNING', `Order confirmation timeout: ${order.orderId}`);
    order.status = 'TIMEOUT';
    STATE.pendingOrders.delete(order.orderId);
    STATE.failedOrders.push(order);
  }
}

async function checkOrderStatus(order) {
  // Simulated order status check
  // In production, this would call the actual KIS API
  
  return new Promise((resolve) => {
    setTimeout(() => {
      // Simulate order fill
      const avgPrice = order.limitPrice || (100 + Math.random() * 10);
      resolve({
        status: 'FILLED',
        avgPrice,
        filledQuantity: order.quantity,
        fills: [{
          price: avgPrice,
          quantity: order.quantity,
          timestamp: new Date().toISOString()
        }]
      });
    }, 200);
  });
}

function sendOrderNotification(order) {
  log('INFO', 'Order notification sent', {
    orderId: order.orderId,
    symbol: order.symbol,
    side: order.side,
    executedPrice: order.executedPrice,
    executedQuantity: order.executedQuantity
  });
}

// TWAP/VWAP execution
async function executeTWAP(symbol, side, totalQuantity, durationMinutes, slices = 10) {
  log('INFO', `Starting TWAP execution`, { symbol, side, totalQuantity, durationMinutes, slices });
  
  const sliceQuantity = Math.floor(totalQuantity / slices);
  const intervalMs = (durationMinutes * 60 * 1000) / slices;
  const orders = [];
  
  for (let i = 0; i < slices; i++) {
    const quantity = i === slices - 1 
      ? totalQuantity - (sliceQuantity * (slices - 1)) // Last slice gets remainder
      : sliceQuantity;
    
    const order = new Order({
      symbol,
      side,
      quantity,
      orderType: 'MARKET'
    });
    
    const result = await submitOrder(order);
    orders.push({ slice: i + 1, order, result });
    
    if (i < slices - 1) {
      await new Promise(resolve => setTimeout(resolve, intervalMs));
    }
  }
  
  return {
    type: 'TWAP',
    symbol,
    side,
    totalQuantity,
    durationMinutes,
    slices,
    orders,
    completedAt: new Date().toISOString()
  };
}

async function executeVWAP(symbol, side, totalQuantity, volumeProfile) {
  log('INFO', `Starting VWAP execution`, { symbol, side, totalQuantity });
  
  // Default volume profile (higher at open and close)
  const profile = volumeProfile || [0.15, 0.10, 0.08, 0.07, 0.07, 0.06, 0.07, 0.08, 0.12, 0.20];
  const orders = [];
  
  for (let i = 0; i < profile.length; i++) {
    const quantity = Math.round(totalQuantity * profile[i]);
    
    if (quantity > 0) {
      const order = new Order({
        symbol,
        side,
        quantity,
        orderType: 'MARKET'
      });
      
      const result = await submitOrder(order);
      orders.push({ slice: i + 1, volumeWeight: profile[i], order, result });
    }
    
    // Small delay between slices
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  
  return {
    type: 'VWAP',
    symbol,
    side,
    totalQuantity,
    volumeProfile: profile,
    orders,
    completedAt: new Date().toISOString()
  };
}

// API Server
const server = http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') {
    res.statusCode = 200;
    res.end();
    return;
  }
  
  if (req.method === 'GET') {
    if (req.url === '/health') {
      res.end(JSON.stringify({
        status: 'healthy',
        service: 'order-execution',
        pendingOrders: STATE.pendingOrders.size,
        executedOrders: STATE.executedOrders.length,
        failedOrders: STATE.failedOrders.length,
        hasToken: !!STATE.accessToken
      }));
      return;
    }
    if (req.url === '/status') {
      res.end(JSON.stringify({
        status: 'ok',
        pendingOrders: STATE.pendingOrders.size,
        executedOrders: STATE.executedOrders.length,
        failedOrders: STATE.failedOrders.length,
        hasToken: !!STATE.accessToken
      }));
    } else if (req.url === '/orders/pending') {
      res.end(JSON.stringify(Array.from(STATE.pendingOrders.values())));
    } else if (req.url === '/orders/executed') {
      res.end(JSON.stringify(STATE.executedOrders.slice(-100)));
    } else if (req.url === '/orders/failed') {
      res.end(JSON.stringify(STATE.failedOrders.slice(-100)));
    } else {
      res.statusCode = 404;
      res.end(JSON.stringify({ error: 'Not found' }));
    }
  } else if (req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', async () => {
      try {
        const data = JSON.parse(body || '{}');
        
        if (req.url === '/order') {
          const order = new Order(data);
          const result = await submitOrder(order);
          res.end(JSON.stringify(result));
        } else if (req.url === '/order/twap') {
          const result = await executeTWAP(
            data.symbol,
            data.side,
            data.quantity,
            data.durationMinutes || 30,
            data.slices || 10
          );
          res.end(JSON.stringify(result));
        } else if (req.url === '/order/vwap') {
          const result = await executeVWAP(
            data.symbol,
            data.side,
            data.quantity,
            data.volumeProfile
          );
          res.end(JSON.stringify(result));
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

const PORT = 8085;

// Main
async function main() {
  console.log('='.repeat(60));
  console.log('실시간 주문 실행 및 체결 확인 시스템');
  console.log('Level 6 Critical 요구사항 구현');
  console.log('='.repeat(60));
  
  // Ensure log directory exists
  try {
    fs.mkdirSync('/home/ubuntu/aub-trading-system/logs', { recursive: true });
  } catch (e) {}
  
  // Start API server
  server.listen(PORT, () => {
    console.log(`\n✅ Order Execution API server running on port ${PORT}`);
    console.log(`   - GET /status - System status`);
    console.log(`   - GET /orders/pending - Pending orders`);
    console.log(`   - GET /orders/executed - Executed orders`);
    console.log(`   - GET /orders/failed - Failed orders`);
    console.log(`   - POST /order - Submit single order`);
    console.log(`   - POST /order/twap - Execute TWAP order`);
    console.log(`   - POST /order/vwap - Execute VWAP order`);
  });
  
  // Test order execution
  console.log('\n--- Testing Order Execution ---');
  
  const testOrder = new Order({
    symbol: 'AAPL',
    side: 'BUY',
    quantity: 100,
    orderType: 'MARKET'
  });
  
  const result = await submitOrder(testOrder);
  console.log('Test order result:', result);
  
  // Wait for confirmation
  await new Promise(resolve => setTimeout(resolve, 2000));
  
  console.log('\n--- Order Execution System Ready ---');
}

main().catch(console.error);
