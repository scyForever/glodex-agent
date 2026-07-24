# ItemSearch 工具设计

`ItemSearch` 是 Glodex Agent 的第一个核心电商工具。它负责从国内电商平台搜索商品候选，并把不同平台返回的数据整理成统一结构，供主 AgentLoop 后续合流、比价、筛选和总结。

## 一、AgentLoop 链路

国内购物任务的基本链路：

```text
用户购物意图
-> Planner 拆解（预算 / 品类 / 配送偏好 / 包邮需求）
-> 主 AgentLoop 在 Think 阶段判断："要跨多个国内平台"
-> dispatch_tool fork 多个同质子 AgentLoop
    ├─ 子 A: 调 item_search(platform="jd", ...)      # 京东联盟官方
    ├─ 子 B: 调 item_search(platform="pdd", ...)     # 多多进宝官方
    └─ 子 C: 调 item_search(platform="taobao", ...)  # 万邦聚合补充淘宝
-> 多份商品候选合流回主 loop
-> PriceCompare / ItemPicker / ShoppingSummary
```

这里的平台不是固定三个。只要是国内平台或国内商品数据源，都可以作为 `ItemSearch` 的搜索来源。主 AgentLoop 可以根据用户需求、平台可用性和配置，动态决定 fork 哪些平台。

如果成熟版已经有完整、足够新的统一商品库，那么主 loop 不一定需要 fork 多个子 Agent 分平台实时搜索。此时可以直接查统一商品库 / Milvus，平台只是过滤字段，例如 `platform in ["jd", "pdd", "taobao"]`。

分平台子 Agent 的价值主要在商品库不完整或需要实时校验时：

- 某个平台商品没有同步进库。
- 价格、库存、优惠券、运费信息可能过期。
- 用户 query 很长尾，库内召回不足。
- 需要临时补某个平台的实时结果。

因此成熟系统更适合混合路线：

```text
统一商品库 / Milvus = 主召回
分平台子 Agent = 实时补数 / 兜底校验
```

如果库内结果够多、够新，就不 fork；如果某个平台缺数据或字段过期，再 fork 对应平台子 Agent 去补。

## 二、主 Loop Prompt 与 dispatch_tool

主 loop 的 prompt 要明确告诉模型：当下一步子任务能并行时，应该调用 `dispatch_tool(demands="...")`。

```text
当下一步子任务满足以下任一条件，你应该调 dispatch_tool(demands="..."):
1. 能并行：多个独立检索可以同时跑（如跨多个国内平台 ItemSearch）
```

模型在 Think 阶段可以产出多个工具调用：

```text
dispatch_tool(demands="在 jd 上搜：旅行收纳袋 不要塑料 小众 预算300")
dispatch_tool(demands="在 pdd 上搜：旅行收纳袋 不要塑料 小众 预算300")
dispatch_tool(demands="在 taobao 上搜：旅行收纳袋 不要塑料 小众 预算300")
```

每个 `dispatch_tool` 调用 fork 一个同质子 AgentLoop。子 loop 内部 Think 一次后调 `item_search(platform="...")`，拿到结果返回主 loop。

### dispatch_tool 的并发实现

`dispatch_tool` 是单次 fork。多个 fork 真正并发，靠主 loop 的 LLM 在一次回复里返回多个 `tool_call`，LangGraph 会用 `asyncio.gather` 同时执行。

```python
# app/agent/dispatch_tool.py（节选）
from uuid import uuid4
from langchain_core.tools import tool
from app.agent.llm import get_llm
from app.agent.prompts import get_system_prompt
from app.api.context import _thread_id_var, _session_dir_var, get_session_dir
from app.api.monitor import monitor
from langgraph.prebuilt import create_react_agent


@tool
async def dispatch_tool(demands: str) -> str:
    """派一个同质子AgentLoop 去执行 demands，返回它的最终回复。
    适用条件（任一即可）：
    1. 能并行：多个子任务可以同时跑
    2. 上下文要隔离：子任务输出很大，不应污染主 loop
    3. 调用链 >= 3：子任务自己内部还要多轮 Think -> Act
    """
    sub_thread_id = f"sub-{uuid4().hex[:8]}"
    parent_session_dir = get_session_dir()
    await monitor.report_fork(sub_thread_id, demands)

    sub_agent = create_react_agent(
        model=get_llm(),
        tools=FULL_TOOL_SET,        # 同质：和主 loop 同一份工具集
        prompt=get_system_prompt(), # 同质：同一段 system prompt
    )

    token_t = _thread_id_var.set(sub_thread_id)
    token_s = _session_dir_var.set(parent_session_dir)
    try:
        result = await sub_agent.ainvoke(
            {"messages": [("user", demands)]},
            config={"configurable": {"thread_id": sub_thread_id}},
        )
        return result["messages"][-1].content
    finally:
        _thread_id_var.reset(token_t)
        _session_dir_var.reset(token_s)
```

