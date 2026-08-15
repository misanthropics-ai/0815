"""Isolated storage for the P4 before/after live demo.

The two target product versions live outside the main application database so
the stable demo cannot pollute or overwrite products uploaded through P5.
Competitor products and generated decisions continue to use the main DB.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from backend import config
from backend.storage import db


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _uj(value: Optional[str], default: Any = None) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.IMPACT_DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS impact_cases (
  case_id TEXT PRIMARY KEY,
  intent_json TEXT NOT NULL,
  competitor_refs_json TEXT NOT NULL,
  changes_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS impact_products (
  case_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('before', 'after')),
  product_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  brand TEXT,
  display_name TEXT,
  source TEXT,
  source_url TEXT,
  raw_text TEXT,
  attributes_json TEXT,
  parent_version INTEGER,
  change_note TEXT,
  created_at TEXT,
  category TEXT,
  PRIMARY KEY (case_id, side),
  UNIQUE (product_id, version),
  FOREIGN KEY (case_id) REFERENCES impact_cases(case_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_impact_product_ref
  ON impact_products(product_id, version);
"""


def init_db() -> None:
    conn = connect()
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _product_from_row(row: sqlite3.Row) -> dict:
    product = dict(row)
    product.pop("case_id", None)
    product.pop("side", None)
    product["attributes"] = _uj(product.pop("attributes_json", None), [])
    product["ref"] = db.make_ref(product["product_id"], product["version"])
    return product


def upsert_case(case: dict) -> str:
    """Replace one version-controlled demo case as a single transaction."""
    case_id = case["case_id"]
    before = case["before"]
    after = case["after"]
    if before["product_id"] != after["product_id"]:
        raise ValueError("impact demo before/after must share a product_id")
    if int(before["version"]) >= int(after["version"]):
        raise ValueError("impact demo after version must be newer than before")

    updated_at = case.get("updated_at") or db.now_iso()
    conn = connect()
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """INSERT OR REPLACE INTO impact_cases
               (case_id, intent_json, competitor_refs_json, changes_json, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                case_id,
                _j(case["intent"]),
                _j(case["competitor_refs"]),
                _j(case["changes_applied"]),
                updated_at,
            ),
        )
        conn.execute("DELETE FROM impact_products WHERE case_id=?", (case_id,))
        for side, product in (("before", before), ("after", after)):
            conn.execute(
                """INSERT INTO impact_products
                   (case_id, side, product_id, version, brand, display_name, source,
                    source_url, raw_text, attributes_json, parent_version, change_note,
                    created_at, category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    case_id,
                    side,
                    product["product_id"],
                    product["version"],
                    product.get("brand"),
                    product.get("display_name"),
                    product.get("source", "manual_prototype"),
                    product.get("source_url"),
                    product.get("raw_text", ""),
                    _j(product.get("attributes", [])),
                    product.get("parent_version"),
                    product.get("change_note"),
                    product.get("created_at") or updated_at,
                    product.get("category"),
                ),
            )
        conn.commit()
        return case_id
    finally:
        conn.close()


def get_product(product_id: str, version: Optional[int] = None) -> Optional[dict]:
    conn = connect()
    try:
        if version is None:
            row = conn.execute(
                """SELECT * FROM impact_products WHERE product_id=?
                   ORDER BY version DESC LIMIT 1""",
                (product_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM impact_products WHERE product_id=? AND version=?",
                (product_id, version),
            ).fetchone()
        return _product_from_row(row) if row else None
    finally:
        conn.close()


def get_product_by_ref(ref: str) -> Optional[dict]:
    product_id, version = db.parse_ref(ref)
    return get_product(product_id, version)


def get_case(case_id: str = "comfort-evidence-lift") -> Optional[dict]:
    conn = connect()
    try:
        case_row = conn.execute("SELECT * FROM impact_cases WHERE case_id=?", (case_id,)).fetchone()
        if not case_row:
            return None
        product_rows = conn.execute(
            "SELECT * FROM impact_products WHERE case_id=?", (case_id,)
        ).fetchall()
        products = {row["side"]: _product_from_row(row) for row in product_rows}
        if set(products) != {"before", "after"}:
            return None
        return {
            "case_id": case_row["case_id"],
            "intent": _uj(case_row["intent_json"], {}),
            "competitor_refs": _uj(case_row["competitor_refs_json"], []),
            "changes_applied": _uj(case_row["changes_json"], []),
            "before": products["before"],
            "after": products["after"],
            "updated_at": case_row["updated_at"],
        }
    finally:
        conn.close()


def count_products() -> int:
    conn = connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM impact_products").fetchone()
        return int(row["n"])
    finally:
        conn.close()
