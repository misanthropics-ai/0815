"""Contract v3 — Pydantic models(唯一真相來源之一, 與 openapi.yaml 同步).

v2 的 Product / DecisionResult / Diagnosis / Debate / Compare 物件全部保留相容;
新增五階段 pipeline 物件 (Run / EngineResponse / Funnel / Evidence / Report)。
改動規則: 需全員同意, 改完同步 backend/mock_fixtures。
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

AttributeId = str          # 必須存在於 backend/taxonomy/taxonomy.json, 否則 "other"
ProductRef = str           # "{product_id}@v{n}", 例 "cabinzero-classic-36l@v1"
BrandSlug = str            # slugify(brand), 例 "cabinzero"; funnel 聚合以 brand 為 canonical 單位
SearchValue = Union[
    str,
    int,
    float,
    bool,
    None,
    list[Union[str, int, float, bool, None]],
    dict[str, Union[str, int, float, bool, None]],
]


# ---------- Cross-category shopper / search profile ----------
class SearchLocation(BaseModel):
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None


class SearchBudget(BaseModel):
    min_amount: Optional[float] = Field(default=None, ge=0)
    max_amount: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    flexibility: Literal["hard", "soft"] = "soft"

    @model_validator(mode="after")
    def validate_range(self) -> "SearchBudget":
        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            raise ValueError("budget min_amount cannot exceed max_amount")
        return self


class SearchCriterion(BaseModel):
    attribute: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    operator: Literal[
        "eq",
        "neq",
        "lte",
        "gte",
        "between",
        "in",
        "not_in",
        "contains",
        "not_contains",
        "supports",
        "exists",
        "maximize",
        "minimize",
    ]
    value: SearchValue = None
    unit: Optional[str] = None
    importance: Literal["must", "should", "nice_to_have"] = "should"
    reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_value(self) -> "SearchCriterion":
        value_optional = {"exists", "maximize", "minimize"}
        if self.operator not in value_optional and self.value is None:
            raise ValueError(f"criterion operator '{self.operator}' requires a value")
        if self.operator == "between":
            if not isinstance(self.value, dict) or not {"min", "max"} <= self.value.keys():
                raise ValueError("between criterion requires value.min and value.max")
            minimum = self.value["min"]
            maximum = self.value["max"]
            if (
                isinstance(minimum, (int, float))
                and not isinstance(minimum, bool)
                and isinstance(maximum, (int, float))
                and not isinstance(maximum, bool)
                and minimum > maximum
            ):
                raise ValueError("between criterion value.min cannot exceed value.max")
        return self


class ReferenceProduct(BaseModel):
    name: str = Field(min_length=1)
    relation: Literal[
        "owns",
        "likes",
        "dislikes",
        "compare_with",
        "alternative_to",
        "compatible_with",
    ] = "compare_with"
    notes: Optional[str] = None


class PersonaProfile(BaseModel):
    persona_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    label: str = Field(min_length=1)
    relationship_to_buyer: str = "self"
    age: Optional[int] = Field(default=None, ge=0, le=120)
    occupation: Optional[str] = None
    location: Optional[SearchLocation] = None
    budget: Optional[SearchBudget] = None
    use_cases: list[str] = Field(default_factory=list)
    criteria: list[SearchCriterion] = Field(default_factory=list)
    reference_products: list[ReferenceProduct] = Field(default_factory=list)
    context: dict[str, SearchValue] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


# ---------- Product (P1) ----------
class ProductAttribute(BaseModel):
    attribute_id: AttributeId
    value: Optional[str]            # None = 頁面上找不到 → 缺陷分析的原料
    evidence: Optional[str]         # 從 raw_text 引用的原句
    confidence: float = Field(ge=0, le=1)


class Product(BaseModel):
    product_id: str
    brand: str
    display_name: str
    source: Literal["url", "manual_prototype"]
    source_url: Optional[str]
    raw_text: str
    attributes: list[ProductAttribute]
    version: int = 1
    parent_version: Optional[int] = None
    change_note: Optional[str] = None
    ref: Optional[ProductRef] = None
    category: Optional[str] = None          # 決定 taxonomy; 未給時由抽取自動偵測


class CreateProductRequest(BaseModel):
    source: Literal["url", "manual_prototype"]
    source_url: Optional[str] = None        # source=url 時必填
    brand: Optional[str] = None             # manual 時必填
    display_name: Optional[str] = None
    raw_text: Optional[str] = None          # manual 時必填
    product_id: Optional[str] = None
    category: Optional[str] = None          # 省略 => 從頁面文字自動偵測


class CreateVersionRequest(BaseModel):
    base_version: int
    additions: list[str]                    # 追加進 raw_text 的段落(會重抽 attributes)
    change_note: str


# ---------- Pipeline: runs ----------
class RunCreateRequest(BaseModel):
    brand: str
    competitors: Optional[list[str]] = None   # 缺省: DB 中其他品牌
    brand_products: Optional[list[str]] = None
    category: Optional[str] = "travel backpack"
    market: Optional[str] = "US/EU"
    language: Optional[str] = "en"
    personas: Optional[list[Union[PersonaProfile, str]]] = None
    n_intents: int = Field(default=60, ge=10, le=300)
    engines: Optional[list[str]] = None       # 缺省: DEFAULT_ENGINES (sim-sonnet,sim-haiku) 或 mock
    mode: Optional[Literal["mock", "live", "auto"]] = None
    judge_model: Optional[str] = None         # smart | fast | 明確 bedrock model id
    product_refs: Optional[list[ProductRef]] = None


class RunStatus(BaseModel):
    run_id: str
    config: dict
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    stage: str                                # intents|execute|funnel|attribution|report|done
    progress: dict                            # {stage: {done, total}}
    funnel_summary: Optional[dict] = None     # FunnelSummary
    evidence: Optional[dict] = None           # EvidenceAudit
    report: Optional[dict] = None             # Report
    error: Optional[str] = None
    created_at: str
    updated_at: str


# ---------- Pipeline: stage 1/2 ----------
class Intent(BaseModel):
    intent_id: Optional[str] = None
    text: str
    cluster_id: str
    cluster_label: Optional[str] = None
    attributes: list[AttributeId] = []
    persona: Optional[str] = None             # legacy display string
    persona_id: Optional[str] = None
    persona_profile: Optional[PersonaProfile] = None
    language: str = "en"
    source: Optional[str] = None              # generated | library | template


class Citation(BaseModel):
    url: str
    title: Optional[str] = None
    doc_id: Optional[str] = None
    brands: Optional[list[BrandSlug]] = None  # 該來源實質涵蓋的品牌
    score: Optional[float] = None


class EngineResponse(BaseModel):
    response_id: str
    run_id: str
    intent_id: str
    engine: str                               # sim-sonnet | sim-haiku | mock | (未來: openai/pplx/gemini)
    model: Optional[str]
    status: Literal["ok", "error"]
    text: str
    citations: list[Citation]
    search_queries: list[str]
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    cluster_id: Optional[str] = None
    intent_text: Optional[str] = None


# ---------- Pipeline: stage 3 funnel ----------
class LossReasonItem(BaseModel):
    text: str                                  # AI 回答中的逐字理由
    attribute: Optional[AttributeId] = None    # stage 4 填入
    attribute_source: Optional[str] = None     # keyword | llm | fallback


class FunnelProduct(BaseModel):
    name: str
    canonical: str                             # roster 的 BrandSlug 或 "other"
    is_target: Optional[bool] = None
    retrieved: bool                            # 品牌證據出現在 citations/search trace
    retrieved_via: list[str] = []
    mentioned: bool
    considered: bool                           # 進入明確比較敘述
    recommended: bool                          # 最終推薦
    rank: Optional[int] = None
    reasons_for: list[str] = []                # 逐字引述
    reasons_against: list[str] = []
    loss_reasons: list[LossReasonItem] = []    # considered 且未 recommended 時


class FunnelAnnotation(BaseModel):
    response_id: str
    engine: str
    top_pick: Optional[str]                    # BrandSlug | "other" | null
    products: list[FunnelProduct]
    judge_model: str
    is_ground_truth: bool = False


class FunnelStats(BaseModel):
    n: int
    retrieved: int
    mentioned: int
    considered: int
    recommended: int
    retrieved_rate: float
    mention_rate: float
    consideration_share: float
    recommendation_share: float


# FunnelSummary (dict shape; 見 response.funnel.json):
# { run_id, n_annotated, engines[], clusters[],
#   per_product: {slug: {display, is_target, overall: FunnelStats,
#                        by_engine: {engine: FunnelStats}, by_cluster: {cluster: FunnelStats},
#                        loss_attributes: {attr: count}}},
#   funnel_dropoff: {slug: {not_retrieved, retrieved_not_mentioned,
#                           mentioned_not_considered, considered_not_recommended, recommended}},
#   other_recommended: {name: count} }


# ---------- Pipeline: stage 4/5 ----------
# EvidenceAudit (dict shape): { attributes: {attr: {brands: {slug: {score, page_score, ext_score,
#   raw_hits, snippets[], n_docs}}, target_page_value, target_page_null, loss_mentions,
#   evidence_gap, classification: information_gap|product_gap|mixed|unclear,
#   classification_source, rationale}}, corpus_hash, n_docs, target }

GapClass = Literal["information_gap", "product_gap", "mixed", "unclear"]


class ReportDefectEvidence(BaseModel):
    cluster_id: str
    losing_share_in_cluster: float
    n_losses: int
    sample_rejection_reasons: list[str]
    competitor_contrast: str


class ReportDefect(BaseModel):
    defect_id: str
    type: Literal["missing_attribute", "weak_evidence", "losing_cluster", "positioning"]
    attribute_id: AttributeId
    severity: Literal["high", "medium", "low"]
    gap: GapClass
    headline: str
    why_it_happens: str = ""
    suggested_fix: str
    content_patch: str = ""                    # 可直接貼上的頁面段落/FAQ/JSON-LD
    impact: float
    clusters: list[str] = []
    evidence: ReportDefectEvidence


class Report(BaseModel):
    run_id: str
    brand: str
    target_slug: BrandSlug
    category: Optional[str]
    generated_at: str
    n_responses: int
    engines: list[str]
    exec_summary: str
    quick_wins: list[str]
    defects: list[ReportDefect]
    funnel: dict
    funnel_dropoff: dict
    evidence_audit: dict
    markdown: str


# ---------- Decision (P2) ----------
class Reason(BaseModel):
    text: str
    attribute: AttributeId


class ProductVerdict(BaseModel):
    product_ref: ProductRef
    considered: bool
    verdict: Literal["recommended", "rejected", "not_considered"]
    rank: Optional[int]
    reasons_for: list[Reason]
    reasons_against: list[Reason]


class DecisionResult(BaseModel):
    decision_id: str
    intent: Intent
    candidates: list[ProductRef]
    winner: Optional[ProductRef]
    per_product: list[ProductVerdict]
    narrative: str
    model: str                                 # "decision-engine/prompt_v1@<bedrock model>"
    created_at: str


class SimulateRequest(BaseModel):
    intent: Intent
    candidates: list[ProductRef]
    stream: bool = True
    cached: bool = False                       # demo fallback 開關
    mode: Optional[str] = None


class ShareStats(BaseModel):
    consideration_share: float = Field(ge=0, le=1)
    recommendation_share: float = Field(ge=0, le=1)
    ci95_recommendation: tuple[float, float]


class BatchRequest(BaseModel):
    cluster_id: str
    candidates: list[ProductRef]
    runs: int = 3
    cached: bool = True
    max_intents: int = 12
    wait: bool = False
    mode: Optional[str] = None


class BatchResult(BaseModel):
    batch_id: str
    cluster_id: str
    candidates: list[ProductRef]
    runs: int
    n_intents: int
    shares: dict[ProductRef, ShareStats] = {}
    decision_ids: list[str] = []
    status: Literal["running", "completed", "failed"]
    n_decisions: Optional[int] = None
    error: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


# ---------- Diagnosis (P3) ----------
class DefectEvidence(BaseModel):
    cluster_id: str
    losing_share_in_cluster: float = Field(ge=0, le=1)
    n_losses: int
    sample_rejection_reasons: list[str]
    competitor_contrast: str


class Defect(BaseModel):
    defect_id: str
    type: Literal["missing_attribute", "weak_evidence", "losing_cluster", "positioning"]
    attribute_id: AttributeId
    severity: Literal["high", "medium", "low"]
    headline: str
    evidence: DefectEvidence
    suggested_fix: str
    # v3 附加欄位 (additive)
    gap: Optional[GapClass] = None
    content_patch: Optional[str] = None
    why_it_happens: Optional[str] = None


class ClusterShare(BaseModel):
    cluster_id: str
    recommendation_share: float = Field(ge=0, le=1)


class Diagnosis(BaseModel):
    product_ref: ProductRef
    generated_at: str
    overall: dict          # {recommendation_share, consideration_share, retrieved_rate?, n_simulations, vs}
    defects: list[Defect]
    winning_clusters: list[ClusterShare]
    source: Optional[dict] = None              # {type: run|batches, ...}
    funnel_dropoff: Optional[dict] = None
    exec_summary: Optional[str] = None


# ---------- Debate (P3) ----------
class CreateDebateRequest(BaseModel):
    product_ref: ProductRef
    focus_defect_id: Optional[str] = None


class ActionOffer(BaseModel):
    type: Literal["create_version_and_rerun"]
    status: Optional[str] = None               # started | failed
    params: dict = {}
    new_ref: Optional[ProductRef] = None
    base_ref: Optional[ProductRef] = None
    batch_a: Optional[str] = None
    batch_b: Optional[str] = None
    cluster_id: Optional[str] = None
    compare_url: Optional[str] = None
    error: Optional[str] = None


class DebateMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    ts: str
    action_offer: Optional[ActionOffer] = None


class DebateSession(BaseModel):
    session_id: str
    product_ref: ProductRef
    messages: list[DebateMessage]


# ---------- Compare (P6 / P5) ----------
class CompareSide(BaseModel):
    product_ref: ProductRef
    recommendation_share: float
    consideration_share: float
    ci95_recommendation: tuple[float, float]


class CompareResult(BaseModel):
    cluster_id: str
    n_per_side: int
    a: CompareSide
    b: CompareSide
    delta_recommendation: float
    changes_applied: list[str]
    diff_url: Optional[str] = None


# ---------- Error ----------
class ErrorBody(BaseModel):
    code: str
    message: str
    hint: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorBody
