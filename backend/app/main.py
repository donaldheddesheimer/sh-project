"""FastAPI application entry point: `uvicorn app.main:app`."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.mcp_tools import build_mcp
from app.api.routes import router, ws_router
from app.config import Settings, get_settings
from app.providers import build_services
from app.websocket.hub import ConnectionHub


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    mcp = build_mcp(lambda: app.state.services)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = build_services(settings, ConnectionHub(), mcp)
        app.state.services = services
        app.state.city = services.city
        app.state.scenarios = services.scenarios
        app.state.implementor = services.implementor
        app.state.episodes = services.episodes
        app.state.memory = services.memory
        services.episodes.arm_at_startup()  # DEMO_SCRIPT: before the first boot, so it plays the early crashes
        await services.city.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await services.episodes.shutdown()
            await services.scenarios.shutdown()
            await services.city.stop()

    app = FastAPI(title="Traffic Operations Center", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
    )
    app.include_router(router)
    app.include_router(ws_router)
    # MCP tools for agents at /mcp (streamable HTTP; stateless, so no session affinity is needed)
    app.router.routes.extend(mcp.streamable_http_app(stateless_http=True, json_response=True).routes)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
