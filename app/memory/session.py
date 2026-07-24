"""L3 会话记忆：当前任务级短期任务状态。

和 L4 长期记忆的区别：
  - L3 只活一次任务，用来保留被压缩历史中的关键任务状态。
  - L4 跨会话存活，下次 AgentLoop 启动时会被召回。

第一版不调用 LLM 总结，只在上下文压缩时用轻量规则抽取结构化快照。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.utils.path_utils import OUTPUT_ROOT


class SessionMemory:
    """当前任务级短期记忆存储。

    以 thread_id 为粒度，每条记录是一个 JSON 事件行。
    文件路径：output/memory/sessions/{thread_id}.jsonl
    """

    def __init__(self, root: Path | None = None) -> None:
        """初始化会话记忆存储。

        Args:
            root: 会话文件根目录，默认为 output/memory/sessions。
        """
        self.root = root or OUTPUT_ROOT / "memory" / "sessions"

    def append_event(self, thread_id: str, event: str, data: dict[str, Any]) -> None:
        """向当前会话追加一条事件记录。

        典型事件类型：
        - "compression"：上下文压缩发生，data 含 CompressionResult。
        - "subtask_done"：子任务完成，data 含子任务结果摘要。
        - "milestone"：关键决策点，data 含决策内容和理由。

        Args:
            thread_id: 当前会话的 thread_id。
            event: 事件类型标签。
            data: 事件附带数据，会被 JSON 序列化。
        """
        path = self.root / f"{thread_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "event": event,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def append_snapshot(
        self,
        thread_id: str,
        messages: list[Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """记录压缩前仍需要保留的当前任务状态摘要。

        这不是完整对话记录，也不是长期记忆。它只保存被压缩历史中
        后续任务可能仍需要知道的结构化信息。
        """
        snapshot = build_session_memory_snapshot(messages)
        if not any(snapshot.values()):
            return
        self.append_event(
            thread_id,
            "session_memory_snapshot",
            {
                **snapshot,
                "metadata": metadata or {},
            },
        )


def build_session_memory_snapshot(messages: list[Any]) -> dict[str, Any]:
    """从当前 messages 轻量抽取任务型 SessionMemory 快照。

    第一版只做确定性抽取，不额外调用 LLM，避免把 Phase 4 做复杂。
    """
    user_messages = [
        _message_content(message)
        for message in messages
        if _message_role(message) == "user" and _message_content(message)
    ]
    assistant_messages = [
        _message_content(message)
        for message in messages
        if _message_role(message) in {"assistant", "ai"} and _message_content(message)
    ]
    tool_messages = [
        _message_content(message)
        for message in messages
        if _message_role(message) in {"tool", "function"} and _message_content(message)
    ]

    return {
        "user_goal": _clip(user_messages[-1]) if user_messages else "",
        "constraints": _unique_clipped(
            message
            for message in user_messages
            if _contains_constraint(message)
        ),
        "completed_steps": _unique_clipped(assistant_messages[-4:], max_chars=260),
        "key_findings": _unique_clipped(tool_messages[-4:], max_chars=320),
        "candidates": _extract_candidate_summaries(tool_messages),
        "decisions": _unique_clipped(
            message
            for message in assistant_messages
            if _contains_decision(message)
        ),
        "next_steps": _unique_clipped(
            message
            for message in assistant_messages
            if _contains_next_step(message)
        ),
    }


CONSTRAINT_KEYWORDS = ("预算", "不要", "必须", "只要", "平台", "品类", "以内", "优先")
DECISION_KEYWORDS = ("选择", "保留", "排除", "淘汰", "推荐", "决定")
NEXT_STEP_KEYWORDS = ("下一步", "继续", "需要", "建议", "待确认")
CANDIDATE_KEYWORDS = ("price", "价格", "商品", "候选", "url", "链接", "平台")


def _contains_constraint(text: str) -> bool:
    return any(keyword in text for keyword in CONSTRAINT_KEYWORDS)


def _contains_decision(text: str) -> bool:
    return any(keyword in text for keyword in DECISION_KEYWORDS)


def _contains_next_step(text: str) -> bool:
    return any(keyword in text for keyword in NEXT_STEP_KEYWORDS)


def _extract_candidate_summaries(tool_messages: list[str], limit: int = 5) -> list[str]:
    summaries: list[str] = []
    for message in tool_messages:
        if any(keyword in message for keyword in CANDIDATE_KEYWORDS):
            summaries.append(_clip(message, max_chars=360))
        if len(summaries) >= limit:
            break
    return summaries


def _unique_clipped(items: Any, max_items: int = 6, max_chars: int = 220) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        clipped = _clip(str(item), max_chars=max_chars)
        if not clipped or clipped in seen:
            continue
        seen.add(clipped)
        result.append(clipped)
        if len(result) >= max_items:
            break
    return result


def _clip(text: str, max_chars: int = 400) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "type", getattr(message, "role", "")))


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return str(getattr(message, "content", ""))


# 全局单例，各模块通过 session_memory.append_event 写入。
session_memory = SessionMemory()
