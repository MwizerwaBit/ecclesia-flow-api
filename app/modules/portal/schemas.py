"""Request/response contracts for the member portal (/me)."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints


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
