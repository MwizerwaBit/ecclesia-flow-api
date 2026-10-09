"""Church registration → onboarding → payment / platform activation.

These tests switch off the test-only auto-activation, so a new church starts
*pending* exactly as it does in production.
"""

import json
import uuid

import pytest

from app.core.config import get_settings
from app.modules.billing import signatures
from tests.conftest import accept_invite, auth, make_platform_admin

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _real_lifecycle(monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_auto_activate_orgs", False)


async def _register(client, church_payload, label, **extra):
    payload = {**church_payload(label), "contact_phone": "+250788000000", "city": "Kigali", **extra}
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200, resp.text
    return payload, resp.json()


async def test_new_church_is_pending_until_it_pays_and_then_activates_automatically(client, church_payload):
    _, session = await _register(client, church_payload, "pay")
    h = auth(session["access_token"])

    # Pending: onboarding works, the church's data doesn't.
    blocked = await client.get("/api/v1/members", headers=h)
    assert blocked.status_code == 402
    assert blocked.json()["error"]["code"] == "org_inactive"
    onboarding = (await client.get("/api/v1/org/onboarding", headers=h)).json()
    assert onboarding["org_status"] == "pending" and onboarding["is_active"] is False
    assert {s["key"]: s["state"] for s in onboarding["steps"]}["payment"] == "todo"

    plans = (await client.get("/api/v1/billing/plans")).json()
    assert {"seed", "parish"} <= {p["tier"] for p in plans if p["purchasable"]}

    checkout = await client.post("/api/v1/billing/checkout", json={"tier": "parish", "interval": "year"}, headers=h)
    assert checkout.status_code == 200, checkout.text
    session_id = checkout.json()["session_id"]
    assert checkout.json()["provider"] == "demo"

    detail = (await client.get(f"/api/v1/billing/demo/checkout/{session_id}", headers=h)).json()
    assert detail["amount_cents"] == 49000  # 10 × monthly for annual

    declined = await client.post(
        f"/api/v1/billing/demo/checkout/{session_id}/pay",
        json={"card_number": "4000 0000 0000 0002", "name_on_card": "Test"},
        headers=h,
    )
    assert declined.status_code == 402
    paid = await client.post(
        f"/api/v1/billing/demo/checkout/{session_id}/pay",
        json={"card_number": "4242 4242 4242 4242", "name_on_card": "Test"},
        headers=h,
    )
    assert paid.status_code == 200, paid.text
    assert paid.json() == {"status": "paid", "org_status": "active"}

    again = await client.post(
        f"/api/v1/billing/demo/checkout/{session_id}/pay",
        json={"card_number": "4242424242424242", "name_on_card": "Test"},
        headers=h,
    )
    assert again.status_code == 409  # a checkout is single-use

    assert (await client.get("/api/v1/members", headers=h)).status_code == 200
    overview = (await client.get("/api/v1/billing/overview", headers=h)).json()
    assert overview["tier"] == "parish" and overview["subscription"]["status"] == "active"
    assert overview["payments"][0]["amount_cents"] == 49000
    profile = (await client.get("/api/v1/org/profile", headers=h)).json()
    assert profile["activation_source"] == "payment"


async def test_webhooks_need_a_valid_fresh_signature_and_are_processed_once(client):
    body = json.dumps({"id": f"evt_test_{uuid.uuid4().hex}", "type": "ping", "data": {"object": {}}}).encode()
    settings = get_settings()
    original = settings.stripe_webhook_secret
    settings.stripe_webhook_secret = "whsec_test"
    try:
        missing = await client.post("/api/v1/billing/webhooks/stripe", content=body)
        assert missing.status_code == 400
        forged = await client.post(
            "/api/v1/billing/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": signatures.sign(body, "wrong")},
        )
        assert forged.status_code == 400
        stale = await client.post(
            "/api/v1/billing/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": signatures.sign(body, "whsec_test", timestamp=1_000_000_000)},
        )
        assert stale.status_code == 400

        good = {"Stripe-Signature": signatures.sign(body, "whsec_test")}
        first = await client.post("/api/v1/billing/webhooks/stripe", content=body, headers=good)
        assert first.json() == {"received": True}
        replay = await client.post("/api/v1/billing/webhooks/stripe", content=body, headers=good)
        assert replay.json() == {"received": True, "duplicate": True}
    finally:
        settings.stripe_webhook_secret = original


