import uuid

import pytest
from httpx import ASGITransport, AsyncClient

import app.db.models_registry  # noqa: F401 — must load before any ORM use
from app.core.rate_limit import limiter
from app.main import app


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    # The limiter is a process-wide singleton (so limits are shared across
    # real requests, which is the point); reset it between tests so one
    # test's /auth/register calls don't exhaust another test's quota.
    limiter.reset()


@pytest.fixture
async def client():
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
