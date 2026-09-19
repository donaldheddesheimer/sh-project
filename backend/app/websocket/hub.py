"""WebSocket fan-out with per-client buffering.

broadcast() never blocks the caller: each client has a small queue drained by
its own task, and a slow client loses its oldest queued frames rather than
delaying everyone else.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import WebSocket, WebSocketDisconnect

CLIENT_BUFFER = 16


class _Client:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CLIENT_BUFFER)

    def offer(self, text: str) -> None:
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(text)

    async def pump(self) -> None:
        while True:
            await self.ws.send_text(await self.queue.get())


class ConnectionHub:
    def __init__(self) -> None:
        self._clients: set[_Client] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def broadcast(self, text: str) -> None:
        for client in list(self._clients):
            client.offer(text)

    async def serve(self, ws: WebSocket, hello: str) -> None:
        """Handle one connection until the client goes away."""
        await ws.accept()
        client = _Client(ws)
        client.offer(hello)
        self._clients.add(client)
        pump = asyncio.create_task(client.pump())
        try:
            while True:
                receive = asyncio.create_task(ws.receive_text())
                done, _ = await asyncio.wait({receive, pump}, return_when=asyncio.FIRST_COMPLETED)
                if pump in done:  # sending failed: connection is gone
                    receive.cancel()
                    break
                receive.result()  # client messages are ignored (keep-alives)
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.discard(client)
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
