"""Paying by mobile money, approved by hand.

    church:    picks a plan and a number of months -> sends money to the MoMo
               number -> submits who sent it, how much, and a screenshot
    platform:  sees the request with the proof -> approves (or rejects) it

Approving is the only thing that grants time, and it runs on the platform
connection (the church's own database role can't update these rows at all).
It needs a full platform admin and a fresh two-step code (see the router), and
it works on a row that is locked and still pending, so one payment can never be
granted twice. Every step is audited.
"""

import secrets
import string
from datetime import UTC, datetime

from prisma import Prisma

from app.core import private_storage
from app.core.config import get_settings
from app.core.database import platform_session
from app.core.entitlement import add_months, to_utc
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.mailer import OutgoingEmail, send_quietly
from app.modules.audit.service import write_audit
from app.modules.billing import repository
from app.modules.notifications import service as notifications_service
from app.modules.org import lifecycle
from app.modules.org.catalogue import PLAN_BY_TIER, PURCHASABLE_TIERS
from app.modules.rbac import service as rbac_service

MAX_MONTHS = 36


class BadPaymentError(AppError):
    status_code = 422
    code = "invalid_payment"


def price_for(tier: str, months: int) -> tuple[str, int]:
    if tier not in PURCHASABLE_TIERS:
        raise BadPaymentError("That plan can't be bought online")
    if not 1 <= months <= MAX_MONTHS:
        raise BadPaymentError(f"Choose between 1 and {MAX_MONTHS} months")
    plan = PLAN_BY_TIER[tier]
    amount = plan.price_for_months(months)
    assert amount is not None  # purchasable tiers always have a price
    return plan.name, amount


def instructions(tier: str, months: int) -> dict:
    settings = get_settings()
    plan_name, amount = price_for(tier, months)
    return {
        "network": settings.momo_network,
        "number": settings.momo_number,
        "account_name": settings.momo_account_name,
        "tier": tier,
        "plan_name": plan_name,
        "months": months,
        "amount_cents": amount,
        "currency": settings.billing_currency,
    }


def _reference() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "EF-" + "".join(secrets.choice(alphabet) for _ in range(6))


async def list_for_church(db: Prisma, limit: int = 12) -> list[dict]:
    # The tenant role's row-level policy limits this to the signed-in church.
    return await repository.list_church_payments(db, limit)


async def has_pending(db: Prisma) -> bool:
    return await repository.has_pending_payment(db)


async def _explain_conflict(proof_sha256: str, txn: str | None) -> None:
    """Which unique rule was hit. Looked up across all churches (the church's
    own session can only see its own rows), and answered without naming one."""
    async with platform_session() as db:
        if await repository.proof_already_used(db, proof_sha256):
            raise ConflictError("That screenshot has already been submitted", code="proof_reused")
        if txn and await repository.transaction_already_used(db, txn):
            raise ConflictError("That MoMo transaction has already been submitted", code="transaction_reused")


async def submit(
    db: Prisma,
    *,
    org_id: str,
    user_id: str,
    tier: str,
    months: int,
    sender_name: str,
    sent_amount_cents: int,
    momo_transaction_id: str | None,
    proof: bytes,
) -> dict:
    sender_name = sender_name.strip()
    if len(sender_name) < 2 or len(sender_name) > 120:
        raise BadPaymentError("Enter the name the money was sent from")
    if sent_amount_cents <= 0:
        raise BadPaymentError("Enter the amount you sent")
    _, expected = price_for(tier, months)

    status = await repository.org_status(db, org_id)
    if status is None or status in ("canceled", "suspended"):
        raise ConflictError("This church's account can't take payments right now")
    if await repository.has_pending_payment(db):
        raise ConflictError(
            "A payment is already waiting for approval. You can send another once it has been decided.",
            code="payment_pending",
        )

    stored = private_storage.save(org_id, proof)  # type and size decided by the bytes
    txn = (momo_transaction_id or "").strip()[:60] or None
    payment = await repository.insert_manual_payment(
        db,
        {
            "tenant_id": org_id,
            "tier": tier,
            "months": months,
            "amount_cents": expected,
            "currency": get_settings().billing_currency,
            "reference_code": _reference(),
            "sender_name": sender_name,
            "sent_amount_cents": sent_amount_cents,
            "momo_transaction_id": txn,
            "proof_storage_key": stored.storage_key,
            "proof_mime_type": stored.mime_type,
            "proof_size_bytes": stored.size_bytes,
            "proof_sha256": stored.sha256,
            "submitted_by_user_id": user_id,
        },
    )
    if payment is None:
        # A unique index refused it. Say which, without saying whose.
        private_storage.discard(stored.storage_key)
        await _explain_conflict(stored.sha256, txn)
        raise ConflictError("A payment is already waiting for approval", code="payment_pending")

    await write_audit(
        db,
        tenant_id=org_id,
        actor_user_id=user_id,
        action="billing.payment_submitted",
        resource_type="billing_manual_payment",
        resource_id=payment["id"],
        metadata={"tier": tier, "months": months, "expected_cents": expected, "sent_cents": sent_amount_cents},
    )
    alert = get_settings().payment_alert_email
    if alert:
        await send_quietly(
            OutgoingEmail(
                to=alert,
                subject=f"Payment waiting for approval ({payment['reference_code']})",
                body=f"A church sent a mobile-money payment for {months} month(s) of {tier}. "
                "Open Payments in the platform console to check the proof and approve it.",
            )
        )
    return payment


