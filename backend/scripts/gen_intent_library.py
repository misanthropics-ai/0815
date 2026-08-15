"""Generate the built-in intent library fixture (offline fallback for Stage 1).

Deterministic template expansion — ~22 intents per cluster over the 8 taxonomy
clusters. Run:  python -m backend.scripts.gen_intent_library
"""
from __future__ import annotations

import json

from backend import config

PERSONAS = [
    "a 32-year-old accountant heading to Europe for three weeks",
    "a university student on a tight budget",
    "a digital nomad living out of one bag",
    "a weekend hiker who wants one bag for city and trail",
    "a hobby photographer carrying a mirrorless kit",
    "a gym-goer commuting daily with training gear",
    "a parent traveling with two kids",
    "an engineer who carries a 16-inch laptop everywhere",
]
DESTS = ["Europe", "Japan", "Southeast Asia", "Portugal", "Italy", "Iceland", "Korea", "eastern Europe"]
DURS = ["a 3-week", "a 10-day", "a weekend", "a 2-month", "a one-week", "a 5-day"]
BUDGETS = ["$80", "$100", "€90", "$150", "€120", "$60", "$130"]
AIRLINES = ["Ryanair", "easyJet", "Wizz Air", "Spirit", "AirAsia", "Vueling"]
LITERS = ["35L", "36L", "40L", "38L", "30L", "45L"]

T = {
    "budget_value": [
        "best budget travel backpack under {budget}",
        "cheapest carry-on backpack that won't fall apart after one trip, budget {budget}",
        "I'm {persona} and can spend at most {budget} on a travel backpack for {dur} trip to {dest} — what should I get?",
        "is an expensive travel backpack actually worth it, or is a {budget} one fine for {dest}?",
        "best value {liters} backpack for {dur} trip, ideally under {budget}",
        "good travel backpack for a broke college student going to {dest}",
        "what {liters} travel backpack gives the most for the money right now?",
        "recommend a no-frills carry-on backpack under {budget} for {persona}",
        "decent travel backpack under {budget} that still looks okay for hostels in {dest}",
        "best cheap alternative to the popular travel backpacks everyone recommends, max {budget}",
        "travel backpack under {budget} with the fewest compromises for {dur} trip",
    ],
    "airline_compliance": [
        "backpack that definitely fits {airline} cabin size limits",
        "best {liters} backpack that passes the {airline} sizer — I refuse to pay bag fees",
        "I'm {persona} flying {airline} to {dest}; which backpack fits under the seat?",
        "largest backpack I can take on {airline} as a personal item?",
        "carry-on backpack guaranteed 55x40x20 compliant for {dur} trip",
        "one-bag setup for {airline} strict limits, {dur} trip to {dest}",
        "backpack that works as underseat bag on budget airlines in {dest}",
        "will a {liters} travel backpack pass {airline} cabin rules? if not what should I buy",
        "best cabin-size backpack for {persona} who only flies low-cost airlines",
        "travel backpack that never gets gate-checked on {airline}",
        "what backpack do frequent {airline} flyers actually use?",
    ],
    "comfort_carry": [
        "most comfortable travel backpack for walking all day",
        "I'm {persona} and will walk 20k steps a day in {dest} — which backpack won't kill my back?",
        "best travel backpack with real back support and a hip belt",
        "{liters} backpack comfortable enough to wear for hours in {dest} heat",
        "travel backpack for someone with lower back pain, {dur} trip",
        "which backpack carries heavy loads most comfortably for {persona}?",
        "backpack with ventilated back panel for sweaty summer travel in {dest}",
        "most ergonomic carry-on backpack for {dur} trip with lots of walking",
        "do I need a hip belt on a {liters} travel pack? what should I buy for {dest}",
        "backpack that distributes weight well for a small-framed traveler",
        "comfort-first travel backpack recommendation for {persona}",
    ],
    "durability_warranty": [
        "most durable travel backpack that will last 10 years",
        "buy-it-for-life travel backpack for {persona}",
        "backpack with the best warranty in case zippers break on {dur} trip",
        "toughest {liters} backpack for rough handling and {dest} weather",
        "travel backpack with lifetime guarantee — which brands honor it?",
        "which travel backpack survives being crammed into {airline} sizers weekly?",
        "waterproof-ish durable backpack for monsoon season in {dest}",
        "backpack materials: what should {persona} look for to avoid tearing, and which model?",
        "best-built travel backpack under {budget} — durability over features",
        "travel pack that handles {dur} overland trip through {dest} without falling apart",
        "which backpack brand has the best repair program?",
    ],
    "organization_tech": [
        "best travel backpack with a proper 16-inch laptop compartment",
        "backpack with great organization for chargers, cables and documents for {persona}",
        "clamshell travel backpack that opens flat like a suitcase, around {liters}",
        "one bag for work and travel: laptop, gym kit, {dur} trip — what fits?",
        "travel backpack with best pocket layout for {persona} going to {dest}",
        "backpack for carrying camera gear plus clothes for {dur} trip",
        "tech-friendly carry-on backpack under {budget}",
        "best travel backpack for staying organized living out of packing cubes in {dest}",
        "backpack with quick-access laptop sleeve for airport security, {liters} size",
        "minimalist backpack that still organizes a laptop, tablet and travel documents",
        "which {liters} pack has the best internal organization for one-baggers?",
    ],
    "weight_minimal": [
        "lightest travel backpack that still holds {dur} trip of clothes",
        "ultralight {liters} backpack for one-bag travel to {dest}",
        "I want my empty bag under 1 kg — best options for {persona}?",
        "lightest carry-on backpack that fits {airline} limits",
        "minimalist travel backpack for packing light, {dur} trip",
        "best sub-1kg travel pack that isn't fragile",
        "how light can a {liters} travel backpack get without losing comfort — recommendations?",
        "featherweight backpack for {persona} who hates heavy bags",
        "light and simple travel backpack under {budget}",
        "best lightweight backpack for summer travel around {dest}",
        "ultralight one-bag setup recommendation for {dur} trip",
    ],
    "hiking_crossover": [
        "travel backpack that doubles as a day-hike pack in {dest}",
        "one bag for city travel plus mountain trails on {dur} trip",
        "best crossover pack for {persona} doing both hostels and hiking",
        "backpack for {dest} trip with a few serious hikes — travel-friendly but trail-capable",
        "{liters} pack comfortable enough for a full-day trek but airline-cabin sized",
        "do I need a real hiking pack for {dest} or will a travel backpack do? recommend one",
        "travel pack with trekking-level suspension for {dur} adventure trip",
        "hybrid hiking/travel backpack under {budget}",
        "best pack for hut-to-hut walking plus city stops in {dest}",
        "backpack for {persona} mixing camping and cheap flights on {airline}",
        "adventure travel backpack that handles trails and {airline} sizers",
    ],
    "brand_alternative": [
        "cheaper alternatives to Osprey Farpoint that are just as good",
        "is CabinZero-style simplicity better than feature-heavy packs for {dur} trip?",
        "alternatives to the Osprey Farpoint 40 for {persona}",
        "backpacks like the Cotopaxi Allpa but lighter or cheaper",
        "what do people buy instead of Osprey for one-bag travel to {dest}?",
        "Decathlon backpack vs premium brands — which should {persona} pick?",
        "best non-mainstream travel backpack brand worth trying under {budget}",
        "which brand makes the most recommended {liters} travel pack right now?",
        "trusted travel backpack brands for {dur} trip — who actually delivers?",
        "reddit-favorite travel backpacks that beat the big brands on value",
        "underrated alternatives to the most recommended travel backpacks",
    ],
}


