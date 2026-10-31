from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

NotificationType = Literal[
    "pastoral_alert",
    "finance_alert",
    "system",
    "announcement",
    "event",
    "approval",
    "billing",
    "reminder",
    "manual",
]
NotificationChannel = Literal["email", "push", "in_app", "sms"]

#: Mirrors the "notifications_type_check" constraint (tests/test_notifications.py checks they agree).
NOTIFICATION_TYPES: tuple[str, ...] = get_args(NotificationType)
CHANNELS: tuple[str, ...] = get_args(NotificationChannel)

#: Types whose in-app copy can't be switched off: the bell is the recipient's
#: only in-app trail of a request waiting on them or of their church's plan.
IN_APP_ALWAYS: frozenset[str] = frozenset({"approval", "billing"})


class NotificationRead(BaseModel):
    id: str
    user_id: str
    type: str
    title: str
    body: str
    is_read: bool
    read_at: datetime | None = None
    action_url: str | None
    resource_type: str | None = None
    resource_id: str | None = None
    created_at: datetime


class UnreadCount(BaseModel):
    count: int


class NotificationUpdate(BaseModel):
    """Mark one notification read or unread. An empty body means "read"."""

    is_read: StrictBool = True


class NotificationPreferencesRead(BaseModel):
    user_id: str
    preferences: dict[str, dict[str, bool]]


class NotificationPreferencesUpdate(BaseModel):
    """{type: {channel: on/off}} — only the types and channels listed here are
    changed; anything left out keeps its default."""

    preferences: dict[NotificationType, dict[NotificationChannel, StrictBool]]

    @model_validator(mode="after")
    def _in_app_stays_on(self):
        for type_ in IN_APP_ALWAYS:
            if self.preferences.get(type_, {}).get("in_app") is False:
                raise ValueError(f"In-app notifications can't be turned off for '{type_}'")
        return self


# ── Manual notifications from an admin ──────────────────────────────────

BROADCAST_TITLE_MAX = 150
BROADCAST_BODY_MAX = 1000


class Audience(BaseModel):
    """Who a manual notification is for: members of these units (home unit) with
    these statuses. Empty means no narrowing on that axis, never "everyone outside
    your branch": a branch-scoped sender only ever reaches their own branch."""

    unit_ids: list[UUID] = Field(default_factory=list, max_length=200)
    statuses: list[Literal["active", "visitor", "prospect"]] = Field(default_factory=list, max_length=3)


class AudienceEstimate(BaseModel):
    members: int
    with_login: int
    without_login: int


class BroadcastCreate(BaseModel):
    #: One per send, generated when the form opens. Sending again with the same key
    #: (a double click, a retry) answers with the first result and sends nothing.
    request_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=BROADCAST_TITLE_MAX)
    body: str = Field(min_length=1, max_length=BROADCAST_BODY_MAX)
    audience: Audience

    @field_validator("title", "body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Can't be empty")
        return value


class BroadcastResult(BaseModel):
    id: str
    audience_size: int  # members matched
    without_login: int  # matched but have no login, so nothing could be sent to them
    notified: int  # notifications created
    opted_out: int  # turned this kind off
    skipped: int  # have a login that isn't an active member here
    already_sent: bool = False  # this request key was used before: nothing new was sent
