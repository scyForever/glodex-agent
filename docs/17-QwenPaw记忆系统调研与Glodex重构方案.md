# QwenPaw 记忆系统调研与 Glodex 重构方案

本文档用于回答两个问题：

1. QwenPaw 的记忆系统到底在什么时机检索、插入、压缩、写回。
2. Glodex Agent 的记忆系统应该如何重构，避免把关键策略只写在 prompt 或文档里。

结论先行：QwenPaw 的核心思想不是“让模型凭语言说明自己决定什么时候记忆”，而是把记忆做成 Agent 生命周期能力。模型只看到必要的使用说明；检索、注入、压缩、写回由 middleware / context manager 在固定钩子点执行。Glodex 应该沿用这个思想，用 LangChain agent middleware 重塑当前 `app/memory`。

## 1. QwenPaw 的三类记忆

QwenPaw 把记忆拆成三套互补系统：

| 类型 | 含义 | 主要实现 | 是否逐字 | 是否提炼 |
| --- | --- | --- | --- | --- |
| Working Memory | 当前模型窗口内的实时上下文 | `ScrollContextManager` 重建 `agent.state.context` | 部分逐字 | 不提炼 |
| Episodic Memory | 跨会话原始历史记录 | `history.db` + `recall_history_python` | 是 | 否 |
| Semantic Memory | 长期语义记忆、偏好、决策、知识 | ReMeLight / ADBPG + `memory_search` | 否 | 是 |

这个拆分很重要：

- 上下文压缩不是长期记忆。
- 原始历史召回不是语义记忆。
- 语义记忆不是完整聊天记录。
- prompt 只负责告诉模型怎么用这些能力，不负责决定这些能力什么时候运行。

## 2. QwenPaw 代码中的关键模块

QwenPaw 相关代码主要在这些位置：

| 位置 | 作用 |
| --- | --- |
| `src/qwenpaw/agents/memory/base_memory_manager.py` | 记忆后端抽象接口，定义生命周期和可选能力 |
| `src/qwenpaw/agents/memory/reme_light_memory_manager.py` | 默认 ReMe 文件化长期记忆后端 |
| `src/qwenpaw/agents/memory/adbpg_memory_manager.py` | ADBPG 云端长期记忆后端 |
| `src/qwenpaw/agents/middlewares.py` | `MemoryMiddleware` 和旧版工具结果裁剪中间件 |
| `src/qwenpaw/agents/context/scroll/manager.py` | Scroll 上下文管理，写穿历史、驱逐、索引重建 |
| `src/qwenpaw/agents/context/scroll/cap_middleware.py` | scroll 下单个超长工具结果写入历史并用指针替代 |
| `src/qwenpaw/agents/context/scroll/repl.py` | `recall_history_python` 原始历史召回工具 |
| `src/qwenpaw/runtime/builder.py` | 组装 agent、memory manager、middleware、scroll components |
| `src/qwenpaw/runtime/prompt_contributors.py` | 注入 memory/scroll 的 system prompt 指南 |
| `website/public/docs/memory.zh.md` | 长期记忆产品文档 |
| `website/public/docs/context.zh.md` | scroll 上下文管理文档 |

## 3. QwenPaw 的长期记忆后端抽象

`BaseMemoryManager` 定义了一组稳定接口：

```python
async def start()
async def close()
def get_memory_prompt() -> str
def list_memory_tools() -> list[Callable]
def build_middlewares() -> list[MiddlewareBase]
async def summarize(messages, **kwargs) -> str
async def dream(**kwargs) -> None
async def auto_memory_search(messages, agent_name="", **kwargs) -> dict | None
async def auto_memory(all_messages, **kwargs) -> None
```

这些接口表达了一个很清晰的边界：

- MemoryManager 不直接控制 Agent 主循环。
- AgentBuilder 负责把 MemoryManager 提供的 tools 和 middlewares 接入 Agent。
- Middleware 在生命周期钩子中调用 MemoryManager。
- 具体后端可以是 ReMeLight，也可以是 ADBPG。

这比 Glodex 当前的 `main_agent.run_agent()` 里直接调用 `store.read_relevant()` 和 `store.create()` 更可扩展。

## 4. QwenPaw 什么时候注入记忆说明

QwenPaw 有两条 prompt 注入路径。

第一条是 `MemoryMiddleware.on_system_prompt`：

```text
on_system_prompt
  -> memory_manager.get_memory_prompt()
  -> 如果当前 prompt 还没有这段说明，就追加到 system prompt 后面
```

