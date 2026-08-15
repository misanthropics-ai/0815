"""P3 — Diagnosis assembly (contract-v2 shape, with pipeline extensions).

Preferred source: the latest completed pipeline run for the product's brand
(funnel summary + defect report). Fallback: aggregate /simulate/batch
decisions. If neither exists, trigger a background batch set and report 202.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from typing import Optional

from backend.pipeline.corpus import slugify
from backend.storage import db
from backend.taxonomy import category_slug

TRIGGER_CLUSTERS = ["comfort_carry", "budget_value", "organization_tech"]
_TRIGGERED: dict[str, dict] = {}


def _cat_slug(category: Optional[str]) -> str:
    return category_slug(category or "travel backpack")


def _find_run_for_brand(brand: str, category: Optional[str] = None) -> Optional[dict]:
    """Latest completed run for this brand IN THE SAME CATEGORY."""
    tslug = slugify(brand)
    want_cat = _cat_slug(category)
    for r in db.list_runs(limit=50):
        cfg = r["config"]
        if (r["status"] == "completed" and slugify(cfg.get("brand", "")) == tslug
                and _cat_slug(cfg.get("category")) == want_cat):
            return db.get_run(r["run_id"])
    return None


def _competitor_refs(product: dict, limit: int = 3) -> list[str]:
    """Competitors = other brands in the SAME category (legacy null = travel backpack)."""
    tslug = slugify(product["brand"])
    tcat = category_slug(product.get("category") or "travel backpack")
    pool = [p for p in db.list_products()
            if slugify(p["brand"]) != tslug
            and category_slug(p.get("category") or "travel backpack") == tcat]
    # NOTE: no cross-category fallback — a TV must never be "diagnosed" against backpacks.
    latest: dict[str, dict] = {}
    for p in pool:
        cur = latest.get(p["product_id"])
        if not cur or p["version"] > cur["version"]:
            latest[p["product_id"]] = p
    return [f"{p['product_id']}@v{p['version']}" for p in list(latest.values())[:limit]]


def diagnosis_from_run(product: dict, run: dict) -> dict:
    cfg = run["config"]
    fs = run["funnel_summary"] or {}
    report = run["report"] or {}
    tslug = slugify(product["brand"])
    per = fs.get("per_product", {})
    t = per.get(tslug, {})
    o = t.get("overall", {})

    ref_by_slug: dict[str, str] = {}
    for r in cfg.get("product_refs", []):
        p = db.get_product_by_ref(r)
        if p:
            ref_by_slug.setdefault(slugify(p["brand"]), r)
    vs = {ref_by_slug.get(s, s): p["overall"]["recommendation_share"]
          for s, p in per.items() if s != tslug}

    defects = []
    for d in report.get("defects", []):
        defects.append({
            "defect_id": d["defect_id"], "type": d["type"], "attribute_id": d["attribute_id"],
            "severity": d["severity"], "headline": d["headline"],
            "evidence": {
                "cluster_id": d["evidence"]["cluster_id"],
                "losing_share_in_cluster": d["evidence"]["losing_share_in_cluster"],
                "n_losses": d["evidence"]["n_losses"],
                "sample_rejection_reasons": d["evidence"]["sample_rejection_reasons"],
                "competitor_contrast": d["evidence"]["competitor_contrast"],
            },
            "suggested_fix": d.get("suggested_fix", ""),
            # extensions (additive to contract)
            "gap": d.get("gap"), "content_patch": d.get("content_patch"),
            "why_it_happens": d.get("why_it_happens"),
        })
    winning = sorted(((c, s.get("recommendation_share", 0))
                      for c, s in t.get("by_cluster", {}).items()),
                     key=lambda kv: -kv[1])[:3]
    diag = {
        "product_ref": product["ref"],
        "generated_at": db.now_iso(),
        "overall": {"recommendation_share": o.get("recommendation_share", 0),
                    "consideration_share": o.get("consideration_share", 0),
                    "retrieved_rate": o.get("retrieved_rate", 0),
                    "n_simulations": o.get("n", 0), "vs": vs},
        "defects": defects,
        "winning_clusters": [{"cluster_id": c, "recommendation_share": s} for c, s in winning],
        "source": {"type": "run", "run_id": run["run_id"], "engines": fs.get("engines", [])},
        "funnel_dropoff": fs.get("funnel_dropoff", {}).get(tslug, {}),
        "exec_summary": report.get("exec_summary", ""),
    }
    db.save_diagnosis(product["ref"], product["ref"], f"run:{run['run_id']}", diag)
    return diag


def diagnosis_from_batches(product: dict) -> Optional[dict]:
    ref = product["ref"]
    decisions = db.get_decisions_for_product(ref)
    if not decisions:
        return None
    n = len(decisions)
    rec = sum(1 for d in decisions if d["winner"] == ref)
    cons = sum(1 for d in decisions for p in d["per_product"]
               if p["product_ref"] == ref and p["considered"])
    by_cluster: dict[str, dict] = {}
    reasons: list[dict] = []
    comp_wins: Counter = Counter()
    for d in decisions:
        cid = (d.get("intent") or {}).get("cluster_id", "other")
        c = by_cluster.setdefault(cid, {"n": 0, "rec": 0})
        c["n"] += 1
        c["rec"] += 1 if d["winner"] == ref else 0
        if d["winner"] and d["winner"] != ref:
            comp_wins[d["winner"]] += 1
        for p in d["per_product"]:
            if p["product_ref"] == ref:
                for r in p.get("reasons_against", []):
                    reasons.append({"cluster_id": cid, **r})
    attr_counts = Counter(r["attribute"] for r in reasons)
    page_null = {a["attribute_id"] for a in product["attributes"] if not a.get("value")}
    top_comp = comp_wins.most_common(1)[0][0] if comp_wins else None
    defects = []
    for i, (attr, cnt) in enumerate(attr_counts.most_common(4)):
        cl_counts = Counter(r["cluster_id"] for r in reasons if r["attribute"] == attr)
        cid = cl_counts.most_common(1)[0][0]
        cstat = by_cluster.get(cid, {"n": 1, "rec": 0})
        defects.append({
            "defect_id": f"def_{i + 1:03d}",
            "type": "missing_attribute" if attr in page_null else "weak_evidence",
            "attribute_id": attr,
            "severity": "high" if cnt >= max(3, n // 8) else "medium",
            "headline": f"{cnt} rejections cite {attr} (cluster {cid} losing "
                        f"{int((1 - cstat['rec'] / max(1, cstat['n'])) * 100)}%)",
            "evidence": {
                "cluster_id": cid,
                "losing_share_in_cluster": round(1 - cstat["rec"] / max(1, cstat["n"]), 3),
                "n_losses": cnt,
                "sample_rejection_reasons": [r["text"] for r in reasons if r["attribute"] == attr][:3],
                "competitor_contrast": f"most losses go to {top_comp}" if top_comp else "",
            },
            "suggested_fix": f"Publish concrete {attr} evidence on the product page.",
            "gap": "information_gap" if attr in page_null else "unclear",
        })
    # --- retrievability: would lexical search over the corpus even surface this
    # product for these intents? (was missing here => frontends rendered 0%)
    retrieved_rate = None
    try:
        from backend.pipeline import corpus as corpus_mod
        corp = corpus_mod.build_corpus([ref] + _competitor_refs(product))
        own_doc = f"product:{ref}"
        uniq: dict[str, dict] = {}
        for d in decisions:
            it = d.get("intent") or {}
            key = it.get("intent_id") or it.get("text")
            if key:
                uniq[key] = it
        if uniq:
            hits = 0
            for it in uniq.values():
                kw = corpus_mod.attr_keywords(it.get("attributes", []), product.get("category"))
                top = corpus_mod.retrieve(corp, it.get("text", ""), kw, k=4,
                                          seed=it.get("intent_id") or "diag")
                if any(h["doc"].doc_id == own_doc for h in top):
                    hits += 1
            retrieved_rate = round(hits / len(uniq), 3)
    except Exception:
        retrieved_rate = None

    # --- salience: the page HAS the attribute but AI rarely cites it => make it
    # more prominent (cross-decision perception-consistency check)
    considered_ct = sum(1 for d in decisions for p in d["per_product"]
                        if p["product_ref"] == ref and p["considered"])
    mention: Counter = Counter()
    for d in decisions:
        for p in d["per_product"]:
            if p["product_ref"] == ref and p["considered"]:
                for r in p.get("reasons_for", []):
                    mention[r.get("attribute")] += 1
    page_attrs = {a["attribute_id"]: a for a in product["attributes"] if a.get("value")}
    relevant = set(attr_counts) | {a for d in decisions
                                   for a in (d.get("intent") or {}).get("attributes", [])}
    salience_added = 0
    for attr in page_attrs:
        if salience_added >= 2 or considered_ct < 5 or attr not in relevant:
            continue
        rate = mention.get(attr, 0) / considered_ct
        if rate < 0.4:
            # evidence: the minority of decisions that DID cite it (verbatim), plus a
            # "competitor gets this attribute cited X% vs your Y%" contrast line
            pos_quotes = [r.get("text") for d2 in decisions for p2 in d2["per_product"]
                          if p2["product_ref"] == ref and p2["considered"]
                          for r in p2.get("reasons_for", [])
                          if r.get("attribute") == attr and r.get("text")][:2]
            samples = ([f'when AI did see it: "{q}"' for q in pos_quotes]
                       or [f"no decision ever cited {attr} — the page information is "
                           "effectively invisible to the AI"])
            contrast = ""
            if top_comp:
                comp_cons = sum(1 for d2 in decisions for p2 in d2["per_product"]
                                if p2["product_ref"] == top_comp and p2["considered"])
                comp_mention = sum(1 for d2 in decisions for p2 in d2["per_product"]
                                   if p2["product_ref"] == top_comp and p2["considered"]
                                   for r in p2.get("reasons_for", [])
                                   if r.get("attribute") == attr)
                if comp_cons:
                    contrast = (f"{top_comp} gets {attr} cited in "
                                f"{int(comp_mention / comp_cons * 100)}% of its considered "
                                f"decisions vs your {int(rate * 100)}%")
            defects.append({
                "defect_id": f"def_{len(defects) + 1:03d}",
                "type": "weak_evidence",
                "attribute_id": attr,
                "severity": "medium",
                "headline": f"'{attr}' is on the page but AI cited it in only "
                            f"{int(rate * 100)}% of {considered_ct} considered decisions — "
                            "low salience",
                "evidence": {"cluster_id": "overall",
                             "losing_share_in_cluster": round(1 - rec / n, 3),
                             "n_losses": considered_ct - mention.get(attr, 0),
                             "sample_rejection_reasons": samples,
                             "competitor_contrast": contrast},
                "suggested_fix": f"Make {attr} impossible to miss: surface it in the title / "
                                 "first bullets / spec table and add structured data so every "
                                 "AI pass sees it.",
                "gap": "information_gap",
                "perception_rate": round(rate, 3),
            })
            salience_added += 1

    # --- image-only specs: extracted via vision => invisible to AI crawlers
    img_attrs = [a["attribute_id"] for a in product["attributes"]
                 if a.get("source") == "image" and a.get("value")]
    if img_attrs:
        defects.append({
            "defect_id": f"def_{len(defects) + 1:03d}",
            "type": "positioning",
            "attribute_id": img_attrs[0],
            "severity": "high" if len(img_attrs) >= 3 else "medium",
            "headline": f"{len(img_attrs)} spec(s) exist ONLY inside images "
                        f"({', '.join(img_attrs[:5])}) — AI crawlers cannot read "
                        "image-only content",
            "evidence": {"cluster_id": "overall",
                         "losing_share_in_cluster": round(1 - rec / n, 3),
                         "n_losses": len(img_attrs),
                         "sample_rejection_reasons": [
                             f"'{a}' was recovered by our vision pass from a product image — "
                             "text-only AI crawlers see nothing" for a in img_attrs[:3]],
                         "competitor_contrast": ""},
            "suggested_fix": "Duplicate every image-only spec into page TEXT: spec table rows, "
                             "first-screen bullets and schema.org properties. Images are "
                             "invisible to most AI retrieval.",
            "gap": "information_gap",
            "image_only": True,
        })

    # order by severity (high → medium → low), then impact; renumber ids to match
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    defects.sort(key=lambda d: (sev_rank.get(d["severity"], 3), -d["evidence"]["n_losses"]))
    for i, d in enumerate(defects):
        d["defect_id"] = f"def_{i + 1:03d}"
    # every defect ships non-empty commentary even before/without LLM enrichment
    for d in defects:
        if not d.get("why_it_happens"):
            d["why_it_happens"] = d["headline"]
        if not d.get("content_patch"):
            d["content_patch"] = (f"Add a prominent '{d['attribute_id']}' section near the top "
                                  "of the page: state the concrete facts with numbers and proof "
                                  "(spec-table row + first-screen bullet + schema.org property).")
        d.setdefault("enriched", False)

    winning = sorted(((c, round(s["rec"] / max(1, s["n"]), 3)) for c, s in by_cluster.items()),
                     key=lambda kv: -kv[1])[:3]
    vs = {r: round(w / n, 3) for r, w in comp_wins.most_common(4)}
    diag = {
        "product_ref": ref, "generated_at": db.now_iso(),
        "overall": {"recommendation_share": round(rec / n, 3),
                    "consideration_share": round(cons / n, 3),
                    "retrieved_rate": retrieved_rate,
                    "n_simulations": n, "vs": vs},
        "defects": defects,
        "winning_clusters": [{"cluster_id": c, "recommendation_share": s} for c, s in winning],
        "source": {"type": "batches", "n_decisions": n},
    }
    db.save_diagnosis(ref, ref, "batches", diag)
    return diag


ENRICH_SCHEMA = {
    "type": "object",
    "properties": {"defect_enrichments": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "defect_id": {"type": "string"},
            "why_it_happens": {"type": "string"},
            "suggested_fix": {"type": "string"},
            "content_patch": {"type": "string"},
        },
        "required": ["defect_id", "suggested_fix", "content_patch"],
    }}},
    "required": ["defect_enrichments"],
}


async def _enrich_batch_defects(product: dict, diag: dict) -> dict:
    """Turn template defects into vendor-actionable page changes (LLM, cached)."""
    from backend.llm import bedrock as llm
    from backend.llm.bedrock import cache_key_for, get_bedrock
    defects = diag.get("defects") or []
    if not defects or not get_bedrock().available():
        return diag
    if all(d.get("enriched") for d in defects):
        return diag  # already enriched with tailored patches
    fp = cache_key_for("diagenrich", product["ref"],
                       [(d["defect_id"], d["headline"]) for d in defects], "v1")
    compact = [
        {"defect_id": d["defect_id"], "type": d["type"], "attribute": d["attribute_id"],
         "headline": d["headline"], "gap": d.get("gap"),
         "samples": d["evidence"]["sample_rejection_reasons"][:2]}
        for d in defects]
    prompt = (
        f"Brand: {product['brand']} — {product['display_name']} "
        f"(category: {product.get('category')}).\n"
        "Current product page text:\n" + (product.get("raw_text") or "")[:2500] + "\n\n"
        "Diagnosis defects (from AI shopping-assistant simulations):\n"
        + json.dumps(compact, ensure_ascii=False) + "\n\n"
        "For EVERY defect_id return: why_it_happens (1-2 sentences grounded in the page "
        "text and samples), suggested_fix (the concrete page change: what to add/move and "
        "WHERE — title, first bullets, spec table, FAQ, schema.org markup), content_patch "
        "(ready-to-paste copy for that fix written for THIS product; a JSON-LD snippet when "
        "structured data fits). Never invent product facts the page doesn't state — for "
        "missing attributes write the patch as a clearly marked [FILL IN: ...] template "
        "the vendor completes.")
    try:
        enrich = await llm.acomplete_json(prompt=prompt, schema=ENRICH_SCHEMA,
                                          max_tokens=3200, cache_key=fp)
    except Exception:
        return diag
    by_id = {e.get("defect_id"): e for e in (enrich or {}).get("defect_enrichments", [])}
    for d in defects:
        e = by_id.get(d["defect_id"])
        if e:
            d["why_it_happens"] = e.get("why_it_happens", d.get("why_it_happens", ""))
            d["suggested_fix"] = e.get("suggested_fix") or d["suggested_fix"]
            d["content_patch"] = e.get("content_patch") or d["content_patch"]
        d["enriched"] = True  # one successful pass covers the defect set
    db.save_diagnosis(product["ref"], product["ref"], "batches", diag)
    return diag


def _trigger_cluster_ids(product: dict) -> list[str]:
    """Clusters to simulate for diagnosis — from the product's OWN category taxonomy."""
    from backend.taxonomy import load_taxonomy
    tax = load_taxonomy(product.get("category"))
    if tax["category"] == "travel_backpack":
        return TRIGGER_CLUSTERS
    return [c["id"] for c in tax["clusters"]][:3]


