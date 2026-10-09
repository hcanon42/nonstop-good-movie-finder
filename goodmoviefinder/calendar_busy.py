"""Busy intervals shared by Apple Calendar and Google Calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from goodmoviefinder.models import Movie

VIENNA = ZoneInfo("Europe/Vienna")

_LABELS = {
    "apple": "Apple Calendar",
    "google": "Google Calendar",
}
_MONTHS = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class CalendarError(RuntimeError):
    """A calendar was requested and could not be read."""


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime


def merge_intervals(intervals: list[BusyInterval]) -> list[BusyInterval]:
    """Sort and join overlapping or touching blocks. Half-open at the end."""
    valid = [item for item in intervals if item.end > item.start]
    valid.sort(key=lambda item: (item.start, item.end))
    if not valid:
        return []
    merged = [valid[0]]
    for item in valid[1:]:
        last = merged[-1]
        if item.start <= last.end:
            if item.end > last.end:
                merged[-1] = BusyInterval(last.start, item.end)
            continue
        merged.append(item)
    return merged


def latest_screening_date(movies: list[Movie]) -> date | None:
    latest: date | None = None
    for movie in movies:
        for screening in movie.screenings:
            if not screening.weekday:
                continue
            try:
                day = date.fromisoformat(screening.weekday)
            except ValueError:
                continue
            if latest is None or day > latest:
                latest = day
    return latest


def fetch_window(
    movies: list[Movie],
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Start of today through 06:00 after the last screening day.

    The extra morning covers a late show that runs past midnight. The day
    starts at midnight so an event that began earlier today still overlaps
    a later screening.
    """
    current = (now or datetime.now(VIENNA)).astimezone(VIENNA)
    latest = latest_screening_date(movies)
    if latest is None:
        return None
    start = datetime.combine(current.date(), time.min, tzinfo=VIENNA)
    end = datetime.combine(latest + timedelta(days=1), time(6, 0), tzinfo=VIENNA)
    if end <= current:
        return None
    return start, end


def active_intervals(
    intervals: list[BusyInterval],
    now: datetime | None = None,
) -> list[BusyInterval]:
    current = (now or datetime.now(VIENNA)).astimezone(VIENNA)
    return [
        item
        for item in merge_intervals(intervals)
        if item.end.astimezone(VIENNA) > current
    ]


def interval_payload(intervals: list[BusyInterval]) -> list[dict[str, str]]:
    """Vienna local start and end, with no event titles."""
    payload: list[dict[str, str]] = []
    for item in merge_intervals(intervals):
        start = item.start.astimezone(VIENNA)
        end = item.end.astimezone(VIENNA)
        payload.append(
            {
                "start": start.strftime("%Y-%m-%dT%H:%M"),
                "end": end.strftime("%Y-%m-%dT%H:%M"),
            }
        )
    return payload


def _names(sources: list[str]) -> str:
    labels = [_LABELS[source] for source in sources]
    if len(labels) == 1:
        return labels[0]
    return " and ".join(labels)


def status_sentence(
    sources: list[str],
    failures: list[str],
    through: date,
) -> str:
    if sources:
        when = f"{through.day} {_MONTHS[through.month]}"
        sentence = f"Using {_names(sources)} through {when}."
        if failures:
            sentence += f" {_names(failures)} could not be read."
        return sentence
    if failures:
        return (
            f"{_names(failures)} could not be read. "
            "Showtimes are not checked against your calendar."
        )
    return ""
