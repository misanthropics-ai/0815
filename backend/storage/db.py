"""SQLite storage. Plain sync DAO (call via asyncio.to_thread from async code).

Complex fields are stored as JSON TEXT columns. WAL mode + busy timeout make
concurrent access from the API and the pipeline background task safe enough.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from backend import config

# ---------------------------------------------------------------- helpers

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def make_ref(product_id: str, version: int) -> str:
    return f"{product_id}@v{version}"


def parse_ref(ref: str) -> tuple[str, Optional[int]]:
    """'cabinzero-classic-36l@v2' -> ('cabinzero-classic-36l', 2); no @v => (id, None)=latest."""
    if "@v" in ref:
        pid, _, v = ref.rpartition("@v")
        try:
            return pid, int(v)
        except ValueError:
            return ref, None
    return ref, None


def _j(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)


def _uj(s: Optional[str], default: Any = None) -> Any:
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  product_id TEXT NOT NULL, version INTEGER NOT NULL,
  brand TEXT, display_name TEXT, source TEXT, source_url TEXT,
  raw_text TEXT, attributes_json TEXT, parent_version INTEGER, change_note TEXT,
  created_at TEXT, PRIMARY KEY (product_id, version)
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, config_json TEXT, status TEXT, stage TEXT,
  progress_json TEXT, funnel_summary_json TEXT, evidence_json TEXT, report_json TEXT,
  error TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS intents (
  intent_id TEXT PRIMARY KEY, run_id TEXT, text TEXT, cluster_id TEXT, cluster_label TEXT,
  attributes_json TEXT, persona TEXT, persona_id TEXT, persona_json TEXT,
  language TEXT, source TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_intents_run ON intents(run_id);
CREATE TABLE IF NOT EXISTS responses (
  response_id TEXT PRIMARY KEY, run_id TEXT, intent_id TEXT, engine TEXT, model TEXT,
  status TEXT, text TEXT, citations_json TEXT, search_queries_json TEXT,
  ground_truth_json TEXT, latency_ms INTEGER, error TEXT, cache_key TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_responses_run ON responses(run_id);
CREATE TABLE IF NOT EXISTS funnel (
  response_id TEXT PRIMARY KEY, run_id TEXT, intent_id TEXT, engine TEXT,
  judge_model TEXT, prompt_version TEXT, top_pick TEXT, products_json TEXT,
  is_ground_truth INTEGER DEFAULT 0, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_funnel_run ON funnel(run_id);
CREATE TABLE IF NOT EXISTS decisions (
  decision_id TEXT PRIMARY KEY, batch_id TEXT, intent_json TEXT, candidates_json TEXT,
  winner TEXT, per_product_json TEXT, narrative TEXT, model TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_batch ON decisions(batch_id);
CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY, cluster_id TEXT, candidates_json TEXT, runs INTEGER,
  status TEXT, n_intents INTEGER, shares_json TEXT, decision_ids_json TEXT,
  error TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS diagnoses (
  key TEXT PRIMARY KEY, product_ref TEXT, source TEXT, json TEXT, generated_at TEXT
);
CREATE TABLE IF NOT EXISTS debate_sessions (
  session_id TEXT PRIMARY KEY, product_ref TEXT, focus_defect_id TEXT,
  messages_json TEXT, meta_json TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS kv_cache (
  cache_key TEXT PRIMARY KEY, kind TEXT, value_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS sources (
  url TEXT PRIMARY KEY, domain TEXT, title TEXT, status TEXT, text TEXT, fetched_at TEXT
);
"""


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        intent_columns = {row["name"] for row in conn.execute("PRAGMA table_info(intents)")}
        if "persona_id" not in intent_columns:
            conn.execute("ALTER TABLE intents ADD COLUMN persona_id TEXT")
        if "persona_json" not in intent_columns:
            conn.execute("ALTER TABLE intents ADD COLUMN persona_json TEXT")
        conn.commit()
    finally:
        conn.close()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- products

