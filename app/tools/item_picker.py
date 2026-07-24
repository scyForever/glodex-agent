from __future__ import annotations

import re
import time
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.api.monitor import monitor


class CandidateItem(BaseModel):
    """国内电商候选商品。

    字段精简为 API 真实可获取的 11 个字段 + attributes 多模态提取。
    """

    item_id: str
    platform: str
    title: str
    price_cny: float | None = None
    coupon_cny: float | None = None
    final_price_cny: float | None = None
    shop_name: str | None = None
    sales: int | None = None
    image_url: str | None = None
    url: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class PickedItem(BaseModel):
    """ItemPicker 精选后的商品。"""

    item_id: str
    platform: str
    title: str
    price_cny: float | None = None
    score: float
    reasons: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class ItemPickerOutput(BaseModel):
    """ItemPicker 的结构化输出。"""

    picks: list[PickedItem]
    rejected_brief: list[str] = Field(default_factory=list)


@tool
async def item_picker(
    candidates: list[CandidateItem],
    insight: dict[str, Any] | None = None,
    user_preferences: list[str] | None = None,
    top_n: int = 3,
    max_budget_cny: float | None = None,
) -> ItemPickerOutput:
    """从国内电商候选商品中精选 1-3 件最适合用户的商品。

    Args:
        candidates: 已经由 ItemSearch 标准化后的候选商品。
        insight: 可选品类洞察，例如合理价格区间、避坑材质、推荐标签等。
        user_preferences: 用户本轮或长期偏好，例如"不要塑料""偏好小众""白色"。
        top_n: 最多返回的精选商品数量，默认 3。
        max_budget_cny: 可选预算上限，超过预算的商品会被硬性排除。

    Returns:
        picks: 精选商品列表。
        rejected_brief: 被排除商品的简短原因，最多保留 8 条。
    """
    await monitor.report_tool_start(
        "item_picker",
        {
            "candidate_count": len(candidates),
            "top_n": top_n,
            "max_budget_cny": max_budget_cny,
            "preferences": user_preferences or [],
        },
    )
    start = time.time()

    top_n = max(1, min(top_n, 3))
    preferences = user_preferences or []
    rejected: list[str] = []
    picked_candidates: list[PickedItem] = []

    for item in candidates:
        hard_fail, flags = _check_hard_constraints(
            item=item,
            preferences=preferences,
            max_budget_cny=max_budget_cny,
        )
        if hard_fail:
            rejected.append(f"{item.item_id}: {hard_fail}")
            continue

        score, reasons, score_flags = _score_item(
            item=item,
            insight=insight or {},
            preferences=preferences,
            max_budget_cny=max_budget_cny,
        )
        picked_candidates.append(
            PickedItem(
                item_id=item.item_id,
                platform=item.platform,
                title=item.title,
                price_cny=item.price_cny,
                score=score,
                reasons=reasons[:3],
                flags=(flags + score_flags)[:5],
            )
        )

    picked_candidates.sort(key=lambda item: item.score, reverse=True)
    output = ItemPickerOutput(
        picks=picked_candidates[:top_n],
        rejected_brief=rejected[:8],
    )

    await monitor.report_tool_end(
        "item_picker",
        int((time.time() - start) * 1000),
    )
    return output


def _check_hard_constraints(
    item: CandidateItem,
    preferences: list[str],
    max_budget_cny: float | None,
) -> tuple[str | None, list[str]]:
    """检查硬约束；明确违反才拒绝，字段缺失只加风险标记。"""
    flags: list[str] = []
    pref_text = _join_text(preferences)
    item_text = _item_search_text(item)

    # ── 预算硬约束 ──
    if max_budget_cny is not None and item.price_cny is not None:
        if item.price_cny > max_budget_cny:
            return f"价格 {item.price_cny} 元超过预算 {max_budget_cny} 元", flags

    # ── 材质约束（从 attributes 或标题判断） ──
    if "不要塑料" in pref_text or "非塑料" in pref_text:
        if _contains_any(item_text, ["塑料", "pp", "pe", "abs", "pvc"]):
            return "命中用户硬约束：不要塑料", flags
        if not item.attributes:
            flags.append("材质信息未知，需二次确认")

    # ── 预售约束 ──
    if "不要预售" in pref_text or "现货" in pref_text:
        if _contains_any(item_text, ["预售", "预定", "定金", "尾款"]):
            return "命中用户硬约束：不要预售", flags

    return None, flags


