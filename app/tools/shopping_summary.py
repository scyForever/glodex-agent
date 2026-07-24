from __future__ import annotations

import json
import time

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.llm import get_llm
from app.agent.prompts import get_shopping_summary_prompt
from app.api.monitor import monitor
from app.tools.item_picker import PickedItem


class ShoppingSummaryOutput(BaseModel):
    """ShoppingSummary 的结构化输出。"""

    final_text: str
    picks: list[PickedItem]
    learned_preferences: list[str] = Field(default_factory=list)


@tool
async def shopping_summary(
    picks: list[PickedItem],
    user_query: str,
    new_preferences: list[str] | None = None,
) -> ShoppingSummaryOutput:
    """生成最终购物清单和选购理由，这是购物链路的终结性工具。

    Args:
        picks: 来自 ItemPicker 的精选商品。
        user_query: 用户最初的购物需求原文。
        new_preferences: 本轮识别出的新偏好，后续可写入长期记忆。

    Returns:
        final_text: 给前端展示的 Markdown 最终答复。
        picks: 本次推荐的精选商品。
        learned_preferences: 本轮沉淀的新偏好。
    """
    await monitor.report_tool_start(
        "shopping_summary",
        {
            "picks_count": len(picks),
            "learned_preferences": new_preferences or [],
        },
    )
    start = time.time()

    if not picks:
        final_text = _empty_summary(user_query)
    else:
        final_text = await _generate_summary_text(
            picks=picks,
            user_query=user_query,
        )

    await monitor.report_tool_end(
        "shopping_summary",
        int((time.time() - start) * 1000),
    )
    return ShoppingSummaryOutput(
        final_text=final_text,
        picks=picks,
        learned_preferences=new_preferences or [],
    )


async def _generate_summary_text(picks: list[PickedItem], user_query: str) -> str:
    """调用 LLM 生成最终 Markdown 答复。"""
    prompt = get_shopping_summary_prompt()
    payload = {
        "user_query": user_query,
        "picks": [pick.model_dump() for pick in picks],
        # 国内购物链路只总结购买建议，不重新搜索、不重新筛选、不发起新工具调用。
        "requirements": [
            "使用 Markdown 输出",
            "最多推荐 3 件商品",
            "每件商品说明价格、平台、推荐理由和需要注意的风险",
            "不要提及关税、跨境直邮、免税等跨境购物信息",
            "如果存在 flags，要用自然语言提醒用户二次确认",
        ],
    }
    response = await get_llm().ainvoke(
        [
            ("system", prompt),
            ("user", json.dumps(payload, ensure_ascii=False)),
        ]
    )
    return str(getattr(response, "content", response))


def _empty_summary(user_query: str) -> str:
    """没有精选结果时给出可收敛的最终说明，避免主 loop 继续空转。"""
    return (
        "暂时没有筛出足够稳妥的商品。\n\n"
        f"- 原始需求：{user_query}\n"
        "- 建议放宽部分硬性条件，或补充预算、材质、平台偏好后重新检索。"
    )