def main() -> None:
    tax = json.loads(config.TAXONOMY_PATH.read_text(encoding="utf-8"))
    clusters = {c["id"]: c for c in tax["clusters"]}
    intents = []
    k = 0
    per_cluster = 22
    for cid, templates in T.items():
        c = clusters[cid]
        made = 0
        i = 0
        while made < per_cluster:
            tpl = templates[i % len(templates)]
            text = tpl.format(
                persona=PERSONAS[(i + k) % len(PERSONAS)],
                dest=DESTS[(i * 3 + k) % len(DESTS)],
                dur=DURS[(i * 2 + k) % len(DURS)],
                budget=BUDGETS[(i * 5 + k) % len(BUDGETS)],
                airline=AIRLINES[(i * 7 + k) % len(AIRLINES)],
                liters=LITERS[(i * 11 + k) % len(LITERS)],
            )
            persona = None
            for p in PERSONAS:
                if p in text:
                    persona = p
                    break
            intents.append({
                "intent_id": f"lib_{cid}_{made:02d}",
                "text": text,
                "cluster_id": cid,
                "cluster_label": c["label"],
                "attributes": c["attributes"],
                "persona": persona,
                "language": "en",
                "source": "library",
            })
            made += 1
            i += 1
        k += 1
    # de-dup safety
    seen, out = set(), []
    for it in intents:
        key = it["text"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    payload = {"version": 1, "category": tax["category"], "count": len(out), "intents": out}
    dest = config.FIXTURES_DIR / "intents.sample.json"
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(out)} intents -> {dest}")


if __name__ == "__main__":
    main()
