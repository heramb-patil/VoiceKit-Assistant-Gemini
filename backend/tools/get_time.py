"""Tool: get the current time in any timezone."""

from datetime import datetime, timedelta, timezone

# Common timezone offsets (avoids needing pytz/zoneinfo)
_OFFSETS = {
    "UTC": 0, "GMT": 0,
    "US/Eastern": -5, "US/Central": -6, "US/Mountain": -7, "US/Pacific": -8,
    "Europe/London": 0, "Europe/Paris": 1, "Europe/Berlin": 1,
    "Asia/Tokyo": 9, "Asia/Shanghai": 8, "Asia/Kolkata": 5.5,
    "Asia/Dubai": 4, "Asia/Singapore": 8,
    "Australia/Sydney": 11,
    "America/New_York": -5, "America/Chicago": -6, "America/Denver": -7,
    "America/Los_Angeles": -8, "America/Sao_Paulo": -3,
}


async def get_current_time(city: str) -> str:
    """Get the current date and time for a city.

    Args:
        city: The city name, e.g. Tokyo, New York, London, Mumbai.
    """
    city_lower = city.lower().strip()

    # Try to match city to a known timezone
    offset_hours = None
    for tz_name, off in _OFFSETS.items():
        if city_lower in tz_name.lower().split("/")[-1].lower().replace("_", " "):
            offset_hours = off
            break

    # Fallback heuristic for common cities
    _CITY_MAP = {
        "mumbai": 5.5, "delhi": 5.5, "bangalore": 5.5, "chennai": 5.5,
        "tokyo": 9, "osaka": 9, "london": 0, "paris": 1, "berlin": 1,
        "new york": -5, "los angeles": -8, "chicago": -6, "denver": -7,
        "san francisco": -8, "seattle": -8, "dubai": 4, "singapore": 8,
        "sydney": 11, "melbourne": 11, "toronto": -5, "vancouver": -8,
        "sao paulo": -3, "beijing": 8, "shanghai": 8, "hong kong": 8,
        "seoul": 9, "bangkok": 7, "jakarta": 7, "cairo": 2, "lagos": 1,
        "nairobi": 3, "johannesburg": 2, "moscow": 3, "istanbul": 3,
    }

    if offset_hours is None:
        offset_hours = _CITY_MAP.get(city_lower)

    if offset_hours is None:
        return f"Sorry, I don't have timezone data for {city}. Try a major city name."

    tz = timezone(timedelta(hours=offset_hours))
    now = datetime.now(tz)
    return f"The current time in {city} is {now.strftime('%I:%M %p on %A, %B %d, %Y')}."
