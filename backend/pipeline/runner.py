"""Pipeline orchestrator: five stages with checkpointing, cancel, resume,
and SSE progress events.

Stages: intents -> execute -> funnel -> attribution -> report.
Every stage skips work that already exists in the DB, so resuming a failed
or interrupted run only re-does the missing pieces (LLM caches make even
full re-runs cheap).
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from backend import config
from backend.llm.bedrock import LLMError, get_bedrock
from backend.pipeline import attribution, recommend
from backend.pipeline import funnel as funnel_mod
from backend.pipeline import intents as intents_mod
from backend.pipeline.corpus import build_corpus, slugify
from backend.pipeline.engines import RunContext, default_engines, make_engine
from backend.storage import db
from backend.taxonomy import category_slug

STAGES = ["intents", "execute", "funnel", "attribution", "report"]
STAGE_WEIGHTS = {"intents": 10, "execute": 40, "funnel": 30, "attribution": 10, "report": 10}


class RunHandle:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.task: Optional[asyncio.Task] = None
        self.queues: list[asyncio.Queue] = []
        self.progress: dict = {}
        self.stage = "intents"

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.queues:
            self.queues.remove(q)

    def emit(self, event: dict) -> None:
        for q in list(self.queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


RUNS: dict[str, RunHandle] = {}


def _pct(stage: str, done: int, total: int) -> int:
    acc = 0
    for s in STAGES:
        if s == stage:
            acc += STAGE_WEIGHTS[s] * (done / max(1, total))
            break
        acc += STAGE_WEIGHTS[s]
    return min(100, int(acc))


def normalize_config(body: dict) -> dict:
    brand = (body.get("brand") or "").strip()
    if not brand:
        raise ValueError("brand is required")
    mode = body.get("mode") or config.DEFAULT_MODE
    if mode == "auto":
        mode = "live" if get_bedrock().available() else "mock"
    if mode == "live" and not get_bedrock().available():
        raise ValueError(f"live mode requested but Bedrock unavailable: {get_bedrock().error}")

    category = body.get("category") or "travel backpack"
    cslug = category_slug(category)
    all_products = db.list_products()
    # keep the run inside one product category (legacy null category = travel backpack)
    products = [p for p in all_products
                if category_slug(p.get("category") or "travel backpack") == cslug] or all_products
    tslug = slugify(brand)
    competitors = body.get("competitors")
    if not competitors:
        seen: list[str] = []
        for p in products:
            if slugify(p["brand"]) != tslug and p["brand"] not in seen:
                seen.append(p["brand"])
        competitors = seen[:4]
    comp_slugs = {slugify(c) for c in competitors}

    product_refs = body.get("product_refs")
    if not product_refs:
        latest: dict[str, dict] = {}
        for p in products:
            s = slugify(p["brand"])
            if s == tslug or s in comp_slugs:
                cur = latest.get(p["product_id"])
                if not cur or p["version"] > cur["version"]:
                    latest[p["product_id"]] = p
        product_refs = [f"{p['product_id']}@v{p['version']}" for p in latest.values()]

    brand_products = body.get("brand_products") or [
        p["display_name"] for p in products if slugify(p["brand"]) == tslug]

    engines = body.get("engines") or default_engines(mode)
    for e in engines:
        make_engine(e)  # validates names early

    cfg = {
        "run_id": body.get("run_id") or db.new_id("run"),
        "brand": brand,
        "brand_products": brand_products,
        "competitors": competitors,
        "category": category,
        "market": body.get("market") or "US/EU",
        "language": body.get("language") or "en",
        "personas": intents_mod.normalize_personas(body.get("personas"), category),
        "n_intents": int(body.get("n_intents") or 60),
        "engines": engines,
        "mode": mode,
        "judge_model": body.get("judge_model"),
        "product_refs": product_refs,
    }
    return cfg


async def _stage_progress(handle: RunHandle, stage: str, done: int, total: int, msg: str) -> None:
    handle.stage = stage
    handle.progress.setdefault(stage, {})
    handle.progress[stage].update({"done": done, "total": total})
    event = {"type": "progress", "run_id": handle.run_id, "stage": stage, "done": done,
             "total": total, "message": msg, "pct": _pct(stage, done, total)}
    handle.emit(event)
    await asyncio.to_thread(db.update_run, handle.run_id, stage=stage, progress=handle.progress)


async def _execute(handle: RunHandle, cfg: dict) -> None:
    run_id = handle.run_id
    try:
        await asyncio.to_thread(db.update_run, run_id, status="running")

        # ---- Stage 1: intents
        await _stage_progress(handle, "intents", 0, 1, "generating intents")
        intents = await intents_mod.generate_intents(
            cfg, progress=lambda d, t, m: _stage_progress(handle, "intents", d, t, m))
        await _stage_progress(handle, "intents", 1, 1, f"{len(intents)} intents ready")

        # ---- Stage 2: execute across engines
        corpus = await asyncio.to_thread(build_corpus, cfg["product_refs"])
        world_path = config.FIXTURES_DIR / "mock_world.json"
        world = json.loads(world_path.read_text(encoding="utf-8")) if world_path.exists() else {}
        ctx = RunContext(run_cfg=cfg, corpus=corpus, mock_world=world,
                         brand_slugs=funnel_mod.brand_slug_map(cfg))
        engines = [make_engine(n) for n in cfg["engines"]]
        existing = {(r["intent_id"], r["engine"])
                    for r in await asyncio.to_thread(db.get_responses, run_id)}
        jobs = [(it, eng) for it in intents for eng in engines
                if (it["intent_id"], eng.name) not in existing]
        total_jobs = len(intents) * len(engines)
        done_jobs = total_jobs - len(jobs)
        errors = 0
        auth_dead = False
        sem = asyncio.Semaphore(config.ENGINE_CONCURRENCY)
        await _stage_progress(handle, "execute", done_jobs, total_jobs,
                              f"querying {len(engines)} engines × {len(intents)} intents")

        async def one(intent: dict, eng) -> None:
            nonlocal done_jobs, errors, auth_dead
            async with sem:
                if auth_dead:
                    return
                try:
                    res = await eng.run(intent, ctx)
                except LLMError as e:
                    if e.code == "aws_auth":
                        auth_dead = True
                        raise
                    res = None
                except Exception:
                    res = None
                row = {
                    "response_id": db.new_id("resp"), "run_id": run_id,
                    "intent_id": intent["intent_id"], "engine": eng.name,
                    "model": getattr(res, "model", None) if res else None,
                    "status": res.status if res else "error",
                    "text": res.text if res else "",
                    "citations": res.citations if res else [],
                    "search_queries": res.search_queries if res else [],
                    "ground_truth": res.ground_truth if res else None,
                    "latency_ms": res.latency_ms if res else None,
                    "error": (res.error if res else "engine crashed"),
                }
                if row["status"] != "ok":
                    errors += 1
                await asyncio.to_thread(db.save_response, row)
                done_jobs += 1
                if done_jobs % 4 == 0 or done_jobs == total_jobs:
                    await _stage_progress(handle, "execute", done_jobs, total_jobs,
                                          f"{done_jobs}/{total_jobs} responses ({errors} errors)")

        results = await asyncio.gather(*[one(it, eng) for it, eng in jobs],
                                       return_exceptions=True)
        for r in results:
            if isinstance(r, LLMError) and r.code == "aws_auth":
                raise r
        ok_responses = [r for r in await asyncio.to_thread(db.get_responses, run_id)
                        if r["status"] == "ok"]
        if not ok_responses:
            raise RuntimeError("no successful engine responses")

        # ---- Stage 3: funnel parsing
        await _stage_progress(handle, "funnel", 0, len(ok_responses), "LLM-as-judge annotating")
        stats = await funnel_mod.annotate_run(
            run_id, cfg, progress=lambda d, t, m: _stage_progress(handle, "funnel", d, t, m))
        await _stage_progress(handle, "funnel", stats["total"], max(1, stats["total"]),
                              f"annotated {stats['annotated']} ({stats['errors']} errors)")

        # ---- Stage 4: attribution + evidence audit
        await _stage_progress(handle, "attribution", 0, 3, "mapping loss reasons")
        await attribution.map_loss_reasons(run_id, cfg)
        await _stage_progress(handle, "attribution", 1, 3, "aggregating funnel")
        summary = await asyncio.to_thread(funnel_mod.aggregate, run_id, cfg)
        await asyncio.to_thread(db.update_run, run_id, funnel_summary=summary)
        await _stage_progress(handle, "attribution", 2, 3, "evidence audit")
        evidence = await attribution.evidence_audit(run_id, cfg, summary)
        await asyncio.to_thread(db.update_run, run_id, evidence=evidence)
        await _stage_progress(handle, "attribution", 3, 3, "attribution done")

        # ---- Stage 5: report
        await _stage_progress(handle, "report", 0, 1, "building recommendations")
        await recommend.build_report(run_id, cfg, summary, evidence)
        await asyncio.to_thread(db.update_run, run_id, status="completed", stage="done")
        handle.emit({"type": "done", "run_id": run_id, "pct": 100,
                     "message": "run completed"})
    except asyncio.CancelledError:
        await asyncio.to_thread(db.update_run, run_id, status="cancelled")
        handle.emit({"type": "error", "run_id": run_id, "message": "cancelled"})
    except Exception as e:  # noqa: BLE001
        await asyncio.to_thread(db.update_run, run_id, status="failed", error=str(e))
        handle.emit({"type": "error", "run_id": run_id, "message": str(e)})


def start_run(cfg: dict) -> RunHandle:
    run_id = cfg["run_id"]
    if db.get_run(run_id) is None:
        db.create_run(run_id, cfg)
    else:
        db.update_run(run_id, config=cfg, error=None)
    handle = RUNS.get(run_id) or RunHandle(run_id)
    RUNS[run_id] = handle
    handle.task = asyncio.get_running_loop().create_task(_execute(handle, cfg))
    return handle


def resume_run(run_id: str) -> RunHandle:
    run = db.get_run(run_id)
    if not run:
        raise KeyError(run_id)
    if run["status"] == "running" and run_id in RUNS and RUNS[run_id].task and not RUNS[run_id].task.done():
        return RUNS[run_id]
    return start_run(run["config"])


def cancel_run(run_id: str) -> bool:
    h = RUNS.get(run_id)
    if h and h.task and not h.task.done():
        h.task.cancel()
        return True
    return False
