"""Stage 1 — Intent Generation.

Live: Claude (Bedrock) generates per-cluster realistic buyer intents from
persona pool + category, tagged with taxonomy attributes.
Fallback/mock: built-in library fixture (mock_fixtures/intents.sample.json).
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from functools import lru_cache
from typing import Awaitable, Callable, Optional, Union

from backend import config
from backend.llm import bedrock
from backend.llm.bedrock import cache_key_for, get_bedrock
from backend.llm.prompts import render_prompt
from backend.storage import db
from backend.taxonomy import category_slug, load_taxonomy
from contracts.schemas import PersonaProfile

PROMPT_VERSION = "intent_v1"

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "persona_id": {"type": "string"},
                    "attributes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "persona_id"],
            },
        }
    },
    "required": ["intents"],
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]+", " ", text.lower()).strip()


def personas_path(category: Optional[str] = None):
    slug = category_slug(category)
    if not slug or load_taxonomy(category)["category"] == "travel_backpack":
        return config.PERSONAS_PATH
    candidate = config.PERSONAS_DIR / f"{slug}.json"
    return candidate if candidate.exists() else config.PERSONAS_DIR / "generic.json"


@lru_cache(maxsize=32)
def default_personas(category: Optional[str] = None) -> list[dict]:
    payload = json.loads(personas_path(category).read_text(encoding="utf-8"))
    return [
        PersonaProfile.model_validate(profile).model_dump(mode="json", exclude_none=True)
        for profile in payload["profiles"]
    ]


def _legacy_profile(text: str, index: int) -> dict:
    label = text.strip()
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:48]
    suffix = slug or f"{index:03d}"
    return PersonaProfile(
        persona_id=f"legacy_{suffix}",
        label=label,
        notes=[label],
    ).model_dump(mode="json", exclude_none=True)


def normalize_personas(
    personas: Optional[list[Union[str, dict, PersonaProfile]]],
    category: Optional[str] = None,
) -> list[dict]:
    if not personas:
        return [dict(profile) for profile in default_personas(category)]

    profiles: list[dict] = []
    for index, raw in enumerate(personas):
        if isinstance(raw, str):
            profile = _legacy_profile(raw, index)
        else:
            profile = PersonaProfile.model_validate(raw).model_dump(mode="json", exclude_none=True)
        profiles.append(profile)

    ids = [profile["persona_id"] for profile in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("persona_id values must be unique within a run")
    return profiles


def clusters(category: Optional[str] = None) -> list[dict]:
    return load_taxonomy(category)["clusters"]


def attribute_ids(category: Optional[str] = None) -> list[str]:
    return [attribute["id"] for attribute in load_taxonomy(category)["attributes"]]


def persona_summary(profile: dict) -> str:
    """Compact deterministic text used when live intent generation is unavailable."""
    parts = [profile["label"]]
    if profile.get("age") is not None:
        parts.append(f"age {profile['age']}")
    if profile.get("occupation"):
        parts.append(f"occupation {profile['occupation']}")
    if profile.get("budget"):
        budget = profile["budget"]
        maximum = budget.get("max_amount")
        if maximum is not None:
            parts.append(f"budget up to {maximum:g} {budget.get('currency') or ''}".strip())
    if profile.get("use_cases"):
        parts.append("use cases: " + ", ".join(profile["use_cases"]))
    if profile.get("criteria"):
        criteria = []
        for criterion in profile["criteria"]:
            value = criterion.get("value")
            value_text = "" if value is None else f" {value}"
            unit = f" {criterion['unit']}" if criterion.get("unit") else ""
            criteria.append(
                f"{criterion['attribute']} {criterion['operator']}{value_text}{unit}"
                f" ({criterion.get('importance', 'should')})"
            )
        parts.append("criteria: " + ", ".join(criteria))
    if profile.get("notes"):
        parts.append("notes: " + ", ".join(profile["notes"]))
    return "; ".join(parts)


def ensure_library_loaded() -> int:
    path = config.FIXTURES_DIR / "intents.sample.json"
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [{**i, "run_id": "library", "source": "library"} for i in data["intents"]]
    expected_ids = {row["intent_id"] for row in rows}
    existing = {row["intent_id"]: row for row in db.get_intents("library")}

    def signature(row: dict) -> tuple:
        return (
            row.get("text"),
            row.get("cluster_id"),
            tuple(row.get("attributes") or []),
            row.get("persona"),
            row.get("language", "en"),
        )

    changed = [row for row in rows if signature(existing.get(row["intent_id"], {})) != signature(row)]
    if changed:
        db.save_intents(changed)
    if set(existing) != expected_ids:
        db.prune_intents("library", expected_ids)
    return len(rows)


def _template_sample(
    run_id: str,
    n: int,
    personas: list[dict],
    category: str,
    language: str = "en",
    selected_clusters: Optional[list[dict]] = None,
) -> list[dict]:
    """Build category-neutral fallback intents from profiles and taxonomy clusters."""
    cluster_pool = selected_clusters or clusters(category)
    out: list[dict] = []
    for idx in range(n):
        cluster = cluster_pool[idx % len(cluster_pool)]
        profile = personas[idx % len(personas)]
        use_case = next(iter(profile.get("use_cases") or []), "the buyer's use case")
        out.append(
            {
                "intent_id": f"{run_id}_i{idx:03d}",
                "run_id": run_id,
                "text": (
                    f"Help me choose a {category} for {use_case}. "
                    f"Prioritize {cluster['label'].lower()}. "
                    f"Shopper context: {persona_summary(profile)}."
                ),
                "cluster_id": cluster["id"],
                "cluster_label": cluster["label"],
                "attributes": cluster["attributes"][:3],
                "persona": profile["label"],
                "persona_id": profile["persona_id"],
                "persona_profile": profile,
                "language": language,
                "source": "template",
            }
        )
    return out


def library_sample(
    run_id: str,
    n: int,
    personas: list[dict],
    category: str = "travel backpack",
    language: str = "en",
) -> list[dict]:
    """Evenly sample n intents across clusters from the library, re-ID'd for this run."""
    if load_taxonomy(category)["category"] != "travel_backpack":
        return _template_sample(run_id, n, personas, category, language)

    ensure_library_loaded()
    by_cluster: dict[str, list[dict]] = {}
    for it in db.get_intents("library"):
        by_cluster.setdefault(it["cluster_id"], []).append(it)
    out: list[dict] = []
    idx = 0
    while len(out) < n and any(by_cluster.values()):
        for cid in list(by_cluster.keys()):
            if by_cluster[cid] and len(out) < n:
                src = by_cluster[cid].pop(0)
                profile = personas[idx % len(personas)]
                out.append(
                    {
                        **src,
                        "intent_id": f"{run_id}_i{idx:03d}",
                        "run_id": run_id,
                        "text": f"{src['text']} Shopper context: {persona_summary(profile)}.",
                        "persona": profile["label"],
                        "persona_id": profile["persona_id"],
                        "persona_profile": profile,
                        "language": language,
                        "source": "library",
                    }
                )
                idx += 1
    return out


