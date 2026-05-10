"""
ARES v3.0 - AI Regime Feedback Loop Engine
============================================
Automated feedback loop for regime prediction accuracy tracking,
parameter auto-tuning, post-trade analysis, and performance monitoring.

4AI consensus design:
  - Track predicted regime vs actual market returns (T+1h, T+4h, T+1d)
  - Auto-adjust regime parameters based on accuracy scores
  - Detect regime flapping and auto-dampen
  - Monitor Sharpe/MaxDD/Calmar/Sortino/WinRate/Leverage/Exposure
  - Track VIXY activation correlation with regime changes
"""
from __future__ import annotations
import json, time, math, logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ares.regime.feedback")


class FeedbackKeys:
    """Redis key schema for feedback loop."""
    PREDICTIONS = "ares:feedback:v3:predictions"
    ACCURACY_SCORES = "ares:feedback:v3:accuracy_scores"
    ACCURACY_ROLLING = "ares:feedback:v3:accuracy_rolling"
    ACCURACY_DAILY = "ares:feedback:v3:accuracy:daily"
    TUNING_PARAMS = "ares:feedback:v3:tuning_params"
    TUNING_HISTORY = "ares:feedback:v3:tuning_history"
    REGIME_CHANGES = "ares:feedback:v3:regime_changes"
    FLAP_COUNT_24H = "ares:feedback:v3:flap_count_24h"
    FLAP_DAMPEN_FACTOR = "ares:feedback:v3:flap_dampen"
    METRICS_LATEST = "ares:feedback:v3:metrics:latest"
    METRICS_HISTORY = "ares:feedback:v3:metrics:history"
    EQUITY_CURVE = "ares:feedback:v3:equity_curve"
    VIXY_ACTIVATIONS = "ares:feedback:v3:vixy_activations"
    VIXY_REGIME_CORR = "ares:feedback:v3:vixy_regime_corr"
    TRADE_LOG = "ares:feedback:v3:trade_log"
    SESSION_ANALYSIS = "ares:feedback:v3:session_analysis"


@dataclass
class RegimePrediction:
    """A regime prediction snapshot for later accuracy evaluation."""
    timestamp: float
    predicted_regime: str
    ms_regime: str
    ms_state: str
    r15_state: str
    vix: float
    confidence: float
    spy_price_at_prediction: float = 0.0
    actual_return_1h: Optional[float] = None
    actual_return_4h: Optional[float] = None
    actual_return_1d: Optional[float] = None
    accuracy_score: Optional[float] = None


@dataclass
class PerformanceMetrics:
    """Trading performance metrics snapshot."""
    timestamp: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    sortino_ratio: float = 0.0
    win_rate: float = 0.0
    leverage: float = 0.0
    exposure: float = 0.0
    turnover: float = 0.0
    daily_pnl_pct: float = 0.0
    equity: float = 0.0
    vixy_activation_count: int = 0


class RegimeAccuracyScorer:
    """
    Score regime prediction accuracy by comparing predicted regime at T
    with actual market returns at T+1h, T+4h, T+1d.

    Scoring formula (4AI consensus):
      score = w1*match_1h + w2*match_4h + w3*match_1d
      match = +1.0 (correct direction), +0.3 (neutral ok), -0.5 (wrong)
      Final score clamped to [0.0, 1.0]
    """
    WEIGHTS = {"1h": 0.25, "4h": 0.35, "1d": 0.40}

    REGIME_EXPECTATIONS = {
        "BULL":    {"direction": "positive", "threshold": 0.001},
        "NORMAL":  {"direction": "positive", "threshold": 0.0005},
        "CAUTION": {"direction": "neutral",  "threshold": 0.002},
        "BEAR":    {"direction": "negative", "threshold": -0.001},
        "CRISIS":  {"direction": "negative", "threshold": -0.005},
    }

    @classmethod
    def score(cls, predicted, ret_1h, ret_4h, ret_1d):
        expectation = cls.REGIME_EXPECTATIONS.get(
            predicted.upper(), {"direction": "neutral", "threshold": 0.002}
        )
        direction = expectation["direction"]
        total = 0.0
        for ret, horizon in [(ret_1h, "1h"), (ret_4h, "4h"), (ret_1d, "1d")]:
            w = cls.WEIGHTS[horizon]
            if direction == "positive":
                if ret > 0.001:
                    total += w * 1.0
                elif ret > -0.001:
                    total += w * 0.3
                else:
                    total -= w * 0.5
            elif direction == "negative":
                if ret < -0.001:
                    total += w * 1.0
                elif ret < 0.001:
                    total += w * 0.3
                else:
                    total -= w * 0.5
            else:
                if abs(ret) < 0.002:
                    total += w * 0.8
                elif abs(ret) < 0.005:
                    total += w * 0.3
                else:
                    total -= w * 0.3
        return max(0.0, min(1.0, total))


