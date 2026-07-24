"""L1 Cache Breakpoint：计算可压缩历史区和近期保护区的分界线。

这是工程概念，不是 Anthropic 的 cache_control API 参数。

核心思想：
  把上下文分成两部分——
  - Breakpoint 之前（历史区）：允许截断、摘要或滑窗。
  - Breakpoint 之后（近期保护区）：保留最近工具结果和当前任务上下文。

压缩时默认保护：
  1. system prompt。
  2. 最近 K 次工具调用和工具结果。
  3. 当前用户消息。
  4. 包含预算/品类/硬约束等关键词的关键消息。
"""

from __future__ import annotations

from typing import Any


def compute_breakpoint(messages: list[Any], keep_recent_tool_calls: int = 3) -> int:
    """计算 Cache Breakpoint 分界索引。

    规则：找到倒数第 keep_recent_tool_calls 个 tool/function 消息的位置，
    该位置之前的内容属于可压缩历史区，该位置之后属于近期保护区。

    如果工具调用数 ≤ keep_recent_tool_calls，则没有明确旧工具历史可压缩，
    返回 len(messages)，由 compressor 走 fallback 策略。

    Args:
        messages: 当前对话的完整消息列表。
        keep_recent_tool_calls: 保留最近几次工具调用，默认 3。

    Returns:
        分界索引，messages[:breakpoint] 是可压缩历史区，
        messages[breakpoint:] 是近期保护区。返回 len(messages) 表示没有
        足够旧工具历史可供按工具调用切分。
    """
    tool_indices = [
        index
        for index, message in enumerate(messages)
        if _message_role(message) in {"tool", "function"}
    ]
    # 工具调用太少，不压缩
    if len(tool_indices) <= keep_recent_tool_calls:
        return len(messages)
    # 第 N 次工具调用的位置之前可压缩
    return tool_indices[-keep_recent_tool_calls]


def is_cache_point(message: Any, index: int, total: int, recent_user_count: int = 3) -> bool:
    """判断某条消息是否属于保护点，不应参与内容截断。

    满足任一条件即为 cache point：
    1. role 为 system（保护 system prompt）。
    2. 最近 recent_user_count 轮内的 user 消息（保护当前意图）。
    3. 包含关键词"预算""不要""必须""只要""品类""平台"（保护关键参数）。

    Args:
        message: 待判断的消息。
        index: 消息在列表中的位置。
        total: 消息列表总长度。
        recent_user_count: 视为"近期"的轮次数，默认 3。

    Returns:
        True 表示该消息必须保留，不参与压缩。
    """
    role = _message_role(message)
    # system prompt 永远保留
    if role == "system":
        return True
    # 最近的用户消息保留（每一轮约 2 条消息：user + assistant/tool）
    if role == "user" and total - index <= recent_user_count * 2:
        return True
    # 包含关键参数的消息保留
    content = _message_content(message)
    return any(keyword in content for keyword in ["预算", "不要", "必须", "只要", "品类", "平台"])


def _message_role(message: Any) -> str:
    """兼容 dict 和 LangChain Message 对象，提取 role。"""
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "type", getattr(message, "role", "")))


def _message_content(message: Any) -> str:
    """兼容 dict 和 LangChain Message 对象，提取 content。

    注意最后兜底用 message 本身当字符串：有些消息对象的 __str__ 就是 content。
    """
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return str(getattr(message, "content", message))
