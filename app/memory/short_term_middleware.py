"""Short-term memory runtime hooks for LangChain agents."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

from app.api.context import get_thread_id
from app.memory.compressor import compress_messages
from app.memory.session import session_memory
from app.memory.tool_guard import DEFAULT_MAX_TOOL_CHARS, truncate_text


class ShortTermMemoryMiddleware(AgentMiddleware):
    """LangChain middleware for current-task context hygiene.

    Phase 1:
      - trim oversized tool results before they enter the agent context.

    Phase 2:
      - compress oversized message state before each model call.
    """

    def __init__(
        self,
        max_tool_chars: int = DEFAULT_MAX_TOOL_CHARS,
        max_context_tokens: int = 12000,
        keep_recent_tool_calls: int = 3,
    ) -> None:
        self.max_tool_chars = max_tool_chars
        self.max_context_tokens = max_context_tokens
        self.keep_recent_tool_calls = keep_recent_tool_calls

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Compress oversized context right before a model call."""
        messages = _state_messages(state)
        if not messages:
            return None

        result = await compress_messages(
            list(messages),
            max_tokens=self.max_context_tokens,
            keep_recent_tool_calls=self.keep_recent_tool_calls,
            max_tool_chars=self.max_tool_chars,
        )
        if result.strategy == "none":
            return None

        _append_session_snapshot(
            messages,
            {
                "strategy": result.strategy,
                "original_tokens": result.original_tokens,
                "compressed_tokens": result.compressed_tokens,
                "breakpoint_idx": result.breakpoint_idx,
                "message_count_before": len(messages),
                "message_count_after": len(result.messages),
            },
        )

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES, content=""),
                *result.messages,
            ],
        }

    async def awrap_tool_call(self, request: Any, handler: Any) -> ToolMessage | Any:
        """Trim oversized tool results before LangChain stores them in context."""
        result = await handler(request)
        if not isinstance(result, ToolMessage):
            return result

        content = result.content
        if not isinstance(content, str):
            return result

        trimmed = truncate_text(content, max_chars=self.max_tool_chars)
        if trimmed == content:
            return result
        return result.model_copy(update={"content": trimmed})


def _state_messages(state: Any) -> list[Any]:
    """Read messages from dict-like or object-like LangChain agent state."""
    if isinstance(state, dict):
        messages = state.get("messages") or []
    else:
        messages = getattr(state, "messages", []) or []
    return list(messages)


def _append_session_snapshot(messages: list[Any], metadata: dict[str, Any]) -> None:
    """Best-effort write of task-state memory when compression happens."""
    thread_id = get_thread_id()
    if not thread_id:
        return
    try:
        session_memory.append_snapshot(thread_id, messages, metadata=metadata)
    except Exception:
        # SessionMemory is auxiliary; context compression must not fail because of it.
        return


short_term_memory_middleware = ShortTermMemoryMiddleware()
