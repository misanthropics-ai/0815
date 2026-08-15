"""Resolve category-specific search taxonomies with a generic fallback."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

TAXONOMY_DIR = Path(__file__).resolve().parent
DEFAULT_PATH = TAXONOMY_DIR / "taxonomy.json"
GENERIC_PATH = TAXONOMY_DIR / "generic.json"
DEFAULT_CATEGORY_ALIASES = {"travel-backpack", "travel_backpack", "travelbackpack"}


def category_slug(category: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (category or "").lower()).strip("_")


def taxonomy_path(category: str | None = None) -> Path:
    slug = category_slug(category)
    if not slug or slug in DEFAULT_CATEGORY_ALIASES:
        return DEFAULT_PATH
    candidate = TAXONOMY_DIR / f"{slug}.json"
    return candidate if candidate.exists() else GENERIC_PATH


@lru_cache(maxsize=32)
def load_taxonomy(category: str | None = None) -> dict:
    return json.loads(taxonomy_path(category).read_text(encoding="utf-8"))
