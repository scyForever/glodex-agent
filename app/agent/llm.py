from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from langchain.chat_models import init_chat_model


@lru_cache(maxsize=1)
def get_llm() -> Any:
    """返回主 AgentLoop 和子 AgentLoop 复用的共享 LLM 实例。"""
    # 使用缓存避免在一次进程生命周期内重复创建模型客户端。
    return init_chat_model(
        # 主模型名称由环境变量控制，便于不同环境切换模型。
        os.environ["LLM_MAIN"],
        model_provider="openai",
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        # 主模型保留少量随机性，兼顾结果质量和表达灵活度。
        temperature=0.3,
    )


@lru_cache(maxsize=1)
def get_judge_llm() -> Any:
    """返回 Rubric judge 使用的更强、确定性更高的 LLM 实例。"""
    # 评审模型要求输出稳定，因此温度固定为 0。
    return init_chat_model(
        # 未显式配置时默认使用 qwen-max 作为评审模型。
        os.environ.get("LLM_JUDGE", "qwen-max"),
        model_provider="openai",
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        temperature=0.0,
    )
