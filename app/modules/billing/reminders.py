"""The "your plan ends soon" email.

`send_due_reminders` is safe to run as often as you like, from several
processes at once: each subscription is claimed with one UPDATE that only
succeeds while no reminder has gone out for its current end date, so a church
gets exactly one email per period. Renewing moves the end date (and clears the
claim, see manual.approve), so the next period gets its own.

`run_forever` is the loop the API starts in its lifespan; the same function
can be run from cron instead (scripts/send_renewal_reminders.py).
"""

import asyncio
import logging

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.entitlement import to_utc
from app.core.mailer import OutgoingEmail, send_quietly
from app.modules.billing import repository
from app.modules.notifications import service as notifications_service
from app.modules.rbac import service as rbac_service

logger = logging.getLogger("ecclesia_flow.billing")

CHECK_EVERY_SECONDS = 6 * 60 * 60


async def _in_app_copy(tenant_id: str, ends) -> None:
    """The same reminder in the bell of whoever manages billing. In-app only: the
    email above already goes to the church's contact address. A failure here is
    logged and never stops the email or the other churches."""
    try:
        async with platform_session() as db:
            await notifications_service.notify(
                db,
                tenant_id,
                await rbac_service.users_with_permission(db, tenant_id, "billing:manage"),
                "billing",
                f"Your plan ends on {ends:%d %B %Y}",
                "Renew from Billing to keep everything working. After it ends, free basics keep working for "
                f"{get_settings().billing_grace_days} days, then your church becomes view-only until you pay.",
                action_url="/staff/settings/billing",
                resource=("organization", tenant_id),
                dedupe_key=f"billing.renewal:{tenant_id}:{ends.isoformat()}",
                email=False,
            )
    except Exception:  # noqa: BLE001 — the email and the other churches go on
        logger.exception("billing.reminder_in_app_failed tenant=%s", tenant_id)


async def send_due_reminders() -> int:
    settings = get_settings()
    async with platform_session() as db:
        due = await repository.claim_due_reminders(db, settings.renewal_reminder_days)
    sent = 0
    for row in due:
        ends = to_utc(row["current_period_end"])
        await _in_app_copy(str(row["tenant_id"]), ends)
        if not row["contact_email"]:
            continue
        await send_quietly(
            OutgoingEmail(
                to=row["contact_email"],
                subject=f"{row['display_name']}: your EcclesiaFlow plan ends on {ends:%d %B %Y}",
                body=(
                    f"Your plan ends on {ends:%d %B %Y}. To keep everything working, renew from "
                    f"Billing: {settings.frontend_url}/staff/settings/billing\n\n"
                    f"After it ends, free basics keep working for {settings.billing_grace_days} days, "
                    "then your church becomes view-only until you pay."
                ),
            )
        )
        sent += 1
    return sent


async def run_forever() -> None:
    while True:
        try:
            sent = await send_due_reminders()
            if sent:
                logger.info("billing.reminders_sent count=%s", sent)
        except Exception:  # noqa: BLE001 — a failed pass must not stop the loop
            logger.exception("billing.reminders_failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
