from datetime import UTC, datetime, timedelta

from prisma import Prisma

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.finance import repository
from app.modules.finance.schemas import DonationBatchCreate, DonationEntryForm, FundCreate

# Never interpolate a client-supplied string into SQL — this is the only
# place `period` is allowed to become a Postgres date_trunc unit, and only
# because it's drawn from this fixed map, never from the request directly.
_PERIOD_BUCKETS = {"weekly": "day", "monthly": "day", "annual": "month"}
_PERIOD_SPANS = {"weekly": timedelta(weeks=1), "monthly": timedelta(days=30), "annual": timedelta(days=365)}


async def list_funds(db: Prisma) -> list:
    return await repository.list_funds(db)


async def create_fund(db: Prisma, tenant_id: str, payload: FundCreate):
    return await repository.create_fund(db, tenant_id, payload.model_dump())


async def list_batches(db: Prisma, *, status: str | None) -> list[dict]:
    return await repository.list_batches(db, status=status)


async def get_batch(db: Prisma, batch_id: str) -> dict:
    row = await repository.get_batch_row(db, batch_id)
    if row is None:
        raise NotFoundError("No such batch")
    return row


async def create_batch(db: Prisma, tenant_id: str, created_by_user_id: str, payload: DonationBatchCreate) -> dict:
    batch = await repository.create_batch(db, tenant_id, created_by_user_id, payload.model_dump())
    return await get_batch(db, batch.id)


async def close_batch(db: Prisma, batch_id: str, verified_total: float) -> dict:
    batch = await repository.get_batch(db, batch_id)
    if batch is None:
        raise NotFoundError("No such batch")
    if batch.status != "open":
        raise ConflictError("Only an open batch can be closed")
    await repository.update_batch(
        db, batch_id, {"status": "closed", "verified_total": verified_total, "closed_at": datetime.now(UTC)}
    )
    return await get_batch(db, batch_id)


async def post_batch(db: Prisma, batch_id: str) -> dict:
    batch = await repository.get_batch(db, batch_id)
    if batch is None:
        raise NotFoundError("No such batch")
    if batch.status != "closed":
        raise ConflictError("Only a closed batch can be posted")
    await repository.update_batch(db, batch_id, {"status": "posted", "posted_at": datetime.now(UTC)})
    return await get_batch(db, batch_id)


async def list_donations(db: Prisma, batch_id: str) -> list[dict]:
    return await repository.list_donations_in_batch(db, batch_id)


async def list_donations_by_member(db: Prisma, member_id: str) -> list[dict]:
    return await repository.list_donations_by_member(db, member_id)


async def create_donation(
    db: Prisma, tenant_id: str, batch_id: str, created_by_user_id: str, form: DonationEntryForm
) -> dict:
    batch = await repository.get_batch(db, batch_id)
    if batch is None:
        raise NotFoundError("No such batch")
    if batch.status != "open":
        raise ConflictError("This batch is no longer open for entries")
    fund = await repository.get_fund(db, form.fund_id)
    if fund is None:
        raise NotFoundError("No such fund")

    data = form.model_dump(exclude={"mode"})
    if form.mode == "member_search" and form.member_id:
        data["is_guest"] = False
    elif not form.member_id:
        data["is_guest"] = True

    donation = await repository.create_donation(
        db, tenant_id=tenant_id, batch_id=batch_id, created_by_user_id=created_by_user_id, data=data
    )
    row = await repository.get_donation_row(db, donation.id)
    # Running batch totals are read fresh from donation_batches by the
    # client after invalidating — Prisma doesn't auto-maintain totalAmount/
    # donationCount, so bump them here in the same request rather than
    # leaving the batch summary stale until some later recompute.
    await repository.update_batch(
        db,
        batch_id,
        {"total_amount": float(batch.total_amount) + form.amount, "donation_count": batch.donation_count + 1},
    )
    await repository.update_fund_total(db, form.fund_id, float(fund.total_received) + form.amount)
    return row


