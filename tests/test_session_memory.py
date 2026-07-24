from __future__ import annotations

from app.memory.session import build_session_memory_snapshot


def test_build_session_memory_snapshot_records_task_state() -> None:
    messages = [
        {
            "role": "user",
            "content": "帮我买 300 元以内沐浴露，预算必须小于 300，不要香味太重，优先京东",
        },
        {
            "role": "tool",
            "content": "商品 A price 129 url https://example.com 平台京东 候选不错",
        },
        {
            "role": "assistant",
            "content": "已排除香味太重的商品，保留商品 A，下一步建议比价",
        },
    ]

    snapshot = build_session_memory_snapshot(messages)

    assert "沐浴露" in snapshot["user_goal"]
    assert snapshot["constraints"]
    assert snapshot["key_findings"]
    assert snapshot["candidates"]
    assert snapshot["decisions"]
    assert snapshot["next_steps"]

