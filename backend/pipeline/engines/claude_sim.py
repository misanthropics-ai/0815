"""Bedrock-simulated answer engine ("controlled simulation").

Mechanics per query: lexical retrieval over the corpus (search trace) ->
sources go into the prompt -> Claude answers as a shopping assistant using
ONLY those sources -> retrieved docs become citations. Two default profiles
(sonnet / haiku) give engine diversity. Responses are cached by
(engine, model, intent text, corpus hash) so re-runs are free and v2 page
edits (corpus hash change) naturally invalidate.
"""
from __future__ import annotations

import time
from typing import Optional

from backend.llm import bedrock
from backend.llm.bedrock import LLMError, cache_key_for, get_bedrock
from backend.llm.prompts import render_prompt
from backend.pipeline import corpus as corpus_mod
from backend.pipeline.engines.base import Engine, EngineResult, RunContext
from backend.storage import db

PROMPT_VERSION = "decision/prompts/prompt_v1"

SIM_PROFILES = {
    "sim-sonnet": {
        "model_attr": "smart",
        "persona": "a thorough AI shopping assistant with live web search",
        "style_hint": "balanced and specific, weighing tradeoffs",
        "length_hint": "300-400 words",
        "temperature": 0.5,
    },
    "sim-haiku": {
        "model_attr": "fast",
        "persona": "a fast, concise AI shopping assistant with live web search",
        "style_hint": "brisk and direct, bottom-line first",
        "length_hint": "180-250 words",
        "temperature": 0.6,
    },
}


class ClaudeSimEngine(Engine):
    def __init__(self, name: str):
        if name not in SIM_PROFILES:
            raise ValueError(f"unknown sim profile: {name}")
        self.name = name
        self.profile = SIM_PROFILES[name]

    def available(self) -> bool:
        return get_bedrock().available()

    def _model(self) -> Optional[str]:
        br = get_bedrock()
        return getattr(br, self.profile["model_attr"], None) or br.smart

    async def run(self, intent: dict, ctx: RunContext) -> EngineResult:
        t0 = time.time()
        model = self._model()
        kw = corpus_mod.attr_keywords(
            intent.get("attributes", []), category=ctx.run_cfg.get("category")
        )
        hits = corpus_mod.retrieve(ctx.corpus, intent["text"], kw, k=4,
                                   seed=f"{intent['intent_id']}:{self.name}")
        citations = [{"url": h["doc"].url, "title": h["doc"].title, "doc_id": h["doc"].doc_id,
                      "brands": h["doc"].brands, "score": h["score"]} for h in hits]
        lines = []
        for i, h in enumerate(hits, 1):
            d = h["doc"]
            lines.append(f"[S{i}] {d.title}\nURL: {d.url}\n{d.text[:1100]}\n")
        prompt = render_prompt(
            PROMPT_VERSION,
            engine_persona=self.profile["persona"],
            intent_text=intent["text"],
            sources_block="\n".join(lines) if lines else "(no sources found)",
            style_hint=self.profile["style_hint"],
            length_hint=self.profile["length_hint"],
        )
        ck = cache_key_for("simresp", self.name, model, intent["text"], ctx.corpus.hash, PROMPT_VERSION)
        cached = db.kv_get(f"llm:{ck}")
        if cached is not None:
            text = cached
        else:
            try:
                text = await bedrock.acomplete(prompt=prompt, model=model, max_tokens=1100,
                                               temperature=self.profile["temperature"])
                db.kv_set(f"llm:{ck}", "simresp", text)
            except LLMError as e:
                return EngineResult(engine=self.name, model=model, status="error", error=str(e),
                                    citations=citations, search_queries=[intent["text"]],
                                    latency_ms=int((time.time() - t0) * 1000))
        return EngineResult(engine=self.name, model=model, text=text, citations=citations,
                            search_queries=[intent["text"]],
                            latency_ms=int((time.time() - t0) * 1000))
