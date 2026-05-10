from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from qiskit import QuantumCircuit, transpile


@dataclass(slots=True)
class IsingModel:
    h: np.ndarray
    j: dict[tuple[int, int], float]
    constant: float



def qubo_to_ising(qubo: np.ndarray) -> IsingModel:
    n = qubo.shape[0]
    h = np.zeros(n, dtype=float)
    j: dict[tuple[int, int], float] = {}
    constant = 0.0

    # x = (1 - z) / 2
    for i in range(n):
        qii = float(qubo[i, i])
        constant += qii / 2.0
        h[i] += -qii / 2.0
        for k in range(i + 1, n):
            q = float(qubo[i, k])
            if q == 0:
                continue
            constant += q / 4.0
            h[i] += -q / 4.0
            h[k] += -q / 4.0
            j[(i, k)] = j.get((i, k), 0.0) + q / 4.0

    return IsingModel(h=h, j=j, constant=constant)



def build_fixed_qaoa_circuit(
    qubo: np.ndarray,
    gamma: float,
    beta: float,
    shots: int = 512,
):
    n = qubo.shape[0]
    model = qubo_to_ising(qubo)
    qc = QuantumCircuit(n, n)

    # |+>^n
    for i in range(n):
        qc.h(i)

    # cost layer e^{-i gamma Hc}
    for i, coeff in enumerate(model.h):
        if abs(coeff) > 1e-12:
            qc.rz(2.0 * gamma * coeff, i)

    for (i, j), coeff in model.j.items():
        if abs(coeff) > 1e-12:
            qc.rzz(2.0 * gamma * coeff, i, j)

    # mixer layer
    for i in range(n):
        qc.rx(2.0 * beta, i)

    qc.measure(range(n), range(n))
    qc.metadata = {"shots": shots}
    return qc



def prepare_for_backend(qc: QuantumCircuit, backend):
    return transpile(qc, backend=backend, optimization_level=1)



def best_feasible_bitstring(
    counts: dict[str, int],
    cardinality: int,
) -> str:
    if not counts:
        return ""

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    for bitstring, _ in ranked:
        if bitstring.count("1") == cardinality:
            return bitstring

    # cardinality 완전 일치가 없으면 가장 빈도 높은 것 사용
    return ranked[0][0]
