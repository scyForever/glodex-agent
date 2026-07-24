# ItemSearch 当前实现

> 本文档描述 `item_search` 工具的实际运行方式，代码在 `app/tools/item_search.py`。

## 一句话

**向量召回优先 + 本地 JSONL 回退**。如果配置了 Tower 编码服务和 ANN 后端，优先走 Query 塔 / User 塔 / Milvus 或 Faiss；否则自动回退到本地 JSONL 关键词匹配。

## 架构概览

```text
用户请求
  │
  ▼
item_search(query="白色 DDR5 32G 内存条", platform="jd", top_k=20)
  │  LangChain @tool，供 AgentLoop 调用
  ▼
_search_vector_index()
  │  1. Query 塔把 query 编码为 query_emb
  │  2. ANN 后端按 query_emb 做语义召回
  │  3. user_id 存在且配置 User 塔时，融合 user_emb + query_emb 做个性化召回
  │  4. 两路结果按 item_id 去重、加权重排
  │
  └─ 不可用时回退 _search_local_index()
      1. 加载 JSONL 文件到内存
      2. 按 platform 过滤
      3. 逐条关键词打分
      4. 按分数降序取 top_k
  ▼
返回 ItemSearchOutput → 交 ItemPicker 二次筛选
```

## 调用入口

```python
@tool
async def item_search(
    query: str,                    # 检索词，Planner 拆解后的用户意图
    platform: Platform = "all",    # "all" | "jd" | "taobao" | "pdd"
    top_k: int = 20,               # 1~50
    user_id: str | None = None,    # 预留，暂未使用
) -> ItemSearchOutput
```

## 在线向量召回

向量召回由 `app/recall` 下的轻客户端提供：

| 文件 | 作用 |
|---|---|
| `app/recall/tower_query.py` | 调 `TOWER_QUERY_ENDPOINT`，把 query 编成向量 |
| `app/recall/tower_user.py` | 调 `TOWER_USER_ENDPOINT`，把 user_id 编成向量 |
| `app/recall/ann.py` | 按 `ANN_BACKEND` 调 Milvus 或 Faiss 做 ANN 检索 |

召回策略：

```text
语义通道：query_emb -> ANN Top-K
个性化通道：(0.6 * user_emb + 0.4 * query_emb) -> ANN Top-K
合并：item_id 去重，双通道命中加权，按 boost 降序
```

缺少 tower endpoint、ANN 依赖、Milvus/Faiss 不可用时，不会中断工具调用，会自动回退本地索引。

## 本地回退数据源

本地回退从环境变量 `ITEM_SEARCH_INDEX_PATH` 指向的文件加载。支持三种格式：

| 格式 | 结构 |
|---|---|
| `.jsonl` | 每行一个 JSON 对象 |
| `.json` (数组) | `[ {...}, {...} ]` |
| `.json` (对象) | `{"items": [...]}` 或 `{"products": [...]}` |

加载结果用 `@lru_cache(maxsize=1)` 缓存，同一进程内只读一次。

## 字段映射

`_to_candidate_item()` 负责把 JSONL 里的原始字段转为统一的 `CandidateItem`。原始数据有两套字段名（麦手 API 格式 + 旧格式），都兼容：

| CandidateItem 字段 | 优先取的 key |
|---|---|
| `item_id` | `goodsId` → `item_id` → `id` → `goods_id` |
| `platform` | `platform` |
| `title` | `title` → `name` |
| `price_cny` | `originalPrice` → `price_cny` → `price` |
| `coupon_cny` | `couponPrice` → `coupon_cny` |
| `final_price_cny` | `actualPrice` → `final_price_cny` |
| `shop_name` | `shopName` → `shop_name` |
| `sales` | `monthSales` → `sales` |
| `image_url` | `picUrl` → `image_url` |
| `url` | `url` |
| `attributes` | `attributes`，必须是 JSON 对象 |

## 关键词打分

`_keyword_score()` 是本地回退的临时相关性打分。

```python
def _keyword_score(query, candidate):
    query_terms = _split_terms(query)          # 分词: ["白色", "ddr5", "32g", "内存条"]

    searchable_text = candidate.title
                    + " " + candidate.shop_name
                    + " " + attributes 的 value 和 key:value 拼起来

    score = 每个 query_term 是否在 searchable_text 中出现（每命中一个 +1）
          + min(sales / 10000, 0.2)           # 销量加分

    return score
```

- 每个 query 分词命中 title / shop_name / attributes 的 value 或 key:value +1 分
- 销量每 10000 件最多额外加 0.2 分
- 0 分的候选直接丢弃

## 输出

```python
class ItemSearchOutput(BaseModel):
    platform: str              # 平台
    query: str                 # 原样返回检索词
    candidates: list[CandidateItem]  # 候选商品
    total_recall: int = 0      # 总命中数（≥0 分的候选）
    truncated: bool = False    # 是否因 top_k 截断
    backend: str = "local_index"  # "vector_ann" 或 "local_index"
    notice: str | None = None  # 提示信息（索引为空等）
```

## 数据流

```text
┌─────────────────────────────────────────────────────────────┐
│  数据采集（离线）                                             │
│                                                             │
│  麦手 search API  ──→  normalizer 标准化  ──→  JSONL 文件     │
│  麦手 detail API  ──→  url 补全        ──→  同一 JSONL       │
│  多模态模型       ──→  attributes 提取  ──→  同一 JSONL       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  在线检索（当前）                                             │
│                                                             │
│  AgentLoop 调 item_search                                    │
│    → 优先 _search_vector_index()                             │
│    → Query 塔 / User 塔 / ANN 双通道召回                      │
│    → 不可用时 _load_local_products() 读 JSONL (lru_cache)     │
│    → platform 过滤 + keyword_score 打分                       │
│    → 返回 top_k CandidateItem                                │
│    → 交 ItemPicker 精选                                      │
└─────────────────────────────────────────────────────────────┘
```

## 待完善

- Milvus collection schema 和写入链路仍需补齐。
- `attributes` 当前保持动态 JSON；高频属性如 `brand` / `material` / `color` / `category` 后续可拆成标量字段，用于过滤和重排。
- 本地 JSONL 继续作为开发环境和向量服务不可用时的兜底路径。
