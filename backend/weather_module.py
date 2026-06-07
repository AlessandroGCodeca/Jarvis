"""Weather via the Open-Meteo API (free, no API key required).

Geocodes a city name to coordinates, then fetches the current conditions and
today's forecast, and returns a natural-language summary. Uses httpx (already a
dependency). Fails gracefully on network/lookup errors.
"""

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

DEFAULT_CITY = "Prague"

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


def get_weather(city: str = DEFAULT_CITY) -> str:
    """Return a natural-language weather summary for ``city``."""
    city = (city or DEFAULT_CITY).strip()
    try:
        geo = _geocode(city)
        if not geo:
            return f"I couldn't find a place called '{city}'."
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
        return f"I couldn't get the weather for {city} right now ({exc})."