注意：`FULL_TOOL_SET` 里包含 `dispatch_tool` 自己，子 Agent 理论上也能再往下 fork。后续需要用 `max_depth` 防止 fork 链失控。

### 什么时候不 fork

如果用户说：

```text
只在淘宝上找带壶嘴的咖啡杯。
```

只有一个平台、一个 query。

| 条件 | 判断 |
| --- | --- |
| 能并行 | 否 |
| 上下文要隔离 | 否，20 件候选不算大 |
| 调用链 >= 3 | 否 |

这时主 loop 直接调 `item_search`，不 fork。AGUI 事件流会更短，没有 fork 事件，前端展示就是一条直链。

## 三、工具职责

`ItemSearch` 只负责一件事：

**按指定国内平台搜索商品候选，并返回标准化商品列表。**

它应该做：

- 根据 `query` 搜商品。
- 按 `platform` 调用对应平台数据源。
- 返回候选商品列表。
- 标准化标题、价格、平台、链接、图片、店铺、销量、评分等字段。
- 带回国内运费、包邮、配送时效等基础信息。

它不应该做：

- 不做最终推荐。
- 不做复杂主观筛选。
- 不写采购清单。
- 不单独计算关税。
- 不负责完整跨平台同款归并。

国内场景先移除独立 `ShippingCalc`：没有跨境关税逻辑，普通运费、包邮、配送时效合并进 `ItemSearch` 的商品字段，以及后续 `PriceCompare` 的总价计算。

## 四、国内平台来源

第一批优先国内平台：

| 平台 | platform | 来源策略 |
| --- | --- | --- |
| 京东 | `jd` | 京东联盟官方 |
| 拼多多 | `pdd` | 多多进宝官方 |
| 淘宝 / 天猫 | `taobao` / `tmall` | 万邦聚合补充淘宝 |
| 1688 | `1688` | 后续接入 |
| Mock | `mock` | 本地开发测试 |

平台集合保持开放。后续可以继续加入抖音电商、小红书、得物、唯品会、苏宁等国内平台。

## 五、工具代码结构

`item_search` 支持单个平台搜索。多平台并发由主 AgentLoop 通过 `dispatch_tool` fork 多个同质子 AgentLoop 完成。

```python
# app/tools/item_search.py
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from typing import Literal


class Candidate(BaseModel):
    """单个候选商品的稳定结构（后续工具按这个 schema 消费）。"""
    item_id: str
    platform: str
    title: str
    price: float
    currency: str
    rating: float | None = None
    sales: int | None = None
    image_url: str | None = None
    attributes: dict = Field(default_factory=dict)  # 材质 / 风格等结构化属性


class ItemSearchOutput(BaseModel):
    platform: str
    candidates: list[Candidate]
    total_recall: int        # 召回总数（语义 + 个性化）
    truncated: bool          # 是否因为 top_k 截断


@tool
async def item_search(
    query: str,
    platform: Literal["jd", "pdd", "taobao"],
    top_k: int = 20,
    user_id: str | None = None,
) -> ItemSearchOutput:
    """在指定平台检索商品候选集。

    Args:
        query: 已经被 Planner 拆解过的具体词（例如 "旅行收纳袋 不要塑料 小众"）。
        platform: 目标平台。
        top_k: 返回候选数量，默认 20，最大 50。
        user_id: 可选，传入则启用个性化召回通道。

    Returns:
        platform / candidates / total_recall / truncated 四字段固定结构。
    """
    ...
```

## 六、三塔模型与 ItemSearch 的关系

三塔模型不是第三方接口，而是项目内部的个性化商品检索模块。它负责把“用户这次想搜什么”“平台商品是什么”“这个用户长期喜欢什么”都变成向量，然后用相似度把更合适的商品召回出来。

三塔分别是：

- 查询塔：把 Planner 拆出来的 `query` 转成向量，例如 `轻便不塑料收纳包`。
- 商品塔：把京东、拼多多、淘宝等平台商品的标题、材质、风格、价格等信息提前转成商品向量，存在向量库里。
- 用户塔：把 `user_id` 对应的长期偏好转成用户向量，例如不爱塑料、喜欢小众、预算 200 以内。

