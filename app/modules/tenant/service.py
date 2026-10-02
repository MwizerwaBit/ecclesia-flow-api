import re

from prisma import Prisma
from prisma.models import Organization

from app.modules.tenant import repository

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    base = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return base or "church"


async def unique_slug(db: Prisma, name: str) -> str:
    base = slugify(name)
    candidate = base
    suffix = 1
    while await repository.slug_exists(db, candidate):
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


async def create_organization_with_root_unit(
    db: Prisma,
    *,
    legal_name: str,
    display_name: str,
    country: str,
    currency: str,
    timezone_name: str,
) -> tuple[Organization, str]:
    """Creates the organization AND its root hierarchy unit in one
    transaction. ``app.tenant_id`` is set manually the instant the new org's
    id exists, so the hierarchy_units insert (NOT NULL tenant_id, RLS-bound)
    satisfies the policy's WITH CHECK — this is the one legitimate place a
    request handler sets that session variable itself rather than inheriting
    it from a JWT, because no JWT can exist yet for a tenant that doesn't."""
    slug = await unique_slug(db, display_name)
    org = await repository.create_organization(
        db,
        legal_name=legal_name,
        display_name=display_name,
        slug=slug,
        country=country,
        currency=currency,
        timezone_name=timezone_name,
    )
    await db.execute_raw("select set_config('app.tenant_id', $1, true)", org.id)

    root_unit = await db.hierarchyunit.create(
        data={"tenant_id": org.id, "parent_id": None, "name": display_name, "type": "Church"}
    )
    return org, root_unit.id
