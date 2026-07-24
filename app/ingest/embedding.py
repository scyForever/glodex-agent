from __future__ import annotations

import os
from typing import Any

import httpx

from app.ingest.env import load_dotenv


class EmbeddingUnavailable(RuntimeError):
    """Raised when the embedding service cannot be used."""


class EmbeddingClient:
    """OpenAI-compatible embedding client."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.environ.get("EMBEDDING_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.base_url = (
            base_url
            or os.environ.get("EMBEDDING_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.model = model or os.environ.get("EMBEDDING_MODEL") or "text-embedding-v3"
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    async def embed(self, text: str) -> list[float]:
        if not self.is_configured():
            raise EmbeddingUnavailable("EMBEDDING_API_KEY/OPENAI_API_KEY is not configured")
        if not text.strip():
            raise EmbeddingUnavailable("embedding text is empty")

        payload = {"model": self.model, "input": text}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        embedding = _first_embedding(data)
        if not embedding:
            raise EmbeddingUnavailable("embedding model returned an empty vector")
        return embedding


def _first_embedding(payload: dict[str, Any]) -> list[float]:
    rows = payload.get("data") or []
    if not rows or not isinstance(rows[0], dict):
        return []
    vector = rows[0].get("embedding") or []
    if not isinstance(vector, list):
        return []
    return [float(value) for value in vector]
