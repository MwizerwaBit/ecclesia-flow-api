"""Plan and per-church overrides are enforced by the API, not just shown by the app.

A church registers on Seed. A paid module above its plan answers 403
`module_not_enabled`, unless the platform granted it; a module switched off
answers the same. Free (core) modules and shared plumbing are never affected.
"""

import pytest

from app.core.database import platform_session
from tests.conftest import auth, make_platform_admin


async def _church(client, church_payload, label):
    resp = await client.post("/api/v1/auth/register", json=church_payload(label))
    assert resp.status_code == 200, resp.text
    session = resp.json()
    return session, auth(session["access_token"]), session["active_membership"]["tenant_id"]


async def _override(client, org_id, module, enabled):
    admin = auth((await make_platform_admin(client))["access_token"])
    resp = await client.put(
        f"/api/v1/platform/orgs/{org_id}/modules/{module}", json={"enabled": enabled}, headers=admin
    )
    assert resp.status_code == 200, resp.text


async def _set_tier(org_id, tier):
    async with platform_session() as db:
        await db.organization.update(where={"id": org_id}, data={"tier": tier})


def _code(resp):
    return resp.json()["error"]["code"]


async def test_module_above_plan_is_refused_until_the_plan_reaches_it(client, church_payload):
    _, h, org_id = await _church(client, church_payload, "plan")

    refused = await client.get("/api/v1/certificates", headers=h)
    assert refused.status_code == 403 and _code(refused) == "module_not_enabled"
    assert (await client.get("/api/v1/finance/funds", headers=h)).status_code == 200  # Seed includes Giving

    await _set_tier(org_id, "parish")
    assert (await client.get("/api/v1/certificates", headers=h)).status_code == 200


async def test_platform_grant_above_plan_and_off_switch_within_plan(client, church_payload):
    _, h, org_id = await _church(client, church_payload, "override")

    await _override(client, org_id, "certificates", True)
    assert (await client.get("/api/v1/certificates", headers=h)).status_code == 200

    await _override(client, org_id, "giving", False)
    off = await client.get("/api/v1/finance/funds", headers=h)
    assert off.status_code == 403 and _code(off) == "module_not_enabled"


async def test_overrides_never_cross_churches(client, church_payload):
    _, _, granted_org = await _church(client, church_payload, "granted")
    _, other_h, _ = await _church(client, church_payload, "other")
    await _override(client, granted_org, "certificates", True)
    assert (await client.get("/api/v1/certificates", headers=other_h)).status_code == 403


async def test_core_modules_and_shared_media_are_not_plan_checked(client, church_payload):
    _, h, _ = await _church(client, church_payload, "core")
    assert (await client.get("/api/v1/members", headers=h)).status_code == 200
    assert (await client.get("/api/v1/events", headers=h)).status_code == 200
    # /media also stores member photos and the church logo, which every plan has.
    assert (await client.get("/api/v1/media", headers=h)).status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/certificates", "/api/v1/affiliations"])
async def test_unpaid_state_still_answers_402_first(client, church_payload, monkeypatch, path):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "dev_auto_activate_orgs", False)
    _, h, _ = await _church(client, church_payload, "unpaid")
    resp = await client.get(path, headers=h)
    assert resp.status_code == 402 and _code(resp) == "module_locked"
