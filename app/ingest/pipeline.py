from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Any

from app.ingest.attribute_extractor import AttributeExtractor
from app.ingest.embedding import EmbeddingClient
from app.ingest.maishou import MaishouClient
from app.ingest.milvus_writer import MilvusProductWriter
from app.ingest.normalizer import normalize_product
from app.ingest.schemas import EmbeddedProduct, NormalizedProduct


@dataclass
class IngestResult:
    searched: int = 0
    normalized: int = 0
    embedded: int = 0
    written: int = 0


async def ingest_maishou_keyword(
    keyword: str,
    platform: str = "jd",
    limit: int = 20,
    enrich_detail: bool = True,
    extract_attributes: bool = True,
    drop_existing: bool = False,
    dry_run: bool = False,
) -> IngestResult:
    maishou = MaishouClient()
    attribute_extractor = AttributeExtractor()
    embedding_client = EmbeddingClient()
    writer = MilvusProductWriter()

    search_rows = await maishou.search(keyword=keyword, platform=platform, page=1, page_size=limit)
    search_rows = search_rows[:limit]
    result = IngestResult(searched=len(search_rows))

    products: list[NormalizedProduct] = []
    seen: set[str] = set()
    for row in search_rows:
        merged = row
        goods_id = str(row.get("goodsId") or row.get("item_id") or row.get("id") or "")
        if enrich_detail and goods_id:
            try:
                detail = await maishou.detail(goods_id=goods_id, platform=platform)
                merged = {**row, **detail}
            except Exception:
                merged = row

        product = normalize_product(merged, platform=platform)
        if not product.item_id or not product.title or product.item_id in seen:
            continue
        seen.add(product.item_id)

        if extract_attributes:
            try:
                product.attributes = await attribute_extractor.extract(product)
            except Exception:
                product.attributes = {}
        products.append(product)

    result.normalized = len(products)

    embedded: list[EmbeddedProduct] = []
    for product in products:
        embedding_text = product.embedding_text()
        embedding = await embedding_client.embed(embedding_text)
        embedded.append(EmbeddedProduct.from_product(product, embedding=embedding))

    result.embedded = len(embedded)
    if dry_run:
        return result

    if embedded:
        writer.ensure_collection(dim=len(embedded[0].embedding), drop_existing=drop_existing)
        result.written = writer.upsert(embedded)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest Maishou products into Milvus.")
    parser.add_argument("--keyword", required=True, help="Search keyword, for example: 白色内存条")
    parser.add_argument("--platform", default="jd", choices=["taobao", "jd", "pdd"], help="Maishou source platform")
    parser.add_argument("--limit", type=int, default=20, help="Max products to ingest")
    parser.add_argument("--no-detail", action="store_true", help="Skip Maishou detail API")
    parser.add_argument("--no-attributes", action="store_true", help="Skip multimodal attribute extraction")
    parser.add_argument("--drop-existing", action="store_true", help="Drop the Milvus collection before writing")
    parser.add_argument("--dry-run", action="store_true", help="Run all steps except Milvus write")
    return parser


async def async_main(args: argparse.Namespace) -> IngestResult:
    return await ingest_maishou_keyword(
        keyword=args.keyword,
        platform=args.platform,
        limit=max(1, args.limit),
        enrich_detail=not args.no_detail,
        extract_attributes=not args.no_attributes,
        drop_existing=args.drop_existing,
        dry_run=args.dry_run,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = asyncio.run(async_main(args))
    print(_format_result(result))


def _format_result(result: IngestResult) -> str:
    values: dict[str, Any] = {
        "searched": result.searched,
        "normalized": result.normalized,
        "embedded": result.embedded,
        "written": result.written,
    }
    return " ".join(f"{key}={value}" for key, value in values.items())


if __name__ == "__main__":
    main()
