"""Destination scoring: filter by capability, then rank by minutes to definitive care.

Pure functions on plain dataclasses, so the logic can be tested without a database
or a routing engine. Drive times are computed elsewhere (app/routing.py) and passed in.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

from .rules import ROMAN, STROKE_NAMES, Rules


@dataclass
class HospitalSnapshot:
    id: str
    name: str
    short_name: str
    lat: float
    lon: float
    trauma_level: int | None
    stroke_level: str | None
    pci_24_7: bool
    burn_center: bool
    labor_delivery: bool
    pediatric_ed: bool
    pediatric_only: bool
    offload_baseline_min: float
    historical_offload_min: float
    feed: Literal["live", "none"]
    ed_occupancy_pct: float | None
    boarding: int | None
    on_diversion: bool
    status_updated_at: datetime | None
    units_en_route: int = 0

    def attrs(self) -> dict[str, Any]:
        """Fields the rules can reference, plus display helpers used in `qualifies` templates."""
        d = asdict(self)
        d["trauma_level_roman"] = ROMAN.get(self.trauma_level or 0, "")
        d["stroke_name"] = STROKE_NAMES.get(self.stroke_level or "", "")
        return d


@dataclass
class Offload:
    minutes: int
    source: Literal["live_feed", "historical_median"]
    data_age_min: float | None
    stale: bool


@dataclass
class Recommendation:
    rank: int
    hospital: HospitalSnapshot
    minutes_total: int
    minutes_drive: int
    minutes_offload: int
    rule_id: str
    capability: str
    offload: Offload
    diversion_exempt: bool


@dataclass
class Excluded:
    hospital: HospitalSnapshot
    rule_id: str
    reason: str


@dataclass
class ScoreResult:
    rules_version: str
    category_id: str
    rule_id: str
    need: str
    recommendations: list[Recommendation]
    ranked_lower: list[Recommendation]
    excluded: list[Excluded]


def predict_offload(h: HospitalSnapshot, rules: Rules, now: datetime) -> Offload:
    age = None
    if h.status_updated_at is not None:
        age = (now - h.status_updated_at).total_seconds() / 60
    live = h.feed == "live" and age is not None and age <= rules.stale_after_minutes
    en_route = rules.per_unit_en_route_min * h.units_en_route
    if live:
        raw = (
            h.offload_baseline_min
            + rules.per_occupancy_point_min * max(0.0, (h.ed_occupancy_pct or 0) - rules.occupancy_threshold_pct)
            + rules.per_boarded_patient_min * (h.boarding or 0)
            + en_route
        )
        return Offload(round(raw), "live_feed", round(age, 1), stale=False)
    stale = h.feed == "live"  # had a feed, but it is too old
    return Offload(
        round(h.historical_offload_min + en_route),
        "historical_median",
        round(age, 1) if (stale and age is not None) else None,
        stale=stale,
    )


def score(
    hospitals: list[HospitalSnapshot],
    category_id: str,
    drive_minutes: dict[str, float],
    rules: Rules,
    now: datetime,
) -> ScoreResult:
    cat = rules.categories[category_id]
    exempt = category_id in rules.diversion_exempt
    eligible: list[Recommendation] = []
    excluded: list[Excluded] = []

    for h in hospitals:
        ok, reason = cat.evaluate(h.attrs())
        if not ok:
            excluded.append(Excluded(h, cat.rule_id, reason))
            continue
        if h.on_diversion and not exempt:
            excluded.append(Excluded(h, rules.diversion_rule_id, "On ED diversion"))
            continue
        if h.id not in drive_minutes:
            excluded.append(Excluded(h, "ROUTE", "No route found"))
            continue
        drive = max(1, round(drive_minutes[h.id]))
        off = predict_offload(h, rules, now)
        eligible.append(
            Recommendation(
                rank=0,
                hospital=h,
                minutes_total=drive + off.minutes,
                minutes_drive=drive,
                minutes_offload=off.minutes,
                rule_id=cat.rule_id,
                capability=reason,
                offload=off,
                diversion_exempt=h.on_diversion and exempt,
            )
        )

    eligible.sort(key=lambda r: (r.minutes_total, r.minutes_drive, r.hospital.id))
    for i, r in enumerate(eligible, start=1):
        r.rank = i
    n = rules.max_recommendations
    return ScoreResult(
        rules_version=rules.version,
        category_id=category_id,
        rule_id=cat.rule_id,
        need=cat.need,
        recommendations=eligible[:n],
        ranked_lower=eligible[n:],
        excluded=excluded,
    )
