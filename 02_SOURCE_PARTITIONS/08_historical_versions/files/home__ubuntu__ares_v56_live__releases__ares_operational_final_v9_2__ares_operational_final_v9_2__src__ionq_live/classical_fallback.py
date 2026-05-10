from __future__ import annotations

import numpy as np



def greedy_cardinality_solution(
    symbols: list[str],
    scores: np.ndarray,
    cardinality: int,
) -> tuple[str, dict[str, float]]:
    if not symbols:
        return "", {}
    order = np.argsort(-scores)
    picks = set(order[: max(1, min(cardinality, len(symbols)))].tolist())
    bits = ["1" if i in picks else "0" for i in range(len(symbols))]
    chosen_scores = {symbols[i]: float(max(scores[i], 1e-9)) for i in picks}
    total = sum(chosen_scores.values())
    weights = {sym: v / total for sym, v in chosen_scores.items()} if total > 0 else {}
    return "".join(bits[::-1]), weights
