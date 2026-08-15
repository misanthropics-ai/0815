#!/usr/bin/env python3
"""Validate P6 demo products against Contract v3 and the live taxonomy."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.schemas import CompareResult, CreateVersionRequest, Product  # noqa: E402

PRODUCTS = [
    *sorted((ROOT / "demo" / "real_products").glob("*.v1.json")),
    ROOT / "demo" / "before_after" / "cabinzero-classic-36l.v2.json",
]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_product(path: Path, taxonomy: set[str]) -> None:
    payload = load(path)
    Product.model_validate(payload)
    seen: set[str] = set()
    for attribute in payload["attributes"]:
        attribute_id = attribute["attribute_id"]
        if attribute_id in seen:
            raise ValueError(f"duplicate attribute {attribute_id}")
        seen.add(attribute_id)
        if attribute_id not in taxonomy:
            raise ValueError(f"{attribute_id} is not in Contract v3 taxonomy")
        value, evidence, confidence = (
            attribute["value"],
            attribute["evidence"],
            attribute["confidence"],
        )
        if value is None and (evidence is not None or confidence != 0):
            raise ValueError(f"{attribute_id}: null value requires null evidence and confidence=0")
        if value is not None and (
            not evidence or evidence.casefold() not in payload["raw_text"].casefold()
        ):
            raise ValueError(f"{attribute_id}: evidence is not a raw_text substring")


def main() -> int:
    taxonomy_payload = load(ROOT / "backend" / "taxonomy" / "taxonomy.json")
    taxonomy = {item["id"] for item in taxonomy_payload["attributes"]}
    failures = 0
    for path in PRODUCTS:
        try:
            check_product(path, taxonomy)
            print(f"PASS  {path.relative_to(ROOT)}")
        except Exception as exc:
            failures += 1
            print(f"FAIL  {path.relative_to(ROOT)}: {exc}")

    typed = [
        (ROOT / "demo" / "before_after" / "create_version.request.json", CreateVersionRequest),
        (ROOT / "demo" / "before_after" / "cached_fallback.compare.json", CompareResult),
    ]
    for path, model in typed:
        try:
            model.model_validate(load(path))
            print(f"PASS  {path.relative_to(ROOT)}")
        except Exception as exc:
            failures += 1
            print(f"FAIL  {path.relative_to(ROOT)}: {exc}")
    print(f"SUMMARY {len(PRODUCTS) + len(typed) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
