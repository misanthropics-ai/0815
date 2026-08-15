"""Deterministic offline mock engine.

Composes plausible assistant answers + citations + a funnel-shaped ground-truth
annotation from mock_fixtures/mock_world.json (win propensities per cluster,
curated reason lines per brand). Works with any brand set: brands missing from
mock_world fall back to generic attribute-based reason templates.
Seeded by (run_id, intent_id, engine) => same run replays identically.
"""
from __future__ import annotations

import random
import time

from backend.pipeline.corpus import slugify
from backend.pipeline.engines.base import Engine, EngineResult, RunContext

GENERIC_FOR = {
    "price": "{b} comes in noticeably cheaper than the premium options",
    "weight": "{b} is one of the lightest bags in this class",
    "comfort": "{b}'s padded back panel and hip belt carry weight well over long days",
    "airline_compliance": "{b} is sized to pass strict budget-airline limits",
    "capacity_size": "{b} hits the sweet spot on capacity for this kind of trip",
    "organization": "{b} opens flat with a well-thought-out compartment layout",
    "durability": "{b}'s fabric and zippers feel built to last for years",
    "warranty": "{b} backs it with an unusually strong warranty",
    "style_design": "{b} looks cleaner than most travel packs",
    "security": "{b} adds lockable zippers and hidden pockets",
    "sustainability": "{b} uses recycled materials without a price penalty",
    "brand_reputation": "{b} is consistently well reviewed by one-bag travelers",
}
GENERIC_AGAINST = {
    "price": "{b} is hard to justify at its price",
    "weight": "{b} weighs more than the alternatives",
    "comfort": "{b} offers little back support for all-day walking",
    "airline_compliance": "{b} risks failing the strictest cabin sizers",
    "capacity_size": "{b}'s capacity may be tight for longer trips",
    "organization": "{b} is essentially one big compartment with minimal organization",
    "durability": "{b}'s materials feel less robust than the competition",
    "warranty": "{b}'s warranty coverage is unclear",
    "style_design": "{b}'s look won't be for everyone",
    "security": "{b} lacks any dedicated security features",
    "sustainability": "{b} publishes little about materials sourcing",
    "brand_reputation": "{b} has far fewer reviews to go on",
}
INTROS = [
    "Good question — here's how the realistic options stack up for your situation.",
    "Based on what you're describing, a few packs stand out; let me compare them.",
    "For this kind of trip there are three or four bags worth considering.",
]


def _world_brand(world: dict, slug: str) -> dict:
    return (world.get("brands") or {}).get(slug, {})


def _reason(world: dict, slug: str, display: str, kind: str, attrs: list[str],
            rng: random.Random) -> tuple[str, str]:
    """Return (sentence, attribute) preferring curated mock_world lines."""
    bank = ((world.get("reasons") or {}).get(slug) or {}).get(kind) or {}
    pool = []
    for a in attrs:
        for line in bank.get(a, []):
            pool.append((line, a))
    if not pool:
        for a, lines in bank.items():
            for line in lines:
                pool.append((line, a))
    if not pool:
        generic = GENERIC_FOR if kind == "for" else GENERIC_AGAINST
        cands = [a for a in attrs if a in generic] or list(generic.keys())
        a = rng.choice(cands)
        return generic[a].format(b=display), a
    return rng.choice(pool)


class MockEngine(Engine):
    name = "mock"

    async def run(self, intent: dict, ctx: RunContext) -> EngineResult:
        t0 = time.time()
        world = ctx.mock_world or {}
        cfg = ctx.run_cfg
        rng = random.Random(f"{cfg['run_id']}:{intent['intent_id']}:{self.name}")
        cluster = intent.get("cluster_id", "other")
        attrs = intent.get("attributes", []) or ["price"]

        target = ctx.brand_slugs["target"]
        all_slugs = [target] + ctx.brand_slugs["competitors"]
        display = ctx.brand_slugs["all"]

        cworld = ((world.get("clusters") or {}).get(cluster) or {})
        weights = cworld.get("win_weights") or {}
        miss = cworld.get("miss_prob") or {}

        # who even shows up (retrieval miss simulation)
        present = [s for s in all_slugs if rng.random() >= float(miss.get(s, 0.05))]
        if len(present) < 2:
            present = all_slugs[:2]
        w = [max(0.02, float(weights.get(s, 1.0 / len(present)))) for s in present]
        winner = rng.choices(present, weights=w, k=1)[0]
        considered = present if len(present) <= 3 else rng.sample(present, 3) + ([winner] if winner not in rng.sample(present, 3) else [])
        considered = list(dict.fromkeys(([winner] + [s for s in present if s != winner])[:4]))

        paras = [rng.choice(INTROS)]
        products = []
        rank = 1
        order = [s for s in considered if s != winner] + [winner]
        for slug in order:
            name = display.get(slug, slug)
            f_line, f_attr = _reason(world, slug, name, "for", attrs, rng)
            a_line, a_attr = _reason(world, slug, name, "against", attrs, rng)
            if slug == winner:
                paras.append(f"{name}: {f_line}. That makes it the strongest match here.")
            else:
                paras.append(f"{name}: {f_line}. However, {a_line}.")
            products.append({
                "name": name, "canonical": slug, "mentioned": True, "considered": True,
                "recommended": slug == winner, "rank": None,
                "reasons_for": [f_line], "reasons_against": [] if slug == winner else [a_line],
                "loss_reasons": [] if slug == winner else [{"text": a_line, "attribute": a_attr}],
            })
        win_name = display.get(winner, winner)
        paras.append(f"Overall I'd go with {win_name} — for this use case it simply fits best.")
        # ranks: winner 1, others in order 2..
        products_sorted = sorted(products, key=lambda p: (not p["recommended"],))
        for i, p in enumerate(products_sorted):
            p["rank"] = i + 1

        citations = []
        cite_bank = world.get("citations") or {}
        for slug in order:
            pool = cite_bank.get(slug) or [{"url": f"https://www.{slug}.com/", "title": f"{display.get(slug, slug)} official site"}]
            citations.append(rng.choice(pool))
        for extra in cite_bank.get("general", [])[:2]:
            if rng.random() < 0.7:
                citations.append(extra)
        # dedupe by url
        seen, cites = set(), []
        for c in citations:
            if c["url"] not in seen:
                seen.add(c["url"])
                cites.append(dict(c))

        gt = {"top_pick": winner, "top_pick_name": win_name, "products": products}
        return EngineResult(engine=self.name, model="mock-v1", text="\n\n".join(paras),
                            citations=cites, search_queries=[intent["text"]],
                            latency_ms=int((time.time() - t0) * 1000), ground_truth=gt)
