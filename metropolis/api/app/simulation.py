"""Surge replay: run a scripted burst of calls through today's strategies and through Metropolis.

Pure logic (no database or network), like scoring.py and ranker.py.

Decisions: each strategy picks a destination when the call comes in, from what it can see then
(Metropolis uses score() with units en route, exactly like the live console).

Outcomes are measured separately, at arrival, the same way for every strategy:

    minutes to care = time on the road (dispatch -> arrival)
                    + ED offload at arrival: the hospital's state at that moment (scripted
                      events applied) + per_unit_en_route_min for each ambulance that reached
                      the same ED within queue_window_min before this one
                    + secondary_transfer_min if the hospital can't treat the patient

So a strategy that piles ambulances onto one ED pays for the queue, and an ED that fills up
after dispatch hurts the patients already headed there. Metropolis is not graded on its own
prediction. Drive times use the straight-line estimate so results don't depend on OSRM.
All figures are modeled, not observed.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from .compare import capable, nearest_capable, nearest_ed
from .ranker import Transport, find_swaps
from .routing import fallback_minutes
from .rules import Rules
from .scoring import HospitalSnapshot, predict_offload, score

Strategy = Literal["nearest_ed", "nearest_capable", "metropolis", "metropolis_swaps"]
STRATEGIES: dict[str, str] = {
    "nearest_ed": "Nearest ED",
    "nearest_capable": "Nearest capable (today)",
    "metropolis": "Metropolis",
    "metropolis_swaps": "Metropolis + swaps",
}
STATUS_FIELDS = ("feed", "ed_occupancy_pct", "boarding", "on_diversion")


@dataclass(frozen=True)
class Call:
    id: int
    t_min: float
    category: str
    acuity: int
    lat: float
    lon: float
    label: str = ""


@dataclass(frozen=True)
class Event:
    """A hospital status change partway through the scenario."""

    t_min: float
    hospital_id: str
    changes: dict[str, Any]
    label: str = ""


@dataclass
class Scenario:
    id: str
    title: str
    description: str
    traffic: str
    secondary_transfer_min: float
    queue_window_min: float
    statuses: dict[str, dict[str, Any]]
    calls: list[Call]
    events: list[Event] = field(default_factory=list)


@dataclass
class Assignment:
    call: Call
    hospital_id: str
    first_hospital_id: str
    t_dispatch: float
    drive: int  # original route
    arrive_at: float
    capable: bool = True
    swapped: bool = False
    minutes_to_care: int = 0
    offload_at_arrival: int = 0
    # current leg, for interpolating the unit's position
    leg_lat: float = 0.0
    leg_lon: float = 0.0
    leg_start: float = 0.0
    leg_drive: float = 1.0


@dataclass
class Metrics:
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


@dataclass
class SimResult:
    strategy: str
    assignments: list[Assignment]
    metrics: Metrics
    timeline: list[dict[str, int]] = field(default_factory=list)  # ambulances on the road or waiting, per ED, after each call


# ------------------------------------------------------------------ scenario loading


def load_scenario(path: str | Path) -> Scenario:
    raw = json.loads(Path(path).read_text())
    a = raw.get("assumptions", {})
    return Scenario(
        id=raw["id"],
        title=raw["title"],
        description=raw.get("description", ""),
        traffic=raw.get("traffic", "normal"),
        secondary_transfer_min=float(a.get("secondary_transfer_min", 45)),
        queue_window_min=float(a.get("queue_window_min", 15)),
        statuses=raw.get("statuses", {}),
        calls=[Call(**c) for c in raw["calls"]],
        events=[
            Event(
                t_min=e["t_min"], hospital_id=e["hospital_id"], label=e.get("label", ""),
                changes={k: e[k] for k in STATUS_FIELDS if k in e},
            )
            for e in raw.get("events", [])
        ],
    )


def apply_statuses(hospitals: list[HospitalSnapshot], statuses: dict[str, dict[str, Any]], base_now: datetime) -> list[HospitalSnapshot]:
    """Scenario hospital state at t=0, independent of whatever the live feed is doing."""
    out = []
    for h in hospitals:
        s = statuses.get(h.id, {})
        feed = s.get("feed", "none")
        out.append(
            replace(
                h,
                feed=feed,
                ed_occupancy_pct=s.get("ed_occupancy_pct"),
                boarding=s.get("boarding"),
                on_diversion=bool(s.get("on_diversion", False)),
                status_updated_at=(base_now - timedelta(minutes=s.get("age_minutes", 0))) if feed == "live" else None,
                units_en_route=0,
            )
        )
    return out


# ------------------------------------------------------------------ world state


class World:
    def __init__(self, hospitals: list[HospitalSnapshot], events: list[Event], base_now: datetime):
        self.base = {h.id: h for h in hospitals}
        self.events = sorted(events, key=lambda e: (e.t_min, e.hospital_id))
        self.base_now = base_now

    def now(self, t: float) -> datetime:
        return self.base_now + timedelta(minutes=t)

    def hospital(self, hid: str, t: float, units_en_route: int = 0) -> HospitalSnapshot:
        """Hospital state at time t: scripted events applied; a live feed's age is unchanged."""
        h = self.base[hid]
        changes: dict[str, Any] = {}
        for e in self.events:
            if e.hospital_id == hid and e.t_min <= t:
                changes.update(e.changes)
        ts = None if h.status_updated_at is None else h.status_updated_at + timedelta(minutes=t)
        if changes.get("feed") == "live" and ts is None:
            ts = self.now(t)
        return replace(h, **changes, status_updated_at=ts, units_en_route=units_en_route)

    def snapshots(self, assignments: list[Assignment], t: float, queue_window: float = 0) -> list[HospitalSnapshot]:
        counts = load(assignments, t, queue_window)
        return [self.hospital(hid, t, counts.get(hid, 0)) for hid in self.base]


