from dataclasses import dataclass, field
from threading import Event, Lock


@dataclass
class InFlightRequest:
    event: Event = field(default_factory=Event)
    item: dict | None = None
    db_ms: float | None = None
    error: Exception | None = None
    waiter_count: int = 0


class RequestCoalescer:
    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[int, InFlightRequest] = {}

    def acquire(self, item_id: int) -> tuple[InFlightRequest, bool]:
        with self._lock:
            entry = self._entries.get(item_id)
            if entry:
                entry.waiter_count += 1
                return entry, False

            entry = InFlightRequest()
            self._entries[item_id] = entry
            return entry, True

    def complete(self, item_id: int, entry: InFlightRequest, item: dict, db_ms: float) -> None:
        with self._lock:
            entry.item = item
            entry.db_ms = db_ms
            entry.event.set()
            if self._entries.get(item_id) is entry:
                del self._entries[item_id]

    def fail(self, item_id: int, entry: InFlightRequest, error: Exception) -> None:
        with self._lock:
            entry.error = error
            entry.event.set()
            if self._entries.get(item_id) is entry:
                del self._entries[item_id]
