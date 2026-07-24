from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_DINGDANXIA_BASE_URL = "https://api.tbk.dingdanxia.com"


class DingdanxiaClient:
    """订单侠 API 客户端。

    当前只封装原始请求能力；不同接口的字段映射放在 normalizer.py 中处理。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("DINGDANXIA_API_KEY", "")
        self.base_url = (base_url or os.environ.get("DINGDANXIA_BASE_URL") or DEFAULT_DINGDANXIA_BASE_URL).rstrip("/")

    async def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """异步 GET 调用订单侠接口，返回原始 JSON。"""
        return await asyncio.to_thread(self._get_sync, path, params)

    def _get_sync(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("缺少 DINGDANXIA_API_KEY，无法调用订单侠 API。")

        merged_params = {
            **params,
            "apikey": self.api_key,
        }
        url = f"{self.base_url}/{path.lstrip('/')}?{urlencode(merged_params)}"
        request = Request(url, method="GET")

        try:
            with urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"订单侠 API 调用失败：{exc}") from exc

        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise RuntimeError("订单侠 API 返回不是 JSON object。")
        return payload


dingdanxia_client = DingdanxiaClient()
