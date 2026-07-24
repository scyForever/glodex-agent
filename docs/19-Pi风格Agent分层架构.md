# Pi 风格 Agent 分层架构

> 本文讨论如何为一个新的 Agent 项目设计可复用的总体目录和分层边界。
> 它回答“项目的各类代码放在哪里”；关于 Hook 的具体生命周期、注册和控制规则，参见 `18-Harness-Hook基础知识.md`。

## 1. 文档目标与适用范围

一个 Agent 项目通常同时包含模型调用、Agent Loop、工具、记忆、运行控制、会话、监控和 API。若这些内容都堆在一个 `agent/` 包里，项目初期容易开发，但工具、记忆和业务场景增加后会迅速失去边界。

本文给出一个通用的 Python 项目结构，适用于：

- 需要支持多个模型供应商的 Agent；
- 有工具调用和多轮 Agent Loop；
- 有短期上下文与长期记忆；
- 需要 Harness Hook 做安全、预算、流程和质量治理；
- 未来可能从一个业务 Agent 扩展为多个业务 Agent。

本文不绑定电商、代码助手或任何单一业务。它使用 Pi Agent 的分层思想作为参考，但目录名称和 Python 实现可自行调整。

## 2. Pi Agent 的分层思想

Pi 的核心分层是：

```text
Application Layer
    ↓
Agent Layer
    ↓
AI Layer
```

- **AI Layer**：统一不同模型供应商的请求、流式响应、工具调用格式和 Token/成本统计。
- **Agent Layer**：提供通用的有状态 Agent Runtime，负责 Agent Loop、工具执行、上下文转换和事件。
- **Application Layer**：实现具体产品，例如编码 Agent 的 Session、Skills、扩展、内置工具和界面。

