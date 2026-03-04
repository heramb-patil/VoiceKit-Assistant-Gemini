"""Google Calendar integration tools (standalone version)."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_auth: Any = None


def _get_service():
    if _auth is None:
        raise RuntimeError("Google is not connected. The user must connect Google in the Integrations panel first.")
    return _auth.build_service("calendar", "v3")


def _fmt_event(event: dict) -> str:
    start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "")
    end = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date", "")
    summary = event.get("summary", "(no title)")
    location = event.get("location", "")
    desc = event.get("description", "")[:200] if event.get("description") else ""
    parts = [f"{summary}  ({start} → {end})"]
    if location:
        parts.append(f"Location: {location}")
    if desc:
        parts.append(f"Notes: {desc}")
    return "\n".join(parts)


async def get_todays_events() -> str:
    """Get your full schedule for today from Google Calendar."""
    return await asyncio.to_thread(_get_todays_events_sync)


def _get_todays_events_sync() -> str:
    try:
        service = _get_service()
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        result = service.events().list(
            calendarId="primary",
            timeMin=start_of_day,
            timeMax=end_of_day,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        if not events:
            return "You have no events scheduled for today."
        return "Today's schedule:\n\n" + "\n\n".join(_fmt_event(e) for e in events)
    except Exception as exc:
        logger.exception("get_todays_events failed")
        return f"Failed to get today's events: {exc}"


async def get_upcoming_events(days: int = 7) -> str:
    """Get your upcoming events for the next N days."""
    return await asyncio.to_thread(_get_upcoming_events_sync, days)


def _get_upcoming_events_sync(days: int) -> str:
    try:
        service = _get_service()
        now = datetime.now(timezone.utc)
        time_max = (now + timedelta(days=days)).isoformat()
        result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()
        events = result.get("items", [])
        if not events:
            return f"No events found in the next {days} days."
        return f"Upcoming events (next {days} days):\n\n" + "\n\n".join(_fmt_event(e) for e in events)
    except Exception as exc:
        logger.exception("get_upcoming_events failed")
        return f"Failed to get upcoming events: {exc}"


async def create_event(
    title: str,
    start_time: str,
    end_time: str,
    description: str = "",
    attendees: list[str] | None = None,
) -> str:
    """Create a new Google Calendar event."""
    return await asyncio.to_thread(_create_event_sync, title, start_time, end_time, description, attendees or [])


def _create_event_sync(title: str, start_time: str, end_time: str, description: str, attendees: list[str]) -> str:
    try:
        service = _get_service()
        event_body: dict = {
            "summary": title,
            "start": {"dateTime": start_time, "timeZone": "UTC"},
            "end": {"dateTime": end_time, "timeZone": "UTC"},
        }
        if description:
            event_body["description"] = description
        if attendees:
            event_body["attendees"] = [{"email": e} for e in attendees]

        created = service.events().insert(calendarId="primary", body=event_body).execute()
        return f"Event created: '{title}' from {start_time} to {end_time}. Event ID: {created.get('id', '')}"
    except Exception as exc:
        logger.exception("create_event failed")
        return f"Failed to create event: {exc}"


async def check_availability(date: str) -> str:
    """Check your free time slots on a specific date."""
    return await asyncio.to_thread(_check_availability_sync, date)


def _check_availability_sync(date: str) -> str:
    try:
        service = _get_service()
        day_start = f"{date}T00:00:00Z"
        day_end = f"{date}T23:59:59Z"
        result = service.events().list(
            calendarId="primary",
            timeMin=day_start,
            timeMax=day_end,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        if not events:
            return f"You're fully free on {date} — no events scheduled."

        busy_slots = []
        for e in events:
            start = e.get("start", {}).get("dateTime", "")
            end = e.get("end", {}).get("dateTime", "")
            summary = e.get("summary", "(busy)")
            busy_slots.append(f"{start[11:16]}–{end[11:16]}: {summary}")

        return f"Busy on {date}:\n" + "\n".join(busy_slots) + "\n\nAll other times are free."
    except Exception as exc:
        logger.exception("check_availability failed")
        return f"Failed to check availability: {exc}"
