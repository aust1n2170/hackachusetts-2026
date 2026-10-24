# Metropolis

EMS destination routing. Metropolis recommends the best hospital for an EMS transport. It filters hospitals by what they can treat for the incident category, then ranks the rest by **minutes to definitive care = drive time + predicted ED offload delay**.

> **Sample data only.** The nine Boston-area hospitals in `api/data/sample_hospitals.json` are fictional, and `api/rules/region-rules.yaml` is not a real protocol. Not for clinical use.

## Quick start

Requires Docker.

```bash
cp .env.example .env
docker compose up --build
```

- Web app: http://localhost:5173
- API docs (Swagger): http://localhost:8000/docs

The API seeds the sample hospitals on first start. The `jobs` service simulates live ED feeds every 60 seconds; South End University is left alone so its feed goes stale and you can see the historical fallback.

Without Docker: run Postgres with PostGIS yourself, then

```bash
cd api && pip install -r requirements-dev.txt
python -m app.seed && uvicorn app.main:app --reload     # API on :8000
python -m jobs.scheduler                                # feed jobs, in a second terminal
cd ../web && npm install && npm run dev                 # web on :5173
```

## Run the demo

About five minutes. Start the stack (Quick start above), open http://localhost:5173, and keep the `DEMO_MODE=true` line from `.env.example` in your `.env`.

1. **Load the demo state.** Click **Load demo scenario** in the bar under the header and confirm. This clears dispatches, sets every hospital to the scripted surge state, and freezes the simulated feed so the numbers below stay the same.
2. **One call, today vs. Metropolis.** Choose **Major trauma**, location **Kenmore Sq, Boston**, traffic **Normal**. The strip above the recommendations compares today's choice with Metropolis:
   - **Today (nearest capable):** Longwood General, 30 min to care. It's close but crowded.
   - **Metropolis:** Charles Valley, 16 min.

   Under **Not recommended**, each excluded hospital shows the rule it failed (for example, "No Level I or II trauma designation").
3. **A destination swap.** Keep Kenmore Sq.
   - Choose **STEMI** (acuity defaults to 1 · Critical) and click **Send Medic 1 here** on **Harborview Medical Center**, even though it's ranked third.
   - Choose **General medical**, set acuity to **3 · Urgent**, and send Medic 2 to **Charles Valley Hospital**.
   - **Suggested swaps** shows Medic 1 ⇄ Medic 2: the critical patient goes from 28 to 18 min, the urgent one from 18 to 28. Click **Accept swap**, and the dispatch log marks both units as swapped.
4. **Surge replay.** Scroll to **Before vs. after: surge replay**, or open http://localhost:5173/?replay=surge to run it on load. Select **Nearest capable (today)** and press **Play** to watch Longwood General fill up. Then select **Metropolis** and play again. The cards show the totals: 24.4 vs. 29.5 min average to care, critical patients 2.1 min faster, and no one sent to a hospital that can't treat them.
5. **Reset.** Click **Reload scenario** to start over, or **Unfreeze feeds** to return to the changing simulated feed.

