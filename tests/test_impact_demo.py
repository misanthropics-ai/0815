from __future__ import annotations

from backend import config
from backend.decision.simulate import _mock_decision
from backend.seeds.seed import seed_impact_demo, seed_products
from backend.storage import db, impact_db


def test_impact_demo_is_isolated_seeded_and_scoreable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "IMPACT_DB_PATH", tmp_path / "impact_demo.db")
    db.init_db()
    seed_products(force=True)

    assert seed_impact_demo() == ["comfort-evidence-lift"]
    assert seed_impact_demo() == ["comfort-evidence-lift"]
    case = impact_db.get_case()

    assert config.DB_PATH != config.IMPACT_DB_PATH
    assert impact_db.count_products() == 2
    assert case is not None
    assert case["before"]["version"] == 1
    assert case["after"]["version"] == 2
    assert case["before"]["product_id"] == case["after"]["product_id"]
    assert db.get_product(case["before"]["product_id"], 1) is None

    before_ref = case["before"]["ref"]
    after_ref = case["after"]["ref"]
    competitor_refs = case["competitor_refs"]
    before = _mock_decision(case["intent"], [before_ref, *competitor_refs], 0)
    after = _mock_decision(case["intent"], [after_ref, *competitor_refs], 0)

    before_rank = next(
        row["rank"] for row in before["per_product"] if row["product_ref"] == before_ref
    )
    after_rank = next(
        row["rank"] for row in after["per_product"] if row["product_ref"] == after_ref
    )
    assert after_rank < before_rank
    assert after["winner"] == after_ref
