"""Weather via the Open-Meteo API (free, no API key required).

Geocodes a city name to coordinates, then fetches the current conditions and
today's forecast, and returns a natural-language summary. Uses httpx (already a
dependency). Fails gracefully on network/lookup errors.
"""

import time

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# IP-based geolocation (free, no key) for auto-detecting the user's city.
IPAPI_URL = "https://ipapi.co/json/"
IPINFO_URL = "https://ipinfo.io/json"

DEFAULT_CITY = "Prague"

# Some networks reject requests with a missing/default user agent.
_HEADERS = {"User-Agent": "JARVIS/1.0 (voice assistant)"}

# Cache the detected location for an hour so we don't hit the IP lookup on
# every weather request.
_LOCATION_TTL = 3600  # seconds
_location_cache = {"data": None, "ts": 0.0}

# WMO weather interpretation codes -> human-readable conditions.
WMO_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "light snow showers",
    86: "heavy snow showers",
    95: "thunderstorms",
    96: "thunderstorms with light hail",
    99: "thunderstorms with heavy hail",
}


def _geocode(city: str):
    """Return (lat, lon, display_name, country) for a city, or None."""
    resp = httpx.get(
        GEOCODE_URL,
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        headers=_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("results")
    if not results:
        return None
    r = results[0]
    return (
        r["latitude"],
        r["longitude"],
        r.get("name", city),
        r.get("country", ""),
    )


def _lookup_ipapi():
    """Detect location via ipapi.co. Returns a location dict or None."""
    try:
        resp = httpx.get(IPAPI_URL, headers=_HEADERS, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 - caller falls back
        return None
    city = data.get("city")
    if not city:
        return None
    return {
        "city": city,
        "country": data.get("country_name") or data.get("country") or "",
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
    }


def _lookup_ipinfo():
    """Detect location via ipinfo.io. Returns a location dict or None."""
    try:
        resp = httpx.get(IPINFO_URL, headers=_HEADERS, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 - caller falls back
        return None
    city = data.get("city")
    if not city:
        return None
    lat = lon = None
    loc = data.get("loc")  # "lat,lon"
    if loc and "," in loc:
        try:
            lat_s, lon_s = loc.split(",", 1)
            lat, lon = float(lat_s), float(lon_s)
        except ValueError:
            pass
    return {
        "city": city,
        "country": data.get("country") or "",
        "latitude": lat,
        "longitude": lon,
    }


def get_location():
    """Detect the user's city via IP geolocation, cached for one hour.

    Tries ipapi.co, then ipinfo.io, then falls back to ``DEFAULT_CITY``. Returns
    a dict with ``city``, ``country``, ``latitude`` and ``longitude`` (the last
    two may be None when only a city name could be determined).
    """
    now = time.time()
    cached = _location_cache.get("data")
    if cached and now - _location_cache.get("ts", 0.0) < _LOCATION_TTL:
        return cached

    location = _lookup_ipapi() or _lookup_ipinfo()
    if not location:
        location = {
            "city": DEFAULT_CITY,
            "country": "",
            "latitude": None,
            "longitude": None,
        }
    _location_cache["data"] = location
    _location_cache["ts"] = now
    return location


def get_weather(city: str = None) -> str:
    """Return a natural-language weather summary.

    When ``city`` is given, that city is used; otherwise the user's location is
    auto-detected by IP (and cached). The detected/looked-up city name is always
    included in the spoken summary.
    """
    try:
        if city and city.strip():
            city = city.strip()
            geo = _geocode(city)
            if not geo:
                return f"I couldn't find a place called '{city}'."
            lat, lon, name, country = geo
        else:
            loc = get_location()
            name = loc["city"]
            country = loc.get("country", "")
            lat = loc.get("latitude")
            lon = loc.get("longitude")
            # If IP lookup gave a city but no coordinates, geocode the name.
            if lat is None or lon is None:
                geo = _geocode(name)
                if not geo:
                    return "I couldn't work out your local weather right now."
                lat, lon, name, country = geo

        resp = httpx.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "temperature_2m,relative_humidity_2m,"
                    "weather_code,wind_speed_10m"
                ),
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
            },
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        current = data["current"]
        daily = data["daily"]
        temp = round(current["temperature_2m"])
        humidity = current["relative_humidity_2m"]
        wind = round(current["wind_speed_10m"])
        condition = WMO_CODES.get(current["weather_code"], "unsettled")
        high = round(daily["temperature_2m_max"][0])
        low = round(daily["temperature_2m_min"][0])

        place = f"{name}, {country}" if country else name
        return (
            f"It's {temp}°C in {place}, {condition} with a high of {high}°C "
            f"and a low of {low}°C today. Humidity is {humidity}% and wind is "
            f"around {wind} km/h."
        )
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        where = f" for {city}" if city and city.strip() else ""
        return f"I couldn't get the weather{where} right now ({exc})."
