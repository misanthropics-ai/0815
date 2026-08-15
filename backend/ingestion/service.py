"""P1 — Product Ingestion.

URL mode: fetch page -> clean HTML -> LLM extracts taxonomy attributes with
verbatim evidence + confidence (null = "the page doesn't say it").
Manual prototype mode: same extraction path over pasted text.
Versioning: create_version() appends additions to raw_text and re-extracts,
producing product_id@v{n+1} (before/after + debate v2 loop).
"""
from __future__ import annotations

import re
from typing import Optional

from backend import config
from backend.llm import bedrock
from backend.llm.bedrock import cache_key_for, get_bedrock
from backend.llm.prompts import render_prompt
from backend.pipeline.corpus import slugify
from backend.storage import db
from backend.taxonomy import GENERIC_PATH, category_slug, load_taxonomy, taxonomy_path

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
        "category": {"type": "string"},
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


def _tax(category: Optional[str] = None) -> dict:
    """Category-aware taxonomy: specific file if one exists, else generic.
    None => generic + let the LLM detect the category."""
    return load_taxonomy(category if category is not None else "generic product")


def _attr_ids(category: Optional[str] = None) -> list[str]:
    return [a["id"] for a in _tax(category)["attributes"] if a["id"] != "other"]


def _keyword_extract(raw_text: str, category: Optional[str] = None) -> list[dict]:
    """Offline fallback: sentence containing an attribute keyword becomes weak evidence."""
    out = []
    sentences = re.split(r"(?<=[.!?])\s+", raw_text or "")
    for a in _tax(category)["attributes"]:
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


async def extract_attributes(raw_text: str, brand_hint: str, display_hint: str,
                             category: Optional[str] = None) -> dict:
    """Return {product_id?, brand, display_name, category, attributes[]}.

    category given  => extract against that category's taxonomy (specific file or generic).
    category absent/blank => extract against the GENERIC taxonomy and let the LLM detect
    the category; if the detected category has a specific taxonomy file (e.g. travel
    backpack), re-extract once with it so the demo categories keep their rich attribute set.
    """
    category = (category or "").strip() or None  # "" from empty form fields == not provided
    detect = category is None
    tax = _tax(category)
    result: Optional[dict] = None
    if get_bedrock().available():
        tax_block = "\n".join(f'- {a["id"]}: {a["label"]} — {a["description"]}'
                              for a in tax["attributes"] if a["id"] != "other")
        if detect:
            existing = sorted({(p.get("category") or "").strip()
                               for p in db.list_products() if p.get("category")})[:20]
            existing_line = (" If it belongs to one of these EXISTING categories, return that "
                             "string EXACTLY: " + "; ".join(existing) + ".") if existing else ""
            category_instruction = (
                "Also return category: the GENERIC product type as a short lowercase English "
                "noun phrase, 1-3 words, no marketing/spec qualifiers (e.g. 'smart tv' — never "
                "'4K Mini LED smart TV'; 'travel backpack', 'wireless earbuds')."
                + existing_line)
        else:
            category_instruction = f"The product category is: {category}."
        prompt = render_prompt("extract_v1", display_name=display_hint or "(unknown)",
                               brand=brand_hint or "(unknown)", taxonomy_block=tax_block,
                               raw_text=raw_text[:14000],
                               category_instruction=category_instruction)
        try:
            result = await bedrock.acomplete_json(
                prompt=prompt, schema=EXTRACT_SCHEMA, max_tokens=3500,
                cache_key=cache_key_for("extract", raw_text[:14000], PROMPT_VERSION,
                                        tax.get("category"), detect))
        except Exception:
            result = None
    if result is None:
        result = {"brand": brand_hint, "display_name": display_hint,
                  "attributes": _keyword_extract(raw_text, category)}
    # detected category with a SPECIFIC taxonomy file => one re-extract with it
    if detect and result.get("category"):
        detected = str(result["category"]).strip()
        if detected and taxonomy_path(detected) != GENERIC_PATH:
            return await extract_attributes(raw_text, brand_hint, display_hint,
                                            category=detected)
        category = detected or None
    # normalize: every taxonomy attribute exactly once (against the taxonomy actually used)
    valid = _attr_ids(category)
    by_id = {}
    for a in result.get("attributes", []):
        aid = a.get("attribute_id")
        if aid in valid and aid not in by_id:
            val = a.get("value")
            conf = max(0.0, min(1.0, float(a.get("confidence") or 0)))
            by_id[aid] = {"attribute_id": aid,
                          "value": (str(val).strip() or None) if val is not None else None,
                          "evidence": (a.get("evidence") or None),
                          "confidence": conf if val is not None else 0.0}
    attributes = [by_id.get(aid, {"attribute_id": aid, "value": None, "evidence": None,
                                  "confidence": 0.0}) for aid in valid]
    return {"product_id": result.get("product_id"),
            "brand": (result.get("brand") or brand_hint or "Unknown").strip(),
            "display_name": (result.get("display_name") or display_hint or "Unknown product").strip(),
            "category": category,
            "attributes": attributes}


