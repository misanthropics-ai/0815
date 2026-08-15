# Frontend Integration Guide (P4 & P5)

The backend (P1+P2+P3) is **done, deployed, and stable**. This doc is everything you need to build the two frontends without reading any backend code.

- **P4 — Shopper Simulator**: pick an intent + 2–4 products → watch the AI decide live (streamed narrative) → winner + reasons.
- **P5 — Diagnosis + Debate**: paste a product URL/text → defect cards ("why AI doesn't recommend you") → argue with the agent → it creates a v2 page and re-runs the simulation → before/after chart.

TypeScript types for every payload: [`contracts/types.ts`](contracts/types.ts).
Real example responses for every endpoint: [`backend/mock_fixtures/`](backend/mock_fixtures/) (generated from actual backend responses — trust them over your memory).

---

## 1. Setup

```bash
# Option A: local backend (recommended for dev)
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
backend/run.sh                       # API at http://localhost:8000
# No AWS creds needed — it auto-falls back to mock mode with identical response shapes.

# Option B: deployed backend (shared, live LLM)
# http://34.227.93.223:8000   ← Elastic IP, stable across redeploys. Status:
#   python deploy/deploy_ec2.py --status
```

```ts
// vite: .env.local
VITE_API_BASE=http://localhost:8000
```

CORS is wide open — call the API directly from the browser. Every error is:

```json
{ "error": { "code": "not_found", "message": "product not found: foo@v9" } }
```

Quick sanity check: `curl $API/health` → `bedrock.ready` tells you if you're on live LLM or mock.

---

## 2. Core concepts (read this once)

| Concept | Meaning |
|---|---|
| **ProductRef** | `"{product_id}@v{n}"`, e.g. `cabinzero-classic-36l@v1`. Always pass refs between endpoints, never bare ids. Omitting `@vN` in GET = latest version. |
| **`value: null` on an attribute** | "The page doesn't state this." Not a bug — it's the core diagnostic signal. **Render as a grey "?" chip** (spec: this is the visual foreshadow that pays off in the diagnosis demo). |
| **Funnel** | Per AI answer, each brand is annotated: `retrieved` (evidence in citations) → `considered` (compared in the answer) → `recommended` (final pick). Drop-off stage = the story. |
| **canonical / slug** | Funnel aggregation keys brands by slug: `cabinzero`, `osprey`, `decathlon`, `cotopaxi`. |
| **cluster_id** | Intent group (`comfort_carry`, `budget_value`, `airline_compliance`, …). Full list from `GET /taxonomy` → `clusters[]`. |
| **SSE events** | Only five types anywhere: `token`, `action`, `progress`, `error`, `done`. Switch on these and you're safe. |

---

## 3. Consuming SSE (both frontends need this)

`POST /simulate` and `POST /debate/sessions/{id}/messages` stream SSE **from a POST** — native `EventSource` can't do that, so parse the fetch body stream:

```ts
export async function* sse(res: Response) {
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) !== -1) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      let event = "message", data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield { event, data: JSON.parse(data) };
    }
  }
}

// usage
const res = await fetch(`${API}/simulate`, { method: "POST",
  headers: { "content-type": "application/json" }, body: JSON.stringify(req) });
for await (const ev of sse(res)) {
  switch (ev.event) {
    case "token": append(ev.data.text); break;          // typewriter effect
    case "done":  showDecision(ev.data.decision); break; // final structured JSON
    case "error": showRetry(ev.data.message); break;
  }
}
```

`GET /runs/{id}/events` is a plain GET — native `EventSource` works there (it also auto-ignores our `: ping` keepalives):

```ts
const es = new EventSource(`${API}/runs/${runId}/events`);
es.addEventListener("progress", e => setProgress(JSON.parse(e.data))); // {stage, done, total, pct, message}
es.addEventListener("done", e => { es.close(); loadReport(); });
es.addEventListener("error", e => { /* show retry → POST /runs/{id}/resume */ });
```

---

## 4. P4 — Shopper Simulator cookbook

### 4.1 Load the product cards

`GET /products` → `{products: Product[]}`. Each product has all 12 taxonomy attributes; show chips, **null value ⇒ grey "?"**. Let the user check 2–4 cards → collect their `ref`s.

### 4.2 The 6 preset intent buttons

Copy-paste these (aligned with backend clusters so diagnosis views match):

```json
[
  {"text": "Most comfortable travel backpack for walking all day", "cluster_id": "comfort_carry", "attributes": ["comfort"]},
  {"text": "Best budget travel backpack under $100", "cluster_id": "budget_value", "attributes": ["price"]},
  {"text": "Backpack that definitely fits Ryanair cabin limits", "cluster_id": "airline_compliance", "attributes": ["airline_compliance"]},
  {"text": "Best travel backpack with a 16-inch laptop compartment", "cluster_id": "organization_tech", "attributes": ["organization"]},
  {"text": "Lightest carry-on backpack for one-bag travel", "cluster_id": "weight_minimal", "attributes": ["weight"]},
  {"text": "Most durable travel backpack that will last 10 years", "cluster_id": "durability_warranty", "attributes": ["durability", "warranty"]}
]
```

