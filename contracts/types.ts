/**
 * Contract v3 — TypeScript types for the frontends (P4/P5).
 * Mirrors contracts/schemas.py; real examples in backend/mock_fixtures/.
 * If a shape here disagrees with a fixture, the fixture wins — tell backend.
 */

// ---------- primitives ----------
export type ProductRef = string;   // "cabinzero-classic-36l@v1"
export type AttributeId =
  | "price" | "weight" | "comfort" | "airline_compliance" | "capacity_size"
  | "organization" | "durability" | "warranty" | "style_design" | "security"
  | "sustainability" | "brand_reputation" | "quality" | "features" | "compatibility"
  | "size_dimensions" | "performance" | "ease_of_use" | "design" | "availability"
  | "other" | (string & {});                 // category-specific taxonomy extension
export type BrandSlug = string;    // "cabinzero" | "osprey" | ...
export type GapClass = "information_gap" | "product_gap" | "mixed" | "unclear";
export type Severity = "high" | "medium" | "low";
export type SearchValue =
  | string | number | boolean | null
  | Array<string | number | boolean | null>
  | Record<string, string | number | boolean | null>;

export interface ApiError { error: { code: string; message: string; hint?: string } }

// ---------- cross-category shopper / search profile ----------
export interface SearchLocation {
  country?: string | null;
  region?: string | null;
  city?: string | null;
}

export interface SearchBudget {
  min_amount?: number | null;
  max_amount?: number | null;
  currency?: string | null;
  flexibility?: "hard" | "soft";
}

export type SearchOperator =
  | "eq" | "neq" | "lte" | "gte" | "between" | "in" | "not_in"
  | "contains" | "not_contains" | "supports" | "exists" | "maximize" | "minimize";

export interface SearchCriterion {
  attribute: string;                         // category-specific taxonomy key
  operator: SearchOperator;
  value?: SearchValue;
  unit?: string | null;
  importance?: "must" | "should" | "nice_to_have";
  reason?: string | null;
}

export interface ReferenceProduct {
  name: string;
  relation?: "owns" | "likes" | "dislikes" | "compare_with" | "alternative_to" | "compatible_with";
  notes?: string | null;
}

export interface PersonaProfile {
  persona_id: string;
  label: string;
  relationship_to_buyer?: string;
  age?: number | null;
  occupation?: string | null;
  location?: SearchLocation | null;
  budget?: SearchBudget | null;
  use_cases?: string[];
  criteria?: SearchCriterion[];
  reference_products?: ReferenceProduct[];
  context?: Record<string, SearchValue>;
  notes?: string[];
}

// ---------- product (P1) ----------
export interface ProductAttribute {
  attribute_id: AttributeId;
  value: string | null;            // null = "the page doesn't say it" → grey "?" chip
  evidence: string | null;         // verbatim quote; image-derived ones start with "[from image]"
  confidence: number;              // 0..1
  source?: "text" | "image";       // "image" = recovered by vision — render a 📷 badge + warning
}

export interface Product {
  product_id: string;
  brand: string;
  display_name: string;
  source: "url" | "manual_prototype";
  source_url: string | null;
  raw_text: string;
  attributes: ProductAttribute[];  // always all taxonomy attributes
  version: number;
  parent_version?: number | null;
  change_note?: string | null;
  ref?: ProductRef;
  category?: string | null;        // drives which taxonomy applies; auto-detected if omitted
}

export interface CreateProductRequest {
  source: "url" | "manual_prototype";
  source_url?: string;             // url mode
  brand?: string;                  // manual mode (required)
  display_name?: string;
  raw_text?: string;               // manual mode (required)
  category?: string;               // omit => auto-detected from page text
}

// GET /personas?category=  → default persona profiles for that category
export interface PersonasResponse {
  category: string | null;
  source_file: string;             // personas/default.json | personas/generic.json | personas/{slug}.json
  profiles: PersonaProfile[];
}

export interface CreateVersionRequest {
  base_version: number;
  additions: string[];             // paragraphs appended to the page (re-extracts attributes)
  change_note: string;
}

