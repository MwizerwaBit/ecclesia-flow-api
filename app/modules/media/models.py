import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPkMixin


class MediaAsset(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "media_assets"
    __table_args__ = (CheckConstraint("kind in ('image','video','document')", name="media_assets_kind_check"),)

    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alt_text: Mapped[str | None] = mapped_column(String, nullable=True)


class MediaAttachment(Base):
    """Polymorphic attach — one asset reusable across event banners, member
    photos, certificate backgrounds, org logos, etc."""

    __tablename__ = "media_attachments"

    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), primary_key=True
    )
    attachable_type: Mapped[str] = mapped_column(String, primary_key=True)
    attachable_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    role: Mapped[str] = mapped_column(String, primary_key=True, default="primary")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
