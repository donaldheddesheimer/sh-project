"""Optional OpenAI-compatible embedding client for semantic memory recall.

The endpoint is deliberately small: the store owns persistence, cache invalidation and the
structured fallback, while this class only turns one or more texts into validated vectors.
"""

from __future__ import annotations

import math

import httpx2


class EmbeddingError(RuntimeError):
    """The embedding endpoint could not produce a usable vector response."""


class NimEmbedder:
    """NVIDIA NIM's OpenAI-compatible ``/embeddings`` endpoint."""

    def __init__(self, base_url: str, model: str, api_key: str | None, timeout_s: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        """Embed ``texts`` as a query or passage, preserving the endpoint's input order."""
        if not texts:
            return []
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        body = {"model": self.model, "input": texts, "input_type": input_type}
        try:
            async with httpx2.AsyncClient(timeout=self._timeout_s) as http:
                response = await http.post(f"{self.base_url}/embeddings", json=body, headers=headers)
        except Exception as exc:  # noqa: BLE001 - this optional client normalizes transport-specific failures
            raise EmbeddingError(f"embedding request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise EmbeddingError(f"embedding endpoint returned {response.status_code}")
        try:
            rows = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
            vectors = [_vector(row["embedding"]) for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("embedding endpoint returned an invalid response") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError(f"embedding endpoint returned {len(vectors)} vectors for {len(texts)} inputs")
        return vectors


def _vector(value: object) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ValueError("embedding is empty")
    vector = [float(item) for item in value]
    if not all(math.isfinite(item) for item in vector):
        raise ValueError("embedding contains a non-finite value")
    return vector