def upsert_product(p: dict) -> dict:
    conn = connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO products
               (product_id, version, brand, display_name, source, source_url, raw_text,
                attributes_json, parent_version, change_note, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (p["product_id"], p.get("version", 1), p.get("brand"), p.get("display_name"),
             p.get("source"), p.get("source_url"), p.get("raw_text", ""),
             _j(p.get("attributes", [])), p.get("parent_version"), p.get("change_note"),
             p.get("created_at") or now_iso()),
        )
        conn.commit()
        return p
    finally:
        conn.close()


def _product_from_row(r: dict) -> dict:
    return {
        "product_id": r["product_id"], "version": r["version"], "brand": r["brand"],
        "display_name": r["display_name"], "source": r["source"], "source_url": r["source_url"],
        "raw_text": r["raw_text"], "attributes": _uj(r["attributes_json"], []),
        "parent_version": r["parent_version"], "change_note": r["change_note"],
        "created_at": r["created_at"], "ref": make_ref(r["product_id"], r["version"]),
    }


def latest_version(product_id: str) -> Optional[int]:
    conn = connect()
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM products WHERE product_id=?", (product_id,)).fetchone()
        return row["v"] if row and row["v"] is not None else None
    finally:
        conn.close()


def get_product(product_id: str, version: Optional[int] = None) -> Optional[dict]:
    if version is None:
        version = latest_version(product_id)
        if version is None:
            return None
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM products WHERE product_id=? AND version=?",
                           (product_id, version)).fetchone()
        return _product_from_row(dict(row)) if row else None
    finally:
        conn.close()


def get_product_by_ref(ref: str) -> Optional[dict]:
    pid, ver = parse_ref(ref)
    return get_product(pid, ver)


def list_products() -> list[dict]:
    conn = connect()
    try:
        cur = conn.execute("SELECT * FROM products ORDER BY product_id, version")
        return [_product_from_row(r) for r in _rows(cur)]
    finally:
        conn.close()


# ---------------------------------------------------------------- runs