async def generate_intents(run_cfg: dict,
                           progress: Optional[Callable[[int, int, str], Awaitable[None]]] = None
                           ) -> list[dict]:
    run_id = run_cfg["run_id"]
    n = max(10, min(300, int(run_cfg.get("n_intents") or 150)))
    existing = db.get_intents(run_id)
    if existing:
        return existing

    category = run_cfg.get("category") or "travel backpack"
    language = run_cfg.get("language") or "en"
    personas = normalize_personas(run_cfg.get("personas"), category)
    use_llm = run_cfg.get("mode") != "mock" and get_bedrock().available()
    if not use_llm:
        out = library_sample(run_id, n, personas, category, language)
        db.save_intents(out)
        if progress:
            source = "library" if out and out[0]["source"] == "library" else "templates"
            await progress(1, 1, f"loaded {len(out)} {source} (mock mode)")
        return out

    cls = clusters(category)
    per = math.ceil(n / len(cls))
    done = 0

    async def gen_cluster(c: dict) -> list[dict]:
        nonlocal done
        prompt = render_prompt(
            "intent_v1",
            category=run_cfg.get("category", "travel backpack"),
            market=run_cfg.get("market", "US/EU"),
            language=run_cfg.get("language", "en"),
            brand=run_cfg["brand"],
            competitors=", ".join(run_cfg.get("competitors", [])),
            personas_json=json.dumps(personas, ensure_ascii=False, indent=2, sort_keys=True),
            cluster_id=c["id"], cluster_label=c["label"],
            cluster_description=c["description"],
            cluster_attributes=", ".join(c["attributes"]),
            count=per, attribute_ids=", ".join(attribute_ids(category)),
        )
        items: list[dict] = []
        try:
            out = await bedrock.acomplete_json(
                prompt=prompt, schema=INTENT_SCHEMA, max_tokens=4096, temperature=0.8,
                cache_key=cache_key_for(
                    "intents",
                    run_cfg["brand"],
                    category,
                    run_cfg.get("market"),
                    run_cfg.get("language"),
                    run_cfg.get("competitors"),
                    personas,
                    c["id"],
                    per,
                    PROMPT_VERSION,
                ),
            )
            items = out.get("intents", []) or []
        except Exception:
            items = []
        if not items:  # per-cluster deterministic fallback
            fallback = _template_sample(
                run_id,
                per,
                personas,
                category,
                language,
                selected_clusters=[c],
            )
            items = [
                {
                    "text": item["text"],
                    "persona_id": item["persona_id"],
                    "attributes": item["attributes"],
                }
                for item in fallback
            ]
        valid_attrs = set(attribute_ids(category))
        profiles_by_id = {profile["persona_id"]: profile for profile in personas}
        rows = []
        for index, it in enumerate(items[: per + 5]):
            attrs = [a for a in (it.get("attributes") or []) if a in valid_attrs] or c["attributes"]
            persona_id = it.get("persona_id")
            profile = profiles_by_id.get(persona_id) or personas[index % len(personas)]
            rows.append(
                {
                    "text": (it.get("text") or "").strip(),
                    "persona": profile["label"],
                    "persona_id": profile["persona_id"],
                    "persona_profile": profile,
                    "attributes": attrs[:3],
                    "cluster_id": c["id"],
                    "cluster_label": c["label"],
                    "language": language,
                    "source": "generated",
                }
            )
        done += 1
        if progress:
            await progress(done, len(cls), f"cluster {c['id']}: {len(rows)} intents")
        return rows

    results = await asyncio.gather(*[gen_cluster(c) for c in cls])
    # interleave clusters to keep balance, dedupe by normalized text, cap n
    seen: set[str] = set()
    out: list[dict] = []
    pools = [list(r) for r in results]
    while len(out) < n and any(pools):
        for pool in pools:
            while pool:
                cand = pool.pop(0)
                key = _norm(cand["text"])
                if cand["text"] and key not in seen:
                    seen.add(key)
                    out.append(cand)
                    break
            if len(out) >= n:
                break
    for idx, it in enumerate(out):
        it["intent_id"] = f"{run_id}_i{idx:03d}"
        it["run_id"] = run_id
    db.save_intents(out)
    return out


