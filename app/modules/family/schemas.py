from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

#: As stored: "related_member is the <kind> of member".
StoredKind = Literal["parent", "guardian", "spouse"]
#: As shown from one person's side: who the OTHER person is to them.
Role = Literal["parent", "guardian", "spouse", "child", "ward"]


class RelationshipCreate(BaseModel):
    #: The other person.
    related_member_id: UUID
    #: What the other person is to the one whose record this is. "child" and
    #: "ward" are the inverse of parent and guardian.
    role: Role


class RelationshipRead(BaseModel):
    id: str
    #: Who the other person is to this one.
    role: Role
    other_member_id: str
    other_name: str
    other_photo_url: str | None
    other_age: int | None
    started_on: date
    ended_on: date | None
    #: True while this person is a minor and the other is their parent or
    #: guardian, i.e. while the relationship currently grants access.
    grants_access: bool


class FamilyOverview(BaseModel):
    relationships: list[RelationshipRead]
    #: The people this signed-in member may act for.
    dependants: list[RelationshipRead]


class DependantView(BaseModel):
    """What a parent or guardian sees of a child. Deliberately narrower than
    the full record: no giving, no pastoral notes, no ID details."""

    id: str
    first_name: str
    last_name: str
    preferred_name: str | None
    photo_url: str | None
    date_of_birth: date | None
    age: int | None
    email: str | None
    phone: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    groups: list[str]