这段 prompt 不是直接塞检索结果，而是告诉模型：

- 每次会话都是新的。
- 长期记忆在哪里。
- 什么情况下应该调用 `memory_search`。
- 不要随便覆盖记忆文件。

第二条是 PromptContributor：

```text
PromptManager
  -> AgentsMdContributor / WorkspacePromptFilesContributor
  -> 如果 AGENTS.md 或工作区 prompt 文件中有 memory section，就处理
  -> ScrollContextContributor 追加 scroll/recall 使用说明
```

也就是说，QwenPaw 把“记忆系统怎么用”的说明注入 system prompt，但不把“什么时候执行检索/压缩/写回”的责任交给 prompt。

## 5. QwenPaw 什么时候自动检索

自动检索发生在模型调用前。

核心位置：`MemoryMiddleware.on_model_call`。

流程：

```text
on_model_call
  -> 判断是否 cron / heartbeat 等自动化请求
  -> 自动化请求跳过 memory search
  -> 找到最近一个真实 user turn 的 msg.id
  -> 如果这一轮还没检索过
      -> memory_manager.auto_memory_search(agent.state.context)
      -> 后端根据最近消息构造 query
      -> 调 memory_search / ReMe search / ADBPG search
      -> 构造一组“已完成的 tool_call + tool_result 消息”
      -> 临时追加到本次 model input
      -> 如配置 persist_to_context=true，再写回 agent.state.context
  -> 调用真实模型
```

检索结果的插入形态不是普通文本拼到 prompt，而是伪造一段已完成工具交互：

```text
assistant: 我正在搜索记忆...
assistant tool_call: memory_search({"query": "...", "max_results": 2})
assistant tool_result: 检索结果文本
```

这种做法的好处：

- 模型能清楚知道这些内容来自 `memory_search`。
- 不污染 system prompt。
- 可以选择是否持久化到上下文。
- 和模型正常工具观察格式一致。

ReMeLight 的自动检索默认关闭：

```text
reme_light_memory_config.auto_memory_search_config.enabled = false
```

ADBPG 的自动检索默认开启：

```text
adbpg_memory_config.auto_memory_search_config.enabled = true
```

两者都会从最近的 user / assistant 消息末尾构造短 query，默认最多取最近约 50 字符。

## 6. QwenPaw 什么时候手动检索

QwenPaw 还把 `memory_search` 注册成普通工具。

AgentBuilder 组装 Agent 时：

```text
memory_manager.list_memory_tools()
  -> [memory_search]
  -> 注册到 Toolkit
```

所以检索有两种模式：

| 模式 | 触发者 | 时机 | 结果进入哪里 |
| --- | --- | --- | --- |
| 自动检索 | Middleware | 每次模型调用前 | 本次 model input，可选写回 context |
| 手动检索 | 模型调用工具 | Agent 认为需要时 | 正常工具结果进入 context |

这也是一个值得借鉴的设计：自动检索负责兜底，手动检索负责模型主动追问历史。

## 7. QwenPaw 什么时候写入长期记忆

长期记忆写入发生在模型回复完成后。

核心位置：`MemoryMiddleware.on_reply`。

流程：

```text
on_reply
  -> 先让真实 reply 完整执行
  -> 跳过 cron / heartbeat 自动化请求
  -> 修复自动检索消息可能被合并的问题
  -> 找到最新真实 user turn
  -> 如果该 user turn 未处理过
      -> 加入 pending_auto_memory_turn_markers
  -> 读取 memory_manager.get_auto_memory_interval()
  -> 如果 pending 数量达到 interval
      -> _flush_auto_memory()
      -> memory_manager.auto_memory(messages, session_id=...)
```

ReMeLight 的默认写入节奏：

```text
auto_memory_interval = 5
```

意思是每 5 个用户回合触发一次 Auto-Memory。触发后不会阻塞主回复，而是通过 `add_summarize_task()` 进入后台队列，串行调用 ReMe 的 `auto_memory` job。

ADBPG 的写入节奏：

```text
get_auto_memory_interval() = 1
```

它每轮持久化新的 user 消息。真正的事实抽取由服务端完成，所以本地只负责把 user messages 发送给 ADBPG。

## 8. QwenPaw 什么时候在压缩前写记忆

QwenPaw 有一个特别关键的钩子：`MemoryMiddleware.on_compress_context`。

流程：

