from __future__ import annotations

from datetime import datetime
from typing import Any

from app.api.connection import manager
from app.api.context import get_thread_id


class Monitor:
    """Unified AGUI monitor event reporter."""

    async def _emit(
        self,
        event: str,
        message: str,
        data: dict[str, Any],
        thread_id: str | None = None,
    ) -> None:
        target_thread_id = thread_id or get_thread_id()
        if target_thread_id is None:
            return

        payload = {
            "type": "monitor_event",
            "event": event,
            "message": message,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        await manager.send_to_thread(payload, target_thread_id)

    async def report_session_created(self, thread_id: str, session_dir: str) -> None:
        await self._emit(
            "session_created",
            "\u4f1a\u8bdd\u5df2\u521b\u5efa",
            {
                "thread_id": thread_id,
                "session_dir": session_dir,
            },
            thread_id=thread_id,
        )

    async def report_assistant_call(self, step: str, preview: str = "") -> None:
        await self._emit(
            "assistant_call",
            "\u6b63\u5728\u601d\u8003",
            {
                "step": step,
                "preview": preview,
            },
        )

    async def report_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        await self._emit(
            "tool_start",
            f"\u6b63\u5728\u8c03\u7528 {tool_name}",
            {
                "tool_name": tool_name,
                "args": args,
            },
        )

    async def report_tool_end(self, tool_name: str, duration_ms: int) -> None:
        await self._emit(
            "tool_end",
            f"{tool_name} \u5b8c\u6210",
            {
                "tool_name": tool_name,
                "duration_ms": duration_ms,
            },
        )

    async def report_fork(self, sub_thread_id: str, demands: str) -> None:
        await self._emit(
            "fork",
            "\u6d3e\u53d1\u5b50 AgentLoop",
            {
                "sub_thread_id": sub_thread_id,
                "demands": demands[:200],
            },
        )

    async def report_task_result(self, final_answer: str) -> None:
        await self._emit(
            "task_result",
            "\u4efb\u52a1\u5b8c\u6210",
            {
                "final_answer": final_answer,
            },
        )

    async def report_task_cancelled(self, thread_id: str | None = None) -> None:
        await self._emit(
            "task_cancelled",
            "\u4efb\u52a1\u5df2\u53d6\u6d88",
            {},
            thread_id=thread_id,
        )

    async def report_error(
        self,
        error_type: str,
        message: str,
        thread_id: str | None = None,
    ) -> None:
        await self._emit(
            "error",
            message,
            {
                "error_type": error_type,
                "message": message,
            },
            thread_id=thread_id,
        )


monitor = Monitor()
