#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def api_json(base: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cached", action="store_true")
    parser.add_argument("--mode", choices=("auto", "mock", "live"), default="auto")
    parser.add_argument("--output", type=Path, default=HERE / "latest.compare.json")
    args = parser.parse_args()
    config = json.loads((HERE / "experiment.config.json").read_text(encoding="utf-8"))
    batches = {}
    for label, ref in (("a", config["baseline_ref"]), ("b", config["treatment_ref"])):
        batches[label] = api_json(
            args.base_url,
            "POST",
            "/simulate/batch",
            {
                "cluster_id": config["cluster_id"],
                "candidates": [ref, *config["fixed_competitors"]],
                "runs": config["runs"],
                "max_intents": config["max_intents"],
                "wait": True,
                "cached": args.cached or config["cached"],
                "mode": args.mode,
            },
        )
        if batches[label].get("status") != "completed":
            raise SystemExit(f"{label} batch did not complete")
        if batches[label].get("n_intents") != config["expected_intents"]:
            raise SystemExit(
                f"{label} used {batches[label].get('n_intents')} intents; "
                f"expected {config['expected_intents']}"
            )
        if batches[label].get("n_decisions") != config["expected_decisions_per_side"]:
            raise SystemExit(
                f"{label} produced {batches[label].get('n_decisions')} decisions; "
                f"expected {config['expected_decisions_per_side']}"
            )
    query = urllib.parse.urlencode(
        {
            "a": config["baseline_ref"],
            "b": config["treatment_ref"],
            "cluster": config["cluster_id"],
        }
    )
    compare = api_json(args.base_url, "GET", f"/metrics/compare?{query}")
    if "delta_recommendation" not in compare:
        raise SystemExit(f"compare result is not ready: {compare}")
    output = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "cached_request": args.cached,
        "config": config,
        "batches": batches,
        "compare": compare,
        "acceptance": {
            "minimum_delta": config["minimum_delta_recommendation"],
            "passed": compare["delta_recommendation"] >= config["minimum_delta_recommendation"],
        },
    }
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output["acceptance"], indent=2))
    return 0 if output["acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
