from __future__ import annotations

import json
import time
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, ValidationError

from app.agent.llm import get_llm
from app.agent.prompts import get_planner_prompt
from app.api.monitor import monitor


class ShoppingPlan(BaseModel):
    """Planner 拆解后的国内购物任务结构。"""

    intent: Literal["shopping", "chat", "other"] = "shopping"
    category: str | None = None
    budget_cny: float | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    platform_preferences: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    need_category_insight: bool = False
    need_web_search: bool = False
    should_fork: bool = False
    fork_demands: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


@tool
async def planner(user_query: str) -> ShoppingPlan:
    """把用户输入拆解成国内电商 AgentLoop 可执行的结构化购物计划。"""
    await monitor.report_tool_start("planner", {"user_query": user_query})
    start = time.time()

    prompt = get_planner_prompt()
    response = await get_llm().ainvoke(
        [
            ("system", prompt),
            (
                "user",
                _build_planner_instruction(user_query),
            ),
        ]
    )
    raw_text = str(getattr(response, "content", response))
    plan = _parse_plan(raw_text, user_query)

    await monitor.report_tool_end("planner", int((time.time() - start) * 1000))
    return plan


def _build_planner_instruction(user_query: str) -> str:
    """构造要求模型输出 JSON 的 Planner 指令。"""
    return (
        "请把下面的用户输入拆成严格 JSON，不要输出 Markdown。\n"
        "字段包括：intent, category, budget_cny, hard_constraints, soft_preferences, "
        "platform_preferences, search_queries, need_category_insight, need_web_search, "
        "should_fork, fork_demands, missing_info。\n"
        "intent 只能是 shopping/chat/other。\n"
        "国内电商平台可包括 jd、taobao、tmall、pdd、1688、douyin、xiaohongshu。\n"
        "只有多平台可并行检索、上下文需要隔离、或子任务明显复杂时 should_fork 才为 true。\n"
        "如果不是购物意图，intent 设为 chat 或 other，并尽量留空购物字段。\n\n"
        f"用户输入：{user_query}"
    )


def _parse_plan(raw_text: str, user_query: str) -> ShoppingPlan:
    """解析模型 JSON；失败时返回保守购物计划。"""
    try:
        payload = json.loads(_strip_json_fence(raw_text))
        return ShoppingPlan.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        return ShoppingPlan(
            intent="shopping",
            search_queries=[user_query],
            missing_info=["Planner 输出无法解析，已使用原始问题作为检索词"],
        )


def _strip_json_fence(text: str) -> str:
    """兼容模型偶尔包上的 ```json 代码块。"""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
