from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, HttpUrl, StringConstraints

OrgStatus = Literal["pending", "trial", "active", "suspended", "canceled"]
VerificationStatus = Literal["unverified", "pending_review", "verified", "rejected"]

Text100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class OrgProfile(BaseModel):
    id: str
    legal_name: str
    display_name: str
    slug: str
    country: str
    currency: str
    timezone: str
    status: OrgStatus
    tier: str
    verification_status: VerificationStatus
    registration_number: str | None
    denomination: str | None
    contact_email: str | None
    contact_phone: str | None
    address_line1: str | None
    city: str | None
    region: str | None
    website: str | None
    logo_url: str | None
    primary_color: str | None
    activated_at: datetime | None
    activation_source: str | None
    created_at: datetime


class OrgProfileUpdate(BaseModel):
    legal_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)] | None = None
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)] | None = None
    registration_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    denomination: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    contact_email: EmailStr | None = None
    contact_phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None = None
    address_line1: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    city: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    region: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    website: HttpUrl | None = None
    primary_color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None


class ModuleSubscription(BaseModel):
    tier: str
    #: Explicit switches on top of the tier. Absent = the tier decides.
    overrides: dict[str, bool]
    #: Modules a platform admin has set; the church cannot change these.
    platform_managed: list[str] = []


class SetModuleInput(BaseModel):
    #: True/False sets a switch; null clears it back to the plan default.
    enabled: bool | None


class SetTierInput(BaseModel):
    tier: Literal["free", "seed", "parish", "growth", "diocese", "enterprise"]


class OrgDocumentRead(BaseModel):
    id: str
    kind: str
    file_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: Literal["pending_review", "verified", "rejected", "superseded"]
    uploaded_at: datetime
    uploaded_by_name: str | None = None
    reviewed_at: datetime | None
    review_note: str | None


class OnboardingStep(BaseModel):
    key: Literal["church_profile", "leader", "administrator", "branches", "certificate", "payment"]
    title: str
    required: bool
    #: "done" | "pending" (in progress / waiting on someone) | "todo"
    state: Literal["done", "pending", "todo"]
    detail: str


class OnboardingStatus(BaseModel):
    org_status: OrgStatus
    verification_status: VerificationStatus
    activation_source: str | None
    is_active: bool
    steps: list[OnboardingStep]
    #: Present while the church still waits on payment or a platform decision.
    next_action: str | None = None


class InvitePersonInput(BaseModel):
    """Invite the church's leader or an administrator during onboarding."""

    role: Literal["leader", "administrator"]
    email: EmailStr
    first_name: Text100
    last_name: Text100 = Field(default="")