All figures come from fictional hospitals and modeled ED waits, with straight-line drive times unless OSRM is set up. Details: [Before vs. after](#before-vs-after).

## Try it

```bash
curl -s -X POST localhost:8000/score -H 'content-type: application/json' \
  -d '{"category":"trauma","lat":42.3489,"lon":-71.0952,"traffic":"normal"}'
```

Categories: `trauma`, `lvo`, `stroke`, `stemi`, `burn`, `peds`, `ob`, `general`.

| Endpoint | What it does |
| --- | --- |
| `GET /rules` | Rules version and incident categories |
| `POST /score` | Ranked recommendations, ranked-lower and excluded hospitals with reasons; writes an audit row |
| `GET /hospitals` | Registry, live status and predicted offload for every hospital |
| `PUT /hospitals/{id}/status` | Update feed, ED occupancy, boarding or diversion (what real connectors will call) |
| `POST /dispatches` | Record a unit sent to a hospital (with `acuity` 1–4 and incident origin); counts as en route |
| `PUT /dispatches/{id}/position` | Update a unit's current position (what a CAD/AVL feed will call) |
| `POST /dispatches/{id}/arrive` | Close a dispatch |
| `GET /dispatches` | Recent dispatches |
| `POST /ranker/evaluate` | Suggest destination swaps between units in transport (rule R-1); writes suggestions |
| `POST /ranker/swaps/{id}/accept` | Re-validate and apply a swap to both dispatches |
| `POST /ranker/swaps/{id}/decline` | Decline a swap; it isn't suggested again |
| `GET /simulate/scenarios`, `POST /simulate` | Before-vs-after surge replay (modeled; writes nothing) |
| `GET /demo`, `POST /demo/load`, `POST /demo/unfreeze` | Demo mode, only when `DEMO_MODE=true` |

## Layout

```
api/
  app/
    main.py         FastAPI endpoints
    scoring.py      Pure scoring logic (capability filter, diversion, offload model, ranking)
    ranker.py       Pure multi-patient ranker: destination swaps between units (rule R-1)
    compare.py      Pure baselines: nearest capable hospital and nearest ED
    simulation.py   Pure surge replay: four strategies, one outcome model
    rules.py        Loads and validates rules/region-rules.yaml
    routing.py      OSRM drive times, straight-line fallback
    models.py       Postgres/PostGIS tables: hospitals, hospital_status, dispatches, score_audit, swap_suggestions
    repository.py   Queries hospitals within the search radius and counts units en route
    seed.py         Creates tables and loads the sample registry
  jobs/
    scheduler.py    Runs connectors every FEED_INTERVAL_SECONDS
    connectors.py   Simulated live feed + stubs for the regional diversion system and hospital partner feeds
  rules/region-rules.yaml   Protocol rules the medical director signs off
  data/sample_hospitals.json
  data/scenarios/surge.json     Scripted surge for the before-vs-after replay
  tests/                        test_scoring, test_ranker, test_compare, test_simulation
web/                React + Vite + Leaflet front end (calls the API through /api)
scripts/prepare-osrm.sh     Builds Massachusetts road routing data
docker-compose.yml
```

## How scoring works

1. **Capability filter.** Each category lists required hospital fields in `region-rules.yaml` (for example, trauma requires `trauma_level` in [1, 2]). Pediatric-only hospitals are excluded from adult categories.
2. **Diversion (rule D-1).** Hospitals on ED diversion are skipped, except for categories in `diversion.exempt_categories`.
3. **Drive time.** OSRM table query with a traffic multiplier, or a straight-line estimate when `OSRM_URL` is empty or OSRM is down.
4. **Offload delay.** With a live feed less than 5 minutes old: baseline + occupancy over 85% + boarding + units en route. Otherwise the historical median + units en route, flagged in the response.
5. **Rank** by total minutes and return the top 3 with reasons, data age and rule IDs. Every response is stored in `score_audit`.

Change weights, thresholds or categories in the YAML and restart the API. Bump `version` with every change.

## How the ranker works (rule R-1)

Each patient is scored on their own when the unit is sent. The ranker then looks across all units in transport and suggests **destination swaps**: units A→X and B→Y become A→Y and B→X.

1. **Inputs.** Each dispatch carries an acuity (1 critical … 4 non-urgent; defaults per category in `ranker.default_acuity`), the incident origin, and the unit's latest position. Positions older than `position_stale_after_min` fall back to the incident origin, flagged `position_source: incident_origin`.
2. **Validity.** Both patients must pass their category rule at the new hospital, and D-1 diversion still applies. A unit within `min_remaining_drive_min` of its destination is not redirected, and a unit swapped within `swap_cooldown_min` is left alone.
3. **Gain.** Minutes to care = remaining drive from the unit's position + the hospital's predicted offload. A swap doesn't change how many units are heading to each hospital, so offload is the same before and after. Weighted gain = Σ acuity weight × minutes before − after.
4. **Suggest.** Only if the weighted gain ≥ `min_weighted_gain_min` and neither patient gets more than `max_individual_loss_min` later. `pareto: true` marks swaps where nobody is worse off. Each unit appears in at most one suggestion, highest gain first.
5. **Decide.** Nothing changes until a dispatcher accepts. Accept re-checks the swap and updates both dispatches in one transaction. Every suggestion and outcome (pending / accepted / declined / expired) stays in `swap_suggestions`. Declined swaps aren't suggested again.

The `jobs` service moves simulated unit positions toward their destinations every tick, standing in for CAD/AVL.

`init_db()` adds the new `dispatches` columns to existing databases (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`) until Alembic replaces it.

## Before vs. after

Metropolis exists to take the hospital choice off the dispatcher's memory. Three features make the difference visible.

**On every call.** `/score` returns `comparison`: the nearest hospital that can treat the patient (today's protocol choice) and the nearest ED of any kind, scored with the same drive and wait model. The console shows "Today · nearest capable" next to the Metropolis pick and the minutes saved, and warns when the closest ED can't treat the patient.

**Surge replay.** "Before vs. after" in the console (or `POST /simulate {"scenario":"surge"}`) runs a scripted burst of calls (`api/data/scenarios/surge.json`: 16 calls in 15 minutes around Fenway and Longwood) through four strategies: nearest ED, nearest capable, Metropolis, and Metropolis + swaps. Every strategy is measured the same way at arrival: time on the road + ED offload wait, counting ambulances queued ahead of you, + a 45-minute transfer if the hospital can't treat the patient. Metropolis isn't graded on its own prediction. Open http://localhost:5173/?replay=surge to run it on load.

Current result: Metropolis averages 24.4 min to care versus 29.5 for nearest capable (5.1 min faster per patient, 81 patient-minutes over 16 calls), critical patients 2.1 min faster, and the busiest ED gets 7 ambulances instead of 9. Nearest ED sends 2 patients to hospitals that can't treat them. Some individual patients are slower under Metropolis; the per-call table shows them.

These are **modeled figures** on fictional hospitals with straight-line drive times. They show how the logic behaves, not real-world outcomes.

**Demo mode.** With `DEMO_MODE=true` (set in `.env.example`), a bar under the header loads the scenario's hospital state into the live console and freezes the simulated feed, so every demo take shows the same numbers. "Unfreeze feeds" resumes the random feed. API: `GET /demo`, `POST /demo/load {"scenario":"surge"}`, `POST /demo/unfreeze`.

> **Security:** `/demo/load` deletes all dispatches and swap suggestions, and the API has no authentication. Keep `DEMO_MODE=false` on any server others can reach.

## Ambulance diversion policy (Massachusetts)

Massachusetts has banned ambulance diversion statewide since **January 1, 2009**. The only exception is **Code Black**: the ED is closed to all patients because of an internal emergency (105 CMR 170.323; DPH policy DHCQ-15-04-635). "Code Help" is a hospital's internal surge plan and does not redirect ambulances.

The sample data and rule D-1 treat diversion as a routine status, which is not realistic for Massachusetts. With real data, `on_diversion` must mean Code Black only, and the medical director must review `diversion.exempt_categories`. Destinations otherwise follow the closest appropriate ED or the DPH-approved point-of-entry plans.

## Data sources

Everything in this repo is sample data today. These are the real sources to use, as researched in October 2026. Re-check licenses and freshness before relying on them, and record the source and as-of date for every real field.

| Field | Source | Status |
| --- | --- | --- |
| Hospitals, address, lat/lon, has ED | [MassGIS Hospitals](https://www.mass.gov/info-details/massgis-data-hospitals) (public domain, quarterly); cross-check [CMS Hospital General Information](https://data.cms.gov/provider-data/dataset/xubh-q36u) | Public, usable now |
| `trauma_level` | MassGIS `TRAUMA_LEVEL_ADULT/PEDS`; authoritative: [DPH Trauma Hospital Destinations](https://www.mass.gov/info-details/trauma-hospital-destinations) | Public, usable now |
| `stroke_level` | MassGIS `DPH_STROKE_TIER`; [DPH Designated Stroke Service Facilities](https://www.mass.gov/info-details/designated-stroke-service-facilities) | Public. MA tiers differ from CSC/TSC/PSC/ASR, so the rules need remapping |
| `pci_24_7` | Regional STEMI point-of-entry plans ([MBEMSC](https://www.mbemsc.org/point-of-entry), [CMEMSC](https://cmemsc.org/point-of-entry.php), [DPH POE plans](https://www.mass.gov/lists/dph-approved-statewide-point-of-entry-plans)) | No clean list; curate by hand, medical director signs off |
| `burn_center` | MassGIS `BURN_UNIT` beds; verify with [ABA Find a Burn Center](https://www.ameriburn.org/quality-care/find-a-burn-center) | ABA terms forbid scraping or republishing; look up only |
| `labor_delivery` | MassGIS `MATERNAL` beds; watch [DPH essential service closures](https://www.mass.gov/info-details/hospital-essential-service-closures) | Public, usable now |
| `pediatric_ed`, `pediatric_only` | MassGIS pediatric beds + pediatric trauma level; `pediatric_only` set by hand | Public, needs review |
| `historical_offload_min` | [CMS Timely and Effective Care](https://data.cms.gov/provider-data/dataset/yv7e-xc69) OP-18; [DPH Health care capacity dashboard](https://www.mass.gov/info-details/health-care-capacity-interactive-dashboard) | Public, but OP-18 is ED length of stay, not offload. Proxy only |
| Ambulance offload time (APOT) | [MATRIS](https://www.mass.gov/info-details/massachusetts-ambulance-trip-record-information-system-matris) (DPH OEMS ePCR data) | Not public; needs a data use agreement |
| Live occupancy, boarding, Code Black | DPH Code Black system; possibly the Juvare EMResource Resource Status API (paid) | Not public; needs agreements. Simulated here |
| Unit positions | CAD/AVL from the dispatch center | Not public; simulated here |
| Road network | [Geofabrik](https://download.geofabrik.de/) Massachusetts extract, used by OSRM | ODbL: credit "© OpenStreetMap contributors" |
| Map tiles | tile.openstreetmap.org | Development only ([OSMF tile policy](https://operations.osmfoundation.org/policies/tiles/)) |

Not used: HIFLD Open (shut down August 2025), HHS facility-level capacity data (ended May 2024), AHA Annual Survey (paid).

Agreements a pilot needs: MA DPH (OEMS for MATRIS, and access to the Code Black system), EMResource if MA uses it, each hospital for live ED feeds (BAA if patient data is involved), regional EMS councils for point-of-entry plans, the ABA if burn-center data is republished, and a commercial map-tile provider.

## Real road routing (optional)

```bash
make osrm                          # downloads Massachusetts from Geofabrik, ~10 minutes
echo "OSRM_URL=http://osrm:5000" >> .env
make up-routing
```

## Tests

```bash
make test        # or: cd api && python -m pytest -q
```

The scoring, ranker, baseline and simulation tests need no database or routing server.

## Before a real pilot

- [ ] Replace the sample registry with real designations (see "Data sources").
- [ ] Remap `on_diversion` to Massachusetts Code Black and have the medical director review the D-1 exemptions.
- [ ] Medical director reviews and signs `region-rules.yaml`; keep it under code review.
- [ ] Implement `RegionalDiversionFeed`, ePCR/CAD arrival times and CAD/AVL unit positions in `jobs/connectors.py`.
- [ ] Add sign-in (AWS Cognito or Auth0) and role checks on every endpoint; the API is open today.
- [ ] Switch `init_db()` to Alembic migrations.
- [ ] Use a map tile provider with a commercial plan; the public OpenStreetMap tile servers are for light development use only.
- [ ] Host on AWS HIPAA-eligible services under a signed BAA before any real EMS or hospital data arrives.
- [ ] Run in shadow mode (log recommendations, don't show them) and audit against actual destinations.
