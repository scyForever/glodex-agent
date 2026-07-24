# LangChain 与 LangGraph API 使用说明

## app.agent.llm

`app/agent/llm.py` 负责统一创建 Agent 运行时使用的 LLM 客户端，目前使用 LangChain 的 `init_chat_model` API。

### 使用的 API

```python
from langchain.chat_models import init_chat_model
```

`init_chat_model` 用于根据模型名称、模型提供商、鉴权信息和推理参数创建聊天模型实例。当前项目通过 `model_provider="openai"` 接入 OpenAI 兼容接口。

### get_llm

`get_llm()` 返回主 AgentLoop 和子 AgentLoop 共用的 LLM 实例。

配置项：

- `LLM_MAIN`：主模型名称，必须配置。
- `OPENAI_API_KEY`：OpenAI 兼容接口密钥，必须配置。
- `OPENAI_BASE_URL`：OpenAI 兼容接口地址，必须配置。
- `temperature=0.3`：主模型保留少量随机性，用于提升生成结果的灵活度。

实现上使用 `@lru_cache(maxsize=1)` 缓存模型实例，避免同一进程内重复创建客户端。

### get_judge_llm

`get_judge_llm()` 返回 Rubric judge 使用的评审模型实例。

配置项：

- `LLM_JUDGE`：评审模型名称，可选；未配置时默认使用 `qwen-max`。
- `OPENAI_API_KEY`：OpenAI 兼容接口密钥，必须配置。
- `OPENAI_BASE_URL`：OpenAI 兼容接口地址，必须配置。
- `temperature=0.0`：评审模型要求结果稳定，因此固定为确定性输出。

同样使用 `@lru_cache(maxsize=1)` 缓存模型实例，保证评审逻辑复用同一个客户端。

## app.agent.main_agent

`app/agent/main_agent.py` 是主 AgentLoop 入口，目前使用 LangChain v1 推荐的 `create_agent` API 创建 Agent。

### 使用的 API

```python
from langchain.agents import create_agent
```

`create_agent` 是 LangChain v1 的标准 Agent 创建方式，用于组合模型、工具和 system prompt。旧版 `langgraph.prebuilt.create_react_agent` 已不再作为当前项目入口使用。

当前主入口写法：

```python
agent = create_agent(
    model=get_llm(),
    tools=FULL_TOOL_SET,
    system_prompt=system_prompt,
)
```

配置说明：

- `model=get_llm()`：复用 `app.agent.llm` 中统一创建的主模型实例。
- `tools=FULL_TOOL_SET`：主 AgentLoop 和子 AgentLoop 共用 `app.tools.tool_registry.FULL_TOOL_SET`，保证同质 fork。
- `system_prompt=system_prompt`：使用 `get_system_prompt(...)` 生成系统提示词，并预留长期偏好注入位置。

### 调用方式

主入口通过异步方式调用 Agent：

```python
result = await agent.ainvoke(
    {
        "messages": [
            {"role": "user", "content": query},
        ],
    },
    config={
        "configurable": {
            "thread_id": thread_id,
        },
        "recursion_limit": MAIN_AGENT_MAX_ITERATIONS,
    },
)
```

`messages` 是 LangChain Agent 的输入状态，当前只放入用户本轮问题。`thread_id` 用于把本轮调用绑定到当前会话，`recursion_limit` 用于限制 Agent 循环次数。

### 预留能力

以下能力的接入代码已经在 `main_agent.py` 中用中文 TODO 注释预留，等对应模块实现后取消注释即可：

- 长期记忆 Store：`app.memory.store.store`
- 上下文压缩：`app.memory.breakpoint.compute_breakpoint` 和 `app.memory.compressor.compress_messages`

## app.agent.dispatch_tool

`app/agent/dispatch_tool.py` 负责把 `dispatch_tool` 注册成 LangChain 工具，让主 AgentLoop 可以派发同质子 AgentLoop。

### 使用的 API

```python
from langchain_core.tools import tool
```

`@tool` 装饰器会把普通异步函数包装成 LangChain 可识别的工具。函数签名、类型标注和 docstring 会作为工具 schema 的来源，供模型决定什么时候调用该工具。

当前写法：

```python
@tool
async def dispatch_tool(demands: str) -> str:
    ...
```

配置说明：

- `demands`：主 AgentLoop 拆出来的子任务描述。
- 返回值：子 AgentLoop 的最终回复，或者 fork 超限、子任务超时等可读错误字符串。
- 异常策略：`ForkLimitExceeded` 和 `asyncio.TimeoutError` 不向外抛出，而是转成普通工具结果，避免主 AgentLoop 崩溃。

