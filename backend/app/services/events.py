"""Operations event log (what the UI shows as the activity feed)."""

from __future__ import annotations

import itertools
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from app.models.api import EventLevel, OpsEvent


class EventLog:
    def __init__(self, on_event: Callable[[OpsEvent], None], maxlen: int = 200):
        self._events: deque[OpsEvent] = deque(maxlen=maxlen)
        self._ids = itertools.count(1)
        self._on_event = on_event

    def add(
        self, level: EventLevel, message: str, sim_time: float | None = None, incident_id: str | None = None
    ) -> OpsEvent:
        event = OpsEvent(
            id=next(self._ids),
            timestamp=datetime.now(UTC),
            sim_time=sim_time,
            level=level,
            message=message,
            incident_id=incident_id,
        )
        self._events.append(event)
        self._on_event(event)
        return event

    def recent(self, limit: int = 50) -> list[OpsEvent]:
        if limit <= 0:  # [-0:] is the whole log, and a negative slice drops from the front
            return []
        return list(self._events)[-limit:]
