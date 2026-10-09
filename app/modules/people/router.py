"""People endpoints.

Every route is RBAC-gated by a permission, and every route that touches a
specific member is ABAC-gated by the session's unit scope (see
app.core.authz): a branch-scoped session only ever reads or writes members of
its own branch, and records outside it answer 404 as if they did not exist.
"""

from fastapi import APIRouter, Depends, Query

from app.core.authz import (
    CurrentScope,
    ensure_member_visible,
    ensure_member_writable,
    ensure_members_visible,
    ensure_self_or_permission,
)
from app.core.deps import CurrentClaims, TenantDb, require_any_permission, require_permission
from app.core.exceptions import NotFoundError
from app.core.pagination import PaginatedRoute
from app.modules.people import service
from app.modules.people.schemas import (
    HouseholdRead,
    MemberCreate,
    MemberDetail,
    MemberListItem,
    MemberUpdate,
    PastoralNoteCreate,
    PastoralNoteRead,
    PossibleDuplicate,
    SacramentalRecordCreate,
    SacramentalRecordRead,
    VisitorFollowUp,
    VisitorQuickAdd,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/members", tags=["people"])


@router.get("", response_model=list[MemberListItem], dependencies=[Depends(require_permission("members:read"))])
async def list_members(
    db: TenantDb,
    scope: CurrentScope,
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    unit_id: str | None = Query(default=None),
):
    # `unit_id` only narrows the query; the scope filter is the access boundary.
    rows = await service.list_members(db, search=search, status=status, unit_id=unit_id)
    return scope.filter_members(rows)


@router.get(
    "/duplicates",
    response_model=list[PossibleDuplicate],
    dependencies=[Depends(require_permission("members:create"))],
)
async def find_duplicates(
    db: TenantDb,
    scope: CurrentScope,
    first_name: str | None = Query(default=None),
    last_name: str | None = Query(default=None),
    phone: str | None = Query(default=None),
    email: str | None = Query(default=None),
    exclude_id: str | None = Query(default=None),
):
    """People in this church who look like the one being registered."""
    matches = await service.find_duplicates(
        db, first_name=first_name, last_name=last_name, phone=phone, email=email, exclude_id=exclude_id
    )
    return [m for m in matches if scope.member_visible(m["member"])]


@router.get("/next-envelope-number", dependencies=[Depends(require_permission("members:create"))])
async def next_envelope_number(db: TenantDb) -> dict:
    return {"envelope_number": await service.next_envelope_number(db)}


@router.get(
    "/not-seen-recently",
    response_model=list[MemberListItem],
    dependencies=[Depends(require_permission("members:read"))],
)
async def get_not_seen_recently(db: TenantDb, scope: CurrentScope):
    return scope.filter_members(await service.get_not_seen_recently(db))


@router.get(
    "/visitor-followups",
    response_model=list[VisitorFollowUp],
    dependencies=[Depends(require_permission("members:read"))],
)
async def list_visitor_followups(db: TenantDb, scope: CurrentScope):
    rows = await service.list_visitor_followups(db)
    visible = set(await ensure_members_visible(db, scope, [r["member_id"] for r in rows]))
    return [r for r in rows if r["member_id"] in visible]


@router.post(
    "/visitor-quick-add", response_model=MemberListItem, dependencies=[Depends(require_permission("members:create"))]
)
async def quick_add_visitor(payload: VisitorQuickAdd, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    return await service.quick_add_visitor(db, claims.tenant_id, payload, unit_id=scope.assignable_unit(None))


@router.get(
    "/by-user/{user_id}",
    response_model=MemberDetail | None,
    dependencies=[Depends(require_any_permission("profile:read", "members:read"))],
)
async def get_member_by_user_id(user_id: str, db: TenantDb, scope: CurrentScope):
    # Self-access: a member reaches only the record linked to their own login;
    # looking up anyone else's needs members:read and the usual unit scope.
    ensure_self_or_permission(scope, user_id, "members:read")
    member = await service.get_member_by_user_id(db, user_id)
    if member is not None and user_id != scope.user_id:
        await ensure_member_visible(db, scope, member["id"])
    return member


@router.get("/{member_id}", response_model=MemberDetail, dependencies=[Depends(require_permission("members:read"))])
async def get_member(member_id: str, db: TenantDb, scope: CurrentScope):
    await ensure_member_visible(db, scope, member_id)
    return await service.get_member_detail(db, member_id)


@router.post("", response_model=MemberDetail, dependencies=[Depends(require_permission("members:create"))])
async def create_member(payload: MemberCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    # A scoped registrar can only register people into their own branch
    # (defaulting to its root); groups outside scope are dropped.
    payload.unit_id = scope.assignable_unit(payload.unit_id)
    return await service.create_member(db, claims.tenant_id, payload, scope=scope)


@router.patch("/{member_id}", response_model=MemberDetail, dependencies=[Depends(require_permission("members:update"))])
async def update_member(member_id: str, payload: MemberUpdate, scope: CurrentScope, db: TenantDb):
    await ensure_member_writable(db, scope, member_id)
    if "unit_id" in payload.model_fields_set:
        payload.unit_id = scope.assignable_unit(payload.unit_id)
    return await service.update_member(db, member_id, payload)


@router.get(
    "/{member_id}/pastoral-notes",
    response_model=list[PastoralNoteRead],
    dependencies=[Depends(require_permission("pastoral_notes:read"))],
)
async def list_pastoral_notes(member_id: str, db: TenantDb, scope: CurrentScope):
    await ensure_member_visible(db, scope, member_id)
    notes = await service.list_pastoral_notes(db, member_id)
    # Owner attribute: a note marked private is readable only by its author.
    return [n for n in notes if not n["is_private"] or n["author_user_id"] == scope.user_id]


@router.post(
    "/{member_id}/pastoral-notes",
    response_model=PastoralNoteRead,
    dependencies=[Depends(require_permission("pastoral_notes:write"))],
)
async def create_pastoral_note(
    member_id: str, payload: PastoralNoteCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    await ensure_member_visible(db, scope, member_id)
    note = await service.create_pastoral_note(
        db, tenant_id=claims.tenant_id, member_id=member_id, author_user_id=claims.sub, payload=payload
    )
    rows = await service.list_pastoral_notes(db, member_id)
    return next(r for r in rows if r["id"] == note.id)


@router.get(
    "/{member_id}/sacramental-records",
    response_model=list[SacramentalRecordRead],
    dependencies=[Depends(require_permission("members:read"))],
)
async def list_sacramental_records(member_id: str, db: TenantDb, scope: CurrentScope):
    await ensure_member_visible(db, scope, member_id)
    return await service.list_sacramental_records(db, member_id)


@router.post(
    "/{member_id}/sacramental-records",
    response_model=SacramentalRecordRead,
    dependencies=[Depends(require_permission("members:update"))],
)
async def create_sacramental_record(
    member_id: str, payload: SacramentalRecordCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    await ensure_member_visible(db, scope, member_id)
    return await service.create_sacramental_record(db, tenant_id=claims.tenant_id, member_id=member_id, payload=payload)


households_router = APIRouter(route_class=PaginatedRoute, prefix="/households", tags=["people"])


def _scoped_household(household: dict, scope) -> dict | None:
    """A household is visible when at least one of its people is; members
    outside scope are left off its roster."""
    members = scope.filter_members(household["members"])
    if scope.is_scoped and not members:
        return None
    return {**household, "members": members}


@households_router.get(
    "", response_model=list[HouseholdRead], dependencies=[Depends(require_permission("members:read"))]
)
async def list_households(db: TenantDb, scope: CurrentScope):
    households = [_scoped_household(h, scope) for h in await service.list_households(db)]
    return [h for h in households if h is not None]


@households_router.get(
    "/{household_id}", response_model=HouseholdRead | None, dependencies=[Depends(require_permission("members:read"))]
)
async def get_household(household_id: str, db: TenantDb, scope: CurrentScope):
    household = await service.get_household(db, household_id)
    if household is None:
        return None
    visible = _scoped_household(household, scope)
    if visible is None:
        raise NotFoundError("No such household")
    return visible
