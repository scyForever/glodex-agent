from __future__ import annotations

import time

from langchain_core.tools import tool
from pydantic import BaseModel

from app.agent.llm import get_llm
from app.api.monitor import monitor


class ChatFallbackOutput(BaseModel):
    """非购物意图或闲聊兜底输出。"""

    reply: str


@tool
async def chat_fallback(user_query: str) -> ChatFallbackOutput:
    """处理闲聊和非购物意图，不发起商品搜索。"""
    await monitor.report_tool_start("chat_fallback", {"user_query": user_query})
    start = time.time()

    response = await get_llm().ainvoke(
        [
            (
                "system",
                "你是国内电商购物 Agent 的闲聊兜底工具。"
                "请用简洁中文回复用户。"
                "不要编造商品、价格、库存或平台信息。"
                "如果用户其实想购物，请温和引导其补充预算、品类和偏好。",
            ),
            ("user", user_query),
        ]
    )
    reply = str(getattr(response, "content", response))

    await monitor.report_tool_end(
        "chat_fallback",
        int((time.time() - start) * 1000),
    )
    return ChatFallbackOutput(reply=reply)
