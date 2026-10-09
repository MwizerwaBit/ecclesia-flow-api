"""Payment providers behind one small interface.

`DemoProvider` needs no keys and makes no network calls: checkout happens on
EcclesiaFlow's own test-card page, which then emits a webhook signed exactly
like Stripe's. `StripeProvider` talks to Stripe's REST API with httpx. The rest
of billing never knows which one it has — swap by setting STRIPE_SECRET_KEY.
"""

import secrets
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError


class PaymentProviderError(AppError):
    status_code = 502
    code = "payment_provider_error"


@dataclass(frozen=True)
class CheckoutRequest:
    tenant_id: str
    church_name: str
    customer_email: str
    tier: str
    plan_name: str
    interval: str
    amount_cents: int
    currency: str


@dataclass(frozen=True)
class CheckoutResult:
    provider_session_id: str
    url: str


class PaymentProvider(Protocol):
    name: str

    def webhook_secret(self) -> str: ...

    async def create_checkout(self, request: CheckoutRequest) -> CheckoutResult: ...


class DemoProvider:
    name = "demo"

    def webhook_secret(self) -> str:
        return get_settings().demo_webhook_secret

    async def create_checkout(self, request: CheckoutRequest) -> CheckoutResult:
        session_id = f"cs_demo_{secrets.token_urlsafe(18)}"
        return CheckoutResult(
            provider_session_id=session_id,
            url=f"{get_settings().frontend_url}/billing/checkout/{session_id}",
        )


class StripeProvider:
    """Stripe Checkout in subscription mode, with inline price data — no
    products or prices need creating in the Stripe dashboard first."""

    name = "stripe"
    api = "https://api.stripe.com/v1"

    def webhook_secret(self) -> str:
        return get_settings().stripe_webhook_secret

    async def create_checkout(self, request: CheckoutRequest) -> CheckoutResult:
        settings = get_settings()
        form = {
            "mode": "subscription",
            "client_reference_id": request.tenant_id,
            "customer_email": request.customer_email,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": request.currency,
            "line_items[0][price_data][unit_amount]": str(request.amount_cents),
            "line_items[0][price_data][recurring][interval]": request.interval,
            "line_items[0][price_data][product_data][name]": f"EcclesiaFlow {request.plan_name}",
            "metadata[tenant_id]": request.tenant_id,
            "metadata[tier]": request.tier,
            "metadata[interval]": request.interval,
            "subscription_data[metadata][tenant_id]": request.tenant_id,
            "subscription_data[metadata][tier]": request.tier,
            "success_url": f"{settings.frontend_url}/onboarding?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{settings.frontend_url}/onboarding?checkout=canceled",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.api}/checkout/sessions",
                    data=form,
                    auth=(settings.stripe_secret_key, ""),
                    # Retries of the same request never create two sessions.
                    headers={"Idempotency-Key": secrets.token_hex(16)},
                )
        except httpx.HTTPError as exc:
            raise PaymentProviderError("Couldn't reach the payment provider. Try again shortly.") from exc
        if resp.status_code >= 400:
            raise PaymentProviderError("The payment provider rejected the checkout request.")
        body = resp.json()
        return CheckoutResult(provider_session_id=body["id"], url=body["url"])


def get_provider() -> PaymentProvider:
    return StripeProvider() if get_settings().billing_provider == "stripe" else DemoProvider()


def provider_by_name(name: str) -> PaymentProvider:
    if name == "stripe":
        return StripeProvider()
    if name == "demo" and get_settings().billing_provider == "demo":
        return DemoProvider()
    raise PaymentProviderError("Unknown payment provider")
