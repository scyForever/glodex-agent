from __future__ import annotations

import os
from typing import Any, Literal

import httpx


MaishouSource = Literal["1", "2", "3"]

MAISHOU_SEARCH_URL = "https://appapi.maishou88.com/api/v1/homepage/searchList"
MAISHOU_DETAIL_URL = "https://appapi.maishou88.com/api/v3/goods/detail"
MAISHOU_TARGET_URL = "https://msapi.maishou88.com/api/v1/share/getTargetUrl"

SOURCE_BY_PLATFORM = {
    "taobao": "1",
    "tb": "1",
    "tmall": "1",
    "jd": "2",
    "jingdong": "2",
    "pdd": "3",
    "pinduoduo": "3",
}


class MaishouClient:
    """Small async client for Maishou search/detail APIs."""

    def __init__(
        self,
        invite_code: str | None = None,
        openid: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.invite_code = invite_code or os.environ.get("MAISHOU_INVITE_CODE") or "6110440"
        self.openid = openid or os.environ.get("MAISHOU_OPENID") or "564bdce0fa408fc9e1d5d42fd022ef0b"
        self.timeout = timeout

    async def search(
        self,
        keyword: str,
        platform: str = "taobao",
        page: int = 1,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        source = platform_to_source(platform)
        data: dict[str, Any] = {
            "isCoupon": 0,
            "keyword": keyword,
            "openid": self.openid,
            "order": "desc",
            "page": page,
            "pddListId": "",
            "sort": "",
            "sourceType": source,
            "user_id": "",
        }
        if page_size:
            data["pageSize"] = page_size

        async with httpx.AsyncClient(headers=_headers(self.openid), timeout=self.timeout) as client:
            response = await client.post(MAISHOU_SEARCH_URL, data=data)
            response.raise_for_status()
            payload = response.json()

        rows = payload.get("data") or []
        if not isinstance(rows, list):
            return []

        results: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                results.append({**row, "sourceType": row.get("sourceType") or source})
        return results

    async def detail(self, goods_id: str, platform: str = "taobao") -> dict[str, Any]:
        source = platform_to_source(platform)
        params = {
            "goodsId": str(goods_id),
            "sourceType": source,
            "inviteCode": self.invite_code,
            "supplierCode": "",
            "activityId": "",
            "isShare": "1",
            "token": "",
        }

        async with httpx.AsyncClient(headers=_headers(self.openid), timeout=self.timeout) as client:
            detail_response = await client.post(
                MAISHOU_DETAIL_URL,
                json={**params, "keyword": "", "usageScene": 5},
            )
            detail_response.raise_for_status()
            detail_payload = detail_response.json()
            detail = detail_payload.get("data") or {}
            if not isinstance(detail, dict):
                detail = {}

            target_response = await client.post(
                MAISHOU_TARGET_URL,
                json={**params, "isDirectDetail": 0},
            )
            target_response.raise_for_status()
            target_payload = target_response.json()
            target = target_payload.get("data") or {}
            if not isinstance(target, dict):
                target = {}

        return {
            **detail,
            "goodsId": str(goods_id),
            "sourceType": source,
            "url": target.get("appUrl") or target.get("schemaUrl") or detail.get("url"),
            "share_command": target.get("kl"),
        }


def platform_to_source(platform: str) -> MaishouSource:
    value = str(platform or "taobao").lower()
    if value in {"1", "2", "3"}:
        return value  # type: ignore[return-value]
    return SOURCE_BY_PLATFORM.get(value, "1")  # type: ignore[return-value]


def _headers(openid: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Referer": "https://hnbc018.kuaizhan.com/",
        "User-Agent": "MaiShouApp/3.7.7 (iPhone; iOS 26.3; Scale/3.00)",
        "openid": openid,
        "version": "3.7.7.2",
    }
