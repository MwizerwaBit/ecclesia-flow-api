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
