from __future__ import annotations

import asyncio

from backend import config
from backend.diagnosis.service import _competitor_refs
from backend.ingestion import fetcher
from backend.ingestion.service import _reusable_url_product, create_product
from backend.storage import db


def test_url_ingestion_reuses_existing_product_in_requested_category(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    db.init_db()
    url = "https://www.cabinzero.com/products/classic-36l"
    db.upsert_product(
        {
            "product_id": "cabinzero-classic-36l",
            "version": 1,
            "brand": "CabinZero",
            "display_name": "CabinZero Classic 36L",
            "source": "manual_prototype",
            "source_url": url,
            "raw_text": "Existing product evidence",
            "attributes": [],
            "category": None,
        }
    )
    db.upsert_product(
        {
            "product_id": "osprey-farpoint-40",
            "version": 1,
            "brand": "Osprey",
            "display_name": "Osprey Farpoint 40",
            "source": "manual_prototype",
            "source_url": "https://example.com/osprey-farpoint-40",
            "raw_text": "Competitor product evidence",
            "attributes": [],
            "category": None,
        }
    )

    async def unexpected_fetch(source_url: str):
        raise AssertionError(f"duplicate URL should not be fetched: {source_url}")

    monkeypatch.setattr(fetcher, "fetch_url", unexpected_fetch)
    result = asyncio.run(
        create_product(
            {
                "source": "url",
                "source_url": f"{url}?fbclid=tracking",
                "category": "travel backpack",
            }
        )
    )

    assert result["ref"] == "cabinzero-classic-36l@v1"
    assert len(db.list_products()) == 2
    assert _competitor_refs(result) == ["osprey-farpoint-40@v1"]
    assert _reusable_url_product(url, "electric kettle") is None
