"""Sends the email copy of notifications.

`notify` writes a pending `notification_deliveries` row in the same transaction
as the notification (an outbox), so by the time this sees a row the action that
caused it has committed, and a crash loses nothing. The in-app notification
never depends on this: a provider outage only delays or fails an email.

`deliver_pending` is safe to run from several processes at once (rows are
claimed with FOR UPDATE SKIP LOCKED) and as often as you like. Delivery is
at-least-once: a worker that dies between sending and recording leaves a row
that is retried after its lease, so an email can very rarely arrive twice, but
never zero times without a record.

`run_forever` is the loop the API starts in its lifespan; the same function can
run from cron instead (scripts/send_notification_emails.py).
"""

import asyncio
import logging
import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.mailer import OutgoingEmail, get_mailer
from app.modules.notifications import repository
from app.modules.notifications.service import with_defaults

logger = logging.getLogger("ecclesia_flow.notifications")

CHECK_EVERY_SECONDS = 30
BATCH_SIZE = 50
#: A claimed row not finished within this is assumed abandoned and retried.
LEASE_SECONDS = 10 * 60
#: Wait after the 1st, 2nd, 3rd, 4th failed attempt; the 5th failure gives up.
RETRY_DELAYS = (60, 5 * 60, 30 * 60, 2 * 60 * 60)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1

_ADDRESS = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
# Not under /portal: staff and board read their notifications elsewhere. The app sends whoever
# opens this to the right place for them (and to sign in first if they aren't).
_PREFERENCES_PATH = "/notifications/preferences"


@dataclass(frozen=True)
class DeliveryStats:
    claimed: int = 0
    sent: int = 0
    retrying: int = 0
    failed: int = 0
    skipped: int = 0


def scrub_error(exc: Exception) -> str:
    """A reason worth keeping, without anyone's address (provider errors often
    quote the recipient) and at most 500 characters."""
    text = _ADDRESS.sub("<address>", f"{type(exc).__name__}: {exc}")
    text = " ".join(text.split())
    return text if len(text) <= 500 else text[:499] + "…"


def build_email(row: dict) -> OutgoingEmail:
    """Plain text: the notification itself, a link back to the app, and why they got it.
    The subject is the notification's own single-line title."""
    base = get_settings().frontend_url.rstrip("/")
    parts = [row["body"]]
    if row["action_url"]:
        parts.append(f"Open it: {base}{row['action_url']}")
    parts.append(
        f"You're getting this because of your notification settings for {row['display_name']}. "
        f"Change them: {base}{_PREFERENCES_PATH}"
    )
    return OutgoingEmail(to=row["email"], subject=f"{row['display_name']}: {row['title']}", body="\n\n".join(parts))


async def _attempt(row: dict, stored_preferences: dict) -> tuple[str, str | None, int | None]:
    """(status, error, retry_in_seconds) for one claimed delivery."""
    if not row["user_active"] or not row["member_active"] or not row["email"]:
        return "skipped", "recipient can no longer be emailed", None
    # Re-check the choice now: they may have switched email off since it was queued.
    if not with_defaults(stored_preferences.get(str(row["user_id"])))[row["type"]]["email"]:
        return "skipped", "recipient turned email off", None
    try:
        await get_mailer().send(build_email(row))
    except Exception as exc:  # noqa: BLE001 — any provider failure is recorded the same way
        error = scrub_error(exc)
        if row["attempts"] >= MAX_ATTEMPTS:
            logger.error("notifications.email_gave_up delivery=%s attempts=%s", row["id"], row["attempts"])
            return "failed", error, None
        logger.warning("notifications.email_failed delivery=%s attempt=%s", row["id"], row["attempts"])
        return "pending", error, RETRY_DELAYS[row["attempts"] - 1]
    return "sent", None, None


async def deliver_pending(limit: int = BATCH_SIZE) -> DeliveryStats:
    """One pass: claim due email deliveries, send each, record the outcome."""
    async with platform_session() as db:
        claimed = await repository.claim_due_email_deliveries(db, limit, LEASE_SECONDS)
        stored = await repository.stored_preferences(db, list({str(r["user_id"]) for r in claimed})) if claimed else {}
    counts = {"sent": 0, "pending": 0, "failed": 0, "skipped": 0}
    for row in claimed:
        status, error, retry_in = await _attempt(row, stored)
        try:
            async with platform_session() as db:
                await repository.finish_delivery(db, str(row["id"]), status, error, retry_in)
        except Exception:  # noqa: BLE001 — the lease will bring the row back for a retry
            logger.exception("notifications.email_outcome_not_recorded delivery=%s", row["id"])
            continue
        counts[status] += 1
    return DeliveryStats(
        claimed=len(claimed),
        sent=counts["sent"],
        retrying=counts["pending"],
        failed=counts["failed"],
        skipped=counts["skipped"],
    )


async def run_forever() -> None:
    while True:
        try:
            while (stats := await deliver_pending()).claimed >= BATCH_SIZE:  # a backlog: keep going
                logger.info("notifications.emails_pass %s", stats)
            if stats.claimed:
                logger.info("notifications.emails_pass %s", stats)
        except Exception:  # noqa: BLE001 — a failed pass must not stop the loop
            logger.exception("notifications.emails_failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