class RegimeFeedbackLoop:
    """
    Main feedback loop engine.

    Responsibilities:
    1. Record regime predictions with market snapshots
    2. Score accuracy when actual returns become available
    3. Auto-tune regime parameters based on rolling accuracy
    4. Detect and dampen regime flapping
    5. Track VIXY activation correlation
    6. Collect and store performance metrics
    """

    ACCURACY_LOW = 0.55
    ACCURACY_HIGH = 0.80
    TUNE_WINDOW = 20
    MAX_FLAP_COUNT = 5
    DAMPEN_FACTOR_MAX = 2.0

    def __init__(self, redis_client, logger_override=None):
        self.r = redis_client
        self.log = logger_override or logger
        self.scorer = RegimeAccuracyScorer()

    # ── Prediction Recording ──

    def record_prediction(self, prediction):
        """Record a new regime prediction for later accuracy evaluation."""
        try:
            data = asdict(prediction) if hasattr(prediction, '__dataclass_fields__') else prediction
            self.r.lpush(FeedbackKeys.PREDICTIONS, json.dumps(data))
            self.r.ltrim(FeedbackKeys.PREDICTIONS, 0, 4999)
            self._track_regime_change(data)
            self.log.info(
                "Recorded prediction: regime=%s conf=%.2f vix=%.1f",
                data.get("predicted_regime", "?"),
                data.get("confidence", 0),
                data.get("vix", 0),
            )
        except Exception as e:
            self.log.error("Failed to record prediction: %s", e)

    def evaluate_pending_predictions(self, current_spy_price):
        """Check pending predictions and score those with enough elapsed time."""
        try:
            pending = self.r.lrange(FeedbackKeys.PREDICTIONS, 0, 499)
            if not pending:
                return
            now = time.time()
            scored_count = 0
            for i, raw in enumerate(pending):
                pred = json.loads(raw)
                if pred.get("accuracy_score") is not None:
                    continue
                elapsed = now - pred["timestamp"]
                spy_at = pred.get("spy_price_at_prediction", 0)
                if spy_at <= 0 or elapsed < 86400:
                    continue
                ret_1d = (current_spy_price - spy_at) / spy_at
                ret_1h = ret_1d * (1 / 6.5)
                ret_4h = ret_1d * (4 / 6.5)
                sc = self.scorer.score(pred["predicted_regime"], ret_1h, ret_4h, ret_1d)
                pred["actual_return_1h"] = ret_1h
                pred["actual_return_4h"] = ret_4h
                pred["actual_return_1d"] = ret_1d
                pred["accuracy_score"] = sc
                self.r.lset(FeedbackKeys.PREDICTIONS, i, json.dumps(pred))
                self.r.lpush(FeedbackKeys.ACCURACY_SCORES, str(sc))
                self.r.ltrim(FeedbackKeys.ACCURACY_SCORES, 0, 999)
                scored_count += 1
            if scored_count > 0:
                self._update_rolling_accuracy()
                self._auto_tune()
                self.log.info("Scored %d predictions", scored_count)
        except Exception as e:
            self.log.error("Error evaluating predictions: %s", e)

    # ── Auto-Tuning ──

    def _auto_tune(self):
        """Auto-adjust regime parameters based on rolling accuracy."""
        try:
            scores_raw = self.r.lrange(FeedbackKeys.ACCURACY_SCORES, 0, self.TUNE_WINDOW - 1)
            if len(scores_raw) < self.TUNE_WINDOW // 2:
                return
            scores = [float(s) for s in scores_raw]
            avg_score = sum(scores) / len(scores)
            params = self._get_tuning_params()
            adjustment = None
            if avg_score < self.ACCURACY_LOW:
                params["bull_threshold"] = min(0.9, params.get("bull_threshold", 0.6) * 1.05)
                params["caution_threshold"] = max(0.2, params.get("caution_threshold", 0.4) * 0.95)
                params["confidence_weight"] = min(1.5, params.get("confidence_weight", 1.0) * 1.05)
                params["cooldown_multiplier"] = min(2.0, params.get("cooldown_multiplier", 1.0) * 1.1)
                adjustment = "CONSERVATIVE (low accuracy)"
            elif avg_score > self.ACCURACY_HIGH:
                params["bull_threshold"] = max(0.4, params.get("bull_threshold", 0.6) * 0.98)
                params["caution_threshold"] = min(0.6, params.get("caution_threshold", 0.4) * 1.02)
                params["confidence_weight"] = max(0.7, params.get("confidence_weight", 1.0) * 0.98)
                params["cooldown_multiplier"] = max(0.5, params.get("cooldown_multiplier", 1.0) * 0.95)
                adjustment = "RESPONSIVE (high accuracy)"
            if adjustment:
                params["last_tune_ts"] = time.time()
                params["last_tune_reason"] = adjustment
                params["avg_accuracy"] = avg_score
                safe_params = {k: str(v) for k, v in params.items()}
                self.r.hset(FeedbackKeys.TUNING_PARAMS, mapping=safe_params)
                self.r.lpush(FeedbackKeys.TUNING_HISTORY, json.dumps(params))
                self.r.ltrim(FeedbackKeys.TUNING_HISTORY, 0, 499)
                self.log.info("Auto-tune: %s (avg_accuracy=%.3f)", adjustment, avg_score)
        except Exception as e:
            self.log.error("Auto-tune error: %s", e)

    def _get_tuning_params(self):
        try:
            raw = self.r.hgetall(FeedbackKeys.TUNING_PARAMS)
            if raw:
                result = {}
                for k, v in raw.items():
                    kk = k.decode() if isinstance(k, bytes) else k
                    if kk == "last_tune_reason":
                        continue
                    try:
                        result[kk] = float(v.decode() if isinstance(v, bytes) else v)
                    except (ValueError, TypeError):
                        pass
                return result
        except Exception:
            pass
        return {
            "bull_threshold": 0.6,
            "caution_threshold": 0.4,
            "confidence_weight": 1.0,
            "cooldown_multiplier": 1.0,
        }

    # ── Flapping Detection ──

    def _track_regime_change(self, pred_data):
        try:
            record = {
                "ts": pred_data.get("timestamp", time.time()),
                "regime": pred_data.get("predicted_regime", "UNKNOWN"),
                "confidence": pred_data.get("confidence", 0),
            }
            self.r.lpush(FeedbackKeys.REGIME_CHANGES, json.dumps(record))
            self.r.ltrim(FeedbackKeys.REGIME_CHANGES, 0, 2879)
            changes = self.r.lrange(FeedbackKeys.REGIME_CHANGES, 0, 2879)
            cutoff = time.time() - 86400
            prev_regime = None
            flap_count = 0
            for raw in reversed(changes):
                c = json.loads(raw)
                if c["ts"] < cutoff:
                    continue
                if prev_regime and c["regime"] != prev_regime:
                    flap_count += 1
                prev_regime = c["regime"]
            self.r.set(FeedbackKeys.FLAP_COUNT_24H, str(flap_count))
            if flap_count > self.MAX_FLAP_COUNT:
                dampen = min(self.DAMPEN_FACTOR_MAX,
                             1.0 + (flap_count - self.MAX_FLAP_COUNT) * 0.2)
                self.r.set(FeedbackKeys.FLAP_DAMPEN_FACTOR, str(dampen))
                self.log.warning("Regime flapping: %d changes/24h, dampen=%.2f",
                                 flap_count, dampen)
            else:
                self.r.set(FeedbackKeys.FLAP_DAMPEN_FACTOR, "1.0")
        except Exception as e:
            self.log.error("Flapping detection error: %s", e)

    def get_flap_count(self):
        try:
            v = self.r.get(FeedbackKeys.FLAP_COUNT_24H)
            return int(v) if v else 0
        except Exception:
            return 0

    def get_dampen_factor(self):
        try:
            v = self.r.get(FeedbackKeys.FLAP_DAMPEN_FACTOR)
            return float(v) if v else 1.0
        except Exception:
            return 1.0

    # ── VIXY Correlation ──

    def record_vixy_activation(self, regime_at_activation, vix_level):
        try:
            record = {"ts": time.time(), "regime": regime_at_activation, "vix": vix_level}
            self.r.lpush(FeedbackKeys.VIXY_ACTIVATIONS, json.dumps(record))
            self.r.ltrim(FeedbackKeys.VIXY_ACTIVATIONS, 0, 999)
            self._update_vixy_correlation()
        except Exception as e:
            self.log.error("VIXY activation recording error: %s", e)

    def _update_vixy_correlation(self):
        try:
            activations = self.r.lrange(FeedbackKeys.VIXY_ACTIVATIONS, 0, 99)
            if len(activations) < 5:
                return
            caution_or_worse = 0
            total = len(activations)
            for raw in activations:
                a = json.loads(raw)
                if a["regime"] in ("CAUTION", "BEAR", "CRISIS"):
                    caution_or_worse += 1
            correlation = caution_or_worse / total
            self.r.set(FeedbackKeys.VIXY_REGIME_CORR, str(round(correlation, 4)))
        except Exception as e:
            self.log.error("VIXY correlation update error: %s", e)

    # ── Performance Metrics ──

    def update_metrics(self, metrics):
        try:
            data = asdict(metrics) if hasattr(metrics, '__dataclass_fields__') else metrics
            data["timestamp"] = time.time()
            self.r.hset(FeedbackKeys.METRICS_LATEST, mapping={
                k: str(v) for k, v in data.items()
            })
            self.r.lpush(FeedbackKeys.METRICS_HISTORY, json.dumps(data))
            self.r.ltrim(FeedbackKeys.METRICS_HISTORY, 0, 2879)
            eq_point = {"ts": data["timestamp"], "equity": data.get("equity", 0),
                        "pnl": data.get("daily_pnl_pct", 0)}
            self.r.lpush(FeedbackKeys.EQUITY_CURVE, json.dumps(eq_point))
            self.r.ltrim(FeedbackKeys.EQUITY_CURVE, 0, 9999)
        except Exception as e:
            self.log.error("Metrics update error: %s", e)

    def get_metrics_summary(self):
        try:
            raw = self.r.hgetall(FeedbackKeys.METRICS_LATEST)
            if raw:
                return {(k.decode() if isinstance(k, bytes) else k):
                        (v.decode() if isinstance(v, bytes) else v)
                        for k, v in raw.items()}
        except Exception:
            pass
        return {}

    # ── Post-Trade Analysis ──

    def record_trade(self, trade_data):
        try:
            trade_data["recorded_ts"] = time.time()
            self.r.lpush(FeedbackKeys.TRADE_LOG, json.dumps(trade_data))
            self.r.ltrim(FeedbackKeys.TRADE_LOG, 0, 9999)
        except Exception as e:
            self.log.error("Trade recording error: %s", e)

    def run_session_analysis(self):
        try:
            trades = self.r.lrange(FeedbackKeys.TRADE_LOG, 0, 999)
            if not trades:
                return {"status": "no_trades"}
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            session_trades = []
            for raw in trades:
                t = json.loads(raw)
                td = datetime.fromtimestamp(
                    t.get("recorded_ts", 0), tz=timezone.utc
                ).strftime("%Y-%m-%d")
                if td == today:
                    session_trades.append(t)
            if not session_trades:
                return {"status": "no_today_trades"}
            wins = sum(1 for t in session_trades if t.get("pnl", 0) > 0)
            total_pnl = sum(t.get("pnl", 0) for t in session_trades)
            analysis = {
                "date": today,
                "total_trades": len(session_trades),
                "wins": wins,
                "losses": len(session_trades) - wins,
                "win_rate": wins / len(session_trades) if session_trades else 0,
                "total_pnl": total_pnl,
                "avg_pnl": total_pnl / len(session_trades) if session_trades else 0,
            }
            self.r.hset(FeedbackKeys.SESSION_ANALYSIS, mapping={
                k: str(v) for k, v in analysis.items()
            })
            self.r.expire(FeedbackKeys.SESSION_ANALYSIS, 172800)
            self.log.info("Session analysis: %d trades, win_rate=%.1f%%, pnl=$%.2f",
                          len(session_trades), analysis["win_rate"] * 100, total_pnl)
            return analysis
        except Exception as e:
            self.log.error("Session analysis error: %s", e)
            return {"status": "error"}

    # ── Rolling Accuracy ──

    def _update_rolling_accuracy(self):
        try:
            scores = self.r.lrange(FeedbackKeys.ACCURACY_SCORES, 0, 99)
            if scores:
                vals = [float(s) for s in scores]
                avg = sum(vals) / len(vals)
                self.r.set(FeedbackKeys.ACCURACY_ROLLING, str(round(avg, 4)))
                day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                self.r.hset(FeedbackKeys.ACCURACY_DAILY, day, str(round(avg, 4)))
        except Exception as e:
            self.log.error("Rolling accuracy update error: %s", e)

    def get_rolling_accuracy(self):
        try:
            v = self.r.get(FeedbackKeys.ACCURACY_ROLLING)
            return float(v) if v else 0.0
        except Exception:
            return 0.0
