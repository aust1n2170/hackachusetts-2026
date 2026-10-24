from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Hospital(Base):
    """Registry entry: where a hospital is and what it can treat. Changes rarely."""

    __tablename__ = "hospitals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str] = mapped_column(String(80))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    location = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=False)

    # Capabilities (from state designations, ACS, Joint Commission)
    trauma_level: Mapped[int | None] = mapped_column(Integer)          # 1, 2, 3 or null
    stroke_level: Mapped[str | None] = mapped_column(String(8))        # CSC, TSC, PSC, ASR or null
    pci_24_7: Mapped[bool] = mapped_column(Boolean, default=False)
    burn_center: Mapped[bool] = mapped_column(Boolean, default=False)
    labor_delivery: Mapped[bool] = mapped_column(Boolean, default=False)
    pediatric_ed: Mapped[bool] = mapped_column(Boolean, default=False)
    pediatric_only: Mapped[bool] = mapped_column(Boolean, default=False)

    # Offload model inputs (learned from ePCR transfer times)
    offload_baseline_min: Mapped[float] = mapped_column(Float, default=5)
    historical_offload_min: Mapped[float] = mapped_column(Float, default=10)

    status: Mapped[HospitalStatus | None] = relationship(back_populates="hospital", uselist=False)


class HospitalStatus(Base):
    """Latest operational status. Written by the feed jobs or the status endpoint."""

    __tablename__ = "hospital_status"

    hospital_id: Mapped[str] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), primary_key=True)
    feed: Mapped[str] = mapped_column(String(16), default="none")      # live | none
    ed_occupancy_pct: Mapped[float | None] = mapped_column(Float)
    boarding: Mapped[int | None] = mapped_column(Integer)
    on_diversion: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    hospital: Mapped[Hospital] = relationship(back_populates="status")


class ScoreAudit(Base):
    """Every recommendation, with its inputs, so medical direction can review it later."""

    __tablename__ = "score_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rules_version: Mapped[str] = mapped_column(String(40))
    request: Mapped[dict] = mapped_column(JSONB)
    response: Mapped[dict] = mapped_column(JSONB)


class Dispatch(Base):
    """A unit sent to a hospital. Open dispatches count as units en route."""

    __tablename__ = "dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    unit: Mapped[str] = mapped_column(String(40))
    hospital_id: Mapped[str] = mapped_column(ForeignKey("hospitals.id"))
    category: Mapped[str] = mapped_column(String(32))
    recommended_rank: Mapped[int | None] = mapped_column(Integer)      # 1 = followed the top pick
    score_audit_id: Mapped[int | None] = mapped_column(ForeignKey("score_audit.id"))
    dispatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Ranker inputs
    acuity: Mapped[int] = mapped_column(Integer, default=3)            # 1 critical .. 4 non-urgent
    origin_lat: Mapped[float | None] = mapped_column(Float)            # incident location at dispatch
    origin_lon: Mapped[float | None] = mapped_column(Float)
    position_lat: Mapped[float | None] = mapped_column(Float)          # latest unit position (AVL stand-in)
    position_lon: Mapped[float | None] = mapped_column(Float)
    position_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_swapped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SwapSuggestion(Base):
    """A ranker (R-1) suggestion to trade destinations between two units, and its outcome."""

    __tablename__ = "swap_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | accepted | declined | expired
    rules_version: Mapped[str] = mapped_column(String(40))
    rule_id: Mapped[str] = mapped_column(String(16))
    dispatch_a_id: Mapped[int] = mapped_column(ForeignKey("dispatches.id", ondelete="CASCADE"))  # a < b
    dispatch_b_id: Mapped[int] = mapped_column(ForeignKey("dispatches.id", ondelete="CASCADE"))
    a_from: Mapped[str] = mapped_column(ForeignKey("hospitals.id"))
    a_to: Mapped[str] = mapped_column(ForeignKey("hospitals.id"))
    b_from: Mapped[str] = mapped_column(ForeignKey("hospitals.id"))
    b_to: Mapped[str] = mapped_column(ForeignKey("hospitals.id"))
    weighted_gain: Mapped[float] = mapped_column(Float)
    pareto: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict] = mapped_column(JSONB)                         # per-side minutes, acuity, sources
