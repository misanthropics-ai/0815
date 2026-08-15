"""In-process event log (ring buffer) exposed at GET /logs.

Hackathon-grade observability: every Bedrock call, batch, and diagnosis stage
records an event with duration so "why is it slow" is answerable from the
browser. Not persisted; survives only as long as the process.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone

_BUF: deque = deque(maxlen=800)
_LOCK = threading.Lock()
_T0 = time.time()


def log(event: str, **fields) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "uptime_s": round(time.time() - _T0, 1),
        "event": event,
        **fields,
    }
    with _LOCK:
        _BUF.append(rec)


def recent(n: int = 200, event_prefix: str | None = None) -> list[dict]:
    with _LOCK:
        items = list(_BUF)
    if event_prefix:
        items = [r for r in items if str(r.get("event", "")).startswith(event_prefix)]
    return items[-n:]