async def _trigger_batches(product: dict, cluster_ids: list[str],
                           batch_ids: dict[str, str],
                           runs: int = 1, max_intents: int = 6) -> None:
    import time as _time

    from backend.decision.simulate import run_batch
    from backend.loglib import log
    ref = product["ref"]
    candidates = [ref] + _competitor_refs(product)
    t0 = _time.time()
    log("diagnosis.trigger", ref=ref, clusters=cluster_ids, n_candidates=len(candidates))
    personas = product.get("personas") or None
    results = await asyncio.gather(  # batches run in PARALLEL (was sequential)
        *[run_batch(cid, candidates, runs=runs, max_intents=max_intents,
                    batch_id=batch_ids[cid], personas=personas)
          for cid in cluster_ids],
        return_exceptions=True)
    total = 0
    for cid, res in zip(cluster_ids, results, strict=False):
        if isinstance(res, BaseException):
            log("diagnosis.batch_error", ref=ref, cluster=cid, error=str(res)[:200])
        else:
            total += len(res.get("decision_ids") or [])
    log("diagnosis.batches_done", ref=ref, ms=int((_time.time() - t0) * 1000),
        n_decisions=total)
    if total == 0:
        # keep the entry in FAILED state so polling surfaces the problem instead of
        # silently re-triggering forever; clear with ?retry=true
        _TRIGGERED[ref] = {"status": "failed", "clusters": cluster_ids, "batch_ids": batch_ids,
                           "detail": "all diagnosis batches produced 0 decisions — check GET "
                                     "/logs for bedrock/batch errors, then retry with "
                                     "GET /products/{ref}/diagnosis?retry=true"}
    else:
        _TRIGGERED.pop(ref, None)


