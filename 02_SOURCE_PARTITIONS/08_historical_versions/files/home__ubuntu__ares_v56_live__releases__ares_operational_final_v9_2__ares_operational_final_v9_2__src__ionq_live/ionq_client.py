from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests
from qiskit_ionq import IonQProvider

from .qaoa_fixed import prepare_for_backend


IONQ_BACKEND_URL = "https://api.ionq.co/v0.4/backends/{backend}"


@dataclass(slots=True)
class BackendGuard:
    backend: str
    status: str
    degraded: bool
    average_queue_time_s: float | None
    allowed: bool
    raw: dict[str, Any]


class IonQRunner:
    def __init__(
        self,
        api_key: str,
        backend_name: str,
        max_queue_time_s: int,
        require_available: bool,
        allow_degraded: bool,
        poll_interval_s: int,
        max_wait_s: int,
    ):
        self.api_key = api_key
        self.backend_name = backend_name
        self.max_queue_time_s = max_queue_time_s
        self.require_available = require_available
        self.allow_degraded = allow_degraded
        self.poll_interval_s = poll_interval_s
        self.max_wait_s = max_wait_s

    def backend_guard(self) -> BackendGuard:
        headers = {"Authorization": f"apiKey {self.api_key}"}
        resp = requests.get(
            IONQ_BACKEND_URL.format(backend=self.backend_name),
            headers=headers,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        status = str(data.get("status", "unknown"))
        degraded = bool(data.get("degraded", False))
        raw_queue = data.get("average_queue_time")
        queue_s = None
        if raw_queue is not None:
            try:
                queue_s = float(raw_queue) / 1000.0 if float(raw_queue) > 10_000 else float(raw_queue)
            except (TypeError, ValueError):
                queue_s = None

        allowed = True
        if self.require_available and status != "available":
            allowed = False
        if (not self.allow_degraded) and degraded:
            allowed = False
        if queue_s is not None and queue_s > self.max_queue_time_s:
            allowed = False

        return BackendGuard(
            backend=self.backend_name,
            status=status,
            degraded=degraded,
            average_queue_time_s=queue_s,
            allowed=allowed,
            raw=data,
        )

    def run_counts(self, circuit, shots: int) -> tuple[dict[str, int], str]:
        provider = IonQProvider(self.api_key)
        backend = provider.get_backend(self.backend_name)
        tqc = prepare_for_backend(circuit, backend)
        job = backend.run(tqc, shots=shots)
        job_id = job.job_id()
        deadline = time.time() + self.max_wait_s

        while time.time() < deadline:
            status = str(job.status()).lower()
            if "done" in status:
                break
            if any(x in status for x in ("cancelled", "error", "fail")):
                raise RuntimeError(f"IonQ job failed: {status}")
            time.sleep(self.poll_interval_s)
        else:
            raise TimeoutError(f"IonQ job timed out after {self.max_wait_s}s: {job_id}")

        counts = job.get_counts()
        return counts, job_id



def require_api_key() -> str:
    key = os.getenv("IONQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("IONQ_API_KEY is required")
    return key
