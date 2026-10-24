---
inclusion: fileMatch
fileMatchPattern: '**/{seed,connectors,models}.py|**/sample_hospitals.json|**/region-rules.yaml'
---

# Real Data Sources (Massachusetts)

Research as of Oct 2026. Verify before relying on it. Label every real field with its source and as-of date.

| Field | Source | Verdict |
| --- | --- | --- |
| Registry, lat/lon, has ED | MassGIS "Hospitals" (HOSPITALS_PT), public domain, quarterly; cross-check CMS Hospital General Information (public domain) | Usable now |
| `trauma_level` (adult/peds) | MassGIS `TRAUMA_LEVEL_ADULT/PEDS`; authoritative: DPH "Trauma Hospital Destinations" | Usable now |
| `stroke_level` | MassGIS `DPH_STROKE_TIER`; DPH Designated Stroke Service Facilities | Usable with work: MA tiers (ASR / Primary Stroke Service / endovascular) ≠ CSC/TSC/PSC/ASR; remap in rules |
| `pci_24_7` | No clean list; regional STEMI point-of-entry plans (MBEMSC, CMEMSC, DPH POE plans) | Curate by hand; medical director signs off |
| `burn_center` | MassGIS `BURN_UNIT` beds as proxy; verify with ABA directory | ABA terms forbid scraping/republishing; look up only |
| `labor_delivery` | MassGIS `MATERNAL` beds > 0; watch DPH essential-service closures | Usable now |
| `pediatric_ed` / `pediatric_only` | MassGIS pediatric beds + peds trauma level; `pediatric_only` set by hand | Usable with work |
| `historical_offload_min` | CMS Timely & Effective Care OP-18 (ED length of stay, lagged); DPH capacity dashboard (monthly ED visits, ambulance trips) | Proxy only: OP-18 is not offload time; scale as a relative index and label it |
| Offload/APOT | MATRIS (DPH OEMS ePCR) | Needs a data use agreement; keep simulated |
| Live occupancy, boarding, Code Black | DPH Code Black system (105 CMR 170.323); possibly Juvare EMResource Resource Status API (paid) | Needs agreement; keep simulated |
| Road network | Geofabrik MA extract (ODbL) | Usable; attribute "© OpenStreetMap contributors"; share-alike only if distributing a derived database |
| Map tiles | tile.openstreetmap.org | Demo only (OSMF tile policy); pilot needs a commercial/self-hosted provider |

Not used: HIFLD Open (shut down Aug 2025), HHS facility capacity (ended May 2024), AHA survey (paid).

## Massachusetts diversion

MA banned ambulance diversion on Jan 1, 2009. The only exception is **Code Black** (ED closed to all patients due to an internal emergency). "Code Help" is a hospital's internal surge plan, not a diversion. With real MA data, `on_diversion` means Code Black, and the D-1 diversion exemptions should be reviewed with the medical director. Destinations otherwise follow the closest appropriate ED or the DPH-approved point-of-entry plans.
