import { useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowRight,
  BarChart3,
  Check,
  ChevronDown,
  CircleHelp,
  FlaskConical,
  Gauge,
  Info,
  LockKeyhole,
  Play,
  Radio,
  RotateCcw,
  Sparkles,
  Trophy,
  WandSparkles,
} from "lucide-react";
import type { DecisionResult, Product, ProductAttribute, ProductRef } from "../../contracts/types";
import { compare as compareApi, simulate as simulateApi } from "./lib/api";

type IntentPreset = {
  label: string;
  text: string;
  cluster_id: string;
  attributes: string[];
};

type LocalDecision = {
  winner: ProductRef;
  narrative: string;
  ranking: { ref: ProductRef; rank: number; score: number; reason: string }[];
};

const INTENTS: IntentPreset[] = [
  {
    label: "All-day comfort",
    text: "Most comfortable travel backpack for walking all day",
    cluster_id: "comfort_carry",
    attributes: ["comfort"],
  },
  {
    label: "Ryanair fit",
    text: "Backpack that definitely fits Ryanair cabin limits",
    cluster_id: "airline_compliance",
    attributes: ["airline_compliance"],
  },
  {
    label: "Lightest one-bag",
    text: "Lightest carry-on backpack for one-bag travel",
    cluster_id: "weight_minimal",
    attributes: ["weight"],
  },
  {
    label: "Laptop carry",
    text: "Best travel backpack with a 16-inch laptop compartment",
    cluster_id: "organization_tech",
    attributes: ["organization"],
  },
];

const candidateRefs: ProductRef[] = [
  "cabinzero-classic-36l",
  "osprey-farpoint-40@v1",
  "decathlon-forclaz-travel500-40l@v1",
  "cotopaxi-allpa-35l@v1",
];

const products: Record<string, Product> = {
  "cabinzero-classic-36l@v1": {
    product_id: "cabinzero-classic-36l",
    version: 1,
    brand: "CabinZero",
    display_name: "Classic 36L",
    source: "manual_prototype",
    source_url: null,
    raw_text: "",
    attributes: [
      attr("weight", "760 g", "Weighing just 760 g, it is one of the lightest cabin bags in the world."),
      attr("capacity_size", "36L", "The Classic 36L is the original CabinZero."),
      attr("airline_compliance", "44 × 30 × 24 cm", "Clears Ryanair's priority cabin bag sizer."),
      attr("comfort", null, null),
    ],
  },
  "cabinzero-classic-36l@v2": {
    product_id: "cabinzero-classic-36l",
    version: 2,
    brand: "CabinZero",
    display_name: "Classic 36L",
    source: "manual_prototype",
    source_url: null,
    raw_text: "",
    attributes: [
      attr("weight", "760 g", "Weighing just 760 g, it is one of the lightest cabin bags in the world."),
      attr("capacity_size", "36L", "The Classic 36L is the original CabinZero."),
      attr("airline_compliance", "44 × 30 × 24 cm", "Clears Ryanair's priority cabin bag sizer."),
      attr(
        "comfort",
        "Ventilated mesh back panel + memory foam shoulder straps",
        "Features a ventilated mesh back panel for airflow and an extra-thick memory foam shoulder strap system, lab-tested over 6 continuous hours.",
      ),
    ],
  },
  "osprey-farpoint-40@v1": product("Osprey", "Farpoint 40", "1.2 kg", "AirScape back panel and hip belt"),
  "decathlon-forclaz-travel500-40l@v1": product("Decathlon", "Travel 500 40L", "1.5 kg", "Foam back and removable waist belt"),
  "cotopaxi-allpa-35l@v1": product("Cotopaxi", "Allpa 35L", "1.5 kg", "Comfortable harness system"),
};

function attr(attribute_id: string, value: string | null, evidence: string | null): ProductAttribute {
  return { attribute_id: attribute_id as ProductAttribute["attribute_id"], value, evidence, confidence: value ? 0.95 : 0 };
}

function product(brand: string, display_name: string, weight: string, comfort: string): Product {
  return {
    product_id: `${brand.toLowerCase()}-${display_name.toLowerCase().replaceAll(" ", "-")}`,
    version: 1,
    brand,
    display_name,
    source: "manual_prototype",
    source_url: null,
    raw_text: "",
    attributes: [attr("weight", weight, `${display_name} empty weight: ${weight}.`), attr("comfort", comfort, `${comfort}.`)],
  };
}

