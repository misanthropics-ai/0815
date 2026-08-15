#!/usr/bin/env python3
"""Make a minimal Bedrock request using the standard AWS credential chain."""

from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from dotenv import load_dotenv


def main() -> int:
    load_dotenv()

    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    model_id = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")

    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        response = client.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": "Reply with exactly: Bedrock is ready"}],
                }
            ],
            inferenceConfig={"maxTokens": 8, "temperature": 0},
        )
    except NoCredentialsError:
        print(
            "AWS credentials were not found. Export temporary credentials first.", file=sys.stderr
        )
        return 2
    except (BotoCoreError, ClientError) as exc:
        print(f"Bedrock check failed: {exc}", file=sys.stderr)
        return 1

    reply = response["output"]["message"]["content"][0]["text"]
    usage = response.get("usage", {})
    print(f"region: {region}")
    print(f"model: {model_id}")
    print(f"reply: {reply}")
    print(f"tokens: {usage.get('totalTokens', 'unknown')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
