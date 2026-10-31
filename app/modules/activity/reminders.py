"""Starting-soon notifications for published gatherings: one a day before and one an
hour before each occurrence.

`send_due_event_reminders` is safe to run as often as you like, from several
processes at once: every reminder has a dedupe key made of the gathering, the
occurrence's start and the stage, so a rerun, a restart or two workers can't notify
anyone twice (the notification service skips a key that exists, and queues an email
only for a notification it actually created).

Which stage an occurrence is in depends only on how long is left. More than an hour
and up to a day: the day-before reminder. An hour or less: the hour-before reminder.
So a gathering published at short notice still gets one useful reminder instead of
two at once, and one that moves gets fresh reminders for its new time. An all-day
gathering gets the day-before one only. A gathering that is no longer published (draft
again, canceled) or whose church is suspended gets nothing, because this reads the
current state each time.

`run_forever` is the loop the API starts in its lifespan; the same function can run
from cron instead (scripts/send_event_reminders.py).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core.database import platform_session
from app.core.timezones import church_tz
from app.modules.activity import recurrence, repository
from app.modules.notifications import service as notifications_service

logger = logging.getLogger("ecclesia_flow.events")

CHECK_EVERY_SECONDS = 60
DAY_BEFORE = timedelta(hours=24)
HOUR_BEFORE = timedelta(hours=1)
EVENTS_URL = "/portal/events"

#: Keys this process has already handled, so a minute-by-minute loop doesn't redo a
#: day's worth of work for the same occurrences. Only an optimisation: the dedupe
#: key in the database is what actually prevents a second notification.
_handled: set[str] = set()
_HANDLED_LIMIT = 20_000


def _dt(value: datetime | str) -> datetime:
    """A raw-query timestamp (an ISO string) or a datetime, as an aware UTC datetime."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _stage(remaining: timedelta, all_day: bool) -> str | None:
    if remaining <= timedelta(0) or remaining > DAY_BEFORE:
        return None
    if remaining <= HOUR_BEFORE:
        return None if all_day else "1h"
    return "24h"


def _wording(stage: str, title: str, when_text: str) -> tuple[str, str]:
    lead = "Coming up" if stage == "24h" else "Starting soon"
    return f"{lead}: {title}", f'"{title}" starts {when_text}.'


async def _remind(db, row: dict, occurrence: datetime, stage: str, zone) -> int:
    local = occurrence.astimezone(zone)
    when_text = f"{local:%a %d %b}" if row["is_all_day"] else f"{local:%a %d %b, %H:%M}"
    title, body = _wording(stage, row["title"], when_text)
    tenant_id = str(row["tenant_id"])
    recipients = await repository.reminder_recipients(
        db,
        event_id=str(row["id"]),
        author_user_id=str(row["created_by_user_id"]),
        group_id=str(row["group_id"]) if row["group_id"] else None,
        include_registrants=row["visibility"] != "private",
    )
    result = await notifications_service.notify(
        db,
        tenant_id,
        recipients,
        "reminder",
        title,
        body,
        action_url=EVENTS_URL,
        resource=("event", str(row["id"])),
        dedupe_key=f"event.reminder:{row['id']}:{occurrence.isoformat()}:{stage}",
    )
    return result.created


async def send_due_event_reminders(now: datetime | None = None) -> int:
    """One pass over every church. Returns how many notifications it created."""
    now = now or datetime.now(UTC)
    horizon = now + DAY_BEFORE
    created = 0
    async with platform_session() as db:
        candidates = await repository.reminder_candidates(db, now=now, horizon=horizon)
    for row in candidates:
        zone = church_tz(row["timezone"])
        starts = recurrence.occurrences(
            row["recurrence_rule"], _dt(row["start_date_time"]), window_start=now, window_end=horizon, tz=zone
        )
        for occurrence in starts:
            stage = _stage(occurrence - now, bool(row["is_all_day"]))
            key = f"{row['id']}:{occurrence.isoformat()}:{stage}"
            if stage is None or key in _handled:
                continue
            try:
                async with platform_session() as db:
                    created += await _remind(db, row, occurrence, stage, zone)
            except Exception:  # noqa: BLE001 — one gathering's failure must not stop the others
                logger.exception("events.reminder_failed event=%s", row["id"])
                continue
            if len(_handled) >= _HANDLED_LIMIT:
                _handled.clear()
            _handled.add(key)
    return created


async def run_forever() -> None:
    while True:
        try:
            created = await send_due_event_reminders()
            if created:
                logger.info("events.reminders_sent count=%s", created)
        except Exception:  # noqa: BLE001 — a failed pass must not stop the loop
            logger.exception("events.reminders_failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
