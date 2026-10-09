from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class UnitMembershipRead(BaseModel):
    id: str
    member_id: str
    unit_id: str
    unit_name: str | None
    kind: Literal["home", "associate"]
    status: Literal["active", "suspended", "transferred", "ended"]
    started_on: date
    ended_on: date | None
    end_reason: str | None
    created_at: datetime


class AssociateMembershipCreate(BaseModel):
    unit_id: str
    started_on: date | None = None


class MoveHomeUnitRequest(BaseModel):
    unit_id: str
    reason: str | None = Field(default=None, max_length=500)


class MembershipStatusChange(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
