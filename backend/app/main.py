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
    mcp = build_mcp(lambda: app.state.scenarios)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        city, scenarios = build_services(settings, ConnectionHub())
        app.state.city = city
        app.state.scenarios = scenarios
        await city.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await scenarios.shutdown()
            await city.stop()

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
