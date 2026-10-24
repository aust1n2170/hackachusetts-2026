# Project Structure

The project is **Metropolis** and lives in `metropolis/` under the workspace root. (`Boston Destination Scorer.html` at the root is a standalone artifact, not part of the running app.)

```
metropolis/
  api/                          FastAPI backend + feed jobs (Python 3.12)
    app/
      main.py                   FastAPI endpoints; wires repository + routing + scoring; writes audit
      scoring.py                PURE scoring: capability filter, diversion, offload model, ranking
      ranker.py                 PURE multi-patient ranker: destination swaps between units (rule R-1)
      rules.py                  Loads/validates region-rules.yaml into typed frozen dataclasses
      routing.py                OSRM drive-time matrix; straight-line haversine fallback
      models.py                 SQLAlchemy/PostGIS tables: hospitals, hospital_status, dispatches, score_audit
      repository.py             Hospital queries within search radius; counts units en route
      schemas.py                Pydantic request/response models (mirror web/src/api.ts)
      config.py                 pydantic-settings loaded from .env
      db.py                     Engine, session, init_db() (table creation), get_session() dependency
      seed.py                   Create tables + load sample registry (python -m app.seed)
    jobs/
      scheduler.py              APScheduler tick(): run connectors, close arrived dispatches
      connectors.py             SimulatedLiveFeed + stubs (RegionalDiversionFeed, HospitalPartnerFeed)
    rules/region-rules.yaml     The protocol (medical-director owned): categories, offload weights, traffic
    data/sample_hospitals.json  9 fictional greater-Boston hospitals
    tests/test_scoring.py       Pure-logic scoring tests (no DB/routing)
    Dockerfile, requirements.txt, requirements-dev.txt, pytest.ini
  web/                          React + Vite + Leaflet SPA
    src/
      App.tsx                   Whole dispatcher console UI: incident entry, map, recommendations, dispatch, status board
      api.ts                    Typed fetch client; prefixes /api; interfaces mirror schemas.py
      main.tsx                  React entry point
      styles.css
    vite.config.ts              Dev server + /api proxy to the API
  scripts/prepare-osrm.sh       One-time Massachusetts OSRM build
  docker-compose.yml, Makefile, README.md, .env.example
```

## Where things go

- **New incident category or capability requirement** → `api/rules/region-rules.yaml` (data, not code). Bump `version`. Add a test in `tests/test_scoring.py`.
- **New scoring/offload behavior** → `api/app/scoring.py` only, as pure functions. Never reach into the DB or network from here.
- **New hospital field** → add to `Hospital`/`HospitalStatus` in `models.py`, the snapshot in `repository.py`, the `HospitalSnapshot` dataclass + `attrs()` in `scoring.py`, and the relevant `schemas.py` model (and `web/src/api.ts` if exposed).
- **New API endpoint** → `api/app/main.py` + a Pydantic model in `schemas.py` + the matching call/interface in `web/src/api.ts`.
- **New live-data source** → a `Connector` subclass in `jobs/connectors.py`, added to the `CONNECTORS` list (the stubs `RegionalDiversionFeed` / `HospitalPartnerFeed` are the intended extension points).
- **UI changes** → `web/src/App.tsx` (single-component console) + `styles.css`.

## Key layering rules

- **Protocol vs. code.** Clinical/routing policy (which hospitals qualify, offload weights, traffic multipliers, diversion exemptions) lives in `region-rules.yaml`, owned by the medical director. Code in `rules.py` only loads, validates, and evaluates it. Prefer changing the YAML over hardcoding policy.
- **Pure core.** `scoring.py` ↔ dataclasses ↔ tests form a pure island. `main.py`/`repository.py`/`routing.py` are the impure shell that gathers inputs and persists outputs.
- **Schema parity.** `schemas.py` (Python) and `api.ts` (TypeScript) describe the same wire contract; edit them together.
- **Auditability.** Every `/score` call writes a `score_audit` row with the rules version and full request/response. Keep this when changing the scoring path.