`dispatch_tool` 内部仍然使用 `create_agent(...)` 创建子 AgentLoop，并传入同一份 `FULL_TOOL_SET`，保证主 loop 和子 loop 的工具能力一致。

## app.tools.item_picker

`app/tools/item_picker.py` 使用同一个 `@tool` API 把 `item_picker` 暴露给 AgentLoop。

当前写法：

```python
@tool
async def item_picker(
    candidates: list[CandidateItem],
    insight: dict[str, Any] | None = None,
    user_preferences: list[str] | None = None,
    top_n: int = 3,
    max_budget_cny: float | None = None,
) -> ItemPickerOutput:
    ...
```

`item_picker` 是国内电商链路里的精选工具，接收标准化候选商品，按硬约束、预算、包邮、配送时效、评分、销量、店铺可信度、用户软偏好和品类洞察进行筛选排序。

候选商品字段大多是可选字段。字段缺失时，工具不会默认淘汰商品；只有明确违反硬约束时才放入 `rejected_brief`。

## app.tools.shopping_summary

`app/tools/shopping_summary.py` 使用 `@tool` 暴露 `shopping_summary`，并通过 `get_llm().ainvoke(...)` 生成最终 Markdown 购物建议。

当前写法：

```python
@tool
async def shopping_summary(
    picks: list[PickedItem],
    user_query: str,
    new_preferences: list[str] | None = None,
) -> ShoppingSummaryOutput:
    ...
```

`shopping_summary` 是终结性工具。它只负责把 `ItemPicker` 的精选结果整理成用户可读的最终答复，不重新搜索、不重新筛选，也不再发起新的工具调用。

返回值包含：

- `final_text`：最终 Markdown 答复。
- `picks`：本次推荐商品。
- `learned_preferences`：本轮沉淀的新偏好，后续可写入长期记忆 Store。

## app.tools.planner

`app/tools/planner.py` 使用 `@tool` 暴露 `planner`，并通过 `get_llm().ainvoke(...)` 把用户输入拆成结构化购物计划。

当前输出结构为 `ShoppingPlan`，核心字段包括：

- `intent`：`shopping`、`chat` 或 `other`。
- `category`：商品品类。
- `budget_cny`：人民币预算。
- `hard_constraints`：硬约束，例如“不要塑料”“必须包邮”。
- `soft_preferences`：软偏好，例如“小众”“简约”。
- `platform_preferences`：平台偏好。
- `search_queries`：后续 `ItemSearch` 可使用的检索词。
- `need_category_insight`：是否需要品类洞察。
- `need_web_search`：是否需要外部事实补全。
- `should_fork` 和 `fork_demands`：是否适合派发同质子 AgentLoop。

Planner 要求模型输出严格 JSON；如果解析失败，会降级为保守计划，把用户原始输入作为检索词。

## app.tools.chat_fallback

`app/tools/chat_fallback.py` 使用 `@tool` 暴露 `chat_fallback`，用于处理闲聊、非购物意图或普通问答。

`chat_fallback` 会调用 `get_llm().ainvoke(...)` 生成简洁中文回复，但不会编造商品、价格、库存或平台信息，也不会发起商品搜索。

## app.tools.web_search

`app/tools/web_search.py` 使用 `@tool` 暴露 `web_search`，当前底层调用 Bocha Web Search API。

当前写法：

```python
@tool
async def web_search(
    query: str,
    count: int = 10,
    freshness: Literal["noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"] = "noLimit",
    summary: bool = True,
) -> WebSearchOutput:
    ...
```

配置项：

- `BOCHA_API_KEY`：Bocha API Key，必须配置后才能真实调用。
- `BOCHA_WEB_SEARCH_ENDPOINT`：Bocha Web Search API 地址，默认 `https://api.bochaai.com/v1/web-search`。

当前版本只返回 Bocha API 的 `raw_response`，不做去重、清洗、来源分级或可信度判断。后续会在此基础上增加结果清洗层。

## app.tools.item_search

`app/tools/item_search.py` 使用 `@tool` 暴露 `item_search`，用于从已有商品索引中搜索国内电商候选商品。

当前写法：

```python
@tool
async def item_search(
    query: str,
    platform: Platform = "all",
    top_k: int = 20,
    user_id: str | None = None,
) -> ItemSearchOutput:
    ...
```

`item_search` 不负责订单侠等上游 API 的数据采集或入库。订单侠负责填充商品库，`item_search` 只查询已有索引并返回 `CandidateItem` 列表。

当前第一版使用 `ITEM_SEARCH_INDEX_PATH` 指向的 JSON / JSONL 商品索引做轻量关键词检索。后续接入 Milvus、Elasticsearch 或混合召回时，保持工具输入输出不变，只替换底层搜索实现。
