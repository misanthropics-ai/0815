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
from functools import lru_cache
from typing import Awaitable, Callable, Optional

from backend import config
from backend.llm import bedrock
from backend.llm.bedrock import cache_key_for, get_bedrock
from backend.llm.prompts import render_prompt
from backend.storage import db

PROMPT_VERSION = "intent_v1"

DEFAULT_PERSONAS = [
    "32-year-old accountant, three-week Europe trip, packs light",
    "13-year-old student going on a school trip",
    "33-year-old gym-goer commuting daily with training gear",
    "engineer carrying a 16-inch laptop, chargers and documents",
    "budget-constrained university student",
    "frequent budget-airline traveler (Ryanair / easyJet)",
    "hobby photographer carrying a mirrorless kit",
    "weekend hiker who wants one bag for city and trail",
    "digital nomad living out of one bag",
    "parent traveling with two kids",
]

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "persona": {"type": "string"},
                    "attributes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text"],
            },
        }
    },
    "required": ["intents"],
}


@lru_cache(maxsize=1)
def taxonomy() -> dict:
    return json.loads(config.TAXONOMY_PATH.read_text(encoding="utf-8"))


def clusters() -> list[dict]:
    return taxonomy()["clusters"]


def attribute_ids() -> list[str]:
    return [a["id"] for a in taxonomy()["attributes"]]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]+", " ", text.lower()).strip()


def ensure_library_loaded() -> int:
    n = db.count_intents("library")
    if n:
        return n
    path = config.FIXTURES_DIR / "intents.sample.json"
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [{**i, "run_id": "library", "source": "library"} for i in data["intents"]]
    db.save_intents(rows)
    return len(rows)


def library_sample(run_id: str, n: int) -> list[dict]:
    """Evenly sample n intents across clusters from the library, re-ID'd for this run."""
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
                out.append({**src, "intent_id": f"{run_id}_i{idx:03d}", "run_id": run_id,
                            "source": "library"})
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

    use_llm = run_cfg.get("mode") != "mock" and get_bedrock().available()
    if not use_llm:
        out = library_sample(run_id, n)
        db.save_intents(out)
        if progress:
            await progress(1, 1, f"loaded {len(out)} library intents (mock mode)")
        return out

    cls = clusters()
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
            personas="\n".join(f"- {p}" for p in (run_cfg.get("personas") or DEFAULT_PERSONAS)),
            cluster_id=c["id"], cluster_label=c["label"],
            cluster_description=c["description"],
            cluster_attributes=", ".join(c["attributes"]),
            count=per, attribute_ids=", ".join(attribute_ids()),
        )
        items: list[dict] = []
        try:
            out = await bedrock.acomplete_json(
                prompt=prompt, schema=INTENT_SCHEMA, max_tokens=4096, temperature=0.8,
                cache_key=cache_key_for("intents", run_cfg["brand"], run_cfg.get("category"),
                                        c["id"], per, PROMPT_VERSION))
            items = out.get("intents", []) or []
        except Exception:
            items = []
        if not items:  # per-cluster fallback to library
            items = [{"text": i["text"], "persona": i.get("persona"), "attributes": i.get("attributes", [])}
                     for i in db.get_intents("library", cluster_id=c["id"])[:per]]
        valid_attrs = set(attribute_ids())
        rows = []
        for it in items[:per + 5]:
            attrs = [a for a in (it.get("attributes") or []) if a in valid_attrs] or c["attributes"]
            rows.append({"text": (it.get("text") or "").strip(), "persona": it.get("persona"),
                         "attributes": attrs[:3], "cluster_id": c["id"], "cluster_label": c["label"],
                         "language": run_cfg.get("language", "en"), "source": "generated"})
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
