from __future__ import annotations

import os

from app.recall.tower_query import TowerUnavailable, _parse_embedding, _post_embedding


class UserTowerClient:
    """HTTP client for user embedding service."""

    def __init__(self) -> None:
        self.endpoint = os.environ.get("TOWER_USER_ENDPOINT")
        self.timeout = float(os.environ.get("TOWER_TIMEOUT_SEC", "5.0"))

    def is_configured(self) -> bool:
        return bool(self.endpoint)

    async def encode_user(self, user_id: str) -> list[float]:
        if not self.endpoint:
            raise TowerUnavailable("TOWER_USER_ENDPOINT is not configured")
        payload = await _post_embedding(
            endpoint=self.endpoint,
            body={"user_id": user_id},
            timeout=self.timeout,
        )
        return _parse_embedding(payload)


user_tower_client = UserTowerClient()
