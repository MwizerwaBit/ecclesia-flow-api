"""Reports. Reading them needs `analytics:read` for now (administrators and the
church leader); a dedicated reports permission can replace it when reports grow."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.authz import CurrentScope
from app.core.deps import TenantDb, require_permission
from app.modules.reports import service
from app.modules.reports.definitions import REPORTS
from app.modules.reports.schemas import ReportRead, ReportResult

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_permission("analytics:read"))])


def _filters(request: Request) -> dict[str, str]:
    return dict(request.query_params.items())


@router.get("", response_model=list[ReportRead])
async def list_reports():
    return [
        {
            "key": r.key,
            "title": r.title,
            "description": r.description,
            "columns": [{"key": c.key, "label": c.label, "kind": c.kind} for c in r.columns],
            "filters": [{"key": f.key, "label": f.label, "kind": f.kind} for f in r.filters],
        }
        for r in REPORTS.values()
    ]


@router.get("/{key}", response_model=ReportResult)
async def run_report(key: str, request: Request, scope: CurrentScope, db: TenantDb):
    return await service.run(db, scope, key, _filters(request))


@router.get("/{key}/export.csv")
async def export_report(key: str, request: Request, scope: CurrentScope, db: TenantDb):
    result = await service.run(db, scope, key, _filters(request))
    return Response(
        service.to_csv(result),
        media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{key}.csv"'},
    )
