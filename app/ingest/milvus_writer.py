from __future__ import annotations

import os
from typing import Any

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient

from app.ingest.env import load_dotenv
from app.ingest.schemas import EmbeddedProduct


class MilvusProductWriter:
    """Create and upsert the product collection used by ItemSearch."""

    def __init__(
        self,
        uri: str | None = None,
        collection_name: str | None = None,
        vector_field: str | None = None,
    ) -> None:
        load_dotenv()
        self.uri = uri or os.environ.get("MILVUS_URI") or "http://localhost:19530"
        self.collection_name = collection_name or os.environ.get("MILVUS_COLLECTION") or "products"
        self.vector_field = vector_field or os.environ.get("MILVUS_VECTOR_FIELD") or "embedding"
        token = os.environ.get("MILVUS_TOKEN")
        db_name = os.environ.get("MILVUS_DB_NAME")

        kwargs: dict[str, Any] = {"uri": self.uri}
        if token:
            kwargs["token"] = token
        if db_name:
            kwargs["db_name"] = db_name
        self.client = MilvusClient(**kwargs)

    def ensure_collection(self, dim: int, drop_existing: bool = False) -> None:
        if drop_existing and self.client.has_collection(self.collection_name):
            self.client.drop_collection(self.collection_name)

        if self.client.has_collection(self.collection_name):
            return

        schema = CollectionSchema(
            fields=[
                FieldSchema(name="item_id", dtype=DataType.VARCHAR, is_primary=True, max_length=128),
                FieldSchema(name="platform", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=1024),
                FieldSchema(name="price_cny", dtype=DataType.FLOAT, nullable=True),
                FieldSchema(name="coupon_cny", dtype=DataType.FLOAT, nullable=True),
                FieldSchema(name="final_price_cny", dtype=DataType.FLOAT, nullable=True),
                FieldSchema(name="shop_name", dtype=DataType.VARCHAR, max_length=512, nullable=True),
                FieldSchema(name="sales", dtype=DataType.INT64, nullable=True),
                FieldSchema(name="image_url", dtype=DataType.VARCHAR, max_length=2048, nullable=True),
                FieldSchema(name="url", dtype=DataType.VARCHAR, max_length=4096, nullable=True),
                FieldSchema(name="attributes_json", dtype=DataType.VARCHAR, max_length=8192),
                FieldSchema(name="embedding_text", dtype=DataType.VARCHAR, max_length=4096),
                FieldSchema(name=self.vector_field, dtype=DataType.FLOAT_VECTOR, dim=dim),
            ],
            description="Glodex product candidates ingested from Maishou.",
            enable_dynamic_field=False,
        )

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name=self.vector_field,
            index_type=os.environ.get("MILVUS_INDEX_TYPE", "AUTOINDEX"),
            metric_type=os.environ.get("MILVUS_METRIC_TYPE", "COSINE"),
        )
        self.client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=index_params,
        )

    def upsert(self, products: list[EmbeddedProduct]) -> int:
        if not products:
            return 0

        dim = len(products[0].embedding)
        if dim <= 0:
            raise ValueError("embedding dimension must be positive")
        self.ensure_collection(dim=dim)

        rows = [_to_milvus_row(product, self.vector_field) for product in products]
        self.client.upsert(collection_name=self.collection_name, data=rows)
        self.client.load_collection(self.collection_name)
        return len(rows)


def _to_milvus_row(product: EmbeddedProduct, vector_field: str) -> dict[str, Any]:
    return {
        "item_id": product.item_id,
        "platform": product.platform,
        "title": _truncate(product.title, 1024),
        "price_cny": product.price_cny,
        "coupon_cny": product.coupon_cny,
        "final_price_cny": product.final_price_cny,
        "shop_name": _truncate(product.shop_name, 512),
        "sales": product.sales,
        "image_url": _truncate(product.image_url, 2048),
        "url": _truncate(product.url, 4096),
        "attributes_json": _truncate(product.attributes_json, 8192) or "{}",
        "embedding_text": _truncate(product.embedding_text, 4096) or product.title,
        vector_field: product.embedding,
    }


def _truncate(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    return str(value)[:max_length]
