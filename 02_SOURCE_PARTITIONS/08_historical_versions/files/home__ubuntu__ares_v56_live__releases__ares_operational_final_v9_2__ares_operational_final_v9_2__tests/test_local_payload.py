from ionq_live.problem_builder import build_reduced_problem, weights_from_selection
from ionq_live.classical_fallback import greedy_cardinality_solution


def test_problem_build_and_fallback():
    signals = {"AAPL": 0.8, "MSFT": 0.6, "NVDA": 0.7, "TLT": 0.2}
    current = {"AAPL": 0.25, "MSFT": 0.25, "SPY": 0.50}
    reduced = build_reduced_problem(
        signals=signals,
        current_weights=current,
        blocked_symbols=set(),
        regime="CALM",
        max_qubits=4,
        cardinality=2,
        risk_aversion=0.1,
        turnover_penalty=0.05,
        cardinality_penalty=1.5,
        regime_penalty=0.03,
    )
    assert len(reduced.symbols) >= 2
    bitstring, weights = greedy_cardinality_solution(reduced.symbols, reduced.scores, 2)
    assert bitstring.count("1") == 2
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    qweights = weights_from_selection(bitstring, reduced.symbols, reduced.scores)
    assert abs(sum(qweights.values()) - 1.0) < 1e-9
