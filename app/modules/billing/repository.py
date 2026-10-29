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


# ───────────────────────────── Manual (mobile-money) payments ─────────────────────────────

CHURCH_COLUMNS = """
    id::text, tier, months, amount_cents, currency, reference_code, sender_name, sent_amount_cents,
    momo_transaction_id, status, submitted_at, decided_at, decision_note, granted_until
"""

ADMIN_SELECT = """
    p.id::text, p.tenant_id::text, o.display_name as church_name, o.contact_email as church_email,
    p.tier, p.months, p.amount_cents, p.currency, p.reference_code, p.sender_name, p.sent_amount_cents,
    p.momo_transaction_id, p.status, p.submitted_at, p.decided_at, p.decision_note, p.granted_until,
    p.proof_mime_type, s.current_period_end, s.provider as sub_provider, s.status as sub_status
"""


async def org_status(db: Prisma, tenant_id: str) -> str | None:
    rows = await db.query_raw("select status from organizations where id = $1::uuid", tenant_id)
    return rows[0]["status"] if rows else None


async def org_contact(db: Prisma, tenant_id: str) -> dict | None:
    rows = await db.query_raw(
        "select status, contact_email, display_name from organizations where id = $1::uuid", tenant_id
    )
    return rows[0] if rows else None


async def set_org_renewal_date(db: Prisma, tenant_id: str, until: datetime) -> None:
    await db.execute_raw(
        "update organizations set renewal_date = $2::timestamptz where id = $1::uuid", tenant_id, until.isoformat()
    )


async def has_pending_payment(db: Prisma) -> bool:
    """Under a church's own session this sees that church's rows only."""
    return bool(await db.query_raw("select 1 from billing_manual_payments where status = 'pending' limit 1"))


async def list_church_payments(db: Prisma, limit: int) -> list[dict]:
    return await db.query_raw(
        f"select {CHURCH_COLUMNS} from billing_manual_payments order by submitted_at desc limit {int(limit)}"
    )


async def insert_manual_payment(db: Prisma, values: dict) -> dict | None:
    """None when a unique rule refused it (one pending per church, the same
    screenshot or transaction twice)."""
    rows = await db.query_raw(
        f"""
        insert into billing_manual_payments
          (tenant_id, tier, months, amount_cents, currency, reference_code, sender_name, sent_amount_cents,
           momo_transaction_id, proof_storage_key, proof_mime_type, proof_size_bytes, proof_sha256,
           submitted_by_user_id)
        values ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::uuid)
        on conflict do nothing
        returning {CHURCH_COLUMNS}
        """,
        values["tenant_id"],
        values["tier"],
        values["months"],
        values["amount_cents"],
        values["currency"],
        values["reference_code"],
        values["sender_name"],
        values["sent_amount_cents"],
        values["momo_transaction_id"],
        values["proof_storage_key"],
        values["proof_mime_type"],
        values["proof_size_bytes"],
        values["proof_sha256"],
        values["submitted_by_user_id"],
    )
    return rows[0] if rows else None


async def proof_already_used(db: Prisma, proof_sha256: str) -> bool:
    """Across every church: needs the platform connection."""
    return bool(
        await db.query_raw(
            "select 1 from billing_manual_payments where proof_sha256 = $1 and status in ('pending', 'approved')",
            proof_sha256,
        )
    )


async def transaction_already_used(db: Prisma, momo_transaction_id: str) -> bool:
    return bool(
        await db.query_raw(
            "select 1 from billing_manual_payments where lower(momo_transaction_id) = lower($1)"
            " and status in ('pending', 'approved')",
            momo_transaction_id,
        )
    )


async def list_admin_payments(db: Prisma, status: str | None) -> list[dict]:
    return await db.query_raw(
        f"""
        select {ADMIN_SELECT}
        from billing_manual_payments p
        join organizations o on o.id = p.tenant_id
        left join billing_subscriptions s on s.tenant_id = p.tenant_id
        where ($1::text is null or p.status = $1)
        order by (p.status = 'pending') desc, p.submitted_at desc
        limit 200
        """,
        status,
    )


