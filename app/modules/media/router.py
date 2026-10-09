from fastapi import APIRouter, File, Query, UploadFile

from app.core.authz import CurrentScope
from app.core.deps import CurrentClaims, TenantDb
from app.core.pagination import PaginatedRoute
from app.modules.media import service
from app.modules.media.schemas import MediaAssetRead

router = APIRouter(route_class=PaginatedRoute, prefix="/media", tags=["media"])

# Deliberately gated on authentication only, not a specific permission: media
# is a generic, cross-cutting utility (avatar upload is self-service for
# every member; org logo/certificate-background/event-banner uploads are
# already permission-checked by their OWNING screen — org:settings,
# certificates:create, events:update respectively — before they ever call
# this endpoint). A single hard-coded gate here would either block a
# member's own avatar upload or be redundant with a check the caller already
# made; tenant isolation (every asset is scoped to the caller's tenant) is
# the actual security boundary for this module.


# Media policy (ownership ABAC): anyone signed in may upload their own files;
# the church's shared library is visible to staff (dashboard:view); everyone
# else sees only what they uploaded. Deleting needs ownership or org:settings.
@router.get("", response_model=list[MediaAssetRead])
async def list_assets(scope: CurrentScope, db: TenantDb, kind: str | None = Query(default=None)):
    assets = await service.list_assets(db, kind=kind)
    if scope.has("dashboard:view"):
        return assets
    return [a for a in assets if str(a.get("uploaded_by_user_id")) == scope.user_id]


@router.post("", response_model=MediaAssetRead)
async def upload(claims: CurrentClaims, db: TenantDb, file: UploadFile = File(...)):
    return await service.upload(db, claims.tenant_id, claims.sub, file)


@router.delete("/{asset_id}", status_code=204)
async def remove(scope: CurrentScope, asset_id: str, db: TenantDb):
    await service.remove(db, asset_id, actor_user_id=scope.user_id, is_admin=scope.has("org:settings"))
