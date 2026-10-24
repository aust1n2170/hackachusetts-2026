# Tech Stack

## Architecture

Four containerized services orchestrated by `metropolis/docker-compose.yml`:

- **db** — PostgreSQL 16 + PostGIS 3.4 (`postgis/postgis:16-3.4`). Hospital registry, live status, dispatches, and the score audit trail. PostGIS powers radius geo-queries.
- **api** — Python 3.12 / FastAPI (`metropolis/api/`). On start: `python -m app.seed` (create tables + load sample hospitals), then `uvicorn app.main:app` on **:8000**.
- **jobs** — same image as `api`, runs `python -m jobs.scheduler`. An APScheduler `BlockingScheduler` firing every `FEED_INTERVAL_SECONDS` (default 60s) that simulates live ED feeds and closes stale dispatches.
- **web** — React 18 + TypeScript + Vite + Leaflet SPA (`metropolis/web/`) on **:5173**. Browser calls `/api/*`; Vite's dev proxy rewrites and forwards to the FastAPI service.
- **osrm** (optional, compose profile `routing`) — `osrm/osrm-backend:v5.27.1` serving Massachusetts road routing on :5000. Active only when `OSRM_URL` is set.

Data flow: browser → Vite `/api` proxy → FastAPI → `repository.py` (reads hospitals + en-route counts) → `routing.py` (drive times via OSRM or fallback) → `scoring.py` (pure ranking) → response + `score_audit` row. The `jobs` scheduler independently writes `hospital_status` rows that the next `/score` or `/hospitals` call reads.

## Backend (api/)

- **FastAPI** 0.115.6, **uvicorn[standard]** 0.32.1
- **SQLAlchemy** 2.0.36 (declarative, 2.0 style), **psycopg** 3.2.3 (binary), **GeoAlchemy2** 0.16.0
- **pydantic-settings** 2.6.1 for config, **httpx** 0.28.1 for OSRM calls
- **pyyaml** 6.0.2 for the rules file, **APScheduler** 3.10.4 for the feed jobs
- Dev: **pytest** 8.3.4
- Python **3.12**

## Frontend (web/)

- **react** / **react-dom** 18.3.1
- **react-leaflet** 4.2.1, **leaflet** 1.9.4 (OpenStreetMap tiles)
- Dev: **typescript** 5.6.3, **vite** 5.4.11, **@vitejs/plugin-react** 4.3.4
- Node **20**

## Common commands

Run from `metropolis/`.

```bash
# Full stack (Docker)
cp .env.example .env            # Makefile targets do this automatically
docker compose up --build       # or: make up
make down                       # stop
make logs                       # tail logs
make reset                      # wipe DB volume and re-seed

# Tests (no DB or routing server needed — scoring is pure)
make test                       # or: cd api && python -m pytest -q

# Real road routing (optional, ~10 min one-time download)
make osrm                       # build Massachusetts OSRM data
echo "OSRM_URL=http://osrm:5000" >> .env
make up-routing

# Without Docker
cd api && pip install -r requirements-dev.txt
python -m app.seed && uvicorn app.main:app --reload
python -m jobs.scheduler        # in a second terminal
cd ../web && npm install && npm run dev
```

- Web app: http://localhost:5173
- API docs (Swagger): http://localhost:8000/docs

## Configuration

Config comes from `.env` (see `api/.env.example`), loaded via pydantic-settings in `api/app/config.py`. Key vars:

- `DATABASE_URL` — Postgres connection (psycopg3 driver).
- `RULES_PATH` — path to `region-rules.yaml`.
- `OSRM_URL` — empty = straight-line fallback; set to enable real routing.
- `SEARCH_RADIUS_KM` — hospital search radius (default 60).
- `CORS_ORIGINS` — comma-separated allowed origins.
- Feed jobs: `SIMULATE_FEEDS`, `SIMULATE_STALE_IDS` (default `seu`), `FEED_INTERVAL_SECONDS` (default 60).

## Conventions

- **Scoring logic is pure.** `api/app/scoring.py` is pure functions over plain dataclasses — no DB, no network. Drive times are computed in `routing.py` and passed in. Keep it that way so tests need no infrastructure.
- **SQLAlchemy 2.0 style** (`select(...)`, `session.scalars(...)`, `Mapped`/typed columns).
- **Pydantic schemas mirror the TS client.** `api/app/schemas.py` and `web/src/api.ts` are kept field-for-field identical. Change both together.
- **Datetimes are UTC.** `utcnow()` returns timezone-aware UTC; the DB connection forces UTC.
- **Graceful degradation is a feature.** OSRM failure must fall back to straight-line estimates; stale/absent feeds must fall back to historical medians — always flagged in the response, never silently.
- **`init_db()` creates tables directly** (no migrations yet). Switching to Alembic is a pre-pilot task; don't assume migrations exist.
- Dependencies are **pinned** in `requirements.txt` / `package.json`. Keep pins exact.
