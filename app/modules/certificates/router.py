from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentClaims, PreTenantDb, TenantDb, require_permission
from app.core.exceptions import NotFoundError
from app.core.pagination import PaginatedRoute
from app.modules.certificates import service
from app.modules.certificates.schemas import (
    BulkIssueInput,
    CertificateRead,
    CertificateTemplateRead,
    CertificateTemplateSave,
    IssueCertificateInput,
    PublicCertificateVerification,
    RevokeCertificateInput,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/certificates", tags=["certificates"])


@router.get(
    "/templates",
    response_model=list[CertificateTemplateRead],
    dependencies=[Depends(require_permission("certificates:read"))],
)
async def list_templates(db: TenantDb, category: str | None = Query(default=None)):
    return await service.list_templates(db, category=category)


@router.get(
    "/templates/{template_id}",
    response_model=CertificateTemplateRead,
    dependencies=[Depends(require_permission("certificates:read"))],
)
async def get_template(template_id: str, db: TenantDb):
    return await service.get_template(db, template_id)


@router.put(
    "/templates/{template_id}",
    response_model=CertificateTemplateRead,
    dependencies=[Depends(require_permission("certificates:create"))],
)
async def save_template(template_id: str, payload: CertificateTemplateSave, claims: CurrentClaims, db: TenantDb):
    payload.id = template_id
    return await service.save_template(db, claims.tenant_id, payload)


@router.post(
    "/templates",
    response_model=CertificateTemplateRead,
    dependencies=[Depends(require_permission("certificates:create"))],
)
async def create_template(payload: CertificateTemplateSave, claims: CurrentClaims, db: TenantDb):
    return await service.save_template(db, claims.tenant_id, payload)


@router.get("", response_model=list[CertificateRead], dependencies=[Depends(require_permission("certificates:read"))])
async def list_issued(db: TenantDb, member_id: str | None = Query(default=None)):
    return await service.list_issued(db, member_id=member_id)


@router.get(
    "/{certificate_id}", response_model=CertificateRead, dependencies=[Depends(require_permission("certificates:read"))]
)
async def get_issued(certificate_id: str, db: TenantDb):
    return await service.get_issued(db, certificate_id)


@router.post(
    "/issue", response_model=CertificateRead, dependencies=[Depends(require_permission("certificates:create"))]
)
async def issue(payload: IssueCertificateInput, claims: CurrentClaims, db: TenantDb):
    return await service.issue(db, claims.tenant_id, claims.sub, payload)


@router.post(
    "/bulk-issue",
    response_model=list[CertificateRead],
    dependencies=[Depends(require_permission("certificates:create"))],
)
async def bulk_issue(payload: BulkIssueInput, claims: CurrentClaims, db: TenantDb):
    return await service.bulk_issue(db, claims.tenant_id, claims.sub, payload)


@router.post(
    "/{certificate_id}/revoke",
    response_model=CertificateRead,
    dependencies=[Depends(require_permission("certificates:create"))],
)
async def revoke(certificate_id: str, payload: RevokeCertificateInput, db: TenantDb):
    return await service.revoke(db, certificate_id, payload.reason)


# Public, unauthenticated — the page behind a certificate's printed QR code.
public_router = APIRouter(route_class=PaginatedRoute, tags=["certificates-public"])


@public_router.get("/verify/{qr_hash}", response_model=PublicCertificateVerification)
async def verify(qr_hash: str, db: PreTenantDb):
    result = await service.verify(db, qr_hash)
    if result is None:
        raise NotFoundError("No certificate matches this code")
    return result
