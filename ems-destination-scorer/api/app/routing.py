"""Drive times from the incident to each candidate hospital.

Uses a self-hosted OSRM server when OSRM_URL is set; otherwise, or if OSRM fails,
falls back to a straight-line estimate. OSRM durations are free-flow, so a traffic
multiplier from the rules file is applied on top.
"""
from __future__ import annotations

import logging
import math

import httpx

from .config import settings
from .rules import Rules
from .scoring import HospitalSnapshot

log = logging.getLogger(__name__)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fallback_minutes(lat: float, lon: float, hospitals: list[HospitalSnapshot], traffic: str, rules: Rules) -> dict[str, float]:
    speed = rules.traffic[traffic].fallback_speed_kmh
    out = {}
    for h in hospitals:
        km = haversine_km(lat, lon, h.lat, h.lon) * rules.road_factor
        out[h.id] = km / speed * 60 + rules.handling_minutes
    return out


async def osrm_minutes(lat: float, lon: float, hospitals: list[HospitalSnapshot], traffic: str, rules: Rules) -> dict[str, float]:
    coords = ";".join([f"{lon},{lat}"] + [f"{h.lon},{h.lat}" for h in hospitals])
    url = f"{settings.osrm_url.rstrip('/')}/table/v1/driving/{coords}"
    async with httpx.AsyncClient(timeout=settings.osrm_timeout_seconds) as client:
        resp = await client.get(url, params={"sources": "0", "annotations": "duration"})
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM returned {data.get('code')}")
    mult = rules.traffic[traffic].osrm_multiplier
    out = {}
    for h, seconds in zip(hospitals, data["durations"][0][1:]):
        if seconds is not None:
            out[h.id] = seconds / 60 * mult + rules.handling_minutes
    return out


async def drive_minutes(lat: float, lon: float, hospitals: list[HospitalSnapshot], traffic: str, rules: Rules) -> tuple[dict[str, float], str]:
    """Return ({hospital_id: minutes}, source) where source is 'osrm' or 'straight_line_estimate'."""
    if not hospitals:
        return {}, "none"
    if settings.osrm_url:
        try:
            return await osrm_minutes(lat, lon, hospitals, traffic, rules), "osrm"
        except Exception as exc:  # keep answering even if routing is down
            log.warning("OSRM unavailable, using straight-line estimate: %s", exc)
    return fallback_minutes(lat, lon, hospitals, traffic, rules), "straight_line_estimate"
