"""Seed data for local development and manual testing.

Connects as the migration/owner role (DATABASE_URL in .env), which is
exempt from RLS as the table owner — the same reason migrations can write
freely across tenants. Never use this connection pattern in request-serving
code; it exists here only because seeding deliberately needs to write
several tenants' worth of data in one pass.

Run with: python scripts/seed.py
Safe to re-run — it clears its own previously-seeded rows first (matched by
a fixed set of slugs/emails below), not a blanket TRUNCATE, so it won't
touch data created through the API in the meantime.
"""

import asyncio
import random
import uuid
from datetime import UTC, date, datetime, timedelta

from prisma import Json, Prisma

from app.core.config import get_settings
from app.core.prisma_utils import coerce_dates
from app.core.security import hash_password
from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_STAFF_ID

ORG_SLUGS = ["st-judes-parish", "grace-community-church", "lagos-diocese"]
SEED_EMAIL_DOMAIN = "@seed.ecclesiaflow.dev"


async def clear_previous(db: Prisma) -> None:
    orgs = await db.organization.find_many(where={"slug": {"in": ORG_SLUGS}})
    for org in orgs:
        await db.organization.delete(where={"id": org.id})
    await db.user.delete_many(where={"email": {"endsWith": SEED_EMAIL_DOMAIN}})