async def void_donation(db: Prisma, donation_id: str, *, reason: str, voided_by_user_id: str) -> dict:
    donation = await repository.get_donation(db, donation_id)
    if donation is None:
        raise NotFoundError("No such donation")
    if donation.is_voided:
        raise ConflictError("This donation has already been voided")
    await repository.void_donation(db, donation_id, reason=reason, voided_by_user_id=voided_by_user_id)
    fund = await repository.get_fund(db, donation.fund_id)
    if fund is not None:
        await repository.update_fund_total(db, donation.fund_id, max(fund.total_received - donation.amount, 0))
    batch = await repository.get_batch(db, donation.batch_id)
    if batch is not None:
        await repository.update_batch(
            db,
            donation.batch_id,
            {
                "total_amount": max(batch.total_amount - donation.amount, 0),
                "donation_count": max(batch.donation_count - 1, 0),
            },
        )
    return await repository.get_donation_row(db, donation_id)


async def list_pledges(db: Prisma) -> list[dict]:
    return await repository.list_pledges(db)


async def get_dashboard(db: Prisma) -> dict:
    now = datetime.now(UTC)
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    last_week_start = week_start - timedelta(days=7)
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    last_year_start = year_start.replace(year=year_start.year - 1)

    this_week_total = await repository.sum_donations_between(db, week_start, now)
    last_week_total = await repository.sum_donations_between(db, last_week_start, week_start)
    year_to_date_total = await repository.sum_donations_between(db, year_start, now)
    last_year_total = await repository.sum_donations_between(db, last_year_start, year_start)
    open_batches = await repository.count_open_batches(db)
    progress = await repository.pledge_progress(db)
    breakdown_rows = await repository.fund_breakdown_since(db, year_start)
    total_for_pct = sum(float(r["amount"]) for r in breakdown_rows) or 1.0
    fund_breakdown = [
        {"fund_name": r["fund_name"], "amount": float(r["amount"]), "percentage": float(r["amount"]) / total_for_pct}
        for r in breakdown_rows
    ]
    return {
        "this_week_total": this_week_total,
        "last_week_total": last_week_total,
        "open_batch_count": open_batches,
        "pledge_progress": progress,
        "year_to_date_total": year_to_date_total,
        "last_year_total": last_year_total,
        "fund_breakdown": fund_breakdown,
    }


async def get_giving_report(db: Prisma, period: str) -> dict:
    if period not in _PERIOD_BUCKETS:
        raise ConflictError("period must be one of: weekly, monthly, annual")
    end = datetime.now(UTC)
    start = end - _PERIOD_SPANS[period]

    by_fund_rows = await repository.donations_grouped_by_fund(db, start, end)
    total = sum(float(r["amount"]) for r in by_fund_rows) or 0.0
    by_fund = [
        {
            "fund_id": r["fund_id"],
            "fund_name": r["fund_name"],
            "amount": float(r["amount"]),
            "percentage": (float(r["amount"]) / total) if total else 0.0,
        }
        for r in by_fund_rows
    ]
    trend_rows = await repository.donations_trend(db, start, end, _PERIOD_BUCKETS[period])
    trend = [{"label": r["label"], "amount": float(r["amount"])} for r in trend_rows]
    donation_count = await repository.count_donations_between(db, start, end)

    return {
        "period": period,
        "start_date": start.date(),
        "end_date": end.date(),
        "total_amount": total,
        "donation_count": donation_count,
        "by_fund": by_fund,
        "trend": trend,
    }


async def generate_contribution_statement(db: Prisma, member_id: str, year: int) -> dict:
    donations = await repository.list_donations_by_member(db, member_id)
    in_year = [d for d in donations if d["created_at"].year == year]
    member_name = await repository.get_member_name(db, member_id)
    if member_name is None:
        raise NotFoundError("No such member")
    return {
        "member_id": member_id,
        "member_name": member_name,
        "year": year,
        "total_amount": sum(float(d["amount"]) for d in in_year),
        "donations": [
            {
                "date": d["created_at"],
                "fund_name": d["fund_name"],
                "amount": float(d["amount"]),
                "payment_method": d["payment_method"],
                "reference_number": d["reference_number"],
            }
            for d in in_year
        ],
        "generated_at": datetime.now(UTC),
    }
