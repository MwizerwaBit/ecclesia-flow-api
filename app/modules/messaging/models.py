import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UpdatedAtMixin, UUIDPkMixin


class Announcement(UUIDPkMixin, TenantMixin, TimestampMixin, UpdatedAtMixin, Base):
    __tablename__ = "announcements"
    __table_args__ = (
        CheckConstraint("status in ('draft','scheduled','sent','archived')", name="announcements_status_check"),
    )

    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    channels: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    audience_filter: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    author_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    sent_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opened_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MessageTemplate(UUIDPkMixin, TenantMixin, TimestampMixin, UpdatedAtMixin, Base):
    __tablename__ = "message_templates"

    name: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Notification(UUIDPkMixin, TenantMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "type in ('pastoral_alert','finance_alert','system','announcement')",
            name="notifications_type_check",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    action_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
