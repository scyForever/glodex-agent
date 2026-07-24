"""Agent 核心编排包。

这个包负责 Glodex Agent 的主 AgentLoop、同质子 AgentLoop fork、LLM 创建、
提示词加载、fork 防失控、工具结果截断和循环检测等核心运行时能力。

业务工具本身放在 `app.tools` 包中，Agent 这里只负责调度和编排。
"""
