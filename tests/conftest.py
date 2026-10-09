import os
import uuid

# New churches start *pending* until they pay. Most tests exercise features of
# a live church, so they start active here; test_lifecycle switches this off
# to exercise the real registration → payment → activation path.
os.environ.setdefault("DEV_AUTO_ACTIVATE_ORGS", "true")

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import connect_clients, disconnect_clients
from app.core.rate_limit import limiter
from app.main import app


@pytest.fixture(scope="session", autouse=True)
async def _prisma_lifecycle():
    # httpx's ASGITransport doesn't run the ASGI lifespan protocol, so the
    # app's own `lifespan=` (which connects/disconnects the Prisma clients)
    # never fires in tests — connect/disconnect explicitly instead, once for
    # the whole session.
    await connect_clients()
    yield
    await disconnect_clients()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    # The limiter is a process-wide singleton (so limits are shared across
    # real requests, which is the point); reset it between tests so one
    # test's /auth/register calls don't exhaust another test's quota.
    limiter.reset()


@pytest.fixture
async def client():
    """A non-browser API client: asks for the refresh token in the JSON body
    (X-Refresh-Token-Transport: body) instead of the httpOnly cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Refresh-Token-Transport": "body"}
    ) as ac:
        yield ac


@pytest.fixture
async def browser():
    """Behaves like the web app: refresh token only ever in the cookie jar."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def unique_email(label: str) -> str:
    return f"{label}.{uuid.uuid4().hex[:10]}@example-church.org"


@pytest.fixture
def church_payload():
    def _make(label: str = "test"):
        return {
            "church_name": f"Test Church {uuid.uuid4().hex[:6]}",
            "first_name": "Test",
            "last_name": "Leader",
            "email": unique_email(label),
            "password": "Testing2026Pass",
            "country": "US",
            "timezone": "UTC",
            "currency": "USD",
        }

    return _make


# ── Shared helpers for multi-actor tests ──────────────────────────────────


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def accept_invite(
    client, invite_url: str, password: str = "Accepted2026Pass", first="Invited", last="Person"
) -> dict:
    token = invite_url.split("#", 1)[1]
    resp = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": password, "first_name": first, "last_name": last},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def make_platform_admin(client) -> dict:
    """A full platform admin, created directly (there is no self-serve way to become one)."""
    from app.core.database import platform_session
    from app.core.security import hash_password

    email = unique_email("platform")
    async with platform_session() as db:
        await db.user.create(
            data={
                "email": email,
                "password_hash": hash_password("PlatformAdmin2026"),
                "first_name": "Plat",
                "last_name": "Form",
                "is_platform_admin": True,
                "platform_admin_level": "full",
            }
        )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": "PlatformAdmin2026"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def adult_member(first: str, last: str = "Test", **extra) -> dict:
    body = {
        "first_name": first,
        "last_name": last,
        "gender": "female",
        "phone": f"+25078{uuid.uuid4().int % 10**7:07d}",
        "consent": {"given_by": "self"},
        "national_id": f"1{uuid.uuid4().int % 10**15:015d}",
    }
    body.update(extra)
    return body