def create_run(run_id: str, cfg: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO runs (run_id, config_json, status, stage, progress_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, _j(cfg), "pending", "intents", _j({}), now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


_RUN_JSON_FIELDS = {"config": "config_json", "progress": "progress_json",
                    "funnel_summary": "funnel_summary_json", "evidence": "evidence_json",
                    "report": "report_json"}


def update_run(run_id: str, **fields: Any) -> None:
    sets, vals = ["updated_at=?"], [now_iso()]
    for k, v in fields.items():
        if k in _RUN_JSON_FIELDS:
            sets.append(f"{_RUN_JSON_FIELDS[k]}=?")
            vals.append(_j(v))
        elif k in ("status", "stage", "error"):
            sets.append(f"{k}=?")
            vals.append(v)
    vals.append(run_id)
    conn = connect()
    try:
        conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id=?", vals)
        conn.commit()
    finally:
        conn.close()


def get_run(run_id: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        return {
            "run_id": r["run_id"], "config": _uj(r["config_json"], {}), "status": r["status"],
            "stage": r["stage"], "progress": _uj(r["progress_json"], {}),
            "funnel_summary": _uj(r["funnel_summary_json"]), "evidence": _uj(r["evidence_json"]),
            "report": _uj(r["report_json"]), "error": r["error"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }
    finally:
        conn.close()


def list_runs(limit: int = 50) -> list[dict]:
    conn = connect()
    try:
        cur = conn.execute("SELECT run_id, config_json, status, stage, progress_json, error, created_at, updated_at "
                           "FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for r in _rows(cur):
            out.append({"run_id": r["run_id"], "config": _uj(r["config_json"], {}), "status": r["status"],
                        "stage": r["stage"], "progress": _uj(r["progress_json"], {}), "error": r["error"],
                        "created_at": r["created_at"], "updated_at": r["updated_at"]})
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------- intents

def save_intents(intents: list[dict]) -> None:
    conn = connect()
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO intents
               (intent_id, run_id, text, cluster_id, cluster_label, attributes_json,
                persona, persona_id, persona_json, language, source, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(i["intent_id"], i.get("run_id", "library"), i["text"], i.get("cluster_id", "other"),
              i.get("cluster_label"), _j(i.get("attributes", [])), i.get("persona"),
              i.get("persona_id"), _j(i.get("persona_profile")) if i.get("persona_profile") else None,
              i.get("language", "en"), i.get("source", "generated"), i.get("created_at") or now_iso())
             for i in intents],
        )
        conn.commit()
    finally:
        conn.close()


def get_intents(run_id: str = "library", cluster_id: Optional[str] = None) -> list[dict]:
    conn = connect()
    try:
        q = "SELECT * FROM intents WHERE run_id=?"
        args: list = [run_id]
        if cluster_id:
            q += " AND cluster_id=?"
            args.append(cluster_id)
        q += " ORDER BY cluster_id, intent_id"
        out = []
        for r in _rows(conn.execute(q, args)):
            out.append({"intent_id": r["intent_id"], "run_id": r["run_id"], "text": r["text"],
                        "cluster_id": r["cluster_id"], "cluster_label": r["cluster_label"],
                        "attributes": _uj(r["attributes_json"], []), "persona": r["persona"],
                        "persona_id": r.get("persona_id"),
                        "persona_profile": _uj(r.get("persona_json")),
                        "language": r["language"], "source": r["source"]})
        return out
    finally:
        conn.close()


def count_intents(run_id: str = "library") -> int:
    conn = connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM intents WHERE run_id=?", (run_id,)).fetchone()
        return row["n"]
    finally:
        conn.close()


def prune_intents(run_id: str, keep_intent_ids: set[str]) -> None:
    """Remove derived intent rows that no longer exist in their committed fixture."""
    conn = connect()
    try:
        if keep_intent_ids:
            placeholders = ",".join("?" for _ in keep_intent_ids)
            conn.execute(
                f"DELETE FROM intents WHERE run_id=? AND intent_id NOT IN ({placeholders})",
                [run_id, *sorted(keep_intent_ids)],
            )
        else:
            conn.execute("DELETE FROM intents WHERE run_id=?", (run_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- responses

def save_response(r: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO responses
               (response_id, run_id, intent_id, engine, model, status, text, citations_json,
                search_queries_json, ground_truth_json, latency_ms, error, cache_key, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["response_id"], r["run_id"], r["intent_id"], r["engine"], r.get("model"),
             r.get("status", "ok"), r.get("text", ""), _j(r.get("citations", [])),
             _j(r.get("search_queries", [])), _j(r.get("ground_truth")) if r.get("ground_truth") else None,
             r.get("latency_ms"), r.get("error"), r.get("cache_key"), r.get("created_at") or now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _response_from_row(r: dict) -> dict:
    return {"response_id": r["response_id"], "run_id": r["run_id"], "intent_id": r["intent_id"],
            "engine": r["engine"], "model": r["model"], "status": r["status"], "text": r["text"],
            "citations": _uj(r["citations_json"], []), "search_queries": _uj(r["search_queries_json"], []),
            "ground_truth": _uj(r["ground_truth_json"]), "latency_ms": r["latency_ms"],
            "error": r["error"], "created_at": r["created_at"]}


def get_response(response_id: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM responses WHERE response_id=?", (response_id,)).fetchone()
        return _response_from_row(dict(row)) if row else None
    finally:
        conn.close()


def get_responses(run_id: str, engine: Optional[str] = None, intent_id: Optional[str] = None,
                  cluster_id: Optional[str] = None, include_text: bool = True) -> list[dict]:
    conn = connect()
    try:
        q = ("SELECT resp.*, i.cluster_id AS cluster_id, i.text AS intent_text "
             "FROM responses resp JOIN intents i ON i.intent_id = resp.intent_id WHERE resp.run_id=?")
        args: list = [run_id]
        if engine:
            q += " AND resp.engine=?"
            args.append(engine)
        if intent_id:
            q += " AND resp.intent_id=?"
            args.append(intent_id)
        if cluster_id:
            q += " AND i.cluster_id=?"
            args.append(cluster_id)
        out = []
        for r in _rows(conn.execute(q, args)):
            d = _response_from_row(r)
            d["cluster_id"] = r["cluster_id"]
            d["intent_text"] = r["intent_text"]
            if not include_text:
                d["text"] = (d["text"] or "")[:280]
            out.append(d)
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------- funnel

def save_funnel(f: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO funnel
               (response_id, run_id, intent_id, engine, judge_model, prompt_version, top_pick,
                products_json, is_ground_truth, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (f["response_id"], f["run_id"], f["intent_id"], f["engine"], f.get("judge_model"),
             f.get("prompt_version"), f.get("top_pick"), _j(f.get("products", [])),
             1 if f.get("is_ground_truth") else 0, f.get("created_at") or now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_funnel(run_id: str) -> list[dict]:
    conn = connect()
    try:
        q = ("SELECT f.*, i.cluster_id AS cluster_id, i.text AS intent_text, i.attributes_json AS intent_attrs "
             "FROM funnel f JOIN intents i ON i.intent_id = f.intent_id WHERE f.run_id=?")
        out = []
        for r in _rows(conn.execute(q, (run_id,))):
            out.append({"response_id": r["response_id"], "run_id": r["run_id"], "intent_id": r["intent_id"],
                        "engine": r["engine"], "judge_model": r["judge_model"],
                        "prompt_version": r["prompt_version"], "top_pick": r["top_pick"],
                        "products": _uj(r["products_json"], []),
                        "is_ground_truth": bool(r["is_ground_truth"]),
                        "cluster_id": r["cluster_id"], "intent_text": r["intent_text"],
                        "intent_attributes": _uj(r["intent_attrs"], [])})
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------- decisions / batches

def save_decision(d: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO decisions
               (decision_id, batch_id, intent_json, candidates_json, winner, per_product_json,
                narrative, model, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (d["decision_id"], d.get("batch_id"), _j(d.get("intent", {})), _j(d.get("candidates", [])),
             d.get("winner"), _j(d.get("per_product", [])), d.get("narrative", ""),
             d.get("model"), d.get("created_at") or now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _decision_from_row(r: dict) -> dict:
    return {"decision_id": r["decision_id"], "batch_id": r["batch_id"],
            "intent": _uj(r["intent_json"], {}), "candidates": _uj(r["candidates_json"], []),
            "winner": r["winner"], "per_product": _uj(r["per_product_json"], []),
            "narrative": r["narrative"], "model": r["model"], "created_at": r["created_at"]}


def get_decision(decision_id: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
        return _decision_from_row(dict(row)) if row else None
    finally:
        conn.close()


def get_decisions_by_batch(batch_id: str) -> list[dict]:
    conn = connect()
    try:
        cur = conn.execute("SELECT * FROM decisions WHERE batch_id=? ORDER BY created_at", (batch_id,))
        return [_decision_from_row(r) for r in _rows(cur)]
    finally:
        conn.close()


def get_decisions_for_product(product_ref: str, limit: int = 400) -> list[dict]:
    """All batch decisions in which product_ref was a candidate (newest batches first)."""
    conn = connect()
    try:
        cur = conn.execute(
            "SELECT * FROM decisions WHERE candidates_json LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f'%"{product_ref}"%', limit))
        return [_decision_from_row(r) for r in _rows(cur)]
    finally:
        conn.close()


def create_batch(b: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO batches
               (batch_id, cluster_id, candidates_json, runs, status, n_intents, shares_json,
                decision_ids_json, error, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (b["batch_id"], b.get("cluster_id"), _j(b.get("candidates", [])), b.get("runs", 3),
             b.get("status", "running"), b.get("n_intents", 0), _j(b.get("shares", {})),
             _j(b.get("decision_ids", [])), b.get("error"),
             b.get("created_at") or now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_batch(batch_id: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        return {"batch_id": r["batch_id"], "cluster_id": r["cluster_id"],
                "candidates": _uj(r["candidates_json"], []), "runs": r["runs"], "status": r["status"],
                "n_intents": r["n_intents"], "shares": _uj(r["shares_json"], {}),
                "decision_ids": _uj(r["decision_ids_json"], []), "error": r["error"],
                "created_at": r["created_at"], "updated_at": r["updated_at"]}
    finally:
        conn.close()


def list_batches(product_ref: Optional[str] = None) -> list[dict]:
    conn = connect()
    try:
        if product_ref:
            cur = conn.execute("SELECT batch_id FROM batches WHERE candidates_json LIKE ? ORDER BY created_at DESC",
                               (f'%"{product_ref}"%',))
        else:
            cur = conn.execute("SELECT batch_id FROM batches ORDER BY created_at DESC")
        return [get_batch(r["batch_id"]) for r in _rows(cur)]
    finally:
        conn.close()


# ---------------------------------------------------------------- diagnoses / debate

def save_diagnosis(key: str, product_ref: str, source: str, payload: dict) -> None:
    conn = connect()
    try:
        conn.execute("INSERT OR REPLACE INTO diagnoses (key, product_ref, source, json, generated_at) VALUES (?,?,?,?,?)",
                     (key, product_ref, source, _j(payload), now_iso()))
        conn.commit()
    finally:
        conn.close()


def get_diagnosis(key: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute("SELECT json FROM diagnoses WHERE key=?", (key,)).fetchone()
        return _uj(row["json"]) if row else None
    finally:
        conn.close()


def create_debate_session(session_id: str, product_ref: str, focus_defect_id: Optional[str],
                          meta: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO debate_sessions (session_id, product_ref, focus_defect_id, messages_json, meta_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (session_id, product_ref, focus_defect_id, _j([]), _j(meta), now_iso(), now_iso()))
        conn.commit()
    finally:
        conn.close()


def get_debate_session(session_id: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM debate_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        return {"session_id": r["session_id"], "product_ref": r["product_ref"],
                "focus_defect_id": r["focus_defect_id"], "messages": _uj(r["messages_json"], []),
                "meta": _uj(r["meta_json"], {}), "created_at": r["created_at"]}
    finally:
        conn.close()


def save_debate_messages(session_id: str, messages: list[dict], meta: Optional[dict] = None) -> None:
    conn = connect()
    try:
        if meta is not None:
            conn.execute("UPDATE debate_sessions SET messages_json=?, meta_json=?, updated_at=? WHERE session_id=?",
                         (_j(messages), _j(meta), now_iso(), session_id))
        else:
            conn.execute("UPDATE debate_sessions SET messages_json=?, updated_at=? WHERE session_id=?",
                         (_j(messages), now_iso(), session_id))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- kv cache / sources

def kv_get(cache_key: str) -> Any:
    conn = connect()
    try:
        row = conn.execute("SELECT value_json FROM kv_cache WHERE cache_key=?", (cache_key,)).fetchone()
        return _uj(row["value_json"]) if row else None
    finally:
        conn.close()


def kv_set(cache_key: str, kind: str, value: Any) -> None:
    conn = connect()
    try:
        conn.execute("INSERT OR REPLACE INTO kv_cache (cache_key, kind, value_json, created_at) VALUES (?,?,?,?)",
                     (cache_key, kind, _j(value), now_iso()))
        conn.commit()
    finally:
        conn.close()


def get_source(url: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_source(url: str, domain: str, title: str, status: str, text: str) -> None:
    conn = connect()
    try:
        conn.execute("INSERT OR REPLACE INTO sources (url, domain, title, status, text, fetched_at) VALUES (?,?,?,?,?,?)",
                     (url, domain, title, status, text, now_iso()))
        conn.commit()
    finally:
        conn.close()
