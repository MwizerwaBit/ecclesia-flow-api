"""Repeating events — a deliberately small, validated subset of RFC 5545 RRULE.

Supported: FREQ (DAILY/WEEKLY/MONTHLY/YEARLY), INTERVAL, BYDAY, BYMONTHDAY,
COUNT, UNTIL. That covers "every Sunday", "every other Tuesday", "the first
Saturday of the month" and "monthly on the 15th, 10 times". Anything else is
rejected rather than silently misread, and expansion is capped so one rule can
never produce an unbounded list.

Time zones. Given a zone (the church's), a series repeats at the same *local*
clock time all year: a 09:00 Sunday service stays at 09:00 on both sides of a
daylight-saving change, and its stored UTC instant moves with the clock. UNTIL
means the end of that local day. Without a zone the rule is expanded on the UTC
instant, as it was before churches had a zone (a fixed UTC time of day).
"""

import re
from datetime import UTC, datetime, timedelta, tzinfo

from dateutil import tz as dateutil_tz
from dateutil.rrule import rrulestr

ALLOWED_KEYS = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "COUNT", "UNTIL"}
FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
_BYDAY = re.compile(r"^([+-]?[1-5])?(MO|TU|WE|TH|FR|SA|SU)$")
_UNTIL = re.compile(r"UNTIL=(\d{8})(?:T\d{6}Z?)?")
MAX_OCCURRENCES = 366


def validate(rule: str) -> str:
    """Returns the normalised rule, or raises ValueError explaining what's wrong."""
    rule = rule.strip().upper().removeprefix("RRULE:")
    parts: dict[str, str] = {}
    for item in rule.split(";"):
        if not item:
            continue
        key, sep, value = item.partition("=")
        if not sep or key not in ALLOWED_KEYS:
            raise ValueError(f"Unsupported repeat setting: {item}")
        if key in parts:
            raise ValueError(f"{key} appears twice")
        parts[key] = value
    if parts.get("FREQ") not in FREQUENCIES:
        raise ValueError("Say how often it repeats (daily, weekly, monthly or yearly)")
    if "INTERVAL" in parts and not (parts["INTERVAL"].isdigit() and 1 <= int(parts["INTERVAL"]) <= 52):
        raise ValueError("Repeat interval must be between 1 and 52")
    if "COUNT" in parts and not (parts["COUNT"].isdigit() and 1 <= int(parts["COUNT"]) <= MAX_OCCURRENCES):
        raise ValueError(f"A series can have at most {MAX_OCCURRENCES} occurrences")
    if "COUNT" in parts and "UNTIL" in parts:
        raise ValueError("Use either an end date or a number of times, not both")
    if "UNTIL" in parts:
        if not re.fullmatch(r"\d{8}(T\d{6}Z?)?", parts["UNTIL"]):
            raise ValueError("End date must look like 20261231")
        try:
            datetime.strptime(parts["UNTIL"][:8], "%Y%m%d")
            if "T" in parts["UNTIL"]:
                datetime.strptime(parts["UNTIL"][9:15], "%H%M%S")
        except ValueError as exc:
            raise ValueError("End date must be a real date like 20261231") from exc
    if "BYDAY" in parts and not all(_BYDAY.match(d) for d in parts["BYDAY"].split(",")):
        raise ValueError("Unrecognised day in BYDAY")
    if "BYMONTHDAY" in parts and not all(
        d.lstrip("-").isdigit() and 1 <= abs(int(d)) <= 31 for d in parts["BYMONTHDAY"].split(",")
    ):
        raise ValueError("Day of month must be between 1 and 31")
    return ";".join(f"{k}={v}" for k, v in parts.items())


def _aware(value: datetime) -> datetime:
    """A naive instant is read as UTC; never compare naive with aware."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def occurrences(
    rule: str | None,
    start: datetime,
    *,
    window_start: datetime,
    window_end: datetime,
    tz: tzinfo | None = None,
) -> list[datetime]:
    """UTC start times within [window_start, window_end]. A one-off event yields
    its own start when it falls inside the window."""
    start, window_start, window_end = _aware(start), _aware(window_start), _aware(window_end)
    if not rule:
        return [start] if window_start <= start <= window_end else []
    try:
        return _expand(rule, start, window_start, window_end, tz)
    except ValueError:
        # A rule stored before it was validated this strictly must not take a whole
        # calendar down: it simply has no dates.
        return []


def _expand(
    rule: str, start: datetime, window_start: datetime, window_end: datetime, tz: tzinfo | None
) -> list[datetime]:
    normalised = validate(rule)
    if tz is None:
        # Legacy: repeat on the fixed UTC time of day. UNTIL ends at 23:59:59Z.
        normalised = _UNTIL.sub(lambda m: f"UNTIL={m.group(1)}T235959Z", normalised)
        series = rrulestr(f"RRULE:{normalised}", dtstart=start)
        out: list[datetime] = []
        for occurrence in series.xafter(window_start - timedelta(seconds=1), count=MAX_OCCURRENCES, inc=True):
            if occurrence > window_end:
                break
            out.append(occurrence)
        return out

    # Local wall-clock expansion: rule maths on naive local times, then each
    # occurrence is placed in the zone (a time skipped by a clock change moves
    # forward) and converted back to UTC.
    normalised = _UNTIL.sub(lambda m: f"UNTIL={m.group(1)}T235959", normalised)
    local_start = start.astimezone(tz).replace(tzinfo=None)
    series = rrulestr(f"RRULE:{normalised}", dtstart=local_start)
    # A day of slack each side covers the offset between local and UTC.
    local_from = window_start.astimezone(tz).replace(tzinfo=None) - timedelta(days=1)
    local_to = window_end.astimezone(tz).replace(tzinfo=None) + timedelta(days=1)
    found: list[datetime] = []
    for local in series.xafter(local_from, count=MAX_OCCURRENCES, inc=True):
        if local > local_to:
            break
        placed = dateutil_tz.resolve_imaginary(local.replace(tzinfo=tz))
        instant = placed.astimezone(UTC)
        if window_start <= instant <= window_end:
            found.append(instant)
    return found


def next_occurrence(
    rule: str | None,
    start: datetime,
    now: datetime,
    tz: tzinfo | None = None,
    duration: timedelta = timedelta(0),
) -> datetime | None:
    """The first occurrence that has not finished: one that began up to `duration`
    ago is still going (an all-day event all day, a camp on its second day)."""
    found = occurrences(rule, start, window_start=now - duration, window_end=now + timedelta(days=3660), tz=tz)
    return found[0] if found else None