```text
on_compress_context
  -> 自动化请求跳过记忆写入，但仍允许压缩继续
  -> 如果配置 summarize_when_compact=true
  -> 且存在 pending auto-memory turns
  -> 且即将触发上下文压缩
      -> 先 _flush_auto_memory()
  -> 再调用真正的 compress_context()
```

这解决了一个很实际的问题：如果上下文马上要被压缩、驱逐或折叠，应该先把尚未沉淀的回合交给长期记忆系统。否则重要内容可能还没写入就从实时窗口消失。

这也是 Glodex 当前最缺的一段生命周期逻辑。

## 9. QwenPaw 如何做上下文压缩

QwenPaw 的上下文管理和长期记忆是分开的。

默认策略是 `scroll`，不是传统摘要压缩。

### 9.1 每次保存都写穿历史

`QwenPawAgent._save_to_context()` 在 AgentScope 把新 blocks 写入 context 后，会调用：

```text
context_manager.on_save(agent, blocks)
```

`ScrollContextManager.on_save()` 再执行：

```text
_persist_new(agent)
  -> 遍历 agent.state.context
  -> 新消息写入 history.db
  -> tool_result 独立写入
  -> assistant headline 抽取成索引叶子
```

这叫 write-through：内容进入实时窗口的同时，也进入持久历史。

### 9.2 触发压缩时先保证持久化

`QwenPawAgent.compress_context()` 会委托给 context manager：

```text
ScrollContextManager.compress(agent)
```

压缩流程：

```text
1. 先 _persist_guarded(agent)
   如果写 history.db 失败，不驱逐

2. 用模型 count_tokens 估算当前输入
   小于 trigger_ratio * context_size 则不压缩

3. 用 AgentScope 的 pairing-safe split
   得到 pinned head、可驱逐 middle、recent tail

4. middle 写入 eviction index
   index 记录 seq 范围和 assistant headline

5. 重建实时上下文
   head + [memory 占位消息: context compressed map] + tail

6. 如果仍超预算
   压缩 eviction index 自身的层级
```

关键点：

- 不用 LLM 摘要旧上下文。
- 不丢原文。
- index 只是地图，真相在 `history.db`。
- 如果历史没有成功持久化，就不允许驱逐。

### 9.3 被驱逐内容如何召回

scroll 会注册工具：

```text
recall_history_python
```

这个工具给模型一个沙箱 Python 环境，预置 `ms = MemorySpace(...)`。

常用接口：

```python
ms.expand(lo, hi)        # 展开 seq 区间
ms.search(query, k=20)   # 搜索跨会话原始历史
ms.recall_tool(id)       # 找回完整工具结果
ms.sessions()            # 列出历史会话
ms.session(session_id)   # 读取某个会话
```

安全边界也很强：没有 sandbox 配置时默认拒绝执行。只有同时设置环境变量和 agent 配置时，才允许非沙箱 recall。

## 10. QwenPaw 如何处理超长工具结果

QwenPaw 有两层工具结果控制。

第一层是 execution layer limiter：

```text
ToolCoordinatorMiddleware + ToolResultLimiter
```

它在工具结果进入 agent context 前做硬上限控制。

第二层是 middleware：

```text
ToolResultPruningMiddleware.on_acting
```

它在每次工具执行后扫描 context 中的 tool_result：

- 最近 N 条保留较大上限。
- 更老结果压到较小上限。
- 特定工具或文件类型可以豁免。
- 完整输出可保存到 `tool_results/` 文件。

如果启用 scroll，还会加入更强的：

```text
ToolResultCapMiddleware.on_acting
```

它对单个超长工具结果：

```text
超过 token_cap
  -> 完整输出写入 history.db
  -> 实时上下文替换成预览
  -> 附带 ms.recall_tool(tool_call_id) 指针
```

这比单纯截断更好，因为它不会丢数据。

## 11. QwenPaw 的 ReMeLight 记忆数据流

ReMeLight 不是写一个简单 JSONL，而是一套文件化记忆流：

```text
Conversation messages
  -> mem_session/dialog/<session_id>.jsonl
  -> memory/<date>/<session_id>.md
  -> memory/<date>.md
  -> digest/* via auto_dream
  -> interests.yaml for proactive
```

主要目录：

| 目录 | 作用 |
| --- | --- |
| `mem_session/` | 原始会话来源 |
| `memory/` | daily 记忆卡片 |
| `digest/` | 长期 digest 记忆 |
| `resource/` | 外部资料输入 |
| `mem_metadata/` | 索引、catalog、graph、embedding cache |

