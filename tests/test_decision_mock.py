from __future__ import annotations

from backend import config
from backend.decision.simulate import _mock_decision
from backend.storage import db


def _product(product_id: str, version: int, comfort: str | None, evidence: str | None) -> dict:
    return {
        "product_id": product_id,
        "version": version,
        "brand": product_id.title(),
        "display_name": product_id.replace("-", " ").title(),
        "source": "manual_prototype",
        "source_url": None,
        "raw_text": evidence or "Basic travel backpack page copy.",
        "category": "travel backpack",
        "attributes": [
            {
                "attribute_id": "comfort",
                "value": comfort,
                "evidence": evidence,
                "confidence": 0.95 if comfort else 0.0,
            },
            {
                "attribute_id": "price",
                "value": "$99",
                "evidence": "Price: $99.",
                "confidence": 1.0,
            },
        ],
    }


def test_mock_decision_responds_to_versioned_evidence(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "decision.db")
    db.init_db()
    db.upsert_product(_product("target-pack", 1, None, None))
    db.upsert_product(
        _product(
            "target-pack",
            2,
            "Ventilated back panel and memory-foam straps",
            "Ventilated back panel and memory-foam straps, tested over 6 continuous hours.",
        )
    )
    db.upsert_product(
        _product(
            "competitor-pack",
            1,
            "Padded shoulder straps",
            "Padded shoulder straps support longer walks.",
        )
    )
    intent = {
        "intent_id": "comfort-test",
        "text": "Most comfortable backpack for walking all day",
        "cluster_id": "comfort_carry",
        "attributes": ["comfort"],
    }

    before = _mock_decision(intent, ["target-pack@v1", "competitor-pack@v1"], 0)
    after = _mock_decision(intent, ["target-pack@v2", "competitor-pack@v1"], 0)

    assert before["winner"] == "competitor-pack@v1"
    assert after["winner"] == "target-pack@v2"
    assert [row["rank"] for row in before["per_product"]] == [2, 1]
    assert [row["rank"] for row in after["per_product"]] == [1, 2]
    assert after["model"] == "mock/decision-v2"
    target_reason = next(
        row["reasons_for"][0]["text"]
        for row in after["per_product"]
        if row["product_ref"] == "target-pack@v2"
    )
    assert "tested over 6 continuous hours" in target_reason


def test_mock_decision_is_paired_deterministic(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "deterministic.db")
    db.init_db()
    db.upsert_product(_product("alpha-pack", 1, "Padded straps", "Padded straps."))
    db.upsert_product(_product("beta-pack", 1, "Mesh back", "Mesh back panel."))
    intent = {
        "intent_id": "paired-test",
        "text": "Comfortable travel backpack",
        "cluster_id": "comfort_carry",
        "attributes": ["comfort"],
    }

    first = _mock_decision(intent, ["alpha-pack@v1", "beta-pack@v1"], 2)
    second = _mock_decision(intent, ["alpha-pack@v1", "beta-pack@v1"], 2)

    assert first["winner"] == second["winner"]
    assert first["per_product"] == second["per_product"]