VISION_SCHEMA = {
    "type": "object",
    "properties": {"attributes": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "attribute_id": {"type": "string"},
            "value": {"type": ["string", "null"]},
            "evidence": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
        },
        "required": ["attribute_id", "confidence"],
    }}},
    "required": ["attributes"],
}
_IMG_FMT = {"image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


async def _extract_from_images(image_urls: list[str], category, missing_ids: list[str]) -> list[dict]:
    """Claude vision over product images: fill ONLY attributes the text missed."""
    import httpx

    from backend.ingestion.fetcher import UA
    max_imgs = int(config.env("IMG_EXTRACT_MAX", "6") or 6)
    blocks: list[dict] = []
    used: list[str] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=15,
                                 headers={"User-Agent": UA}) as client:
        for u in image_urls:
            if len(blocks) >= max_imgs:
                break
            try:
                r = await client.get(u)
                ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                fmt = _IMG_FMT.get(ct) or _IMG_FMT.get(
                    "image/" + u.rsplit(".", 1)[-1].lower().replace("jpg", "jpeg"), None)
                data = r.content
                if r.status_code != 200 or not fmt or not (3_000 < len(data) < 4_000_000):
                    continue
                blocks.append({"image": {"format": fmt, "source": {"bytes": data}}})
                used.append(u)
            except Exception:
                continue
    if not blocks:
        return []
    tax = _tax(category)
    subset = [a for a in tax["attributes"] if a["id"] in set(missing_ids)]
    tax_block = "\n".join(f'- {a["id"]}: {a["label"]} — {a["description"]}' for a in subset)
    prompt = (
        "These are product images from an e-commerce page (spec banners often contain the "
        "real specifications). Extract ONLY attributes clearly VISIBLE in these images for "
        "this taxonomy (value=null when not shown; never guess):\n" + tax_block
        + "\nFor each: attribute_id, value (concise fact as shown), evidence (short "
          "description of where/what in the image, e.g. 'spec table in image 2: 120Hz'), "
          "confidence 0..1.")
    out = await bedrock.acomplete_json(
        messages=[{"role": "user", "content": blocks + [{"text": prompt}]}],
        schema=VISION_SCHEMA, max_tokens=2500,
        cache_key=cache_key_for("imgextract", used, sorted(missing_ids),
                                tax.get("category")))
    return out.get("attributes", []) if isinstance(out, dict) else []


def _unique_product_id(candidate: str) -> str:
    pid = slugify(candidate) or db.new_id("product")
    if db.latest_version(pid) is None:
        return pid
    i = 2
    while db.latest_version(f"{pid}-{i}") is not None:
        i += 1
    return f"{pid}-{i}"


def _reusable_url_product(source_url: str, category: Optional[str]) -> Optional[dict]:
    """Reuse an already-ingested URL when it belongs to the requested category.

    Legacy products without a category are treated as travel backpacks, matching
    the diagnosis service's backward-compatibility rule.
    """
    candidates = db.list_products_by_source_url(source_url)
    requested = (category or "").strip()
    if requested:
        wanted = category_slug(requested)
        candidates = [
            product
            for product in candidates
            if category_slug(product.get("category") or "travel backpack") == wanted
        ]
    return candidates[0] if candidates else None


