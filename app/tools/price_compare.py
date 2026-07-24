from __future__ import annotations

import re
import statistics
import time
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.api.monitor import monitor
from app.tools.item_picker import CandidateItem


class PriceOffer(BaseModel):
    """A normalized price comparison row for one candidate item."""

    item_id: str
    platform: str
    title: str
    shop_name: str | None = None
    price_cny: float | None = None
    coupon_cny: float | None = None
    final_price_cny: float | None = None
    effective_price_cny: float | None = None
    discount_rate: float | None = None
    unit_label: str | None = None
    unit_price_cny: float | None = None
    rank: int
    flags: list[str] = Field(default_factory=list)


class PriceCompareOutput(BaseModel):
    """Structured output for price comparison."""

    offers: list[PriceOffer]
    cheapest: PriceOffer | None = None
    median_price_cny: float | None = None
    price_spread_cny: float | None = None
    summary: str


@tool
async def price_compare(
    candidates: list[CandidateItem],
    mode: Literal["effective_price", "unit_price"] = "effective_price",
    top_n: int = 10,
) -> PriceCompareOutput:
    """Compare prices among already recalled domestic ecommerce candidates.

    This tool does not call external price APIs. It compares the candidates
    returned by ItemSearch using available fields: original price, coupon,
    final price, title, shop name, sales, and attributes.
    """
    await monitor.report_tool_start(
        "price_compare",
        {
            "candidate_count": len(candidates),
            "mode": mode,
            "top_n": top_n,
        },
    )
    start = time.time()

    top_n = max(1, min(top_n, 50))
    offers = [_to_offer(item) for item in candidates]
    priced = [offer for offer in offers if offer.effective_price_cny is not None]

    if mode == "unit_price":
        priced.sort(
            key=lambda offer: (
                offer.unit_price_cny is None,
                offer.unit_price_cny if offer.unit_price_cny is not None else float("inf"),
                offer.effective_price_cny if offer.effective_price_cny is not None else float("inf"),
            )
        )
    else:
        priced.sort(key=lambda offer: offer.effective_price_cny or float("inf"))

    ranked: list[PriceOffer] = []
    for idx, offer in enumerate(priced[:top_n], start=1):
        ranked_offer = offer.model_copy(update={"rank": idx})
        ranked.append(ranked_offer)

    _mark_outliers(ranked)
    prices = [offer.effective_price_cny for offer in ranked if offer.effective_price_cny is not None]
    median_price = round(statistics.median(prices), 2) if prices else None
    spread = round(max(prices) - min(prices), 2) if len(prices) >= 2 else None
    cheapest = ranked[0] if ranked else None

    output = PriceCompareOutput(
        offers=ranked,
        cheapest=cheapest,
        median_price_cny=median_price,
        price_spread_cny=spread,
        summary=_build_summary(ranked, median_price, spread, mode),
    )

    await monitor.report_tool_end("price_compare", int((time.time() - start) * 1000))
    return output


def _to_offer(item: CandidateItem) -> PriceOffer:
    effective_price = _effective_price(item)
    unit_label, unit_size = _extract_unit(item)
    unit_price = None
    if effective_price is not None and unit_size:
        unit_price = round(effective_price / unit_size, 2)

    discount_rate = None
    if item.price_cny and effective_price is not None and item.price_cny > 0:
        discount_rate = round(1 - effective_price / item.price_cny, 4)

    flags: list[str] = []
    if item.final_price_cny is None and item.price_cny is not None:
        flags.append("缺少券后价，使用原价估算")
    if item.price_cny is None and item.final_price_cny is None:
        flags.append("缺少价格")
    if item.coupon_cny and item.coupon_cny > 0:
        flags.append("有优惠券")
    if item.sales is not None and item.sales < 20:
        flags.append("销量较低，低价需谨慎")

    return PriceOffer(
        item_id=item.item_id,
        platform=item.platform,
        title=item.title,
        shop_name=item.shop_name,
        price_cny=item.price_cny,
        coupon_cny=item.coupon_cny,
        final_price_cny=item.final_price_cny,
        effective_price_cny=effective_price,
        discount_rate=discount_rate,
        unit_label=unit_label,
        unit_price_cny=unit_price,
        rank=0,
        flags=flags,
    )


def _effective_price(item: CandidateItem) -> float | None:
    if item.final_price_cny is not None:
        return round(float(item.final_price_cny), 2)
    if item.price_cny is not None and item.coupon_cny is not None:
        return round(max(float(item.price_cny) - float(item.coupon_cny), 0.0), 2)
    if item.price_cny is not None:
        return round(float(item.price_cny), 2)
    return None


def _extract_unit(item: CandidateItem) -> tuple[str | None, float | None]:
    text = " ".join([item.title, *_attribute_parts(item.attributes)])

    memory_match = re.search(r"(\d+(?:\.\d+)?)\s*(tb|gb|g)\b", text, flags=re.IGNORECASE)
    if memory_match:
        size = float(memory_match.group(1))
        unit = memory_match.group(2).lower()
        if unit == "tb":
            size *= 1024
        return "每GB", size

    pack_match = re.search(r"(\d+)\s*(个|只|件|片|支|条|袋|包|盒)", text)
    if pack_match and int(pack_match.group(1)) > 1:
        return f"每{pack_match.group(2)}", float(pack_match.group(1))

    return None, None


def _attribute_parts(attributes: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for key, value in attributes.items():
        if value in (None, ""):
            continue
        parts.append(str(value))
        parts.append(f"{key}:{value}")
    return parts


def _mark_outliers(offers: list[PriceOffer]) -> None:
    prices = [offer.effective_price_cny for offer in offers if offer.effective_price_cny is not None]
    if len(prices) < 3:
        return
    median_price = statistics.median(prices)
    for offer in offers:
        price = offer.effective_price_cny
        if price is None:
            continue
        if price < median_price * 0.6:
            offer.flags.append("价格显著低于中位数，需确认规格/店铺")
        elif price > median_price * 1.6:
            offer.flags.append("价格显著高于中位数")


def _build_summary(
    offers: list[PriceOffer],
    median_price: float | None,
    spread: float | None,
    mode: str,
) -> str:
    if not offers:
        return "没有可比较的价格数据。"

    cheapest = offers[0]
    price_text = (
        f"{cheapest.effective_price_cny:.2f} 元"
        if cheapest.effective_price_cny is not None
        else "价格未知"
    )
    basis = "单价" if mode == "unit_price" else "到手价"
    parts = [f"按{basis}比较，最低的是 {cheapest.platform} 的「{cheapest.title}」，价格 {price_text}。"]
    if median_price is not None:
        parts.append(f"本批候选中位价约 {median_price:.2f} 元。")
    if spread is not None:
        parts.append(f"最高与最低价差约 {spread:.2f} 元。")
    return "".join(parts)
