"""Create tables and load the sample hospitals.

    python -m app.seed            # create tables, insert hospitals that are missing
    python -m app.seed --reset    # also wipe statuses, dispatches and audits, then reload
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from geoalchemy2.elements import WKTElement
from sqlalchemy import delete

from .db import SessionLocal, init_db
from .models import DemoState, Dispatch, Hospital, HospitalStatus, ScoreAudit, SwapSuggestion

DATA = Path(__file__).resolve().parent.parent / "data" / "sample_hospitals.json"
REGISTRY_FIELDS = (
    "name", "short_name", "lat", "lon", "trauma_level", "stroke_level", "pci_24_7", "burn_center",
    "labor_delivery", "pediatric_ed", "pediatric_only", "offload_baseline_min", "historical_offload_min",
)


def seed(reset: bool = False) -> int:
    init_db()
    rows = json.loads(DATA.read_text())["hospitals"]
    now = datetime.now(timezone.utc)
    added = 0
    with SessionLocal() as session:
        if reset:
            for model in (SwapSuggestion, Dispatch, ScoreAudit, HospitalStatus, DemoState, Hospital):
                session.execute(delete(model))
        for r in rows:
            h = session.get(Hospital, r["id"])
            if h is None:
                h = Hospital(id=r["id"])
                session.add(h)
                added += 1
            for f in REGISTRY_FIELDS:
                setattr(h, f, r.get(f))
            h.location = WKTElement(f"POINT({r['lon']} {r['lat']})", srid=4326)

            st = r.get("status", {})
            if session.get(HospitalStatus, r["id"]) is None:
                session.add(
                    HospitalStatus(
                        hospital_id=r["id"],
                        feed=st.get("feed", "none"),
                        ed_occupancy_pct=st.get("ed_occupancy_pct"),
                        boarding=st.get("boarding"),
                        on_diversion=st.get("on_diversion", False),
                        updated_at=now - timedelta(minutes=st.get("age_minutes", 1)),
                    )
                )
        session.commit()
    return added


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reset", action="store_true", help="wipe operational data and reload the sample registry")
    args = p.parse_args()
    n = seed(reset=args.reset)
    print(f"Seed complete: {n} hospital(s) added.")
