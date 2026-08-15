"""End-to-end pipeline smoke test.

Mock (no AWS):   python -m backend.scripts.smoke_e2e
Live (Bedrock):  python -m backend.scripts.smoke_e2e --live --n=12
"""
from __future__ import annotations

import asyncio
import sys

from backend.pipeline import runner
from backend.seeds.seed import seed_all
from backend.storage import db


async def main() -> int:
    live = "--live" in sys.argv
    n = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--n=")), "24"))
    engines = next((a.split("=")[1].split(",") for a in sys.argv if a.startswith("--engines=")), None)
    print("seed:", seed_all())
    body = {"brand": "CabinZero", "mode": "live" if live else "mock", "n_intents": n}
    if engines:
        body["engines"] = engines
    cfg = runner.normalize_config(body)
    print("run:", cfg["run_id"], "| mode:", cfg["mode"], "| engines:", cfg["engines"],
          "| refs:", cfg["product_refs"])
    handle = runner.start_run(cfg)
    q = handle.subscribe()
    while True:
        ev = await q.get()
        if ev["type"] == "progress":
            print(f"[{ev['pct']:3d}%] {ev['stage']}: {ev['message']}")
        else:
            print("FINAL EVENT:", ev)
            break
    run = db.get_run(cfg["run_id"])
    print("status:", run["status"], "| error:", run.get("error"))
    if run["status"] != "completed":
        return 1
    fs = run["funnel_summary"]
    print(f"\n=== FUNNEL ({fs['n_annotated']} annotated) ===")
    for slug, p in fs["per_product"].items():
        o = p["overall"]
        tag = "*" if p["is_target"] else " "
        print(f"{tag} {p['display']:<40} retrieved {o['retrieved_rate']:.2f}  considered "
              f"{o['consideration_share']:.2f}  recommended {o['recommendation_share']:.2f}  (n={o['n']})")
    target = fs["per_product"][run["config"]["brand"].lower().replace(" ", "-")]
    print("target loss attributes:", target["loss_attributes"])
    print("dropoff:", fs["funnel_dropoff"].get(run["report"]["target_slug"]))
    rep = run["report"]
    print("\n=== DEFECTS ===")
    for d in rep["defects"]:
        print(f"{d['defect_id']} [{d['severity']}/{d['type']}/{d['gap']}] {d['attribute_id']}: {d['headline']}")
    print("\nexec_summary:", rep["exec_summary"][:400])
    print("\nquick_wins:", rep["quick_wins"])
    ev_attrs = list((run.get("evidence") or {}).get("attributes", {}).keys())
    print("evidence audit attrs:", ev_attrs)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
