"""AWS Bedrock LLM client (Converse API).

- Model auto-discovery: picks the best available Anthropic model (fallback Nova)
  for this account/region, verified with a tiny converse call, cached in data/.
- complete(): plain text.  complete_json(): schema-forced via toolConfig with
  graceful fallbacks.  stream(): text chunk iterator (converse_stream).
- Built-in throttle retry with exponential backoff (hackathon quotas are low).
- All sync; async wrappers at the bottom run in threads behind a shared semaphore.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from backend import config

# boto3 imported lazily so the mock-only path works without it installed
_boto3 = None


def _boto():
    global _boto3
    if _boto3 is None:
        import boto3  # type: ignore
        _boto3 = boto3
    return _boto3


class LLMError(Exception):
    def __init__(self, message: str, code: str = "llm_error"):
        super().__init__(message)
        self.code = code


RETRYABLE = {"ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
             "InternalServerException", "ModelNotReadyException", "ModelTimeoutException",
             "ModelStreamErrorException"}
AUTH_ERRORS = {"ExpiredTokenException", "UnrecognizedClientException", "InvalidSignatureException",
               "ExpiredToken", "InvalidClientTokenId"}

SMART_PREF = [r"claude-sonnet-5", r"claude-5-sonnet", r"claude-opus-5", r"claude-sonnet-4-[6-9]",
              r"claude-sonnet-4-5", r"claude-opus-4-[5-9]", r"claude-sonnet-4-\d{8}", r"claude-opus-4-1", r"claude-3-7-sonnet",
              r"claude-3-5-sonnet-20241022", r"claude-3-5-sonnet", r"claude-haiku-4-5",
              r"claude-3-5-haiku", r"nova-pro", r"claude-3-sonnet", r"nova-lite", r"claude-3-haiku"]
FAST_PREF = [r"claude-haiku-4-5", r"claude-3-5-haiku", r"nova-lite", r"claude-sonnet-4-5",
             r"claude-sonnet-4-\d{8}", r"claude-3-7-sonnet", r"claude-3-5-sonnet",
             r"claude-3-haiku", r"nova-pro", r"nova-micro"]

# Used when bedrock:ListFoundationModels is denied — verified one by one.
FALLBACK_IDS = [
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-3-5-haiku-20241022-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.amazon.nova-lite-v1:0",
]

MODEL_CACHE = config.DATA_DIR / "bedrock_models.json"


def _err_code(e: Exception) -> str:
    resp = getattr(e, "response", None)
    if isinstance(resp, dict):
        return resp.get("Error", {}).get("Code", "") or ""
    return ""


class BedrockLLM:
    def __init__(self) -> None:
        self._rt = None
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(config.BEDROCK_MAX_CONCURRENCY)
        self._ready = False
        self.smart: Optional[str] = config.BEDROCK_MODEL
        self.fast: Optional[str] = config.BEDROCK_FAST_MODEL
        self.error: Optional[str] = None
        self.discovered: list[str] = []

    # ------------------------------------------------------------ setup

    def _runtime(self):
        if self._rt is None:
            from botocore.config import Config as BotoConfig  # type: ignore
            self._rt = _boto().client(
                "bedrock-runtime", region_name=config.AWS_REGION,
                config=BotoConfig(read_timeout=config.LLM_TIMEOUT_S, connect_timeout=10,
                                  retries={"max_attempts": 0}))
        return self._rt

    def _verify(self, model_id: str) -> bool:
        from botocore.exceptions import NoCredentialsError  # type: ignore
        for attempt in range(2):
            try:
                self._runtime().converse(
                    modelId=model_id,
                    messages=[{"role": "user", "content": [{"text": "ping"}]}],
                    inferenceConfig={"maxTokens": 8})
                return True
            except NoCredentialsError as e:
                raise LLMError("no AWS credentials available (env vars or instance role)",
                               code="aws_auth") from e
            except Exception as e:
                code = _err_code(e)
                if code in AUTH_ERRORS:
                    raise LLMError(f"AWS credentials invalid/expired ({code}). Refresh backend/.env.",
                                   code="aws_auth") from e
                if code in RETRYABLE and attempt == 0:
                    time.sleep(2.0)
                    continue
                return False
        return False

    def _candidates_from_listing(self) -> list[str]:
        br = _boto().client("bedrock", region_name=config.AWS_REGION)
        out = br.list_foundation_models(byOutputModality="TEXT")
        ids: list[str] = []
        for m in out.get("modelSummaries", []):
            mid = m.get("modelId", "")
            inf = m.get("inferenceTypesSupported", []) or []
            if "ON_DEMAND" in inf:
                ids.append(mid)
            elif "INFERENCE_PROFILE" in inf:
                ids.append(f"us.{mid}")
                ids.append(f"global.{mid}")
        return ids

    @staticmethod
    def _pick(prefs: list[str], candidates: list[str]) -> list[str]:
        ranked: list[str] = []
        for pat in prefs:
            for cid in candidates:
                if re.search(pat, cid) and cid not in ranked:
                    ranked.append(cid)
        return ranked

    def ensure_ready(self, force: bool = False) -> bool:
        with self._lock:
            if self._ready and not force:
                return self.error is None and bool(self.smart)
            self.error = None
            try:
                # NOTE: no env-var check here — boto3 resolves credentials from the
                # standard chain (env vars, shared config, EC2/ECS instance role).
                # explicit override
                if config.BEDROCK_MODEL:
                    self.smart = config.BEDROCK_MODEL
                    self.fast = config.BEDROCK_FAST_MODEL or config.BEDROCK_MODEL
                    if not self._verify(self.smart):
                        self.error = f"configured BEDROCK_MODEL not usable: {self.smart}"
                        self._ready = True
                        return False
                    self._ready = True
                    return True
                # cached discovery
                if MODEL_CACHE.exists() and not force:
                    try:
                        cached = json.loads(MODEL_CACHE.read_text())
                        if cached.get("region") == config.AWS_REGION and cached.get("smart"):
                            if self._verify(cached["smart"]):
                                self.smart = cached["smart"]
                                self.fast = cached.get("fast") or cached["smart"]
                                self._ready = True
                                return True
                    except LLMError:
                        raise
                    except Exception:
                        pass
                # live discovery
                try:
                    candidates = self._candidates_from_listing()
                except LLMError:
                    raise
                except Exception:
                    candidates = list(FALLBACK_IDS)
                self.discovered = candidates
                smart = next((c for c in self._pick(SMART_PREF, candidates) if self._verify(c)), None)
                if not smart:
                    smart = next((c for c in FALLBACK_IDS if self._verify(c)), None)
                if not smart:
                    self.error = "no usable Bedrock text model found (check model access in console)"
                    self._ready = True
                    return False
                fast = next((c for c in self._pick(FAST_PREF, candidates)
                             if (c == smart or self._verify(c))), None) or smart
                self.smart, self.fast = smart, fast
                config.DATA_DIR.mkdir(parents=True, exist_ok=True)
                MODEL_CACHE.write_text(json.dumps(
                    {"region": config.AWS_REGION, "smart": smart, "fast": fast,
                     "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=2))
                self._ready = True
                return True
            except LLMError as e:
                self.error = str(e)
                self._ready = True
                return False
            except Exception as e:  # e.g. boto3 missing / no network
                self.error = f"bedrock init failed: {e}"
                self._ready = True
                return False

    def available(self) -> bool:
        return self.ensure_ready()

    # ------------------------------------------------------------ core call

    def _converse(self, *, model: str, messages: list[dict], system: Optional[str],
                  max_tokens: int, temperature: float, tool_config: Optional[dict] = None) -> dict:
        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if tool_config:
            kwargs["toolConfig"] = tool_config
        attempts = 6
        for attempt in range(attempts):
            try:
                with self._sem:
                    return self._runtime().converse(**kwargs)
            except Exception as e:
                code = _err_code(e)
                if code in AUTH_ERRORS:
                    raise LLMError(f"AWS credentials invalid/expired ({code}). Refresh backend/.env.",
                                   code="aws_auth") from e
                if code in RETRYABLE and attempt < attempts - 1:
                    time.sleep(min(25.0, (1.4 * (2 ** attempt)) + random.uniform(0, 1.2)))
                    continue
                if code:
                    raise LLMError(f"bedrock error {code}: {e}", code=code) from e
                raise LLMError(f"bedrock call failed: {e}") from e
        raise LLMError("bedrock call failed after retries", code="retry_exhausted")

    @staticmethod
    def _msgs(prompt: Optional[str], messages: Optional[list[dict]]) -> list[dict]:
        if messages is not None:
            out = []
            for m in messages:
                content = m["content"]
                if isinstance(content, str):
                    content = [{"text": content}]
                out.append({"role": m["role"], "content": content})
            return out
        return [{"role": "user", "content": [{"text": prompt or ""}]}]

    def complete(self, prompt: Optional[str] = None, *, messages: Optional[list[dict]] = None,
                 system: Optional[str] = None, model: Optional[str] = None,
                 max_tokens: int = 1500, temperature: float = 0.3) -> str:
        if not self.available():
            raise LLMError(self.error or "bedrock unavailable", code="unavailable")
        out = self._converse(model=model or self.smart, messages=self._msgs(prompt, messages),
                             system=system, max_tokens=max_tokens, temperature=temperature)
        parts = out.get("output", {}).get("message", {}).get("content", [])
        return "".join(p.get("text", "") for p in parts if "text" in p)

    def complete_json(self, prompt: Optional[str] = None, *, schema: dict,
                      messages: Optional[list[dict]] = None, system: Optional[str] = None,
                      model: Optional[str] = None, max_tokens: int = 3000,
                      temperature: float = 0.0, cache_key: Optional[str] = None,
                      tool_name: str = "emit", tool_description: str = "Emit the structured result.") -> Any:
        from backend.storage import db
        from backend.llm.jsonutil import extract_json
        if cache_key:
            hit = db.kv_get(f"llm:{cache_key}")
            if hit is not None:
                return hit
        if not self.available():
            raise LLMError(self.error or "bedrock unavailable", code="unavailable")
        mid = model or self.smart
        msgs = self._msgs(prompt, messages)
        tool = {"toolSpec": {"name": tool_name, "description": tool_description,
                             "inputSchema": {"json": schema}}}
        result: Any = None
        # ladder: forced tool -> any tool -> plain text JSON
        for tool_choice in ({"tool": {"name": tool_name}}, {"any": {}}, None):
            try:
                if tool_choice is not None:
                    out = self._converse(model=mid, messages=msgs, system=system,
                                         max_tokens=max_tokens, temperature=temperature,
                                         tool_config={"tools": [tool], "toolChoice": tool_choice})
                    content = out.get("output", {}).get("message", {}).get("content", [])
                    for item in content:
                        tu = item.get("toolUse")
                        if tu and tu.get("name") == tool_name:
                            result = tu.get("input")
                            break
                    if result is not None:
                        break
                else:
                    schema_hint = json.dumps(schema, ensure_ascii=False)
                    sys2 = (system or "") + "\nRespond ONLY with a single JSON value matching this JSON Schema, no prose:\n" + schema_hint
                    text = self.complete(messages=msgs, system=sys2, model=mid,
                                         max_tokens=max_tokens, temperature=temperature)
                    result = extract_json(text)
                    break
            except LLMError as e:
                if e.code in ("aws_auth", "unavailable"):
                    raise
                continue  # try next rung
            except Exception:
                continue
        if result is None:
            raise LLMError("structured output failed on all fallbacks", code="json_failed")
        if cache_key:
            db.kv_set(f"llm:{cache_key}", "llm", result)
        return result

    def stream(self, prompt: Optional[str] = None, *, messages: Optional[list[dict]] = None,
               system: Optional[str] = None, model: Optional[str] = None,
               max_tokens: int = 1500, temperature: float = 0.4) -> Iterator[str]:
        if not self.available():
            raise LLMError(self.error or "bedrock unavailable", code="unavailable")
        kwargs: dict[str, Any] = {
            "modelId": model or self.smart,
            "messages": self._msgs(prompt, messages),
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        attempts = 4
        with self._sem:
            for attempt in range(attempts):
                try:
                    resp = self._runtime().converse_stream(**kwargs)
                    for ev in resp["stream"]:
                        delta = ev.get("contentBlockDelta", {}).get("delta", {})
                        if "text" in delta:
                            yield delta["text"]
                    return
                except Exception as e:
                    code = _err_code(e)
                    if code in AUTH_ERRORS:
                        raise LLMError(f"AWS credentials invalid/expired ({code}).", code="aws_auth") from e
                    if code in RETRYABLE and attempt < attempts - 1:
                        time.sleep(min(20.0, (1.4 * (2 ** attempt)) + random.uniform(0, 1.0)))
                        continue
                    raise LLMError(f"bedrock stream failed: {e}", code=code or "stream_error") from e


_INSTANCE: Optional[BedrockLLM] = None
_INSTANCE_LOCK = threading.Lock()


def get_bedrock() -> BedrockLLM:
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = BedrockLLM()
        return _INSTANCE


def cache_key_for(*parts: Any) -> str:
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


# ------------------------------------------------------------ async wrappers

async def acomplete(**kw) -> str:
    return await asyncio.to_thread(get_bedrock().complete, **kw)


async def acomplete_json(**kw) -> Any:
    return await asyncio.to_thread(lambda: get_bedrock().complete_json(**kw))


async def astream(**kw):
    """Async generator over stream() chunks."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def worker() -> None:
        try:
            for chunk in get_bedrock().stream(**kw):
                loop.call_soon_threadsafe(q.put_nowait, ("tok", chunk))
        except Exception as e:
            loop.call_soon_threadsafe(q.put_nowait, ("err", e))
        finally:
            loop.call_soon_threadsafe(q.put_nowait, ("end", None))

    threading.Thread(target=worker, daemon=True).start()
    while True:
        kind, val = await q.get()
        if kind == "tok":
            yield val
        elif kind == "err":
            raise val
        else:
            break
