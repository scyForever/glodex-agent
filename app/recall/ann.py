from __future__ import annotations

import json
import os
import re
from functools import cached_property
from pathlib import Path
from typing import Any


class AnnUnavailable(RuntimeError):
    """Raised when the configured ANN backend cannot be used."""


class AnnClient:
    """Milvus-backed recall client used by ItemSearch.

    The filename keeps the historical `ann.py` name, but this class is now the
    reusable recall layer for product search:

    - embedding ANN search is delegated to Milvus/Faiss;
    - title keyword recall is delegated to Milvus scalar query when available;
    - hybrid recall merges title and embedding hits by item_id and boosts
      products found by both channels.

    It does not implement a vector index itself. Milvus remains the database
    and ANN engine; this client only wraps project-specific recall policy.
    """

    def __init__(self) -> None:
        self.backend = os.environ.get("ANN_BACKEND", "milvus").lower()
        self.index_path = os.environ.get("ANN_INDEX_PATH")
        self.collection_name = os.environ.get("MILVUS_COLLECTION", "products")
        self.vector_field = os.environ.get("MILVUS_VECTOR_FIELD", "embedding")
        self.title_field = os.environ.get("MILVUS_TITLE_FIELD", "title")

    def is_configured(self) -> bool:
        if self.backend == "faiss":
            return bool(self.index_path)
        if self.backend == "milvus":
            return True
        return False

    def search(
        self,
        emb: list[float],
        top_k: int,
        platform: str,
    ) -> list[dict[str, Any]]:
        """Search ANN backend and return product metadata rows."""
        if not emb:
            return []
        if self.backend == "faiss":
            return self._search_faiss(emb=emb, top_k=top_k, platform=platform)
        if self.backend == "milvus":
            return self._search_milvus(emb=emb, top_k=top_k, platform=platform)
        raise AnnUnavailable(f"Unsupported ANN_BACKEND: {self.backend}")

    def hybrid_search(
        self,
        query: str,
        emb: list[float],
        top_k: int,
        platform: str,
    ) -> list[dict[str, Any]]:
        """Run title keyword recall plus embedding ANN recall.

        Milvus is still responsible for both storage and retrieval. This method
        only coordinates two Milvus calls and applies Glodex-specific merge
        policy.
        """
        vector_hits = [
            {**row, "recall_channels": ["embedding"]}
            for row in self.search(emb=emb, top_k=top_k, platform=platform)
        ]
        title_hits = [
            {**row, "recall_channels": ["title"]}
            for row in self.search_title(query=query, top_k=top_k, platform=platform)
        ]
        return self.merge_results(
            primary=vector_hits,
            secondary=title_hits,
            primary_weight=1.0,
            secondary_weight=0.9,
            duplicate_boost=0.6,
        )[:top_k]

    def search_title(
        self,
        query: str,
        top_k: int,
        platform: str,
    ) -> list[dict[str, Any]]:
        """Search title field for keyword hits.

        Title search currently uses Milvus scalar filtering with LIKE. If the
        backend is not Milvus, or the collection schema does not support this
        query form, the method returns an empty list and vector recall continues.
        """
        if self.backend != "milvus":
            return []
        terms = _split_terms(query)
        if not terms:
            return []

        expr_parts = [f'{self.title_field} like "%{_escape_filter_value(term)}%"' for term in terms]
        expr = " or ".join(expr_parts)
        if platform.lower() != "all":
            platform_expr = f'platform == "{_escape_filter_value(platform.lower())}"'
            expr = f"({platform_expr}) and ({expr})"

        try:
            rows = self._milvus_client.query(
                collection_name=self.collection_name,
                filter=expr,
                output_fields=self._output_fields,
                limit=top_k,
            )
        except Exception:
            return []

        ranked: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ranked.append({**row, "score": _title_score(query=query, title=str(row.get("title") or ""))})
        ranked.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return ranked[:top_k]

    def merge_results(
        self,
        primary: list[dict[str, Any]],
        secondary: list[dict[str, Any]],
        primary_weight: float = 1.0,
        secondary_weight: float = 0.8,
        duplicate_boost: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Merge two recall channels by item_id and boost duplicated hits."""
        bag: dict[str, dict[str, Any]] = {}

        for row in primary:
            item_id = _row_item_id(row)
            if not item_id:
                continue
            bag[item_id] = {**row, "boost": primary_weight * _score_value(row)}

        for row in secondary:
            item_id = _row_item_id(row)
            if not item_id:
                continue
            score = secondary_weight * _score_value(row)
            existing = bag.get(item_id)
            if existing:
                existing["boost"] = float(existing.get("boost") or 0.0) + duplicate_boost * score
                existing["recall_channels"] = _merge_channels(existing, row)
                continue
            bag[item_id] = {**row, "boost": score}

        return sorted(bag.values(), key=lambda item: float(item.get("boost") or 0.0), reverse=True)

    @cached_property
    def _faiss_index(self) -> Any:
        if not self.index_path:
            raise AnnUnavailable("ANN_INDEX_PATH is not configured")
        try:
            import faiss  # type: ignore
        except ImportError as exc:
            raise AnnUnavailable("faiss is not installed") from exc

        path = Path(self.index_path)
        if not path.exists():
            raise AnnUnavailable(f"Faiss index not found: {path}")
        return faiss.read_index(str(path))

    @cached_property
    def _faiss_meta(self) -> dict[int, dict[str, Any]]:
        if not self.index_path:
            raise AnnUnavailable("ANN_INDEX_PATH is not configured")

        meta_path = Path(self.index_path).with_suffix(".json")
        if not meta_path.exists():
            raise AnnUnavailable(f"Faiss metadata not found: {meta_path}")
        with meta_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise AnnUnavailable("Faiss metadata must be a JSON object")
        return {int(k): v for k, v in raw.items() if isinstance(v, dict)}

    def _search_faiss(
        self,
        emb: list[float],
        top_k: int,
        platform: str,
    ) -> list[dict[str, Any]]:
        try:
            import numpy as np  # type: ignore
        except ImportError as exc:
            raise AnnUnavailable("numpy is not installed") from exc

        vec = np.asarray([emb], dtype=np.float32)
        limit = top_k * 3 if platform != "all" else top_k
        scores, idxs = self._faiss_index.search(vec, limit)

        results: list[dict[str, Any]] = []
        normalized_platform = platform.lower()
        for score, idx in zip(scores[0], idxs[0]):
            if idx < 0:
                continue
            meta = self._faiss_meta.get(int(idx))
            if not meta:
                continue
            if (
                normalized_platform != "all"
                and str(meta.get("platform", "")).lower() != normalized_platform
            ):
                continue
            results.append({**meta, "score": float(score)})
            if len(results) >= top_k:
                break
        return results

    @cached_property
    def _milvus_client(self) -> Any:
        try:
            from pymilvus import MilvusClient  # type: ignore
        except ImportError as exc:
            raise AnnUnavailable("pymilvus is not installed") from exc

        uri = os.environ.get("MILVUS_URI", "http://localhost:19530")
        token = os.environ.get("MILVUS_TOKEN")
        db_name = os.environ.get("MILVUS_DB_NAME")
        kwargs: dict[str, Any] = {"uri": uri}
        if token:
            kwargs["token"] = token
        if db_name:
            kwargs["db_name"] = db_name
        return MilvusClient(**kwargs)

    def _search_milvus(
        self,
        emb: list[float],
        top_k: int,
        platform: str,
    ) -> list[dict[str, Any]]:
        expr = None
        if platform.lower() != "all":
            escaped = platform.lower().replace('"', '\\"')
            expr = f'platform == "{escaped}"'

        hits = self._milvus_client.search(
            collection_name=self.collection_name,
            data=[emb],
            anns_field=self.vector_field,
            limit=top_k,
            filter=expr,
            output_fields=self._output_fields,
        )

        rows: list[dict[str, Any]] = []
        for hit in hits[0] if hits else []:
            entity = hit.get("entity") if isinstance(hit, dict) else None
            if not isinstance(entity, dict):
                entity = {}
            distance = hit.get("distance") if isinstance(hit, dict) else None
            score = hit.get("score") if isinstance(hit, dict) else distance
            rows.append({**entity, "score": score})
        return rows

    @property
    def _output_fields(self) -> list[str]:
        return [
            "item_id",
            "platform",
            "title",
            "price_cny",
            "coupon_cny",
            "final_price_cny",
            "shop_name",
            "sales",
            "image_url",
            "url",
            "attributes_json",
            "embedding_text",
        ]


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


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _title_score(query: str, title: str) -> float:
    normalized = title.lower()
    score = 0.0
    for term in _split_terms(query):
        if term in normalized:
            score += 1.0
    return score


def _row_item_id(row: dict[str, Any]) -> str:
    return str(row.get("item_id") or row.get("id") or row.get("goods_id") or row.get("goodsId") or "")


def _score_value(row: dict[str, Any]) -> float:
    for key in ("boost", "score", "distance"):
        value = row.get(key)
        try:
            if value is not None and value != "":
                return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _merge_channels(existing: dict[str, Any], row: dict[str, Any]) -> list[str]:
    channels: list[str] = []
    for value in existing.get("recall_channels") or []:
        if value not in channels:
            channels.append(str(value))
    for value in row.get("recall_channels") or []:
        if value not in channels:
            channels.append(str(value))
    if not channels:
        channels = ["hybrid"]
    return channels


ann_client = AnnClient()