`item_search` 是对外工具入口，三塔模型是它底下的召回算法。

不传 `user_id` 时，只跑查询塔 + 商品塔：只看这次搜索词和商品是否相关，所有人搜同一个词，结果大体一致。

传 `user_id` 时，启用查询塔 + 商品塔 + 用户塔：既看这次搜什么，也看这个人长期偏好，排序会更个性化。

例如同样搜 `收纳袋`：

- 不传 `user_id`：召回大众收纳袋。
- 传入“不爱塑料”的用户 ID：帆布、硅胶、小众材质商品会排得更靠前，塑料商品靠后。

所以 `user_id` 在 `item_search` 里就是个性化开关：没有它就是通用检索，有它就是三塔个性化召回。

## 七、工具内部：三塔召回接入

### 6.1 三塔召回位置

```text
User 塔:  user_id -> user_emb
Query 塔: query   -> query_emb
Item 塔:  item    -> item_emb（离线灌入 Milvus）

语义通道:
query_emb 在 Milvus 中找 Top-K

个性化通道:
(user_emb + query_emb) 融合后在 Milvus 中找 Top-K

合并:
两个通道结果取并集 -> 去重 -> 重排
```

### 6.2 召回客户端抽象

`TowerClient` 负责调用 User 塔和 Query 塔接口，输出 embedding。

```python
# app/recall/towers.py
import os
import httpx


class TowerClient:
    def __init__(self) -> None:
        self.user_endpoint = os.environ["TOWER_USER_ENDPOINT"]
        self.query_endpoint = os.environ["TOWER_QUERY_ENDPOINT"]
        self.client = httpx.AsyncClient(timeout=5.0)

    async def encode_user(self, user_id: str) -> list[float]:
        r = await self.client.post(self.user_endpoint, json={"user_id": user_id})
        r.raise_for_status()
        return r.json()["embedding"]

    async def encode_query(self, query: str) -> list[float]:
        r = await self.client.post(self.query_endpoint, json={"query": query})
        r.raise_for_status()
        return r.json()["embedding"]


tower_client = TowerClient()
```

`MilvusRecallClient` 负责在 Milvus 商品向量库里做近邻检索，并按平台过滤。

```python
# app/recall/milvus.py
import os
from pymilvus import MilvusClient


class MilvusRecallClient:
    def __init__(self) -> None:
        self.client = MilvusClient(uri=os.environ["MILVUS_URI"])
        self.collection_name = os.environ["MILVUS_ITEM_COLLECTION"]

    def search(self, emb: list[float], top_k: int, platform: str) -> list[dict]:
        results = self.client.search(
            collection_name=self.collection_name,
            data=[emb],
            limit=top_k * 3,
            filter=f'platform == "{platform}"',
            output_fields=[
                "item_id",
                "platform",
                "title",
                "price",
                "currency",
                "rating",
                "sales",
                "image_url",
                "attributes",
            ],
        )

        items = []
        for hit in results[0]:
            entity = dict(hit.get("entity") or {})
            items.append({**entity, "score": float(hit.get("distance", 0.0))})
            if len(items) >= top_k:
                break
        return items


milvus_recall_client = MilvusRecallClient()
```

### 6.3 双通道召回和合并

```python
# app/tools/item_search.py
import asyncio

from app.recall.towers import tower_client
from app.recall.milvus import milvus_recall_client


async def _recall(
    query: str,
    platform: str,
    top_k: int,
    user_id: str | None,
) -> tuple[list[dict], int]:
    semantic_task = asyncio.create_task(
        _semantic_recall(query, platform, top_k)
    )

    personalized_task = (
        asyncio.create_task(_personalized_recall(query, platform, top_k, user_id))
        if user_id
        else None
    )

    semantic = await semantic_task
    personalized = await personalized_task if personalized_task else []

    merged = _dedupe_and_rerank(semantic, personalized)
    total_recall = len({item["item_id"] for item in semantic + personalized})
    return merged[:top_k], total_recall


async def _semantic_recall(query: str, platform: str, top_k: int) -> list[dict]:
    query_emb = await tower_client.encode_query(query)
    return milvus_recall_client.search(query_emb, top_k, platform)


async def _personalized_recall(
    query: str,
    platform: str,
    top_k: int,
    user_id: str,
) -> list[dict]:
    user_emb, query_emb = await asyncio.gather(
        tower_client.encode_user(user_id),
        tower_client.encode_query(query),
    )
    fused = [0.6 * u + 0.4 * q for u, q in zip(user_emb, query_emb)]
    return milvus_recall_client.search(fused, top_k, platform)


def _dedupe_and_rerank(semantic: list[dict], personalized: list[dict]) -> list[dict]:
    bag: dict[str, dict] = {}

    for item in semantic:
        bag[item["item_id"]] = {**item, "boost": item["score"]}

    for item in personalized:
        existing = bag.get(item["item_id"])
        if existing:
            existing["boost"] += 0.5 * item["score"]
        else:
            bag[item["item_id"]] = {**item, "boost": item["score"] * 0.8}

    return sorted(bag.values(), key=lambda item: item["boost"], reverse=True)
```

