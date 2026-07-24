from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator


_fork_depth: ContextVar[int] = ContextVar("globex_fork_depth", default=0)
MAX_FORK_DEPTH = 2


class ForkLimitExceeded(Exception):
    """fork 深度超过上限时抛出，由 dispatch_tool 转成普通工具结果。"""


@contextmanager
def enter_fork() -> Iterator[int]:
    """进入一次 fork 作用域，退出时自动恢复父级深度。"""
    current_depth = _fork_depth.get()
    if current_depth >= MAX_FORK_DEPTH:
        raise ForkLimitExceeded(f"fork 深度超过上限 {MAX_FORK_DEPTH}")

    token = _fork_depth.set(current_depth + 1)
    try:
        yield current_depth + 1
    finally:
        _fork_depth.reset(token)


def current_fork_depth() -> int:
    """返回当前协程上下文中的 fork 深度。"""
    return _fork_depth.get()
