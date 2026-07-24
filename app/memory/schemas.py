"""记忆系统统一数据结构。

L3 会话记忆和 L4 长期记忆共用 MemoryItem，压缩层使用 CompressionResult。
MemoryWrite 是抽取器交给 Store 的中间写入对象，不直接暴露给外部。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# 记忆类型：preference 软偏好，constraint 硬约束，correction 用户纠错，
# summary 会话沉淀摘要，fact 稳定事实。
MemoryKind = Literal["preference", "constraint", "correction", "summary", "fact"]


class MemoryItem(BaseModel):
    """长期记忆的持久化存储结构。

    每条记忆对应一个用户的一条稳定认知，例如"用户偏好小众风格""不要塑料材质"。
    删除采用软删除（deleted=True），保留历史可追溯。
    """

    memory_id: str
    """全局唯一记忆 ID，由 Store 在写入时生成。"""

    user_id: str
    """归属用户 ID，多租户隔离的基础。"""

    kind: MemoryKind = "preference"
    """记忆类型，影响检索排序权重和注入 prompt 的优先级。"""

    text: str
    """记忆文本，例如"用户不喜欢塑料材质的餐具"。"""

    scope: str = "shopping"
    """业务域标签，预留后续扩展到非购物场景。"""

    tags: list[str] = Field(default_factory=list)
    """自动推断的标签，如 material、style、platform、shipping、budget。"""

    source_thread_id: str | None = None
    """产生这条记忆的会话 ID，用于追溯来源。"""

    confidence: float = 0.8
    """置信度 0-1，多次命中同一偏好会递增。"""

    created_at: datetime
    """首次写入时间。"""

    updated_at: datetime
    """最后更新时间，合并或纠错时会刷新。"""

    deleted: bool = False
    """软删除标记，已删除记忆不参与检索但保留在 JSONL 中。"""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """扩展字段，后续可存放来源工具名、抽取方式等。"""


class MemoryWrite(BaseModel):
    """抽取器交给 Store 的待写入记忆。

    和 MemoryItem 的区别：
    - 没有 memory_id / user_id / created_at / deleted —— 由 Store 赋予。
    - 只描述"想写什么"，不描述"如何存储"。
    """

    text: str
    """记忆文本。"""

    kind: MemoryKind = "preference"
    """记忆类型，可显式指定也可由 policy 自动推断。"""

    tags: list[str] = Field(default_factory=list)
    """标签列表。"""

    confidence: float = 0.8
    """初始置信度。"""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """扩展字段。"""


class CompressionResult(BaseModel):
    """上下文压缩操作的产出，记录压缩前后体积和策略。

    主 AgentLoop 在调用 LLM 前可用此结构决定是否还需要进一步压缩。
    """

    messages: list[Any]
    """压缩后的消息列表，直接传给 LLM。"""

    original_tokens: int
    """压缩前的估算 token 数。"""

    compressed_tokens: int
    """压缩后的估算 token 数。"""

    breakpoint_idx: int
    """Cache Breakpoint 分界索引，breakpoint 之前的消息被完整保留。"""

    strategy: str
    """实际使用的压缩策略，如 "none"、"tool_trim"、"tool_trim+sliding_window"。"""

    summary: str | None = None
    """摘要文本，第一版暂不启用（不调用 LLM 做摘要压缩）。"""
