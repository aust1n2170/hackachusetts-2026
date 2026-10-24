from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Traffic = Literal["light", "normal", "rush"]


class ScoreRequest(BaseModel):
    category: str = Field(examples=["trauma"])
    lat: float = Field(ge=-90, le=90, examples=[42.3489])
    lon: float = Field(ge=-180, le=180, examples=[-71.0952])
    traffic: Traffic = "normal"


class OffloadOut(BaseModel):
    minutes: int
    source: Literal["live_feed", "historical_median"]
    data_age_min: float | None
    stale: bool


class RecommendationOut(BaseModel):
    rank: int
    hospital_id: str
    name: str
    short_name: str
    lat: float
    lon: float
    minutes_total: int
    minutes_drive: int
    minutes_offload: int
    rule_id: str
    capability: str
    offload: OffloadOut
    units_en_route: int
    on_diversion: bool
    diversion_exempt: bool


class ExcludedOut(BaseModel):
    hospital_id: str
    short_name: str
    rule_id: str
    reason: str


class BaselineOut(BaseModel):
    hospital_id: str
    short_name: str
    minutes_drive: int
    minutes_offload: int
    minutes_total: int
    capable: bool
    reason: str
    same_as_top: bool


class ComparisonOut(BaseModel):
    """What a dispatcher would pick today, scored with the same model."""

    nearest_capable: BaselineOut | None
    nearest_ed: BaselineOut | None


class ScoreResponse(BaseModel):
    audit_id: int
    rules_version: str
    category: str
    rule_id: str
    need: str
    routing_source: str
    recommendations: list[RecommendationOut]
    ranked_lower: list[RecommendationOut]
    excluded: list[ExcludedOut]
    comparison: ComparisonOut


class CategoryOut(BaseModel):
    id: str
    rule_id: str
    label: str
    need: str
    default_acuity: int


class RulesOut(BaseModel):
    version: str
    categories: list[CategoryOut]


class HospitalOut(BaseModel):
    id: str
    name: str
    short_name: str
    lat: float
    lon: float
    capabilities: list[str]
    feed: Literal["live", "none"]
    ed_occupancy_pct: float | None
    boarding: int | None
    on_diversion: bool
    status_updated_at: datetime | None
    units_en_route: int
    predicted_offload: OffloadOut


class StatusUpdate(BaseModel):
    feed: Literal["live", "none"] | None = None
    ed_occupancy_pct: float | None = Field(default=None, ge=0, le=300)
    boarding: int | None = Field(default=None, ge=0, le=200)
    on_diversion: bool | None = None


class DispatchIn(BaseModel):
    unit: str = Field(min_length=1, max_length=40, examples=["Medic 12"])
    hospital_id: str
    category: str
    recommended_rank: int | None = Field(default=None, ge=1)
    score_audit_id: int | None = None
    acuity: int | None = Field(default=None, ge=1, le=4, description="1 critical .. 4 non-urgent; defaults by category")
    origin_lat: float | None = Field(default=None, ge=-90, le=90)
    origin_lon: float | None = Field(default=None, ge=-180, le=180)


class DispatchOut(BaseModel):
    id: int
    unit: str
    hospital_id: str
    category: str
    recommended_rank: int | None
    dispatched_at: datetime
    arrived_at: datetime | None
    acuity: int
    position_lat: float | None
    position_lon: float | None
    position_updated_at: datetime | None
    last_swapped_at: datetime | None


class PositionUpdate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RankerEvaluateIn(BaseModel):
    traffic: Traffic = "normal"


class SwapSideOut(BaseModel):
    dispatch_id: int
    unit: str
    category: str
    acuity: int
    from_hospital: str
    to_hospital: str
    minutes_before: int
    minutes_after: int
    drive_before: int
    drive_after: int
    offload: OffloadOut
    position_source: Literal["live", "incident_origin"]


class SwapSuggestionOut(BaseModel):
    id: int
    status: Literal["pending", "accepted", "declined", "expired"]
    rules_version: str
    rule_id: str
    a: SwapSideOut
    b: SwapSideOut
    weighted_gain: float
    pareto: bool
    routing_source: str
    created_at: datetime
    decided_at: datetime | None


# ---------------------------------------------------------------- simulation (before vs. after)

ScenarioId = Field(pattern=r"^[a-z0-9_-]{1,40}$", examples=["surge"])


class ScenarioOut(BaseModel):
    id: str
    title: str
    description: str
    calls: int


class SimulateIn(BaseModel):
    scenario: str = ScenarioId


class SimHospitalOut(BaseModel):
    id: str
    short_name: str
    lat: float
    lon: float


class SimCallOut(BaseModel):
    id: int
    t_min: float
    category: str
    acuity: int
    lat: float
    lon: float
    label: str


class SimEventOut(BaseModel):
    t_min: float
    hospital_id: str
    label: str


class SimMetricsOut(BaseModel):
    assigned: int
    unassigned: int
    mean: float
    median: float
    acuity1_mean: float | None
    total: int
    not_capable: int
    peak_en_route: int
    peak_hospital: str | None
    swaps: int


class SimAssignmentOut(BaseModel):
    call_id: int
    hospital_id: str
    first_hospital_id: str
    t_dispatch: float
    arrive_at: float
    drive: int
    offload_at_arrival: int
    minutes_to_care: int
    capable: bool
    swapped: bool


class SimStrategyOut(BaseModel):
    id: str
    label: str
    metrics: SimMetricsOut
    assignments: list[SimAssignmentOut]
    timeline: list[dict[str, int]]


class SimulationOut(BaseModel):
    scenario: str
    title: str
    description: str
    rules_version: str
    traffic: str
    secondary_transfer_min: float
    queue_window_min: float
    hospitals: list[SimHospitalOut]
    calls: list[SimCallOut]
    events: list[SimEventOut]
    strategies: list[SimStrategyOut]


# ---------------------------------------------------------------- demo mode


class DemoLoadIn(BaseModel):
    scenario: str = ScenarioId


class DemoOut(BaseModel):
    enabled: bool
    frozen: bool
    scenario: str | None
