import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from prisma import Prisma

from app.core.exceptions import NotFoundError
from app.modules.notifications import repository
from app.modules.notifications.schemas import (
    CHANNELS,
    IN_APP_ALWAYS,
    NOTIFICATION_TYPES,
    NotificationPreferencesUpdate,
)

# Every call takes the caller's own tenant and user id from the token. A
# notification that isn't the caller's is "not found", never "forbidden".


async def list_notifications(
    db: Prisma, tenant_id: str, user_id: str, *, unread_only: bool, type_: str | None, limit: int, offset: int
) -> tuple[list[dict], int]:
    return await repository.list_notifications(
        db, tenant_id, user_id, unread_only=unread_only, type_=type_, limit=limit, offset=offset
    )


async def unread_count(db: Prisma, tenant_id: str, user_id: str) -> int:
    return await repository.unread_count(db, tenant_id, user_id)


async def get_notification(db: Prisma, tenant_id: str, user_id: str, notification_id: str) -> dict:
    row = await repository.get_notification(db, tenant_id, user_id, notification_id)
    if row is None:
        raise NotFoundError("No such notification")
    return row


async def set_read(db: Prisma, tenant_id: str, user_id: str, notification_id: str, is_read: bool) -> dict:
    row = await repository.set_read(db, tenant_id, user_id, notification_id, is_read)
    if row is None:
        raise NotFoundError("No such notification")
    return row


async def mark_all_read(db: Prisma, tenant_id: str, user_id: str) -> int:
    return await repository.mark_all_read(db, tenant_id, user_id)


DEFAULT_PREFERENCES = {t: {"email": True, "push": True, "in_app": True, "sms": False} for t in NOTIFICATION_TYPES}


def with_defaults(stored: dict | None) -> dict[str, dict[str, bool]]:
    """Every type and channel, with the user's own choices laid over the defaults.
    Anything unknown in what was stored is dropped; in-app stays on where it must."""
    stored = stored or {}
    merged: dict[str, dict[str, bool]] = {}
    for type_ in NOTIFICATION_TYPES:
        chosen = stored.get(type_) if isinstance(stored.get(type_), dict) else {}
        channels = dict(DEFAULT_PREFERENCES[type_])
        channels.update({c: v for c, v in chosen.items() if c in CHANNELS and isinstance(v, bool)})
        if type_ in IN_APP_ALWAYS:
            channels["in_app"] = True
        merged[type_] = channels
    return merged


async def get_preferences(db: Prisma, user_id: str) -> dict:
    row = await repository.get_preferences(db, user_id)
    return {"user_id": user_id, "preferences": with_defaults(row["preferences"] if row else None)}


async def update_preferences(db: Prisma, user_id: str, payload: NotificationPreferencesUpdate) -> dict:
    current = (await get_preferences(db, user_id))["preferences"]
    for type_, channels in payload.preferences.items():
        current[type_].update(channels)
    row = await repository.upsert_preferences(db, user_id, current)
    return {"user_id": row["user_id"], "preferences": with_defaults(row["preferences"])}


# ───────────────────────────── Sending (called by other modules) ─────────────────────────────
#
# The one way to create a notification: no other module writes the table
# (tests/test_architecture.py checks it). It runs on the caller's own session,
# so it is part of the caller's transaction — if the action it announces rolls
# back, the notification never existed — and the church is the caller's too.
#
# What may go into a notification (title, body, action_url). It is shown in a
# list, may be mailed, and outlives the permission that allowed the action, so
# write it as a pointer, not a copy of the record:
#   - never: national/ID numbers, anything from a pastoral note, a private note
#     or a sacramental record, passwords, tokens, one-time codes, or a link that
#     carries a secret;
#   - never: another person's giving (who gave, how much), or health, family or
#     discipline details about a named person;
#   - fine: the title of an event or group the recipient can already see, a
#     date and time, the name of the person who acted, "a payment was rejected"
#     (the reason stays in the app, behind the recipient's own permission);
#   - the action_url is where the recipient re-checks their own access: the
#     destination page enforces it, the notification does not grant any.

TITLE_MAX = 150
BODY_MAX = 1000
ACTION_URL_MAX = 300
DEDUPE_KEY_MAX = 200  # the notifications_dedupe_key_check constraint

# In-app paths only: one of the app's three signed-in areas, then path
# characters. No scheme, no host, no "//", no backslash, no whitespace.
_ACTION_URL = re.compile(
    r"^/(staff|portal|board)(/[A-Za-z0-9._~!$&'()*+,;=:@%-]+)*/?(\?[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*)?$"
)
_RESOURCE_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")


