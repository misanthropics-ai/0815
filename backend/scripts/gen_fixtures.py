"""Generate mock_fixtures/* from real DB/server state so fixtures always match
the live response shapes.  Run with the backend DB populated (after smoke_e2e
and a debate session):  python -m backend.scripts.gen_fixtures
"""
from __future__ import annotations

import asyncio
import json
import shutil

from backend import config
from backend.storage import db

FX = config.FIXTURES_DIR


def w(name: str, payload) -> None:
    path = FX / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {name}")


def sse_lines(events: list[tuple[str, dict]]) -> str:
    out = []
    for etype, data in events:
        out.append(f"event: {etype}")
        out.append(f"data: {json.dumps(data, ensure_ascii=False)}")
        out.append("")
    return "\n".join(out) + "\n"


async def main() -> None:
    # taxonomy
    shutil.copy(config.TAXONOMY_PATH, FX / "taxonomy.json")
    print("wrote taxonomy.json")

    # products
    cz1 = db.get_product("cabinzero-classic-36l", 1)
    cz2 = db.get_product("cabinzero-classic-36l", 2) or cz1
    osp = db.get_product("osprey-farpoint-40", 1)
    w("product.cabinzero-classic-36l.json", cz1)
    w("product.cabinzero-classic-36l.v2.json", cz2)
    w("product.osprey-farpoint-40.json", osp)

    w("request.post_products.manual.json", {
        "source": "manual_prototype", "brand": "CabinZero",
        "display_name": "CabinZero Classic 36L",
        "raw_text": cz1["raw_text"][:600] + " ..."})
    w("response.post_products.manual.json", cz1)

    # simulate
    refs = ["cabinzero-classic-36l@v1", "osprey-farpoint-40@v1",
            "decathlon-forclaz-travel500-40l@v1", "cotopaxi-allpa-35l@v1"]
    w("request.post_simulate.json", {
        "intent": {"text": "most comfortable travel backpack for walking all day",
                   "cluster_id": "comfort_carry", "attributes": ["comfort"]},
        "candidates": refs, "stream": True, "cached": False})
    from backend.decision.simulate import run_decision
    decision = await run_decision(
        {"text": "most comfortable travel backpack for walking all day",
         "cluster_id": "comfort_carry", "attributes": ["comfort"]},
        refs, mode="mock")
    w("response.decision_result.json", decision)
    toks = [decision["narrative"][i:i + 30] for i in range(0, min(120, len(decision["narrative"])), 30)]
    w("sse.simulate.stream.txt", sse_lines(
        [("token", {"text": t}) for t in toks] + [("done", {"decision": decision})]))

    # batch
    w("request.post_simulate_batch.json", {
        "cluster_id": "comfort_carry", "candidates": refs, "runs": 2,
        "max_intents": 6, "wait": True, "cached": True})
    batch = next((b for b in db.list_batches() if b and b["status"] == "completed"), None)
    if batch:
        batch["n_decisions"] = len(batch.get("decision_ids") or [])
        w("response.simulate_batch.json", batch)

    # diagnosis
    diag = db.get_diagnosis("cabinzero-classic-36l@v1")
    if diag:
        w("response.diagnosis.json", diag)

    # debate
    w("request.post_debate_session.json", {"product_ref": "cabinzero-classic-36l@v1",
                                           "focus_defect_id": "def_001"})
    sessions = []
    conn = db.connect()
    try:
        sessions = [r["session_id"] for r in
                    conn.execute("SELECT session_id FROM debate_sessions ORDER BY created_at").fetchall()]
    finally:
        conn.close()
    if sessions:
        s = db.get_debate_session(sessions[0])
        w("response.post_debate_session.json", {"session_id": s["session_id"],
                                                "product_ref": s["product_ref"],
                                                "diagnosis_ready": True})
        w("response.get_debate_session.json", {
            "session_id": s["session_id"], "product_ref": s["product_ref"],
            "messages": [{"role": m["role"], "text": m["text"], "ts": m["ts"],
                          "action_offer": m.get("action_offer")} for m in s["messages"]]})
        # sse.debate.stream.txt from the action-bearing exchange
        act_msg = next((m for m in s["messages"] if m.get("action_offer")), None)
        if act_msg:
            toks = [act_msg["text"][i:i + 30] for i in range(0, min(150, len(act_msg["text"])), 30)]
            w("sse.debate.stream.txt", sse_lines(
                [("token", {"text": t}) for t in toks]
                + [("action", {"action": act_msg["action_offer"]}),
                   ("done", {"session_id": s["session_id"]})]))

    # compare
    from backend.app import metrics_compare
    cmp_res = await metrics_compare(a="cabinzero-classic-36l@v1",
                                    b="cabinzero-classic-36l@v2", cluster="weight_minimal")
    if isinstance(cmp_res, dict):
        w("response.metrics_compare.json", cmp_res)

    # runs
    w("request.post_runs.json", {
        "brand": "CabinZero",
        "competitors": ["Osprey", "Decathlon", "Cotopaxi"],
        "category": "travel backpack", "n_intents": 60,
        "engines": ["sim-sonnet", "sim-haiku"], "mode": "auto"})
    run_row = next((r for r in db.list_runs() if r["status"] == "completed"), None)
    if run_row:
        run = db.get_run(run_row["run_id"])
        w("response.run_status.json", run)
        if run.get("funnel_summary"):
            w("response.funnel.json", run["funnel_summary"])
        if run.get("report"):
            w("response.report.json", run["report"])
        w("sse.run_events.txt", sse_lines([
            ("progress", {"run_id": run["run_id"], "stage": "intents", "done": 1, "total": 1,
                          "message": "60 intents ready", "pct": 10}),
            ("progress", {"run_id": run["run_id"], "stage": "execute", "done": 60, "total": 120,
                          "message": "60/120 responses (0 errors)", "pct": 30}),
            ("progress", {"run_id": run["run_id"], "stage": "funnel", "done": 120, "total": 120,
                          "message": "judged 120/120", "pct": 80}),
            ("done", {"run_id": run["run_id"], "pct": 100, "message": "run completed"})]))

    # error
    w("response.error.sample.json", {"error": {"code": "not_found",
                                               "message": "product not found: foo@v9",
                                               "hint": "GET /products for valid refs"}})


if __name__ == "__main__":
    asyncio.run(main())
