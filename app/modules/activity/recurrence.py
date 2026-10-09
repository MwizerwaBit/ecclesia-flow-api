"""Repeating events — a deliberately small, validated subset of RFC 5545 RRULE.

Supported: FREQ (DAILY/WEEKLY/MONTHLY/YEARLY), INTERVAL, BYDAY, BYMONTHDAY,
COUNT, UNTIL. That covers "every Sunday", "every other Tuesday", "the first
Saturday of the month" and "monthly on the 15th, 10 times". Anything else is
rejected rather than silently misread, and expansion is capped so one rule can
never produce an unbounded list.
"""

import re
from datetime import datetime, timedelta

from dateutil.rrule import rrulestr

ALLOWED_KEYS = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "COUNT", "UNTIL"}
FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
_BYDAY = re.compile(r"^([+-]?[1-5])?(MO|TU|WE|TH|FR|SA|SU)$")
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
    if "UNTIL" in parts and not re.fullmatch(r"\d{8}(T\d{6}Z?)?", parts["UNTIL"]):
        raise ValueError("End date must look like 20261231")
    if "BYDAY" in parts and not all(_BYDAY.match(d) for d in parts["BYDAY"].split(",")):
        raise ValueError("Unrecognised day in BYDAY")
    if "BYMONTHDAY" in parts and not all(
        d.lstrip("-").isdigit() and 1 <= abs(int(d)) <= 31 for d in parts["BYMONTHDAY"].split(",")
    ):
        raise ValueError("Day of month must be between 1 and 31")
    return ";".join(f"{k}={v}" for k, v in parts.items())


def occurrences(rule: str | None, start: datetime, *, window_start: datetime, window_end: datetime) -> list[datetime]:
    """Start times within [window_start, window_end]. A one-off event yields
    its own start when it falls inside the window."""
    if not rule:
        return [start] if window_start <= start <= window_end else []
    # UNTIL must carry a timezone when DTSTART does.
    normalised = validate(rule)
    if "UNTIL=" in normalised and start.tzinfo is not None:
        normalised = re.sub(r"UNTIL=(\d{8})(?:T\d{6}Z?)?", lambda m: f"UNTIL={m.group(1)}T235959Z", normalised)
    series = rrulestr(f"RRULE:{normalised}", dtstart=start)
    out: list[datetime] = []
    for occurrence in series.xafter(window_start - timedelta(seconds=1), count=MAX_OCCURRENCES, inc=True):
        if occurrence > window_end:
            break
        out.append(occurrence)
    return out


def next_occurrence(rule: str | None, start: datetime, now: datetime) -> datetime | None:
    found = occurrences(rule, start, window_start=now, window_end=now + timedelta(days=3660))
    return found[0] if found else None
