"""Stage 5 — Recommendation Engine.

Deterministic aggregation builds defect skeletons (types per contract:
missing_attribute / weak_evidence / losing_cluster / positioning, plus a
`gap` classification from the evidence audit). Claude enriches each defect
with headline / why / fix / ready-to-paste content patch and writes the
exec summary; a template fallback keeps mock mode fully offline.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Optional

from backend.llm import bedrock
from backend.llm.bedrock import cache_key_for, get_bedrock
from backend.llm.prompts import render_prompt
from backend.pipeline.funnel import aggregate, brand_slug_map, collect_loss_reasons
from backend.storage import db

PROMPT_VERSION = "recommend_v1"

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "exec_summary": {"type": "string"},
        "quick_wins": {"type": "array", "items": {"type": "string"}},
        "defect_enrichments": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "defect_id": {"type": "string"},
                "headline": {"type": "string"},
                "why_it_happens": {"type": "string"},
                "suggested_fix": {"type": "string"},
                "content_patch": {"type": "string"},
            },
            "required": ["defect_id", "headline", "suggested_fix"],
        }},
    },
    "required": ["exec_summary", "quick_wins", "defect_enrichments"],
}


def _target_product(run_cfg: dict, target_slug: str) -> Optional[dict]:
    from backend.pipeline.corpus import slugify
    for ref in run_cfg.get("product_refs", []):
        p = db.get_product_by_ref(ref)
        if p and slugify(p.get("brand", "")) == target_slug:
            return p
    return None


def build_defect_skeletons(run_cfg: dict, funnel_summary: dict, evidence: dict,
                           losses: list[dict]) -> list[dict]:
    slugs = brand_slug_map(run_cfg)
    target = slugs["target"]
    tstats = funnel_summary["per_product"].get(target, {})
    by_cluster = tstats.get("by_cluster", {})
    overall = tstats.get("overall", {})
    dropoff = funnel_summary.get("funnel_dropoff", {}).get(target, {})
    n_total = max(1, overall.get("n", 1))

    product = _target_product(run_cfg, target)
    page_map = {a["attribute_id"]: a for a in (product or {}).get("attributes", [])}

    loss_by_attr: dict[str, list[dict]] = {}
    for l in losses:
        loss_by_attr.setdefault(l["attribute"] or "other", []).append(l)

    defects: list[dict] = []

    # A) attribute-level defects from evidence audit
    for attr, ev in (evidence.get("attributes") or {}).items():
        lset = loss_by_attr.get(attr, [])
        n_losses = len(lset) or ev.get("loss_mentions", 0)
        if n_losses < 1:
            continue
        cluster_counts = Counter(l["cluster_id"] for l in lset)
        cluster_id = cluster_counts.most_common(1)[0][0] if cluster_counts else \
            (list(by_cluster.keys())[0] if by_cluster else "overall")
        cstat = by_cluster.get(cluster_id, overall)
        losing_share = round(1 - cstat.get("recommendation_share", 0), 3)
        page_entry = page_map.get(attr, {})
        page_null = page_entry.get("value") is None if page_entry else True
        dtype = "missing_attribute" if page_null else "weak_evidence"
        best_comp, best_score, contrast = None, -1.0, ""
        for s in slugs["competitors"]:
            sc = ev["brands"].get(s, {}).get("score", 0)
            if sc > best_score:
                best_comp, best_score = s, sc
        if best_comp:
            snips = ev["brands"][best_comp].get("snippets") or []
            disp = slugs["all"].get(best_comp, best_comp)
            contrast = f"{disp}: {snips[0]}" if snips else f"{disp} shows stronger {attr} evidence (score {best_score} vs {ev['brands'][target]['score']})"
        impact = round(n_losses * (0.4 + losing_share), 2)
        defects.append({
            "type": dtype, "attribute_id": attr, "cluster_id": cluster_id,
            "losing_share_in_cluster": losing_share, "n_losses": n_losses,
            "sample_rejection_reasons": [l["text"] for l in lset[:3]] or
                                        [f"(evidence audit: no retrievable {attr} evidence)"],
            "competitor_contrast": contrast,
            "gap": ev.get("classification", "unclear"),
            "gap_rationale": ev.get("rationale", ""),
            "clusters": [c for c, _ in cluster_counts.most_common(3)] or [cluster_id],
            "impact": impact,
        })

    covered_clusters = {d["cluster_id"] for d in defects}
    # B) losing clusters not already explained
    for cid, cs in by_cluster.items():
        if cs.get("n", 0) >= 5 and cs.get("recommendation_share", 0) <= 0.15 and cid not in covered_clusters:
            lset = [l for l in losses if l["cluster_id"] == cid]
            top_attr = Counter(l["attribute"] for l in lset).most_common(1)
            attr = top_attr[0][0] if top_attr else "other"
            defects.append({
                "type": "losing_cluster", "attribute_id": attr, "cluster_id": cid,
                "losing_share_in_cluster": round(1 - cs.get("recommendation_share", 0), 3),
                "n_losses": len(lset) or cs.get("considered", 0),
                "sample_rejection_reasons": [l["text"] for l in lset[:3]] or
                                            [f"cluster {cid} lost "
                                             f"{int((1 - cs.get('recommendation_share', 0)) * 100)}% "
                                             "of decisions (no attribute-mapped quotes)"],
                "competitor_contrast": "", "gap": "unclear", "gap_rationale": "",
                "clusters": [cid], "impact": round(cs.get("n", 0) * (1 - cs.get("recommendation_share", 0)), 2),
            })

    # C) visibility / retrieval defect
    not_retrieved = dropoff.get("not_retrieved", 0)
    if not_retrieved / n_total >= 0.15:
        worst = min(by_cluster.items(), key=lambda kv: kv[1].get("retrieved_rate", 1),
                    default=(None, {}))
        defects.append({
            "type": "positioning", "attribute_id": "brand_reputation",
            "cluster_id": worst[0] or "overall",
            "losing_share_in_cluster": round(1 - (worst[1].get("retrieved_rate", 0) if worst[0] else 0), 3),
            "n_losses": not_retrieved,
            "sample_rejection_reasons": [f"brand absent from retrieved sources in {not_retrieved}/{n_total} answers"],
            "competitor_contrast": "",
            "gap": "information_gap", "gap_rationale": "brand content not retrieved for these intents",
            "clusters": [worst[0]] if worst[0] else [], "impact": round(not_retrieved * 0.8, 2),
        })

    defects.sort(key=lambda d: -d["impact"])
    defects = defects[:6]
    top_impact = defects[0]["impact"] if defects else 1
    for i, d in enumerate(defects):
        d["defect_id"] = f"def_{i + 1:03d}"
        d["severity"] = "high" if d["impact"] >= 0.6 * top_impact else \
                        ("medium" if d["impact"] >= 0.25 * top_impact else "low")
    return defects


def _fallback_enrich(defects: list[dict], run_cfg: dict, funnel_summary: dict) -> dict:
    target = brand_slug_map(run_cfg)["target"]
    o = funnel_summary["per_product"].get(target, {}).get("overall", {})
    enr = []
    for d in defects:
        attr = d["attribute_id"]
        pct = int(d["losing_share_in_cluster"] * 100)
        head = {
            "missing_attribute": f"No {attr} information on the page — losing {pct}% of {d['cluster_id']} comparisons",
            "weak_evidence": f"Weak {attr} evidence vs competitors — {d['n_losses']} stated losses",
            "losing_cluster": f"Cluster {d['cluster_id']} nearly all lost ({pct}% losing share)",
            "positioning": f"Brand missing from retrieved sources in {d['n_losses']} answers",
        }[d["type"]]
        fix = {
            "missing_attribute": f"Publish concrete {attr} specs on the product page (structured section + schema.org attributes).",
            "weak_evidence": f"Strengthen {attr} evidence: specifics, numbers, FAQ entry, third-party quotes.",
            "losing_cluster": f"Create use-case content targeting {d['cluster_id']} intents and close the {attr} evidence gap.",
            "positioning": "Improve discoverability: product feed/schema markup, presence in roundups and community threads.",
        }[d["type"]]
        enr.append({"defect_id": d["defect_id"], "headline": head,
                    "why_it_happens": (d["sample_rejection_reasons"][0]
                                       if d["sample_rejection_reasons"] else ""),
                    "suggested_fix": fix,
                    "content_patch": f"[{attr}] Add to product page: state the {attr} facts explicitly "
                                     f"(what, numbers, proof). See defect evidence for the competitor bar."})
    return {
        "exec_summary": (f"{run_cfg['brand']} is recommended in {int(o.get('recommendation_share', 0) * 100)}% "
                         f"of {o.get('n', 0)} simulated answers and considered in "
                         f"{int(o.get('consideration_share', 0) * 100)}%. The defect list identifies where the "
                         "losses concentrate and which ones look like information gaps rather than product gaps."),
        "quick_wins": [f"Fix {d['defect_id']} ({d['attribute_id']}): {d['n_losses']} stated losses"
                       for d in defects[:3]],
        "defect_enrichments": enr,
    }


def render_markdown(report: dict) -> str:
    lines = [f"# AI Recommendation Diagnosis — {report['brand']}",
             f"_run {report['run_id']} · {report['n_responses']} answers · engines: {', '.join(report['engines'])}_",
             "", "## Executive summary", report["exec_summary"], "", "## Funnel"]
    for slug, p in report["funnel"].items():
        o = p["overall"]
        tag = " (target)" if p.get("is_target") else ""
        lines.append(f"- **{p['display']}**{tag}: retrieved {int(o['retrieved_rate']*100)}% → "
                     f"considered {int(o['consideration_share']*100)}% → recommended "
                     f"{int(o['recommendation_share']*100)}% (n={o['n']})")
    lines += ["", "## Quick wins"]
    lines += [f"1. {q}" if i == 0 else f"{i + 1}. {q}" for i, q in enumerate(report["quick_wins"])]
    lines += ["", "## Defects"]
    for d in report["defects"]:
        lines += [f"### {d['defect_id']} · {d['headline']}",
                  f"- type: `{d['type']}` · attribute: `{d['attribute_id']}` · severity: **{d['severity']}** · gap: **{d['gap']}**",
                  f"- cluster: {d['evidence']['cluster_id']} (losing share {int(d['evidence']['losing_share_in_cluster']*100)}%, n_losses {d['evidence']['n_losses']})"]
        if d["evidence"]["sample_rejection_reasons"]:
            lines.append(f"- AI's stated reasons: " + " | ".join(f"“{r}”" for r in d["evidence"]["sample_rejection_reasons"][:2]))
        if d["evidence"]["competitor_contrast"]:
            lines.append(f"- competitor contrast: {d['evidence']['competitor_contrast']}")
        if d.get("why_it_happens"):
            lines.append(f"- why: {d['why_it_happens']}")
        lines += [f"- **fix**: {d['suggested_fix']}"]
        if d.get("content_patch"):
            lines += ["", "```", d["content_patch"], "```", ""]
    return "\n".join(lines)


async def build_report(run_id: str, run_cfg: dict, funnel_summary: Optional[dict] = None,
                       evidence: Optional[dict] = None) -> dict:
    funnel_summary = funnel_summary or aggregate(run_id, run_cfg)
    run = db.get_run(run_id) or {}
    evidence = evidence or run.get("evidence") or {"attributes": {}}
    slugs = brand_slug_map(run_cfg)
    target = slugs["target"]
    losses = collect_loss_reasons(run_id, canonical=target)
    defects = build_defect_skeletons(run_cfg, funnel_summary, evidence, losses)

    enrich = None
    if run_cfg.get("mode") != "mock" and get_bedrock().available():
        tstats = funnel_summary["per_product"].get(target, {})
        loss_lines = []
        for attr, n in list(tstats.get("loss_attributes", {}).items())[:6]:
            samples = [l["text"] for l in losses if l["attribute"] == attr][:2]
            loss_lines.append(f"- {attr}: {n} stated losses; e.g. " + " | ".join(f'"{s}"' for s in samples))
        ev_lines = []
        for attr, ev in (evidence.get("attributes") or {}).items():
            row = ", ".join(f"{s}:{ev['brands'][s]['score']}" for s in ev.get("brands", {}))
            ev_lines.append(f"- {attr}: {row} | page_value={ev.get('target_page_value')} | class={ev.get('classification')}")
        funnel_lines = []
        for slug, p in funnel_summary["per_product"].items():
            o = p["overall"]
            funnel_lines.append(f"- {p['display']}{' (TARGET)' if p['is_target'] else ''}: "
                                f"retrieved {o['retrieved_rate']}, considered {o['consideration_share']}, "
                                f"recommended {o['recommendation_share']} (n={o['n']})")
        prompt = render_prompt(
            "recommend_v1", brand=run_cfg["brand"], category=run_cfg.get("category", ""),
            n_responses=funnel_summary["n_annotated"],
            n_intents=len({l['intent_id'] for l in losses}) or funnel_summary["n_annotated"],
            engines=", ".join(funnel_summary["engines"]),
            funnel_block="\n".join(funnel_lines),
            losses_block="\n".join(loss_lines) or "(none)",
            evidence_block="\n".join(ev_lines) or "(none)",
            defects_block=json.dumps(defects, ensure_ascii=False))
        try:
            enrich = await bedrock.acomplete_json(
                prompt=prompt, schema=ENRICH_SCHEMA, max_tokens=4500,
                cache_key=cache_key_for("report", run_id, [d["defect_id"] for d in defects],
                                        PROMPT_VERSION))
        except Exception:
            enrich = None
    if enrich is None:
        enrich = _fallback_enrich(defects, run_cfg, funnel_summary)

    enr_by_id = {e["defect_id"]: e for e in enrich.get("defect_enrichments", [])}
    final_defects = []
    for d in defects:
        e = enr_by_id.get(d["defect_id"], {})
        final_defects.append({
            "defect_id": d["defect_id"], "type": d["type"], "attribute_id": d["attribute_id"],
            "severity": d["severity"], "gap": d["gap"], "gap_rationale": d["gap_rationale"],
            "headline": e.get("headline") or f"{d['type']} on {d['attribute_id']}",
            "why_it_happens": e.get("why_it_happens", ""),
            "suggested_fix": e.get("suggested_fix", ""),
            "content_patch": e.get("content_patch", ""),
            "impact": d["impact"], "clusters": d["clusters"],
            "evidence": {
                "cluster_id": d["cluster_id"],
                "losing_share_in_cluster": d["losing_share_in_cluster"],
                "n_losses": d["n_losses"],
                "sample_rejection_reasons": d["sample_rejection_reasons"],
                "competitor_contrast": d["competitor_contrast"],
            },
        })

    report = {
        "run_id": run_id, "brand": run_cfg["brand"], "target_slug": target,
        "category": run_cfg.get("category"), "generated_at": db.now_iso(),
        "n_responses": funnel_summary["n_annotated"], "engines": funnel_summary["engines"],
        "exec_summary": enrich.get("exec_summary", ""),
        "quick_wins": enrich.get("quick_wins", []),
        "defects": final_defects,
        "funnel": funnel_summary["per_product"],
        "funnel_dropoff": funnel_summary.get("funnel_dropoff", {}),
        "evidence_audit": evidence,
    }
    report["markdown"] = render_markdown(report)
    db.update_run(run_id, report=report)
    return report
