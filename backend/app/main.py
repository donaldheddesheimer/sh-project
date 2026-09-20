"""FastAPI application entry point: `uvicorn app.main:app`."""

from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import Response
from starlette.routing import Match, Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.mcp_tools import build_mcp
from app.api.routes import router, ws_router
from app.config import Settings, get_settings
from app.logging_setup import configure_logging
from app.services.maps import MapManager
from app.websocket.hub import ConnectionHub

# The console's static geojson would otherwise go out as application/octet-stream (Python's table has no entry).
mimetypes.add_type("application/geo+json", ".geojson")
mimetypes.add_type("text/javascript", ".mjs")

# Files under assets/ carry a content hash in their name, so a cached copy can never be stale.
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",  # the console has unauthenticated controls; do not let another site frame it
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware:
    """Adds the response headers a browser expects from a public console.

    Plain ASGI rather than BaseHTTPMiddleware, so it never buffers a streamed response. WebSockets pass through.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _methods_served_at(scope: Scope) -> list[str]:
    """The methods an API route serves at this request's path, when it does not serve this request's method.

    The console is mounted at "/", and Starlette prefers that mount's full match over an API route's partial one,
    so without this a GET to a POST-only API path is answered 404 by the file server instead of 405.
    """
    allowed: set[str] = set()
    for route in getattr(scope.get("app"), "routes", ()):
        if isinstance(route, Mount):
            continue
        match, _ = route.matches(scope)
        if match is Match.PARTIAL:
            allowed.update(getattr(route, "methods", None) or ())
    return sorted(allowed)


class ConsoleFiles(StaticFiles):
    """The built console, with the method and caching rules a browser app needs."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if allowed := _methods_served_at(scope):
            raise HTTPException(405, headers={"Allow": ", ".join(allowed)})
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            # the HTML (and unhashed files such as the favicon) must be revalidated, or a new deploy is never seen
            response.headers["Cache-Control"] = ASSET_CACHE_CONTROL if path.startswith("assets/") else "no-cache"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    mcp = build_mcp(lambda: app.state.map_manager.services)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager = MapManager(settings, ConnectionHub(), mcp)
        app.state.map_manager = manager
        await manager.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await manager.stop()

    app = FastAPI(title="Traffic Operations Center", version="0.1.0", lifespan=lifespan)
    # added last = outermost: headers go on every response, and compression sees the final body
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)  # the console bundle is over a megabyte uncompressed
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(router)
    app.include_router(ws_router)
    # MCP tools for agents at /mcp (streamable HTTP; stateless, so no session affinity is needed). Left alone, the
    # SDK enables DNS-rebinding protection for its default loopback host and rejects any other Host with a 421.
    mcp_security = (
        None
        if settings.mcp_dns_rebinding_protection
        else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    app.router.routes.extend(
        mcp.streamable_http_app(stateless_http=True, json_response=True, transport_security=mcp_security).routes
    )
    # The Cloud Run image builds the console into frontend/dist. Keep local development unchanged: without that
    # directory, Vite still serves the UI and proxies /api and /ws to this app.
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", ConsoleFiles(directory=frontend_dist, html=True), name="console")
    return app


configure_logging()
app = create_app()
