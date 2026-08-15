from __future__ import annotations

import asyncio

from backend import config
from backend.decision.simulate import run_batch
from backend.storage import db


def test_mock_batch_reaches_decision_execution(monkeypatch, tmp_path) -> None:
    """Guard the batch-concurrency setup used by every diagnosis request."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "batch.db")
    db.init_db()

    refs = []
    for product_id, brand in (("target-bag", "Target"), ("competitor-bag", "Competitor")):
        db.upsert_product(
            {
                "product_id": product_id,
                "version": 1,
                "brand": brand,
                "display_name": f"{brand} backpack",
                "source": "manual_prototype",
                "source_url": f"https://example.com/{product_id}",
                "raw_text": "A lightweight travel backpack with padded shoulder straps.",
                "attributes": [
                    {
                        "attribute_id": "comfort",
                        "value": "padded shoulder straps",
                        "confidence": 1,
                        "evidence": "padded shoulder straps",
                    }
                ],
                "category": "travel backpack",
            }
        )
        refs.append(db.make_ref(product_id, 1))

    result = asyncio.run(
        run_batch(
            "comfort_carry",
            refs,
            runs=1,
            max_intents=2,
            batch_id="batch_regression",
            mode="mock",
        )
    )

    assert result["status"] == "completed"
    assert result["n_decisions"] == 2
    assert result["error"] is None
    assert len(db.get_decisions_by_batch("batch_regression")) == 2
