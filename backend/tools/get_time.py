"""Tool: get the current time in any timezone."""

from datetime import datetime


# City → IANA timezone name (used by zoneinfo — DST-aware)
_CITY_TO_TZ = {
    "utc": "UTC", "gmt": "UTC",
    "new york": "America/New_York", "nyc": "America/New_York",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "chicago": "America/Chicago", "denver": "America/Denver",
    "toronto": "America/Toronto", "vancouver": "America/Vancouver",
    "sao paulo": "America/Sao_Paulo",
    "london": "Europe/London",
    "paris": "Europe/Paris", "berlin": "Europe/Berlin",
    "amsterdam": "Europe/Amsterdam", "madrid": "Europe/Madrid",
    "rome": "Europe/Rome", "zurich": "Europe/Zurich",
    "moscow": "Europe/Moscow", "istanbul": "Europe/Istanbul",
    "dubai": "Asia/Dubai", "riyadh": "Asia/Riyadh",
    "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata", "chennai": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata", "india": "Asia/Kolkata",
    "singapore": "Asia/Singapore", "hong kong": "Asia/Hong_Kong",
    "shanghai": "Asia/Shanghai", "beijing": "Asia/Shanghai",
    "tokyo": "Asia/Tokyo", "osaka": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "seoul": "Asia/Seoul", "korea": "Asia/Seoul",
    "bangkok": "Asia/Bangkok", "jakarta": "Asia/Jakarta",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "auckland": "Pacific/Auckland",
    "cairo": "Africa/Cairo", "lagos": "Africa/Lagos",
    "nairobi": "Africa/Nairobi", "johannesburg": "Africa/Johannesburg",
}


async def get_current_time(city: str = "") -> str:
    """Get the current date and time, optionally for a specific city.

    Args:
        city: City name (e.g. 'Tokyo', 'New York', 'Mumbai').
              Leave empty to get the local server time.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    city_lower = city.lower().strip()

    if not city_lower:
        # No city specified — return local time
        now = datetime.now().astimezone()
        return f"The current time is {now.strftime('%I:%M %p on %A, %B %d, %Y')}."

    # Look up IANA timezone
    tz_name = _CITY_TO_TZ.get(city_lower)

    # If not in our map, try using the city name directly as an IANA zone
    if tz_name is None:
        # e.g. user said "Asia/Tokyo" directly
        try:
            ZoneInfo(city)
            tz_name = city
        except (ZoneInfoNotFoundError, KeyError):
            pass

    if tz_name is None:
        return (
            f"I don't have timezone data for '{city}'. "
            "Try a major city name like Tokyo, London, New York, or Mumbai."
        )

    try:
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        label = city.title() if city_lower != tz_name.lower() else tz_name
        return f"The current time in {label} is {now.strftime('%I:%M %p on %A, %B %d, %Y')}."
    except Exception as exc:
        return f"Could not get time for {city}: {exc}"