def load(assignments: list[Assignment], t: float, queue_window: float = 0) -> dict[str, int]:
    """Ambulances counting against each ED at time t: on the road, or arrived within
    queue_window and still waiting to offload. Mirrors the live system, where a dispatch
    counts as en route until it is closed well after arrival."""
    counts: dict[str, int] = {}
    for a in assignments:
        if a.t_dispatch <= t < a.arrive_at + queue_window:
            counts[a.hospital_id] = counts.get(a.hospital_id, 0) + 1
    return counts


def en_route(assignments: list[Assignment], t: float) -> dict[str, int]:
    return load(assignments, t, 0)


def _position(a: Assignment, hosp: HospitalSnapshot, t: float) -> tuple[float, float]:
    f = min(1.0, max(0.0, (t - a.leg_start) / max(a.leg_drive, 1e-9)))
    return a.leg_lat + (hosp.lat - a.leg_lat) * f, a.leg_lon + (hosp.lon - a.leg_lon) * f


# ------------------------------------------------------------------ decisions


def _choose(strategy: str, snaps: list[HospitalSnapshot], call: Call, drive: dict[str, float], rules: Rules, now: datetime):
    """Return (hospital_id, drive_minutes) or None."""
    if strategy in ("metropolis", "metropolis_swaps"):
        rec = score(snaps, call.category, drive, rules, now).recommendations
        if rec:
            return rec[0].hospital.id, rec[0].minutes_drive
        b = nearest_ed(snaps, call.category, drive, rules, now)
    elif strategy == "nearest_capable":
        b = nearest_capable(snaps, call.category, drive, rules, now) or nearest_ed(snaps, call.category, drive, rules, now)
    elif strategy == "nearest_ed":
        b = nearest_ed(snaps, call.category, drive, rules, now)
    else:
        raise ValueError(f"Unknown strategy {strategy!r}")
    return None if b is None else (b.hospital.id, b.minutes_drive)


def _apply_swaps(world: World, assignments: list[Assignment], t: float, traffic: str, rules: Rules, last_swapped: dict[int, datetime], queue_window: float) -> int:
    """Run the ranker on units still on the road and apply its suggestions (auto-accepted in the replay)."""
    active = [a for a in assignments if a.t_dispatch <= t < a.arrive_at]
    if len(active) < 2:
        return 0
    snaps = {h.id: h for h in world.snapshots(assignments, t, queue_window)}
    dest_snaps = [snaps[d] for d in sorted({a.hospital_id for a in active})]
    transports, drive, pos = [], {}, {}
    for a in active:
        lat, lon = _position(a, world.base[a.hospital_id], t)
        pos[a.call.id] = (lat, lon)
        drive[a.call.id] = fallback_minutes(lat, lon, dest_snaps, traffic, rules)
        transports.append(
            Transport(
                dispatch_id=a.call.id, unit=f"Unit {a.call.id}", category=a.call.category, acuity=a.call.acuity,
                hospital_id=a.hospital_id, position_source="live", last_swapped_at=last_swapped.get(a.call.id),
            )
        )
    now = world.now(t)
    swaps = find_swaps(transports, snaps, drive, rules, now)
    by_call = {a.call.id: a for a in active}
    for s in swaps:
        for side in (s.a, s.b):
            a = by_call[side.dispatch_id]
            a.leg_lat, a.leg_lon = pos[a.call.id]
            a.leg_start, a.leg_drive = t, side.drive_after
            a.hospital_id = side.to_hospital
            a.arrive_at = t + side.drive_after
            a.swapped = True
            last_swapped[a.call.id] = now
    return len(swaps)


