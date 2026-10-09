"""The signed-in church's own settings: profile, modules, official documents,
onboarding. Deliberately NOT behind the active-organisation gate — a church
still waiting for activation must be able to finish onboarding."""

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response

from app.core.authz import CurrentScope
from app.core.deps import CurrentClaims, TenantDb, require_any_permission, require_permission
from app.core.exceptions import ForbiddenError
from app.core.pagination import PaginatedRoute
from app.modules.org import service, terminology
from app.modules.org.schemas import (
    InvitePersonInput,
    ModuleSubscription,
    OnboardingStatus,
    OrgDocumentRead,
    OrgProfile,
    OrgProfileUpdate,
    SetModuleInput,
    SetTierInput,
)
from app.modules.org.terminology import TerminologyRead, TerminologyUpdate
from app.modules.team.schemas import TeamMemberRead

router = APIRouter(route_class=PaginatedRoute, prefix="/org", tags=["organisation"])

ANY_MEMBER = [Depends(require_any_permission("profile:read", "portal:view"))]
ONBOARDING = [Depends(require_any_permission("org:documents", "billing:manage", "team:invite"))]


@router.get("/profile", response_model=OrgProfile, dependencies=ANY_MEMBER)
async def get_profile(claims: CurrentClaims, db: TenantDb):
    return await service.get_profile(db, claims.tenant_id)


@router.patch(
    "/profile",
    response_model=OrgProfile,
    dependencies=[Depends(require_any_permission("org:settings", "org:documents"))],
)
async def update_profile(payload: OrgProfileUpdate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    # While a church is still onboarding its administrators may complete the
    # profile; once it is live, profile changes are an MFA-gated org setting.
    org = await service.get_profile(db, claims.tenant_id)
    if org.status != "pending":
        await require_permission("org:settings")(claims)
    return await service.update_profile(db, claims.tenant_id, scope.user_id, payload)


@router.get("/terminology", response_model=TerminologyRead, dependencies=ANY_MEMBER)
async def get_terminology(claims: CurrentClaims, db: TenantDb):
    return await terminology.get_terminology(db, claims.tenant_id)


@router.put("/terminology", response_model=TerminologyRead, dependencies=[Depends(require_permission("org:settings"))])
async def update_terminology(payload: TerminologyUpdate, claims: CurrentClaims, db: TenantDb):
    return await terminology.update_terminology(db, claims.tenant_id, claims.sub, payload)


@router.get("/modules", response_model=ModuleSubscription, dependencies=ANY_MEMBER)
async def get_modules(claims: CurrentClaims, db: TenantDb):
    return await service.get_subscription(db, claims.tenant_id)


@router.put(
    "/modules/tier", response_model=ModuleSubscription, dependencies=[Depends(require_permission("org:settings"))]
)
async def set_tier(_payload: SetTierInput):
    # Changing plan is a purchase, not a setting.
    raise ForbiddenError("Change your plan from Billing", code="use_billing")


@router.put(
    "/modules/{module_id}",
    response_model=ModuleSubscription,
    dependencies=[Depends(require_permission("org:settings"))],
)
async def set_module(module_id: str, payload: SetModuleInput, claims: CurrentClaims, db: TenantDb):
    return await service.set_module(
        db, claims.tenant_id, module_id, payload.enabled, actor_user_id=claims.sub, by_platform=False
    )


@router.get("/onboarding", response_model=OnboardingStatus, dependencies=ONBOARDING)
async def get_onboarding(claims: CurrentClaims, db: TenantDb):
    return await service.onboarding_status(db, claims.tenant_id)


@router.post(
    "/people-in-charge",
    response_model=TeamMemberRead,
    dependencies=[Depends(require_any_permission("team:invite", "admins:manage"))],
)
async def invite_person_in_charge(payload: InvitePersonInput, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    return await service.invite_person_in_charge(db, claims.tenant_id, scope, payload)


@router.get(
    "/documents", response_model=list[OrgDocumentRead], dependencies=[Depends(require_permission("org:documents"))]
)
async def list_documents(claims: CurrentClaims, db: TenantDb):
    return await service.list_documents(db, claims.tenant_id)


@router.post(
    "/documents/certificate",
    response_model=OrgDocumentRead,
    status_code=201,
    dependencies=[Depends(require_permission("org:documents"))],
)
async def upload_certificate(claims: CurrentClaims, db: TenantDb, file: UploadFile = File(...)):
    data = await file.read()
    return await service.upload_certificate(db, claims.tenant_id, claims.sub, file.filename or "certificate", data)


@router.get("/documents/{document_id}/download", dependencies=[Depends(require_permission("org:documents"))])
async def download_document(document_id: str, claims: CurrentClaims, db: TenantDb):
    data, mime, name = await service.read_document(db, claims.tenant_id, document_id)
    return document_response(data, mime, name)


def document_response(data: bytes, mime: str, name: str) -> Response:
    """Always an attachment, never rendered inline by the browser, never cached."""
    safe_name = "".join(c for c in name if c.isalnum() or c in "._- ")[:100] or "document"
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
