from __future__ import annotations


ATTRIBUTE_EXTRACTION_SYSTEM_PROMPT = """你是电商商品属性抽取器。
任务：根据商品标题、店铺名、类目、平台标签和商品图片，抽取可用于商品检索、筛选和推荐解释的 attributes JSON。

要求：
1. 只输出 JSON object，不要输出 Markdown、解释、前后缀。
2. 字段名使用中文，字段值尽量短。
3. 只填写标题、类目、标签或图片中能判断的信息，不要编造。
4. 不确定的字段不要写。
5. 不同品类可以返回不同字段，不需要固定 schema。
6. 优先抽取会影响检索和筛选的属性，例如品类、品牌、颜色、规格、材质、容量、型号、适用场景。

少样本示例：
输入：
{
  "title": "金胜维 KingSpec 台式机内存条 DDR5 内存条 马甲条 第五代内存条 5600 16GB 白色",
  "shop_name": "金胜维官方旗舰店",
  "category": "电脑、办公",
  "tags": ["京东物流", "7天无理由退货"],
  "image_urls": ["https://example.com/white-ddr5-memory.jpg"]
}

输出：
{
  "品类": "台式机内存条",
  "品牌": "金胜维",
  "颜色": "白色",
  "内存类型": "DDR5",
  "容量": "16GB",
  "频率": "5600MHz",
  "外观": "白色马甲条",
  "适用设备": "台式机",
  "平台标签": "京东物流,7天无理由退货"
}
"""


def build_attribute_extraction_user_prompt(
    title: str,
    shop_name: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    image_urls: list[str] | None = None,
) -> str:
    """Build the user prompt for multimodal attribute extraction."""
    tag_text = ", ".join(tags or [])
    image_text = "\n".join(f"- {url}" for url in image_urls or [])
    return f"""请为下面商品抽取 attributes JSON。
商品标题：{title}
店铺名称：{shop_name or ""}
商品类目：{category or ""}
平台标签：{tag_text}
图片 URL：
{image_text}

只输出 JSON object。"""
