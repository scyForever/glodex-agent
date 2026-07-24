# Spider 采集与多模态 Attributes 方案

本文档说明当前 `spider/spider.py` 如何作为商品数据采集入口，并如何结合图片和标题生成动态 `attributes` 字段，最终服务 `ItemSearch`、Milvus 向量召回和 `ItemPicker`。

## 一句话

`spider.py` 负责从麦手接口拿商品固定字段、主图、详情图和购买链接；多模态模型负责把标题和图片转成动态 `attributes`；入库层把固定字段和 `attributes` 一起写入 JSONL / Milvus。

```text
spider search
  -> 商品固定字段 + 主图
spider detail
  -> 购买链接 + banner 多图 + 类目/tag/shop
多模态模型
  -> 根据 title + shopName + category + image_urls 生成 attributes
normalizer
  -> 统一成 NormalizedProduct
writer / milvus writer
  -> 写入本地 JSONL 或 Milvus
ItemSearch
  -> title 关键词检索 + embedding 向量检索
ItemPicker
  -> attributes 辅助硬约束、偏好打分和推荐解释
```

## 当前 Spider 能拿到什么

`spider/spider.py search --keyword "白色内存条" --source 2` 当前会调用麦手 `searchList` 接口，并返回 CSV 形态的商品列表。

搜索结果字段：

| 字段 | 含义 | 是否可直接入库 |
|---|---|---|
| `goodsId` | 平台商品 ID / 麦手商品 ID | 是，映射 `item_id` |
| `source` | 平台来源，`1=淘宝`、`2=京东`、`3=拼多多` | 是，映射 `platform` |
| `title` | 商品标题 | 是 |
| `shopName` | 店铺名 | 是 |
| `originalPrice` | 原价 | 是，映射 `price_cny` |
| `actualPrice` | 券后价 / 当前到手价 | 是，映射 `final_price_cny` |
| `couponPrice` | 优惠券金额 | 是，映射 `coupon_cny` |
| `commission` | 佣金 | 不入库，当前不作为核心字段 |
| `monthSales` | 月销量 | 是，映射 `sales` |
| `picUrl` | 主图 | 是，映射 `image_url` |

`spider/spider.py detail --id ... --source ...` 当前会调用商品详情和转链接口。

详情结果里有这些关键字段：

| 字段 | 含义 | 用途 |
|---|---|---|
| `购买链接` | 转链后的购买 URL | 映射 `url` |
| `商品详情.picUrl` | 主图 | 多模态输入 + `image_url` |
| `商品详情.goodsBannerList` | 多张商品 banner 图 | 多模态输入 |
| `商品详情.tagList` | 平台标签，如京东物流、7天无理由 | 生成 `attributes` |
| `商品详情.levelOneCategoryName` | 一级类目 | 生成 `attributes.category` |
| `商品详情.shopName` | 店铺名 | 固定字段 + 多模态上下文 |
| `商品详情.originalPrice/actualPrice/couponPrice` | 价格字段 | 固定字段 |
| `商品详情.monthSales/salesStr` | 销量字段 | 固定字段 |

## 为什么这种方式可行

当前 `NormalizedProduct` 已经按“固定字段 + 动态 attributes”设计：

```python
class NormalizedProduct(BaseModel):
    item_id: str
    platform: Platform
    title: str
    price_cny: float | None = None
    coupon_cny: float | None = None
    final_price_cny: float | None = None
    shop_name: str | None = None
    sales: int | None = None
    image_url: str | None = None
    url: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
```

麦手搜索和详情接口已经能覆盖这些固定字段。`attributes` 不要求固定 schema，因此很适合由多模态模型从标题和图片中补充。

例如“白色内存条”商品可以生成：

```json
{
  "品类": "台式机内存条",
  "品牌": "KingSpec 金胜维",
  "颜色": "白色",
  "内存类型": "DDR5",
  "容量": "16GB",
  "频率": "5600MHz",
  "外观": "白色马甲条",
  "适用设备": "台式机"
}
```

这些字段会进入 `embedding_text()`：

```text
title + shop_name + attributes(key:value)
```

因此 `attributes` 不只是展示字段，也会影响 embedding 向量召回和本地关键词回退。

同时，`title` 会作为独立字段保留，用于关键词检索。最终检索采用混合方式：

```text
title 关键词检索 + embedding 向量检索 -> 按 item_id 去重合并 -> 双路命中加权
```

## 推荐的数据流

第一版可以分三步跑通。

### 1. 搜索阶段

输入：

```text
keyword = 白色内存条
source = 1 / 2 / 3
```

输出：

```text
list[maishou_search_item]
```

保留搜索结果里的固定字段，不保存完整原始返回。

### 2. 详情补全阶段

