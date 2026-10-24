from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.ranker import Transport, find_swaps
from app.rules import load_rules
from app.scoring import HospitalSnapshot

RULES_PATH = Path(__file__).resolve().parent.parent / "rules" / "region-rules.yaml"
RULES = load_rules(RULES_PATH)
NOW = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)


def hosp(id, **kw) -> HospitalSnapshot:
    # feed="none" so offload is just historical_offload_min (deterministic)
    base = dict(
        id=id, name=id.upper(), short_name=id, lat=42.35, lon=-71.06,
        trauma_level=None, stroke_level=None, pci_24_7=False, burn_center=False,
        labor_delivery=False, pediatric_ed=False, pediatric_only=False,
        offload_baseline_min=5, historical_offload_min=10,
        feed="none", ed_occupancy_pct=None, boarding=None, on_diversion=False,
        status_updated_at=None, units_en_route=0,
    )
    base.update(kw)
    return HospitalSnapshot(**base)


def unit(id, hospital_id, acuity=3, category="general", **kw) -> Transport:
    return Transport(dispatch_id=id, unit=f"Medic {id}", category=category, acuity=acuity,
                     hospital_id=hospital_id, position_source="live", **kw)


def hs(*hospitals):
    return {h.id: h for h in hospitals}


# Base scenario: X is a short wait (5), Y a long wait (15); every drive is 10 min.
# Severe patient 1 heads to Y, mild patient 2 heads to X.
#   before: t1 = 10+15 = 25, t2 = 10+5 = 15     after: t1 = 15, t2 = 25
#   weighted gain = (4*25 + 1*15) - (4*15 + 1*25) = 30
X, Y = hosp("x", historical_offload_min=5), hosp("y", historical_offload_min=15)
DRIVE = {1: {"x": 10, "y": 10}, 2: {"x": 10, "y": 10}}


def test_rules_load_ranker_section():
    rk = RULES.ranker
    assert rk.rule_id == "R-1"
    assert set(rk.acuity_weights) == {1, 2, 3, 4}
    assert set(rk.default_acuity) == set(RULES.categories)


def test_severity_weighted_swap_is_suggested():
    [s] = find_swaps([unit(1, "y", acuity=1), unit(2, "x", acuity=3)], hs(X, Y), DRIVE, RULES, NOW)
    assert s.weighted_gain == 30 and s.rule_id == "R-1" and not s.pareto
    assert (s.a.dispatch_id, s.a.from_hospital, s.a.to_hospital, s.a.minutes_before, s.a.minutes_after) == (1, "y", "x", 25, 15)
    assert (s.b.dispatch_id, s.b.from_hospital, s.b.to_hospital, s.b.minutes_before, s.b.minutes_after) == (2, "x", "y", 15, 25)
    assert s.a.offload_after.minutes == 5 and s.b.offload_after.minutes == 15


def test_pareto_swap_is_flagged():
    # Each unit is closer to the other's destination; equal waits.
    drive = {1: {"x": 20, "y": 8}, 2: {"x": 8, "y": 20}}
    [s] = find_swaps([unit(1, "x"), unit(2, "y")], hs(hosp("x"), hosp("y")), drive, RULES, NOW)
    assert s.pareto and s.weighted_gain == 24  # 1*12 + 1*12


def test_equal_acuity_trade_off_has_no_gain():
    assert find_swaps([unit(1, "y"), unit(2, "x")], hs(X, Y), DRIVE, RULES, NOW) == []


def test_individual_loss_cap_blocks_swap():
    y = hosp("y", historical_offload_min=20)  # patient 2 would lose 15 min > cap of 10
    assert find_swaps([unit(1, "y", acuity=1), unit(2, "x")], hs(X, y), DRIVE, RULES, NOW) == []


def test_capability_must_hold_at_new_hospital():
    x, y = hosp("x", historical_offload_min=5), hosp("y", historical_offload_min=15, pci_24_7=True)
    # STEMI patient at Y (cath lab) would be swapped to X (no cath lab)
    swaps = find_swaps([unit(1, "y", acuity=1, category="stemi"), unit(2, "x")], hs(x, y), DRIVE, RULES, NOW)
    assert swaps == []
    x.pci_24_7 = True
    assert len(find_swaps([unit(1, "y", acuity=1, category="stemi"), unit(2, "x")], hs(x, y), DRIVE, RULES, NOW)) == 1