检索由 ReMe `search` job 完成：

- BM25 关键词检索默认启用。
- 配置 embedding 后启用向量检索。
- 两路结果用 RRF 融合。
- 默认搜索 daily 和 digest。
- `enable_search_raw_log=true` 时也索引 resource / jsonl。

## 12. QwenPaw 的 ADBPG 记忆数据流

ADBPG 后端把长期记忆交给云端服务：

```text
on_reply / auto_memory
  -> 过滤 user messages
  -> fire-and-forget add_memory()
  -> 服务端做事实抽取
```

检索：

```text
memory_search
  -> ADBPG semantic search
  -> 同时 keyword 搜本地 MEMORY.md 和 memory/*.md
  -> 合并结果返回
```

ADBPG 更偏“服务端语义记忆”，ReMeLight 更偏“本地文件化记忆”。

## 13. QwenPaw 对 Glodex 的启发

Glodex 当前 `app/memory` 已有不少代码：

```text
store.py       JSONL 长期记忆
injector.py    prompt 注入
extractor.py   任务结束抽取
policy.py      去噪、去重、打分
breakpoint.py  压缩分界
compressor.py  上下文压缩
session.py     会话记忆事件
tool_guard.py  工具结果截断
```

但当前接入方式偏硬编码：

```text
main_agent.run_agent()
  -> 任务开始前 read_relevant
  -> 注入 system_prompt
  -> 任务结束后 extract_memories + store.create
```

这部分已经能工作，但问题是：

- 长期记忆检索写死在 `run_agent()` 开头。
- 写回只发生在最终回复后。
- 压缩模块没有真正挂到 AgentLoop 生命周期。
- SessionMemory 没有参与上下文压缩和阶段摘要。
- 工具结果截断分散在 agent middleware / memory tool_guard 之间。
- “什么时候压缩、什么时候写入”还主要停留在文档规划。

因此 Glodex 应该学习 QwenPaw 的结构，而不是直接照搬 ReMe 或 Agentscope。

## 14. Glodex 重构目标

目标不是把记忆系统做复杂，而是把边界理顺：

```text
MemoryManager：管长期记忆能力
MemoryMiddleware：管生命周期接入
ContextManager：管上下文压缩和会话历史
ToolResultGuard：管工具结果进入上下文前后的体积
Prompt：只说明能力如何使用，不承担触发策略
```

重构后，Glodex 的 AgentLoop 应该变成：

```text
before_agent
  -> 初始化 thread/session/user context
  -> 初始化 memory snapshot

before_model
  -> 自动检索长期记忆
  -> 注入本次 model input 或 system message
  -> 检查 token 阈值，必要时先压缩

wrap_tool_call / after_tool
  -> 工具结果过长先落盘或截断
  -> 工具结果进入 context 前控制体积

after_model / after_agent
  -> 记录本轮消息
  -> 按 interval 或最终回复写入长期记忆

on_compress / before_compact
  -> 压缩前 flush pending memory
  -> 再执行上下文压缩
```

## 15. 建议的新模块结构

建议把 `app/memory` 重构成：

```text
app/memory/
  manager.py
  middleware.py
  store.py
  schemas.py
  policy.py
  extractor.py
  injector.py
  retrieval.py
  context.py
  compressor.py
  session.py
  tool_guard.py
```

职责：

| 文件 | 职责 |
| --- | --- |
| `manager.py` | 定义 `BaseMemoryManager` 和 `JsonlMemoryManager` |
| `middleware.py` | LangChain middleware 接入点 |
| `store.py` | JSONL / 后续 DB 存储实现 |
| `retrieval.py` | read_relevant、query 构造、打分封装 |
| `injector.py` | 把记忆转成 system/message/tool-result 片段 |
| `extractor.py` | 从最终回复、工具结果、用户显式要求中抽取记忆 |
| `context.py` | ContextManager 抽象，未来可做 scroll-like 历史 |
| `compressor.py` | 当前压缩实现，迁到 middleware 调用 |
| `session.py` | 当前任务级事件和阶段摘要 |
| `tool_guard.py` | 工具结果截断、落盘、指针 |

## 16. Glodex 的 MemoryManager 接口草案