(Free-text input also works — cluster `other` is fine.)

### 4.3 Simulate (the theater moment)

```jsonc
POST /simulate
{
  "intent": { "text": "...", "cluster_id": "comfort_carry", "attributes": ["comfort"] },
  "candidates": ["cabinzero-classic-36l@v1", "osprey-farpoint-40@v1"],
  "stream": true,
  "cached": false          // ⚠ set true during rehearsals/demo = instant deterministic replay
}
```

SSE: `token` events stream the narrative (typewriter), then **`done` carries `{decision: DecisionResult}`**:

- `decision.winner` → highlight that card.
- `decision.per_product[]` → losers get `reasons_against[].text` as red tags (verbatim AI quotes), `verdict` is `recommended | rejected | not_considered`.
- Fixture: `response.decision_result.json`, stream shape: `sse.simulate.stream.txt`.

Live latency ≈ 8–20 s per simulate; show a spinner until first token, never a blank screen. On `error` event or network drop: show a Retry button that re-POSTs (with `cached: true` retry is instant if the first attempt finished server-side).

### 4.4 "Run ×5" scoreboard (stochasticity defense)

Loop 5× `POST /simulate` with `"stream": false, "cached": false` (each returns a full `DecisionResult` JSON). Tally `winner` per run into the side scoreboard. Run sequentially (LLM quota) — ~1 min total live; interleave a small progress note ("run 3/5…").

---

## 5. P5 — Diagnosis + Debate cookbook

### 5.1 Input page → product

```jsonc
POST /products        // URL mode
{ "source": "url", "source_url": "https://www.cabinzero.com/products/classic-36l" }

POST /products        // manual prototype mode (paste text)
{ "source": "manual_prototype", "brand": "CabinZero", "display_name": "CabinZero Classic 36L", "raw_text": "..." }
```

Returns a full `Product` (10–20 s live — show an "extracting attributes…" progress state). Render the attribute table: value+evidence rows, **null rows in grey with "not on page"**. Fixtures: `request/response.post_products.manual.json`.

**Categories (cross-product support):** add optional `"category"` to POST /products (e.g. `"wireless earbuds"`). Omit it and the extractor **auto-detects** the category from the page text. The category picks the attribute taxonomy (backpacks keep the rich demo taxonomy; anything else uses the generic one — fetch it via `GET /taxonomy?category=...` to render the right attribute chips). Competitors in diagnosis/runs stay within the same category automatically.

**Personas:** `GET /personas?category=...` returns the default structured persona profiles for that category (backpack set or generic set) — use it to build a persona picker; pass the chosen/edited profiles into `POST /runs` `personas`.

**URL-mode failures (important UX):** sites that block server-side access (Shopee, some JS-only stores) return `422`:

```json
{ "error": { "code": "page_not_extractable",  // or "fetch_failed"
             "message": "...", "hint": "copy the product description ... source=manual_prototype" } }
```

On these two codes: show the `hint` and **auto-switch the form to paste mode** (`manual_prototype`). Demo line: "if an AI crawler can't read your page, AI can't recommend you — that's the product's whole point."

### 5.2 Diagnosis page

```
GET /products/{ref}/diagnosis
→ 200 Diagnosis          // ready
→ 202 {status:"running"} // backend is generating — poll every 3–5 s until 200
```

Top of page (from `diagnosis.overall`): big `recommendation_share` %, `n_simulations`, and `vs` = `{competitor_ref: share}` for the comparison bars.

Defect cards (`diagnosis.defects[]`, already sorted by severity):

| UI element | Field |
|---|---|
| Headline | `headline` (contains a real number) |
| Badges | `type` (`missing_attribute`/`weak_evidence`/`losing_cluster`/`positioning`), `severity`, **`gap`** (`information_gap` 🟢 fixable-by-content vs `product_gap` 🔴 vs `mixed`) |
| Cluster loss stat | `evidence.cluster_id` + `evidence.losing_share_in_cluster` (%) + `evidence.n_losses` |
| Quoted rejection | `evidence.sample_rejection_reasons[]` — render as quotes, these are the AI's verbatim words |
| Competitor contrast | `evidence.competitor_contrast` |
| Suggested fix | `suggested_fix`, and `content_patch` = ready-to-paste copy (show in a copyable code block) |
| **"Discuss this" button** | opens debate with `focus_defect_id: defect.defect_id` |

Fixture: `response.diagnosis.json`. Also available if you want extra views: `exec_summary`, `winning_clusters[]`, `funnel_dropoff`.

### 5.3 Debate page (the main event)

