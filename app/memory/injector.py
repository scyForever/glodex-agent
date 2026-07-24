"""长期记忆注入器：把检索到的记忆格式化为 system prompt 片段。

核心设计：
- format_memories_for_prompt：把 MemoryItem 列表转为简短文本列表。
- set_memory_prompt / get_memory_prompt：基于 ContextVar 的协程级记忆快照，
  主 Agent 检索一次后，子 Agent 通过 get_memory_prompt() 复用，避免重复检索。

ContextVar 的优势：
- 协程隔离：不同请求的记忆互不干扰。
- 零拷贝：不同于传参，不需要修改 dispatch_tool 的接口。
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from app.memory.schemas import MemoryItem


# 协程级记忆快照，主 Agent 在 run_agent 入口处 set，子 Agent 通过 get 复用。
_memory_prompt_var: ContextVar[str] = ContextVar(
    "globex_memory_prompt", default="（暂无沉淀偏好）"
)


def format_memories_for_prompt(memories: list[MemoryItem], max_items: int = 5) -> str:
    """把检索结果压成短列表，避免长期记忆污染 system prompt。

    排序规则：硬约束 > 纠错 > 偏好 > 事实 > 摘要，同类按 confidence 降序。
    每条记忆截断到 48 字，低置信度（<0.6）前加"可能"标记。

    Args:
        memories: store.read_relevant 返回的相关记忆。
        max_items: 最多注入 prompt 的条数，默认 5。

    Returns:
        可直接填入 {long_term_preferences} 的格式化文本。
    """
    active = [memory for memory in memories if not memory.deleted]
    if not active:
        return "（暂无沉淀偏好）"

    # 排序：高优先级 kind 在前，同 kind 内高置信度在前
    priority = {
        "constraint": 0,
        "correction": 1,
        "preference": 2,
        "fact": 3,
        "summary": 4,
    }
    active.sort(key=lambda item: (priority.get(item.kind, 9), -item.confidence))

    lines: list[str] = []
    for memory in active[:max_items]:
        label = _kind_label(memory.kind)
        text = _shorten(memory.text, 48)
        # 低置信度记忆加上"可能"前缀，提醒模型这不是确定的偏好
        if memory.confidence < 0.6:
            text = f"可能{text}"
        lines.append(f"- [{label}] {text}")
    return "\n".join(lines)


def set_memory_prompt(text: str) -> Token[str]:
    """把主 Agent 检索到的记忆快照放进当前协程上下文。

    返回值是 ContextVar Token，用于最后 reset 恢复原值。

    Args:
        text: format_memories_for_prompt 的输出。

    Returns:
        ContextVar Token，传给 reset_memory_prompt 恢复。
    """
    return _memory_prompt_var.set(text or "（暂无沉淀偏好）")


def reset_memory_prompt(token: Token[str]) -> None:
    """恢复记忆快照到调用 set_memory_prompt 之前的值。

    Args:
        token: set_memory_prompt 返回的 Token。
    """
    _memory_prompt_var.reset(token)


def get_memory_prompt() -> str:
    """读取当前协程的记忆快照。

    子 AgentLoop 在 dispatch_tool 中通过此函数复用主 Agent 已检索的记忆，
    避免跨 fork 的协程隔离导致子 Agent 拿不到偏好上下文。

    Returns:
        当前协程下的记忆格式化文本。
    """
    return _memory_prompt_var.get()


def _kind_label(kind: str) -> str:
    """kind 值到中文标签的映射，用于注入 prompt 时的前缀标记。"""
    return {
        "constraint": "硬约束",
        "correction": "纠错",
        "preference": "偏好",
        "fact": "事实",
        "summary": "摘要",
    }.get(kind, "记忆")


def _shorten(text: str, limit: int) -> str:
    """截断过长记忆文本，保证注入 prompt 的每行不超过 limit 字。"""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
