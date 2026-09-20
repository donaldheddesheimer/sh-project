"""Shared chat-client contract for model-backed episode analysts and reviewers."""

from __future__ import annotations

from typing import Protocol


class ChatClient(Protocol):
    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 4096, *, purpose: str = "chat"
    ) -> dict:
        """One completion. ``purpose`` labels the call in the model-call log (propose, recommend, decide, review)."""
        ...
