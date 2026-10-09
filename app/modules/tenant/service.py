import re

from prisma import Prisma
from prisma.models import Organization

from app.core.database import set_tenant_context
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
    await set_tenant_context(db, org.id)

    root_unit = await db.hierarchyunit.create(
        data={"tenant_id": org.id, "parent_id": None, "name": display_name, "type": "Church"}
    )
    await seed_default_group_roles(db, org.id)
    return org, root_unit.id


# The built-in group roles every church starts with; churches add their own
# (Secretary, Treasurer…) alongside. Mirrors the backfill in migration
# 20261005090000.
DEFAULT_GROUP_ROLES = [
    ("leader", "Leader", ["manage_roster", "edit_group", "message", "manage_events"], 0),
    ("assistant", "Assistant", ["manage_roster", "message"], 10),
    ("member", "Member", [], 100),
]


async def seed_default_group_roles(db: Prisma, tenant_id: str) -> None:
    for key, name, capabilities, rank in DEFAULT_GROUP_ROLES:
        await db.grouprole.create(
            data={
                "tenant_id": tenant_id,
                "key": key,
                "name": name,
                "capabilities": capabilities,
                "rank": rank,
                "is_system": True,
            }
        )
