import json
from datetime import UTC, datetime, timedelta

from prisma import Json, Prisma

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.activity import recurrence, repository
from app.modules.activity.schemas import (
    AttendanceMarkInput,
    ChurchEventCreate,
    ChurchEventUpdate,
    HeadcountEntry,
)
from app.modules.audit.service import write_audit
from app.modules.rbac.service import resolve_permissions


def _decorate(row: dict, now: datetime | None = None) -> dict:
    """Adds the computed next occurrence and normalises the stored theme."""
    now = now or datetime.now(UTC)
    start = row["start_date_time"]
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    theme = row.get("theme")
    if isinstance(theme, str):
        theme = json.loads(theme or "{}")
    return {
        **row,
        "theme": theme or {},
        "next_occurrence": recurrence.next_occurrence(row.get("recurrence_rule"), start, now),
    }


async def list_events(
    db: Prisma, *, search: str | None, upcoming: bool | None, status: str | None = None
) -> list[dict]:
    return [_decorate(r) for r in await repository.list_events(db, search=search, upcoming=upcoming, status=status)]


async def get_event(db: Prisma, event_id: str) -> dict:
    row = await repository.get_event_row(db, event_id)
    if row is None:
        raise NotFoundError("No such gathering")
    decorated = _decorate(row)
    now = datetime.now(UTC)
    start = decorated["start_date_time"]
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    decorated["upcoming_occurrences"] = recurrence.occurrences(
        row.get("recurrence_rule"), start, window_start=now, window_end=now + timedelta(days=120)
    )[:12]
    decorated["reviews"] = await repository.list_reviews(db, event_id)
    return decorated


async def _check_group(db: Prisma, group_id: str | None) -> None:
    if group_id and await db.group.find_unique(where={"id": group_id}) is None:
        raise NotFoundError("No such group")


def _event_data(payload, *, partial: bool) -> dict:
    data = payload.model_dump(exclude_unset=partial, exclude={"theme", "online_url"})
    if "theme" in payload.model_fields_set or not partial:
        data["theme"] = Json(payload.theme.model_dump() if payload.theme else {})
    if "online_url" in payload.model_fields_set:
        data["online_url"] = str(payload.online_url) if payload.online_url else None
    if "recurrence_rule" in data:
        data["is_recurring"] = bool(data["recurrence_rule"])
    if "visibility" in data and data["visibility"] is not None:
        data["is_public"] = data["visibility"] == "public"
    return data


async def create_event(db: Prisma, tenant_id: str, created_by_user_id: str, payload: ChurchEventCreate) -> dict:
    """New events always start as drafts — publishing goes through review."""
    await _check_group(db, payload.group_id)
    data = _event_data(payload, partial=False)
    data["status"] = "draft"
    event = await repository.create_event(db, tenant_id, created_by_user_id, data)
    return await get_event(db, event.id)


async def update_event(db: Prisma, event_id: str, payload: ChurchEventUpdate) -> dict:
    existing = await repository.get_event(db, event_id)
    if existing is None:
        raise NotFoundError("No such gathering")
    if existing.status in ("canceled", "completed"):
        raise ConflictError("A canceled or completed gathering can't be edited")
    await _check_group(db, payload.group_id)
    data = _event_data(payload, partial=True)
    merged_visibility = data.get("visibility", existing.visibility)
    merged_group = data.get("group_id", existing.group_id)
    if merged_visibility == "private" and not merged_group:
        raise ConflictError("A private event belongs to a group — choose which one")
    if data and existing.status == "pending_review":
        # What the reviewers are looking at changed: they review it again.
        data["status"] = "draft"
        await db.eventreview.delete_many(where={"event_id": event_id})
    if data:
        await repository.update_event(db, event_id, data)
    return await get_event(db, event_id)


# ───────────────────────────── Review workflow ─────────────────────────────


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
    await db.eventreview.delete_many(where={"event_id": event_id})
    for reviewer in reviewers:
        await db.eventreview.create(data={"tenant_id": tenant_id, "event_id": event_id, "reviewer_user_id": reviewer})
    await repository.update_event(db, event_id, {"status": "pending_review", "submitted_at": datetime.now(UTC)})
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=submitter_user_id,
        action="event.submitted",
        resource_type="event",
        resource_id=event_id,
        metadata={"reviewers": reviewers},
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
    review = await db.eventreview.find_first(where={"event_id": event_id, "reviewer_user_id": reviewer_user_id})
    if review is None:
        # Not assigned to you: indistinguishable from not existing.
        raise NotFoundError("Nothing to review here")
    if review.status != "pending":
        raise ConflictError("You've already reviewed this")
    now = datetime.now(UTC)
    await db.eventreview.update(
        where={"id": review.id}, data={"status": decision, "comment": comment, "decided_at": now}
    )
    if decision == "changes_requested":
        await repository.update_event(db, event_id, {"status": "changes_requested"})
    elif await db.eventreview.count(where={"event_id": event_id, "status": {"not": "approved"}}) == 0:
        await repository.update_event(db, event_id, {"status": "published", "published_at": now})
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
    return await get_event(db, event_id)


async def review_queue(db: Prisma, reviewer_user_id: str) -> list[dict]:
    return [_decorate(r) for r in await repository.review_queue(db, reviewer_user_id)]


async def occurrences(db: Prisma, event_id: str, window_start: datetime, window_end: datetime) -> list[datetime]:
    event = await repository.get_event(db, event_id)
    if event is None:
        raise NotFoundError("No such gathering")
    if window_end - window_start > timedelta(days=400):
        raise ConflictError("Ask for at most about a year at a time")
    return recurrence.occurrences(
        event.recurrence_rule, event.start_date_time, window_start=window_start, window_end=window_end
    )


async def events_for_member(db: Prisma, member_id: str | None) -> list[dict]:
    return [_decorate(r) for r in await repository.list_events_for_member(db, member_id)]


async def public_events(db: Prisma) -> list[dict]:
    now = datetime.now(UTC)
    out = []
    for row in await repository.list_public_events(db):
        decorated = _decorate(row, now)
        if decorated["next_occurrence"] is not None:
            out.append(decorated)
    return sorted(out, key=lambda e: e["next_occurrence"])


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
