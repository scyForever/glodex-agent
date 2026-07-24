"""长期记忆 Store —— JSONL 文件后端。

第一版使用本地 JSONL 文件存储，不引入数据库或向量库。
每条记录一行 JSON，删除默认软删除。
后续迁移 SQLite / Postgres / Milvus 时只需替换本文件内部实现。

线程安全：所有写操作通过 asyncio.Lock 串行化，避免并发追加导致 JSONL 行交错。
原子写入：_rewrite_all 使用 temp file + replace 保证全量重写时文件不损坏。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.memory.policy import (
    build_memory_write,
    is_duplicate,
    merge_memory,
    score_memory,
)
from app.memory.schemas import MemoryItem, MemoryKind
from app.utils.path_utils import OUTPUT_ROOT


class JsonlMemoryStore:
    """本地 JSONL 长期记忆 Store，适合第一版调试和迁移。

    使用方式：
        store = JsonlMemoryStore()                        # 默认路径
        store = JsonlMemoryStore("data/custom.jsonl")     # 自定义路径

    读操作不加锁（允许并发读），写操作通过异步锁串行化。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        """初始化 Store。

        Args:
            path: JSONL 文件路径，默认从环境变量 MEMORY_STORE_PATH 读取，
                  否则使用 output/memory/user_memories.jsonl。
        """
        configured = path or os.environ.get("MEMORY_STORE_PATH")
        self.path = Path(configured) if configured else OUTPUT_ROOT / "memory" / "user_memories.jsonl"
        # 写锁：保证并发场景下 JSONL 行不会交错，全量重写时不会出现读写竞态。
        self._lock = asyncio.Lock()

    async def create(
        self,
        user_id: str,
        text: str,
        kind: MemoryKind = "preference",
        tags: list[str] | None = None,
        source_thread_id: str | None = None,
        metadata: dict | None = None,
        confidence: float = 0.8,
    ) -> MemoryItem:
        """写入一条新记忆，自动去重合并。

        写入前流程：
        1. policy.build_memory_write 过滤噪声、推断 kind/tags。
        2. 扫描已有记忆，若相似度 ≥ 75% 则合并（更新 tags + confidence）。
        3. 新记忆追加到 JSONL 末尾。

        Args:
            user_id: 用户 ID。
            text: 记忆原文，例如"用户偏好小众品牌"。
            kind: 记忆类型，为空时由 policy 自动推断。
            tags: 手动指定标签，为空时自动推断。
            source_thread_id: 来源会话 ID，用于追溯。
            metadata: 扩展字段。
            confidence: 初始置信度，默认 0.8。

        Returns:
            写入或合并后的 MemoryItem。
        """
        candidate = build_memory_write(
            text=text,
            kind=kind,
            tags=tags,
            confidence=confidence,
        )
        if candidate is None:
            raise ValueError("Memory text is empty or too noisy to write.")

        async with self._lock:
            items = self._read_all()
            # 去重：检查是否与已有记忆高度相似
            for index, item in enumerate(items):
                if item.user_id == user_id and is_duplicate(candidate, item):
                    merged = merge_memory(item, candidate)
                    items[index] = merged
                    self._rewrite_all(items)
                    return merged

            now = datetime.now(timezone.utc)
            memory = MemoryItem(
                memory_id=uuid4().hex,
                user_id=user_id,
                kind=candidate.kind,
                text=candidate.text,
                tags=candidate.tags,
                source_thread_id=source_thread_id,
                confidence=candidate.confidence,
                created_at=now,
                updated_at=now,
                metadata=metadata or candidate.metadata,
            )
            self._append(memory)
            return memory

    async def write_many(
        self,
        user_id: str,
        texts: list[str],
        kind: MemoryKind = "preference",
        source_thread_id: str | None = None,
    ) -> list[MemoryItem]:
        """批量写入多条文本，逐条经 policy 过滤后写入。

        Args:
            user_id: 用户 ID。
            texts: 待写入的原始文本列表，每条都会过 policy 过滤。
            kind: 统一记忆类型。
            source_thread_id: 来源会话 ID。

        Returns:
            成功写入的 MemoryItem 列表（被 policy 过滤掉的不会出现）。
        """
        written: list[MemoryItem] = []
        for text in texts:
            try:
                written.append(
                    await self.create(
                        user_id=user_id,
                        text=text,
                        kind=kind,
                        source_thread_id=source_thread_id,
                    )
                )
            except ValueError:
                # policy 判定不值得写入，静默跳过。
                continue
        return written

    async def list(
        self,
        user_id: str,
        kind: MemoryKind | None = None,
        include_deleted: bool = False,
    ) -> list[MemoryItem]:
        """列出某个用户的全部记忆，主要用于管理/调试。

        Args:
            user_id: 用户 ID。
            kind: 按类型筛选，为 None 则返回全部类型。
            include_deleted: 是否包含已软删除的记忆。

        Returns:
            MemoryItem 列表，按 JSONL 顺序（即写入顺序）。
        """
        items = self._read_all()
        return [
            item
            for item in items
            if item.user_id == user_id
            and (kind is None or item.kind == kind)
            and (include_deleted or not item.deleted)
        ]

    async def read_relevant(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[MemoryItem]:
        """根据当前 query 召回最相关的记忆，AgentLoop 启动时调用。

        第一版使用轻量关键词 + kind 权重 + 标签命中 + 置信度打分，
        不做向量 embedding。最多返回 top_k 条（默认 5 条），
        避免长期记忆把 system prompt 撑大。

        Args:
            user_id: 用户 ID。
            query: 当前用户输入，用于相关性匹配。
            top_k: 最多返回条数。

        Returns:
            按相关性降序排列的 MemoryItem 列表。
        """
        items = await self.list(user_id=user_id)
        ranked = sorted(
            ((score_memory(item, query), item) for item in items),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [item for score, item in ranked if score > 0][:top_k]

    async def update(
        self,
        memory_id: str,
        text: str | None = None,
        tags: list[str] | None = None,
        confidence: float | None = None,
        metadata: dict | None = None,
    ) -> MemoryItem:
        """更新一条已有记忆的部分字段。

        主要用于：
        1. 用户纠错（修改 text）。
        2. 去重合并（更新 tags / confidence）。
        3. 标签补全。

        Args:
            memory_id: 要更新的记忆 ID。
            text: 新文本，为 None 则保留原值。
            tags: 新标签列表，为 None 则保留原值。
            confidence: 新置信度，为 None 则保留原值。
            metadata: 新扩展字段，为 None 则保留原值。

        Returns:
            更新后的 MemoryItem。
        """
        async with self._lock:
            items = self._read_all()
            for index, item in enumerate(items):
                if item.memory_id != memory_id:
                    continue
                updated = item.model_copy(
                    update={
                        "text": text if text is not None else item.text,
                        "tags": tags if tags is not None else item.tags,
                        "confidence": confidence if confidence is not None else item.confidence,
                        "metadata": metadata if metadata is not None else item.metadata,
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                items[index] = updated
                self._rewrite_all(items)
                return updated
        raise KeyError(f"Memory not found: {memory_id}")

    async def delete(self, memory_id: str, soft: bool = True) -> None:
        """删除一条记忆，默认软删除（deleted=True）。

        软删除的好处：
        - 可追溯，避免误删用户偏好。
        - 后续 compact 操作可物理清理已删除记录。

        Args:
            memory_id: 要删除的记忆 ID。
            soft: True 为软删除（仅标记），False 为物理删除。
        """
        async with self._lock:
            items = self._read_all()
            next_items: list[MemoryItem] = []
            found = False
            for item in items:
                if item.memory_id != memory_id:
                    next_items.append(item)
                    continue
                found = True
                if soft:
                    # 软删除：保留记录但标记 deleted=True
                    next_items.append(
                        item.model_copy(
                            update={
                                "deleted": True,
                                "updated_at": datetime.now(timezone.utc),
                            }
                        )
                    )
                # 物理删除：直接不放入 next_items
            if not found:
                raise KeyError(f"Memory not found: {memory_id}")
            self._rewrite_all(next_items)

    # ------------------------------------------------------------------
    # 内部 IO 方法
    # ------------------------------------------------------------------

    def _append(self, item: MemoryItem) -> None:
        """追加一行到 JSONL 末尾（不持锁时调用需外层加锁）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(item.model_dump_json() + "\n")

    def _read_all(self) -> list[MemoryItem]:
        """读取整个 JSONL 文件，损坏的行静默跳过。

        全量读适合第一版数据量（几百条以内），后续量大时改为分页或索引。
        """
        if not self.path.exists():
            return []
        items: list[MemoryItem] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(MemoryItem.model_validate(json.loads(line)))
                except (json.JSONDecodeError, ValueError):
                    # 单行损坏不影响整库读取，后续可加监控事件。
                    continue
        return items

    def _rewrite_all(self, items: list[MemoryItem]) -> None:
        """全量重写 JSONL 文件，通过 temp file + rename 保证原子性。

        先写入临时文件，成功后再替换原文件，避免写入中途崩溃导致文件损坏。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(item.model_dump_json() + "\n")
        # 原子替换
        temp_path.replace(self.path)


# 全局单例，所有 AgentLoop 共用。
store = JsonlMemoryStore()
