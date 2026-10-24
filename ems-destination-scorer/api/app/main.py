from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import repository
from .config import settings
from .db import get_session
from .models import Dispatch, Hospital, HospitalStatus, ScoreAudit, SwapSuggestion
from .ranker import PatientSide, SwapCandidate, find_swaps
from .routing import drive_minutes
from .rules import Rules, load_rules
from .schemas import (
    CategoryOut,
    DispatchIn,
    DispatchOut,
    ExcludedOut,
    HospitalOut,
    OffloadOut,
    PositionUpdate,
    RankerEvaluateIn,
    RecommendationOut,
    RulesOut,
    ScoreRequest,
    ScoreResponse,
    StatusUpdate,
    SwapSideOut,
    SwapSuggestionOut,
    Traffic,
)
from .scoring import HospitalSnapshot, Recommendation, predict_offload, score


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.rules = load_rules(settings.rules_path)
    yield


app = FastAPI(title="EMS Destination Scorer", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_rules(request: Request) -> Rules:
    return request.app.state.rules


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def capabilities(h: HospitalSnapshot) -> list[str]:
    caps = []
    if h.trauma_level:
        caps.append(f"Trauma {'I' * h.trauma_level if h.trauma_level < 4 else h.trauma_level}")
    if h.stroke_level:
        caps.append(f"Stroke {h.stroke_level}")
    if h.pci_24_7:
        caps.append("PCI 24/7")
    if h.burn_center:
        caps.append("Burn")
    if h.labor_delivery:
        caps.append("L&D")
    if h.pediatric_ed:
        caps.append("Pediatric only" if h.pediatric_only else "Peds ED")
    return caps or ["ED"]


def rec_out(r: Recommendation) -> RecommendationOut:
    h = r.hospital
    return RecommendationOut(
        rank=r.rank,
        hospital_id=h.id,
        name=h.name,
        short_name=h.short_name,
        lat=h.lat,
        lon=h.lon,
        minutes_total=r.minutes_total,
        minutes_drive=r.minutes_drive,
        minutes_offload=r.minutes_offload,
        rule_id=r.rule_id,
        capability=r.capability,
        offload=OffloadOut(**r.offload.__dict__),
        units_en_route=h.units_en_route,
        on_diversion=h.on_diversion,
        diversion_exempt=r.diversion_exempt,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/rules", response_model=RulesOut)
def get_rules_summary(rules: Rules = Depends(get_rules)) -> RulesOut:
    return RulesOut(
        version=rules.version,
        categories=[
            CategoryOut(id=c.id, rule_id=c.rule_id, label=c.label, need=c.need, default_acuity=rules.ranker.default_acuity[c.id])
            for c in rules.categories.values()
        ],
    )


@app.post("/score", response_model=ScoreResponse)
async def post_score(
    req: ScoreRequest,
    session: Session = Depends(get_session),
    rules: Rules = Depends(get_rules),
) -> ScoreResponse:
    if req.category not in rules.categories:
        raise HTTPException(422, f"Unknown category {req.category!r}. Valid: {sorted(rules.categories)}")
    now = utcnow()
    hospitals = repository.load_snapshots(session, rules, now, req.lat, req.lon, settings.search_radius_km)
    minutes, source = await drive_minutes(req.lat, req.lon, hospitals, req.traffic, rules)
    result = score(hospitals, req.category, minutes, rules, now)

    body = dict(
        rules_version=result.rules_version,
        category=result.category_id,
        rule_id=result.rule_id,
        need=result.need,
        routing_source=source,
        recommendations=[rec_out(r) for r in result.recommendations],
        ranked_lower=[rec_out(r) for r in result.ranked_lower],
        excluded=[
            ExcludedOut(hospital_id=x.hospital.id, short_name=x.hospital.short_name, rule_id=x.rule_id, reason=x.reason)
            for x in result.excluded
        ],
    )
    audit = ScoreAudit(
        rules_version=result.rules_version,
        request=req.model_dump(),
        response={k: (v if isinstance(v, str) else [m.model_dump() for m in v]) for k, v in body.items()},
    )
    session.add(audit)
    session.commit()
    return ScoreResponse(audit_id=audit.id, **body)


@app.get("/hospitals", response_model=list[HospitalOut])
def list_hospitals(session: Session = Depends(get_session), rules: Rules = Depends(get_rules)) -> list[HospitalOut]:
    now = utcnow()
    out = []
    for h in repository.load_snapshots(session, rules, now):
        off = predict_offload(h, rules, now)
        out.append(
            HospitalOut(
                id=h.id,
                name=h.name,
                short_name=h.short_name,
                lat=h.lat,
                lon=h.lon,
                capabilities=capabilities(h),
                feed=h.feed,
                ed_occupancy_pct=h.ed_occupancy_pct,
                boarding=h.boarding,
                on_diversion=h.on_diversion,
                status_updated_at=h.status_updated_at,
                units_en_route=h.units_en_route,
                predicted_offload=OffloadOut(**off.__dict__),
            )
        )
    return out


@app.put("/hospitals/{hospital_id}/status", response_model=dict)
def update_status(hospital_id: str, upd: StatusUpdate, session: Session = Depends(get_session)) -> dict:
    if session.get(Hospital, hospital_id) is None:
        raise HTTPException(404, f"No hospital {hospital_id!r}")
    status = session.get(HospitalStatus, hospital_id) or HospitalStatus(hospital_id=hospital_id)
    for field, value in upd.model_dump(exclude_none=True).items():
        setattr(status, field, value)
    status.updated_at = utcnow()
    session.add(status)
    session.commit()
    return {"hospital_id": hospital_id, "updated_at": status.updated_at.isoformat()}


@app.post("/dispatches", response_model=DispatchOut, status_code=201)
def create_dispatch(d: DispatchIn, session: Session = Depends(get_session), rules: Rules = Depends(get_rules)) -> Dispatch:
    if session.get(Hospital, d.hospital_id) is None:
        raise HTTPException(404, f"No hospital {d.hospital_id!r}")
    if d.category not in rules.categories:
        raise HTTPException(422, f"Unknown category {d.category!r}")
    fields = d.model_dump()
    fields["acuity"] = d.acuity or rules.ranker.default_acuity[d.category]
    row = Dispatch(**fields, dispatched_at=utcnow())
    session.add(row)
    session.commit()
    return row


@app.put("/dispatches/{dispatch_id}/position", response_model=DispatchOut)
def update_position(dispatch_id: int, pos: PositionUpdate, session: Session = Depends(get_session)) -> Dispatch:
    """Latest unit position (what a CAD/AVL feed will call)."""
    row = session.get(Dispatch, dispatch_id)
    if row is None:
        raise HTTPException(404, f"No dispatch {dispatch_id}")
    if row.arrived_at is not None:
        raise HTTPException(409, f"Dispatch {dispatch_id} has already arrived")
    row.position_lat, row.position_lon, row.position_updated_at = pos.lat, pos.lon, utcnow()
    session.commit()
    return row


@app.post("/dispatches/{dispatch_id}/arrive", response_model=DispatchOut)
def mark_arrived(dispatch_id: int, session: Session = Depends(get_session)) -> Dispatch:
    row = session.get(Dispatch, dispatch_id)
    if row is None:
        raise HTTPException(404, f"No dispatch {dispatch_id}")
    if row.arrived_at is None:
        row.arrived_at = utcnow()
        session.commit()
    return row


@app.get("/dispatches", response_model=list[DispatchOut])
def list_dispatches(limit: int = 50, session: Session = Depends(get_session)) -> list[Dispatch]:
    return list(session.scalars(select(Dispatch).order_by(Dispatch.dispatched_at.desc()).limit(min(limit, 500))))


# ---------------------------------------------------------------- ranker (rule R-1)


async def evaluate_swaps(
    session: Session, rules: Rules, now: datetime, traffic: Traffic, only: set[int] | None = None
) -> tuple[list[SwapCandidate], str]:
    """Load units in transport, compute drive times from each unit's position, run find_swaps."""
    rows = repository.load_transports(session, rules, now)
    if only is not None:
        rows = [r for r in rows if r.transport.dispatch_id in only]
    if len(rows) < 2:
        return [], "none"
    hospitals = {h.id: h for h in repository.load_snapshots(session, rules, now)}
    dest = [hospitals[i] for i in sorted({r.transport.hospital_id for r in rows}) if i in hospitals]
    results = await asyncio.gather(*(drive_minutes(r.lat, r.lon, dest, traffic, rules) for r in rows))
    drive = {r.transport.dispatch_id: minutes for r, (minutes, _) in zip(rows, results)}
    sources = {src for _, src in results}
    source = "osrm" if sources == {"osrm"} else "straight_line_estimate"
    ids = [r.transport.dispatch_id for r in rows]
    declined = {
        (s.dispatch_a_id, s.dispatch_b_id, s.a_to, s.b_to)
        for s in session.scalars(
            select(SwapSuggestion).where(SwapSuggestion.status == "declined", SwapSuggestion.dispatch_a_id.in_(ids))
        )
    }
    return find_swaps([r.transport for r in rows], hospitals, drive, rules, now, declined), source


def side_dict(s: PatientSide) -> dict:
    return dict(
        dispatch_id=s.dispatch_id, unit=s.unit, category=s.category, acuity=s.acuity,
        from_hospital=s.from_hospital, to_hospital=s.to_hospital,
        minutes_before=s.minutes_before, minutes_after=s.minutes_after,
        drive_before=s.drive_before, drive_after=s.drive_after,
        offload=s.offload_after.__dict__, position_source=s.position_source,
    )


def swap_out(row: SwapSuggestion) -> SwapSuggestionOut:
    d = row.detail
    return SwapSuggestionOut(
        id=row.id, status=row.status, rules_version=row.rules_version, rule_id=row.rule_id,  # type: ignore[arg-type]
        a=SwapSideOut(**d["a"]), b=SwapSideOut(**d["b"]),
        weighted_gain=row.weighted_gain, pareto=row.pareto, routing_source=d["routing_source"],
        created_at=row.created_at, decided_at=row.decided_at,
    )


@app.post("/ranker/evaluate", response_model=list[SwapSuggestionOut])
async def ranker_evaluate(
    body: RankerEvaluateIn,
    session: Session = Depends(get_session),
    rules: Rules = Depends(get_rules),
) -> list[SwapSuggestionOut]:
    """Find destination swaps among units in transport. Suggestions only; nothing is applied.

    A suggestion that is still valid keeps its id and gets fresh figures; pending suggestions
    that are no longer valid are marked expired.
    """
    now = utcnow()
    candidates, source = await evaluate_swaps(session, rules, now, body.traffic)
    pending = {
        (r.dispatch_a_id, r.dispatch_b_id, r.a_to, r.b_to): r
        for r in session.scalars(select(SwapSuggestion).where(SwapSuggestion.status == "pending"))
    }
    keep: list[SwapSuggestion] = []
    for c in candidates:
        detail = dict(a=side_dict(c.a), b=side_dict(c.b), routing_source=source, traffic=body.traffic)
        key = (c.a.dispatch_id, c.b.dispatch_id, c.a.to_hospital, c.b.to_hospital)
        row = pending.pop(key, None)
        if row is None:
            row = SwapSuggestion(
                status="pending", rule_id=c.rule_id,
                dispatch_a_id=c.a.dispatch_id, dispatch_b_id=c.b.dispatch_id,
                a_from=c.a.from_hospital, a_to=c.a.to_hospital, b_from=c.b.from_hospital, b_to=c.b.to_hospital,
                created_at=now,
            )
            session.add(row)
        row.rules_version = rules.version
        row.weighted_gain = c.weighted_gain
        row.pareto = c.pareto
        row.detail = detail
        keep.append(row)
    for stale in pending.values():
        stale.status, stale.decided_at = "expired", now
    session.commit()
    return [swap_out(r) for r in keep]


@app.post("/ranker/swaps/{swap_id}/accept", response_model=SwapSuggestionOut)
async def ranker_accept(
    swap_id: int,
    session: Session = Depends(get_session),
    rules: Rules = Depends(get_rules),
) -> SwapSuggestionOut:
    """Apply a pending swap after re-validating it. Both dispatches change in one transaction."""
    row = session.get(SwapSuggestion, swap_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, f"No swap suggestion {swap_id}")
    if row.status != "pending":
        raise HTTPException(409, f"Swap {swap_id} is {row.status}")
    ds = {
        d.id: d
        for d in session.scalars(
            select(Dispatch).where(Dispatch.id.in_([row.dispatch_a_id, row.dispatch_b_id])).with_for_update()
        )
    }
    a, b = ds.get(row.dispatch_a_id), ds.get(row.dispatch_b_id)
    if a is None or b is None or a.arrived_at or b.arrived_at:
        raise HTTPException(409, "A unit in this swap has arrived or no longer exists")
    if a.hospital_id != row.a_from or b.hospital_id != row.b_from:
        raise HTTPException(409, "A destination changed since this swap was suggested")

    now = utcnow()
    still, _ = await evaluate_swaps(session, rules, now, row.detail.get("traffic", "normal"), only={a.id, b.id})
    if not any(c.a.to_hospital == row.a_to and c.b.to_hospital == row.b_to for c in still):
        raise HTTPException(409, "This swap no longer improves care; re-evaluate")

    a.hospital_id, b.hospital_id = row.a_to, row.b_to
    a.last_swapped_at = b.last_swapped_at = now
    row.status, row.decided_at = "accepted", now
    # Other pending suggestions involving these units are now out of date.
    for other in session.scalars(
        select(SwapSuggestion).where(
            SwapSuggestion.status == "pending",
            SwapSuggestion.id != row.id,
            (SwapSuggestion.dispatch_a_id.in_([a.id, b.id])) | (SwapSuggestion.dispatch_b_id.in_([a.id, b.id])),
        )
    ):
        other.status, other.decided_at = "expired", now
    session.commit()
    return swap_out(row)


@app.post("/ranker/swaps/{swap_id}/decline", response_model=SwapSuggestionOut)
def ranker_decline(swap_id: int, session: Session = Depends(get_session)) -> SwapSuggestionOut:
    row = session.get(SwapSuggestion, swap_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, f"No swap suggestion {swap_id}")
    if row.status != "pending":
        raise HTTPException(409, f"Swap {swap_id} is {row.status}")
    row.status, row.decided_at = "declined", utcnow()
    session.commit()
    return swap_out(row)
