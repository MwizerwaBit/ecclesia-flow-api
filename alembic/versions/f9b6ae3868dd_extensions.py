"""extensions

Revision ID: f9b6ae3868dd
Revises: 
Create Date: 2026-10-01 23:17:35.886471

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'f9b6ae3868dd'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("create extension if not exists pgcrypto")
    op.execute("create extension if not exists citext")


def downgrade() -> None:
    op.execute("drop extension if exists citext")
    op.execute("drop extension if exists pgcrypto")
