"""Plans, checkout and the webhook that activates a church when it pays.

Flow:   checkout (tenant session) → provider → signed webhook → process_webhook
        (platform session, idempotent) → subscription active → org activated.

The demo provider runs the identical second half: its "pay" button builds a
Stripe-shaped event, signs it with the demo secret, and hands it to the same
process_webhook — so what is demonstrated is the production code path.
"""

import json
import secrets
from datetime import UTC, datetime, timedelta

from prisma import Prisma

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.modules.billing import signatures
from app.modules.billing.providers import CheckoutRequest, DemoProvider, get_provider, provider_by_name
from app.modules.billing.schemas import CheckoutInput, DemoPayInput
from app.modules.org import lifecycle
from app.modules.org.catalogue import MODULES, PLAN_BY_TIER, PLANS, PURCHASABLE_TIERS, tier_at_least

# Stripe's documented test card numbers.
DEMO_CARD_SUCCESS = "4242424242424242"
DEMO_CARD_DECLINED = "4000000000000002"


class PaymentDeclinedError(AppError):
    status_code = 402
    code = "card_declined"


def list_plans() -> list[dict]:
    return [
        {
            "tier": p.tier,
            "name": p.name,
            "tagline": p.tagline,
            "monthly_cents": p.monthly_cents,
            "yearly_cents": p.yearly_cents,
            "max_members": p.max_members,
            "modules": [m.id for m in MODULES if tier_at_least(p.tier, m.min_tier)],
            "purchasable": p.tier in PURCHASABLE_TIERS,
        }
        for p in PLANS
    ]


async def overview(db: Prisma, org_id: str) -> dict:
    settings = get_settings()
    org = await db.organization.find_unique(where={"id": org_id})
    subscription = await db.billingsubscription.find_unique(where={"tenant_id": org_id})
    payments = await db.billingpayment.find_many(where={"tenant_id": org_id}, order={"created_at": "desc"}, take=24)
    return {
        "provider": settings.billing_provider,
        "publishable_key": settings.stripe_publishable_key or None,
        "org_status": org.status,
        "tier": org.tier,
        "subscription": subscription,
        "payments": payments,
    }


async def create_checkout(db: Prisma, org_id: str, user_id: str, user_email: str, payload: CheckoutInput) -> dict:
    org = await db.organization.find_unique(where={"id": org_id})
    if org.status == "canceled":
        raise ConflictError("This church's account is closed")
    plan = PLAN_BY_TIER[payload.tier]
    amount = plan.price(payload.interval)
    settings = get_settings()
    provider = get_provider()
    result = await provider.create_checkout(
        CheckoutRequest(
            tenant_id=org_id,
            church_name=org.display_name,
            customer_email=user_email,
            tier=plan.tier,
            plan_name=plan.name,
            interval=payload.interval,
            amount_cents=amount,
            currency=settings.billing_currency,
        )
    )
    await db.billingcheckoutsession.create(
        data={
            "tenant_id": org_id,
            "provider": provider.name,
            "provider_session_id": result.provider_session_id,
            "tier": plan.tier,
            "interval": payload.interval,
            "amount_cents": amount,
            "currency": settings.billing_currency,
            "created_by_user_id": user_id,
        }
    )
    return {"provider": provider.name, "session_id": result.provider_session_id, "checkout_url": result.url}


# ───────────────────────────── Demo checkout ─────────────────────────────


async def _demo_session(db: Prisma, org_id: str, session_id: str):
    if get_settings().billing_provider != "demo":
        raise NotFoundError("No such checkout")
    session = await db.billingcheckoutsession.find_first(
        where={"tenant_id": org_id, "provider": "demo", "provider_session_id": session_id}
    )
    if session is None:
        raise NotFoundError("No such checkout")
    return session


async def demo_checkout(db: Prisma, org_id: str, session_id: str) -> dict:
    session = await _demo_session(db, org_id, session_id)
    org = await db.organization.find_unique(where={"id": org_id})
    return {
        "session_id": session.provider_session_id,
        "church_name": org.display_name,
        "plan_name": PLAN_BY_TIER[session.tier].name,
        "interval": session.interval,
        "amount_cents": session.amount_cents,
        "currency": session.currency,
        "status": session.status,
    }


async def demo_pay(db: Prisma, org_id: str, session_id: str, payload: DemoPayInput) -> dict:
    session = await _demo_session(db, org_id, session_id)
    if session.status != "open":
        raise ConflictError("This checkout has already been used")
    card = "".join(ch for ch in payload.card_number if ch.isdigit())
    if card == DEMO_CARD_DECLINED:
        raise PaymentDeclinedError("Your card was declined. (Demo: use 4242 4242 4242 4242.)")
    if card != DEMO_CARD_SUCCESS:
        raise PaymentDeclinedError("Use a test card — 4242 4242 4242 4242 succeeds, 4000 0000 0000 0002 is declined.")

    # Exactly the shape Stripe sends for a completed subscription checkout.
    event = {
        "id": f"evt_demo_{secrets.token_urlsafe(12)}",
        "type": "checkout.session.completed",
        "created": int(datetime.now(UTC).timestamp()),
        "data": {
            "object": {
                "id": session.provider_session_id,
                "object": "checkout.session",
                "client_reference_id": org_id,
                "customer": f"cus_demo_{org_id[:8]}",
                "subscription": f"sub_demo_{secrets.token_urlsafe(8)}",
                "amount_total": session.amount_cents,
                "currency": session.currency,
                "payment_status": "paid",
                "metadata": {"tenant_id": org_id, "tier": session.tier, "interval": session.interval},
            }
        },
    }
    body = json.dumps(event).encode()
    header = signatures.sign(body, DemoProvider().webhook_secret())
    await process_webhook("demo", body, header)
    org = await db.organization.find_unique(where={"id": org_id})
    return {"status": "paid", "org_status": org.status}


