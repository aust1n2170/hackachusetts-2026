"""Load and evaluate the regional protocol rules (rules/region-rules.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}
STROKE_NAMES = {
    "CSC": "Comprehensive stroke",
    "TSC": "Thrombectomy-capable stroke",
    "PSC": "Primary stroke",
    "ASR": "Acute stroke ready",
}
OPS = {"eq", "in", "true", "lte", "gte"}


@dataclass(frozen=True)
class Requirement:
    field: str
    op: str
    value: Any = None

    def check(self, attrs: dict[str, Any]) -> bool:
        actual = attrs.get(self.field)
        if self.op == "true":
            return bool(actual)
        if actual is None:
            return False
        if self.op == "eq":
            return actual == self.value
        if self.op == "in":
            return actual in self.value
        if self.op == "lte":
            return actual <= self.value
        if self.op == "gte":
            return actual >= self.value
        raise ValueError(f"Unknown op {self.op!r}")


@dataclass(frozen=True)
class CategoryRule:
    id: str
    rule_id: str
    label: str
    need: str
    adult_only: bool
    requires: tuple[Requirement, ...]
    qualifies: str
    fail_reason: str

    def evaluate(self, attrs: dict[str, Any]) -> tuple[bool, str]:
        """Return (qualifies, human-readable reason)."""
        if self.adult_only and attrs.get("pediatric_only"):
            return False, "Pediatric-only facility"
        if all(r.check(attrs) for r in self.requires):
            return True, self.qualifies.format(**attrs)
        return False, self.fail_reason


@dataclass(frozen=True)
class TrafficProfile:
    fallback_speed_kmh: float
    osrm_multiplier: float


@dataclass(frozen=True)
class RankerRules:
    rule_id: str
    acuity_weights: dict[int, float]
    default_acuity: dict[str, int]
    min_weighted_gain_min: float
    max_individual_loss_min: float
    min_remaining_drive_min: float
    swap_cooldown_min: float
    position_stale_after_min: float


ACUITY_LEVELS = (1, 2, 3, 4)


def _load_ranker(raw: dict[str, Any], categories: set[str]) -> RankerRules:
    weights = {int(k): float(v) for k, v in raw["acuity_weights"].items()}
    if set(weights) != set(ACUITY_LEVELS):
        raise ValueError(f"ranker.acuity_weights must cover exactly {list(ACUITY_LEVELS)}, got {sorted(weights)}")
    if any(w <= 0 for w in weights.values()):
        raise ValueError("ranker.acuity_weights must be positive")
    defaults = {str(k): int(v) for k, v in raw["default_acuity"].items()}
    missing = categories - set(defaults)
    if missing:
        raise ValueError(f"ranker.default_acuity is missing categories: {sorted(missing)}")
    unknown = set(defaults) - categories
    if unknown:
        raise ValueError(f"ranker.default_acuity has unknown categories: {sorted(unknown)}")
    bad = {k: v for k, v in defaults.items() if v not in ACUITY_LEVELS}
    if bad:
        raise ValueError(f"ranker.default_acuity values must be in {list(ACUITY_LEVELS)}: {bad}")
    thresholds = {
        k: float(raw[k])
        for k in (
            "min_weighted_gain_min",
            "max_individual_loss_min",
            "min_remaining_drive_min",
            "swap_cooldown_min",
            "position_stale_after_min",
        )
    }
    negative = [k for k, v in thresholds.items() if v < 0]
    if negative:
        raise ValueError(f"ranker thresholds must be >= 0: {negative}")
    return RankerRules(rule_id=raw["rule_id"], acuity_weights=weights, default_acuity=defaults, **thresholds)


@dataclass(frozen=True)
class Rules:
    version: str
    max_recommendations: int
    diversion_rule_id: str
    diversion_exempt: frozenset[str]
    occupancy_threshold_pct: float
    per_occupancy_point_min: float
    per_boarded_patient_min: float
    per_unit_en_route_min: float
    stale_after_minutes: float
    en_route_window_minutes: float
    road_factor: float
    handling_minutes: float
    traffic: dict[str, TrafficProfile]
    ranker: RankerRules
    categories: dict[str, CategoryRule] = field(default_factory=dict)


def load_rules(path: str | Path) -> Rules:
    raw = yaml.safe_load(Path(path).read_text())
    cats: dict[str, CategoryRule] = {}
    for cid, c in raw["categories"].items():
        reqs = []
        for r in c.get("requires", []):
            if r["op"] not in OPS:
                raise ValueError(f"{cid}: unknown op {r['op']!r}")
            reqs.append(Requirement(field=r["field"], op=r["op"], value=r.get("value")))
        cats[cid] = CategoryRule(
            id=cid,
            rule_id=c["rule_id"],
            label=c["label"],
            need=c["need"],
            adult_only=bool(c.get("adult_only", True)),
            requires=tuple(reqs),
            qualifies=c["qualifies"],
            fail_reason=c["fail_reason"],
        )
    off, rt, dv = raw["offload"], raw["routing"], raw["diversion"]
    unknown_exempt = set(dv["exempt_categories"]) - set(cats)
    if unknown_exempt:
        raise ValueError(f"diversion.exempt_categories has unknown ids: {sorted(unknown_exempt)}")
    return Rules(
        version=str(raw["version"]),
        max_recommendations=int(raw.get("max_recommendations", 3)),
        diversion_rule_id=dv["rule_id"],
        diversion_exempt=frozenset(dv["exempt_categories"]),
        occupancy_threshold_pct=off["occupancy_threshold_pct"],
        per_occupancy_point_min=off["per_occupancy_point_min"],
        per_boarded_patient_min=off["per_boarded_patient_min"],
        per_unit_en_route_min=off["per_unit_en_route_min"],
        stale_after_minutes=off["stale_after_minutes"],
        en_route_window_minutes=off["en_route_window_minutes"],
        road_factor=rt["road_factor"],
        handling_minutes=rt["handling_minutes"],
        traffic={k: TrafficProfile(**v) for k, v in rt["traffic"].items()},
        ranker=_load_ranker(raw["ranker"], set(cats)),
        categories=cats,
    )
