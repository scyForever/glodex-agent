from __future__ import annotations

import asyncio
from uuid import uuid4

from app.agent.main_agent import run_agent
from app.api.monitor import monitor
from app.utils.path_utils import ensure_session_dir


class TaskManager:
    """Manage active background AgentLoop tasks by thread_id."""

    def __init__(self) -> None:
        self.active_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def start_task(
        self,
        query: str,
        thread_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Start an AgentLoop in the background and return its thread_id."""
        resolved_thread_id = thread_id or uuid4().hex
        await self.cancel_task(resolved_thread_id, missing_ok=True)

        session_dir = ensure_session_dir(resolved_thread_id)
        task = asyncio.create_task(
            self._run_and_cleanup(
                query=query,
                thread_id=resolved_thread_id,
                user_id=user_id,
            )
        )
        async with self._lock:
            self.active_tasks[resolved_thread_id] = task

        await monitor.report_session_created(
            thread_id=resolved_thread_id,
            session_dir=str(session_dir),
        )
        return resolved_thread_id

    async def cancel_task(self, thread_id: str, missing_ok: bool = False) -> bool:
        """Cancel a running task by thread_id."""
        async with self._lock:
            task = self.active_tasks.get(thread_id)

        if task is None or task.done():
            if missing_ok:
                return False
            raise KeyError(thread_id)

        task.cancel()
        return True

    async def _run_and_cleanup(self, query: str, thread_id: str, user_id: str | None) -> None:
        try:
            await run_agent(query=query, thread_id=thread_id, user_id=user_id)
        finally:
            async with self._lock:
                if self.active_tasks.get(thread_id) is asyncio.current_task():
                    del self.active_tasks[thread_id]


task_manager = TaskManager()