// ---------- taxonomy ----------
export interface TaxonomyAttribute { id: AttributeId; label: string; description: string; keywords: string[] }
export interface TaxonomyCluster { id: string; label: string; description: string; attributes: AttributeId[] }
export interface Taxonomy { version: number; category: string; attributes: TaxonomyAttribute[]; clusters: TaxonomyCluster[] }

// ---------- simulate (P2 / Frontend A) ----------
export interface Intent {
  intent_id?: string | null;
  text: string;
  cluster_id: string;              // taxonomy cluster id, or "other"
  attributes: AttributeId[];
  persona?: string | null;                    // legacy display value
  persona_id?: string | null;
  persona_profile?: PersonaProfile | null;
  language?: string;
}

export interface SimulateRequest {
  intent: Intent;
  candidates: ProductRef[];        // 2–4
  stream?: boolean;                // default true (SSE)
  cached?: boolean;                // true = deterministic instant replay (demo insurance)
  mode?: "mock" | "live";          // omit = auto
}

export interface Reason { text: string; attribute: AttributeId }

export interface ProductVerdict {
  product_ref: ProductRef;
  considered: boolean;
  verdict: "recommended" | "rejected" | "not_considered";
  rank: number | null;
  reasons_for: Reason[];
  reasons_against: Reason[];       // loser tags for the UI
}

export interface DecisionResult {
  decision_id: string;
  intent: Intent;
  candidates: ProductRef[];
  winner: ProductRef | null;
  per_product: ProductVerdict[];
  narrative: string;
  model: string;
  created_at: string;
}

// ---------- batch / compare ----------
export interface ShareStats {
  recommendation_share: number;
  consideration_share: number;
  ci95_recommendation: [number, number];
}

export interface BatchResult {
  batch_id: string;
  cluster_id: string;
  candidates: ProductRef[];
  runs: number;
  n_intents: number;
  shares: Record<ProductRef, ShareStats>;
  decision_ids: string[];
  status: "running" | "completed" | "failed";
  n_decisions?: number;
  error?: string | null;
  created_at: string;
}

export interface CompareSide {
  product_ref: ProductRef;
  recommendation_share: number;
  consideration_share: number;
  ci95_recommendation: [number, number];
}

export interface CompareResult {              // GET /metrics/compare (200)
  cluster_id: string;
  n_per_side: number;
  a: CompareSide;
  b: CompareSide;
  delta_recommendation: number;               // the big before/after number
  changes_applied: string[];                  // "we only added these paragraphs"
  diff_url: string | null;
}
export interface ComparePending { status: "pending"; missing: ProductRef[]; cluster_id: string; hint: string } // 202

// ---------- diagnosis (P3 / Frontend B) ----------
export interface DefectEvidence {
  cluster_id: string;
  losing_share_in_cluster: number;            // 0..1
  n_losses: number;
  sample_rejection_reasons: string[];         // verbatim AI quotes
  competitor_contrast: string;
}

export interface Defect {
  defect_id: string;
  type: "missing_attribute" | "weak_evidence" | "losing_cluster" | "positioning";
  attribute_id: AttributeId;
  severity: Severity;
  headline: string;
  evidence: DefectEvidence;
  suggested_fix: string;
  gap?: GapClass;                             // 🟢 information_gap vs 🔴 product_gap
  content_patch?: string;                     // ready-to-paste copy → copyable code block
  why_it_happens?: string;
  enriched?: boolean;                         // false while the tailored copy is still pending
}

export interface Diagnosis {                  // GET /products/{ref}/diagnosis (200)
  product_ref: ProductRef;
  generated_at: string;
  overall: {
    recommendation_share: number;
    consideration_share: number;
    retrieved_rate?: number;
    n_simulations: number;
    vs: Record<ProductRef | BrandSlug, number>;   // competitor shares for the bars
  };
  defects: Defect[];
  winning_clusters: { cluster_id: string; recommendation_share: number }[];
  source?: { type: "run" | "batches"; run_id?: string; engines?: string[] };
  funnel_dropoff?: Record<string, number>;
  exec_summary?: string;
  partial?: boolean;                          // deadline mode: diagnosis is usable but still enriching
}
export interface DiagnosisPending {
  status: "running" | "failed" | "needs_competitors";
  detail?: string;
  category?: string | null;
  clusters?: string[];
  progress?: {
    decisions_done: number;
    decisions_expected: number | null;
    batches: {
      cluster_id: string;
      batch_id: string;
      status: string;
      decisions_done: number;
      decisions_expected: number | null;
    }[];
  };
} // 202

