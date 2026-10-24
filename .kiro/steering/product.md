# Product

## What this is

The **EMS Destination Scorer** recommends the best hospital for an EMS (ambulance) transport. Given an incident location and a clinical category, it:

1. **Filters** hospitals by what they can actually treat for that category (capability filter).
2. **Ranks** the survivors by **minutes to definitive care = drive time + predicted ED offload delay**.

It is decision support for EMS dispatchers and regional hospital systems — the goal is to get the right patient to the right hospital in the least time, accounting for both how far away a hospital is and how backed up its emergency department is.

The frontend presents this as the "Boston Destination Scorer" dispatcher console: pick an incident type and location, see ranked recommendations on a map, and dispatch a unit.

## End-to-end workflow (target)

The platform serves three parties: dispatch, EMS crews, and hospitals.

1. **Call intake.** A caller reaches dispatch; the dispatcher records call notes in the system.
2. **Notes to crew.** The notes are sent to the assigned EMS crew en route to the scene.
3. **On-scene assessment.** The crew makes a live field assessment and uses the tool to set the nature of the injury/illness (incident category and, in future, severity).
4. **Scoring.** For that patient and location, each hospital gets a score from drive time, the nature of the injury, and live hospital data: wait/offload time, whether the hospital can treat that injury at all, and whether it specializes in it.
5. **Destination + routing.** The best hospital is chosen and the unit is routed there (recorded as a dispatch, counted as "en route" at that hospital).
6. **Ranker (multi-patient).** Across all patients currently in transport, the system looks for destination swaps: if two units would do better by trading destinations (e.g. a less severe patient headed to a short-wait ED while a more severe patient is headed to a long-wait ED), it suggests the swap.

### What exists today vs. planned

| Step | Status in the POC |
| --- | --- |
| 1–2 Dispatcher notes → crew | **Not built.** No notes/incident/patient entity; would carry PHI. |
| 3 On-scene injury selection | **Partial.** Crew picks one of 8 categories and an acuity (1–4); no notes or structured assessment. |
| 4 Score: capability + drive + wait | **Built.** Capability filter, diversion, drive time, offload model. |
| 4 Score: specialization | **Not built.** Capability is pass/fail; a Level I and Level II trauma center rank only on minutes. |
| 5 Choose + dispatch | **Built.** `POST /dispatches`; en-route units raise that hospital's predicted offload. |
| 6 Ranker / swaps | **Built (v1).** Pairwise swaps by severity-weighted minutes (rule R-1), suggest-then-accept, audited in `swap_suggestions`. Unit positions are simulated. |

## Who it is for

- **EMS dispatchers / field crews** — choosing a transport destination in real time.
- **Regional EMS medical directors** — who own the routing protocol (`region-rules.yaml`) and review the decision audit trail.
- **Hospital systems** — who feed live ED status (occupancy, boarding, diversion).

## Status: proof of concept

This is a **demo / proof of concept**, not a production clinical system.

- All nine Boston-area hospitals in `api/data/sample_hospitals.json` are **fictional**.
- `api/rules/region-rules.yaml` is a **sample protocol**, not a real medical protocol.
- The system is **not for clinical use**. Every surface that shows recommendations must keep the "sample data / not for clinical use" framing.

## Core value propositions

- **Capability-aware**: never recommends a hospital that cannot treat the patient (e.g. a non-trauma center for major trauma).
- **Congestion-aware**: the closest hospital is not always the best — a crowded ED's offload delay can make a farther hospital faster to definitive care.
- **Graceful degradation**: keeps producing answers even when the routing engine (OSRM) or a hospital's live feed is unavailable, falling back to straight-line estimates and historical medians, and flagging when it does so.
- **Auditable**: every recommendation cites the rules version and a rule ID, and is written to a `score_audit` trail for medical-direction review.

## Non-goals (for the POC)

- Authentication / authorization (the API is intentionally open in the POC).
- Real hospital registries, signed protocols, or live hospital feeds.
- HIPAA-compliant hosting or handling of real patient data.

These are tracked in the README "Before a real pilot" checklist and must not be assumed to exist.

## The road to a pilot

When extending this toward production, respect the README checklist: real designations, a medical-director-signed protocol under code review, real connectors (`RegionalDiversionFeed`, ePCR/CAD arrivals), auth + role checks on every endpoint, Alembic migrations, a commercial map-tile plan, AWS HIPAA-eligible hosting under a BAA, and shadow-mode piloting before anything is shown to crews.
