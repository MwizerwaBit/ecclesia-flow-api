"""Notifications raised by real workflows (TODO.md, Notifications module N5).

Each test drives the workflow through its API and then reads the recipients'
own bells through `GET /notifications`, so it checks what a person would see:
who is told, who is not (never the person who acted), the wording stays a
pointer, and a repeated step doesn't tell anyone twice.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.core.database import platform_session, tenant_session
from app.core.security import decode_token
from app.modules.billing.reminders import send_due_reminders
from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_STAFF_ID
from app.modules.team import service as team_service
from tests.conftest import accept_invite, auth, make_platform_admin, unique_email
from tests.test_manual_payments import church as paying_church
from tests.test_manual_payments import step_up as admin_step_up
from tests.test_manual_payments import submit as submit_payment
from tests.test_notifications_service import _rows

BASE = "/api/v1"


async def _register(client, church_payload, label):
    resp = await client.post(f"{BASE}/auth/register", json=church_payload(label))
    assert resp.status_code == 200, resp.text
    session = resp.json()
    return session, auth(session["access_token"])


async def _teammate(client, lead, role_id=SYSTEM_ROLE_STAFF_ID):
    """(membership id, session, headers) of a new team member. The board role can't be
    invited (leader-only), so such a member is invited as staff, given the role in the
    database, and signs in again."""
    email = unique_email("team")
    invite = await client.post(
        f"{BASE}/team/invite", json={"email": email, "role_id": SYSTEM_ROLE_STAFF_ID}, headers=lead
    )
    assert invite.status_code == 200, invite.text
    membership_id = invite.json()["id"]
    session = await accept_invite(client, invite.json()["invite_url"], first="Team", last="Mate")
    if role_id != SYSTEM_ROLE_STAFF_ID:
        async with platform_session() as db:
            await db.execute_raw(
                "update tenant_memberships set role_id = $2::uuid where id = $1::uuid", membership_id, role_id
            )
        login = await client.post(f"{BASE}/auth/login", json={"email": email, "password": "Accepted2026Pass"})
        assert login.status_code == 200, login.text
        session = login.json()
    return membership_id, session, auth(session["access_token"])


async def _inbox(client, headers) -> list[dict]:
    resp = await client.get(f"{BASE}/notifications", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _titles(items) -> list[str]:
    return sorted(n["title"] for n in items)


# ───────────────────────────── Event workflow ─────────────────────────────


async def _event(client, headers, title="Harvest Sunday"):
    start = (datetime.now(UTC) + timedelta(days=5)).replace(microsecond=0)
    resp = await client.post(
        f"{BASE}/events",
        json={"title": title, "type": "service", "start_date_time": start.isoformat()},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _submit(client, headers, event_id, reviewers):
    resp = await client.post(f"{BASE}/events/{event_id}/submit", json={"reviewer_user_ids": reviewers}, headers=headers)
    assert resp.status_code == 200, resp.text


async def _review(client, headers, event_id, decision, comment=None):
    body = {"decision": decision, **({"comment": comment} if comment else {})}
    resp = await client.post(f"{BASE}/events/{event_id}/review", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_event_workflow_tells_the_right_people_and_never_the_actor(client, church_payload):
    leader, lead = await _register(client, church_payload, "n5-event")
    _, staff, staff_h = await _teammate(client, lead)
    event = await _event(client, staff_h)

    # Sent for review: the reviewer is asked (an approval request); the author is not told about their own act.
    await _submit(client, staff_h, event["id"], [leader["user"]["id"]])
    (ask,) = await _inbox(client, lead)
    assert (ask["title"], ask["type"]) == ("Gathering waiting for your review", "approval")
    assert ask["body"] == 'Team Mate sent "Harvest Sunday" for your review.'
    assert ask["resource_type"] == "event" and ask["resource_id"] == event["id"]
    assert ask["action_url"] == f"/staff/events/{event['id']}" and ask["is_read"] is False
    assert await _inbox(client, staff_h) == []

    # Changes requested: the author hears; the reviewer's comment is not copied into the notice.
    await _review(client, lead, event["id"], "changes_requested", comment="Please add the secret budget figure 4242")
    (changes,) = await _inbox(client, staff_h)
    assert (changes["title"], changes["type"]) == ("Changes requested on your gathering", "event")
    assert "4242" not in changes["body"] and "budget" not in changes["body"]
    assert "Open it to see" in changes["body"]
    assert len(await _inbox(client, lead)) == 1  # the reviewer is told nothing about their own decision

    # Sent back for review after changes: a new request, not swallowed as a duplicate of the first.
    await _submit(client, staff_h, event["id"], [leader["user"]["id"]])
    assert _titles(await _inbox(client, lead)) == ["Gathering waiting for your review"] * 2

    # Approved by the only reviewer: published, and the author is told.
    await _review(client, lead, event["id"], "approved")
    assert "Gathering approved and published" in _titles(await _inbox(client, staff_h))
    assert len(await _inbox(client, lead)) == 2  # still just the two requests

    # Canceled by the reviewer: the author is told; the reviewer is not told of their own act.
    assert (await client.post(f"{BASE}/events/{event['id']}/cancel", headers=lead)).status_code == 200
    assert _titles(await _inbox(client, staff_h)) == [
        "Changes requested on your gathering",
        "Gathering approved and published",
        "Gathering canceled",
    ]
    assert len(await _inbox(client, lead)) == 2


async def test_with_two_reviewers_only_the_last_approval_publishes_the_news(client, church_payload):
    leader, lead = await _register(client, church_payload, "n5-two")
    _, staff, staff_h = await _teammate(client, lead)
    board_membership, board, board_h = await _teammate(client, lead, role_id=SYSTEM_ROLE_BOARD_ID)
    event = await _event(client, staff_h)
    await _submit(client, staff_h, event["id"], [leader["user"]["id"], board["user"]["id"]])
    assert _titles(await _inbox(client, board_h)) == ["Gathering waiting for your review"]

    await _review(client, board_h, event["id"], "approved")  # one of two: nothing to announce yet
    assert await _inbox(client, staff_h) == []

    await _review(client, lead, event["id"], "approved")  # the last one publishes
    assert _titles(await _inbox(client, staff_h)) == ["Gathering approved and published"]
    # The other reviewer is told too; the one who just approved is not.
    assert _titles(await _inbox(client, board_h)) == [
        "Gathering approved and published",
        "Gathering waiting for your review",
    ]
    assert _titles(await _inbox(client, lead)) == ["Gathering waiting for your review"]


async def test_publishing_directly_tells_the_author(client, church_payload):
    _, lead = await _register(client, church_payload, "n5-direct")
    _, staff, staff_h = await _teammate(client, lead)
    event = await _event(client, staff_h, title="Prayer night")
    assert (await client.post(f"{BASE}/events/{event['id']}/publish", headers=lead)).status_code == 200
    (note,) = await _inbox(client, staff_h)
    assert note["title"] == "Gathering published" and note["body"].endswith('published "Prayer night".')


async def test_a_notification_does_not_outlive_a_failed_workflow_step(client, church_payload):
    """Submitting twice is refused (409), and the refused attempt tells nobody."""
    leader, lead = await _register(client, church_payload, "n5-refused")
    _, staff, staff_h = await _teammate(client, lead)
    event = await _event(client, staff_h)
    await _submit(client, staff_h, event["id"], [leader["user"]["id"]])
    again = await client.post(
        f"{BASE}/events/{event['id']}/submit", json={"reviewer_user_ids": [leader["user"]["id"]]}, headers=staff_h
    )
    assert again.status_code == 409
    assert len(await _inbox(client, lead)) == 1


# ───────────────────────────── Leadership transfer ─────────────────────────────


async def _transfer_setup(client, church_payload, label):
    leader, lead = await _register(client, church_payload, label)
    nominee_membership, nominee, nominee_h = await _teammate(client, lead)
    approvers = [await _teammate(client, lead) for _ in range(2)]
    return leader, lead, (nominee_membership, nominee, nominee_h), approvers


# The transfer is driven through the team service on the caller's own church session: the HTTP routes for it
# answer 500 today (their response model doesn't match what the repository returns), which has nothing to do
# with notifications and is recorded in TODO.md rather than fixed here.


async def _as(session, call, *args):
    tenant = session["active_membership"]["tenant_id"]
    async with tenant_session(tenant, session["user"]["id"]) as db:
        return await call(db, tenant, *args)


async def test_leadership_transfer_tells_approvers_then_the_leader_then_the_new_leader(client, church_payload):
    leader, lead, (nominee_membership, nominee, nominee_h), approvers = await _transfer_setup(
        client, church_payload, "n5-transfer"
    )
    transfer = await _as(leader, team_service.request_leadership_transfer, leader["user"]["id"], nominee_membership)

    # The people who can approve are asked; the leader who asked and the nominee are not.
    for _, _, headers in approvers:
        (ask,) = await _inbox(client, headers)
        assert (ask["title"], ask["type"]) == ("Leadership transfer needs your approval", "approval")
        assert "2 approvals are needed" in ask["body"] and ask["resource_id"] == transfer["id"]
        assert ask["resource_type"] == "leadership_transfer" and ask["action_url"] == "/staff/team"
    assert await _inbox(client, lead) == [] and await _inbox(client, nominee_h) == []

    # One approval: the leader hears about progress. Approving again changes nothing.
    first_membership, first, first_h = approvers[0]
    await _as(first, team_service.approve_leadership_transfer, transfer["id"], first_membership)
    await _as(first, team_service.approve_leadership_transfer, transfer["id"], first_membership)
    (progress,) = await _inbox(client, lead)
    assert progress["title"] == "A team member approved the leadership transfer" and "(1 of 2)" in progress["body"]

    # The second approval completes it: the outgoing leader and the new leader are told. Both
    # have new roles now, so their old tokens are stale: read their bells straight from the table.
    second_membership, second, second_h = approvers[1]
    await _as(second, team_service.approve_leadership_transfer, transfer["id"], second_membership)
    tenant = leader["active_membership"]["tenant_id"]
    assert sorted(r["title"] for r in await _rows(tenant, leader["user"]["id"])) == [
        "A team member approved the leadership transfer",
        "Leadership transfer completed",
    ]
    (new_leader,) = await _rows(tenant, nominee["user"]["id"])
    assert new_leader["title"] == "You are now the church leader" and new_leader["type"] == "approval"
    # The approvers got only the original request; the final approver is not told of their own act.
    assert _titles(await _inbox(client, first_h)) == ["Leadership transfer needs your approval"]
    assert _titles(await _inbox(client, second_h)) == ["Leadership transfer needs your approval"]


async def test_withdrawing_a_transfer_tells_the_approvers(client, church_payload):
    leader, lead, (nominee_membership, nominee, nominee_h), approvers = await _transfer_setup(
        client, church_payload, "n5-withdraw"
    )
    transfer = await _as(leader, team_service.request_leadership_transfer, leader["user"]["id"], nominee_membership)
    await _as(leader, team_service.cancel_leadership_transfer, transfer["id"], leader["user"]["id"])
    for _, _, headers in approvers:
        assert _titles(await _inbox(client, headers)) == [
            "Leadership transfer needs your approval",
            "Leadership transfer withdrawn",
        ]
    assert await _inbox(client, lead) == []


# ───────────────────────────── Billing ─────────────────────────────


@pytest.fixture
def real_lifecycle(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "dev_auto_activate_orgs", False)
    monkeypatch.setattr(settings, "payment_method", "manual")
    monkeypatch.setattr(settings, "momo_number", "0780000000")
    monkeypatch.setattr(settings, "momo_account_name", "EcclesiaFlow Ltd")


async def _delivery_count(user_id: str) -> int:
    async with platform_session() as db:
        rows = await db.query_raw(
            "select count(*)::int as n from notification_deliveries where user_id = $1::uuid", user_id
        )
    return rows[0]["n"]


async def test_payment_decisions_reach_whoever_manages_billing_in_app_only(client, church_payload, real_lifecycle):
    session = await paying_church(client, church_payload, "n5-pay")
    h = auth(session["access_token"])
    # A team member with the billing permission (board) hears too; a plain staff member does not.
    _, board, board_h = await _teammate(client, h, role_id=SYSTEM_ROLE_BOARD_ID)
    _, staff, staff_h = await _teammate(client, h)
    admin = await make_platform_admin(client)
    ah = {**auth(admin["access_token"]), **admin_step_up(admin)}

    rejected_payment = (await submit_payment(client, session["access_token"], tier="seed", months=1)).json()
    rejected = await client.post(
        f"{BASE}/platform/payments/{rejected_payment['id']}/reject",
        json={"note": "Screenshot unreadable, secret-reason-77"},
        headers=ah,
    )
    assert rejected.status_code == 200, rejected.text
    for headers in (h, board_h):
        (note,) = await _inbox(client, headers)
        assert (note["title"], note["type"]) == ("Payment could not be approved", "billing")
        assert "secret-reason-77" not in note["body"] and note["action_url"] == "/staff/settings/billing"
    assert await _inbox(client, staff_h) == []

    # A corrected payment is approved: the same people hear, once.
    payment = (await submit_payment(client, session["access_token"], tier="seed", months=2)).json()
    approved = await client.post(f"{BASE}/platform/payments/{payment['id']}/approve", json={}, headers=ah)
    assert approved.status_code == 200, approved.text
    await client.post(
        f"{BASE}/platform/payments/{payment['id']}/approve", json={}, headers=ah
    )  # refused: already decided
    for headers in (h, board_h):
        assert _titles(await _inbox(client, headers)) == ["Payment approved", "Payment could not be approved"]
    approved_note = next(n for n in await _inbox(client, h) if n["title"] == "Payment approved")
    assert approved_note["body"].startswith("2 months of the Seed plan were added.")
    assert await _inbox(client, staff_h) == []

    # The email for a decision already goes to the church's contact address: no second one from here.
    assert await _delivery_count(session["user"]["id"]) == 0


async def test_the_renewal_reminder_also_lands_in_the_bell_once(client, church_payload, real_lifecycle):
    session = await paying_church(client, church_payload, "n5-renew")
    h = auth(session["access_token"])
    tenant_id = decode_token(session["access_token"])["tenant_id"]
    admin = await make_platform_admin(client)
    ah = {**auth(admin["access_token"]), **admin_step_up(admin)}
    payment = (await submit_payment(client, session["access_token"], tier="seed", months=1)).json()
    await client.post(f"{BASE}/platform/payments/{payment['id']}/approve", json={}, headers=ah)
    before = len(await _inbox(client, h))

    async with platform_session() as db:
        await db.execute_raw(
            "update billing_subscriptions set current_period_end = now() + interval '5 days'"
            " where tenant_id = $1::uuid",
            tenant_id,
        )
    await send_due_reminders()
    await send_due_reminders()  # already told about this end date
    inbox = await _inbox(client, h)
    reminders = [n for n in inbox if n["title"].startswith("Your plan ends on")]
    assert len(reminders) == 1 and len(inbox) == before + 1
    assert reminders[0]["type"] == "billing" and reminders[0]["action_url"] == "/staff/settings/billing"
    assert await _delivery_count(session["user"]["id"]) == 0  # the email goes to the contact address
