from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OrgListItem(BaseModel):
    id: str
    display_name: str
    slug: str
    country: str
    tier: str
    status: str
    member_count: int
    created_at: datetime
    trial_ends_at: datetime | None
    renewal_date: datetime | None
    last_active_at: datetime | None
    verification_status: str = "unverified"
    activation_source: str | None = None
    activated_at: datetime | None = None
    city: str | None = None
    contact_email: str | None = None
    documents_pending: int = 0
    subscription_status: str | None = None
    #: Development only: the leader's set-password link for a church registered by staff.
    invite_url: str | None = None


class OrgDetail(OrgListItem):
    """Superset of OrgListItem — the frontend's org-detail screen reads
    today's shape plus these extras (see architecture-doc-review.md's note
    on `Organisation` vs `OrgListItem`: this fills the detail screen in
    properly rather than leaving it reading list-row data)."""

    legal_name: str
    currency: str
    timezone: str
    language: str
    logo_url: str | None
    primary_color: str | None
    custom_domain: str | None
    storage_used_mb: int
    unit_count: int
    primary_admin_email: str | None
    primary_admin_name: str | None


class CreateOrgInput(BaseModel):
    legal_name: str
    display_name: str
    country: str
    currency: str
    timezone: str
    tier: str = "seed"
    primary_admin_email: str
    primary_admin_name: str
    trial_days: int = 30


class SetOrgStatusInput(BaseModel):
    status: str
    reason: str


class FeatureFlagRead(BaseModel):
    code: str
    label: str
    description: str
    tier_default: bool
    current_value: bool
    is_overridden: bool
    override_set_by: str | None
    override_set_at: datetime | None
    override_expires_at: datetime | None
    override_note: str | None


class SetFeatureFlagInput(BaseModel):
    value: bool
    note: str | None = None
    expires_at: datetime | None = None


class AuditLogEntryRead(BaseModel):
    id: str
    org_id: str | None
    org_name: str | None
    user_id: str | None
    user_name: str | None
    action: str
    resource_type: str
    resource_id: str | None
    metadata: dict | None
    ip_address: str | None
    is_impersonated: bool
    impersonated_by: str | None
    created_at: datetime


class CustomDomainRead(BaseModel):
    id: str
    tenant_id: str
    org_name: str
    domain: str
    status: str
    ssl_provisioned: bool
    verified_at: datetime | None
    created_at: datetime


class DataErasureRequestRead(BaseModel):
    id: str
    org_id: str
    org_name: str
    member_id: str
    member_display_name: str
    requested_by: str
    requested_at: datetime
    status: str
    completed_at: datetime | None
    processed_by: str | None
    notes: str | None


class DecideErasureRequestInput(BaseModel):
    decision: str  # 'approved' | 'rejected'
    notes: str | None = None


class PlatformAdminRead(BaseModel):
    id: str
    name: str
    email: str
    level: str
    mfa_enabled: bool
    last_login_at: datetime | None
    impersonation_count: int


class PlatformMetrics(BaseModel):
    total_orgs: int
    active_orgs: int
    total_members: int
    new_signups_today: int
    new_signups_this_week: int
    new_signups_this_month: int
    trials_ending_in_7_days: list[OrgListItem]
    mrr: float
    api_p50_ms: float | None
    api_p95_ms: float | None
    api_p99_ms: float | None
    error_rate: float | None
    queue_depth: int | None


class SystemHealthService(BaseModel):
    name: str
    status: str
    detail: str


class SystemHealth(BaseModel):
    services: list[SystemHealthService]
    active_sessions: int
    failed_jobs: int | None
    rate_limit_violations: list[dict]
    recent_errors: list[dict]


class ImpersonationRequest(BaseModel):
    role_to_impersonate: str


class ImpersonationSession(BaseModel):
    org_id: str
    org_name: str
    role: str
    token: str
    expires_at: int


class ActivateOrgInput(BaseModel):
    #: Why this church is being activated without payment — kept in the audit log.
    note: str = Field(min_length=3, max_length=500)


class ReviewDocumentInput(BaseModel):
    decision: Literal["verified", "rejected"]
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _reason_for_rejection(self) -> "ReviewDocumentInput":
        if self.decision == "rejected" and not (self.note or "").strip():
            raise ValueError("Say why the certificate was not accepted, so the church can fix it")
        return self