# ───────────────────────────── Platform side ─────────────────────────────


def _admin_view(row: dict) -> dict:
    row = dict(row)
    now = datetime.now(UTC)
    provider, status = row.pop("sub_provider", None), row.pop("sub_status", None)
    end = to_utc(row.get("current_period_end"))
    running = provider == "manual" and status == "active" and end is not None and end > now
    row["amount_matches"] = row["sent_amount_cents"] == row["amount_cents"]
    row["would_grant_until"] = (
        add_months(end if running else now, row["months"]) if row["status"] == "pending" else None
    )
    row["current_period_end"] = end if running else None
    return row


async def list_all(db: Prisma, status: str | None) -> list[dict]:
    return [_admin_view(r) for r in await repository.list_admin_payments(db, status)]


async def get_one(db: Prisma, payment_id: str) -> dict:
    row = await repository.get_admin_payment(db, payment_id)
    if row is None:
        raise NotFoundError("No such payment")
    return _admin_view(row)


async def pending_count(db: Prisma) -> int:
    return await repository.count_pending_payments(db)


async def _lock(db: Prisma, payment_id: str) -> dict:
    payment = await repository.lock_payment(db, payment_id)
    if payment is None:
        raise NotFoundError("No such payment")
    return payment


async def read_proof(db: Prisma, admin_id: str, payment_id: str) -> tuple[bytes, str]:
    row = await repository.get_proof_row(db, payment_id)
    if row is None:
        raise NotFoundError("No such payment")
    data = private_storage.read(row["proof_storage_key"], row["proof_sha256"])
    await write_audit(
        db,
        tenant_id=row["tenant_id"],
        actor_user_id=admin_id,
        action="billing.proof_viewed",
        resource_type="billing_manual_payment",
        resource_id=payment_id,
    )
    return data, row["proof_mime_type"]


BILLING_URL = "/staff/settings/billing"


def months_text(months: int) -> str:
    return f"{months} month" + ("" if months == 1 else "s")


async def _tell_church(db: Prisma, tenant_id: str, payment: dict, *, title: str, body: str, key: str) -> None:
    """The in-app notice of a decision, for whoever can manage the church's billing
    and for whoever submitted the payment. The email the church already gets goes
    to its contact address, so this is in-app only (no second email). A pointer, not
    the platform admin's note: the reason stays in Billing, behind the permission."""
    admins = await rbac_service.users_with_permission(db, tenant_id, "billing:manage")
    await notifications_service.notify(
        db,
        tenant_id,
        [*admins, str(payment["submitted_by_user_id"])],
        "billing",
        title,
        body,
        action_url=BILLING_URL,
        resource=("billing_manual_payment", str(payment["id"])),
        dedupe_key=key,
        email=False,
    )


