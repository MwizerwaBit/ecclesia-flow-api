import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


def check_password_strength(v: str) -> str:
    """Server-side backstop: a weak password can never land even if a client
    is bypassed. Length is enforced by the field; this checks composition."""
    if v.lower() == v or v.upper() == v or not any(c.isdigit() for c in v):
        raise ValueError("Password must mix upper/lowercase letters and at least one digit")
    return v


class OtherPersonInCharge(BaseModel):
    """The second person every church needs: the leader (when an administrator
    registers) or an administrator (when the leader registers)."""

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(default="", max_length=100)
    email: EmailStr


class RegisterRequest(BaseModel):
    church_name: str = Field(min_length=2, max_length=200)
    #: Official name if different from the everyday one.
    legal_name: str | None = Field(default=None, max_length=200)
    denomination: str | None = Field(default=None, max_length=100)
    registration_number: str | None = Field(default=None, max_length=80)
    contact_phone: str | None = Field(default=None, max_length=30)
    address_line1: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    #: Who is registering: the church's leader, or its administrator/maintainer.
    registrant_role: Literal["leader", "administrator"] = "leader"
    other_person: OtherPersonInCharge | None = None
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    country: str = Field(min_length=2, max_length=2, default="US")
    timezone: str = Field(default="UTC")
    currency: str = Field(min_length=3, max_length=3, default="USD")

    @model_validator(mode="after")
    def _leader_named(self) -> "RegisterRequest":
        # A church always has a leader: an administrator registering it must say who.
        if self.registrant_role == "administrator" and self.other_person is None:
            raise ValueError("Tell us who leads the church so we can invite them")
        if self.other_person and self.other_person.email.lower() == self.email.lower():
            raise ValueError("The other person needs their own email address")
        return self

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        # Mirrors the frontend's password-strength meter copy on Reset Password:
        # length is the floor; real strength scoring is the client's concern,
        # this is the server-side backstop so a weak password can never land
        # even if a client is bypassed entirely.
        return check_password_strength(v)


class JoinChurchRequest(BaseModel):
    """A person registering themselves into a church they attend."""

    church_slug: str = Field(min_length=2, max_length=120)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    phone: str = Field(min_length=7, max_length=30)
    gender: Literal["male", "female"]
    date_of_birth: date
    id_type: Literal["national_id", "passport", "other"] = "national_id"
    national_id: str = Field(min_length=4, max_length=30)
    #: Must be true — membership records reveal religious belief.
    consent_data_processing: bool
    consent_communications: bool = False

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return check_password_strength(v)

    @model_validator(mode="after")
    def _adult_and_consenting(self) -> "JoinChurchRequest":
        today = date.today()
        dob = self.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if dob > today or age > 120:
            raise ValueError("That date of birth doesn't look right")
        if age < 18:
            raise ValueError("Under-18s are registered by the church office with a parent or guardian")
        if not self.consent_data_processing:
            raise ValueError("Consent is needed for the church to keep your record")
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MfaChallengeRequest(BaseModel):
    challenge_token: str
    # 6 for a TOTP code, up to 10 for a backup code (see
    # security.generate_backup_codes — secrets.token_hex(5)).
    code: str = Field(min_length=6, max_length=10)


class RefreshRequest(BaseModel):
    # Browsers send nothing here — the token arrives in the httpOnly cookie.
    # Non-browser clients (and the test suite) may pass it in the body.
    refresh_token: str | None = None


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=10, max_length=300)
    password: str = Field(min_length=10, max_length=128)
    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return check_password_strength(v)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetRequested(BaseModel):
    status: Literal["ok"] = "ok"
    message: str = "If an account exists for that email, we've sent a link to reset its password."


class PasswordResetComplete(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return check_password_strength(v)


class SwitchTenantRequest(BaseModel):
    membership_id: uuid.UUID


class StepUpRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class MfaVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class MembershipSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    role_name: str
    is_primary: bool
    is_leader: bool
    unit_scope_id: uuid.UUID | None


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    photo_url: str | None
    mfa_enabled: bool
    is_platform_admin: bool


class SessionResponse(BaseModel):
    """What authService.register/login return to the frontend: enough to
    populate AuthSession without a second round trip."""

    access_token: str
    # Only populated for clients that ask for body transport (see router);
    # browsers get it as an httpOnly cookie and never see it in JavaScript.
    refresh_token: str | None = None
    token_type: str = "bearer"
    #: Seconds until the access token expires, so the client can refresh ahead of time.
    expires_in: int = 0
    #: Development only: the invitation link for the other person in charge.
    invite_url: str | None = None
    user: UserPublic
    active_membership: MembershipSummary | None
    memberships: list[MembershipSummary]
    permissions: list[str]
    role: str | None
    unit_scope_id: uuid.UUID | None


class MfaChallengeResponse(BaseModel):
    """Returned instead of SessionResponse when the account has MFA enabled —
    the frontend's MFA Challenge Screen posts the code + this token to
    /auth/mfa/challenge to get the real session."""

    mfa_required: bool = True
    challenge_token: str


class MfaSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str
    backup_codes: list[str]


class StepUpResponse(BaseModel):
    step_up_token: str
    expires_in_seconds: int
