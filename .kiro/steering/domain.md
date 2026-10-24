---
inclusion: fileMatch
fileMatchPattern: '**/{scoring,rules,routing,repository,connectors,seed}.py|**/region-rules.yaml|**/sample_hospitals.json|**/test_scoring.py|**/api.ts|**/App.tsx'
---

# Domain Model & Scoring Reference

Load this when touching the scoring path, the rules file, routing, or the data. It captures the clinical/EMS domain so changes stay faithful to how the system is meant to behave.

## The ranking metric

**Minutes to definitive care = drive time + predicted ED offload delay.**

A hospital is only ranked if it (1) passes the capability filter for the incident category, (2) is not on diversion unless the category is diversion-exempt, and (3) has a drive time. Eligible hospitals sort by `(minutes_total, minutes_drive, hospital.id)`; the top `max_recommendations` (default 3) are `recommendations`, the rest are `ranked_lower`. Everything removed is `excluded` with a `rule_id` and a human reason.

## Incident categories (region-rules.yaml)

| id | rule_id | Requires | adult_only |
| --- | --- | --- | --- |
| `trauma` | T-1 | `trauma_level` in [1,2] | yes |
| `lvo` | S-1 | `stroke_level` in [CSC, TSC] (thrombectomy-capable) | yes |
| `stroke` | S-2 | `stroke_level` in [CSC, TSC, PSC, ASR] | yes |
| `stemi` | C-1 | `pci_24_7` true (24/7 cath lab) | yes |
| `burn` | B-1 | `burn_center` true | yes |
| `peds` | P-1 | `pediatric_ed` true | **no** |
| `ob` | O-1 | `labor_delivery` true | yes |
| `general` | G-1 | (none) | yes |

Pediatric-only facilities fail every `adult_only` category.

## Capability vocabulary

- **Trauma level** I–III (`trauma_level`); major trauma (`trauma`) needs Level I or II.
- **Stroke certification** `stroke_level`: CSC (comprehensive), TSC (thrombectomy-capable), PSC (primary), ASR (acute stroke ready). LVO (large-vessel occlusion) needs CSC/TSC.
- **STEMI** (ST-elevation MI) needs a 24/7 cath lab = `pci_24_7` (PCI, percutaneous coronary intervention).
- **Burn**, **L&D** (labor & delivery / OB), **pediatric ED** are boolean capabilities.

## Diversion (rule D-1)

A hospital `on_diversion` is not accepting ambulances, so it is excluded — **except** for time-critical categories in `diversion.exempt_categories` (trauma, lvo, stemi, burn), which still consider it and get `diversion_exempt=True`. Changing this set is a clinical-policy decision.

**Massachusetts:** ambulance diversion has been banned since Jan 1, 2009, except **Code Black** (ED closed to all patients by an internal emergency). With real MA data `on_diversion` means Code Black only; see `data-sources.md` and the README.

## Offload delay model (`predict_offload`)

ED offload delay = time to hand the patient off at the ED. This is what makes a congested-but-close hospital rank below a farther, emptier one.

A feed is **live** only if `feed == "live"` AND `status_updated_at` age ≤ `stale_after_minutes` (5 min).

- **Live:** `offload_baseline_min + per_occupancy_point_min * max(0, ed_occupancy_pct - occupancy_threshold_pct) + per_boarded_patient_min * boarding + per_unit_en_route_min * units_en_route`. Source `live_feed`.
- **Fallback** (no feed, or feed too old): `historical_offload_min + per_unit_en_route_min * units_en_route`. Source `historical_median`. If a live feed went stale, `stale=True` and `data_age_min` is reported.

All weights/thresholds come from `region-rules.yaml` `offload:` — never hardcode them.

## Units en route

Open (unarrived) dispatches to a hospital within `en_route_window_minutes` (90) count as `units_en_route` and add to predicted offload — the system anticipates its own recommendations crowding a hospital. `jobs` closes dispatches older than ~40 min (stand-in for real ePCR/CAD arrival times).

## Routing (`routing.py`)

- With `OSRM_URL` set: one OSRM `/table` matrix query (incident → hospitals), seconds→minutes × traffic `osrm_multiplier` + `handling_minutes`. Source `osrm`.
- Fallback (OSRM unset or failing): haversine km × `road_factor` (1.35) ÷ traffic `fallback_speed_kmh` × 60 + `handling_minutes`. Source `straight_line_estimate`.
- Traffic profiles: `light`, `normal`, `rush`.

## Auditability invariant

Every `/score` response carries `rules_version` and per-category `rule_id`, and is persisted to `score_audit` with the full request/response. Preserve this when editing the scoring path — it is the basis for medical-direction review and shadow-mode evaluation.

## Sample data landmarks (for tests/demos)

9 fictional greater-Boston hospitals. Notable: **lwc** Longwood Children's (pediatric-only), **seu** South End University (feed deliberately left stale to demo historical fallback), **bay** Bayside (on diversion). Keep these behaviors when editing `sample_hospitals.json` so demos and tests stay meaningful.

## Planned: specialization preference (not built)

Today capability is a hard pass/fail filter. "Specializes in the injury" (e.g. Level I over Level II trauma, CSC over TSC) would be a ranking term, not a filter. Express it in `region-rules.yaml` (e.g. a per-category minutes credit for preferred designations) so the medical director controls the trade-off between specialization and time, and cite it in the recommendation reason.

## Multi-patient ranker / swaps (rule R-1, built v1)

Code: `api/app/ranker.py` (`find_swaps`, pure), endpoints in `api/app/main.py` (`/ranker/*`), `repository.load_transports`, `jobs/connectors.move_simulated_units`. Spec: `.kiro/specs/destination-ranker/`. Parameters: `ranker:` in `region-rules.yaml`.

Invariants to keep:

- **Pure Pareto swaps are rare.** Each patient already gets their individual best, so a swap that helps *both* only appears when inputs changed after dispatch (offload/feed updates, en-route effects from later dispatches) or a crew didn't take the top pick. The common case — severe patient to the short wait, less severe to the long wait — helps one and costs the other. That needs an explicit **acuity weight** per patient and an objective such as minimize Σ weightᵢ × minutes_to_careᵢ.
- **Formulation.** v1 is a pairwise swap search with greedy non-conflicting selection. A full assignment solver (`scipy.optimize.linear_sum_assignment`) for 3+ unit re-assignments is out of scope for v1. Keep it a pure function next to `score()`.
- **Capability holds for both sides.** A swap is only valid if each patient's category passes at the new hospital (and diversion rules still apply).
- **Use remaining time, not original.** Drive time is from each unit's current position (`position_lat/lon`, simulated by `jobs` until CAD/AVL exists), falling back to the incident origin when stale, flagged `position_source`.
- **Hysteresis.** Only suggest when the gain exceeds a configurable threshold, and avoid re-swapping the same units, so crews aren't thrashed. Threshold and acuity weights belong in the YAML.
- **Suggest, don't auto-apply**, and audit every suggestion and its acceptance like `score_audit`.

## When changing policy

Prefer editing `region-rules.yaml` over code. On any change to it: **bump `version`**, add/adjust a case in `tests/test_scoring.py`, and remember the file is medical-director-owned and under code review. The sample protocol is `not a real protocol` — keep the "sample / not for clinical use" framing in any UI.