def _trigger_state_payload(ref: str) -> dict:
    info = _TRIGGERED.get(ref) or {}
    batches = []
    done_sum, exp_sum = 0, 0
    for cid, bid in (info.get("batch_ids") or {}).items():
        b = db.get_batch(bid)
        done = len(db.get_decisions_by_batch(bid))
        expected = (b["n_intents"] * b["runs"]) if b and b.get("n_intents") else None
        batches.append({"cluster_id": cid, "batch_id": bid,
                        "status": (b or {}).get("status") or "pending",
                        "decisions_done": done, "decisions_expected": expected})
        done_sum += done
        exp_sum += expected or 0
    return {"status": info.get("status", "running"),
            "detail": info.get("detail", "diagnosis batches running"),
            "clusters": info.get("clusters", []),
            "progress": {"decisions_done": done_sum,
                         "decisions_expected": exp_sum or None,
                         "batches": batches}}


async def get_or_build(product_ref: str, allow_trigger: bool = True,
                       force_retry: bool = False, deadline_s: Optional[float] = None,
                       min_decisions: Optional[int] = None,
                       depth: str = "standard") -> tuple[Optional[dict], Optional[dict]]:
    """Return (diagnosis, pending). pending is set when a batch set was triggered."""
    product = db.get_product_by_ref(product_ref)
    if not product:
        raise KeyError(product_ref)
    if force_retry:  # full fresh diagnosis: clear failed state AND old batch decisions
        _TRIGGERED.pop(product["ref"], None)
        db.delete_batch_decisions_for_product(product["ref"])
        db.delete_diagnosis(product["ref"])  # stale saved diagnosis must not short-circuit
    run = _find_run_for_brand(product["brand"], product.get("category"))
    if run and run.get("funnel_summary") and run.get("report"):
        return diagnosis_from_run(product, run), None
    if product["ref"] in _TRIGGERED:
        info = _TRIGGERED[product["ref"]]
        if info.get("status") == "running" and (deadline_s or min_decisions):
            import time as _time
            done = sum(len(db.get_decisions_by_batch(b))
                       for b in (info.get("batch_ids") or {}).values())
            elapsed = _time.time() - info.get("started_at", _time.time())
            if (min_decisions and done >= min_decisions) or \
                    (deadline_s and elapsed >= deadline_s and done >= 3):
                partial = diagnosis_from_batches(product)
                if partial:  # batches keep running; later GETs return fuller data
                    partial["partial"] = True
                    partial["progress"] = _trigger_state_payload(product["ref"]).get("progress")
                    return partial, None
        return None, _trigger_state_payload(product["ref"])
    diag = diagnosis_from_batches(product)
    if diag:
        return await _enrich_batch_defects(product, diag), None
    cached = db.get_diagnosis(product["ref"])
    if cached:
        return cached, None
    if allow_trigger:
        if not _competitor_refs(product):
            return None, {
                "status": "needs_competitors",
                "category": product.get("category"),
                "detail": "diagnosis compares brands: ingest at least one product of the SAME "
                          "category from a DIFFERENT brand (POST /products), then retry. If a "
                          "competitor exists but its category string differs, fix it with "
                          "PATCH /products/{id} {\"category\": \"...\"}.",
            }
        cluster_ids = _trigger_cluster_ids(product)
        if product["ref"] not in _TRIGGERED:
            import time as _time
            runs, max_intents = {"quick": (1, 4), "standard": (1, 6),
                                 "deep": (2, 8)}.get(depth, (1, 6))
            batch_ids = {cid: db.new_id("batch") for cid in cluster_ids}
            _TRIGGERED[product["ref"]] = {"status": "running", "clusters": cluster_ids,
                                          "batch_ids": batch_ids,
                                          "started_at": _time.time(), "depth": depth}
            asyncio.get_running_loop().create_task(
                _trigger_batches(product, cluster_ids, batch_ids,
                                 runs=runs, max_intents=max_intents))
        return None, _trigger_state_payload(product["ref"])
    return None, None
