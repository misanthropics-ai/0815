"""Contract validation: fixtures vs schemas.py, plus optional live endpoint probe.

Usage (repo root):
  python contracts/check_contract.py                 # validate mock_fixtures only
  python contracts/check_contract.py http://localhost:8000   # + probe live endpoints
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from contracts import schemas  # noqa: E402

FIXTURES = ROOT / "backend" / "mock_fixtures"
PERSONAS_DIR = ROOT / "backend" / "personas"

FIXTURE_MODELS = {
    "response.post_products.manual.json": schemas.Product,
    "product.cabinzero-classic-36l.json": schemas.Product,
    "product.cabinzero-classic-36l.v2.json": schemas.Product,
    "product.osprey-farpoint-40.json": schemas.Product,
    "response.decision_result.json": schemas.DecisionResult,
    "response.simulate_batch.json": schemas.BatchResult,
    "response.diagnosis.json": schemas.Diagnosis,
    "response.get_debate_session.json": schemas.DebateSession,
    "response.metrics_compare.json": schemas.CompareResult,
    "response.error.sample.json": schemas.ErrorResponse,
    "response.run_status.json": schemas.RunStatus,
}

REQUEST_MODELS = {
    "request.post_products.manual.json": schemas.CreateProductRequest,
    "request.post_simulate.json": schemas.SimulateRequest,
    "request.post_simulate_batch.json": schemas.BatchRequest,
    "request.post_debate_session.json": schemas.CreateDebateRequest,
    "request.post_runs.json": schemas.RunCreateRequest,
}


def check_fixtures() -> int:
    fails = 0
    for name, model in {**FIXTURE_MODELS, **REQUEST_MODELS}.items():
        path = FIXTURES / name
        if not path.exists():
            print(f"  MISSING  {name}")
            fails += 1
            continue
        try:
            model.model_validate(json.loads(path.read_text(encoding="utf-8")))
            print(f"  OK       {name}")
        except Exception as e:
            print(f"  FAIL     {name}: {str(e)[:160]}")
            fails += 1

    try:
        payload = json.loads((FIXTURES / "intents.sample.json").read_text(encoding="utf-8"))
        for intent in payload["intents"]:
            schemas.Intent.model_validate(intent)
        print("  OK       intents.sample.json")
    except Exception as e:
        print(f"  FAIL     intents.sample.json: {str(e)[:160]}")
        fails += 1

    for path in sorted(PERSONAS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            profiles = [schemas.PersonaProfile.model_validate(item) for item in payload["profiles"]]
            ids = [profile.persona_id for profile in profiles]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate persona_id")
            print(f"  OK       personas/{path.name}")
        except Exception as e:
            print(f"  FAIL     personas/{path.name}: {str(e)[:160]}")
            fails += 1
    return fails


def probe_live(base: str) -> int:
    import urllib.request

    def get(path: str):
        with urllib.request.urlopen(base + path, timeout=30) as r:
            return r.status, json.loads(r.read().decode())

    fails = 0
    checks = [
        ("/health", None),
        ("/taxonomy", None),
        ("/products", None),
        ("/products/cabinzero-classic-36l@v1", schemas.Product),
    ]
    for path, model in checks:
        try:
            status, body = get(path)
            if model:
                model.model_validate(body)
            print(f"  OK       GET {path} ({status})")
        except Exception as e:
            print(f"  FAIL     GET {path}: {str(e)[:140]}")
            fails += 1
    return fails


def main() -> int:
    print("== fixtures ==")
    fails = check_fixtures()
    if len(sys.argv) > 1:
        print(f"== live probe {sys.argv[1]} ==")
        fails += probe_live(sys.argv[1].rstrip("/"))
    print("PASS" if fails == 0 else f"{fails} FAILURES")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
