import { useEffect, useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowRight,
  BarChart3,
  Check,
  CircleHelp,
  FlaskConical,
  Gauge,
  Info,
  LockKeyhole,
  Play,
  RotateCcw,
  Sparkles,
  Trophy,
  WandSparkles,
} from "lucide-react";
import type {
  ActionOffer,
  BatchResult,
  CompareResult,
  DecisionResult,
  Intent,
  Product,
  ProductAttribute,
  ProductRef,
} from "../../contracts/types";
import {
  apiAvailable,
  getBatch,
  getDebateSession,
  getDecision,
  getImpactDemo,
  getProduct,
  getTaxonomy,
  listProducts,
  simulate as simulateApi,
  simulateBatch,
} from "./lib/api";

type IntentPreset = Intent & { label: string };
type DecisionView = {
  winner: ProductRef | null;
  narrative: string;
  ranking: { ref: ProductRef; rank: number; reason: string }[];
};
type Handoff = { before: ProductRef; after: ProductRef; cluster?: string; compareUrl?: string };

const TARGET_BEFORE: ProductRef = "cabinzero-classic-36l@v1";
const TARGET_AFTER: ProductRef = "cabinzero-classic-36l@v2";
const DIAGNOSIS_URL = import.meta.env.VITE_DIAGNOSIS_URL?.trim();

const INTENTS: IntentPreset[] = [
  { label: "All-day comfort", text: "Most comfortable travel backpack for walking all day", cluster_id: "comfort_carry", attributes: ["comfort"] },
  { label: "Ryanair fit", text: "Backpack that definitely fits Ryanair cabin limits", cluster_id: "airline_compliance", attributes: ["airline_compliance"] },
  { label: "Lightest one-bag", text: "Lightest carry-on backpack for one-bag travel", cluster_id: "weight_minimal", attributes: ["weight"] },
  { label: "Laptop carry", text: "Best travel backpack with a 16-inch laptop compartment", cluster_id: "organization_tech", attributes: ["organization"] },
];

const fallbackCandidateRefs: ProductRef[] = [
  TARGET_BEFORE,
  "osprey-farpoint-40@v1",
  "decathlon-forclaz-travel500-40l@v1",
  "cotopaxi-allpa-35l@v1",
];

const fallbackProducts: Record<ProductRef, Product> = {
  [TARGET_BEFORE]: productRecord("cabinzero-classic-36l", "CabinZero", "Classic 36L", 1, [
    attr("price", "$79.95", "Price: $79.95."),
    attr("weight", "760 g", "Weighing just 760 g, it is one of the lightest cabin bags in the world."),
    attr("capacity_size", "36L", "The Classic 36L is the original CabinZero."),
    attr("airline_compliance", "44 × 30 × 24 cm", "Clears Ryanair's priority cabin bag sizer."),
    attr("comfort", null, null),
  ]),
  [TARGET_AFTER]: productRecord("cabinzero-classic-36l", "CabinZero", "Classic 36L", 2, [
    attr("price", "$79.95", "Price: $79.95."),
    attr("weight", "760 g", "Weighing just 760 g, it is one of the lightest cabin bags in the world."),
    attr("capacity_size", "36L", "The Classic 36L is the original CabinZero."),
    attr("airline_compliance", "44 × 30 × 24 cm", "Clears Ryanair's priority cabin bag sizer."),
    attr("comfort", "Ventilated mesh back panel + memory foam shoulder straps", "Features a ventilated mesh back panel for airflow and an extra-thick memory foam shoulder strap system, lab-tested over 6 continuous hours."),
  ]),
  "osprey-farpoint-40@v1": productRecord("osprey-farpoint-40", "Osprey", "Farpoint 40", 1, [attr("price", "$185", "Price: $185."), attr("weight", "1.2 kg", "Farpoint 40 empty weight: 1.2 kg."), attr("comfort", "AirScape back panel and hip belt", "AirScape back panel and hip belt.")]),
  "decathlon-forclaz-travel500-40l@v1": productRecord("decathlon-forclaz-travel500-40l", "Decathlon", "Travel 500 40L", 1, [attr("price", "$99", "Price: $99."), attr("weight", "1.5 kg", "Travel 500 empty weight: 1.5 kg."), attr("comfort", "Foam back and removable waist belt", "Foam back and removable waist belt.")]),
  "cotopaxi-allpa-35l@v1": productRecord("cotopaxi-allpa-35l", "Cotopaxi", "Allpa 35L", 1, [attr("price", "$170", "Price: $170."), attr("weight", "1.5 kg", "Allpa 35L empty weight: 1.5 kg."), attr("comfort", "Comfortable harness system", "Comfortable harness system.")]),
};

