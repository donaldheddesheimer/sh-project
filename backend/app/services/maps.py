"""Runtime selection between the bundled traffic maps."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from app.config import REPO_ROOT, Settings
from app.models.domain import NetworkGeometry
from app.providers import Services, build_services
from app.websocket.hub import ConnectionHub

log = logging.getLogger(__name__)


MAP_DIRECTORIES: dict[str, Path] = {
    "downtown_grid": REPO_ROOT / "simulation" / "scenarios" / "downtown_grid",
    "pittsburgh_oakland": REPO_ROOT / "simulation" / "scenarios" / "pittsburgh_oakland",
}


class UnknownMap(KeyError):
    pass


async def _shutdown(services: Services) -> None:
    errors: list[Exception] = []
    for name, stop in (
        ("episodes", services.episodes.shutdown),
        ("scenarios", services.scenarios.shutdown),
        ("city", services.city.stop),
    ):
        try:
            await stop()
        except Exception as exc:  # noqa: BLE001 - finish stopping the other parts before reporting cleanup failure
            log.exception("could not stop old %s service", name)
            errors.append(exc)
    if errors:
        raise errors[0]


class MapManager:
    """Own the active service graph and replace it when the operator changes maps."""

    def __init__(self, settings: Settings, hub: ConnectionHub, mcp_server: MCPServer):
        self._base_settings = settings
        self._hub = hub
        self._mcp_server = mcp_server
        self._services: Services | None = None
        self._lock = asyncio.Lock()

    @property
    def services(self) -> Services:
        if self._services is None:
            raise RuntimeError("traffic services have not started")
        return self._services

    async def start(self) -> Services:
        services = build_services(self._base_settings, self._hub, self._mcp_server)
        services.episodes.arm_at_startup()
        await services.city.start()
        self._services = services
        return services

    async def select(self, map_id: str) -> NetworkGeometry:
        directory = MAP_DIRECTORIES.get(map_id)
        if directory is None:
            raise UnknownMap(map_id)

        async with self._lock:
            current = self.services
            if current.city.scenario.id == map_id:
                return current.city.geometry

            # Stop the old episode service before the replacement is built. Its EpisodeService snapshots the
            # next episode id from the memory directory at construction, so a review still writing EP-0003 would
            # otherwise hand the same id to both graphs and one lesson would overwrite the other. The city keeps
            # running until the replacement has started; only the autonomous half pauses here, and if the
            # replacement then fails to start the operator keeps a live city with no armed agent.
            await current.episodes.shutdown()

            settings = self._base_settings.model_copy(update={"scenario_dir": directory})
            replacement = build_services(settings, self._hub, self._mcp_server)
            replacement.episodes.arm_at_startup()
            try:
                await replacement.city.start()
            except Exception:
                # A failed replacement must not take down the city that is still serving operators.
                with contextlib.suppress(Exception):
                    await _shutdown(replacement)
                raise

            # The old city and the replacement briefly overlap. Detach the old broadcaster
            # before announcing the replacement so no late frame can overwrite the fresh hello.
            current.city.hub = ConnectionHub()
            self._services = replacement
            # Existing WebSocket clients share this hub. A fresh hello clears map-specific
            # history immediately instead of waiting for the next live-state frame.
            self._hub.broadcast(replacement.city.hello_message())
            try:
                await _shutdown(current)
            except Exception:  # replacement is live; failed cleanup must not make the client restore the old map
                log.exception("the new map is live, but part of the previous map did not stop cleanly")
            return replacement.city.geometry

    async def stop(self) -> None:
        if self._services is not None:
            services, self._services = self._services, None
            await _shutdown(services)
