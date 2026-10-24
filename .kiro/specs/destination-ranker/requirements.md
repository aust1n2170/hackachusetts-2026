# Requirements: Destination Ranker (multi-patient swaps)

## Introduction

Today every patient is scored on their own when the crew dispatches (`POST /score` → `POST /dispatches`). The ranker looks at all patients currently in transport and suggests **destination swaps** between two units when trading hospitals improves outcomes, weighted by severity. For example, a less severe patient headed to a short-wait ED and a more severe patient headed to a long-wait ED could trade.

The ranker is decision support. It suggests swaps; a dispatcher or crew accepts or declines them. Every suggestion and decision is audited. This remains a proof of concept with fictional data, not for clinical use.

### Glossary

- **Patient in transport**: an open dispatch (`arrived_at` is null) within the en-route window.
- **Acuity**: a per-patient severity level, 1 (critical) to 4 (non-urgent), set by the crew. Each level maps to a weight in `region-rules.yaml`.
- **Minutes to care**: remaining drive time from the unit's current position to a hospital, plus that hospital's predicted offload delay.
- **Weighted cost**: acuity weight × minutes to care.
- **Swap**: two dispatches A→X and B→Y become A→Y and B→X.
- **Pareto swap**: neither patient is worse off and at least one is better off.

## Requirements

### Requirement 1: Patient acuity

**User story:** As an EMS crew member, I want to record how severe my patient is, so that the ranker can prioritize sicker patients.

1. WHEN a dispatch is created THE SYSTEM SHALL accept an optional `acuity` integer from 1 to 4.
2. IF `acuity` is omitted THEN THE SYSTEM SHALL use the category's `default_acuity` from `region-rules.yaml`.
3. IF `acuity` is outside 1–4 THEN THE SYSTEM SHALL reject the request with HTTP 422.
4. THE SYSTEM SHALL store acuity on the dispatch and return it in `DispatchOut`.
5. WHEN the rules load THE SYSTEM SHALL fail if `ranker.default_acuity` doesn't cover every category or `ranker.acuity_weights` doesn't cover levels 1–4.

### Requirement 2: Unit position

**User story:** As a dispatcher, I want each unit's current position known, so that swaps compare the *remaining* drive, not the original one.

1. THE SYSTEM SHALL store the incident origin (lat/lon) on each dispatch when it is created.
2. THE SYSTEM SHALL provide `PUT /dispatches/{id}/position` to update a unit's current lat/lon and set `position_updated_at` to now (UTC).
3. IF the dispatch does not exist THEN THE SYSTEM SHALL return 404; IF it has arrived THEN THE SYSTEM SHALL return 409.
4. IF no position update exists, or the latest is older than `ranker.position_stale_after_min`, THEN THE SYSTEM SHALL use the incident origin and flag the suggestion `position_source: "incident_origin"`. Otherwise it SHALL use `"live"`.
5. WHERE simulated feeds are enabled THE `jobs` service SHALL move each open dispatch's position toward its destination every tick, so the demo has live positions.

### Requirement 3: Swap evaluation

**User story:** As a dispatcher, I want the system to find destination swaps that improve severity-weighted time to care, so that the sickest patients reach care fastest without unduly harming others.

1. WHEN the ranker is evaluated THE SYSTEM SHALL consider every unordered pair of patients in transport whose destinations differ.
2. A swap SHALL be valid only if each patient's category passes the capability filter at the new hospital (same `CategoryRule.evaluate` used by `/score`).
3. A swap SHALL be invalid if the new hospital is on diversion and the patient's category is not diversion-exempt (rule D-1).
4. A swap SHALL be invalid if either unit's remaining drive to its current destination is below `ranker.min_remaining_drive_min`.
5. A swap SHALL be invalid if either dispatch was swapped within `ranker.swap_cooldown_min`.
6. A swap SHALL be invalid if a drive time is missing for either patient to the new hospital.
7. THE SYSTEM SHALL compute weighted gain = (wA·tA,X + wB·tB,Y) − (wA·tA,Y + wB·tB,X).
8. THE SYSTEM SHALL suggest a swap only if weighted gain ≥ `ranker.min_weighted_gain_min` AND neither patient's minutes to care increases by more than `ranker.max_individual_loss_min`.
9. THE SYSTEM SHALL mark each suggestion `pareto: true` when neither patient is worse off.
10. WHEN one dispatch appears in more than one valid swap THE SYSTEM SHALL keep only the highest-gain swap for that dispatch (greedy, highest gain first), so suggestions never conflict.
11. THE SYSTEM SHALL return suggestions ordered by weighted gain, descending, ties broken by dispatch ids.
12. Offload delay SHALL come from the existing `predict_offload`, with the same live/stale/historical behavior and flags.

### Requirement 4: Suggestions, decisions and audit

**User story:** As a medical director, I want every swap suggestion and decision recorded, so that I can review how the ranker affected destinations.

1. THE SYSTEM SHALL provide `POST /ranker/evaluate` (body: `traffic`) that runs the evaluation and returns current pending suggestions.
2. IF an identical pending suggestion exists (same pair, same new destinations) THEN THE SYSTEM SHALL return it instead of creating a duplicate.
3. WHEN evaluation runs THE SYSTEM SHALL mark pending suggestions that are no longer valid as `expired`.
4. Each suggestion SHALL persist: rules version, rule_id `R-1`, both dispatch ids, from/to hospitals, minutes to care before/after for each patient, acuities, weighted gain, `pareto`, `position_source` per unit, `routing_source`, status (`pending | accepted | declined | expired`), and timestamps.
5. THE SYSTEM SHALL provide `POST /ranker/swaps/{id}/accept`, which, after re-validating, updates both dispatches' `hospital_id`, sets `last_swapped_at`, and marks the suggestion `accepted`, all in one transaction.
6. IF on accept the suggestion is not pending, either dispatch has arrived, or either destination changed since the suggestion THEN THE SYSTEM SHALL return 409 and change nothing.
7. THE SYSTEM SHALL provide `POST /ranker/swaps/{id}/decline`, which marks the suggestion `declined`.
8. THE SYSTEM SHALL never apply a swap without an explicit accept.

### Requirement 5: Dispatcher console

**User story:** As a dispatcher, I want to see and act on swap suggestions in the console.

1. THE console SHALL let the crew pick acuity (defaulting to the category default) before sending a unit.
2. THE console SHALL show pending swaps with both units, from→to hospitals, minutes before→after per patient, weighted gain, a Pareto badge, and a warning chip when a position or offload figure is estimated or stale.
3. THE console SHALL provide Accept and Decline buttons and refresh after either.
4. THE console SHALL keep the "sample data · not for clinical use" framing.
5. Controls SHALL be keyboard-operable and labeled for screen readers.

### Requirement 6: Policy in the protocol file

1. All ranker parameters (weights, default acuity, thresholds, cooldown, staleness) SHALL live in a `ranker:` section of `region-rules.yaml`, and the file `version` SHALL be bumped.
2. The swap logic SHALL be a pure function in `api/app/` with no database or network access, tested in `api/tests/`.

## Out of scope (v1)

- Three-way or larger re-assignments (a full assignment solver such as `linear_sum_assignment`).
- Rerouting a single unit to a better hospital without a swap partner.
- Acuity-dependent offload (EDs triage sicker patients first).
- Notifying hospitals of a destination change (pre-alert), and real CAD/AVL unit positions.
- Authentication: the API stays open, as in the rest of the POC.
