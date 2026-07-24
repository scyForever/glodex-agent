"""L2 上下文压缩：对 breakpoint 之前的旧历史区执行截断、滑窗、摘要。

压缩策略（三级递进）：
  1. tool_trim：对旧历史区中的 tool/function 消息做字符截断。
  2. sliding_window：如果 tool_trim 后仍超阈值，保留最近的消息 + 所有 system 消息。
  3. summary：第一版暂不启用（不调 LLM 做摘要），后续接入。

当前只做前两级，摘要压缩留到后续版本。

设计原则：
  - 只在 token 估算超阈值时才触发，不压缩时不改消息列表。
  - 逐消息处理，不修改 cache point 和近期保护区消息。
  - 压缩失败时降级到 sliding_window（保留 system + 最近 N 条）。
"""

from __future__ import annotations

from typing import Any

from app.memory.breakpoint import compute_breakpoint, is_cache_point
from app.memory.schemas import CompressionResult
from app.memory.tool_guard import truncate_text


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数，第一版不引入 tokenizer。

    使用简化的 1 token ≈ 4 字符 估算，误差在 ±20% 以内，
    对压缩决策来说够用（只需要判断是否超阈值，不需要精确计数）。

    Args:
        text: 任意文本。

    Returns:
        估算的 token 数，最小返回 1。
    """
    return max(1, len(text) // 4)


async def compress_messages(
    messages: list[Any],
    max_tokens: int = 12000,
    keep_recent_tool_calls: int = 3,
    max_tool_chars: int = 8000,
) -> CompressionResult:
    """在 OpenAI 兼容协议下做工程压缩，不写入 Anthropic cache_control。

    压缩流程：
    1. 估算总 token，未超阈值直接返回（不压缩）。
    2. 计算 breakpoint 分界线。
    3. 对 breakpoint 之前的旧历史区做 tool/function 消息截断、长文本截断。
    4. 如果仍超阈值，再做 sliding_window（保留 system + 最近消息）。
    5. 当前暂不做 LLM 摘要压缩。

    Args:
        messages: 当前对话消息列表。
        max_tokens: 上下文 token 预算上限，默认 12000。
        keep_recent_tool_calls: 保留最近几次工具调用不被压缩，默认 3。
        max_tool_chars: 工具结果最大字符数，默认 8000。

    Returns:
        CompressionResult 含压缩后消息列表、前后 token 数、策略等。
    """
    original_tokens = _estimate_messages_tokens(messages)

    # 未超阈值，不压缩
    if original_tokens <= max_tokens:
        return CompressionResult(
            messages=messages,
            original_tokens=original_tokens,
            compressed_tokens=original_tokens,
            breakpoint_idx=len(messages),
            strategy="none",
        )

    breakpoint_idx = compute_breakpoint(messages, keep_recent_tool_calls)
    if breakpoint_idx >= len(messages):
        # 没有可压缩区（breakpoint 在最后），用 fallback 至少保留 system
        breakpoint_idx = _fallback_breakpoint(messages)

    # 旧历史区可压缩，近期保护区不动。
    compressible_part = messages[:breakpoint_idx]
    protected_part = messages[breakpoint_idx:]

    # 第一轮：逐消息压缩（tool 截断 + 长文本截断）
    compressed_part = [
        _compress_message(message, index, len(messages), max_tool_chars)
        for index, message in enumerate(compressible_part)
    ]
    next_messages = compressed_part + protected_part
    compressed_tokens = _estimate_messages_tokens(next_messages)

    # 第二轮：如果仍超阈值，做 sliding_window
    if compressed_tokens > max_tokens:
        next_messages = _sliding_window(next_messages, max_messages=max(6, keep_recent_tool_calls * 4))
        compressed_tokens = _estimate_messages_tokens(next_messages)
        strategy = "tool_trim+sliding_window"
    else:
        strategy = "tool_trim"

    return CompressionResult(
        messages=next_messages,
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        breakpoint_idx=breakpoint_idx,
        strategy=strategy,
    )


def _compress_message(message: Any, index: int, total: int, max_tool_chars: int) -> Any:
    """压缩单条消息。

    - cache point 消息原样保留。
    - tool/function 角色的消息做字符截断。
    - 超过 3000 字符的普通消息也截断。
    """
    if is_cache_point(message, index, total):
        return message

    role = _message_role(message)
    content = _message_content(message)
    if role in {"tool", "function"}:
        return _replace_content(message, truncate_text(content, max_chars=max_tool_chars))
    if len(content) > 3000:
        return _replace_content(message, truncate_text(content, max_chars=3000))
    return message


def _sliding_window(messages: list[Any], max_messages: int) -> list[Any]:
    """滑窗：保留所有 system 消息 + 最近 max_messages 条，去重后返回。

    这是压缩失败时的兜底策略，保证上下文至少不会爆。
    """
    system_messages = [message for message in messages if _message_role(message) == "system"]
    tail = messages[-max_messages:]
    merged: list[Any] = []
    seen_ids: set[int] = set()
    for message in system_messages + tail:
        identity = id(message)
        if identity in seen_ids:
            continue
        seen_ids.add(identity)
        merged.append(message)
    return merged


def _fallback_breakpoint(messages: list[Any]) -> int:
    """没有足够工具调用但已超阈值时，至少保留开头的 system 稳定区。

    返回 system 消息段之后的第一个位置作为分界线。
    """
    index = 0
    while index < len(messages) and _message_role(messages[index]) == "system":
        index += 1
    return max(1, index)


def _estimate_messages_tokens(messages: list[Any]) -> int:
    """估算整个消息列表的 token 总数。"""
    return sum(estimate_tokens(_message_content(message)) for message in messages)


def _message_role(message: Any) -> str:
    """兼容 dict 和 LangChain Message 对象，提取 role。"""
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "type", getattr(message, "role", "")))


def _message_content(message: Any) -> str:
    """兼容 dict 和 LangChain Message 对象，提取 content。"""
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return str(getattr(message, "content", message))


def _replace_content(message: Any, content: str) -> Any:
    """创建消息的副本，仅替换 content 字段。

    兼容 dict（浅拷贝 + 覆盖）和 Pydantic model（model_copy）。
    如果都不是，直接返回截断字符串本身作为兜底。
    """
    if isinstance(message, dict):
        copied = dict(message)
        copied["content"] = content
        return copied
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": content})
    return content
