"""Integration points with two public web APIs (plain HTTPS, no API key):

  * OpenTopoData  -> ground elevation of a property
  * Open-Meteo    -> current weather + recent/forecast rain at a property

Rules: short timeout, never raise into the page, cache so we stay polite.
"""
import time

import requests
from flask import current_app

_cache: dict[tuple, tuple[float, object]] = {}
WEATHER_TTL = 600  # seconds

WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    80: "Rain showers", 81: "Rain showers", 82: "Violent rain showers", 95: "Thunderstorm",
    96: "Thunderstorm, hail", 99: "Thunderstorm, heavy hail",
}


def fetch_elevation(lat: float, lng: float) -> float | None:
    cfg = current_app.config
    try:
        r = requests.get(cfg["OPENTOPODATA_URL"], params={"locations": f"{lat},{lng}"}, timeout=cfg["API_TIMEOUT"])
        r.raise_for_status()
        value = r.json()["results"][0]["elevation"]
        return float(value) if value is not None else None
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
        return None


def fetch_weather(lat: float, lng: float) -> dict | None:
    key = ("weather", round(lat, 3), round(lng, 3))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < WEATHER_TTL:
        return hit[1]
    cfg = current_app.config
    params = {
        "latitude": lat, "longitude": lng, "timezone": cfg["TIMEZONE"],
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code",
        "daily": "precipitation_sum", "past_days": 7, "forecast_days": 3,
    }
    try:
        r = requests.get(cfg["OPENMETEO_URL"], params=params, timeout=cfg["API_TIMEOUT"])
        r.raise_for_status()
        j = r.json()
        cur = j["current"]
        rain = [x or 0 for x in j["daily"]["precipitation_sum"]]
        data = {
            "temperature_c": cur["temperature_2m"],
            "humidity_pct": cur["relative_humidity_2m"],
            "wind_kmh": cur["wind_speed_10m"],
            "condition": WMO.get(cur.get("weather_code"), "—"),
            "rain_past_7d_mm": round(sum(rain[:7]), 1),
            "rain_next_3d_mm": round(sum(rain[7:]), 1),
        }
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
        return None
    _cache[key] = (time.time(), data)
    return data
