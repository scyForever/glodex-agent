from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Platform = Literal["taobao", "jd", "pdd"]


class NormalizedProduct(BaseModel):
    """Product shape after normalizing Maishou search/detail data."""

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

    def embedding_text(self) -> str:
        """Build the text used by the embedding model."""
        attr_text = " ".join(
            f"{key}:{value}"
            for key, value in self.attributes.items()
            if value not in (None, "")
        )
        return " ".join(
            part.strip()
            for part in [self.title, self.shop_name or "", attr_text]
            if part and part.strip()
        )


class EmbeddedProduct(BaseModel):
    """Milvus-ready product row."""

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
    attributes_json: str = "{}"
    embedding_text: str
    embedding: list[float]

    @classmethod
    def from_product(cls, product: NormalizedProduct, embedding: list[float]) -> "EmbeddedProduct":
        import json

        return cls(
            item_id=product.item_id,
            platform=product.platform,
            title=product.title,
            price_cny=product.price_cny,
            coupon_cny=product.coupon_cny,
            final_price_cny=product.final_price_cny,
            shop_name=product.shop_name,
            sales=product.sales,
            image_url=product.image_url,
            url=product.url,
            attributes=product.attributes,
            attributes_json=json.dumps(product.attributes, ensure_ascii=False),
            embedding_text=product.embedding_text(),
            embedding=embedding,
        )
