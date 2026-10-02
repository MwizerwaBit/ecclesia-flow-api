import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPkMixin


class HierarchyUnit(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    """Diocese → Parish → Zone → Branch, tenant-configurable labels."""

    __tablename__ = "hierarchy_units"

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hierarchy_units.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)


class HierarchyClosure(Base):
    """Closure table: O(1) "all descendants"/"all ancestors" at any depth
    without a recursive CTE on every request (plan.md Phase 2 spec)."""

    __tablename__ = "hierarchy_closure"

    ancestor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hierarchy_units.id", ondelete="CASCADE"), primary_key=True
    )
    descendant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hierarchy_units.id", ondelete="CASCADE"), primary_key=True
    )
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
