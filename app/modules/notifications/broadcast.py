"""Manual notifications: a person with ``notifications:send`` writes a message and
picks an audience of members.

Three guards sit around the plain "notify these users":

- scope (ABAC): a branch-scoped sender reaches only their own branch. The members
  are read on the sender's own session, so the database's unit-scope policy
  already hides everyone else, and an explicit unit outside the branch is refused;
- idempotency: the sender's ``request_key`` is unique per send. A repeat answers
  with the first result and sends nothing; the notifications themselves are also
  deduped on it, so even two requests racing cannot notify anyone twice;
- a rate limit per sender, counted from the send records, so it holds across
  processes and restarts.

The audit entry carries the audience and counts, never the message.
"""

from prisma import Prisma

from app.core.authz import Scope
from app.core.exceptions import AppError, ForbiddenError, RateLimitedError
from app.modules.audit.service import write_audit
from app.modules.notifications import repository
from app.modules.notifications.schemas import Audience, BroadcastCreate
from app.modules.notifications.service import notify

MAX_SENDS_PER_HOUR = 10
RATE_WINDOW_SECONDS = 60 * 60


class EmptyAudienceError(AppError):
    status_code = 422
    code = "empty_audience"


async def _members(db: Prisma, scope: Scope, audience: Audience) -> list[dict]:
    unit_ids = [str(u) for u in audience.unit_ids]
    if scope.is_scoped:
        if any(u not in scope.unit_ids for u in unit_ids):
            raise ForbiddenError("That unit is outside your assigned branch", code="out_of_scope")
    return await repository.audience_members(db, unit_ids=unit_ids, statuses=list(audience.statuses))


def _login_ids(members: list[dict]) -> list[str]:
    return list(dict.fromkeys(str(m["user_id"]) for m in members if m["user_id"]))


async def estimate_audience(db: Prisma, scope: Scope, audience: Audience) -> dict:
    """Counts only, so a preview can't be used to read who is in a branch."""
    members = await _members(db, scope, audience)
    with_login = sum(1 for m in members if m["user_id"])
    return {"members": len(members), "with_login": with_login, "without_login": len(members) - with_login}


def _result(row: dict, *, already_sent: bool) -> dict:
    return {
        "id": str(row["id"]),
        "audience_size": row["audience_size"],
        "without_login": row["without_login"],
        "notified": row["notified"],
        "opted_out": row["opted_out"],
        "skipped": row["skipped"],
        "already_sent": already_sent,
    }


async def send(db: Prisma, scope: Scope, tenant_id: str, sender_user_id: str, payload: BroadcastCreate) -> dict:
    earlier = await repository.find_broadcast(db, tenant_id, sender_user_id, payload.request_key)
    if earlier is not None:
        return _result(earlier, already_sent=True)

    recent = await repository.count_recent_broadcasts(db, tenant_id, sender_user_id, within_seconds=RATE_WINDOW_SECONDS)
    if recent >= MAX_SENDS_PER_HOUR:
        raise RateLimitedError(
            f"You've sent {MAX_SENDS_PER_HOUR} notifications in the last hour. Try again a little later.",
            code="send_limit",
        )

    members = await _members(db, scope, payload.audience)
    if not members:
        raise EmptyAudienceError("No members match that audience")
    login_ids = _login_ids(members)
    outcome = await notify(
        db,
        tenant_id,
        login_ids,
        "manual",
        payload.title,
        payload.body,
        dedupe_key=f"manual:{sender_user_id}:{payload.request_key}",
        actor_user_id=sender_user_id,
    )
    audience = {
        "unit_ids": [str(u) for u in payload.audience.unit_ids],
        "statuses": list(payload.audience.statuses),
    }
    row = await repository.insert_broadcast(
        db,
        tenant_id,
        sender_user_id,
        payload.request_key,
        audience=audience,
        audience_size=len(members),
        without_login=sum(1 for m in members if not m["user_id"]),
        notified=outcome.created,
        opted_out=outcome.opted_out,
        skipped=outcome.skipped,
    )
    if row is None:  # a parallel request with the same key won the race
        winner = await repository.find_broadcast(db, tenant_id, sender_user_id, payload.request_key)
        return _result(winner, already_sent=True)
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=sender_user_id,
        action="notifications.broadcast_sent",
        resource_type="notification_broadcast",
        resource_id=str(row["id"]),
        metadata={
            **audience,
            "audience_size": row["audience_size"],
            "notified": row["notified"],
            "without_login": row["without_login"],
            "opted_out": row["opted_out"],
            "skipped": row["skipped"],
        },
    )
    return _result(row, already_sent=False)