async def approve(db: Prisma, admin_id: str, payment_id: str, *, note: str, accept_amount_difference: bool) -> dict:
    payment = await _lock(db, payment_id)  # row lock: two admins can't both approve
    if payment["status"] != "pending":
        raise ConflictError(f"This payment was already {payment['status']}", code="already_decided")
    if payment["sent_amount_cents"] < payment["amount_cents"] and not accept_amount_difference:
        raise ConflictError(
            "The church says it sent less than the price. Confirm you want to approve it anyway.",
            code="amount_differs",
        )

    tenant_id = str(payment["tenant_id"])
    now = datetime.now(UTC)
    sub = await repository.lock_subscription(db, tenant_id)
    start = now
    if sub and sub["provider"] == "manual" and sub["status"] == "active":
        end = to_utc(sub["current_period_end"])
        if end is not None and end > now:
            start = end  # paying early adds to the time left, never loses it
    until = add_months(start, payment["months"])

    await repository.mark_payment_approved(
        db, payment_id, admin_id=admin_id, note=note or None, start=start, until=until
    )
    await repository.upsert_manual_subscription(
        db,
        tenant_id=tenant_id,
        tier=payment["tier"],
        interval="year" if payment["months"] % 12 == 0 else "month",
        period_end=until,
    )
    await repository.create_manual_billing_payment(
        db,
        tenant_id=tenant_id,
        payment_id=payment_id,
        amount_cents=payment["sent_amount_cents"],
        currency=payment["currency"],
        description=f"{PLAN_BY_TIER[payment['tier']].name} plan, {payment['months']} month(s), mobile money",
    )
    org = await repository.org_contact(db, tenant_id)
    if org and org["status"] in ("pending", "trial", "active"):
        await lifecycle.transition(
            db,
            tenant_id,
            "active",
            actor_user_id=admin_id,
            source="payment",
            note="Mobile money approved",
            tier=payment["tier"],
        )
    await repository.set_org_renewal_date(db, tenant_id, until)
    differs = payment["sent_amount_cents"] != payment["amount_cents"]
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=admin_id,
        action="billing.payment_approved",
        resource_type="billing_manual_payment",
        resource_id=payment_id,
        metadata={
            "months": payment["months"],
            "tier": payment["tier"],
            "expected_cents": payment["amount_cents"],
            "sent_cents": payment["sent_amount_cents"],
            "granted_until": until.isoformat(),
            "amount_difference_accepted": accept_amount_difference and differs,
        },
    )
    await _tell_church(
        db,
        tenant_id,
        payment,
        title="Payment approved",
        body=f"{months_text(payment['months'])} of the {PLAN_BY_TIER[payment['tier']].name} plan were added. "
        f"Your plan now runs until {until:%d %B %Y}.",
        key=f"billing.payment_approved:{payment_id}",
    )
    if org and org["contact_email"]:
        await send_quietly(
            OutgoingEmail(
                to=org["contact_email"],
                subject="Your payment was approved",
                body=f"Thank you. {org['display_name']} now has the {PLAN_BY_TIER[payment['tier']].name} plan "
                f"until {until:%d %B %Y}.",
            )
        )
    return await get_one(db, payment_id)


async def reject(db: Prisma, admin_id: str, payment_id: str, *, reason: str) -> dict:
    if len(reason) < 3:
        raise BadPaymentError("Say why the payment is being rejected so the church can fix it")
    payment = await _lock(db, payment_id)
    if payment["status"] != "pending":
        raise ConflictError(f"This payment was already {payment['status']}", code="already_decided")
    await repository.mark_payment_rejected(db, payment_id, admin_id=admin_id, reason=reason)
    tenant_id = str(payment["tenant_id"])
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=admin_id,
        action="billing.payment_rejected",
        resource_type="billing_manual_payment",
        resource_id=payment_id,
        metadata={"reason": reason},
    )
    await _tell_church(
        db,
        tenant_id,
        payment,
        title="Payment could not be approved",
        body="We couldn't approve the payment you sent. Open Billing to see why and send it again.",
        key=f"billing.payment_rejected:{payment_id}",
    )
    org = await repository.org_contact(db, tenant_id)
    if org and org["contact_email"]:
        await send_quietly(
            OutgoingEmail(
                to=org["contact_email"],
                subject="Your payment could not be approved",
                body=f"We could not approve the payment for {org['display_name']}: {reason}\n"
                "You can submit it again from Billing.",
            )
        )
    return await get_one(db, payment_id)
