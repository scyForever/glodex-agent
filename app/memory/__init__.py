"""Glodex 记忆系统。

五层记忆架构（L0 → L4），从轻量工具结果截断到长期语义沉淀：
  L0 — tool_guard：工具结果进入上下文前先瘦身。
  L1 — breakpoint：计算稳定区和可压缩区的分界线。
  L2 — compressor：对可压缩区执行截断、滑窗、摘要。
  L3 — session：当前任务的过程摘要和关键决策。
  L4 — store：用户长期偏好、纠错、稳定购买倾向。

对外暴露的核心符号：
  - store：全局 JSONL 长期记忆 Store。
  - MemoryItem / MemoryWrite / MemoryKind / CompressionResult：记忆数据结构。
  - format_memories_for_prompt / set_memory_prompt / get_memory_prompt：记忆注入工具。
"""

from app.memory.injector import (
    format_memories_for_prompt,
    get_memory_prompt,
    reset_memory_prompt,
    set_memory_prompt,
)
from app.memory.schemas import CompressionResult, MemoryItem, MemoryKind, MemoryWrite
from app.memory.store import JsonlMemoryStore, store

__all__ = [
    "CompressionResult",
    "JsonlMemoryStore",
    "MemoryItem",
    "MemoryKind",
    "MemoryWrite",
    "format_memories_for_prompt",
    "get_memory_prompt",
    "reset_memory_prompt",
    "set_memory_prompt",
    "store",
]
