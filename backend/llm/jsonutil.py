"""Robust JSON extraction from LLM text output."""
from __future__ import annotations

import json
import re
from typing import Any


def _strip_trailing_commas(s: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", s)


def _balanced_slice(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(text: str) -> Any:
    """Best-effort: parse the first JSON object/array found in `text`. Raises ValueError."""
    if not text:
        raise ValueError("empty text")
    # 1. fenced block
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidates = []
    if m:
        candidates.append(m.group(1).strip())
    # 2. whole text
    candidates.append(text.strip())
    # 3. first balanced object / array
    for oc, cc in (("{", "}"), ("[", "]")):
        sl = _balanced_slice(text, oc, cc)
        if sl:
            candidates.append(sl)
    for cand in candidates:
        for attempt in (cand, _strip_trailing_commas(cand)):
            try:
                return json.loads(attempt)
            except Exception:
                continue
    raise ValueError("no parseable JSON found in text")
