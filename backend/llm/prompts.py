"""Versioned prompt loader.

Prompts live as .md files: names without '/' resolve to backend/prompts/{name}.md;
names with '/' resolve relative to backend/ (e.g. "decision/prompts/prompt_v1").
Placeholders use {{key}} (safe with JSON braces inside templates).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from backend import config


@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    if "/" in name:
        path = config.BACKEND_DIR / f"{name}.md"
    else:
        path = config.PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def render(template: str, **kw: Any) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{{" + k + "}}", v if isinstance(v, str) else str(v))
    return out


def render_prompt(name: str, **kw: Any) -> str:
    return render(load_prompt(name), **kw)
