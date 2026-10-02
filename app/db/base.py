"""Declarative base + the mixins every domain model is built from.

Per plan.md's Phase 0 spec: "Base declarative model with UUID primary keys,
tenant_id, and timestamp mixins."
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDPkMixin:
    """Every table's primary key: a client-unguessable UUID, never an
    auto-increment int — avoids leaking row counts/creation order and
    matches every `id: string` field already in the frontend's types."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UpdatedAtMixin:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantMixin:
    """The column that makes a table tenant-scoped, and the thing every
    migration's RLS policy is keyed off. Required (not null) — a tenant
    table with a nullable tenant_id is exactly the gap this codebase found
    and closed in `roles`/`audit_logs` (see alembic migration 0001, §RLS)."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
