from datetime import UTC, datetime

from prisma import Prisma
from prisma.models import Donation, DonationBatch, Fund

from app.core.prisma_utils import coerce_dates


async def list_funds(db: Prisma, *, active_only: bool = True) -> list[Fund]:
    where = {"is_active": True} if active_only else {}
    return await db.fund.find_many(where=where, order={"name": "asc"})


async def create_fund(db: Prisma, tenant_id: str, data: dict) -> Fund:
    return await db.fund.create(data={**data, "tenant_id": tenant_id})


async def get_fund(db: Prisma, fund_id: str) -> Fund | None:
    return await db.fund.find_unique(where={"id": fund_id})


async def update_fund_total(db: Prisma, fund_id: str, total_received: float) -> None:
    await db.fund.update(where={"id": fund_id}, data={"total_received": total_received})


async def list_batches(db: Prisma, *, status: str | None) -> list[dict]:
    where_sql = "where b.status = $1" if status else ""
    params = [status] if status else []
    return await db.query_raw(
        f"""
        select b.*, e.title as service_name, (u.first_name || ' ' || u.last_name) as created_by_name
        from donation_batches b
        left join events e on e.id = b.service_id
        join users u on u.id = b.created_by_user_id
        {where_sql}
        order by b.date desc
        """,
        *params,
    )


async def get_batch_row(db: Prisma, batch_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select b.*, e.title as service_name, (u.first_name || ' ' || u.last_name) as created_by_name
        from donation_batches b
        left join events e on e.id = b.service_id
        join users u on u.id = b.created_by_user_id
        where b.id = $1::uuid
        """,
        batch_id,
    )
    return rows[0] if rows else None


async def get_batch(db: Prisma, batch_id: str) -> DonationBatch | None:
    return await db.donationbatch.find_unique(where={"id": batch_id})


async def create_batch(db: Prisma, tenant_id: str, created_by_user_id: str, data: dict) -> DonationBatch:
    data = coerce_dates({**data, "tenant_id": tenant_id, "created_by_user_id": created_by_user_id})
    return await db.donationbatch.create(data=data)


async def update_batch(db: Prisma, batch_id: str, data: dict) -> DonationBatch:
    return await db.donationbatch.update(where={"id": batch_id}, data=data)


async def list_donations_in_batch(db: Prisma, batch_id: str) -> list[dict]:
    return await db.query_raw(
        """
        select d.*, coalesce(m.first_name || ' ' || m.last_name, d.guest_name) as member_name, f.name as fund_name
        from donations d
        left join members m on m.id = d.member_id
        join funds f on f.id = d.fund_id
        where d.batch_id = $1::uuid
        order by d.created_at desc
        """,
        batch_id,
    )


async def list_donations_by_member(db: Prisma, member_id: str) -> list[dict]:
    return await db.query_raw(
        """
        select d.*, (m.first_name || ' ' || m.last_name) as member_name, f.name as fund_name
        from donations d join members m on m.id = d.member_id join funds f on f.id = d.fund_id
        where d.member_id = $1::uuid and not d.is_voided
        order by d.created_at desc
        """,
        member_id,
    )


async def get_member_name(db: Prisma, member_id: str) -> str | None:
    member = await db.member.find_unique(where={"id": member_id})
    return f"{member.first_name} {member.last_name}" if member else None


async def create_donation(
    db: Prisma, *, tenant_id: str, batch_id: str, created_by_user_id: str, data: dict
) -> Donation:
    return await db.donation.create(
        data={**data, "tenant_id": tenant_id, "batch_id": batch_id, "created_by_user_id": created_by_user_id}
    )


async def get_donation_row(db: Prisma, donation_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select d.*, coalesce(m.first_name || ' ' || m.last_name, d.guest_name) as member_name, f.name as fund_name
        from donations d
        left join members m on m.id = d.member_id
        join funds f on f.id = d.fund_id
        where d.id = $1::uuid
        """,
        donation_id,
    )
    return rows[0] if rows else None


async def get_donation(db: Prisma, donation_id: str) -> Donation | None:
    return await db.donation.find_unique(where={"id": donation_id})


async def void_donation(db: Prisma, donation_id: str, *, reason: str, voided_by_user_id: str) -> None:
    await db.donation.update(
        where={"id": donation_id},
        data={
            "is_voided": True,
            "void_reason": reason,
            "voided_at": datetime.now(UTC),
            "voided_by_user_id": voided_by_user_id,
        },
    )


async def list_pledges(db: Prisma) -> list[dict]:
    return await db.query_raw(
        """
        select p.*, (m.first_name || ' ' || m.last_name) as member_name, f.name as fund_name
        from pledges p join members m on m.id = p.member_id join funds f on f.id = p.fund_id
        order by p.created_at desc
        """
    )


# ───────────────────────────── Dashboard / reports ─────────────────────────────


async def sum_donations_between(db: Prisma, start: datetime, end: datetime) -> float:
    # query_raw sends parameters as text, not typed values — unlike asyncpg,
    # Prisma's engine needs an explicit cast to compare them against a
    # timestamptz column, or Postgres rejects it outright (`timestamptz >=
    # text` has no operator). Every raw datetime comparison in this file
    # needs the same `::timestamptz` cast.
    rows = await db.query_raw(
        "select coalesce(sum(amount), 0) as total from donations"
        " where not is_voided and created_at >= $1::timestamptz and created_at < $2::timestamptz",
        start,
        end,
    )
    return float(rows[0]["total"])


async def count_donations_between(db: Prisma, start: datetime, end: datetime) -> int:
    return await db.donation.count(where={"is_voided": False, "created_at": {"gte": start, "lt": end}})


async def count_open_batches(db: Prisma) -> int:
    return await db.donationbatch.count(where={"status": "open"})


async def pledge_progress(db: Prisma) -> float:
    rows = await db.query_raw(
        "select coalesce(sum(amount_fulfilled),0) as fulfilled, coalesce(sum(pledge_amount),0) as pledged from pledges"
    )
    pledged = float(rows[0]["pledged"])
    return (float(rows[0]["fulfilled"]) / pledged) if pledged else 0.0


async def fund_breakdown_since(db: Prisma, start: datetime) -> list[dict]:
    return await db.query_raw(
        """
        select f.name as fund_name, coalesce(sum(d.amount), 0) as amount
        from funds f left join donations d on d.fund_id = f.id and not d.is_voided and d.created_at >= $1::timestamptz
        group by f.id, f.name
        order by amount desc
        """,
        start,
    )


async def donations_grouped_by_fund(db: Prisma, start: datetime, end: datetime) -> list[dict]:
    return await db.query_raw(
        """
        select f.id as fund_id, f.name as fund_name, coalesce(sum(d.amount),0) as amount
        from funds f left join donations d on d.fund_id = f.id and not d.is_voided
          and d.created_at >= $1::timestamptz and d.created_at < $2::timestamptz
        group by f.id, f.name
        having coalesce(sum(d.amount),0) > 0
        order by amount desc
        """,
        start,
        end,
    )


async def donations_trend(db: Prisma, start: datetime, end: datetime, bucket: str) -> list[dict]:
    # `bucket` is already allow-listed by the service; it is bound as a
    # parameter anyway so no caller value is ever spliced into SQL.
    return await db.query_raw(
        """
        select to_char(date_trunc($3, created_at), 'YYYY-MM-DD') as label,
               coalesce(sum(amount), 0) as amount
        from donations
        where not is_voided and created_at >= $1::timestamptz and created_at < $2::timestamptz
        group by 1 order by 1
        """,
        start,
        end,
        bucket,
    )
