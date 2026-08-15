"""P3 — Diagnosis assembly (contract-v2 shape, with pipeline extensions).

Preferred source: the latest completed pipeline run for the product's brand
(funnel summary + defect report). Fallback: aggregate /simulate/batch
decisions. If neither exists, trigger a background batch set and report 202.
"""
from __future__ import annotations

import asyncio
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
    winning = sorted(((c, round(s["rec"] / max(1, s["n"]), 3)) for c, s in by_cluster.items()),
                     key=lambda kv: -kv[1])[:3]
    vs = {r: round(w / n, 3) for r, w in comp_wins.most_common(4)}
    diag = {
        "product_ref": ref, "generated_at": db.now_iso(),
        "overall": {"recommendation_share": round(rec / n, 3),
                    "consideration_share": round(cons / n, 3),
                    "n_simulations": n, "vs": vs},
        "defects": defects,
        "winning_clusters": [{"cluster_id": c, "recommendation_share": s} for c, s in winning],
        "source": {"type": "batches", "n_decisions": n},
    }
    db.save_diagnosis(ref, ref, "batches", diag)
    return diag


def _trigger_cluster_ids(product: dict) -> list[str]:
    """Clusters to simulate for diagnosis — from the product's OWN category taxonomy."""
    from backend.taxonomy import load_taxonomy
    tax = load_taxonomy(product.get("category"))
    if tax["category"] == "travel_backpack":
        return TRIGGER_CLUSTERS
    return [c["id"] for c in tax["clusters"]][:3]


async def _trigger_batches(product: dict, cluster_ids: list[str]) -> None:
    from backend.decision.simulate import run_batch
    ref = product["ref"]
    candidates = [ref] + _competitor_refs(product)
    for cid in cluster_ids:
        try:
            await run_batch(cid, candidates, runs=2, max_intents=8)
        except Exception:
            continue
    _TRIGGERED.pop(ref, None)


async def get_or_build(product_ref: str, allow_trigger: bool = True
                       ) -> tuple[Optional[dict], Optional[dict]]:
    """Return (diagnosis, pending). pending is set when a batch set was triggered."""
    product = db.get_product_by_ref(product_ref)
    if not product:
        raise KeyError(product_ref)
    run = _find_run_for_brand(product["brand"], product.get("category"))
    if run and run.get("funnel_summary") and run.get("report"):
        return diagnosis_from_run(product, run), None
    if product["ref"] in _TRIGGERED:  # trigger batches still running — no partial diagnosis
        return None, {"status": "running", "detail": "diagnosis batches running",
                      "clusters": _TRIGGERED[product["ref"]].get("clusters", [])}
    diag = diagnosis_from_batches(product)
    if diag:
        return diag, None
    cached = db.get_diagnosis(product["ref"])
    if cached:
        return cached, None
    if allow_trigger:
        if not _competitor_refs(product):
            return None, {
                "status": "needs_competitors",
                "category": product.get("category"),
                "detail": "no same-category competitor products ingested yet — POST /products "
                          "at least one competitor in this category, then retry diagnosis",
            }
        cluster_ids = _trigger_cluster_ids(product)
        if product["ref"] not in _TRIGGERED:
            _TRIGGERED[product["ref"]] = {"status": "running", "clusters": cluster_ids}
            asyncio.get_running_loop().create_task(_trigger_batches(product, cluster_ids))
        return None, {"status": "running", "detail": "diagnosis batches running",
                      "clusters": cluster_ids}
    return None, None