```jsonc
POST /debate/sessions { "product_ref": "cabinzero-classic-36l@v1", "focus_defect_id": "def_001" }
→ { "session_id": "dbt_...", "product_ref": "..." }

POST /debate/sessions/{id}/messages { "text": "我的背包明明很舒適" }   // → SSE
GET  /debate/sessions/{id}    // full history for page refresh (fixture: response.get_debate_session.json)
```

SSE handling per message:

- `token` → append to the assistant bubble (agent replies in the user's language).
- **`action`** → the climax. Payload:

```json
{ "action": {
    "type": "create_version_and_rerun", "status": "started",
    "params": { "additions": ["...the new page copy..."], "cluster_id": "comfort_carry" },
    "base_ref": "cabinzero-classic-36l@v1", "new_ref": "cabinzero-classic-36l@v2",
    "batch_a": "batch_...", "batch_b": "batch_...",
    "compare_url": "/metrics/compare?a=cabinzero-classic-36l@v1&b=cabinzero-classic-36l@v2&cluster=comfort_carry"
}}
```

  Insert a **progress card** in the chat flow ("Rewriting page → v2 created ✓ → re-running simulation…"), then poll `GET {compare_url}` every 5 s:
  - `202 {status:"pending"}` → keep the progress animation (live batches take ~1–3 min).
  - `200 CompareResult` → render the inline **before/after card**: `a` vs `b` `recommendation_share` bars, `delta_recommendation` as the big number, `changes_applied[]` as "we only added these paragraphs".
- `done` → unlock the input box. **Lock the input while streaming** (spec requirement — avoids the race).
- `status:"failed"` action or `error` event → show the message + retry.

### 5.4 Full demo path (already verified live end-to-end)

paste URL → diagnosis cards → "Discuss" → user pushes back (agent rebuts with numbers + verbatim quotes, never caves) → user reveals real info ("其實我們有 ventilated back panel…") → `action` → progress card → before/after **+25pt** card → (optional) jump to P4, re-run the same intent with `...@v2` in candidates → AI now picks it.

---

## 6. Optional shared views (pipeline dashboard)

If either of you wants a "run the full 5-stage analysis" screen:

```
POST /runs {"brand":"CabinZero","n_intents":60}   → {run_id}
GET  /runs/{id}/events    (EventSource; progress per stage: intents/execute/funnel/attribution/report)
GET  /runs/{id}/funnel    → per-brand retrieved/considered/recommended by engine & cluster  (response.funnel.json)
GET  /runs/{id}/losses    → every verbatim loss reason w/ attribute + cluster + engine
GET  /runs/{id}/report    → full report JSON; ?format=md returns renderable markdown       (response.report.json)
```

⚠ Runs pick the **latest** product versions by default. For a v1-baseline run after a v2 exists, pass explicit `product_refs: ["cabinzero-classic-36l@v1", ...]`.

### 6.1 Structured personas (cross-category)

`personas` accepts the legacy string form and the new structured `PersonaProfile`. Prefer the
structured form for new UI because it preserves budget, use cases and hard/soft search criteria:

```json
{
  "brand": "CabinZero",
  "category": "travel backpack",
  "personas": [{
    "persona_id": "accountant_europe_trip",
    "label": "Europe trip office worker",
    "age": 32,
    "occupation": "accountant",
    "budget": { "max_amount": 150, "currency": "EUR", "flexibility": "soft" },
    "use_cases": ["three-week Europe trip", "daily city walking"],
    "criteria": [{
      "attribute": "comfort",
      "operator": "maximize",
      "importance": "should",
      "reason": "long walking days"
    }],
    "notes": []
  }]
}
```

Each generated `Intent` returns `persona_id` and `persona_profile`, so the UI can explain which
shopper context produced a recommendation. For a non-backpack category, call
`GET /taxonomy?category=<category>`; unknown categories receive the generic product taxonomy.

---

## 7. Gotchas & demo insurance

1. **`cached: true` is your demo insurance** (P6 red line: every live step needs a fallback). Rehearse once with `cached:false`, then demo with `cached:true` → identical output, instant.
2. **202 is a normal response**, not an error: diagnosis (building) and compare (batches running). Always poll.
3. **Mock vs live is invisible to you** — same shapes. `GET /health` → `bedrock.ready:false` means mock. You can force per-request: `"mode":"mock"` on simulate/batch.
4. Live LLM calls can take 8–25 s and occasionally retry on AWS throttling — design every waiting state (skeletons/spinners), never white-screen (spec acceptance criterion).
5. If the backend restarts mid-run, `/runs/{id}/events` sends an `error` event telling you to `POST /runs/{id}/resume` — wire that to a retry button.
6. Deployed URL `http://34.227.93.223:8000` is an Elastic IP — stable across redeploys; still keep it in one env var.
7. Don't invent SSE event types or read backend internals — the contract (`contracts/openapi.yaml` + fixtures) is the only truth. If a shape looks wrong, run `python contracts/check_contract.py http://localhost:8000` and ping backend.
