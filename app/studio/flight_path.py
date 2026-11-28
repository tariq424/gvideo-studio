"""Descent tracks taken from published ADS-B reports, for a route/plan Unreal shot.

flydubai FZ1073 (30 September 2026, A6-FKF) is the public record used here.
Altitudes are the Flightradar24 readings reconstructed by Hindustan Times.
The post-descent position 31.1N 37.2E is PlaneTrack's published ADS-B point
(adsb.lol, ODbL). Tabuk's aerodrome reference point is the public airport coordinate.
The plunge itself has no published latitudes, only "about 235 km north-east of Tabuk",
so those samples stay on that one published offset instead of inventing a track
through the reception gap.
"""
from __future__ import annotations
import math
import re

TABUK = {"lat": 28.371389, "lon": 36.618611, "alt_ft": 2551, "name": "Tabuk"}
_EARTH_KM = 6371.0


def _offset(lat, lon, bearing_deg, distance_km):
    br = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    d = distance_km / _EARTH_KM
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2))
    return {"lat": round(math.degrees(lat2), 5), "lon": round(math.degrees(lon2), 5)}


def _km(a, b):
    lat1, lon1 = math.radians(a["lat"]), math.radians(a["lon"])
    lat2, lon2 = math.radians(b["lat"]), math.radians(b["lon"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(h))


def fz1073_descent():
    """The published descent, highest first, ending on the ground at Tabuk."""
    start = _offset(TABUK["lat"], TABUK["lon"], 45.0, 235.0)
    turn = {"lat": 31.1, "lon": 37.2}
    alts = (34000, 33225, 18925, 16600, 21725, 14950, 13675)
    points = [{"lat": start["lat"], "lon": start["lon"], "alt_ft": alt} for alt in alts]
    points.append({"lat": turn["lat"], "lon": turn["lon"], "alt_ft": 15025})
    points.append({"lat": TABUK["lat"], "lon": TABUK["lon"], "alt_ft": TABUK["alt_ft"]})
    return points


def for_narration(text: str):
    """The public descent when the narration is this diversion. Otherwise nothing."""
    blob = text or ""
    flight = re.search(r"\b(FZ\s*1073|fly\s?dubai|flydubai)\b", blob, re.I)
    descent = re.search(r"\b(descent|descended|divert|tabuk|tel aviv|flight path|thousand feet)\b", blob, re.I)
    if flight and descent:
        return fz1073_descent()
    return None
