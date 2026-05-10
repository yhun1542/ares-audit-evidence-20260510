#!/usr/bin/env python3
"""
실시간 스트리밍 서버
Redis Stream 구독 + WebSocket/SSE 브로드캐스트

대시보드 클라이언트에 실시간 데이터 제공

Author: Manus AI
Date: 2025-01-20
"""

import os
import json
import asyncio
import redis
from datetime import datetime
from typing import Set, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import logging

# ============================================================
# 로깅 설정
# ============================================================
os.makedirs('/home/ubuntu/logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/home/ubuntu/logs/realtime_streaming.log')
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 설정
# ============================================================
CONFIG = {
    'redis_host': 'localhost',
    'redis_port': 6379,
    'stream_name': 'events:all',
    'server_host': '0.0.0.0',
    'server_port': 9090,
}

# ============================================================
# FastAPI 앱
# ============================================================
app = FastAPI(
    title="AUB Realtime Streaming Server",
    description="실시간 데이터 스트리밍 서버",
    version="1.0.0"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 연결 관리자
# ============================================================
class ConnectionManager:
    def __init__(self):
        self.active_websockets: Set[WebSocket] = set()
        self.sse_queues: Dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()
    
    async def connect_websocket(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_websockets.add(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_websockets)}")
    
    async def disconnect_websocket(self, websocket: WebSocket):
        async with self._lock:
            self.active_websockets.discard(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.active_websockets)}")
    
    def create_sse_queue(self, client_id: str) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=100)
        self.sse_queues[client_id] = queue
        logger.info(f"SSE client connected: {client_id}. Total: {len(self.sse_queues)}")
        return queue
    
    def remove_sse_queue(self, client_id: str):
        if client_id in self.sse_queues:
            del self.sse_queues[client_id]
        logger.info(f"SSE client disconnected: {client_id}. Total: {len(self.sse_queues)}")
    
    async def broadcast(self, message: dict):
        """모든 클라이언트에 브로드캐스트"""
        message_str = json.dumps(message)
        
        # WebSocket 브로드캐스트
        disconnected = set()
        for websocket in self.active_websockets:
            try:
                await websocket.send_text(message_str)
            except Exception:
                disconnected.add(websocket)
        
        # 연결 해제된 WebSocket 정리
        for ws in disconnected:
            await self.disconnect_websocket(ws)
        
        # SSE 브로드캐스트
        for client_id, queue in list(self.sse_queues.items()):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # 큐가 가득 차면 오래된 메시지 제거
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except:
                    pass

manager = ConnectionManager()

# ============================================================
# Redis Stream 리더
# ============================================================
class RedisStreamReader:
    def __init__(self):
        self.redis = redis.Redis(
            host=CONFIG['redis_host'],
            port=CONFIG['redis_port'],
            decode_responses=True
        )
        self.stream_name = CONFIG['stream_name']
        self.last_id = '$'  # 새 메시지만 읽기
        self.running = False
    
    async def start(self):
        """스트림 읽기 시작"""
        self.running = True
        logger.info(f"Starting Redis Stream reader: {self.stream_name}")
        
        while self.running:
            try:
                # 블로킹 읽기 (1초 타임아웃)
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.redis.xread(
                        {self.stream_name: self.last_id},
                        count=100,
                        block=1000
                    )
                )
                
                if result:
                    for stream_name, messages in result:
                        for message_id, fields in messages:
                            self.last_id = message_id
                            await self._process_message(fields)
            
            except Exception as e:
                logger.error(f"Redis Stream read error: {e}")
                await asyncio.sleep(1)
    
    async def _process_message(self, fields: dict):
        """메시지 처리 및 브로드캐스트"""
        try:
            event_type = fields.get('type', 'UNKNOWN')
            data_str = fields.get('data', '{}')
            timestamp = fields.get('timestamp', datetime.now().isoformat())
            
            # 관심 있는 이벤트만 브로드캐스트
            if event_type in ['PRICE_UPDATE', 'WS_TRADE', 'WS_QUOTE', 'WS_AGGREGATE', 
                             'MACRO_UPDATE', 'SENTIMENT_UPDATE', 'NEWS_UPDATE']:
                message = {
                    'type': event_type,
                    'data': json.loads(data_str) if data_str else {},
                    'timestamp': timestamp,
                }
                await manager.broadcast(message)
        
        except Exception as e:
            logger.error(f"Message processing error: {e}")
    
    def stop(self):
        self.running = False

