import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tenant.models import Organization

DISCOVERABLE_STATUSES = ("trial", "active")


async def get_by_slug(db: AsyncSession, slug: str) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.slug == slug))
    return result.scalar_one_or_none()


async def get_by_id(db: AsyncSession, org_id: str | uuid.UUID) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    return result.scalar_one_or_none()


async def slug_exists(db: AsyncSession, slug: str) -> bool:
    return await get_by_slug(db, slug) is not None


async def search_public(db: AsyncSession, query: str | None) -> list[Organization]:
    stmt = select(Organization).where(Organization.status.in_(DISCOVERABLE_STATUSES))
    if query:
        like = f"%{query.lower()}%"
        stmt = stmt.where(
            or_(Organization.display_name.ilike(like), Organization.slug.ilike(like))
        )
    stmt = stmt.order_by(Organization.display_name).limit(50)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_organization(
    db: AsyncSession,
    *,
    legal_name: str,
    display_name: str,
    slug: str,
    country: str,
    currency: str,
    timezone_name: str,
) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        legal_name=legal_name,
        display_name=display_name,
        slug=slug,
        country=country.upper(),
        currency=currency.upper(),
        timezone=timezone_name,
        status="trial",
        tier="seed",
        trial_ends_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(org)
    await db.flush()
    return org
