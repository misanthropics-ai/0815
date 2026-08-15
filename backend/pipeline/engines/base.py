"""Engine abstraction for Stage 2 (query execution).

All current engines run on AWS Bedrock (team decision: LLM = AWS only).
External engines (ChatGPT search / Perplexity / Gemini) can be added later by
implementing Engine and registering in ENGINE_FACTORIES — the pipeline,
funnel parser and aggregations are engine-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from backend import config


@dataclass
class RunContext:
    run_cfg: dict                 # normalized run config (brand, competitors, product_refs, ...)
    corpus: object                # pipeline.corpus.Corpus
    mock_world: dict              # mock behavior config
    brand_slugs: dict             # {"target": slug, "competitors": [slug...], "all": {slug: display}}


@dataclass
class EngineResult:
    engine: str
    model: Optional[str]
    status: str = "ok"            # ok | error
    text: str = ""
    citations: list = field(default_factory=list)       # [{url, title, doc_id?, brands?, score?}]
    search_queries: list = field(default_factory=list)
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    ground_truth: Optional[dict] = None                 # funnel-shaped annotation (mock / sim)


class Engine:
    name: str = "base"

    def available(self) -> bool:
        return True

    async def run(self, intent: dict, ctx: RunContext) -> EngineResult:  # pragma: no cover
        raise NotImplementedError


def default_engines(mode: str) -> list[str]:
    from backend.llm.bedrock import get_bedrock
    if mode == "mock":
        return ["mock"]
    if get_bedrock().available():
        return [e.strip() for e in config.DEFAULT_ENGINES if e.strip()]
    return ["mock"]
