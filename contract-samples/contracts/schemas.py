"""
Contract v2 — Pydantic models. 唯一真相來源之一 (與 openapi.yaml 同步)。
後端: FastAPI 直接用這些 model 當 request/response type。
前端: 對照欄位名; P6 的 check_contract.py 用它驗 mock 與真實 response。
改動規則: 需全員同意, 改完同步 mock_fixtures。
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field

AttributeId = str          # 必須存在於 taxonomy.json, 否則用 "other"
ProductRef = str           # 格式: "{product_id}@v{n}", 例 "cabinzero-classic-36l@v1"

# ---------- Product (P1) ----------
class ProductAttribute(BaseModel):
    attribute_id: AttributeId
    value: Optional[str]            # None = 頁面上找不到 → 缺陷分析的原料
    evidence: Optional[str]         # 從 raw_text 引用的原句
    confidence: float = Field(ge=0, le=1)

class Product(BaseModel):
    product_id: str                 # 小寫 slug: "{brand}-{model}"
    brand: str
    display_name: str
    source: Literal["url", "manual_prototype"]
    source_url: Optional[str]
    raw_text: str
    attributes: list[ProductAttribute]
    version: int = 1
    parent_version: Optional[int] = None
    change_note: Optional[str] = None

class CreateProductRequest(BaseModel):
    source: Literal["url", "manual_prototype"]
    source_url: Optional[str] = None        # source=url 時必填
    brand: Optional[str] = None             # manual 時必填
    display_name: Optional[str] = None
    raw_text: Optional[str] = None          # manual 時必填

class CreateVersionRequest(BaseModel):
    base_version: int
    additions: list[str]                    # 要加進 raw_text 的段落
    change_note: str

# ---------- Decision (P2) ----------
class Intent(BaseModel):
    intent_id: Optional[str] = None
    text: str
    cluster_id: str
    attributes: list[AttributeId]
    language: str = "en"

class Reason(BaseModel):
    text: str
    attribute: AttributeId              # 對不上 taxonomy 填 "other"

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
    winner: Optional[ProductRef]        # None = 模型拒絕選 (應極少)
    per_product: list[ProductVerdict]
    narrative: str
    model: str                          # prompt 版本, 例 "decision-engine/prompt_v1"
    created_at: str

class SimulateRequest(BaseModel):
    intent: Intent
    candidates: list[ProductRef]
    stream: bool = True
    cached: bool = False                # demo fallback 開關

class ShareStats(BaseModel):
    consideration_share: float = Field(ge=0, le=1)
    recommendation_share: float = Field(ge=0, le=1)
    ci95_recommendation: tuple[float, float]

class BatchRequest(BaseModel):
    cluster_id: str
    candidates: list[ProductRef]
    runs: int = 3
    cached: bool = True

class BatchResult(BaseModel):
    batch_id: str
    cluster_id: str
    candidates: list[ProductRef]
    n_intents: int
    runs: int
    n_decisions: int
    shares: dict[ProductRef, ShareStats]
    decision_ids: list[str]
    status: Literal["running", "completed", "failed"]
    created_at: str

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

class ClusterShare(BaseModel):
    cluster_id: str
    recommendation_share: float = Field(ge=0, le=1)

class Diagnosis(BaseModel):
    product_ref: ProductRef
    generated_at: str
    overall: dict                       # 見 mock: recommendation_share / consideration_share / n_simulations / vs
    defects: list[Defect]
    winning_clusters: list[ClusterShare]

# ---------- Debate (P3) ----------
class CreateDebateRequest(BaseModel):
    product_ref: ProductRef
    focus_defect_id: Optional[str] = None

class ActionOffer(BaseModel):
    type: Literal["create_version_and_rerun"]
    params: dict

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
