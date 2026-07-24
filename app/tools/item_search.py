from __future__ import annotations

import asyncio
import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.api.monitor import monitor
from app.recall.ann import AnnUnavailable, ann_client
from app.recall.tower_query import TowerUnavailable, query_tower_client
from app.recall.tower_user import user_tower_client
from app.tools.item_picker import CandidateItem


Platform = Literal[
    "all",
    "jd",
    "taobao",
    "pdd",
    "mock",
]


class ItemSearchOutput(BaseModel):
    """ItemSearch 的结构化输出。"""

    platform: str
    query: str
    candidates: list[CandidateItem]
    total_recall: int = 0
    truncated: bool = False
    backend: str = "local_index"
    notice: str | None = None


@tool
async def item_search(
    query: str,
    platform: Platform = "all",
    top_k: int = 20,
    user_id: str | None = None,
) -> ItemSearchOutput:
    """从已有商品索引中检索国内电商候选商品。

    Args:
        query: Planner 拆解后的检索词。
        platform: 平台过滤条件，可指定 jd / taobao / pdd 等，也可使用 all。
        top_k: 最多返回候选数量，默认 20，最大 50。
        user_id: 可选用户 ID；后续接个性化召回时使用。

    Returns:
        标准化候选商品列表，可直接交给 ItemPicker。
    """
    top_k = max(1, min(top_k, 50))
    await monitor.report_tool_start(
        "item_search",
        {
            "query": query,
            "platform": platform,
            "top_k": top_k,
            "user_id": user_id,
        },
    )
    start = time.time()

    backend = "local_index"
    notice: str | None = None

    try:
        candidates, total_recall, notice = await _search_vector_index(
            query=query,
            platform=platform,
            top_k=top_k,
            user_id=user_id,
        )
        backend = "vector_ann"
    except Exception as exc:
        candidates, total_recall, local_notice = _search_local_index(
            query=query,
            platform=platform,
            top_k=top_k,
        )
        notice = local_notice or f"向量召回不可用，已回退本地索引：{exc}"

    await monitor.report_tool_end("item_search", int((time.time() - start) * 1000))
    return ItemSearchOutput(
        platform=platform,
        query=query,
        candidates=candidates,
        total_recall=total_recall,
        truncated=total_recall > len(candidates),
        backend=backend,
        notice=notice,
    )


async def _search_vector_index(
    query: str,
    platform: str,
    top_k: int,
    user_id: str | None,
) -> tuple[list[CandidateItem], int, str | None]:
    """Run title + embedding hybrid recall and optional personalized recall."""
    if not ann_client.is_configured():
        raise AnnUnavailable("ANN backend is not configured")
    if not query_tower_client.is_configured():
        raise TowerUnavailable("TOWER_QUERY_ENDPOINT is not configured")

    query_emb = await query_tower_client.encode_query(query)
    semantic_task = asyncio.create_task(
        asyncio.to_thread(ann_client.hybrid_search, query, query_emb, top_k, platform)
    )

    personalized_task: asyncio.Task[list[dict[str, Any]]] | None = None
    if user_id and user_tower_client.is_configured():
        personalized_task = asyncio.create_task(
            _personalized_recall(
                query_emb=query_emb,
                platform=platform,
                top_k=top_k,
                user_id=user_id,
            )
        )

    semantic = await semantic_task
    personalized = await personalized_task if personalized_task else []
    merged = ann_client.merge_results(
        primary=semantic,
        secondary=personalized,
        primary_weight=1.0,
        secondary_weight=0.8,
        duplicate_boost=0.5,
    )
    candidates = [_to_candidate_item(row) for row in merged[:top_k]]
    total_recall = len(semantic) + len(personalized)

    notice = None
    if user_id and personalized_task is None:
        notice = "未配置 TOWER_USER_ENDPOINT，本次只使用语义召回。"
    return candidates, total_recall, notice


async def _personalized_recall(
    query_emb: list[float],
    platform: str,
    top_k: int,
    user_id: str,
) -> list[dict[str, Any]]:
    user_emb = await user_tower_client.encode_user(user_id)
    fused = _fuse_embeddings(user_emb=user_emb, query_emb=query_emb)
    rows = await asyncio.to_thread(ann_client.search, fused, top_k, platform)
    return [{**row, "recall_channels": ["personalized_embedding"]} for row in rows]


