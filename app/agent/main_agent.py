from __future__ import annotations

import asyncio
from typing import Any

from langchain.agents import create_agent

from app.agent.llm import get_llm
from app.agent.prompts import get_system_prompt
from app.api.context import set_thread_context
from app.api.monitor import monitor
from app.memory.extractor import (
    extract_learned_preferences_from_result,
    extract_memories,
)
from app.memory.injector import (
    format_memories_for_prompt,
    reset_memory_prompt,
    set_memory_prompt,
)
from app.memory.short_term_middleware import short_term_memory_middleware
from app.memory.store import store
from app.tools.tool_registry import FULL_TOOL_SET
from app.utils.path_utils import ensure_session_dir


MAIN_AGENT_MAX_ITERATIONS = 30
MAIN_AGENT_TIMEOUT_SEC = 300


def _build_main_agent(system_prompt: str) -> Any:
    """创建主 AgentLoop 使用的 LangChain Agent。"""
    return create_agent(
        model=get_llm(),
        # 主 loop 和子 loop 都从同一份 FULL_TOOL_SET 取工具，保证同质 fork。
        tools=FULL_TOOL_SET,
        system_prompt=system_prompt,
        middleware=[short_term_memory_middleware],
    )


def _get_final_text(result: dict[str, Any]) -> str:
    """从 Agent 返回状态中提取最后一条消息文本。"""
    messages = result.get("messages") or []
    if not messages:
        return ""

    final_message = messages[-1]
    content = getattr(final_message, "content", final_message)
    return str(content)


async def run_agent(
    query: str,
    thread_id: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """主 AgentLoop 的统一入口。"""
    session_dir = ensure_session_dir(thread_id)
    set_thread_context(thread_id, session_dir)

    # 任务开始前先检索长期记忆，把相关偏好注入 system prompt。
    long_term = await store.read_relevant(user_id=user_id, query=query) if user_id else []
    pref_text = format_memories_for_prompt(long_term)
    memory_token = set_memory_prompt(pref_text)

    system_prompt = get_system_prompt(long_term_preferences=pref_text)
    agent = _build_main_agent(system_prompt)

    try:
        await monitor.report_assistant_call(step="thinking", preview=query[:120])
        result = await asyncio.wait_for(
            agent.ainvoke(
                {
                    "messages": [
                        {"role": "user", "content": query},
                    ],
                },
                config={
                    "configurable": {
                        "thread_id": thread_id,
                    },
                    # 限制 Agent 循环次数，避免工具调用或模型思考陷入无限循环。
                    "recursion_limit": MAIN_AGENT_MAX_ITERATIONS,
                },
            ),
            timeout=MAIN_AGENT_TIMEOUT_SEC,
        )
    except asyncio.CancelledError:
        await monitor.report_task_cancelled()
        return {
            "status": "cancelled",
            "thread_id": thread_id,
        }
    except asyncio.TimeoutError:
        await monitor.report_error(
            "timeout",
            f"主任务超时 {MAIN_AGENT_TIMEOUT_SEC} 秒",
        )
        return {
            "status": "timeout",
            "thread_id": thread_id,
        }
    except Exception as exc:
        await monitor.report_error(type(exc).__name__, str(exc))
        return {
            "status": "error",
            "thread_id": thread_id,
            "error": str(exc),
        }
    finally:
        reset_memory_prompt(memory_token)

    final_text = _get_final_text(result)

    # 任务结束后再沉淀长期记忆；写入失败不影响主任务结果。
    if user_id:
        learned_preferences = extract_learned_preferences_from_result(result)
        try:
            writes = await extract_memories(
                user_query=query,
                final_text=final_text,
                learned_preferences=learned_preferences,
            )
            for write in writes:
                await store.create(
                    user_id=user_id,
                    text=write.text,
                    kind=write.kind,
                    tags=write.tags,
                    source_thread_id=thread_id,
                    metadata=write.metadata,
                    confidence=write.confidence,
                )
        except Exception as exc:
            await monitor.report_error("memory_write_failed", str(exc))

    await monitor.report_task_result(final_text)
    return {
        "status": "ok",
        "thread_id": thread_id,
        "final": final_text,
    }
