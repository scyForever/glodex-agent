"""L0 工具侧防线：工具结果进入上下文前先瘦身。

这是整个记忆系统投入产出比最高的一层——
从源头减少 token，而不是等上下文膨胀之后再补救。

三个核心函数：
- truncate_text：纯截断，超过 max_chars 就砍掉。
- compact_tool_result：结构化精简，只保留 IMPORTANT_KEYS 中的关键字段。
- _compact_value：递归压缩 dict/list/str，去掉大段描述、评论原文、图片列表等。

设计原则：
- 按工具类型区别对待（item_search 保留 20 件、web_search 只留标题摘要）。
- 截断时加明确提示，让 LLM 知道结果被截断了。
"""

from __future__ import annotations

import json
from typing import Any


# 默认单次工具结果最大字符数（≈2000 token）。
DEFAULT_MAX_TOOL_CHARS = 8000

# 精简模式下保留的关键字段名。
# 不在这个集合中的字段在 compact_tool_result 时可能被丢弃。
IMPORTANT_KEYS = {
    "item_id",
    "title",
    "platform",
    "price_cny",
    "final_price_cny",
    "rating",
    "sales",
    "url",
    "reason",
    "reasons",
    "flags",
    "summary",
    "source",
}


def truncate_text(text: str, max_chars: int = DEFAULT_MAX_TOOL_CHARS) -> str:
    """L0 防线：工具结果过长时从源头截断。

    保留前 max_chars 个字符，末尾追加截断提示。

    Args:
        text: 工具返回的原始文本。
        max_chars: 最大字符数，默认 8000。

    Returns:
        截断后的文本。
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[工具结果过长，已由 memory.tool_guard 截断]"


def compact_tool_result(result: Any, max_chars: int = DEFAULT_MAX_TOOL_CHARS) -> str:
    """把工具结果转成较短 JSON 文本，只保留关键字段。

    流程：递归精简 dict/list → 序列化为 JSON → 截断到 max_chars。

    Args:
        result: 工具返回的原始对象（dict / Pydantic model / list 等）。
        max_chars: 最大字符数。

    Returns:
        精简并截断后的 JSON 字符串。
    """
    compacted = _compact_value(result)
    text = json.dumps(compacted, ensure_ascii=False, default=str)
    return truncate_text(text, max_chars=max_chars)


def _compact_value(value: Any) -> Any:
    """递归压缩任意嵌套结构。

    规则：
    - dict：如果 key 在 IMPORTANT_KEYS 中则保留，否则最多保留前 8 个 key。
      但如果 dict 本身 ≤ 12 个 key，则全部保留（可能是小对象）。
    - list：最多保留前 20 个元素。
    - str：超过 1200 字符则截断。
    - 其他类型原样返回。
    """
    if isinstance(value, dict):
        kept = {
            key: _compact_value(item)
            for key, item in value.items()
            if key in IMPORTANT_KEYS or len(value) <= 12
        }
        # 如果按 IMPORTANT_KEYS 过滤后为空，兜底保留前 8 个 key
        return kept or {key: _compact_value(value[key]) for key in list(value)[:8]}
    if isinstance(value, list):
        return [_compact_value(item) for item in value[:20]]
    if isinstance(value, str):
        return truncate_text(value, max_chars=1200)
    return value
