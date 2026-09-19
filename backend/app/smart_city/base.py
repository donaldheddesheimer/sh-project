"""Smart City data boundary.

The rest of the application consumes incidents and camera metadata only
through this interface, so it cannot tell whether they came from the mock or
from NVIDIA VSS.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from enum import StrEnum

from pydantic import BaseModel

from app.models.domain import GeoPoint, Incident


class Camera(BaseModel):
    id: str
    name: str
    location: GeoPoint
    intersection_id: str | None = None
    status: str = "online"


class SmartCityEventKind(StrEnum):
    INCIDENT_DETECTED = "incident.detected"
    INCIDENT_UPDATED = "incident.updated"
    INCIDENT_CLEARED = "incident.cleared"


class SmartCityEvent(BaseModel):
    kind: SmartCityEventKind
    incident: Incident


EventSink = Callable[[SmartCityEvent], Awaitable[None]]


class SmartCityProvider(ABC):
    name: str

    @abstractmethod
    async def start(self, emit: EventSink) -> None:
        """Begin producing events; ``emit`` is awaited for each one."""

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def list_incidents(self, include_cleared: bool = False) -> list[Incident]: ...

    @abstractmethod
    async def get_incident(self, incident_id: str) -> Incident | None: ...

    @abstractmethod
    async def list_cameras(self) -> list[Camera]: ...
