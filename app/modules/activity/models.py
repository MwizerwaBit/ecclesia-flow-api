import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UpdatedAtMixin, UUIDPkMixin


class Event(UUIDPkMixin, TenantMixin, TimestampMixin, UpdatedAtMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "type in ('service','meeting','event','prayer','outreach','other')", name="events_type_check"
        ),
        CheckConstraint("status in ('draft','published','canceled','completed')", name="events_status_check"),
        CheckConstraint("attendance_mode in ('individual','headcount')", name="events_attendance_mode_check"),
    )

    title: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    start_date_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recurrence_rule: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hierarchy_units.id", ondelete="SET NULL"), nullable=True
    )
    attendance_mode: Mapped[str] = mapped_column(String, nullable=False, default="individual")
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)


class AttendanceRecord(UUIDPkMixin, TenantMixin, Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        CheckConstraint("mode in ('individual','headcount')", name="attendance_records_mode_check"),
        UniqueConstraint("event_id", "member_id", name="attendance_one_per_member_key"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String, nullable=False)
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=True
    )
    adult_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    child_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    marked_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
