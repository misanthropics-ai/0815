"""AI Recommendation Diagnostics — backend API (P1+P2+P3).

Run from repo root:  backend/run.sh   (or: uvicorn backend.app:app --port 8000)
All LLM calls go through AWS Bedrock; with no AWS creds every endpoint still
works in mock mode. Errors: {"error": {"code", "message"}}.
SSE events: token / action / progress / result-in-done / error / done.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend import config
from backend.llm.bedrock import LLMError, get_bedrock
from backend.pipeline import funnel as funnel_mod
from backend.pipeline import runner
from backend.pipeline.engines import engine_status
from backend.storage import db, impact_db
from backend.taxonomy import load_taxonomy
from contracts.schemas import RunCreateRequest

app = FastAPI(title="AI Recommendation Diagnostics API", version="v3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- errors


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(ValueError)
async def _val_err(_: Request, exc: ValueError):
    code = getattr(exc, "code", "bad_request")
    hint = getattr(exc, "hint", None)
    status = 422 if hint or code != "bad_request" else 400
    body = {"error": {"code": code, "message": str(exc)}}
    if hint:
        body["error"]["hint"] = hint
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(KeyError)
async def _key_err(_: Request, exc: KeyError):
    return _err(404, "not_found", str(exc.args[0]) if exc.args else "not found")


@app.exception_handler(LLMError)
async def _llm_err(_: Request, exc: LLMError):
    return _err(503, exc.code, str(exc))


@app.exception_handler(Exception)
async def _any_err(_: Request, exc: Exception):
    return _err(500, "internal", f"{type(exc).__name__}: {exc}")


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


# ---------------------------------------------------------------- startup


@app.on_event("startup")
async def _startup() -> None:
    from backend.seeds.seed import seed_all
    from backend.taxonomy.builder import restore_learned_taxonomies

    await asyncio.to_thread(seed_all)
    await asyncio.to_thread(restore_learned_taxonomies)  # learned taxonomies: DB -> files
    # warm bedrock discovery off the request path
    asyncio.get_running_loop().run_in_executor(None, get_bedrock().ensure_ready)


# ---------------------------------------------------------------- health / meta


@app.get("/")
async def index():
    return {
        "service": "AI Recommendation Diagnostics API",
        "version": "v3",
        "interactive_docs": "/docs",
        "health": "/health",
        "browse": [
            "/health",
            "/impact-demo",
            "/docs",
            "/taxonomy",
            "/products",
            "/engines",
            "/runs",
        ],
        "guide": "see FRONTEND.md / TESTING.md in the repo",
    }


@app.get("/health")
async def health():
    br = get_bedrock()
    ready = await asyncio.to_thread(br.ensure_ready)
    return {
        "status": "ok",
        "mode_default": config.DEFAULT_MODE,
        "bedrock": {
            "ready": ready,
            "smart_model": br.smart,
            "fast_model": br.fast,
            "error": br.error,
        },
        "engines": engine_status(),
        "products": len(db.list_products()),
        "impact_demo": {
            "ready": impact_db.get_case() is not None,
            "products": impact_db.count_products(),
            "database": config.IMPACT_DB_PATH.name,
        },
        "library_intents": db.count_intents("library"),
    }


@app.get("/impact-demo")
async def impact_demo(case_id: str = Query(default="comfort-evidence-lift")):
    """Stable P4 case backed by an isolated, deployment-seeded database."""
    case = impact_db.get_case(case_id)
    if not case:
        raise KeyError(case_id)
    return case


@app.get("/taxonomy")
async def taxonomy(category: Optional[str] = Query(default=None)):
    return load_taxonomy((category or "").strip() or None)


@app.get("/engines")
async def engines():
    return {"available": engine_status(), "default": config.DEFAULT_ENGINES}


@app.get("/logs")
async def logs(n: int = Query(default=200, ge=1, le=800), event: Optional[str] = None):
    """Recent in-process events (bedrock calls w/ latency, batches, diagnosis stages).
    Filter with ?event=bedrock / ?event=batch / ?event=diagnosis."""
    from backend.loglib import recent

    return {"events": recent(n, event_prefix=event)}


# USD per 1M tokens (input, output) — Bedrock on-demand, matched by substring.
# Adjust here if AWS repricing; source of truth = AWS Bedrock pricing page.
PRICES_PER_MTOK = {"sonnet": (3.0, 15.0), "haiku": (1.0, 5.0),
                   "nova-pro": (0.8, 3.2), "nova-lite": (0.06, 0.24)}


@app.get("/usage")
async def usage():
    """Cumulative LLM token usage + estimated USD since process start (or last reset)."""
    from backend.llm.bedrock import usage_snapshot

    rows, total_cost = [], 0.0
    for model, u in sorted(usage_snapshot().items()):
        price = next((p for k, p in PRICES_PER_MTOK.items() if k in model), (3.0, 15.0))
        cost = u["input_tokens"] / 1e6 * price[0] + u["output_tokens"] / 1e6 * price[1]
        total_cost += cost
        rows.append({**u, "model": model, "price_per_mtok": {"input": price[0], "output": price[1]},
                     "est_cost_usd": round(cost, 4)})
    return {"models": rows, "total_est_cost_usd": round(total_cost, 4),
            "note": "estimates use PRICES_PER_MTOK; POST /usage/reset to start a measurement window"}


@app.post("/usage/reset")
async def usage_reset_ep():
    from backend.llm.bedrock import usage_reset

    usage_reset()
    return {"reset": True}


@app.get("/personas")
async def personas(category: Optional[str] = Query(default=None)):
    """Default persona profiles for a category (frontend persona pickers).
    Custom personas go directly in POST /runs body."""
    from backend.pipeline.intents import default_personas, personas_path

    category = (category or "").strip() or None
    return {
        "category": category,
        "source_file": personas_path(category).name,
        "profiles": default_personas(category),
    }


@app.post("/runs")
async def create_run(body: RunCreateRequest):
    cfg = runner.normalize_config(body.model_dump(exclude_none=True))
    handle = runner.start_run(cfg)
    return {"run_id": handle.run_id, "status": "running", "config": cfg}


@app.get("/runs")
async def list_runs():
    return {"runs": db.list_runs()}


@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise KeyError(run_id)
    return run


@app.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    ok = runner.cancel_run(run_id)
    return {"run_id": run_id, "cancelled": ok}


@app.post("/runs/{run_id}/resume")
async def resume_run(run_id: str):
    handle = runner.resume_run(run_id)
    return {"run_id": handle.run_id, "status": "running"}


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise KeyError(run_id)

    async def gen() -> AsyncIterator[str]:
        handle = runner.RUNS.get(run_id)
        if run["status"] in ("completed", "failed", "cancelled") and (
            not handle or not handle.task or handle.task.done()
        ):
            final = "done" if run["status"] == "completed" else "error"
            yield _sse(
                final,
                {
                    "run_id": run_id,
                    "status": run["status"],
                    "message": run.get("error") or "run " + run["status"],
                    "pct": 100,
                },
            )
            return
        if not handle:
            yield _sse(
                "error",
                {
                    "run_id": run_id,
                    "message": "run not active in this process (restart lost it); "
                    "POST /runs/{id}/resume to continue",
                },
            )
            return
        q = handle.subscribe()
        yield _sse(
            "progress",
            {
                "run_id": run_id,
                "stage": handle.stage,
                "message": "subscribed",
                "pct": 0,
                "progress": handle.progress,
            },
        )
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                etype = ev.pop("type", "progress")
                yield _sse(etype, ev)
                if etype in ("done", "error"):
                    return
        finally:
            handle.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/runs/{run_id}/intents")
async def run_intents(run_id: str):
    if not db.get_run(run_id):
        raise KeyError(run_id)
    return {"intents": db.get_intents(run_id)}


@app.get("/runs/{run_id}/responses")
async def run_responses(
    run_id: str,
    engine: Optional[str] = None,
    cluster: Optional[str] = None,
    intent_id: Optional[str] = None,
    full: bool = False,
):
    if not db.get_run(run_id):
        raise KeyError(run_id)
    rows = db.get_responses(
        run_id, engine=engine, intent_id=intent_id, cluster_id=cluster, include_text=full
    )
    for r in rows:
        r.pop("ground_truth", None)
    return {"responses": rows, "n": len(rows)}


@app.get("/responses/{response_id}")
async def get_response(response_id: str):
    r = db.get_response(response_id)
    if not r:
        raise KeyError(response_id)
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM funnel WHERE response_id=?", (response_id,)).fetchone()
    finally:
        conn.close()
    annotation = None
    if row:
        row = dict(row)
        annotation = {
            "top_pick": row["top_pick"],
            "products": json.loads(row["products_json"]),
            "judge_model": row["judge_model"],
            "is_ground_truth": bool(row["is_ground_truth"]),
        }
    r.pop("ground_truth", None)
    return {"response": r, "funnel": annotation}


@app.get("/runs/{run_id}/funnel")
async def run_funnel(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise KeyError(run_id)
    if run.get("funnel_summary"):
        return run["funnel_summary"]
    return await asyncio.to_thread(funnel_mod.aggregate, run_id, run["config"])


@app.get("/runs/{run_id}/losses")
async def run_losses(
    run_id: str,
    canonical: Optional[str] = None,
    attribute: Optional[str] = None,
    cluster: Optional[str] = None,
    engine: Optional[str] = None,
):
    run = db.get_run(run_id)
    if not run:
        raise KeyError(run_id)
    target = funnel_mod.brand_slug_map(run["config"])["target"]
    rows = funnel_mod.collect_loss_reasons(run_id, canonical=canonical or target)
    if attribute:
        rows = [r for r in rows if r["attribute"] == attribute]
    if cluster:
        rows = [r for r in rows if r["cluster_id"] == cluster]
    if engine:
        rows = [r for r in rows if r["engine"] == engine]
    return {"losses": rows, "n": len(rows)}


@app.get("/runs/{run_id}/evidence")
async def run_evidence(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise KeyError(run_id)
    return run.get("evidence") or {"attributes": {}}


@app.get("/runs/{run_id}/report")
async def run_report(run_id: str, format: str = Query(default="json")):
    run = db.get_run(run_id)
    if not run:
        raise KeyError(run_id)
    report = run.get("report")
    if not report:
        return _err(409, "not_ready", f"run status={run['status']}, report not generated yet")
    if format == "md":
        return PlainTextResponse(report.get("markdown", ""), media_type="text/markdown")
    return report


# ---------------------------------------------------------------- products (P1)


class ProductCreate(BaseModel):
    source: str  # url | manual_prototype
    source_url: Optional[str] = None
    brand: Optional[str] = None
    display_name: Optional[str] = None
    raw_text: Optional[str] = None
    product_id: Optional[str] = None
    category: Optional[str] = None  # omit => auto-detected from the page text
    personas: Optional[list] = None  # vendor-defined target customers (PersonaProfile | str)


class VersionCreate(BaseModel):
    base_version: int
    additions: list[str]
    change_note: str


@app.post("/products")
async def create_product(body: ProductCreate):
    from backend.ingestion.service import create_product as _create

    return await _create(body.model_dump())


@app.get("/products")
async def list_products():
    out = []
    for p in db.list_products():
        q = dict(p)
        q["raw_text"] = (q["raw_text"] or "")[:300]
        out.append(q)
    return {"products": out}


@app.get("/products/{ref}")
async def get_product(ref: str):
    p = db.get_product_by_ref(ref) or impact_db.get_product_by_ref(ref)
    if not p:
        raise KeyError(ref)
    return p


@app.post("/products/{product_id}/versions")
async def create_version(product_id: str, body: VersionCreate):
    from backend.ingestion.service import create_version as _cv

    return await _cv(product_id, body.base_version, body.additions, body.change_note)


class ProductPatch(BaseModel):
    category: Optional[str] = None
    personas: Optional[list] = None          # PersonaProfile dicts or strings; [] clears


@app.patch("/products/{product_id}")
async def patch_product(product_id: str, body: ProductPatch):
    """Fix category and/or set vendor target personas on all versions of a product."""
    if body.category is None and body.personas is None:
        raise ValueError("provide category and/or personas")
    updated: dict = {}
    if body.category is not None:
        n = db.set_product_category(product_id, (body.category or "").strip() or None)
        if n == 0:
            raise KeyError(product_id)
        updated["category"] = body.category
        updated["updated_versions"] = n
    if body.personas is not None:
        from backend.pipeline.intents import normalize_personas

        profiles = normalize_personas(body.personas, body.category) if body.personas else None
        n = db.set_product_personas(product_id, profiles)
        if n == 0:
            raise KeyError(product_id)
        updated["personas"] = [p["persona_id"] for p in (profiles or [])]
        updated["updated_versions"] = n
    return {"product_id": product_id, **updated}


@app.delete("/products/{product_id}")
async def delete_product(product_id: str):
    """Remove ALL versions of a product (test-data cleanup; frontends: confirm first)."""
    n = db.delete_product(product_id)
    if n == 0:
        raise KeyError(product_id)
    return {"product_id": product_id, "deleted_versions": n}


# ---------------------------------------------------------------- simulate (P2)


class IntentIn(BaseModel):
    intent_id: Optional[str] = None
    text: str
    cluster_id: str = "other"
    attributes: list[str] = []
    language: str = "en"


class SimulateReq(BaseModel):
    intent: IntentIn
    candidates: list[str]
    stream: bool = True
    cached: bool = False
    mode: Optional[str] = None


@app.post("/simulate")
async def simulate(body: SimulateReq, request: Request):
    from backend.decision.simulate import run_decision, stream_decision

    wants_sse = body.stream or "text/event-stream" in (request.headers.get("accept") or "")
    intent = body.intent.model_dump()
    if not wants_sse:
        return await run_decision(intent, body.candidates, cached=body.cached, mode=body.mode)

    async def gen() -> AsyncIterator[str]:
        try:
            async for ev in stream_decision(
                intent, body.candidates, cached=body.cached, mode=body.mode
            ):
                if ev["type"] == "token":
                    yield _sse("token", {"text": ev["text"]})
                elif ev["type"] == "result":
                    yield _sse("done", {"decision": ev["decision"]})
                elif ev["type"] == "error":
                    yield _sse("error", {"message": ev["message"]})
                    return
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


class BatchReq(BaseModel):
    cluster_id: str
    candidates: list[str]
    runs: int = Field(default=3, ge=1, le=5)
    cached: bool = True
    max_intents: int = Field(default=12, ge=2, le=25)
    wait: bool = False
    mode: Optional[str] = None


@app.post("/simulate/batch")
async def simulate_batch(body: BatchReq):
    from backend.decision.simulate import run_batch

    if body.wait:
        return await run_batch(
            body.cluster_id,
            body.candidates,
            runs=body.runs,
            cached=body.cached,
            max_intents=body.max_intents,
            mode=body.mode,
        )
    batch_id = db.new_id("batch")
    db.create_batch(
        {
            "batch_id": batch_id,
            "cluster_id": body.cluster_id,
            "candidates": body.candidates,
            "runs": body.runs,
            "status": "running",
            "n_intents": 0,
        }
    )
    asyncio.get_running_loop().create_task(
        run_batch(
            body.cluster_id,
            body.candidates,
            runs=body.runs,
            cached=body.cached,
            max_intents=body.max_intents,
            batch_id=batch_id,
            mode=body.mode,
        )
    )
    return {
        "batch_id": batch_id,
        "status": "running",
        "cluster_id": body.cluster_id,
        "candidates": body.candidates,
    }


@app.get("/simulate/batch/{batch_id}")
async def get_batch(batch_id: str):
    b = db.get_batch(batch_id)
    if not b:
        raise KeyError(batch_id)
    b["n_decisions"] = len(b.get("decision_ids") or [])
    return b


@app.get("/decisions/{decision_id}")
async def get_decision(decision_id: str):
    d = db.get_decision(decision_id)
    if not d:
        raise KeyError(decision_id)
    return d


# ---------------------------------------------------------------- diagnosis (P3)


@app.get("/products/{ref}/diagnosis")
async def product_diagnosis(ref: str, retry: bool = False,
                            deadline_s: Optional[float] = Query(default=None, ge=5, le=600),
                            min_decisions: Optional[int] = Query(default=None, ge=1, le=200),
                            depth: str = Query(default="standard")):
    """Completion modes — default waits for ALL simulation workers
    (depth=quick|standard|deep scales worker count 12/18/48). Keep polling with
    ?deadline_s=N to get a partial diagnosis (partial:true) from workers finished
    so far once N seconds elapse; ?min_decisions=K declares done at K decisions."""
    from backend.diagnosis.service import get_or_build

    diag, pending = await get_or_build(ref, allow_trigger=True, force_retry=retry,
                                       deadline_s=deadline_s, min_decisions=min_decisions,
                                       depth=depth)
    if diag:
        return diag
    return JSONResponse(status_code=202, content=pending or {"status": "running"})


# ---------------------------------------------------------------- debate (P3)


class DebateCreate(BaseModel):
    product_ref: str
    focus_defect_id: Optional[str] = None


class MessageIn(BaseModel):
    text: str


@app.post("/debate/sessions")
async def debate_create(body: DebateCreate):
    from backend.debate.agent import create_session

    return await create_session(body.product_ref, body.focus_defect_id)


@app.get("/debate/sessions/{session_id}")
async def debate_get(session_id: str):
    s = db.get_debate_session(session_id)
    if not s:
        raise KeyError(session_id)
    return {
        "session_id": s["session_id"],
        "product_ref": s["product_ref"],
        "messages": [
            {
                "role": m["role"],
                "text": m["text"],
                "ts": m["ts"],
                "action_offer": m.get("action_offer"),
            }
            for m in s["messages"]
        ],
    }


@app.post("/debate/sessions/{session_id}/messages")
async def debate_message(session_id: str, body: MessageIn):
    from backend.debate.agent import stream_reply

    async def gen() -> AsyncIterator[str]:
        try:
            async for ev in stream_reply(session_id, body.text):
                etype = ev.pop("type")
                yield _sse(etype, ev)
                if etype in ("done", "error"):
                    return
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------------------------------------------------------------- metrics (P6 support)


def _latest_batch_for(ref: str, cluster: str) -> Optional[dict]:
    for b in db.list_batches(product_ref=ref):
        if (
            b
            and b["cluster_id"] == cluster
            and b["status"] == "completed"
            and b.get("shares", {}).get(ref)
        ):
            return b
    return None


@app.get("/metrics/compare")
async def metrics_compare(a: str, b: str, cluster: str):
    ba, bb = _latest_batch_for(a, cluster), _latest_batch_for(b, cluster)
    if not ba or not bb:
        missing = [x for x, bx in ((a, ba), (b, bb)) if not bx]
        return JSONResponse(
            status_code=202,
            content={
                "status": "pending",
                "missing": missing,
                "cluster_id": cluster,
                "hint": "run POST /simulate/batch for the missing side or wait for the debate action batches",
            },
        )

    def side(ref: str, batch: dict) -> dict:
        s = batch["shares"][ref]
        return {
            "product_ref": ref,
            "recommendation_share": s["recommendation_share"],
            "consideration_share": s["consideration_share"],
            "ci95_recommendation": s["ci95_recommendation"],
        }

    pa = db.get_product_by_ref(a) or impact_db.get_product_by_ref(a)
    pb = db.get_product_by_ref(b) or impact_db.get_product_by_ref(b)
    changes: list[str] = []
    if pa and pb and pa["product_id"] == pb["product_id"]:
        older, newer = (pa, pb) if pa["version"] < pb["version"] else (pb, pa)
        delta_text = newer["raw_text"][len(older["raw_text"]) :].strip()
        changes = [c.strip() for c in delta_text.split("\n\n") if c.strip()]
        if newer.get("change_note"):
            changes.insert(0, f"note: {newer['change_note']}")
    return {
        "cluster_id": cluster,
        "n_per_side": min(len(ba.get("decision_ids") or []), len(bb.get("decision_ids") or [])),
        "a": side(a, ba),
        "b": side(b, bb),
        "delta_recommendation": round(
            bb["shares"][b]["recommendation_share"] - ba["shares"][a]["recommendation_share"], 3
        ),
        "changes_applied": changes,
        "diff_url": None,
    }