function attr(attribute_id: string, value: string | null, evidence: string | null): ProductAttribute {
  return { attribute_id, value, evidence, confidence: value ? 0.95 : 0 };
}

function productRecord(product_id: string, brand: string, display_name: string, version: number, attributes: ProductAttribute[]): Product {
  return { product_id, version, brand, display_name, source: "manual_prototype", source_url: null, raw_text: "", attributes, ref: `${product_id}@v${version}` };
}

const fallbackBefore: DecisionView = {
  winner: "osprey-farpoint-40@v1",
  narrative: "Osprey is the safer recommendation for an all-day carry. CabinZero is lighter and cheaper, but the page gives the AI no clear evidence for back support or long-wear comfort.",
  ranking: [
    { ref: "osprey-farpoint-40@v1", rank: 1, reason: "Back panel and hip belt are explicit." },
    { ref: "decathlon-forclaz-travel500-40l@v1", rank: 2, reason: "Foam back and waist belt are named." },
    { ref: TARGET_BEFORE, rank: 3, reason: "Lightweight, but comfort evidence is missing." },
    { ref: "cotopaxi-allpa-35l@v1", rank: 4, reason: "Comfort is less specific for this use case." },
  ],
};

const fallbackAfter: DecisionView = {
  winner: TARGET_AFTER,
  narrative: "CabinZero now moves to the front because the same lightweight product has explicit, searchable comfort evidence: airflow, memory foam straps, and a six-hour lab test.",
  ranking: [
    { ref: TARGET_AFTER, rank: 1, reason: "Comfort proof is now explicit and specific." },
    { ref: "osprey-farpoint-40@v1", rank: 2, reason: "Strong harness evidence, but heavier." },
    { ref: "decathlon-forclaz-travel500-40l@v1", rank: 3, reason: "Good value, less proof." },
    { ref: "cotopaxi-allpa-35l@v1", rank: 4, reason: "Comfort remains less specific." },
  ],
};

const fallbackCompare: CompareResult = {
  cluster_id: "comfort_carry",
  n_per_side: 16,
  a: { product_ref: TARGET_BEFORE, recommendation_share: 0.75, consideration_share: 1, ci95_recommendation: [0.505, 0.898] },
  b: { product_ref: TARGET_AFTER, recommendation_share: 1, consideration_share: 1, ci95_recommendation: [0.806, 1] },
  delta_recommendation: 0.25,
  changes_applied: ["Features a ventilated mesh back panel for airflow and an extra-thick memory foam shoulder strap system, lab-tested over 6 continuous hours."],
  diff_url: null,
};

function handoffFromLocation(): Handoff | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  const before = params.get("before");
  const after = params.get("after");
  if (!before || !after || !before.includes("@v") || !after.includes("@v")) return null;
  return { before, after, cluster: params.get("cluster") || undefined, compareUrl: params.get("compare_url") || undefined };
}

function refFor(product: Product): ProductRef {
  return product.ref || `${product.product_id}@v${product.version}`;
}

