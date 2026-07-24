from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from app.ingest.attribute_prompt import (
    ATTRIBUTE_EXTRACTION_SYSTEM_PROMPT,
    build_attribute_extraction_user_prompt,
)
from app.ingest.env import load_dotenv
from app.ingest.schemas import NormalizedProduct


class AttributeExtractionUnavailable(RuntimeError):
    """Raised when the multimodal attribute extractor cannot be used."""


class AttributeExtractor:
    """OpenAI-compatible multimodal client for product attributes."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.environ.get("VISION_API_KEY")
        self.base_url = (base_url or os.environ.get("VISION_BASE_URL") or "").rstrip("/")
        self.model = model or os.environ.get("VISION_MODEL")
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    async def extract(self, product: NormalizedProduct) -> dict[str, Any]:
        if not self.is_configured():
            raise AttributeExtractionUnavailable("VISION_API_KEY, VISION_BASE_URL, or VISION_MODEL is not configured")

        image_urls = [product.image_url] if product.image_url else []
        text = build_attribute_extraction_user_prompt(
            title=product.title,
            shop_name=product.shop_name,
            image_urls=image_urls,
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for image_url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": image_url}})

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": ATTRIBUTE_EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content_text = _message_content(data)
        parsed = _parse_json_object(content_text)
        return _clean_attributes(parsed)


def _message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise AttributeExtractionUnavailable("vision model returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    raise AttributeExtractionUnavailable("vision model returned an unsupported message shape")


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            return {}
        payload = json.loads(match.group(0))
    return payload if isinstance(payload, dict) else {}


def _clean_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in attributes.items():
        if key in (None, "") or value in (None, ""):
            continue
        if isinstance(value, (dict, list)):
            cleaned[str(key)] = json.dumps(value, ensure_ascii=False)
        else:
            cleaned[str(key)] = value
    return cleaned
