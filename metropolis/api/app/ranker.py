"""Multi-patient ranker (rule R-1): suggest destination swaps between units in transport.

Pure functions on plain dataclasses, like scoring.py. The shell (main.py, repository.py,
routing.py) loads open dispatches and drive times from each unit's current position.

A swap trades destinations between two units: A->X, B->Y becomes A->Y, B->X. It is
suggested when it lowers severity-weighted minutes to care without making either
patient much later. Swaps never change how many units are headed to each hospital,
so each hospital's predicted offload is the same before and after; only drive differs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .rules import Rules
from .scoring import HospitalSnapshot, Offload, predict_offload

PositionSource = Literal["live", "incident_origin"]


@dataclass(frozen=True)
class Transport:
    """One patient in transport (an open dispatch)."""

    dispatch_id: int
    unit: str
    category: str
    acuity: int
    hospital_id: str  # current destination
    position_source: PositionSource
    last_swapped_at: datetime | None = None


@dataclass
class PatientSide:
    dispatch_id: int
    unit: str
    category: str
    acuity: int
    weight: float
    from_hospital: str
    to_hospital: str
    minutes_before: int
    minutes_after: int
    drive_before: int
    drive_after: int
    offload_after: Offload
    position_source: PositionSource


@dataclass
class SwapCandidate:
    a: PatientSide
    b: PatientSide
    weighted_gain: float
    pareto: bool
    rule_id: str


def _drive(drive: dict[int, dict[str, float]], t: Transport, hid: str) -> int | None:
    m = drive.get(t.dispatch_id, {}).get(hid)
    return None if m is None else max(1, round(m))  # same rounding as score()


def _can_receive(t: Transport, h: HospitalSnapshot, rules: Rules) -> bool:
    """Capability filter and diversion rule D-1, exactly as /score applies them."""
    cat = rules.categories.get(t.category)
    if cat is None:
        return False
    ok, _ = cat.evaluate(h.attrs())
    if not ok:
        return False
    return not (h.on_diversion and t.category not in rules.diversion_exempt)


def _in_cooldown(t: Transport, rules: Rules, now: datetime) -> bool:
    if t.last_swapped_at is None:
        return False
    return (now - t.last_swapped_at).total_seconds() / 60 < rules.ranker.swap_cooldown_min


def find_swaps(
    transports: list[Transport],
    hospitals: dict[str, HospitalSnapshot],
    drive: dict[int, dict[str, float]],
    rules: Rules,
    now: datetime,
    declined: frozenset[tuple[int, int, str, str]] | set[tuple[int, int, str, str]] = frozenset(),
) -> list[SwapCandidate]:
    """Return non-conflicting swap suggestions, best weighted gain first.

    `drive` maps dispatch_id -> hospital_id -> minutes from the unit's current position.
    `declined` holds (a_id, b_id, a_to, b_to) swaps a dispatcher already turned down;
    they are not suggested again.
    """
    rk = rules.ranker
    offload: dict[str, Offload] = {}

    def off(hid: str) -> Offload:
        if hid not in offload:
            offload[hid] = predict_offload(hospitals[hid], rules, now)
        return offload[hid]

    ordered = sorted(transports, key=lambda t: t.dispatch_id)
    candidates: list[SwapCandidate] = []
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            x, y = a.hospital_id, b.hospital_id
            if x == y or x not in hospitals or y not in hospitals:
                continue
            if (a.dispatch_id, b.dispatch_id, y, x) in declined:
                continue
            if _in_cooldown(a, rules, now) or _in_cooldown(b, rules, now):
                continue
            if not (_can_receive(a, hospitals[y], rules) and _can_receive(b, hospitals[x], rules)):
                continue
            ax, ay, bx, by = _drive(drive, a, x), _drive(drive, a, y), _drive(drive, b, x), _drive(drive, b, y)
            if None in (ax, ay, bx, by):
                continue
            if ax < rk.min_remaining_drive_min or by < rk.min_remaining_drive_min:
                continue

            t_ax, t_ay = ax + off(x).minutes, ay + off(y).minutes
            t_by, t_bx = by + off(y).minutes, bx + off(x).minutes
            wa, wb = rk.acuity_weights[a.acuity], rk.acuity_weights[b.acuity]
            gain = (wa * t_ax + wb * t_by) - (wa * t_ay + wb * t_bx)
            if gain < rk.min_weighted_gain_min or gain <= 0:
                continue
            if t_ay - t_ax > rk.max_individual_loss_min or t_bx - t_by > rk.max_individual_loss_min:
                continue

            pareto = t_ay <= t_ax and t_bx <= t_by and (t_ay < t_ax or t_bx < t_by)
            candidates.append(
                SwapCandidate(
                    a=PatientSide(a.dispatch_id, a.unit, a.category, a.acuity, wa, x, y, t_ax, t_ay, ax, ay, off(y), a.position_source),
                    b=PatientSide(b.dispatch_id, b.unit, b.category, b.acuity, wb, y, x, t_by, t_bx, by, bx, off(x), b.position_source),
                    weighted_gain=round(gain, 1),
                    pareto=pareto,
                    rule_id=rk.rule_id,
                )
            )

    # Greedy: highest gain first; a dispatch appears in at most one suggestion.
    candidates.sort(key=lambda c: (-c.weighted_gain, c.a.dispatch_id, c.b.dispatch_id))
    used: set[int] = set()
    chosen: list[SwapCandidate] = []
    for c in candidates:
        if c.a.dispatch_id in used or c.b.dispatch_id in used:
            continue
        used.update((c.a.dispatch_id, c.b.dispatch_id))
        chosen.append(c)
    return chosen
