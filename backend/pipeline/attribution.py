"""Stage 4 — Attribute & Evidence Attribution.

1) Map every stated loss reason onto the attribute taxonomy
   (keyword fallback first, LLM batch for the rest).
2) Evidence audit: per attribute, compare evidence density in each brand's
   retrievable content (product page + third-party corpus docs), then classify
   the gap: potential information gap vs product gap (LLM-assisted, rule fallback).
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from typing import Optional

from backend import config
from backend.llm import bedrock
from backend.llm.bedrock import cache_key_for, get_bedrock
from backend.llm.prompts import render_prompt
from backend.pipeline import corpus as corpus_mod
from backend.pipeline.funnel import brand_slug_map, collect_loss_reasons
from backend.storage import db

PROMPT_VERSION = "attribution_v1"

MAP_SCHEMA = {
    "type": "object",
    "properties": {"mappings": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "attribute": {"type": "string"}},
        "required": ["id", "attribute"]}}},
    "required": ["mappings"],
}

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"classifications": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "attribute": {"type": "string"},
            "classification": {"type": "string",
                               "enum": ["information_gap", "product_gap", "mixed", "unclear"]},
            "rationale": {"type": "string"}},
        "required": ["attribute", "classification", "rationale"]}}},
    "required": ["classifications"],
}


@lru_cache(maxsize=1)
def _tax() -> dict:
    return json.loads(config.TAXONOMY_PATH.read_text(encoding="utf-8"))


def _attr_keywords() -> dict[str, list[str]]:
    return {a["id"]: [k.lower() for k in a.get("keywords", [])] for a in _tax()["attributes"]}


def keyword_map_reason(text: str) -> Optional[str]:
    t = (text or "").lower()
    best, best_hits = None, 0
    for attr, kws in _attr_keywords().items():
        hits = sum(1 for k in kws if k and k in t)
        if hits > best_hits:
            best, best_hits = attr, hits
    return best


# ---------------------------------------------------------------- 4.1 loss reason mapping

async def map_loss_reasons(run_id: str, run_cfg: dict) -> dict:
    rows = db.get_funnel(run_id)
    pending: list[tuple[str, int, int, str]] = []  # (response_id, product_idx, lr_idx, text)
    for row in rows:
        for pi, p in enumerate(row["products"]):
            for li, lr in enumerate(p.get("loss_reasons", [])):
                if not lr.get("attribute"):
                    kw = keyword_map_reason(lr.get("text", ""))
                    if kw:
                        lr["attribute"] = kw
                        lr["attribute_source"] = "keyword"
                    else:
                        pending.append((row["response_id"], pi, li, lr.get("text", "")))

    llm_mapped = 0
    if pending and run_cfg.get("mode") != "mock" and get_bedrock().available():
        tax_block = "\n".join(f'- {a["id"]}: {a["label"]} — {a["description"]}'
                              for a in _tax()["attributes"])
        valid = {a["id"] for a in _tax()["attributes"]}
        by_row = {(r, pi, li): txt for r, pi, li, txt in pending}
        items = [{"id": f"{r}|{pi}|{li}", "text": txt} for (r, pi, li), txt in by_row.items()]
        results: dict[str, str] = {}
        for i in range(0, len(items), 40):
            chunk = items[i:i + 40]
            prompt = render_prompt("attribution_v1", taxonomy_block=tax_block,
                                   reasons_block=json.dumps(chunk, ensure_ascii=False))
            try:
                out = await bedrock.acomplete_json(
                    prompt=prompt, schema=MAP_SCHEMA, model=get_bedrock().fast, max_tokens=3000,
                    cache_key=cache_key_for("attrmap", [c["text"] for c in chunk], PROMPT_VERSION))
                for m in out.get("mappings", []):
                    if m.get("attribute") in valid:
                        results[m["id"]] = m["attribute"]
            except Exception:
                continue
        lookup = {}
        for row in rows:
            lookup[row["response_id"]] = row
        for key, attr in results.items():
            rid, pi, li = key.split("|")
            row = lookup.get(rid)
            try:
                lr = row["products"][int(pi)]["loss_reasons"][int(li)]
                lr["attribute"] = attr
                lr["attribute_source"] = "llm"
                llm_mapped += 1
            except (KeyError, IndexError, TypeError):
                continue
    # unresolved -> other
    unresolved = 0
    for row in rows:
        for p in row["products"]:
            for lr in p.get("loss_reasons", []):
                if not lr.get("attribute"):
                    lr["attribute"] = "other"
                    lr["attribute_source"] = "fallback"
                    unresolved += 1
        db.save_funnel(row)
    return {"total_reasons": sum(len(p.get("loss_reasons", [])) for row in rows for p in row["products"]),
            "llm_mapped": llm_mapped, "unresolved_to_other": unresolved}


# ---------------------------------------------------------------- 4.2 evidence audit

def _sentences_with_keywords(text: str, kws: list[str], limit: int = 2) -> list[str]:
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", text or ""):
        s_low = sent.lower()
        if any(k in s_low for k in kws if k):
            out.append(sent.strip()[:220])
            if len(out) >= limit:
                break
    return out


def _evidence_score(docs: list, kws: list[str]) -> tuple[float, int, list[str]]:
    """(score 0..3, raw_hits, sample snippets) across a brand's docs."""
    total = 0.0
    raw = 0
    snippets: list[str] = []
    for d in docs:
        text_low = (d.text or "").lower()
        hits = sum(text_low.count(k) for k in kws if k)
        raw += hits
        weight = 1.0 if d.kind == "product_page" else 0.6
        total += weight * hits
        if hits and len(snippets) < 3:
            snippets.extend(_sentences_with_keywords(d.text, kws, 2 - min(len(snippets), 2)))
    score = min(3.0, round(math.log2(1 + total), 2))
    return score, raw, snippets[:3]


