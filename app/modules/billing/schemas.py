from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

Interval = Literal["month", "year"]


class PlanRead(BaseModel):
    tier: str
    name: str
    tagline: str
    monthly_cents: int | None
    yearly_cents: int | None
    max_members: int | None
    modules: list[str]
    purchasable: bool
    currency: str = "usd"


class CheckoutInput(BaseModel):
    tier: Literal["seed", "parish", "growth", "diocese"]
    interval: Interval = "month"


class CheckoutRead(BaseModel):
    provider: str
    session_id: str
    checkout_url: str


class SubscriptionRead(BaseModel):
    provider: str
    tier: str
    interval: str
    status: str
    current_period_end: datetime | None


class PaymentRead(BaseModel):
    id: str
    amount_cents: int
    currency: str
    status: str
    description: str | None
    paid_at: datetime | None


class BillingOverview(BaseModel):
    provider: str
    publishable_key: str | None
    org_status: str
    tier: str
    subscription: SubscriptionRead | None
    payments: list[PaymentRead]
    access: "AccessRead | None" = None
    pending_payment: "ManualPaymentRead | None" = None
    last_payment: "ManualPaymentRead | None" = None


class DemoCheckoutRead(BaseModel):
    session_id: str
    church_name: str
    plan_name: str
    interval: str
    amount_cents: int
    currency: str
    status: str


class DemoPayInput(BaseModel):
    """Test cards only — the demo never handles a real card number."""

    card_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=12, max_length=23)]
    name_on_card: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class DemoPayResult(BaseModel):
    status: Literal["paid", "declined"]
    org_status: str


# ───────────────────────────── Manual (mobile-money) payments ─────────────────────────────


class AccessRead(BaseModel):
    """What the church may do right now (see entitlement.py)."""

    state: Literal["full", "basics", "view_only", "suspended", "closed"]
    tier: str
    can_write: bool
    period_end: datetime | None
    grace_ends_at: datetime | None
    days_left: int | None
    renewal_due: bool
    #: A payment has been reported and is waiting for a platform admin.
    payment_pending: bool = False


class PaymentInstructions(BaseModel):
    """Where to send the money, and how much, for the plan and months chosen."""

    network: str
    number: str
    account_name: str
    tier: str
    plan_name: str
    months: int
    amount_cents: int
    currency: str


ManualStatus = Literal["pending", "approved", "rejected"]


class ManualPaymentRead(BaseModel):
    id: str
    tier: str
    months: int
    amount_cents: int
    currency: str
    reference_code: str
    sender_name: str
    sent_amount_cents: int
    momo_transaction_id: str | None
    status: ManualStatus
    submitted_at: datetime
    decided_at: datetime | None
    decision_note: str | None
    granted_until: datetime | None


class ManualPaymentAdminRead(ManualPaymentRead):
    """The platform admin's view: which church, and whether the numbers agree."""

    tenant_id: str
    church_name: str
    church_email: str | None
    proof_mime_type: str
    amount_matches: bool
    #: The end date the church would have if this were approved now.
    would_grant_until: datetime | None
    current_period_end: datetime | None


class DecisionInput(BaseModel):
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    #: Approve even though less than the price was sent. Off unless the admin says so.
    accept_amount_difference: bool = False


BillingOverview.model_rebuild()
