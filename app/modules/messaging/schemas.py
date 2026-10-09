from datetime import datetime

from pydantic import BaseModel


class AudienceFilter(BaseModel):
    unit_ids: list[str] | None = None
    statuses: list[str] | None = None
    tags: list[str] | None = None
    estimated_count: int | None = None


class AnnouncementRead(BaseModel):
    id: str
    tenant_id: str
    title: str
    body: str
    is_pinned: bool
    status: str
    channels: list[str]
    audience_filter: dict | None
    scheduled_at: datetime | None
    sent_at: datetime | None
    author_user_id: str
    author_name: str | None = None
    sent_count: int | None
    delivered_count: int | None
    opened_count: int | None
    created_at: datetime
    updated_at: datetime


class AnnouncementCreate(BaseModel):
    title: str
    body: str
    is_pinned: bool = False
    status: str = "draft"
    channels: list[str] = []
    audience_filter: AudienceFilter | None = None
    scheduled_at: datetime | None = None


class MessageTemplateRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    subject: str | None
    body: str
    category: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class NotificationRead(BaseModel):
    id: str
    user_id: str
    type: str
    title: str
    body: str
    is_read: bool
    action_url: str | None
    created_at: datetime


class NotificationPreferencesRead(BaseModel):
    user_id: str
    preferences: dict


class NotificationPreferencesUpdate(BaseModel):
    preferences: dict
