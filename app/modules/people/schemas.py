from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints, model_validator

Gender = Literal["male", "female"]
MemberStatus = Literal["active", "visitor", "prospect", "inactive"]
MaritalStatus = Literal["single", "married", "widowed", "divorced", "separated"]
ContactChannel = Literal["phone", "sms", "whatsapp", "email"]
JoinMethod = Literal["first_visit", "transfer", "baptism", "profession_of_faith", "born_into", "other"]
HouseholdRole = Literal["head", "spouse", "child", "relative", "other"]
# Group roles are church-defined (group_roles table); this is just the key.
GroupRole = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,39}$")]
IdType = Literal["national_id", "passport", "other"]
IdNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=30)]
ADULT_AGE = 18


def _age(dob: date | None) -> int | None:
    if dob is None:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Envelope = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{1,8}$")]


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


class AddressIn(BaseModel):
    line1: str
    line2: str | None = None
    city: str
    state: str | None = None
    country: str
    postal_code: str | None = None


class MemberGroupRef(BaseModel):
    id: str
    name: str
    color: str | None = None
    role: GroupRole


class MemberListItem(BaseModel):
    id: str
    first_name: str
    last_name: str
    preferred_name: str | None
    photo_url: str | None
    initials: str
    status: str
    unit_name: str | None
    envelope_number: str | None
    last_seen_at: date | None
    unit_id: str | None = None
    email: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    gender: str | None = None
    joined_at: date | None = None
    household_id: str | None = None
    groups: list[MemberGroupRef] = []
    id_type: str | None = None
    national_id_last4: str | None = None


class EmergencyContact(BaseModel):
    name: Name
    phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=30)]
    relationship: str | None = None


class ConsentIn(BaseModel):
    given_by: Literal["self", "guardian"] = "self"
    communications: bool = False
    directory_visible: bool = True


class ConsentOut(ConsentIn):
    data_processing_at: datetime


class HouseholdChoice(BaseModel):
    """Where a newly registered person lives relative to households on file."""

    mode: Literal["none", "new", "existing"] = "none"
    name: str | None = None
    household_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "HouseholdChoice":
        if self.mode == "new" and not (self.name or "").strip():
            raise ValueError("A new household needs a name")
        if self.mode == "existing" and not self.household_id:
            raise ValueError("Choose the household to join")
        return self


class PossibleDuplicate(BaseModel):
    member: MemberListItem
    reasons: list[Literal["name", "phone", "email"]]


class HouseholdRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    head_member_id: str | None
    address: AddressIn | None
    members: list[MemberListItem]
    total_giving: float
    created_at: datetime


class MemberDetail(BaseModel):
    id: str
    tenant_id: str
    user_id: str | None
    first_name: str
    last_name: str
    preferred_name: str | None
    photo_url: str | None
    initials: str
    email: str | None
    phone: str | None
    whatsapp: str | None
    status: str
    envelope_number: str | None
    unit_id: str | None
    unit_name: str | None
    joined_at: date | None
    last_seen_at: date | None
    household_id: str | None
    created_at: datetime
    updated_at: datetime
    date_of_birth: date | None
    gender: str | None
    marital_status: str | None
    occupation: str | None
    address: AddressIn | None
    attendance_rate: float | None
    total_giving: float | None
    giving_this_year: float | None
    title: str | None = None
    middle_name: str | None = None
    employer: str | None = None
    preferred_contact: str | None = None
    join_method: str | None = None
    previous_church: str | None = None
    invited_by: str | None = None
    is_baptised: bool | None = None
    baptism_date: date | None = None
    household_role: str | None = None
    emergency_contact: EmergencyContact | None = None
    consent: ConsentOut | None = None
    notes: str | None = None
    groups: list[MemberGroupRef] = []
    id_type: str | None = None
    #: Only ever the last four characters — see app/core/pii.py.
    national_id_last4: str | None = None


