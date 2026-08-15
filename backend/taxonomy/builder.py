"""LLM-learned category taxonomies.

First product of an unseen category => generate a category-specific attribute
taxonomy from the actual product page (8–12 decision attributes + intent
clusters), persisted BOTH to the DB (source of truth, survives redeploys) and
as backend/taxonomy/{slug}.json so the existing file-based resolution
(load_taxonomy) picks it up everywhere — extraction, evidence audit, funnel,
decision prompts, /taxonomy endpoint.

Later products of the same category can EXTEND the learned taxonomy (max 2 new
attributes per product, hard cap 14 total): "what the category's players
advertise" accumulates, and a null value on your product = the gap signal.

Curated taxonomies (the backpack demo file, anything hand-written without
`"learned": true`) are never modified.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from backend.llm import bedrock
from backend.llm.bedrock import cache_key_for, get_bedrock
from backend.storage import db
from backend.taxonomy import (GENERIC_PATH, TAXONOMY_DIR, category_slug, load_taxonomy,
                              taxonomy_path)

MAX_ATTRS = 14
ALWAYS_ATTRS = ("price", "brand_reputation")

GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "attributes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "label": {"type": "string"},
                "description": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "label", "description", "keywords"],
        }},
        "clusters": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "label": {"type": "string"},
                "description": {"type": "string"},
                "attributes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "label", "description", "attributes"],
        }},
    },
    "required": ["attributes", "clusters"],
}

EXTEND_SCHEMA = {
    "type": "object",
    "properties": {"new_attributes": {"type": "array", "items":
                   GEN_SCHEMA["properties"]["attributes"]["items"]}},
    "required": ["new_attributes"],
}


def _aid(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (raw or "").lower()).strip("_")[:40]


def _persist(slug: str, tax: dict) -> None:
    db.kv_set(f"taxonomy:{slug}", "taxonomy", tax)
    (TAXONOMY_DIR / f"{slug}.json").write_text(
        json.dumps(tax, indent=2, ensure_ascii=False), encoding="utf-8")
    load_taxonomy.cache_clear()


def restore_learned_taxonomies() -> int:
    """Startup: DB -> files, so learned taxonomies survive redeploys/fresh checkouts."""
    restored = 0
    for key, tax in db.kv_list("taxonomy:").items():
        slug = key.split(":", 1)[1]
        path = TAXONOMY_DIR / f"{slug}.json"
        if tax and not path.exists():
            path.write_text(json.dumps(tax, indent=2, ensure_ascii=False), encoding="utf-8")
            restored += 1
    if restored:
        load_taxonomy.cache_clear()
    return restored


def _normalize(slug: str, attributes: list[dict], clusters: list[dict]) -> dict:
    generic = json.loads(GENERIC_PATH.read_text(encoding="utf-8"))
    gmap = {a["id"]: a for a in generic["attributes"]}
    out_attrs: list[dict] = []
    seen: set[str] = set()
    for a in attributes:
        aid = _aid(a.get("id", ""))
        if not aid or aid in seen or aid == "other":
            continue
        seen.add(aid)
        out_attrs.append({"id": aid, "label": (a.get("label") or aid)[:60],
                          "description": (a.get("description") or "")[:200],
                          "keywords": [str(k).lower()[:40] for k in (a.get("keywords") or [])][:12]})
        if len(out_attrs) >= MAX_ATTRS - 1:
            break
    for must in ALWAYS_ATTRS:
        if must not in seen and must in gmap:
            out_attrs.append(gmap[must])
            seen.add(must)
    out_attrs.append(gmap["other"])
    valid = {a["id"] for a in out_attrs}
    out_clusters = []
    cseen: set[str] = set()
    for c in clusters[:8]:
        cid = _aid(c.get("id", ""))
        attrs = [x for x in (c.get("attributes") or []) if _aid(x) in valid]
        if not cid or cid in cseen or not attrs:
            continue
        cseen.add(cid)
        out_clusters.append({"id": cid, "label": (c.get("label") or cid)[:60],
                             "description": (c.get("description") or "")[:200],
                             "attributes": [_aid(x) for x in attrs][:4]})
    if not out_clusters:
        out_clusters = generic["clusters"]
    return {"version": 1, "category": slug, "learned": True,
            "attributes": out_attrs, "clusters": out_clusters}


async def ensure_category_taxonomy(category: Optional[str], sample_text: str) -> Optional[dict]:
    """Make sure a category has the best taxonomy we can offer. Returns it (or None)."""
    slug = category_slug(category)
    if not slug or not get_bedrock().available():
        return None
    path = taxonomy_path(category)
    if path != GENERIC_PATH:
        tax = load_taxonomy(category)
        if tax.get("learned"):
            return await _maybe_extend(slug, tax, sample_text)
        return tax  # curated file (e.g. backpack demo) — never touched
    # brand-new category => generate
    prompt = (
        f'Define the attribute taxonomy an AI shopping assistant should use to compare products '
        f'in the category "{category}".\n'
        "Pick the 8-12 attributes real buyers and reviews actually decide on for THIS category "
        "(concrete and category-specific — e.g. for earbuds: noise_cancelling, battery_life, "
        "codec_support; not vague catch-alls). For each: id (snake_case), label, one-line "
        "description, keywords = 6-12 lowercase substrings used to search evidence in page text "
        "(include common Traditional-Chinese equivalents like 續航/降噪 when obvious). "
        "Do not include a generic 'price' or 'brand_reputation' (added automatically) nor 'other'.\n"
        "Also define 4-6 buyer intent clusters (id, label, description, 2-4 related attribute ids).\n\n"
        f"Sample product page from this category:\n{sample_text[:4000]}")
    try:
        out = await bedrock.acomplete_json(
            prompt=prompt, schema=GEN_SCHEMA, max_tokens=3000,
            cache_key=cache_key_for("taxgen", slug, "v1"))
    except Exception:
        return None
    tax = _normalize(slug, out.get("attributes", []), out.get("clusters", []))
    _persist(slug, tax)
    return tax


async def _maybe_extend(slug: str, tax: dict, sample_text: str) -> dict:
    if len(tax.get("attributes", [])) >= MAX_ATTRS + 1:  # +1 for "other"
        return tax
    current = ", ".join(a["id"] for a in tax["attributes"])
    prompt = (
        f'Category "{slug}" taxonomy currently covers: {current}.\n'
        "Below is a NEW product page from this category. If (and ONLY if) it prominently "
        "advertises a decision-relevant attribute NOT covered above, return it (max 2, same "
        "format: id snake_case, label, description, keywords incl. zh-TW equivalents). "
        "If everything is already covered, return an empty list — be strict, no near-duplicates.\n\n"
        f"{sample_text[:3500]}")
    try:
        out = await bedrock.acomplete_json(
            prompt=prompt, schema=EXTEND_SCHEMA, model=get_bedrock().fast, max_tokens=1200,
            cache_key=cache_key_for("taxext", slug, current, sample_text[:2000]))
    except Exception:
        return tax
    existing = {a["id"] for a in tax["attributes"]}
    added = False
    for a in out.get("new_attributes", [])[:2]:
        aid = _aid(a.get("id", ""))
        if aid and aid not in existing and len(tax["attributes"]) < MAX_ATTRS + 1:
            other = tax["attributes"].pop()  # keep "other" last
            tax["attributes"].append({"id": aid, "label": (a.get("label") or aid)[:60],
                                      "description": (a.get("description") or "")[:200],
                                      "keywords": [str(k).lower()[:40] for k in (a.get("keywords") or [])][:12]})
            tax["attributes"].append(other)
            existing.add(aid)
            added = True
    if added:
        _persist(slug, tax)
    return tax
