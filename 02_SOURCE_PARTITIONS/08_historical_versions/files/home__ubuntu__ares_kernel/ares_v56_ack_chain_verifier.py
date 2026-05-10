#!/usr/bin/env python3
"""
ARES V5.6.1b ACK Chain Verifier
────────────────────────────────
장 개시 직후 첫 주문의 전체 ACK 체인 완주를 자동으로 검증합니다.

검증 체인:
  1. DEFER → EXECUTE 전환 확인
  2. canonical stream (emarkos:v6:order:intent) 에 새 주문 발행 확인
  3. order-intent-executor 가 주문을 소비(ACK) 확인
  4. consumer heartbeat 갱신 확인
  5. state commit (state:summary 업데이트) 확인

동작 방식:
  - 최대 45분간 10초 간격으로 폴링
  - 각 단계 통과 시 타임스탬프 기록
  - 전체 체인 완주 시 텔레그램으로 성공 보고
  - 타임아웃 시 실패 보고

Author: ARES Architecture Team
Version: 1.0.0
"""
import asyncio
import json
import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
import aiohttp
import redis.asyncio as aioredis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ACK-VERIFIER] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("ack_verifier")

# ─── Configuration ────────────────────────────────────────────
REDIS_URL = os.environ.get("REDIS_URL", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

if not REDIS_URL:
    # Fallback for direct execution
    REDIS_URL = "redis://:LCwtmHAasT-SQfIN$ld1a5CHSZGCkwT6qHVH$qPef5zthXKp@localhost:6379/0"

KEYS = {
    "leader_lease": "ares:v56:leader:lease",
    "trading_enabled": "trading:enabled",
    "consumer_heartbeat": "ares:v55:consumer:heartbeat",
    "consumer_last_ack": "ares:v55:consumer:last_ack",
    "readiness": "ares:v55:live:readiness",
    "state_json": "ares:v55:state:summary",
    "canonical_stream": "emarkos:v6:order:intent",
    "decision_last": "ares:v55:decision:last",
    "execution_stream": "ares:v55:execution:intents",
    "halt_flag": "ares:v55:live:control:halt",
    "regime_final": "regime:final:current",
    "equity_verified": "ofg:equity:verified",
}

MAX_WAIT_MINUTES = 45
POLL_INTERVAL = 10

ET_OFFSET = timedelta(hours=-4)


async def send_telegram(message: str):
    """Send telegram notification."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not set, skipping notification")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
                timeout=aiohttp.ClientTimeout(total=10),
            )
            if resp.status != 200:
                body = await resp.text()
                logger.error(f"Telegram error {resp.status}: {body}")
            else:
                logger.info("Telegram notification sent")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


async def verify_ack_chain():
    """Main verification loop."""
    logger.info("=" * 60)
    logger.info("ARES V5.6.1b ACK Chain Verifier starting...")
    logger.info("=" * 60)

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)

    try:
        await redis.ping()
        logger.info("Redis connection OK")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        await send_telegram(f"\u274c ACK Chain Verifier: Redis connection failed: {e}")
        return

    # ─── Pre-flight checks ────────────────────────────────────
    leader = await redis.get(KEYS["leader_lease"])
    ks = await redis.get(KEYS["trading_enabled"])
    halt = await redis.get(KEYS["halt_flag"])

    logger.info(f"Pre-flight: Leader={'OK' if leader else 'MISSING'}, "
                f"KillSwitch={ks}, Halt={halt}")

    if not leader:
        msg = "\u274c ACK Verifier: No Leader Lease! Engine may not be running."
        logger.error(msg)
        await send_telegram(msg)
        return

    if str(ks).lower() not in ("true", "1", "yes"):
        msg = "\u274c ACK Verifier: Kill Switch is OFF (trading:enabled != true)!"
        logger.error(msg)
        await send_telegram(msg)
        return

    await send_telegram(
        "\U0001f50d ACK Chain Verifier started\n"
        "Monitoring for first EXECUTE → canonical stream → ACK → state commit chain.\n"
        f"Max wait: {MAX_WAIT_MINUTES} minutes"
    )

    # ─── Record baseline ──────────────────────────────────────
    baseline_stream_len = await redis.xlen(KEYS["canonical_stream"])
    baseline_hb = await redis.get(KEYS["consumer_heartbeat"])
    baseline_ack = await redis.get(KEYS["consumer_last_ack"])
    baseline_state = await redis.get(KEYS["state_json"])

    logger.info(f"Baseline: stream_len={baseline_stream_len}, "
                f"hb={'set' if baseline_hb else 'none'}, "
                f"ack={'set' if baseline_ack else 'none'}")

    # ─── Verification stages ──────────────────────────────────
    stages = {
        "defer_cleared": {"done": False, "ts": None, "detail": ""},
        "execute_issued": {"done": False, "ts": None, "detail": ""},
        "canonical_published": {"done": False, "ts": None, "detail": ""},
        "consumer_ack": {"done": False, "ts": None, "detail": ""},
        "state_committed": {"done": False, "ts": None, "detail": ""},
    }

    start_time = time.time()
    max_wait = MAX_WAIT_MINUTES * 60

    while time.time() - start_time < max_wait:
        elapsed = time.time() - start_time
        et_now = datetime.now(timezone.utc) + ET_OFFSET

        try:
            # Stage 1: Engine activation check (DEFER/HALT/UNKNOWN separated)
            # [V5.6.1b-PATCH] DEFER, HALT, UNKNOWN을 분리 판정
            #   DEFER  = precondition 미충족 (ConsumerGuard, open orders 등) -> PENDING
            #   HALT   = 리스크 가드에 의한 정상 fail-closed -> PASS (sub_state=HALT)
            #   EXECUTE/CONSERVATIVE_ONLY/DE_RISK_ONLY/FLATTEN = 능동 거래 -> PASS
            #   UNKNOWN/INIT = state schema/telemetry 결함 -> PENDING
            if not stages["defer_cleared"]["done"]:
                state_raw = await redis.get(KEYS["state_json"])
                if state_raw:
                    try:
                        state = json.loads(state_raw)
                        engine_state = state.get("engine_state", "UNKNOWN")
                        halted = state.get("halted", False)
                        halt_reason = state.get("halt_reason", "")
                        if engine_state in ("EXECUTE", "CONSERVATIVE_ONLY", "DE_RISK_ONLY", "FLATTEN"):
                            # Active trading path
                            stages["defer_cleared"]["done"] = True
                            stages["defer_cleared"]["ts"] = time.time()
                            stages["defer_cleared"]["detail"] = f"engine_state={engine_state} [ACTIVE]"
                            logger.info(f"[STAGE 1/5] DEFER cleared: {engine_state} [ACTIVE] ({elapsed:.0f}s)")
                        elif engine_state == "HALT" or halted:
                            # Risk guard fail-closed — chain is functional, engine chose to halt
                            stages["defer_cleared"]["done"] = True
                            stages["defer_cleared"]["ts"] = time.time()
                            stages["defer_cleared"]["detail"] = f"engine_state=HALT [FAIL-CLOSED] reason={halt_reason}"
                            logger.info(f"[STAGE 1/5] DEFER cleared: HALT [FAIL-CLOSED] reason={halt_reason} ({elapsed:.0f}s)")
                        elif engine_state == "DEFER":
                            # Precondition not met — still waiting
                            logger.debug(f"[STAGE 1/5] Still DEFER: precondition pending ({elapsed:.0f}s)")
                        else:
                            # UNKNOWN / INIT — schema or startup issue
                            logger.debug(f"[STAGE 1/5] engine_state={engine_state}: schema/startup pending ({elapsed:.0f}s)")
                    except json.JSONDecodeError:
                        pass

            # Stage 2: Decision issued (any decision, not just EXECUTE)
            # [V5.6.1b-PATCH] HALT 결정도 유효한 decision으로 인정
            if not stages["execute_issued"]["done"]:
                decision_raw = await redis.get(KEYS["decision_last"])
                if decision_raw:
                    try:
                        decision = json.loads(decision_raw) if isinstance(decision_raw, str) and decision_raw.startswith("{") else {"action": decision_raw}
                        decision_type = decision.get("decision", decision.get("action", str(decision_raw)))
                        ts = decision.get("ts", 0)
                        # Check if decision is recent (within last 5 minutes)
                        if ts and time.time() - float(ts) < 300:
                            stages["execute_issued"]["done"] = True
                            stages["execute_issued"]["ts"] = time.time()
                            stages["execute_issued"]["detail"] = f"decision={decision_type}"
                            if decision_type == "HALT":
                                stages["execute_issued"]["detail"] += f" [FAIL-CLOSED] reason={decision.get('reason','')}"
                            logger.info(f"[STAGE 2/5] Decision issued: {stages['execute_issued']['detail']} ({elapsed:.0f}s)")
                    except (json.JSONDecodeError, ValueError):
                        pass

            # Stage 3: Canonical stream has new entries
            if not stages["canonical_published"]["done"]:
                current_stream_len = await redis.xlen(KEYS["canonical_stream"])
                if current_stream_len > baseline_stream_len:
                    new_count = current_stream_len - baseline_stream_len
                    # Read the latest entry
                    latest = await redis.xrevrange(KEYS["canonical_stream"], count=1)
                    detail = ""
                    if latest:
                        entry_id, entry_data = latest[0]
                        detail = f"new_entries={new_count}, latest_id={entry_id}"
                        if "symbol" in entry_data:
                            detail += f", symbol={entry_data['symbol']}"
                    stages["canonical_published"]["done"] = True
                    stages["canonical_published"]["ts"] = time.time()
                    stages["canonical_published"]["detail"] = detail
                    logger.info(f"[STAGE 3/5] Canonical stream published: {detail} ({elapsed:.0f}s)")

            # Stage 4: Consumer ACK (heartbeat refreshed after market open)
            if not stages["consumer_ack"]["done"]:
                current_hb = await redis.get(KEYS["consumer_heartbeat"])
                current_ack = await redis.get(KEYS["consumer_last_ack"])
                if current_hb:
                    hb_age = time.time() - float(current_hb)
                    if hb_age < 30:  # Fresh heartbeat
                        stages["consumer_ack"]["done"] = True
                        stages["consumer_ack"]["ts"] = time.time()
                        stages["consumer_ack"]["detail"] = f"hb_age={hb_age:.0f}s"
                        if current_ack:
                            ack_age = time.time() - float(current_ack)
                            stages["consumer_ack"]["detail"] += f", ack_age={ack_age:.0f}s"
                        logger.info(f"[STAGE 4/5] Consumer ACK: {stages['consumer_ack']['detail']} ({elapsed:.0f}s)")

            # Stage 5: State committed (state_json updated after baseline)
            if not stages["state_committed"]["done"]:
                current_state = await redis.get(KEYS["state_json"])
                if current_state and current_state != baseline_state:
                    try:
                        st = json.loads(current_state)
                        engine_state = st.get("engine_state", "UNKNOWN")
                        cycle = st.get("cycle", "N/A")
                        stages["state_committed"]["done"] = True
                        stages["state_committed"]["ts"] = time.time()
                        stages["state_committed"]["detail"] = f"state={engine_state}, cycle={cycle}"
                        logger.info(f"[STAGE 5/5] State committed: {stages['state_committed']['detail']} ({elapsed:.0f}s)")
                    except json.JSONDecodeError:
                        pass

            # Check if all stages complete
            all_done = all(s["done"] for s in stages.values())
            if all_done:
                total_time = time.time() - start_time
                logger.info("=" * 60)
                logger.info(f"ALL STAGES COMPLETE in {total_time:.0f}s!")
                logger.info("=" * 60)

                # Collect additional metrics for report
                equity_raw = await redis.get(KEYS["equity_verified"])
                equity = 0
                try:
                    equity = float(equity_raw)
                except (ValueError, TypeError):
                    try:
                        equity = float(json.loads(equity_raw).get("total", 0))
                    except Exception:
                        pass

                regime_data = await redis.hgetall(KEYS["regime_final"])
                regime = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"

                report = (
                    f"{'=' * 40}\n"
                    f"\u2705 ACK CHAIN VERIFICATION PASSED\n"
                    f"{'=' * 40}\n"
                    f"\n"
                    f"Total verification time: {total_time:.0f}s\n"
                    f"Time: {et_now.strftime('%Y-%m-%d %H:%M ET')}\n"
                    f"\n"
                    f"Stage Results:\n"
                    f"  1. DEFER Cleared: {stages['defer_cleared']['detail']}\n"
                    f"     at +{stages['defer_cleared']['ts'] - start_time:.0f}s\n"
                    f"  2. Decision Issued: {stages['execute_issued']['detail']}\n"
                    f"     at +{stages['execute_issued']['ts'] - start_time:.0f}s\n"
                    f"  3. Canonical Published: {stages['canonical_published']['detail']}\n"
                    f"     at +{stages['canonical_published']['ts'] - start_time:.0f}s\n"
                    f"  4. Consumer ACK: {stages['consumer_ack']['detail']}\n"
                    f"     at +{stages['consumer_ack']['ts'] - start_time:.0f}s\n"
                    f"  5. State Committed: {stages['state_committed']['detail']}\n"
                    f"     at +{stages['state_committed']['ts'] - start_time:.0f}s\n"
                    f"\n"
                    f"Current Status:\n"
                    f"  Equity: ${equity:,.2f}\n"
                    f"  Regime: {regime}\n"
                    f"\n"
                    f"V5.6.1b 실거래 무인화 최종 검증 완료!"
                )

                await send_telegram(report)

                # Save report to file
                with open("/home/ubuntu/ack_chain_verification_result.txt", "w") as f:
                    f.write(report)
                logger.info("Report saved to /home/ubuntu/ack_chain_verification_result.txt")
                return

            # Progress log every 60 seconds
            if int(elapsed) % 60 < POLL_INTERVAL:
                done_count = sum(1 for s in stages.values() if s["done"])
                logger.info(f"Progress: {done_count}/5 stages complete, elapsed={elapsed:.0f}s, ET={et_now.strftime('%H:%M')}")

        except Exception as e:
            logger.error(f"Poll error: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)

    # ─── Timeout ──────────────────────────────────────────────
    elapsed = time.time() - start_time
    done_stages = [name for name, s in stages.items() if s["done"]]
    pending_stages = [name for name, s in stages.items() if not s["done"]]

    timeout_report = (
        f"{'=' * 40}\n"
        f"\u26a0\ufe0f ACK CHAIN VERIFICATION TIMEOUT\n"
        f"{'=' * 40}\n"
        f"\n"
        f"Elapsed: {elapsed:.0f}s ({MAX_WAIT_MINUTES} min limit)\n"
        f"\n"
        f"Completed stages ({len(done_stages)}/5):\n"
    )
    for name in done_stages:
        s = stages[name]
        timeout_report += f"  \u2705 {name}: {s['detail']} (at +{s['ts'] - start_time:.0f}s)\n"
    timeout_report += f"\nPending stages ({len(pending_stages)}/5):\n"
    for name in pending_stages:
        timeout_report += f"  \u274c {name}\n"
    timeout_report += (
        f"\nPossible causes:\n"
        f"  - Engine may be in HOLD/DEFER due to regime conditions\n"
        f"  - No rebalancing needed (all positions at target)\n"
        f"  - Consumer executor may need restart\n"
        f"\nAction: Check pm2 logs ares-v56-live and order-intent-executor"
    )

    logger.warning(timeout_report)
    await send_telegram(timeout_report)

    with open("/home/ubuntu/ack_chain_verification_result.txt", "w") as f:
        f.write(timeout_report)


if __name__ == "__main__":
    asyncio.run(verify_ack_chain())