# ---------------------------------------------------------------- category intent libraries

CATEGORY_INTENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "intents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "cluster_id": {"type": "string"},
                    "attributes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "cluster_id"],
            },
        }
    },
    "required": ["intents"],
}


def category_library_id(category: Optional[str]) -> str:
    slug = category_slug(category)
    if not slug or load_taxonomy(category)["category"] == "travel_backpack":
        return "library"
    return f"library:{slug}"


async def ensure_category_intents(category: Optional[str], per_cluster: int = 5) -> list[dict]:
    """Reusable intent library for a category (drives /simulate/batch + diagnosis).

    Backpack (demo default) => the built-in 163-intent library. Any other
    category => lazily built from ITS taxonomy clusters: LLM-generated buyer
    intents when Bedrock is up, template fallback otherwise. Cached in the
    intents table under run_id "library:{slug}".
    """
    lib_id = category_library_id(category)
    if lib_id == "library":
        ensure_library_loaded()
        return db.get_intents("library")
    existing = db.get_intents(lib_id)
    if existing:
        return existing
    from backend.loglib import log
    t0 = time.time()
    tax = load_taxonomy(category)
    cls = tax["clusters"][:6]
    valid_cl = {c["id"]: c for c in cls}
    valid_attrs = {a["id"] for a in tax["attributes"]}
    rows: list[dict] = []
    if get_bedrock().available():
        cluster_block = "\n".join(
            f'- {c["id"]}: {c["label"]} — {c["description"]} (attributes: {", ".join(c["attributes"])})'
            for c in cls
        )
        prompt = (
            f'Generate realistic buyer intents for the product category "{category}" — the '
            "queries real consumers type into AI shopping assistants when deciding what to buy.\n"
            f"For EACH cluster below produce {per_cluster} distinct intents (mix short "
            "search-style queries and longer contextual asks; vary personas, budgets and use "
            f"cases; no near-duplicates):\n{cluster_block}\n"
            "Tag each intent with its cluster_id and 1-3 attribute ids from: "
            + ", ".join(sorted(valid_attrs))
        )
        try:
            out = await bedrock.acomplete_json(
                prompt=prompt,
                schema=CATEGORY_INTENTS_SCHEMA,
                max_tokens=4000,
                temperature=0.8,
                cache_key=cache_key_for("catintents", lib_id, per_cluster, "v1"),
            )
            for it in out.get("intents", []):
                cid = it.get("cluster_id")
                if it.get("text") and cid in valid_cl:
                    attrs = [a for a in (it.get("attributes") or []) if a in valid_attrs][:3]
                    rows.append(
                        {
                            "text": it["text"].strip(),
                            "cluster_id": cid,
                            "attributes": attrs or valid_cl[cid]["attributes"][:3],
                        }
                    )
        except Exception:
            rows = []
    if not rows:  # offline/template fallback
        for c in cls:
            rows += [
                {"text": f"best {category} for {c['label'].lower()}", "cluster_id": c["id"],
                 "attributes": c["attributes"][:3]},
                {"text": f"which {category} should I buy? I mostly care about "
                         f"{c['description'].lower()}", "cluster_id": c["id"],
                 "attributes": c["attributes"][:3]},
                {"text": f"good value {category} recommendation where {c['label'].lower()} matters",
                 "cluster_id": c["id"], "attributes": (c["attributes"] + ["price"])[:3]},
            ]
    for i, r in enumerate(rows):
        r.update(
            {
                "intent_id": f"{lib_id}_i{i:03d}",
                "run_id": lib_id,
                "cluster_label": valid_cl.get(r["cluster_id"], {}).get("label"),
                "language": "en",
                "source": "category_library",
            }
        )
    db.save_intents(rows)
    log("intents.category_library", category=str(category), lib=lib_id, n=len(rows),
        ms=int((time.time() - t0) * 1000))
    return rows
