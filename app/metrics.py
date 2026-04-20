from dataclasses import dataclass, asdict
from threading import Lock


@dataclass
class MetricState:
    request_count: int = 0
    cache_hit_count: int = 0
    cache_miss_count: int = 0
    db_query_count: int = 0
    rebuild_count: int = 0
    last_request_latency_ms: float = 0.0
    avg_request_latency_ms: float = 0.0
    total_request_latency_ms: float = 0.0


class MetricsStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._state = MetricState()

    def reset(self) -> None:
        with self._lock:
            self._state = MetricState()

    def increment(self, field_name: str, amount: int = 1) -> None:
        with self._lock:
            current = getattr(self._state, field_name)
            setattr(self._state, field_name, current + amount)

    def record_latency(self, latency_ms: float) -> None:
        with self._lock:
            self._state.request_count += 1
            self._state.last_request_latency_ms = latency_ms
            self._state.total_request_latency_ms += latency_ms
            self._state.avg_request_latency_ms = round(
                self._state.total_request_latency_ms / self._state.request_count,
                2,
            )

    def snapshot(self) -> dict:
        with self._lock:
            state = asdict(self._state)
        state["last_request_latency_ms"] = round(state["last_request_latency_ms"], 2)
        state["total_request_latency_ms"] = round(state["total_request_latency_ms"], 2)
        return state