class _MemberFields(BaseModel):
    """Optional fields shared by registration and update."""

    title: str | None = None
    middle_name: str | None = None
    preferred_name: str | None = None
    photo_url: str | None = None
    whatsapp: str | None = None
    date_of_birth: date | None = None
    marital_status: MaritalStatus | None = None
    occupation: str | None = None
    employer: str | None = None
    preferred_contact: ContactChannel | None = None
    address: AddressIn | None = None
    unit_id: str | None = None
    joined_at: date | None = None
    join_method: JoinMethod | None = None
    previous_church: str | None = None
    invited_by: str | None = None
    is_baptised: bool | None = None
    baptism_date: date | None = None
    envelope_number: Envelope | None = None
    household_role: HouseholdRole | None = None
    emergency_contact: EmergencyContact | None = None
    notes: Annotated[str, StringConstraints(max_length=4000)] | None = None
    id_type: IdType | None = None
    #: Write-only: stored encrypted + hashed, never returned.
    national_id: IdNumber | None = None

    @model_validator(mode="after")
    def _dates(self):
        today = date.today()
        for field in ("date_of_birth", "joined_at", "baptism_date"):
            value = getattr(self, field, None)
            if value and value > today:
                raise ValueError(f"{field} can't be in the future")
        if self.baptism_date and self.date_of_birth and self.baptism_date < self.date_of_birth:
            raise ValueError("baptism_date is before date_of_birth")
        return self


class MemberCreate(_MemberFields):
    """A full registration. Required: name, gender, a phone or an email, a
    status, and recorded consent — membership reveals religious belief, which
    data-protection law treats as special-category data."""

    first_name: Name
    last_name: Name
    gender: Gender
    email: EmailStr | None = None
    phone: str | None = None
    status: MemberStatus = "active"
    consent: ConsentIn
    household: HouseholdChoice = Field(default_factory=HouseholdChoice)
    group_ids: list[str] = []

    @model_validator(mode="after")
    def _reachable(self) -> "MemberCreate":
        if not self.email and not self.phone:
            raise ValueError("Add a phone number or an email")
        age = _age(self.date_of_birth)
        is_adult = age is None or age >= ADULT_AGE
        # Members of the church who are adults are registered with an ID
        # document; visitors, prospects and children may be added without one.
        if self.status in ("active", "inactive") and is_adult and not self.national_id:
            raise ValueError("An ID number is required to register an adult member")
        if self.national_id and not self.id_type:
            self.id_type = "national_id"
        if self.phone and not 7 <= len(_digits(self.phone)) <= 15:
            raise ValueError("Enter a full phone number")
        return self


class MemberUpdate(_MemberFields):
    first_name: Name | None = None
    last_name: Name | None = None
    gender: Gender | None = None
    email: EmailStr | None = None
    phone: str | None = None
    status: MemberStatus | None = None
    household_id: str | None = None
    household: HouseholdChoice | None = None
    # Only accepted when none is on file yet; an existing consent is never rewritten.
    consent: ConsentIn | None = None


class VisitorQuickAdd(BaseModel):
    first_name: str
    last_name: str
    phone: str | None = None
    invited_by: str | None = None


class VisitorFollowUp(BaseModel):
    id: str
    member_id: str
    member: MemberListItem
    days_since_visit: int
    visit_date: date
    assigned_to_id: str | None
    assigned_to_name: str | None
    status: str
    notes: str | None


class PastoralNoteRead(BaseModel):
    id: str
    member_id: str
    content: str
    author_user_id: str
    author_name: str
    is_private: bool
    created_at: datetime
    updated_at: datetime


class PastoralNoteCreate(BaseModel):
    content: str
    is_private: bool = True


class SacramentalRecordRead(BaseModel):
    id: str
    member_id: str
    type: str
    date: date
    officiant_name: str | None
    location: str | None
    notes: str | None
    created_at: datetime


class SacramentalRecordCreate(BaseModel):
    type: str
    date: date
    officiant_name: str | None = None
    location: str | None = None
    notes: str | None = None
