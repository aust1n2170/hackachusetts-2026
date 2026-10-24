"""Baseline destination choices: what a dispatcher would pick today without Metropolis.

Pure functions, using the same drive times, rounding and offload model as score(), so a
baseline and a recommendation are directly comparable.

- nearest_capable: closest hospital that passes the category rule and diversion rule D-1.
  The fair baseline: a good dispatcher following protocol from memory.
- nearest_ed: closest open ED of any kind. The weak baseline: closest hospital, capability
  not checked. It may not be able to treat the patient.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .rules import Rules
from .scoring import HospitalSnapshot, predict_offload


@dataclass
class Baseline:
    hospital: HospitalSnapshot
    minutes_drive: int
    minutes_offload: int
    minutes_total: int
    capable: bool
    reason: str  # capability text if capable, otherwise the rule's fail reason


def capable(h: HospitalSnapshot, category: str, rules: Rules) -> tuple[bool, str]:
    return rules.categories[category].evaluate(h.attrs())


def open_to(h: HospitalSnapshot, category: str, rules: Rules) -> bool:
    """Would an ambulance with this category be taken there at all? Diversion (D-1) applies
    unless the category is exempt; pediatric-only EDs don't take adult categories."""
    if h.on_diversion and category not in rules.diversion_exempt:
        return False
    return not (rules.categories[category].adult_only and h.pediatric_only)


def _baseline(h: HospitalSnapshot, drive: float, category: str, rules: Rules, now: datetime) -> Baseline:
    d = max(1, round(drive))  # same rounding as score()
    off = predict_offload(h, rules, now).minutes
    ok, reason = capable(h, category, rules)
    return Baseline(h, d, off, d + off, ok, reason)


def _nearest(candidates: list[HospitalSnapshot], drive: dict[str, float]) -> HospitalSnapshot | None:
    routed = [h for h in candidates if h.id in drive]
    if not routed:
        return None
    return min(routed, key=lambda h: (max(1, round(drive[h.id])), h.id))


def nearest_capable(
    hospitals: list[HospitalSnapshot], category: str, drive: dict[str, float], rules: Rules, now: datetime
) -> Baseline | None:
    h = _nearest([h for h in hospitals if open_to(h, category, rules) and capable(h, category, rules)[0]], drive)
    return None if h is None else _baseline(h, drive[h.id], category, rules, now)


def nearest_ed(
    hospitals: list[HospitalSnapshot], category: str, drive: dict[str, float], rules: Rules, now: datetime
) -> Baseline | None:
    h = _nearest([h for h in hospitals if open_to(h, category, rules)], drive)
    return None if h is None else _baseline(h, drive[h.id], category, rules, now)
