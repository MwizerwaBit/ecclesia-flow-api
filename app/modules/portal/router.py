"""/me — the signed-in member's own data. Every route resolves "me" from the
token's user id; there is no way to ask for someone else's."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, StringConstraints

from app.core.deps import CurrentClaims, TenantDb, require_any_permission
from app.core.pagination import PaginatedRoute
from app.modules.activity.schemas import EventListItem
from app.modules.people.schemas import MemberDetail
from app.modules.portal import service

router = APIRouter(route_class=PaginatedRoute, prefix="/me", tags=["portal"])
SIGNED_IN = [Depends(require_any_permission("portal:view", "profile:read"))]


class MyGroup(BaseModel):
    membership_id: str
    status: Literal["invited", "active"]
    role: str
    role_name: str
    capabilities: list[str]
    rank: int
    invited_at: datetime | None
    group_id: str
    name: str
    type: str
    description: str | None
    color: str
    meeting_frequency: str
    meeting_day: str | None
    meeting_time: str | None
    meeting_location: str | None
    member_count: int


class RespondInput(BaseModel):
    accept: bool


class RosterPerson(BaseModel):
    member_id: str
    first_name: str
    last_name: str
    photo_url: str | None
    role: str
    role_name: str | None
    status: str
    note: str | None = None
    phone: str | None = None
    email: str | None = None


class MyGroupDetail(BaseModel):
    group: dict
    my_role: dict
    roster: list[RosterPerson]
    assignable_roles: list[dict]


class GroupEdit(BaseModel):
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    meeting_day: Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] | None = None
    meeting_time: Annotated[str, StringConstraints(pattern=r"^[0-2][0-9]:[0-5][0-9]$")] | None = None
    meeting_location: Annotated[str, StringConstraints(max_length=200)] | None = None


class InviteInput(BaseModel):
    member_ids: list[str]
    role: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,39}$")] = "member"


class RoleInput(BaseModel):
    role: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,39}$")]


class Candidate(BaseModel):
    id: str
    first_name: str
    last_name: str
    photo_url: str | None


@router.get("/member", response_model=MemberDetail, dependencies=SIGNED_IN)
async def my_member(claims: CurrentClaims, db: TenantDb):
    return await service.own_member(db, claims.sub)


@router.get("/groups", response_model=list[MyGroup], dependencies=SIGNED_IN)
async def my_groups(claims: CurrentClaims, db: TenantDb):
    return await service.my_groups(db, claims.sub)


@router.post("/groups/invitations/{membership_id}", response_model=list[MyGroup], dependencies=SIGNED_IN)
async def respond(membership_id: str, payload: RespondInput, claims: CurrentClaims, db: TenantDb):
    return await service.respond(db, claims.sub, membership_id, payload.accept)


@router.get("/groups/{group_id}", response_model=MyGroupDetail, dependencies=SIGNED_IN)
async def group_detail(group_id: str, claims: CurrentClaims, db: TenantDb):
    return await service.group_detail(db, claims.sub, group_id)


@router.patch("/groups/{group_id}", response_model=MyGroupDetail, dependencies=SIGNED_IN)
async def edit_group(group_id: str, payload: GroupEdit, claims: CurrentClaims, db: TenantDb):
    return await service.update_group(db, claims.sub, group_id, payload.model_dump(exclude_unset=True))


@router.get("/groups/{group_id}/candidates", response_model=list[Candidate], dependencies=SIGNED_IN)
async def candidates(
    group_id: str, claims: CurrentClaims, db: TenantDb, search: str = Query(min_length=2, max_length=60)
):
    return await service.candidates(db, claims.sub, group_id, search)


@router.post("/groups/{group_id}/invitations", response_model=MyGroupDetail, dependencies=SIGNED_IN)
async def invite(group_id: str, payload: InviteInput, claims: CurrentClaims, db: TenantDb):
    return await service.invite(db, claims.tenant_id, claims.sub, group_id, payload.member_ids, payload.role)


@router.patch("/groups/{group_id}/members/{member_id}", response_model=MyGroupDetail, dependencies=SIGNED_IN)
async def change_role(group_id: str, member_id: str, payload: RoleInput, claims: CurrentClaims, db: TenantDb):
    return await service.change_role(db, claims.sub, group_id, member_id, payload.role)


@router.delete("/groups/{group_id}/members/{member_id}", dependencies=SIGNED_IN)
async def remove(group_id: str, member_id: str, claims: CurrentClaims, db: TenantDb):
    detail = await service.remove(db, claims.sub, group_id, member_id)
    return detail if detail is not None else Response(status_code=204)


@router.get("/events", response_model=list[EventListItem], dependencies=SIGNED_IN)
async def my_events(claims: CurrentClaims, db: TenantDb):
    return await service.my_events(db, claims.sub)
