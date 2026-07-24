from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from langchain.agents import create_agent
from langchain_core.tools import tool

from app.agent.fork_guard import ForkLimitExceeded, enter_fork
from app.agent.llm import get_llm
from app.agent.prompts import get_system_prompt
from app.api.context import get_session_dir, push_thread_context, reset_thread_context
from app.api.monitor import monitor
from app.memory.injector import get_memory_prompt
from app.memory.short_term_middleware import short_term_memory_middleware


SUB_AGENT_TIMEOUT_SEC = 90
SUB_AGENT_MAX_ITERATIONS = 12


@tool
async def dispatch_tool(demands: str) -> str:
    """派一个同质子 AgentLoop 执行 demands，并返回它的最终回复。

    适用条件：
    1. 子任务可以并行执行。
    2. 子任务上下文需要隔离，避免大量结果污染主 loop。
    3. 子任务内部可能还要多轮 Think -> Act。
    """
    start = time.time()
    await monitor.report_tool_start("dispatch_tool", {"demands": demands[:200]})

    async def report_dispatch_end() -> None:
        await monitor.report_tool_end(
            "dispatch_tool",
            int((time.time() - start) * 1000),
        )

    # 延迟导入，避免工具注册表和 dispatch_tool 之间形成循环 import。
    from app.tools.tool_registry import FULL_TOOL_SET

    try:
        with enter_fork() as depth:
            sub_thread_id = f"sub-{uuid4().hex[:8]}-d{depth}"
            parent_session_dir = get_session_dir()
            if parent_session_dir is None:
                await monitor.report_error(
                    "missing_session_dir",
                    "dispatch_tool requires an active session_dir",
                )
                await report_dispatch_end()
                return "[dispatch_tool 拒绝] 当前没有 session_dir，无法派发子 AgentLoop。"

            await monitor.report_fork(sub_thread_id, demands)

            sub_agent = create_agent(
                model=get_llm(),
                tools=FULL_TOOL_SET,
                # 子 Agent 复用主 Agent 已检索到的记忆快照，避免偏好上下文丢失。
                system_prompt=get_system_prompt(long_term_preferences=get_memory_prompt()),
                middleware=[short_term_memory_middleware],
            )

            token = push_thread_context(sub_thread_id, parent_session_dir)
            try:
                result = await asyncio.wait_for(
                    sub_agent.ainvoke(
                        {
                            "messages": [
                                {"role": "user", "content": demands},
                            ],
                        },
                        config={
                            "configurable": {
                                "thread_id": sub_thread_id,
                            },
                            # 子 loop 迭代次数更小，避免子任务拖垮主任务。
                            "recursion_limit": SUB_AGENT_MAX_ITERATIONS,
                        },
                    ),
                    timeout=SUB_AGENT_TIMEOUT_SEC,
                )
            finally:
                reset_thread_context(token)

            messages = result.get("messages") or []
            if not messages:
                await report_dispatch_end()
                return ""

            final_message = messages[-1]
            content = getattr(final_message, "content", final_message)
            await report_dispatch_end()
            return str(content)
    except ForkLimitExceeded as exc:
        await monitor.report_error("fork_limit_exceeded", str(exc))
        await report_dispatch_end()
        return f"[dispatch_tool 拒绝] {exc}。建议主 loop 自己处理或换一种拆分方式。"
    except asyncio.TimeoutError:
        await monitor.report_error(
            "dispatch_tool_timeout",
            f"dispatch_tool timed out after {SUB_AGENT_TIMEOUT_SEC}s",
        )
        await report_dispatch_end()
        return f"[dispatch_tool 超时] 子任务 {SUB_AGENT_TIMEOUT_SEC}s 未完成。建议缩小子任务范围。"
    except Exception as exc:
        await monitor.report_error(type(exc).__name__, str(exc))
        await report_dispatch_end()
        raise
