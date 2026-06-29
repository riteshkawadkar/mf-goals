"""Pydantic v2 schemas matching the frozen OpenAPI contract exactly."""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional, Any
from pydantic import BaseModel, Field


# ---------- Error ----------

class Error(BaseModel):
    code: str
    message: str


# ---------- TaxLot ----------

class TaxLot(BaseModel):
    id: str
    holding_id: str
    units: float
    nav_at_buy: float
    cost_basis: float
    buy_date: date
    lock_until: Optional[date] = None
    gain_type: str  # stcg | ltcg | locked


# ---------- Holding ----------

class Holding(BaseModel):
    id: str
    scheme_code: str
    scheme_name: str
    amc: str
    category: str
    asset_class: str  # liquid | debt | hybrid | equity
    equity_fraction: float
    style_cluster_id: str
    sector_tags: list[str] = Field(default_factory=list)
    current_units: float
    current_nav: float
    current_value: float
    tax_lots: list[TaxLot] = Field(default_factory=list)


# ---------- DetectedSip ----------

class DetectedSip(BaseModel):
    scheme_code: str
    scheme_name: str
    suggested_amount: float
    cadence: str  # monthly | quarterly
    last_installment_date: date
    detection_confidence: str  # high | medium | low


# ---------- ActiveSip ----------

class ActiveSipInput(BaseModel):
    scheme_code: str
    amount: float
    cadence: str  # monthly | quarterly
    run_until: Optional[date] = None


class ActiveSip(ActiveSipInput):
    id: str
    source: str  # detected | confirmed


# ---------- Goal ----------

class GoalInput(BaseModel):
    name: str
    archetype: str  # emergency | near_term_fixed | education | retirement | perpetual_wealth | recurring_liability
    target_today: Optional[float] = None
    horizon_date: Optional[date] = None
    priority: int = 99
    inflation_rate: Optional[float] = None


class Goal(BaseModel):
    id: str
    name: str
    archetype: str
    target_today: Optional[float] = None
    horizon_date: Optional[date] = None
    priority: int
    inflation_rate: float
    target_future_value: Optional[float] = None
    confidence_tag: str  # high | medium | low
    equity_band_low: float
    equity_band_high: float
    glide_start_date: Optional[date] = None
    is_perpetual: bool


# ---------- Assumptions ----------

class AssumptionInput(BaseModel):
    key: str
    value: float


class Assumption(AssumptionInput):
    is_default: bool


# ---------- Earmark ----------

class Earmark(BaseModel):
    id: str
    holding_id: str
    goal_id: Optional[str] = None
    percentage: float
    amount: float
    locked_by_user: bool
    reasoning_object_id: str


# ---------- ReasoningObject ----------

class ReasoningObject(BaseModel):
    id: str
    type: str  # earmark | flag | sufficiency | path_safety | portfolio_health
    subject_ref: str
    rule_id: str
    inputs_used: dict[str, Any] = Field(default_factory=dict)
    assumptions_referenced: list[str] = Field(default_factory=list)
    plain_language: str


# ---------- Stress / Structural ----------

class StressResult(BaseModel):
    scenario: str  # moderate | severe | gfc_2008 | covid
    equity_shock: float
    resulting_value: float
    breaks_goal: bool


class StructuralFlag(BaseModel):
    type: str
    severity: str  # info | warn | critical
    plain_language: str
    reasoning_object_id: str


# ---------- Diagnosis ----------

class SufficiencyResult(BaseModel):
    p10: float
    p50: float
    p90: float
    target_future_value: float
    verdict: str  # on_track | behind | ahead | not_assured
    judged_against: str  # p10 | p50
    illustrative_note: str


class PathSafetyResult(BaseModel):
    fragility: str  # low | moderate | high
    scenarios: list[StressResult]


class Diagnosis(BaseModel):
    goal_id: str
    sufficiency: Optional[SufficiencyResult] = None
    path_safety: PathSafetyResult
    structural_flags: list[StructuralFlag] = Field(default_factory=list)


# ---------- Portfolio Health ----------

class AggregateMix(BaseModel):
    equity: float
    debt: float
    liquid: float


class Concentration(BaseModel):
    style: list[StructuralFlag] = Field(default_factory=list)
    amc: list[StructuralFlag] = Field(default_factory=list)
    sector: list[StructuralFlag] = Field(default_factory=list)


class PortfolioHealth(BaseModel):
    emergency_reserve_adequate: bool
    aggregate_mix: AggregateMix
    concentration: Concentration
    liquidity_posture: str
    summary_line: str
    flags: list[StructuralFlag] = Field(default_factory=list)


# ---------- GoalView ----------

class GoalView(BaseModel):
    goal: Goal
    earmarks: list[Earmark] = Field(default_factory=list)
    diagnosis: Diagnosis
    resulting_equity_fraction: float


# ---------- Unallocated ----------

class UnallocatedBucket(BaseModel):
    total_value: float
    earmarks: list[Earmark] = Field(default_factory=list)


# ---------- DataFreshness ----------

class DataFreshness(BaseModel):
    last_cas_upload: Optional[datetime] = None
    last_nav_date: date
    nudge_due: bool
    nudge_reason: Optional[str] = None


# ---------- DashboardState ----------

class DashboardState(BaseModel):
    computed_at: datetime
    portfolio_health: PortfolioHealth
    goals: list[GoalView] = Field(default_factory=list)
    unallocated: UnallocatedBucket
    reasoning_objects: list[ReasoningObject] = Field(default_factory=list)
    data_freshness: DataFreshness


# ---------- ChatResponse ----------

class ChatQuery(BaseModel):
    query: str
    subject_ref: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    in_scope: bool
    refused: bool
    referenced_reasoning_object_ids: list[str] = Field(default_factory=list)
    not_computed: bool


# ---------- CAS Upload Response ----------

class CasUploadResponse(BaseModel):
    holdings_imported: int
    tax_lots_imported: int
    holdings: list[Holding]
    detected_sips: list[DetectedSip]


# ---------- Auth (not in OpenAPI but needed) ----------

class OtpRequest(BaseModel):
    email: str


class OtpVerify(BaseModel):
    email: str
    otp: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
