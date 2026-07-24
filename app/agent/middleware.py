from __future__ import annotations

from collections import deque

from app.memory.tool_guard import DEFAULT_MAX_TOOL_CHARS, truncate_text

MAX_TOOL_RESULT_TOKENS = DEFAULT_MAX_TOOL_CHARS // 4


def truncate_long_tool_result(result_text: str) -> str:
    """兼容旧调用；实际截断规则统一走 app.memory.tool_guard。"""
    return truncate_text(result_text, max_chars=DEFAULT_MAX_TOOL_CHARS)


class LoopDetector:
    """检测短窗口内是否重复调用同一个工具。"""

    def __init__(self, window: int = 6, repeat_threshold: int = 4) -> None:
        self.window = window
        self.threshold = repeat_threshold
        self._recent: deque[str] = deque(maxlen=window)

    def record(self, tool_name: str) -> bool:
        """记录一次工具调用，返回 True 表示疑似触发循环。"""
        self._recent.append(tool_name)
        return self._recent.count(tool_name) >= self.threshold