async def test_certificate_upload_review_and_manual_activation_by_platform(client, church_payload):
    _, session = await _register(client, church_payload, "cert")
    h = auth(session["access_token"])
    org_id = session["active_membership"]["tenant_id"]

    rejected_type = await client.post(
        "/api/v1/org/documents/certificate",
        files={"file": ("x.exe", b"MZ\x90\x00binary", "application/pdf")},
        headers=h,
    )
    assert rejected_type.status_code == 415  # judged by the bytes, not the claimed type
    upload = await client.post(
        "/api/v1/org/documents/certificate", files={"file": ("certificate.pdf", PDF, "application/pdf")}, headers=h
    )
    assert upload.status_code == 201, upload.text
    doc = upload.json()
    assert doc["status"] == "pending_review" and len(doc["sha256"]) == 64
    download = await client.get(f"/api/v1/org/documents/{doc['id']}/download", headers=h)
    assert download.content == PDF
    assert download.headers["content-disposition"].startswith("attachment")
    assert download.headers["x-content-type-options"] == "nosniff"

    admin = auth((await make_platform_admin(client))["access_token"])
    listed = (await client.get("/api/v1/platform/orgs", params={"status": "pending"}, headers=admin)).json()
    row = next(o for o in listed if o["id"] == org_id)
    assert row["documents_pending"] == 1 and row["verification_status"] == "pending_review"

    no_reason = await client.post(
        f"/api/v1/platform/orgs/{org_id}/documents/{doc['id']}/review", json={"decision": "rejected"}, headers=admin
    )
    assert no_reason.status_code == 422
    verified = await client.post(
        f"/api/v1/platform/orgs/{org_id}/documents/{doc['id']}/review", json={"decision": "verified"}, headers=admin
    )
    assert verified.status_code == 200

    activated = await client.post(
        f"/api/v1/platform/orgs/{org_id}/activate", json={"note": "Sponsored by the diocese"}, headers=admin
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active" and activated.json()["activation_source"] == "platform_admin"
    assert (await client.get("/api/v1/members", headers=h)).status_code == 200

    # Lifecycle transitions are validated: an active church can't go back to pending.
    bad = await client.post(
        f"/api/v1/platform/orgs/{org_id}/status", json={"status": "pending", "reason": "x"}, headers=admin
    )
    assert bad.status_code == 409

    suspended = await client.post(
        f"/api/v1/platform/orgs/{org_id}/status", json={"status": "suspended", "reason": "Unpaid"}, headers=admin
    )
    assert suspended.status_code == 200
    blocked = await client.get("/api/v1/members", headers=h)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "org_suspended"

    # A church can't reach the platform console.
    assert (await client.get("/api/v1/platform/orgs", headers=h)).status_code == 403


async def test_administrator_registers_and_the_leader_is_invited(client, church_payload):
    missing_leader = await client.post(
        "/api/v1/auth/register", json={**church_payload("adminreg"), "registrant_role": "administrator"}
    )
    assert missing_leader.status_code == 422

    _, session = await _register(
        client,
        church_payload,
        "adminreg2",
        registrant_role="administrator",
        other_person={"first_name": "Pastor", "last_name": "Lead", "email": "pastor.lead.x1@example-church.org"},
    )
    assert session["role"] == "board"
    assert "leadership:manage" not in session["permissions"]
    steps = {
        s["key"]: s
        for s in (await client.get("/api/v1/org/onboarding", headers=auth(session["access_token"]))).json()["steps"]
    }
    assert steps["leader"]["state"] == "pending"

    leader = await accept_invite(client, session["invite_url"], first="Pastor", last="Lead")
    assert leader["role"] == "leader" and leader["active_membership"]["is_leader"] is True
    steps = {
        s["key"]: s
        for s in (await client.get("/api/v1/org/onboarding", headers=auth(session["access_token"]))).json()["steps"]
    }
    assert steps["leader"]["state"] == "done"

    # Only the leader appoints administrators.
    by_admin = await client.post(
        "/api/v1/org/people-in-charge",
        json={"role": "administrator", "email": "second.admin.x1@example-church.org", "first_name": "Sec"},
        headers=auth(session["access_token"]),
    )
    assert by_admin.status_code == 403
    by_leader = await client.post(
        "/api/v1/org/people-in-charge",
        json={"role": "administrator", "email": "second.admin.x1@example-church.org", "first_name": "Sec"},
        headers=auth(leader["access_token"]),
    )
    assert by_leader.status_code == 200
    second_leader = await client.post(
        "/api/v1/org/people-in-charge",
        json={"role": "leader", "email": "another.leader.x1@example-church.org", "first_name": "Two"},
        headers=auth(leader["access_token"]),
    )
    assert second_leader.status_code == 409  # one leader per church


async def test_module_settings(client, church_payload):
    _, session = await _register(client, church_payload, "modules")
    h = auth(session["access_token"])
    subscription = (await client.get("/api/v1/org/modules", headers=h)).json()
    assert subscription["tier"] in ("seed", "free") and subscription["overrides"] == {}

    # Module switches are an MFA-gated org setting.
    toggle = await client.put("/api/v1/org/modules/giving", json={"enabled": False}, headers=h)
    assert toggle.status_code == 403 and toggle.json()["error"]["code"] == "mfa_required"
    tier = await client.put("/api/v1/org/modules/tier", json={"tier": "enterprise"}, headers=h)
    assert tier.status_code == 403

    admin = auth((await make_platform_admin(client))["access_token"])
    org_id = session["active_membership"]["tenant_id"]
    granted = await client.put(
        f"/api/v1/platform/orgs/{org_id}/modules/analytics", json={"enabled": True}, headers=admin
    )
    assert granted.json()["overrides"] == {"analytics": True} and granted.json()["platform_managed"] == ["analytics"]
    core = await client.put(f"/api/v1/platform/orgs/{org_id}/modules/people", json={"enabled": False}, headers=admin)
    assert core.status_code == 409
    assert (await client.get("/api/v1/org/modules", headers=h)).json()["overrides"] == {"analytics": True}