```python
class BaseMemoryManager(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...

    def get_memory_prompt(self) -> str: ...
    def list_memory_tools(self) -> list[Callable]: ...

    async def auto_memory_search(
        self,
        messages: list[Any],
        *,
        user_id: str,
        thread_id: str,
    ) -> MemorySearchResult | None: ...

    async def auto_memory(
        self,
        messages: list[Any],
        *,
        user_id: str,
        thread_id: str,
        final_text: str | None = None,
    ) -> None: ...

    async def flush_pending(
        self,
        *,
        user_id: str,
        thread_id: str,
    ) -> None: ...
```

第一版可以只有一个 `JsonlMemoryManager`，内部复用现有：

- `store.read_relevant`
- `store.create`
- `extract_memories`
- `format_memories_for_prompt`

## 17. LangChain middleware 映射建议

LangChain `create_agent` 官方支持 node-style 和 wrap-style middleware。可用钩子包括：

```text
before_agent
before_model
after_model
after_agent
wrap_model_call
wrap_tool_call
```

对应 Glodex：

| LangChain 钩子 | Glodex 行为 |
| --- | --- |
| `before_agent` | 初始化 session_dir、thread context、memory manager |
| `before_model` | 自动检索长期记忆，检查是否需要压缩 |
| `wrap_model_call` | 动态修改 system message，注入 memory snapshot |
| `wrap_tool_call` | 工具执行后做结果截断、落盘、指针化 |
| `after_model` | 记录本轮 assistant 输出，更新 pending turns |
| `after_agent` | 最终抽取长期记忆并写回 |

如果当前 LangChain 版本接入 middleware 成本较高，也可以先做一个 `run_agent()` 外层 wrapper，模拟这些阶段。但长期应迁到 middleware，因为这和 LangChain 的 Agent 生命周期对齐。

## 18. Glodex 自动检索策略

建议不要每轮都把 JSONL 相关记忆硬塞进 system prompt，而是分两级。

第一版简单策略：

```text
before_model
  -> 如果 user_id 存在
  -> 根据最近 user message 构造 query
  -> store.read_relevant(user_id, query, top_k=5)
  -> format_memories_for_prompt()
  -> 注入 system message 的“长期记忆”段
```

第二版更像 QwenPaw：

```text
auto_memory_search
  -> 构造一组已完成 tool_call/tool_result
  -> 临时追加到本次 model input
  -> 默认不持久化到 context
```

推荐第二版，因为它更清楚地告诉模型“这些内容来自检索”，也不会让 system prompt 越来越厚。

## 19. Glodex 自动写入策略

建议分三种写入：

| 触发 | 时机 | 写什么 |
| --- | --- | --- |
| final flush | `after_agent` | 本轮最终沉淀的偏好/约束/纠错 |
| interval flush | 每 N 个真实 user turn | 阶段性偏好、关键决策 |
| compact flush | 压缩前 | 尚未写入但即将被压缩掉的上下文摘要 |

第一版可以只实现：

```text
after_agent
  -> extract_memories(user_query, final_text, learned_preferences)
  -> store.create(...)
```

但要把它从 `main_agent.py` 移到 `MemoryMiddleware.after_agent`，这样后续 interval / compact flush 不需要再改主 Agent。

## 20. Glodex 上下文压缩策略

当前 `app/memory/compressor.py` 已经有：

- token 粗估
- breakpoint
- tool/function 消息截断
- sliding window

建议第一版接入方式：

```text
before_model
  -> estimate_messages_tokens(messages)
  -> 如果超过 max_tokens * 0.8
      -> memory_manager.flush_pending()
      -> compress_messages()
      -> 把 compression event 写入 SessionMemory
```

第二版再考虑 QwenPaw scroll-like 方案：

```text
history.jsonl / history.db
  -> 每轮 write-through
  -> 压缩时 head + index + tail
  -> recall_history 工具按 id / seq 找回原文
```

对 Glodex 当前阶段，不建议一上来做完整 scroll。原因：

- Glodex 购物 Agent 的对话长度和历史召回复杂度低于 QwenPaw。
- 当前最急的是把压缩时机工程化。
- JSONL 长期记忆已经够支撑第一版购物偏好。

## 21. Glodex 工具结果处理策略

建议统一为三层：

```text
execution limit
  工具返回前控制最大字节数

context prune
  工具结果进入上下文后，旧结果进一步压缩

offload pointer
  超长结果写到 output/tool_results/*.txt，context 只保留摘要和路径
```

当前 `dispatch_tool` 里已经有 `truncate_long_tool_result()`，`app/memory/tool_guard.py` 也有截断函数。后续应该归并为一个 `ToolResultMemoryMiddleware`，避免各工具各自截断。

## 22. 重构后的主流程

