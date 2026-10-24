import json
from datetime import datetime, timezone
from pathlib import Path

from app.rules import load_rules
from app.scoring import HospitalSnapshot
from app.simulation import STRATEGIES, Call, Event, apply_statuses, load_scenario, run_all, simulate

ROOT = Path(__file__).resolve().parent.parent
RULES = load_rules(ROOT / "rules" / "region-rules.yaml")
NOW = datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)


def hosp(id, lat, lon, **kw) -> HospitalSnapshot:
    base = dict(
        id=id, name=id, short_name=id, lat=lat, lon=lon,
        trauma_level=None, stroke_level=None, pci_24_7=False, burn_center=False,
        labor_delivery=False, pediatric_ed=False, pediatric_only=False,
        offload_baseline_min=5, historical_offload_min=10,
        feed="live", ed_occupancy_pct=85, boarding=0, on_diversion=False,
        status_updated_at=NOW, units_en_route=0,
    )
    base.update(kw)
    return HospitalSnapshot(**base)


def registry() -> list[HospitalSnapshot]:
    fields = ("name short_name lat lon trauma_level stroke_level pci_24_7 burn_center labor_delivery "
              "pediatric_ed pediatric_only offload_baseline_min historical_offload_min").split()
    rows = json.loads((ROOT / "data" / "sample_hospitals.json").read_text())["hospitals"]
    return [
        HospitalSnapshot(id=r["id"], **{f: r[f] for f in fields}, feed="none", ed_occupancy_pct=None,
                         boarding=None, on_diversion=False, status_updated_at=None)
        for r in rows
    ]


def run(calls, hs, strategy, **kw):
    return simulate(calls, hs, RULES, strategy, "normal", 45, NOW, **kw)


def test_deterministic():
    sc = load_scenario(ROOT / "data" / "scenarios" / "surge.json")
    a = run_all(sc, registry(), RULES, NOW)
    b = run_all(sc, registry(), RULES, NOW)
    assert [r.metrics for r in a] == [r.metrics for r in b]
    assert [[x.hospital_id for x in r.assignments] for r in a] == [[x.hospital_id for x in r.assignments] for r in b]


def test_bundled_surge_assigns_every_call_and_metropolis_beats_today():
    sc = load_scenario(ROOT / "data" / "scenarios" / "surge.json")
    res = {r.strategy: r for r in run_all(sc, registry(), RULES, NOW)}
    assert set(res) == set(STRATEGIES)
    for r in res.values():
        assert r.metrics.assigned == len(sc.calls) and r.metrics.unassigned == 0
        assert len(r.timeline) == len(sc.calls)
    m, today, ed = res["metropolis"].metrics, res["nearest_capable"].metrics, res["nearest_ed"].metrics
    assert m.mean < today.mean < ed.mean
    assert m.acuity1_mean < today.acuity1_mean
    assert m.not_capable == today.not_capable == 0 and ed.not_capable > 0


def test_pile_on_raises_offload_for_later_ambulances():
    # Five calls at the same corner, all nearest to "a"; each arrival queues behind the earlier ones.
    hs = [hosp("a", 42.36, -71.06), hosp("b", 42.30, -71.20)]
    calls = [Call(i, i * 0.5, "general", 3, 42.355, -71.065) for i in range(5)]
    r = run(calls, hs, "nearest_capable")
    assert [x.hospital_id for x in r.assignments] == ["a"] * 5
    offloads = [x.offload_at_arrival for x in r.assignments]
    assert offloads == sorted(offloads) and offloads[-1] > offloads[0]
    assert r.metrics.peak_en_route == 5 and r.metrics.peak_hospital == "a"


def test_queue_window_releases_earlier_arrivals():
    hs = [hosp("a", 42.36, -71.06)]
    calls = [Call(1, 0, "general", 3, 42.355, -71.065), Call(2, 60, "general", 3, 42.355, -71.065)]
    r = run(calls, hs, "nearest_capable", queue_window_min=15)
    assert r.assignments[0].offload_at_arrival == r.assignments[1].offload_at_arrival


def test_penalty_when_hospital_cant_treat():
    hs = [hosp("near", 42.36, -71.06), hosp("cath", 42.40, -71.12, pci_24_7=True)]
    call = [Call(1, 0, "stemi", 1, 42.355, -71.065)]
    ed, cap = run(call, hs, "nearest_ed"), run(call, hs, "nearest_capable")
    assert ed.assignments[0].hospital_id == "near" and not ed.assignments[0].capable
    assert cap.assignments[0].hospital_id == "cath" and cap.assignments[0].capable
    near = ed.assignments[0]
    assert near.minutes_to_care == round((near.arrive_at - near.t_dispatch) + near.offload_at_arrival + 45)
    assert ed.metrics.not_capable == 1


def test_metropolis_avoids_crowded_ed():
    hs = [hosp("crowded", 42.36, -71.06, ed_occupancy_pct=130, boarding=20), hosp("calm", 42.37, -71.09)]
    call = [Call(1, 0, "general", 3, 42.358, -71.065)]
    assert run(call, hs, "nearest_capable").assignments[0].hospital_id == "crowded"
    m = run(call, hs, "metropolis")
    assert m.assignments[0].hospital_id == "calm"
    assert m.metrics.mean < run(call, hs, "nearest_capable").metrics.mean


def test_event_changes_hospital_state_midway():
    hs = [hosp("a", 42.36, -71.06), hosp("b", 42.37, -71.09)]
    calls = [Call(1, 0, "general", 3, 42.358, -71.065), Call(2, 10, "general", 3, 42.358, -71.065)]
    ev = [Event(5, "a", {"ed_occupancy_pct": 140, "boarding": 20})]
    r = run(calls, hs, "metropolis", events=ev)
    assert [x.hospital_id for x in r.assignments] == ["a", "b"]


def test_swaps_strategy_applies_ranker_swap_when_conditions_change():
    # Mild patient heads to x, critical to y; y then gets more crowded while both are on the road.
    hs = [hosp("x", 42.40, -71.10), hosp("y", 42.40, -71.00), hosp("z", 42.20, -71.30)]
    calls = [Call(1, 0, "general", 4, 42.30, -71.055), Call(2, 0.5, "general", 1, 42.30, -71.045),
             Call(3, 1, "general", 3, 42.20, -71.30)]
    ev = [Event(1, "y", {"ed_occupancy_pct": 100})]
    plain = run(calls, hs, "metropolis", events=ev)
    swapped = run(calls, hs, "metropolis_swaps", events=ev)
    assert swapped.metrics.swaps == 1
    by_id = {a.call.id: a for a in swapped.assignments}
    assert by_id[1].swapped and by_id[2].swapped
    assert (by_id[1].first_hospital_id, by_id[1].hospital_id) == ("x", "y")
    crit_before = next(a for a in plain.assignments if a.call.id == 2).minutes_to_care
    assert by_id[2].minutes_to_care < crit_before


def test_apply_statuses_uses_scenario_not_live_state():
    live = [hosp("a", 42.36, -71.06, ed_occupancy_pct=50, on_diversion=True)]
    [h] = apply_statuses(live, {"a": {"feed": "live", "ed_occupancy_pct": 120, "boarding": 3, "age_minutes": 18}}, NOW)
    assert (h.ed_occupancy_pct, h.boarding, h.on_diversion) == (120, 3, False)
    assert (NOW - h.status_updated_at).total_seconds() == 18 * 60
    [n] = apply_statuses(live, {}, NOW)
    assert n.feed == "none" and n.status_updated_at is None
