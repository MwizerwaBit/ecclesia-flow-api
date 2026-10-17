import re

from prisma import Prisma
from prisma.models import Organization

from app.core.database import set_tenant_context
from app.core.exceptions import NotFoundError
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

    root_unit_id = await repository.create_root_unit(db, tenant_id=org.id, name=display_name)
    await seed_default_group_roles(db, org.id)
    return org, root_unit_id


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
        await repository.create_group_role(
            db, tenant_id=tenant_id, key=key, name=name, capabilities=capabilities, rank=rank
        )


# ── Public church directory ─────────────────────────────────────────────


def _public_summary(org: Organization) -> dict:
    return {
        "slug": org.slug,
        "display_name": org.display_name,
        "country": org.country,
        "logo_url": org.logo_url,
        "primary_color": org.primary_color,
        "city": org.city,
        "verified": org.verification_status == "verified",
    }


async def apply_registration_details(db: Prisma, org_id: str, data: dict) -> None:
    await repository.update_registration_details(db, org_id, data)


async def get_live_org_by_slug(db: Prisma, slug: str) -> Organization:
    """A church that is publicly visible. Suspended or closed churches 404
    exactly like ones that don't exist (matches the frontend's
    DISCOVERABLE_STATUSES)."""
    org = await repository.get_by_slug(db, slug)
    if org is None or org.status not in repository.DISCOVERABLE_STATUSES:
        raise NotFoundError("No church found at this address")
    return org


async def search_public_churches(db: Prisma, query: str | None) -> list[dict]:
    return [_public_summary(o) for o in await repository.search_public(db, query)]


async def public_church(db: Prisma, slug: str) -> dict:
    return _public_summary(await get_live_org_by_slug(db, slug))
