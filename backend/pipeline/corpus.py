"""Retrieval corpus for the simulated answer engines.

Documents = product pages (from DB, at pinned versions) + bundled third-party
sources (seeds/sources/*.json). Retrieval is deterministic lexical top-k; the
set of retrieved docs is the engine's "search trace" and becomes its citations.
Editing a product page (v2) changes retrieval + evidence => before/after works
mechanically.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from backend import config
from backend.storage import db
from backend.taxonomy import load_taxonomy

STOP = {"the", "a", "an", "and", "or", "for", "with", "of", "to", "in", "on", "is", "it", "that",
        "i", "im", "my", "me", "at", "as", "be", "do", "what", "which", "who", "how", "should",
        "will", "can", "get", "one", "need", "want", "going", "best", "good", "vs"}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOP and len(t) > 1]


@dataclass
class Doc:
    doc_id: str
    kind: str                  # product_page | third_party
    brands: list[str]          # brand slugs substantially covered by this doc
    title: str
    url: str
    text: str
    product_ref: Optional[str] = None
    _tokens: Counter = field(default_factory=Counter, repr=False)


@dataclass
class Corpus:
    docs: list[Doc]
    hash: str

    def docs_for_brand(self, brand_slug: str) -> list[Doc]:
        return [d for d in self.docs if brand_slug in d.brands]


def load_seed_sources() -> list[Doc]:
    docs = []
    src_dir = config.SEEDS_DIR / "sources"
    if src_dir.exists():
        for p in sorted(src_dir.glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            docs.append(Doc(doc_id=d["doc_id"], kind=d.get("kind", "third_party"),
                            brands=d.get("brands", []), title=d["title"], url=d["url"],
                            text=d["text"]))
    return docs


def product_doc(p: dict) -> Doc:
    ref = p.get("ref") or f"{p['product_id']}@v{p.get('version', 1)}"
    return Doc(doc_id=f"product:{ref}", kind="product_page", brands=[slugify(p.get("brand", ""))],
               title=f"{p.get('display_name')} — official product page",
               url=p.get("source_url") or f"https://local.page/{p['product_id']}",
               text=p.get("raw_text", ""), product_ref=ref)


def build_corpus(product_refs: list[str]) -> Corpus:
    docs: list[Doc] = []
    for ref in product_refs:
        p = db.get_product_by_ref(ref)
        if p:
            docs.append(product_doc(p))
    docs.extend(load_seed_sources())
    for d in docs:
        d._tokens = Counter(tokenize(d.text + " " + d.title))
    blob = "|".join(f"{d.doc_id}:{hashlib.sha1(d.text.encode()).hexdigest()[:10]}" for d in docs)
    return Corpus(docs=docs, hash=hashlib.sha1(blob.encode()).hexdigest()[:12])


def retrieve(corpus: Corpus, query: str, extra_keywords: Optional[list[str]] = None,
             k: int = 4, seed: Optional[str] = None) -> list[dict]:
    """Deterministic lexical top-k. Returns [{doc, score}] sorted desc."""
    q = set(tokenize(query))
    kw = set(tokenize(" ".join(extra_keywords or [])))
    scored = []
    for d in corpus.docs:
        s = 0.0
        for t in q:
            if d._tokens.get(t):
                s += 1.0 + math.log(1 + d._tokens[t])
        for t in kw - q:
            if d._tokens.get(t):
                s += 1.2 + 0.8 * math.log(1 + d._tokens[t])
        s /= (1.0 + sum(d._tokens.values()) / 900.0)
        if seed is not None:
            s += random.Random(f"{seed}:{d.doc_id}").uniform(0, 0.25)
        if s > 0:
            scored.append({"doc": d, "score": round(s, 3)})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]


def attr_keywords(attribute_ids: list[str], category: Optional[str] = None) -> list[str]:
    tax = load_taxonomy(category)
    out: list[str] = []
    for a in tax["attributes"]:
        if a["id"] in attribute_ids:
            out.extend(a.get("keywords", [])[:8])
    return out
