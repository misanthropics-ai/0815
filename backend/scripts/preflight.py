"""Preflight: verify AWS creds + discover usable Bedrock models.

Run from repo root:  python -m backend.scripts.preflight [--force]
"""
from __future__ import annotations

import sys

from backend import config
from backend.llm.bedrock import get_bedrock


def main() -> int:
    force = "--force" in sys.argv
    print(f"region          : {config.AWS_REGION}")
    print(f"aws key present : {bool(config.env('AWS_ACCESS_KEY_ID'))}")
    try:
        import boto3
        sts = boto3.client("sts", region_name=config.AWS_REGION)
        ident = sts.get_caller_identity()
        print(f"caller identity : {ident.get('Arn')}")
    except Exception as e:
        print(f"caller identity : FAILED ({e})")
    br = get_bedrock()
    ok = br.ensure_ready(force=force)
    print(f"bedrock ready   : {ok}")
    print(f"smart model     : {br.smart}")
    print(f"fast model      : {br.fast}")
    if br.error:
        print(f"error           : {br.error}")
    if ok:
        try:
            txt = br.complete("Reply with exactly: OK", max_tokens=10, temperature=0)
            print(f"live check      : {txt.strip()[:40]}")
        except Exception as e:
            print(f"live check      : FAILED ({e})")
    anthropic_models = [m for m in br.discovered if "anthropic" in m]
    if anthropic_models:
        print("anthropic models visible:")
        for m in sorted(set(anthropic_models)):
            print(f"  - {m}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
