from __future__ import annotations

import pytest

from app.memory.compressor import compress_messages


@pytest.mark.asyncio
async def test_compresses_old_tool_result_but_keeps_recent_tool_result() -> None:
    old_tool = "old-" + "x" * 200
    recent_tool = "recent-" + "y" * 200
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "first request"},
        {"role": "tool", "content": old_tool},
        {"role": "assistant", "content": "old answer"},
        {"role": "tool", "content": "middle result"},
        {"role": "assistant", "content": "middle answer"},
        {"role": "tool", "content": recent_tool},
        {"role": "user", "content": "current request"},
    ]

    result = await compress_messages(
        messages,
        max_tokens=100,
        keep_recent_tool_calls=1,
        max_tool_chars=20,
    )

    assert result.strategy in {"tool_trim", "tool_trim+sliding_window"}
    assert result.messages[2]["content"].startswith("old-" + "x" * 16)
    assert "已由 memory.tool_guard 截断" in result.messages[2]["content"]
    assert result.messages[6]["content"] == recent_tool


@pytest.mark.asyncio
async def test_keeps_hard_constraint_message_content() -> None:
    constraint = "预算必须小于 300 元，平台只要京东。" + "z" * 200
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": constraint},
        {"role": "tool", "content": "old-" + "x" * 200},
        {"role": "assistant", "content": "old answer"},
        {"role": "tool", "content": "recent-" + "y" * 200},
    ]

    result = await compress_messages(
        messages,
        max_tokens=100,
        keep_recent_tool_calls=1,
        max_tool_chars=20,
    )

    assert result.messages[1]["content"] == constraint
    assert result.messages[-1]["content"] == "recent-" + "y" * 200
