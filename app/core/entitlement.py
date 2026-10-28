"""What a church may do right now, worked out from dates.

Nothing here is stored or scheduled. The answer is derived on every request
from the organisation, its subscription and the clock, so it is correct when a
payment fails, when a plan runs out overnight, and when no background job ran.

    full       paid up (or activated by the platform, or on a card plan that
               is still active): everything the plan includes
    basics     a new church that has not paid yet, or one whose plan just ran
               out, inside the grace days: the free modules work, the paid ones
               are locked
    view_only  past the grace days: everything can be read, nothing changed;
               paid modules stay locked
    suspended  stopped by the platform
    closed     canceled
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from prisma import Prisma

from app.core.config import get_settings
from app.core.exceptions import AppError, ForbiddenError

FULL, BASICS, VIEW_ONLY, SUSPENDED, CLOSED = "full", "basics", "view_only", "suspended", "closed"


class SubscriptionRequiredError(AppError):
    """402: reading is fine, changing things needs a paid plan."""

    status_code = 402
    code = "subscription_required"


class ModuleLockedError(AppError):
    """402: this part of the product is locked until the church pays."""

    status_code = 402
    code = "module_locked"


@dataclass(frozen=True)
class Access:
    state: str
    #: The tier to treat the church as having: its plan while paid, otherwise free.
    tier: str
    #: When the paid period ends (full) or ended (basics / view_only).
    period_end: datetime | None = None
    #: When the free basics stop and the church becomes view-only.
    grace_ends_at: datetime | None = None
    #: Whole days until period_end while paid; None otherwise.
    days_left: int | None = None
    #: True in the last `renewal_reminder_days` of a paid period.
    renewal_due: bool = False

    @property
    def can_write(self) -> bool:
        return self.state in (FULL, BASICS)


def compute_access(
    *,
    org_status: str,
    org_tier: str,
    created_at: datetime,
    trial_ends_at: datetime | None,
    sub_provider: str | None,
    sub_status: str | None,
    sub_tier: str | None,
    sub_period_end: datetime | None,
    sub_updated_at: datetime | None,
    now: datetime,
) -> Access:
    settings = get_settings()
    grace = timedelta(days=settings.billing_grace_days)

    if org_status == "canceled":
        return Access(CLOSED, "free")
    if org_status == "suspended":
        return Access(SUSPENDED, "free")

    def lapsed(since: datetime) -> Access:
        until = since + grace
        return Access(BASICS if now < until else VIEW_ONLY, "free", period_end=since, grace_ends_at=until)

    if org_status == "pending":
        # Never paid: the clock starts at sign-up.
        return lapsed(created_at)

    if sub_provider == "manual":
        # Time bought by hand is exactly what the end date says.
        if sub_status == "active" and sub_period_end is not None:
            if now <= sub_period_end:
                days = (sub_period_end - now).days
                return Access(
                    FULL,
                    sub_tier or org_tier,
                    period_end=sub_period_end,
                    days_left=days,
                    renewal_due=days < settings.renewal_reminder_days,
                )
            return lapsed(sub_period_end)
        return lapsed(sub_period_end or created_at)

    if sub_provider is not None:
        # A card subscription renews itself; the processor reports trouble.
        if sub_status in ("past_due", "canceled", "incomplete"):
            return lapsed(sub_updated_at or created_at)
        return Access(FULL, sub_tier or org_tier, period_end=sub_period_end)

    # Active or on trial with no subscription at all: activated by a platform
    # admin (a verified church, a comped one). Only a trial has an end.
    if org_status == "trial" and trial_ends_at is not None and now > trial_ends_at:
        return lapsed(trial_ends_at)
    return Access(FULL, org_tier, period_end=trial_ends_at if org_status == "trial" else None)


async def load_access(db: Prisma, tenant_id: str) -> Access | None:
    rows = await db.query_raw(
        """
        select o.status, o.tier, o.created_at, o.trial_ends_at,
               s.provider, s.status as sub_status, s.tier as sub_tier,
               s.current_period_end, s.updated_at as sub_updated_at
        from organizations o
        left join billing_subscriptions s on s.tenant_id = o.id
        where o.id = $1::uuid
        """,
        tenant_id,
    )
    if not rows:
        return None
    r = rows[0]
    return compute_access(
        org_status=r["status"],
        org_tier=r["tier"],
        created_at=to_utc(r["created_at"]),
        trial_ends_at=to_utc(r["trial_ends_at"]),
        sub_provider=r["provider"],
        sub_status=r["sub_status"],
        sub_tier=r["sub_tier"],
        sub_period_end=to_utc(r["current_period_end"]),
        sub_updated_at=to_utc(r["sub_updated_at"]),
        now=datetime.now(UTC),
    )


def to_utc(value: datetime | str | None) -> datetime | None:
    """Raw queries hand timestamps back as ISO strings; normalise to aware UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def refuse_if_closed(access: Access) -> None:
    if access.state == SUSPENDED:
        raise ForbiddenError("This church is suspended. Contact EcclesiaFlow support.", code="org_suspended")
    if access.state == CLOSED:
        raise ForbiddenError("This church's account is closed", code="org_closed")


def add_months(start: datetime, months: int) -> datetime:
    """`start` plus calendar months, clamping the day (31 Jan + 1 month = 28/29 Feb)."""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    for day in range(start.day, 27, -1):
        try:
            return start.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    return start.replace(year=year, month=month, day=28)
