from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.modules.people.schemas import MemberListItem

GroupType = Literal["ministry", "small_group", "choir", "department", "fellowship", "class", "committee", "team"]
Frequency = Literal["weekly", "fortnightly", "monthly", "irregular"]
Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
# Church-defined (group_roles); the key is what memberships store.
GroupRole = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,39}$")]
Capability = Literal["manage_roster", "edit_group", "message", "manage_events"]
MembershipStatus = Literal["invited", "active", "declined"]

GroupName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
MeetingTime = Annotated[str, StringConstraints(pattern=r"^[0-2][0-9]:[0-5][0-9]$")]


class Schedule(BaseModel):
    frequency: Frequency = "weekly"
    day: Weekday | None = None
    time: MeetingTime | None = None
    location: str | None = None


class GroupCreate(BaseModel):
    name: GroupName
    type: GroupType = "ministry"
    description: str | None = None
    unit_id: str | None = None
    schedule: Schedule | None = None
    is_open: bool = True
    capacity: Annotated[int, Field(gt=0)] | None = None
    color: HexColor = "#4f46e5"


class GroupUpdate(BaseModel):
    name: GroupName | None = None
    type: GroupType | None = None
    description: str | None = None
    unit_id: str | None = None
    schedule: Schedule | None = None
    is_open: bool | None = None
    capacity: Annotated[int, Field(gt=0)] | None = None
    color: HexColor | None = None
    is_archived: bool | None = None


class LeaderRef(BaseModel):
    id: str
    first_name: str
    last_name: str
    photo_url: str | None
    initials: str


class GroupRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    type: str
    description: str | None
    unit_id: str | None
    unit_name: str | None = None
    schedule: Schedule | None
    is_open: bool
    capacity: int | None
    color: str
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class GroupListItem(GroupRead):
    member_count: int
    invited_count: int = 0
    leaders: list[LeaderRef]


class GroupMembershipRead(BaseModel):
    id: str
    tenant_id: str
    group_id: str
    member_id: str
    role: str
    joined_at: date
    note: str | None
    status: MembershipStatus = "active"
    invited_at: datetime | None = None
    responded_at: datetime | None = None


class RosterEntry(GroupMembershipRead):
    role_name: str | None = None
    member: MemberListItem


class GroupDetail(GroupListItem):
    roster: list[RosterEntry]


class AddMembers(BaseModel):
    member_ids: list[str]
    role: GroupRole = "member"


class GroupRoleRead(BaseModel):
    id: str
    key: str
    name: str
    capabilities: list[Capability]
    rank: int
    is_system: bool


class GroupRoleCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    capabilities: list[Capability] = []
    #: Lower ranks outrank higher ones (Leader is 0). Custom roles sit between 1 and 99.
    rank: Annotated[int, Field(ge=1, le=99)] = 50


class GroupRoleUpdate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None
    capabilities: list[Capability] | None = None
    rank: Annotated[int, Field(ge=1, le=99)] | None = None


class MembershipUpdate(BaseModel):
    role: GroupRole | None = None
    note: Annotated[str, StringConstraints(max_length=200)] | None = None
