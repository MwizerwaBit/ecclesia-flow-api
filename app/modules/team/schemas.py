from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr


class TeamMemberRead(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    first_name: str
    last_name: str
    email: str
    photo_url: str | None
    role_id: str
    role_name: str
    role_color: str | None
    unit_scope: str | None  # unit id, or "all"
    unit_scope_name: str | None
    mfa_enabled: bool
    last_active_at: datetime | None
    invited_at: datetime | None
    accepted_at: datetime | None
    is_leader: bool
    status: str | None = None
    #: Development only: the link that would be emailed. Never returned in production.
    invite_url: str | None = None


class InviteStaffPayload(BaseModel):
    email: EmailStr
    role_id: str
    unit_scope: str | None = None
    message: str | None = None


class UpdateAssignmentPayload(BaseModel):
    role_id: str | None = None
    unit_scope: str | None = None
    #: Suspending takes effect on the person's very next request.
    status: Literal["active", "suspended"] | None = None


class LeadershipTransferApprovalRead(BaseModel):
    team_member_id: str
    name: str
    approved_at: datetime


class LeadershipTransferRead(BaseModel):
    id: str
    tenant_id: str
    outgoing_leader_id: str
    outgoing_leader_name: str
    nominee_id: str
    nominee_name: str
    initiated_by_team_member_id: str
    initiated_at: datetime
    mfa_verified_at: datetime
    required_approvals: int
    eligible_approver_ids: list[str]
    approvals: list[LeadershipTransferApprovalRead]
    status: str
    completed_at: datetime | None
    canceled_at: datetime | None


class RequestLeadershipTransferPayload(BaseModel):
    nominee_id: str
