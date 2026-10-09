"""Small, shared fixes for passing Python values into prisma-client-py's
`data={...}` dicts — gotchas that bite in more than one module, so they live
here instead of being rediscovered/re-patched per module.
"""

from datetime import UTC, date, datetime


def coerce_dates(data: dict) -> dict:
    """prisma-client-py's query builder rejects a bare `datetime.date` for a
    `@db.Date` column with `TypeError: Type <class 'datetime.date'> not
    serializable` — it wants a full timezone-aware `datetime`, even though
    the column only stores a date. Every `date` value in `data` (not
    `datetime` — that's a `date` subclass, checked by exact type here, not
    `isinstance`, so real datetimes pass through untouched) is promoted to
    midnight UTC before it reaches Prisma.
    """
    return {
        k: (datetime.combine(v, datetime.min.time(), tzinfo=UTC) if type(v) is date else v) for k, v in data.items()
    }
