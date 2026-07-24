"""记忆抽取器：从对话末尾提取可沉淀为长期记忆的偏好/约束/事实。

抽取策略（三级降级）：
1. 优先使用 ShoppingSummary 工具显式给出的 learned_preferences（结构化，最可靠）。
2. LLM 抽取：调 LLM 从 final_text + user_query 中抽取 JSON 数组（覆盖面广）。
3. 规则兜底：LLM 调用失败时用正则匹配"不要…""偏好…""必须…"等模式。

设计原则：抽取失败不能影响主任务返回，所有异常静默降级。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.agent.llm import get_llm
from app.memory.policy import build_memory_write
from app.memory.schemas import MemoryWrite


# LLM 抽取的 system prompt，要求只输出 JSON 数组。
MEMORY_EXTRACT_PROMPT = """你是 Glodex 的记忆抽取器。
只抽取未来购物任务可复用的稳定信息，例如用户偏好、硬约束、平台偏好、纠错。
不要保存一次性搜索结果、商品价格、闲聊、当前任务临时过程。
请只输出 JSON 数组，每项字段为 text、kind、tags、confidence。
kind 只能是 preference、constraint、correction、summary、fact。
"""


async def extract_memories(
    user_query: str,
    final_text: str,
    learned_preferences: list[str] | None = None,
) -> list[MemoryWrite]:
    """从任务结束时的最终回复中抽取可沉淀的长期记忆。

    三级降级：
    1. 工具显式给出的 learned_preferences（最优先）。
    2. LLM 结构化抽取。
    3. 规则兜底（正则匹配）。

    Args:
        user_query: 用户的原始购物需求。
        final_text: AgentLoop 的最终回复文本。
        learned_preferences: ShoppingSummary 输出的显式偏好列表。

    Returns:
        去重后的 MemoryWrite 列表，可直接传给 store.create。
    """
    writes: list[MemoryWrite] = []
    # 第一优先级：工具显式给出的偏好
    for text in learned_preferences or []:
        write = build_memory_write(text)
        if write is not None:
            writes.append(write)

    if writes:
        return writes

    # 第二优先级：LLM 结构化抽取
    try:
        response = await get_llm().ainvoke(
            [
                ("system", MEMORY_EXTRACT_PROMPT),
                (
                    "user",
                    json.dumps(
                        {
                            "user_query": user_query,
                            "final_text": final_text[:4000],
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        raw = str(getattr(response, "content", response))
        writes.extend(_parse_llm_writes(raw))
    except Exception:
        # 第三优先级：正则规则兜底
        writes.extend(_rule_based_extract(user_query))

    return _dedupe_writes(writes)


def extract_learned_preferences_from_result(result: dict[str, Any]) -> list[str]:
    """从 LangChain Agent 返回的消息列表中捞出 learned_preferences。

    遍历所有消息的 content / additional_kwargs / dict 形式，
    递归查找 learned_preferences 或 new_preferences 字段。

    Args:
        result: agent.ainvoke 的返回字典，含 "messages" 键。

    Returns:
        去重后的偏好文本列表。
    """
    values: list[str] = []
    for message in result.get("messages") or []:
        payloads = [
            getattr(message, "content", None),
            getattr(message, "additional_kwargs", None),
            message if isinstance(message, dict) else None,
        ]
        for payload in payloads:
            values.extend(_find_preferences(payload))
    return _dedupe_texts(values)


# ------------------------------------------------------------------
# LLM 抽取
# ------------------------------------------------------------------


def _parse_llm_writes(raw: str) -> list[MemoryWrite]:
    """解析 LLM 返回的 JSON 数组，失败时降级到规则抽取。"""
    stripped = _strip_json_fence(raw)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return _rule_based_extract(stripped)
    if not isinstance(payload, list):
        return []

    writes: list[MemoryWrite] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            write = MemoryWrite.model_validate(item)
        except ValidationError:
            # LLM 输出缺字段时，用 text 字段走 policy 规范化
            text = str(item.get("text", ""))
            write = build_memory_write(text)
        if write is not None:
            writes.append(write)
    return writes


# ------------------------------------------------------------------
# 规则兜底
# ------------------------------------------------------------------


def _rule_based_extract(text: str) -> list[MemoryWrite]:
    """用正则从文本中提取偏好/约束候选句，每条过 policy 过滤。

    匹配模式：
    - 否定偏好："(我|用户)?(?:不喜欢|不要|不买|避免|拒绝)..."
    - 正向偏好："(我|用户)?(?:喜欢|偏好|优先|倾向)..."
    - 硬性要求："(?:必须|只要)..."
    """
    candidates: list[str] = []
    patterns = [
        r"(?:我|用户)?(?:不喜欢|不要|不买|避免|拒绝)[^，。；\n]{1,40}",
        r"(?:我|用户)?(?:喜欢|偏好|优先|倾向)[^，。；\n]{1,40}",
        r"(?:必须|只要)[^，。；\n]{1,40}",
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, text))
    writes = [build_memory_write(candidate) for candidate in candidates]
    return [write for write in writes if write is not None]


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------


def _find_preferences(payload: Any) -> list[str]:
    """递归搜索 dict/list/str 中的 learned_preferences / new_preferences。

    兼容不同工具输出格式：有的包在 content JSON 里，有的在 additional_kwargs 里。
    """
    if payload is None:
        return []
    if isinstance(payload, dict):
        values: list[str] = []
        prefs = payload.get("learned_preferences") or payload.get("new_preferences")
        if isinstance(prefs, list):
            values.extend(str(item) for item in prefs)
        # 递归搜索嵌套结构
        for value in payload.values():
            values.extend(_find_preferences(value))
        return values
    if isinstance(payload, list):
        values: list[str] = []
        for item in payload:
            values.extend(_find_preferences(item))
        return values
    if isinstance(payload, str) and "learned_preferences" in payload:
        try:
            return _find_preferences(json.loads(payload))
        except json.JSONDecodeError:
            return []
    return []


def _strip_json_fence(text: str) -> str:
    """去除 LLM 偶尔包上的 ```json ... ``` 代码块标记。"""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _dedupe_writes(writes: list[MemoryWrite]) -> list[MemoryWrite]:
    """按 text 去重，保留首次出现的顺序。"""
    seen: set[str] = set()
    result: list[MemoryWrite] = []
    for write in writes:
        key = write.text.strip()
        if key in seen:
            continue
        seen.add(key)
        result.append(write)
    return result


def _dedupe_texts(texts: list[str]) -> list[str]:
    """按 strip 后的文本去重，保留首次出现的顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for text in texts:
        normalized = text.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
