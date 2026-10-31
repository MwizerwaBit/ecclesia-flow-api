import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from dateutil import tz as dateutil_tz
from prisma import Json, Prisma

from app.core.database import set_tenant_context
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.timezones import church_tz, get_tz
from app.modules.activity import recurrence, repository
from app.modules.activity.schemas import (
    AttendanceMarkInput,
    ChurchEventCreate,
    ChurchEventUpdate,
    CollisionCheckInput,
    HeadcountEntry,
    NotifyKeyParticipantsInput,
)
from app.modules.audit.service import history_for, write_audit
from app.modules.forms import service as forms_service
from app.modules.forms.service import OWNER_EVENT, FormClosedError
from app.modules.groups import service as groups_service
from app.modules.hierarchy import service as hierarchy_service
from app.modules.notifications import service as notifications_service
from app.modules.rbac.service import resolve_permissions
from app.modules.tenant import service as tenant_service

#: Permissions that mean "this person runs events". Everyone else is an ordinary
#: member: they see what a member may see, never drafts, reviews or private groups.
MANAGE_PERMISSIONS = ("events:create", "events:update", "events:review")

#: Largest calendar window, and the most dates one response carries.
MAX_WINDOW_DAYS = 93
MAX_CALENDAR_ITEMS = 2000
MAX_OCCURRENCE_WINDOW_DAYS = 365  # below the 366-date expansion cap, so a daily series is never silently cut
_DIFF_FIELDS = (
    "title",
    "type",
    "location",
    "start_date_time",
    "end_date_time",
    "recurrence_rule",
    "visibility",
    "group_id",
    "unit_id",
    "is_all_day",
    "capacity",
    "attendance_mode",
    "online_url",
)


class BadWindowError(AppError):
    status_code = 422
    code = "invalid_window"


class InvalidRangeError(AppError):
    status_code = 422
    code = "invalid_range"


class DateOutOfRangeError(AppError):
    status_code = 422
    code = "invalid_date"


class EditConflictError(AppError):
    status_code = 409
    code = "edit_conflict"


@dataclass(frozen=True)
class Viewer:
    """Who is looking at events. A restricted viewer (an ordinary member) is
    limited to published events they may see; the rule is in the repository."""

    restricted: bool
    member_id: str | None = None


async def viewer_for(db: Prisma, *, can_manage: bool, user_id: str) -> Viewer:
    if can_manage:
        return Viewer(restricted=False)
    return Viewer(restricted=True, member_id=await repository.member_id_for_user(db, user_id))


async def _tz(db: Prisma) -> tuple[str, tzinfo]:
    """The church's IANA zone name and tzinfo (UTC when unset or unrecognised)."""
    name = await repository.church_timezone(db)
    return (name if get_tz(name) else "UTC"), church_tz(name)