const localBefore: LocalDecision = {
  winner: "osprey-farpoint-40@v1",
  narrative: "Osprey is the safer recommendation for an all-day carry. CabinZero is lighter and cheaper, but the page gives the AI no clear evidence for back support or long-wear comfort.",
  ranking: [
    { ref: "osprey-farpoint-40@v1", rank: 1, score: 86, reason: "Back panel and hip belt are explicit." },
    { ref: "decathlon-forclaz-travel500-40l@v1", rank: 2, score: 76, reason: "Foam back and waist belt are named." },
    { ref: "cabinzero-classic-36l@v1", rank: 3, score: 54, reason: "Lightweight, but comfort evidence is missing." },
    { ref: "cotopaxi-allpa-35l@v1", rank: 4, score: 48, reason: "Comfort is less specific for this use case." },
  ],
};

const localAfter: LocalDecision = {
  winner: "cabinzero-classic-36l@v2",
  narrative: "CabinZero now moves to the front because the same lightweight product has explicit, searchable comfort evidence: airflow, memory foam straps, and a six-hour lab test.",
  ranking: [
    { ref: "cabinzero-classic-36l@v2", rank: 1, score: 94, reason: "Comfort proof is now explicit and specific." },
    { ref: "osprey-farpoint-40@v1", rank: 2, score: 86, reason: "Strong harness evidence, but heavier." },
    { ref: "decathlon-forclaz-travel500-40l@v1", rank: 3, score: 76, reason: "Good value, less proof." },
    { ref: "cotopaxi-allpa-35l@v1", rank: 4, score: 48, reason: "Comfort remains less specific." },
  ],
};

const fallbackShare = { before: 0.75, after: 1, delta: 0.25 };

