from fastapi import APIRouter, Depends, Query

from app.core.deps import PlatformClaims, PlatformDb, require_platform_admin_level
from app.core.pagination import PaginatedRoute
from app.modules.audit.service import write_audit
from app.modules.billing.schemas import BillingOverview
from app.modules.org import service as org_service
from app.modules.org.router import document_response
from app.modules.org.schemas import ModuleSubscription, OrgDocumentRead, SetModuleInput, SetTierInput
from app.modules.platform_admin import service
from app.modules.platform_admin.schemas import (
    ActivateOrgInput,
    AuditLogEntryRead,
    CreateOrgInput,
    CustomDomainRead,
    DataErasureRequestRead,
    DecideErasureRequestInput,
    FeatureFlagRead,
    ImpersonationRequest,
    ImpersonationSession,
    OrgDetail,
    OrgListItem,
    PlatformAdminRead,
    PlatformMetrics,
    ReviewDocumentInput,
    SetFeatureFlagInput,
    SetOrgStatusInput,
    SystemHealth,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/platform", tags=["platform-admin"])


@router.get("/metrics", response_model=PlatformMetrics)
async def get_metrics(db: PlatformDb):
    return await service.get_platform_metrics(db)


@router.get("/system-health", response_model=SystemHealth)
async def get_system_health(db: PlatformDb):
    return await service.get_system_health(db)


@router.get("/orgs", response_model=list[OrgListItem])
async def list_orgs(
    db: PlatformDb,
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    tier: str | None = Query(default=None),
):
    return await service.list_orgs(db, search=search, status=status, tier=tier)


@router.get("/orgs/{org_id}", response_model=OrgDetail)
async def get_org(org_id: str, db: PlatformDb):
    return await service.get_org_detail(db, org_id)


@router.post("/orgs", response_model=OrgListItem)
async def create_org(payload: CreateOrgInput, claims: PlatformClaims, db: PlatformDb):
    return await service.create_org(db, claims.sub, payload)


@router.post("/orgs/{org_id}/status", response_model=OrgListItem)
async def set_org_status(org_id: str, payload: SetOrgStatusInput, claims: PlatformClaims, db: PlatformDb):
    return await service.set_org_status(db, claims.sub, org_id, payload)


@router.get("/orgs/{org_id}/feature-flags", response_model=list[FeatureFlagRead])
async def list_feature_flags(org_id: str, db: PlatformDb):
    return await service.list_feature_flags(db, org_id)


@router.put("/orgs/{org_id}/feature-flags/{code}", response_model=FeatureFlagRead)
async def set_feature_flag(
    org_id: str, code: str, payload: SetFeatureFlagInput, claims: PlatformClaims, db: PlatformDb
):
    return await service.set_feature_flag(db, claims.sub, org_id, code, payload)


@router.post("/orgs/{org_id}/impersonate", response_model=ImpersonationSession)
async def start_impersonation(org_id: str, payload: ImpersonationRequest, claims: PlatformClaims, db: PlatformDb):
    return await service.start_impersonation(db, claims.sub, org_id, payload)


@router.get("/audit-log", response_model=list[AuditLogEntryRead])
async def list_audit_log(
    db: PlatformDb, org_id: str | None = Query(default=None), action: str | None = Query(default=None)
):
    return await service.list_audit_log(db, org_id=org_id, action=action)


@router.get("/domains", response_model=list[CustomDomainRead])
async def list_domains(db: PlatformDb):
    return await service.list_domains(db)


@router.post("/domains/{domain_id}/verify", response_model=CustomDomainRead)
async def verify_domain(domain_id: str, db: PlatformDb):
    return await service.verify_domain(db, domain_id)


@router.get("/erasure-requests", response_model=list[DataErasureRequestRead])
async def list_erasure_requests(db: PlatformDb):
    return await service.list_erasure_requests(db)


@router.post("/erasure-requests/{request_id}", response_model=DataErasureRequestRead)
async def decide_erasure_request(
    request_id: str, payload: DecideErasureRequestInput, claims: PlatformClaims, db: PlatformDb
):
    return await service.decide_erasure_request(db, claims.sub, request_id, payload)


@router.get("/admins", response_model=list[PlatformAdminRead])
async def list_platform_admins(db: PlatformDb):
    return await service.list_platform_admins(db)


# ───────────────────────────── Registration review & lifecycle ─────────────────────────────
FULL = [Depends(require_platform_admin_level("full"))]


@router.post("/orgs/{org_id}/activate", response_model=OrgListItem, dependencies=FULL)
async def activate_org(org_id: str, payload: ActivateOrgInput, claims: PlatformClaims, db: PlatformDb):
    return await service.activate_org(db, claims.sub, org_id, payload.note)


@router.get("/orgs/{org_id}/documents", response_model=list[OrgDocumentRead])
async def list_org_documents(org_id: str, db: PlatformDb):
    return await service.list_org_documents(db, org_id)


@router.get("/orgs/{org_id}/documents/{document_id}/download")
async def download_org_document(org_id: str, document_id: str, claims: PlatformClaims, db: PlatformDb):
    data, mime, name = await service.read_org_document(db, org_id, document_id)
    await write_audit(
        db,
        tenant_id=None,
        actor_user_id=claims.sub,
        action="org.certificate_downloaded",
        resource_type="org_document",
        resource_id=document_id,
    )
    return document_response(data, mime, name)


@router.post("/orgs/{org_id}/documents/{document_id}/review", response_model=list[OrgDocumentRead], dependencies=FULL)
async def review_org_document(
    org_id: str, document_id: str, payload: ReviewDocumentInput, claims: PlatformClaims, db: PlatformDb
):
    return await service.review_org_document(db, claims.sub, org_id, document_id, payload.decision, payload.note)


@router.get("/orgs/{org_id}/billing", response_model=BillingOverview)
async def org_billing(org_id: str, db: PlatformDb):
    return await service.org_billing(db, org_id)


@router.get("/orgs/{org_id}/modules", response_model=ModuleSubscription)
async def org_modules(org_id: str, db: PlatformDb):
    return await org_service.get_subscription(db, org_id)


@router.put("/orgs/{org_id}/modules/tier", response_model=ModuleSubscription, dependencies=FULL)
async def set_org_tier(org_id: str, payload: SetTierInput, claims: PlatformClaims, db: PlatformDb):
    await db.organization.update(where={"id": org_id}, data={"tier": payload.tier})
    await write_audit(
        db,
        tenant_id=org_id,
        actor_user_id=claims.sub,
        action="org.tier_set_by_platform",
        resource_type="organization",
        resource_id=org_id,
        metadata={"tier": payload.tier},
    )
    return await org_service.get_subscription(db, org_id)


@router.put("/orgs/{org_id}/modules/{module_id}", response_model=ModuleSubscription, dependencies=FULL)
async def set_org_module(org_id: str, module_id: str, payload: SetModuleInput, claims: PlatformClaims, db: PlatformDb):
    return await org_service.set_module(
        db, org_id, module_id, payload.enabled, actor_user_id=claims.sub, by_platform=True
    )
