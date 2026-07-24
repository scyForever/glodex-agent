# AgentLoop 防失控设计

## 为什么要防御

主 AgentLoop 和子 AgentLoop 共用同一份 `FULL_TOOL_SET`。这意味着子 Agent 也能看到 `dispatch_tool`，理论上可以继续 fork 子 Agent。如果不加限制，可能出现以下问题：

| 失控类型 | 现象 |
| --- | --- |
| 无限 fork | 子再 fork 子，递归没有边界，资源指数爆炸 |
| 死循环工具 | 模型一直调用同一个工具，不收敛 |
| 单工具炸 token | 工具一次返回大量文本，后续轮次全部被污染 |
| 长任务卡死 | 子 Agent 长时间无响应，拖慢主任务 |

## 四件套

### 1. fork 深度上限

`app/agent/fork_guard.py` 负责维护当前协程上下文里的 fork 深度。

核心对象：

- `MAX_FORK_DEPTH`：允许的最大 fork 深度，当前为 `2`。
- `enter_fork()`：进入一次 fork 作用域，自动把深度加一，并在退出时恢复。
- `ForkLimitExceeded`：超过深度时抛出，交给 `dispatch_tool` 转成普通工具结果。
- `current_fork_depth()`：读取当前 fork 深度，方便后续调试和监控。

注意：`ForkLimitExceeded` 不应该直接打崩主 AgentLoop。`dispatch_tool` 会捕获它并返回字符串，让主 loop 把这次子任务失败当作普通工具结果处理。

### 2. 升级版 dispatch_tool

`app/agent/dispatch_tool.py` 现在是一个真正的 LangChain Tool：

```python
from langchain_core.tools import tool

@tool
async def dispatch_tool(demands: str) -> str:
    ...
```

它负责：

- 使用 `enter_fork()` 控制 fork 深度。
- 使用 `create_agent(...)` 创建同质子 AgentLoop。
- 子 Agent 复用 `FULL_TOOL_SET`。
- 子 Agent 复用 `get_llm()` 和 `get_system_prompt()`。
- 子任务绑定独立 `sub_thread_id`。
- 使用 `SUB_AGENT_TIMEOUT_SEC` 限制子任务耗时。
- 使用 `SUB_AGENT_MAX_ITERATIONS` 限制子 Agent 循环次数。
- 超限、超时都返回字符串，而不是向外抛异常。

### 3. 单工具结果体积截断

`app/agent/middleware.py` 中的 `truncate_long_tool_result()` 用于限制单个工具结果长度。

当前使用简化估算：

```text
1 token ~= 4 字符
```

当工具结果超过 `MAX_TOOL_RESULT_TOKENS` 后，只保留前部内容，并追加截断提示：

```text
[工具结果过长已截断，主 loop 可调更窄的查询参数]
```

后续如果引入真实 tokenizer，可以替换这里的字符估算逻辑。

### 4. 循环检测

`LoopDetector` 用于检测短窗口内是否重复调用同一个工具。

默认策略：

- 窗口大小：最近 `6` 次工具调用。
- 阈值：同一个工具出现 `4` 次即认为疑似循环。

当前先实现基础类，后续接入 LangChain middleware 或工具调用节点时启用。

## 文件落点

| 文件 | 职责 |
| --- | --- |
| `app/agent/fork_guard.py` | fork 深度控制 |
| `app/agent/dispatch_tool.py` | 同质子 AgentLoop 派发 |
| `app/agent/middleware.py` | 工具结果截断和循环检测 |
| `app/tools/tool_registry.py` | `FULL_TOOL_SET` 工具集合 |

## 设计原则

1. 防御逻辑优先返回普通工具结果，避免主 AgentLoop 崩溃。
2. 子 AgentLoop 的超时和迭代次数必须小于主 AgentLoop。
3. `FULL_TOOL_SET` 可以包含 `dispatch_tool`，但必须配合 fork 深度限制。
4. 工具结果默认做体积保护，避免单个工具污染后续上下文。
5. 循环检测先做轻量实现，等工具节点稳定后再接入执行链路。