Pi 的 `pi-agent-core` 本身不关心它是编码 Agent、聊天 Agent 还是电商 Agent；它只处理通用的模型交互、工具调用和状态管理。具体业务能力放在应用层。这种分层可使同一个 Agent Core 被多个产品复用。[Pi 官方架构文档](https://www.mintlify.com/badlogic/pi-mono/concepts/architecture)

将这个思想映射到 Python 项目：

```text
Pi 的 pi-ai              → app/ai/
Pi 的 pi-agent-core      → app/agent_core/
Pi 的 pi-coding-agent    → app/agent_app/
```

此外，本项目将工具、记忆、Harness 和可观测性拆成独立包，避免它们被业务 Agent 或通用 Loop 吞没。

## 3. 通用 Agent 项目的目标分层

推荐的依赖方向是：

```text
api / UI
  ↓
agent_app
  ↓
agent_core ───→ ai
  ↓
tools

agent_app ───→ memory
agent_app ───→ harness
harness   ───→ tools / memory 的公开能力
```

核心原则：

| 层 | 应负责什么 | 不应负责什么 |
|---|---|---|
| `ai/` | 模型供应商、流式响应、Token | Agent Loop、业务工具、记忆 |
| `agent_core/` | 通用 Loop、消息、事件、工具调度 | 电商/代码等业务规则、具体记忆策略 |
| `agent_app/` | 业务 Prompt、Session、业务状态、组装 | 重新实现模型协议或工具执行循环 |
| `tools/` | 单一业务动作 | 预算、循环检测、上下文压缩 |
| `memory/` | 存、取、提炼、压缩 | 具体工具调用时机 |
| `harness/` | 生命周期控制、检查、修改、拒绝、治理 | 搜索算法、数据库存储算法 |

## 4. 推荐目录结构总览

```text
app/
├─ api/                               # HTTP / WebSocket 等外部接口
│  ├─ server.py
│  └─ schemas.py
│
├─ ai/                                # AI Layer：模型统一适配
│  ├─ models.py                       # 模型协议、ModelConfig
│  ├─ providers.py                    # OpenAI / Anthropic / Qwen 等适配
│  ├─ streaming.py                    # 流式响应统一格式
│  └─ usage.py                        # Token、成本统计
│
├─ agent_core/                        # Agent Layer：可复用运行时
│  ├─ agent.py                        # Agent 类：持有 State，对外 run()/steer()
│  ├─ loop.py                         # Think → Tool → Observe 的通用循环
│  ├─ state.py                        # 通用 AgentState
│  ├─ messages.py                     # Message / ToolCall / ToolResult 类型
│  ├─ events.py                       # EventBus、流式生命周期事件
│  ├─ context.py                      # State 转换为模型上下文
│  ├─ tool_runtime.py                 # 统一工具调度与执行
│  └─ extension.py                    # 通用扩展点 / 生命周期协议
│
├─ agent_app/                         # Application Layer：具体业务 Agent
│  ├─ runtime.py                      # run_agent()：一次业务请求入口
│  ├─ factory.py                      # 组装 model、tools、memory、harness
│  ├─ state.py                        # 业务控制 State：阶段、预算、漂移等
│  ├─ prompts.py                      # 业务 System Prompt
│  ├─ session.py                      # 会话创建、恢复、分支
│  ├─ resources.py                    # 配置、Skills、Prompt 模板加载
│  ├─ extensions.py                   # 业务扩展注册
│  └─ config.py                       # 当前业务 Agent 配置
│
├─ tools/                             # 工具包：只实现业务动作
│  ├─ contracts.py                    # Tool 协议、输入输出 Schema
│  ├─ registry.py                     # build_tool_set()：统一暴露工具
│  ├─ search/
│  │  ├─ item_search.py
│  │  └─ web_search.py
│  ├─ commerce/
│  │  ├─ item_picker.py
│  │  ├─ price_compare.py
│  │  └─ shopping_summary.py
│  └─ common/
│     └─ calculator.py
│
├─ memory/                            # 记忆包：真正实现存、取、压缩
│  ├─ contracts.py                    # MemoryItem、MemoryStore 等协议
│  ├─ store.py                        # 长期记忆读写
│  ├─ retrieval.py                    # 相关记忆检索
│  ├─ extractor.py                    # 从对话提炼可写入记忆
│  ├─ compressor.py                   # 上下文压缩算法
│  ├─ session_store.py                # 会话快照、恢复
│  └─ policy.py                       # 去重、衰减、评分、写入规则
│
├─ harness/                           # Agent 运行治理：Hook Pipeline
│  ├─ core/
│  │  ├─ types.py
│  │  ├─ pipeline.py
│  │  ├─ decorators.py
│  │  └─ signals.py
│  ├─ hooks/
│  │  ├─ tool/
│  │  ├─ memory/
│  │  └─ governance/
│  ├─ adapters/
│  │  ├─ langchain.py
│  │  └─ langgraph.py
│  └─ bootstrap.py
│
├─ observability/                     # Trace、日志、指标、Langfuse
│  ├─ tracing.py
│  └─ metrics.py
│
└─ eval/                              # 离线与端到端评测
   ├─ rubric.py
   └─ judge.py
```

## 5. AI 层：模型统一适配

`ai/` 的目标是将不同模型供应商的差异挡在 Agent 之外。

它应提供统一能力：

```text
输入统一 Message / Tool 定义
→ 转为特定供应商 API 格式
→ 发起普通或流式模型调用
→ 转回统一 AssistantMessage / ToolCall
→ 记录 Token 与成本
```

`ai/` 不应知道：

```text
“当前是第几轮”
“要不要调用搜索工具”
“用户偏好是什么”
“是否已经发生漂移”
```

这些属于 Agent Core、应用层或 Harness。

## 6. Agent Core：通用 Agent 运行时

`agent_core/` 是可复用的 Agent 引擎。它的最小职责是：

```text
维护通用 State
→ 调用模型
→ 收集 tool_call
→ 调度工具
→ 将 ToolResult 写回 messages
→ 决定下一轮或结束
→ 发出生命周期事件
```

一个通用 `AgentState` 可只包含运行时基础信息：

```python
class AgentState(TypedDict):
    messages: list[AgentMessage]
    system_prompt: str
    model: ModelConfig
    tools: list[AgentTool]
    streaming_message: AgentMessage | None
    error_message: str | None
```

这与 Pi 的核心 State 思路一致：会话消息、当前模型、System Prompt、可用工具、流式中的消息和错误信息都属于 Agent Runtime 的基础状态。[Pi SDK 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)

`agent_core/` 还应提供事件总线，例如：

```text
agent_start
turn_start
model_start
model_end
tool_start
tool_end
agent_end
error
```

这些事件适合 UI 流式渲染、Trace、日志和指标订阅。它们是“观察”接口；需要修改或拒绝执行时，应由上层 Harness 的控制接口完成。

## 7. Agent App：具体业务 Agent

`agent_app/` 是具体产品层。例如同一套 Core 可以被组装成：

```text
shopping_agent/
coding_agent/
customer_service_agent/
research_agent/
```

这里负责：

- 业务 System Prompt；
- 业务工具组合；
- Session 的创建、恢复、分支；
- Skills、模板、配置和资源加载；
- 当前业务的控制状态；
- 将工具、记忆、Harness、模型组装成可运行 Agent。

典型组装入口：

```python
def build_agent_app() -> Agent:
    tools = build_tool_set()
    memory_manager = build_memory_manager()
    harness = build_harness(memory_manager)

    return Agent(
        model=build_model(),
        tools=tools,
        system_prompt=build_system_prompt(),
        extensions=[harness],
    )
```

这里的重点是：应用层负责“选择和组合”，而通用 Core 负责“执行循环”。

## 8. Tools、Memory、Harness 的边界与协作

三者最容易混淆，应保持如下边界：

| 包 | 主要问题 | 示例 |
|---|---|---|
| `tools/` | “如何完成一个外部动作？” | 如何查询商品、如何比价、如何调用订单 API |
| `memory/` | “如何保存、检索和压缩信息？” | 如何从向量库检索偏好、如何压缩历史消息 |
| `harness/` | “何时执行、是否允许、如何治理？” | 调工具前查白名单；模型前压缩；结束后写记忆 |

因此：

```text
tools/item_search.py
→ 真正执行商品搜索

memory/compressor.py
→ 真正执行消息压缩

harness/hooks/tool/result_truncate.py
→ 工具返回后决定是否截断结果

harness/hooks/memory/context_compression.py
→ 模型调用前决定是否调用 compressor.py
```

Harness 的记忆 Hook 调用 `memory/` 的公开能力，而不是重新实现记忆算法。

## 9. State 如何分层

不要把完整消息、长期记忆和业务控制变量都塞进一个巨大的字典。建议至少分三层：

```text
AgentState
→ 通用运行状态，由 agent_core 管理

AgentControlState
→ 业务和 Harness 的控制状态，由 agent_app 管理

LongTermMemory Store
→ 跨会话的持久化偏好与事实，由 memory 管理
```

### 9.1 AgentState：通用运行状态

```text
messages
system_prompt
model
tools
streaming_message
error_message
```

### 9.2 AgentControlState：业务控制状态

```text
original_query
task_constraints
phase
round_count
called_tools
recent_actions_summary
token_used / token_budget
assertions_failed
drift_detected
inject_messages
```

### 9.3 LongTermMemory：长期数据

```text
用户稳定偏好
用户禁忌或黑名单
历史决策
可复用事实
```

`messages` 可以很长，长期记忆可能很大；两者都不应被直接当作每条 Hook 的控制变量。Hook 应优先读取紧凑、结构化的 `AgentControlState` 摘要。

## 10. 一次请求的完整调用链

```text
用户 Query
  ↓
api/
  ↓
agent_app/runtime.py
  ├─ 创建或恢复 Session
  ├─ 加载业务 AgentControlState
  ├─ 调用 Harness on_session_start
  ↓
agent_core/loop.py
  ├─ Harness pre_think
  ├─ ai/ 调用 LLM
  ├─ 有 tool_call 时：tool_runtime 调度 tools/
  ├─ Harness pre_tool_call / post_tool_call
  ├─ 更新 AgentState 与 AgentControlState
  └─ 决定继续还是结束
  ↓
agent_app/runtime.py
  ├─ Harness on_session_end
  ├─ memory/ 写入长期记忆
  └─ 返回最终结果
```

## 11. 新功能应放在哪里

| 新需求 | 推荐位置 |
|---|---|
| 新增一个商品搜索 API 工具 | `tools/search/`，并通过 `tools/registry.py` 暴露 |
| 新增模型供应商 | `ai/providers.py` 或对应 provider 模块 |
| 新增上下文压缩算法 | `memory/compressor.py` 或新的 memory service |
| 新增“工具调用前检查” | `harness/hooks/tool/` |
| 新增“模型调用前注入信息” | `harness/hooks/memory/` 或 `harness/hooks/governance/` |
| 新增循环、预算、输出控制 | `harness/hooks/governance/` |
| 新增某一业务 Agent | 新建 `agent_app/<business_name>/` 或独立应用包 |
| 新增 Trace、指标上报 | `observability/`，订阅 Core 事件或由 Trace Hook 调用 |
| 新增离线质量评分 | `eval/` |

## 12. 与 Harness Hook 文档的关系

两篇文档回答不同的问题：

| 文档 | 主要回答的问题 |
|---|---|
| `18-Harness-Hook基础知识.md` | Hook 是什么？在哪些生命周期运行？怎样注册、接入和治理？ |
| 本文 | 一个 Agent 项目总体怎样分层？AI、Core、业务、工具、记忆、Harness 应放在哪里？ |

可以简化记忆为：

```text
Agent Core：可复用的发动机
Agent App：具体业务的整车
Tools：具体执行动作的部件
Memory：存取和压缩信息的系统
Harness：管理运行过程的控制系统
AI：连接不同模型供应商的动力接口
```

