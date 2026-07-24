from __future__ import annotations

import re
from typing import Any

from app.ingest.schemas import NormalizedProduct, Platform


def normalize_product(raw: dict[str, Any], platform: str = "") -> NormalizedProduct:
    """Normalize a Maishou search/detail row into the internal product shape."""
    normalized_platform = _normalize_platform(platform, raw)
    price = _first_float(raw, ["originalPrice", "price_cny", "price", "zkFinalPrice"])
    coupon = _first_float(raw, ["couponPrice", "coupon_cny", "couponAmount"])
    final_price = _first_float(raw, ["actualPrice", "final_price_cny", "finalPrice"])
    if final_price is None:
        final_price = price - coupon if price is not None and coupon is not None else price

    image_url = _first_str(
        raw,
        [
            "picUrl",
            "image_url",
            "pict_url",
            "mainPic",
            "whiteImage",
            "cover",
            "goodsThumbnailUrl",
        ],
    )
    url = _first_str(raw, ["url", "item_url", "materialUrl", "appUrl", "schemaUrl"])

    return NormalizedProduct(
        item_id=str(_first(raw, ["goodsId", "item_id", "id", "skuId", "itemId"]) or ""),
        platform=normalized_platform,
        title=_first_str(raw, ["title", "skuName", "goods_name", "name"]),
        price_cny=price,
        coupon_cny=coupon,
        final_price_cny=final_price,
        shop_name=_first_str(raw, ["shopName", "shop_name", "merchant_name", "sellerName"]),
        sales=_parse_sales(_first(raw, ["monthSales", "sales", "volume", "soldQuantity"])),
        image_url=image_url or None,
        url=url or None,
    )


def _normalize_platform(platform: str, raw: dict[str, Any]) -> Platform:
    value = str(platform or raw.get("platform") or raw.get("sourceType") or "").lower()
    if value in {"1", "taobao", "tb", "tmall", "天猫"}:
        return "taobao"
    if value in {"2", "jd", "jingdong", "京东"}:
        return "jd"
    if value in {"3", "pdd", "pinduoduo", "拼多多"}:
        return "pdd"
    return "taobao"


def _first(raw: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_str(raw: dict[str, Any], keys: list[str], default: str | None = None) -> str:
    value = _first(raw, keys)
    if value in (None, ""):
        return default or ""
    return str(value)


def _first_float(raw: dict[str, Any], keys: list[str]) -> float | None:
    value = _first(raw, keys)
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_sales(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)(万)?", text)
    if not match:
        return None
    number = float(match.group(1))
    if match.group(2):
        number *= 10000
    return int(number)
