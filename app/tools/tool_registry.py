from __future__ import annotations

from typing import Any

# TODO: 各工具实现完成后，取消对应 import 注释，并加入 FULL_TOOL_SET。
from app.tools.planner import planner
from app.tools.chat_fallback import chat_fallback
from app.tools.web_search import web_search
# from app.tools.category_insight import category_insight
from app.tools.item_picker import item_picker
from app.tools.item_search import item_search
from app.tools.price_compare import price_compare
# from app.tools.shipping_calc import shipping_calc
from app.tools.shopping_summary import shopping_summary

from app.agent.dispatch_tool import dispatch_tool


# 主 AgentLoop 和子 AgentLoop 必须共用这一份工具集，保证同质 fork。
FULL_TOOL_SET: list[Any] = [
    planner,
    chat_fallback,
    web_search,
    # category_insight,
    item_search,
    item_picker,
    price_compare,
    # shipping_calc,
    shopping_summary,
    dispatch_tool,
]