def _page_attr_map(product: Optional[dict]) -> dict[str, dict]:
    if not product:
        return {}
    return {a["attribute_id"]: a for a in product.get("attributes", [])}


async def evidence_audit(run_id: str, run_cfg: dict, funnel_summary: dict) -> dict:
    slugs = brand_slug_map(run_cfg)
    target = slugs["target"]
    roster = [target] + slugs["competitors"]
    corp = corpus_mod.build_corpus(run_cfg.get("product_refs", []))
    kwmap = _attr_keywords()

    # attribute set: top loss attrs for target + target-page nulls that got loss mentions
    loss_attrs = funnel_summary["per_product"].get(target, {}).get("loss_attributes", {})
    attrs = [a for a in loss_attrs if a not in ("other", "unmapped")][:6]
    target_product = next((db.get_product_by_ref(r) for r in run_cfg.get("product_refs", [])
                           if corpus_mod.slugify((db.get_product_by_ref(r) or {}).get("brand", "")) == target), None)
    page_map = _page_attr_map(target_product)
    for aid, a in page_map.items():
        if a.get("value") is None and aid not in attrs and loss_attrs.get(aid):
            attrs.append(aid)
    attrs = attrs[:8]

    result: dict[str, dict] = {}
    for attr in attrs:
        kws = kwmap.get(attr, [])
        brands_block: dict[str, dict] = {}
        for slug in roster:
            docs = corp.docs_for_brand(slug)
            score, raw, snips = _evidence_score(docs, kws)
            page_score, _, _ = _evidence_score([d for d in docs if d.kind == "product_page"], kws)
            ext_score, _, _ = _evidence_score([d for d in docs if d.kind != "product_page"], kws)
            brands_block[slug] = {"score": score, "page_score": page_score,
                                  "ext_score": ext_score, "raw_hits": raw,
                                  "snippets": snips, "n_docs": len(docs)}
        t_page = page_map.get(attr, {})
        t_page_null = t_page.get("value") is None if t_page else True
        best_comp = max((brands_block[s]["score"] for s in slugs["competitors"]), default=0.0)
        t_score = brands_block[target]["score"]
        # rule-based fallback classification (the brand's OWN page is what matters
        # for an information gap; third-party chatter can be negative evidence)
        if (t_page_null or brands_block[target]["page_score"] == 0) and best_comp > 0:
            rule = "information_gap"
        elif t_score > 0 and best_comp > t_score * 1.8:
            rule = "information_gap"
        elif best_comp > t_score:
            rule = "mixed"
        else:
            rule = "unclear"
        result[attr] = {
            "brands": brands_block,
            "target_page_value": t_page.get("value"),
            "target_page_null": t_page.get("value") is None if t_page else True,
            "loss_mentions": loss_attrs.get(attr, 0),
            "evidence_gap": round(best_comp - t_score, 2),
            "classification": rule,
            "classification_source": "rule",
            "rationale": "",
        }

    # LLM-assisted classification
    if result and run_cfg.get("mode") != "mock" and get_bedrock().available():
        losses = collect_loss_reasons(run_id, canonical=target)
        payload = []
        for attr, r in result.items():
            samples = [l["text"] for l in losses if l["attribute"] == attr][:3]
            best_slug = max(slugs["competitors"], key=lambda s: r["brands"][s]["score"], default=None)
            payload.append({
                "attribute": attr,
                "stated_loss_reasons": samples,
                "target_page_value": r["target_page_value"],
                "target_evidence_snippets": r["brands"][target]["snippets"],
                "best_competitor": best_slug,
                "competitor_evidence_snippets": r["brands"][best_slug]["snippets"] if best_slug else [],
            })
        prompt = (
            f"Brand under analysis: {run_cfg['brand']} ({run_cfg.get('category')}).\n"
            "For each attribute below, decide whether the brand's losses on it look like an "
            "INFORMATION gap (product likely fine or unknown, but the brand's retrievable content "
            "shows little/no evidence while competitors' content does — fixable by publishing "
            "evidence) or a PRODUCT gap (competitor content demonstrates a real feature/spec "
            "advantage the brand's product genuinely lacks), 'mixed' if both, 'unclear' if data "
            "is insufficient. Base ONLY on the provided data. One-sentence rationale each.\n\n"
            + json.dumps(payload, ensure_ascii=False))
        try:
            out = await bedrock.acomplete_json(
                prompt=prompt, schema=CLASSIFY_SCHEMA, max_tokens=2500,
                cache_key=cache_key_for("gapclass", run_id, sorted(result.keys()), PROMPT_VERSION))
            for c in out.get("classifications", []):
                a = c.get("attribute")
                if a in result:
                    result[a]["classification"] = c["classification"]
                    result[a]["classification_source"] = "llm"
                    result[a]["rationale"] = c.get("rationale", "")
        except Exception:
            pass

    return {"attributes": result, "corpus_hash": corp.hash,
            "n_docs": len(corp.docs), "target": target}