def test_pediatric_only_cannot_receive_adult():
    x = hosp("x", historical_offload_min=5, pediatric_ed=True, pediatric_only=True)
    y = hosp("y", historical_offload_min=15, pediatric_ed=True)
    assert find_swaps([unit(1, "y", acuity=1), unit(2, "x", category="peds")], hs(x, y), DRIVE, RULES, NOW) == []


def test_diversion_blocks_non_exempt_but_not_exempt():
    x = hosp("x", historical_offload_min=5, on_diversion=True, trauma_level=1)
    y = hosp("y", historical_offload_min=15, trauma_level=1)
    general = [unit(1, "y", acuity=1), unit(2, "x", category="trauma")]
    assert find_swaps(general, hs(x, y), DRIVE, RULES, NOW) == []
    trauma = [unit(1, "y", acuity=1, category="trauma"), unit(2, "x", category="trauma")]
    assert len(find_swaps(trauma, hs(x, y), DRIVE, RULES, NOW)) == 1


def test_unit_close_to_arrival_is_not_redirected():
    drive = {1: {"x": 10, "y": 3}, 2: {"x": 10, "y": 10}}  # unit 1 is 3 min from Y (< 4)
    assert find_swaps([unit(1, "y", acuity=1), unit(2, "x")], hs(X, Y), drive, RULES, NOW) == []


def test_cooldown_after_recent_swap():
    recent = [unit(1, "y", acuity=1, last_swapped_at=NOW - timedelta(minutes=5)), unit(2, "x")]
    assert find_swaps(recent, hs(X, Y), DRIVE, RULES, NOW) == []
    old = [unit(1, "y", acuity=1, last_swapped_at=NOW - timedelta(minutes=20)), unit(2, "x")]
    assert len(find_swaps(old, hs(X, Y), DRIVE, RULES, NOW)) == 1


def test_missing_drive_time_skips_pair():
    drive = {1: {"y": 10}, 2: {"x": 10, "y": 10}}
    assert find_swaps([unit(1, "y", acuity=1), unit(2, "x")], hs(X, Y), drive, RULES, NOW) == []


def test_same_destination_is_not_a_swap():
    assert find_swaps([unit(1, "x", acuity=1), unit(2, "x")], hs(X, Y), DRIVE, RULES, NOW) == []


def test_conflicting_swaps_keep_highest_gain():
    # Units 2 and 3 both head to X; unit 3 is a bit closer to Y, so swapping 1<->3 gains more (31 vs 30).
    drive = {**DRIVE, 3: {"x": 10, "y": 9}}
    swaps = find_swaps([unit(1, "y", acuity=1), unit(2, "x"), unit(3, "x")], hs(X, Y), drive, RULES, NOW)
    assert [(s.a.dispatch_id, s.b.dispatch_id, s.weighted_gain) for s in swaps] == [(1, 3, 31)]


def test_ties_are_deterministic_and_non_overlapping():
    drive = {i: {"x": 10, "y": 10} for i in (1, 2, 3, 4)}
    ts = [unit(1, "y", acuity=1), unit(2, "x"), unit(3, "y", acuity=1), unit(4, "x")]
    swaps = find_swaps(ts, hs(X, Y), drive, RULES, NOW)
    assert [(s.a.dispatch_id, s.b.dispatch_id) for s in swaps] == [(1, 2), (3, 4)]


def test_declined_swap_is_not_suggested_again():
    ts = [unit(1, "y", acuity=1), unit(2, "x")]
    assert find_swaps(ts, hs(X, Y), DRIVE, RULES, NOW, declined={(1, 2, "x", "y")}) == []


def test_missing_acuity_weight_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(RULES_PATH.read_text().replace(", 4: 0.5 }", " }"))
    with pytest.raises(ValueError, match="acuity_weights"):
        load_rules(p)


def test_missing_default_acuity_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(RULES_PATH.read_text().replace(", general: 3 }", " }"))
    with pytest.raises(ValueError, match="default_acuity"):
        load_rules(p)