async def get_admin_payment(db: Prisma, payment_id: str) -> dict | None:
    rows = await db.query_raw(
        f"""
        select {ADMIN_SELECT}
        from billing_manual_payments p
        join organizations o on o.id = p.tenant_id
        left join billing_subscriptions s on s.tenant_id = p.tenant_id
        where p.id = $1::uuid
        """,
        payment_id,
    )
    return rows[0] if rows else None


async def count_pending_payments(db: Prisma) -> int:
    rows = await db.query_raw("select count(*)::int as n from billing_manual_payments where status = 'pending'")
    return rows[0]["n"]


async def lock_payment(db: Prisma, payment_id: str) -> dict | None:
    """Row-locked for the rest of the transaction: two admins can't both decide it."""
    rows = await db.query_raw("select * from billing_manual_payments where id = $1::uuid for update", payment_id)
    return rows[0] if rows else None


async def get_proof_row(db: Prisma, payment_id: str) -> dict | None:
    rows = await db.query_raw(
        "select tenant_id::text, proof_storage_key, proof_sha256, proof_mime_type from billing_manual_payments"
        " where id = $1::uuid",
        payment_id,
    )
    return rows[0] if rows else None


async def lock_subscription(db: Prisma, tenant_id: str) -> dict | None:
    rows = await db.query_raw(
        "select provider, status, current_period_end from billing_subscriptions where tenant_id = $1::uuid for update",
        tenant_id,
    )
    return rows[0] if rows else None


async def mark_payment_approved(
    db: Prisma, payment_id: str, *, admin_id: str, note: str | None, start: datetime, until: datetime
) -> None:
    await db.execute_raw(
        """
        update billing_manual_payments
        set status = 'approved', decided_by_user_id = $2::uuid, decided_at = now(), decision_note = $3,
            granted_from = $4::timestamptz, granted_until = $5::timestamptz
        where id = $1::uuid and status = 'pending'
        """,
        payment_id,
        admin_id,
        note,
        start.isoformat(),
        until.isoformat(),
    )


async def mark_payment_rejected(db: Prisma, payment_id: str, *, admin_id: str, reason: str) -> None:
    await db.execute_raw(
        """
        update billing_manual_payments
        set status = 'rejected', decided_by_user_id = $2::uuid, decided_at = now(), decision_note = $3
        where id = $1::uuid and status = 'pending'
        """,
        payment_id,
        admin_id,
        reason,
    )


async def upsert_manual_subscription(
    db: Prisma, *, tenant_id: str, tier: str, interval: str, period_end: datetime
) -> None:
    await db.execute_raw(
        """
        insert into billing_subscriptions
          (tenant_id, provider, tier, interval, status, current_period_end, renewal_reminder_for, updated_at)
        values ($1::uuid, 'manual', $2, $3, 'active', $4::timestamptz, null, now())
        on conflict (tenant_id) do update set
          provider = 'manual', tier = excluded.tier, interval = excluded.interval, status = 'active',
          provider_customer_id = null, provider_subscription_id = null,
          current_period_end = excluded.current_period_end, renewal_reminder_for = null, updated_at = now()
        """,
        tenant_id,
        tier,
        interval,
        period_end.isoformat(),
    )


async def create_manual_billing_payment(
    db: Prisma, *, tenant_id: str, payment_id: str, amount_cents: int, currency: str, description: str
) -> None:
    await db.execute_raw(
        """
        insert into billing_payments
          (tenant_id, provider, provider_reference, amount_cents, currency, status, description, paid_at)
        values ($1::uuid, 'manual', $2, $3, $4, 'paid', $5, now())
        """,
        tenant_id,
        payment_id,
        amount_cents,
        currency,
        description,
    )


async def claim_due_reminders(db: Prisma, within_days: int) -> list[dict]:
    """Marks each plan ending within `within_days` as reminded for its current
    end date, and returns the ones just claimed. One UPDATE, so concurrent
    callers can't both claim the same plan."""
    return await db.query_raw(
        """
        update billing_subscriptions s
        set renewal_reminder_for = s.current_period_end
        from organizations o
        where o.id = s.tenant_id
          and s.provider = 'manual' and s.status = 'active'
          and s.current_period_end > now()
          and s.current_period_end <= now() + make_interval(days => $1::int)
          and s.renewal_reminder_for is distinct from s.current_period_end
        returning s.tenant_id, o.display_name, o.contact_email, s.current_period_end
        """,
        within_days,
    )
