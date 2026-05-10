from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


REGIME_MULTIPLIER = {
    "CALM": 1.0,
    "RISK_ON": 0.9,
    "RISK_OFF": 1.25,
    "CRISIS": 1.5,
    "UNKNOWN": 1.1,
}


@dataclass(slots=True)
class ReducedProblem:
    symbols: list[str]
    scores: np.ndarray
    qubo: np.ndarray
    cardinality: int



def build_reduced_problem(
    signals: dict[str, float],
    current_weights: dict[str, float],
    blocked_symbols: set[str],
    regime: str,
    max_qubits: int,
    cardinality: int,
    risk_aversion: float,
    turnover_penalty: float,
    cardinality_penalty: float,
    regime_penalty: float,
) -> ReducedProblem:
    candidates = []
    regime_mult = REGIME_MULTIPLIER.get(regime.upper(), REGIME_MULTIPLIER["UNKNOWN"])

    for sym, raw_signal in signals.items():
        sym = sym.upper()
        if sym in blocked_symbols:
            continue
        sig = float(raw_signal)
        current_w = float(current_weights.get(sym, 0.0))
        turnover_cost = abs(sig - current_w) * turnover_penalty
        adjusted = sig - turnover_cost - regime_penalty * (regime_mult - 1.0) * abs(sig)
        candidates.append((sym, sig, adjusted, current_w))

    candidates.sort(key=lambda row: abs(row[2]), reverse=True)
    reduced = candidates[: max(2, max_qubits)]
    if not reduced:
        return ReducedProblem(symbols=[], scores=np.array([]), qubo=np.zeros((0, 0)), cardinality=0)

    symbols = [row[0] for row in reduced]
    adjusted_scores = np.array([max(0.0, row[2]) for row in reduced], dtype=float)
    current = np.array([max(0.0, row[3]) for row in reduced], dtype=float)
    n = len(symbols)
    k = min(max(1, cardinality), n)

    # 간단 공분산 근사: 현재 노출이 비슷할수록 pairwise penalty 증가
    corr = np.zeros((n, n), dtype=float)
    for i in range(n):
        for j in range(i + 1, n):
            corr[i, j] = corr[j, i] = math.sqrt((current[i] + 1e-6) * (current[j] + 1e-6))

    # QUBO: maximize adjusted_scores - risk - cardinality deviation
    # minimization form으로 부호 반전
    qubo = np.zeros((n, n), dtype=float)
    for i in range(n):
        qubo[i, i] += -adjusted_scores[i]
        qubo[i, i] += cardinality_penalty * (1 - 2 * k)
        qubo[i, i] += risk_aversion * corr[i, i]
        for j in range(i + 1, n):
            qubo[i, j] += 2 * cardinality_penalty
            qubo[i, j] += risk_aversion * corr[i, j]
            qubo[j, i] = qubo[i, j]

    return ReducedProblem(symbols=symbols, scores=adjusted_scores, qubo=qubo, cardinality=k)



def decode_selection(bitstring: str, symbols: list[str]) -> list[str]:
    return [symbols[i] for i, bit in enumerate(bitstring[::-1]) if bit == "1"]



def weights_from_selection(bitstring: str, symbols: list[str], scores: np.ndarray) -> dict[str, float]:
    picks: list[tuple[str, float]] = []
    rev = bitstring[::-1]
    for i, sym in enumerate(symbols):
        if i < len(rev) and rev[i] == "1":
            picks.append((sym, float(max(scores[i], 1e-9))))
    total = sum(v for _, v in picks)
    if total <= 0:
        return {}
    return {sym: score / total for sym, score in picks}
