import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.authz import CurrentScope, ensure_form_readable, ensure_form_writable
from app.core.deps import CurrentClaims, PreTenantDb, TenantDb, require_permission
from app.core.pagination import PaginatedRoute
from app.core.rate_limit import limiter
from app.core.responses import attachment_response
from app.modules.forms import service
from app.modules.forms.schemas import (
    FieldRead,
    FormCreate,
    FormListItem,
    FormRead,
    FormUpdate,
    PublicFormRead,
    SetFieldsInput,
    SubmissionDetail,
    SubmissionInput,
    SubmissionRead,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/forms", tags=["forms"])

# Reading a form's questions and managing it are separate permissions from reading
# what people answered (their names and contact details): forms:read / forms:manage /
# forms:responses. A branch-scoped session only reaches its own branch's forms and
# church-wide ones to read; changing a church-wide form needs a whole-church session.


@router.get("", response_model=list[FormListItem], dependencies=[Depends(require_permission("forms:read"))])
async def list_forms(
    db: TenantDb, scope: CurrentScope, status: Literal["draft", "published", "closed"] | None = Query(default=None)
):
    return scope.filter_shared(await service.list_forms(db, status))


@router.post(
    "",
    response_model=FormRead,
    status_code=201,
    dependencies=[Depends(require_permission("forms:manage"))],
)
async def create_form(payload: FormCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    unit_id = scope.assignable_unit(payload.unit_id)
    data = payload.model_dump(exclude={"unit_id"})
    return await service.create_form(db, claims.tenant_id, claims.sub, data, unit_id)


@router.get("/{form_id}", response_model=FormRead, dependencies=[Depends(require_permission("forms:read"))])
async def get_form(form_id: uuid.UUID, db: TenantDb, scope: CurrentScope):
    await ensure_form_readable(db, scope, str(form_id))
    return await service.get_form(db, str(form_id))


@router.patch("/{form_id}", response_model=FormRead, dependencies=[Depends(require_permission("forms:manage"))])
async def update_form(
    form_id: uuid.UUID, payload: FormUpdate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    await ensure_form_writable(db, scope, str(form_id))
    changes = payload.model_dump(exclude_unset=True)
    return await service.update_form(db, claims.tenant_id, claims.sub, str(form_id), changes)


@router.put(
    "/{form_id}/fields",
    response_model=list[FieldRead],
    dependencies=[Depends(require_permission("forms:manage"))],
)
async def set_fields(
    form_id: uuid.UUID, payload: SetFieldsInput, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    """Replaces the whole set of questions, in the order given. Responses already
    collected keep the questions they were answered against."""
    await ensure_form_writable(db, scope, str(form_id))
    fields = [f.model_dump() for f in payload.fields]
    return await service.set_fields(db, claims.tenant_id, claims.sub, str(form_id), fields)


@router.post("/{form_id}/publish", response_model=FormRead, dependencies=[Depends(require_permission("forms:manage"))])
async def publish_form(form_id: uuid.UUID, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    """Opens a draft, or reopens a closed form, to answers through its public link."""
    await ensure_form_writable(db, scope, str(form_id))
    return await service.set_status(db, claims.tenant_id, claims.sub, str(form_id), "published")


@router.post("/{form_id}/close", response_model=FormRead, dependencies=[Depends(require_permission("forms:manage"))])
async def close_form(form_id: uuid.UUID, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    await ensure_form_writable(db, scope, str(form_id))
    return await service.set_status(db, claims.tenant_id, claims.sub, str(form_id), "closed")


@router.delete("/{form_id}", status_code=204, dependencies=[Depends(require_permission("forms:manage"))])
async def delete_form(form_id: uuid.UUID, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    await ensure_form_writable(db, scope, str(form_id))
    await service.delete_form(db, claims.tenant_id, claims.sub, str(form_id))
    return Response(status_code=204)


@router.get(
    "/{form_id}/submissions",
    response_model=list[SubmissionRead],
    dependencies=[Depends(require_permission("forms:responses"))],
)
async def list_submissions(form_id: uuid.UUID, db: TenantDb, scope: CurrentScope):
    await ensure_form_readable(db, scope, str(form_id))
    return await service.list_submissions(db, str(form_id))


# Before /{submission_id}, or "export" would be read as an id.
@router.get(
    "/{form_id}/submissions/export",
    response_class=Response,
    dependencies=[Depends(require_permission("forms:responses"))],
)
async def export_submissions(form_id: uuid.UUID, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    await ensure_form_readable(db, scope, str(form_id))
    filename, body = await service.export_submissions(db, claims.tenant_id, claims.sub, str(form_id))
    return attachment_response(body.encode("utf-8"), "text/csv; charset=utf-8", filename)


@router.get(
    "/{form_id}/submissions/{submission_id}",
    response_model=SubmissionDetail,
    dependencies=[Depends(require_permission("forms:responses"))],
)
async def get_submission(
    form_id: uuid.UUID, submission_id: uuid.UUID, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    await ensure_form_readable(db, scope, str(form_id))
    return await service.get_submission(db, claims.tenant_id, claims.sub, str(form_id), str(submission_id))


# ───────────────────────────── Public ─────────────────────────────
public_router = APIRouter(route_class=PaginatedRoute, prefix="/churches", tags=["church-directory"])


@public_router.get("/{slug}/forms/{public_key}", response_model=PublicFormRead)
async def public_form(slug: str, public_key: str, db: PreTenantDb):
    """A published or closed standalone form, by its unguessable key: the questions
    and nothing else. Drafts and event forms answer 404."""
    return await service.public_form(db, slug, public_key)


@public_router.post("/{slug}/forms/{public_key}/submit", status_code=201)
@limiter.limit("5/minute;20/hour")
async def submit_form(request: Request, slug: str, public_key: str, payload: SubmissionInput, db: PreTenantDb):
    saved = await service.submit_public(db, slug, public_key, payload.model_dump())
    # A receipt, not the response: nothing someone typed comes back out of a public endpoint.
    return {"id": saved["id"], "created_at": saved["created_at"]}