对 Top-N 搜索结果调用 `detail(goodsId, source)`。

补齐：

- `url`
- `goodsBannerList`
- `tagList`
- `levelOneCategoryName`
- 更完整的价格、店铺、销量字段

建议不要所有搜索结果都拉 detail，先对每个平台 Top 20 做详情补全即可，避免延迟和接口压力过大。

### 3. Attributes 生成阶段

多模态模型输入建议：

```json
{
  "title": "商品标题",
  "shop_name": "店铺名",
  "category": "一级类目",
  "tags": ["京东物流", "7天无理由退货"],
  "image_urls": [
    "主图",
    "banner 图 1",
    "banner 图 2"
  ]
}
```

输出要求：

- 返回 JSON object。
- key 可以动态，但应尽量使用中文业务名。
- 不确定的信息不要编造。
- 优先抽取会影响检索和筛选的属性。

推荐优先抽取：

| 属性 | 示例 |
|---|---|
| `品类` | 台式机内存条 |
| `品牌` | 金胜维 |
| `颜色` | 白色 |
| `材质` | 金属马甲 |
| `容量` | 16GB |
| `规格` | DDR5 / 5600MHz |
| `适用设备` | 台式机 |
| `外观` | 白色马甲条 |
| `是否RGB` | 否 |
| `平台标签` | 京东物流、7天无理由 |

## Spider 代码建议

当前 `search()` 返回 CSV 字符串，适合 CLI 展示，但不适合入库流水线。建议拆成两层：

```text
search_items(keyword, source, page) -> list[dict]
format_search_csv(items) -> str
```

`detail()` 也建议拆成：

```text
fetch_detail(goods_id, source) -> dict
format_detail_yaml(detail) -> str
```

这样 CLI 仍然可以打印 CSV/YAML，而 ingest 层可以直接拿结构化 dict，不需要再反向解析字符串。

## 入库字段建议

写 JSONL / Milvus 时建议保留：

| 字段 | 来源 |
|---|---|
| `item_id` | `goodsId` |
| `platform` | `sourceType` 标准化 |
| `title` | search/detail |
| `price_cny` | `originalPrice` |
| `coupon_cny` | `couponPrice` |
| `final_price_cny` | `actualPrice` |
| `shop_name` | `shopName` |
| `sales` | `monthSales` / `salesStr` |
| `image_url` | `picUrl` |
| `url` | 转链接口返回的购买链接 |
| `attributes_json` | 多模态模型生成的动态属性 |
| `embedding_text` | `title + shop_name + attributes(key:value)` |
| `embedding` | 由 `embedding_text` 生成的向量 |

不保存 `raw` 原始返回。麦手 search/detail 的完整响应只在调试日志或临时文件中保留，正式入库只保留上表字段。

Milvus 中把 `attributes` 存成 `attributes_json`，同时把 `brand` / `category` / `material` / `color` 等高频属性后续拆成标量字段，用于过滤和重排。

## 混合检索策略

入库时同时保存 `title` 和 `embedding`：

| 字段 | 中文名 | 用途 |
|---|---|---|
| `title` | 商品标题 | 关键词检索，适合匹配 DDR5、16G、白色、品牌名等精确词 |
| `embedding_text` | 向量化文本 | 生成商品向量的原文，方便排查 |
| `embedding` | 商品向量 | Milvus ANN 语义召回 |

查询时走两路：

```text
1. title 关键词检索
   query 与 title 做 contains / 后续 BM25 匹配

2. embedding 向量检索
   query -> query_embedding -> Milvus ANN

3. 合并重排
   item_id 去重
   title 和 embedding 双路命中的商品加分
```

第一版可以先实现 `title contains + embedding ANN`，后续再升级到 Milvus sparse/BM25。

## 注意点

- 搜索接口会有误召回，例如淘宝搜索“白色内存条”可能混入主板、整机或无关商品，需要后续用 `attributes.品类` 和 `ItemPicker` 剔除。
- `attributes` 可以动态，但关键属性最好逐步标准化，例如品牌、类目、颜色、材质、容量。
- 多模态模型只应基于标题、类目、标签和图片判断；图片看不出来的信息不要硬填。
- 详情接口有网络和限流风险，建议异步并发但限制并发数。
- `goodsBannerList` 对多模态很有价值，优先使用前 3-5 张即可。

## 当前结论

该方案可行。`spider.py` 已经能拿到固定字段、主图、详情多图和购买链接；`NormalizedProduct` 已经支持动态 `attributes`；`ItemSearch` 和 `ItemPicker` 已经会使用 `attributes` 的 value 和 `key:value` 文本。

下一步实现重点不是改字段模型，而是把 `spider.py` 从 CLI 打印工具拆成可复用采集模块，并补一个多模态 `extract_attributes()` 步骤。
