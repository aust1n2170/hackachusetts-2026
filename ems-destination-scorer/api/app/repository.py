"""Database reads that turn rows into scoring snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.orm import Session, selectinload

from .models import Dispatch, Hospital
from .ranker import Transport
from .rules import Rules
from .scoring import HospitalSnapshot


def point(lat: float, lon: float):
    return cast(func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326), Geography)


def en_route_counts(session: Session, rules: Rules, now: datetime) -> dict[str, int]:
    since = now - timedelta(minutes=rules.en_route_window_minutes)
    rows = session.execute(
        select(Dispatch.hospital_id, func.count())
        .where(Dispatch.arrived_at.is_(None), Dispatch.dispatched_at >= since)
        .group_by(Dispatch.hospital_id)
    ).all()
    return {hid: n for hid, n in rows}


def to_snapshot(h: Hospital, en_route: int) -> HospitalSnapshot:
    s = h.status
    return HospitalSnapshot(
        id=h.id,
        name=h.name,
        short_name=h.short_name,
        lat=h.lat,
        lon=h.lon,
        trauma_level=h.trauma_level,
        stroke_level=h.stroke_level,
        pci_24_7=h.pci_24_7,
        burn_center=h.burn_center,
        labor_delivery=h.labor_delivery,
        pediatric_ed=h.pediatric_ed,
        pediatric_only=h.pediatric_only,
        offload_baseline_min=h.offload_baseline_min,
        historical_offload_min=h.historical_offload_min,
        feed=(s.feed if s else "none"),  # type: ignore[arg-type]
        ed_occupancy_pct=s.ed_occupancy_pct if s else None,
        boarding=s.boarding if s else None,
        on_diversion=bool(s and s.on_diversion),
        status_updated_at=s.updated_at if s else None,
        units_en_route=en_route,
    )


def load_snapshots(
    session: Session,
    rules: Rules,
    now: datetime,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float | None = None,
) -> list[HospitalSnapshot]:
    """All hospitals, or those within radius_km of (lat, lon) when given."""
    stmt = select(Hospital).options(selectinload(Hospital.status)).order_by(Hospital.id)
    if lat is not None and lon is not None and radius_km:
        stmt = stmt.where(func.ST_DWithin(Hospital.location, point(lat, lon), radius_km * 1000))
    counts = en_route_counts(session, rules, now)
    return [to_snapshot(h, counts.get(h.id, 0)) for h in session.scalars(stmt)]


@dataclass
class TransportRow:
    transport: Transport
    lat: float
    lon: float


def open_dispatches(session: Session, rules: Rules, now: datetime) -> list[Dispatch]:
    """Dispatches that count as en route: not arrived, inside the en-route window."""
    since = now - timedelta(minutes=rules.en_route_window_minutes)
    return list(
        session.scalars(
            select(Dispatch)
            .where(Dispatch.arrived_at.is_(None), Dispatch.dispatched_at >= since)
            .order_by(Dispatch.id)
        )
    )


def to_transport(d: Dispatch, rules: Rules, now: datetime) -> TransportRow | None:
    """Pick the live position if fresh, otherwise the incident origin. None if neither is known."""
    fresh = (
        d.position_lat is not None
        and d.position_lon is not None
        and d.position_updated_at is not None
        and (now - d.position_updated_at).total_seconds() / 60 <= rules.ranker.position_stale_after_min
    )
    if fresh:
        lat, lon, source = d.position_lat, d.position_lon, "live"
    elif d.origin_lat is not None and d.origin_lon is not None:
        lat, lon, source = d.origin_lat, d.origin_lon, "incident_origin"
    else:
        return None
    t = Transport(
        dispatch_id=d.id,
        unit=d.unit,
        category=d.category,
        acuity=d.acuity,
        hospital_id=d.hospital_id,
        position_source=source,  # type: ignore[arg-type]
        last_swapped_at=d.last_swapped_at,
    )
    return TransportRow(t, lat, lon)  # type: ignore[arg-type]


def load_transports(session: Session, rules: Rules, now: datetime) -> list[TransportRow]:
    rows = (to_transport(d, rules, now) for d in open_dispatches(session, rules, now))
    return [r for r in rows if r is not None]