def _score_item(
    item: CandidateItem,
    insight: dict[str, Any],
    preferences: list[str],
    max_budget_cny: float | None,
) -> tuple[float, list[str], list[str]]:
    """按国内电商常见维度做轻量综合打分。

    只依赖实际可获取的字段：价格、销量、attributes、标题。
    """
    score = 0.0
    reasons: list[str] = []
    flags: list[str] = []
    pref_text = _join_text(preferences)
    item_text = _item_search_text(item)
    attr_text = _join_text(_attr_values(item))

    # ── 价格评分 ──
    price_score, price_reason, price_flag = _score_price(item, insight, max_budget_cny)
    score += price_score
    if price_reason:
        reasons.append(price_reason)
    if price_flag:
        flags.append(price_flag)

    # ── 券后价优势（有券 = 更划算） ──
    if item.coupon_cny is not None and item.coupon_cny > 0:
        score += 0.12
        reasons.append(f"可领 {int(item.coupon_cny)} 元优惠券")
    if item.final_price_cny is not None and item.price_cny is not None:
        if item.final_price_cny < item.price_cny * 0.8:
            score += 0.10
            reasons.append("券后价有明显优势")

    # ── 销量评分 ──
    if item.sales is not None:
        if "小众" in pref_text and item.sales > 10000:
            flags.append("销量很高，可能偏爆款")
        elif item.sales >= 10000:
            score += 0.15
            reasons.append("销量高")
        elif item.sales >= 1000:
            score += 0.10
            reasons.append("有一定销量基础")
        elif item.sales < 20:
            flags.append("销量较少，需关注")

    # ── 店铺评分（从 shop_name 推测，不再依赖 shop_type） ──
    shop_lower = (item.shop_name or "").lower()
    if _contains_any(shop_lower, ["自营", "旗舰", "官方"]):
        score += 0.15
        reasons.append("店铺可信度较高")
    elif _contains_any(shop_lower, ["专卖", "专营", "品牌"]):
        score += 0.08
        reasons.append("品牌授权店铺")

    # ── attributes 匹配用户偏好 ──
    # 颜色偏好
    for color_pref in ["白色", "黑色", "灰色", "蓝色", "红色", "粉色", "绿色", "银色"]:
        if color_pref in pref_text and color_pref in attr_text:
            score += 0.10
            reasons.append(f"颜色匹配偏好：{color_pref}")
            break

    # 材质偏好
    if "不要塑料" in pref_text or "非塑料" in pref_text:
        if not _contains_any(attr_text, ["塑料", "pp", "pe"]):
            score += 0.08
            reasons.append("材质符合非塑料偏好")

    # 简约风格
    if "简约" in pref_text and _contains_any(item_text, ["简约", "极简", "素色", "纯色"]):
        score += 0.08
        reasons.append("风格匹配简约偏好")

    # 耐用偏好
    if "耐用" in pref_text and _contains_any(item_text, ["耐用", "加厚", "高强度", "加固"]):
        score += 0.08
        reasons.append("耐用性描述匹配")

    # ── insight 推荐/避坑（从 attributes 匹配） ──
    avoid_keywords = insight.get("avoid_keywords") or insight.get("avoid_tags") or []
    if avoid_keywords and _contains_any(attr_text, [str(w) for w in avoid_keywords]):
        flags.append("命中品类避坑关键词")
        score -= 0.15

    recommend_keywords = insight.get("recommend_keywords") or insight.get("recommend_tags") or []
    if recommend_keywords and _contains_any(attr_text, [str(w) for w in recommend_keywords]):
        score += 0.12
        reasons.append("匹配品类推荐特征")

    if not reasons:
        reasons.append("基础信息可用，未发现明显硬伤")

    return round(score, 2), reasons, flags


def _score_price(
    item: CandidateItem,
    insight: dict[str, Any],
    max_budget_cny: float | None,
) -> tuple[float, str | None, str | None]:
    """价格打分：有预算看预算，无预算时参考品类洞察价格区间。"""
    if item.price_cny is None:
        return 0.0, None, "价格未知"

    if max_budget_cny is not None:
        ratio = item.price_cny / max_budget_cny
        if ratio <= 0.85:
            return 0.25, "价格低于预算", None
        return 0.18, "价格接近预算上限但仍可接受", None

    price_range = _extract_price_range(insight)
    if price_range is None:
        return 0.08, "价格信息明确", None

    low, high = price_range
    if low <= item.price_cny <= high:
        return 0.25, "价格落在品类合理区间", None
    if item.price_cny < low:
        return 0.08, "价格偏低，需注意质量风险", "价格明显低于品类常见区间"
    return 0.02, "价格偏高", "价格高于品类常见区间"


def _extract_price_range(insight: dict[str, Any]) -> tuple[float, float] | None:
    """兼容不同形态的品类洞察价格区间。"""
    price_range = insight.get("reasonable_price_cny") or insight.get("price_range_cny")
    if isinstance(price_range, (list, tuple)) and len(price_range) >= 2:
        return float(price_range[0]), float(price_range[1])

    price_tiers = insight.get("price_tiers")
    if isinstance(price_tiers, list):
        for tier in price_tiers:
            if not isinstance(tier, dict):
                continue
            tier_name = str(tier.get("tier", ""))
            tier_range = tier.get("range_cny")
            if "中" in tier_name and isinstance(tier_range, (list, tuple)) and len(tier_range) >= 2:
                return float(tier_range[0]), float(tier_range[1])
    return None


def _item_search_text(item: CandidateItem) -> str:
    """把标题和 attributes 拼成检索文本，便于做轻量规则判断。"""
    return _join_text([item.title, *_attr_values(item)])


def _attr_values(item: CandidateItem) -> list[str]:
    """提取 attributes 的 key/value，扁平化为可检索文本。"""
    parts: list[str] = []
    for key, value in item.attributes.items():
        if value in (None, ""):
            continue
        parts.append(str(value))
        parts.append(f"{key}:{value}")
    return parts


def _join_text(parts: list[str]) -> str:
    return " ".join(part.lower() for part in parts if part)


def _contains_any(text: str, keywords: list[str]) -> bool:
    normalized = text.lower()
    return any(re.search(re.escape(keyword.lower()), normalized) for keyword in keywords)
