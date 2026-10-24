"""Feed connectors. Each one returns status updates; the scheduler writes them.

Only the simulator and the arrival sweep do anything today. The diversion and
partner-feed connectors are stubs showing where real integrations plug in.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Dispatch, Hospital, HospitalStatus
from app.routing import haversine_km

log = logging.getLogger(__name__)


@dataclass
class StatusUpdate:
    hospital_id: str
    ed_occupancy_pct: float | None = None
    boarding: int | None = None
    on_diversion: bool | None = None


class Connector:
    name = "base"

    def run(self, session: Session, now: datetime) -> list[StatusUpdate]:
        raise NotImplementedError


class SimulatedLiveFeed(Connector):
    """Random-walks ED occupancy and boarding for hospitals marked feed=live, so the
    dev environment behaves like it has partner feeds. Hospitals in SIMULATE_STALE_IDS
    are skipped so their feed goes stale and the historical fallback can be seen."""

    name = "simulated_live_feed"

    def run(self, session: Session, now: datetime) -> list[StatusUpdate]:
        if not settings.simulate_feeds:
            return []
        skip = {s.strip() for s in settings.simulate_stale_ids.split(",") if s.strip()}
        out = []
        for st in session.scalars(select(HospitalStatus).where(HospitalStatus.feed == "live")):
            if st.hospital_id in skip:
                continue
            occ = min(140.0, max(55.0, (st.ed_occupancy_pct or 85) + random.uniform(-3, 3)))
            board = max(0, (st.boarding or 0) + random.choice([-1, 0, 0, 1]))
            out.append(StatusUpdate(st.hospital_id, round(occ, 1), board))
        return out


class RegionalDiversionFeed(Connector):
    """TODO: poll the regional diversion system (for example, an EMResource-type API)
    and map its facility IDs to ours. Needs an agreement with the EMS region."""

    name = "regional_diversion"

    def run(self, session: Session, now: datetime) -> list[StatusUpdate]:
        return []


class HospitalPartnerFeed(Connector):
    """TODO: read aggregate ED metrics from a partner hospital (FHIR R4, HL7 v2 via an
    interface engine, or a vendor feed such as Qventus). Requires a BAA and a data use
    agreement per hospital. Only aggregate counts, never patient records."""

    name = "hospital_partner"

    def run(self, session: Session, now: datetime) -> list[StatusUpdate]:
        return []


def move_simulated_units(session: Session, now: datetime, speed_kmh: float, road_factor: float) -> int:
    """Stand-in for CAD/AVL unit positions: move each open dispatch toward its destination
    in a straight line, at `speed_kmh` along the road (so speed / road_factor as the crow
    flies). Dispatches without a known origin are left alone."""
    if not settings.simulate_feeds:
        return 0
    rows = session.execute(
        select(Dispatch, Hospital.lat, Hospital.lon)
        .join(Hospital, Hospital.id == Dispatch.hospital_id)
        .where(Dispatch.arrived_at.is_(None), Dispatch.origin_lat.is_not(None), Dispatch.origin_lon.is_not(None))
    ).all()
    moved = 0
    for d, hlat, hlon in rows:
        lat = d.position_lat if d.position_lat is not None else d.origin_lat
        lon = d.position_lon if d.position_lon is not None else d.origin_lon
        since = d.position_updated_at or d.dispatched_at
        elapsed_h = max(0.0, (now - since).total_seconds() / 3600)
        step_km = speed_kmh / road_factor * elapsed_h
        left_km = haversine_km(lat, lon, hlat, hlon)
        f = 1.0 if left_km <= step_km or left_km == 0 else step_km / left_km
        d.position_lat = lat + (hlat - lat) * f
        d.position_lon = lon + (hlon - lon) * f
        d.position_updated_at = now
        moved += 1
    return moved


def close_arrived_dispatches(session: Session, now: datetime, after_minutes: int = 40) -> int:
    """Stand-in for ePCR/CAD arrival times: treat dispatches older than `after_minutes`
    as arrived so they stop counting as units en route."""
    res = session.execute(
        update(Dispatch)
        .where(Dispatch.arrived_at.is_(None), Dispatch.dispatched_at < now - timedelta(minutes=after_minutes))
        .values(arrived_at=now)
    )
    return res.rowcount or 0


CONNECTORS: list[Connector] = [SimulatedLiveFeed(), RegionalDiversionFeed(), HospitalPartnerFeed()]
