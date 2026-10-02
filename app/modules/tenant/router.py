from fastapi import APIRouter, Query

from app.core.deps import PreTenantDb
from app.core.exceptions import NotFoundError
from app.modules.tenant import repository
from app.modules.tenant.models import Organization
from app.modules.tenant.schemas import PublicChurchSummary

router = APIRouter(prefix="/churches", tags=["church-directory"])


def _to_summary(org: Organization) -> PublicChurchSummary:
    return PublicChurchSummary(
        slug=org.slug,
        display_name=org.display_name,
        country=org.country,
        logo_url=org.logo_url,
        primary_color=org.primary_color,
    )


@router.get("", response_model=list[PublicChurchSummary])
async def search_churches(db: PreTenantDb, q: str | None = Query(default=None, max_length=100)):
    orgs = await repository.search_public(db, q)
    return [_to_summary(o) for o in orgs]


@router.get("/{slug}", response_model=PublicChurchSummary)
async def get_church_by_slug(slug: str, db: PreTenantDb):
    org = await repository.get_by_slug(db, slug)
    # Suspended/canceled orgs 404 like they don't exist — matches the
    # frontend's churchDirectoryService DISCOVERABLE_STATUSES behavior.
    if org is None or org.status not in ("trial", "active"):
        raise NotFoundError("No church found at this address")
    return _to_summary(org)
