"""P1 — Product Ingestion.

URL mode: fetch page -> clean HTML -> LLM extracts taxonomy attributes with
verbatim evidence + confidence (null = "the page doesn't say it").
Manual prototype mode: same extraction path over pasted text.
Versioning: create_version() appends additions to raw_text and re-extracts,
producing product_id@v{n+1} (before/after + debate v2 loop).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Optional

from backend import config
from backend.llm import bedrock
from backend.llm.bedrock import cache_key_for, get_bedrock
from backend.llm.prompts import render_prompt
from backend.pipeline.corpus import slugify
from backend.storage import db

PROMPT_VERSION = "extract_v1"


class IngestionError(ValueError):
    """Structured ingestion failure -> API returns 422 {error:{code,message,hint}}."""

    def __init__(self, message: str, code: str = "ingestion_failed", hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "product_id": {"type": "string"},
        "brand": {"type": "string"},
        "display_name": {"type": "string"},
        "attributes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "attribute_id": {"type": "string"},
                "value": {"type": ["string", "null"]},
                "evidence": {"type": ["string", "null"]},
                "confidence": {"type": "number"},
            },
            "required": ["attribute_id", "confidence"],
        }},
    },
    "required": ["attributes"],
}


@lru_cache(maxsize=1)
def _tax() -> dict:
    return json.loads(config.TAXONOMY_PATH.read_text(encoding="utf-8"))


def _attr_ids() -> list[str]:
    return [a["id"] for a in _tax()["attributes"] if a["id"] != "other"]


def _keyword_extract(raw_text: str) -> list[dict]:
    """Offline fallback: sentence containing an attribute keyword becomes weak evidence."""
    out = []
    sentences = re.split(r"(?<=[.!?])\s+", raw_text or "")
    for a in _tax()["attributes"]:
        if a["id"] == "other":
            continue
        hit = None
        for s in sentences:
            s_low = s.lower()
            if any(k in s_low for k in a.get("keywords", [])):
                hit = s.strip()[:200]
                break
        out.append({"attribute_id": a["id"], "value": hit, "evidence": hit,
                    "confidence": 0.4 if hit else 0.0})
    return out


async def extract_attributes(raw_text: str, brand_hint: str, display_hint: str) -> dict:
    """Return {product_id?, brand, display_name, attributes[]} (normalized, all attrs present)."""
    result: Optional[dict] = None
    if get_bedrock().available():
        tax_block = "\n".join(f'- {a["id"]}: {a["label"]} — {a["description"]}'
                              for a in _tax()["attributes"] if a["id"] != "other")
        prompt = render_prompt("extract_v1", display_name=display_hint or "(unknown)",
                               brand=brand_hint or "(unknown)", taxonomy_block=tax_block,
                               raw_text=raw_text[:14000])
        try:
            result = await bedrock.acomplete_json(
                prompt=prompt, schema=EXTRACT_SCHEMA, max_tokens=3500,
                cache_key=cache_key_for("extract", raw_text[:14000], PROMPT_VERSION))
        except Exception:
            result = None
    if result is None:
        result = {"brand": brand_hint, "display_name": display_hint,
                  "attributes": _keyword_extract(raw_text)}
    # normalize: every taxonomy attribute exactly once
    by_id = {}
    for a in result.get("attributes", []):
        aid = a.get("attribute_id")
        if aid in _attr_ids() and aid not in by_id:
            val = a.get("value")
            conf = max(0.0, min(1.0, float(a.get("confidence") or 0)))
            by_id[aid] = {"attribute_id": aid,
                          "value": (str(val).strip() or None) if val is not None else None,
                          "evidence": (a.get("evidence") or None),
                          "confidence": conf if val is not None else 0.0}
    attributes = [by_id.get(aid, {"attribute_id": aid, "value": None, "evidence": None,
                                  "confidence": 0.0}) for aid in _attr_ids()]
    return {"product_id": result.get("product_id"),
            "brand": (result.get("brand") or brand_hint or "Unknown").strip(),
            "display_name": (result.get("display_name") or display_hint or "Unknown product").strip(),
            "attributes": attributes}


def _unique_product_id(candidate: str) -> str:
    pid = slugify(candidate) or db.new_id("product")
    if db.latest_version(pid) is None:
        return pid
    i = 2
    while db.latest_version(f"{pid}-{i}") is not None:
        i += 1
    return f"{pid}-{i}"


async def create_product(body: dict) -> dict:
    source = body.get("source")
    if source == "url":
        if not body.get("source_url"):
            raise ValueError("source_url required for source=url")
        import httpx

        from backend.ingestion.fetcher import fetch_url, unwrap_url
        body["source_url"] = unwrap_url(body["source_url"])  # store the real page as source
        try:
            title, raw_text = await fetch_url(body["source_url"])
        except httpx.HTTPError as e:
            raise IngestionError(
                f"could not fetch the page ({e})", code="fetch_failed",
                hint="The site refuses server-side access. Copy the product description from "
                     "your browser and retry with source=manual_prototype.") from e
        if len(raw_text) < 200:
            raise IngestionError(
                "the page has almost no server-readable content (client-side rendered app or "
                "anti-bot protection, e.g. Shopee) — there is nothing for an AI crawler to read",
                code="page_not_extractable",
                hint="This is itself an AI-visibility problem for that listing. For testing: "
                     "copy the product description text from your browser and POST /products "
                     "with source=manual_prototype (brand + raw_text).")
        display_hint = body.get("display_name") or title[:80]
        brand_hint = body.get("brand") or (title.split("|")[0].split("—")[0].strip()[:40] if title else "")
    elif source == "manual_prototype":
        raw_text = (body.get("raw_text") or "").strip()
        if not raw_text or not body.get("brand"):
            raise ValueError("brand and raw_text required for manual_prototype")
        if len(raw_text) < 40:
            raise IngestionError("raw_text too short to extract from",
                                 code="raw_text_too_short",
                                 hint="paste at least a few sentences of product description")
        display_hint = body.get("display_name") or f"{body['brand']} prototype"
        brand_hint = body["brand"]
    else:
        raise ValueError("source must be 'url' or 'manual_prototype'")
    ext = await extract_attributes(raw_text, brand_hint, display_hint)
    pid = body.get("product_id") or ext.get("product_id") or f"{ext['brand']}-{ext['display_name']}"
    product = {
        "product_id": _unique_product_id(pid),
        "version": 1,
        "brand": ext["brand"],
        "display_name": ext["display_name"],
        "source": source,
        "source_url": body.get("source_url"),
        "raw_text": raw_text,
        "attributes": ext["attributes"],
    }
    db.upsert_product(product)
    product["ref"] = f"{product['product_id']}@v1"
    return product


async def create_version(product_id: str, base_version: int, additions: list[str],
                         change_note: str) -> dict:
    base = db.get_product(product_id, base_version)
    if not base:
        raise KeyError(f"{product_id}@v{base_version} not found")
    additions = [a.strip() for a in additions if a and a.strip()]
    if not additions:
        raise ValueError("additions must contain at least one non-empty paragraph")
    raw_text = base["raw_text"].rstrip() + "\n\n" + "\n\n".join(additions)
    ext = await extract_attributes(raw_text, base["brand"], base["display_name"])
    new_version = (db.latest_version(product_id) or base_version) + 1
    product = {
        "product_id": product_id,
        "version": new_version,
        "brand": base["brand"],
        "display_name": base["display_name"],
        "source": base["source"],
        "source_url": base["source_url"],
        "raw_text": raw_text,
        "attributes": ext["attributes"],
        "parent_version": base_version,
        "change_note": change_note,
    }
    db.upsert_product(product)
    product["ref"] = f"{product_id}@v{new_version}"
    return product
