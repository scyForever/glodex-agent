# ItemPicker 与 ShoppingSummary 工具设计

## 一、工具定位

`ItemPicker` 和 `ShoppingSummary` 是购物链路后半段的两个工具。

```text
ItemSearch
-> PriceCompare
-> ItemPicker
-> ShoppingSummary
```

`ItemPicker` 负责判断商品是否适合用户。

`ShoppingSummary` 负责把最终结果整理成推荐清单和理由。

## 二、ItemPicker

`ItemPicker` 负责根据多维条件筛选和重排商品。

它接收 `PriceCompare` 排好价格后的商品列表，再结合用户需求、用户长期偏好、品类洞察结果进行筛选。

### 2.1 主要判断维度

- 材质
- 风格
- 评分
- 销量
- 店铺
- 是否自营
- 用户长期偏好
- `CategoryInsight` 输出结果

### 2.2 和 CategoryInsight 的关系

`CategoryInsight` 给 `ItemPicker` 提供品类常识。

例如：

- `components`：判断套装类商品有没有缺组件。
- `attributes`：判断候选属性是否符合品类主流。
- `price_tiers`：判断候选价格是否落在合理档位。
- `confidence`：判断品类洞察是否可信。

### 2.3 ItemPicker 不做的事

`ItemPicker` 不负责：

- 搜商品，这属于 `ItemSearch`。
- 算最终价格，这属于 `PriceCompare`。
- 生成最终推荐话术，这属于 `ShoppingSummary`。

## 三、ShoppingSummary

`ShoppingSummary` 负责把筛选后的商品结果生成最终推荐清单和理由。

它接收 `ItemPicker` 输出的最终候选商品，生成用户可读的购买建议。

### 3.1 输出内容

`ShoppingSummary` 输出：

- 推荐商品清单
- 每个商品的推荐理由
- 价格和平台信息
- 关键优缺点
- 不推荐商品的简要原因
- 最终购买建议

### 3.2 ShoppingSummary 不做的事

`ShoppingSummary` 不负责：

- 重新搜索商品。
- 重新计算价格。
- 重新筛选商品。

它只负责最终表达和总结。

## 四、完整链路

```text
用户需求
-> Planner 拆解预算 / 品类 / 偏好
-> CategoryInsight 获取品类常识
-> ItemSearch 搜多平台商品
-> PriceCompare 计算最终到手价并排序
-> ItemPicker 按用户偏好和品类规则筛选
-> ShoppingSummary 生成最终推荐清单
```