// ---------- debate (P3 / Frontend B) ----------
export interface CreateDebateRequest { product_ref: ProductRef; focus_defect_id?: string }
export interface DebateSessionCreated { session_id: string; product_ref: ProductRef; diagnosis_ready?: boolean }

export interface ActionOffer {
  type: "create_version_and_rerun";
  status?: "started" | "failed";
  params: { additions: string[]; cluster_id: string };
  base_ref?: ProductRef;
  new_ref?: ProductRef;
  batch_a?: string;
  batch_b?: string;
  cluster_id?: string;
  compare_url?: string;                       // poll this until 200 → CompareResult
  error?: string;
}

export interface DebateMessage {
  role: "user" | "assistant";
  text: string;
  ts: string;
  action_offer?: ActionOffer | null;
}

export interface DebateSession { session_id: string; product_ref: ProductRef; messages: DebateMessage[] }

// ---------- pipeline runs (optional dashboard) ----------
export interface RunCreateRequest {
  brand: string;
  competitors?: string[];
  brand_products?: string[];
  category?: string;
  market?: string;
  language?: string;
  personas?: Array<PersonaProfile | string>;  // strings remain accepted for v3 compatibility
  n_intents?: number;                          // 10..300, default 60
  engines?: ("sim-sonnet" | "sim-haiku" | "mock")[];
  mode?: "mock" | "live" | "auto";
  judge_model?: string;
  product_refs?: ProductRef[];                 // pin @v1 for baseline runs!
}

export interface FunnelStats {
  n: number; retrieved: number; mentioned: number; considered: number; recommended: number;
  retrieved_rate: number; mention_rate: number; consideration_share: number; recommendation_share: number;
}

export interface FunnelSummary {
  run_id: string;
  n_annotated: number;
  engines: string[];
  clusters: string[];
  per_product: Record<BrandSlug, {
    display: string; is_target: boolean;
    overall: FunnelStats;
    by_engine: Record<string, FunnelStats>;
    by_cluster: Record<string, FunnelStats>;
    loss_attributes: Record<string, number>;
  }>;
  funnel_dropoff: Record<BrandSlug, {
    not_retrieved: number; retrieved_not_mentioned: number; mentioned_not_considered: number;
    considered_not_recommended: number; recommended: number;
  }>;
  other_recommended: Record<string, number>;
}

export interface RunStatus {
  run_id: string;
  config: RunCreateRequest & { run_id: string; engines: string[]; mode: string; product_refs: ProductRef[] };
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  stage: "intents" | "execute" | "funnel" | "attribution" | "report" | "done";
  progress: Record<string, { done: number; total: number }>;
  funnel_summary?: FunnelSummary | null;
  report?: Report | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Report {
  run_id: string; brand: string; target_slug: BrandSlug; category?: string;
  generated_at: string; n_responses: number; engines: string[];
  exec_summary: string; quick_wins: string[]; defects: Defect[];
  funnel: FunnelSummary["per_product"]; funnel_dropoff: FunnelSummary["funnel_dropoff"];
  evidence_audit: unknown; markdown: string;
}

// ---------- SSE ----------
export type SseEventName = "token" | "action" | "progress" | "error" | "done";
export interface SseToken { text: string }
export interface SseAction { action: ActionOffer }
export interface SseProgress { run_id: string; stage: string; done: number; total: number; message: string; pct: number }
export interface SseErrorEv { message: string; run_id?: string }
export interface SseDoneSimulate { decision: DecisionResult }     // POST /simulate
export interface SseDoneDebate { session_id: string }             // POST /debate/.../messages
export interface SseDoneRun { run_id: string; pct: 100; message: string } // GET /runs/{id}/events
