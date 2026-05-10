"""
ARES v3.0 - Feedback Loop Daemon
==================================
Background service that runs the AI Regime Feedback Loop.
Integrates: accuracy scoring, auto-tuning, flapping detection,
VIXY correlation, performance metrics collection.

Runs as PM2 process alongside kernel_daemon and monitor_daemon.
"""
from __future__ import annotations
import json, time, os, sys, logging, traceback
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ares.feedback.daemon")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

try:
    import redis
    r = redis.from_url(REDIS_URL, decode_responses=False)
    r.ping()
    logger.info("Redis connected: %s", REDIS_URL)
except Exception as e:
    logger.error("Redis connection failed: %s", e)
    sys.exit(1)

# Import feedback loop components
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from regime_feedback_loop import (
    RegimeFeedbackLoop, RegimePrediction, PerformanceMetrics, FeedbackKeys
)
from kernel_rebalance_controller import KernelRebalanceController, RebalKeys

feedback = RegimeFeedbackLoop(r)
rebal_ctrl = KernelRebalanceController(r)

# ── Helper: Read regime from Redis ──

def _get_current_regime():
    """Read current regime from Redis multi-source keys."""
    try:
        # Try regime:final:current first
        raw = r.hgetall("regime:final:current")
        if raw:
            data = {}
            for k, v in raw.items():
                kk = k.decode() if isinstance(k, bytes) else k
                vv = v.decode() if isinstance(v, bytes) else v
                data[kk] = vv
            return data

        # Fallback: individual keys
        ms_regime = r.get("realtime:regime:ms_regime")
        ms_state = r.get("realtime:regime:ms_state")
        r15_state = r.get("realtime:regime:r15_state")
        return {
            "ms_regime": (ms_regime.decode() if ms_regime else "UNKNOWN"),
            "ms_state": (ms_state.decode() if ms_state else "UNKNOWN"),
            "r15_state": (r15_state.decode() if r15_state else "UNKNOWN"),
        }
    except Exception as e:
        logger.error("Failed to read regime: %s", e)
        return {"ms_regime": "UNKNOWN", "ms_state": "UNKNOWN", "r15_state": "UNKNOWN"}


def _get_vix():
    """Read current VIX from Redis."""
    try:
        for key in ["realtime:feed:VIXY:last", "realtime:vix:current", "market:vix"]:
            v = r.get(key)
            if v:
                return float(v)
    except Exception:
        pass
    return 20.0


def _get_spy_price():
    """Read current SPY price from Redis."""
    try:
        for key in ["realtime:feed:SPY:last", "realtime:price:SPY"]:
            v = r.get(key)
            if v:
                return float(v)
    except Exception:
        pass
    return 0.0


def _get_equity():
    """Read current equity from Redis."""
    try:
        for key in ["ofg:equity:verified", "emarkos:v1:equity"]:
            v = r.get(key)
            if v:
                try:
                    data = json.loads(v)
                    if isinstance(data, dict):
                        return float(data.get("total", data.get("equity", 0)))
                    return float(data)
                except (json.JSONDecodeError, TypeError):
                    return float(v)
    except Exception:
        pass
    return 0.0


def _get_positions_count():
    """Read current position count."""
    try:
        v = r.get("emarkos:v1:positions")
        if v:
            data = json.loads(v)
            if isinstance(data, dict):
                positions = data.get("positions", data)
                if isinstance(positions, dict):
                    return len(positions)
                elif isinstance(positions, list):
                    return len(positions)
            return 0
    except Exception:
        pass
    return 0


# ── Main Loop ──

PREDICTION_INTERVAL = 300     # Record prediction every 5 min
SCORING_INTERVAL = 1800       # Score predictions every 30 min
METRICS_INTERVAL = 60         # Collect metrics every 60 sec
SESSION_ANALYSIS_INTERVAL = 3600  # Session analysis every hour

last_prediction_ts = 0
last_scoring_ts = 0
last_metrics_ts = 0
last_session_ts = 0
prev_regime = None

logger.info("=" * 60)
logger.info("ARES v3.0 Feedback Loop Daemon Starting")
logger.info("=" * 60)

cycle = 0
while True:
    try:
        now = time.time()
        cycle += 1

        # 1. Record regime prediction (every 5 min)
        if now - last_prediction_ts >= PREDICTION_INTERVAL:
            regime_data = _get_current_regime()
            vix = _get_vix()
            spy = _get_spy_price()

            # Determine effective regime for prediction
            ms_regime = regime_data.get("ms_regime", "UNKNOWN")
            ms_state = regime_data.get("ms_state", "UNKNOWN")
            r15_state = regime_data.get("r15_state", "UNKNOWN")

            # Simple composite regime
            severity = {"BULL": 0, "BULLISH": 0, "NORMAL": 1, "CAUTION": 2,
                        "TRANSITION": 2, "BEAR": 3, "BEARISH": 3, "CRISIS": 4, "CRASH": 4}
            s1 = severity.get(ms_regime.upper(), 2)
            s2 = severity.get(ms_state.upper(), 1)
            effective_sev = max(s1, s2)
            sev_to_regime = {0: "BULL", 1: "NORMAL", 2: "CAUTION", 3: "BEAR", 4: "CRISIS"}
            predicted = sev_to_regime.get(effective_sev, "CAUTION")

            prediction = RegimePrediction(
                timestamp=now,
                predicted_regime=predicted,
                ms_regime=ms_regime,
                ms_state=ms_state,
                r15_state=r15_state,
                vix=vix,
                confidence=0.5,  # Default; can be enhanced
                spy_price_at_prediction=spy,
            )
            feedback.record_prediction(prediction)

            # Check for regime change (for flapping)
            if prev_regime and predicted != prev_regime:
                logger.info("Regime changed: %s -> %s", prev_regime, predicted)
            prev_regime = predicted
            last_prediction_ts = now

        # 2. Score pending predictions (every 30 min)
        if now - last_scoring_ts >= SCORING_INTERVAL:
            spy = _get_spy_price()
            if spy > 0:
                feedback.evaluate_pending_predictions(spy)
            last_scoring_ts = now

        # 3. Collect performance metrics (every 60 sec)
        if now - last_metrics_ts >= METRICS_INTERVAL:
            equity = _get_equity()
            pos_count = _get_positions_count()
            vix = _get_vix()

            metrics = PerformanceMetrics(
                equity=equity,
                leverage=0.0,  # TODO: calculate from positions
                exposure=pos_count / 68.0 if pos_count > 0 else 0.0,
            )
            feedback.update_metrics(metrics)

            # Log status periodically
            if cycle % 30 == 0:  # Every ~15 min
                accuracy = feedback.get_rolling_accuracy()
                flaps = feedback.get_flap_count()
                dampen = feedback.get_dampen_factor()
                logger.info(
                    "Status: equity=$%.0f pos=%d accuracy=%.3f flaps=%d dampen=%.2f",
                    equity, pos_count, accuracy, flaps, dampen,
                )

            last_metrics_ts = now

        # 4. Session analysis (every hour)
        if now - last_session_ts >= SESSION_ANALYSIS_INTERVAL:
            feedback.run_session_analysis()
            last_session_ts = now

        # Heartbeat
        r.setex("ares:feedback:daemon:heartbeat", 60, str(now))

        time.sleep(30)

    except KeyboardInterrupt:
        logger.info("Feedback daemon shutting down")
        break
    except Exception as e:
        logger.error("Feedback daemon error: %s\n%s", e, traceback.format_exc())
        time.sleep(10)
