"""Paying by mobile money: submit -> platform approves -> time is granted.

Also the access rules that make a lapsed church greyed out, derived from dates.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.mailer import get_mailer
from app.core.security import create_step_up_token, decode_token
from app.modules.billing.reminders import send_due_reminders
from app.modules.org.catalogue import PLAN_BY_TIER
from tests.conftest import auth, make_platform_admin

BASE = "/api/v1"


@pytest.fixture(autouse=True)
def _real_lifecycle(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "dev_auto_activate_orgs", False)
    monkeypatch.setattr(settings, "payment_method", "manual")
    monkeypatch.setattr(settings, "momo_number", "0780000000")
    monkeypatch.setattr(settings, "momo_account_name", "EcclesiaFlow Ltd")


def png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + os.urandom(64)


async def church(client, church_payload, label="momo"):
    resp = await client.post(f"{BASE}/auth/register", json={**church_payload(label), "contact_phone": "+250788000001"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def submit(client, token, *, tier="parish", months=3, sent=None, txn=None, proof=None):
    expected = PLAN_BY_TIER[tier].price_for_months(months)
    data = {
        "tier": tier,
        "months": str(months),
        "sender_name": "Jean Mukiza",
        "sent_amount_cents": str(expected if sent is None else sent),
    }
    if txn:
        data["momo_transaction_id"] = txn
    return await client.post(
        f"{BASE}/billing/manual/payments",
        data=data,
        files={"proof": ("proof.png", proof or png(), "image/png")},
        headers=auth(token),
    )


def step_up(admin_session) -> dict:
    sub = decode_token(admin_session["access_token"])["sub"]
    return {"X-Step-Up-Token": create_step_up_token(user_id=uuid.UUID(sub), tenant_id=None)}


async def expire(tenant_id: str, ended_days_ago: float):
    async with platform_session() as db:
        await db.execute_raw(
            "update billing_subscriptions set current_period_end = $2::timestamptz where tenant_id = $1::uuid",
            tenant_id,
            (datetime.now(UTC) - timedelta(days=ended_days_ago)).isoformat(),
        )


def test_price_for_months():
    seed = PLAN_BY_TIER["seed"]
    assert seed.price_for_months(1) == 1900
    assert seed.price_for_months(3) == 5700
    assert seed.price_for_months(11) == 19000  # eleven months never cost more than a year
    assert seed.price_for_months(12) == 19000
    assert seed.price_for_months(14) == 19000 + 3800
    assert seed.price_for_months(24) == 38000


async def test_new_church_has_free_basics_only_then_pays_and_is_approved(client, church_payload):
    session = await church(client, church_payload)
    h = auth(session["access_token"])

    # Pending: free basics work, paid modules are locked.
    assert (await client.get(f"{BASE}/members", headers=h)).status_code == 200
    locked = await client.get(f"{BASE}/finance/funds", headers=h)
    assert locked.status_code == 402 and locked.json()["error"]["code"] == "module_locked"
    overview = (await client.get(f"{BASE}/billing/overview", headers=h)).json()
    assert overview["provider"] == "manual" and overview["access"]["state"] == "basics"
    assert (await client.get(f"{BASE}/org/modules", headers=h)).json()["tier"] == "free"

    info = (await client.get(f"{BASE}/billing/manual/instructions?tier=parish&months=3", headers=h)).json()
    assert info["number"] == "0780000000" and info["amount_cents"] == 4900 * 3

    sent = await submit(client, session["access_token"], tier="parish", months=3)
    assert sent.status_code == 201, sent.text
    payment = sent.json()
    assert payment["status"] == "pending" and payment["reference_code"].startswith("EF-")

    # Only one waits at a time.
    again = await submit(client, session["access_token"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "payment_pending"

    # The church cannot decide its own payment.
    own = await client.post(f"{BASE}/platform/payments/{payment['id']}/approve", json={}, headers=h)
    assert own.status_code == 403

    admin = await make_platform_admin(client)
    ah = auth(admin["access_token"])
    queue = (await client.get(f"{BASE}/platform/payments?status=pending", headers=ah)).json()
    mine = next(p for p in queue if p["id"] == payment["id"])
    assert mine["amount_matches"] is True and mine["church_name"]

    proof = await client.get(f"{BASE}/platform/payments/{payment['id']}/proof", headers=ah)
    assert proof.status_code == 200 and proof.content.startswith(b"\x89PNG")
    assert (await client.get(f"{BASE}/platform/payments/{payment['id']}/proof", headers=h)).status_code == 403

    # Approving needs a fresh two-step code.
    no_step_up = await client.post(f"{BASE}/platform/payments/{payment['id']}/approve", json={}, headers=ah)
    assert no_step_up.status_code == 403 and no_step_up.json()["error"]["code"] == "step_up_required"

    approved = await client.post(
        f"{BASE}/platform/payments/{payment['id']}/approve", json={}, headers={**ah, **step_up(admin)}
    )
    assert approved.status_code == 200, approved.text
    first_end = datetime.fromisoformat(approved.json()["granted_until"])

    # A payment is decided once.
    twice = await client.post(
        f"{BASE}/platform/payments/{payment['id']}/approve", json={}, headers={**ah, **step_up(admin)}
    )
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "already_decided"

    # Access is now full, on the paid plan, and everything unlocks.
    assert (await client.get(f"{BASE}/finance/funds", headers=h)).status_code == 200
    overview = (await client.get(f"{BASE}/billing/overview", headers=h)).json()
    assert overview["access"]["state"] == "full" and overview["tier"] == "parish"
    assert overview["subscription"]["provider"] == "manual"
    assert overview["payments"][0]["amount_cents"] == 4900 * 3
    assert (await client.get(f"{BASE}/org/modules", headers=h)).json()["tier"] == "parish"

    # Renewing early adds to the time left.
    renewal = await submit(client, session["access_token"], tier="parish", months=1)
    assert renewal.status_code == 201, renewal.text
    renewed = await client.post(
        f"{BASE}/platform/payments/{renewal.json()['id']}/approve", json={}, headers={**ah, **step_up(admin)}
    )
    second_end = datetime.fromisoformat(renewed.json()["granted_until"])
    assert 28 <= (second_end - first_end).days <= 31


async def test_underpaid_needs_explicit_confirmation_and_a_reject_needs_a_reason(client, church_payload):
    session = await church(client, church_payload, "short")
    payment = (await submit(client, session["access_token"], tier="seed", months=2, sent=1000)).json()
    admin = await make_platform_admin(client)
    headers = {**auth(admin["access_token"]), **step_up(admin)}

    refused = await client.post(f"{BASE}/platform/payments/{payment['id']}/approve", json={}, headers=headers)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "amount_differs"
    no_reason = await client.post(f"{BASE}/platform/payments/{payment['id']}/reject", json={}, headers=headers)
    assert no_reason.status_code == 422

    rejected = await client.post(
        f"{BASE}/platform/payments/{payment['id']}/reject", json={"note": "Amount does not match"}, headers=headers
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    h = auth(session["access_token"])
    last = (await client.get(f"{BASE}/billing/overview", headers=h)).json()["last_payment"]
    assert last["decision_note"] == "Amount does not match"
    # Nothing was granted, and a corrected payment can be sent again.
    assert (await client.get(f"{BASE}/finance/funds", headers=h)).status_code == 402
    assert (await submit(client, session["access_token"], tier="seed", months=2)).status_code == 201


async def test_the_same_screenshot_or_transaction_cannot_pay_twice(client, church_payload):
    a = await church(client, church_payload, "dup-a")
    b = await church(client, church_payload, "dup-b")
    shared = png()
    code = uuid.uuid4().hex[:8].upper()  # the database outlives the test run
    assert (await submit(client, a["access_token"], proof=shared, txn=f"MP{code}")).status_code == 201
    same_file = await submit(client, b["access_token"], proof=shared)
    assert same_file.status_code == 409 and same_file.json()["error"]["code"] == "proof_reused"
    same_txn = await submit(client, b["access_token"], txn=f"mp{code.lower()}")
    assert same_txn.status_code == 409 and same_txn.json()["error"]["code"] == "transaction_reused"
    assert (await submit(client, b["access_token"], txn=f"MP{code}X")).status_code == 201


async def test_proof_must_be_an_image_or_pdf(client, church_payload):
    session = await church(client, church_payload, "bad")
    resp = await submit(client, session["access_token"], proof=b"<script>alert(1)</script>")
    assert resp.status_code == 415


async def test_access_follows_the_dates_with_a_three_day_grace_then_view_only(client, church_payload):
    session = await church(client, church_payload, "lapse")
    h = auth(session["access_token"])
    tenant_id = decode_token(session["access_token"])["tenant_id"]
    admin = await make_platform_admin(client)
    ah = {**auth(admin["access_token"]), **step_up(admin)}
    payment = (await submit(client, session["access_token"], tier="parish", months=1)).json()
    await client.post(f"{BASE}/platform/payments/{payment['id']}/approve", json={}, headers=ah)
    assert (await client.get(f"{BASE}/finance/funds", headers=h)).status_code == 200

    # Ended yesterday: free basics stay, paid modules lock.
    await expire(tenant_id, 1)
    state = (await client.get(f"{BASE}/billing/overview", headers=h)).json()["access"]
    assert state["state"] == "basics" and state["tier"] == "free"
    assert (await client.get(f"{BASE}/finance/funds", headers=h)).status_code == 402
    assert (await client.get(f"{BASE}/members", headers=h)).status_code == 200

    # Ended four days ago: view-only. Reading works, changing does not.
    await expire(tenant_id, 4)
    assert (await client.get(f"{BASE}/billing/overview", headers=h)).json()["access"]["state"] == "view_only"
    assert (await client.get(f"{BASE}/members", headers=h)).status_code == 200
    blocked = await client.post(f"{BASE}/members", json={"first_name": "A", "last_name": "B"}, headers=h)
    assert blocked.status_code == 402 and blocked.json()["error"]["code"] == "subscription_required"
    # Billing stays reachable so they can pay.
    assert (await submit(client, session["access_token"], tier="parish", months=1)).status_code == 201


async def test_renewal_reminder_goes_out_once_in_the_last_week(client, church_payload):
    session = await church(client, church_payload, "remind")
    tenant_id = decode_token(session["access_token"])["tenant_id"]
    admin = await make_platform_admin(client)
    ah = {**auth(admin["access_token"]), **step_up(admin)}
    payment = (await submit(client, session["access_token"], tier="seed", months=1)).json()
    await client.post(f"{BASE}/platform/payments/{payment['id']}/approve", json={}, headers=ah)

    async with platform_session() as db:
        await db.execute_raw(
            "update billing_subscriptions set current_period_end = now() + interval '5 days'"
            " where tenant_id = $1::uuid",
            tenant_id,
        )
    mailer = get_mailer()
    before = len(mailer.outbox)
    assert await send_due_reminders() >= 1
    assert any("plan ends" in m.subject for m in mailer.outbox[before:])
    assert await send_due_reminders() == 0  # already told about this end date
    overview = (await client.get(f"{BASE}/billing/overview", headers=auth(session["access_token"]))).json()
    assert overview["access"]["renewal_due"] is True