async def create_product(body: dict) -> dict:
    source = body.get("source")
    if source == "url":
        if not body.get("source_url"):
            raise ValueError("source_url required for source=url")
        import httpx

        from backend.ingestion.fetcher import fetch_page, unwrap_url
        body["source_url"] = unwrap_url(body["source_url"])  # store the real page as source
        from urllib.parse import urlparse
        path = urlparse(body["source_url"]).path.lower()
        if any(seg in path for seg in ("/categories/", "/category/", "/search", "/list",
                                       "/collections/", "/brand/")):
            raise IngestionError(
                "this looks like a category/listing page, not a single product page",
                code="listing_page",
                hint="貼單一商品頁的網址（例如 momo 的 /goods/GoodsDetail.jsp?i_code=...），"
                     "分類頁包含多個商品，無法抽取單一產品屬性")
        existing = _reusable_url_product(body["source_url"], body.get("category"))
        if existing:
            return existing
        try:
            page = await fetch_page(body["source_url"])
            title, raw_text, images = page["title"], page["text"], page["images"]
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
        images = []
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
    ext = await extract_attributes(raw_text, brand_hint, display_hint,
                                   category=body.get("category"))
    category = ext.get("category") or (body.get("category") or "").strip() or None
    if category:
        # learn (or extend) the category taxonomy from this page; if that changed the
        # attribute set, re-extract once against the category-specific taxonomy
        from backend.taxonomy.builder import ensure_category_taxonomy
        try:
            tax = await ensure_category_taxonomy(category, raw_text)
            if tax:
                want = {a["id"] for a in tax["attributes"] if a["id"] != "other"}
                have = {a["attribute_id"] for a in ext["attributes"]}
                if want != have:
                    ext = await extract_attributes(raw_text, brand_hint, display_hint,
                                                   category=category)
        except Exception:
            pass
    # --- vision fallback: too many attributes missing from TEXT => read the product
    # images (spec banners). Image-derived attrs are flagged — they are INVISIBLE to
    # AI crawlers, which becomes an explicit visibility warning in the diagnosis.
    image_derived: list[str] = []
    if source == "url" and images and get_bedrock().available():
        total = len(ext["attributes"]) or 1
        filled = sum(1 for a in ext["attributes"] if a["value"])
        if filled / total < float(config.env("IMG_COVERAGE_THRESHOLD", "0.6") or 0.6):
            missing = [a["attribute_id"] for a in ext["attributes"] if not a["value"]]
            try:
                vis = await _extract_from_images(images, category, missing)
                by = {a.get("attribute_id"): a for a in vis if a.get("value")}
                for a in ext["attributes"]:
                    v = by.get(a["attribute_id"])
                    if v and not a["value"]:
                        a["value"] = str(v["value"]).strip()[:200]
                        a["evidence"] = ("[from image] "
                                         + str(v.get("evidence") or v["value"]).strip()[:180])
                        a["confidence"] = min(0.85, float(v.get("confidence") or 0.6))
                        a["source"] = "image"
                        image_derived.append(a["attribute_id"])
                from backend.loglib import log
                log("ingest.image_extract", url=body.get("source_url", "")[:80],
                    images_used=min(len(images), int(config.env("IMG_EXTRACT_MAX", "6") or 6)),
                    attrs_filled=len(image_derived))
            except Exception:
                pass

    pid = body.get("product_id") or ext.get("product_id") or f"{ext['brand']}-{ext['display_name']}"
    product = {
        "product_id": _unique_product_id(pid),
        "version": 1,
        "brand": ext["brand"],
        "display_name": ext["display_name"],
        "category": category,
        "personas": None,
        "source": source,
        "source_url": body.get("source_url"),
        "raw_text": raw_text,
        "attributes": ext["attributes"],
    }
    if body.get("personas"):
        from backend.pipeline.intents import normalize_personas
        product["personas"] = normalize_personas(body["personas"], product.get("category"))
    db.upsert_product(product)
    product["ref"] = f"{product['product_id']}@v1"
    product["image_derived_attributes"] = image_derived
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
    ext = await extract_attributes(raw_text, base["brand"], base["display_name"],
                                   category=base.get("category"))
    new_version = (db.latest_version(product_id) or base_version) + 1
    product = {
        "product_id": product_id,
        "version": new_version,
        "brand": base["brand"],
        "display_name": base["display_name"],
        "category": base.get("category"),
        "personas": base.get("personas"),
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
