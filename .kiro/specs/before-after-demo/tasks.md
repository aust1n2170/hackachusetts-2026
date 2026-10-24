# Tasks: Before-and-after demo

- [x] 1. Baselines
  - [x] 1.1 `api/app/compare.py` with `open_to`, `capable`, `nearest_capable`, `nearest_ed`.
  - [x] 1.2 `api/tests/test_compare.py` (6 tests).
  - _Requirements: 1.1–1.3_

- [x] 2. Comparison in `/score`
  - [x] 2.1 `BaselineOut`, `ComparisonOut` in `schemas.py`; `comparison` on `ScoreResponse`; audit stores the full response.
  - [x] 2.2 Mirror in `web/src/api.ts`; comparison strip in `App.tsx`.
  - _Requirements: 1.1–1.6_

- [x] 3. Simulation core
  - [x] 3.1 `api/data/scenarios/surge.json` (16 calls, crowded Longwood General, a STEMI near a hospital without a cath lab, a burn call, Code Black at Bayside, a stale feed).
  - [x] 3.2 `api/app/simulation.py` (`Call`, `Event`, `Assignment`, `World`, `simulate`, `run_all`, scenario loader).
  - [x] 3.3 `api/tests/test_simulation.py` (9 tests).
  - _Requirements: 2.1, 2.3–2.8_

- [x] 4. Simulation API
  - [x] 4.1 `GET /simulate/scenarios`, `POST /simulate`, schemas, 404 for unknown scenario, 422 for an invalid id.
  - _Requirements: 2.2, 2.9, 2.10_

- [x] 5. Demo mode
  - [x] 5.1 `demo_mode` setting, `.env.example`, `DemoState` model.
  - [x] 5.2 `GET /demo`, `POST /demo/load`, `POST /demo/unfreeze`.
  - [x] 5.3 Frozen behavior in `SimulatedLiveFeed`.
  - _Requirements: 4.1–4.4_

- [x] 6. Console
  - [x] 6.1 `web/src/Simulation.tsx`: picker, Run, strategy cards, replay map with slider and Play, per-call table, simulation label, `aria-live`. `?replay=<scenario>` runs it on load.
  - [x] 6.2 Demo bar in `App.tsx`.
  - _Requirements: 3.1–3.6, 4.5_

- [x] 7. Verify and document
  - [x] 7.1 All API tests pass (45).
  - [x] 7.2 Web type-check passes.
  - [x] 7.3 Against the running stack: `/score` comparison (and in the audit row), `/simulate` matches local runs, 404/422 on bad ids, `/demo/load` clears dispatches and values hold across a jobs tick with feeds still live, `/demo/unfreeze`, demo endpoints 404 with `DEMO_MODE=false`. Headless-Chrome screenshots of the console and replay.
  - [x] 7.4 README: "Before vs. after" section with the modeled-figures caveat and security warning.

## Changes from the design

- **Outcome model.** The first version scored each patient with the offload predicted at dispatch. That's the same number Metropolis optimizes, so the comparison was circular. Outcomes are now measured at arrival, with ambulances queued ahead within `queue_window_min`, the same for every strategy.
- **Load counts.** Units count against an ED until `queue_window_min` after arrival (like the live system's en-route window), not only while driving. Without this, decisions ignored the queue that outcomes charged for.
- **Scripted events.** Hospital status changes partway through a scenario are supported (`events`), and tested. The bundled surge has none: every placement tried either produced no swaps or made Metropolis look better than an honest run would. The swap mechanism is shown with a crafted test and in the live console instead. The replay card says why swaps are 0.
