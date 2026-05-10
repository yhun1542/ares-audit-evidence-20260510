from __future__ import annotations

if __package__ in {None, ''}:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from ionq_live.ares_io import AresRedis, getenv_bool, normalize_weights, turnover_bps
    from ionq_live.classical_fallback import greedy_cardinality_solution
    from ionq_live.ionq_client import IonQRunner, require_api_key
    from ionq_live.problem_builder import build_reduced_problem, weights_from_selection
    from ionq_live.qaoa_fixed import best_feasible_bitstring, build_fixed_qaoa_circuit
else:
    from .ares_io import AresRedis, getenv_bool, normalize_weights, turnover_bps
    from .classical_fallback import greedy_cardinality_solution
    from .ionq_client import IonQRunner, require_api_key
    from .problem_builder import build_reduced_problem, weights_from_selection
    from .qaoa_fixed import best_feasible_bitstring, build_fixed_qaoa_circuit

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests


@dataclass(slots=True)
class Config:
    redis_url: str
    account_id: str
    engine_id: str
    target_key_mode: str
    metadata_ttl_s: int

    ionq_backend: str
    ionq_shots: int
    ionq_max_queue_time_s: int
    ionq_require_available: bool
    ionq_allow_degraded: bool
    ionq_poll_interval_s: int
    ionq_max_wait_s: int

    quantum_max_qubits: int
    quantum_cardinality: int
    quantum_gamma: float
    quantum_beta: float
    quantum_risk_aversion: float
    quantum_turnover_penalty: float
    quantum_cardinality_penalty: float
    quantum_regime_penalty: float
    quantum_max_book_share: float
    quantum_rebalance_threshold_bps: float
    quantum_live_apply: bool

    run_interval_s: int
    market_timezone: str
    active_weekdays: set[int]
    active_windows_et: list[tuple[int, int]]

    telegram_bot_token: str
    telegram_chat_id: str



