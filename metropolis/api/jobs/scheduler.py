"""Runs every connector on a fixed interval and writes their updates.

    python -m jobs.scheduler
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import settings
from app.db import SessionLocal
from app.models import HospitalStatus
from app.rules import load_rules

from .connectors import CONNECTORS, close_arrived_dispatches, move_simulated_units

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jobs")
RULES = load_rules(settings.rules_path)


def tick() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        for c in CONNECTORS:
            try:
                updates = c.run(session, now)
            except Exception:
                log.exception("connector %s failed; other connectors still run", c.name)
                continue
            for u in updates:
                st = session.get(HospitalStatus, u.hospital_id)
                if st is None:
                    log.warning("%s: unknown hospital %s", c.name, u.hospital_id)
                    continue
                for field in ("ed_occupancy_pct", "boarding", "on_diversion"):
                    value = getattr(u, field)
                    if value is not None:
                        setattr(st, field, value)
                st.updated_at = now
            if updates:
                log.info("%s: %d update(s)", c.name, len(updates))
        try:
            with session.begin_nested():  # a failure here must not lose the feed updates
                moved = move_simulated_units(
                    session, now, RULES.traffic["normal"].fallback_speed_kmh, RULES.road_factor
                )
            if moved:
                log.info("moved %d simulated unit(s)", moved)
        except Exception:
            log.exception("simulated unit movement failed; other jobs still run")
        closed = close_arrived_dispatches(session, now)
        if closed:
            log.info("marked %d dispatch(es) as arrived", closed)
        session.commit()


def main() -> None:
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(tick, "interval", seconds=settings.feed_interval_seconds, next_run_time=datetime.now(timezone.utc), max_instances=1, coalesce=True)
    log.info("feed scheduler started, every %ss", settings.feed_interval_seconds)
    sched.start()


if __name__ == "__main__":
    main()
