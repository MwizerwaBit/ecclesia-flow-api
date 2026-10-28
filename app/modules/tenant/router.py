from fastapi import APIRouter, Query

from app.core.deps import PreTenantDb
from app.core.pagination import PaginatedRoute
from app.modules.tenant import service
from app.modules.tenant.schemas import PublicChurchSummary

router = APIRouter(route_class=PaginatedRoute, prefix="/churches", tags=["church-directory"])


@router.get("", response_model=list[PublicChurchSummary])
async def search_churches(db: PreTenantDb, q: str | None = Query(default=None, max_length=100)):
    return await service.search_public_churches(db, q)


@router.get("/{slug}", response_model=PublicChurchSummary)
async def get_church_by_slug(slug: str, db: PreTenantDb):
    return await service.public_church(db, slug)
