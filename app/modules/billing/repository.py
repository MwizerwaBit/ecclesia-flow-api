"""Billing persistence: checkout sessions, subscriptions, payments and the
webhook event log. Webhook writes run on the platform connection (no tenant
session exists for them), so callers pass that client in."""

import json
from datetime import datetime

from prisma import Prisma


async def get_subscription(db: Prisma, tenant_id: str):
    return await db.billingsubscription.find_unique(where={"tenant_id": tenant_id})


async def list_payments(db: Prisma, tenant_id: str, limit: int = 24) -> list:
    return await db.billingpayment.find_many(where={"tenant_id": tenant_id}, order={"created_at": "desc"}, take=limit)


async def create_checkout_session(db: Prisma, data: dict) -> None:
    await db.billingcheckoutsession.create(data=data)


async def find_checkout_session(db: Prisma, *, tenant_id: str, provider: str, provider_session_id: str):
    return await db.billingcheckoutsession.find_first(
        where={"tenant_id": tenant_id, "provider": provider, "provider_session_id": provider_session_id}
    )


async def complete_checkout_session(db: Prisma, session_id: str, completed_at: datetime) -> None:
    await db.billingcheckoutsession.update(
        where={"id": session_id}, data={"status": "completed", "completed_at": completed_at}
    )


async def record_webhook_event(
    db: Prisma, *, provider: str, event_id: str, event_type: str, tenant_id: str | None, event: dict
) -> str | None:
    """Stores the event once; returns its id, or None if it was already seen."""
    rows = await db.query_raw(
        """
        insert into billing_webhook_events (provider, provider_event_id, type, tenant_id, payload)
        values ($1, $2, $3, $4::uuid, $5::jsonb)
        on conflict (provider, provider_event_id) do nothing
        returning id
        """,
        provider,
        event_id,
        event_type,
        tenant_id,
        json.dumps(event),
    )
    return rows[0]["id"] if rows else None


async def mark_webhook_processed(db: Prisma, event_row_id: str) -> None:
    await db.execute_raw("update billing_webhook_events set processed_at = now() where id = $1::uuid", event_row_id)


async def set_subscription_status(db: Prisma, tenant_id: str, status: str) -> None:
    await db.billingsubscription.update_many(where={"tenant_id": tenant_id}, data={"status": status})


async def upsert_active_subscription(
    db: Prisma,
    *,
    tenant_id: str,
    provider: str,
    tier: str,
    interval: str,
    customer_id: str | None,
    subscription_id: str | None,
    period_end: datetime,
) -> None:
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
        customer_id,
        subscription_id,
        period_end.isoformat(),
    )


async def create_payment(db: Prisma, data: dict) -> None:
    await db.billingpayment.create(data=data)
