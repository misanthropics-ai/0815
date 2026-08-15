from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from backend import config
from backend.pipeline.intents import library_sample, normalize_personas, personas_path
from backend.storage import db
from backend.taxonomy import load_taxonomy, taxonomy_path
from contracts.schemas import PersonaProfile, RunCreateRequest, SearchCriterion

STRUCTURED_PERSONA = {
    "persona_id": "designer_home_office",
    "label": "Home-office designer",
    "age": 29,
    "occupation": "designer",
    "budget": {"max_amount": 1200, "currency": "USD", "flexibility": "soft"},
    "use_cases": ["daily design work", "video calls"],
    "criteria": [
        {
            "attribute": "screen_size",
            "operator": "between",
            "value": {"min": 27, "max": 32},
            "unit": "inch",
            "importance": "must",
        }
    ],
    "context": {"desk_width_cm": 140},
    "notes": ["Prefers a clean design"],
}


def test_run_contract_accepts_structured_and_legacy_personas() -> None:
    request = RunCreateRequest(
        brand="Example",
        category="computer monitor",
        personas=[STRUCTURED_PERSONA, "legacy budget shopper"],
    )

    assert isinstance(request.personas[0], PersonaProfile)
    assert request.personas[1] == "legacy budget shopper"


def test_persona_normalization_is_structured_and_unique() -> None:
    profiles = normalize_personas([STRUCTURED_PERSONA, "legacy budget shopper"])

    assert profiles[0]["persona_id"] == "designer_home_office"
    assert profiles[0]["criteria"][0]["attribute"] == "screen_size"
    assert profiles[1]["persona_id"].startswith("legacy_")
    assert profiles[1]["notes"] == ["legacy budget shopper"]

    duplicate = [STRUCTURED_PERSONA, STRUCTURED_PERSONA]
    with pytest.raises(ValueError, match="persona_id values must be unique"):
        normalize_personas(duplicate)


def test_criterion_validation_rejects_incomplete_ranges() -> None:
    with pytest.raises(ValidationError, match="value.min and value.max"):
        SearchCriterion(
            attribute="screen_size",
            operator="between",
            value={"min": 27},
            importance="must",
        )

    with pytest.raises(ValidationError, match="value.min cannot exceed value.max"):
        SearchCriterion(
            attribute="screen_size",
            operator="between",
            value={"min": 32, "max": 27},
            importance="must",
        )


def test_unknown_category_uses_generic_taxonomy() -> None:
    taxonomy = load_taxonomy("computer monitor")

    assert taxonomy_path("computer monitor").name == "generic.json"
    assert taxonomy["category"] == "generic_product"
    assert "compatibility" in {attribute["id"] for attribute in taxonomy["attributes"]}

    profiles = normalize_personas(None, category="computer monitor")
    assert personas_path("computer monitor").name == "generic.json"
    assert all("travel" not in profile["label"].lower() for profile in profiles)


def test_intent_persona_snapshot_survives_sqlite_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    profile = PersonaProfile.model_validate(STRUCTURED_PERSONA).model_dump(
        mode="json", exclude_none=True
    )
    db.save_intents(
        [
            {
                "intent_id": "intent_persona_001",
                "run_id": "run_persona_001",
                "text": "best monitor for daily design work",
                "cluster_id": "feature_fit",
                "attributes": ["features"],
                "persona": profile["label"],
                "persona_id": profile["persona_id"],
                "persona_profile": profile,
            }
        ]
    )

    stored = db.get_intents("run_persona_001")[0]
    assert stored["persona_id"] == "designer_home_office"
    assert stored["persona_profile"]["budget"]["max_amount"] == 1200

    columns = {
        row[1]
        for row in sqlite3.connect(config.DB_PATH).execute("PRAGMA table_info(intents)").fetchall()
    }
    assert {"persona_id", "persona_json"} <= columns


def test_mock_library_intents_include_searchable_persona_context(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "library.db")
    db.init_db()

    rows = library_sample("run_library_001", 2, normalize_personas([STRUCTURED_PERSONA]))

    assert len(rows) == 2
    assert all(row["persona_id"] == "designer_home_office" for row in rows)
    assert "budget up to 1200 USD" in rows[0]["text"]
    assert rows[0]["persona_profile"]["criteria"][0]["attribute"] == "screen_size"


def test_mock_unknown_category_uses_generic_templates(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "generic.db")
    db.init_db()

    rows = library_sample(
        "run_generic_001",
        12,
        normalize_personas([STRUCTURED_PERSONA]),
        category="computer monitor",
    )

    generic_cluster_ids = {
        cluster["id"] for cluster in load_taxonomy("computer monitor")["clusters"]
    }
    assert len(rows) == 12
    assert {row["cluster_id"] for row in rows} <= generic_cluster_ids
    assert all(row["source"] == "template" for row in rows)
    assert all("computer monitor" in row["text"] for row in rows)
    assert all("travel backpack" not in row["text"] for row in rows)
