"""Live MCP and development replay clients for VSS-shaped data."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.smart_city.vss_mapping import external_id, unpack_collection


class VssClient(Protocol):
    name: str

    async def incidents_since(self, since: datetime | None) -> list[dict[str, Any]]: ...
    async def get_incident(self, incident_id: str) -> dict[str, Any] | None: ...
    async def get_sensors(self) -> Any: ...
    async def get_places(self) -> Any: ...
    def set_sim_time(self, sim_time: float) -> None: ...


class McpVssClient:
    name = "nvidia-vss"

    def __init__(self, url: str, api_key: str | None = None):
        self._url = url
        self._api_key = api_key

    def set_sim_time(self, sim_time: float) -> None:
        return None

    async def incidents_since(self, since: datetime | None) -> list[dict[str, Any]]:
        args: dict[str, Any] = {"max_count": 100, "includes": ["objectIds", "info"]}
        if since is not None:
            args["start_time"] = since.isoformat().replace("+00:00", "Z")
        payload = await self._call("get_incidents", args)
        return [item for item in unpack_collection(payload, "incidents") if isinstance(item, dict)]

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        payload = await self._call("get_incident", {"id": incident_id, "includes": ["objectIds", "info"]})
        items = [item for item in unpack_collection(payload, "incidents") if isinstance(item, dict)]
        return items[0] if items else None

    async def get_sensors(self) -> Any:
        return await self._call("get_sensor_ids", {})

    async def get_places(self) -> Any:
        return await self._call("get_places", {})

    async def _call(self, short_name: str, arguments: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        timeout = httpx2.Timeout(15.0, read=30.0)
        async with httpx2.AsyncClient(headers=headers, timeout=timeout) as http_client:
            transport = streamable_http_client(self._url, http_client=http_client)
            async with Client(transport, read_timeout_seconds=30.0) as client:
                listed = await client.list_tools()
                names = {tool.name for tool in listed.tools}
                candidates = (f"video_analytics__{short_name}", short_name)
                tool_name = next((name for name in candidates if name in names), None)
                if tool_name is None:
                    raise RuntimeError(f"VSS MCP tool not found: {short_name}")
                result = await client.call_tool(tool_name, arguments)
                if result.is_error:
                    raise RuntimeError(_content_text(result.content) or f"{tool_name} failed")
                structured = getattr(result, "structured_content", None)
                if structured is not None:
                    return structured
                text = _content_text(result.content)
                return json.loads(text) if text else []


class ReplayVssClient:
    """Development-only client whose release/clear schedule runs on simulation time."""

    name = "vss-replay"

    def __init__(self, path: Path):
        payload = json.loads(path.read_text())
        self._documents = [item for item in unpack_collection(payload, "incidents") if isinstance(item, dict)]
        self._sim_time = 0.0

    def set_sim_time(self, sim_time: float) -> None:
        self._sim_time = sim_time

    async def incidents_since(self, since: datetime | None) -> list[dict[str, Any]]:
        released: list[dict[str, Any]] = []
        for original in self._documents:
            replay = original.get("replay") if isinstance(original.get("replay"), dict) else {}
            if self._sim_time < float(replay.get("release_at_sim_s", 0.0)):
                continue
            document = deepcopy(original)
            document.pop("replay", None)
            clear_at = replay.get("clear_at_sim_s")
            if clear_at is not None and self._sim_time >= float(clear_at):
                document["end"] = document.get("end") or document.get("timestamp") or document.get("start")
            released.append(document)
        return released

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        return next((item for item in await self.incidents_since(None) if external_id(item) == incident_id), None)

    async def get_sensors(self) -> Any:
        return {"sensorIds": sorted({sensor for item in self._documents for sensor in _sensor_ids(item)})}

    async def get_places(self) -> Any:
        return {"places": [item["place"] | {"sensorId": item.get("sensorId")} for item in self._documents if isinstance(item.get("place"), dict)]}


def _content_text(content: list[Any]) -> str:
    return "\n".join(str(getattr(item, "text", "")) for item in content).strip()


def _sensor_ids(document: dict[str, Any]) -> list[str]:
    value = document.get("sensorId") or document.get("sensorIds") or []
    return [str(item) for item in value] if isinstance(value, list) else [str(value)]
