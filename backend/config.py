"""Central configuration.

Load order: backend/.env first (setdefault), then process environment wins.
BEDROCK_MODEL left empty means "auto-discover at startup" (see llm/bedrock.py).
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
FIXTURES_DIR = BACKEND_DIR / "mock_fixtures"
PROMPTS_DIR = BACKEND_DIR / "prompts"
SEEDS_DIR = BACKEND_DIR / "seeds"
TAXONOMY_PATH = BACKEND_DIR / "taxonomy" / "taxonomy.json"
PERSONAS_DIR = BACKEND_DIR / "personas"
PERSONAS_PATH = PERSONAS_DIR / "default.json"
DB_PATH = DATA_DIR / "app.db"
IMPACT_DB_PATH = DATA_DIR / "impact_demo.db"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key, value)


_load_dotenv(BACKEND_DIR / ".env")


def env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key)
    return v if v not in (None, "") else default


# --- AWS / Bedrock ---
AWS_REGION = env("AWS_DEFAULT_REGION") or env("AWS_REGION") or "us-east-1"
BEDROCK_MODEL = env("BEDROCK_MODEL")            # None => auto-discover
BEDROCK_FAST_MODEL = env("BEDROCK_FAST_MODEL")  # None => same as BEDROCK_MODEL (or discovered haiku)
BEDROCK_MAX_CONCURRENCY = int(env("BEDROCK_MAX_CONCURRENCY", "4"))
LLM_TIMEOUT_S = int(env("LLM_TIMEOUT_S", "180"))

# --- Engines (ALL LLM calls go through AWS Bedrock; external engines are future drop-ins) ---
DEFAULT_ENGINES = (env("DEFAULT_ENGINES") or "sim-sonnet,sim-haiku").split(",")

# --- Pipeline knobs ---
ENGINE_CONCURRENCY = int(env("ENGINE_CONCURRENCY", "6"))
JUDGE_CONCURRENCY = int(env("JUDGE_CONCURRENCY", "4"))
BATCH_CONCURRENCY = int(env("BATCH_CONCURRENCY", "10"))
SOURCE_FETCH_TIMEOUT_S = float(env("SOURCE_FETCH_TIMEOUT_S", "8"))
SOURCE_FETCH_MAX = int(env("SOURCE_FETCH_MAX", "40"))

# --- Server ---
DEFAULT_MODE = env("MODE", "auto")  # mock | live | auto
PORT = int(env("PORT", "8000"))
