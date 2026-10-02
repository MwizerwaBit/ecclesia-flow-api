from datetime import UTC, datetime, timedelta

from prisma import Prisma
from prisma.models import Organization

DISCOVERABLE_STATUSES = ("trial", "active")


async def get_by_slug(db: Prisma, slug: str) -> Organization | None:
    return await db.organization.find_unique(where={"slug": slug})


async def get_by_id(db: Prisma, org_id: str) -> Organization | None:
    return await db.organization.find_unique(where={"id": org_id})


async def slug_exists(db: Prisma, slug: str) -> bool:
    return await get_by_slug(db, slug) is not None


async def search_public(db: Prisma, query: str | None) -> list[Organization]:
    where: dict = {"status": {"in": list(DISCOVERABLE_STATUSES)}}
    if query:
        where["OR"] = [
            {"display_name": {"contains": query, "mode": "insensitive"}},
            {"slug": {"contains": query, "mode": "insensitive"}},
        ]
    return await db.organization.find_many(where=where, order={"display_name": "asc"}, take=50)


async def create_organization(
    db: Prisma,
    *,
    legal_name: str,
    display_name: str,
    slug: str,
    country: str,
    currency: str,
    timezone_name: str,
) -> Organization:
    return await db.organization.create(
        data={
            "legal_name": legal_name,
            "display_name": display_name,
            "slug": slug,
            "country": country.upper(),
            "currency": currency.upper(),
            "timezone": timezone_name,
            "status": "trial",
            "tier": "seed",
            "trial_ends_at": datetime.now(UTC) + timedelta(days=30),
        }
    )
