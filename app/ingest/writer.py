from __future__ import annotations

import json
from pathlib import Path

from app.ingest.schemas import NormalizedProduct


def write_products_jsonl(products: list[NormalizedProduct], output_path: str | Path) -> None:
    """把统一商品结构写入 JSONL，供 item_search 第一版读取。"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for product in products:
            f.write(json.dumps(product.model_dump(), ensure_ascii=False) + "\n")
