import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    church_name: str = Field(min_length=2, max_length=200)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    country: str = Field(min_length=2, max_length=2, default="US")
    timezone: str = Field(default="UTC")
    currency: str = Field(min_length=3, max_length=3, default="USD")

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        # Mirrors the frontend's password-strength meter copy on Reset Password:
        # length is the floor; real strength scoring is the client's concern,
        # this is the server-side backstop so a weak password can never land
        # even if a client is bypassed entirely.
        if v.lower() == v or v.upper() == v or not any(c.isdigit() for c in v):
            raise ValueError("Password must mix upper/lowercase letters and at least one digit")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MfaChallengeRequest(BaseModel):
    challenge_token: str
    # 6 for a TOTP code, up to 10 for a backup code (see
    # security.generate_backup_codes — secrets.token_hex(5)).
    code: str = Field(min_length=6, max_length=10)


class RefreshRequest(BaseModel):
    refresh_token: str


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
    refresh_token: str
    token_type: str = "bearer"
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
