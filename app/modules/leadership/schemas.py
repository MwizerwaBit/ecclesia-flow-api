from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class PositionHolder(BaseModel):
    membership_id: str
    user_id: str
    first_name: str
    last_name: str
    email: str
    photo_url: str | None
    assigned_at: datetime


class PositionRead(BaseModel):
    id: str
    parent_id: str | None
    title: str
    description: str | None
    role_id: str | None
    role_name: str | None
    unit_id: str | None
    unit_name: str | None
    sort_order: int
    holders: list[PositionHolder]


class PositionCreate(BaseModel):
    title: Title
    parent_id: str | None = None
    description: Annotated[str, StringConstraints(max_length=500)] | None = None
    #: The RBAC role holders receive. None = an honorific position (no extra access).
    role_id: str | None = None
    #: The branch holders are scoped to. None = whole church.
    unit_id: str | None = None
    sort_order: int = 0


class PositionUpdate(BaseModel):
    title: Title | None = None
    parent_id: str | None = None
    description: Annotated[str, StringConstraints(max_length=500)] | None = None
    role_id: str | None = None
    unit_id: str | None = None
    sort_order: int | None = None


class AssignHolderInput(BaseModel):
    membership_id: str
