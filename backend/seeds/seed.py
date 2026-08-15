"""Seed the DB with demo products + intent library. Idempotent.

Run:  python -m backend.seeds.seed
"""
from __future__ import annotations

import json

from backend import config
from backend.pipeline.intents import ensure_library_loaded
from backend.storage import db


def seed_products(force: bool = False) -> list[str]:
    out = []
    pdir = config.SEEDS_DIR / "products"
    for path in sorted(pdir.glob("*.json")):
        p = json.loads(path.read_text(encoding="utf-8"))
        p.setdefault("version", 1)
        if force or not db.get_product(p["product_id"], p["version"]):
            db.upsert_product(p)
            out.append(f"{p['product_id']}@v{p['version']}")
    return out


def seed_all() -> dict:
    db.init_db()
    products = seed_products()
    n_intents = ensure_library_loaded()
    return {"seeded_products": products, "library_intents": n_intents,
            "total_products": len(db.list_products())}


if __name__ == "__main__":
    print(json.dumps(seed_all(), indent=2))
