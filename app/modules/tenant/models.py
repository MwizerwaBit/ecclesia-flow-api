from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UpdatedAtMixin, UUIDPkMixin


class Organization(UUIDPkMixin, TimestampMixin, UpdatedAtMixin, Base):
    """The tenant catalog itself — no tenant_id, this table IS the list.
    Backs OrgListItem/Organisation and the public church-directory search."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("status in ('trial','active','suspended','canceled')", name="organizations_status_check"),
        CheckConstraint(
            "tier in ('free','seed','parish','growth','diocese','enterprise')", name="organizations_tier_check"
        ),
        UniqueConstraint("slug", name="organizations_slug_key"),
        UniqueConstraint("custom_domain", name="organizations_custom_domain_key"),
    )

    legal_name: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str] = mapped_column(String, nullable=False, default="en")
    status: Mapped[str] = mapped_column(String, nullable=False, default="trial")
    tier: Mapped[str] = mapped_column(String, nullable=False, default="seed")
    logo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_color: Mapped[str | None] = mapped_column(String, nullable=True)
    custom_domain: Mapped[str | None] = mapped_column(String, nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewal_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    storage_used_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
