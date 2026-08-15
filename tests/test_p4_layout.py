from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLES = (ROOT / "frontend-simulator" / "src" / "styles.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", STYLES)
    assert match, f"missing CSS rule: {selector}"
    return match.group(1)


def test_p4_candidates_stay_inside_the_control_panel() -> None:
    assert "min-width: 0" in _rule(".workspace-grid > *")
    assert "max-width: 100%" in _rule(".candidate-row")
    assert "overflow: hidden" in _rule(".candidate-row")
    assert "text-overflow: ellipsis" in _rule(".candidate-price")


def test_p4_before_after_cards_keep_the_same_dimensions() -> None:
    assert "align-items: stretch" in _rule(".comparison-cards")
    assert "height: 430px" in _rule(".comparison-card")
    assert "min-height: 120px" in _rule(".mini-ranking")
    assert "overflow-y: auto" in _rule(".narrative")