# ───────────────────────────── Webhooks ─────────────────────────────


async def process_webhook(provider_name: str, body: bytes, signature_header: str | None) -> dict:
    """Verifies, records (once) and applies a provider event. Runs on the
    platform connection: a webhook carries no user and no tenant session, and
    the tenant it concerns is read from the signed payload itself."""
    provider = provider_by_name(provider_name)
    signatures.verify(body, signature_header, provider.webhook_secret())
    event = json.loads(body)
    obj = event.get("data", {}).get("object", {})
    tenant_id = (obj.get("metadata") or {}).get("tenant_id") or obj.get("client_reference_id")

    async with platform_session() as db:
        inserted = await db.query_raw(
            """
            insert into billing_webhook_events (provider, provider_event_id, type, tenant_id, payload)
            values ($1, $2, $3, $4::uuid, $5::jsonb)
            on conflict (provider, provider_event_id) do nothing
            returning id
            """,
            provider.name,
            event["id"],
            event.get("type", ""),
            tenant_id,
            json.dumps(event),
        )
        if not inserted:
            return {"received": True, "duplicate": True}

        if event.get("type") == "checkout.session.completed" and tenant_id:
            await _checkout_completed(db, provider.name, tenant_id, obj)
        elif event.get("type") == "invoice.payment_failed" and tenant_id:
            await db.billingsubscription.update_many(where={"tenant_id": tenant_id}, data={"status": "past_due"})
        elif event.get("type") == "customer.subscription.deleted" and tenant_id:
            await db.billingsubscription.update_many(where={"tenant_id": tenant_id}, data={"status": "canceled"})
            org = await db.organization.find_unique(where={"id": tenant_id})
            if org and org.status == "active" and org.activation_source == "payment":
                await lifecycle.transition(
                    db, tenant_id, "suspended", actor_user_id=None, source="payment", note="Subscription ended"
                )

        await db.execute_raw(
            "update billing_webhook_events set processed_at = now() where id = $1::uuid", inserted[0]["id"]
        )
    return {"received": True}


async def _checkout_completed(db: Prisma, provider: str, tenant_id: str, obj: dict) -> None:
    metadata = obj.get("metadata") or {}
    tier = metadata.get("tier")
    interval = metadata.get("interval", "month")
    if tier not in PURCHASABLE_TIERS:
        return
    session = await db.billingcheckoutsession.find_first(
        where={"provider": provider, "provider_session_id": obj.get("id"), "tenant_id": tenant_id}
    )
    if session is None or session.status != "open":
        return  # not a checkout we started, or already applied
    # Never trust the amount in the event over what we priced at checkout.
    if obj.get("amount_total") is not None and int(obj["amount_total"]) != session.amount_cents:
        return

    now = datetime.now(UTC)
    period_end = now + (timedelta(days=365) if interval == "year" else timedelta(days=30))
    await db.billingcheckoutsession.update(where={"id": session.id}, data={"status": "completed", "completed_at": now})
    await db.execute_raw(
        """
        insert into billing_subscriptions
          (tenant_id, provider, tier, interval, status, provider_customer_id, provider_subscription_id,
           current_period_end, updated_at)
        values ($1::uuid, $2, $3, $4, 'active', $5, $6, $7::timestamptz, now())
        on conflict (tenant_id) do update set
          provider = excluded.provider, tier = excluded.tier, interval = excluded.interval, status = 'active',
          provider_customer_id = excluded.provider_customer_id,
          provider_subscription_id = excluded.provider_subscription_id,
          current_period_end = excluded.current_period_end, updated_at = now()
        """,
        tenant_id,
        provider,
        tier,
        interval,
        obj.get("customer"),
        obj.get("subscription"),
        period_end.isoformat(),
    )
    await db.billingpayment.create(
        data={
            "tenant_id": tenant_id,
            "provider": provider,
            "provider_reference": obj.get("id"),
            "amount_cents": session.amount_cents,
            "currency": session.currency,
            "status": "paid",
            "description": f"{PLAN_BY_TIER[tier].name} plan — {'annual' if interval == 'year' else 'monthly'}",
            "paid_at": now,
        }
    )
    org = await db.organization.find_unique(where={"id": tenant_id})
    if org.status in ("pending", "suspended", "trial", "active"):
        await lifecycle.transition(
            db,
            tenant_id,
            "active",
            actor_user_id=None,
            source="payment",
            note=f"Paid via {provider}",
            tier=tier,
        )