def _fuse_embeddings(user_emb: list[float], query_emb: list[float]) -> list[float]:
    """Fuse user and query embeddings for the personalized channel."""
    if len(user_emb) != len(query_emb):
        return query_emb
    return [
        0.6 * user_value + 0.4 * query_value
        for user_value, query_value in zip(user_emb, query_emb)
    ]


def _search_local_index(
    query: str,
    platform: str,
    top_k: int,
) -> tuple[list[CandidateItem], int, str | None]:
    """搜索本地 JSON/JSONL 商品索引，作为向量库接入前的轻量实现。"""
    products = _load_local_products()
    if not products:
        return (
            [],
            0,
            "未配置 ITEM_SEARCH_INDEX_PATH 或商品索引为空；请先由数据采集任务填充商品库。",
        )

    normalized_platform = platform.lower()
    filtered = [
        product
        for product in products
        if normalized_platform == "all"
        or str(product.get("platform", "")).lower() == normalized_platform
    ]

    scored: list[tuple[float, CandidateItem]] = []
    for product in filtered:
        candidate = _to_candidate_item(product)
        score = _keyword_score(query, candidate)
        if score > 0:
            scored.append((score, candidate))

    scored.sort(key=lambda item: item[0], reverse=True)
    picked = [candidate for _, candidate in scored[:top_k]]
    return picked, len(scored), None


@lru_cache(maxsize=1)
def _load_local_products() -> list[dict[str, Any]]:
    """加载已有商品索引；支持 JSON 数组或 JSONL。"""
    index_path = os.environ.get("ITEM_SEARCH_INDEX_PATH")
    if not index_path:
        return []

    path = Path(index_path)
    if not path.exists() or not path.is_file():
        return []

    if path.suffix.lower() == ".jsonl":
        products: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                payload = json.loads(stripped)
                if isinstance(payload, dict):
                    products.append(payload)
        return products

    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("products") or []
        return [item for item in items if isinstance(item, dict)]
    return []


def _to_candidate_item(product: dict[str, Any]) -> CandidateItem:
    """把商品库中的 dict 兼容转换为 CandidateItem。"""
    return CandidateItem(
        item_id=str(product.get("item_id") or product.get("id") or product.get("goods_id") or product.get("goodsId")),
        platform=str(product.get("platform") or "unknown"),
        title=str(product.get("title") or product.get("name") or ""),
        price_cny=_to_float(product.get("price_cny") or product.get("originalPrice") or product.get("price")),
        coupon_cny=_to_float(product.get("coupon_cny") or product.get("couponPrice")),
        final_price_cny=_to_float(product.get("final_price_cny") or product.get("actualPrice")),
        shop_name=_to_str_or_none(product.get("shop_name") or product.get("shopName")),
        sales=_to_int_or_none(product.get("sales") or product.get("monthSales")),
        image_url=_to_str_or_none(product.get("image_url") or product.get("picUrl")),
        url=_to_str_or_none(product.get("url")),
        attributes=_to_dict(product.get("attributes") or product.get("attributes_json")),
    )


def _keyword_score(query: str, candidate: CandidateItem) -> float:
    """临时关键词打分；向量召回接入后会替换掉。"""
    query_terms = _split_terms(query)
    if not query_terms:
        return 0.0

    searchable_text = " ".join(
        [
            candidate.title,
            candidate.shop_name or "",
            *_attr_text_parts(candidate.attributes),
        ]
    ).lower()

    score = 0.0
    for term in query_terms:
        if term in searchable_text:
            score += 1.0
    if candidate.sales:
        score += min(candidate.sales / 10000, 0.2)
    return score


def _split_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in re.split(r"[\s,，;；]+", query.lower()):
        token = token.strip()
        if not token:
            continue
        terms.append(token)
        if re.search(r"[\u4e00-\u9fff]", token) and len(token) > 2:
            for size in (2, 3):
                for idx in range(0, max(len(token) - size + 1, 0)):
                    terms.append(token[idx : idx + size])

    deduped: list[str] = []
    for term in terms:
        if term not in deduped:
            deduped.append(term)
    return deduped[:12]


def _attr_text_parts(attributes: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for key, value in attributes.items():
        if value in (None, ""):
            continue
        parts.append(str(value))
        parts.append(f"{key}:{value}")
    return parts


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_str_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}