# ------------------------------------------------------------------ outcomes


def _score_outcomes(world: World, assignments: list[Assignment], rules: Rules, penalty: float, window: float) -> None:
    for a in assignments:
        ahead = sum(
            1
            for b in assignments
            if b is not a
            and b.hospital_id == a.hospital_id
            and a.arrive_at - window <= b.arrive_at
            and (b.arrive_at < a.arrive_at or (b.arrive_at == a.arrive_at and b.call.id < a.call.id))
        )
        h = world.hospital(a.hospital_id, a.arrive_at, units_en_route=ahead)
        off = predict_offload(h, rules, world.now(a.arrive_at)).minutes
        a.capable = capable(h, a.call.category, rules)[0]
        a.offload_at_arrival = off
        a.minutes_to_care = round((a.arrive_at - a.t_dispatch) + off + (0 if a.capable else penalty))


def simulate(
    calls: list[Call],
    hospitals: list[HospitalSnapshot],
    rules: Rules,
    strategy: str,
    traffic: str,
    transfer_penalty_min: float,
    base_now: datetime,
    events: list[Event] | None = None,
    queue_window_min: float = 15,
) -> SimResult:
    world = World(hospitals, events or [], base_now)
    assignments: list[Assignment] = []
    unassigned = swaps = 0
    last_swapped: dict[int, datetime] = {}
    timeline: list[dict[str, int]] = []
    peak, peak_h = 0, None

    for call in sorted(calls, key=lambda c: (c.t_min, c.id)):
        t = call.t_min
        snaps = world.snapshots(assignments, t, queue_window_min)
        drive = fallback_minutes(call.lat, call.lon, snaps, traffic, rules)
        choice = _choose(strategy, snaps, call, drive, rules, world.now(t))
        if choice is None:
            unassigned += 1
        else:
            hid, d = choice
            assignments.append(
                Assignment(
                    call=call, hospital_id=hid, first_hospital_id=hid, t_dispatch=t, drive=d, arrive_at=t + d,
                    leg_lat=call.lat, leg_lon=call.lon, leg_start=t, leg_drive=d,
                )
            )
            if strategy == "metropolis_swaps":
                swaps += _apply_swaps(world, assignments, t, traffic, rules, last_swapped, queue_window_min)
        counts = load(assignments, t, queue_window_min)
        timeline.append(counts)
        for hid, n in sorted(counts.items()):
            if n > peak:
                peak, peak_h = n, hid

    _score_outcomes(world, assignments, rules, transfer_penalty_min, queue_window_min)
    minutes = [a.minutes_to_care for a in assignments]
    crit = [a.minutes_to_care for a in assignments if a.call.acuity == 1]
    metrics = Metrics(
        assigned=len(assignments),
        unassigned=unassigned,
        mean=round(statistics.fmean(minutes), 1) if minutes else 0.0,
        median=float(statistics.median(minutes)) if minutes else 0.0,
        acuity1_mean=round(statistics.fmean(crit), 1) if crit else None,
        total=sum(minutes),
        not_capable=sum(1 for a in assignments if not a.capable),
        peak_en_route=peak,
        peak_hospital=peak_h,
        swaps=swaps,
    )
    return SimResult(strategy, assignments, metrics, timeline)


def run_all(scenario: Scenario, hospitals: list[HospitalSnapshot], rules: Rules, base_now: datetime) -> list[SimResult]:
    hs = apply_statuses(hospitals, scenario.statuses, base_now)
    return [
        simulate(
            scenario.calls, hs, rules, s, scenario.traffic, scenario.secondary_transfer_min, base_now,
            events=scenario.events, queue_window_min=scenario.queue_window_min,
        )
        for s in STRATEGIES
    ]
