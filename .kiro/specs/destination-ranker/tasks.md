# Tasks: Destination Ranker

- [x] 1. Ranker rules
  - [x] 1.1 Add the `ranker:` section to `api/rules/region-rules.yaml` and bump `version` to `1.4-sample`.
  - [x] 1.2 Add `RankerRules` to `api/app/rules.py`, parse it in `load_rules`, and validate weights (levels 1–4), default acuity (every category, values 1–4) and non-negative thresholds.
  - [x] 1.3 Tests: valid load; missing weight level → `ValueError`; category missing from `default_acuity` → `ValueError`.
  - _Requirements: 1.5, 6.1_

- [x] 2. Pure swap logic
  - [x] 2.1 Create `api/app/ranker.py` with `Transport`, `PatientSide`, `SwapCandidate` and `find_swaps` per the design (capability, diversion, remaining-drive, cooldown, missing-drive checks; weighted gain; loss cap; Pareto flag; greedy non-conflicting selection; deterministic order).
  - [x] 2.2 `api/tests/test_ranker.py` covering every case in the design's Testing section.
  - _Requirements: 3.1–3.12, 6.2_

- [x] 3. Data model
  - [x] 3.1 Add `acuity`, `origin_lat/lon`, `position_lat/lon`, `position_updated_at`, `last_swapped_at` to `Dispatch` in `api/app/models.py`.
  - [x] 3.2 Add the `SwapSuggestion` model (`swap_suggestions` table).
  - [x] 3.3 Include `SwapSuggestion` in the `seed.py --reset` wipe, deleted before `Dispatch`.
  - _Requirements: 1.4, 2.1, 4.4_

- [x] 4. Schemas (keep `schemas.py` and `web/src/api.ts` identical)
  - [x] 4.1 Extend `DispatchIn`/`DispatchOut`; add `PositionUpdate`, `RankerEvaluateIn`, `SwapSideOut`, `SwapSuggestionOut`; add `default_acuity` to each category in `RulesOut`.
  - [x] 4.2 Mirror the types and add `updatePosition`, `evaluateSwaps`, `acceptSwap`, `declineSwap` in `web/src/api.ts`.
  - _Requirements: 1.1–1.4, 2.2, 4.1–4.7_

- [x] 5. Endpoints and repository
  - [x] 5.1 `POST /dispatches`: accept acuity (default from rules), origin; 422 on invalid acuity.
  - [x] 5.2 `PUT /dispatches/{id}/position` with 404/409 handling.
  - [x] 5.3 `repository.load_transports` with fresh-position vs origin fallback and `position_source`.
  - [x] 5.4 `POST /ranker/evaluate`: drive times per transport (`asyncio.gather` over `routing.drive_minutes`), `find_swaps`, upsert by dedup key, expire stale pending rows.
  - [x] 5.5 `POST /ranker/swaps/{id}/accept` (row locks, re-validate, atomic swap, 409 on conflict) and `/decline`.
  - _Requirements: 1.1–1.3, 2.2–2.4, 4.1–4.8_

- [x] 6. Simulated unit movement
  - [x] 6.1 `move_simulated_units` in `api/jobs/connectors.py`, called from `tick()` in `api/jobs/scheduler.py` with per-step failure isolation; only when `SIMULATE_FEEDS` is true.
  - _Requirements: 2.5_

- [x] 7. Console
  - [x] 7.1 Acuity selector (labeled, keyboard-operable) defaulting to the category's default; `send()` passes acuity and origin.
  - [x] 7.2 "Suggested swaps" panel with before/after minutes, gain, Pareto and estimate/stale chips, Accept/Decline; `refresh()` calls `evaluateSwaps`.
  - [x] 7.3 Keep the "sample data · not for clinical use" framing.
  - _Requirements: 5.1–5.5_

- [x] 8. Verify and document
  - [x] 8.1 `cd metropolis/api && python -m pytest -q` passes (old and new tests: 30).
  - [x] 8.2 End-to-end against the running stack via the API: suggestion, dedup, position update, accept, decline, cooldown, 404/409/422 paths, simulated movement. (Ran on the existing dev DB instead of `make reset`; the console was not checked in a browser.)
  - [x] 8.3 `npx tsc --noEmit` passes (run inside the web container).
  - [x] 8.4 Add the ranker endpoints and `ranker:` rules to `metropolis/README.md`.

## Changes from the design

- Declined swaps are passed to `find_swaps` (`declined=`) and not suggested again; otherwise they reappeared on the next 30-second refresh.
- Accepting a swap expires other pending suggestions involving either unit.
- `init_db()` adds the new `dispatches` columns with `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, so existing dev databases work without `make reset`.
- The console also draws unit positions on the map.