def load_config() -> Config:
    return Config(
        redis_url=os.getenv("REDIS_URL", ""),
        account_id=os.getenv("ARES_ACCOUNT_ID", "main"),
        engine_id=os.getenv("ARES_ENGINE_ID", "ionq-live-canary"),
        target_key_mode=os.getenv("QUANTUM_TARGET_KEY_MODE", "weights").strip(),
        metadata_ttl_s=int(os.getenv("QUANTUM_METADATA_TTL_S", "1209600")),
        ionq_backend=os.getenv("IONQ_BACKEND", "qpu.aria-1"),
        ionq_shots=int(os.getenv("IONQ_SHOTS", "512")),
        ionq_max_queue_time_s=int(os.getenv("IONQ_MAX_QUEUE_TIME_S", "120")),
        ionq_require_available=getenv_bool("IONQ_REQUIRE_AVAILABLE", True),
        ionq_allow_degraded=getenv_bool("IONQ_ALLOW_DEGRADED", False),
        ionq_poll_interval_s=int(os.getenv("IONQ_POLL_INTERVAL_S", "5")),
        ionq_max_wait_s=int(os.getenv("IONQ_MAX_WAIT_S", "180")),
        quantum_max_qubits=int(os.getenv("QUANTUM_MAX_QUBITS", "8")),
        quantum_cardinality=int(os.getenv("QUANTUM_CARDINALITY", "4")),
        quantum_gamma=float(os.getenv("QUANTUM_GAMMA", "0.7")),
        quantum_beta=float(os.getenv("QUANTUM_BETA", "0.45")),
        quantum_risk_aversion=float(os.getenv("QUANTUM_RISK_AVERSION", "0.12")),
        quantum_turnover_penalty=float(os.getenv("QUANTUM_TURNOVER_PENALTY", "0.06")),
        quantum_cardinality_penalty=float(os.getenv("QUANTUM_CARDINALITY_PENALTY", "1.5")),
        quantum_regime_penalty=float(os.getenv("QUANTUM_REGIME_PENALTY", "0.04")),
        quantum_max_book_share=float(os.getenv("QUANTUM_MAX_BOOK_SHARE", "0.10")),
        quantum_rebalance_threshold_bps=float(os.getenv("QUANTUM_REBALANCE_THRESHOLD_BPS", "25")),
        quantum_live_apply=getenv_bool("QUANTUM_LIVE_APPLY", True),
        run_interval_s=int(os.getenv("RUN_INTERVAL_S", "300")),
        market_timezone=os.getenv("MARKET_TIMEZONE", "America/New_York"),
        active_weekdays={int(x) for x in os.getenv("ACTIVE_WEEKDAYS", "1,2,3,4,5").split(",") if x.strip()},
        active_windows_et=parse_windows(os.getenv("ACTIVE_WINDOWS_ET", "09:20-09:28,12:00-12:05")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    )



def parse_windows(spec: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        start_s, end_s = chunk.split("-")
        sh, sm = map(int, start_s.split(":"))
        eh, em = map(int, end_s.split(":"))
        out.append((sh * 60 + sm, eh * 60 + em))
    return out



def now_in_active_window(cfg: Config) -> bool:
    now = datetime.now(ZoneInfo(cfg.market_timezone))
    if now.isoweekday() not in cfg.active_weekdays:
        return False
    current = now.hour * 60 + now.minute
    return any(start <= current <= end for start, end in cfg.active_windows_et)



def send_telegram(cfg: Config, text: str) -> None:
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": cfg.telegram_chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        return



def blend_portfolio(
    current_weights: dict[str, float],
    overlay_weights: dict[str, float],
    overlay_share: float,
) -> dict[str, float]:
    base = normalize_weights(current_weights)
    overlay = normalize_weights(overlay_weights)
    if not base:
        return overlay
    if not overlay:
        return base

    symbols = set(base) | set(overlay)
    out = {}
    for sym in symbols:
        out[sym] = (1.0 - overlay_share) * base.get(sym, 0.0) + overlay_share * overlay.get(sym, 0.0)
    return normalize_weights(out)



def should_trade(inputs) -> tuple[bool, str]:
    if not inputs.trading_enabled:
        return False, "trading_disabled"
    if inputs.trade_halt:
        return False, f"trade_halt:{inputs.trade_halt_reason or 'unknown'}"
    verdict = inputs.go_nogo_verdict.upper()
    if "NO_GO" in verdict:
        return False, f"go_nogo:{inputs.go_nogo_verdict}"
    if not inputs.signals:
        return False, "empty_signals"
    if not inputs.current_weights:
        return False, "empty_current_weights"
    return True, "ok"



def run_once(cfg: Config) -> dict:
    api_key = require_api_key()
    if not cfg.redis_url:
        raise RuntimeError("REDIS_URL is required")

    rr = AresRedis(cfg.redis_url)
    inputs = rr.load_inputs(cfg.account_id)

    allowed, reason = should_trade(inputs)
    if not allowed:
        return {"status": "skipped", "reason": reason}

    reduced = build_reduced_problem(
        signals=inputs.signals,
        current_weights=inputs.current_weights,
        blocked_symbols=inputs.blocked_symbols,
        regime=inputs.regime,
        max_qubits=cfg.quantum_max_qubits,
        cardinality=cfg.quantum_cardinality,
        risk_aversion=cfg.quantum_risk_aversion,
        turnover_penalty=cfg.quantum_turnover_penalty,
        cardinality_penalty=cfg.quantum_cardinality_penalty,
        regime_penalty=cfg.quantum_regime_penalty,
    )
    if not reduced.symbols:
        return {"status": "skipped", "reason": "no_reduced_universe"}

    runner = IonQRunner(
        api_key=api_key,
        backend_name=cfg.ionq_backend,
        max_queue_time_s=cfg.ionq_max_queue_time_s,
        require_available=cfg.ionq_require_available,
        allow_degraded=cfg.ionq_allow_degraded,
        poll_interval_s=cfg.ionq_poll_interval_s,
        max_wait_s=cfg.ionq_max_wait_s,
    )

    backend_guard = runner.backend_guard()
    job_id = ""
    decision_source = "quantum"

    if not backend_guard.allowed:
        bitstring, overlay = greedy_cardinality_solution(
            reduced.symbols,
            reduced.scores,
            reduced.cardinality,
        )
        decision_source = "classical_fallback_backend_guard"
    else:
        circuit = build_fixed_qaoa_circuit(
            reduced.qubo,
            gamma=cfg.quantum_gamma,
            beta=cfg.quantum_beta,
            shots=cfg.ionq_shots,
        )
        try:
            counts, job_id = runner.run_counts(circuit, shots=cfg.ionq_shots)
            bitstring = best_feasible_bitstring(counts, reduced.cardinality)
            overlay = weights_from_selection(bitstring, reduced.symbols, reduced.scores)
            if not overlay:
                bitstring, overlay = greedy_cardinality_solution(
                    reduced.symbols,
                    reduced.scores,
                    reduced.cardinality,
                )
                decision_source = "classical_fallback_empty_quantum"
        except Exception as exc:
            bitstring, overlay = greedy_cardinality_solution(
                reduced.symbols,
                reduced.scores,
                reduced.cardinality,
            )
            decision_source = f"classical_fallback_exception:{type(exc).__name__}"

    proposed = blend_portfolio(inputs.current_weights, overlay, cfg.quantum_max_book_share)
    delta_bps = turnover_bps(inputs.current_weights, proposed)

    meta = {
        "ts": int(time.time()),
        "engine": cfg.engine_id,
        "source": decision_source,
        "ionq_backend": cfg.ionq_backend,
        "ionq_job_id": job_id,
        "backend_guard": {
            "status": backend_guard.status,
            "degraded": backend_guard.degraded,
            "average_queue_time_s": backend_guard.average_queue_time_s,
            "allowed": backend_guard.allowed,
        },
        "regime": inputs.regime,
        "symbols": reduced.symbols,
        "bitstring": bitstring,
        "overlay": overlay,
        "proposed": proposed,
        "delta_bps": delta_bps,
        "equity_total": inputs.equity_total,
    }

    if delta_bps < cfg.quantum_rebalance_threshold_bps:
        rr.set_json("sleeve:ares:weights:quantum_meta", meta, ex=cfg.metadata_ttl_s)
        return {"status": "skipped", "reason": "below_threshold", **meta}

    if cfg.quantum_live_apply:
        live_key = rr.apply_live_weights(
            proposed,
            account_id=cfg.account_id,
            engine_id=cfg.engine_id,
            target_mode=cfg.target_key_mode,
            metadata=meta,
            metadata_ttl_s=cfg.metadata_ttl_s,
        )
        send_telegram(
            cfg,
            f"ARES IonQ live apply\nsource={decision_source}\nkey={live_key}\ndelta_bps={delta_bps:.1f}\nbackend={cfg.ionq_backend}",
        )
        return {"status": "applied", "live_key": live_key, **meta}

    rr.set_json("sleeve:ares:weights:quantum_meta", meta, ex=cfg.metadata_ttl_s)
    return {"status": "prepared", **meta}



def daemon(cfg: Config) -> None:
    while True:
        try:
            if now_in_active_window(cfg):
                result = run_once(cfg)
                print(json.dumps(result, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"status": "error", "error": repr(exc)}, ensure_ascii=False), file=sys.stderr)
        time.sleep(cfg.run_interval_s)



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    if args.once:
        result = run_once(cfg)
        print(json.dumps(result, ensure_ascii=False))
        return
    daemon(cfg)


if __name__ == "__main__":
    main()
