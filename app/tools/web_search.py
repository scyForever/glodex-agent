from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.api.monitor import monitor


BOCHA_WEB_SEARCH_ENDPOINT = "https://api.bochaai.com/v1/web-search"


class WebSearchOutput(BaseModel):
    """Bocha Web Search 的原始搜索结果包装。"""

    query: str
    provider: str = "bocha"
    raw_response: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


@tool
async def web_search(
    query: str,
    count: int = 10,
    freshness: Literal["noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"] = "noLimit",
    summary: bool = True,
) -> WebSearchOutput:
    """调用 Bocha Web Search API 获取原始网页搜索结果。"""
    await monitor.report_tool_start(
        "web_search",
        {
            "query": query,
            "count": count,
            "freshness": freshness,
            "summary": summary,
        },
    )
    start = time.time()

    api_key = os.environ.get("BOCHA_API_KEY")
    if not api_key:
        await monitor.report_tool_end("web_search", int((time.time() - start) * 1000))
        return WebSearchOutput(
            query=query,
            error="缺少 BOCHA_API_KEY，无法调用 Bocha Web Search API。",
        )

    endpoint = os.environ.get("BOCHA_WEB_SEARCH_ENDPOINT", BOCHA_WEB_SEARCH_ENDPOINT)
    payload = {
        "query": query,
        "summary": summary,
        "freshness": freshness,
        "count": max(1, min(count, 20)),
    }

    try:
        raw_response = await asyncio.to_thread(
            _post_bocha_web_search,
            endpoint,
            api_key,
            payload,
        )
        output = WebSearchOutput(query=query, raw_response=raw_response)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        output = WebSearchOutput(query=query, error=str(exc))

    await monitor.report_tool_end("web_search", int((time.time() - start) * 1000))
    return output


def _post_bocha_web_search(
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """同步调用 Bocha API；外层通过 asyncio.to_thread 避免阻塞事件循环。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(request, timeout=10) as response:
        response_body = response.read().decode("utf-8")
        raw_response = json.loads(response_body)

    # TODO: 后续在这里之后增加去重、来源分级、低质结果过滤、摘要清洗和可信度判断。
    # 当前版本只负责拿到 Bocha API 原始结果，保持原始字段，方便后续设计清洗层。
    return raw_response
