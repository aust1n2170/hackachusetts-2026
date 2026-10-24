# Design: Destination Ranker

## Overview

The ranker adds a cross-patient layer on top of the existing single-patient scorer. It reuses the existing pieces unchanged: `CategoryRule.evaluate` for capability, the D-1 diversion rule, `predict_offload` for wait time, and `routing.drive_minutes` for drive time. The new decision logic is one pure function, `find_swaps`, in a new module `api/app/ranker.py`. The impure shell (`main.py`, `repository.py`, `routing.py`) gathers inputs and persists suggestions, the same split `/score` uses.

```
POST /ranker/evaluate
  └─ repository.load_transports()      open dispatches + positions (DB)
  └─ repository.load_snapshots()       hospitals + en-route counts (DB)
  └─ routing.drive_minutes() per unit  current position → candidate hospitals
  └─ ranker.find_swaps()               PURE: validate, cost, select
  └─ upsert/expire SwapSuggestion rows (DB) → response
```

## Why per-hospital offload is unchanged by a swap

`units_en_route` counts open dispatches per hospital. A swap moves one unit out of X and one into X (same for Y), so the counts and `predict_offload(X)`/`predict_offload(Y)` are identical before and after. The ranker can compute offload once per hospital and only the drive term differs per patient. This is a v1 simplification. Acuity-dependent queueing is out of scope.

## Rules file additions (`region-rules.yaml`, version → `1.4-sample`)

```yaml
ranker:
  rule_id: R-1
  # Acuity 1 = critical, 2 = emergent, 3 = urgent, 4 = non-urgent (sample levels, not a standard)
  acuity_weights: { 1: 4.0, 2: 2.0, 3: 1.0, 4: 0.5 }
  default_acuity: { trauma: 1, lvo: 1, stemi: 1, burn: 2, stroke: 2, peds: 2, ob: 3, general: 3 }
  min_weighted_gain_min: 5        # suggest only if weighted minutes saved >= this
  max_individual_loss_min: 10     # never make one patient more than this much later
  min_remaining_drive_min: 4      # too close to arrival to redirect
  swap_cooldown_min: 15           # a dispatch can't be swapped again this soon
  position_stale_after_min: 3     # older positions fall back to incident origin
```

`rules.py`: add a frozen `RankerRules` dataclass and a `ranker: RankerRules` field on `Rules`. `load_rules` validates that the weights cover exactly 1–4, that `default_acuity` covers every category with values 1–4, and that thresholds are ≥ 0. Any failure raises `ValueError`, matching the existing validation style.

## Data model changes (`models.py`)

`Dispatch` gets new nullable/defaulted columns (no Alembic yet, so `init_db()` creates them. Existing local DBs need `make reset`):

| Column | Type | Notes |
| --- | --- | --- |
| `acuity` | int | set from request or category default |
| `origin_lat`, `origin_lon` | float, nullable | incident location at dispatch |
| `position_lat`, `position_lon` | float, nullable | latest unit position |
| `position_updated_at` | timestamptz, nullable | |
| `last_swapped_at` | timestamptz, nullable | cooldown |

New table `swap_suggestions` (`SwapSuggestion`):

| Column | Type |
| --- | --- |
| `id` | int PK |
| `created_at`, `decided_at` | timestamptz |
| `status` | `pending \| accepted \| declined \| expired` |
| `rules_version`, `rule_id` | str |
| `dispatch_a_id`, `dispatch_b_id` | FK dispatches (stored with a < b) |
| `a_from`, `a_to`, `b_from`, `b_to` | FK hospitals |
| `weighted_gain` | float |
| `pareto` | bool |
| `detail` | JSONB: per-patient acuity, weight, minutes before/after, drive/offload split, offload source/stale, position_source; plus routing_source |

Dedup key for pending suggestions: `(dispatch_a_id, dispatch_b_id, a_to, b_to)` with status `pending`.

## Pure core (`api/app/ranker.py`)

```python
@dataclass(frozen=True)
class Transport:
    dispatch_id: int
    unit: str
    category: str
    acuity: int
    hospital_id: str                 # current destination
    position_source: Literal["live", "incident_origin"]
    last_swapped_at: datetime | None

@dataclass
class PatientSide:
    dispatch_id: int
    unit: str
    acuity: int
    weight: float
    from_hospital: str
    to_hospital: str
    minutes_before: int
    minutes_after: int
    offload_after: Offload
    position_source: str

@dataclass
class SwapCandidate:
    a: PatientSide
    b: PatientSide
    weighted_gain: float
    pareto: bool
    rule_id: str

def find_swaps(
    transports: list[Transport],
    hospitals: dict[str, HospitalSnapshot],
    drive: dict[int, dict[str, float]],     # dispatch_id -> hospital_id -> minutes from current position
    rules: Rules,
    now: datetime,
) -> list[SwapCandidate]:
```

Algorithm:

