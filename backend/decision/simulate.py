"""P2 — Decision Engine.

Single simulate: shopping-assistant comparison over pinned candidate product
versions (context-only: "if the page doesn't say it, it's unknown"), streamed
narrative + structured DecisionResult. Batch: cluster intents × N runs with
caching -> per-product recommendation/consideration shares (Wilson CI).
Falls back to deterministic mock decisions when Bedrock is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import math
import random
from typing import AsyncIterator, Optional

from backend import config
from backend.llm import bedrock
from backend.llm.bedrock import LLMError, cache_key_for, get_bedrock
from backend.llm.jsonutil import extract_json
from backend.llm.prompts import render_prompt
from backend.pipeline.corpus import slugify
from backend.storage import db

PROMPT_VERSION = "decision/prompts/prompt_v1"
MODEL_TAG = "decision-engine/prompt_v1"

REASON_ITEM = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "attribute": {"type": "string"}},
    "required": ["text", "attribute"],
}
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "winner": {"type": ["string", "null"]},
        "per_product": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "product_ref": {"type": "string"},
                "considered": {"type": "boolean"},
                "verdict": {"type": "string", "enum": ["recommended", "rejected", "not_considered"]},
                "rank": {"type": ["integer", "null"]},
                "reasons_for": {"type": "array", "items": REASON_ITEM},
                "reasons_against": {"type": "array", "items": REASON_ITEM},
            },
            "required": ["product_ref", "considered", "verdict", "reasons_for", "reasons_against"],
        }},
    },
    "required": ["narrative", "winner", "per_product"],
}


def _taxonomy_ids() -> list[str]:
    tax = json.loads(config.TAXONOMY_PATH.read_text(encoding="utf-8"))
    return [a["id"] for a in tax["attributes"]]


def _candidates_block(refs: list[str]) -> tuple[str, dict[str, dict]]:
    products: dict[str, dict] = {}
    lines = []
    for i, ref in enumerate(refs, 1):
        p = db.get_product_by_ref(ref)
        if not p:
            raise KeyError(f"product not found: {ref}")
        products[ref] = p
        attrs = "; ".join(f"{a['attribute_id']}={a['value']}" for a in p["attributes"]
                          if a.get("value")) or "(no structured attributes)"
        nulls = ", ".join(a["attribute_id"] for a in p["attributes"] if not a.get("value"))
        lines.append(
            f"[S{i}] {p['display_name']} (candidate_ref: {ref})\n"
            f"Known attributes: {attrs}\n"
            f"NOT stated on page: {nulls or '(none)'}\n"
            f"Page text: {p['raw_text'][:1300]}\n")
    return "\n".join(lines), products


def _base_prompt(intent: dict, refs: list[str]) -> str:
    block, _ = _candidates_block(refs)
    return render_prompt(
        PROMPT_VERSION,
        engine_persona="an impartial AI shopping assistant",
        intent_text=intent["text"],
        sources_block=block,
        style_hint="balanced and specific, comparing candidates head-to-head",
        length_hint="250-350 words",
    ) + ("\nCandidates (use these exact refs in structured output): "
         + ", ".join(refs))


def _normalize_decision(raw: dict, intent: dict, refs: list[str], model_label: str) -> dict:
    valid_attrs = set(_taxonomy_ids())

    def _reasons(lst):
        out = []
        for r in (lst or [])[:4]:
            if isinstance(r, dict) and r.get("text"):
                attr = r.get("attribute") if r.get("attribute") in valid_attrs else "other"
                out.append({"text": str(r["text"])[:300], "attribute": attr})
        return out

    winner = raw.get("winner")
    if winner not in refs:
        winner = None
    rows = {r.get("product_ref"): r for r in raw.get("per_product", []) if isinstance(r, dict)}
    per_product = []
    for ref in refs:
        r = rows.get(ref, {})
        considered = bool(r.get("considered", True))
        verdict = r.get("verdict")
        if ref == winner:
            verdict, considered = "recommended", True
        elif verdict not in ("rejected", "not_considered"):
            verdict = "rejected" if considered else "not_considered"
        per_product.append({
            "product_ref": ref, "considered": considered, "verdict": verdict,
            "rank": r.get("rank") if isinstance(r.get("rank"), int) else None,
            "reasons_for": _reasons(r.get("reasons_for")),
            "reasons_against": _reasons(r.get("reasons_against")),
        })
    if winner is None:
        recs = [p for p in per_product if p["verdict"] == "recommended"]
        if recs:
            winner = recs[0]["product_ref"]
    return {
        "decision_id": db.new_id("dec"),
        "intent": {"intent_id": intent.get("intent_id"), "text": intent["text"],
                   "cluster_id": intent.get("cluster_id", "other"),
                   "attributes": intent.get("attributes", []),
                   "language": intent.get("language", "en")},
        "candidates": refs,
        "winner": winner,
        "per_product": per_product,
        "narrative": raw.get("narrative", ""),
        "model": model_label,
        "created_at": db.now_iso(),
    }


# ---------------------------------------------------------------- mock path

def _mock_decision(intent: dict, refs: list[str], run_idx: int) -> dict:
    world_path = config.FIXTURES_DIR / "mock_world.json"
    world = json.loads(world_path.read_text(encoding="utf-8")) if world_path.exists() else {}
    rng = random.Random(f"dec:{intent['text']}:{'|'.join(refs)}:{run_idx}")
    products = {ref: db.get_product_by_ref(ref) for ref in refs}
    slug_of = {ref: slugify((p or {}).get("brand", ref)) for ref, p in products.items()}
    weights = ((world.get("clusters") or {}).get(intent.get("cluster_id", ""), {})
               .get("win_weights", {}))
    w = [max(0.05, float(weights.get(slug_of[ref], 0.25))) for ref in refs]
    winner = rng.choices(refs, weights=w, k=1)[0]
    attrs = intent.get("attributes", []) or ["price"]
    per_product, narrative = [], []
    for ref in refs:
        slug = slug_of[ref]
        name = (products[ref] or {}).get("display_name", ref)
        bank = ((world.get("reasons") or {}).get(slug) or {})
        fors = [(t, a) for a, ts in (bank.get("for") or {}).items() for t in ts]
        againsts = [(t, a) for a, ts in (bank.get("against") or {}).items() for t in ts
                    if a in attrs] or [(t, a) for a, ts in (bank.get("against") or {}).items() for t in ts]
        f = rng.choice(fors) if fors else (f"{name} covers the basics well", "other")
        g = rng.choice(againsts) if againsts else (f"{name} shows weaknesses for this use case", "other")
        if ref == winner:
            narrative.append(f"{name}: {f[0]} — the best match here.")
            per_product.append({"product_ref": ref, "considered": True, "verdict": "recommended",
                                "rank": 1, "reasons_for": [{"text": f[0], "attribute": f[1]}],
                                "reasons_against": []})
        else:
            narrative.append(f"{name}: {f[0]}. However, {g[0]}.")
            per_product.append({"product_ref": ref, "considered": True, "verdict": "rejected",
                                "rank": None, "reasons_for": [{"text": f[0], "attribute": f[1]}],
                                "reasons_against": [{"text": g[0], "attribute": g[1]}]})
    raw = {"narrative": "\n\n".join(narrative), "winner": winner, "per_product": per_product}
    return _normalize_decision(raw, intent, refs, "mock/decision-v1")


# ---------------------------------------------------------------- single

def _use_llm(mode: Optional[str]) -> bool:
    if mode == "mock":
        return False
    return get_bedrock().available()


async def run_decision(intent: dict, candidates: list[str], *, cached: bool = True,
                       run_idx: int = 0, model: Optional[str] = None,
                       mode: Optional[str] = None) -> dict:
    """Non-streaming DecisionResult (used by /simulate stream=false and batch)."""
    if not _use_llm(mode):
        d = _mock_decision(intent, candidates, run_idx)
        db.save_decision(d)
        return d
    br = get_bedrock()
    mid = model or br.smart
    ck = cache_key_for("decision", intent["text"], candidates, run_idx, PROMPT_VERSION, mid)
    prompt = _base_prompt(intent, candidates)
    raw = await bedrock.acomplete_json(
        prompt=prompt + "\nProduce the structured decision via the tool: narrative (the full "
                        "consumer-advice answer), winner (exact candidate_ref of your top pick, "
                        "or null), per_product rows for EVERY candidate with verdict, "
                        "reasons_for/against (short quotes from your narrative; attribute must be "
                        "one of: " + ", ".join(_taxonomy_ids()) + ").",
        schema=DECISION_SCHEMA, model=mid, max_tokens=3000,
        temperature=0.4 if run_idx else 0.2,
        cache_key=(ck if cached else None))
    d = _normalize_decision(raw, intent, candidates, f"{MODEL_TAG}@{mid}")
    db.save_decision(d)
    return d


async def stream_decision(intent: dict, candidates: list[str], *, cached: bool = True,
                          mode: Optional[str] = None) -> AsyncIterator[dict]:
    """Yield {'type':'token','text':..} events then {'type':'result','decision':..}.

    Narrative streams token by token; the trailing ```json block is held back
    and parsed into the structured DecisionResult.
    """
    if not _use_llm(mode):
        d = _mock_decision(intent, candidates, 0)
        db.save_decision(d)
        for chunk in [d["narrative"][i:i + 24] for i in range(0, len(d["narrative"]), 24)]:
            yield {"type": "token", "text": chunk}
            await asyncio.sleep(0.02)
        yield {"type": "result", "decision": d}
        return

    br = get_bedrock()
    mid = br.smart
    ck = cache_key_for("decision_stream", intent["text"], candidates, PROMPT_VERSION, mid)
    cached_full = db.kv_get(f"llm:{ck}") if cached else None
    prompt = _base_prompt(intent, candidates) + (
        "\nAfter your final recommendation sentence, output EXACTLY one fenced code block:\n"
        "```json\n{\"winner\": \"<candidate_ref or null>\", \"per_product\": [{\"product_ref\", "
        "\"considered\", \"verdict\": \"recommended|rejected|not_considered\", \"rank\", "
        "\"reasons_for\": [{\"text\", \"attribute\"}], \"reasons_against\": [...]}]}\n```\n"
        "attribute must be one of: " + ", ".join(_taxonomy_ids()))
    full = ""
    emitted = 0
    try:
        if cached_full is not None:
            full = cached_full
            for i in range(0, len(full.split("```")[0]), 24):
                yield {"type": "token", "text": full.split("```")[0][i:i + 24]}
                await asyncio.sleep(0.015)
        else:
            async for chunk in bedrock.astream(prompt=prompt, model=mid, max_tokens=2200,
                                               temperature=0.3):
                full += chunk
                cut = full.find("```")
                safe = len(full) if cut == -1 else cut
                if safe > emitted:
                    yield {"type": "token", "text": full[emitted:safe]}
                    emitted = safe
            if cached:
                db.kv_set(f"llm:{ck}", "decision_stream", full)
    except LLMError as e:
        yield {"type": "error", "message": str(e)}
        return
    narrative = full.split("```")[0].strip()
    try:
        parsed = extract_json(full[len(narrative):]) if "```" in full else {}
    except ValueError:
        parsed = {}
    parsed = parsed if isinstance(parsed, dict) else {}
    parsed["narrative"] = narrative
    d = _normalize_decision(parsed, intent, candidates, f"{MODEL_TAG}@{mid}")
    db.save_decision(d)
    yield {"type": "result", "decision": d}


