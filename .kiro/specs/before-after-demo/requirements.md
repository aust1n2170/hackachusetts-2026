# Requirements: Before-and-after demo

## Introduction

Today a dispatcher or crew picks the destination from memory, protocol cards and radio calls about ED status. Metropolis removes that friction: one click sends the unit to the hospital that can treat the patient and gets them to care fastest. This feature makes the difference visible and measurable, for users and for a demo video:

1. **Comparison on every call:** what today's choice would be next to what Metropolis picks.
2. **Surge replay:** a scripted burst of calls run through today's strategies and through Metropolis, with metrics and a map replay.
3. **Demo mode:** a scripted, frozen hospital state so every demo run tells the same story.

All figures come from fictional hospitals and the modeled wait time. Every surface must say so.

### Glossary

- **Nearest capable:** the closest hospital by drive time that passes the category rule and diversion rule D-1. This is the fair "today" baseline: a good dispatcher following protocol.
- **Nearest ED:** the closest open ED of any kind (pediatric-only EDs excluded for adult categories). The weak baseline: going to the closest hospital without checking capability.
- **Metropolis:** the rank-1 recommendation from `score()`.
- **Metropolis + swaps:** Metropolis, plus ranker (R-1) swaps applied automatically after each dispatch (simulation only; in the live console a dispatcher still accepts each swap).
- **Minutes to care:** drive time + predicted ED offload, as in `/score`.
- **Secondary transfer penalty:** minutes added when a patient reaches a hospital that can't treat them, a scenario assumption.

## Requirements

### Requirement 1: Comparison on every call

**User story:** As a dispatcher, I want to see what the usual choice would cost, so that I trust the recommendation.

1. WHEN `/score` returns THE SYSTEM SHALL include `comparison.nearest_capable` and `comparison.nearest_ed`, each with hospital id, short name, drive, offload and total minutes, whether it can treat the category, and whether it is the same hospital as the rank-1 recommendation.
2. IF no hospital qualifies for a baseline THEN that baseline SHALL be null.
3. The comparison SHALL use the same drive times and offload model as the recommendations.
4. THE comparison SHALL be written to `score_audit` with the rest of the response.
5. THE console SHALL show above the recommendations: the nearest-capable choice and its minutes, the Metropolis choice and its minutes, and the minutes saved. When they're the same hospital it SHALL say Metropolis agrees.
6. WHEN the nearest ED can't treat the category THE console SHALL say so (for example, "no 24/7 cath lab").

### Requirement 2: Surge replay

**User story:** As a judge or regional EMS leader, I want to see how much time Metropolis saves across many calls, so that I understand the system-wide effect.

1. THE SYSTEM SHALL provide scripted scenarios in `api/data/scenarios/*.json`. Each has hospital statuses, a traffic profile, a secondary-transfer penalty, and a list of calls (id, minute offset, category, acuity, lat/lon, label).
2. THE SYSTEM SHALL provide `GET /simulate/scenarios` (id, title, description, call count) and `POST /simulate` (body: `scenario`) that runs the scenario through four strategies: nearest ED, nearest capable, Metropolis, Metropolis + swaps.
3. Simulation SHALL be a pure function with no database or network access, and SHALL give identical results for identical inputs.
4. Drive times SHALL use the straight-line estimate (`fallback_minutes`) so results don't depend on OSRM being available.
5. Units dispatched in the simulation SHALL count as en route at their hospital from dispatch until arrival (dispatch time + drive), so they raise that hospital's predicted offload for later calls.
6. Each patient's minutes to care SHALL be drive + offload at the destination at dispatch time, plus the secondary-transfer penalty if the destination can't treat the category.
7. For Metropolis + swaps, after each dispatch THE SYSTEM SHALL run `find_swaps` on units still en route, using positions interpolated along their route, and apply every returned swap. A swapped patient's minutes = elapsed time + remaining drive to the new hospital + offload there.
8. For each strategy THE SYSTEM SHALL report: mean and median minutes to care, mean for acuity-1 patients, total minutes, number of patients sent somewhere that can't treat them, peak units en route at one hospital (and which), swaps applied, and per-call destination and minutes.
9. THE response SHALL include each hospital's location and each call's location, so the console can replay it on a map.
10. IF the scenario id is unknown THEN THE SYSTEM SHALL return 404.

### Requirement 3: Surge replay in the console

1. THE console SHALL have a "Before vs. after" section with a scenario picker and a Run button.
2. Results SHALL show one card per strategy with the metrics in 2.8, and the minutes saved versus nearest capable.
3. THE console SHALL replay the scenario on a map for a chosen strategy: a call-by-call slider and a Play/Pause button. Lines go from each call to its destination; hospital markers grow with units en route at that moment.
4. THE console SHALL show a per-call table comparing nearest capable and Metropolis destinations and minutes.
5. Every result view SHALL be labeled "Simulation · fictional hospitals · modeled wait times".
6. Controls SHALL be keyboard-operable and labeled; results SHALL be announced via an `aria-live` region.

### Requirement 4: Demo mode

**User story:** As a presenter, I want the live console in a known state, so that every take of the demo shows the same story.

1. Demo endpoints SHALL exist only when `DEMO_MODE` is true (default false in code, true in `.env.example`); otherwise they return 404.
2. `POST /demo/load` (body: `scenario`) SHALL delete swap suggestions and dispatches, apply the scenario's hospital statuses, and freeze the simulated feed.
3. WHILE frozen THE simulated feed SHALL keep live hospitals' values unchanged and refresh their timestamps (so they stay live). Hospitals in `SIMULATE_STALE_IDS` SHALL still go stale.
4. `POST /demo/unfreeze` SHALL resume the random feed. `GET /demo` SHALL return `enabled`, `frozen` and `scenario`.
5. THE console SHALL show demo controls only when demo mode is enabled, and show whether feeds are frozen.
6. THE README SHALL warn that demo endpoints let anyone reset operational data, since the API has no authentication.

## Out of scope

- Real historical call data (needs MATRIS or CAD data under an agreement).
- ED occupancy rising as simulated patients arrive (the offload model stays as in `/score`).
- OSRM drive times in the simulation.
