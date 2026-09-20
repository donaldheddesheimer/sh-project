"""Model-call log: proof that a hosted model was reached, and what it answered.

The operations log records what the agent decided; this records the HTTP calls behind those decisions, so
an operator can tell "Nemotron proposed nothing" from "Nemotron was never called" or "Nemotron answered 503".
The console renders it as the Model calls tab and ``GET /api/model-calls`` serves the same entries to curl.

One entry is published twice: as ``pending`` when the request goes out, and again with its outcome. Both carry
the same id, so a client replaces the entry instead of appending a second one, and a call still in flight is
visible while it runs. Like the ops log and the trend, the log belongs to one service graph: a map switch
starts a fresh one.
"""

from __future__ import annotations

import itertools
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from app.models.api import ModelCall, ModelCallStatus

MAX_DETAIL_CHARS = 400


def preview(text: str | None) -> str | None:
    """One collapsed line of a model reply or an error, short enough for a log row."""
    if not text:
        return None
    collapsed = " ".join(text.split())
    return collapsed[: MAX_DETAIL_CHARS - 1] + "…" if len(collapsed) > MAX_DETAIL_CHARS else collapsed


class ModelCallHandle:
    """One in-flight call. Every method republishes the same entry with what is now known."""

    def __init__(self, call: ModelCall, publish: Callable[[ModelCall], None]):
        self.call = call
        self._publish = publish
        self._started = time.monotonic()

    @property
    def settled(self) -> bool:
        return self.call.status is not ModelCallStatus.PENDING

    def responded(self, http_status: int) -> None:
        """An HTTP reply arrived; the call is not finished until it is parsed."""
        self.call.http_status = http_status
        self._republish()

    def retrying(self) -> None:
        """The reply was retryable (a busy endpoint); another attempt follows after a back-off."""
        self.call.attempts += 1
        self._republish()

    def succeeded(
        self,
        *,
        response_chars: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.call.status = ModelCallStatus.OK
        self.call.response_chars = response_chars
        self.call.prompt_tokens = prompt_tokens
        self.call.completion_tokens = completion_tokens
        self.call.detail = preview(detail)
        self._republish()

    def failed(self, detail: str) -> None:
        self.call.status = ModelCallStatus.ERROR
        self.call.detail = preview(detail)
        self._republish()

    def _republish(self) -> None:
        self.call.duration_ms = int((time.monotonic() - self._started) * 1000)
        self._publish(self.call)


def _no_sink(call: ModelCall) -> None:
    """Default sink: the log still records calls made before a CityService attaches a broadcaster."""


class ModelCallLog:
    """The recent model calls, kept in memory and broadcast as they change.

    Built before the service graph it belongs to (the model clients are constructed first), so the
    broadcaster and the simulation clock are attached afterwards by ``CityService``.
    """

    def __init__(self, maxlen: int = 100):
        self._calls: deque[ModelCall] = deque(maxlen=maxlen)
        self._ids = itertools.count(1)
        self._publish: Callable[[ModelCall], None] = _no_sink
        self._clock: Callable[[], float | None] = lambda: None

    def attach(self, publish: Callable[[ModelCall], None], clock: Callable[[], float | None]) -> None:
        self._publish = publish
        self._clock = clock

    def recent(self, limit: int = 50) -> list[ModelCall]:
        if limit <= 0:  # [-0:] is the whole log, and a negative slice drops from the front
            return []
        return list(self._calls)[-limit:]

    @contextmanager
    def call(
        self, *, provider: str, role: str, model: str, purpose: str, endpoint: str, request_chars: int | None = None
    ) -> Iterator[ModelCallHandle]:
        """Record one call for the length of the block. An escaping exception is recorded as the failure."""
        entry = ModelCall(
            id=next(self._ids),
            timestamp=datetime.now(UTC),
            sim_time=self._clock(),
            provider=provider,
            role=role,
            model=model,
            purpose=purpose,
            endpoint=endpoint,
            status=ModelCallStatus.PENDING,
            request_chars=request_chars,
        )
        self._calls.append(entry)
        handle = ModelCallHandle(entry, self._publish)
        self._publish(entry)
        try:
            yield handle
        except BaseException as exc:
            # Including cancellation and timeouts: an entry left pending would read as a call still running.
            if not handle.settled:
                handle.failed(f"{type(exc).__name__}: {exc}")
            raise
        if not handle.settled:  # a caller that returned without reporting an outcome
            handle.succeeded()
