"""Stage 3 — Funnel Parsing (LLM-as-judge). The technical core.

Per response we produce one annotation:
  top_pick + per-product rows {retrieved, mentioned, considered, recommended,
  rank, verbatim reasons, loss_reasons}.
- retrieved  = brand evidence present in citations / search trace (computed
  deterministically from citations, independent of the answer prose)
- considered = substantively compared in the answer (judge)
- recommended= final/top pick (judge)
- considered & !recommended => stated loss reasons (verbatim quotes)
Mock/sim responses that carry a ground-truth annotation skip the judge.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Awaitable, Callable, Optional

from backend import config
from backend.llm import bedrock
from backend.llm.bedrock import LLMError, cache_key_for, get_bedrock
from backend.llm.prompts import render_prompt
from backend.pipeline.corpus import slugify, tokenize
from backend.storage import db

PROMPT_VERSION = "funnel_v1"

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "top_pick": {"type": ["string", "null"]},
        "top_pick_name": {"type": ["string", "null"]},
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "canonical": {"type": "string"},
                    "mentioned": {"type": "boolean"},
                    "considered": {"type": "boolean"},
                    "recommended": {"type": "boolean"},
                    "rank": {"type": ["integer", "null"]},
                    "reasons_for": {"type": "array", "items": {"type": "string"}},
                    "reasons_against": {"type": "array", "items": {"type": "string"}},
                    "loss_reasons": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "canonical", "mentioned", "considered", "recommended"],
            },
        },
    },
    "required": ["top_pick", "products"],
}


# ---------------------------------------------------------------- helpers

def brand_slug_map(run_cfg: dict) -> dict:
    """{'target': slug, 'competitors': [...], 'all': {slug: display}}"""
    target = slugify(run_cfg["brand"])
    comps = [slugify(c) for c in run_cfg.get("competitors", [])]
    disp = {target: run_cfg["brand"]}
    for c, s in zip(run_cfg.get("competitors", []), comps):
        disp[s] = c
    return {"target": target, "competitors": comps, "all": disp}


def resolve_judge_model(run_cfg: dict) -> Optional[str]:
    jm = run_cfg.get("judge_model")
    br = get_bedrock()
    if jm in (None, "", "smart"):
        return br.smart
    if jm == "fast":
        return br.fast
    return jm


def _citations_block(citations: list[dict]) -> str:
    if not citations:
        return "(no sources cited)"
    lines = []
    for i, c in enumerate(citations, 1):
        lines.append(f"[{i}] {c.get('title', '?')} — {c.get('url', '')}")
    return "\n".join(lines)


def _brand_tokens(display: str, slug: str) -> set[str]:
    return set(tokenize(display)) | {slug.replace("-", "")} | set(slug.split("-"))


def deterministic_retrieved(slug: str, display: str, citations: list[dict]) -> tuple[bool, list[str]]:
    """Does the search trace contain evidence for this brand?"""
    via: list[str] = []
    btoks = {t for t in _brand_tokens(display, slug) if len(t) > 2}
    for c in citations:
        url = (c.get("url") or "").lower()
        title_toks = set(tokenize(c.get("title") or ""))
        doc_brands = c.get("brands") or []
        hit = slug in doc_brands or any(t in url for t in btoks) or (btoks & title_toks)
        if hit:
            via.append(c.get("url") or c.get("doc_id") or "?")
    return bool(via), via


def _norm_products(raw_products: list[dict], top_pick, run_cfg: dict,
                   citations: list[dict]) -> tuple[list[dict], Optional[str]]:
    """Validate/normalize judge output; ensure roster rows exist; compute retrieved."""
    slugs = brand_slug_map(run_cfg)
    roster = [slugs["target"]] + slugs["competitors"]
    disp = slugs["all"]
    by_canon: dict[str, dict] = {}
    others: list[dict] = []
    for p in raw_products or []:
        canon = p.get("canonical") or "other"
        if canon not in roster:
            canon = "other"
        mentioned = bool(p.get("mentioned"))
        considered = bool(p.get("considered")) and mentioned
        recommended = bool(p.get("recommended")) and considered
        loss = p.get("loss_reasons") or []
        row = {
            "name": p.get("name") or disp.get(canon, canon),
            "canonical": canon,
            "is_target": canon == slugs["target"],
            "mentioned": mentioned or considered or recommended,
            "considered": considered or recommended,
            "recommended": recommended,
            "rank": p.get("rank") if isinstance(p.get("rank"), int) else None,
            "reasons_for": [str(x) for x in (p.get("reasons_for") or [])][:3],
            "reasons_against": [str(x) for x in (p.get("reasons_against") or [])][:3],
            "loss_reasons": [x if isinstance(x, dict) else {"text": str(x), "attribute": None}
                             for x in loss][:4],
        }
        if not row["considered"]:
            row["loss_reasons"] = []
        if canon == "other":
            others.append(row)
        elif canon in by_canon:  # merge duplicate rows for same canonical
            ex = by_canon[canon]
            for k in ("mentioned", "considered", "recommended"):
                ex[k] = ex[k] or row[k]
            ex["reasons_for"] = (ex["reasons_for"] + row["reasons_for"])[:3]
            ex["reasons_against"] = (ex["reasons_against"] + row["reasons_against"])[:3]
            ex["loss_reasons"] = (ex["loss_reasons"] + row["loss_reasons"])[:4]
            if ex["rank"] is None:
                ex["rank"] = row["rank"]
        else:
            by_canon[canon] = row
    # roster rows always present (retrieved-but-never-mentioned must be visible)
    for slug in roster:
        if slug not in by_canon:
            by_canon[slug] = {"name": disp.get(slug, slug), "canonical": slug,
                              "is_target": slug == slugs["target"], "mentioned": False,
                              "considered": False, "recommended": False, "rank": None,
                              "reasons_for": [], "reasons_against": [], "loss_reasons": []}
    for slug, row in by_canon.items():
        ret, via = deterministic_retrieved(slug, disp.get(slug, slug), citations)
        row["retrieved"] = ret
        row["retrieved_via"] = via[:4]
    for row in others:
        ret, via = deterministic_retrieved(slugify(row["name"]), row["name"], citations)
        row["retrieved"] = ret
        row["retrieved_via"] = via[:4]
    products = [by_canon[s] for s in roster] + others
    # normalize top_pick
    tp = top_pick if top_pick in roster else ("other" if top_pick else None)
    recommended_roster = [s for s in roster if by_canon[s]["recommended"]]
    if tp is None and recommended_roster:
        tp = recommended_roster[0]
    return products, tp


# ---------------------------------------------------------------- judging

async def judge_response(resp: dict, run_cfg: dict, judge_model: Optional[str]) -> dict:
    """Return a funnel row (dict ready for db.save_funnel)."""
    slugs = brand_slug_map(run_cfg)
    if resp.get("ground_truth"):
        gt = resp["ground_truth"]
        products, tp = _norm_products(gt.get("products", []), gt.get("top_pick"),
                                      run_cfg, resp.get("citations", []))
        return {"response_id": resp["response_id"], "run_id": resp["run_id"],
                "intent_id": resp["intent_id"], "engine": resp["engine"],
                "judge_model": "ground_truth", "prompt_version": PROMPT_VERSION,
                "top_pick": tp, "products": products, "is_ground_truth": True}

    text = resp.get("text") or ""
    canonical_ids = ", ".join(f'"{s}"' for s in [slugs["target"]] + slugs["competitors"])
    prompt = render_prompt(
        "funnel_v1",
        intent_text=resp.get("intent_text", ""),
        brand=run_cfg["brand"],
        brand_products=", ".join(run_cfg.get("brand_products", []) or ["(any)"]),
        competitors=", ".join(run_cfg.get("competitors", [])),
        canonical_ids=canonical_ids,
        response_text=text[:12000],
        citations_block=_citations_block(resp.get("citations", [])),
    )
    text_hash = hashlib.sha1(text.encode()).hexdigest()[:16]
    out = await bedrock.acomplete_json(
        prompt=prompt, schema=JUDGE_SCHEMA, model=judge_model, max_tokens=3500,
        temperature=0.0,
        cache_key=cache_key_for("funnel", text_hash, canonical_ids, PROMPT_VERSION, judge_model))
    products, tp = _norm_products(out.get("products", []), out.get("top_pick"),
                                  run_cfg, resp.get("citations", []))
    return {"response_id": resp["response_id"], "run_id": resp["run_id"],
            "intent_id": resp["intent_id"], "engine": resp["engine"],
            "judge_model": judge_model or "?", "prompt_version": PROMPT_VERSION,
            "top_pick": tp, "products": products, "is_ground_truth": False}


async def annotate_run(run_id: str, run_cfg: dict,
                       progress: Optional[Callable[[int, int, str], Awaitable[None]]] = None
                       ) -> dict:
    responses = [r for r in await asyncio.to_thread(db.get_responses, run_id)
                 if r.get("status") == "ok"]
    done_ids = {f["response_id"] for f in await asyncio.to_thread(db.get_funnel, run_id)}
    todo = [r for r in responses if r["response_id"] not in done_ids]
    judge_model = resolve_judge_model(run_cfg)
    sem = asyncio.Semaphore(config.JUDGE_CONCURRENCY)
    n_done = len(done_ids)
    total = len(responses)
    errors = 0

    async def one(resp: dict) -> None:
        nonlocal n_done, errors
        async with sem:
            try:
                row = await judge_response(resp, run_cfg, judge_model)
                await asyncio.to_thread(db.save_funnel, row)
            except LLMError as e:
                errors += 1
                if e.code in ("aws_auth", "unavailable"):
                    raise
            except Exception:
                errors += 1
            n_done += 1
            if progress and (n_done % 5 == 0 or n_done == total):
                await progress(n_done, total, f"judged {n_done}/{total}")

    await asyncio.gather(*[one(r) for r in todo])
    return {"annotated": n_done, "errors": errors, "total": total}


# ---------------------------------------------------------------- aggregation

def _empty_stat() -> dict:
    return {"n": 0, "retrieved": 0, "mentioned": 0, "considered": 0, "recommended": 0}


def _rates(s: dict) -> dict:
    n = max(1, s["n"])
    return {**s,
            "retrieved_rate": round(s["retrieved"] / n, 3),
            "mention_rate": round(s["mentioned"] / n, 3),
            "consideration_share": round(s["considered"] / n, 3),
            "recommendation_share": round(s["recommended"] / n, 3)}


def aggregate(run_id: str, run_cfg: dict) -> dict:
    rows = db.get_funnel(run_id)
    slugs = brand_slug_map(run_cfg)
    roster = [slugs["target"]] + slugs["competitors"]
    per: dict[str, dict] = {s: {"display": slugs["all"].get(s, s), "is_target": s == slugs["target"],
                                "overall": _empty_stat(), "by_engine": {}, "by_cluster": {}}
                            for s in roster}
    dropoff = {s: {"not_retrieved": 0, "retrieved_not_mentioned": 0, "mentioned_not_considered": 0,
                   "considered_not_recommended": 0, "recommended": 0} for s in roster}
    loss_attr: dict[str, dict[str, int]] = {s: {} for s in roster}
    others_count: dict[str, int] = {}
    engines, clusters = set(), set()

    for row in rows:
        engines.add(row["engine"])
        clusters.add(row["cluster_id"])
        for p in row["products"]:
            c = p.get("canonical")
            if c == "other":
                if p.get("recommended"):
                    others_count[p.get("name", "?")] = others_count.get(p.get("name", "?"), 0) + 1
                continue
            if c not in per:
                continue
            for scope_key, scope in (("overall", per[c]["overall"]),
                                     (row["engine"], per[c]["by_engine"].setdefault(row["engine"], _empty_stat())),
                                     (row["cluster_id"], per[c]["by_cluster"].setdefault(row["cluster_id"], _empty_stat()))):
                scope["n"] += 1
                scope["retrieved"] += 1 if p.get("retrieved") else 0
                scope["mentioned"] += 1 if p.get("mentioned") else 0
                scope["considered"] += 1 if p.get("considered") else 0
                scope["recommended"] += 1 if p.get("recommended") else 0
            d = dropoff[c]
            if p.get("recommended"):
                d["recommended"] += 1
            elif p.get("considered"):
                d["considered_not_recommended"] += 1
            elif p.get("mentioned"):
                d["mentioned_not_considered"] += 1
            elif p.get("retrieved"):
                d["retrieved_not_mentioned"] += 1
            else:
                d["not_retrieved"] += 1
            for lr in p.get("loss_reasons", []):
                attr = lr.get("attribute") or "unmapped"
                loss_attr[c][attr] = loss_attr[c].get(attr, 0) + 1

    for s in roster:
        per[s]["overall"] = _rates(per[s]["overall"])
        per[s]["by_engine"] = {k: _rates(v) for k, v in per[s]["by_engine"].items()}
        per[s]["by_cluster"] = {k: _rates(v) for k, v in per[s]["by_cluster"].items()}
        per[s]["loss_attributes"] = dict(sorted(loss_attr[s].items(), key=lambda kv: -kv[1]))

    return {"run_id": run_id, "n_annotated": len(rows), "engines": sorted(engines),
            "clusters": sorted(clusters), "per_product": per, "funnel_dropoff": dropoff,
            "other_recommended": dict(sorted(others_count.items(), key=lambda kv: -kv[1])[:10])}


def collect_loss_reasons(run_id: str, canonical: Optional[str] = None) -> list[dict]:
    out = []
    for row in db.get_funnel(run_id):
        for p in row["products"]:
            if canonical and p.get("canonical") != canonical:
                continue
            for lr in p.get("loss_reasons", []):
                out.append({"response_id": row["response_id"], "intent_id": row["intent_id"],
                            "engine": row["engine"], "cluster_id": row["cluster_id"],
                            "canonical": p.get("canonical"), "name": p.get("name"),
                            "text": lr.get("text"), "attribute": lr.get("attribute")})
    return out