function decisionView(result: DecisionResult): DecisionView {
  const ordered = [...result.per_product].sort((a, b) => {
    const aRank = a.rank ?? (a.verdict === "recommended" ? 1 : 99);
    const bRank = b.rank ?? (b.verdict === "recommended" ? 1 : 99);
    return aRank - bRank;
  });
  return {
    winner: result.winner,
    narrative: result.narrative,
    ranking: ordered.map((item, index) => ({
      ref: item.product_ref,
      rank: item.rank ?? index + 1,
      reason: (item.verdict === "recommended" ? item.reasons_for[0]?.text : item.reasons_against[0]?.text)
        || item.reasons_for[0]?.text || "No explicit reason returned.",
    })),
  };
}

function comparisonFromBatches(
  before: BatchResult,
  after: BatchResult,
  beforeRef: ProductRef,
  afterRef: ProductRef,
  cluster: string,
  changesApplied: string[],
): CompareResult {
  const a = before.shares[beforeRef];
  const b = after.shares[afterRef];
  if (!a || !b) throw new Error("The completed batches did not include the target product.");
  return {
    cluster_id: cluster,
    n_per_side: Math.min(before.decision_ids.length, after.decision_ids.length),
    a: { product_ref: beforeRef, ...a },
    b: { product_ref: afterRef, ...b },
    delta_recommendation: Number((b.recommendation_share - a.recommendation_share).toFixed(3)),
    changes_applied: changesApplied,
    diff_url: null,
  };
}

export function debateSessionId(product: Product): string | null {
  const match = product.change_note?.match(/^debate:(.+)$/);
  return match?.[1] || null;
}

export function matchingAction(messages: { action_offer?: ActionOffer | null }[], before: ProductRef, after: ProductRef): ActionOffer | null {
  return [...messages].reverse().map((message) => message.action_offer).find((action) => (
    action?.status === "started" && action.base_ref === before && action.new_ref === after
  )) || null;
}

function brandKey(brand: string): string {
  return brand.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "");
}

function categoryKey(category: string | null | undefined): string {
  return (category || "travel backpack").toLocaleLowerCase().replace(/[^a-z0-9]+/g, "");
}

function latestCompetitors(items: Product[], target: Product): Product[] {
  const otherBrands = items.filter((item) => (
    item.product_id !== target.product_id && brandKey(item.brand) !== brandKey(target.brand)
  ));
  const sameCategory = otherBrands.filter((item) => categoryKey(item.category) === categoryKey(target.category));
  const pool = sameCategory.length > 0 ? sameCategory : otherBrands;
  const latest = new Map<string, Product>();
  for (const item of pool) {
    const current = latest.get(item.product_id);
    if (!current || item.version > current.version) latest.set(item.product_id, item);
  }
  return [...latest.values()].slice(0, 3);
}

