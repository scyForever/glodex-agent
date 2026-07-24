from __future__ import annotations

import os
from typing import Any


class TowerUnavailable(RuntimeError):
    """Raised when an online tower encoder is not available."""


class QueryTowerClient:
    """HTTP client for query embedding service.

    If no dedicated tower endpoint is configured, it falls back to the same
    OpenAI-compatible embedding client used by ingestion. This keeps Milvus
    product vectors and query vectors in the same vector space.
    """

    def __init__(self) -> None:
        try:
            from app.ingest.env import load_dotenv

            load_dotenv()
        except Exception:
            pass
        self.endpoint = os.environ.get("TOWER_QUERY_ENDPOINT")
        self.timeout = float(os.environ.get("TOWER_TIMEOUT_SEC", "5.0"))

    def is_configured(self) -> bool:
        if self.endpoint:
            return True
        return bool(os.environ.get("EMBEDDING_API_KEY") or os.environ.get("OPENAI_API_KEY"))

    async def encode_query(self, query: str) -> list[float]:
        if not self.endpoint:
            return await _embed_with_ingest_client(query)
        try:
            payload = await _post_embedding(
                endpoint=self.endpoint,
                body={"query": query},
                timeout=self.timeout,
            )
            return _parse_embedding(payload)
        except Exception:
            if os.environ.get("EMBEDDING_API_KEY") or os.environ.get("OPENAI_API_KEY"):
                return await _embed_with_ingest_client(query)
            raise


async def _post_embedding(
    endpoint: str,
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:
        raise TowerUnavailable("httpx is not installed") from exc

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, json=body)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise TowerUnavailable("tower response must be a JSON object")
    return payload


def _parse_embedding(payload: dict[str, Any]) -> list[float]:
    embedding = payload.get("embedding")
    if not isinstance(embedding, list):
        raise TowerUnavailable("tower response missing embedding list")
    try:
        return [float(value) for value in embedding]
    except (TypeError, ValueError) as exc:
        raise TowerUnavailable("tower embedding contains non-numeric values") from exc


async def _embed_with_ingest_client(query: str) -> list[float]:
    try:
        from app.ingest.embedding import EmbeddingClient
    except ImportError as exc:
        raise TowerUnavailable("embedding client is not available") from exc

    try:
        return await EmbeddingClient().embed(query)
    except Exception as exc:
        raise TowerUnavailable("embedding fallback failed") from exc


query_tower_client = QueryTowerClient()
