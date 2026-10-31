"""IANA time zone lookup.

Uses python-dateutil, already a dependency for repeating events, which ships
its own copy of the zone database. (The standard library's zoneinfo needs the
OS or a `tzdata` package to supply one, and neither is there on every host.)
"""

from datetime import UTC, tzinfo

from dateutil import tz


def get_tz(name: str | None) -> tzinfo | None:
    """The zone for an IANA name like "Africa/Kigali", or None if unknown."""
    if not name or not name.strip():
        return None
    zone = tz.gettz(name.strip())
    # gettz also parses POSIX strings ("UTC+3", "XYZ3") and the machine's local zone;
    # a church's zone must be a real database name that browsers understand too.
    if zone is None or isinstance(zone, (tz.tzstr, tz.tzlocal)):
        return None
    return zone


def is_valid(name: str | None) -> bool:
    return get_tz(name) is not None


def church_tz(name: str | None) -> tzinfo:
    """A church's zone, falling back to UTC when none is set or the name is not
    recognised, so a bad value never breaks a calendar."""
    return get_tz(name) or UTC