async def seed_org(
    db: Prisma, *, legal_name: str, display_name: str, slug: str, tier: str, status: str, country: str, currency: str
) -> dict:
    org = await db.organization.create(
        data={
            "legal_name": legal_name,
            "display_name": display_name,
            "slug": slug,
            "country": country,
            "currency": currency,
            "timezone": "UTC",
            "status": status,
            "tier": tier,
            "trial_ends_at": datetime.now(UTC) + timedelta(days=30) if status == "trial" else None,
        }
    )

    root = await db.hierarchyunit.create(data={"tenant_id": org.id, "name": display_name, "type": "Church"})
    branch = await db.hierarchyunit.create(
        data={"tenant_id": org.id, "name": f"{display_name} — Youth Branch", "type": "Branch", "parent_id": root.id}
    )

    # ── Staff ──────────────────────────────────────────────────────────────
    leader_user = await db.user.create(
        data={
            "email": f"leader.{slug}{SEED_EMAIL_DOMAIN}",
            "password_hash": hash_password("SeedData2026Pass"),
            "first_name": "Grace",
            "last_name": "Okafor",
        }
    )
    await db.tenantmembership.create(
        data={
            "user_id": leader_user.id,
            "tenant_id": org.id,
            "role_id": SYSTEM_ROLE_STAFF_ID,
            "is_primary": True,
            "is_leader": True,
            "status": "active",
            "accepted_at": datetime.now(UTC),
        }
    )
    board_user = await db.user.create(
        data={
            "email": f"board.{slug}{SEED_EMAIL_DOMAIN}",
            "password_hash": hash_password("SeedData2026Pass"),
            "first_name": "Daniel",
            "last_name": "Mensah",
        }
    )
    await db.tenantmembership.create(
        data={
            "user_id": board_user.id,
            "tenant_id": org.id,
            "role_id": SYSTEM_ROLE_BOARD_ID,
            "is_primary": True,
            "status": "active",
            "accepted_at": datetime.now(UTC),
        }
    )

    # ── Members + a household ────────────────────────────────────────────────
    household = await db.household.create(
        data={
            "tenant_id": org.id,
            "name": "The Smith Household",
            "address_line1": "12 Grace Way",
            "city": "Springfield",
            "country": country,
        }
    )
    first_names = [
        "John",
        "Mary",
        "James",
        "Patricia",
        "Robert",
        "Linda",
        "Michael",
        "Susan",
        "William",
        "Jessica",
        "David",
        "Karen",
        "Joseph",
        "Sarah",
        "Thomas",
    ]
    last_names = ["Smith", "Johnson", "Brown", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris", "Martin"]
    statuses = ["active"] * 10 + ["visitor"] * 3 + ["inactive"] * 2
    member_ids = []
    for i in range(15):
        fn, ln = first_names[i], random.choice(last_names)
        member = await db.member.create(
            data=coerce_dates(
                {
                    "tenant_id": org.id,
                    "first_name": fn,
                    "last_name": ln,
                    "status": statuses[i],
                    "unit_id": branch.id if i % 3 == 0 else root.id,
                    "envelope_number": f"{100 + i}",
                    "joined_at": date.today() - timedelta(days=random.randint(10, 1000)),
                    "last_seen_at": date.today() - timedelta(days=random.randint(0, 60)),
                    "household_id": household.id if i < 2 else None,
                    "phone": f"+1555000{i:04d}",
                }
            )
        )
        member_ids.append(member.id)
    await db.household.update(where={"id": household.id}, data={"head_member_id": member_ids[0]})

    # Link the leader's own portal login to a member record (so "my giving"
    # has something real to show for the demo login).
    linked_member = await db.member.create(
        data=coerce_dates(
            {
                "tenant_id": org.id,
                "user_id": leader_user.id,
                "first_name": leader_user.first_name,
                "last_name": leader_user.last_name,
                "status": "active",
                "unit_id": root.id,
                "joined_at": date.today() - timedelta(days=400),
            }
        )
    )
    member_ids.append(linked_member.id)

    await db.sacramentalrecord.create(
        data=coerce_dates(
            {
                "tenant_id": org.id,
                "member_id": member_ids[0],
                "type": "Baptism",
                "date": date(2015, 6, 12),
                "officiant_name": "Rev. Grace Okafor",
            }
        )
    )
    await db.pastoralnote.create(
        data={
            "tenant_id": org.id,
            "member_id": member_ids[0],
            "author_user_id": leader_user.id,
            "content": "Visited at home; family doing well after relocation.",
            "is_private": True,
        }
    )

    # ── Events + attendance ───────────────────────────────────────────────
    past_event = await db.event.create(
        data={
            "tenant_id": org.id,
            "title": "Sunday Service",
            "type": "service",
            "start_date_time": datetime.now(UTC) - timedelta(days=7),
            "status": "completed",
            "attendance_mode": "individual",
            "is_public": True,
            "unit_id": root.id,
            "created_by_user_id": leader_user.id,
            "location": display_name,
        }
    )
    for member_id in member_ids[:8]:
        await db.attendancerecord.create(
            data={
                "tenant_id": org.id,
                "event_id": past_event.id,
                "mode": "individual",
                "member_id": member_id,
                "marked_by_user_id": leader_user.id,
            }
        )
    await db.event.create(
        data={
            "tenant_id": org.id,
            "title": "Christmas Concert",
            "type": "event",
            "start_date_time": datetime.now(UTC) + timedelta(days=14),
            "status": "published",
            "attendance_mode": "headcount",
            "is_public": True,
            "unit_id": root.id,
            "created_by_user_id": leader_user.id,
            "location": display_name,
        }
    )

    # ── Finance ───────────────────────────────────────────────────────────
    general_fund = await db.fund.create(data={"tenant_id": org.id, "name": "General Fund", "is_default": True})
    missions_fund = await db.fund.create(data={"tenant_id": org.id, "name": "Missions"})
    batch = await db.donationbatch.create(
        data=coerce_dates(
            {
                "tenant_id": org.id,
                "name": "Sunday Service Offering",
                "date": date.today() - timedelta(days=7),
                "status": "open",
                "created_by_user_id": leader_user.id,
            }
        )
    )
    batch_total = 0.0
    for member_id in member_ids[:6]:
        amount = float(random.choice([25, 50, 75, 100, 150]))
        batch_total += amount
        await db.donation.create(
            data={
                "tenant_id": org.id,
                "batch_id": batch.id,
                "member_id": member_id,
                "fund_id": general_fund.id,
                "amount": amount,
                "payment_method": random.choice(["cash", "check", "card"]),
                "created_by_user_id": leader_user.id,
            }
        )
    await db.donationbatch.update(where={"id": batch.id}, data={"total_amount": batch_total, "donation_count": 6})
    await db.fund.update(where={"id": general_fund.id}, data={"total_received": batch_total})
    await db.pledge.create(
        data=coerce_dates(
            {
                "tenant_id": org.id,
                "member_id": member_ids[0],
                "fund_id": missions_fund.id,
                "pledge_amount": 1200,
                "amount_fulfilled": 400,
                "start_date": date.today() - timedelta(days=90),
                "end_date": date.today() + timedelta(days=275),
                "frequency": "monthly",
                "status": "active",
            }
        )
    )

    # ── Announcements ─────────────────────────────────────────────────────
    await db.announcement.create(
        data={
            "tenant_id": org.id,
            "title": "Welcome to our new portal!",
            "body": "<p>We're excited to share our new member portal.</p>",
            "is_pinned": True,
            "status": "sent",
            "channels": ["email", "in_app"],
            "author_user_id": leader_user.id,
            "sent_at": datetime.now(UTC) - timedelta(days=3),
            "sent_count": len(member_ids),
            "delivered_count": len(member_ids) - 1,
            "opened_count": 9,
        }
    )

    # ── Certificates ──────────────────────────────────────────────────────
    template = await db.certificatetemplate.create(
        data={
            "tenant_id": org.id,
            "name": "Baptism Certificate",
            "category": "sacramental",
            "status": "active",
            "tokens": Json(
                [
                    {
                        "key": "name",
                        "label": "Recipient Name",
                        "x": 50,
                        "y": 40,
                        "fontSize": 24,
                        "fontFamily": "serif",
                        "color": "#1a1a1a",
                    }
                ]
            ),
        }
    )
    await db.certificate.create(
        data={
            "tenant_id": org.id,
            "template_id": template.id,
            "member_id": member_ids[0],
            "issued_by_user_id": leader_user.id,
            "serial_number": "SAC-2026-0001",
            "qr_hash": uuid.uuid4().hex,
            "custom_values": Json({"name": "John Smith"}),
        }
    )

    # ── Custom role example ───────────────────────────────────────────────
    media_role = await db.role.create(
        data={"tenant_id": org.id, "name": "Media Lead", "color": "#0284C7", "is_system": False}
    )
    for perm in ("members:read", "events:read", "announcements:read", "announcements:create"):
        await db.rolepermission.create(data={"role_id": media_role.id, "permission": perm})

    return {"org": org, "leader_email": leader_user.email, "root_unit_id": root.id}


async def main() -> None:
    db = Prisma(datasource={"url": get_settings().database_url})
    await db.connect()
    await clear_previous(db)

    summary = []
    summary.append(
        await seed_org(
            db,
            legal_name="St. Jude's Parish Inc.",
            display_name="St. Jude's Parish",
            slug="st-judes-parish",
            tier="seed",
            status="trial",
            country="US",
            currency="USD",
        )
    )
    summary.append(
        await seed_org(
            db,
            legal_name="Grace Community Church Ltd.",
            display_name="Grace Community Church",
            slug="grace-community-church",
            tier="parish",
            status="active",
            country="US",
            currency="USD",
        )
    )
    summary.append(
        await seed_org(
            db,
            legal_name="Lagos Diocese",
            display_name="Lagos Diocese",
            slug="lagos-diocese",
            tier="diocese",
            status="active",
            country="NG",
            currency="NGN",
        )
    )

    # Platform admin account
    await db.user.delete_many(where={"email": f"platform-admin{SEED_EMAIL_DOMAIN}"})
    await db.user.create(
        data={
            "email": f"platform-admin{SEED_EMAIL_DOMAIN}",
            "password_hash": hash_password("SeedData2026Pass"),
            "first_name": "Priya",
            "last_name": "Patel",
            "is_platform_admin": True,
            "platform_admin_level": "full",
        }
    )

    await db.disconnect()

    print("Seeded:")
    for s in summary:
        print(f"  {s['org'].display_name}  ({s['org'].slug})  leader login: {s['leader_email']} / SeedData2026Pass")
    print(f"  Platform admin login: platform-admin{SEED_EMAIL_DOMAIN} / SeedData2026Pass")


if __name__ == "__main__":
    asyncio.run(main())