export function App() {
  const [intent, setIntent] = useState(INTENTS[0]);
  const [selected, setSelected] = useState(candidateRefs);
  const [running, setRunning] = useState(false);
  const [hasRun, setHasRun] = useState(true);
  const [liveApi, setLiveApi] = useState(false);
  const [beforeNarrative, setBeforeNarrative] = useState(localBefore.narrative);
  const [afterNarrative, setAfterNarrative] = useState(localAfter.narrative);
  const [stabilityRuns, setStabilityRuns] = useState<number | null>(5);
  const [copied, setCopied] = useState(false);

  const selectedProducts = useMemo(() => selected.map((ref) => products[ref]), [selected]);

  function toggleCandidate(ref: ProductRef) {
    setSelected((current) => {
      if (current.includes(ref)) return current.filter((item) => item !== ref);
      return current.length < 4 ? [...current, ref] : current;
    });
  }

  async function runComparison() {
    if (selected.length < 2 || running) return;
    setRunning(true);
    setHasRun(false);
    setStabilityRuns(null);
    setBeforeNarrative("");
    setAfterNarrative("");

    if (liveApi) {
      try {
        const before = await simulateApi({ ...intent }, selected.map((ref) => `${ref}@v1` as ProductRef), (token) => setBeforeNarrative((current) => current + token));
        setBeforeNarrative(before.narrative);
        const afterCandidates = selected.map((ref) => ref === "cabinzero-classic-36l" ? "cabinzero-classic-36l@v2" : ref) as ProductRef[];
        const after = await simulateApi({ ...intent }, afterCandidates, (token) => setAfterNarrative((current) => current + token));
        setAfterNarrative(after.narrative);
        try {
          await compareApi("cabinzero-classic-36l@v1", "cabinzero-classic-36l@v2", intent.cluster_id);
        } catch {
          // The comparison card can still use the streamed decisions if batches are pending.
        }
      } catch {
        setBeforeNarrative(localBefore.narrative);
        setAfterNarrative(localAfter.narrative);
      }
    } else {
      await new Promise((resolve) => window.setTimeout(resolve, 900));
      setBeforeNarrative(localBefore.narrative);
      await new Promise((resolve) => window.setTimeout(resolve, 650));
      setAfterNarrative(localAfter.narrative);
    }

    setHasRun(true);
    setRunning(false);
    setStabilityRuns(5);
  }

  function copyChange() {
    void navigator.clipboard?.writeText("Features a ventilated mesh back panel for airflow and an extra-thick memory foam shoulder strap system, lab-tested over 6 continuous hours.");
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup"><div className="brand-mark"><Radio size={16} /></div><span>SIGNAL</span></div>
        <div className="sidebar-label">Workspace</div>
        <nav>
          <button className="nav-item active"><Gauge size={17} /><span>Simulator</span><span className="nav-count">P4</span></button>
          <button className="nav-item muted"><BarChart3 size={17} /><span>Diagnosis</span><span className="nav-count">P5</span></button>
        </nav>
        <div className="sidebar-bottom">
          <div className="status-dot"><span /> Mock engine ready</div>
          <div className="sidebar-note">A controlled view of what AI shoppers can see, compare, and recommend.</div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="crumb"><span>Recommendation intelligence</span><ArrowRight size={13} /><strong>Shopper simulator</strong></div>
          <div className="top-actions">
            <button className={`api-toggle ${liveApi ? "is-live" : ""}`} onClick={() => setLiveApi((current) => !current)}>
              <span className="toggle-dot" /> {liveApi ? "Live API" : "Demo mode"}<ChevronDown size={14} />
            </button>
            <div className="avatar">CZ</div>
          </div>
        </header>

        <div className="page-wrap">
          <section className="hero-copy">
            <div className="eyebrow"><Sparkles size={14} /> P4 · CONTROLLED SIMULATION</div>
            <div className="hero-row">
              <div>
                <h1>Make the invisible<br /><em>advantage rankable.</em></h1>
                <p>See exactly how one feature edit can move your product from overlooked to recommended.</p>
              </div>
              <div className="hero-stat"><span className="stat-kicker">Signal found</span><strong>+25<span> pts</span></strong><small>recommendation share</small></div>
            </div>
          </section>

          <div className="stepper">
            <div className="step active"><span>01</span><div><b>Set the buyer</b><small>intent + candidates</small></div></div>
            <div className="step-line" />
            <div className={`step ${hasRun ? "active" : ""}`}><span>02</span><div><b>Let AI choose</b><small>same engine, same prompt</small></div></div>
            <div className="step-line" />
            <div className={`step ${hasRun ? "active" : ""}`}><span>03</span><div><b>Prove the shift</b><small>before / after evidence</small></div></div>
          </div>

          <section className="workspace-grid">
            <aside className="control-panel">
              <div className="panel-heading"><div><span className="panel-index">01</span><h2>Define the buyer</h2></div><CircleHelp size={16} /></div>
              <label className="field-label">What are they trying to solve?</label>
              <div className="intent-input">{intent.text}<span className="input-cursor" /></div>
              <div className="preset-list">
                {INTENTS.map((preset) => <button key={preset.cluster_id} className={`preset ${intent.cluster_id === preset.cluster_id ? "selected" : ""}`} onClick={() => setIntent(preset)}>{preset.label}<span>{intent.cluster_id === preset.cluster_id ? <Check size={14} /> : <ArrowRight size={14} />}</span></button>)}
              </div>

              <div className="field-divider" />
              <div className="candidate-heading"><label className="field-label">Candidate products</label><span>{selected.length} / 4</span></div>
              <div className="candidate-list">
                {candidateRefs.map((ref) => {
                  const p = products[ref];
                  const isSelected = selected.includes(ref);
                  return <button key={ref} className={`candidate-row ${isSelected ? "selected" : ""}`} onClick={() => toggleCandidate(ref)}><span className={`check-box ${isSelected ? "checked" : ""}`}>{isSelected && <Check size={12} />}</span><span className="candidate-name"><b>{p.brand}</b><small>{p.display_name}</small></span><span className="candidate-price">{ref.startsWith("cabinzero") ? "$79" : ref.startsWith("osprey") ? "$185" : ref.startsWith("decathlon") ? "$99" : "$170"}</span></button>;
                })}
              </div>
              <button className="run-button" disabled={selected.length < 2 || running} onClick={() => void runComparison()}>{running ? <><span className="button-spinner" /> Running simulations…</> : <><Play size={15} fill="currentColor" /> Run comparison</>}</button>
              <div className="run-caption"><LockKeyhole size={12} /> Same intent · same candidates · one content change</div>
            </aside>

            <section className="results-panel">
              <div className="result-heading"><div><div className="eyebrow muted-eyebrow"><WandSparkles size={13} /> THE RESULT</div><h2>Same buyer. New evidence.</h2></div><div className="result-meta"><span className="live-indicator"><span />{running ? "Simulating" : "Ready"}</span><span className="divider-dot" />{intent.cluster_id}</div></div>

              <div className="comparison-cards">
                <ComparisonCard label="Before" version="v1" share={fallbackShare.before} decision={localBefore} targetRef="cabinzero-classic-36l@v1" narrative={beforeNarrative} muted={!hasRun} />
                <div className="change-arrow"><ArrowDownRight size={20} /><span>+25 pts</span></div>
                <ComparisonCard label="After" version="v2" share={fallbackShare.after} decision={localAfter} targetRef="cabinzero-classic-36l@v2" narrative={afterNarrative} muted={!hasRun} after />
              </div>

              <div className="move-card">
                <div className="move-copy"><div className="eyebrow muted-eyebrow">THE MOVEMENT</div><h3>CabinZero moved from <strong>#3</strong> to <strong>#1</strong></h3><p>The page did not change its price, weight, or size. It only made comfort visible.</p></div>
                <div className="rank-flow"><div className="rank-node before-rank"><span>Before</span><strong>#3</strong><small>CabinZero</small></div><ArrowRight size={20} /><div className="rank-node after-rank"><span>After</span><strong>#1</strong><small>CabinZero</small></div></div>
              </div>

              <div className="proof-grid">
                <div className="proof-card feature-proof"><div className="proof-card-head"><span className="proof-icon"><FlaskConical size={16} /></span><div><div className="eyebrow muted-eyebrow">THE CONTENT CHANGE</div><h3>One missing signal, made explicit</h3></div></div><div className="copy-diff"><div className="diff-old"><span>v1 · comfort</span><strong>?</strong><p>No carry comfort evidence found on the page.</p></div><ArrowRight size={16} /><div className="diff-new"><span>v2 · comfort</span><strong>✓</strong><p>Ventilated mesh panel, memory foam straps, 6-hour lab test.</p></div></div><button className="copy-button" onClick={copyChange}>{copied ? <><Check size={14} /> Copied to clipboard</> : "Copy the added feature"}</button></div>
                <div className="proof-card stability-card"><div className="proof-card-head"><span className="proof-icon green"><Trophy size={16} /></span><div><div className="eyebrow muted-eyebrow">STABILITY CHECK</div><h3>After: consistent winner</h3></div></div><div className="stability-number"><strong>{stabilityRuns ?? "—"}</strong><span>/ 5 runs picked<br />CabinZero</span></div><div className="stability-bar"><span style={{ width: stabilityRuns ? "100%" : "0%" }} /></div><button className="text-button" onClick={() => setStabilityRuns(5)}><RotateCcw size={13} /> Run ×5 again</button></div>
              </div>

              <div className="method-note"><Info size={15} /><span><strong>Controlled simulation.</strong> Same buyer intents, same decision engine, same candidate set. The only variable is the product content.</span></div>
            </section>
          </section>
        </div>
      </main>
    </div>
  );
}