@dataclass(frozen=True)
class NotifyResult:
    created: int  # new notifications
    duplicates: int  # already notified for this dedupe_key
    opted_out: int  # in-app turned off for this type
    skipped: int  # not a member of this church, not active, or not an id at all
    emails_queued: int = 0  # of the created ones, those whose email preference is on


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clip(text: str, limit: int, *, multiline: bool = False) -> str:
    """No control characters; a title is one line, a body may keep its line breaks."""
    text = _CONTROL.sub("", text)
    text = text.strip() if multiline else " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _uuid_or_none(value: str | None) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError):
        return None


def _check_action_url(url: str | None) -> str | None:
    if url is None:
        return None
    path = url.split("?", 1)[0]
    if len(url) > ACTION_URL_MAX or not _ACTION_URL.fullmatch(url) or {".", ".."} & set(path.split("/")):
        raise ValueError(f"action_url must be an in-app path like /staff/events/<id>, got {url[:40]!r}")
    return url


async def notify(
    db: Prisma,
    tenant_id: str,
    user_ids: Iterable[str],
    type_: str,
    title: str,
    body: str,
    *,
    action_url: str | None = None,
    resource: tuple[str, str] | None = None,
    dedupe_key: str | None = None,
    actor_user_id: str | None = None,
    email: bool = True,
) -> NotifyResult:
    """Notify these users in one church.

    ``dedupe_key`` names the trigger ("event.published:<event id>"): a user who
    already has a notification with that key is not notified again, so a retried
    request, an overlapping job or a double click is harmless. Leave it out only
    for something that genuinely may repeat. ``resource`` is (type, id) of what
    it is about, kept so the link can be re-checked later. ``email=False`` for a
    notification whose email is already sent another way (the in-app copy of a
    mail the caller sends itself), so nobody gets it twice.

    Recipients that can't be notified are skipped and counted, never an error: a
    leader without a login, someone who left, an id from another church. A bad
    *call* (unknown type, empty text, an external link) is a bug in the caller
    and raises ValueError before anything is written."""
    if type_ not in NOTIFICATION_TYPES:
        raise ValueError(f"Unknown notification type {type_!r}")
    title, body = _clip(title, TITLE_MAX), _clip(body, BODY_MAX, multiline=True)
    if not title or not body:
        raise ValueError("A notification needs a title and a body")
    action_url = _check_action_url(action_url)
    resource_type, resource_id = (None, None)
    if resource is not None:
        resource_type, resource_id = resource[0], _uuid_or_none(resource[1])
        if not _RESOURCE_TYPE.fullmatch(resource_type or "") or resource_id is None:
            raise ValueError("resource must be (type, uuid)")
    if dedupe_key is not None and not 1 <= len(dedupe_key) <= DEDUPE_KEY_MAX:
        raise ValueError(f"dedupe_key must be 1-{DEDUPE_KEY_MAX} characters")
    if actor_user_id is not None and _uuid_or_none(actor_user_id) is None:
        raise ValueError("actor_user_id must be a user id")

    asked = list(dict.fromkeys(user_ids))  # each person once, in the order given
    normalised = [_uuid_or_none(i) for i in asked]
    valid = list(dict.fromkeys(i for i in normalised if i))
    not_ids = normalised.count(None)
    if not valid:
        return NotifyResult(0, 0, 0, not_ids)

    eligible = await repository.eligible_recipients(db, tenant_id, valid)
    stored = await repository.stored_preferences(db, eligible) if eligible else {}
    wanted = [u for u in eligible if with_defaults(stored.get(u))[type_]["in_app"]]
    created_rows = (
        await repository.insert_notifications(
            db,
            tenant_id,
            wanted,
            type_=type_,
            title=title,
            body=body,
            action_url=action_url,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_user_id=_uuid_or_none(actor_user_id),
            dedupe_key=dedupe_key,
        )
        if wanted
        else []
    )
    # Email is queued in the same transaction (a durable outbox row); delivery.py
    # sends it after commit and records failures. In-app never waits for it.
    to_mail = [r for r in created_rows if with_defaults(stored.get(r["user_id"]))[type_]["email"]] if email else []
    queued = (
        await repository.queue_email_deliveries(
            db, tenant_id, [r["id"] for r in to_mail], [r["user_id"] for r in to_mail]
        )
        if to_mail
        else 0
    )
    created = len(created_rows)
    return NotifyResult(
        created=created,
        duplicates=len(wanted) - created,
        opted_out=len(eligible) - len(wanted),
        skipped=not_ids + len(valid) - len(eligible),
        emails_queued=queued,
    )
