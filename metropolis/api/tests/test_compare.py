from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.compare import nearest_capable, nearest_ed
from app.rules import load_rules
from app.scoring import HospitalSnapshot, score

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


def test_nearest_capable_skips_incapable_hospitals():
    hs = [hosp("near"), hosp("mid", pci_24_7=True), hosp("far", pci_24_7=True)]
    b = nearest_capable(hs, "stemi", {"near": 3, "mid": 6, "far": 9}, RULES, NOW)
    assert b.hospital.id == "mid" and b.capable and b.minutes_drive == 6 and b.minutes_total == 6 + 5


def test_diversion_blocks_general_but_not_exempt_category():
    hs = [hosp("div", on_diversion=True, trauma_level=1), hosp("open", trauma_level=1)]
    drive = {"div": 2, "open": 8}
    assert nearest_capable(hs, "general", drive, RULES, NOW).hospital.id == "open"
    assert nearest_capable(hs, "trauma", drive, RULES, NOW).hospital.id == "div"


def test_nearest_ed_can_be_incapable_and_says_why():
    hs = [hosp("near"), hosp("far", pci_24_7=True)]
    b = nearest_ed(hs, "stemi", {"near": 3, "far": 9}, RULES, NOW)
    assert b.hospital.id == "near" and not b.capable and b.reason == "No 24/7 cath lab"


def test_pediatric_only_excluded_for_adults_included_for_peds():
    hs = [hosp("kids", pediatric_ed=True, pediatric_only=True), hosp("adult")]
    drive = {"kids": 2, "adult": 6}
    assert nearest_ed(hs, "general", drive, RULES, NOW).hospital.id == "adult"
    assert nearest_ed(hs, "peds", drive, RULES, NOW).hospital.id == "kids"


def test_none_when_nothing_qualifies_or_no_route():
    assert nearest_capable([hosp("a")], "burn", {"a": 3}, RULES, NOW) is None
    assert nearest_ed([hosp("a")], "general", {}, RULES, NOW) is None


def test_baseline_matches_score_figures():
    hs = [hosp("crowded", trauma_level=1, ed_occupancy_pct=125, boarding=15), hosp("calm", trauma_level=1)]
    drive = {"crowded": 4.4, "calm": 11.6}
    b = nearest_capable(hs, "trauma", drive, RULES, NOW)
    rec = {r.hospital.id: r for r in score(hs, "trauma", drive, RULES, NOW).recommendations}
    assert b.hospital.id == "crowded"
    assert (b.minutes_drive, b.minutes_offload, b.minutes_total) == (
        rec["crowded"].minutes_drive, rec["crowded"].minutes_offload, rec["crowded"].minutes_total)
