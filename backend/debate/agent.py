"""P3 — Debate Agent.

Data-grounded, combative-but-fair chat over a product's diagnosis. Every reply
carries a number or verbatim rejection quote. When the user supplies genuinely
new product information, the model emits <action>{...}</action>; we execute it:
create product v2 (re-extract attributes) + re-run compare batches in the
background — the "argue → concede new info → v2 → re-simulate" loop.
SSE events: token / action / error / done.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import AsyncIterator, Optional

from backend.llm import bedrock
from backend.llm.bedrock import LLMError, get_bedrock
from backend.llm.prompts import render_prompt
from backend.storage import db

PROMPT_VERSION = "debate/prompts/prompt_v1"
ACTION_RE = re.compile(r"<action>\s*(\{.*?\})\s*</action>", re.DOTALL)
NEW_INFO_HINTS = ["其實", "實際上", "我們有", "我們其實", "actually", "we have", "we do have",
                  "it does have", "there is a", "we added", "沒寫", "not on the page"]


def _blocks(diag: dict) -> dict:
    o = diag.get("overall", {})
    overall = (f"recommendation_share={o.get('recommendation_share')}, "
               f"consideration_share={o.get('consideration_share')}, "
               f"n_simulations={o.get('n_simulations')}, vs={json.dumps(o.get('vs', {}))}")
    defects = json.dumps([
        {"defect_id": d["defect_id"], "type": d["type"], "attribute": d["attribute_id"],
         "severity": d["severity"], "headline": d["headline"],
         "cluster": d["evidence"]["cluster_id"],
         "losing_share": d["evidence"]["losing_share_in_cluster"],
         "n_losses": d["evidence"]["n_losses"], "gap": d.get("gap")}
        for d in diag.get("defects", [])], ensure_ascii=False)
    samples = json.dumps([r for d in diag.get("defects", [])
                          for r in d["evidence"]["sample_rejection_reasons"][:2]][:8],
                         ensure_ascii=False)
    contrast = json.dumps([d["evidence"]["competitor_contrast"]
                           for d in diag.get("defects", []) if d["evidence"]["competitor_contrast"]][:4],
                          ensure_ascii=False)
    return {"overall_block": overall, "defects_block": defects, "samples_block": samples,
            "contrast_block": contrast}


def _system_prompt(product: dict, diag: dict, focus_defect_id: Optional[str]) -> str:
    blocks = _blocks(diag)
    evidence_block = "(see defects)"
    sp = render_prompt(PROMPT_VERSION, brand=product["brand"], product_ref=product["ref"],
                       evidence_block=evidence_block, **blocks)
    if focus_defect_id:
        sp += f"\n\nThe user opened this chat from defect {focus_defect_id}; lead with it when relevant."
    return sp


async def create_session(product_ref: str, focus_defect_id: Optional[str]) -> dict:
    from backend.diagnosis.service import get_or_build
    product = db.get_product_by_ref(product_ref)
    if not product:
        raise KeyError(product_ref)
    diag, pending = await get_or_build(product_ref, allow_trigger=True)
    session_id = db.new_id("dbt")
    meta = {"focus_defect_id": focus_defect_id,
            "diagnosis_ready": diag is not None, "pending": pending}
    db.create_debate_session(session_id, product["ref"], focus_defect_id, meta)
    return {"session_id": session_id, "product_ref": product["ref"],
            "diagnosis_ready": diag is not None}


def _mock_reply(diag: dict, user_text: str, turn: int) -> tuple[str, Optional[dict]]:
    defects = diag.get("defects", [])
    o = diag.get("overall", {})
    d = defects[turn % len(defects)] if defects else None
    new_info = any(h in user_text.lower() for h in NEW_INFO_HINTS) and len(user_text) > 12
    if new_info and d:
        text = (f"That changes things — if that's true, it is exactly the problem: it isn't on your page, "
                f"so the AI can't see it. Right now you're recommended in only "
                f"{int(o.get('recommendation_share', 0) * 100)}% of {o.get('n_simulations', 0)} simulations, "
                f"and rejections say things like \"{d['evidence']['sample_rejection_reasons'][0] if d['evidence']['sample_rejection_reasons'] else 'no evidence found'}\". "
                f"Let me add your claim to the page as v2 and re-run the simulation.")
        action = {"type": "create_version_and_rerun",
                  "params": {"additions": [user_text.strip()],
                             "cluster_id": d["evidence"]["cluster_id"]}}
        return text, action
    if d:
        text = (f"The data disagrees. In {o.get('n_simulations', 0)} simulations you were recommended "
                f"{int(o.get('recommendation_share', 0) * 100)}% of the time; in cluster "
                f"{d['evidence']['cluster_id']} you lose {int(d['evidence']['losing_share_in_cluster'] * 100)}% "
                f"of comparisons. The AI's own words: "
                f"\"{d['evidence']['sample_rejection_reasons'][0] if d['evidence']['sample_rejection_reasons'] else 'no supporting evidence found'}\". "
                f"The issue isn't whether the product is good — it's that the page shows the AI no evidence. "
                f"AI can't recommend advantages it cannot see.")
        return text, None
    return ("I only argue from the simulation data — run a diagnosis first and I'll defend every number.",
            None)


async def _execute_action(session_id: str, product: dict, action: dict, diag: dict) -> dict:
    """Create v2 + launch compare batches in background. Returns action event payload."""
    from backend.decision.simulate import run_batch
    from backend.diagnosis.service import _competitor_refs
    from backend.ingestion.service import create_version
    params = action.get("params", {})
    additions = params.get("additions") or []
    cluster_id = params.get("cluster_id") or (
        diag["defects"][0]["evidence"]["cluster_id"] if diag.get("defects") else "comfort_carry")
    new_product = await create_version(product["product_id"], product["version"], additions,
                                       change_note=f"debate:{session_id}")
    old_ref, new_ref = product["ref"], new_product["ref"]
    comps = _competitor_refs(product, limit=3)
    batch_a, batch_b = db.new_id("batch"), db.new_id("batch")

    async def _bg() -> None:
        try:
            await run_batch(cluster_id, [old_ref] + comps, runs=2, max_intents=8,
                            batch_id=batch_a)
            await run_batch(cluster_id, [new_ref] + comps, runs=2, max_intents=8,
                            batch_id=batch_b)
        except Exception:
            pass

    asyncio.get_running_loop().create_task(_bg())
    return {"type": "create_version_and_rerun", "status": "started",
            "params": {"additions": additions, "cluster_id": cluster_id},
            "new_ref": new_ref, "base_ref": old_ref,
            "batch_a": batch_a, "batch_b": batch_b, "cluster_id": cluster_id,
            "compare_url": f"/metrics/compare?a={old_ref}&b={new_ref}&cluster={cluster_id}"}


async def stream_reply(session_id: str, user_text: str) -> AsyncIterator[dict]:
    from backend.diagnosis.service import get_or_build
    session = db.get_debate_session(session_id)
    if not session:
        yield {"type": "error", "message": f"session not found: {session_id}"}
        return
    product = db.get_product_by_ref(session["product_ref"])
    diag, _ = await get_or_build(session["product_ref"], allow_trigger=False)
    if diag is None:
        diag = {"overall": {}, "defects": []}
    messages = session["messages"]
    messages.append({"role": "user", "text": user_text, "ts": db.now_iso()})
    db.save_debate_messages(session_id, messages)

    full = ""
    action_payload: Optional[dict] = None
    if get_bedrock().available():
        system = _system_prompt(product, diag, session.get("focus_defect_id"))
        history = [{"role": m["role"] if m["role"] in ("user", "assistant") else "user",
                    "content": m["text"][:1500]} for m in messages[-12:]]
        emitted = 0
        try:
            async for chunk in bedrock.astream(messages=history, system=system,
                                               max_tokens=900, temperature=0.4):
                full += chunk
                cut = full.find("<action")
                safe = len(full) if cut == -1 else cut
                if safe > emitted:
                    yield {"type": "token", "text": full[emitted:safe]}
                    emitted = safe
        except LLMError as e:
            yield {"type": "error", "message": str(e)}
            return
        m = ACTION_RE.search(full)
        if m:
            try:
                action_payload = json.loads(m.group(1))
            except Exception:
                action_payload = None
        assistant_text = ACTION_RE.sub("", full).strip()
    else:
        turn = sum(1 for m in messages if m["role"] == "assistant")
        assistant_text, action_payload = _mock_reply(diag, user_text, turn)
        for i in range(0, len(assistant_text), 24):
            yield {"type": "token", "text": assistant_text[i:i + 24]}
            await asyncio.sleep(0.02)

    action_event = None
    if action_payload and action_payload.get("type") == "create_version_and_rerun":
        try:
            action_event = await _execute_action(session_id, product, action_payload, diag)
            yield {"type": "action", "action": action_event}
        except Exception as e:  # noqa: BLE001
            yield {"type": "action", "action": {"type": "create_version_and_rerun",
                                                "status": "failed", "error": str(e)}}

    messages.append({"role": "assistant", "text": assistant_text, "ts": db.now_iso(),
                     "action_offer": action_event})
    db.save_debate_messages(session_id, messages)
    yield {"type": "done", "session_id": session_id}