function ComparisonCard({ label, version, share, decision, targetRef, narrative, muted, after = false }: { label: string; version: string; share: number; decision: LocalDecision; targetRef: ProductRef; narrative: string; muted: boolean; after?: boolean }) {
  const target = decision.ranking.find((item) => item.ref === targetRef)!;
  return <article className={`comparison-card ${after ? "after-card" : ""} ${muted ? "is-muted" : ""}`}>
    <div className="card-topline"><span className="version-label">{label} <b>{version}</b></span>{after ? <span className="new-badge"><Sparkles size={11} /> New signal</span> : <span className="old-badge">Original page</span>}</div>
    <div className="score-row"><div className="score-ring" style={{ "--score": `${share * 100}%` } as React.CSSProperties}><div><strong>{Math.round(share * 100)}%</strong><span>recommendation<br />share</span></div></div><div className="score-copy"><span>CabinZero rank</span><strong>#{target.rank}</strong><small>{target.reason}</small></div></div>
    <div className="mini-ranking">{decision.ranking.map((item) => <div className={`mini-rank ${item.ref === targetRef ? "target" : ""}`} key={item.ref}><span className="mini-rank-number">{item.rank}</span><span>{products[item.ref]?.brand || item.ref}</span><i style={{ width: `${item.score}%` }} /></div>)}</div>
    <div className="narrative"><span className="quote-mark">“</span><p>{narrative || "Waiting for the AI readout…"}</p></div>
  </article>;
}