stream_reader = RedisStreamReader()

# ============================================================
# API 엔드포인트
# ============================================================
@app.on_event("startup")
async def startup_event():
    """서버 시작 시 Redis Stream 리더 시작"""
    asyncio.create_task(stream_reader.start())
    logger.info("🚀 Realtime Streaming Server started")

@app.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 정리"""
    stream_reader.stop()
    logger.info("Realtime Streaming Server stopped")

@app.get("/")
async def root():
    """헬스 체크"""
    return {
        "status": "ok",
        "service": "AUB Realtime Streaming Server",
        "version": "1.0.0",
        "websocket_clients": len(manager.active_websockets),
        "sse_clients": len(manager.sse_queues),
    }

@app.get("/health")
async def health():
    """상세 헬스 체크"""
    try:
        # Redis 연결 확인
        redis_client = redis.Redis(
            host=CONFIG['redis_host'],
            port=CONFIG['redis_port'],
            decode_responses=True
        )
        redis_client.ping()
        redis_status = "connected"
    except:
        redis_status = "disconnected"
    
    return {
        "status": "healthy" if redis_status == "connected" else "degraded",
        "redis": redis_status,
        "websocket_clients": len(manager.active_websockets),
        "sse_clients": len(manager.sse_queues),
        "timestamp": datetime.now().isoformat(),
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 엔드포인트"""
    await manager.connect_websocket(websocket)
    
    try:
        while True:
            # 클라이언트로부터 메시지 수신 (구독 필터 등)
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
                
                # ping/pong 처리
                if message.get('type') == 'ping':
                    await websocket.send_text(json.dumps({'type': 'pong', 'timestamp': datetime.now().isoformat()}))
                
                # 구독 요청 처리
                elif message.get('type') == 'subscribe':
                    symbols = message.get('symbols', [])
                    await websocket.send_text(json.dumps({
                        'type': 'subscribed',
                        'symbols': symbols,
                        'timestamp': datetime.now().isoformat()
                    }))
            
            except json.JSONDecodeError:
                pass
    
    except WebSocketDisconnect:
        await manager.disconnect_websocket(websocket)

@app.get("/sse")
async def sse_endpoint(request: Request):
    """Server-Sent Events 엔드포인트"""
    client_id = f"sse_{datetime.now().timestamp()}"
    queue = manager.create_sse_queue(client_id)
    
    async def event_generator():
        try:
            while True:
                # 연결 확인
                if await request.is_disconnected():
                    break
                
                try:
                    # 메시지 대기 (1초 타임아웃)
                    message = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield f"data: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    # 하트비트
                    yield f": heartbeat\n\n"
        finally:
            manager.remove_sse_queue(client_id)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@app.get("/api/prices")
async def get_prices():
    """현재 가격 데이터 조회"""
    try:
        redis_client = redis.Redis(
            host=CONFIG['redis_host'],
            port=CONFIG['redis_port'],
            decode_responses=True
        )
        
        prices = {}
        keys = redis_client.keys('price:*')
        
        for key in keys:
            symbol = key.split(':')[1]
            data = redis_client.get(key)
            if data:
                try:
                    prices[symbol] = json.loads(data)
                except:
                    prices[symbol] = {'price': float(data)}
        
        return {"prices": prices, "count": len(prices)}
    
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/macro")
async def get_macro():
    """매크로 지표 조회"""
    try:
        redis_client = redis.Redis(
            host=CONFIG['redis_host'],
            port=CONFIG['redis_port'],
            decode_responses=True
        )
        
        data = redis_client.get('macro:indicators')
        if data:
            return {"indicators": json.loads(data)}
        return {"indicators": {}}
    
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/sentiment/{symbol}")
async def get_sentiment(symbol: str):
    """감성 분석 결과 조회"""
    try:
        redis_client = redis.Redis(
            host=CONFIG['redis_host'],
            port=CONFIG['redis_port'],
            decode_responses=True
        )
        
        data = redis_client.get(f'sentiment:{symbol}')
        if data:
            return json.loads(data)
        return {"error": "Not found"}
    
    except Exception as e:
        return {"error": str(e)}

# ============================================================
# 메인 실행
# ============================================================
if __name__ == '__main__':
    uvicorn.run(
        app,
        host=CONFIG['server_host'],
        port=CONFIG['server_port'],
        log_level="info"
    )
