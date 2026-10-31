"""Export and import of the church's data as spreadsheets (CSV).

Rights follow the data, not the page: exporting people needs `members:export`
(which also demands a two-step-verified session, see MFA_REQUIRED_PERMISSIONS)
and importing them needs `members:create`. Downloading the empty template only
needs the import right, because it contains no data. Branch-scoped sessions see
and write only their own branch, exactly as in the directory.
"""

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response

from app.core.authz import CurrentScope
from app.core.deps import CurrentClaims, TenantDb, require_permission
from app.core.rate_limit import limiter
from app.modules.dataexchange import service
from app.modules.dataexchange.datasets import DATASETS
from app.modules.dataexchange.schemas import DatasetRead, ImportReport

router = APIRouter(prefix="/data", tags=["data"])

_CSV_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _csv(body: str, filename: str) -> Response:
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={**_CSV_HEADERS, "Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/datasets", response_model=list[DatasetRead])
async def list_datasets(claims: CurrentClaims):
    """What can be exported or imported, and what each column means. Each
    dataset says whether *this* session may import or export it."""
    allowed = set(claims.permissions)
    return [
        {
            "key": d.key,
            "label": d.label,
            "description": d.description,
            "can_import": d.import_permission in allowed,
            "can_export": d.export_permission in allowed,
            "columns": [
                {"key": c.key, "label": c.label, "required": c.required, "help": c.help, "example": c.example}
                for c in d.columns
            ],
        }
        for d in DATASETS.values()
    ]


@router.get("/templates/{dataset}.csv")
async def download_template(dataset: str, claims: CurrentClaims):
    """The empty spreadsheet: just the header row an import expects."""
    definition = service.get_dataset(dataset)
    await require_permission(definition.import_permission)(claims)
    return _csv(service.template_csv(dataset), f"{dataset}-template.csv")


@router.get("/exports/{dataset}.csv")
@limiter.limit("20/hour")
async def download_export(request: Request, dataset: str, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    """Everything in the dataset, in the same columns an import reads. With no
    data yet it is just the header, so it doubles as the template."""
    definition = service.get_dataset(dataset)
    await require_permission(definition.export_permission)(claims)
    body, _ = await service.export_csv(
        db, scope, dataset_key=dataset, tenant_id=claims.tenant_id, actor_user_id=claims.sub
    )
    return _csv(body, f"{dataset}.csv")


@router.post("/imports/{dataset}", response_model=ImportReport)
@limiter.limit("30/hour")
async def import_file(
    request: Request,
    dataset: str,
    claims: CurrentClaims,
    scope: CurrentScope,
    db: TenantDb,
    file: Annotated[UploadFile, File()],
    dry_run: Annotated[bool, Form()] = True,
    consent_confirmed: Annotated[bool, Form()] = False,
    skip_invalid: Annotated[bool, Form()] = False,
):
    """Checks the file (`dry_run`, the default, saves nothing) or imports it.
    A real import needs `consent_confirmed`, and saves nothing at all unless
    every row is good or `skip_invalid` says to leave the bad ones out."""
    definition = service.get_dataset(dataset)
    await require_permission(definition.import_permission)(claims)
    return await service.run_import(
        db,
        scope,
        dataset_key=dataset,
        tenant_id=claims.tenant_id,
        actor_user_id=claims.sub,
        data=await file.read(service.MAX_BYTES + 1),
        dry_run=dry_run,
        consent_confirmed=consent_confirmed,
        skip_invalid=skip_invalid,
    )
