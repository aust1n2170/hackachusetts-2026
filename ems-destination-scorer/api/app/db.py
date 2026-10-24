from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"options": "-c timezone=utc"},  # return every timestamp in UTC
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create the PostGIS extension and tables. Swap for Alembic migrations before production."""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(engine)
    # create_all doesn't add columns to existing tables. Until Alembic, patch columns
    # added after the first release so existing dev databases keep working.
    with engine.begin() as conn:
        for ddl in (
            "acuity integer NOT NULL DEFAULT 3",
            "origin_lat double precision",
            "origin_lon double precision",
            "position_lat double precision",
            "position_lon double precision",
            "position_updated_at timestamptz",
            "last_swapped_at timestamptz",
        ):
            conn.execute(text(f"ALTER TABLE dispatches ADD COLUMN IF NOT EXISTS {ddl}"))


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