1. Precompute `off[h] = predict_offload(hospitals[h], rules, now)` for every destination involved.
2. For each transport, `t(p, h) = max(1, round(drive[p][h])) + off[h].minutes`, using the same rounding as `score()`.
3. For each unordered pair (A, B) with different destinations X, Y, skip the pair if any validity rule fails (Req 3.2–3.6): capability via `rules.categories[cat].evaluate(hospitals[h].attrs())`, diversion vs `rules.diversion_exempt`, `drive[p][own] < min_remaining_drive_min`, cooldown, missing drive.
4. Compute gain and individual deltas. Keep the pair if gain ≥ threshold and both losses ≤ `max_individual_loss_min`.
5. Sort by `(-weighted_gain, a.dispatch_id, b.dispatch_id)`. Greedily select, skipping any candidate whose dispatch was already used.

Complexity is O(N²) pairs over open dispatches. That's fine for regional volumes (tens of units).

## Shell

- `repository.load_transports(session, rules, now)`: open dispatches within `en_route_window_minutes`. Picks the live position if fresh, otherwise the origin, and returns `(Transport, lat, lon)`.
- Drive times: call the existing `routing.drive_minutes(lat, lon, hospitals, traffic, rules)` once per transport via `asyncio.gather`, restricted to the hospitals that are current destinations. Report `routing_source` as `"osrm"` only if every call used OSRM, otherwise `"straight_line_estimate"`. (A multi-source OSRM `/table` call is a later optimization.)
- `POST /ranker/evaluate`: run `find_swaps`, upsert pending suggestions by dedup key, mark other pending rows `expired`, commit, and return pending suggestions.
- `POST /ranker/swaps/{id}/accept`: in one transaction, lock both dispatches (`SELECT … FOR UPDATE`), and check status is pending, both open, and destinations still equal `a_from`/`b_from`. Re-run `find_swaps` on just this pair to confirm it is still valid. Then swap `hospital_id`, set `last_swapped_at=now`, `status=accepted`, `decided_at=now`. Any failure returns 409.
- `POST /ranker/swaps/{id}/decline`: pending → declined, else 409.
- `PUT /dispatches/{id}/position`: validate lat/lon ranges like `ScoreRequest`. 404 if missing, 409 if arrived.
- `POST /dispatches`: accept `acuity` (1–4, optional) and `origin_lat`/`origin_lon` (optional). The console sends the incident location.

## Schemas (`schemas.py` ↔ `web/src/api.ts`, kept identical)

- `DispatchIn`: `+ acuity: int | None (1–4)`, `+ origin_lat/origin_lon: float | None`.
- `DispatchOut`: `+ acuity`, `+ position_lat/position_lon/position_updated_at`.
- `PositionUpdate { lat, lon }`.
- `RankerEvaluateIn { traffic: Traffic = "normal" }`.
- `SwapSideOut { dispatch_id, unit, acuity, from_hospital, to_hospital, minutes_before, minutes_after, offload: OffloadOut, position_source }`.
- `SwapSuggestionOut { id, status, rules_version, rule_id, a: SwapSideOut, b: SwapSideOut, weighted_gain, pareto, routing_source, created_at }`.
- `RulesOut`: `+ default_acuity: dict[str, int]` so the console can preselect acuity.

## Simulated positions (`jobs/connectors.py`)

Add `move_simulated_units(session, now)`, called from `tick()` after connectors and guarded like them. For each open dispatch it moves the position a fraction of the way from the current position (or origin) toward the destination. The step is the elapsed time since the last update × a nominal speed (from the `normal` traffic profile), capped at the destination. It runs only when `SIMULATE_FEEDS` is true. `close_arrived_dispatches` stays as is.

## Console (`web/src/App.tsx`)

- An acuity `<select>` (labeled) next to the send button, defaulting to `rules.default_acuity[category]`.
- `send()` passes `acuity`, `origin_lat`, `origin_lon`.
- `refresh()` also calls `api.evaluateSwaps({ traffic })`. A "Suggested swaps" panel lists cards: "Medic 3 ⇄ Medic 5", each side `from → to · 24 → 17 min · acuity 1`, gain, a Pareto chip, and warning chips for `incident_origin` positions or stale offload. Accept/Decline buttons refresh afterwards.
- The map draws dashed lines for proposed new routes on the hovered/focused suggestion (optional, if time permits).

## Error handling and degradation

- OSRM down → straight-line estimate, flagged via `routing_source`.
- Stale/missing positions → origin, flagged via `position_source`.
- Stale feed → historical offload, flagged on each side's `offload`.
- No open dispatches or fewer than 2 → empty list (not an error).

## Testing (`api/tests/test_ranker.py`, pure, no DB)

- Severe patient to long wait + mild patient to short wait → swap suggested; gain matches the hand calculation.
- Pareto case flagged `pareto: true`.
- Gain below threshold → no suggestion; individual loss above cap → no suggestion.
- Capability fails at the new hospital (e.g. STEMI to non-PCI) → no suggestion.
- New hospital on diversion: non-exempt category blocked, exempt allowed.
- Remaining drive below `min_remaining_drive_min` → excluded; cooldown → excluded; missing drive → excluded.
- Conflicting swaps → greedy keeps highest gain, no dispatch twice.
- Deterministic order on ties.
- `load_rules` rejects missing acuity weights / default acuity for a category.
- Existing `test_scoring.py` still passes after the rules version bump.
