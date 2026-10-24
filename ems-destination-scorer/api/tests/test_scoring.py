from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.routing import fallback_minutes, haversine_km
from app.rules import load_rules
from app.scoring import HospitalSnapshot, predict_offload, score

RULES = load_rules(Path(__file__).resolve().parent.parent / "rules" / "region-rules.yaml")
NOW = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)


def hosp(id, **kw) -> HospitalSnapshot:
    base = dict(
        id=id, name=id.upper(), short_name=id, lat=42.35, lon=-71.06,
        trauma_level=None, stroke_level=None, pci_24_7=False, burn_center=False,
        labor_delivery=False, pediatric_ed=False, pediatric_only=False,
        offload_baseline_min=5, historical_offload_min=10,
        feed="live", ed_occupancy_pct=85, boarding=0, on_diversion=False,
        status_updated_at=NOW - timedelta(minutes=1), units_en_route=0,
    )
    base.update(kw)
    return HospitalSnapshot(**base)


def test_rules_file_loads_every_category():
    assert set(RULES.categories) == {"trauma", "lvo", "stroke", "stemi", "burn", "peds", "ob", "general"}
    assert RULES.diversion_exempt == {"trauma", "lvo", "stemi", "burn"}


def test_capability_filter_and_reasons():
    hs = [hosp("a", trauma_level=1), hosp("b", trauma_level=3), hosp("kids", pediatric_ed=True, pediatric_only=True, trauma_level=1)]
    r = score(hs, "trauma", {"a": 10, "b": 5, "kids": 3}, RULES, NOW)
    assert [x.hospital.id for x in r.recommendations] == ["a"]
    assert r.recommendations[0].capability == "Level I trauma center"
    reasons = {x.hospital.id: x.reason for x in r.excluded}
    assert reasons == {"b": "No Level I or II trauma designation", "kids": "Pediatric-only facility"}


def test_pediatric_category_includes_childrens_hospital():
    hs = [hosp("kids", pediatric_ed=True, pediatric_only=True), hosp("adult")]
    r = score(hs, "peds", {"kids": 8, "adult": 4}, RULES, NOW)
    assert [x.hospital.id for x in r.recommendations] == ["kids"]


def test_diversion_excludes_general_but_not_time_critical():
    h = hosp("a", trauma_level=1, on_diversion=True)
    general = score([h], "general", {"a": 5}, RULES, NOW)
    assert general.recommendations == [] and general.excluded[0].rule_id == "D-1"
    trauma = score([h], "trauma", {"a": 5}, RULES, NOW)
    assert trauma.recommendations[0].diversion_exempt is True


def test_crowding_can_beat_proximity():
    near = hosp("near", stroke_level="CSC", ed_occupancy_pct=125, boarding=15)
    far = hosp("far", stroke_level="TSC", ed_occupancy_pct=80, boarding=1)
    r = score([near, far], "lvo", {"near": 4, "far": 12}, RULES, NOW)
    assert [x.hospital.id for x in r.recommendations] == ["far", "near"]
    top = r.recommendations[0]
    assert top.minutes_total == top.minutes_drive + top.minutes_offload


def test_live_offload_formula():
    h = hosp("a", offload_baseline_min=6, ed_occupancy_pct=105, boarding=10, units_en_route=2)
    off = predict_offload(h, RULES, NOW)
    # 6 + 0.35*20 + 0.4*10 + 2*2 = 21
    assert off.minutes == 21 and off.source == "live_feed" and not off.stale


def test_stale_feed_falls_back_to_historical_and_is_flagged():
    h = hosp("a", historical_offload_min=12, units_en_route=1, status_updated_at=NOW - timedelta(minutes=18))
    off = predict_offload(h, RULES, NOW)
    assert off.source == "historical_median" and off.stale and off.minutes == 14 and off.data_age_min == 18.0


def test_no_feed_uses_historical_not_flagged_stale():
    off = predict_offload(hosp("a", feed="none", historical_offload_min=9), RULES, NOW)
    assert off.source == "historical_median" and not off.stale and off.minutes == 9


def test_units_en_route_push_a_hospital_down():
    a = hosp("a", pci_24_7=True)
    b = hosp("b", pci_24_7=True)
    first = score([a, b], "stemi", {"a": 8, "b": 9}, RULES, NOW).recommendations[0].hospital.id
    assert first == "a"
    a.units_en_route = 2
    assert score([a, b], "stemi", {"a": 8, "b": 9}, RULES, NOW).recommendations[0].hospital.id == "b"


def test_top_n_and_ranked_lower():
    hs = [hosp(f"h{i}") for i in range(5)]
    r = score(hs, "general", {f"h{i}": 5 + i for i in range(5)}, RULES, NOW)
    assert [x.rank for x in r.recommendations] == [1, 2, 3]
    assert [x.rank for x in r.ranked_lower] == [4, 5]


def test_missing_route_is_excluded():
    r = score([hosp("a")], "general", {}, RULES, NOW)
    assert r.excluded[0].reason == "No route found"


def test_fallback_routing_is_plausible():
    # Kenmore Sq to downtown, about 3 km straight line
    km = haversine_km(42.3489, -71.0952, 42.3601, -71.0589)
    assert 2.5 < km < 3.6
    mins = fallback_minutes(42.3489, -71.0952, [hosp("d", lat=42.3601, lon=-71.0589)], "normal", RULES)["d"]
    assert 7 < mins < 12


def test_bad_op_in_rules_is_rejected(tmp_path):
    bad = (Path(__file__).resolve().parent.parent / "rules" / "region-rules.yaml").read_text().replace("op: in, value: [1, 2]", "op: between, value: [1, 2]")
    p = tmp_path / "bad.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError):
        load_rules(p)