### 6.4 工具入口

```python
# app/tools/item_search.py
import time

from app.api.monitor import monitor


@tool
async def item_search(
    query: str,
    platform: Literal["jd", "pdd", "taobao"],
    top_k: int = 20,
    user_id: str | None = None,
) -> ItemSearchOutput:
    """在指定平台检索商品候选集。"""
    top_k = min(top_k, 50)
    await monitor.report_tool_start("item_search", {
        "query": query,
        "platform": platform,
        "top_k": top_k,
    })

    t0 = time.time()
    raw, total_recall = await _recall(query, platform, top_k, user_id)

    candidates = [
        Candidate(
            item_id=r["item_id"],
            platform=platform,
            title=r["title"],
            price=r["price"],
            currency=r["currency"],
            rating=r.get("rating"),
            sales=r.get("sales"),
            image_url=r.get("image_url"),
            attributes=r.get("attributes", {}),
        )
        for r in raw
    ]

    await monitor.report_tool_end("item_search", int((time.time() - t0) * 1000))
    return ItemSearchOutput(
        platform=platform,
        candidates=candidates,
        total_recall=total_recall,
        truncated=total_recall > top_k,
    )
```

### 6.5 整体流程

```text
Query 塔把 query 编码成 query_emb
User 塔把 user_id 编码成 user_emb（可选）
Item 塔提前把商品编码成 item_emb 并写入 Milvus

不传 user_id:
query_emb -> Milvus -> Top-K 商品

传 user_id:
query_emb -> Milvus -> 语义 Top-K
user_emb + query_emb -> Milvus -> 个性化 Top-K
两路结果 -> 去重 -> 重排 -> Candidate[]
```

## 八、设计取舍说明

### platform 使用 Literal 而非 str

限制固定枚举平台值，防止模型输出 Amazon / AMAZON / amzn 这类不规范字符串，统一入参格式，减少分支判断与兼容 bug。

### top_k 默认值 20

平衡两端：给到 ItemPicker 二次筛选有充足候选样本；同时不会一次性返回过长列表造成上下文 token 溢出。

### user_id 可选参数

无用户 ID 时走纯语义商品检索；传入用户 ID 则开启个性化召回（结合用户长期偏好），实现功能渐进增强。

### 返回值为 Pydantic 模型

LangChain 会自动将模型序列化为结构化文本供给 LLM 阅读；业务代码中可直接作为对象访问字段，兼顾模型理解与后端程序读写。

## 九、与其他工具的关系

```text
Planner
  -> 拆出预算 / 品类 / 配送偏好 / 包邮需求

dispatch_tool
  -> fork 多个同质子 AgentLoop
  -> 每个子 loop 调一个平台的 item_search

ItemSearch
  -> 返回各平台候选商品

主 AgentLoop
  -> 合流多个平台候选

PriceCompare
  -> 计算商品价 + 运费后的国内总价
  -> 做同款 / 近似款比价

ItemPicker
  -> 按材质、风格、品牌偏好、评价风险做二次筛选

ShoppingSummary
  -> 生成最终采购建议
```

## 十、建议文件落点

最小实现结构：

```text
app/tools/
└── item_search.py
```

第一版先实现 `mock`。真实平台按顺序接：

1. 京东联盟官方。
2. 多多进宝官方。
3. 万邦聚合补充淘宝。

## 十一、验收标准

第一版完成后至少满足：

1. `item_search(platform="jd" / "pdd" / "taobao", ...)` 能返回统一商品结构。
2. schema 能被 LangChain `StructuredTool` 使用。
3. 主 AgentLoop 可以通过 `dispatch_tool` fork 多个平台搜索任务。
4. 多个平台结果可以合流回 `state.context.candidate_items`。
5. 国内运费和包邮信息已在商品字段中体现。
6. 后续 `PriceCompare` 不需要再调用独立 `ShippingCalc`。
