# Design: Before-and-after demo

## Overview

Two new pure modules, alongside `scoring.py` and `ranker.py`:

- `api/app/compare.py`: `baselines()` picks nearest-capable and nearest-ED destinations from the same inputs `score()` uses.
- `api/app/simulation.py`: `simulate()` replays a scenario through a strategy and returns outcomes and metrics.

The shell (`main.py`) loads hospitals and scenario files, calls these functions, and returns results. Demo mode adds a one-row `demo_state` table that `jobs` reads.

## `compare.py`

```python
@dataclass
class Baseline:
    hospital: HospitalSnapshot
    minutes_drive: int
    minutes_offload: int
    minutes_total: int
    capable: bool
    reason: str            # capability text or fail reason

def open_to(h, category, rules) -> bool
    # not on diversion unless category exempt; pediatric-only excluded for adult categories
def capable(h, category, rules) -> tuple[bool, str]          # CategoryRule.evaluate
def nearest_capable(hospitals, category, drive, rules, now) -> Baseline | None
def nearest_ed(hospitals, category, drive, rules, now) -> Baseline | None
```

Both pick the minimum rounded drive (ties: hospital id), using the same rounding as `score()` (`max(1, round(m))`) and `predict_offload`. Nearest capable = `open_to` and `capable`. Nearest ED = `open_to` only.

`/score` adds `comparison: {nearest_capable, nearest_ed}` with `BaselineOut {hospital_id, short_name, minutes_drive, minutes_offload, minutes_total, capable, reason, same_as_top}`. The audit row stores `response.model_dump(mode="json")`, so the comparison is included.

## Scenario files (`api/data/scenarios/<id>.json`)

```json
{
  "id": "surge",
  "title": "...", "description": "...",
  "traffic": "normal",
  "assumptions": { "secondary_transfer_min": 45 },
  "statuses": { "lwg": { "feed": "live", "ed_occupancy_pct": 118, "boarding": 14, "on_diversion": false, "age_minutes": 0 }, ... },
  "calls": [ { "id": 1, "t_min": 0, "category": "stemi", "acuity": 1, "lat": 42.35, "lon": -71.10, "label": "Fenway" }, ... ]
}
```

Hospitals missing from `statuses` keep their registry defaults (no feed, not on diversion). Calls are processed in `(t_min, id)` order.

## `simulation.py`

```python
@dataclass(frozen=True)
class Call: id, t_min, category, acuity, lat, lon, label

@dataclass
class Assignment:
    call: Call
    hospital_id: str
    t_dispatch: float
    drive: int              # minutes for the original route
    arrive_at: float        # t_dispatch + drive (updated on swap)
    minutes_to_care: int
    capable: bool
    swapped: bool

Strategy = Literal["nearest_ed", "nearest_capable", "metropolis", "metropolis_swaps"]

def simulate(calls, hospitals, rules, strategy, traffic, transfer_penalty_min, base_now) -> SimResult
```

Loop over calls at time `t`:

1. Hospital state at `t` = scenario statuses with any scripted `events` (status changes) applied. A unit counts against an ED from dispatch until `queue_window_min` after it arrives, matching the live system, where a dispatch counts as en route until it's closed. Build snapshots with those counts.
2. Drive from the call: `fallback_minutes(call.lat, call.lon, hospitals, traffic, rules)`.
3. Choose: `nearest_ed` / `nearest_capable` from `compare.py`, `metropolis` = `score(...).recommendations[0]`. If a strategy has no candidate, fall back to nearest ED; if none, the call is `unassigned`.
4. `metropolis_swaps` only: run `find_swaps` on units still on the road, with positions interpolated along their current leg, and apply every swap (new leg from the current position).
5. After all calls, **score outcomes at arrival, the same way for every strategy**: time on the road + `predict_offload` at the destination's state at arrival time, with one en-route unit for each ambulance that reached the same ED within `queue_window_min` before this one, + `secondary_transfer_min` if the hospital can't treat the patient.

Metrics: mean and median of `minutes_to_care`, acuity-1 mean, total, `not_capable` count, `peak_en_route` (max load at one ED right after any call, with hospital id), `swaps`, `unassigned`. `timeline` holds per-ED load after each call for the map replay.

Why the comparison is fair: decisions use what each strategy can see at dispatch; outcomes are measured afterwards with one model. Metropolis isn't graded on its own prediction. The UI says the figures are modeled.

## API

- `GET /simulate/scenarios` → `[ScenarioOut {id, title, description, calls}]`
- `POST /simulate {scenario}` → `SimulationOut { scenario, title, rules_version, traffic, assumptions, hospitals: [{id, short_name, lat, lon}], calls: [CallOut], strategies: [StrategyOut {id, label, metrics: MetricsOut, assignments: [AssignmentOut]}] }`
- Hospitals = DB registry (`load_snapshots`) with scenario statuses applied, so the simulation ignores whatever the live feed is doing.

## Demo mode

- `Settings.demo_mode: bool = False`. `.env.example` sets `DEMO_MODE=true`.
- Model `DemoState(id=1, frozen bool, scenario str|None, updated_at)`, created by `init_db()`.
- `GET /demo`, `POST /demo/load {scenario}`, `POST /demo/unfreeze`. All return 404 when demo mode is off.
- `/demo/load` in one transaction: delete `SwapSuggestion`, then `Dispatch`; upsert `HospitalStatus` from the scenario (`updated_at = now − age_minutes`); set `frozen = true`, `scenario = id`.
- `SimulatedLiveFeed.run`: if frozen, return `StatusUpdate(hospital_id)` with no values for each live hospital not in the stale list. The scheduler only refreshes `updated_at`.

## Console

- `api.ts`: `Comparison`, `Baseline`, simulation and demo types; `scenarios()`, `simulate()`, `demo()`, `demoLoad()`, `demoUnfreeze()`.
- `App.tsx`: comparison strip above the recommendation cards; demo bar under the header (when enabled).
- New `web/src/Simulation.tsx`:
  - scenario `<select>` and Run button;
  - strategy cards with a horizontal bar for mean minutes, highlighting minutes saved versus nearest capable;
  - a Leaflet map with a strategy toggle, step slider (0..N calls) and Play/Pause (one call per 700 ms);
  - per-call table;
  - "Simulation · fictional hospitals · modeled wait times" label.

## Testing (`api/tests/test_compare.py`, `api/tests/test_simulation.py`)

- **Baselines:** nearest capable skips incapable and diverting hospitals; nearest ED can pick an incapable hospital and reports `capable=False`; pediatric-only excluded for adults; null when nothing qualifies.
- **Simulation:**
  - deterministic (same input twice gives equal results);
  - en-route units raise later offload (pile-on);
  - the penalty is applied when not capable;
  - arrived units stop counting;
  - Metropolis is never worse than nearest capable on a crafted pile-on scenario;
  - the swaps strategy applies a swap in a crafted scenario and recomputes minutes;
  - the bundled `surge.json` loads and every call is assigned under every strategy.