def _dt(value: datetime | str) -> datetime:
    """A raw-query timestamp (ISO string) or a datetime, as an aware UTC datetime."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _to_utc(value: datetime, zone: tzinfo) -> datetime:
    """A time with an offset is an instant; one without is read as the church's
    local clock time (what a date-time input on the form means)."""
    try:
        if value.tzinfo is None:
            value = dateutil_tz.resolve_imaginary(value.replace(tzinfo=zone))
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as exc:  # the first and last years of the calendar
        raise DateOutOfRangeError("That date is out of range") from exc


def _local_midnight(day: date, zone: tzinfo) -> datetime:
    return dateutil_tz.resolve_imaginary(datetime.combine(day, time.min, tzinfo=zone)).astimezone(UTC)


def _all_day_bounds(start: datetime, end: datetime | None, zone: tzinfo) -> tuple[datetime, datetime]:
    """Whole days in the church's zone: midnight of the first day to the
    midnight after the last (never less than one day)."""
    first = start.astimezone(zone).date()
    if end is None:
        last_exclusive = first + timedelta(days=1)
    else:
        local_end = end.astimezone(zone)
        last_exclusive = local_end.date() + (timedelta(days=1) if local_end.time() != time.min else timedelta(0))
    if last_exclusive <= first:
        last_exclusive = first + timedelta(days=1)
    return _local_midnight(first, zone), _local_midnight(last_exclusive, zone)


def _decorate(row: dict, now: datetime | None = None, zone: tzinfo | None = None) -> dict:
    """Adds the computed next occurrence and normalises the stored theme."""
    now = now or datetime.now(UTC)
    start = _dt(row["start_date_time"])
    theme = row.get("theme")
    if isinstance(theme, str):
        theme = json.loads(theme or "{}")
    end = row.get("end_date_time")
    duration = (_dt(end) - start) if end else timedelta(0)
    return {
        **row,
        "theme": theme or {},
        "next_occurrence": recurrence.next_occurrence(row.get("recurrence_rule"), start, now, zone, duration),
    }


async def list_events(
    db: Prisma, viewer: Viewer, *, search: str | None, upcoming: bool | None, status: str | None = None
) -> list[dict]:
    _, zone = await _tz(db)
    rows = await repository.list_events(
        db,
        search=search,
        upcoming=upcoming,
        status=status,
        restrict_to_member=viewer.restricted,
        member_id=viewer.member_id,
    )
    decorated = [_decorate(r, zone=zone) for r in rows]
    if upcoming is True:
        # Only what still has a date to come, soonest first (a finished series has none).
        decorated = [r for r in decorated if r["next_occurrence"] is not None]
        decorated.sort(key=lambda r: (r["next_occurrence"], str(r["id"])))
    elif upcoming is False:
        decorated = [r for r in decorated if r["next_occurrence"] is None]
    return decorated


async def _require_visible(db: Prisma, event_id: str, viewer: Viewer | None) -> None:
    """A restricted viewer gets 404 for anything a member may not see, exactly
    as for an event that does not exist."""
    if viewer is not None and viewer.restricted:
        if not await repository.event_visible_to_member(db, event_id, viewer.member_id):
            raise NotFoundError("No such event")  # the same words as for an id that does not exist


async def get_event(db: Prisma, event_id: str, viewer: Viewer | None = None) -> dict:
    await _require_visible(db, event_id, viewer)
    row = await repository.get_event_row(db, event_id)
    if row is None:
        raise NotFoundError("No such gathering")
    _, zone = await _tz(db)
    decorated = _decorate(row, zone=zone)
    now = datetime.now(UTC)
    decorated["upcoming_occurrences"] = recurrence.occurrences(
        row.get("recurrence_rule"),
        _dt(row["start_date_time"]),
        window_start=now,
        window_end=now + timedelta(days=120),
        tz=zone,
    )[:12]
    if viewer is not None and viewer.restricted:
        # Review comments and submission details are for the people running the event.
        decorated["reviews"] = []
        decorated["submitted_at"] = None
    else:
        decorated["reviews"] = await repository.list_reviews(db, event_id)
    return decorated


async def _check_group(db: Prisma, group_id: str | None) -> None:
    if group_id and not await groups_service.group_exists(db, group_id):
        raise NotFoundError("No such group")


async def _check_unit(db: Prisma, unit_id: str | None) -> None:
    """A unit must exist in this church. (A foreign key alone would accept
    another church's unit id from a whole-church session.)"""
    if unit_id and not await hierarchy_service.unit_exists(db, unit_id):
        raise NotFoundError("No such unit")


def _event_data(payload, *, partial: bool) -> dict:
    data = payload.model_dump(exclude_unset=partial, exclude={"theme", "online_url", "expected_updated_at"})
    if "theme" in payload.model_fields_set or not partial:
        data["theme"] = Json(payload.theme.model_dump() if payload.theme else {})
    if "online_url" in payload.model_fields_set:
        data["online_url"] = str(payload.online_url) if payload.online_url else None
    if "recurrence_rule" in data:
        data["is_recurring"] = bool(data["recurrence_rule"])
    if "visibility" in data and data["visibility"] is not None:
        data["is_public"] = data["visibility"] == "public"
    return data


def _plain(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value[:200]
    return value


def _changes(before: dict, after: dict) -> dict:
    return {
        key: {"from": _plain(before.get(key)), "to": _plain(after.get(key))}
        for key in _DIFF_FIELDS
        if key in after and _plain(before.get(key)) != _plain(after.get(key))
    }


async def create_event(db: Prisma, tenant_id: str, created_by_user_id: str, payload: ChurchEventCreate) -> dict:
    """New events always start as drafts — publishing goes through review."""
    await _check_group(db, payload.group_id)
    await _check_unit(db, payload.unit_id)
    _, zone = await _tz(db)
    data = _event_data(payload, partial=False)
    data["start_date_time"] = _to_utc(payload.start_date_time, zone)
    if payload.end_date_time:
        data["end_date_time"] = _to_utc(payload.end_date_time, zone)
    if data.get("is_all_day"):
        data["start_date_time"], data["end_date_time"] = _all_day_bounds(
            data["start_date_time"], data.get("end_date_time"), zone
        )
    if data.get("end_date_time") and data["end_date_time"] <= data["start_date_time"]:
        raise InvalidRangeError("The event must end after it starts")
    data["status"] = "draft"
    event = await repository.create_event(db, tenant_id, created_by_user_id, data)
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=created_by_user_id,
        action="event.created",
        resource_type="event",
        resource_id=event.id,
        metadata={
            "title": _plain(data["title"]),
            "start": _plain(data["start_date_time"]),
            "visibility": data.get("visibility"),
        },
    )
    return await get_event(db, event.id)


def _comparable(value):
    """A value in a form that compares equal whether it came from the request or the database."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat() if value.tzinfo else value.replace(tzinfo=UTC).isoformat()
    if isinstance(value, Json):
        return value.data
    if isinstance(value, str):
        return value
    return value


def _stored(existing: dict, key: str):
    value = existing.get(key)
    if key == "theme":
        return json.loads(value or "{}") if isinstance(value, str) else (value or {})
    if key in ("start_date_time", "end_date_time") and value is not None:
        return _dt(value).isoformat()
    return value


#: Changes that do not alter what reviewers looked at, so they keep a review in progress.
_REVIEW_NEUTRAL = {"theme"}


async def update_event(
    db: Prisma, tenant_id: str, actor_user_id: str, event_id: str, payload: ChurchEventUpdate
) -> dict:
    await repository.lock_event(db, event_id)  # one edit at a time; makes the stale-edit check reliable
    existing = await repository.get_event_row(db, event_id)
    if existing is None:
        raise NotFoundError("No such gathering")
    if existing["status"] in ("canceled", "completed"):
        raise ConflictError("A canceled or completed gathering can't be edited")
    if payload.expected_updated_at is not None and _dt(payload.expected_updated_at) != _dt(existing["updated_at"]):
        raise EditConflictError(
            "Someone else changed this gathering while you were editing. Reload to see their changes."
        )
    await _check_group(db, payload.group_id)
    if "unit_id" in payload.model_fields_set:
        await _check_unit(db, payload.unit_id)
    _, zone = await _tz(db)
    data = _event_data(payload, partial=True)
    if payload.start_date_time:
        data["start_date_time"] = _to_utc(payload.start_date_time, zone)
    if payload.end_date_time:
        data["end_date_time"] = _to_utc(payload.end_date_time, zone)
    merged_visibility = data.get("visibility", existing["visibility"])
    merged_group = data.get("group_id", existing["group_id"])
    if merged_visibility == "private" and not merged_group:
        raise ConflictError("A private event belongs to a group — choose which one")
    merged_start = data.get("start_date_time", _dt(existing["start_date_time"]))
    merged_end = (
        data["end_date_time"]
        if "end_date_time" in data
        else (_dt(existing["end_date_time"]) if existing["end_date_time"] else None)
    )
    merged_all_day = data.get("is_all_day", bool(existing["is_all_day"]))
    if merged_all_day and ({"start_date_time", "end_date_time", "is_all_day"} & data.keys()):
        data["start_date_time"], data["end_date_time"] = _all_day_bounds(merged_start, merged_end, zone)
        merged_start, merged_end = data["start_date_time"], data["end_date_time"]
    if merged_end is not None and _dt(merged_end) <= _dt(merged_start):
        raise InvalidRangeError("The event must end after it starts")

    # What actually changed. Saving a form without touching it changes nothing: no write,
    # no audit entry, and above all no wiping of a review that is in progress.
    changed_keys = [
        k
        for k, v in data.items()
        if k not in ("is_recurring", "is_public") and _comparable(v) != _comparable(_stored(existing, k))
    ]
    if not changed_keys:
        return await get_event(db, event_id)
    data = {k: v for k, v in data.items() if k in changed_keys or k in ("is_recurring", "is_public")}
    before = {key: existing.get(key) for key in _DIFF_FIELDS}
    if existing["status"] == "pending_review" and not set(changed_keys) <= _REVIEW_NEUTRAL:
        # What the reviewers are looking at changed: they review it again.
        data["status"] = "draft"
        await repository.clear_reviews(db, event_id)
    await repository.update_event(db, event_id, data)
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action="event.updated",
        resource_type="event",
        resource_id=event_id,
        metadata={
            "status": existing["status"],
            "changed": _changes(before, data),
            "fields": sorted(k for k in changed_keys if k not in _DIFF_FIELDS),
        },
    )
    return await get_event(db, event_id)


# ───────────────────────────── Review workflow ─────────────────────────────


async def _tell(
    db: Prisma,
    tenant_id: str,
    event,
    user_ids: list[str],
    *,
    actor_user_id: str,
    type_: str,
    title: str,
    body: str,
    key: str,
) -> None:
    """Tell people about a workflow step on an event. Never the person who did it;
    `key` makes a repeated step a no-op. Pointers only: the title of the event and
    who acted, never a reviewer's comment (that stays in the event's history)."""
    audience = [u for u in dict.fromkeys(user_ids) if u != actor_user_id]
    if audience:
        await notifications_service.notify(
            db,
            tenant_id,
            audience,
            type_,
            title,
            body,
            action_url=f"/staff/events/{event.id}",
            resource=("event", str(event.id)),
            dedupe_key=key,
            actor_user_id=actor_user_id,
        )


async def _author_and_reviewers(db: Prisma, event) -> list[str]:
    reviewers = [str(r["reviewer_user_id"]) for r in await repository.list_reviews(db, event.id)]
    return [str(event.created_by_user_id), *reviewers]


async def reviewer_options(db: Prisma, exclude_user_id: str) -> list[dict]:
    out = []
    for row in await repository.reviewer_candidates(db):
        if row["user_id"] == exclude_user_id:
            continue
        if "events:review" in await resolve_permissions(db, row["role_id"]):
            out.append({"user_id": row["user_id"], "name": row["name"], "role_name": row["role_name"]})
    return out


async def submit_for_review(
    db: Prisma, tenant_id: str, event_id: str, submitter_user_id: str, reviewer_ids: list[str]
) -> dict:
    event = await repository.get_event(db, event_id)
    if event is None:
        raise NotFoundError("No such gathering")
    if event.status not in ("draft", "changes_requested"):
        raise ConflictError("Only a draft (or one with requested changes) can be sent for review")
    allowed = {o["user_id"] for o in await reviewer_options(db, submitter_user_id)}
    reviewers = list(dict.fromkeys(reviewer_ids))
    invalid = [r for r in reviewers if r not in allowed]
    if invalid:
        raise ConflictError(
            "Reviewers must be other team members allowed to review gatherings", code="invalid_reviewer"
        )
    await repository.clear_reviews(db, event_id)
    for reviewer in reviewers:
        await repository.create_review(db, tenant_id=tenant_id, event_id=event_id, reviewer_user_id=reviewer)
    submitted_at = datetime.now(UTC)
    await repository.update_event(db, event_id, {"status": "pending_review", "submitted_at": submitted_at})
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=submitter_user_id,
        action="event.submitted",
        resource_type="event",
        resource_id=event_id,
        metadata={"reviewers": reviewers},
    )
    await _tell(
        db,
        tenant_id,
        event,
        reviewers,
        actor_user_id=submitter_user_id,
        type_="approval",
        title="Gathering waiting for your review",
        body=f'{await repository.user_name(db, submitter_user_id)} sent "{event.title}" for your review.',
        # One per submission: sending it back for changes and resubmitting asks again.
        key=f"event.review_requested:{event_id}:{submitted_at.isoformat()}",
    )
    return await get_event(db, event_id)


async def decide_review(
    db: Prisma, tenant_id: str, event_id: str, reviewer_user_id: str, decision: str, comment: str | None
) -> dict:
    """An assigned reviewer's decision. One "changes requested" sends it back;
    it publishes once every assigned reviewer has approved."""
    event = await repository.get_event(db, event_id)
    if event is None or event.status != "pending_review":
        raise NotFoundError("Nothing to review here")
    review = await repository.find_review(db, event_id, reviewer_user_id)
    if review is None:
        # Not assigned to you: indistinguishable from not existing.
        raise NotFoundError("Nothing to review here")
    if review.status != "pending":
        raise ConflictError("You've already reviewed this")
    now = datetime.now(UTC)
    await repository.record_review_decision(db, review.id, {"status": decision, "comment": comment, "decided_at": now})
    if decision == "changes_requested":
        await repository.update_event(db, event_id, {"status": "changes_requested"})
        await _tell(
            db,
            tenant_id,
            event,
            [str(event.created_by_user_id)],
            actor_user_id=reviewer_user_id,
            type_="event",
            title="Changes requested on your gathering",
            body=f'{await repository.user_name(db, reviewer_user_id)} asked for changes to "{event.title}". '
            "Open it to see what they wrote.",
            key=f"event.changes_requested:{review.id}",  # a review row is per submission
        )
    elif await repository.count_reviews_not_approved(db, event_id) == 0:
        await repository.update_event(db, event_id, {"status": "published", "published_at": now})
        await write_audit(
            db,
            tenant_id=tenant_id,
            actor_user_id=reviewer_user_id,
            action="event.published",
            resource_type="event",
            resource_id=event_id,
            metadata={"via": "review"},
        )
        # The last approval is what counts for the author; one reviewer approving
        # while others are still to decide is not announced.
        await _tell(
            db,
            tenant_id,
            event,
            await _author_and_reviewers(db, event),
            actor_user_id=reviewer_user_id,
            type_="event",
            title="Gathering approved and published",
            body=f'"{event.title}" was approved by its reviewers and is now published.',
            key=f"event.published:{event_id}",
        )
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=reviewer_user_id,
        action=f"event.review_{decision}",
        resource_type="event",
        resource_id=event_id,
        metadata={"comment": comment},
    )
    return await get_event(db, event_id)


async def publish_directly(db: Prisma, tenant_id: str, event_id: str, actor_user_id: str) -> dict:
    """For reviewers themselves: publish without a separate review."""
    event = await repository.get_event(db, event_id)
    if event is None:
        raise NotFoundError("No such gathering")
    if event.status not in ("draft", "changes_requested", "pending_review"):
        raise ConflictError(f"A {event.status.replace('_', ' ')} gathering can't be published")
    await repository.update_event(db, event_id, {"status": "published", "published_at": datetime.now(UTC)})
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action="event.published_directly",
        resource_type="event",
        resource_id=event_id,
    )
    await _tell(
        db,
        tenant_id,
        event,
        await _author_and_reviewers(db, event),
        actor_user_id=actor_user_id,
        type_="event",
        title="Gathering published",
        body=f'{await repository.user_name(db, actor_user_id)} published "{event.title}".',
        key=f"event.published:{event_id}",
    )
    return await get_event(db, event_id)


async def cancel_event(db: Prisma, tenant_id: str, event_id: str, actor_user_id: str) -> dict:
    event = await repository.get_event(db, event_id)
    if event is None:
        raise NotFoundError("No such gathering")
    if event.status in ("canceled", "completed"):
        raise ConflictError("This gathering is already closed")
    await repository.update_event(db, event_id, {"status": "canceled"})
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action="event.canceled",
        resource_type="event",
        resource_id=event_id,
    )
    await _tell(
        db,
        tenant_id,
        event,
        await _author_and_reviewers(db, event),
        actor_user_id=actor_user_id,
        type_="event",
        title="Gathering canceled",
        body=f'{await repository.user_name(db, actor_user_id)} canceled "{event.title}".',
        key=f"event.canceled:{event_id}",
    )
    return await get_event(db, event_id)


async def review_queue(db: Prisma, reviewer_user_id: str) -> list[dict]:
    _, zone = await _tz(db)
    return [_decorate(r, zone=zone) for r in await repository.review_queue(db, reviewer_user_id)]


async def occurrences(
    db: Prisma, event_id: str, window_start: datetime, window_end: datetime, viewer: Viewer | None = None
) -> list[datetime]:
    await _require_visible(db, event_id, viewer)
    event = await repository.get_event(db, event_id)
    if event is None:
        raise NotFoundError("No such gathering")
    _, zone = await _tz(db)
    window_start, window_end = _to_utc(window_start, zone), _to_utc(window_end, zone)
    if window_end < window_start:
        raise BadWindowError("The window ends before it starts")
    if window_end - window_start > timedelta(days=MAX_OCCURRENCE_WINDOW_DAYS):
        raise BadWindowError("Ask for at most about a year at a time")
    return recurrence.occurrences(
        event.recurrence_rule, event.start_date_time, window_start=window_start, window_end=window_end, tz=zone
    )


async def events_for_member(db: Prisma, member_id: str | None) -> list[dict]:
    _, zone = await _tz(db)
    return [_decorate(r, zone=zone) for r in await repository.list_events_for_member(db, member_id)]


async def public_events_for_church(db: Prisma, slug: str) -> list[dict]:
    """A live church's published, public gatherings, read under RLS exactly
    like a tenant session."""
    org = await tenant_service.get_live_org_by_slug(db, slug)
    await set_tenant_context(db, org.id)
    return await public_events(db)


async def public_events(db: Prisma) -> list[dict]:
    now = datetime.now(UTC)
    _, zone = await _tz(db)
    out = []
    for row in await repository.list_public_events(db):
        decorated = _decorate(row, now, zone)
        if decorated["next_occurrence"] is not None:
            out.append(decorated)
    return sorted(out, key=lambda e: e["next_occurrence"])


# ───────────────────────────── Calendar window ─────────────────────────────


def _window(start: datetime, end: datetime, zone: tzinfo) -> tuple[datetime, datetime]:
    start, end = _to_utc(start, zone), _to_utc(end, zone)
    if end <= start:
        raise BadWindowError("The window must end after it starts")
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise BadWindowError(f"Ask for at most {MAX_WINDOW_DAYS} days at a time")
    return start, end


def _expand(rows: list[dict], start: datetime, end: datetime, zone: tzinfo) -> tuple[list[dict], bool]:
    """Every dated appearance of each event inside [start, end], ordered by time.
    A multi-day or long event that began before the window still counts while it
    overlaps it."""
    out: list[dict] = []
    for row in rows:
        first = _dt(row["start_date_time"])
        last = _dt(row["end_date_time"]) if row.get("end_date_time") else None
        all_day = bool(row.get("is_all_day"))
        duration = (last - first) if last else None
        span_days = max(1, round(duration / timedelta(days=1))) if (all_day and duration) else 1
        reach = duration or timedelta(0)
        starts = recurrence.occurrences(
            row.get("recurrence_rule"), first, window_start=start - reach, window_end=end, tz=zone
        )
        for occurrence in starts:
            if all_day:
                day = occurrence.astimezone(zone).date()
                finish = _local_midnight(day + timedelta(days=span_days), zone)
            else:
                finish = occurrence + duration if duration else None
            # An event that ends exactly as the window opens is not in it.
            if (finish <= start) if finish else (occurrence < start):
                continue
            if occurrence >= end:  # the window is [start, end): a date starting as it closes belongs to the next one
                continue
            out.append({**row, "occurrence_start": occurrence, "occurrence_end": finish})
    out.sort(key=lambda o: (o["occurrence_start"], str(o["id"])))
    return out[:MAX_CALENDAR_ITEMS], len(out) > MAX_CALENDAR_ITEMS


async def calendar(
    db: Prisma,
    viewer: Viewer,
    *,
    start: datetime,
    end: datetime,
    type: str | None = None,
    group_id: str | None = None,
    unit_id: str | None = None,
    status: str | None = None,
    in_scope: Callable[[str | None], bool] | None = None,
) -> dict:
    """Dated appearances of events in a window, for a month or agenda view. What
    a viewer may see is decided here, in one place, for staff and members alike.
    `in_scope` (a branch-scoped caller's unit rule) is applied before the size cap,
    so other branches' events can never crowd this branch's out of the response."""
    tzname, zone = await _tz(db)
    start, end = _window(start, end, zone)
    rows = await repository.list_events_in_window(
        db,
        start=start,
        end=end,
        restrict_to_member=viewer.restricted,
        member_id=viewer.member_id,
        type=type,
        group_id=group_id,
        unit_id=unit_id,
        status=status,
    )
    if in_scope is not None:
        rows = [r for r in rows if in_scope(r["unit_id"])]
    items, truncated = _expand(rows, start, end, zone)
    return {
        "timezone": tzname,
        "window_start": start,
        "window_end": end,
        "truncated": truncated,
        "occurrences": [
            {
                "event_id": str(o["id"]),
                "title": o["title"],
                "type": o["type"],
                "status": o["status"],
                "visibility": o["visibility"],
                "unit_id": o["unit_id"],
                "group_id": o["group_id"],
                "group_name": o["group_name"],
                "location": o["location"],
                "occurrence_start": o["occurrence_start"],
                "occurrence_end": o["occurrence_end"],
                "is_all_day": bool(o["is_all_day"]),
                "is_recurring": bool(o["is_recurring"]),
                "accent_color": o.get("accent_color"),
            }
            for o in items
        ],
    }


async def public_calendar(db: Prisma, slug: str, *, start: datetime, end: datetime) -> dict:
    """A live church's published public events in a window — only what the
    public page may show (no status, group, unit or visibility)."""
    org = await tenant_service.get_live_org_by_slug(db, slug)
    await set_tenant_context(db, org.id)
    tzname, zone = await _tz(db)
    start, end = _window(start, end, zone)
    rows = await repository.list_public_events_in_window(db, start=start, end=end)
    items, truncated = _expand(rows, start, end, zone)
    return {
        "timezone": tzname,
        "window_start": start,
        "window_end": end,
        "truncated": truncated,
        "occurrences": [
            {
                "event_id": str(o["id"]),
                "title": o["title"],
                "type": o["type"],
                "location": o["location"],
                "occurrence_start": o["occurrence_start"],
                "occurrence_end": o["occurrence_end"],
                "is_all_day": bool(o["is_all_day"]),
                "is_recurring": bool(o["is_recurring"]),
                "accent_color": o.get("accent_color"),
            }
            for o in items
        ],
    }


async def event_history(db: Prisma, event_id: str) -> list[dict]:
    out = []
    for row in await history_for(db, "event", event_id):
        metadata = row.get("metadata")
        if isinstance(metadata, str):
            metadata = json.loads(metadata or "null")
        out.append({**row, "metadata": metadata})
    return out


async def get_attendance_summary(db: Prisma, event_id: str) -> dict:
    event = await repository.get_event(db, event_id)
    if event is None:
        raise NotFoundError("No such gathering")
    present = await repository.list_present_members(db, event_id)
    absent = await repository.list_absent_members(db, event_id, event.unit_id)
    total_expected = len(present) + len(absent)
    rate = (len(present) / total_expected) if total_expected else 0.0
    return {
        "event_id": event_id,
        "total_expected": total_expected,
        "total_present": len(present),
        "attendance_rate": rate,
        "present_members": present,
        "absent_members": absent,
    }


async def mark_present(db: Prisma, tenant_id: str, marked_by_user_id: str, payload: AttendanceMarkInput) -> dict:
    event = await repository.get_event(db, payload.event_id)
    if event is None:
        raise NotFoundError("No such gathering")
    existing = await repository.get_attendance_record(db, payload.event_id, payload.member_id)
    if existing is not None:
        return existing
    record = await repository.mark_present(
        db,
        tenant_id=tenant_id,
        event_id=payload.event_id,
        member_id=payload.member_id,
        marked_by_user_id=marked_by_user_id,
    )
    return record


async def unmark_present(db: Prisma, event_id: str, member_id: str) -> None:
    await repository.unmark_present(db, event_id, member_id)


async def submit_headcount(db: Prisma, tenant_id: str, marked_by_user_id: str, payload: HeadcountEntry) -> dict:
    event = await repository.get_event(db, payload.event_id)
    if event is None:
        raise NotFoundError("No such gathering")
    if payload.total != payload.adults + payload.children:
        raise ConflictError("Adults + children must equal the total")
    return await repository.submit_headcount(
        db,
        tenant_id=tenant_id,
        event_id=payload.event_id,
        adults=payload.adults,
        children=payload.children,
        total=payload.total,
        marked_by_user_id=marked_by_user_id,
    )


# ───────────────────────────── Schedule collisions ─────────────────────────────


async def check_collisions(db: Prisma, payload: CollisionCheckInput) -> list[dict]:
    if not await groups_service.group_exists(db, payload.group_id):
        raise NotFoundError("No such group")
    end = payload.end_date_time or (payload.start_date_time + timedelta(hours=2))
    rows = await repository.find_colliding_events(
        db, group_id=payload.group_id, start=payload.start_date_time, end=end, exclude_event_id=payload.exclude_event_id
    )
    out = []
    for r in rows:
        candidates = await repository.key_participants_for(db, group_ids=[payload.group_id, r["group_id"]])
        # Someone leading both colliding groups appears once per group here —
        # they're still one person to ask, not two.
        key_participants = list({c["member_id"]: c for c in candidates}.values())
        out.append({**r, "key_participants": key_participants})
    return out


async def notify_key_participants(
    db: Prisma, tenant_id: str, actor_user_id: str, payload: NotifyKeyParticipantsInput
) -> int:
    """Only reaches people who actually have a login — a leader without one
    needs a phone call or a word after service, not a silent no-op, so the
    count returned is who it newly reached, not who was asked to be. Someone
    already asked about this same clash isn't asked again (the notification
    service dedupes on the pair of gatherings), so a second click reaches 0."""
    event = await repository.get_event(db, payload.event_id)
    colliding = await repository.get_event(db, payload.colliding_event_id)
    if event is None or colliding is None:
        raise NotFoundError("No such gathering")

    candidates = await repository.key_participants_for(db, group_ids=[event.group_id, colliding.group_id])
    wanted = set(payload.member_ids)
    # A person leading both colliding groups appears once per group here; the
    # notification service sends each user one notification, not two identical ones.
    user_ids = [c["user_id"] for c in candidates if c["member_id"] in wanted and c["user_id"]]

    event_group = await groups_service.get_group_record(db, event.group_id)
    colliding_group = await groups_service.get_group_record(db, colliding.group_id)
    when_a = event.start_date_time.strftime("%a %d %b, %H:%M")
    when_b = colliding.start_date_time.strftime("%a %d %b, %H:%M")
    body = (
        f'Two gatherings you\'re tied to overlap: "{event.title}" ({when_a}, {event_group.name}) and '
        f'"{colliding.title}" ({when_b}, {colliding_group.name}). Let both group leaders know which one '
        "you'll be at."
    )
    result = await notifications_service.notify(
        db,
        tenant_id,
        user_ids,
        "event",
        "Scheduling clash — which will you attend?",
        body,
        action_url=f"/staff/events/{event.id}",
        resource=("event", str(event.id)),
        # The same clash, whichever of the two gatherings it was raised from.
        dedupe_key="event.clash:" + ":".join(sorted([str(event.id), str(colliding.id)])),
        actor_user_id=actor_user_id,
    )
    return result.created


# ───────────────────────────── Registration forms ─────────────────────────────


async def get_registration_fields(db: Prisma, event_id: str, viewer: Viewer | None = None) -> list[dict]:
    await _require_visible(db, event_id, viewer)
    return await forms_service.owned_fields(db, OWNER_EVENT, event_id)


async def _form_context(db: Prisma, event_id: str) -> dict:
    event = await repository.event_form_context(db, event_id)
    if event is None:
        raise NotFoundError("No such event")
    return event


async def set_registration_fields(
    db: Prisma, tenant_id: str, event_id: str, fields: list[dict], actor_user_id: str
) -> list[dict]:
    event = await _form_context(db, event_id)
    return await forms_service.set_owned_fields(
        db,
        tenant_id,
        OWNER_EVENT,
        event_id,
        title=event["title"],
        unit_id=event["unit_id"],
        fields=fields,
        actor_user_id=actor_user_id,
    )


async def registration_status(db: Prisma, event_id: str) -> str:
    return await forms_service.owned_status(db, OWNER_EVENT, event_id)


async def set_registration_status(db: Prisma, tenant_id: str, event_id: str, status: str, actor_user_id: str) -> str:
    event = await _form_context(db, event_id)
    return await forms_service.set_owned_status(
        db,
        tenant_id,
        OWNER_EVENT,
        event_id,
        status,
        title=event["title"],
        unit_id=event["unit_id"],
        actor_user_id=actor_user_id,
    )


async def list_registrations(db: Prisma, event_id: str) -> list[dict]:
    return await forms_service.owned_submissions(db, OWNER_EVENT, event_id)


async def export_registrations(db: Prisma, tenant_id: str, event_id: str, actor_user_id: str) -> tuple[str, str]:
    event = await _form_context(db, event_id)
    return await forms_service.export_owned(db, tenant_id, actor_user_id, OWNER_EVENT, event_id, title=event["title"])


async def public_event_detail(db: Prisma, slug: str, event_id: str) -> dict:
    org = await tenant_service.get_live_org_by_slug(db, slug)
    await set_tenant_context(db, org.id)
    row = await repository.get_public_event_row(db, event_id)
    if row is None:
        raise NotFoundError("No such gathering")
    _, zone = await _tz(db)
    decorated = _decorate(row, zone=zone)
    decorated["registration_fields"] = await forms_service.owned_fields(db, OWNER_EVENT, event_id)
    decorated["registration_open"] = await forms_service.owned_is_open(db, OWNER_EVENT, event_id)
    return decorated


async def submit_registration(db: Prisma, slug: str, event_id: str, payload: dict) -> dict:
    org = await tenant_service.get_live_org_by_slug(db, slug)
    await set_tenant_context(db, org.id)
    event = await repository.get_public_event_row(db, event_id)
    if event is None:
        raise NotFoundError("No such gathering")
    try:
        return await forms_service.submit_owned(
            db,
            org.id,
            OWNER_EVENT,
            event_id,
            payload,
            title=event["title"],
            unit_id=event["unit_id"],
            # Whoever runs the event hears that someone signed up (a pointer, never the answers).
            notify_user_ids=[str(event["created_by_user_id"])] if event["created_by_user_id"] else [],
            notify_link=f"/staff/events/{event_id}/responses",
            notify_type="event",
        )
    except FormClosedError:
        raise ConflictError("Sign-ups for this gathering are closed", code="registration_closed") from None