# ---------------------------------------------------------------- batch

def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(max(0.0, (center - margin) / denom), 3),
            round(min(1.0, (center + margin) / denom), 3))


async def run_batch(cluster_id: str, candidates: list[str], *, runs: int = 3,
                    cached: bool = True, max_intents: int = 12,
                    batch_id: Optional[str] = None, mode: Optional[str] = None) -> dict:
    from backend.pipeline.intents import ensure_library_loaded
    ensure_library_loaded()
    intents = db.get_intents("library", cluster_id=cluster_id)[:max_intents]
    if not intents:
        intents = db.get_intents("library")[:max_intents]
    batch_id = batch_id or db.new_id("batch")
    runs = max(1, min(5, runs))
    db.create_batch({"batch_id": batch_id, "cluster_id": cluster_id, "candidates": candidates,
                     "runs": runs, "status": "running", "n_intents": len(intents)})
    use_llm = _use_llm(mode)
    model = get_bedrock().fast if use_llm else None
    sem = asyncio.Semaphore(4)
    decisions: list[dict] = []
    errors = 0

    async def one(it: dict, ridx: int) -> None:
        nonlocal errors
        async with sem:
            try:
                d = await run_decision(it, candidates, cached=cached, run_idx=ridx,
                                       model=model, mode=mode)
                d["batch_id"] = batch_id
                db.save_decision(d)
                decisions.append(d)
            except Exception:
                errors += 1

    await asyncio.gather(*[one(it, r) for it in intents for r in range(runs)])
    n = len(decisions)
    shares = {}
    for ref in candidates:
        rec = sum(1 for d in decisions if d["winner"] == ref)
        cons = sum(1 for d in decisions for p in d["per_product"]
                   if p["product_ref"] == ref and p["considered"])
        shares[ref] = {"recommendation_share": round(rec / n, 3) if n else 0.0,
                       "consideration_share": round(cons / n, 3) if n else 0.0,
                       "ci95_recommendation": wilson_ci(rec, n)}
    status = "completed" if n else "failed"
    db.create_batch({"batch_id": batch_id, "cluster_id": cluster_id, "candidates": candidates,
                     "runs": runs, "status": status, "n_intents": len(intents),
                     "shares": shares, "decision_ids": [d["decision_id"] for d in decisions],
                     "error": f"{errors} decision errors" if errors else None})
    out = db.get_batch(batch_id)
    out["n_decisions"] = n
    return out
