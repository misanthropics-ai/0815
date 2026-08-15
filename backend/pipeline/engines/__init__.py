from __future__ import annotations

from backend.pipeline.engines.base import Engine, EngineResult, RunContext, default_engines
from backend.pipeline.engines.claude_sim import SIM_PROFILES, ClaudeSimEngine
from backend.pipeline.engines.mock_engine import MockEngine


def make_engine(name: str) -> Engine:
    if name == "mock":
        return MockEngine()
    if name in SIM_PROFILES:
        return ClaudeSimEngine(name)
    raise ValueError(f"unknown engine: {name}")


def engine_status() -> dict[str, bool]:
    out = {"mock": True}
    for n in SIM_PROFILES:
        out[n] = ClaudeSimEngine(n).available()
    return out


__all__ = ["Engine", "EngineResult", "RunContext", "default_engines", "make_engine",
           "engine_status", "SIM_PROFILES"]