export function App() {
  const initialHandoff = useMemo(handoffFromLocation, []);
  const [intent, setIntent] = useState<IntentPreset>(() => INTENTS.find((item) => item.cluster_id === initialHandoff?.cluster) || INTENTS[0]);
  const [targetBefore, setTargetBefore] = useState<ProductRef>(initialHandoff?.before || TARGET_BEFORE);
  const [targetAfter, setTargetAfter] = useState<ProductRef>(initialHandoff?.after || TARGET_AFTER);
  const [catalog, setCatalog] = useState<Record<ProductRef, Product>>(fallbackProducts);
  const [candidateRefs, setCandidateRefs] = useState<ProductRef[]>(fallbackCandidateRefs);
  const [selected, setSelected] = useState<ProductRef[]>(fallbackCandidateRefs);
  const [running, setRunning] = useState(false);
  const [hasRun, setHasRun] = useState(false);
  const [apiContextReady, setApiContextReady] = useState(false);
  const liveApi = apiAvailable && apiContextReady;
  const [decisionMode, setDecisionMode] = useState<"mock" | "live">("mock");
  const [runPhase, setRunPhase] = useState<"idle" | "baseline" | "updated" | "complete">("idle");
  const [beforeDecision, setBeforeDecision] = useState<DecisionView>({ winner: null, narrative: "", ranking: [] });
  const [afterDecision, setAfterDecision] = useState<DecisionView>({ winner: null, narrative: "", ranking: [] });
  const [comparison, setComparison] = useState<CompareResult | null>(null);
  const [stabilityRuns, setStabilityRuns] = useState<number | null>(null);
  const [stabilityLoading, setStabilityLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const [evidenceChange, setEvidenceChange] = useState(initialHandoff ? "" : fallbackCompare.changes_applied[0]);
  const [statusMessage, setStatusMessage] = useState(initialHandoff ? "Loading the P5 before / after handoff…" : "");

  useEffect(() => {
    if (!apiAvailable) {
      if (initialHandoff) {
        setError("This P5 handoff needs a configured API. Set VITE_API_BASE for the deployed simulator.");
      }
      return;
    }
    setApiContextReady(false);
    setComparison(null);
    let cancelled = false;

    if (!initialHandoff) {
      setStatusMessage("Loading the AWS-seeded v1 / v2 demo case…");
      void (async () => {
        try {
          const demo = await getImpactDemo();
          const competitors = await Promise.all(demo.competitor_refs.map(getProduct));
          if (cancelled) return;
          const beforeRef = refFor(demo.before);
          const afterRef = refFor(demo.after);
          const nextCatalog: Record<ProductRef, Product> = { ...fallbackProducts };
          for (const item of [demo.before, demo.after, ...competitors]) {
            nextCatalog[refFor(item)] = item;
          }
          const nextCandidates = [beforeRef, ...competitors.map(refFor)].slice(0, 4);
          setCatalog(nextCatalog);
          setTargetBefore(beforeRef);
          setTargetAfter(afterRef);
          setCandidateRefs(nextCandidates);
          setSelected(nextCandidates);
          setIntent({ ...demo.intent, label: "AWS seeded demo intent" });
          setEvidenceChange(demo.changes_applied[0] || "");
          setBeforeDecision({ winner: null, narrative: "", ranking: [] });
          setAfterDecision({ winner: null, narrative: "", ranking: [] });
          setHasRun(false);
          setRunPhase("idle");
          setApiContextReady(true);
          setStatusMessage("Loaded two isolated product versions from the AWS impact demo database.");
        } catch {
          if (cancelled) return;
          setStatusMessage("The AWS demo database is unavailable, so this page is using its local fixture replay.");
        }
      })();
      return () => { cancelled = true; };
    }

    void (async () => {
      try {
        const [before, after, allProducts] = await Promise.all([
          getProduct(initialHandoff.before),
          getProduct(initialHandoff.after),
          listProducts(),
        ]);
        if (cancelled) return;
        let competitors = latestCompetitors(allProducts, before);
        let recoveredContext = false;
        let recoveredIntent: Intent | null = null;
        const sessionId = debateSessionId(after);
        if (sessionId) {
          try {
            const session = await getDebateSession(sessionId);
            const action = matchingAction(session.messages, refFor(before), refFor(after));
            if (action?.params.additions?.length) setEvidenceChange(action.params.additions[0]);
            if (action?.batch_a) {
              const baselineBatch = await getBatch(action.batch_a);
              const competitorRefs = baselineBatch.candidates.filter((ref) => ref !== refFor(before));
              const byRef = new Map(allProducts.map((product) => [refFor(product), product]));
              competitors = (await Promise.all(competitorRefs.map(async (ref) => (
                byRef.get(ref) || getProduct(ref)
              )))).slice(0, 3);
              recoveredContext = competitors.length > 0;
              if (baselineBatch.decision_ids[0]) {
                recoveredIntent = (await getDecision(baselineBatch.decision_ids[0])).intent;
              }
            }
          } catch {
            recoveredContext = false;
          }
        }
        if (!recoveredIntent) {
          const taxonomy = await getTaxonomy(before.category);
          const clusterId = initialHandoff.cluster || taxonomy.clusters[0]?.id || "other";
          const cluster = taxonomy.clusters.find((item) => item.id === clusterId);
          recoveredIntent = {
            text: cluster?.description || `Best ${before.category || "product"} for this buyer need`,
            cluster_id: clusterId,
            attributes: cluster?.attributes || [],
          };
        }
        if (!evidenceChange) {
          const appended = after.raw_text.startsWith(before.raw_text)
            ? after.raw_text.slice(before.raw_text.length).trim()
            : "";
          if (appended) setEvidenceChange(appended.split(/\n{2,}/)[0]);
        }
        const nextCatalog: Record<ProductRef, Product> = { ...fallbackProducts };
        for (const item of [before, after, ...competitors]) nextCatalog[refFor(item)] = item;
        const nextCandidates = [refFor(before), ...competitors.map(refFor)].slice(0, 4);
        setCatalog(nextCatalog);
        setTargetBefore(refFor(before));
        setTargetAfter(refFor(after));
        setCandidateRefs(nextCandidates);
        setSelected(nextCandidates);
        setIntent({ ...recoveredIntent, label: "P5 diagnosed intent" });
        setBeforeDecision({ winner: null, narrative: "", ranking: [] });
        setAfterDecision({ winner: null, narrative: "", ranking: [] });
        setHasRun(false);
        setRunPhase("idle");
        setApiContextReady(true);
        setStatusMessage(recoveredContext
          ? "Recovered the exact product versions, buyer intent, and comparison set persisted by P5."
          : "P5 product versions loaded. No persisted batch context was found, so same-category competitors were selected.");
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load the P5 handoff.");
      }
    })();
    return () => { cancelled = true; };
  }, [initialHandoff]);

  const targetProduct = catalog[targetBefore] || fallbackProducts[TARGET_BEFORE];
  const targetBrand = targetProduct.brand;
  const beforeRank = beforeDecision.ranking.find((item) => item.ref === targetBefore)?.rank ?? "—";
  const afterRank = afterDecision.ranking.find((item) => item.ref === targetAfter)?.rank ?? "—";
  const deltaPoints = comparison ? Math.round(comparison.delta_recommendation * 100) : null;
  const featureChange = comparison?.changes_applied.find((item) => !item.startsWith("note:")) || evidenceChange || "New product evidence added in P5.";

  function toggleCandidate(ref: ProductRef) {
    if (ref === targetBefore) return;
    setSelected((current) => current.includes(ref) ? current.filter((item) => item !== ref) : current.length < 4 ? [...current, ref] : current);
  }

  async function runComparison() {
    if (selected.length < 2 || running) return;
    setRunning(true);
    setHasRun(false);
    setStabilityRuns(null);
    setError("");
    setRunPhase("baseline");
    setStatusMessage(liveApi ? `Running the v1 baseline with the ${decisionMode === "mock" ? "deterministic evidence" : "Bedrock"} model…` : "Running the controlled fixture replay…");

    if (liveApi) {
      try {
        const before = await simulateApi(intent, selected, () => undefined, { mode: decisionMode });
        const beforeBatch = await simulateBatch(intent.cluster_id, selected, { mode: decisionMode });
        setBeforeDecision(decisionView(before));
        setRunPhase("updated");
        setStatusMessage("Baseline frozen. Switching only the target product from v1 to v2…");
        const afterCandidates = selected.map((ref) => ref === targetBefore ? targetAfter : ref);
        const after = await simulateApi(intent, afterCandidates, () => undefined, { mode: decisionMode });
        const afterBatch = await simulateBatch(intent.cluster_id, afterCandidates, { mode: decisionMode });
        setAfterDecision(decisionView(after));
        setComparison(comparisonFromBatches(beforeBatch, afterBatch, targetBefore, targetAfter, intent.cluster_id, [featureChange]));
        setRunPhase("complete");
        setStatusMessage("Controlled comparison complete. Both sides used the same intents, competitors, model, and run count.");
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "The comparison failed.");
        setStatusMessage("The comparison stopped; completed results were preserved.");
        setRunPhase("idle");
      }
    } else {
      await new Promise((resolve) => window.setTimeout(resolve, 650));
      setBeforeDecision(fallbackBefore);
      setRunPhase("updated");
      setStatusMessage("Baseline frozen. Applying the v2 evidence change…");
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      setAfterDecision(fallbackAfter);
      setComparison(fallbackCompare);
      setRunPhase("complete");
      setStatusMessage("Controlled demo replay complete.");
    }

    setHasRun(true);
    setRunning(false);
    setStabilityRuns(liveApi ? null : 5);
  }

  async function runStabilityCheck() {
    if (stabilityLoading) return;
    if (!liveApi) {
      setStabilityRuns(5);
      return;
    }
    setStabilityLoading(true);
    setStabilityRuns(0);
    setError("");
    const afterCandidates = selected.map((ref) => ref === targetBefore ? targetAfter : ref);
    let wins = 0;
    try {
      for (let index = 0; index < 5; index += 1) {
        const result = await simulateApi(intent, afterCandidates, () => undefined, { cached: false, mode: decisionMode });
        if (result.winner === targetAfter) wins += 1;
        setStabilityRuns(wins);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The stability check failed.");
    } finally {
      setStabilityLoading(false);
    }
  }

  function copyChange() {
    void navigator.clipboard?.writeText(featureChange);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup"><div className="brand-mark">S</div><span>Signal Audit</span></div>
        <div className="sidebar-label">Workspace</div>
        <nav>
          {DIAGNOSIS_URL ? <a className="nav-item" href={DIAGNOSIS_URL}><BarChart3 size={17} /><span>Diagnosis</span><span className="nav-count">03</span></a> : <button className="nav-item muted" disabled><BarChart3 size={17} /><span>Diagnosis</span><span className="nav-count">03</span></button>}
          <button className="nav-item muted" disabled><CircleHelp size={17} /><span>Debate</span><span className="nav-count">04</span></button>
          <button className="nav-item active"><Gauge size={17} /><span>Impact test</span><span className="nav-count">P4</span></button>
        </nav>
        <div className="sidebar-bottom">
          <div className="status-dot"><span /> {liveApi ? "Connected API" : "Fixture preview"}</div>
          <div className="sidebar-note">One controlled evidence change at a time.</div>
        </div>
      </aside>

      <main className="main-content">
        <div className="page-wrap">
          {error && <div className="error-banner" role="alert"><strong>Something needs attention.</strong><span>{error}</span><button onClick={() => setError("")}>×</button></div>}
          {statusMessage && <div className="status-banner"><Info size={14} /><span>{statusMessage}</span></div>}
          <section className="hero-copy">
            <div className="eyebrow">BEFORE / AFTER RECOMMENDATION TEST</div>
            <div className="hero-row">
              <div><h1>Measure what the new evidence changed.</h1><p>Run the same buyer questions against the original P5 product and its updated version. Only the product evidence changes.</p></div>
              <div className="hero-side"><span className="stage">05 · IMPACT TEST</span><div className="hero-stat"><span className="stat-kicker">Measured lift</span><strong>{deltaPoints === null ? "—" : <>{deltaPoints >= 0 ? "+" : ""}{deltaPoints}</>}<span>{deltaPoints === null ? " pending" : " pts"}</span></strong><small>recommendation share</small></div></div>
            </div>
          </section>

          <div className="stepper">
            <div className={`step ${runPhase !== "idle" || hasRun ? "active" : ""}`}><span>{runPhase === "updated" || runPhase === "complete" || hasRun ? "✓" : "01"}</span><div><b>Run baseline</b><small>v1 + fixed competitors</small></div></div><div className="step-line" />
            <div className={`step ${runPhase === "updated" || runPhase === "complete" || hasRun ? "active" : ""}`}><span>{runPhase === "complete" || hasRun ? "✓" : "02"}</span><div><b>Apply evidence</b><small>switch v1 → v2 only</small></div></div><div className="step-line" />
            <div className={`step ${runPhase === "complete" || hasRun ? "active" : ""}`}><span>{runPhase === "complete" || hasRun ? "✓" : "03"}</span><div><b>Run updated</b><small>same prompts + model</small></div></div>
          </div>

          <section className="workspace-grid">
            <aside className="control-panel">
              <div className="panel-heading"><div><span className="panel-index">TEST SETUP</span><h2>{targetProduct.display_name}</h2></div><CircleHelp size={16} /></div>
              <label className="field-label">What are they trying to solve?</label>
              <div className="intent-input">{intent.text}</div>
              {!initialHandoff && <div className="preset-list">{INTENTS.map((preset) => <button key={preset.cluster_id} className={`preset ${intent.cluster_id === preset.cluster_id ? "selected" : ""}`} onClick={() => setIntent(preset)}>{preset.label}<span>{intent.cluster_id === preset.cluster_id ? <Check size={14} /> : <ArrowRight size={14} />}</span></button>)}</div>}
              <div className="field-divider" />
              <div className="candidate-heading"><label className="field-label">Decision model</label><span>{decisionMode === "mock" ? "Repeatable" : "AWS Bedrock"}</span></div>
              <div className="mode-toggle"><button className={decisionMode === "mock" ? "selected" : ""} onClick={() => setDecisionMode("mock")}>Deterministic evidence</button><button className={decisionMode === "live" ? "selected" : ""} onClick={() => setDecisionMode("live")}>Bedrock live</button></div>
              <div className="field-divider" />
              <div className="candidate-heading"><label className="field-label">Candidate products</label><span>{selected.length} / 4</span></div>
              <div className="candidate-list">{candidateRefs.map((ref) => {
                const item = catalog[ref];
                if (!item) return null;
                const isSelected = selected.includes(ref);
                const price = item.attributes.find((attribute) => attribute.attribute_id === "price")?.value || `v${item.version}`;
                return <button key={ref} className={`candidate-row ${isSelected ? "selected" : ""}`} onClick={() => toggleCandidate(ref)}><span className={`check-box ${isSelected ? "checked" : ""}`}>{isSelected && <Check size={12} />}</span><span className="candidate-name"><b title={item.brand}>{item.brand}</b><small title={`${item.display_name} · v${item.version}`}>{item.display_name} · v{item.version}</small></span><span className="candidate-price" title={price}>{price}</span></button>;
              })}</div>
              <button className="run-button" disabled={selected.length < 2 || running} onClick={() => void runComparison()}>{running ? <><span className="button-spinner" /> {runPhase === "updated" ? "Running updated version…" : "Running baseline…"}</> : <><Play size={15} fill="currentColor" /> Run both versions</>}</button>
              <div className="run-caption"><LockKeyhole size={12} /> Same intent · same candidates · one content change</div>
            </aside>

            <section className="results-panel">
              <div className="result-heading"><div><div className="eyebrow muted-eyebrow"><WandSparkles size={13} /> THE RESULT</div><h2>Same buyer. New evidence.</h2></div><div className="result-meta"><span className="live-indicator"><span />{running ? "Simulating" : "Ready"}</span><span className="divider-dot" />{intent.cluster_id}</div></div>
              <div className="comparison-cards">
                <ComparisonCard label="Before" version={versionFromRef(targetBefore)} share={comparison?.a.recommendation_share ?? null} decision={beforeDecision} targetRef={targetBefore} catalog={catalog} muted={!hasRun} />
                <div className="change-arrow"><ArrowDownRight size={20} /><span>{deltaPoints === null ? "Pending" : <>{deltaPoints >= 0 ? "+" : ""}{deltaPoints} pts</>}</span></div>
                <ComparisonCard label="After" version={versionFromRef(targetAfter)} share={comparison?.b.recommendation_share ?? null} decision={afterDecision} targetRef={targetAfter} catalog={catalog} muted={!hasRun} after />
              </div>

              <div className="move-card"><div className="move-copy"><div className="eyebrow muted-eyebrow">THE MOVEMENT</div><h3>{comparison ? (deltaPoints === 0 ? "No measurable recommendation-share change" : <>{targetBrand} moved from <strong>#{beforeRank}</strong> to <strong>#{afterRank}</strong></>) : "Run both versions to reveal the impact"}</h3><p>{deltaPoints !== null && deltaPoints > 0 ? "The newly explicit evidence improved this controlled result." : "The result is reported as measured; improvement is never forced."}</p></div><div className="rank-flow"><div className="rank-node before-rank"><span>Before</span><strong>#{beforeRank}</strong><small>{targetBrand}</small></div><ArrowRight size={20} /><div className="rank-node after-rank"><span>After</span><strong>#{afterRank}</strong><small>{targetBrand}</small></div></div></div>

              <div className="proof-grid">
                <div className="proof-card feature-proof"><div className="proof-card-head"><span className="proof-icon"><FlaskConical size={16} /></span><div><div className="eyebrow muted-eyebrow">THE CONTENT CHANGE</div><h3>One missing signal, made explicit</h3></div></div><div className="copy-diff"><div className="diff-old"><span>{versionFromRef(targetBefore)} · evidence</span><strong>?</strong><p>No verifiable evidence for this buyer need.</p></div><ArrowRight size={16} /><div className="diff-new"><span>{versionFromRef(targetAfter)} · evidence</span><strong>✓</strong><p>{featureChange}</p></div></div><button className="copy-button" onClick={copyChange}>{copied ? <><Check size={14} /> Copied to clipboard</> : "Copy the added feature"}</button></div>
                <div className="proof-card stability-card"><div className="proof-card-head"><span className="proof-icon green"><Trophy size={16} /></span><div><div className="eyebrow muted-eyebrow">STABILITY CHECK</div><h3>After: repeated picks</h3></div></div><div className="stability-number"><strong>{stabilityRuns ?? "—"}</strong><span>/ 5 runs picked<br />{targetBrand}</span></div><div className="stability-bar"><span style={{ width: stabilityRuns === null ? "0%" : `${stabilityRuns * 20}%` }} /></div><button className="text-button" disabled={stabilityLoading} onClick={() => void runStabilityCheck()}><RotateCcw size={13} /> {stabilityLoading ? "Running…" : "Run ×5 again"}</button></div>
              </div>
              <div className="method-note"><Info size={15} /><span><strong>Controlled simulation.</strong> Same buyer intents, decision model, candidate set, and run count. The only changed input is {versionFromRef(targetBefore)} → {versionFromRef(targetAfter)} product evidence.</span></div>
            </section>
          </section>
        </div>
      </main>
    </div>
  );
}

function versionFromRef(ref: ProductRef): string {
  return ref.match(/@(v\d+)$/)?.[1] || "version";
}

function ComparisonCard({ label, version, share, decision, targetRef, catalog, muted, after = false }: { label: string; version: string; share: number | null; decision: DecisionView; targetRef: ProductRef; catalog: Record<ProductRef, Product>; muted: boolean; after?: boolean }) {
  const target = decision.ranking.find((item) => item.ref === targetRef);
  return <article className={`comparison-card ${after ? "after-card" : ""} ${muted ? "is-muted" : ""}`}>
    <div className="card-topline"><span className="version-label">{label} <b>{version}</b></span>{after ? <span className="new-badge"><Sparkles size={11} /> New signal</span> : <span className="old-badge">Original page</span>}</div>
      <div className="score-row"><div className="score-ring" style={{ "--score": `${Math.max(0, Math.min(1, share ?? 0)) * 100}%` } as React.CSSProperties}><div><strong>{share === null ? "—" : `${Math.round(share * 100)}%`}</strong><span>recommendation<br />share</span></div></div><div className="score-copy"><span>{catalog[targetRef]?.brand || "Product"} rank</span><strong>#{target?.rank ?? "—"}</strong><small>{target?.reason || "Run the simulation to load a reason."}</small></div></div>
    <div className="mini-ranking">{decision.ranking.map((item) => <div className={`mini-rank ${item.ref === targetRef ? "target" : ""}`} key={item.ref}><span className="mini-rank-number">{item.rank}</span><span>{catalog[item.ref]?.brand || item.ref.split("@")[0]}</span></div>)}</div>
    <div className="narrative"><span className="quote-mark">“</span><p>{decision.narrative || "Waiting for the AI readout…"}</p></div>
  </article>;
}