目标流程：

```text
API / run_agent
  -> 创建 AgentRuntimeContext(thread_id, user_id, session_dir)
  -> create_agent(..., middleware=[
       MemoryMiddleware(memory_manager),
       ContextCompressionMiddleware(context_manager),
       ToolResultMemoryMiddleware(...)
     ])
  -> agent.ainvoke(...)
```

MemoryMiddleware 内部：

```text
before_agent:
  set_thread_context()
  memory_manager.start()

before_model:
  maybe_auto_search()
  maybe_compress_before_model()

wrap_model_call:
  inject_memory_snapshot()

wrap_tool_call:
  prune_or_offload_tool_result()

after_agent:
  flush_final_memory()
  memory_manager.close()
```

`main_agent.py` 最终应该只负责：

- 创建 session。
- 构建 agent。
- 调用 agent。
- 返回结果。

不再直接知道 JSONL store、extractor、injector 的细节。

## 23. 和 QwenPaw 的差异取舍

不建议照搬：

- ReMe jobs 全套文件图谱。
- digest / dream / proactive。
- sandboxed Python recall。
- scroll eviction index。

建议吸收：

- MemoryManager 抽象。
- middleware 生命周期接入。
- 自动检索结果作为 tool_call/tool_result 注入。
- 写入按 interval / compact / final 分层。
- 压缩前先 flush memory。
- 工具结果先持久化再截断，避免丢数据。

## 24. 分阶段实施计划

### Phase 1：生命周期接入

目标：不改存储模型，只把当前记忆逻辑从 `main_agent.py` 移到 middleware。

任务：

1. 新增 `app/memory/manager.py`。
2. 新增 `app/memory/middleware.py`。
3. `JsonlMemoryManager` 包装现有 `store / extractor / injector`。
4. `main_agent.py` 改为通过 `create_agent(..., middleware=[...])` 接入。
5. 保持现有 JSONL 文件兼容。

验收：

- 有 user_id 时会自动检索并注入。
- 无 user_id 时不启用长期记忆。
- 最终回复后仍能写入 JSONL。
- 子 Agent 能复用同一 memory snapshot。

### Phase 2：压缩工程化

目标：把 `compressor.py` 真正接进模型调用前。

任务：

1. 新增 `ContextCompressionMiddleware`。
2. 在 `before_model` 判断 token 阈值。
3. 压缩前调用 `memory_manager.flush_pending()`。
4. 压缩结果写入 `SessionMemory`。
5. 测试超长 messages 能被压缩，不破坏 system prompt 和最近 user intent。

验收：

- 超阈值才压缩。
- 压缩前会写 pending memory。
- 压缩不会吞掉最近工具结果和当前用户需求。

### Phase 3：工具结果统一治理

目标：统一 `dispatch_tool`、工具返回、memory.tool_guard 的截断逻辑。

任务：

1. 新增 `ToolResultMemoryMiddleware`。
2. 超长工具结果写入 `output/tool_results/`。
3. context 中保留摘要、文件路径和必要字段。
4. `dispatch_tool` 复用统一逻辑。

验收：

- 大结果不会污染主 loop。
- 完整结果仍可追溯。
- 工具结果截断提示统一。

### Phase 4：可选召回历史

目标：如果购物 Agent 后续也需要跨会话原始历史，再实现轻量 scroll。

任务：

1. 增加 `output/memory/history.jsonl` 或 SQLite。
2. 每轮 write-through。
3. 压缩时保留 head + compact index + tail。
4. 增加只读 `recall_history` 工具。

验收：

- 被压缩原文可召回。
- 不依赖 LLM 摘要作为唯一事实来源。

## 25. 最终判断

当前 Glodex 记忆系统不是没有代码，而是“长期记忆已经接入，生命周期还没有抽象出来”。QwenPaw 给出的关键经验是：

```text
记忆系统不应该主要靠 prompt 约束。
检索、压缩、写回都应该是 Agent 生命周期里的工程钩子。
prompt 只告诉模型如何使用记忆，不负责保证记忆策略被执行。
```

因此 Glodex 的重构方向应该是：

```text
从 main_agent 里的手工调用
  -> 迁移到 MemoryManager + LangChain Middleware

从文档说明压缩时机
  -> 迁移到 before_model / wrap_tool_call / after_agent 钩子

从直接塞 system prompt
  -> 迁移到可控的 memory snapshot / tool_result 注入
```

这会让记忆系统更轻、更稳定，也更容易继续演进。
