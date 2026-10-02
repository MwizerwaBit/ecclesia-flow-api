import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPkMixin


class Fund(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "funds"

    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    target: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_received: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DonationBatch(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "donation_batches"
    __table_args__ = (CheckConstraint("status in ('open','closed','posted')", name="donation_batches_status_check"),)

    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    verified_total: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Donation(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "donations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="donations_amount_positive_check"),
        CheckConstraint(
            "payment_method in ('cash','check','card','transfer','mobile_money')",
            name="donations_payment_method_check",
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("donation_batches.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("members.id"), nullable=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    guest_name: Mapped[str | None] = mapped_column(String, nullable=True)
    envelope_number: Mapped[str | None] = mapped_column(String, nullable=True)
    fund_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("funds.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String, nullable=True)
    is_voided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    voided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)


class Pledge(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "pledges"
    __table_args__ = (
        CheckConstraint("frequency in ('weekly','monthly','annual','one-time')", name="pledges_frequency_check"),
        CheckConstraint("status in ('active','fulfilled','overdue','canceled')", name="pledges_status_check"),
    )

    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("members.id"), nullable=False)
    fund_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("funds.id"), nullable=False)
    pledge_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    amount_fulfilled: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    frequency: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
