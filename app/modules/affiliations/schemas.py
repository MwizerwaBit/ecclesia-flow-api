from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: What an affiliated parent may see of a church. Counts and shared
#: gatherings only — never people, notes, giving or anything else.
Grant = Literal["aggregate_stats", "published_events"]


class OrgRef(BaseModel):
    id: str
    name: str
    slug: str


class AffiliationRead(BaseModel):
    id: str
    status: Literal["requested", "active", "declined", "withdrawn", "ended"]
    #: This church's side of the relationship.
    my_role: Literal["parent", "child"]
    #: True when this church proposed it (so the other side must answer).
    initiated_by_me: bool
    counterpart: OrgRef
    grants: list[Grant]
    message: str | None
    requested_at: datetime
    decided_at: datetime | None
    decision_note: str | None
    effective_from: datetime | None
    ended_at: datetime | None
    end_reason: str | None


class AffiliationPropose(BaseModel):
    counterpart_slug: str = Field(min_length=1, max_length=120)
    #: What the other organisation would be to this church.
    counterpart_role: Literal["parent", "child"]
    #: Proposed sharing. When this church is the child, this is its consent;
    #: when it is the parent, the child decides on acceptance.
    grants: list[Grant] = Field(default_factory=list)
    message: str | None = Field(default=None, max_length=1000)


class AffiliationAccept(BaseModel):
    #: Only the child may set this (narrowing or widening what was proposed).
    grants: list[Grant] | None = None
    note: str | None = Field(default=None, max_length=1000)


class AffiliationDecision(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class AffiliationGrantsUpdate(BaseModel):
    grants: list[Grant]


class AffiliateSummary(BaseModel):
    child_org_id: str
    child_name: str
    active_members: int
    units: int
    active_groups: int
    upcoming_events: int


class AffiliateEvent(BaseModel):
    id: str
    title: str
    type: str
    location: str | None
    start_date_time: datetime
    end_date_time: datetime | None
